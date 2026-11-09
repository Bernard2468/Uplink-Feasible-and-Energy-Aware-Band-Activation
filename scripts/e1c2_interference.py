#!/usr/bin/env python3
"""E1c.2: interference-aware (SINR) uplink/downlink feasibility of the co-sited 3.5/7.2 GHz layers in a
19-site layout around site 0, with the E1.5 model (Monte Carlo).

Implements configs/e1/e1c2_interference.json. Run inside WSL with the project venv (scipy).
Inputs: E1.1 outdoor mask, E1c indoor samples, E1c.2 site geometry (scripts/e1c2_geometry_sites.py) and
the E1.5.5 primary model through the E1c.1 engine (scripts/e1c_area_maps.py: mappings, thresholds).
Outputs in --outdir: summary.json, validation.json, manifest.json; raw/counts_isd<ISD>.npz (git-ignored).

Model summary (the config is normative):
  * victims: samples in site 0's nominal hexagonal cell; per realization each victim is served by the
    site with the largest 3.5 GHz received power with its beam (E1.4 gain - D) and best sector;
  * DL interference: every other TRxP (other sectors of the serving site included) at the case power,
    element gain of its sector toward the victim, 0 dB array gain (beam-direction average), activity eta;
  * UL interference: one UE per other TRxP, drawn from a candidate pool associated like the victims,
    power 'target' (its own noise-limited SNR = the profile's threshold, capped at 23 dBm) or 'full'
    (23 dBm), received with the element gain of the victim's serving sector, activity eta;
  * every random term per link and realization (shadowing / O2I shadowing, hE) or per UE (car /
    building variate), shared by both carriers and by the three sectors of a site.
"""
import argparse
import hashlib
import json
import math
import platform
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'scripts'))
import e1_antennas_o2i as e14  # noqa: E402
import e1_5_5_reclassification as e155  # noqa: E402
import e1c_area_maps as e1c  # noqa: E402
from cmwave.radio import antenna as ant  # noqa: E402
from cmwave.radio import link_budget as lb  # noqa: E402
from cmwave.radio import o2i  # noqa: E402
from cmwave.radio import pathloss_3gpp as pl3  # noqa: E402

CONFIG = ROOT / 'configs/e1/e1c2_interference.json'
R = ROOT / 'results/phase2'
E11_DIR, GEO_DIR, SITES_DIR = R / 'e1_geometry', R / 'e1c_geometry', R / 'e1c2_geometry'
E1C_DIR, OUTDIR = R / 'e1c_area_maps', R / 'e1c2_interference'
ISDS = (200, 500)
CASES = ('A_equal_elements', 'B_equal_aperture', 'C_deployment')
STATES = e1c.STATES                                   # outdoor, in_car, indoor_low, indoor_high
CAND_STATES = ('in_car', 'indoor_low', 'indoor_high')
PROFILES = ('F1', 'F2', 'F3')
PARTITION = list(e14.PARTITION)
FEAS = ('DL_3.5GHz', 'UL_3.5GHz', 'DL_7.2GHz', 'UL_7.2GHz')
NAMES = PARTITION + list(FEAS)
VAR = e1c.VARIANTS['primary']
O2I_VERSION = VAR['o2i']
BEARINGS = np.array(ant.TR38901_SECTOR_BEARINGS_DEG)
N_SECT = len(BEARINGS)
N_HE = 5                                              # hE in {1, 12, 15, 18, 21} m for hUT <= 22.5 m
N_CAND = 3000
SIG = {True: pl3.SHADOWING_STD_DB[('uma', 'los')], False: pl3.SHADOWING_STD_DB[('uma', 'nlos')]}
SETTINGS = {
    'primary': {'eta': 1.0, 'ul': 'target', 'act72': 'all', 'server': 'best'},
    'eta0p5': {'eta': 0.5, 'ul': 'target', 'act72': 'all', 'server': 'best'},
    'eta0p2': {'eta': 0.2, 'ul': 'target', 'act72': 'all', 'server': 'best'},
    'ul_off': {'eta': 1.0, 'ul': 'off', 'act72': 'all', 'server': 'best'},
    'ul_full': {'eta': 1.0, 'ul': 'full', 'act72': 'all', 'server': 'best'},
    'iso72': {'eta': 1.0, 'ul': 'target', 'act72': 'isolated', 'server': 'best'},
    'noise_multi': {'eta': 0.0, 'ul': 'off', 'act72': 'all', 'server': 'best'},
    'single_noise': {'eta': 0.0, 'ul': 'off', 'act72': 'all', 'server': 'site0'},
}
RAW_SETTINGS = ('primary', 'noise_multi', 'single_noise')
G3_REALIZATIONS = (0, 1)
G3_PAIRS = 50
HIST = (-100.0, 160.0, 0.05)
NB = int(round((HIST[1] - HIST[0]) / HIST[2]))
METRICS = ('DL', 'UL_F2', 'UL_F3')


# ---------------------------------------------------------------- pure helpers (unit-tested)
def rng_for(seed, isd, r, stream):
    """Independent stream per (ISD, realization, stream): 0 = UL candidates, 1 + i = victims of STATES[i]."""
    return np.random.default_rng(np.random.SeedSequence([int(seed), int(isd), int(r), int(stream)]))


def lattice_points(origin, isd, rings):
    """Hexagonal lattice (vectors (ISD, 0), (ISD/2, ISD sqrt(3)/2)) around origin, sorted by ring."""
    a1, a2 = np.array([isd, 0.0]), np.array([isd / 2.0, isd * math.sqrt(3.0) / 2.0])
    pts = []
    for i in range(-rings, rings + 1):
        for j in range(-rings, rings + 1):
            rr = max(abs(i), abs(j), abs(i + j))
            if rr <= rings:
                pts.append((rr, i, j, np.asarray(origin[:2], dtype=float) + i * a1 + j * a2))
    pts.sort(key=lambda t: (t[0], t[1], t[2]))
    return np.array([p[3] for p in pts]), np.array([p[0] for p in pts])


def nearest_ring(xy, origin, isd, rings=4):
    """Ring of the nearest lattice point (nominal cell membership); ties go to the lower ring."""
    pts, ring = lattice_points(origin, isd, rings)
    best = np.full(len(xy), np.inf)
    out = np.zeros(len(xy), dtype=int)
    for p, rr in zip(pts, ring):
        d = np.hypot(xy[:, 0] - p[0], xy[:, 1] - p[1])
        closer = d < best
        best = np.where(closer, d, best)
        out = np.where(closer, rr, out)
    return out


def he_components(d2d, h_ut):
    """Vectorized pathloss_3gpp.uma_he_distribution (TR 38.901 Table 7.4.1-1 Note 1).
    Returns (hE values, probabilities), shape (..., 5); component 0 is hE = 1 m; unused components have
    probability 0 (value 1 m)."""
    d2d, h = np.broadcast_arrays(np.asarray(d2d, dtype=float), np.asarray(h_ut, dtype=float))
    if np.any((h < 1.5) | (h > 22.5)):
        raise ValueError('UMa hUT must lie in [1.5, 22.5] m')
    g = np.where(d2d <= 18.0, 0.0, 1.25 * (d2d / 100.0) ** 3 * np.exp(-d2d / 150.0))
    c = np.where(h < 13.0, 0.0, (np.maximum(h - 13.0, 0.0) / 10.0) ** 1.5 * g)
    p1 = 1.0 / (1.0 + c)
    n_oth = np.where(h - 1.5 >= 12.0, np.floor((h - 1.5 - 12.0) / 3.0 + 1e-9).astype(int) + 1, 0)
    if np.max(n_oth, initial=0) > N_HE - 1:
        raise ValueError('more hE components than supported')
    single = (n_oth == 0) | (p1 == 1.0)
    k = np.arange(1, N_HE)
    valid = (k <= n_oth[..., None]) & ~single[..., None]
    vals = np.concatenate([np.ones(h.shape + (1,)), np.where(valid, 12.0 + 3.0 * (k - 1), 1.0)], axis=-1)
    q = np.where(single, 0.0, (1.0 - p1) / np.maximum(n_oth, 1))
    probs = np.concatenate([np.where(single, 1.0, p1)[..., None], np.where(valid, q[..., None], 0.0)], axis=-1)
    return vals, probs


def he_index(u, he_p):
    """Component drawn by the uniform u: the number of cumulative probabilities <= u, capped at the
    last component with positive probability."""
    cum = np.cumsum(he_p, axis=-1)
    nvalid = np.count_nonzero(he_p > 0.0, axis=-1)
    return np.minimum(np.count_nonzero(np.asarray(u)[..., None] >= cum, axis=-1), nvalid - 1)


def uma_pl(d2d, d3d, fc_hz, h_bs, h_ut, h_e, los):
    """Vectorized TR 38.901 UMa path loss with an explicit hE (pathloss_3gpp.uma_los_he / uma_nlos_he;
    equal to uma_los / uma_nlos for hE = 1 m). los: boolean array."""
    f_ghz = fc_hz / 1e9
    dbp = 4.0 * (h_bs - h_e) * (h_ut - h_e) * fc_hz / pl3.C0
    near = 28.0 + 22.0 * np.log10(d3d) + 20.0 * math.log10(f_ghz)
    far = (28.0 + 40.0 * np.log10(d3d) + 20.0 * math.log10(f_ghz)
           - 9.0 * np.log10(dbp ** 2 + (h_bs - h_ut) ** 2))
    pl_los = np.where(d2d <= dbp, near, far)
    pl_nlos = 13.54 + 39.08 * np.log10(d3d) + 20.0 * math.log10(fc_hz / 1e9) - 0.6 * (h_ut - 1.5)
    return np.where(los, pl_los, np.maximum(pl_los, pl_nlos))


def hist_add(h, v):
    i = np.clip(np.floor((np.asarray(v) - HIST[0]) / HIST[2]).astype(np.int64), 0, NB - 1)
    h += np.bincount(i.ravel(), minlength=NB)


def hist_quantiles(h, qs=(0.05, 0.5, 0.95)):
    """Quantiles of a histogram on the HIST grid (linear within a bin)."""
    h = np.asarray(h, dtype=float)
    c = np.cumsum(h) / h.sum()
    out = []
    for q in qs:
        j = int(np.searchsorted(c, q))
        prev = c[j - 1] if j > 0 else 0.0
        frac = (q - prev) / (c[j] - prev) if c[j] > prev else 0.0
        out.append(HIST[0] + (j + frac) * HIST[2])
    return out


def ul_target_power(s_star, noise_dbm, loss, gain, p_max):
    """Power at which the own noise-limited SNR equals s*, capped at p_max (dBm)."""
    return np.minimum(p_max, s_star + noise_dbm + loss - gain)


# ---------------------------------------------------------------- link blocks
def link_block(sxyz, ux, uy, uh, los, din, freqs):
    """Deterministic link quantities from every UE (rows) to every site (columns)."""
    dx = ux[:, None] - sxyz[None, :, 0]
    dy = uy[:, None] - sxyz[None, :, 1]
    d2 = np.hypot(dx, dy)
    hb = np.broadcast_to(sxyz[None, :, 2], d2.shape)
    hu = np.broadcast_to(uh[:, None], d2.shape)
    d3 = np.hypot(d2, hb - hu)
    zen = np.degrees(np.arctan2(d2, hu - hb))
    az = np.degrees(np.arctan2(dy, dx))
    E = ant.element_gain_dbi(zen[..., None], az[..., None] - BEARINGS)       # [n, S, sectors]
    sb = np.argmax(E, axis=-1)
    Elin = 10.0 ** (E / 10.0)
    he_v, he_p = he_components(d2, hu)
    pl = np.stack([uma_pl(d2[..., None], d3[..., None], f, hb[..., None], hu[..., None], he_v, los[..., None])
                   for f in freqs], axis=-1)                                  # [n, S, N_HE, F]
    return {'n': len(ux), 'd2': d2, 'd3': d3, 'zen': zen, 'az': az, 'los': los, 'din': din, 'hu': uh, 'E': E,
            'sb': sb, 'T': Elin.sum(axis=-1),
            'Tmb': np.where(np.arange(N_SECT) != sb[..., None], Elin, 0.0).sum(axis=-1),
            'he_v': he_v, 'he_p': he_p, 'pl': pl}


def case_gains(block, case, table, kind):
    """Per carrier (ascending frequency): UL gain (E1.4 ideal steering - D, E1.5.5 V2) and DL gain (+ UE
    MRC) of every link, [n, S]. kind 'out': O2O strata; 'ind': O2I stratum."""
    carriers = sorted(case['carriers'], key=lambda c: c['frequency_hz'])
    los_s = np.where(block['los'], 'LOS', 'NLOS').ravel()
    state = 'in_car' if kind == 'out' else 'indoor_low'
    out = []
    for c in carriers:
        panel = e14.panel_of(c, case)
        g, best = ant.gnb_gain_dbi(panel, block['zen'], block['az'])
        if not np.array_equal(best, block['sb']):
            raise SystemExit('sector choice differs from the element-pattern choice')
        bf, d, _ = e155.gains_for(case, panel, c['frequency_hz'], state, {'los': los_s}, VAR, table)
        gul = g - bf.reshape(g.shape) - d.reshape(g.shape)
        out.append({'ul': gul, 'dl': gul + ant.ue_rx_gain_db(c['ue_n_rx'])})
    return out


def state_terms(state, block, freqs):
    """Deterministic penetration [n, S, F], link shadowing sigma [n, S] and per-UE sigma (car/building)."""
    shape = block['d2'].shape
    if state in ('outdoor', 'in_car'):
        sig = np.where(block['los'], SIG[True], SIG[False])
        pen = np.full(shape + (len(freqs),), 0.0 if state == 'outdoor' else o2i.CAR_MU_DB)
        return pen, sig, (0.0 if state == 'outdoor' else o2i.CAR_SIGMA_DB)
    model = {'indoor_low': 'low', 'indoor_high': 'high'}[state]
    pen = np.stack([o2i.wall_loss_db(model, f, O2I_VERSION) + o2i.indoor_loss_db(block['din']) for f in freqs], -1)
    return pen, np.full(shape, o2i.O2I_SF_STD_DB), o2i.sigma_p_db(model)


def draw_ue_terms(rng, n, n_sites):
    z = rng.standard_normal((n, n_sites))      # link shadowing (O2O or O2I), shared by sectors and carriers
    u = rng.random((n, n_sites))               # hE component selector per link
    x = rng.standard_normal(n)                 # car / building variate per UE
    return z, u, x


def losses(block, pen, sig, sue, z, u, x):
    """Total loss [n, S, F] = PL(drawn hE) + penetration + shadowing + per-UE variate."""
    idx = he_index(u, block['he_p'])
    n, S = idx.shape
    pl = block['pl'][np.arange(n)[:, None], np.arange(S)[None, :], idx, :]
    rnd = sig * z + (np.asarray(sue) * np.asarray(x))[:, None]      # sue: scalar or one value per UE
    return pl + pen + rnd[..., None], idx


def draw_candidates(rng, n_out, n_ind, m, n_sites, mix):
    st = rng.choice(len(CAND_STATES), size=m, p=[mix[s] for s in CAND_STATES])
    io = rng.integers(n_out, size=m)
    ii = rng.integers(n_ind, size=m)
    z, u, x = draw_ue_terms(rng, m, n_sites)
    usel = rng.random(N_SECT * n_sites)
    return {'state': st, 'io': io, 'ii': ii, 'z': z, 'u': u, 'x': x, 'usel': usel}


def candidate_block(cand, data, sxyz, pools, freqs):
    """Link block and losses of the UL candidate UEs (state per candidate)."""
    st = cand['state']
    is_out = st == 0
    po, pi = pools['out'][cand['io']], pools['ind'][cand['ii']]
    mk, gi, gs = data['mask'], data['geo'], data['sites']
    ux = np.where(is_out, mk['x'][po], gi['x'][pi])
    uy = np.where(is_out, mk['y'][po], gi['y'][pi])
    uh = np.where(is_out, mk['z'][po], gi['h_ut'][pi])
    los = np.where(is_out[:, None], gs['los_out'][po], gs['facade_los'][pi])
    din = np.where(is_out[:, None], 0.0, gs['din'][pi])
    b = link_block(sxyz, ux, uy, uh, los, din, freqs)
    pen = np.zeros(b['d2'].shape + (len(freqs),))
    sig = np.zeros(b['d2'].shape)
    sue = np.zeros(b['n'])
    for si, state in enumerate(CAND_STATES):
        sel = st == si
        p_, s_, u_ = state_terms(state, b, freqs)
        pen[sel], sig[sel], sue[sel] = p_[sel], s_[sel], u_
    b['L'], b['he_idx'] = losses(b, pen, sig, sue, cand['z'], cand['u'], cand['x'])
    b['is_out'] = is_out
    b['sample'] = np.where(is_out, po, pi)
    return b


def candidate_gains(cb, case, table):
    go, gi = case_gains(cb, case, table, 'out'), case_gains(cb, case, table, 'ind')
    m = cb['is_out'][:, None]
    return [{k: np.where(m, a[k], b[k]) for k in ('ul', 'dl')} for a, b in zip(go, gi)]


# ---------------------------------------------------------------- case context, UL interference, evaluation
def case_context(case, maps, pkt_off, sel_r):
    se2, se3, floors = e1c.mappings_for(VAR, case, maps, pkt_off, sel_r)
    cx = {'th': [], 'p_dl': [], 'n_dl': [], 'p_ue': [], 'n_ul': [], 'n_prb': [], 'n_max': [], 'freqs': [],
          'se2': se2, 'se3': se3}
    for c in sorted(case['carriers'], key=lambda c: c['frequency_hz']):
        cfg = lb.CarrierConfig(**{k: c[k] for k in e14.CFG_FIELDS})
        n_max = cfg.n_prb // e1c.K_USERS
        cx['freqs'].append(cfg.frequency_hz)
        cx['th'].append(e1c.thresholds(VAR, cfg, se2, se3, floors))
        cx['p_dl'].append(cfg.gnb_power_dbm)
        cx['n_dl'].append(float(lb.noise_dbm(cfg.occupied_bw_hz, cfg.nf_ue_db)))
        cx['p_ue'].append(cfg.ue_power_dbm)
        cx['n_ul'].append({'F1': float(lb.noise_dbm(cfg.prb_bw_hz, cfg.nf_gnb_db)),
                           'F2': float(lb.noise_dbm(cfg.occupied_bw_hz, cfg.nf_gnb_db)),
                           'F3': float(lb.noise_dbm(n_max * cfg.prb_bw_hz, cfg.nf_gnb_db))})
        cx['n_prb'].append(cfg.n_prb)
        cx['n_max'].append(n_max)
    return cx


def ul_interference(cb, Gc, cx, usel, n_sites):
    """One interferer per TRxP (uniform among the candidates it serves; -1 if none). Returns the
    selection and the UL interference [mW] received at every TRxP from the other TRxPs' interferers
    (eta = 1), per carrier, interferer profile (F2: full band; F3: n_max PRBs) and power mode."""
    n_trxp = N_SECT * n_sites
    L = cb['L']
    rows = np.arange(len(L))
    kc = np.argmax(Gc[0]['ul'] - L[:, :, 0], axis=1)
    tc = N_SECT * kc + cb['sb'][rows, kc]
    sel = np.full(n_trxp, -1)
    for t in range(n_trxp):
        mem = np.flatnonzero(tc == t)
        if len(mem):
            sel[t] = mem[min(int(usel[t] * len(mem)), len(mem) - 1)]
    have = np.flatnonzero(sel >= 0)
    u, ku = sel[have], have // N_SECT
    trx = np.arange(n_trxp)
    k_rx, s_rx = trx // N_SECT, trx % N_SECT
    e_rx = cb['E'][u[:, None], k_rx[None, :], s_rx[None, :]]               # [interferers, receiving TRxPs]
    other = have[:, None] != trx[None, :]
    out, powers = [], []
    for fi in range(len(cx['freqs'])):
        l_rx = L[u[:, None], k_rx[None, :], fi]
        l_own, g_own = L[u, ku, fi], Gc[fi]['ul'][u, ku]
        res, pw = {}, {}
        for p in ('F2', 'F3'):
            tgt = ul_target_power(cx['th'][fi][(p, 'UL')], cx['n_ul'][fi][p], l_own, g_own, cx['p_ue'][fi])
            pw[p] = tgt
            res[p] = {mode: np.where(other, 10.0 ** ((pv[:, None] + e_rx - l_rx) / 10.0), 0.0).sum(axis=0)
                      for mode, pv in (('target', tgt), ('full', np.full(len(u), cx['p_ue'][fi])))}
        out.append(res)
        powers.append(pw)
    return {'sel': sel, 'tc': tc, 'I': out, 'P_target': powers}


def evaluate_case(b, G, L, Llin, ulI, cx, settings=SETTINGS):
    """SINR [dB] per setting: DL per carrier, UL per profile and carrier; serving site and TRxP."""
    n, S, F = L.shape
    rows = np.arange(n)
    kbest = np.argmax(G[0]['ul'] - L[:, :, 0], axis=1)
    servers = {}
    for server in sorted({s['server'] for s in settings.values()}):
        kk = kbest if server == 'best' else np.zeros(n, dtype=int)
        own = np.arange(S)[None, :] == kk[:, None]
        tsel = np.where(own, b['Tmb'], b['T'])
        sv = {'k': kk, 't': N_SECT * kk + b['sb'][rows, kk], 'Sdl': [], 'Sul': [], 'Idl': []}
        for fi in range(F):
            lk = L[rows, kk, fi]
            sv['Sdl'].append(cx['p_dl'][fi] + G[fi]['dl'][rows, kk] - lk)
            sv['Sul'].append(cx['p_ue'][fi] + G[fi]['ul'][rows, kk] - lk)
            sv['Idl'].append(10.0 ** (cx['p_dl'][fi] / 10.0) * (tsel * Llin[:, :, fi]).sum(axis=1))
        servers[server] = sv
    out = {}
    for name, st in settings.items():
        sv = servers[st['server']]
        dl, ul = [], {p: [] for p in PROFILES}
        for fi in range(F):
            on = st['eta'] * (0.0 if (st['act72'] == 'isolated' and fi == 1) else 1.0)
            dl.append(sv['Sdl'][fi] - 10.0 * np.log10(10.0 ** (cx['n_dl'][fi] / 10.0) + on * sv['Idl'][fi]))
            for p in PROFILES:
                i_ul = 0.0
                if st['ul'] != 'off' and on > 0.0:
                    i_ul = ulI[fi]['F3' if p == 'F3' else 'F2'][st['ul']][sv['t']]
                    if p == 'F1':
                        i_ul = i_ul / cx['n_prb'][fi]          # F2 interferers spread over N PRBs
                ul[p].append(sv['Sul'][fi] - 10.0 * np.log10(10.0 ** (cx['n_ul'][fi][p] / 10.0) + on * i_ul))
        out[name] = {'dl': dl, 'ul': ul, 'k': sv['k'], 't': sv['t']}
    return out


def class_flags(res, cx):
    """Per profile: K1-K6 (scripts/e1_antennas_o2i.py classes14) and the four feasibility flags."""
    out = {}
    for p in PROFILES:
        r = {fi: {'DL': {p: res['dl'][fi] >= cx['th'][fi][(p, 'DL')]},
                  'UL': {p: res['ul'][p][fi] >= cx['th'][fi][(p, 'UL')]}} for fi in (0, 1)}
        k = e14.classes14(r, 0, 1, p)
        fl = {n: k[n] for n in PARTITION}
        fl.update({'DL_3.5GHz': r[0]['DL'][p], 'UL_3.5GHz': r[0]['UL'][p],
                   'DL_7.2GHz': r[1]['DL'][p], 'UL_7.2GHz': r[1]['UL'][p]})
        out[p] = fl
    return out


# ---------------------------------------------------------------- independent scalar implementation (gate G3)
def s_element(zen, az_local):
    """TR 38.901 Table 7.3-1 element (re-typed): 65 deg beamwidths, 30 dB limits, 8 dBi."""
    ph = (az_local + 180.0) % 360.0 - 180.0
    a_v = -min(12.0 * ((zen - 90.0) / 65.0) ** 2, 30.0)
    a_h = -min(12.0 * (ph / 65.0) ** 2, 30.0)
    return 8.0 - min(-(a_v + a_h), 30.0)


def s_he(d2, hu, u):
    comps = pl3.uma_he_distribution(d2, hu) if hu >= 13.0 else [(1.0, 1.0)]
    acc, cnt = 0.0, 0
    for _, p in comps:
        acc += p
        if u >= acc:
            cnt += 1
    return comps[min(cnt, len(comps) - 1)][0]


def s_pathloss(d2, d3, f, hb, hu, he, los):
    if hu < 13.0:
        return pl3.uma_los(d2, d3, f, hb, hu) if los else pl3.uma_nlos(d2, d3, f, hb, hu)
    return pl3.uma_los_he(d2, d3, f, hb, hu, he) if los else pl3.uma_nlos_he(d2, d3, f, hb, hu, he)


def s_link(ue, site, f, state, z, u, x, k):
    """Total loss, zenith and azimuth of UE ue = (x, y, h, los list, din list) toward site k."""
    ux, uy, uh, los_l, din_l = ue
    sx, sy, sh = site
    d2 = math.hypot(ux - sx, uy - sy)
    d3 = math.hypot(d2, sh - uh)
    los = los_l[k]
    pl = s_pathloss(d2, d3, f, sh, uh, s_he(d2, uh, u), los)
    if state in ('outdoor', 'in_car'):
        sig = 4.0 if los else 6.0
        pen, sue = (0.0, 0.0) if state == 'outdoor' else (9.0, 5.0)
    else:
        model = 'low' if state == 'indoor_low' else 'high'
        pen = e14.tw_independent(model, f, '19.5.0') + 0.5 * din_l[k]
        sig, sue = 7.0, {'low': 4.4, 'high': 6.5}[model]
    zen = math.degrees(math.atan2(d2, uh - sh))
    az = math.degrees(math.atan2(uy - sy, ux - sx))
    return pl + pen + (sig * z + sue * x), zen, az


def s_gains(case, fi, zen, az, los, kind, table):
    """UL gain, DL gain, best sector and the three element gains (scalar)."""
    c = sorted(case['carriers'], key=lambda c: c['frequency_hz'])[fi]
    rows, cols = c['gnb_panel']['rows'], c['gnb_panel']['cols']
    es = [s_element(zen, az - b) for b in (30.0, 150.0, 270.0)]
    sb = max(range(3), key=lambda s: (es[s], -s))
    g = es[sb] + 10.0 * math.log10(rows * cols)
    if case['regime'].split()[0] == 'C':
        g -= 0.0 if los else 4.5
    else:
        stratum = ('o2o_los' if los else 'o2o_nlos') if kind == 'out' else 'o2i'
        arr = {(8, 16): 'a', (16, 32): 'b'}[(rows, cols)]
        g -= float(table[(arr, float(c['frequency_hz']), stratum)]['D_cb_db'])
    return g, g + 10.0 * math.log10(c['ue_n_rx']), sb, es


def s_noise(bw, nf):
    return -174.0 + 10.0 * math.log10(bw) + nf


def s_ue(data, kind, i):
    s = data['sites']
    if kind == 'out':
        m = data['mask']
        return (float(m['x'][i]), float(m['y'][i]), float(m['z'][i]), [bool(v) for v in s['los_out'][i]],
                [0.0] * len(s['site_x']))
    g = data['geo']
    return (float(g['x'][i]), float(g['y'][i]), float(g['h_ut'][i]), [bool(v) for v in s['facade_los'][i]],
            [float(v) for v in s['din'][i]])


def scalar_primary(data, case, table, cx, seed, isd, r, state, vi, mix, cache):
    """Independent scalar re-implementation of the primary setting for victim vi of `state` in
    realization r: association, DL SINR per carrier, UL SINR per profile and carrier."""
    s = data['sites']
    S = len(s['site_x'])
    sites = [(float(s['site_x'][k]), float(s['site_y'][k]), float(s['site_h'][k])) for k in range(S)]
    freqs = [float(c['frequency_hz']) for c in sorted(case['carriers'], key=lambda c: c['frequency_hz'])]
    key = (isd, r)
    if key not in cache:
        cand = draw_candidates(rng_for(seed, isd, r, 0), len(data['pools']['out']), len(data['pools']['ind']),
                               N_CAND, S, mix)
        ues, tc = [], []
        for m in range(N_CAND):
            st = CAND_STATES[int(cand['state'][m])]
            kind = 'out' if st == 'in_car' else 'ind'
            smp = int(data['pools']['out'][cand['io'][m]] if kind == 'out' else data['pools']['ind'][cand['ii'][m]])
            ue = s_ue(data, kind, smp)
            best = None
            for k in range(S):
                L, zen, az = s_link(ue, sites[k], freqs[0], st, cand['z'][m, k], cand['u'][m, k], cand['x'][m], k)
                g, _, sb, _ = s_gains(case, 0, zen, az, ue[3][k], kind, table)
                if best is None or g - L > best[0]:
                    best = (g - L, k, sb)
            ues.append((ue, st, kind))
            tc.append(N_SECT * best[1] + best[2])
        sel = {}
        for t in range(N_SECT * S):
            mem = [m for m in range(N_CAND) if tc[m] == t]
            if mem:
                sel[t] = mem[min(int(cand['usel'][t] * len(mem)), len(mem) - 1)]
        cache[key] = (cand, ues, sel)
    cand, ues, sel = cache[key]
    kind = 'out' if state in ('outdoor', 'in_car') else 'ind'
    smp = int(data['victims'][kind][vi])
    z, u, x = draw_ue_terms(rng_for(seed, isd, r, 1 + STATES.index(state)), len(data['victims'][kind]), S)
    ue = s_ue(data, kind, smp)
    lk = [[None] * S for _ in freqs]
    gk = [[None] * S for _ in freqs]
    for fi, f in enumerate(freqs):
        for k in range(S):
            L, zen, az = s_link(ue, sites[k], f, state, z[vi, k], u[vi, k], x[vi], k)
            lk[fi][k] = L
            gk[fi][k] = s_gains(case, fi, zen, az, ue[3][k], kind, table)
    ks = max(range(S), key=lambda k: (gk[0][k][0] - lk[0][k], -k))
    ss = gk[0][ks][2]
    t_v = N_SECT * ks + ss
    out = {'k': ks, 't': t_v, 'dl': [], 'ul': {p: [] for p in PROFILES}}
    for fi, f in enumerate(freqs):
        p_dl = cx['p_dl'][fi]
        i_dl = 0.0
        for k in range(S):
            for sct in range(N_SECT):
                if (k, sct) != (ks, ss):
                    i_dl += 10.0 ** ((p_dl + gk[fi][k][3][sct] - lk[fi][k]) / 10.0)
        nb = cx['n_prb'][fi] * 12 * 30e3
        out['dl'].append(p_dl + gk[fi][ks][1] - lk[fi][ks] - 10.0 * math.log10(10.0 ** (s_noise(nb, 7.0) / 10.0) + i_dl))
        noise = {'F1': s_noise(12 * 30e3, 5.0), 'F2': s_noise(nb, 5.0), 'F3': s_noise(cx['n_max'][fi] * 12 * 30e3, 5.0)}
        i_ul = {'F2': 0.0, 'F3': 0.0}
        for t, m in sel.items():
            if t == t_v:
                continue
            ue_i, st_i, kind_i = ues[m]
            k_own = t // N_SECT
            l_own, zo, ao = s_link(ue_i, sites[k_own], f, st_i, cand['z'][m, k_own], cand['u'][m, k_own],
                                   cand['x'][m], k_own)
            g_own = s_gains(case, fi, zo, ao, ue_i[3][k_own], kind_i, table)[0]
            l_rx, zr, ar = s_link(ue_i, sites[ks], f, st_i, cand['z'][m, ks], cand['u'][m, ks], cand['x'][m], ks)
            e_rx = s_element(zr, ar - (30.0, 150.0, 270.0)[ss])
            for p in ('F2', 'F3'):
                pw = min(23.0, cx['th'][fi][(p, 'UL')] + noise[p] + l_own - g_own)
                i_ul[p] += 10.0 ** ((pw + e_rx - l_rx) / 10.0)
        s_ul = 23.0 + gk[fi][ks][0] - lk[fi][ks]
        for p in PROFILES:
            ii = i_ul['F3'] if p == 'F3' else (i_ul['F2'] / cx['n_prb'][fi] if p == 'F1' else i_ul['F2'])
            out['ul'][p].append(s_ul - 10.0 * math.log10(10.0 ** (noise[p] / 10.0) + ii))
    return out


# ---------------------------------------------------------------- gates G1a, G2
def g2_check(block, freqs):
    """Vectorized path loss vs pathloss_3gpp for every link and hE component with positive probability."""
    v, k, c = np.nonzero(block['he_p'] > 0.0)
    worst, errors = 0.0, 0
    for fi, f in enumerate(freqs):
        vec = block['pl'][v, k, c, fi]
        d2l, d3l = block['d2'][v, k].tolist(), block['d3'][v, k].tolist()
        hul, hbl = block['hu'][v].tolist(), block['sxyz'][k, 2].tolist()
        hel, losl = block['he_v'][v, k, c].tolist(), block['los'][v, k].tolist()
        for j in range(len(v)):
            try:
                ref = s_pathloss(d2l[j], d3l[j], f, hbl[j], hul[j], hel[j], bool(losl[j]))
            except ValueError:
                errors += 1
                continue
            worst = max(worst, abs(ref - float(vec[j])))
    return {'links_components': int(len(v)), 'max_abs_diff_db': worst, 'scalar_errors': errors,
            'passed': errors == 0 and worst <= 1e-9}


def e1c_rows(data, kind, idx, gnb, freqs):
    if kind == 'out':
        return e1c.outdoor_rows({k: data['mask'][k][idx] for k in ('los', 'x', 'y', 'z', 'd2d', 'd3d')}, gnb, freqs)
    keys = ('floor', 'd2d', 'h_ut', 'facade_los', 'x', 'y', 'd2d_in', 'point_id')
    return e1c.indoor_rows({k: data['geo'][k][idx] for k in keys}, gnb, freqs)


# ---------------------------------------------------------------- main
def sha256(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--config', default=str(CONFIG))
    ap.add_argument('--outdir', default=str(OUTDIR))
    ap.add_argument('--git-head', default=None)
    args = ap.parse_args()
    t_start = time.time()
    conf = json.loads(Path(args.config).read_text(encoding='utf-8'))
    conf14 = json.loads(e155.E14_CONFIG.read_text(encoding='utf-8'))
    seed, n_real = int(conf['settings']['seed']), int(conf['settings']['realizations'])
    outdir = Path(args.outdir)
    (outdir / 'raw').mkdir(parents=True, exist_ok=True)
    validation = {}
    # ---- G0 inputs
    mdirs = (e155.E1521_DIR, e155.E153_DIR, e155.E153A_DIR, e155.E154_DIR, R / 'e1_5_5_reclassification', E1C_DIR)
    mans = {d.name: json.loads((d / 'manifest.json').read_text()) for d in mdirs}
    e11 = json.loads((E11_DIR / 'site_tx0_manifest.json').read_text())
    gman = json.loads((GEO_DIR / 'site_tx0_indoor_manifest.json').read_text())
    sman = json.loads((SITES_DIR / 'e1c2_geometry_manifest.json').read_text())
    site_files = {isd: SITES_DIR / f'e1c2_sites_isd{isd}.npz' for isd in ISDS}
    g0 = (all(m['all_checks_passed'] for m in mans.values()) and e11['all_checks_passed']
          and gman['all_checks_passed'] and sman['all_checks_passed']
          and sha256(E11_DIR / 'site_tx0_mask.npz') == e11['files']['site_tx0_mask.npz']
          and sha256(GEO_DIR / 'site_tx0_indoor.npz') == gman['files']['site_tx0_indoor.npz']
          and all(sha256(p) == sman['files'][p.name] for p in site_files.values()))
    in_sha = {f'{d.name}/manifest.json': sha256(d / 'manifest.json') for d in mdirs}
    in_sha.update({'e1_geometry/site_tx0_mask.npz': sha256(E11_DIR / 'site_tx0_mask.npz'),
                   'e1c_geometry/site_tx0_indoor.npz': sha256(GEO_DIR / 'site_tx0_indoor.npz'),
                   'e1c2_geometry/e1c2_geometry_manifest.json': sha256(SITES_DIR / 'e1c2_geometry_manifest.json')})
    in_sha.update({f'e1c2_geometry/{p.name}': sha256(p) for p in site_files.values()})
    validation['G0_inputs'] = {'input_sha256': in_sha, 'passed': bool(g0)}
    if not g0:
        raise SystemExit('G0 failed')
    s153 = json.loads((e155.E153_DIR / 'summary.json').read_text())
    s153a = json.loads((e155.E153A_DIR / 'summary.json').read_text())
    s1521 = json.loads((e155.E1521_DIR / 'summary.json').read_text())
    s154 = json.loads((e155.E154_DIR / 'summary.json').read_text())
    maps = e155.build_mappings(s153a, s153)
    table = e155.gain_table(s1521)
    pkt_off = e155.packet_offsets(s154)
    sel_r = {d: v['factor'] for d, v in e155.selectivity_factors(s154).items()}
    cases = {c: conf14['cases'][c] for c in CASES}
    if any(list(cases[c]['sector_bearings_deg']) != list(BEARINGS) for c in CASES):
        raise SystemExit('sector bearings differ from TR 38.901 Table 7.8-1')
    mixes = conf14['mixes']
    mix = mixes['M2412']['weights']
    cxs = {c: case_context(cases[c], maps, pkt_off, sel_r) for c in CASES}
    freqs = cxs['A_equal_elements']['freqs']
    # ---- G4 mappings (as E1c.1 C5) and partition (checked after the run)
    grid = np.arange(-60.0, 120.0, 0.001)
    mono = {f'{c}/{tag}/{d}': bool(np.all(np.diff(np.asarray(cxs[c][tag](grid, d))) >= 0))
            for c in CASES for tag in ('se2', 'se3') for d in ('DL', 'UL')}
    validation['G4_mapping_partition'] = {'non_monotone': [k for k, v in mono.items() if not v],
                                          'partition_errors': 0, 'passed': all(mono.values())}
    # ---- inputs
    with np.load(E11_DIR / 'site_tx0_mask.npz') as z:
        mask = {k: z[k] for k in z.files}
    with np.load(GEO_DIR / 'site_tx0_indoor.npz') as z:
        geo = {k: z[k] for k in z.files}
    gnb = np.array(mask['gnb'], dtype=float)
    summary = {'n_realizations': n_real, 'n_candidates': N_CAND, 'settings': SETTINGS, 'isd': {}}
    g1a, g1, g2, g3 = {}, {}, {}, {}
    part_err = 0
    for isd in ISDS:
        t_isd = time.time()
        with np.load(site_files[isd]) as z:
            sites = {k: z[k] for k in z.files}
        S = len(sites['site_x'])
        sxyz = np.column_stack([sites['site_x'], sites['site_y'], sites['site_h']]).astype(float)
        site0_ok = bool(np.max(np.abs(sxyz[0] - gnb)) <= 1e-9)
        xy_o = np.column_stack([mask['x'], mask['y']])
        xy_i = np.column_stack([geo['x'], geo['y']])
        ring_o, ring_i = nearest_ring(xy_o, gnb, isd), nearest_ring(xy_i, gnb, isd)
        keep_i = ~sites['indoor_excluded']
        victims = {'out': np.flatnonzero(ring_o == 0), 'ind': np.flatnonzero((ring_i == 0) & keep_i)}
        pools = {'out': np.flatnonzero(ring_o <= 2), 'ind': np.flatnonzero((ring_i <= 2) & keep_i)}
        data = {'mask': mask, 'geo': geo, 'sites': sites, 'pools': pools, 'victims': victims}
        vb = {}
        for kind, idx in victims.items():
            if kind == 'out':
                vb[kind] = link_block(sxyz, mask['x'][idx], mask['y'][idx], mask['z'][idx], sites['los_out'][idx],
                                      np.zeros((len(idx), S)), freqs)
            else:
                vb[kind] = link_block(sxyz, geo['x'][idx], geo['y'][idx], geo['h_ut'][idx], sites['facade_los'][idx],
                                      sites['din'][idx], freqs)
            vb[kind]['sxyz'] = sxyz
        vg = {kind: {c: case_gains(vb[kind], cases[c], table, kind) for c in CASES} for kind in vb}
        sterm = {st: state_terms(st, vb['out' if st in ('outdoor', 'in_car') else 'ind'], freqs) for st in STATES}
        print(f'ISD {isd}: victims {[len(v) for v in victims.values()]}, pool {[len(v) for v in pools.values()]}',
              flush=True)
        # ---- G2
        g2[f'isd{isd}'] = {kind: g2_check(vb[kind], freqs) for kind in vb}
        print('G2', {k: v['passed'] for k, v in g2[f'isd{isd}'].items()}, flush=True)
        # ---- G1a: site-0 link budget = E1c.1 rows (deterministic part)
        rows_e1c = {kind: e1c_rows(data, kind, victims[kind], gnb, freqs) for kind in victims}
        worst = {'L0': 0.0, 'gain': 0.0, 'sigma': 0.0, 'weight': 0.0, 'd2d_m': 0.0}
        count_ok = True
        exact = {}
        for kind in victims:
            b, rw = vb[kind], rows_e1c[kind]
            vv, cc = np.nonzero(b['he_p'][:, 0, :] > 0.0)
            count_ok &= len(vv) == len(rw['w']) and np.array_equal(vv, rw['pid'])
            if not count_ok:
                continue
            worst['weight'] = max(worst['weight'], float(np.max(np.abs(b['he_p'][vv, 0, cc] - rw['w']))))
            worst['d2d_m'] = max(worst['d2d_m'], float(np.max(np.abs(b['d2'][vv, 0] - rw['d2d']))))
        for c in CASES:
            for st in STATES:
                kind = 'out' if st in ('outdoor', 'in_car') else 'ind'
                if not count_ok:
                    break
                b, rw = vb[kind], rows_e1c[kind]
                vv, cc = np.nonzero(b['he_p'][:, 0, :] > 0.0)
                ev = e1c.evaluate(cases[c], st, rw, VAR, maps, table, pkt_off, sel_r)
                pen, sig, sue = sterm[st]
                for fi in range(2):
                    aux = ev['aux'][fi]
                    mine = b['pl'][vv, 0, cc, fi] + pen[vv, 0, fi]
                    worst['L0'] = max(worst['L0'], float(np.max(np.abs(mine - aux['L0']))))
                    worst['gain'] = max(worst['gain'], float(np.max(np.abs(vg[kind][c][fi]['dl'][vv, 0] - aux['g_dl']))),
                                        float(np.max(np.abs(vg[kind][c][fi]['ul'][vv, 0] - aux['g_ul']))))
                    worst['sigma'] = max(worst['sigma'], float(np.max(np.abs(np.hypot(sig[vv, 0], sue) - aux['sig']))))
                for p in PROFILES:
                    cp, feas = e1c.probabilities(ev, p)
                    pv = {n: e1c.per_sample(rw, cp[n]) for n in PARTITION}
                    pv.update({n: e1c.per_sample(rw, feas[n]) for n in FEAS})
                    exact[(c, st, p)] = pv
        g1a[f'isd{isd}'] = dict(worst, site0_is_e11_gnb=site0_ok, rows_match=bool(count_ok),
                                passed=bool(site0_ok and count_ok and worst['L0'] <= 1e-9 and worst['gain'] <= 1e-9
                                            and worst['sigma'] <= 1e-12 and worst['weight'] <= 1e-12
                                            and worst['d2d_m'] <= 1e-6))
        print('G1a', g1a[f'isd{isd}']['passed'], flush=True)
        # ---- Monte Carlo
        nv = {st: vb['out' if st in ('outdoor', 'in_car') else 'ind']['n'] for st in STATES}
        counts = {(sn, c, st, p, n): np.zeros(nv[st], dtype=np.int32)
                  for sn in SETTINGS for c in CASES for st in STATES for p in PROFILES for n in NAMES}
        hists = {(sn, c, st, fi, mt): np.zeros(NB, dtype=np.int64)
                 for sn in SETTINGS for c in CASES for st in STATES for fi in range(2) for mt in METRICS}
        served0 = {(c, st): 0 for c in CASES for st in STATES}
        istat = {(c, fi, p): {'sum_p': 0.0, 'n': 0, 'at_cap': 0} for c in CASES for fi in range(2) for p in ('F2', 'F3')}
        no_intf = {c: 0 for c in CASES}
        g3_rec = {}
        for r in range(n_real):
            cand = draw_candidates(rng_for(seed, isd, r, 0), len(pools['out']), len(pools['ind']), N_CAND, S, mix)
            cb = candidate_block(cand, data, sxyz, pools, freqs)
            ulI = {}
            for c in CASES:
                Gc = candidate_gains(cb, cases[c], table)
                ui = ul_interference(cb, Gc, cxs[c], cand['usel'], S)
                ulI[c] = ui['I']
                no_intf[c] += int(np.sum(ui['sel'] < 0))
                for fi in range(2):
                    for p in ('F2', 'F3'):
                        pw = ui['P_target'][fi][p]
                        istat[(c, fi, p)]['sum_p'] += float(pw.sum())
                        istat[(c, fi, p)]['n'] += len(pw)
                        istat[(c, fi, p)]['at_cap'] += int(np.sum(pw >= cxs[c]['p_ue'][fi]))
            for si, st in enumerate(STATES):
                kind = 'out' if st in ('outdoor', 'in_car') else 'ind'
                b = vb[kind]
                z, u, x = draw_ue_terms(rng_for(seed, isd, r, 1 + si), b['n'], S)
                pen, sig, sue = sterm[st]
                L, _ = losses(b, pen, sig, sue, z, u, x)
                Llin = 10.0 ** (-L / 10.0)
                for c in CASES:
                    res = evaluate_case(b, vg[kind][c], L, Llin, ulI[c], cxs[c])
                    served0[(c, st)] += int(np.sum(res['primary']['k'] == 0))
                    if c == 'A_equal_elements' and r in G3_REALIZATIONS:
                        g3_rec[(r, st)] = res['primary']
                    for sn, rs in res.items():
                        fl = class_flags(rs, cxs[c])
                        for p in PROFILES:
                            tot = np.zeros(b['n'], dtype=np.int32)
                            for n in NAMES:
                                counts[(sn, c, st, p, n)] += fl[p][n]
                            for n in PARTITION:
                                tot += fl[p][n]
                            part_err += int(np.sum(tot != 1))
                        for fi in range(2):
                            hist_add(hists[(sn, c, st, fi, 'DL')], rs['dl'][fi])
                            hist_add(hists[(sn, c, st, fi, 'UL_F2')], rs['ul']['F2'][fi])
                            hist_add(hists[(sn, c, st, fi, 'UL_F3')], rs['ul']['F3'][fi])
            if r % 10 == 9:
                print(f'ISD {isd}: realization {r + 1}/{n_real}, {time.time() - t_isd:.0f} s', flush=True)
        # ---- G1: Monte-Carlo single_noise vs E1c.1 exact on the same victims
        fails, n_tests, worst_z = [], 0, 0.0
        for (c, st, p), pv in exact.items():
            for n in NAMES:
                pe = pv[n]
                mc = counts[('single_noise', c, st, p, n)] / n_real
                se = math.sqrt(float(np.sum(pe * (1.0 - pe))) / n_real) / len(pe)
                diff = abs(float(mc.mean()) - float(pe.mean()))
                n_tests += 1
                if se > 0:
                    worst_z = max(worst_z, diff / se)
                if diff > 5.0 * se + 1e-12:
                    fails.append(f'{c}/{st}/{p}/{n}: diff {diff:.5f} se {se:.5f}')
        g1[f'isd{isd}'] = {'tests': n_tests, 'max_diff_over_se': worst_z, 'failures': fails,
                           'passed': bool(exact) and not fails}
        print('G1', g1[f'isd{isd}']['passed'], f'max |diff|/SE {worst_z:.2f}', flush=True)
        # ---- G3: scalar recomputation of the primary setting, case A
        rg = np.random.default_rng(seed + 3)
        cache, worst3, assoc_bad, pairs = {}, 0.0, 0, []
        for _ in range(G3_PAIRS):
            r = int(rg.choice(G3_REALIZATIONS))
            st = STATES[int(rg.integers(len(STATES)))]
            vi = int(rg.integers(nv[st]))
            ref = scalar_primary(data, cases['A_equal_elements'], table, cxs['A_equal_elements'], seed, isd, r, st,
                                 vi, mix, cache)
            eng = g3_rec[(r, st)]
            assoc_bad += int(ref['k'] != int(eng['k'][vi]) or ref['t'] != int(eng['t'][vi]))
            d = [abs(ref['dl'][fi] - float(eng['dl'][fi][vi])) for fi in range(2)]
            d += [abs(ref['ul'][p][fi] - float(eng['ul'][p][fi][vi])) for p in PROFILES for fi in range(2)]
            worst3 = max(worst3, max(d))
            pairs.append([r, st, vi])
        g3[f'isd{isd}'] = {'pairs': len(pairs), 'association_mismatches': assoc_bad, 'max_abs_diff_db': worst3,
                           'passed': assoc_bad == 0 and worst3 <= 1e-9}
        print('G3', g3[f'isd{isd}']['passed'], f'{worst3:.2e} dB', flush=True)
        # ---- summary for this ISD
        def frac_block(getc, nst):
            fr = {n: float(getc(n).mean()) for n in NAMES}
            se = {n: math.sqrt(float(np.sum(getc(n) * (1 - getc(n)))) / n_real) / nst for n in NAMES}
            return fr, se
        res_isd = {}
        for sn in SETTINGS:
            res_isd[sn] = {}
            for c in CASES:
                per_state, se_state = {}, {}
                for st in STATES:
                    per_state[st], se_state[st] = {}, {}
                    for p in PROFILES:
                        fr, se = frac_block(lambda n: counts[(sn, c, st, p, n)] / n_real, nv[st])
                        per_state[st][p], se_state[st][p] = fr, se
                mx = {mn: {p: {n: sum(w * per_state[s_][p][n] for s_, w in mm['weights'].items()) for n in NAMES}
                           for p in PROFILES} for mn, mm in mixes.items()}
                mx_se = {mn: {p: {n: math.sqrt(sum(w ** 2 * se_state[s_][p][n] ** 2 for s_, w in mm['weights'].items()))
                                  for n in NAMES} for p in PROFILES} for mn, mm in mixes.items()}
                q = {}
                for st in STATES:
                    q[st] = {f'{freqs[fi] / 1e9:.1f}GHz': {mt: hist_quantiles(hists[(sn, c, st, fi, mt)]) for mt in METRICS}
                             for fi in range(2)}
                q['M2412'] = {f'{freqs[fi] / 1e9:.1f}GHz': {mt: hist_quantiles(sum(
                    w * hists[(sn, c, s_, fi, mt)] / hists[(sn, c, s_, fi, mt)].sum() for s_, w in mix.items()))
                    for mt in METRICS} for fi in range(2)}
                res_isd[sn][c] = {'per_state': per_state, 'mixes': mx, 'mixes_mc_se': mx_se, 'quantiles_db_5_50_95': q}
        e1c_same = {}
        for c in CASES:
            ps = {st: {p: {n: float(exact[(c, st, p)][n].mean()) for n in NAMES} for p in PROFILES} for st in STATES}
            e1c_same[c] = {'per_state': ps, 'mixes': {mn: {p: {n: sum(w * ps[s_][p][n] for s_, w in mm['weights'].items())
                                                             for n in NAMES} for p in PROFILES}
                                                      for mn, mm in mixes.items()}}
        edge = {f'{sn}|{c}|{st}|{fi}|{mt}': [int(h[0]), int(h[-1])] for (sn, c, st, fi, mt), h in hists.items()
                if h[0] or h[-1]}
        summary['isd'][str(isd)] = {
            'sites': {'kinds': {k: int(np.sum(sites['site_kind'] == k)) for k in ('site0', 'rooftop', 'mast', 'virtual')},
                      'height_m': sites['site_h'].round(2).tolist()},
            'victims': {'outdoor_points': len(victims['out']), 'indoor_samples': len(victims['ind'])},
            'candidate_pool': {'outdoor_points': len(pools['out']), 'indoor_samples': len(pools['ind'])},
            'served_by_site0': {c: {st: served0[(c, st)] / (nv[st] * n_real) for st in STATES} for c in CASES},
            'trxp_without_interferer_mean': {c: no_intf[c] / n_real for c in CASES},
            'interferer_target_power': {c: {f'{freqs[fi] / 1e9:.1f}GHz': {p: {
                'mean_dbm': istat[(c, fi, p)]['sum_p'] / max(istat[(c, fi, p)]['n'], 1),
                'share_at_23dbm': istat[(c, fi, p)]['at_cap'] / max(istat[(c, fi, p)]['n'], 1)}
                for p in ('F2', 'F3')} for fi in range(2)} for c in CASES},
            'histogram_edge_counts': edge,
            'settings': res_isd, 'e1c1_exact_same_victims': e1c_same}
        raw = {'victim_out_index': victims['out'], 'victim_ind_index': victims['ind']}
        for (sn, c, st, p, n), v in counts.items():
            if sn in RAW_SETTINGS:
                raw[f'{sn}|{c}|{st}|{p}|{n}'] = v.astype(np.int16)
        np.savez_compressed(outdir / 'raw' / f'counts_isd{isd}.npz', **raw)
        print(f'ISD {isd} done in {time.time() - t_isd:.0f} s', flush=True)
    validation['G1a_site0_link_budget'] = dict(g1a, passed=all(v['passed'] for v in g1a.values()))
    validation['G1_single_site_limit'] = dict(g1, passed=all(v['passed'] for v in g1.values()))
    validation['G2_path_loss'] = dict(g2, passed=all(v['passed'] for d in g2.values() for v in d.values()))
    validation['G3_scalar_recompute'] = dict(g3, passed=all(v['passed'] for v in g3.values()))
    validation['G4_mapping_partition']['partition_errors'] = part_err
    validation['G4_mapping_partition']['passed'] = bool(validation['G4_mapping_partition']['passed'] and part_err == 0)
    gates_ok = all(v['passed'] for v in validation.values())
    validation['all_checks_passed'] = gates_ok
    # ---- prediction P1
    qa = summary['isd']['200']['settings']
    sinr = qa['primary']['A_equal_elements']['quantiles_db_5_50_95']['M2412']['7.2GHz']['DL'][1]
    snr = qa['noise_multi']['A_equal_elements']['quantiles_db_5_50_95']['M2412']['7.2GHz']['DL'][1]
    summary['predictions'] = {'P1': {'median_dl_sinr_7p2_primary_db': sinr, 'median_dl_snr_7p2_noise_multi_db': snr,
                                     'held': sinr <= snr - 3.0}}
    summary['decision'] = conf['decision_rules']['all_gates_pass' if gates_ok else 'gate_fails']
    summary['wall_time_s'] = time.time() - t_start
    (outdir / 'summary.json').write_text(json.dumps(summary, indent=1, default=float), encoding='utf-8')
    (outdir / 'validation.json').write_text(json.dumps(validation, indent=2, default=float), encoding='utf-8')
    manifest = {'generated_utc': datetime.now(timezone.utc).isoformat(), 'tool': 'scripts/e1c2_interference.py',
                'git_head_at_run': args.git_head, 'python': platform.python_version(), 'numpy': np.__version__,
                'config_sha256': sha256(args.config), 'input_sha256': in_sha,
                'outputs_sha256': {'summary.json': sha256(outdir / 'summary.json'),
                                   **{f'raw/counts_isd{isd}.npz': sha256(outdir / 'raw' / f'counts_isd{isd}.npz')
                                      for isd in ISDS}},
                'all_checks_passed': gates_ok}
    (outdir / 'manifest.json').write_text(json.dumps(manifest, indent=2), encoding='utf-8')
    for k, v in validation.items():
        if isinstance(v, dict):
            print(k, 'PASS' if v['passed'] else 'FAIL')
    print('E1c.2', 'GATES PASSED' if gates_ok else 'GATES FAILED', f'{time.time() - t_start:.0f} s')
    sys.exit(0 if gates_ok else 1)


if __name__ == '__main__':
    main()
