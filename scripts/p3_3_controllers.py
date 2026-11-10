"""P3.3: band-activation controllers on development data (P3.0 revision 5, D27-D33).

Pre-registered in configs/p3/p3_3_controllers.json. Imports, unchanged: scripts/p3_2c_energy_ledger.py (energy models,
population, all-user outage), and through it the P3.2b traffic layer and the P3.1 energy models;
scripts/p3_3_traces.py (Milan traces, development week only).

1. Radio table: the P3.2b population of ISD 200, case C, solved for all eight sector patterns (amendment 1; D30) on
   the traffic grid x = 0, 0.025, ..., 5.0 (DL at three 7.2 GHz DL offsets; UL for both mixes); per pattern the
   admitted and all-user x* (bisection as P3.2b). The four canonical patterns are loaded from run 1's verified table
   (amendment 1, gate F11); the four rotated patterns are solved here.
2. Energy tables per scenario: per pattern and traffic level the site power of every radio realization (D25, D29).
3. Traces: development squares x development week, normalized by each square's busiest hour, x = g a / a_ref,
   quantized (D31); seasonal-persistence forecast fitted on development data.
4. Controllers (D32): aon, off, tod, thr_ul, thr_dl, exh (= mpc with H = 1), mpc, oracle, oracle_dl; tuned on development
   data per mix and overhead scenario by the registered rule and frozen for P3.4. Transitions are make-before-break;
   where no pattern complies the fallback is the maximum-capacity pattern (amendment 1).
5. Gates F0-F11.
Outputs in --outdir: summary.json, validation.json, manifest.json; raw/table_isd200.npz, raw/runs.json.gz (git-ignored).
"""
import argparse
import gzip
import hashlib
import json
import math
import platform
import random
import sys
import time
from datetime import datetime, timezone
from itertools import product
from pathlib import Path

import numpy as np
import scipy
from scipy import stats

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'scripts'))
import p3_2c_energy_ledger as C  # noqa: E402
import p3_3_traces as TR  # noqa: E402

B, EM = C.B, C.EM
A = B.A
CONFIG = ROOT / 'configs/p3/p3_3_controllers.json'
OUTDIR = ROOT / 'results/phase3/p3_3_controllers'
ISD, CASE = 200, 'C_deployment'
N_SECT = B.N_SECT
PATTERNS = {0: (0, 0, 0), 1: (1, 0, 0), 2: (0, 1, 0), 3: (0, 0, 1), 4: (1, 1, 0), 5: (1, 0, 1), 6: (0, 1, 1), 7: (1, 1, 1)}
NP = len(PATTERNS)
KS = tuple(range(NP))
FULL = 7                                           # all 7.2 GHz cells on
CANON = {0: 0, 1: 1, 2: 4, 3: 7}                   # run 1's nested pattern k -> pattern index (amendment 1)
NEW_PATTERNS = (2, 3, 5, 6)                        # solved in run 2
N_ON = np.array([sum(PATTERNS[p]) for p in KS])
NW = np.array([[sum(int(b and not a) for a, b in zip(PATTERNS[o], PATTERNS[n])) for n in KS] for o in KS])   # cells woken o -> n
NS = np.array([[sum(int(a and not b) for a, b in zip(PATTERNS[o], PATTERNS[n])) for n in KS] for o in KS])   # cells put to sleep
RUN1_DIR = OUTDIR / 'run1'
MIXES = dict(C.MIXES)
MIX_KEYS = tuple(MIXES)
DPS = (-3.0, 0.0, -10.0)
DP_PRIMARY = -3.0
EPS = 0.05
SLOT_S = 600.0
GROWTH = (0.5, 1.0, 1.5, 2.0, 2.5, 3.0, 3.5)
BUDGET = 0.01
SPLIT_SEED = 20261111
PENALTY_J_PER_S = 1e9
T_SCELL = C.t_scell_s(20.0)
CRITS = ('all_user', 'admitted', 'dl_only_all_user')
TUNE = {'thr_ul': {'m': (0.0, 0.05, 0.1, 0.15, 0.2, 0.3, 0.4, 0.5, 0.75, 1.0), 'H': (1, 2, 3, 6)},
        'thr_dl': {'m': (0.0, 0.05, 0.1, 0.15, 0.2, 0.3, 0.4, 0.5, 0.75, 1.0), 'H': (1, 2, 3, 6)},
        'exh': {'alpha': (0.5, 0.9, 0.95, 0.99, 0.999)},
        'mpc': {'alpha': (0.5, 0.9, 0.95, 0.99, 0.999), 'H': (3, 6)}}
TUNE_CRIT = {'thr_ul': 'all_user', 'thr_dl': 'dl_only_all_user', 'exh': 'all_user', 'mpc': 'all_user'}
CONTROLLERS = ('aon', 'off', 'tod', 'thr_ul', 'thr_dl', 'exh', 'mpc', 'oracle', 'oracle_dl')
CLASS = {'aon': 'fixed', 'off': 'fixed', 'tod': 'rule (time of day)', 'thr_ul': 'rule', 'thr_dl': 'rule (UL-agnostic)',
         'exh': 'online', 'mpc': 'online (proposed)', 'oracle': 'offline oracle', 'oracle_dl': 'offline oracle (UL-agnostic)'}
TOD_ON_FROM_SLOT = 36                 # 06:00
H_MAX = 6
TUNE_SCEN = {'S0': 'primary_S0', 'S1': 'primary_S1', 'S2': 'primary_S2'}    # amendment 1: tuning per overhead scenario
BRUTE_T = 4                           # amendment 1: 8^4 sequences


def scenario_list():
    """D32 / P3.3 scope: 20 energy scenarios (name, dl offset, hardware point, overhead, TR category, set, 3.5 GHz case,
    UL-proportional 3.5 GHz receive power)."""
    sc = []
    hw = {-3.0: C.HW_KEY[-3.0], 0.0: C.HW_KEY[0.0], -10.0: C.HW_KEY[-10.0]}
    for s in ('S0', 'S1', 'S2'):
        sc.append({'name': f'primary_{s}', 'dp': -3.0, 'hw': hw[-3.0], 'S': s, 'cat': 'cat1', 'set': 'set1', 'case35': 'right', 'ulsens': False})
    for dp, lab in ((0.0, 'hw53'), (-10.0, 'hw43')):
        for s in ('S0', 'S1', 'S2'):
            sc.append({'name': f'{lab}_{s}', 'dp': dp, 'hw': hw[dp], 'S': s, 'cat': 'cat1', 'set': 'set1', 'case35': 'right', 'ulsens': False})
    for s in ('S0', 'S1', 'S2'):
        sc.append({'name': f'cat2_{s}', 'dp': -3.0, 'hw': hw[-3.0], 'S': s, 'cat': 'cat2', 'set': 'set1', 'case35': 'right', 'ulsens': False})
    for s in ('S0', 'S1', 'S2'):
        sc.append({'name': f'ulsens_{s}', 'dp': -3.0, 'hw': hw[-3.0], 'S': s, 'cat': 'cat1', 'set': 'set1', 'case35': 'right', 'ulsens': True})
    sc.append({'name': 'left_S0', 'dp': -3.0, 'hw': hw[-3.0], 'S': 'S0', 'cat': 'cat1', 'set': 'set1', 'case35': 'left', 'ulsens': False})
    sc.append({'name': 'set3_S0', 'dp': -3.0, 'hw': hw[-3.0], 'S': 'S0', 'cat': 'cat1', 'set': 'set3', 'case35': 'right', 'ulsens': False})
    for k in C.HW_MRF:
        sc.append({'name': f'mrf{k.split("MRF")[1]}_S0', 'dp': -3.0, 'hw': k, 'S': 'S0', 'cat': 'cat1', 'set': 'set1', 'case35': 'right', 'ulsens': False})
    return sc


SCENARIOS = scenario_list()
PRIMARY = 'primary_S0'


def sha256(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


# ---------------------------------------------------------------- UL-proportional 3.5 GHz receive power (D29)
def ul_reduction_w(conf1):
    """Maximum reduction of the 3.5 GHz cell power at zero UL load: tau_UL [M P_TRX (1 - d_TRX) / eta_AFE
    + P_PHY (B/B*)^a4 K (1 - d_PHY) / eta_DBB], with the model's own parameters (right case)."""
    g = conf1['golard_table2']
    pub = conf1['sub6_table2_published']['configs']['64T64R']
    proto, _alpha, _eta, _pa, eff, _p_misc, p_trx, bb = EM._sub6_common(pub, g, 'golard')
    d_trx = conf1['sub6_table2_published']['cases']['right']['delta_idle_trx']
    dbb = g['dbb']
    p_phy = dbb['p_phy_ref_w'] * bb ** dbb['alpha4'] * pub['K']
    return proto['tau_ul'] * (pub['M'] * p_trx * (1.0 - d_trx) / eff['afe'] + p_phy * (1.0 - dbb['delta_idle_phy']) / eff['dbb'])


# ---------------------------------------------------------------- accounting (shared by every controller)
class Model:
    """Tables for one (mix, DL offset) and one energy scenario, in the form the simulator and the controllers use.
    comp[p, i], anyc[i] and maxcap (the fallback pattern) per criterion; Pw[p, i, r] site power per realization;
    Pm = mean over r. p indexes PATTERNS."""

    def __init__(self, tab, mi, dpi, en):
        self.xg, self.T50, self.rho = tab['xg'], tab['T50'], MIXES[MIX_KEYS[mi]]
        self.comp = {c: tab['comp'][c][:, mi, dpi, :] for c in CRITS}
        self.anyc = {c: tab['anyc'][c][mi, dpi, :] for c in CRITS}
        self.maxcap = {c: int(tab['maxcap'][c][mi, dpi]) for c in CRITS}
        self.sDL, self.sUL = tab['sDL'][:, dpi, :], tab['sUL'][:, mi, :]
        self.thr = {'both': tab['thr_both'][:, mi, dpi], 'dl': tab['thr_dl'][:, dpi]}
        if en is not None:
            self.Pw = en['Pw'][:, mi, :, :]
            self.Pm = self.Pw.mean(axis=2)
            self.p_act, self.p_deep, self.e_add = en['p_act'], en['p_deep'], en['e_add']
            self.t_tr, self.t_sc = en['t_tr'], en['t_sc']
            self.tw = self.t_tr + self.t_sc

    def slot_energy_mean(self, k, kp, i):
        """Expected slot energy [J] for pattern k (this slot) after kp (previous slot) at traffic index i (broadcastable).
        Make-before-break (amendment 1): if any cell wakes, the previous pattern serves for tw and the cells leaving it
        sleep after the gap; one E_add per cell entering sleep."""
        nw, ns = NW[kp, k], NS[kp, k]
        up = nw > 0
        gap = np.where(up, self.tw, 0.0)
        return (self.Pm[k, i] * (SLOT_S - gap) + np.where(up, self.Pm[kp, i] * self.tw + nw * (self.p_act - self.p_deep) * self.t_sc, 0.0)
                + ns * self.e_add)

    def gap_violation(self, k, kp, i, crit):
        up = NW[kp, k] > 0
        return np.where(up & ~self.comp[crit][kp, i] & self.anyc[crit][i], self.tw, 0.0)


def simulate(ctrl, xi, model, per_real=True):
    """Run a controller over traces xi (n, T) of grid indices. Decisions for slot t see xi[:, :t] only (the oracle is
    given the whole trace at reset). Returns per-run totals. Every run starts with all cells on."""
    n, T = xi.shape
    ctrl.reset(n, model, xi if ctrl.is_oracle else None)
    kp = np.full(n, FULL, dtype=np.int64)
    R = model.Pw.shape[2]
    E = np.zeros((n, R)) if per_real else np.zeros(n)
    out = {'viol_s': {c: np.zeros(n) for c in CRITS}, 'unavoid_s': {c: np.zeros(n) for c in CRITS},
           'bits': np.zeros(n), 'n_wake': np.zeros(n), 'n_sleep': np.zeros(n), 'gap_s': np.zeros(n)}
    ks = np.zeros((n, T), dtype=np.int8)
    rows = np.arange(n)
    for t in range(T):
        k = np.asarray(ctrl.decide(t, xi[:, :t]), dtype=np.int64)
        i = xi[:, t]
        nw, ns = NW[kp, k], NS[kp, k]
        up = nw > 0
        gap = np.where(up, model.tw, 0.0)
        if per_real:
            Pk, Pp = model.Pw[k, i, :], model.Pw[kp, i, :]
            E += (Pk * (SLOT_S - gap)[:, None] + np.where(up[:, None], Pp * model.tw + (nw * (model.p_act - model.p_deep) * model.t_sc)[:, None], 0.0)
                  + (ns * model.e_add)[:, None])
        else:
            E += model.slot_energy_mean(k, kp, i)
        for c in CRITS:
            av = model.anyc[c][i]
            fk, fp = ~model.comp[c][k, i], ~model.comp[c][kp, i]
            out['viol_s'][c] += np.where(av, (SLOT_S - gap) * fk + gap * fp, 0.0)
            out['unavoid_s'][c] += np.where(av, 0.0, SLOT_S)
        x = model.xg[i]
        out['bits'] += (model.sDL[k, i] * (SLOT_S - gap) + model.sDL[kp, i] * gap) * x * model.T50
        out['bits'] += (model.sUL[k, i] * (SLOT_S - gap) + model.sUL[kp, i] * gap) * model.rho * x * model.T50
        out['n_wake'] += nw
        out['n_sleep'] += ns
        out['gap_s'] += gap
        ks[rows, t] = k
        kp = k
    out['E_J'] = E
    out['k'] = ks
    return out


# ---------------------------------------------------------------- controllers (D32, amendment 1)
class Fixed:
    is_oracle = False

    def __init__(self, k):
        self.k = k

    def reset(self, n, model, full):
        self.n = n

    def decide(self, t, past):
        return np.full(self.n, self.k)


class TimeOfDay:
    is_oracle = False

    def reset(self, n, model, full):
        self.n = n

    def decide(self, t, past):
        return np.full(self.n, FULL if t % TR.SLOTS_DAY >= TOD_ON_FROM_SLOT else 0)


class Threshold:
    """Capacity thresholds with hysteresis on the measured traffic of the previous slot (amendment 1 for non-nested
    patterns). kind 'both' (UL-feasible, thresholds x*_all,both) or 'dl' (UL-agnostic, x*_all,DL).
    target = the pattern with the fewest cells on whose threshold covers x(1 + m), ties to the higher threshold; none:
    the maximum-capacity pattern. Switch at once if the current pattern does not cover x(1 + m); otherwise move to fewer
    cells only after H consecutive slots in which a pattern with fewer cells covers it, taking the highest-capacity
    target of that streak."""
    is_oracle = False

    def __init__(self, m, H, kind):
        self.m, self.H, self.kind = m, H, kind

    def reset(self, n, model, full):
        self.n, self.xg, self.th = n, model.xg, np.asarray(model.thr[self.kind], dtype=float)
        self.k = np.full(n, FULL)
        self.cnt = np.zeros(n, dtype=np.int64)
        self.best = np.full(n, -1)
        self.kmax = int(np.argmax(self.th))
        # order of preference: fewer cells first, then higher threshold, then lower index
        self.order = np.array(sorted(KS, key=lambda p: (N_ON[p], -self.th[p], p)))

    def target(self, x):
        need = x * (1.0 + self.m)
        ok = need[:, None] <= self.th[self.order][None, :]
        first = np.argmax(ok, axis=1)
        return np.where(ok.any(axis=1), self.order[first], self.kmax)

    def decide(self, t, past):
        if t == 0:
            return self.k.copy()
        x = self.xg[past[:, -1]]
        tg = self.target(x)
        insufficient = x * (1.0 + self.m) > self.th[self.k]
        fewer = (~insufficient) & (N_ON[tg] < N_ON[self.k])
        self.cnt = np.where(fewer, self.cnt + 1, 0)
        better = np.where(self.best < 0, tg, np.where(self.th[tg] > self.th[np.maximum(self.best, 0)], tg, self.best))
        self.best = np.where(fewer, better, -1)
        drop = fewer & (self.cnt >= self.H)
        newk = np.where(insufficient, tg, np.where(drop, self.best, self.k))
        self.cnt = np.where(drop, 0, self.cnt)
        self.best = np.where(drop, -1, self.best)
        self.k = newk
        return newk.copy()


def dp_plan(cost, allowed, k0):
    """Exact dynamic program. cost (T, n, P kp, P k), allowed (T, n, P k) -> decisions (n, T) from kp = k0 at t = 0,
    and the optimal value (n,). Ties: the lowest index."""
    T, n, P = cost.shape[0], cost.shape[1], cost.shape[-1]
    V = np.zeros((n, P))
    pol = np.zeros((T, n, P), dtype=np.int8)
    for t in range(T - 1, -1, -1):
        c = np.where(allowed[t][:, None, :], cost[t] + V[:, None, :], np.inf)
        pol[t] = np.argmin(c, axis=2)
        V = np.take_along_axis(c, pol[t].astype(np.int64)[:, :, None], axis=2)[:, :, 0]
    ks = np.zeros((n, T), dtype=np.int64)
    kp = np.full(n, k0, dtype=np.int64)
    val = V[np.arange(n), kp]
    for t in range(T):
        kp = pol[t][np.arange(n), kp].astype(np.int64)
        ks[:, t] = kp
    return ks, val


def allowed_set(model, i, crit):
    """(..., P) allowed patterns at traffic indices i (any shape): the compliant patterns where some pattern complies,
    else only the maximum-capacity pattern (amendment 1)."""
    i = np.asarray(i)
    kk = np.arange(NP)
    comp_i = np.moveaxis(model.comp[crit][:, i], 0, -1)
    return np.where(model.anyc[crit][i][..., None], comp_i, kk == model.maxcap[crit])


def stage_cost(model, i, crit, i_energy=None):
    """(..., P kp, P k) cost and (..., P k) allowed set at traffic indices i (any shape): slot energy (at i_energy if
    given, else at i) + PENALTY x gap violation (at i); allowed set at i."""
    i = np.asarray(i)
    ie = i if i_energy is None else np.asarray(i_energy)
    kk = np.arange(NP)
    k = kk.reshape((1,) * i.ndim + (1, NP))
    kp = kk.reshape((1,) * i.ndim + (NP, 1))
    cost = model.slot_energy_mean(k, kp, ie[..., None, None]) + PENALTY_J_PER_S * model.gap_violation(k, kp, i[..., None, None], crit)
    return cost, allowed_set(model, i, crit)


class Oracle:
    is_oracle = True

    def __init__(self, crit):
        self.crit = crit

    def reset(self, n, model, full):
        cost, allowed = stage_cost(model, np.asarray(full).T, self.crit)       # (T, n, P, P), (T, n, P)
        self.plan, self.value = dp_plan(cost, allowed, FULL)

    def decide(self, t, past):
        return self.plan[:, t]


class MPC:
    """Seasonal-persistence forecast with the alpha-quantile upper bound; DP over H slots on the bound (allowed sets,
    gap penalty) and the point forecast (energy); first action applied. H = 1 is 'exh'."""
    is_oracle = False

    def __init__(self, alpha, H, S, q, crit='all_user'):
        self.alpha, self.H, self.S, self.q, self.crit = alpha, H, S, q, crit

    def reset(self, n, model, full):
        self.n, self.model = n, model
        self.k = np.full(n, FULL)

    def decide(self, t, past):
        if t == 0:
            return self.k.copy()
        md = self.model
        x_prev = md.xg[past[:, -1]]
        Tn = self.H
        step, top = md.xg[1] - md.xg[0], len(md.xg) - 1
        hh = np.arange(Tn)
        xh = TR.forecast(x_prev[:, None], t - 1, hh[None, :], self.S)                             # (n, H)
        ip = np.minimum(np.floor(xh / step + 0.5).astype(np.int64), top)                         # point forecast: energy
        iu = np.minimum(np.ceil(xh * np.exp(np.asarray(self.q[:Tn]))[None, :] / step - 1e-9).astype(np.int64), top)   # upper bound
        cost, allowed = stage_cost(md, iu.T, self.crit, i_energy=ip.T)                           # (H, n, P, P), (H, n, P)
        V = np.zeros((self.n, NP))
        pol = np.zeros((Tn, self.n, NP), dtype=np.int64)
        for h in range(Tn - 1, -1, -1):
            c = np.where(allowed[h][:, None, :], cost[h] + V[:, None, :], np.inf)
            pol[h] = np.argmin(c, axis=2)
            V = np.take_along_axis(c, pol[h][:, :, None], axis=2)[:, :, 0]
        k = pol[0][np.arange(self.n), self.k]
        self.k = k
        return k.copy()


def make_controller(name, params, S, qtab):
    if name == 'aon':
        return Fixed(FULL)
    if name == 'off':
        return Fixed(0)
    if name == 'tod':
        return TimeOfDay()
    if name in ('thr_ul', 'thr_dl'):
        return Threshold(params['m'], params['H'], 'both' if name == 'thr_ul' else 'dl')
    if name == 'exh':
        return MPC(params['alpha'], 1, S, qtab[params['alpha']])
    if name == 'mpc':
        return MPC(params['alpha'], params['H'], S, qtab[params['alpha']])
    if name == 'oracle':
        return Oracle('all_user')
    if name == 'oracle_dl':
        return Oracle('dl_only_all_user')
    raise ValueError(name)


# ---------------------------------------------------------------- independent scalar reference (gate F6)
def reference_run(ks, xi_row, tab_np, mi, dpi, en):
    """Pure-Python slot loop over one run's decisions: energy (mean over realizations), avoidable violation seconds per
    criterion, delivered bits, wakes and sleeps. Written separately from simulate(): cell counts from the pattern tuples."""
    xg, T50, rho = tab_np['xg'], tab_np['T50'], MIXES[MIX_KEYS[mi]]
    Pm = en['Pw'][:, mi, :, :].mean(axis=2)
    tw = en['t_tr'] + en['t_sc']
    e = 0.0
    bits = 0.0
    viol = {c: 0.0 for c in CRITS}
    wakes = sleeps = 0
    prev = FULL
    for t in range(len(xi_row)):
        k, i = int(ks[t]), int(xi_row[t])
        woken = sum(1 for a, b in zip(PATTERNS[prev], PATTERNS[k]) if b == 1 and a == 0)
        slept = sum(1 for a, b in zip(PATTERNS[prev], PATTERNS[k]) if a == 1 and b == 0)
        g_ = tw if woken > 0 else 0.0
        e += float(Pm[k, i]) * (SLOT_S - g_)
        if woken > 0:
            e += float(Pm[prev, i]) * tw + woken * (en['p_act'] - en['p_deep']) * en['t_sc']
        e += slept * en['e_add']
        wakes += woken
        sleeps += slept
        for c in CRITS:
            comp = tab_np['comp'][c][:, mi, dpi, i]
            if comp.any():
                viol[c] += (SLOT_S - g_) * (0.0 if comp[k] else 1.0) + g_ * (0.0 if comp[prev] else 1.0)
        x = float(xg[i])
        bits += (float(tab_np['sDL'][k, dpi, i]) * (SLOT_S - g_) + float(tab_np['sDL'][prev, dpi, i]) * g_) * x * T50
        bits += (float(tab_np['sUL'][k, mi, i]) * (SLOT_S - g_) + float(tab_np['sUL'][prev, mi, i]) * g_) * rho * x * T50
        prev = k
    return {'E_J': e, 'viol_s': viol, 'bits': bits, 'n_wake': wakes, 'n_sleep': sleeps}


def brute_force(model, xi_row, crit):
    """Exhaustive search over P^T sequences (small T): minimum of the oracle's objective."""
    T = len(xi_row)
    best = math.inf
    for seq in product(range(NP), repeat=T):
        kp, tot, ok = FULL, 0.0, True
        for t, k in enumerate(seq):
            i = xi_row[t]
            if model.anyc[crit][i]:
                if not model.comp[crit][k, i]:
                    ok = False
                    break
            elif k != model.maxcap[crit]:
                ok = False
                break
            tot += float(model.slot_energy_mean(np.int64(k), np.int64(kp), np.int64(i)))
            tot += PENALTY_J_PER_S * float(model.gap_violation(np.int64(k), np.int64(kp), np.int64(i), crit))
            kp = k
        if ok:
            best = min(best, tot)
    return best


# ---------------------------------------------------------------- statistics
def tci(v):
    v = np.asarray(v, dtype=float)
    mu = float(v.mean())
    if len(v) < 2:
        return {'mean': mu, 'lo': float('nan'), 'hi': float('nan')}
    h = float(stats.t.ppf(0.975, len(v) - 1)) * float(v.std(ddof=1)) / math.sqrt(len(v))
    return {'mean': mu, 'lo': mu - h, 'hi': mu + h}


# ---------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--config', default=str(CONFIG))
    ap.add_argument('--outdir', default=str(OUTDIR))
    ap.add_argument('--git-head', default=None)
    ap.add_argument('--smoke-realizations', type=int, default=None,
                    help='code-path and timing smoke test: never a measured run (forced FAIL, outdir must differ, no result values printed)')
    args = ap.parse_args()
    t0 = time.time()
    conf = json.loads(Path(args.config).read_text(encoding='utf-8'))
    conf2b = json.loads(B.CONFIG.read_text(encoding='utf-8'))
    conf1 = json.loads(C.P31_CONFIG.read_text(encoding='utf-8'))
    conf14 = json.loads(B.M2.e155.E14_CONFIG.read_text(encoding='utf-8'))
    smoke = args.smoke_realizations is not None
    n_real = int(conf2b['settings']['realizations'])
    if smoke:
        if Path(args.outdir).resolve() == OUTDIR.resolve():
            raise SystemExit('smoke runs must not write to the measured-run directory')
        n_real = int(args.smoke_realizations)
    outdir = Path(args.outdir)
    (outdir / 'raw').mkdir(parents=True, exist_ok=True)
    validation = {}
    # ---- F0 provenance and constants
    inp = conf['inputs']
    prov = {}
    for key, d in (('p3_1', C.P31_DIR), ('p3_2b', B.OUTDIR), ('p3_2c', C.OUTDIR)):
        man = json.loads((d / 'manifest.json').read_text())
        prov[f'{key}_manifest'] = sha256(d / 'manifest.json') == inp[key]['manifest_sha256'] and bool(man['all_checks_passed'])
        prov[f'{key}_outputs'] = all(sha256(d / k) == v for k, v in man['outputs_sha256'].items())
    prov['p3_2c_config'] = sha256(C.CONFIG) == inp['p3_2c']['config_sha256']
    tf = conf['traces']['files']
    prov['trace_files_present'] = all((TR.DATA / f).exists() for f in tf)
    prov['trace_files_sha256'] = prov['trace_files_present'] and all(TR.sha256(TR.DATA / f) == h for f, h in tf.items())
    tr_c = conf['traces']
    consts = {
        'dates': tuple(tr_c['dev_dates']) == TR.DEV_DATES and tuple(tr_c['test_dates']) == TR.TEST_DATES,
        'trace_rules': (tr_c['n_keep'], tr_c['n_dev_squares'], tr_c['ref_window_slots'], tr_c['x_step'], tr_c['x_max'], tr_c['split_seed'],
                        tr_c['local_offset_ms']) == (TR.N_KEEP, TR.N_DEV_SQ, TR.REF_WINDOW, TR.X_STEP, TR.X_MAX, SPLIT_SEED, TR.LOCAL_OFFSET_MS),
        'growth': tuple(conf['growth']) == GROWTH,
        'patterns': {int(k): tuple(v) for k, v in conf['patterns'].items()} == PATTERNS,
        'budget_eps': conf['violation_budget'] == BUDGET and conf['epsilon'] == EPS == B.EPS_OUT,
        'tuning': {k: {a: tuple(b) for a, b in v.items()} for k, v in conf['tuning']['grids'].items()} == TUNE
                  and conf['tuning']['criterion'] == TUNE_CRIT,
        'controllers': tuple(conf['controllers']) == CONTROLLERS,
        'scenarios': conf['scenarios'] == SCENARIOS,
        'timing': conf['timing']['slot_s'] == SLOT_S and conf['timing']['t_scell_s'] == T_SCELL and conf['timing']['tod_on_from_slot'] == TOD_ON_FROM_SLOT,
        'oracle_penalty': conf['oracle_gap_penalty_j_per_s'] == PENALTY_J_PER_S,
        'amendment_1': ('amendment_1' in conf and conf['tuning'].get('tune_scenarios') == TUNE_SCEN
                        and conf['amendment_1']['pass_rule'] == 'F0-F11' and conf['gates']['pass_rule'] == 'F0-F11'),
    }
    validation['F0_provenance'] = {'inputs': prov, 'constants': consts, 'passed': bool(all(prov.values()) and all(consts.values()))}
    if not validation['F0_provenance']['passed']:
        (outdir / 'validation.json').write_text(json.dumps(validation, indent=2, default=float), encoding='utf-8')
        raise SystemExit('F0 failed')
    print('F0 PASS', flush=True)
    # ---- traces (development week only; the test week's files are hashed above, not read)
    t_tr = time.time()
    act, pres, cov = TR.read_week(TR.DEV_DATES)
    sel1 = TR.select_squares(act, pres, SPLIT_SEED)
    sel2 = TR.select_squares(act, pres, SPLIT_SEED)
    dev_sq, test_sq, sel_info = sel1
    aref = {int(s): TR.a_ref(act[s - 1]) for s in np.concatenate([dev_sq, test_sq])}
    norm_dev = np.stack([act[s - 1] / aref[int(s)] for s in dev_sq])
    S_prof = TR.seasonal_profile(norm_dev)
    T = norm_dev.shape[1]
    qtab = {}
    for alpha in TUNE['exh']['alpha']:
        qa = []
        for h in range(H_MAX):
            tp = np.arange(0, T - 1 - h)
            xh = TR.forecast(norm_dev[:, tp], tp[None, :], h, S_prof)
            err = np.log(norm_dev[:, tp + 1 + h] / xh)
            qa.append(float(np.quantile(err, alpha)))
        qtab[alpha] = qa
    xi_runs, qerr, clipped = {}, 0.0, {}
    for g in GROWTH:
        xg_ = g * norm_dev
        ii, nclip = TR.quantize(xg_)
        clipped[str(g)] = nclip
        inside = xg_ <= TR.X_MAX
        if inside.any():
            qerr = max(qerr, float(np.max(np.abs(TR.x_grid()[ii] - xg_)[inside])))
        xi_runs[g] = ii
    f5 = {'coverage': cov, 'all_days_aligned': all(c['slot_min'] == 0 and c['slot_max'] == 143 and c['rows_outside_day'] == 0
                                                     and c['misaligned_rows'] == 0 and c['distinct_slots'] == 144 for c in cov.values()),
          'selection_reproducible': bool(np.array_equal(sel1[0], sel2[0]) and np.array_equal(sel1[1], sel2[1])),
          'candidates': sel_info['candidates'], 'dev_squares': dev_sq.tolist(), 'test_squares': test_sq.tolist(),
          'selected_complete': bool(np.all(pres[np.concatenate([dev_sq, test_sq]) - 1]) and np.all(act[np.concatenate([dev_sq, test_sq]) - 1] > 0)),
          'max_quantization_error': qerr, 'clipped_slots': clipped, 'seconds': time.time() - t_tr}
    f5['passed'] = bool(f5['all_days_aligned'] and f5['selection_reproducible'] and f5['selected_complete']
                        and qerr <= TR.X_STEP / 2 + 1e-12 and len(dev_sq) == TR.N_DEV_SQ and len(test_sq) == TR.N_KEEP - TR.N_DEV_SQ
                        and not set(dev_sq.tolist()) & set(test_sq.tolist()))
    validation['F5_traces'] = f5
    act = pres = None
    print(f'traces: F5 {"PASS" if f5["passed"] else "FAIL"} ({f5["seconds"]:.0f} s)', flush=True)
    # ---- radio population and table
    U, ctx, cx = C.build_population(conf2b, conf14, n_real)
    R, K = n_real, n_real * N_SECT
    cal = {ell: B.calibrate(U, ctx, ell) for ell in B.ELLS}
    T50 = cal[0.5]['T_bps']
    x30 = cal[0.3]['T_bps'] / T50
    xg = TR.x_grid()
    G = len(xg)
    f3 = {'solves': 0, 'not_converged': 0, 'max_residual': 0.0, 'closure_max_rel': 0.0, 'occupancy_identity_max_abs': 0.0,
          'load_range_bad': 0, 'workload_mismatches': 0, 'workload_comparisons': 0, 'monotone_load_bad': 0, 'zero_traffic_bad': 0}

    def track(sol):
        f3['solves'] += 1
        f3['not_converged'] += int(not sol['converged'])
        f3['max_residual'] = max(f3['max_residual'], sol['res'])

    def make_check(st):
        def check(b_, tb, sols, fls):
            offered = np.zeros(len(U['w']))
            for fi, g_ in st['groups'].items():
                fl, sol = fls[fi], sols[fi]
                rel = np.abs(fl['served'] + fl['unserved'] - fl['offered']) / np.maximum(fl['offered'], 1e-300)
                f3['closure_max_rel'] = max(f3['closure_max_rel'], float(rel.max()) if rel.size else 0.0)
                on = g_['cell_on']
                if on.any():
                    f3['occupancy_identity_max_abs'] = max(f3['occupancy_identity_max_abs'],
                                                           float(np.max(np.abs(fl['occ_from_served'][on] - sol['L'][b_][on]))))
                f3['load_range_bad'] += int(np.sum((sol['L'][b_] < 0.0) | (sol['L'][b_] > 1.0)))
                offered[g_['idx']] = fl['offered']
            none = st['car'] < 0
            offered[none] = tb * U['w'][none]
            f3['workload_mismatches'] += int(not np.array_equal(offered, tb * U['w']))
            f3['workload_comparisons'] += 1
        return check

    def state(d, k, dp=0.0, pattern=None):
        a72 = np.array(pattern if pattern is not None else PATTERNS[k], dtype=float)
        if d == 'DL':
            rule = 'off' if not a72.any() else 'on'
            return B.build_state(U, ctx, 'DL', rule, a72, dp, False)
        rule = 'off' if not a72.any() else 'on_ulca'
        return B.build_state(U, ctx, 'UL', rule, a72, 0.0, False)

    # F1: reproduction of P3.2c (bitwise on its own batches) and of its x*
    f1 = {'raw_compared': 0, 'raw_mismatches': 0, 'x_star_compared': 0, 'x_star_mismatches': 0, 'table_vs_p32c_load_max_abs': 0.0,
          'table_vs_p32c_outage_max_abs': 0.0, 'calibration_equal': None}
    s2c = json.loads((C.OUTDIR / 'summary.json').read_text())
    f1['calibration_equal'] = (T50 == s2c['calibration']['T_bps_per_site']['0.5'] and x30 == s2c['calibration']['x_medium_over_busy'])
    xs_dl, ys_ul = s2c['constants']['traffic_dl_x'], s2c['constants']['traffic_ul_y']
    if not smoke:
        raw2c = dict(np.load(C.OUTDIR / 'raw' / 'levels_isd200.npz'))
        for key, d, p, dp, tv in (('DL|0.0|off', 'DL', 0, 0.0, xs_dl), ('DL|-3.0|on', 'DL', FULL, -3.0, xs_dl),
                                  ('UL|off', 'UL', 0, 0.0, ys_ul), ('UL|on_ulca', 'UL', FULL, 0.0, ys_ul)):
            ev = B.eval_state(U, ctx, state(d, p, dp), np.array(tv) * T50, quantiles=False)
            for fi, s_ in ev['sols'].items():
                f1['raw_compared'] += 1
                f1['raw_mismatches'] += int(not np.array_equal(s_['L'], raw2c[f'{key}|L{fi}']))
        raw2c = None
    # ---- amendment 1: run 1 evidence and reuse of its canonical-pattern table (gate F11)
    reuse = not smoke
    a1 = conf['amendment_1']
    man_r1 = json.loads((RUN1_DIR / 'manifest.json').read_text())
    f11 = {'run1_manifest': bool(sha256(RUN1_DIR / 'manifest.json') == a1['run1']['manifest_sha256'] and man_r1['all_checks_passed']
                                 and man_r1['git_head_at_run'] == a1['run1']['git_head'] and man_r1['config_sha256'] == a1['run1']['config_sha256']),
           'run1_outputs': all(sha256(RUN1_DIR / k) == v for k, v in man_r1['outputs_sha256'].items()),
           'reuse': reuse, 'shapes_ok': True, 'spot_arrays_compared': 0, 'spot_mismatches': 0}
    t_tab = time.time()
    DL, UL = {}, {}

    def solve_dl(p, dp):
        st = state('DL', p, dp)
        ev = B.eval_state(U, ctx, st, xg * T50, check=make_check(st), quantiles=False)
        for s_ in ev['sols'].values():
            track(s_)
            f3['monotone_load_bad'] += int(np.sum(np.diff(s_['L'], axis=0) < -1e-12))
            f3['zero_traffic_bad'] += int(np.any(s_['L'][0] != 0.0))
        L0 = ev['sols'][0]['L']
        L1 = ev['sols'][1]['L'] if 1 in ev['sols'] else np.zeros_like(L0)
        return {'L0': L0, 'L1': L1, 'agg': ev['agg'], 'reused': False,
                'oa': np.array([a['outage_all_users'] for a in ev['agg']]), 'om': np.array([a['outage_admitted_pooled'] for a in ev['agg']]),
                's': np.array([a['traffic_fractions']['served'] for a in ev['agg']])}

    def solve_ul(p, mx):
        st = state('UL', p)
        ev = B.eval_state(U, ctx, st, xg * MIXES[mx] * T50, check=make_check(st), quantiles=False)
        for s_ in ev['sols'].values():
            track(s_)
            f3['monotone_load_bad'] += int(np.sum(np.diff(s_['L'], axis=0) < -1e-12))
            f3['zero_traffic_bad'] += int(np.any(s_['L'][0] != 0.0))
        L0 = ev['sols'][0]['L']
        L1 = ev['sols'][1]['L'] if 1 in ev['sols'] else np.zeros_like(L0)
        return {'L0': L0, 'L1': L1, 'agg': ev['agg'], 'reused': False,
                'oa': np.array([a['outage_all_users'] for a in ev['agg']]), 'om': np.array([a['outage_admitted_pooled'] for a in ev['agg']]),
                's': np.array([a['traffic_fractions']['served'] for a in ev['agg']])}

    s1r = None
    if reuse:
        r1 = dict(np.load(RUN1_DIR / 'raw' / f'table_isd{ISD}.npz'))
        s1r = json.loads((RUN1_DIR / 'summary.json').read_text())
        for k1, p in CANON.items():
            for di, dp in enumerate(DPS):
                DL[(p, dp)] = {'L0': r1[f'DL_L0_k{k1}_dp{dp}'], 'L1': r1[f'DL_L1_k{k1}_dp{dp}'], 'reused': True,
                               'oa': r1['oDLa'][k1, di], 'om': r1['oDLm'][k1, di], 's': r1['sDL'][k1, di]}
            for mi, mx in enumerate(MIX_KEYS):
                UL[(p, mx)] = {'L0': r1[f'UL_L0_k{k1}_{mx}'], 'L1': r1[f'UL_L1_k{k1}_{mx}'], 'reused': True,
                               'oa': r1['oULa'][k1, mi], 'om': r1['oULm'][k1, mi], 's': r1['sUL'][k1, mi]}
        for rec in list(DL.values()) + list(UL.values()):
            f11['shapes_ok'] = f11['shapes_ok'] and rec['L0'].shape == (G, K) and rec['L1'].shape == (G, K) and rec['oa'].shape == (G,)
        r1 = None
        # spot recomputation: the all-on DL state at -3 dB and the all-on UL state at 26/74 must equal run 1's bitwise
        for d, key in (('DL', (FULL, -3.0)), ('UL', (FULL, 'genai_2025'))):
            fresh = solve_dl(*key) if d == 'DL' else solve_ul(*key)
            ref = DL[key] if d == 'DL' else UL[key]
            for f in ('L0', 'L1', 'oa', 'om', 's'):
                f11['spot_arrays_compared'] += 1
                f11['spot_mismatches'] += int(not np.array_equal(fresh[f], ref[f]))
            fresh = None
    todo = NEW_PATTERNS if reuse else KS
    for p in todo:
        for dp in DPS:
            if p == 0 and dp != DPS[0]:
                DL[(0, dp)] = DL[(0, DPS[0])]
                continue
            DL[(p, dp)] = solve_dl(p, dp)
        for mx in MIX_KEYS:
            UL[(p, mx)] = solve_ul(p, mx)
    print(f'table solved ({time.time() - t_tab:.0f} s)', flush=True)
    f11['passed'] = bool((not reuse) or (f11['run1_manifest'] and f11['run1_outputs'] and f11['shapes_ok']
                                         and f11['spot_arrays_compared'] == 10 and f11['spot_mismatches'] == 0))
    validation['F11_run1_reuse'] = f11
    # F2 activation coupling (all eight patterns; association recomputed for reused ones)
    f2 = {'off_sector_72_load_nonzero': 0, 'off_sector_users_on_72': 0, 'on_sector_72_load_zero_at_traffic': 0}
    cell_sec = np.arange(K) % N_SECT
    for (p, dp), rec in DL.items():
        pat = np.array(PATTERNS[p])
        off = pat[cell_sec] == 0
        f2['off_sector_72_load_nonzero'] += int(np.sum(rec['L1'][:, off] != 0.0))
        car = state('DL', p, dp)['car']
        f2['off_sector_users_on_72'] += int(np.sum((car == 1) & (pat[U['sec']] == 0)))
        if pat.any():
            f2['on_sector_72_load_zero_at_traffic'] += int(np.sum(rec['L1'][1:, ~off].mean(axis=1) <= 0.0))
    for (p, mx), rec in UL.items():
        pat = np.array(PATTERNS[p])
        off = pat[cell_sec] == 0
        f2['off_sector_72_load_nonzero'] += int(np.sum(rec['L1'][:, off] != 0.0))
        car = state('UL', p)['car']
        f2['off_sector_users_on_72'] += int(np.sum((car == 1) & (pat[U['sec']] == 0)))
    f2['passed'] = bool(f2['off_sector_72_load_nonzero'] == 0 and f2['off_sector_users_on_72'] == 0 and f2['on_sector_72_load_zero_at_traffic'] == 0)
    validation['F2_activation_coupling'] = f2
    # x* per pattern (admitted and all-user): canonical patterns from run 1, the others by bisection
    xs_tab = {}
    if reuse:
        for k1, p in CANON.items():
            for dp in DPS:
                for c in ('adm', 'all'):
                    xs_tab[f'DL|{p}|{dp}|{c}'] = s1r['x_star_per_pattern'][f'DL|{k1}|{dp}|{c}']
            for c in ('adm', 'all'):
                xs_tab[f'UL|{p}|{c}'] = s1r['x_star_per_pattern'][f'UL|{k1}|{c}']
    for p in todo:
        for dp in DPS:
            if p == 0 and dp != DPS[0]:
                for c in ('adm', 'all'):
                    xs_tab[f'DL|{p}|{dp}|{c}'] = xs_tab[f'DL|0|{DPS[0]}|{c}']
                continue
            st = state('DL', p, dp)
            xs_tab[f'DL|{p}|{dp}|adm'] = B.bisect_traffic(U, ctx, st, T50)
            xs_tab[f'DL|{p}|{dp}|all'] = C.bisect_all(U, ctx, st, T50, B.X_MAX)
        st = state('UL', p)
        xs_tab[f'UL|{p}|adm'] = B.bisect_traffic(U, ctx, st, T50, hi=B.Y_MAX)
        xs_tab[f'UL|{p}|all'] = C.bisect_all(U, ctx, st, T50, B.Y_MAX)
    st = None
    if not smoke:
        xa, xm = s2c['x_star']['all_user'], s2c['x_star']['admitted']
        pairs = (('DL|0|0.0', 'DL|0.0|off'), (f'DL|{FULL}|-3.0', 'DL|-3.0|on'), (f'DL|{FULL}|0.0', 'DL|0.0|on'), (f'DL|{FULL}|-10.0', 'DL|-10.0|on'),
                 ('UL|0', 'UL|off'), (f'UL|{FULL}', 'UL|on_ulca'))
        for mine, theirs in pairs:
            for c, ref in (('all', xa), ('adm', xm)):
                f1['x_star_compared'] += 1
                f1['x_star_mismatches'] += int(json.loads(json.dumps(xs_tab[f'{mine}|{c}'], default=float)) != ref[theirs])
        lv = s2c['levels']
        for key, p, dp in (('DL|off|0.0', 0, 0.0), ('DL|on|-3.0', FULL, -3.0)):
            for a in lv[key]:
                ix = np.flatnonzero(np.abs(xg - a['x']) < 1e-12)
                if ix.size:
                    rec = DL[(p, dp)]
                    f1['table_vs_p32c_load_max_abs'] = max(f1['table_vs_p32c_load_max_abs'], abs(float(rec['L0'][int(ix[0])].mean()) - a['loads']['3.5GHz']['mean']))
                    f1['table_vs_p32c_outage_max_abs'] = max(f1['table_vs_p32c_outage_max_abs'], abs(float(rec['oa'][int(ix[0])]) - a['outage_all_users']))
        f1['passed'] = bool(f1['calibration_equal'] and f1['raw_compared'] == 6 and f1['raw_mismatches'] == 0
                            and f1['x_star_compared'] == 12 and f1['x_star_mismatches'] == 0
                            and f1['table_vs_p32c_load_max_abs'] <= 1e-8 and f1['table_vs_p32c_outage_max_abs'] <= 1e-5)
    else:
        f1['reference'] = 'smoke: code path only'
        f1['passed'] = bool(math.isfinite(T50))
    validation['F1_radio_reproduction'] = f1
    U = None
    print(f'x* and F1 done ({time.time() - t0:.0f} s)', flush=True)
    # F3 (solved states), F4 (identity on solved states; monotonicity on every pattern; x* order)
    validation['F3_fixed_point'] = dict(f3, passed=bool(f3['not_converged'] == 0 and f3['max_residual'] <= 1e-9 and f3['closure_max_rel'] <= 1e-12
                                                       and f3['occupancy_identity_max_abs'] <= 1e-9 and f3['load_range_bad'] == 0
                                                       and f3['workload_comparisons'] > 0 and f3['workload_mismatches'] == 0
                                                       and f3['monotone_load_bad'] == 0 and f3['zero_traffic_bad'] == 0))
    f4 = {'identity_max_abs': 0.0, 'monotone_all_bad': 0, 'all_above_admitted': 0, 'identity_states': 0}
    for rec in list(DL.values()) + list(UL.values()):
        f4['monotone_all_bad'] += int(np.sum(np.diff(rec['oa']) < -1e-12))
        if 'agg' in rec:
            f4['identity_states'] += 1
            for a in rec['agg']:
                n_ = a['not_admitted_share']
                f4['identity_max_abs'] = max(f4['identity_max_abs'], abs(a['outage_all_users'] - (n_ + (1.0 - n_) * a['outage_admitted_pooled'])))
    for key in xs_tab:
        if key.endswith('|all'):
            f4['all_above_admitted'] += int(xs_tab[key]['x_star'] > xs_tab[key[:-4] + '|adm']['x_star'])
    validation['F4_all_user'] = dict(f4, passed=bool(f4['identity_states'] > 0 and f4['identity_max_abs'] <= 1e-12 and f4['monotone_all_bad'] == 0
                                                     and f4['all_above_admitted'] == 0))
    # numpy tables (all eight patterns)
    nM, nD = len(MIX_KEYS), len(DPS)
    tab = {'xg': xg, 'T50': T50, 'oDLa': np.zeros((NP, nD, G)), 'oDLm': np.zeros((NP, nD, G)), 'sDL': np.zeros((NP, nD, G)),
           'oULa': np.zeros((NP, nM, G)), 'oULm': np.zeros((NP, nM, G)), 'sUL': np.zeros((NP, nM, G))}
    for p in KS:
        for di, dp in enumerate(DPS):
            tab['oDLa'][p, di], tab['oDLm'][p, di], tab['sDL'][p, di] = DL[(p, dp)]['oa'], DL[(p, dp)]['om'], DL[(p, dp)]['s']
        for mi, mx in enumerate(MIX_KEYS):
            tab['oULa'][p, mi], tab['oULm'][p, mi], tab['sUL'][p, mi] = UL[(p, mx)]['oa'], UL[(p, mx)]['om'], UL[(p, mx)]['s']
    od_a, od_m = tab['oDLa'][:, None, :, :], tab['oDLm'][:, None, :, :]
    ou_a, ou_m = tab['oULa'][:, :, None, :], tab['oULm'][:, :, None, :]
    tab['comp'] = {'all_user': (od_a <= EPS) & (ou_a <= EPS), 'admitted': (od_m <= EPS) & (ou_m <= EPS),
                   'dl_only_all_user': np.broadcast_to(od_a <= EPS, (NP, nM, nD, G)).copy()}
    tab['anyc'] = {c: tab['comp'][c].any(axis=0) for c in CRITS}
    tab['thr_both'] = np.zeros((NP, nM, nD))
    tab['thr_both_adm'] = np.zeros((NP, nM, nD))
    tab['thr_dl'] = np.zeros((NP, nD))
    for p in KS:
        for di, dp in enumerate(DPS):
            xd, xdm = xs_tab[f'DL|{p}|{dp}|all']['x_star'], xs_tab[f'DL|{p}|{dp}|adm']['x_star']
            tab['thr_dl'][p, di] = xd
            for mi, mx in enumerate(MIX_KEYS):
                tab['thr_both'][p, mi, di] = min(xd, xs_tab[f'UL|{p}|all']['x_star'] / MIXES[mx])
                tab['thr_both_adm'][p, mi, di] = min(xdm, xs_tab[f'UL|{p}|adm']['x_star'] / MIXES[mx])
    # amendment 1: fallback = maximum-capacity pattern per criterion, mix and DL offset
    tab['maxcap'] = {'all_user': np.argmax(tab['thr_both'], axis=0), 'admitted': np.argmax(tab['thr_both_adm'], axis=0),
                     'dl_only_all_user': np.broadcast_to(np.argmax(tab['thr_dl'], axis=0)[None, :], (nM, nD)).copy()}
    # energy tables per scenario
    en_mod = C.Energy(conf1)
    ulred = ul_reduction_w(conf1)
    energy = {}
    for sc in SCENARIOS:
        hw = sc['hw']
        aux = en_mod.p_aux(hw, sc['S'])
        deep = en_mod.ladder(hw, sc['S'], sc['cat'], sc['set'])['deep']
        Pw = np.zeros((NP, nM, G, R))
        for p in KS:
            on_cell = np.array(PATTERNS[p])[cell_sec] == 1
            dlr = DL[(p, sc['dp'])]
            for mi, mx in enumerate(MIX_KEYS):
                ulr = UL[(p, mx)]
                p35 = en_mod.p35(sc['case35'], dlr['L0'])
                if sc['ulsens']:
                    p35 = p35 - ulred * (1.0 - ulr['L0'])
                p72 = np.where(on_cell[None, :], en_mod.p72(hw, dlr['L1'], ulr['L1']) + aux, deep['p_w'])
                Pw[p, mi] = (p35 + p72).reshape(G, R, N_SECT).sum(axis=2)
        energy[sc['name']] = {'Pw': Pw, 'p_act': en_mod.p_empty(hw) + aux, 'p_deep': deep['p_w'], 'e_add': deep['e_add_j'],
                              't_tr': en_mod.tr['transition_time_s'][sc['cat']]['deep'], 't_sc': T_SCELL}

    print(f'energy tables done ({time.time() - t0:.0f} s)', flush=True)

    def model_for(mx, sc_name):
        sc = next(s for s in SCENARIOS if s['name'] == sc_name)
        return Model(tab, MIX_KEYS.index(mx), DPS.index(sc['dp']), energy[sc_name])

    # ---- runs: development squares x growth, per mix
    xi_all = np.concatenate([xi_runs[g] for g in GROWTH], axis=0)            # (len(GROWTH) * 25, 1008)
    run_g = np.repeat(np.array(GROWTH), len(dev_sq))
    run_sq = np.tile(dev_sq, len(GROWTH))
    # tuning per mix and overhead scenario at the primary hardware (amendment 1)
    tuned, tuning_log = {}, {}
    for name in ('thr_ul', 'thr_dl', 'exh', 'mpc'):
        grid = TUNE[name]
        keys = list(grid)
        for mx in MIX_KEYS:
            for S_, scn in TUNE_SCEN.items():
                md = model_for(mx, scn)
                cands = []
                for vals in product(*(grid[k_] for k_ in keys)):
                    params = dict(zip(keys, vals))
                    o = simulate(make_controller(name, params, S_prof, qtab), xi_all, md, per_real=False)
                    share = float(o['viol_s'][TUNE_CRIT[name]].sum() / (xi_all.size * SLOT_S))
                    cands.append({'params': params, 'energy_kwh_day': float(o['E_J'].mean() / 7 / 3.6e6), 'viol_share': share})
                ok = [c_ for c_ in cands if c_['viol_share'] <= BUDGET]
                pick = min(ok, key=lambda c_: (c_['energy_kwh_day'], json.dumps(c_['params']))) if ok else min(cands, key=lambda c_: c_['viol_share'])
                tuned[f'{name}|{mx}|{S_}'] = dict(pick, budget_met=bool(ok))
                tuning_log[f'{name}|{mx}|{S_}'] = cands
        print(f'  tuned {name} ({time.time() - t0:.0f} s)', flush=True)
    print(f'tuning done ({time.time() - t0:.0f} s)', flush=True)

    def ctrl_for(name, mx, S_):
        key = f'{name}|{mx}|{S_}'
        return make_controller(name, tuned[key]['params'] if key in tuned else {}, S_prof, qtab)

    # evaluation
    results, runs_out = {}, []
    sims = {}
    for sc in SCENARIOS:
        for mx in MIX_KEYS:
            md = model_for(mx, sc['name'])
            for name in CONTROLLERS:
                o = simulate(ctrl_for(name, mx, sc['S']), xi_all, md, per_real=True)
                sims[(name, mx, sc['name'])] = o
    print(f'evaluation done ({time.time() - t0:.0f} s)', flush=True)
    for (name, mx, scn), o in sims.items():
        aon = sims[('aon', mx, scn)]
        for g in GROWTH:
            sel = run_g == g
            E = o['E_J'][sel] / 7 / 3.6e6                     # kWh per site-day, (squares, R)
            Ea = aon['E_J'][sel] / 7 / 3.6e6
            secs = sel.sum() * xi_all.shape[1] * SLOT_S
            share = {c: float(o['viol_s'][c][sel].sum() / secs) for c in CRITS}
            unav = {c: float(o['unavoid_s'][c][sel].sum() / secs) for c in CRITS}
            share_aon = float(aon['viol_s']['all_user'][sel].sum() / secs)
            sav_sq = 1.0 - E.mean(axis=1) / Ea.mean(axis=1)
            sav_r = 1.0 - E.mean(axis=0) / Ea.mean(axis=0)
            both_ok = share['all_user'] <= BUDGET and share_aon <= BUDGET
            bits = o['bits'][sel].sum()
            results[f'{name}|{mx}|{g}|{scn}'] = {
                'E_site_kWh_day': {'by_square': tci(E.mean(axis=1)), 'by_realization': tci(E.mean(axis=0))},
                'saving_vs_aon': ({'by_square': tci(sav_sq), 'by_realization': tci(sav_r)} if both_ok else None),
                'saving_vs_aon_unqualified_mean': float(sav_sq.mean()),
                'avoidable_violation_share': share, 'unavoidable_share': unav, 'budget_met_all_user': share['all_user'] <= BUDGET,
                'aon_budget_met': share_aon <= BUDGET,
                'transitions_per_day': float((o['n_wake'][sel] + o['n_sleep'][sel]).mean() / 7),
                'gap_s_per_day': float(o['gap_s'][sel].mean() / 7),
                'J_per_Mbit': float(o['E_J'][sel].mean(axis=1).sum() / bits * 1e6) if bits > 0 else None,
                'mean_cells_on': float(N_ON[o['k'][sel].astype(np.int64)].mean()),
                'pattern_share': {str(PATTERNS[p_]): float(np.mean(o['k'][sel] == p_)) for p_ in KS}}
            if scn == PRIMARY:
                for j in np.flatnonzero(sel):
                    runs_out.append({'controller': name, 'mix': mx, 'g': g, 'square': int(run_sq[j]),
                                     'E_kWh_day': float(o['E_J'][j].mean() / 7 / 3.6e6),
                                     'viol_share': {c: float(o['viol_s'][c][j] / (xi_all.shape[1] * SLOT_S)) for c in CRITS}})
    # ---- F6 ledger (independent scalar loop)
    rng = random.Random(SPLIT_SEED + 6)
    tab_np = {'xg': xg, 'T50': T50, 'comp': tab['comp'], 'sDL': tab['sDL'], 'sUL': tab['sUL']}
    f6 = {'runs_checked': 0, 'energy_max_rel': 0.0, 'viol_max_abs_s': 0.0, 'bits_max_rel': 0.0, 'transition_mismatches': 0}
    keys_all = list(sims)
    for _ in range(100):
        name, mx, scn = keys_all[rng.randrange(len(keys_all))]
        j = rng.randrange(xi_all.shape[0])
        o = sims[(name, mx, scn)]
        sc = next(s for s in SCENARIOS if s['name'] == scn)
        ref = reference_run(o['k'][j], xi_all[j], tab_np, MIX_KEYS.index(mx), DPS.index(sc['dp']), energy[scn])
        f6['runs_checked'] += 1
        f6['energy_max_rel'] = max(f6['energy_max_rel'], EM.rel_diff(float(o['E_J'][j].mean()), ref['E_J']))
        f6['viol_max_abs_s'] = max(f6['viol_max_abs_s'], max(abs(float(o['viol_s'][c][j]) - ref['viol_s'][c]) for c in CRITS))
        f6['bits_max_rel'] = max(f6['bits_max_rel'], EM.rel_diff(float(o['bits'][j]), ref['bits']))
        f6['transition_mismatches'] += int(o['n_wake'][j] != ref['n_wake'] or o['n_sleep'][j] != ref['n_sleep'])
    f6['passed'] = bool(f6['runs_checked'] == 100 and f6['energy_max_rel'] <= 1e-9 and f6['viol_max_abs_s'] <= 1e-6
                        and f6['bits_max_rel'] <= 1e-9 and f6['transition_mismatches'] == 0)
    validation['F6_ledger'] = f6
    print(f'F6 done ({time.time() - t0:.0f} s)', flush=True)
    # ---- F7 causality
    f7 = {'checks': 0, 'violations': 0}
    md = model_for(MIX_KEYS[1], PRIMARY)
    for name in CONTROLLERS:
        if name in ('oracle', 'oracle_dl'):
            continue
        rows_, cuts_ = [], []
        for _ in range(20):                    # each base run and its 5 perturbed copies are independent rows
            base = xi_all[rng.randrange(xi_all.shape[0])].copy()
            rows_.append(base)
            cuts_.append(None)
            for cut in sorted(rng.sample(range(1, base.shape[0]), 5)):
                pert = base.copy()
                pert[cut:] = np.array([rng.randrange(G) for _ in range(base.shape[0] - cut)])
                rows_.append(pert)
                cuts_.append(cut)
        o = simulate(ctrl_for(name, MIX_KEYS[1], 'S0'), np.stack(rows_), md, per_real=False)
        for r0 in range(0, len(rows_), 6):
            for q_ in range(1, 6):
                cut = cuts_[r0 + q_]
                f7['checks'] += 1
                f7['violations'] += int(not np.array_equal(o['k'][r0, :cut + 1], o['k'][r0 + q_, :cut + 1]))
    f7['passed'] = bool(f7['checks'] > 0 and f7['violations'] == 0)
    validation['F7_causality'] = f7
    print(f'F7 done ({time.time() - t0:.0f} s)', flush=True)
    # ---- F8 DP correctness
    f8 = {'random_instances': 0, 'real_windows': 0, 'dp_vs_brute_max_rel': 0.0}
    rs = np.random.default_rng(SPLIT_SEED + 8)
    for _ in range(200):
        tiny = Model.__new__(Model)
        tiny.xg = np.arange(5) * 1.0
        tiny.Pm = rs.uniform(100, 900, (NP, 5))
        tiny.p_act, tiny.p_deep, tiny.e_add = 170.0, 3.0, rs.uniform(0, 2000)
        tiny.t_tr, tiny.t_sc = 10.0, 0.105
        tiny.tw = tiny.t_tr + tiny.t_sc
        comp = rs.random((NP, 5)) < 0.6
        tiny.comp = {'all_user': comp}
        tiny.anyc = {'all_user': comp.any(axis=0)}
        tiny.maxcap = {'all_user': int(rs.integers(0, NP))}
        row = rs.integers(0, 5, BRUTE_T)
        cost, alw = stage_cost(tiny, row[:, None], 'all_user')
        _, val = dp_plan(cost, alw, FULL)
        bf = brute_force(tiny, row, 'all_user')
        f8['random_instances'] += 1
        f8['dp_vs_brute_max_rel'] = max(f8['dp_vs_brute_max_rel'], EM.rel_diff(float(val[0]), bf))
    md = model_for(MIX_KEYS[1], PRIMARY)
    for _ in range(50):
        j = rng.randrange(xi_all.shape[0])
        t_ = rng.randrange(1, xi_all.shape[1] - BRUTE_T)
        row = xi_all[j, t_:t_ + BRUTE_T]
        cost, alw = stage_cost(md, row[:, None], 'all_user')
        _, val = dp_plan(cost, alw, FULL)
        f8['real_windows'] += 1
        f8['dp_vs_brute_max_rel'] = max(f8['dp_vs_brute_max_rel'], EM.rel_diff(float(val[0]), brute_force(md, row, 'all_user')))
    f8['passed'] = bool(f8['random_instances'] == 200 and f8['real_windows'] == 50 and f8['dp_vs_brute_max_rel'] <= 1e-12)
    validation['F8_dp_correctness'] = f8
    print(f'F8 done ({time.time() - t0:.0f} s)', flush=True)
    # ---- F9 fairness: identical traces for every controller (by construction: one xi_all), oracle-only full access
    f9 = {'trace_sha256': hashlib.sha256(xi_all.tobytes()).hexdigest(),
          'oracle_only_full_trace': all((name in ('oracle', 'oracle_dl')) == make_controller(name, {'m': 0, 'H': 1, 'alpha': 0.9}, S_prof, qtab).is_oracle
                                        for name in CONTROLLERS),
          'controllers_on_identical_runs': len({(o['k'].shape) for o in sims.values()}) == 1}
    f9['passed'] = bool(f9['oracle_only_full_trace'] and f9['controllers_on_identical_runs'])
    validation['F9_fairness'] = f9
    # ---- F10 decision time (one development square, g = 2, 26/74, primary)
    f10 = {}
    md = model_for(MIX_KEYS[1], PRIMARY)
    row = xi_runs[2.0][:1]
    for name in CONTROLLERS:
        ctl = ctrl_for(name, MIX_KEYS[1], 'S0')
        t_r = time.perf_counter()
        ctl.reset(1, md, row if ctl.is_oracle else None)
        t_reset = time.perf_counter() - t_r
        ts = []
        for t in range(row.shape[1]):
            t_d = time.perf_counter()
            ctl.decide(t, row[:, :t])
            ts.append(time.perf_counter() - t_d)
        f10[name] = {'reset_ms': t_reset * 1e3, 'decide_median_ms': float(np.median(ts) * 1e3), 'decide_p99_ms': float(np.quantile(ts, 0.99) * 1e3)}
    validation['F10_decision_time'] = {'per_controller': f10, 'machine': platform.platform(), 'processor': platform.processor(),
                                       'passed': len(f10) == len(CONTROLLERS)}
    gates_ok = all(v['passed'] for v in validation.values() if isinstance(v, dict)) and not smoke
    validation['smoke_run'] = smoke
    validation['all_checks_passed'] = gates_ok
    # ---- summary
    maxcap = {}
    for mi, mx in enumerate(MIX_KEYS):
        for di, dp in enumerate(DPS):
            th = tab['thr_both'][:, mi, di]
            maxcap[f'{mx}|{dp}'] = {'x_star_both_all_by_pattern': {str(PATTERNS[p_]): float(th[p_]) for p_ in KS},
                                    'max_pattern': str(PATTERNS[int(np.argmax(th))]), 'all_on_is_max': bool(int(np.argmax(th)) == FULL)}
    summary = {
        'n_realizations': R, 'calibration': {'T_bps_per_site': {str(e): v['T_bps'] for e, v in cal.items()}, 'x_medium_over_busy': x30},
        'labels': conf['labels'], 'classes': CLASS,
        'patterns': {str(p_): list(v) for p_, v in PATTERNS.items()}, 'x_star_per_pattern': xs_tab, 'max_capacity_pattern': maxcap,
        'fallback_pattern': {c: {f'{mx}|{dp}': str(PATTERNS[int(tab['maxcap'][c][mi, di])]) for mi, mx in enumerate(MIX_KEYS)
                                 for di, dp in enumerate(DPS)} for c in CRITS},
        'ul_reduction_w_per_cell_at_zero_ul_load': ulred,
        'traces': {'dev_squares': dev_sq.tolist(), 'test_squares': test_sq.tolist(), 'a_ref': aref, 'seasonal_profile': S_prof.tolist(),
                   'forecast_log_error_quantiles': {str(a): q for a, q in qtab.items()}, 'clipped_slots': clipped, 'candidates': sel_info['candidates']},
        'tuning': {'selected': tuned, 'log': tuning_log},
        'frozen_for_p3_4': {'params': {k: v['params'] for k, v in tuned.items()}, 'seasonal_profile': S_prof.tolist(),
                            'forecast_log_error_quantiles': {str(a): q for a, q in qtab.items()}, 'a_ref_test_squares': {str(s): aref[int(s)] for s in test_sq},
                            'test_squares': test_sq.tolist(), 'T50_bps': T50, 'thresholds_x_star_dev': {'both': tab['thr_both'].tolist(), 'dl': tab['thr_dl'].tolist()},
                            'fallback_pattern_index': {c: tab['maxcap'][c].tolist() for c in CRITS}, 'tuning_key': 'controller|mix|S'},
        'results_dev': results, 'decision_time': f10,
        'decision': conf['decision_rules']['all_gates_pass' if gates_ok else 'gate_fails'],
    }
    summary['wall_time_s'] = time.time() - t0
    np.savez_compressed(outdir / 'raw' / f'table_isd{ISD}.npz', xg=xg, oDLa=tab['oDLa'], oDLm=tab['oDLm'], sDL=tab['sDL'], oULa=tab['oULa'],
                        oULm=tab['oULm'], sUL=tab['sUL'], **{f'Pw_{k}': v['Pw'] for k, v in energy.items()},
                        **{f'DL_L0_k{k}_dp{dp}': v['L0'] for (k, dp), v in DL.items()}, **{f'DL_L1_k{k}_dp{dp}': v['L1'] for (k, dp), v in DL.items()},
                        **{f'UL_L0_k{k}_{mx}': v['L0'] for (k, mx), v in UL.items()}, **{f'UL_L1_k{k}_{mx}': v['L1'] for (k, mx), v in UL.items()})
    with gzip.open(outdir / 'raw' / 'runs.json.gz', 'wt', encoding='utf-8') as fh:
        json.dump(runs_out, fh)
    (outdir / 'summary.json').write_text(json.dumps(summary, indent=1, default=float), encoding='utf-8')
    (outdir / 'validation.json').write_text(json.dumps(validation, indent=2, default=float), encoding='utf-8')
    manifest = {'generated_utc': datetime.now(timezone.utc).isoformat(), 'tool': 'scripts/p3_3_controllers.py', 'git_head_at_run': args.git_head,
                'python': platform.python_version(), 'numpy': np.__version__, 'scipy': scipy.__version__, 'pandas': TR.pd.__version__,
                'config_sha256': sha256(args.config),
                'input_sha256': {'p3_1/manifest.json': sha256(C.P31_DIR / 'manifest.json'), 'p3_2b/manifest.json': sha256(B.OUTDIR / 'manifest.json'),
                                 'p3_2c/manifest.json': sha256(C.OUTDIR / 'manifest.json'), 'p3_3/run1/manifest.json': sha256(RUN1_DIR / 'manifest.json'),
                                 **{f'traces/{f}': h for f, h in tf.items()}},
                'outputs_sha256': {'summary.json': sha256(outdir / 'summary.json'), f'raw/table_isd{ISD}.npz': sha256(outdir / 'raw' / f'table_isd{ISD}.npz'),
                                   'raw/runs.json.gz': sha256(outdir / 'raw' / 'runs.json.gz')},
                'all_checks_passed': gates_ok}
    (outdir / 'manifest.json').write_text(json.dumps(manifest, indent=2), encoding='utf-8')
    for k, v in validation.items():
        if isinstance(v, dict):
            print(k, 'PASS' if v['passed'] else 'FAIL', flush=True)
    print('P3.3', 'GATES PASSED' if gates_ok else 'GATES FAILED', f'{time.time() - t0:.0f} s')
    sys.exit(0 if gates_ok else 1)


if __name__ == '__main__':
    main()
