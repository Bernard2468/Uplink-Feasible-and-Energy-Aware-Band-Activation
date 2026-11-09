#!/usr/bin/env python3
"""E1c.3: directional (per-beam) interference in the E1c.2 19-site layout (Monte Carlo).

Implements configs/e1/e1c3_directional.json. Run inside WSL with the project venv (scipy).
Reuses scripts/e1c2_interference.py unchanged (geometry, victims, candidates, association, losses,
E1.5.5 V2 gains, thresholds, UL power rules, random streams), so every E1c.2 draw is reproduced and
every comparison with E1c.2 is paired.

Beam model: each TRxP is a rows x cols half-wavelength UPA per sector, ideally steered (E1.4) toward its
target: DL, every TRxP toward its own scheduled UE; UL, every serving TRxP toward its victim. A beam's
power gain toward a link is element x [a g + (1 - a)], g = |AF|^2 / N (peak N, direction mean 1); a is
calibrated per link so that a beam's gain toward its own target equals the V2 serving gain
(a N + 1 - a = N 10^(-D/10)). Brackets: a = 0 (E1c.2 average), a = 1 (pure geometric pattern).
Outputs in --outdir: summary.json, validation.json, manifest.json; raw/counts_isd<ISD>.npz (git-ignored).
"""
import argparse
import cmath
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
import e1c2_interference as m2  # noqa: E402
from cmwave.radio import antenna as ant  # noqa: E402

CONFIG = ROOT / 'configs/e1/e1c3_directional.json'
R = m2.R
E1C2_DIR, OUTDIR = R / 'e1c2_interference', R / 'e1c3_directional'
ISDS, CASES, STATES, PROFILES = m2.ISDS, m2.CASES, m2.STATES, m2.PROFILES
PARTITION, FEAS, NAMES = m2.PARTITION, m2.FEAS, m2.NAMES
BEARINGS, N_SECT = m2.BEARINGS, m2.N_SECT
STREAM_ACT, STREAM_SYN = 10, 11
N_PROPOSALS = 512
UE_H = 1.5
QS = (0.01, 0.05, 0.5, 0.95)
H4_PAIRS = 50
H4_REALIZATIONS = (0, 1)


def _pb(beam='sd', eta=1.0, act='bern', ul='target', act72='all', stress=False):
    return {'beam': beam, 'eta': eta, 'act': act, 'ul': ul, 'act72': act72, 'stress': stress}


PB = {
    'sd': _pb(),
    'geo': _pb(beam='geo'),
    'avg_pb': _pb(beam='avg'),
    'sd_eta0p5_bern': _pb(eta=0.5), 'sd_eta0p5_mean': _pb(eta=0.5, act='mean'),
    'sd_eta0p2_bern': _pb(eta=0.2), 'sd_eta0p2_mean': _pb(eta=0.2, act='mean'),
    'sd_ul_off': _pb(ul='off'), 'sd_ul_full': _pb(ul='full'), 'geo_ul_full': _pb(beam='geo', ul='full'),
    'sd_iso72': _pb(act72='isolated'),
    'sd_stress': _pb(stress=True),
}
ALL_SETTINGS = list(m2.SETTINGS) + list(PB)
RAW_SETTINGS = ('primary', 'sd', 'geo', 'sd_stress')
PAIRS = {'sd_minus_avg': ('sd', 'primary'), 'geo_minus_avg': ('geo', 'primary'),
         'sd_ul_full_minus_ul_full': ('sd_ul_full', 'ul_full'), 'sd_minus_sd_iso72': ('sd', 'sd_iso72'),
         'eta0p5_bern_minus_mean': ('sd_eta0p5_bern', 'sd_eta0p5_mean'),
         'eta0p2_bern_minus_mean': ('sd_eta0p2_bern', 'sd_eta0p2_mean')}
PAIR_PROFILES = ('F2', 'F3')


# ---------------------------------------------------------------- pure helpers (unit-tested)
def dirichlet_power(x, n):
    """|sum_{m<n} exp(j m x)|^2 / n: peak n at x = 0 (mod 2 pi), mean 1 over x."""
    x = np.asarray(x, dtype=float)
    s = np.sin(0.5 * x)
    small = np.abs(s) < 1e-9
    with np.errstate(divide='ignore', invalid='ignore'):
        val = np.sin(0.5 * n * x) ** 2 / (n * s ** 2)
    return np.where(small, float(n), val)


def array_power_gain(zen_e, az_e, zen_s, az_s, bearing, rows, cols):
    """Normalized power pattern g = |AF|^2 / N toward (zen_e, az_e) of a rows x cols half-wavelength UPA
    (rows along z, columns along the sector's local y axis) ideally steered to (zen_s, az_s); degrees."""
    te, ts = np.radians(zen_e), np.radians(zen_s)
    pe, ps = np.radians(np.asarray(az_e) - bearing), np.radians(np.asarray(az_s) - bearing)
    u = np.pi * (np.cos(te) - np.cos(ts))
    w = np.pi * (np.sin(te) * np.sin(pe) - np.sin(ts) * np.sin(ps))
    return dirichlet_power(u, rows) * dirichlet_power(w, cols)


def array_power_gain_explicit(zen_e, az_e, zen_s, az_s, bearing, rows, cols):
    """The same pattern as an explicit phasor sum over the elements (gate H2 reference)."""
    te, ts = np.radians(np.atleast_1d(zen_e)), np.radians(np.atleast_1d(zen_s))
    pe = np.radians(np.atleast_1d(az_e) - bearing)
    ps = np.radians(np.atleast_1d(az_s) - bearing)
    du = np.cos(te) - np.cos(ts)
    dw = np.sin(te) * np.sin(pe) - np.sin(ts) * np.sin(ps)
    m, n = np.arange(rows), np.arange(cols)
    ph = np.pi * (du[:, None, None] * m[None, :, None] + dw[:, None, None] * n[None, None, :])
    return np.abs(np.exp(1j * ph).sum(axis=(1, 2))) ** 2 / (rows * cols)


def specular_fraction(n_el, deficit_db):
    """a with a N + (1 - a) = N 10^(-D/10)."""
    a = (n_el * 10.0 ** (-np.asarray(deficit_db, dtype=float) / 10.0) - 1.0) / (n_el - 1.0)
    if np.any(a < -1e-12) or np.any(a > 1.0 + 1e-12):
        raise ValueError('deficit outside [0, 10 log10 N] dB')
    return np.clip(a, 0.0, 1.0)


def specular_from_gains(E, g_ul, n_el):
    """Per link: a from the V2 deficit D = ideal gain (best element + 10 log10 N) - V2 UL gain."""
    return specular_fraction(n_el, E.max(axis=-1) + 10.0 * math.log10(n_el) - g_ul)


def beam_fraction(beam, a_sd):
    if beam == 'sd':
        return a_sd
    return np.ones_like(a_sd) if beam == 'geo' else np.zeros_like(a_sd)


def activity(st, uni):
    if st['act'] == 'mean':
        return np.full(len(uni), float(st['eta']))
    return (np.asarray(uni) < st['eta']).astype(float)


def case_dims(case):
    cs = sorted(case['carriers'], key=lambda c: c['frequency_hz'])
    return [(int(c['gnb_panel']['rows']), int(c['gnb_panel']['cols'])) for c in cs]


def draw_activity(rng, n_trxp):
    return rng.random(n_trxp), rng.random(n_trxp)


def synthetic_targets(rng, sites, origin, isd, n_prop=N_PROPOSALS):
    """One uniform position (h 1.5 m) per TRxP in its site's nominal hexagonal cell and its sector
    (best element gain); the DL target of a TRxP serving no candidate. Fixed draw count per site."""
    S = len(sites['site_x'])
    a1, a2 = np.array([isd, 0.0]), np.array([isd / 2.0, isd * math.sqrt(3.0) / 2.0])
    nb = (a1, -a1, a2, -a2, a2 - a1, a1 - a2)
    rad = isd / math.sqrt(3.0)
    u1, u2 = rng.random((S, n_prop)), rng.random((S, n_prop))
    out, fallback = np.zeros((N_SECT * S, 3)), 0
    for k in range(S):
        c = np.asarray(origin[:2], dtype=float) + float(sites['site_i'][k]) * a1 + float(sites['site_j'][k]) * a2
        r, th = rad * np.sqrt(u1[k]), 2.0 * np.pi * u2[k]
        p = c + np.column_stack([r * np.cos(th), r * np.sin(th)])
        d0 = np.hypot(p[:, 0] - c[0], p[:, 1] - c[1])
        inside = np.all([d0 <= np.hypot(p[:, 0] - c[0] - v[0], p[:, 1] - c[1] - v[1]) for v in nb], axis=0)
        sxyz = np.array([sites['site_x'][k], sites['site_y'][k], sites['site_h'][k]], dtype=float)
        zen, az = ant.link_angles(sxyz, np.column_stack([p, np.full(n_prop, UE_H)]))
        sb = np.argmax(ant.element_gain_dbi(zen[:, None], az[:, None] - BEARINGS), axis=1)
        for s in range(N_SECT):
            ok = np.flatnonzero(inside & (sb == s))
            if len(ok):
                out[N_SECT * k + s] = (p[ok[0], 0], p[ok[0], 1], UE_H)
            else:
                fallback += 1
                b = math.radians(BEARINGS[s])
                out[N_SECT * k + s] = (c[0] + 0.5 * rad * math.cos(b), c[1] + 0.5 * rad * math.sin(b), UE_H)
    return out, fallback


def target_positions(cb, sel, data, syn):
    pos = syn.copy()
    for t in np.flatnonzero(sel >= 0):
        m = int(sel[t])
        i = int(cb['sample'][m])
        if cb['is_out'][m]:
            pos[t] = (data['mask']['x'][i], data['mask']['y'][i], data['mask']['z'][i])
        else:
            pos[t] = (data['geo']['x'][i], data['geo']['y'][i], data['geo']['h_ut'][i])
    return pos


def steering(sxyz, targets):
    k = np.arange(len(targets)) // N_SECT
    dx, dy, dz = targets[:, 0] - sxyz[k, 0], targets[:, 1] - sxyz[k, 1], targets[:, 2] - sxyz[k, 2]
    return np.degrees(np.arctan2(np.hypot(dx, dy), dz)), np.degrees(np.arctan2(dy, dx))


# ---------------------------------------------------------------- per-beam evaluation
def case_beam_context(cb, Gc, ui, case, cx, data, sxyz, syn, act_dl, act_ul):
    dims = case_dims(case)
    n_el = [r * c for r, c in dims]
    sel = ui['sel']
    have = np.flatnonzero(sel >= 0)
    zs, az = steering(sxyz, target_positions(cb, sel, data, syn))
    return {'dims': dims, 'n_el': n_el, 'have': have, 'u': sel[have], 'zen_s': zs, 'az_s': az,
            'P_target': [{p: ui['P_target'][fi][p] for p in ('F2', 'F3')} for fi in range(len(dims))],
            'ca': [specular_from_gains(cb['E'], Gc[fi]['ul'], n_el[fi]) for fi in range(len(dims))],
            'czen': cb['zen'], 'caz': cb['az'], 'cElin': cb['Elin'], 'cLlin': cb['Llin'],
            'act_dl': act_dl, 'act_ul': act_ul, 'n_synthetic': int(np.sum(sel < 0))}


def evaluate_pb(b, G, L, Llin, cx, va, cc, settings=PB):
    """Per-beam SINR [dB] per setting: DL per carrier, UL per profile and carrier; serving site and TRxP."""
    n, S, F = L.shape
    T3 = S * N_SECT
    rows = np.arange(n)
    kk = np.argmax(G[0]['ul'] - L[:, :, 0], axis=1)
    ss = b['sb'][rows, kk]
    tv = N_SECT * kk + ss
    notown = np.ones((n, T3), dtype=bool)
    notown[rows, tv] = False
    elin = b['Elin'].reshape(n, T3)
    dims = cc['dims']
    zs, as_ = cc['zen_s'].reshape(S, N_SECT), cc['az_s'].reshape(S, N_SECT)
    gdl = {d: array_power_gain(b['zen'][..., None], b['az'][..., None], zs[None], as_[None], BEARINGS, *d).reshape(n, T3)
           for d in set(dims)}
    have, u = cc['have'], cc['u']
    nU = len(have)
    keep = have[None, :] != tv[:, None]
    gul = {d: array_power_gain(cc['czen'][u[None, :], kk[:, None]], cc['caz'][u[None, :], kk[:, None]],
                               b['zen'][rows, kk][:, None], b['az'][rows, kk][:, None], BEARINGS[ss][:, None], *d)
           for d in set(dims)}
    erx = cc['cElin'][u[None, :], kk[:, None], ss[:, None]]
    s_dl = [cx['p_dl'][fi] + G[fi]['dl'][rows, kk] - L[rows, kk, fi] for fi in range(F)]
    s_ul = [cx['p_ue'][fi] + G[fi]['ul'][rows, kk] - L[rows, kk, fi] for fi in range(F)]
    cache = {}

    def dl_parts(beam, fi):
        key = ('dl', beam, fi)
        if key not in cache:
            a = np.repeat(beam_fraction(beam, va[fi]), N_SECT, axis=1)
            base = elin * np.repeat(Llin[:, :, fi], N_SECT, axis=1) * notown
            cache[key] = (base, a * gdl[dims[fi]] + (1.0 - a), a * cc['n_el'][fi] + (1.0 - a))
        return cache[key]

    def ul_parts(beam, fi):
        key = ('ul', beam, fi)
        if key not in cache:
            a = beam_fraction(beam, cc['ca'][fi][u[None, :], kk[:, None]])
            base = erx * cc['cLlin'][u[None, :], kk[:, None], fi] * keep
            cache[key] = (base, a * gul[dims[fi]] + (1.0 - a), a * cc['n_el'][fi] + (1.0 - a))
        return cache[key]

    out = {}
    for name, st in settings.items():
        dl, ul = [], {p: [] for p in PROFILES}
        for fi in range(F):
            off = st['act72'] == 'isolated' and fi == 1
            i_dl = 0.0
            if not off:
                base, fac, peak = dl_parts(st['beam'], fi)
                w = base * activity(st, cc['act_dl'])[None, :]
                i_dl = (w * fac).sum(axis=1)
                if st['stress']:
                    j = np.argmax(w, axis=1)
                    i_dl = i_dl + w[rows, j] * (peak[rows, j] - fac[rows, j])
                i_dl = 10.0 ** (cx['p_dl'][fi] / 10.0) * i_dl
            dl.append(s_dl[fi] - 10.0 * np.log10(10.0 ** (cx['n_dl'][fi] / 10.0) + i_dl))
            for p in PROFILES:
                i_ul = 0.0
                if st['ul'] != 'off' and not off and nU:
                    base, fac, peak = ul_parts(st['beam'], fi)
                    pk = 'F3' if p == 'F3' else 'F2'
                    pw = (10.0 ** (cc['P_target'][fi][pk] / 10.0) if st['ul'] == 'target'
                          else np.full(nU, 10.0 ** (cx['p_ue'][fi] / 10.0)))
                    w = base * (pw * activity(st, cc['act_ul'])[have])[None, :]
                    i_ul = (w * fac).sum(axis=1)
                    if st['stress']:
                        j = np.argmax(w, axis=1)
                        i_ul = i_ul + w[rows, j] * (peak[rows, j] - fac[rows, j])
                    if p == 'F1':
                        i_ul = i_ul / cx['n_prb'][fi]
                ul[p].append(s_ul[fi] - 10.0 * np.log10(10.0 ** (cx['n_ul'][fi][p] / 10.0) + i_ul))
        out[name] = {'dl': dl, 'ul': ul, 'k': kk, 't': tv}
    return out


# ---------------------------------------------------------------- independent scalar implementation (gate H4)
def s_pattern(zen_e, az_e, zen_s, az_s, bearing, rows, cols):
    te, ts = math.radians(zen_e), math.radians(zen_s)
    pe, ps = math.radians(az_e - bearing), math.radians(az_s - bearing)
    du = math.cos(te) - math.cos(ts)
    dw = math.sin(te) * math.sin(pe) - math.sin(ts) * math.sin(ps)
    acc = 0j
    for mm in range(rows):
        for nn in range(cols):
            acc += cmath.exp(1j * math.pi * (mm * du + nn * dw))
    return abs(acc) ** 2 / (rows * cols)


def s_spec(case, fi, los, kind, table):
    c = sorted(case['carriers'], key=lambda c: c['frequency_hz'])[fi]
    rows, cols = c['gnb_panel']['rows'], c['gnb_panel']['cols']
    n_el = rows * cols
    if case['regime'].split()[0] == 'C':
        d = 0.0 if los else 4.5
    else:
        stratum = ('o2o_los' if los else 'o2o_nlos') if kind == 'out' else 'o2i'
        d = float(table[({(8, 16): 'a', (16, 32): 'b'}[(rows, cols)], float(c['frequency_hz']), stratum)]['D_cb_db'])
    return (n_el * 10.0 ** (-d / 10.0) - 1.0) / (n_el - 1.0), n_el, rows, cols


def scalar_pb(data, case, table, cx, seed, isd, r, state, vi, mix, cache, origin):
    """Independent scalar sd and sd_stress SINRs (eta 1, UL target power) for victim vi in realization r."""
    m2.scalar_primary(data, case, table, cx, seed, isd, r, state, vi, mix, cache)
    cand, ues, sel = cache[(isd, r)]
    s = data['sites']
    S = len(s['site_x'])
    sites = [(float(s['site_x'][k]), float(s['site_y'][k]), float(s['site_h'][k])) for k in range(S)]
    freqs = [float(c['frequency_hz']) for c in sorted(case['carriers'], key=lambda c: c['frequency_hz'])]
    syn, _ = synthetic_targets(m2.rng_for(seed, isd, r, STREAM_SYN), s, origin, isd)
    kind = 'out' if state in ('outdoor', 'in_car') else 'ind'
    z, u, x = m2.draw_ue_terms(m2.rng_for(seed, isd, r, 1 + STATES.index(state)), len(data['victims'][kind]), S)
    ue = m2.s_ue(data, kind, int(data['victims'][kind][vi]))
    lk = [[None] * S for _ in freqs]
    gk = [[None] * S for _ in freqs]
    ang = [None] * S
    for fi, f in enumerate(freqs):
        for k in range(S):
            L, zen, az = m2.s_link(ue, sites[k], f, state, z[vi, k], u[vi, k], x[vi], k)
            lk[fi][k], gk[fi][k], ang[k] = L, m2.s_gains(case, fi, zen, az, ue[3][k], kind, table), (zen, az)
    ks = max(range(S), key=lambda k: (gk[0][k][0] - lk[0][k], -k))
    sv = gk[0][ks][2]
    t_v = N_SECT * ks + sv
    steer = {}
    for t in range(N_SECT * S):
        tx = ues[sel[t]][0][:3] if t in sel else tuple(float(v) for v in syn[t])
        sx, sy, sh = sites[t // N_SECT]
        steer[t] = (math.degrees(math.atan2(math.hypot(tx[0] - sx, tx[1] - sy), tx[2] - sh)),
                    math.degrees(math.atan2(tx[1] - sy, tx[0] - sx)))
    bear = (30.0, 150.0, 270.0)
    out = {'sd': {'dl': [], 'ul': {p: [] for p in PROFILES}}, 'sd_stress': {'dl': [], 'ul': {p: [] for p in PROFILES}},
           'k': ks, 't': t_v}
    for fi, f in enumerate(freqs):
        p_dl = cx['p_dl'][fi]
        terms = []
        for k in range(S):
            a_k, n_el, rr, cc_ = s_spec(case, fi, ue[3][k], kind, table)
            for sct in range(N_SECT):
                t = N_SECT * k + sct
                if t == t_v:
                    continue
                g = s_pattern(ang[k][0], ang[k][1], steer[t][0], steer[t][1], bear[sct], rr, cc_)
                w = 10.0 ** ((p_dl + gk[fi][k][3][sct] - lk[fi][k]) / 10.0)
                terms.append((w, a_k * g + 1.0 - a_k, a_k * n_el + 1.0 - a_k))
        jmax = max(range(len(terms)), key=lambda j: (terms[j][0], -j))
        nb = cx['n_prb'][fi] * 12 * 30e3
        n_dl = 10.0 ** (m2.s_noise(nb, 7.0) / 10.0)
        s_dl = p_dl + gk[fi][ks][1] - lk[fi][ks]
        i_sd = sum(w * fc for w, fc, _ in terms)
        i_st = sum((pk if j == jmax else fc) * w for j, (w, fc, pk) in enumerate(terms))
        out['sd']['dl'].append(s_dl - 10.0 * math.log10(n_dl + i_sd))
        out['sd_stress']['dl'].append(s_dl - 10.0 * math.log10(n_dl + i_st))
        noise = {'F1': m2.s_noise(12 * 30e3, 5.0), 'F2': m2.s_noise(nb, 5.0), 'F3': m2.s_noise(cx['n_max'][fi] * 12 * 30e3, 5.0)}
        uterms = {'F2': [], 'F3': []}
        for t in sorted(sel):
            if t == t_v:
                continue
            mi = sel[t]
            ue_i, st_i, kind_i = ues[mi]
            k_own = t // N_SECT
            l_own, zo, ao = m2.s_link(ue_i, sites[k_own], f, st_i, cand['z'][mi, k_own], cand['u'][mi, k_own], cand['x'][mi], k_own)
            g_own = m2.s_gains(case, fi, zo, ao, ue_i[3][k_own], kind_i, table)[0]
            l_rx, zr, ar = m2.s_link(ue_i, sites[ks], f, st_i, cand['z'][mi, ks], cand['u'][mi, ks], cand['x'][mi], ks)
            e_rx = m2.s_element(zr, ar - bear[sv])
            a_u, n_el, rr, cc_ = s_spec(case, fi, ue_i[3][ks], kind_i, table)
            g = s_pattern(zr, ar, ang[ks][0], ang[ks][1], bear[sv], rr, cc_)
            for p in ('F2', 'F3'):
                pw = min(23.0, cx['th'][fi][(p, 'UL')] + noise[p] + l_own - g_own)
                uterms[p].append((10.0 ** ((pw + e_rx - l_rx) / 10.0), a_u * g + 1.0 - a_u, a_u * n_el + 1.0 - a_u))
        s_ul = 23.0 + gk[fi][ks][0] - lk[fi][ks]
        for p in PROFILES:
            tl = uterms['F3' if p == 'F3' else 'F2']
            scale = 1.0 / cx['n_prb'][fi] if p == 'F1' else 1.0
            i_sd = sum(w * fc for w, fc, _ in tl) * scale
            if tl:
                jm = max(range(len(tl)), key=lambda j: (tl[j][0], -j))
                i_st = sum((pk if j == jm else fc) * w for j, (w, fc, pk) in enumerate(tl)) * scale
            else:
                i_st = 0.0
            out['sd']['ul'][p].append(s_ul - 10.0 * math.log10(10.0 ** (noise[p] / 10.0) + i_sd))
            out['sd_stress']['ul'][p].append(s_ul - 10.0 * math.log10(10.0 ** (noise[p] / 10.0) + i_st))
    return out


# ---------------------------------------------------------------- gates H2, H3
def h2_check(rng, n=20000):
    worst, peak_err, mean_err = 0.0, 0.0, 0.0
    for rows, cols in ((8, 16), (16, 32)):
        ze, zs = rng.uniform(20.0, 160.0, n), rng.uniform(20.0, 160.0, n)
        ae, as_ = rng.uniform(-180.0, 180.0, n), rng.uniform(-180.0, 180.0, n)
        bearing = rng.choice(BEARINGS, n)
        g = array_power_gain(ze, ae, zs, as_, bearing, rows, cols)
        ref = array_power_gain_explicit(ze, ae, zs, as_, bearing, rows, cols)
        worst = max(worst, float(np.max(np.abs(g - ref) / np.maximum(ref, 1.0))))
        peak_err = max(peak_err, float(np.max(np.abs(array_power_gain(zs, as_, zs, as_, bearing, rows, cols) - rows * cols))))
        k = 64
        grid = 2.0 * np.pi * np.arange(k) / k
        mean = float(np.mean(dirichlet_power(grid[:, None], rows) * dirichlet_power(grid[None, :], cols)))
        mean_err = max(mean_err, abs(mean - 1.0))
    return {'max_rel_diff': worst, 'peak_abs_err': peak_err, 'grid_mean_err': mean_err,
            'passed': worst <= 1e-9 and peak_err <= 1e-9 * 512 and mean_err <= 1e-12}


def h3_check(cases, table):
    worst, bad = 0.0, []
    for cname, case in cases.items():
        for fi, (rows, cols) in enumerate(case_dims(case)):
            for kind, los in (('out', True), ('out', False), ('ind', False), ('ind', True)):
                a, n_el, _, _ = s_spec(case, fi, los, kind, table)
                c = sorted(case['carriers'], key=lambda c: c['frequency_hz'])[fi]
                if case['regime'].split()[0] == 'C':
                    d = 0.0 if los else 4.5
                else:
                    stratum = ('o2o_los' if los else 'o2o_nlos') if kind == 'out' else 'o2i'
                    d = float(table[({(8, 16): 'a', (16, 32): 'b'}[(rows, cols)], float(c['frequency_hz']), stratum)]['D_cb_db'])
                a_v = float(specular_fraction(n_el, d))
                err = abs(10.0 * math.log10(a_v * n_el + 1.0 - a_v) - (10.0 * math.log10(n_el) - d))
                worst = max(worst, err, abs(a_v - a))
                if not 0.0 <= a_v <= 1.0:
                    bad.append(f'{cname}/{fi}/{kind}/{los}')
    return {'max_abs_err_db': worst, 'out_of_range': bad, 'passed': worst <= 1e-9 and not bad}


# ---------------------------------------------------------------- aggregation
def aggregate(counts, hists, settings, nv, n_real, mixes, mix, freqs):
    res = {}
    for sn in settings:
        res[sn] = {}
        for c in CASES:
            per_state, se_state = {}, {}
            for st in STATES:
                per_state[st], se_state[st] = {}, {}
                for p in PROFILES:
                    fr, se = {}, {}
                    for n in NAMES:
                        v = counts[(sn, c, st, p, n)] / n_real
                        fr[n] = float(v.mean())
                        se[n] = math.sqrt(float(np.sum(v * (1 - v))) / n_real) / nv[st]
                    per_state[st][p], se_state[st][p] = fr, se
            mx = {mn: {p: {n: sum(w * per_state[s_][p][n] for s_, w in mm['weights'].items()) for n in NAMES}
                       for p in PROFILES} for mn, mm in mixes.items()}
            mx_se = {mn: {p: {n: math.sqrt(sum(w ** 2 * se_state[s_][p][n] ** 2 for s_, w in mm['weights'].items()))
                              for n in NAMES} for p in PROFILES} for mn, mm in mixes.items()}
            q = {st: {f'{freqs[fi] / 1e9:.1f}GHz': {mt: m2.hist_quantiles(hists[(sn, c, st, fi, mt)], QS)
                                                    for mt in m2.METRICS} for fi in range(2)} for st in STATES}
            q['M2412'] = {f'{freqs[fi] / 1e9:.1f}GHz': {mt: m2.hist_quantiles(sum(
                w * hists[(sn, c, s_, fi, mt)] / hists[(sn, c, s_, fi, mt)].sum() for s_, w in mix.items()), QS)
                for mt in m2.METRICS} for fi in range(2)}
            res[sn][c] = {'per_state': per_state, 'mixes': mx, 'mixes_mc_se': mx_se, 'quantiles_db_1_5_50_95': q}
    return res


def pair_summary(pairs, nv, n_real, mixes):
    out = {}
    for pn in PAIRS:
        out[pn] = {}
        for c in CASES:
            per_state, se_state = {}, {}
            for st in STATES:
                per_state[st], se_state[st] = {}, {}
                for p in PAIR_PROFILES:
                    d, s = {}, {}
                    for n in NAMES:
                        n10, n01 = pairs[(pn, c, st, p, n)]
                        dv = (n10.astype(float) - n01) / n_real
                        var = (n10.astype(float) + n01) / n_real - dv ** 2
                        d[n] = float(dv.mean())
                        s[n] = math.sqrt(float(np.sum(var)) / n_real) / nv[st]
                    per_state[st][p], se_state[st][p] = d, s
            out[pn][c] = {mn: {p: {n: {'diff': sum(w * per_state[s_][p][n] for s_, w in mm['weights'].items()),
                                       'se': math.sqrt(sum(w ** 2 * se_state[s_][p][n] ** 2 for s_, w in mm['weights'].items()))}
                                   for n in NAMES} for p in PAIR_PROFILES} for mn, mm in mixes.items()}
    return out


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
    conf2 = json.loads(m2.CONFIG.read_text(encoding='utf-8'))
    conf14 = json.loads(m2.e155.E14_CONFIG.read_text(encoding='utf-8'))
    seed, n_real = int(conf['settings']['seed']), int(conf['settings']['realizations'])
    if seed != int(conf2['settings']['seed']) or n_real != int(conf2['settings']['realizations']):
        raise SystemExit('E1c.3 must use the E1c.2 seed and realization count (paired comparison)')
    outdir = Path(args.outdir)
    (outdir / 'raw').mkdir(parents=True, exist_ok=True)
    validation = {}
    # ---- H0 inputs
    man2 = json.loads((E1C2_DIR / 'manifest.json').read_text())
    sman = json.loads((m2.SITES_DIR / 'e1c2_geometry_manifest.json').read_text())
    raw2 = {isd: E1C2_DIR / 'raw' / f'counts_isd{isd}.npz' for isd in ISDS}
    site_files = {isd: m2.SITES_DIR / f'e1c2_sites_isd{isd}.npz' for isd in ISDS}
    h0 = (man2['all_checks_passed'] and sman['all_checks_passed']
          and sha256(m2.CONFIG) == man2['config_sha256']
          and sha256(E1C2_DIR / 'summary.json') == man2['outputs_sha256']['summary.json']
          and all(p.exists() and sha256(p) == man2['outputs_sha256'][f'raw/{p.name}'] for p in raw2.values())
          and all(sha256(p) == sman['files'][p.name] for p in site_files.values())
          and all(sha256(R / k) == v for k, v in man2['input_sha256'].items()))
    in_sha = {'e1c2_interference/manifest.json': sha256(E1C2_DIR / 'manifest.json'),
              'e1c2_interference/summary.json': sha256(E1C2_DIR / 'summary.json')}
    in_sha.update({f'e1c2_interference/raw/{p.name}': sha256(p) for p in raw2.values() if p.exists()})
    in_sha.update(man2['input_sha256'])
    validation['H0_inputs'] = {'input_sha256': in_sha, 'passed': bool(h0)}
    if not h0:
        raise SystemExit('H0 failed')
    s2 = json.loads((E1C2_DIR / 'summary.json').read_text())
    s153 = json.loads((m2.e155.E153_DIR / 'summary.json').read_text())
    s153a = json.loads((m2.e155.E153A_DIR / 'summary.json').read_text())
    s1521 = json.loads((m2.e155.E1521_DIR / 'summary.json').read_text())
    s154 = json.loads((m2.e155.E154_DIR / 'summary.json').read_text())
    maps = m2.e155.build_mappings(s153a, s153)
    table = m2.e155.gain_table(s1521)
    pkt_off = m2.e155.packet_offsets(s154)
    sel_r = {d: v['factor'] for d, v in m2.e155.selectivity_factors(s154).items()}
    cases = {c: conf14['cases'][c] for c in CASES}
    mixes = conf14['mixes']
    mix = mixes['M2412']['weights']
    cxs = {c: m2.case_context(cases[c], maps, pkt_off, sel_r) for c in CASES}
    freqs = cxs['A_equal_elements']['freqs']
    # ---- H2, H3, H5 (mappings)
    validation['H2_array_pattern'] = h2_check(np.random.default_rng(seed + 2))
    validation['H3_calibration'] = h3_check(cases, table)
    grid = np.arange(-60.0, 120.0, 0.001)
    mono = {f'{c}/{tag}/{d}': bool(np.all(np.diff(np.asarray(cxs[c][tag](grid, d))) >= 0))
            for c in CASES for tag in ('se2', 'se3') for d in ('DL', 'UL')}
    validation['H5_mapping_partition'] = {'non_monotone': [k for k, v in mono.items() if not v],
                                          'partition_errors': 0, 'passed': all(mono.values())}
    print('H2', validation['H2_array_pattern']['passed'], 'H3', validation['H3_calibration']['passed'], flush=True)
    with np.load(m2.E11_DIR / 'site_tx0_mask.npz') as z:
        mask = {k: z[k] for k in z.files}
    with np.load(m2.GEO_DIR / 'site_tx0_indoor.npz') as z:
        geo = {k: z[k] for k in z.files}
    gnb = np.array(mask['gnb'], dtype=float)
    summary = {'n_realizations': n_real, 'n_candidates': m2.N_CAND, 'per_beam_settings': PB,
               'e1c2_settings': m2.SETTINGS, 'quantiles': QS, 'isd': {}}
    h1a, h1b, h4 = {}, {}, {}
    part_err = 0
    for isd in ISDS:
        t_isd = time.time()
        with np.load(site_files[isd]) as z:
            sites = {k: z[k] for k in z.files}
        S = len(sites['site_x'])
        sxyz = np.column_stack([sites['site_x'], sites['site_y'], sites['site_h']]).astype(float)
        xy_o, xy_i = np.column_stack([mask['x'], mask['y']]), np.column_stack([geo['x'], geo['y']])
        ring_o, ring_i = m2.nearest_ring(xy_o, gnb, isd), m2.nearest_ring(xy_i, gnb, isd)
        keep_i = ~sites['indoor_excluded']
        victims = {'out': np.flatnonzero(ring_o == 0), 'ind': np.flatnonzero((ring_i == 0) & keep_i)}
        pools = {'out': np.flatnonzero(ring_o <= 2), 'ind': np.flatnonzero((ring_i <= 2) & keep_i)}
        data = {'mask': mask, 'geo': geo, 'sites': sites, 'pools': pools, 'victims': victims}
        vb = {}
        for kind, idx in victims.items():
            if kind == 'out':
                vb[kind] = m2.link_block(sxyz, mask['x'][idx], mask['y'][idx], mask['z'][idx], sites['los_out'][idx],
                                         np.zeros((len(idx), S)), freqs)
            else:
                vb[kind] = m2.link_block(sxyz, geo['x'][idx], geo['y'][idx], geo['h_ut'][idx], sites['facade_los'][idx],
                                         sites['din'][idx], freqs)
            vb[kind]['Elin'] = 10.0 ** (vb[kind]['E'] / 10.0)
        vg = {kind: {c: m2.case_gains(vb[kind], cases[c], table, kind) for c in CASES} for kind in vb}
        va = {kind: {c: [specular_from_gains(vb[kind]['E'], vg[kind][c][fi]['ul'], n)
                         for fi, n in enumerate(r_ * c_ for r_, c_ in case_dims(cases[c]))] for c in CASES} for kind in vb}
        sterm = {st: m2.state_terms(st, vb['out' if st in ('outdoor', 'in_car') else 'ind'], freqs) for st in STATES}
        nv = {st: vb['out' if st in ('outdoor', 'in_car') else 'ind']['n'] for st in STATES}
        print(f'ISD {isd}: victims {[len(v) for v in victims.values()]}', flush=True)
        counts = {(sn, c, st, p, n): np.zeros(nv[st], dtype=np.int16)
                  for sn in ALL_SETTINGS for c in CASES for st in STATES for p in PROFILES for n in NAMES}
        hists = {(sn, c, st, fi, mt): np.zeros(m2.NB, dtype=np.int64)
                 for sn in ALL_SETTINGS for c in CASES for st in STATES for fi in range(2) for mt in m2.METRICS}
        pairs = {(pn, c, st, p, n): (np.zeros(nv[st], dtype=np.int16), np.zeros(nv[st], dtype=np.int16))
                 for pn in PAIRS for c in CASES for st in STATES for p in PAIR_PROFILES for n in NAMES}
        n_syn = {c: 0 for c in CASES}
        fallbacks = 0
        h1b_worst = 0.0
        h4_rec = {}
        for r in range(n_real):
            cand = m2.draw_candidates(m2.rng_for(seed, isd, r, 0), len(pools['out']), len(pools['ind']), m2.N_CAND, S, mix)
            cb = m2.candidate_block(cand, data, sxyz, pools, freqs)
            cb['Elin'], cb['Llin'] = 10.0 ** (cb['E'] / 10.0), 10.0 ** (-cb['L'] / 10.0)
            act_dl, act_ul = draw_activity(m2.rng_for(seed, isd, r, STREAM_ACT), N_SECT * S)
            syn, fb = synthetic_targets(m2.rng_for(seed, isd, r, STREAM_SYN), sites, gnb, isd)
            fallbacks += fb
            ulI, cc = {}, {}
            for c in CASES:
                Gc = m2.candidate_gains(cb, cases[c], table)
                ui = m2.ul_interference(cb, Gc, cxs[c], cand['usel'], S)
                ulI[c] = ui['I']
                cc[c] = case_beam_context(cb, Gc, ui, cases[c], cxs[c], data, sxyz, syn, act_dl, act_ul)
                n_syn[c] += cc[c]['n_synthetic']
            for si, st in enumerate(STATES):
                kind = 'out' if st in ('outdoor', 'in_car') else 'ind'
                b = vb[kind]
                z, u, x = m2.draw_ue_terms(m2.rng_for(seed, isd, r, 1 + si), b['n'], S)
                pen, sig, sue = sterm[st]
                L, _ = m2.losses(b, pen, sig, sue, z, u, x)
                Llin = 10.0 ** (-L / 10.0)
                for c in CASES:
                    res = m2.evaluate_case(b, vg[kind][c], L, Llin, ulI[c], cxs[c])
                    res.update(evaluate_pb(b, vg[kind][c], L, Llin, cxs[c], va[kind][c], cc[c]))
                    ref, pb = res['primary'], res['avg_pb']
                    h1b_worst = max(h1b_worst, max(float(np.max(np.abs(pb['dl'][fi] - ref['dl'][fi]))) for fi in range(2)),
                                    max(float(np.max(np.abs(pb['ul'][p][fi] - ref['ul'][p][fi])))
                                        for p in PROFILES for fi in range(2)))
                    if c == 'A_equal_elements' and r in H4_REALIZATIONS:
                        h4_rec[(r, st)] = {'sd': res['sd'], 'sd_stress': res['sd_stress']}
                    flags = {}
                    for sn, rs in res.items():
                        fl = m2.class_flags(rs, cxs[c])
                        flags[sn] = fl
                        for p in PROFILES:
                            tot = np.zeros(b['n'], dtype=np.int16)
                            for n in NAMES:
                                counts[(sn, c, st, p, n)] += fl[p][n]
                            for n in PARTITION:
                                tot += fl[p][n]
                            part_err += int(np.sum(tot != 1))
                        for fi in range(2):
                            m2.hist_add(hists[(sn, c, st, fi, 'DL')], rs['dl'][fi])
                            m2.hist_add(hists[(sn, c, st, fi, 'UL_F2')], rs['ul']['F2'][fi])
                            m2.hist_add(hists[(sn, c, st, fi, 'UL_F3')], rs['ul']['F3'][fi])
                    for pn, (sa, sb_) in PAIRS.items():
                        for p in PAIR_PROFILES:
                            for n in NAMES:
                                fa, fb_ = flags[sa][p][n], flags[sb_][p][n]
                                n10, n01 = pairs[(pn, c, st, p, n)]
                                n10 += fa & ~fb_
                                n01 += ~fa & fb_
            if r % 10 == 9:
                print(f'ISD {isd}: realization {r + 1}/{n_real}, {time.time() - t_isd:.0f} s', flush=True)
        # ---- H1a: E1c.2 reproduced
        with np.load(raw2[isd]) as z:
            raw_e1c2 = {k: z[k] for k in z.files}
        raw_bad = [k for k in raw_e1c2 if '|' in k and not np.array_equal(
            raw_e1c2[k], counts[tuple(k.split('|'))].astype(raw_e1c2[k].dtype))]
        raw_n = sum('|' in k for k in raw_e1c2)
        res_isd = aggregate(counts, hists, ALL_SETTINGS, nv, n_real, mixes, mix, freqs)
        worst_mix = 0.0
        for sn in m2.SETTINGS:
            for c in CASES:
                for mn in mixes:
                    for p in PROFILES:
                        for n in NAMES:
                            worst_mix = max(worst_mix, abs(res_isd[sn][c]['mixes'][mn][p][n]
                                                           - s2['isd'][str(isd)]['settings'][sn][c]['mixes'][mn][p][n]))
        h1a[f'isd{isd}'] = {'raw_arrays_compared': raw_n, 'raw_mismatches': raw_bad[:10], 'max_mix_diff': worst_mix,
                            'victims_match': bool(np.array_equal(victims['out'], raw_e1c2['victim_out_index'])
                                                  and np.array_equal(victims['ind'], raw_e1c2['victim_ind_index'])),
                            'passed': bool(raw_n > 0 and not raw_bad and worst_mix <= 1e-12)}
        h1a[f'isd{isd}']['passed'] &= h1a[f'isd{isd}']['victims_match']
        h1b[f'isd{isd}'] = {'max_abs_diff_db': h1b_worst, 'passed': h1b_worst <= 1e-9}
        print('H1a', h1a[f'isd{isd}']['passed'], 'H1b', h1b[f'isd{isd}']['passed'], f'{h1b_worst:.2e}', flush=True)
        # ---- H4: scalar recomputation (sd, sd_stress; case A)
        rg = np.random.default_rng(seed + 4)
        cache, worst4, assoc_bad = {}, 0.0, 0
        for _ in range(H4_PAIRS):
            r = int(rg.choice(H4_REALIZATIONS))
            st = STATES[int(rg.integers(len(STATES)))]
            vi = int(rg.integers(nv[st]))
            ref = scalar_pb(data, cases['A_equal_elements'], table, cxs['A_equal_elements'], seed, isd, r, st, vi, mix,
                            cache, gnb)
            for sn in ('sd', 'sd_stress'):
                eng = h4_rec[(r, st)][sn]
                assoc_bad += int(ref['k'] != int(eng['k'][vi]) or ref['t'] != int(eng['t'][vi]))
                d = [abs(ref[sn]['dl'][fi] - float(eng['dl'][fi][vi])) for fi in range(2)]
                d += [abs(ref[sn]['ul'][p][fi] - float(eng['ul'][p][fi][vi])) for p in PROFILES for fi in range(2)]
                worst4 = max(worst4, max(d))
        h4[f'isd{isd}'] = {'pairs': H4_PAIRS, 'association_mismatches': assoc_bad, 'max_abs_diff_db': worst4,
                           'passed': assoc_bad == 0 and worst4 <= 1e-9}
        print('H4', h4[f'isd{isd}']['passed'], f'{worst4:.2e} dB', flush=True)
        robust = {}
        for c in CASES:
            for p in ('F2', 'F3'):
                for n in FEAS:
                    a0 = res_isd['primary'][c]['mixes']['M2412'][p][n]
                    d_sd = res_isd['sd'][c]['mixes']['M2412'][p][n] - a0
                    d_geo = res_isd['geo'][c]['mixes']['M2412'][p][n] - a0
                    robust[f'{c}|{p}|{n}'] = {'sd_minus_avg': d_sd, 'geo_minus_avg': d_geo,
                                              'robust': abs(d_sd) <= 0.02 and abs(d_geo) <= 0.02}
        summary['isd'][str(isd)] = {
            'victims': {'outdoor_points': len(victims['out']), 'indoor_samples': len(victims['ind'])},
            'synthetic_dl_targets_mean_per_realization': {c: n_syn[c] / n_real for c in CASES},
            'synthetic_sampling_fallbacks': fallbacks,
            'settings': res_isd, 'paired_differences': pair_summary(pairs, nv, n_real, mixes),
            'beam_model_robustness_m2412': robust,
            'histogram_edge_counts': {f'{sn}|{c}|{st}|{fi}|{mt}': [int(h[0]), int(h[-1])]
                                      for (sn, c, st, fi, mt), h in hists.items() if h[0] or h[-1]}}
        raw = {'victim_out_index': victims['out'], 'victim_ind_index': victims['ind']}
        for (sn, c, st, p, n), v in counts.items():
            if sn in RAW_SETTINGS:
                raw[f'{sn}|{c}|{st}|{p}|{n}'] = v
        np.savez_compressed(outdir / 'raw' / f'counts_isd{isd}.npz', **raw)
        print(f'ISD {isd} done in {time.time() - t_isd:.0f} s', flush=True)
    validation['H1a_e1c2_reproduced'] = dict(h1a, passed=all(v['passed'] for v in h1a.values()))
    validation['H1b_per_beam_limit'] = dict(h1b, passed=all(v['passed'] for v in h1b.values()))
    validation['H4_scalar_recompute'] = dict(h4, passed=all(v['passed'] for v in h4.values()))
    validation['H5_mapping_partition']['partition_errors'] = part_err
    validation['H5_mapping_partition']['passed'] = bool(validation['H5_mapping_partition']['passed'] and part_err == 0)
    gates_ok = all(v['passed'] for v in validation.values())
    validation['all_checks_passed'] = gates_ok
    q = summary['isd']['200']['settings']
    qd = {sn: q[sn]['A_equal_elements']['quantiles_db_1_5_50_95']['M2412']['7.2GHz']['DL'] for sn in ('primary', 'sd', 'geo')}
    summary['predictions'] = {
        'P1': {'q5': {k: v[1] for k, v in qd.items()}, 'q50': {k: v[2] for k, v in qd.items()},
               'held': qd['sd'][1] < qd['primary'][1] and qd['sd'][2] > qd['primary'][2]},
        'P2': {'q1': {k: v[0] for k, v in qd.items()}, 'held': qd['geo'][0] < qd['sd'][0] < qd['primary'][0]}}
    summary['decision'] = conf['decision_rules']['all_gates_pass' if gates_ok else 'gate_fails']
    summary['wall_time_s'] = time.time() - t_start
    (outdir / 'summary.json').write_text(json.dumps(summary, indent=1, default=float), encoding='utf-8')
    (outdir / 'validation.json').write_text(json.dumps(validation, indent=2, default=float), encoding='utf-8')
    manifest = {'generated_utc': datetime.now(timezone.utc).isoformat(), 'tool': 'scripts/e1c3_directional.py',
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
    print('E1c.3', 'GATES PASSED' if gates_ok else 'GATES FAILED', f'{time.time() - t_start:.0f} s')
    sys.exit(0 if gates_ok else 1)


if __name__ == '__main__':
    main()
