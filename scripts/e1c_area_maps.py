#!/usr/bin/env python3
"""E1c.1: single-site area-weighted feasibility maps with the E1.5 model (exact engine).

Implements configs/e1/e1c_area_maps.json. Run inside WSL with the project venv (scipy).
Outputs in --outdir: summary.json, validation.json, manifest.json; raw/maps.npz (git-ignored).
"""
import argparse
import csv
import hashlib
import json
import math
import platform
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
from scipy import special, stats

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'scripts'))
import e1_antennas_o2i as e14  # noqa: E402
import e1_5_5_reclassification as e155  # noqa: E402
from cmwave.radio import antenna as ant  # noqa: E402
from cmwave.radio import link_budget as lb  # noqa: E402
from cmwave.radio import o2i  # noqa: E402
from cmwave.radio import pathloss_3gpp as pl3  # noqa: E402
from cmwave.radio import uma_hybrid as uh  # noqa: E402

CONFIG = ROOT / 'configs/e1/e1c_area_maps.json'
R = ROOT / 'results/phase2'
E11_DIR, GEO_DIR, OUTDIR = R / 'e1_geometry', R / 'e1c_geometry', R / 'e1c_area_maps'
BINS = (20, 50, 100, 200, 300, 450, 600)
STATES = ('outdoor', 'in_car', 'indoor_low', 'indoor_high')
PARTITION = list(e14.PARTITION)
K5 = 'K5_7p2_ul_without_dl'
DIRS = ('DL', 'UL')
PROFILES = ('F1', 'F2', 'F3')
F2T = {'DL': 2.25, 'UL': 1.5}
F3T = {'DL': 0.225, 'UL': 0.15}
K_USERS = 10
EPS = 1e-6
GL_STOCH, GL_C3 = 24, 64
VARIANTS = {
    'primary': {'mapping': 'camc_t2', 'gain': 'cb', 'o2i': '19.5.0'},
    'v16': {'mapping': 'camc_t2', 'gain': 'cb', 'o2i': '16.1.0'},
    'g_dp': {'mapping': 'camc_t2', 'gain': 'dp', 'o2i': '19.5.0'},
    'g_eig': {'mapping': 'camc_t2', 'gain': 'eig', 'o2i': '19.5.0'},
    'g_c_matched': {'mapping': 'camc_t2', 'gain': 'cb', 'c_rule': 'matched', 'o2i': '19.5.0'},
    'pkt': {'mapping': 'camc_t2', 'gain': 'cb', 'pkt': True, 'o2i': '19.5.0'},
    'sel': {'mapping': 'camc_t2', 'gain': 'cb', 'sel': True, 'o2i': '19.5.0'},
    'm_trlow': {'mapping': 'camc_t2_trlow', 'gain': 'cb', 'o2i': '19.5.0'},
    'indoor_ground': {'mapping': 'camc_t2', 'gain': 'cb', 'o2i': '19.5.0', 'floors': 'ground'},
    'indoor_stoch_din': {'mapping': 'camc_t2', 'gain': 'cb', 'o2i': '19.5.0', 'din': 'stochastic'},
    'e14_model': {'mapping': 'tr36942', 'gain': 'e14', 'f3_dl': 'e150', 'o2i': '16.1.0'},
}


# ---------------------------------------------------------------- pure helpers (unit-tested)
def disk_radius(isd_m):
    """Radius of the disk with the area of a hexagonal cell of circumradius isd / sqrt(3)."""
    return isd_m / math.sqrt(3.0) * math.sqrt(3.0 * math.sqrt(3.0) / (2.0 * math.pi))


def interval_prob(a, b):
    """P(a < Z <= b) for standard normal Z, elementwise, 0 if b <= a; numerically stable tails."""
    a, b = np.broadcast_arrays(np.asarray(a, dtype=float), np.asarray(b, dtype=float))
    upper = special.ndtr(-a) - special.ndtr(-b)          # accurate for a >= 0
    lower = special.ndtr(b) - special.ndtr(a)            # accurate for a < 0
    p = np.where(a >= 0, upper, lower)
    return np.where(b > a, np.clip(p, 0.0, 1.0), 0.0)


def class_probs(m):
    """m[(i, d)]: margin of the event {Z <= m} for carrier i (0 = 3.5, 1 = 7.2 GHz) and direction d.
    Returns the probabilities of E1.3 K1-K5 and E1.4 K6 (scripts/e1_antennas_o2i.py classes14)."""
    d35, u35, d72, u72 = m[(0, 'DL')], m[(0, 'UL')], m[(1, 'DL')], m[(1, 'UL')]
    ninf = np.full_like(d35, -np.inf)
    return {'K1_both_carriers_both_directions': interval_prob(ninf, np.minimum.reduce([d35, u35, d72, u72])),
            'K2_7p2_dl_only_with_3p5_ul': interval_prob(u72, np.minimum(d72, u35)),
            'K3_7p2_dl_without_any_ul': interval_prob(np.maximum(u72, u35), d72),
            'K4_7p2_dl_not_feasible': interval_prob(d72, np.full_like(d72, np.inf)),
            K5: interval_prob(d72, u72),
            e14.K6: interval_prob(np.minimum(d35, u35), np.minimum(d72, u72))}


def snr_threshold(fn, d, target, factor=1.0, lo=-80.0, hi=150.0, iters=200):
    """Smallest SNR s with factor * fn(s, d) >= target for a non-decreasing fn (bisection);
    -inf if met at lo, +inf if not met at hi."""
    f = lambda s: factor * float(np.asarray(fn(np.array([s]), d))[0])  # noqa: E731
    if f(lo) >= target:
        return -np.inf
    if f(hi) < target:
        return np.inf
    for _ in range(iters):
        mid = 0.5 * (lo + hi)
        if f(mid) >= target:
            hi = mid
        else:
            lo = mid
        if hi - lo <= 1e-13:
            break
    return hi


def stoch_din_nodes(n, d_max=o2i.D2D_IN_MAX_M):
    """Gauss-Legendre nodes and weights for d2D-in = d_max min(U1, U2): pdf 2 (d_max - x) / d_max^2."""
    x, w = np.polynomial.legendre.leggauss(n)
    nodes = 0.5 * d_max * (x + 1.0)
    weights = 0.5 * d_max * w * 2.0 * (d_max - nodes) / d_max ** 2
    return nodes, weights


def margins(snr0, s_star, sigma):
    """(snr0 - s*) / sigma with +-inf for unreachable / always-met thresholds."""
    if np.isposinf(s_star):
        return np.full_like(snr0, -np.inf)
    if np.isneginf(s_star):
        return np.full_like(snr0, np.inf)
    return (snr0 - s_star) / sigma


# ---------------------------------------------------------------- populations
def outdoor_rows(mask, gnb, freqs):
    los = np.where(mask['los'] == 1, 'LOS', 'NLOS')
    x, y, z = mask['x'], mask['y'], mask['z']
    pl = {f: np.array([uh.deterministic_pathloss_db(a, b, f, gnb[2], hz, s)
                       for a, b, hz, s in zip(mask['d2d'], mask['d3d'], z, los)]) for f in freqs}
    zen, az = ant.link_angles(gnb, np.column_stack([x, y, z]))
    n = len(x)
    return {'pid': np.arange(n), 'w': np.ones(n), 'los': los, 'd2d': mask['d2d'], 'h_ut': z, 'zen': zen, 'az': az,
            'pl': pl, 'd_in': np.zeros(n), 'floor': np.zeros(n, dtype=int), 'point': np.arange(n), 'n': n}


def indoor_rows(geo, gnb, freqs, ground_only=False):
    sel = geo['floor'] == 1 if ground_only else np.ones(len(geo['floor']), dtype=bool)
    idx = np.nonzero(sel)[0]
    rows = defaultdict(list)
    for k, i in enumerate(idx):
        d2, h = float(geo['d2d'][i]), float(geo['h_ut'][i])
        comps = [(1.0, 1.0)] if h < 13.0 else pl3.uma_he_distribution(d2, h)
        for he, wgt in comps:
            rows['pid'].append(k)
            rows['w'].append(wgt)
            rows['he'].append(he)
            rows['i'].append(i)
    out = {k: np.array(v) for k, v in rows.items()}
    i = out['i']
    h = geo['h_ut'][i]
    d2 = geo['d2d'][i]
    d3 = np.hypot(d2, gnb[2] - h)
    los = np.where(geo['facade_los'][i] == 1, 'LOS', 'NLOS')
    pl = {}
    for f in freqs:
        v = np.empty(len(i))
        for r, (a, b, hu, s, he) in enumerate(zip(d2, d3, h, los, out['he'])):
            if hu < 13.0:
                v[r] = uh.deterministic_pathloss_db(a, b, f, gnb[2], hu, s)
            else:
                v[r] = (pl3.uma_los_he if s == 'LOS' else pl3.uma_nlos_he)(a, b, f, gnb[2], hu, he)
        pl[f] = v
    zen, az = ant.link_angles(gnb, np.column_stack([geo['x'][i], geo['y'][i], h]))
    return {'pid': out['pid'], 'w': out['w'], 'los': los, 'd2d': d2, 'h_ut': h, 'zen': zen, 'az': az, 'pl': pl,
            'd_in': geo['d2d_in'][i], 'floor': geo['floor'][i], 'point': geo['point_id'][i], 'n': len(idx)}


def expand_stoch(rows, n_nodes):
    nodes, wts = stoch_din_nodes(n_nodes)
    nr = len(rows['w'])
    rep = np.repeat(np.arange(nr), n_nodes)
    out = {k: (v[rep] if isinstance(v, np.ndarray) else v) for k, v in rows.items() if k != 'pl'}
    out['pl'] = {f: v[rep] for f, v in rows['pl'].items()}
    out['w'] = rows['w'][rep] * np.tile(wts, nr)
    out['d_in'] = np.tile(nodes, nr)
    return out


# ---------------------------------------------------------------- engine
def state_terms(state, rows, f, version):
    if state == 'outdoor':
        pen = np.zeros(len(rows['w']))
        sig = np.where(rows['los'] == 'LOS', pl3.SHADOWING_STD_DB[('uma', 'los')], pl3.SHADOWING_STD_DB[('uma', 'nlos')])
    elif state == 'in_car':
        pen = np.full(len(rows['w']), o2i.CAR_MU_DB)
        s0 = np.where(rows['los'] == 'LOS', pl3.SHADOWING_STD_DB[('uma', 'los')], pl3.SHADOWING_STD_DB[('uma', 'nlos')])
        sig = np.hypot(s0, o2i.CAR_SIGMA_DB)
    else:
        model = {'indoor_low': 'low', 'indoor_high': 'high'}[state]
        pen = o2i.wall_loss_db(model, f, version) + o2i.indoor_loss_db(rows['d_in'])
        sig = np.full(len(rows['w']), math.hypot(o2i.O2I_SF_STD_DB, o2i.sigma_p_db(model)))
    return pen, sig


def mappings_for(var, case, maps, pkt_off, sel_r):
    se_base, floors, _ = maps[var['mapping']]
    se2 = se3 = se_base
    if var.get('pkt'):
        se2, se3 = e155.shifted(se_base, pkt_off[0]), e155.shifted(se_base, pkt_off[1])
    if var.get('sel') and case['regime'].split()[0] in ('A', 'B'):
        se2, se3 = e155.scaled(se2, sel_r), e155.scaled(se3, sel_r)
    return se2, se3, floors


def thresholds(var, cfg, se2, se3, floors):
    n_max = cfg.n_prb // K_USERS
    th = {('F1', 'DL'): floors['DL'], ('F1', 'UL'): floors['UL'],
          ('F2', 'DL'): snr_threshold(se2, 'DL', F2T['DL']), ('F2', 'UL'): snr_threshold(se2, 'UL', F2T['UL']),
          ('F3', 'UL'): snr_threshold(se3, 'UL', F3T['UL'], n_max / cfg.n_prb)}
    th[('F3', 'DL')] = (th[('F2', 'DL')] if var.get('f3_dl') == 'e150'
                        else snr_threshold(se3, 'DL', F3T['DL'], n_max / cfg.n_prb))
    return th


def evaluate(case, state, rows, var, maps, table, pkt_off, sel_r):
    """Per row: margins m[(profile, carrier index, dir)], plus the quantities for the C2 check."""
    se2, se3, floors = mappings_for(var, case, maps, pkt_off, sel_r)
    carriers = sorted(case['carriers'], key=lambda c: c['frequency_hz'])
    out = {'m': {}, 'aux': {}}
    for ci, c in enumerate(carriers):
        cfg = lb.CarrierConfig(**{k: c[k] for k in e14.CFG_FIELDS})
        f = cfg.frequency_hz
        panel = e14.panel_of(c, case)
        g_gnb, _ = ant.gnb_gain_dbi(panel, rows['zen'], rows['az'])
        bf, dfc, _ = e155.gains_for(case, panel, f, state, {'los': rows['los']}, var, table)
        g_ue = ant.ue_rx_gain_db(c['ue_n_rx'])
        g_dl, g_ul = g_gnb - bf - dfc + g_ue, g_gnb - bf - dfc
        pen, sig = state_terms(state, rows, f, var['o2i'])
        L0 = rows['pl'][f] + pen
        n_max = cfg.n_prb // K_USERS
        snr = {'DL': cfg.gnb_power_dbm + g_dl - L0 - lb.noise_dbm(cfg.occupied_bw_hz, cfg.nf_ue_db),
               'UL_full': cfg.ue_power_dbm + g_ul - L0 - lb.noise_dbm(cfg.occupied_bw_hz, cfg.nf_gnb_db),
               'UL_nmax': cfg.ue_power_dbm + g_ul - L0 - lb.noise_dbm(n_max * cfg.prb_bw_hz, cfg.nf_gnb_db),
               'UL_1': cfg.ue_power_dbm + g_ul - L0 - lb.noise_dbm(cfg.prb_bw_hz, cfg.nf_gnb_db)}
        th = thresholds(var, cfg, se2, se3, floors)
        which = {('F1', 'DL'): 'DL', ('F1', 'UL'): 'UL_1', ('F2', 'DL'): 'DL', ('F2', 'UL'): 'UL_full',
                 ('F3', 'DL'): 'DL', ('F3', 'UL'): 'UL_nmax'}
        for (p, d), key in which.items():
            out['m'][(p, ci, d)] = margins(snr[key], th[(p, d)], sig)
        out['aux'][ci] = {'cfg': cfg, 'g_dl': g_dl, 'g_ul': g_ul, 'L0': L0, 'sig': sig, 'se2': se2, 'se3': se3,
                          'floors': floors, 'n_max': n_max}
    return out


def direct_flags(aux, L, var):
    """The E1.5.5 per-record pipeline (cmwave link_budget with the variant's mapping) at loss L."""
    cfg, se2, se3, floors, n_max = aux['cfg'], aux['se2'], aux['se3'], aux['floors'], aux['n_max']
    dl = lb.downlink(L, cfg, gain_db=aux['g_dl'], se_fn=se2)
    ul = lb.uplink(L, cfg, gain_db=aux['g_ul'], se_fn=se2)
    ul3 = lb.uplink(L, cfg, gain_db=aux['g_ul'], n_max_prb=n_max, se_fn=se3)
    f3dl = (dl['se_bps_hz'] >= F2T['DL'] if var.get('f3_dl') == 'e150'
            else np.asarray(se3(dl['snr_db'], 'DL')) * n_max / cfg.n_prb >= F3T['DL'])
    return {('F1', 'DL'): dl['snr_db'] >= floors['DL'], ('F1', 'UL'): ul['snr_1prb_db'] >= floors['UL'],
            ('F2', 'DL'): dl['se_bps_hz'] >= F2T['DL'], ('F2', 'UL'): ul['se_norm_bps_hz'] >= F2T['UL'],
            ('F3', 'DL'): f3dl, ('F3', 'UL'): ul3['se_norm_bps_hz'] >= F3T['UL']}


def subset_rows(rows, idx):
    out = {k: (v[idx] if isinstance(v, np.ndarray) else v) for k, v in rows.items() if k != 'pl'}
    out['pl'] = {f: v[idx] for f, v in rows['pl'].items()}
    return out


def c2_check(case, state, rows, var, maps, table, pkt_off, sel_r, rng, n=300):
    idx = np.sort(rng.choice(len(rows['w']), size=min(n, len(rows['w'])), replace=False))
    sub = subset_rows(rows, idx)
    ev = evaluate(case, state, sub, var, maps, table, pkt_off, sel_r)
    bad = 0
    for (p, ci, d), m in ev['m'].items():
        aux = ev['aux'][ci]
        for z, expect in ((np.where(np.isfinite(m), m - EPS, -8.0), True), (np.where(np.isfinite(m), m + EPS, 8.0), False)):
            L = aux['L0'] + aux['sig'] * z
            fl = direct_flags(aux, L, var)[(p, d)]
            want = np.where(np.isfinite(m), expect, m > 0)
            bad += int(np.sum(fl != want))
    return bad


def probabilities(ev, profile):
    m = {(ci, d): ev['m'][(profile, ci, d)] for ci in (0, 1) for d in DIRS}
    cp = class_probs(m)
    feas = {f'{d}_{f}GHz': special.ndtr(m[(ci, d)]) for ci, f in ((0, '3.5'), (1, '7.2')) for d in DIRS}
    return cp, feas


def per_sample(rows, values):
    """Area weights: every sample equal; rows of one sample (hE components, quadrature) weighted by w."""
    return np.bincount(rows['pid'], weights=rows['w'] * values, minlength=rows['n'])


def aggregate(rows, sample_vals, sample_d2d, sample_floor, radii):
    out = {'radius': {}, 'bin': {}, 'floor': {}}
    for rname, rad in radii.items():
        sel = sample_d2d < rad
        out['radius'][rname] = {k: float(np.mean(v[sel])) for k, v in sample_vals.items()}
    for b0, b1 in zip(BINS[:-1], BINS[1:]):
        sel = (sample_d2d >= b0) & (sample_d2d < b1)
        out['bin'][f'{b0}-{b1}'] = {k: float(np.mean(v[sel])) if sel.any() else None for k, v in sample_vals.items()}
    if sample_floor is not None:
        for fl in np.unique(sample_floor):
            sel = sample_floor == fl
            out['floor'][int(fl)] = {k: float(np.mean(v[sel])) for k, v in sample_vals.items()}
    return out


# ---------------------------------------------------------------- C3 (E1.5.5 agreement at the 27 positions)
def c3_check(conf14, maps, table, pkt_off, sel_r, cases):
    eargs = argparse.Namespace(geometry_dir=str(R / 'e1_geometry'), propagation_dir=str(R / 'e1_3gpp_propagation'),
                               e13_dir=str(R / 'e1_3_link_budget'))
    gman, links, pman, prop, *_ = e14.load_inputs(eargs)
    c = dict(conf14)
    c['o2i_tr38901_version'] = '16.1.0'
    states, geo, *_ = e14.build_states(c, gman, links, prop)
    var5 = e155.VARIANTS['V2_primary']
    gnb = np.array(gman['gnb_position_m'], dtype=float)
    J = len(links)
    los = np.array([l['los_state'] for l in links])
    d2o = np.array([float(l['d2d_m']) for l in links])
    hb = np.array([float(l['h_bs_m']) for l in links])
    hu = np.array([float(l['h_ut_m']) for l in links])
    xyz = np.array([[float(l['x_m']), float(l['y_m']), float(l['z_m'])] for l in links])
    zen_o, az = ant.link_angles(gnb, xyz)
    freqs = geo['freqs']
    nodes, wts = stoch_din_nodes(GL_C3)
    worst_p, pooled_bad, n_tests = 1.0, [], 0
    for cname in cases:
        case = conf14['cases'][cname]
        out, _ = e155.evaluate_variant(case, states, geo, conf14['thresholds'], var5, maps, table, pkt_off, sel_r)
        for state in STATES:
            if state in ('outdoor', 'in_car'):
                rows = {'pid': np.arange(J), 'w': np.ones(J), 'los': los, 'zen': zen_o, 'az': az, 'n': J,
                        'd_in': np.zeros(J), 'pl': {f: np.array([uh.deterministic_pathloss_db(
                            a, math.hypot(a, x1 - x2), f, x1, x2, s) for a, x1, x2, s in zip(d2o, hb, hu, los)])
                            for f in freqs}}
            else:
                rep = np.repeat(np.arange(J), GL_C3)
                din = np.tile(nodes, J)
                d2 = d2o[rep] + din
                zen = np.degrees(np.arctan2(d2, xyz[rep, 2] - gnb[2]))
                rows = {'pid': rep, 'w': np.tile(wts, J), 'los': los[rep], 'zen': zen, 'az': az[rep], 'n': J,
                        'd_in': din, 'pl': {f: np.array([uh.deterministic_pathloss_db(
                            a, math.hypot(a, x1 - x2), f, x1, x2, s) for a, x1, x2, s
                            in zip(d2, hb[rep], hu[rep], los[rep])]) for f in freqs}}
            ev = evaluate(case, state, rows, dict(var5, o2i='16.1.0'), maps, table, pkt_off, sel_r)
            for p in PROFILES:
                cp, _ = probabilities(ev, p)
                k = e14.classes14(out[state], *freqs, p)
                for name in PARTITION:
                    pa = per_sample(rows, cp[name])
                    flags = k[name].reshape(-1)
                    counts = np.bincount(geo['jj'], weights=flags.astype(float), minlength=J)
                    nrec = np.bincount(geo['jj'], minlength=J)
                    for j in range(J):
                        pj = min(max(pa[j], 0.0), 1.0)
                        kk, nn = int(round(counts[j])), int(nrec[j])
                        if pj <= 0.0:
                            pv = 1.0 if kk == 0 else 0.0
                        elif pj >= 1.0:
                            pv = 1.0 if kk == nn else 0.0
                        else:
                            pv = min(1.0, 2 * min(stats.binom.cdf(kk, nn, pj), stats.binom.sf(kk - 1, nn, pj)))
                        worst_p = min(worst_p, pv)
                        n_tests += 1
                    se = math.sqrt(np.sum(pa * (1 - pa) / nrec)) / J
                    diff = abs(np.mean(counts / nrec) - np.mean(pa))
                    if diff > 5 * se + 1e-12:
                        pooled_bad.append(f'{cname}/{state}/{p}/{name}: diff {diff:.4f} se {se:.4f}')
    return {'min_p_value': worst_p, 'n_tests': n_tests, 'pooled_failures': pooled_bad,
            'passed': worst_p >= 1e-6 and not pooled_bad}


# ---------------------------------------------------------------- main
def sha256(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--config', default=str(CONFIG))
    ap.add_argument('--outdir', default=str(OUTDIR))
    ap.add_argument('--git-head', default=None)
    args = ap.parse_args()
    conf = json.loads(Path(args.config).read_text(encoding='utf-8'))
    conf14 = json.loads(e155.E14_CONFIG.read_text(encoding='utf-8'))
    outdir = Path(args.outdir)
    (outdir / 'raw').mkdir(parents=True, exist_ok=True)
    validation = {}
    # ---- C0
    mans = {d.name: json.loads((d / 'manifest.json').read_text()) for d in
            (e155.E1521_DIR, e155.E153_DIR, e155.E153A_DIR, e155.E154_DIR, R / 'e1_5_5_reclassification')}
    e11 = json.loads((E11_DIR / 'site_tx0_manifest.json').read_text())
    gman = json.loads((GEO_DIR / 'site_tx0_indoor_manifest.json').read_text())
    c0 = (all(m['all_checks_passed'] for m in mans.values()) and e11['all_checks_passed'] and gman['all_checks_passed']
          and sha256(E11_DIR / 'site_tx0_mask.npz') == e11['files']['site_tx0_mask.npz']
          and sha256(GEO_DIR / 'site_tx0_indoor.npz') == gman['files']['site_tx0_indoor.npz'])
    in_sha = {f'{n}/manifest.json': sha256(d / 'manifest.json') for n, d in
              [(d.name, d) for d in (e155.E1521_DIR, e155.E153_DIR, e155.E153A_DIR, e155.E154_DIR,
                                     R / 'e1_5_5_reclassification', E11_DIR)] if (d / 'manifest.json').exists()}
    in_sha.update({'e1_geometry/site_tx0_mask.npz': sha256(E11_DIR / 'site_tx0_mask.npz'),
                   'e1c_geometry/site_tx0_indoor.npz': sha256(GEO_DIR / 'site_tx0_indoor.npz'),
                   'e1c_geometry/site_tx0_indoor_manifest.json': sha256(GEO_DIR / 'site_tx0_indoor_manifest.json')})
    validation['C0_inputs'] = {'input_sha256': in_sha, 'passed': bool(c0)}
    if not c0:
        raise SystemExit('C0 failed')
    s153 = json.loads((e155.E153_DIR / 'summary.json').read_text())
    s153a = json.loads((e155.E153A_DIR / 'summary.json').read_text())
    s1521 = json.loads((e155.E1521_DIR / 'summary.json').read_text())
    s154 = json.loads((e155.E154_DIR / 'summary.json').read_text())
    maps = e155.build_mappings(s153a, s153)
    table = e155.gain_table(s1521)
    pkt_off = e155.packet_offsets(s154)
    sel_r = {d: v['factor'] for d, v in e155.selectivity_factors(s154).items()}
    cases = conf['cases']
    # ---- C5 mappings
    grid = np.arange(-60.0, 120.0, 0.001)
    c5 = {}
    for vname, var in VARIANTS.items():
        for cname in cases:
            se2, se3, _ = mappings_for(var, conf14['cases'][cname], maps, pkt_off, sel_r)
            for tag, fn in (('se2', se2), ('se3', se3)):
                for d in DIRS:
                    vals = np.asarray(fn(grid, d))
                    lo_ok = grid >= (maps['camc_t2'][1][d] if var['mapping'] == 'camc_t2_trlow' else -np.inf)
                    c5[f'{vname}/{cname}/{tag}/{d}'] = bool(np.all(np.diff(vals[lo_ok]) >= 0))
    validation['C5_mapping'] = {'non_monotone': [k for k, v in c5.items() if not v], 'passed': all(c5.values())}
    # ---- C3
    validation['C3_e155_agreement'] = c3_check(conf14, maps, table, pkt_off, sel_r, cases)
    print('C3', 'PASS' if validation['C3_e155_agreement']['passed'] else 'FAIL',
          validation['C3_e155_agreement']['min_p_value'], flush=True)
    # ---- populations
    mask = np.load(E11_DIR / 'site_tx0_mask.npz')
    geo = np.load(GEO_DIR / 'site_tx0_indoor.npz')
    gnb = np.array(mask['gnb'], dtype=float)
    freqs = sorted(c['frequency_hz'] for c in conf14['cases']['A_equal_elements']['carriers'])
    out_rows = outdoor_rows(mask, gnb, freqs)
    ind_all = indoor_rows(geo, gnb, freqs)
    ind_ground = indoor_rows(geo, gnb, freqs, ground_only=True)
    ind_stoch = expand_stoch(ind_all, GL_STOCH)
    print('rows', {k: len(v['w']) for k, v in (('outdoor', out_rows), ('indoor', ind_all), ('ground', ind_ground),
                                               ('stoch', ind_stoch))}, flush=True)
    radii = {'isd200': disk_radius(200.0), 'isd500': disk_radius(500.0), 'all': 600.0}
    rng = np.random.default_rng(20261109)
    summary = {'radii_m': radii, 'counts': {'outdoor_points': out_rows['n'], 'indoor_samples': ind_all['n'],
                                            'indoor_ground_samples': ind_ground['n']},
               'variants': {}}
    c2_bad, c4 = 0, {'max_partition_err': 0.0, 'min_prob': 1.0, 'max_k6': 0.0}
    maps_out = {}
    for vname, var in VARIANTS.items():
        summary['variants'][vname] = {}
        for cname in cases:
            case = conf14['cases'][cname]
            res = {}
            for state in STATES:
                if state in ('outdoor', 'in_car'):
                    rows = out_rows
                elif var.get('floors') == 'ground':
                    rows = ind_ground
                elif var.get('din') == 'stochastic':
                    rows = ind_stoch
                else:
                    rows = ind_all
                c2_bad += c2_check(case, state, rows, var, maps, table, pkt_off, sel_r, rng)
                ev = evaluate(case, state, rows, var, maps, table, pkt_off, sel_r)
                first = np.r_[True, np.diff(rows['pid']) != 0]
                s_d2d = rows['d2d'][first]
                s_floor = rows['floor'][first] if state.startswith('indoor') else None
                st = {}
                for p in PROFILES:
                    cp, feas = probabilities(ev, p)
                    tot = sum(cp[n] for n in PARTITION)
                    c4['max_partition_err'] = max(c4['max_partition_err'], float(np.max(np.abs(tot - 1.0))))
                    c4['min_prob'] = min(c4['min_prob'], float(min(np.min(v) for v in cp.values())))
                    c4['max_k6'] = max(c4['max_k6'], float(np.max(cp[e14.K6])))
                    vals = {n: per_sample(rows, cp[n]) for n in PARTITION}
                    vals.update({k: per_sample(rows, v) for k, v in feas.items()})
                    st[p] = aggregate(rows, vals, s_d2d, s_floor, radii)
                    if vname == 'primary' and cname in ('A_equal_elements', 'B_equal_aperture', 'C_deployment') \
                            and p in ('F2', 'F3') and state in ('in_car', 'indoor_low', 'indoor_high'):
                        maps_out[(cname, p, state)] = (rows, vals)
                if state == 'outdoor' and vname == 'primary':
                    st['by_los'] = {ls: {n: float(np.mean(per_sample(rows, probabilities(ev, 'F2')[0][n])[out_rows['los'] == ls]))
                                         for n in PARTITION} for ls in ('LOS', 'NLOS')}
                res[state] = st
            mix = {}
            for mname, mm in conf14['mixes'].items():
                mix[mname] = {p: {rn: {k: sum(w * res[s][p]['radius'][rn][k] for s, w in mm['weights'].items())
                                       for k in res['in_car'][p]['radius'][rn]} for rn in radii} for p in PROFILES}
            summary['variants'][vname][cname] = {'per_state': res, 'mixes': mix}
            print(f'{vname} {cname} done', flush=True)
    validation['C2_engine_exact'] = {'mismatches': c2_bad, 'passed': c2_bad == 0}
    validation['C4_probabilities'] = dict(c4, passed=c4['max_partition_err'] <= 1e-12 and c4['min_prob'] >= -1e-15)
    gates_ok = all(v['passed'] for v in validation.values())
    validation['all_checks_passed'] = gates_ok
    # ---- maps (raw)
    arrays = {}
    for (cname, p, state), (rows, vals) in maps_out.items():
        for n in PARTITION:
            arrays[f'{cname}|{p}|{state}|{n.split("_")[0]}'] = vals[n].astype(np.float32)
    arrays['outdoor_x'], arrays['outdoor_y'] = mask['x'], mask['y']
    arrays['indoor_point'], arrays['indoor_x'], arrays['indoor_y'] = geo['point_id'], geo['x'], geo['y']
    # per indoor point: mean over its floors of 0.8 low-loss + 0.2 high-loss (the M2412 indoor split)
    pt = geo['point_id']
    npt = int(pt.max()) + 1
    cnt = np.bincount(pt, minlength=npt)
    first_pt = np.r_[True, np.diff(pt) != 0]
    arrays['indoor_point_x'], arrays['indoor_point_y'] = geo['x'][first_pt], geo['y'][first_pt]
    for cname in ('A_equal_elements', 'B_equal_aperture', 'C_deployment'):
        for p in ('F2', 'F3'):
            vl, vh = maps_out[(cname, p, 'indoor_low')][1], maps_out[(cname, p, 'indoor_high')][1]
            for n in PARTITION:
                mixed = 0.8 * vl[n] + 0.2 * vh[n]
                arrays[f'{cname}|{p}|indoor_point_mix|{n.split("_")[0]}'] = (
                    np.bincount(pt, weights=mixed, minlength=npt) / cnt).astype(np.float32)
    np.savez_compressed(outdir / 'raw' / 'maps.npz', **arrays)
    # ---- predictions
    def mixv(v, c, p, rn, n):
        return summary['variants'][v][c]['mixes']['M2412'][p][rn][n]
    k3 = 'K3_7p2_dl_without_any_ul'
    p1 = {f'{c}_{rn}': (mixv('primary', c, 'F3', rn, k3), mixv('primary', c, 'F2', rn, k3))
          for c in ('A_equal_elements', 'B_equal_aperture', 'C_deployment') for rn in ('isd200', 'isd500')}
    a = summary['variants']['primary']['A_equal_elements']['per_state']['indoor_low']['F2']['radius']['isd500']['UL_7.2GHz']
    b = summary['variants']['indoor_ground']['A_equal_elements']['per_state']['indoor_low']['F2']['radius']['isd500']['UL_7.2GHz']
    summary['predictions'] = {'P1': {'values_k3_f3_f2': p1, 'held': all(x < y for x, y in p1.values())},
                              'P2': {'all_floors': a, 'ground_only': b, 'held': a > b}}
    summary['decision'] = conf['decision_rules']['all_gates_pass' if gates_ok else 'gate_fails']
    (outdir / 'summary.json').write_text(json.dumps(summary, indent=1, default=float), encoding='utf-8')
    (outdir / 'validation.json').write_text(json.dumps(validation, indent=2, default=float), encoding='utf-8')
    manifest = {'generated_utc': datetime.now(timezone.utc).isoformat(), 'tool': 'scripts/e1c_area_maps.py',
                'git_head_at_run': args.git_head, 'python': platform.python_version(), 'numpy': np.__version__,
                'config_sha256': sha256(args.config), 'input_sha256': in_sha,
                'outputs_sha256': {'summary.json': sha256(outdir / 'summary.json'),
                                   'raw/maps.npz': sha256(outdir / 'raw' / 'maps.npz')},
                'all_checks_passed': gates_ok}
    (outdir / 'manifest.json').write_text(json.dumps(manifest, indent=2), encoding='utf-8')
    for k, v in validation.items():
        if isinstance(v, dict):
            print(k, 'PASS' if v['passed'] else 'FAIL')
    print('E1c.1', 'GATES PASSED' if gates_ok else 'GATES FAILED')
    sys.exit(0 if gates_ok else 1)


if __name__ == '__main__':
    main()
