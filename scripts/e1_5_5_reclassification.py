#!/usr/bin/env python3
"""E1.5.5: reclassification of the E1.4 feasibility records with the E1.5-validated gain, NR link
mapping and multiple-access thresholds.

Implements configs/e1/e1_5_5_reclassification.json. Reuses the E1.4 functions
(scripts/e1_antennas_o2i.py) unchanged. Outputs in --outdir: summary.json, sensitivity.csv,
validation.json, manifest.json.
"""
import argparse
import csv
import json
import math
import platform
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'scripts'))
import e1_antennas_o2i as e14  # noqa: E402
import e1_link_budget as e13  # noqa: E402
import e1_5_0_surrogates as e150  # noqa: E402
from cmwave.radio import antenna as ant  # noqa: E402
from cmwave.radio import link_budget as lb  # noqa: E402

CONFIG = ROOT / 'configs/e1/e1_5_5_reclassification.json'
E14_CONFIG = ROOT / 'configs/e1/e1_4_antennas_o2i.json'
R = ROOT / 'results/phase2'
E14_DIR, E150_DIR = R / 'e1_4_antennas_o2i', R / 'e1_5_0_surrogates'
E1521_DIR, E153_DIR, E153A_DIR, E154_DIR = (R / 'e1_5_2_1_beams', R / 'e1_5_3_link_mapping',
                                           R / 'e1_5_3a_amc_diagnostic', R / 'e1_5_4_multiuser')
OUTDIR = R / 'e1_5_5_reclassification'
PROFILES = ('F1', 'F2', 'F3')
DIRS = ('DL', 'UL')
K_USERS = 10
C_NLOS_LOSS_DB = 4.5
F3_PER_USER = {'DL': 0.225, 'UL': 0.15}
TR_FLOOR = {'DL': -10.0, 'UL': -10.0}
CHAIN = ('V0_e14', 'V1_gain', 'V1b_mapping', 'V2_primary')
X6_GATED = ('V0_e14', 'V1_gain', 'V1b_mapping', 'V2_primary', 'g_dp', 'g_eig', 'g_cb_ci_lo', 'g_cb_ci_hi',
            'g_c_matched', 'sel', 'v19')
VARIANTS = {
    'V0_e14': {'mapping': 'tr36942', 'gain': 'e14', 'f3_dl': 'e150'},
    'V1_gain': {'mapping': 'tr36942', 'gain': 'cb'},
    'V1b_mapping': {'mapping': 'camc_t2', 'gain': 'e14'},
    'V2_primary': {'mapping': 'camc_t2', 'gain': 'cb'},
    'm_camc_t1': {'mapping': 'camc_t1', 'gain': 'cb'},
    'm_raw_t2': {'mapping': 'raw_t2', 'gain': 'cb'},
    'm_trlow': {'mapping': 'camc_t2_trlow', 'gain': 'cb'},
    'g_dp': {'mapping': 'camc_t2', 'gain': 'dp'},
    'g_eig': {'mapping': 'camc_t2', 'gain': 'eig'},
    'g_cb_ci_lo': {'mapping': 'camc_t2', 'gain': 'ci_lo'},
    'g_cb_ci_hi': {'mapping': 'camc_t2', 'gain': 'ci_hi'},
    'g_c_matched': {'mapping': 'camc_t2', 'gain': 'cb', 'c_rule': 'matched'},
    'pkt': {'mapping': 'camc_t2', 'gain': 'cb', 'pkt': True},
    'sel': {'mapping': 'camc_t2', 'gain': 'cb', 'sel': True},
    'v19': {'mapping': 'camc_t2', 'gain': 'cb', 'o2i': '19.5.0'},
}
GAIN_KEY = {'cb': 'D_cb_db', 'dp': 'D_dp_db', 'eig': 'D_eig_db', 'ci_lo': ('D_cb_ci95', 0), 'ci_hi': ('D_cb_ci95', 1)}


# ---------------------------------------------------------------- mappings (unit-tested)
def curve_fn(curves, snr_min, low_tr=False):
    """SE = piecewise-linear curve for snr >= snr_min, 0 below (or TR 36.942 below if low_tr)."""
    xs, ys = {}, {}
    for d in DIRS:
        pts = sorted((float(a), float(b)) for a, b in curves[d])
        xs[d] = np.array([p[0] for p in pts])
        ys[d] = np.array([p[1] for p in pts])
        if np.any(np.diff(xs[d]) <= 0):
            raise SystemExit(f'curve {d}: SNR grid not strictly increasing')

    def fn(snr, d):
        s = np.asarray(snr, dtype=float)
        below = lb.mapped_se(s, d) if low_tr else 0.0
        return np.where(s >= snr_min[d], np.interp(s, xs[d], ys[d]), below)
    fn.grid = (xs, ys)
    return fn


def shifted(fn, delta):
    return lambda s, d: fn(np.asarray(s, dtype=float) - delta[d], d)


def scaled(fn, r):
    return lambda s, d: r[d] * np.asarray(fn(s, d), dtype=float)


def build_mappings(s3a, s3):
    sb = s3a['stage_b']

    def cur(src, m):
        return {d: src['curves'][f'{m}_{d}'] for d in DIRS}, {d: src['fits'][f'{m}_{d}']['snr_min_db'] for d in DIRS}
    c2, m2 = cur(sb, 't2')
    c1, m1 = cur(sb, 't1')
    cr, mr = cur(s3, 't2')
    return {'tr36942': (lb.mapped_se, dict(TR_FLOOR), None),
            'camc_t2': (curve_fn(c2, m2), m2, (c2, m2)),
            'camc_t1': (curve_fn(c1, m1), m1, (c1, m1)),
            'raw_t2': (curve_fn(cr, mr), mr, (cr, mr)),
            'camc_t2_trlow': (curve_fn(c2, m2, low_tr=True), dict(TR_FLOOR), (c2, m2))}


# ---------------------------------------------------------------- inputs from E1.5.2-1 / E1.5.4 (unit-tested)
def gain_table(s1521):
    t = {}
    for row in s1521['gain_model']:
        t[(row['array'], float(row['frequency_hz']), row['stratum'])] = row
    return t


def strata_of(state, los):
    if state in ('outdoor', 'in_car'):
        return np.where(los == 'LOS', 'o2o_los', 'o2o_nlos')
    return np.full(los.shape, 'o2i')


def array_of(panel):
    if panel.isotropic:
        return None
    dims = (panel.rows, panel.cols)
    return {(8, 16): 'a', (16, 32): 'b'}.get(dims)


def lookup(table, array, f, strata, key):
    def val(s):
        row = table[(array, f, s)]
        return float(row[key[0]][key[1]]) if isinstance(key, tuple) else float(row[key])
    vals = {s: val(s) for s in np.unique(strata)}
    return np.array([vals[s] for s in strata])


def selectivity_factors(s154, cut=0.55, n_min=5):
    """E1.5.4 post-hoc definition: min over configurations (>= n_min UEs) of the median arm-A /
    arm-Ag per-user SE ratio among UEs with 0 < SE(Ag) < cut."""
    pr = s154['per_run']
    out = {}
    for d in DIRS:
        meds = {}
        for cfg in ('a_f3p5', 'a_f7p2', 'b_f7p2'):
            rat = []
            for seed in (1, 2, 3):
                qa = pr[f'A_{cfg}_s{seed}']['metrics'][d]['ue']
                qg = pr[f'Ag_{cfg}_s{seed}']['metrics'][d]['ue']
                rat += [a['se_user'] / g['se_user'] for a, g in zip(qa, qg) if 0 < g['se_user'] < cut]
            if len(rat) >= n_min:
                meds[cfg] = float(np.median(rat))
        out[d] = {'factor': min(meds.values()), 'medians': meds}
    return out


def packet_offsets(s154):
    a, t = s154['analytic_thresholds_db'], s154['thresholds_db']
    return ({d: t['H_TC'][d] - a['F2'][d] for d in DIRS}, {d: t['S_C'][d] - a['F3'][d] for d in DIRS})


# ---------------------------------------------------------------- evaluation
def gains_for(case, panel, f, state, geo, var, table):
    """(bf, D, x4_ok): bf = NLOS beamforming loss, D = E1.5 deficit, both in dB per record."""
    fam = case['regime'].split()[0]
    nlos = (geo['los'] == 'NLOS').astype(float)
    zero = np.zeros(nlos.shape)
    if var['gain'] == 'e14':
        return case['nlos_bf_loss_db'] * nlos, zero, True
    if fam == 'reference':
        if not panel.isotropic:
            raise SystemExit('reference case with a beamformed panel')
        return case['nlos_bf_loss_db'] * nlos, zero, True
    strata = strata_of(state, geo['los'])
    arr = array_of(panel)
    if fam == 'C':
        if var.get('c_rule', 'nlos45') == 'nlos45':
            return C_NLOS_LOSS_DB * nlos, zero, True
        if arr is None:
            raise SystemExit(f'no measured array for panel {panel}')
        d = lookup(table, arr, f, strata, 'D_cb_db')
        d2 = np.array([table[(arr, f, s)]['D_cb_db'] for s in strata])
        return zero, d, bool(np.max(np.abs(d - d2)) <= 1e-12)
    if fam not in ('A', 'B') or case['nlos_bf_loss_db'] != 0.0 or arr is None:
        raise SystemExit(f'unexpected case/panel: {case["regime"]} {panel}')
    key = GAIN_KEY[var['gain']]
    d = lookup(table, arr, f, strata, key)
    d2 = np.array([(table[(arr, f, s)][key[0]][key[1]] if isinstance(key, tuple) else table[(arr, f, s)][key])
                   for s in strata])
    return zero, d, bool(np.max(np.abs(d - d2)) <= 1e-12)


def evaluate_variant(case, states, geo, thresholds, var, maps, table, pkt_off, sel_r):
    se_base, floors, _ = maps[var['mapping']]
    se2 = se3 = se_base
    if var.get('pkt'):
        se2, se3 = shifted(se_base, pkt_off[0]), shifted(se_base, pkt_off[1])
    if var.get('sel') and case['regime'].split()[0] in ('A', 'B'):
        se2, se3 = scaled(se2, sel_r), scaled(se3, sel_r)
    t2 = thresholds['F2_m2410_equivalent']
    carriers = sorted(case['carriers'], key=lambda c: c['frequency_hz'])
    out, flags = {}, {'x4': True, 'x7': True, 'x6_lost': 0, 'x6_viol': 0}
    for state in e14.STATES:
        st = states[state]
        res = {}
        for c in carriers:
            cfg = lb.CarrierConfig(**{k: c[k] for k in e14.CFG_FIELDS})
            panel = e14.panel_of(c, case)
            f = cfg.frequency_hz
            g_gnb, sector = ant.gnb_gain_dbi(panel, st['zenith'], st['azimuth'])
            bf, dfc, x4 = gains_for(case, panel, f, state, geo, var, table)
            g_ue = ant.ue_rx_gain_db(c['ue_n_rx'])
            g_dl, g_ul = g_gnb - bf - dfc + g_ue, g_gnb - bf - dfc
            loss = st['loss'][f]
            dl = lb.downlink(loss, cfg, gain_db=g_dl, se_fn=se2)
            ul = lb.uplink(loss, cfg, gain_db=g_ul, se_fn=se2)
            n_max = cfg.n_prb // K_USERS
            ul3 = lb.uplink(loss, cfg, gain_db=g_ul, n_max_prb=n_max, se_fn=se3)
            dl['F1'] = dl['snr_db'] >= floors['DL']
            ul['F1'] = ul['snr_1prb_db'] >= floors['UL']
            dl['F2'] = dl['se_bps_hz'] >= t2['DL_se_min']
            ul['F2'] = ul['se_norm_bps_hz'] >= t2['UL_se_norm_min']
            ul['F3'] = ul3['se_norm_bps_hz'] >= F3_PER_USER['UL']
            if var.get('f3_dl') == 'e150':
                dl['F3'] = dl['F2']
            else:
                dl['F3'] = np.asarray(se3(dl['snr_db'], 'DL')) * n_max / cfg.n_prb >= F3_PER_USER['DL']
            # X7: best-n flags equal the full-allocation flags
            full2 = np.asarray(se2(ul['snr_full_db'], 'UL')) >= t2['UL_se_norm_min']
            snr3 = ul['rx_power_dbm'] - lb.noise_dbm(n_max * cfg.prb_bw_hz, cfg.nf_gnb_db)
            full3 = np.asarray(se3(snr3, 'UL')) * (n_max * cfg.prb_bw_hz) / cfg.occupied_bw_hz >= F3_PER_USER['UL']
            flags['x7'] &= bool(np.array_equal(ul['F2'], full2) and np.array_equal(ul['F3'], full3))
            flags['x4'] &= x4
            lost = ul['F2'] & ~ul['F3']
            flags['x6_lost'] += int(lost.sum())
            flags['x6_viol'] += int(np.sum(lost & (ul['best_n_prb'] <= 10 * n_max)))
            res[f] = {'cfg': cfg, 'panel': panel, 'n_rx': c['ue_n_rx'], 'g_gnb': g_gnb - bf - dfc, 'g_ue': g_ue,
                      'sector': np.zeros(sector.shape), 'DL': dl, 'UL': ul}
        out[state] = res
    return out, flags


# ---------------------------------------------------------------- summaries
def label_of(k):
    """Partition class index per record (order e14.PARTITION); -1 if none."""
    lab = np.full(next(iter(k.values())).shape, -1)
    for i, name in enumerate(e14.PARTITION):
        lab = np.where(k[name], i, lab)
    return lab


def summarise(out, geo, bins, mixes, detail):
    f1, f2 = geo['freqs']
    ue_of = np.array([kk[1] for kk in geo['keys']])
    blist = sorted(set(bins.values()), key=lambda t: float(t.split('-')[0]))
    bmask = {b: np.array([bins[u] == b for u in ue_of]) for b in blist}
    per_state, part = {}, {'exclusive_exhaustive': True, 'k6_shared': 0}
    for state in e14.STATES:
        st = {}
        for p in PROFILES:
            k = e14.classes14(out[state], f1, f2, p)
            part['exclusive_exhaustive'] &= bool(np.all(np.sum([k[n] for n in e14.PARTITION], axis=0) == 1))
            if state in e14.SHARED_STATES:
                part['k6_shared'] += int(np.sum(k[e14.K6]))
            prof = {'feasible_fraction': {f'{d}_{f/1e9:.1f}GHz': float(np.mean(out[state][f][d][p]))
                                          for f in geo['freqs'] for d in DIRS},
                    'class_fraction': {n: float(np.mean(v)) for n, v in k.items()}}
            if detail:
                prof['by_distance_bin'] = {b: {n: float(np.mean(v[bmask[b]])) for n, v in k.items()}
                                           for b in blist}
                prof['by_los'] = {ls: {n: float(np.mean(v[geo['los'] == ls])) for n, v in k.items()}
                                  for ls in ('LOS', 'NLOS')}
            st[p] = prof
        per_state[state] = st
    mm = {}
    for mname, mix in mixes.items():
        w = mix['weights']
        mm[mname] = {p: {key: {n: sum(w[s] * per_state[s][p][key][n] for s in w)
                               for n in per_state[next(iter(w))][p][key]}
                         for key in ('feasible_fraction', 'class_fraction')} for p in PROFILES}
    return {'per_state': per_state, 'mixes': mm}, part


def transitions(out_a, out_b, geo, mixes):
    f1, f2 = geo['freqs']
    res = {}
    for p in PROFILES:
        per = {}
        for state in e14.STATES:
            la = label_of(e14.classes14(out_a[state], f1, f2, p))
            lb_ = label_of(e14.classes14(out_b[state], f1, f2, p))
            m = np.zeros((len(e14.PARTITION), len(e14.PARTITION)))
            for i in range(len(e14.PARTITION)):
                for j in range(len(e14.PARTITION)):
                    m[i, j] = np.mean((la == i) & (lb_ == j))
            per[state] = m
        res[p] = {mname: (sum(w * per[s] for s, w in mix['weights'].items())).tolist()
                  for mname, mix in mixes.items()}
        res[p]['per_state'] = {s: v.tolist() for s, v in per.items()}
    return res


def hazard15(out_v2, out_lo, geo):
    return {state: {f'{d}_{f/1e9:.1f}GHz': float(np.mean(out_lo[state][f][d]['F1'] & ~out_v2[state][f][d]['F1']))
                    for f in geo['freqs'] for d in DIRS} for state in e14.STATES}


# ---------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--config', default=str(CONFIG))
    ap.add_argument('--outdir', default=str(OUTDIR))
    ap.add_argument('--git-head', default=None)
    args = ap.parse_args()
    conf55 = json.loads(Path(args.config).read_text(encoding='utf-8'))
    conf = json.loads(E14_CONFIG.read_text(encoding='utf-8'))
    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    validation = {}

    # ---- X0 inputs
    mans = {d.name: json.loads((d / 'manifest.json').read_text()) for d in
            (E14_DIR, E150_DIR, E1521_DIR, E153_DIR, E153A_DIR, E154_DIR)}
    e14man = mans[E14_DIR.name]
    x0 = (all(m['all_checks_passed'] for m in mans.values())
          and e13.sha256(E14_CONFIG) == e14man['config_sha256']
          and e13.sha256(E14_DIR / 'summary.json') == e14man['outputs_sha256']['summary.json']
          and e13.sha256(E150_DIR / 'summary.json') == mans[E150_DIR.name]['outputs_sha256']['summary.json'])
    in_sha = {f'{d.name}/{f}': e13.sha256(d / f) for d in (E14_DIR, E150_DIR, E1521_DIR, E153_DIR, E153A_DIR,
                                                            E154_DIR) for f in ('summary.json', 'manifest.json')}
    validation['X0_inputs'] = {'input_sha256': in_sha, 'passed': bool(x0)}
    if not x0:
        raise SystemExit('X0 failed: inputs not validated or changed')
    e14_summary = json.loads((E14_DIR / 'summary.json').read_text())
    e150_summary = json.loads((E150_DIR / 'summary.json').read_text())['full']['16.1.0']
    s1521 = json.loads((E1521_DIR / 'summary.json').read_text())
    s153 = json.loads((E153_DIR / 'summary.json').read_text())
    s153a = json.loads((E153A_DIR / 'summary.json').read_text())
    s154 = json.loads((E154_DIR / 'summary.json').read_text())
    maps = build_mappings(s153a, s153)
    table = gain_table(s1521)
    pkt_off = packet_offsets(s154)
    sel = selectivity_factors(s154)
    sel_r = {d: sel[d]['factor'] for d in DIRS}

    # ---- X3 mappings
    x3 = {}
    fine = np.arange(-40.0, 70.0, 0.001)
    for name, (fn, floors, raw) in maps.items():
        if raw is None:
            continue
        curves, smin = raw
        err, below = 0.0, 0.0
        for d in DIRS:
            for s, se in curves[d]:
                v = float(fn(np.array([s]), d)[0])
                if s >= smin[d]:
                    err = max(err, abs(v - se))
                elif name != 'camc_t2_trlow':
                    below = max(below, abs(v))
        x3[name] = {'max_abs_err_at_grid': err, 'max_abs_below_floor': below}
    mono = {d: bool(np.all(np.diff(maps['camc_t2'][0](fine, d)) >= 0)) for d in DIRS}
    validation['X3_mapping'] = {'per_mapping': x3, 'camc_t2_non_decreasing': mono,
                                'passed': all(v['max_abs_err_at_grid'] == 0.0 and v['max_abs_below_floor'] == 0.0
                                              for v in x3.values()) and all(mono.values())}

    # ---- E1.4 inputs and states
    eargs = argparse.Namespace(geometry_dir=str(R / 'e1_geometry'), propagation_dir=str(R / 'e1_3gpp_propagation'),
                               e13_dir=str(R / 'e1_3_link_budget'))
    gman, links, pman, prop, e3man, e3rec, e3dir = e14.load_inputs(eargs)
    with (R / 'e1_geometry/site_tx0_links.csv').open(encoding='utf-8') as fh:
        bins = {r['ue_id']: r['distance_bin_m'] for r in csv.DictReader(fh)}
    st_cache = {}

    def states_for(version):
        if version not in st_cache:
            c = dict(conf)
            c['o2i_tr38901_version'] = version
            states, geo, _, _, _ = e14.build_states(c, gman, links, prop)
            st_cache[version] = (states, geo)
        return st_cache[version]

    mixes = conf['mixes']
    validation['X8_mixes'] = {'passed': all(all(w >= 0 for w in m['weights'].values())
                                            and abs(sum(m['weights'].values()) - 1) <= 1e-12
                                            and set(m['weights']) <= set(e14.SHARED_STATES)
                                            for m in mixes.values())}

    summary = {'inputs': {'packet_offsets_db': {'F2': pkt_off[0], 'F3': pkt_off[1]}, 'selectivity': sel,
                          'snr_min_db': {k: v[1] for k, v in maps.items()},
                          'gain_table': s1521['gain_model']},
               'variants': {}, 'transitions_V0_to_V2': {}, 'hazard15_f1_only_with_tr_low_end': {}}
    x1_diff, x2_diff = 0, 0
    x4_ok, x7_ok, part_ok = True, True, True
    x6 = {}
    keep = {}
    for vname, var in VARIANTS.items():
        states, geo = states_for(var.get('o2i', '16.1.0'))
        summary['variants'][vname] = {}
        x6[vname] = {'lost': 0, 'viol': 0}
        e14_s = {}
        for cname, case in conf['cases'].items():
            out, flags = evaluate_variant(case, states, geo, conf['thresholds'], var, maps, table, pkt_off, sel_r)
            s, part = summarise(out, geo, bins, mixes, detail=vname in CHAIN)
            summary['variants'][vname][cname] = s
            x4_ok &= flags['x4']
            x7_ok &= flags['x7']
            part_ok &= part['exclusive_exhaustive'] and part['k6_shared'] == 0
            if not (part['exclusive_exhaustive'] and part['k6_shared'] == 0):
                print(f'X5 failure: {vname} {cname} {part}', flush=True)
            x6[vname]['lost'] += flags['x6_lost']
            x6[vname]['viol'] += flags['x6_viol']
            if vname == 'V0_e14':
                e14_s[cname] = e14.summarise(cname, case, out, geo, bins, mixes)
                for state in e14.STATES:
                    res3 = {f: {'DL': {'F3': out[state][f]['DL']['F3']}, 'UL': {'F3': out[state][f]['UL']['F3']}}
                            for f in geo['freqs']}
                    ff, cf, _ = e150.class_and_feasible(res3, *geo['freqs'], 'F3', geo['freqs'])
                    ref = e150_summary[cname]['F3_ofdma_share']['per_state'][state]
                    x2_diff += sum(int(ff[n] != ref['feasible_fraction'][n]) for n in ff)
                    x2_diff += sum(int(cf[n] != ref['class_fraction'][n]) for n in cf)
                for m in mixes:
                    ref = e150_summary[cname]['F3_ofdma_share']['mixes'][m]
                    x2_diff += sum(int(abs(s['mixes'][m]['F3'][k][n] - ref[k][n]) > 0)
                                   for k in ('feasible_fraction', 'class_fraction') for n in ref[k])
            if vname in ('V0_e14', 'V2_primary', 'm_trlow'):
                keep[(vname, cname)] = out
            print(f'{vname} {cname}: done', flush=True)
        if vname == 'V0_e14':
            x1_diff = e150.equal_fractions(e14_s, e14_summary)
            print(f'X1 differing fractions: {x1_diff}; X2: {x2_diff}', flush=True)
            if x1_diff or x2_diff:
                break
    validation['X1_e14_reproduction'] = {'differing_fractions': x1_diff, 'passed': x1_diff == 0}
    validation['X2_e150_reproduction'] = {'differing_fractions': x2_diff, 'passed': x2_diff == 0}
    if x1_diff or x2_diff:
        validation['all_checks_passed'] = False
        (outdir / 'validation.json').write_text(json.dumps(validation, indent=2), encoding='utf-8')
        raise SystemExit('X1/X2 failed: stop')
    validation['X4_gain'] = {'passed': bool(x4_ok)}
    validation['X5_partition'] = {'passed': bool(part_ok)}
    validation['X6_dominance'] = {'per_variant': x6, 'gated': list(X6_GATED),
                                  'passed': all(x6[v]['viol'] == 0 for v in X6_GATED)}
    validation['X7_allocation_identity'] = {'passed': bool(x7_ok)}
    gates_ok = all(v['passed'] for v in validation.values())
    validation['all_checks_passed'] = gates_ok

    states, geo = states_for('16.1.0')
    for cname in conf['cases']:
        summary['transitions_V0_to_V2'][cname] = transitions(keep[('V0_e14', cname)], keep[('V2_primary', cname)],
                                                             geo, mixes)
        summary['hazard15_f1_only_with_tr_low_end'][cname] = hazard15(keep[('V2_primary', cname)],
                                                                      keep[('m_trlow', cname)], geo)
    summary['partition_order'] = list(e14.PARTITION)

    # ---- predictions
    def mixv(v, c, p, key, n):
        return summary['variants'][v][c]['mixes']['M2412'][p][key][n]
    p1 = {f'{c}_{f}': (mixv('V2_primary', c, 'F2', 'feasible_fraction', f'UL_{f}'),
                       mixv('V0_e14', c, 'F2', 'feasible_fraction', f'UL_{f}'))
          for c in ('A_equal_elements', 'B_equal_aperture') for f in ('3.5GHz', '7.2GHz')}
    p2 = {c: (mixv('V2_primary', c, 'F2', 'feasible_fraction', 'DL_7.2GHz'),
              mixv('V0_e14', c, 'F2', 'feasible_fraction', 'DL_7.2GHz'))
          for c in ('A_equal_elements', 'B_equal_aperture')}
    k3 = 'K3_7p2_dl_without_any_ul'
    p3 = {c: (mixv('V2_primary', c, 'F3', 'class_fraction', k3), mixv('V2_primary', c, 'F2', 'class_fraction', k3))
          for c in ('A_equal_elements', 'B_equal_aperture', 'C_deployment')}
    summary['predictions'] = {
        'P1': {'values_v2_v0': p1, 'held': all(a < b for a, b in p1.values())},
        'P2': {'values_v2_v0': p2, 'held': all(a < b for a, b in p2.values())},
        'P3': {'values_k3_f3_f2': p3, 'held': all(a < b for a, b in p3.values())}}
    summary['decision'] = (conf55['decision_rules']['all_gates_pass'] if gates_ok
                           else conf55['decision_rules']['gate_fails'])

    # ---- sensitivity table (M2412 mix)
    rows = []
    for vname in VARIANTS:
        for cname in ('R0_isotropic_e13', 'A_equal_elements', 'B_equal_aperture', 'C_deployment', 'S1_A_ue1rx'):
            for p in PROFILES:
                mm = summary['variants'][vname][cname]['mixes']['M2412'][p]
                rows.append([vname, cname, p] + [repr(mm['feasible_fraction'][k]) for k in
                                                  ('DL_3.5GHz', 'UL_3.5GHz', 'DL_7.2GHz', 'UL_7.2GHz')]
                            + [repr(mm['class_fraction'][n]) for n in e14.PARTITION])
    with (outdir / 'sensitivity.csv').open('w', newline='', encoding='utf-8') as fh:
        w = csv.writer(fh, lineterminator='\n')
        w.writerow(['variant', 'case', 'profile', 'DL_3.5GHz', 'UL_3.5GHz', 'DL_7.2GHz', 'UL_7.2GHz']
                   + [n.split('_')[0] for n in e14.PARTITION])
        w.writerows(rows)

    (outdir / 'summary.json').write_text(json.dumps(summary, indent=1, default=float), encoding='utf-8')
    (outdir / 'validation.json').write_text(json.dumps(validation, indent=2), encoding='utf-8')
    manifest = {'generated_utc': datetime.now(timezone.utc).isoformat(), 'tool': 'scripts/e1_5_5_reclassification.py',
                'git_head_at_run': args.git_head or e13.git_head(), 'python': platform.python_version(),
                'numpy': np.__version__, 'config_sha256': e13.sha256(args.config),
                'e1_4_config_sha256': e13.sha256(E14_CONFIG), 'input_sha256': in_sha,
                'outputs_sha256': {'summary.json': e13.sha256(outdir / 'summary.json'),
                                   'sensitivity.csv': e13.sha256(outdir / 'sensitivity.csv')},
                'all_checks_passed': gates_ok}
    (outdir / 'manifest.json').write_text(json.dumps(manifest, indent=2), encoding='utf-8')
    for k, v in validation.items():
        if isinstance(v, dict):
            print(k, 'PASS' if v['passed'] else 'FAIL')
    print('E1.5.5', 'GATES PASSED' if gates_ok else 'GATES FAILED')
    sys.exit(0 if gates_ok else 1)


if __name__ == '__main__':
    main()
