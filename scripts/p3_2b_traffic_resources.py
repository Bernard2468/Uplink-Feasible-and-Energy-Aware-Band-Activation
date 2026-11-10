"""P3.2b: traffic and resource layer (load-coupled flow-level model) on the P3.2a radio engine.

Pre-registered in configs/p3/p3_2b_traffic_resources.json. scripts/p3_2a_coupled_feasibility.py (and through it
e1c3_directional / e1c2_interference) is imported, not modified.

Model (typical-site frame, homogeneous network):
- Users = E1c.3 victims in site 0's hexagonal cell (M.2412 mix states), each served by its own TRxP; by the lattice
  translation, a victim served by TRxP t is a user of the typical site's sector t mod 3. Loads and 7.2 GHz activation
  are periodic in the sector index: every TRxP with sector index s has load L_s and activation a_s.
- Load coupling (Siomina & Yuan 2012): the interference of TRxP t is scaled by its realized occupancy; each carrier
  and direction is an independent fixed point L = min(1, f(L)), f_k = T sum_{u in k} w_u / (cap SE(gamma_u(L))).
- Flow level (time-fair processor sharing; Bonald, Borst, Hegde, Proutiere 2003, eq. 5): flow throughput
  theta_u = C_u (1 - L_k) if the offered load rho_k < 1, else 0.
- Peak rate C = SE (C-AMC T2) x N_PRB x 12 x 30 kHz x TDD share x (1 - OH); QoS target R = SE5 x W_op(3.5 GHz) x TDD
  share (M.2410 Table 1 and sec. 4.4); criterion: pooled outage of admitted users <= 5 %.
Amendment 1 (after run 1, before run 2): the admission point is the first C-AMC grid point at or above snr_min on the
MCS-0 plateau (UL -0.64 dB as registered; run 1 used +0.36 dB); gate B9 requires every DL output to equal run 1's.
Outputs in --outdir: summary.json, validation.json, manifest.json; raw/loads_isd<ISD>.npz (git-ignored).
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
import p3_2a_coupled_feasibility as A  # noqa: E402

m, M2 = A.m, A.M2
CONFIG = ROOT / 'configs/p3/p3_2b_traffic_resources.json'
OUTDIR = ROOT / 'results/phase3/p3_2b_traffic_resources'
N_SECT, PROFILES, STATES = A.N_SECT, A.PROFILES, A.STATES
DIRS = ('DL', 'UL')
MIX_STATES = ('in_car', 'indoor_low', 'indoor_high')
SCS_HZ = 30e3
TAU = {'DL': 52.0 / 70.0, 'UL': 16.0 / 70.0}            # DDDSU, S = 10D:2G:2U (TR 38.864 Table B-1)
OH = {'DL': 0.14, 'UL': 0.08}                           # TS 38.306 V19.4.0 cl. 4.1.2, FR1
SE5 = {'DL': 0.225, 'UL': 0.15}                         # M.2410 Table 1, Dense Urban-eMBB
EPS_OUT = 0.05
RHO = {'ericsson_typical': 10.0 / 90.0, 'genai_2025': 26.0 / 74.0}
RHO_PRIMARY = 'ericsson_typical'
ELLS = (0.3, 0.5)
GRID_X = (0.25, 0.5, 1.0, 1.5, 2.0, 3.0, 4.0, 6.0)
X_MAX, Y_MAX, BIS_TOL = 16.0, 8.0, 0.005
FP_TOL, FP_MAX_IT = 1e-10, 20000
UNIQ_REAL = 10
CHUNK_ELEMS = 12_000_000                                # batch points x users per vectorized solve
SCOPE = {200: {'A_equal_elements': (0.0,), 'B_equal_aperture': (0.0,), 'C_deployment': (0.0, -3.0, -10.0)},
         500: {'C_deployment': (0.0, -3.0, -10.0)}}
PRIMARY = (200, 'C_deployment', -3.0)
DL_STATES = ('off', 'on')
UL_STATES = ('off', 'on_ulca', 'on_scell_dl')
UL_RESCUE_STATES = ('off_rescue', 'on_ulca_rescue')
RESCUE_CASES = ('C_deployment',)
PLATEAU_MIN_SE = 0.1        # amendment 1: admission on the MCS-0 plateau (the hazard-15 floor point has SE 0.0016)
RUN1_DIR = OUTDIR / 'run1'  # amendment 1: run 1 evidence, the reference of gate B9


# ---------------------------------------------------------------- engine extension (per-carrier load weights)
def engine_sinr_lc(T, cx, a72, w_dl, w_ul, dp72=0.0):
    """A.engine_sinr with separate DL / UL interference weights per carrier (w_dl = (w35, w72), w_ul likewise; None =
    all 1) and the 7.2 GHz activation a72 for the D13 serving rule. With w_dl = w_ul = (None, w72) and a72 = w72 it
    performs the same operations as A.engine_sinr(T, cx, w72, None, dp72) (gate B1: bitwise equal)."""
    own = A.own_on(T, a72)
    dl, ul = [], {p: [] for p in PROFILES}
    for fi in range(2):
        d = 0.0 if fi == 0 else dp72
        i_dl = 10.0 ** ((cx['p_dl'][fi] + d) / 10.0) * A.masked_sum(T['wf_dl'][fi], w_dl[fi])
        x = (T['s_dl'][fi] + d) - 10.0 * np.log10(10.0 ** (cx['n_dl'][fi] / 10.0) + i_dl)
        dl.append(x if fi == 0 else np.where(own, x, -np.inf))
        for p in PROFILES:
            i_ul = 0.0
            if T['nU']:
                wu = None
                if w_ul[fi] is not None:
                    wa = np.asarray(w_ul[fi], dtype=float)
                    wu = wa[T['have']] if wa.ndim == 1 else wa[:, T['have']]
                i_ul = A.masked_sum(T['wf_ul'][fi]['F3' if p == 'F3' else 'F2'], wu)
                if p == 'F1':
                    i_ul = i_ul / cx['n_prb'][fi]
            y = T['s_ul'][fi] - 10.0 * np.log10(10.0 ** (cx['n_ul'][fi][p] / 10.0) + i_ul)
            ul[p].append(y if fi == 0 else np.where(own, y, -np.inf))
    return {'dl': dl, 'ul': ul, 'k': T['k'], 't': T['tv'], 'own72': own}


def periodic(v3, T3):
    """Per-TRxP vector from a per-sector-index vector (TRxP t has sector index t mod 3)."""
    return np.asarray(v3, dtype=float)[np.arange(T3) % N_SECT]


# ---------------------------------------------------------------- grouping (typical-site frame)
def group_terms(T, cx):
    """Per victim: serving signal [dB] and sector-index sums of the interference terms, per carrier.
    DL: G[:, s] = 10^(p_dl/10) sum_{t mod 3 = s} wf_dl[:, t]; UL (F2): G[:, s] = sum_{have[j] mod 3 = s} wf_ul[:, j]."""
    n = T['n']
    sec_t = np.arange(T['T3']) % N_SECT
    out = {'sec': (T['tv'] % N_SECT).astype(np.int64), 'dl': [], 'ul': []}
    for fi in range(2):
        g = np.stack([T['wf_dl'][fi][:, sec_t == s].sum(axis=1) for s in range(N_SECT)], axis=1)
        out['dl'].append({'s': np.asarray(T['s_dl'][fi], dtype=float), 'G': 10.0 ** (cx['p_dl'][fi] / 10.0) * g})
        if T['nU']:
            sh = T['have'] % N_SECT
            gu = np.stack([T['wf_ul'][fi]['F2'][:, sh == s].sum(axis=1) for s in range(N_SECT)], axis=1)
        else:
            gu = np.zeros((n, N_SECT))
        out['ul'].append({'s': np.asarray(T['s_ul'][fi], dtype=float), 'G': gu})
    return out


def grouped_sinr(s, noise, G, Ls, d=0.0):
    """SINR [dB] = (s + d) - 10 log10(noise + 10^(d/10) sum_s G[:, s] Ls[s]); Ls[s] = weight of sector index s
    (scalar, or an array broadcastable to the users)."""
    I = G[:, 0] * Ls[0] + G[:, 1] * Ls[1] + G[:, 2] * Ls[2]
    return (s + d) - 10.0 * np.log10(noise + 10.0 ** (d / 10.0) * I)


# ---------------------------------------------------------------- rates (unit-tested)
def cap_factor(n_prb, d):
    """Bit/s per unit SE: N_PRB x 12 x 30 kHz x TDD share x (1 - OH)."""
    return n_prb * 12 * SCS_HZ * TAU[d] * (1.0 - OH[d])


def required_rate(bw_op_35, d):
    """M.2410 5th-percentile user SE x effective bandwidth (operating bandwidth of the 3.5 GHz layer x TDD share)."""
    return SE5[d] * bw_op_35 * TAU[d]


def admission_point(se_fn, d, snr_min):
    """(SNR, SE) of the C-AMC curve at its first grid point at or above snr_min that lies on the 5G-LENA MCS-0 plateau
    (SE >= PLATEAU_MIN_SE): DL -0.64 dB (its -1.64 dB snr_min point is the MCS-0 BLER floor, SE 0.0016, hazard 15) and
    UL -0.64 dB = the UL snr_min (SE 0.1965), as D19 and the config register. Amendment 1: run 1 took the first point
    strictly above snr_min, which moved the UL point to +0.36 dB on the same plateau."""
    xs, ys = se_fn.grid
    k = int(np.flatnonzero((xs[d] >= snr_min) & (ys[d] >= PLATEAU_MIN_SE))[0])
    return float(xs[d][k]), float(ys[d][k])


def se_rate(gam, se, d, g_adm, rescue):
    """Rate SE (peak rate / cap): full band, or (rescue, UL) a fluid PRB share n/N = 10^((gamma - g_adm)/10) below
    g_adm, transmitting at g_adm."""
    if not rescue:
        return se(gam, d)
    return se(np.maximum(gam, g_adm), d) * np.minimum(1.0, 10.0 ** ((gam - g_adm) / 10.0))


def se_load(gam, se, d, g_adm, rescue):
    """Resource SE (load per bit = 1 / (cap SE_load))."""
    return se(np.maximum(gam, g_adm), d) if rescue else se(gam, d)


def admitted(gam_full, se, d, se_adm, g_adm, n_prb, rescue):
    """Link-level admission at full-buffer SINR: SE >= SE_adm (full band) or, rescue, at least one PRB at g_adm."""
    if rescue:
        return gam_full + 10.0 * np.log10(n_prb) >= g_adm
    return se(gam_full, d) >= se_adm


# ---------------------------------------------------------------- population and groups
def population(parts, R):
    """Concatenate per-(realization, state) grouped terms: parts = list of (r, weight, grouped dict)."""
    U = {'r': np.concatenate([np.full(len(g['sec']), r, dtype=np.int64) for r, _, g in parts]),
         'sec': np.concatenate([g['sec'] for _, _, g in parts]),
         'w': np.concatenate([np.full(len(g['sec']), w) for _, w, g in parts]), 'R': R}
    for dd in ('dl', 'ul'):
        U[dd] = [{'s': np.concatenate([g[dd][fi]['s'] for _, _, g in parts]),
                  'G': np.concatenate([g[dd][fi]['G'] for _, _, g in parts])} for fi in range(2)]
    U['cell'] = U['r'] * N_SECT + U['sec']
    return U


class Ctx:
    """Case constants: noise, PRBs, caps, SE mapping, admission points, QoS targets."""

    def __init__(self, cx, carriers):
        self.se = cx['se2']
        self.n_prb = [int(v) for v in cx['n_prb']]
        self.noise = {'DL': [10.0 ** (cx['n_dl'][fi] / 10.0) for fi in range(2)],
                      'UL': [10.0 ** (cx['n_ul'][fi]['F2'] / 10.0) for fi in range(2)]}
        self.cap = {d: [cap_factor(self.n_prb[fi], d) for fi in range(2)] for d in DIRS}
        self.floor = {d: float(cx['th'][0][('F1', d)]) for d in DIRS}
        if any(float(cx['th'][1][('F1', d)]) != self.floor[d] for d in DIRS):
            raise SystemExit('carrier-dependent SE floor')
        pts = {d: admission_point(self.se, d, self.floor[d]) for d in DIRS}
        self.g_adm = {d: pts[d][0] for d in DIRS}
        self.se_adm = {d: pts[d][1] for d in DIRS}
        cs = sorted(carriers, key=lambda c: c['frequency_hz'])
        self.bw_op35 = float(cs[0]['channel_bw_hz'])
        self.req = {d: required_rate(self.bw_op35, d) for d in DIRS}


def fb_sinr(U, ctx, d, fi, act, dp):
    """Full-buffer SINR: every ON TRxP of the carrier at weight 1 (3.5 GHz: all; 7.2 GHz: a_s)."""
    a = np.asarray(act, dtype=float)
    return grouped_sinr(U[d.lower()][fi]['s'], ctx.noise[d][fi], U[d.lower()][fi]['G'], [a[0], a[1], a[2]], dp)


def assign(U, ctx, d, a72, dp, rule, rescue):
    """Static carrier per user and direction: 0 = 3.5 GHz, 1 = 7.2 GHz, -1 = not admitted on any carrier.
    'off': 3.5 only; 'on' (DL) / 'on_ulca' (UL): 7.2 iff the own 7.2 cell is on, admitted there and its full-buffer
    peak rate >= the 3.5 GHz one; 'on_scell_dl' (UL): 3.5 only."""
    a72 = np.asarray(a72, dtype=float)
    gam0 = fb_sinr(U, ctx, d, 0, np.ones(N_SECT), 0.0)
    ok0 = admitted(gam0, ctx.se, d, ctx.se_adm[d], ctx.g_adm[d], ctx.n_prb[0], rescue)
    C0 = np.where(ok0, ctx.cap[d][0] * se_rate(gam0, ctx.se, d, ctx.g_adm[d], rescue), 0.0)
    car = np.where(ok0, 0, -1)
    if rule in ('on', 'on_ulca'):
        gam1 = fb_sinr(U, ctx, d, 1, a72, dp if d == 'DL' else 0.0)
        ok1 = (a72[U['sec']] > 0.0) & admitted(gam1, ctx.se, d, ctx.se_adm[d], ctx.g_adm[d], ctx.n_prb[1], rescue)
        C1 = np.where(ok1, ctx.cap[d][1] * se_rate(gam1, ctx.se, d, ctx.g_adm[d], rescue), 0.0)
        car = np.where(ok1 & (C1 >= C0), 1, car)
    return car


def make_group(U, ctx, d, fi, sel, act, dp, rescue, rmax=None):
    keep = sel if rmax is None else sel & (U['r'] < rmax)
    idx = np.flatnonzero(keep)
    R = U['R'] if rmax is None else rmax
    act = np.asarray(act, dtype=float)
    return {'r': U['r'][idx], 'cell': U['cell'][idx], 'K': R * N_SECT, 'R': R, 'idx': idx,
            's': U[d.lower()][fi]['s'][idx], 'G': U[d.lower()][fi]['G'][idx], 'w': U['w'][idx],
            'noise': ctx.noise[d][fi], 'act': act, 'cell_on': np.tile(act > 0.0, R), 'd': dp, 'dir': d,
            'cap': ctx.cap[d][fi], 'se': ctx.se, 'g_adm': ctx.g_adm[d], 'rescue': rescue}


def build_state(U, ctx, d, rule, a72, dp, rescue, rmax=None):
    car = assign(U, ctx, d, a72, dp, rule, rescue)
    groups = {0: make_group(U, ctx, d, 0, car == 0, np.ones(N_SECT), 0.0, rescue, rmax)}
    if rule in ('on', 'on_ulca'):
        groups[1] = make_group(U, ctx, d, 1, car == 1, a72, dp if d == 'DL' else 0.0, rescue, rmax)
    return {'car': car, 'groups': groups, 'dir': d, 'rule': rule, 'rescue': rescue}


# ---------------------------------------------------------------- load-coupled fixed point (unit-tested)
def offered_load(g, traffic, L):
    """f[b, k] = traffic_b sum_{u in cell k} w_u / (cap SE_load(gamma_u(L))) and the users' SINR gamma[b, u]."""
    traffic = np.asarray(traffic, dtype=float)
    B = len(traffic)
    Lw = L.reshape(B, g['R'], N_SECT) * g['act'][None, None, :]
    gam = grouped_sinr(g['s'], g['noise'], g['G'], [Lw[:, g['r'], s] for s in range(N_SECT)], g['d'])
    if len(g['w']) == 0:
        return np.zeros((B, g['K'])), gam
    inv = g['w'][None, :] / se_load(gam, g['se'], g['dir'], g['g_adm'], g['rescue'])
    f = np.stack([np.bincount(g['cell'], weights=inv[b], minlength=g['K']) for b in range(B)])
    return traffic[:, None] * f / g['cap'], gam


def _solve_chunk(g, traffic, start, tol, max_it):
    B = len(traffic)
    L = np.full((B, g['K']), float(start))
    L[:, ~g['cell_on']] = 0.0
    it, dlt = 0, 0.0
    for it in range(1, max_it + 1):
        f, _ = offered_load(g, traffic, L)
        new = np.minimum(1.0, f)
        dlt = float(np.max(np.abs(new - L))) if new.size else 0.0
        L = new
        if dlt <= tol:
            break
    f, gam = offered_load(g, traffic, L)
    res = float(np.max(np.abs(L - np.minimum(1.0, f)))) if L.size else 0.0
    return L, f, gam, it, dlt, res


def solve(g, traffic, start=1.0, tol=FP_TOL, max_it=FP_MAX_IT):
    """L = min(1, f(L)) by plain iteration from L = start (1: monotone decrease to the maximal fixed point), in chunks
    of traffic levels. Returns loads L, offered loads rho = f(L), SINR, iterations, last step, residual."""
    traffic = np.asarray(traffic, dtype=float)
    step = max(1, CHUNK_ELEMS // max(1, len(g['w'])))
    out = [_solve_chunk(g, traffic[i:i + step], start, tol, max_it) for i in range(0, len(traffic), step)]
    return {'L': np.concatenate([o[0] for o in out]), 'rho': np.concatenate([o[1] for o in out]),
            'gam': np.concatenate([o[2] for o in out]), 'it': max(o[3] for o in out),
            'delta': max(o[4] for o in out), 'res': max(o[5] for o in out),
            'converged': all(o[4] <= tol for o in out)}


def flows_at(g, sol, b, traffic_b, req):
    """Per user of the group at traffic level b: flow throughput, outage, offered / served / unserved traffic and the
    occupancy recomputed from the served traffic (time-fair PS)."""
    Lc, rho, gam = sol['L'][b][g['cell']], sol['rho'][b][g['cell']], sol['gam'][b]
    C = g['cap'] * se_rate(gam, g['se'], g['dir'], g['g_adm'], g['rescue'])
    theta = np.where(rho < 1.0, C * (1.0 - Lc), 0.0)
    offered = traffic_b * g['w']
    with np.errstate(divide='ignore'):
        inv_rho = np.where(rho > 0.0, 1.0 / rho, np.inf)
    served = offered * np.minimum(1.0, inv_rho)
    unserved = offered * np.maximum(0.0, 1.0 - inv_rho)
    occ = (np.bincount(g['cell'], weights=served / (g['cap'] * se_load(gam, g['se'], g['dir'], g['g_adm'], g['rescue'])),
                       minlength=g['K']) if len(g['w']) else np.zeros(g['K']))
    return {'theta': theta, 'C': C, 'out': theta < req, 'offered': offered, 'served': served, 'unserved': unserved,
            'occ_from_served': occ}


def weighted_quantiles(v, w, qs):
    if len(v) == 0 or float(np.sum(w)) <= 0.0:
        return [float('nan')] * len(qs)
    o = np.argsort(v, kind='stable')
    cw = np.cumsum(w[o])
    cw = cw / cw[-1]
    return [float(v[o][min(int(np.searchsorted(cw, q, side='left')), len(v) - 1)]) for q in qs]


def eval_state(U, ctx, st, traffic, check=None, quantiles=True, keep_gam=False):
    """Solve every carrier group of a direction state at the traffic levels and aggregate per level.
    check(b, traffic_b, sols, flows_b) is called per level with the per-user flows (not stored); the per-user SINR
    arrays are dropped after aggregation unless keep_gam (memory at ISD 500)."""
    traffic = np.asarray(traffic, dtype=float)
    d, R = st['dir'], U['R']
    sols = {fi: solve(g, traffic) for fi, g in st['groups'].items()}
    w_srv_r = np.bincount(U['r'], weights=U['w'] * (st['car'] >= 0), minlength=R)
    w_uns_r = np.bincount(U['r'], weights=U['w'] * (st['car'] < 0), minlength=R)
    agg = []
    for b, tb in enumerate(traffic):
        out_r = np.zeros(R)
        tot = {'served': 0.0, 'unserved': 0.0, 'on72': 0.0}
        thetas, weights, fls = [], [], {}
        for fi, g in st['groups'].items():
            fl = flows_at(g, sols[fi], b, tb, ctx.req[d])
            fls[fi] = fl
            out_r += np.bincount(g['r'], weights=g['w'] * fl['out'], minlength=R)
            tot['served'] += float(fl['served'].sum())
            tot['unserved'] += float(fl['unserved'].sum())
            if fi == 1:
                tot['on72'] += float(fl['offered'].sum())
            thetas.append(fl['theta'])
            weights.append(g['w'])
        if check is not None:
            check(b, tb, sols, fls)
        offered_all = float(tb * U['w'].sum())
        unservable = float(tb * U['w'][st['car'] < 0].sum())
        frac = (lambda v: v / offered_all) if offered_all > 0 else (lambda v: 0.0)
        per_r = np.where(w_srv_r > 0, out_r / np.maximum(w_srv_r, 1e-300), 1.0)
        rec = {'traffic_bps': float(tb),
               'outage_admitted_pooled': float(out_r.sum() / w_srv_r.sum()) if w_srv_r.sum() > 0 else 1.0,
               'outage_admitted_se': float(np.std(per_r, ddof=1) / math.sqrt(R)) if R > 1 else 0.0,
               'outage_all_users': float(np.mean(out_r + w_uns_r)),
               'not_admitted_share': float(np.mean(w_uns_r)),
               'traffic_fractions': {'served': frac(tot['served']), 'unserved_saturation': frac(tot['unserved']),
                                     'unservable_not_admitted': frac(unservable), 'offered_on_7.2GHz': frac(tot['on72'])},
               'loads': {}}
        if quantiles:
            rec['theta_q05_q50_bps'] = weighted_quantiles(np.concatenate(thetas) if thetas else np.zeros(0),
                                                          np.concatenate(weights) if weights else np.zeros(0), (0.05, 0.5))
        for fi, s in sols.items():
            on = st['groups'][fi]['cell_on']
            Lb, rb = s['L'][b][on], s['rho'][b][on]
            rec['loads']['3.5GHz' if fi == 0 else '7.2GHz'] = {
                'mean': float(Lb.mean()) if Lb.size else 0.0, 'max': float(Lb.max()) if Lb.size else 0.0,
                'saturated_cell_share': float(np.mean(rb >= 1.0)) if rb.size else 0.0,
                'offered_load_mean': float(rb.mean()) if rb.size else 0.0}
        agg.append(rec)
    if not keep_gam:
        for s in sols.values():
            s['gam'] = None
    return {'agg': agg, 'sols': sols}


def outage_at(U, ctx, st, tb):
    """Pooled admitted-user outage at one traffic level (bisection path)."""
    d = st['dir']
    num, den = 0.0, float(np.sum(U['w'] * (st['car'] >= 0)))
    for fi, g in st['groups'].items():
        sol = solve(g, [tb])
        num += float(np.sum(g['w'] * flows_at(g, sol, 0, tb, ctx.req[d])['out']))
    return num / den if den > 0 else 1.0


def bisect_traffic(U, ctx, st, t_unit, hi=X_MAX, tol=BIS_TOL):
    """Largest traffic (units of t_unit) with pooled admitted-user outage <= EPS_OUT; outage is non-decreasing in
    traffic (static association, loads non-decreasing; gate B7f on the grid)."""
    def ok(x):
        return outage_at(U, ctx, st, x * t_unit) <= EPS_OUT
    if not ok(1e-9):
        return {'x_star': 0.0, 'bracket': [0.0, 1e-9], 'status': 'qos_infeasible_unloaded', 'steps': 1}
    if ok(hi):
        return {'x_star': hi, 'bracket': [hi, None], 'status': 'at_least_upper_limit', 'steps': 2}
    a, b, steps = 0.0, hi, 2
    while b - a > tol:
        mid = 0.5 * (a + b)
        steps += 1
        if ok(mid):
            a = mid
        else:
            b = mid
    return {'x_star': a, 'bracket': [a, b], 'status': 'bracketed', 'steps': steps}


def calibrate(U, ctx, ell, tol=1e-11, max_steps=200):
    """Offered DL traffic per site T such that the mean (over realizations and sectors) 3.5 GHz DL occupancy of the
    all-off network equals ell (Illinois regula falsi on T; the mean is continuous and non-decreasing in T)."""
    g = build_state(U, ctx, 'DL', 'off', np.zeros(N_SECT), 0.0, False)['groups'][0]

    def h(T):
        return float(solve(g, [T])['L'].mean()) - ell
    f1, _ = offered_load(g, np.array([1.0]), np.full((1, g['K']), ell))
    t0 = ell / float(f1.mean())                         # explicit: every cell at load ell
    a, fa = 0.0, -ell
    b, fb = t0, h(t0)
    k = 0
    while fb < 0.0:
        a, fa = b, fb
        b *= 2.0
        fb = h(b)
        k += 1
        if k > 60:
            raise SystemExit('calibration: no upper bracket')
    side, steps, c, fc = 0, 0, b, fb
    while steps < max_steps:
        steps += 1
        c = (a * fb - b * fa) / (fb - fa)
        fc = h(c)
        if abs(fc) <= tol:
            break
        if fc * fb > 0:
            b, fb = c, fc
            if side == -1:
                fa *= 0.5
            side = -1
        else:
            a, fa = c, fc
            if side == 1:
                fb *= 0.5
            side = 1
    return {'T_bps': c, 'residual': fc, 'steps': steps, 'explicit_start_bps': t0}


def sha256(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


# ---------------------------------------------------------------- independent reference (gate B8)
def reference_check(Tr, cx, ctx, U, st, sols, traffic, r=0):
    """Realization r: per cell, the offered load recomputed with the ungrouped engine (engine_sinr_lc with explicit
    57-weight vectors), the SE mapping called per user and a Python loop over users; outage recounted.
    Tr: {mix state: (engine terms of realization r, user weight)} in MIX_STATES order."""
    d = st['dir']
    worst_f, out_bad, n_users = 0.0, 0, 0
    users_r = np.flatnonzero(U['r'] == r)
    loads = {fi: sols[fi]['L'][0].reshape(U['R'], N_SECT)[r] for fi in st['groups']}
    for fi, g in st['groups'].items():
        rho_r = sols[fi]['rho'][0].reshape(U['R'], N_SECT)[r]
        act = g['act']
        cap = ctx.n_prb[fi] * 12 * SCS_HZ * TAU[d] * (1.0 - OH[d])
        f_ref = [0.0] * N_SECT
        outs = []
        base = 0
        for st_name in MIX_STATES:
            T, w = Tr[st_name]
            T3 = T['T3']
            if fi == 0:
                wv = (periodic(loads[0], T3), None)
            else:
                wv = (None, periodic(act * loads[1], T3))
            res = engine_sinr_lc(T, cx, periodic(act, T3), wv if d == 'DL' else (None, None),
                                 wv if d == 'UL' else (None, None), g['d'] if (d == 'DL' and fi == 1) else 0.0)
            gam_all = res['dl'][fi] if d == 'DL' else res['ul']['F2'][fi]
            sel_u = users_r[base:base + T['n']]
            base += T['n']
            for j, ui in enumerate(sel_u):
                if st['car'][ui] != fi:
                    continue
                gam = float(gam_all[j])
                sec = int(U['sec'][ui])
                if st['rescue']:
                    se_l = float(ctx.se(np.array([max(gam, ctx.g_adm[d])]), d)[0])
                    se_r = se_l * min(1.0, 10.0 ** ((gam - ctx.g_adm[d]) / 10.0))
                else:
                    se_l = se_r = float(ctx.se(np.array([gam]), d)[0])
                f_ref[sec] += traffic * w / (cap * se_l)
                th = cap * se_r * (1.0 - loads[fi][sec]) if rho_r[sec] < 1.0 else 0.0
                outs.append((int(ui), th < ctx.req[d]))
                n_users += 1
        for s in range(N_SECT):
            if act[s] > 0 and rho_r[s] > 0:
                worst_f = max(worst_f, abs(f_ref[s] - rho_r[s]) / abs(rho_r[s]))
        fl = flows_at(g, sols[fi], 0, traffic, ctx.req[d])
        pos = {int(u): k for k, u in enumerate(g['idx'])}
        out_bad += sum(int(bool(fl['out'][pos[ui]]) != o) for ui, o in outs)
    return {'max_rel_diff_offered_load': worst_f, 'outage_mismatches': out_bad, 'users_checked': n_users}


# ---------------------------------------------------------------- main
# ---------------------------------------------------------------- amendment 1 (gate B9)
def dl_view(s):
    """Every summary field that the UL admission point cannot reach: per ISD the victims; per case the constants
    (DL admission point only), user count, calibration, grids, DL hazard-15 labels, all DL results and every x*_DL."""
    out = {}
    for isd, si in s['isd'].items():
        out[isd] = {'victims': si['victims'], 'cases': {}}
        for c, cr in si['cases'].items():
            k = dict(cr['constants'])
            k['se_admission'], k['snr_admission_db'] = k['se_admission']['DL'], k['snr_admission_db']['DL']
            out[isd]['cases'][c] = {
                'constants': k, 'n_users_total': cr['n_users_total'], 'calibration': cr['calibration'],
                'grid_x': cr['grid_x'], 'grid_y_ul': cr['grid_y_ul'],
                'hazard15_DL': cr['hazard15_labels_3.5GHz_full_buffer']['DL'], 'DL': cr['DL'],
                'x_star_DL': {kk: v['x_star_DL'] for kk, v in cr['sustainable_traffic'].items()}}
    return out


def amendment_check(s1, s2, raw1, raw2):
    """Gate B9. s1 / raw1: run 1 summary and raw arrays (per ISD dicts), s2 / raw2: this run. (a) every field of
    dl_view and every raw DL array is exactly equal (the DL never uses the UL admission point); (b) per case the UL
    not-admitted share drops by exactly run 1's share between snr_min and its admission point (<= 1e-12), that share
    is now 0, and the TR 36.942 low-end share is unchanged."""
    view_equal = dl_view(s1) == dl_view(s2)
    raw_n, raw_bad = 0, 0
    for isd in sorted(set(raw1) | set(raw2)):
        k1 = sorted(k for k in raw1.get(isd, {}) if '|DL|' in k)
        k2 = sorted(k for k in raw2.get(isd, {}) if '|DL|' in k)
        raw_bad += int(k1 != k2)
        for k in set(k1) & set(k2):
            raw_n += 1
            raw_bad += int(not np.array_equal(raw1[isd][k], raw2[isd][k]))
    shift, between2, low_bad, n_cases = 0.0, 0.0, 0, 0
    for isd, si in s2['isd'].items():
        for c, cr in si['cases'].items():
            h1 = s1['isd'][isd]['cases'][c]['hazard15_labels_3.5GHz_full_buffer']['UL']
            h2 = cr['hazard15_labels_3.5GHz_full_buffer']['UL']
            shift = max(shift, abs(h2['not_admitted_share'] - (h1['not_admitted_share'] - h1['share_between_snr_min_and_admission'])))
            between2 = max(between2, h2['share_between_snr_min_and_admission'])
            low_bad += int(h2['share_in_tr36942_low_end'] != h1['share_in_tr36942_low_end'])
            n_cases += 1
    return {'dl_view_equal': bool(view_equal), 'raw_dl_arrays_compared': raw_n, 'raw_dl_mismatches': raw_bad,
            'ul_not_admitted_shift_max_abs': shift, 'ul_between_share_max': between2, 'ul_low_end_changed': low_bad,
            'cases_compared': n_cases,
            'passed': bool(view_equal and raw_n > 0 and raw_bad == 0 and shift <= 1e-12 and between2 == 0.0
                           and low_bad == 0 and n_cases > 0)}


def load_raw(d):
    """Raw per-ISD arrays of an output folder, keyed by ISD as in the summary."""
    out = {}
    for isd in sorted(SCOPE):
        with np.load(Path(d) / 'raw' / f'loads_isd{isd}.npz') as z:
            out[str(isd)] = {k: z[k] for k in z.files}
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--config', default=str(CONFIG))
    ap.add_argument('--outdir', default=str(OUTDIR))
    ap.add_argument('--git-head', default=None)
    ap.add_argument('--smoke-realizations', type=int, default=None,
                    help='code-path and timing smoke test on N realizations: never a measured run (forced FAIL, outdir '
                         'must differ from the default, no result values printed)')
    args = ap.parse_args()
    t0 = time.time()
    conf = json.loads(Path(args.config).read_text(encoding='utf-8'))
    conf3 = json.loads(m.CONFIG.read_text(encoding='utf-8'))
    conf14 = json.loads(M2.e155.E14_CONFIG.read_text(encoding='utf-8'))
    seed, n_real = int(conf['settings']['seed']), int(conf['settings']['realizations'])
    if seed != int(conf3['settings']['seed']) or n_real != int(conf3['settings']['realizations']):
        raise SystemExit('P3.2b must use the E1c.3 seed and realization count')
    smoke = args.smoke_realizations is not None
    if smoke:
        if Path(args.outdir).resolve() == OUTDIR.resolve():
            raise SystemExit('smoke runs must not write to the measured-run directory')
        n_real = int(args.smoke_realizations)
    outdir = Path(args.outdir)
    (outdir / 'raw').mkdir(parents=True, exist_ok=True)
    validation = {}
    # ---- B0 inputs and constants
    man_a = json.loads((A.OUTDIR / 'manifest.json').read_text())
    man3 = json.loads((A.E1C3_DIR / 'manifest.json').read_text())
    man2 = json.loads((A.E1C2_DIR / 'manifest.json').read_text())
    b0 = (man_a['all_checks_passed'] and man3['all_checks_passed'] and man2['all_checks_passed']
          and sha256(A.CONFIG) == man_a['config_sha256']
          and all(sha256(A.OUTDIR / k) == v for k, v in man_a['outputs_sha256'].items())
          and all(sha256(M2.R / k) == v for k, v in man_a['input_sha256'].items())
          and sha256(m.CONFIG) == man3['config_sha256']
          and all(sha256(M2.R / k) == v for k, v in man3['input_sha256'].items() if (M2.R / k).exists())
          and all((M2.R / k).exists() for k in man3['input_sha256']))
    mc = conf['model']
    consts = (abs(mc['tdd']['tau_dl'] - TAU['DL']) < 1e-15 and abs(mc['tdd']['tau_ul'] - TAU['UL']) < 1e-15
              and mc['overhead_ts38306']['DL'] == OH['DL'] and mc['overhead_ts38306']['UL'] == OH['UL']
              and mc['qos']['se5_m2410']['DL'] == SE5['DL'] and mc['qos']['se5_m2410']['UL'] == SE5['UL']
              and mc['qos']['epsilon'] == EPS_OUT
              and all(abs(conf['traffic']['ul_dl_ratio'][k]['value'] - v) < 1e-15 for k, v in RHO.items())
              and tuple(conf['traffic']['grid_x']) == GRID_X and tuple(conf['traffic']['etsi_levels']) == ELLS)
    in_sha = {'p3_2a_coupled_feasibility/manifest.json': sha256(A.OUTDIR / 'manifest.json'),
              'configs/p3/p3_2a_coupled_feasibility.json': sha256(A.CONFIG)}
    # amendment 1: run 1 evidence (the reference of gate B9) is intact
    a1 = conf['amendment_1']
    r1man = json.loads((RUN1_DIR / 'manifest.json').read_text())
    run1_ok = (sha256(RUN1_DIR / 'manifest.json') == a1['run1']['manifest_sha256'] and r1man['all_checks_passed']
               and r1man['git_head_at_run'] == a1['run1']['git_head'] and r1man['config_sha256'] == a1['run1']['config_sha256']
               and all(sha256(RUN1_DIR / k) == v for k, v in r1man['outputs_sha256'].items()))
    in_sha['p3_2b_traffic_resources/run1/manifest.json'] = sha256(RUN1_DIR / 'manifest.json')
    validation['B0_inputs'] = {'input_sha256': in_sha, 'constants_match_config': bool(consts), 'run1_evidence_intact': bool(run1_ok),
                               'passed': bool(b0 and consts and run1_ok)}
    if not validation['B0_inputs']['passed']:
        raise SystemExit('B0 failed')
    s153 = json.loads((M2.e155.E153_DIR / 'summary.json').read_text())
    s153a = json.loads((M2.e155.E153A_DIR / 'summary.json').read_text())
    s1521 = json.loads((M2.e155.E1521_DIR / 'summary.json').read_text())
    s154 = json.loads((M2.e155.E154_DIR / 'summary.json').read_text())
    maps = M2.e155.build_mappings(s153a, s153)
    table = M2.e155.gain_table(s1521)
    pkt_off = M2.e155.packet_offsets(s154)
    sel_r = {d: v['factor'] for d, v in M2.e155.selectivity_factors(s154).items()}
    cases = {c: conf14['cases'][c] for c in A.CASES}
    mix = conf14['mixes']['M2412']['weights']
    if tuple(sorted(mix)) != tuple(sorted(MIX_STATES)) or abs(sum(mix.values()) - 1.0) > 1e-12:
        raise SystemExit('unexpected M2412 mix')
    cxs = {c: M2.case_context(cases[c], maps, pkt_off, sel_r) for c in A.CASES}
    adm_reg = a1['admission_points']   # B0, amendment 1: the admission points equal the registered values exactly
    adm_ok = True
    for c in A.CASES:
        ctx_c = Ctx(cxs[c], cases[c]['carriers'])
        adm_ok = adm_ok and all(ctx_c.g_adm[d] == adm_reg[d]['snr_db'] and ctx_c.se_adm[d] == adm_reg[d]['se'] for d in DIRS)
    validation['B0_inputs']['admission_points_match_config'] = bool(adm_ok)
    if not adm_ok:
        validation['B0_inputs']['passed'] = False
        raise SystemExit('B0 failed: admission points')
    freqs = cxs['A_equal_elements']['freqs']
    with np.load(M2.E11_DIR / 'site_tx0_mask.npz') as z:
        mask = {k: z[k] for k in z.files}
    with np.load(M2.GEO_DIR / 'site_tx0_indoor.npz') as z:
        geo = {k: z[k] for k in z.files}
    gnb = np.array(mask['gnb'], dtype=float)
    summary = {'n_realizations': n_real, 'model': conf['model'], 'isd': {}}
    gb = {k: {} for k in ('B1', 'B2', 'B3', 'B4', 'B5', 'B6', 'B7', 'B8')}
    rng_b2 = np.random.default_rng(seed + 21)
    patterns = [np.array(p, dtype=float) for p in ((1, 1, 1), (0, 0, 0), (1, 0, 0), (0, 1, 0), (0, 0, 1),
                                                   (1, 1, 0), (1, 0, 1), (0, 1, 1))]
    for isd in sorted(SCOPE):
        t_isd = time.time()
        scope = SCOPE[isd]
        with np.load(M2.SITES_DIR / f'e1c2_sites_isd{isd}.npz') as z:
            sites = {k: z[k] for k in z.files}
        S = len(sites['site_x'])
        T3 = N_SECT * S
        sxyz = np.column_stack([sites['site_x'], sites['site_y'], sites['site_h']]).astype(float)
        ring_o = M2.nearest_ring(np.column_stack([mask['x'], mask['y']]), gnb, isd)
        ring_i = M2.nearest_ring(np.column_stack([geo['x'], geo['y']]), gnb, isd)
        keep_i = ~sites['indoor_excluded']
        victims = {'out': np.flatnonzero(ring_o == 0), 'ind': np.flatnonzero((ring_i == 0) & keep_i)}
        pools = {'out': np.flatnonzero(ring_o <= 2), 'ind': np.flatnonzero((ring_i <= 2) & keep_i)}
        data = {'mask': mask, 'geo': geo, 'sites': sites, 'pools': pools, 'victims': victims}
        vb = {}
        for kind, idx in victims.items():
            if kind == 'out':
                vb[kind] = M2.link_block(sxyz, mask['x'][idx], mask['y'][idx], mask['z'][idx], sites['los_out'][idx],
                                         np.zeros((len(idx), S)), freqs)
            else:
                vb[kind] = M2.link_block(sxyz, geo['x'][idx], geo['y'][idx], geo['h_ut'][idx], sites['facade_los'][idx],
                                         sites['din'][idx], freqs)
            vb[kind]['Elin'] = 10.0 ** (vb[kind]['E'] / 10.0)
        vg = {kind: {c: M2.case_gains(vb[kind], cases[c], table, kind) for c in scope} for kind in vb}
        va = {kind: {c: [m.specular_from_gains(vb[kind]['E'], vg[kind][c][fi]['ul'], n)
                         for fi, n in enumerate(r_ * c_ for r_, c_ in m.case_dims(cases[c]))] for c in scope} for kind in vb}
        sterm = {st: M2.state_terms(st, vb['out' if st in ('outdoor', 'in_car') else 'ind'], freqs) for st in MIX_STATES}
        nv = {st: vb['out' if st in ('outdoor', 'in_car') else 'ind']['n'] for st in MIX_STATES}
        print(f'ISD {isd}: victims {[len(v) for v in victims.values()]}, cases {list(scope)}', flush=True)
        parts = {c: [] for c in scope}
        keep_t0 = {}
        b1_bad, b1_n, b2_worst, b2_n = 0, 0, 0.0, 0
        for r in range(n_real):
            cand = M2.draw_candidates(M2.rng_for(seed, isd, r, 0), len(pools['out']), len(pools['ind']), M2.N_CAND, S, mix)
            cb = M2.candidate_block(cand, data, sxyz, pools, freqs)
            cb['Elin'], cb['Llin'] = 10.0 ** (cb['E'] / 10.0), 10.0 ** (-cb['L'] / 10.0)
            act_dl, act_ul = m.draw_activity(M2.rng_for(seed, isd, r, m.STREAM_ACT), T3)
            syn, _ = m.synthetic_targets(M2.rng_for(seed, isd, r, m.STREAM_SYN), sites, gnb, isd)
            cc = {}
            for c in scope:
                Gc = M2.candidate_gains(cb, cases[c], table)
                ui = M2.ul_interference(cb, Gc, cxs[c], cand['usel'], S)
                cc[c] = m.case_beam_context(cb, Gc, ui, cases[c], cxs[c], data, sxyz, syn, act_dl, act_ul)
            for st in MIX_STATES:
                si = STATES.index(st)                    # E1c.3 random stream of the state
                kind = 'out' if st in ('outdoor', 'in_car') else 'ind'
                b = vb[kind]
                z, u, x = M2.draw_ue_terms(M2.rng_for(seed, isd, r, 1 + si), b['n'], S)
                pen, sig, sue = sterm[st]
                L, _ = M2.losses(b, pen, sig, sue, z, u, x)
                Llin = 10.0 ** (-L / 10.0)
                for c in scope:
                    T = A.engine_terms(b, vg[kind][c], L, Llin, cxs[c], va[kind][c], cc[c])
                    grp = group_terms(T, cxs[c])
                    parts[c].append((r, mix[st] / nv[st], grp))
                    if r == 0 and (isd, c) == PRIMARY[:2]:
                        keep_t0[st] = (T, mix[st] / nv[st])
                    if r >= 2:
                        continue
                    for dp in scope[c]:
                        for pat in patterns:
                            # B1: extension bitwise equal to the P3.2a engine for every sector pattern and offset
                            w72 = periodic(pat, T3)
                            ref = A.engine_sinr(T, cxs[c], w72, None, dp)
                            ext = engine_sinr_lc(T, cxs[c], w72, (None, w72), (None, w72), dp)
                            ok = (all(np.array_equal(ext['dl'][fi], ref['dl'][fi]) for fi in range(2))
                                  and all(np.array_equal(ext['ul'][p][fi], ref['ul'][p][fi]) for p in PROFILES for fi in range(2))
                                  and np.array_equal(ext['own72'], ref['own72']))
                            b1_bad += int(not ok)
                            b1_n += 1
                            # B2: grouped SINR equals the extension with expanded random load weights
                            Ld, Lu_ = rng_b2.random((2, N_SECT)), rng_b2.random((2, N_SECT))
                            wd = (Ld[0], pat * Ld[1])
                            wu = (Lu_[0], pat * Lu_[1])
                            ext = engine_sinr_lc(T, cxs[c], w72, (periodic(wd[0], T3), periodic(wd[1], T3)),
                                                 (periodic(wu[0], T3), periodic(wu[1], T3)), dp)
                            own = pat[grp['sec']] > 0
                            for fi in range(2):
                                gd = grouped_sinr(grp['dl'][fi]['s'], 10.0 ** (cxs[c]['n_dl'][fi] / 10.0), grp['dl'][fi]['G'],
                                                  list(wd[fi]), 0.0 if fi == 0 else dp)
                                gu = grouped_sinr(grp['ul'][fi]['s'], 10.0 ** (cxs[c]['n_ul'][fi]['F2'] / 10.0),
                                                  grp['ul'][fi]['G'], list(wu[fi]), 0.0)
                                msk = np.ones(T['n'], dtype=bool) if fi == 0 else own
                                if msk.any():
                                    b2_worst = max(b2_worst, float(np.max(np.abs(gd[msk] - ext['dl'][fi][msk]))),
                                                   float(np.max(np.abs(gu[msk] - ext['ul']['F2'][fi][msk]))))
                                b2_n += int(msk.sum())
            if r % 10 == 9 or r == n_real - 1:
                print(f'ISD {isd}: realization {r + 1}/{n_real}, {time.time() - t_isd:.0f} s', flush=True)
        gb['B1'][f'isd{isd}'] = {'comparisons': b1_n, 'not_bitwise_equal': b1_bad, 'passed': b1_n > 0 and b1_bad == 0}
        gb['B2'][f'isd{isd}'] = {'user_comparisons': b2_n, 'max_abs_diff_db': b2_worst, 'passed': b2_n > 0 and b2_worst <= 1e-9}
        res_isd = {'victims': {'outdoor_points': len(victims['out']), 'indoor_samples': len(victims['ind'])}, 'cases': {}}
        raw = {}
        b3 = {'solves': 0, 'not_converged': 0, 'max_residual': 0.0, 'max_iterations': 0, 'uniq_max_diff': 0.0,
              'uniq_compared': 0}
        b4 = {'closure_max_rel': 0.0, 'occupancy_identity_max_abs': 0.0, 'load_range_bad': 0}
        b5 = {}
        b6 = {'comparisons': 0, 'mismatches': 0}
        b7 = {'off_dl_dp_identity_bad': 0, 'scell_dl_identity_bad': 0, 'on35_le_off35_bad': 0, 'monotone_load_bad': 0,
              'monotone_outage_bad': 0, 'zero_traffic_bad': 0, 'off_has_72_group': 0}
        b8 = {}

        def track(sol):
            b3['solves'] += 1
            b3['not_converged'] += int(not sol['converged'])
            b3['max_residual'] = max(b3['max_residual'], sol['res'])
            b3['max_iterations'] = max(b3['max_iterations'], sol['it'])

        def make_check(U, st):
            def check(b_, tb, sols, fls):
                offered = np.zeros(len(U['w']))
                for fi, g in st['groups'].items():
                    fl, sol = fls[fi], sols[fi]
                    rel = np.abs(fl['served'] + fl['unserved'] - fl['offered']) / np.maximum(fl['offered'], 1e-300)
                    b4['closure_max_rel'] = max(b4['closure_max_rel'], float(rel.max()) if rel.size else 0.0)
                    on = g['cell_on']
                    if on.any():
                        b4['occupancy_identity_max_abs'] = max(b4['occupancy_identity_max_abs'], float(np.max(np.abs(
                            fl['occ_from_served'][on] - sol['L'][b_][on]))))
                    b4['load_range_bad'] += int(np.sum((sol['L'][b_] < 0.0) | (sol['L'][b_] > 1.0)))
                    offered[g['idx']] = fl['offered']
                none = st['car'] < 0
                offered[none] = tb * U['w'][none]
                b6['mismatches'] += int(not np.array_equal(offered, tb * U['w']))
                b6['comparisons'] += 1
            return check

        def zero_check(U, ctx, st):
            ez = eval_state(U, ctx, st, [0.0], quantiles=False, keep_gam=True)
            for fi, g in st['groups'].items():
                s_ = ez['sols'][fi]
                b7['zero_traffic_bad'] += int(np.any(s_['L'] != 0.0))
                fl = flows_at(g, s_, 0, 0.0, ctx.req[st['dir']])
                b7['zero_traffic_bad'] += int(not np.array_equal(fl['theta'], fl['C']))
                track(s_)

        def uniq_check(U, ctx, st_args, ev, tr):
            sub = build_state(U, ctx, *st_args, rmax=min(UNIQ_REAL, n_real))
            for fi, g in sub['groups'].items():
                lo_ = solve(g, tr, start=0.0)
                hi_ = ev['sols'][fi]['L'][:, :g['K']]
                if hi_.size:
                    b3['uniq_max_diff'] = max(b3['uniq_max_diff'], float(np.max(np.abs(lo_['L'] - hi_))))
                b3['uniq_compared'] += int(hi_.size)
                track(lo_)

        for c, dps in scope.items():
            tc = time.time()
            U = population(parts[c], n_real)
            parts[c] = None
            ctx = Ctx(cxs[c], cases[c]['carriers'])
            cres = {'constants': {'n_prb': ctx.n_prb, 'cap_bps_per_se': ctx.cap, 'required_rate_bps': ctx.req,
                                  'se_admission': ctx.se_adm, 'snr_admission_db': ctx.g_adm, 'snr_min_db': ctx.floor,
                                  'bw_op_3.5GHz_hz': ctx.bw_op35}, 'n_users_total': int(len(U['w']))}
            cal = {ell: calibrate(U, ctx, ell) for ell in ELLS}
            T50 = cal[0.5]['T_bps']
            x30 = cal[0.3]['T_bps'] / T50
            b5[c] = {str(ell): {'T_bps': v['T_bps'], 'residual': v['residual'], 'steps': v['steps']} for ell, v in cal.items()}
            cres['calibration'] = {'T_bps_per_site': {str(ell): v['T_bps'] for ell, v in cal.items()},
                                   'x_medium_over_busy': x30}
            grid = sorted(set(GRID_X) | {x30})
            ygrid = sorted({rv * x for rv in RHO.values() for x in grid})
            cres['grid_x'], cres['grid_y_ul'] = grid, ygrid
            h15 = {}
            for d in DIRS:
                gam = fb_sinr(U, ctx, d, 0, np.ones(N_SECT), 0.0)
                notadm = ctx.se(gam, d) < ctx.se_adm[d]
                h15[d] = {'not_admitted_share': float(np.sum(U['w'] * notadm) / n_real),
                          'share_between_snr_min_and_admission': float(np.sum(U['w'] * (notadm & (gam >= ctx.floor[d]))) / n_real),
                          'share_in_tr36942_low_end': float(np.sum(U['w'] * (notadm & (gam < ctx.floor[d]) & (gam >= -10.0))) / n_real)}
            cres['hazard15_labels_3.5GHz_full_buffer'] = h15
            # ---- DL
            cres['DL'] = {}
            ev_off0 = None
            for dp in dps:
                ev_off = None
                for rule in DL_STATES:
                    a72 = np.ones(N_SECT) if rule == 'on' else np.zeros(N_SECT)
                    st = build_state(U, ctx, 'DL', rule, a72, dp, False)
                    b7['off_has_72_group'] += int(rule == 'off' and 1 in st['groups'])
                    tr = np.array(grid) * T50
                    ev = eval_state(U, ctx, st, tr, check=make_check(U, st))
                    for s_ in ev['sols'].values():
                        track(s_)
                        b7['monotone_load_bad'] += int(np.sum(np.diff(s_['L'], axis=0) < -1e-12))
                    key = f'{dp}|{rule}'
                    cres['DL'][key] = {'grid': [dict(a, x=x) for a, x in zip(ev['agg'], grid)]}
                    for fi, s_ in ev['sols'].items():
                        raw[f'{c}|DL|{key}|{fi}|L'] = s_['L']
                        raw[f'{c}|DL|{key}|{fi}|rho'] = s_['rho']
                    outs = [a['outage_admitted_pooled'] for a in ev['agg']]
                    b7['monotone_outage_bad'] += int(np.sum(np.diff(outs) < -1e-12))
                    if rule == 'off':
                        ev_off = ev
                        if ev_off0 is None:
                            ev_off0 = ev
                        else:       # B7a: the all-off DL does not depend on the 7.2 GHz power offset
                            b7['off_dl_dp_identity_bad'] += int(not (np.array_equal(ev['sols'][0]['L'], ev_off0['sols'][0]['L'])
                                                                     and np.array_equal(ev['sols'][0]['rho'], ev_off0['sols'][0]['rho'])))
                    else:           # B7c: 3.5 GHz DL load with 7.2 on <= all off, per instance
                        b7['on35_le_off35_bad'] += int(np.sum(ev['sols'][0]['L'] > ev_off['sols'][0]['L'] + 1e-12))
                    uniq_check(U, ctx, ('DL', rule, a72, dp, False), ev, tr)
                    zero_check(U, ctx, st)
                    cres['DL'][key]['x_star'] = bisect_traffic(U, ctx, st, T50)
                    if (isd, c, dp) == PRIMARY and keep_t0:
                        for xr in (1.0, 4.0):
                            e1 = eval_state(U, ctx, st, [xr * T50], quantiles=False, keep_gam=True)
                            b8[f'DL|{rule}|x{xr}'] = reference_check(keep_t0, cxs[c], ctx, U, st, e1['sols'], xr * T50)
                    print(f'  ISD {isd} {c} DL dp {dp} {rule}: {time.time() - tc:.0f} s', flush=True)
            ev_off0 = ev_off = ev = None
            # ---- UL (independent of the DL power offset)
            cres['UL'] = {}
            ev_ul_off = None
            for rule in list(UL_STATES) + (list(UL_RESCUE_STATES) if c in RESCUE_CASES else []):
                rescue = rule.endswith('_rescue')
                base = rule.replace('_rescue', '')
                a72 = np.zeros(N_SECT) if base == 'off' else np.ones(N_SECT)
                st = build_state(U, ctx, 'UL', base, a72, 0.0, rescue)
                tr = np.array(ygrid) * T50
                ev = eval_state(U, ctx, st, tr, check=make_check(U, st))
                for s_ in ev['sols'].values():
                    track(s_)
                    b7['monotone_load_bad'] += int(np.sum(np.diff(s_['L'], axis=0) < -1e-12))
                cres['UL'][rule] = {'grid': [dict(a, y=y) for a, y in zip(ev['agg'], ygrid)]}
                for fi, s_ in ev['sols'].items():
                    raw[f'{c}|UL|{rule}|{fi}|L'] = s_['L']
                    raw[f'{c}|UL|{rule}|{fi}|rho'] = s_['rho']
                outs = [a['outage_admitted_pooled'] for a in ev['agg']]
                b7['monotone_outage_bad'] += int(np.sum(np.diff(outs) < -1e-12))
                if rule == 'off':
                    ev_ul_off = ev
                if rule == 'on_scell_dl':   # B7b: a DL-only SCell leaves the all-off UL exactly unchanged
                    same = (set(ev['sols']) == {0}
                            and np.array_equal(ev['sols'][0]['L'], ev_ul_off['sols'][0]['L'])
                            and np.array_equal(ev['sols'][0]['rho'], ev_ul_off['sols'][0]['rho'])
                            and [a['outage_admitted_pooled'] for a in ev['agg']] == [a['outage_admitted_pooled'] for a in ev_ul_off['agg']])
                    b7['scell_dl_identity_bad'] += int(not same)
                    cres['UL'][rule]['y_star'] = dict(cres['UL']['off']['y_star'], note='equal to off by gate B7b')
                else:
                    cres['UL'][rule]['y_star'] = bisect_traffic(U, ctx, st, T50, hi=Y_MAX)
                uniq_check(U, ctx, ('UL', base, a72, 0.0, rescue), ev, tr)
                zero_check(U, ctx, st)
                if (isd, c) == PRIMARY[:2] and rule in ('off', 'on_ulca', 'on_ulca_rescue') and keep_t0:
                    for yr in (RHO[RHO_PRIMARY] * 1.0, RHO['genai_2025'] * 4.0):
                        e1 = eval_state(U, ctx, st, [yr * T50], quantiles=False, keep_gam=True)
                        b8[f'UL|{rule}|y{yr:.4f}'] = reference_check(keep_t0, cxs[c], ctx, U, st, e1['sols'], yr * T50)
                print(f'  ISD {isd} {c} UL {rule}: {time.time() - tc:.0f} s', flush=True)
            ev = ev_ul_off = None
            xs = {}
            pairs = [('off', 'off'), ('on', 'on_ulca'), ('on', 'on_scell_dl')]
            if c in RESCUE_CASES:
                pairs += [('off', 'off_rescue'), ('on', 'on_ulca_rescue')]
            for dp in dps:
                for dl_rule, ul_rule in pairs:
                    xd = cres['DL'][f'{dp}|{dl_rule}']['x_star']['x_star']
                    for rn, rv in RHO.items():
                        xu = cres['UL'][ul_rule]['y_star']['x_star'] / rv
                        xs[f'{dp}|{dl_rule}+{ul_rule}|{rn}'] = {'x_star_DL': xd, 'x_star_UL': xu, 'x_star_both': min(xd, xu),
                                                                 'binding': 'UL' if xu < xd else 'DL'}
            cres['sustainable_traffic'] = xs
            res_isd['cases'][c] = cres
            U = None
            print(f'ISD {isd} {c} done, {time.time() - tc:.0f} s', flush=True)
        keep_t0 = None
        gb['B3'][f'isd{isd}'] = dict(b3, passed=(b3['not_converged'] == 0 and b3['max_residual'] <= 1e-9
                                                and b3['uniq_compared'] > 0 and b3['uniq_max_diff'] <= 1e-8))
        gb['B4'][f'isd{isd}'] = dict(b4, passed=(b4['closure_max_rel'] <= 1e-12 and b4['occupancy_identity_max_abs'] <= 1e-9
                                                and b4['load_range_bad'] == 0))
        gb['B5'][f'isd{isd}'] = {'calibration': b5, 'passed': all(abs(v['residual']) <= 1e-9 for cv in b5.values() for v in cv.values())}
        gb['B6'][f'isd{isd}'] = dict(b6, passed=b6['comparisons'] > 0 and b6['mismatches'] == 0)
        gb['B7'][f'isd{isd}'] = dict(b7, passed=all(v == 0 for v in b7.values()))
        if b8:
            gb['B8'][f'isd{isd}'] = {'checks': b8, 'passed': all(v['max_rel_diff_offered_load'] <= 1e-9 and v['outage_mismatches'] == 0
                                                                 and v['users_checked'] > 0 for v in b8.values())}
        summary['isd'][str(isd)] = res_isd
        np.savez_compressed(outdir / 'raw' / f'loads_isd{isd}.npz', **raw)
        print(f'ISD {isd} done in {time.time() - t_isd:.0f} s; gates ' +
              ' '.join(f'{k} {"PASS" if v[f"isd{isd}"]["passed"] else "FAIL"}' for k, v in gb.items() if f'isd{isd}' in v),
              flush=True)
    names = {'B1': 'B1_engine_extension_bitwise', 'B2': 'B2_grouping', 'B3': 'B3_fixed_point', 'B4': 'B4_closure',
             'B5': 'B5_calibration', 'B6': 'B6_identical_workloads', 'B7': 'B7_identities_monotonicity',
             'B8': 'B8_independent_reference'}
    for k, nm in names.items():
        validation[nm] = dict(gb[k], passed=bool(gb[k]) and all(v['passed'] for v in gb[k].values()))
    # ---- B9 (amendment 1): everything the UL admission point cannot reach equals run 1 exactly
    s2 = json.loads(json.dumps(summary, default=float))
    if smoke:   # code path only: run 1 has 100 realizations
        s1, raw1, ref = s2, load_raw(outdir), 'smoke self-comparison (code path only)'
    else:
        s1, raw1, ref = json.loads((RUN1_DIR / 'summary.json').read_text()), load_raw(RUN1_DIR), 'run 1 (results/phase3/p3_2b_traffic_resources/run1)'
    validation['B9_amendment1_dl_unchanged'] = dict(amendment_check(s1, s2, raw1, load_raw(outdir)), reference=ref)
    s1 = s2 = raw1 = None
    gates_ok = all(v['passed'] for v in validation.values() if isinstance(v, dict)) and not smoke
    validation['smoke_run'] = smoke
    validation['all_checks_passed'] = gates_ok
    # ---- predictions (ISD 200, C at 50 dBm)
    xs = summary['isd'][str(PRIMARY[0])]['cases'][PRIMARY[1]]['sustainable_traffic']
    dp = PRIMARY[2]
    p1 = xs[f'{dp}|off+off|{RHO_PRIMARY}']
    p2 = xs[f'{dp}|on+on_ulca|genai_2025']
    p3b = xs[f'{dp}|on+on_scell_dl|genai_2025']
    summary['predictions'] = {
        'P1_ul_binds_today': {'x_star_UL': p1['x_star_UL'], 'x_star_DL': p1['x_star_DL'], 'held': p1['x_star_UL'] < p1['x_star_DL']},
        'P2_dl_only_overstates_ai_era': {'x_star_DL': p2['x_star_DL'], 'x_star_both': p2['x_star_both'],
                                         'held': p2['x_star_both'] > 0 and p2['x_star_DL'] >= 1.25 * p2['x_star_both']},
        'P3_ul_ca_value_ai_era': {'x_star_both_ulca': p2['x_star_both'], 'x_star_both_scell_dl': p3b['x_star_both'],
                                  'held': p3b['x_star_both'] > 0 and p2['x_star_both'] >= 1.2 * p3b['x_star_both']},
        'basis': 'ISD 200, C_deployment at -3 dB (50 dBm), M2412; sustainable traffic in units of the calibrated ETSI busy-hour traffic'}
    summary['decision'] = conf['decision_rules']['all_gates_pass' if gates_ok else 'gate_fails']
    summary['wall_time_s'] = time.time() - t0
    (outdir / 'summary.json').write_text(json.dumps(summary, indent=1, default=float), encoding='utf-8')
    (outdir / 'validation.json').write_text(json.dumps(validation, indent=2, default=float), encoding='utf-8')
    manifest = {'generated_utc': datetime.now(timezone.utc).isoformat(), 'tool': 'scripts/p3_2b_traffic_resources.py',
                'git_head_at_run': args.git_head, 'python': platform.python_version(), 'numpy': np.__version__,
                'config_sha256': sha256(args.config), 'input_sha256': in_sha,
                'outputs_sha256': {'summary.json': sha256(outdir / 'summary.json'),
                                   **{f'raw/loads_isd{isd}.npz': sha256(outdir / 'raw' / f'loads_isd{isd}.npz') for isd in sorted(SCOPE)}},
                'all_checks_passed': gates_ok}
    (outdir / 'manifest.json').write_text(json.dumps(manifest, indent=2), encoding='utf-8')
    for k, v in validation.items():
        if isinstance(v, dict):
            print(k, 'PASS' if v['passed'] else 'FAIL', flush=True)
    if not smoke:
        print('predictions', json.dumps(summary['predictions'], default=float))
    print('P3.2b', 'GATES PASSED' if gates_ok else 'GATES FAILED', f'{time.time() - t0:.0f} s')
    sys.exit(0 if gates_ok else 1)


if __name__ == '__main__':
    main()
