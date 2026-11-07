#!/usr/bin/env python3
"""E1.4: antenna regimes and outdoor-to-indoor deployment states, UL and DL.

Implements the pre-registration configs/e1/e1_4_antennas_o2i.json on the
hash-checked E1.1 geometry and E1.2 propagation. Outputs in --outdir:
  raw/records.csv.gz  one row per case, state, realization, UE, carrier and
                      direction (regenerable; SHA-256 in the manifest)
  links_median.csv    median-realization budgets per case, state and UE
  summary.json        feasibility and classes K1-K5 per case, state, profile;
                      M.2412 and 50/50 deployment mixes
  validation.json     checks N1-N10;  manifest.json provenance
"""
import argparse
import csv
import gzip
import io
import json
import math
import platform
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'scripts'))
from cmwave.radio import antenna as ant  # noqa: E402
from cmwave.radio import link_budget as lb  # noqa: E402
from cmwave.radio import o2i  # noqa: E402
from cmwave.radio import uma_hybrid as uh  # noqa: E402
import e1_link_budget as e13  # noqa: E402  (shared helpers: sha256, git_head, classes)

TOL = 1e-9
SHARED_STATES = ('outdoor', 'in_car', 'indoor_low', 'indoor_high')
STATES = SHARED_STATES + ('indoor_low_indep', 'indoor_high_indep')
BUILDING = {'indoor_low': 'low', 'indoor_high': 'high', 'indoor_low_indep': 'low',
            'indoor_high_indep': 'high'}
CFG_FIELDS = ('frequency_hz', 'channel_bw_hz', 'scs_hz', 'n_prb', 'gnb_power_dbm', 'ue_power_dbm',
              'nf_gnb_db', 'nf_ue_db', 'gain_gnb_dbi', 'gain_ue_dbi')
FIELDS = ['case', 'regime', 'state', 'site_id', 'ue_id', 'realization', 'frequency_hz', 'direction',
          'los_state', 'distance_2d_out_m', 'distance_2d_in_m', 'distance_2d_m', 'pathloss_db',
          'penetration_db', 'shadowing_db', 'total_loss_db', 'gnb_gain_dbi', 'gnb_sector_bearing_deg',
          'ue_gain_db', 'bf_loss_db', 'antenna_gain_db', 'tx_power_dbm', 'bandwidth_hz',
          'rx_power_dbm', 'noise_dbm', 'snr_db', 'ul_snr_1prb_db', 'n_prb_used', 'se_bps_hz',
          'rate_bps', 'feasible_F1_link_closure', 'feasible_F2_m2410_equivalent']


K6 = 'K6_7p2_both_without_3p5_both'
PARTITION = ('K1_both_carriers_both_directions', 'K2_7p2_dl_only_with_3p5_ul', 'K3_7p2_dl_without_any_ul',
             'K4_7p2_dl_not_feasible', K6)


def classes14(res, f1, f2, profile):
    """E1.3 classes K1-K5 plus K6 (amendment 1): 7.2 GHz DL and UL feasible while
    3.5 GHz is not feasible in both directions. K1, K2, K3, K4, K6 partition all
    outcomes; K6 can occur only when 7.2 GHz is the better carrier on a link."""
    k = e13.classes(res, f1, f2, profile)
    d35, u35 = res[f1]['DL'][profile], res[f1]['UL'][profile]
    d72, u72 = res[f2]['DL'][profile], res[f2]['UL'][profile]
    k[K6] = d72 & u72 & ~(d35 & u35)
    return k


def tw_independent(model, fc_hz):
    """Re-typed TR 38.901 Table 7.4.3-2 (independent of cmwave.radio.o2i), for N5/N6."""
    f = fc_hz / 1e9
    if model == 'low':
        s = 0.3 * 10 ** (-(2 + 0.2 * f) / 10) + 0.7 * 10 ** (-(5 + 4 * f) / 10)
    else:
        s = 0.7 * 10 ** (-(23 + 0.3 * f) / 10) + 0.3 * 10 ** (-(5 + 4 * f) / 10)
    return 5 - 10 * math.log10(s)


def fcol(a):
    return [repr(round(x, 9)) for x in np.asarray(a, dtype=float).tolist()]


def panel_of(c, case):
    p = dict(c['gnb_panel'])
    return ant.GnbPanel(rows=p.get('rows', 1), cols=p.get('cols', 1),
                        subarray_rows=p.get('subarray_rows', 1), etilt_deg=p.get('etilt_deg', 0.0),
                        sector_bearings_deg=tuple(case['sector_bearings_deg']),
                        isotropic=bool(p.get('isotropic', False)))


def load_inputs(args):
    gdir = Path(args.geometry_dir)
    gman = json.loads((gdir / 'site_tx0_manifest.json').read_text(encoding='utf-8'))
    links_path = gdir / 'site_tx0_links.csv'
    if not gman['all_checks_passed'] or e13.sha256(links_path) != gman['files']['site_tx0_links.csv']:
        raise SystemExit('E1.1 geometry not validated or links file changed')
    with links_path.open(encoding='utf-8') as fh:
        links = list(csv.DictReader(fh))
    pdir = Path(args.propagation_dir)
    pman = json.loads((pdir / 'manifest.json').read_text(encoding='utf-8'))
    real_path = pdir / 'realizations.csv.gz'
    if (not pman['all_checks_passed'] or e13.sha256(real_path) != pman['realizations_sha256']
            or pman['links_csv_sha256'] != gman['files']['site_tx0_links.csv']):
        raise SystemExit('E1.2 propagation not validated, changed, or built on other geometry')
    with gzip.open(real_path, 'rt', encoding='utf-8') as fh:
        prop = list(csv.DictReader(fh))
    e3dir = Path(args.e13_dir)
    e3man = json.loads((e3dir / 'manifest.json').read_text(encoding='utf-8'))
    e3rec = e3dir / 'records.csv.gz'
    if (not e3man['all_checks_passed'] or e13.sha256(e3rec) != e3man['outputs_sha256']['records.csv.gz']
            or e3man['realizations_sha256'] != pman['realizations_sha256']):
        raise SystemExit('E1.3 reference not validated, changed, or built on other propagation')
    return gman, links, pman, prop, e3man, e3rec, e3dir


def build_states(conf, gman, links, prop):
    """Per-state losses, components and UE directions in the E1.2 key order."""
    freqs = sorted({float(r['frequency_hz']) for r in prop})
    keys = sorted({(int(r['realization']), r['ue_id']) for r in prop})
    n = len(keys)
    jidx = {l['ue_id']: j for j, l in enumerate(links)}
    n_real = len({k[0] for k in keys})
    if n != n_real * len(links) or set(jidx) != {k[1] for k in keys}:
        raise SystemExit('E1.2 keys do not cover realizations x E1.1 links')
    idx = {k: i for i, k in enumerate(keys)}
    pl_out = {f: np.full(n, np.nan) for f in freqs}
    loss_out = {f: np.full(n, np.nan) for f in freqs}
    sf_out = np.full(n, np.nan)
    for r in prop:
        i = idx[(int(r['realization']), r['ue_id'])]
        f = float(r['frequency_hz'])
        pl_out[f][i] = float(r['pathloss_db'])
        loss_out[f][i] = float(r['total_loss_db'])
        sf_out[i] = float(r['shadowing_db'])
    assert all(np.isfinite(pl_out[f]).all() and np.isfinite(loss_out[f]).all() for f in freqs)
    rr = np.array([k[0] for k in keys])
    jj = np.array([jidx[k[1]] for k in keys])
    L = [links[j] for j in jj]
    los = np.array([l['los_state'] for l in L])
    x = np.array([float(l['x_m']) for l in L])
    y = np.array([float(l['y_m']) for l in L])
    z = np.array([float(l['z_m']) for l in L])
    d2_out = np.array([float(l['d2d_m']) for l in L])
    h_bs = np.array([float(l['h_bs_m']) for l in L])
    h_ut = np.array([float(l['h_ut_m']) for l in L])
    g = np.array(gman['gnb_position_m'], dtype=float)
    dz = z - g[2]
    if np.max(np.abs(-dz - (h_bs - h_ut))) > 1e-9:
        raise SystemExit('scene height difference != h_bs - h_ut')
    zen_out, az = ant.link_angles(g, np.column_stack([x, y, z]))

    rnd = conf['random']
    rng = np.random.default_rng(rnd['seed'])
    shape = (n_real, len(links))
    draws = {}
    for name in rnd['draw_order']:
        draws[name] = rng.random(shape) if name in ('u1', 'u2') else rng.standard_normal(shape)
    v = {name: a[rr, jj] for name, a in draws.items()}
    d_in = o2i.indoor_distance_m(v['u1'], v['u2'])
    d2_in_tot = d2_out + d_in
    d3_in_tot = np.hypot(d2_in_tot, h_bs - h_ut)
    zen_in = np.degrees(np.arctan2(d2_in_tot, dz))
    pl_in_b = {f: np.array([uh.deterministic_pathloss_db(a, b, f, hb, hu, s) for a, b, hb, hu, s
                            in zip(d2_in_tot, d3_in_tot, h_bs, h_ut, los)]) for f in freqs}

    states = {}
    zeros = np.zeros(n)
    states['outdoor'] = {'pl_b': pl_out, 'pen': {f: zeros for f in freqs}, 'sf': {f: sf_out for f in freqs},
                         'd_in': zeros, 'd2': d2_out, 'zenith': zen_out}
    car = o2i.CAR_MU_DB + o2i.CAR_SIGMA_DB * v['x_car']
    states['in_car'] = {'pl_b': pl_out, 'pen': {f: car for f in freqs}, 'sf': {f: sf_out for f in freqs},
                        'd_in': zeros, 'd2': d2_out, 'zenith': zen_out}
    tw_used = {}
    for state, model in BUILDING.items():
        indep = state.endswith('_indep')
        sig = o2i.sigma_p_db(model)
        pen, sf = {}, {}
        for k, f in enumerate(freqs):
            xp = v['x_p_2'] if (indep and k == 1) else v['x_p']
            xs = v['x_sf_2'] if (indep and k == 1) else v['x_sf']
            tw_used[(model, f)] = o2i.wall_loss_db(model, f)
            pen[f] = tw_used[(model, f)] + o2i.indoor_loss_db(d_in) + sig * xp
            sf[f] = o2i.O2I_SF_STD_DB * xs
        states[state] = {'pl_b': pl_in_b, 'pen': pen, 'sf': sf, 'd_in': d_in, 'd2': d2_in_tot,
                         'zenith': zen_in}
    for s in states.values():
        s['loss'] = {f: s['pl_b'][f] + s['pen'][f] + s['sf'][f] for f in freqs}
        s['azimuth'] = az
    # E1.2 outdoor losses must be reproduced exactly by the component sum.
    for f in freqs:
        if np.max(np.abs(states['outdoor']['loss'][f] - loss_out[f])) > 1e-9:
            raise SystemExit('outdoor component sum != E1.2 total loss')
    geo = {'keys': keys, 'freqs': freqs, 'los': los, 'd2_out': d2_out, 'rr': rr, 'jj': jj,
           'h_bs': h_bs, 'h_ut': h_ut, 'gnb': g, 'xy': np.column_stack([x, y]), 'dz': dz,
           'az': az}
    return states, geo, draws, v, tw_used


def evaluate(case, states, geo, thresholds):
    freqs = geo['freqs']
    carriers = sorted(case['carriers'], key=lambda c: c['frequency_hz'])
    if [c['frequency_hz'] for c in carriers] != freqs:
        raise SystemExit('case carriers differ from the propagation carriers')
    t1, t2 = thresholds['F1_link_closure'], thresholds['F2_m2410_equivalent']
    nlos = (geo['los'] == 'NLOS').astype(float)
    out = {}
    for state in STATES:
        st = states[state]
        res = {}
        for c in carriers:
            cfg = lb.CarrierConfig(**{k: c[k] for k in CFG_FIELDS})
            panel = panel_of(c, case)
            f = cfg.frequency_hz
            g_gnb, sector = ant.gnb_gain_dbi(panel, st['zenith'], st['azimuth'])
            bf = case['nlos_bf_loss_db'] * nlos
            g_ue = ant.ue_rx_gain_db(c['ue_n_rx'])
            g_dl = g_gnb - bf + g_ue
            g_ul = g_gnb - bf
            dl = lb.downlink(st['loss'][f], cfg, gain_db=g_dl)
            ul = lb.uplink(st['loss'][f], cfg, gain_db=g_ul)
            dl['F1'] = dl['snr_db'] >= t1['DL_snr_db_min']
            ul['F1'] = ul['snr_1prb_db'] >= t1['UL_snr_1prb_db_min']
            dl['F2'] = dl['se_bps_hz'] >= t2['DL_se_min']
            ul['F2'] = ul['se_norm_bps_hz'] >= t2['UL_se_norm_min']
            res[f] = {'cfg': cfg, 'panel': panel, 'n_rx': c['ue_n_rx'], 'g_gnb': g_gnb,
                      'sector': np.array(panel.sector_bearings_deg)[sector] if not panel.isotropic
                      else np.zeros(sector.shape), 'g_ue': g_ue, 'bf': bf, 'g_dl': g_dl, 'g_ul': g_ul,
                      'DL': dl, 'UL': ul}
        out[state] = res
    return out


def write_block(fh, cname, regime, state, st, res, geo, site):
    keys, freqs = geo['keys'], geo['freqs']
    n = len(keys)
    common = {'ue': [k[1] for k in keys], 'real': [str(k[0]) for k in keys], 'los': list(geo['los']),
              'd2o': fcol(geo['d2_out']), 'din': fcol(st['d_in']), 'd2': fcol(st['d2'])}
    cols = {}
    for f in freqs:
        r = res[f]
        cfg = r['cfg']
        base = {'pl': fcol(st['pl_b'][f]), 'pen': fcol(st['pen'][f]), 'sf': fcol(st['sf'][f]),
                'loss': fcol(st['loss'][f]), 'ggnb': fcol(r['g_gnb']), 'sec': fcol(r['sector']),
                'gue': repr(round(r['g_ue'], 9)), 'bf': fcol(r['bf'])}
        dl, ul = r['DL'], r['UL']
        cols[(f, 'DL')] = dict(base, gant=fcol(dl['antenna_gain_db']), tx=repr(round(cfg.gnb_power_dbm, 9)),
                               bw=fcol(dl['bandwidth_hz']), rx=fcol(dl['rx_power_dbm']),
                               noise=fcol(dl['noise_dbm']), snr=fcol(dl['snr_db']), snr1=[''] * n,
                               nprb=[str(cfg.n_prb)] * n, se=fcol(dl['se_bps_hz']), rate=fcol(dl['rate_bps']),
                               F1=[str(bool(b)) for b in dl['F1']], F2=[str(bool(b)) for b in dl['F2']])
        cols[(f, 'UL')] = dict(base, gant=fcol(ul['antenna_gain_db']), tx=repr(round(cfg.ue_power_dbm, 9)),
                               bw=fcol(ul['eval_bw_hz']), rx=fcol(ul['rx_power_dbm']),
                               noise=fcol(ul['noise_eval_dbm']), snr=fcol(ul['snr_best_db']),
                               snr1=fcol(ul['snr_1prb_db']), nprb=[str(int(b)) for b in ul['best_n_prb']],
                               se=fcol(ul['se_norm_bps_hz']), rate=fcol(ul['rate_bps']),
                               F1=[str(bool(b)) for b in ul['F1']], F2=[str(bool(b)) for b in ul['F2']])
    lines = []
    for i in range(n):
        for f in freqs:
            for d in ('DL', 'UL'):
                c = cols[(f, d)]
                lines.append(','.join((
                    cname, regime, state, site, common['ue'][i], common['real'][i], repr(round(f, 9)), d,
                    common['los'][i], common['d2o'][i], common['din'][i], common['d2'][i], c['pl'][i],
                    c['pen'][i], c['sf'][i], c['loss'][i], c['ggnb'][i], c['sec'][i], c['gue'], c['bf'][i],
                    c['gant'][i], c['tx'], c['bw'][i], c['rx'][i], c['noise'][i], c['snr'][i], c['snr1'][i],
                    c['nprb'][i], c['se'][i], c['rate'][i], c['F1'][i], c['F2'][i])))
    fh.write('\n'.join(lines) + '\n')
    return len(lines)


def median_rows(cname, case, conf, geo, links, bins, thresholds):
    """Budgets with every random variate at its median (normals 0, d2D-in median, car 9 dB)."""
    freqs = geo['freqs']
    J = len(links)
    los = np.array([l['los_state'] for l in links])
    d2o = np.array([float(l['d2d_m']) for l in links])
    d3o = np.array([float(l['d3d_m']) for l in links])
    hb = np.array([float(l['h_bs_m']) for l in links])
    hu = np.array([float(l['h_ut_m']) for l in links])
    xyz = np.array([[float(l['x_m']), float(l['y_m']), float(l['z_m'])] for l in links])
    zen_o, az = ant.link_angles(geo['gnb'], xyz)
    d_med = o2i.D2D_IN_MAX_M * (1 - 1 / math.sqrt(2))
    d2i = d2o + d_med
    zen_i = np.degrees(np.arctan2(d2i, xyz[:, 2] - geo['gnb'][2]))
    rows = []
    for state in SHARED_STATES:
        st = {'zenith': zen_o if state in ('outdoor', 'in_car') else zen_i, 'azimuth': az}
        st['loss'] = {}
        for f in freqs:
            if state in ('outdoor', 'in_car'):
                pl = np.array([uh.deterministic_pathloss_db(a, b, f, x1, x2, s)
                               for a, b, x1, x2, s in zip(d2o, d3o, hb, hu, los)])
                st['loss'][f] = pl + (o2i.CAR_MU_DB if state == 'in_car' else 0.0)
            else:
                pl = np.array([uh.deterministic_pathloss_db(a, math.hypot(a, x1 - x2), f, x1, x2, s)
                               for a, x1, x2, s in zip(d2i, hb, hu, los)])
                st['loss'][f] = (pl + o2i.wall_loss_db(BUILDING[state], f)
                                 + float(o2i.indoor_loss_db(d_med)))
        g = {'keys': [(0, l['ue_id']) for l in links], 'freqs': freqs, 'los': los}
        res = evaluate(case, {s: st for s in STATES}, g, thresholds)[state]
        for j, l in enumerate(links):
            row = {'case': cname, 'state': state, 'ue_id': l['ue_id'], 'los_state': los[j],
                   'distance_2d_out_m': d2o[j], 'distance_bin_m': bins[l['ue_id']],
                   'd2d_in_m': 0.0 if state in ('outdoor', 'in_car') else d_med}
            for f, tag in zip(freqs, ('3p5', '7p2')):
                r = res[f]
                row[f'total_loss_db_{tag}'] = st['loss'][f][j]
                row[f'gnb_gain_dbi_{tag}'] = r['g_gnb'][j]
                row[f'dl_snr_db_{tag}'] = r['DL']['snr_db'][j]
                row[f'ul_snr_1prb_db_{tag}'] = r['UL']['snr_1prb_db'][j]
                row[f'ul_snr_full_db_{tag}'] = r['UL']['snr_full_db'][j]
                row[f'dl_rate_mbps_{tag}'] = r['DL']['rate_bps'][j] / 1e6
                row[f'ul_rate_mbps_{tag}'] = r['UL']['rate_bps'][j] / 1e6
                row[f'ul_best_n_prb_{tag}'] = int(r['UL']['best_n_prb'][j])
                for p in ('F1', 'F2'):
                    row[f'{p}_dl_{tag}'] = bool(r['DL'][p][j])
                    row[f'{p}_ul_{tag}'] = bool(r['UL'][p][j])
            rows.append({k: e13.fmt(v) for k, v in row.items()})
    return rows


def run_checks(cname, case, out, states, geo, tw_used, e13_ref, e13_summary):
    freqs = geo['freqs']
    f1, f2 = freqs
    c = {}
    # N1 identities
    n1 = 0.0
    for state in STATES:
        for f in freqs:
            r = out[state][f]
            cfg, dl, ul, loss = r['cfg'], r['DL'], r['UL'], states[state]['loss'][f]
            n1 = max(n1, np.max(np.abs(dl['rx_power_dbm'] - (cfg.gnb_power_dbm + r['g_dl'] - loss))))
            n1 = max(n1, np.max(np.abs(dl['noise_dbm'] - (-174 + 10 * np.log10(cfg.occupied_bw_hz) + cfg.nf_ue_db))))
            n1 = max(n1, np.max(np.abs(dl['snr_db'] - (dl['rx_power_dbm'] - dl['noise_dbm']))))
            n1 = max(n1, np.max(np.abs(ul['rx_power_dbm'] - (cfg.ue_power_dbm + r['g_ul'] - loss))))
            n1 = max(n1, np.max(np.abs(ul['noise_eval_dbm'] - (-174 + 10 * np.log10(ul['eval_bw_hz']) + cfg.nf_gnb_db))))
            n1 = max(n1, np.max(np.abs(ul['snr_best_db'] - (ul['rx_power_dbm'] - ul['noise_eval_dbm']))))
            n1 = max(n1, np.max(np.abs(ul['snr_1prb_db'] - (ul['rx_power_dbm'] - (
                -174 + 10 * np.log10(cfg.prb_bw_hz) + cfg.nf_gnb_db)))))
            n1 = max(n1, np.max(np.abs(dl['antenna_gain_db'] - r['g_dl'])),
                     np.max(np.abs(ul['antenna_gain_db'] - r['g_ul'])))
    c['N1_identities'] = {'max_abs_error_db': float(n1), 'passed': bool(n1 <= TOL)}
    # N2 reciprocity
    n2 = 0.0
    for state in STATES:
        for f in freqs:
            r = out[state][f]
            l_dl = r['cfg'].gnb_power_dbm + r['g_dl'] - r['DL']['rx_power_dbm']
            l_ul = r['cfg'].ue_power_dbm + r['g_ul'] - r['UL']['rx_power_dbm']
            n2 = max(n2, np.max(np.abs(l_dl - l_ul)), np.max(np.abs((r['g_dl'] - r['g_ue']) - r['g_ul'])))
    c['N2_reciprocity'] = {'max_abs_error_db': float(n2), 'passed': bool(n2 <= TOL)}
    # N3 E1.3 reproduction (reference case only)
    if cname == 'R0_isotropic_e13':
        e_snr, e_rate, flag_mismatch, n_cmp = 0.0, 0.0, 0, 0
        for i, (real, ue) in enumerate(geo['keys']):
            for f in freqs:
                r = out['outdoor'][f]
                for d in ('DL', 'UL'):
                    ref = e13_ref[(ue, real, f, d)]
                    x = r[d]
                    snr = x['snr_db'][i] if d == 'DL' else x['snr_best_db'][i]
                    e_snr = max(e_snr, abs(snr - ref['snr_db']))
                    if d == 'UL':
                        e_snr = max(e_snr, abs(x['snr_1prb_db'][i] - ref['ul_snr_1prb_db']))
                    e_rate = max(e_rate, abs(x['rate_bps'][i] - ref['rate_bps']))
                    flag_mismatch += int(bool(x['F1'][i]) != ref['F1']) + int(bool(x['F2'][i]) != ref['F2'])
                    n_cmp += 1
        cls_equal = True
        for p in ('F1', 'F2'):
            k = e13.classes(out['outdoor'], f1, f2, p)
            ref = e13_summary['main_eqbw20']['per_profile'][p]['class_fraction']
            cls_equal &= all(float(np.mean(v)) == ref[name] for name, v in k.items())
        c['N3_e13_reproduction'] = {'records_compared': n_cmp, 'max_abs_snr_error_db': float(e_snr),
                                    'max_abs_rate_error_bps': float(e_rate), 'flag_mismatches': flag_mismatch,
                                    'class_fractions_identical': bool(cls_equal),
                                    'passed': bool(n_cmp == 4 * len(geo['keys']) and e_snr <= TOL
                                                   and e_rate <= 1e-6 and flag_mismatch == 0 and cls_equal)}
    # N4 gain bounds and serving sector
    ok4, worst = True, -np.inf
    for state in STATES:
        for f in freqs:
            r = out[state][f]
            g, nom = r['g_gnb'], r['panel'].nominal_gain_dbi
            ok4 &= bool(np.all(np.isfinite(g)) and np.all(g <= nom + 1e-12))
            worst = max(worst, float(np.max(g - nom)))
        ok4 &= bool(np.array_equal(out[state][f1]['sector'], out[state][f2]['sector']))
    c['N4_gain_bounds'] = {'max_gain_minus_nominal_db': worst, 'passed': bool(ok4)}
    # N5 analytic carrier offsets
    a, b = out['outdoor'][f1], out['outdoor'][f2]
    ca, cb = a['cfg'], b['cfg']
    gap = 20 * math.log10(f2 / f1)
    d_gnom = a['panel'].nominal_gain_dbi - b['panel'].nominal_gain_dbi
    dl_base = ((ca.gnb_power_dbm - cb.gnb_power_dbm) - 10 * math.log10(ca.occupied_bw_hz / cb.occupied_bw_hz)
               - (ca.nf_ue_db - cb.nf_ue_db) + d_gnom + 10 * math.log10(a['n_rx'] / b['n_rx']) + gap)
    ul_base = ((ca.ue_power_dbm - cb.ue_power_dbm) - 10 * math.log10(ca.prb_bw_hz / cb.prb_bw_hz)
               - (ca.nf_gnb_db - cb.nf_gnb_db) + d_gnom + gap)
    n5 = {}
    for state in SHARED_STATES:
        dtw = (tw_independent(BUILDING[state], f2) - tw_independent(BUILDING[state], f1)
               if state in BUILDING else 0.0)
        r1, r2 = out[state][f1], out[state][f2]
        e_dl = float(np.max(np.abs(r1['DL']['snr_db'] - r2['DL']['snr_db'] - (dl_base + dtw))))
        e_ul = float(np.max(np.abs(r1['UL']['snr_1prb_db'] - r2['UL']['snr_1prb_db'] - (ul_base + dtw))))
        n5[state] = {'expected_dl_db': dl_base + dtw, 'expected_ul_1prb_db': ul_base + dtw,
                     'max_abs_error_db': max(e_dl, e_ul)}
    c['N5_carrier_offsets'] = {'per_state': n5, 'passed': all(v['max_abs_error_db'] <= TOL for v in n5.values())}
    # N6 O2I components
    e6 = max(abs(tw_used[(m, f)] - tw_independent(m, f)) for m in ('low', 'high') for f in freqs)
    ok6 = e6 <= TOL
    for state, model in BUILDING.items():
        st = states[state]
        ok6 &= bool(np.all((st['d_in'] >= 0) & (st['d_in'] <= o2i.D2D_IN_MAX_M)))
        resid = {f: st['pen'][f] - tw_used[(model, f)] - 0.5 * st['d_in'] for f in freqs}
        same = np.max(np.abs(resid[f1] - resid[f2])) <= TOL and np.max(np.abs(st['sf'][f1] - st['sf'][f2])) <= TOL
        ok6 &= bool(same) if not state.endswith('_indep') else bool(not same)
    c['N6_o2i_components'] = {'max_abs_wall_loss_error_db': float(e6), 'passed': bool(ok6)}
    # N8 partition, amendment 1 (mix weights are checked once, globally): K1, K2,
    # K3, K4, K6 mutually exclusive and exhaustive in every state; K6 = 0 in the
    # shared-variate states, implied by the non-negative carrier offsets (N5).
    part, k6_shared, k6_indep = True, 0, 0
    for state in STATES:
        for p in ('F1', 'F2'):
            k = classes14(out[state], f1, f2, p)
            part &= bool(np.all(np.sum([k[name] for name in PARTITION], axis=0) == 1))
            if state in SHARED_STATES:
                k6_shared += int(np.sum(k[K6]))
            else:
                k6_indep += int(np.sum(k[K6]))
    c['N8_partition'] = {'exclusive_and_exhaustive': part, 'k6_outcomes_shared_states': k6_shared,
                         'k6_outcomes_independent_states': k6_indep, 'passed': bool(part and k6_shared == 0)}
    # N10 uplink allocation
    ok10 = all(np.all(out[s][f]['UL']['rate_bps'] >= out[s][f]['UL']['rate_1prb_bps'] - 1e-6)
               and np.all(out[s][f]['UL']['rate_bps'] >= out[s][f]['UL']['rate_full_bps'] - 1e-6)
               for s in STATES for f in freqs)
    c['N10_ul_allocation'] = {'passed': bool(ok10)}
    return c


def draw_checks(conf, draws):
    n = draws['u1'].size
    din = o2i.indoor_distance_m(draws['u1'], draws['u2']).ravel()
    mu, sd = 25 / 3, 25 * math.sqrt(1 / 18)
    res = {'d2d_in_m': {'mean': float(din.mean()), 'std': float(din.std(ddof=1)),
                        'passed': bool(abs(din.mean() - mu) <= 4 * sd / math.sqrt(n)
                                       and abs(din.std(ddof=1) / sd - 1) <= 4 / math.sqrt(2 * n))}}
    for name in conf['random']['draw_order']:
        if name in ('u1', 'u2'):
            continue
        x = draws[name].ravel()
        res[name] = {'mean': float(x.mean()), 'std': float(x.std(ddof=1)),
                     'passed': bool(abs(x.mean()) <= 4 / math.sqrt(n)
                                    and abs(x.std(ddof=1) - 1) <= 4 / math.sqrt(2 * n))}
    return {'n_per_variate': n, 'per_variate': res, 'passed': all(v['passed'] for v in res.values())}


def summarise(cname, case, out, geo, bins, mixes):
    freqs = geo['freqs']
    f1, f2 = freqs
    ue_of = np.array([k[1] for k in geo['keys']])
    blist = sorted(set(bins.values()), key=lambda t: float(t.split('-')[0]))
    s = {'regime': case['regime'], 'description': case['description'], 'carriers': {}, 'per_state': {},
         'mixes': {}}
    for f in freqs:
        r = out['outdoor'][f]
        cfg, panel = r['cfg'], r['panel']
        eirp = cfg.gnb_power_dbm + panel.nominal_gain_dbi
        g = r['g_gnb']
        off = r['DL']['snr_db'] - r['UL']['snr_full_db']
        s['carriers'][f'{f/1e9:.1f}GHz'] = {
            'gnb_array': 'isotropic' if panel.isotropic else f'{panel.rows}x{panel.cols} per polarisation',
            'subarray_rows': panel.subarray_rows, 'etilt_deg': panel.etilt_deg,
            'nominal_gnb_gain_dbi': panel.nominal_gain_dbi, 'ue_n_rx': r['n_rx'], 'ue_rx_gain_db': r['g_ue'],
            'channel_bw_hz': cfg.channel_bw_hz, 'n_prb': cfg.n_prb, 'gnb_power_dbm': cfg.gnb_power_dbm,
            'gnb_eirp_dbm_per_channel': eirp,
            'gnb_eirp_dbm_per_100mhz': eirp - 10 * math.log10(cfg.channel_bw_hz / 100e6),
            'outdoor_gnb_gain_dbi': {'min': float(g.min()), 'median': float(np.median(g)), 'max': float(g.max())},
            'outdoor_dl_minus_ul_fullband_snr_db': {'value': float(np.median(off)), 'spread': float(np.ptp(off))}}
    for state in STATES:
        st = {}
        for p in ('F1', 'F2'):
            k = classes14(out[state], f1, f2, p)
            prof = {'feasible_fraction': {f'{d}_{f/1e9:.1f}GHz': float(np.mean(out[state][f][d][p]))
                                          for f in freqs for d in ('DL', 'UL')},
                    'class_fraction': {name: float(np.mean(v)) for name, v in k.items()},
                    'by_distance_bin': {}}
            for b in blist:
                m = np.array([bins[u] == b for u in ue_of])
                prof['by_distance_bin'][b] = {name: float(np.mean(v[m])) for name, v in k.items()}
                prof['by_distance_bin'][b]['n_link_realizations'] = int(m.sum())
            st[p] = prof
        s['per_state'][state] = st
    for mname, mix in mixes.items():
        w = mix['weights']
        mm = {}
        for p in ('F1', 'F2'):
            ps = {st: s['per_state'][st][p] for st in w}
            mm[p] = {'feasible_fraction': {key: sum(w[st] * ps[st]['feasible_fraction'][key] for st in w)
                                           for key in ps[next(iter(w))]['feasible_fraction']},
                     'class_fraction': {key: sum(w[st] * ps[st]['class_fraction'][key] for st in w)
                                        for key in ps[next(iter(w))]['class_fraction']},
                     'by_distance_bin': {b: {key: sum(w[st] * ps[st]['by_distance_bin'][b][key] for st in w)
                                             for key in ps[next(iter(w))]['class_fraction']} for b in blist}}
        s['mixes'][mname] = mm
    return s


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--config', default=str(ROOT / 'configs/e1/e1_4_antennas_o2i.json'))
    ap.add_argument('--geometry-dir', default=str(ROOT / 'results/phase2/e1_geometry'))
    ap.add_argument('--propagation-dir', default=str(ROOT / 'results/phase2/e1_3gpp_propagation'))
    ap.add_argument('--e13-dir', default=str(ROOT / 'results/phase2/e1_3_link_budget'))
    ap.add_argument('--outdir', default=str(ROOT / 'results/phase2/e1_4_antennas_o2i'))
    ap.add_argument('--git-head', default=None, help='commit id of the code being run')
    args = ap.parse_args()
    outdir = Path(args.outdir)
    (outdir / 'raw').mkdir(parents=True, exist_ok=True)
    conf = json.loads(Path(args.config).read_text(encoding='utf-8'))
    gman, links, pman, prop, e3man, e3rec, e3dir = load_inputs(args)
    with Path(args.geometry_dir, 'site_tx0_links.csv').open(encoding='utf-8') as fh:
        bins = {r['ue_id']: r['distance_bin_m'] for r in csv.DictReader(fh)}
    site = links[0]['site_id']

    e13_ref = {}
    with gzip.open(e3rec, 'rt', encoding='utf-8') as fh:
        for r in csv.DictReader(fh):
            if r['case'] != 'main_eqbw20':
                continue
            e13_ref[(r['ue_id'], int(r['realization']), float(r['frequency_hz']), r['direction'])] = {
                'snr_db': float(r['snr_db']), 'rate_bps': float(r['rate_bps']),
                'ul_snr_1prb_db': float(r['ul_snr_1prb_db']) if r['ul_snr_1prb_db'] else None,
                'F1': r['feasible_F1_link_closure'] == 'True', 'F2': r['feasible_F2_m2410_equivalent'] == 'True'}
    e13_summary = json.loads((e3dir / 'summary.json').read_text(encoding='utf-8'))

    states, geo, draws, v, tw_used = build_states(conf, gman, links, prop)
    mixes = conf['mixes']
    mix_ok = all(all(w >= 0 for w in m['weights'].values()) and abs(sum(m['weights'].values()) - 1) <= 1e-12
                 and set(m['weights']) <= set(SHARED_STATES) for m in mixes.values())

    rec_path = outdir / 'raw' / 'records.csv.gz'
    checks, summary, med_rows, n_records = {}, {}, [], 0
    with open(rec_path, 'wb') as raw, \
            gzip.GzipFile(filename='', mode='wb', fileobj=raw, mtime=0, compresslevel=6) as gz, \
            io.TextIOWrapper(gz, encoding='utf-8', newline='') as fh:
        fh.write(','.join(FIELDS) + '\n')
        for cname, case in conf['cases'].items():
            out = evaluate(case, states, geo, conf['thresholds'])
            for state in STATES:
                n_records += write_block(fh, cname, case['regime'], state, states[state], out[state], geo, site)
            checks[cname] = run_checks(cname, case, out, states, geo, tw_used, e13_ref, e13_summary)
            summary[cname] = summarise(cname, case, out, geo, bins, mixes)
            med_rows += median_rows(cname, case, conf, geo, links, bins, conf['thresholds'])
            print(f'{cname}: ' + ', '.join(f"{k} {'PASS' if x['passed'] else 'FAIL'}" for k, x in checks[cname].items()),
                  flush=True)

    grid = np.linspace(-40, 60, 10001)
    n9 = all(np.all(np.diff(lb.mapped_se(grid, d)) >= 0) and np.all(lb.mapped_se(grid, d) <= lb.shannon_se(grid) + 1e-12)
             and np.all(lb.mapped_se(grid, d)[grid < -10] == 0) for d in ('DL', 'UL'))
    global_checks = {'N7_draw_statistics': draw_checks(conf, draws),
                     'N8_mix_weights': {'passed': bool(mix_ok)},
                     'N9_mapping': {'passed': bool(n9)},
                     'N3_present': {'passed': 'N3_e13_reproduction' in checks.get('R0_isotropic_e13', {})}}
    with (outdir / 'links_median.csv').open('w', newline='', encoding='utf-8') as fh:
        w = csv.DictWriter(fh, fieldnames=list(med_rows[0].keys()), lineterminator='\n')
        w.writeheader()
        w.writerows(med_rows)
    passed = (all(x['passed'] for c in checks.values() for x in c.values())
              and all(x['passed'] for x in global_checks.values()))
    (outdir / 'validation.json').write_text(json.dumps({'per_case': checks, 'global': global_checks,
                                                        'all_checks_passed': passed}, indent=2), encoding='utf-8')
    (outdir / 'summary.json').write_text(json.dumps(summary, indent=2), encoding='utf-8')
    manifest = {
        'generated_utc': datetime.now(timezone.utc).isoformat(), 'tool': 'scripts/e1_antennas_o2i.py',
        'git_head_at_run': args.git_head or e13.git_head(), 'python': platform.python_version(),
        'numpy': np.__version__, 'config': str(Path(args.config).resolve().relative_to(ROOT)),
        'config_sha256': e13.sha256(args.config),
        'inputs_sha256': {'e1_geometry_manifest': e13.sha256(Path(args.geometry_dir, 'site_tx0_manifest.json')),
                          'e1_geometry_links': gman['files']['site_tx0_links.csv'],
                          'e1_2_manifest': e13.sha256(Path(args.propagation_dir, 'manifest.json')),
                          'e1_2_realizations': pman['realizations_sha256'],
                          'e1_3_manifest': e13.sha256(e3dir / 'manifest.json'),
                          'e1_3_records': e3man['outputs_sha256']['records.csv.gz']},
        'seed': conf['random']['seed'],
        'outputs_sha256': {'raw/records.csv.gz': e13.sha256(rec_path),
                           'links_median.csv': e13.sha256(outdir / 'links_median.csv'),
                           'summary.json': e13.sha256(outdir / 'summary.json')},
        'n_records': n_records, 'all_checks_passed': passed}
    (outdir / 'manifest.json').write_text(json.dumps(manifest, indent=2), encoding='utf-8')

    print('global: ' + ', '.join(f"{k} {'PASS' if x['passed'] else 'FAIL'}" for k, x in global_checks.items()))
    for cname, s in summary.items():
        for state in ('outdoor', 'in_car', 'indoor_low', 'indoor_high'):
            k = s['per_state'][state]['F2']['class_fraction']
            print(f"{cname:20s} {state:12s} F2: " + ' '.join(f"{n.split('_')[0]}={x:.3f}" for n, x in k.items()))
        k = s['mixes']['M2412']['F2']['class_fraction']
        print(f"{cname:20s} {'MIX M2412':12s} F2: " + ' '.join(f"{n.split('_')[0]}={x:.3f}" for n, x in k.items()))
    print(f'records: {n_records}')
    print('E1.4 ANTENNAS + O2I', 'PASSED' if passed else 'FAILED')
    sys.exit(0 if passed else 1)


if __name__ == '__main__':
    main()
