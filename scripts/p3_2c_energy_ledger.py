"""P3.2c: time-dependent energy and service ledger over the ETSI day.

Pre-registered in configs/p3/p3_2c_energy_ledger.json (P3.0 revision 4, D20-D26). scripts/p3_2b_traffic_resources.py
(the traffic layer, run 2 of record) and scripts/p3_1_energy_models.py (the energy models) are imported, not modified.

Per level of the day (low 00-06 zero traffic, medium 06-16 x = g x_30, busy 16-24 x = g) and per radio state the
P3.2b layer gives the per-cell realized occupancies and the admitted-user and all-user outage (D21). P3.1 maps the
occupancies to cell powers: 3.5 GHz, the measurement-anchored model at its DL load; 7.2 GHz, the FR3 component model
at its DL and UL loads plus the overhead P_aux of S0-S2 (D11, D25). A time-of-day schedule puts each 7.2 GHz cell
on, in deep sleep, or in an activation interval (D4, D23, D24); the ledger integrates the powers over the day.
Labels (D20): 'on' / 'on_ulca' are carrier selection, not carrier aggregation.
Outputs in --outdir: summary.json, validation.json, manifest.json; raw/levels_isd200.npz and raw/energy_table.json.gz
(git-ignored, hashed in the manifest).
"""
import argparse
import gzip
import hashlib
import json
import math
import platform
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import scipy
from scipy import stats

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'scripts'))
import p3_1_energy_models as EM  # noqa: E402
import p3_2b_traffic_resources as B  # noqa: E402

A, m, M2 = B.A, B.m, B.M2
CONFIG = ROOT / 'configs/p3/p3_2c_energy_ledger.json'
OUTDIR = ROOT / 'results/phase3/p3_2c_energy_ledger'
P31_DIR = ROOT / 'results/phase3/p3_1_energy_models'
P31_CONFIG = ROOT / 'configs/p3/p3_1_energy_models.json'
N_SECT, DIRS = B.N_SECT, B.DIRS
ISD, CASE = 200, 'C_deployment'
DAY_S = 86400.0
LEVELS = (('low', 0.0, 21600.0), ('medium', 21600.0, 57600.0), ('busy', 57600.0, 86400.0))
TRAFFIC_LEVELS = ('medium', 'busy')
GROWTH = tuple(0.5 + 0.25 * k for k in range(15))
MIXES = {'ericsson_typical': 10.0 / 90.0, 'genai_2025': 26.0 / 74.0}
MIX_PRIMARY = 'ericsson_typical'
EPS = 0.05
DPS = (0.0, -3.0, -10.0)                              # P3.2b order
DP_PRIMARY = -3.0
HW_KEY = {-3.0: '50dBm_SiGe_SiGe_MRF256', 0.0: '53dBm_assumedPA_eta0.15_MRF256', -10.0: '43dBm_SiGe_SiGe_MRF256'}
HW_MRF = ('50dBm_SiGe_SiGe_MRF64', '50dBm_SiGe_SiGe_MRF128', '50dBm_SiGe_SiGe_MRF1024')
CASES35 = ('right', 'left')
SCEN = ('S0', 'S1', 'S2')
CATS = ('cat1', 'cat2')
SETS = ('set1', 'set3')
SMTC_MS = {'primary': 20.0, 'sensitivity': 160.0}
WAKE = ('proactive', 'reactive')
STATIC_FORM = 'golard'
S1_EXPECTED_W = 172.0
POLICIES = {
    '35only': {'on_levels': [], 'ul': None, 'bound': False},
    'aon_ulsel': {'on_levels': ['low', 'medium', 'busy'], 'ul': 'on_ulca', 'bound': False},
    'aon_dlonly': {'on_levels': ['low', 'medium', 'busy'], 'ul': 'on_scell_dl', 'bound': False},
    'sch_busy_ulsel': {'on_levels': ['busy'], 'ul': 'on_ulca', 'bound': False},
    'sch_busy_dlonly': {'on_levels': ['busy'], 'ul': 'on_scell_dl', 'bound': False},
    'sch_medbusy_ulsel': {'on_levels': ['medium', 'busy'], 'ul': 'on_ulca', 'bound': False},
    'sch_medbusy_dlonly': {'on_levels': ['medium', 'busy'], 'ul': 'on_scell_dl', 'bound': False},
    'bound_sch_busy_ulsel': {'on_levels': ['busy'], 'ul': 'on_ulca', 'bound': True},
    'bound_sch_busy_dlonly': {'on_levels': ['busy'], 'ul': 'on_scell_dl', 'bound': True},
    'bound_sch_medbusy_ulsel': {'on_levels': ['medium', 'busy'], 'ul': 'on_ulca', 'bound': True},
    'bound_sch_medbusy_dlonly': {'on_levels': ['medium', 'busy'], 'ul': 'on_scell_dl', 'bound': True},
}
CRITERIA = ('all_user', 'admitted', 'dl_only_all_user')
TOY = {'levels': (('off', 0.0, 7200.0), ('on', 7200.0, 10800.0)), 'p_on': 100.0, 'p_sleep': 2.0, 't_tr': 0.05,
       'e_add': 3.0, 'p_act': 50.0, 'expected_j': 374403.0, 'expected_act_j': 374451.0}


def sha256(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def t_scell_s(smtc_ms):
    """D23: T_SCell = 5 P + 5 ms (TS 38.133 V19.6.0 cl. 8.3.2, unknown FR1 SCell, inter-band, extrapolated)."""
    return (5.0 * smtc_ms + 5.0) / 1000.0


# ---------------------------------------------------------------- energy models (P3.1, unchanged)
class Energy:
    """P3.1's models at the project points: P_3.5(case, L), P_7.2(point, x_DL, x_UL), P_mu, P_empty, P_aux, ladder."""

    def __init__(self, conf1):
        self.g = conf1['golard_table2']
        self.pub64 = conf1['sub6_table2_published']['configs']['64T64R']
        self.tr = conf1['tr38864']
        self.eta35 = EM.golard_eta_max(self.pub64['p_max_w'], self.pub64['f_hz'], self.g)
        self.pts = EM.project_points(conf1, self.eta35)

    def p35(self, case, L):
        par = EM.sub6_published_params(self.pub64, case)
        return EM.sub6_compact(par, self.pub64['M'], L, self.pub64['M'], self.pub64['p_max_w'])

    def p72(self, key, xd, xu):
        return EM.fr3_power(self.pts[key], xd, xu, STATIC_FORM)

    def p_mu(self, key):
        return EM.fr3_power(self.pts[key], 0.0, 0.0, STATIC_FORM, tau_sig=0.0)

    def p_empty(self, key):
        return EM.fr3_power(self.pts[key], 0.0, 0.0, STATIC_FORM)

    def p_aux(self, key, scen):
        """D11: S0 = 0; S1 = Golard's AAU misc AFE and link-layer baseband through their eta; S2 = no-load parity."""
        if scen == 'S0':
            return 0.0
        if scen == 'S1':
            hw, g = self.pts[key], self.g
            afe, dbb, psc = g['afe'], g['dbb'], g['psc']
            p_misc = (afe['p_misc_ref_w']['AAU'] * (hw['B'] / g['b_star_hz']) ** afe['alpha11']
                      * (hw['M_ant'] * hw['pa']['p_max_w'] / g['p_star_w']) ** afe['alpha12'])
            p_link = dbb['p_link_ref_w'] / 1.0 ** dbb['alpha3']
            return p_misc / (psc['eta_supply'] * psc['eta_cool_afe']) + p_link / (psc['eta_supply'] * psc['eta_cool_bb'])
        if scen == 'S2':
            return self.p35('right', 0.0) - self.p_empty(key)
        raise ValueError(scen)

    def ladder(self, key, scen, cat, set_name):
        return EM.ladder(self.p_mu(key) + self.p_aux(key, scen), self.tr, cat, set_name)


def power_reproduction(en, conf1, s1):
    """Gate E1: the power functions reproduce P3.1's tabulated values (<= 1e-12 relative)."""
    worst, n = 0.0, 0
    lay3 = conf1['project']['layer_3p5']
    for case in CASES35:
        for L in lay3['loads']:
            worst = max(worst, EM.rel_diff(en.p35(case, L), s1['layer_3p5']['P_w_by_case_and_load'][case][str(L)]))
            n += 1
    lay7 = conf1['project']['layer_7p2']
    loads = [(x, x) for x in lay7['loads_equal_dl_ul']] + [tuple(v) for v in lay7['loads_extra']]
    for key in list(HW_KEY.values()) + list(HW_MRF):
        rec = s1['layer_7p2']['points'][key]
        for xd, xu in loads:
            worst = max(worst, EM.rel_diff(en.p72(key, xd, xu), rec['P_w'][f'{xd}_{xu}']))
            n += 1
        worst = max(worst, EM.rel_diff(en.p_mu(key), rec['P_mu_w']), EM.rel_diff(en.p_empty(key), rec['P_empty_w']))
        n += 2
        for cat in CATS:
            for s_name in SETS:
                lad, ref = en.ladder(key, 'S0', cat, s_name), rec['ladder'][f'{cat}_{s_name}']
                for st in ('light', 'deep'):
                    for f in ('p_w', 't_s', 'e_add_j'):
                        worst = max(worst, EM.rel_diff(lad[st][f], ref[st][f]))
                        n += 1
    s2_worst = max(EM.rel_diff(en.p_empty(k) + en.p_aux(k, 'S2'), en.p35('right', 0.0)) for k in list(HW_KEY.values()) + list(HW_MRF))
    s1_primary = en.p_aux(HW_KEY[DP_PRIMARY], 'S1')
    return {'compared': n, 'max_rel_diff': worst, 's2_parity_max_rel_diff': s2_worst, 's1_primary_w': s1_primary,
            'passed': bool(n > 0 and worst <= 1e-12 and s2_worst <= 1e-12)}


# ---------------------------------------------------------------- population (as P3.2b main, ISD 200, case C only)
def build_population(conf2b, conf14, n_real):
    """The P3.2b population of ISD 200, case C (the same random streams and operations as P3.2b's main loop; gate E2
    checks the reproduction bitwise). Returns U, ctx, cx."""
    seed = int(conf2b['settings']['seed'])
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
    if tuple(sorted(mix)) != tuple(sorted(B.MIX_STATES)) or abs(sum(mix.values()) - 1.0) > 1e-12:
        raise SystemExit('unexpected M2412 mix')
    cxs = {c: M2.case_context(cases[c], maps, pkt_off, sel_r) for c in A.CASES}
    freqs = cxs['A_equal_elements']['freqs']
    with np.load(M2.E11_DIR / 'site_tx0_mask.npz') as z:
        mask = {k: z[k] for k in z.files}
    with np.load(M2.GEO_DIR / 'site_tx0_indoor.npz') as z:
        geo = {k: z[k] for k in z.files}
    gnb = np.array(mask['gnb'], dtype=float)
    isd, scope = ISD, (CASE,)
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
    sterm = {st: M2.state_terms(st, vb['out' if st in ('outdoor', 'in_car') else 'ind'], freqs) for st in B.MIX_STATES}
    nv = {st: vb['out' if st in ('outdoor', 'in_car') else 'ind']['n'] for st in B.MIX_STATES}
    print(f'ISD {isd}: victims {[len(v) for v in victims.values()]}, case {CASE}', flush=True)
    parts = []
    t0 = time.time()
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
        for st in B.MIX_STATES:
            si = B.STATES.index(st)
            kind = 'out' if st in ('outdoor', 'in_car') else 'ind'
            b = vb[kind]
            z, u, x = M2.draw_ue_terms(M2.rng_for(seed, isd, r, 1 + si), b['n'], S)
            pen, sig, sue = sterm[st]
            L, _ = M2.losses(b, pen, sig, sue, z, u, x)
            Llin = 10.0 ** (-L / 10.0)
            for c in scope:
                T = A.engine_terms(b, vg[kind][c], L, Llin, cxs[c], va[kind][c], cc[c])
                parts.append((r, mix[st] / nv[st], B.group_terms(T, cxs[c])))
        if r % 10 == 9 or r == n_real - 1:
            print(f'ISD {isd}: realization {r + 1}/{n_real}, {time.time() - t0:.0f} s', flush=True)
    U = B.population(parts, n_real)
    return U, B.Ctx(cxs[CASE], cases[CASE]['carriers']), cxs[CASE]


# ---------------------------------------------------------------- service (all-user criterion, D21)
def outage_all_at(U, ctx, st, tb):
    """All-user outage at one traffic level: (admitted users in outage + not-admitted users) / users (weights)."""
    d, R = st['dir'], U['R']
    num = float(np.sum(U['w'] * (st['car'] < 0)))
    for fi, g in st['groups'].items():
        sol = B.solve(g, [tb])
        num += float(np.sum(g['w'] * B.flows_at(g, sol, 0, tb, ctx.req[d])['out']))
    return num / R


def bisect_all(U, ctx, st, t_unit, hi):
    """Largest traffic (units of t_unit) with all-user outage <= EPS: B.bisect_traffic with the all-user outage."""
    def ok(x):
        return outage_all_at(U, ctx, st, x * t_unit) <= EPS
    if not ok(1e-9):
        return {'x_star': 0.0, 'bracket': [0.0, 1e-9], 'status': 'qos_infeasible_unloaded', 'steps': 1}
    if ok(hi):
        return {'x_star': hi, 'bracket': [hi, None], 'status': 'at_least_upper_limit', 'steps': 2}
    a, b, steps = 0.0, hi, 2
    while b - a > B.BIS_TOL:
        mid = 0.5 * (a + b)
        steps += 1
        if ok(mid):
            a = mid
        else:
            b = mid
    return {'x_star': a, 'bracket': [a, b], 'status': 'bracketed', 'steps': steps}


# ---------------------------------------------------------------- schedules and ledger (D4, D23, D24)
def schedule(policy, wake, t_tr, t_scell, levels=LEVELS, policies=POLICIES):
    """7.2 GHz cell segments [(kind, level, t0, t1)] covering [0, day) once; kind: 'on' (serving), 'sleep' (one deep-
    sleep period, including its transition time), 'act' (awake and empty, SCell activation, serving nothing), 'hold'
    (35only: deep sleep, no transition). Decisions [(t, action)] depend on the time of day only. Reactive gaps
    [(level, t0, t1)]: the level's traffic carried by the 3.5-GHz-only state."""
    pol = policies[policy]
    on = set(pol['on_levels'])
    names = [lv[0] for lv in levels]
    day = levels[-1][2]
    if not on:
        return [('hold', n, a, b) for n, a, b in levels], [(0.0, 'hold')], []
    if on == set(names):
        return [('on', n, a, b) for n, a, b in levels], [(0.0, 'on')], []
    first = min(i for i, n in enumerate(names) if n in on)
    if names[0] in on or any(n not in on for n in names[first:]):
        raise ValueError('schedule not of the registered form (one sleep from 00:00 to the first on level)')
    t_b = levels[first][1]
    if pol['bound']:
        t_tr, t_scell = 0.0, 0.0
    if wake == 'proactive':
        req, t_sleep_end, t_act_end = t_b - t_scell - t_tr, t_b - t_scell, t_b
    elif wake == 'reactive':
        req, t_sleep_end, t_act_end = t_b, t_b + t_tr, t_b + t_tr + t_scell
    else:
        raise ValueError(wake)
    if t_sleep_end < t_tr or t_sleep_end <= 0.0:
        raise ValueError(f'sleep of {t_sleep_end} s shorter than its transition time {t_tr} s')
    segs = []
    for a, b, kind in ((0.0, t_sleep_end, 'sleep'), (t_sleep_end, t_act_end, 'act'), (t_act_end, day, 'on')):
        for n, la, lb in levels:
            lo, hi = max(a, la), min(b, lb)
            if hi > lo:
                segs.append((kind, n, lo, hi))
    decisions = [(0.0, 'sleep'), (req, 'wake_request'), (t_sleep_end, 'awake_activate'), (t_act_end, 'serve')]
    gaps = [(names[first], t_b, t_act_end)] if (wake == 'reactive' and t_act_end > t_b) else []
    return segs, decisions, gaps


def ledger_72(segs, p_on, p_act, sleep_st):
    """Energy [J] of a 7.2 GHz cell (per-cell arrays): sum P D over on / act / hold / sleep segments plus one E_add per
    sleep period. p_on(level) -> power array; p_act array; sleep_st = {'p_w', 't_s', 'e_add_j'}."""
    e = 0.0
    n_sleep, in_sleep = 0, False
    for kind, lvl, a, b in segs:
        dt = b - a
        if kind == 'on':
            e = e + p_on(lvl) * dt
        elif kind == 'act':
            e = e + p_act * dt
        elif kind in ('sleep', 'hold'):
            e = e + sleep_st['p_w'] * dt
        if kind == 'sleep' and not in_sleep:
            n_sleep += 1
        in_sleep = kind == 'sleep'
    return e + n_sleep * sleep_st['e_add_j'], n_sleep


def sequence_check(segs, p_on_mean, p_act_mean, sleep_st):
    """Independent recomputation with P3.1's sequence_energy on the cell-mean powers (adjacent sleep segments merged
    into one sleep period; 'hold' is a non-transitioning state)."""
    seq, states, k = [], {}, 0
    for kind, lvl, a, b in segs:
        dt = b - a
        if kind == 'sleep' and seq and seq[-1][0].startswith('sleep'):
            seq[-1] = (seq[-1][0], seq[-1][1] + dt)
            continue
        name = f'{kind}{k}'
        k += 1
        if kind == 'on':
            states[name] = {'p_w': p_on_mean(lvl), 'sleep': False, 't_s': 0.0, 'e_add_j': 0.0}
        elif kind == 'act':
            states[name] = {'p_w': p_act_mean, 'sleep': False, 't_s': 0.0, 'e_add_j': 0.0}
        elif kind == 'hold':
            states[name] = {'p_w': sleep_st['p_w'], 'sleep': False, 't_s': 0.0, 'e_add_j': 0.0}
        else:
            name = f'sleep{k}'
            states[name] = {'p_w': sleep_st['p_w'], 'sleep': True, 't_s': sleep_st['t_s'], 'e_add_j': sleep_st['e_add_j']}
        seq.append((name, dt))
    return EM.sequence_energy(seq, states)


def toy_fixtures():
    """Gate E8: hand-computed answers on a two-level toy day."""
    lv = TOY['levels']
    pols = {'toy': {'on_levels': ['on'], 'ul': None, 'bound': False}}
    st = {'p_w': TOY['p_sleep'], 't_s': TOY['t_tr'], 'e_add_j': TOY['e_add']}
    out = {}
    segs, _, _ = schedule('toy', 'proactive', TOY['t_tr'], 0.0, lv, pols)
    e, _ = ledger_72(segs, lambda _l: TOY['p_on'], TOY['p_act'], st)
    out['no_activation_j'] = e
    segs, _, _ = schedule('toy', 'proactive', TOY['t_tr'], 1.0, lv, pols)
    e2, _ = ledger_72(segs, lambda _l: TOY['p_on'], TOY['p_act'], st)
    out['activation_1s_j'] = e2
    try:
        schedule('toy', 'proactive', 0.05, 0.0, (('off', 0.0, 0.01), ('on', 0.01, 10.0)), pols)
        out['short_sleep_rejected'] = False
    except ValueError:
        out['short_sleep_rejected'] = True
    _, _, gaps = schedule('toy', 'reactive', 0.05, 0.105, lv, pols)
    out['reactive_gap_s'] = gaps[0][2] - gaps[0][1] if gaps else None
    out['passed'] = bool(out['no_activation_j'] == TOY['expected_j'] and out['activation_1s_j'] == TOY['expected_act_j']
                         and out['short_sleep_rejected'] and gaps and abs(out['reactive_gap_s'] - (0.05 + 0.105)) <= 1e-9)
    return out


def causality_check():
    """Gate E6. The schedules are time-of-day rules: (a) schedule() takes no traffic input (its parameters are checked);
    (b) every decision list is reproduced exactly by a second derivation; (c) a reactive wake request is never issued
    before the level boundary at which the traffic rises. (The traffic-replacement test of an adaptive controller is
    P3.3's gate; here the decisions cannot see the traffic at all.)"""
    import inspect
    params = list(inspect.signature(schedule).parameters)
    no_traffic = params == ['policy', 'wake', 't_tr', 't_scell', 'levels', 'policies']
    bad, n = 0, 0
    for pol in POLICIES:
        for wake in WAKE:
            for t_tr in (0.05, 10.0):
                for t_sc in (t_scell_s(SMTC_MS['primary']), t_scell_s(SMTC_MS['sensitivity'])):
                    d1, d2 = schedule(pol, wake, t_tr, t_sc)[1], schedule(pol, wake, t_tr, t_sc)[1]
                    bad += int(d1 != d2)
                    n += 1
                    if wake == 'reactive' and POLICIES[pol]['on_levels'] and len(POLICIES[pol]['on_levels']) < len(LEVELS):
                        first = min(lv[1] for lv in LEVELS if lv[0] in POLICIES[pol]['on_levels'])
                        bad += sum(int(t < first) for t, act in d1 if act == 'wake_request')
    return {'schedule_parameters': params, 'no_traffic_input': no_traffic, 'derivations': n, 'violations': bad,
            'passed': bool(no_traffic and n > 0 and bad == 0)}


# ---------------------------------------------------------------- statistics
def ci95(v):
    v = np.asarray(v, dtype=float)
    R = len(v)
    mu = float(v.mean())
    if R < 2:
        return {'mean': mu, 'lo': float('nan'), 'hi': float('nan')}
    h = float(stats.t.ppf(0.975, R - 1)) * float(v.std(ddof=1)) / math.sqrt(R)
    return {'mean': mu, 'lo': mu - h, 'hi': mu + h}


def site_sum(cell_vals, R):
    return np.asarray(cell_vals, dtype=float).reshape(R, N_SECT).sum(axis=1)


# ---------------------------------------------------------------- main
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
    conf2b = json.loads(B.CONFIG.read_text(encoding='utf-8'))
    conf1 = json.loads(P31_CONFIG.read_text(encoding='utf-8'))
    conf14 = json.loads(M2.e155.E14_CONFIG.read_text(encoding='utf-8'))
    smoke = args.smoke_realizations is not None
    n_real = int(conf2b['settings']['realizations'])
    if smoke:
        if Path(args.outdir).resolve() == OUTDIR.resolve():
            raise SystemExit('smoke runs must not write to the measured-run directory')
        n_real = int(args.smoke_realizations)
    outdir = Path(args.outdir)
    (outdir / 'raw').mkdir(parents=True, exist_ok=True)
    validation = {}
    # ---- E0 provenance and constants
    inp = conf['inputs']
    man1 = json.loads((P31_DIR / 'manifest.json').read_text())
    man_a = json.loads((A.OUTDIR / 'manifest.json').read_text())
    man_b = json.loads((B.OUTDIR / 'manifest.json').read_text())
    prov = {
        'p3_1_manifest': sha256(P31_DIR / 'manifest.json') == inp['p3_1']['manifest_sha256'] and man1['all_checks_passed'],
        'p3_1_config': sha256(P31_CONFIG) == inp['p3_1']['config_sha256'] == man1['config_sha256'],
        'p3_1_outputs': all(sha256(P31_DIR / k) == v for k, v in man1['outputs_sha256'].items()),
        'p3_2a_manifest': sha256(A.OUTDIR / 'manifest.json') == inp['p3_2a']['manifest_sha256'] and man_a['all_checks_passed'],
        'p3_2a_config': sha256(A.CONFIG) == inp['p3_2a']['config_sha256'] == man_a['config_sha256'],
        'p3_2b_manifest': sha256(B.OUTDIR / 'manifest.json') == inp['p3_2b']['manifest_sha256'] and man_b['all_checks_passed'],
        'p3_2b_run_of_record': man_b['git_head_at_run'] == inp['p3_2b']['git_head'],
        'p3_2b_config': sha256(B.CONFIG) == inp['p3_2b']['config_sha256'] == man_b['config_sha256'],
        'p3_2b_outputs': all(sha256(B.OUTDIR / k) == v for k, v in man_b['outputs_sha256'].items()),
    }
    dy, wk = conf['day'], conf['wake']
    consts = {
        'levels': [(lv['name'], lv['start_s'], lv['end_s']) for lv in dy['levels']] == list(LEVELS),
        'growth': tuple(dy['growth_grid']) == GROWTH,
        'mixes': all(abs(dy['mixes'][k]['ul_dl'] - v) < 1e-15 and abs(B.RHO[k] - v) < 1e-15 for k, v in MIXES.items()),
        'epsilon': conf['service']['epsilon'] == EPS == B.EPS_OUT,
        'hardware': {float(k): v for k, v in conf['hardware']['map_dl_offset_to_p3_1_point'].items()} == HW_KEY
                    and conf['hardware']['primary_dl_offset'] == DP_PRIMARY and tuple(conf['hardware']['m_rf_energy_only']) == HW_MRF,
        'cases35': (conf['hardware']['layer_3p5']['case_primary'], conf['hardware']['layer_3p5']['case_sensitivity']) == CASES35,
        'sleep': tuple(conf['sleep']['categories']) == CATS and (conf['sleep']['set_primary'], conf['sleep']['set_sensitivity']) == SETS,
        'wake': tuple(wk['modes']) == WAKE and wk['scell_activation']['smtc_period_ms'] == SMTC_MS
                and all(wk['scell_activation']['t_scell_s'][k] == t_scell_s(v) for k, v in SMTC_MS.items()),
        'policies': conf['policies'] == POLICIES,
        'p3_2b_layer': B.SCOPE[ISD][CASE] == DPS and B.EPS_OUT == EPS,
    }
    validation['E0_provenance'] = {'inputs': prov, 'constants': consts, 'passed': bool(all(prov.values()) and all(consts.values()))}
    if not validation['E0_provenance']['passed']:
        (outdir / 'validation.json').write_text(json.dumps(validation, indent=2, default=float), encoding='utf-8')
        raise SystemExit('E0 failed')
    # ---- E1 power reproduction
    en = Energy(conf1)
    s1 = json.loads((P31_DIR / 'summary.json').read_text())
    validation['E1_power_reproduction'] = power_reproduction(en, conf1, s1)
    validation['E1_power_reproduction']['s1_primary_vs_hand_estimate_w'] = [validation['E1_power_reproduction']['s1_primary_w'], S1_EXPECTED_W]
    print('E0 PASS; E1', 'PASS' if validation['E1_power_reproduction']['passed'] else 'FAIL', flush=True)
    # ---- radio population
    U, ctx, cx = build_population(conf2b, conf14, n_real)
    R = n_real
    K = R * N_SECT
    s2b = json.loads((B.OUTDIR / 'summary.json').read_text())
    c2b = s2b['isd'][str(ISD)]['cases'][CASE]
    cal = {ell: B.calibrate(U, ctx, ell) for ell in B.ELLS}
    T50 = cal[0.5]['T_bps']
    x30 = cal[0.3]['T_bps'] / T50
    e3 = {'solves': 0, 'not_converged': 0, 'max_residual': 0.0, 'closure_max_rel': 0.0, 'occupancy_identity_max_abs': 0.0,
          'load_range_bad': 0, 'workload_mismatches': 0, 'workload_comparisons': 0, 'monotone_load_bad': 0, 'zero_traffic_bad': 0}

    def track(sol):
        e3['solves'] += 1
        e3['not_converged'] += int(not sol['converged'])
        e3['max_residual'] = max(e3['max_residual'], sol['res'])

    def make_check(st):
        def check(b_, tb, sols, fls):
            offered = np.zeros(len(U['w']))
            for fi, g in st['groups'].items():
                fl, sol = fls[fi], sols[fi]
                rel = np.abs(fl['served'] + fl['unserved'] - fl['offered']) / np.maximum(fl['offered'], 1e-300)
                e3['closure_max_rel'] = max(e3['closure_max_rel'], float(rel.max()) if rel.size else 0.0)
                on = g['cell_on']
                if on.any():
                    e3['occupancy_identity_max_abs'] = max(e3['occupancy_identity_max_abs'],
                                                           float(np.max(np.abs(fl['occ_from_served'][on] - sol['L'][b_][on]))))
                e3['load_range_bad'] += int(np.sum((sol['L'][b_] < 0.0) | (sol['L'][b_] > 1.0)))
                offered[g['idx']] = fl['offered']
            none = st['car'] < 0
            offered[none] = tb * U['w'][none]
            e3['workload_mismatches'] += int(not np.array_equal(offered, tb * U['w']))
            e3['workload_comparisons'] += 1
        return check

    def state(d, rule, dp=0.0):
        a72 = np.zeros(N_SECT) if rule == 'off' else np.ones(N_SECT)
        return B.build_state(U, ctx, d, rule, a72, dp if d == 'DL' else 0.0, False)

    # ---- E2 reproduction of P3.2b run 2 (grids, raw arrays, calibration, admitted x*)
    t_e2 = time.time()
    e2 = {'calibration_equal': all(cal[ell]['T_bps'] == c2b['calibration']['T_bps_per_site'][str(ell)] for ell in B.ELLS)
          and x30 == c2b['calibration']['x_medium_over_busy'],
          'raw_arrays_compared': 0, 'raw_mismatches': 0, 'x_star_compared': 0, 'x_star_mismatches': 0}
    grid = sorted(set(B.GRID_X) | {x30})
    ygrid = sorted({rv * x for rv in B.RHO.values() for x in grid})
    if not smoke:
        with np.load(B.OUTDIR / 'raw' / f'loads_isd{ISD}.npz') as z:
            raw2b = {k: z[k] for k in z.files if k.startswith(CASE + '|')}
    x_adm = {}
    for dp in DPS:
        for rule in B.DL_STATES:
            st = state('DL', rule, dp)
            ev = B.eval_state(U, ctx, st, np.array(grid) * T50, quantiles=False)
            for fi, s_ in ev['sols'].items():
                track(s_)
                if not smoke:
                    for f in ('L', 'rho'):
                        e2['raw_arrays_compared'] += 1
                        e2['raw_mismatches'] += int(not np.array_equal(s_[f], raw2b[f'{CASE}|DL|{dp}|{rule}|{fi}|{f}']))
            xs = B.bisect_traffic(U, ctx, st, T50)
            x_adm[f'DL|{dp}|{rule}'] = xs
            if not smoke:
                e2['x_star_compared'] += 1
                e2['x_star_mismatches'] += int(json.loads(json.dumps(xs, default=float)) != c2b['DL'][f'{dp}|{rule}']['x_star'])
    for rule in B.UL_STATES:
        st = state('UL', rule)
        ev = B.eval_state(U, ctx, st, np.array(ygrid) * T50, quantiles=False)
        for fi, s_ in ev['sols'].items():
            track(s_)
            if not smoke:
                for f in ('L', 'rho'):
                    e2['raw_arrays_compared'] += 1
                    e2['raw_mismatches'] += int(not np.array_equal(s_[f], raw2b[f'{CASE}|UL|{rule}|{fi}|{f}']))
        if rule != 'on_scell_dl':
            ys = B.bisect_traffic(U, ctx, st, T50, hi=B.Y_MAX)
            x_adm[f'UL|{rule}'] = ys
            if not smoke:
                e2['x_star_compared'] += 1
                e2['x_star_mismatches'] += int(json.loads(json.dumps(ys, default=float)) != c2b['UL'][rule]['y_star'])
    ev = st = None
    raw2b = None
    if smoke:
        e2['reference'] = 'smoke: no 2-realization reference exists; code path only'
        e2['passed'] = bool(math.isfinite(T50) and T50 > 0)
    else:
        e2['reference'] = 'P3.2b run 2 (results/phase3/p3_2b_traffic_resources), ISD 200, C_deployment'
        # 26 arrays: DL off (L, rho of 3.5 GHz) and on (both carriers) at three offsets = 18; UL off 2, on_ulca 4, on_scell_dl 2
        e2['passed'] = bool(e2['calibration_equal'] and e2['raw_arrays_compared'] == 26 and e2['raw_mismatches'] == 0
                            and e2['x_star_compared'] == 2 * len(DPS) + 2 and e2['x_star_mismatches'] == 0)
    validation['E2_p32b_reproduction'] = e2
    print(f'E2 {"PASS" if e2["passed"] else "FAIL"} ({time.time() - t_e2:.0f} s)', flush=True)
    # ---- growth levels of the day
    t_g = time.time()
    xs_dl = sorted({0.0} | {g for g in GROWTH} | {x30 * g for g in GROWTH})
    xi = {('low', g): xs_dl.index(0.0) for g in GROWTH}
    xi.update({('medium', g): xs_dl.index(x30 * g) for g in GROWTH})
    xi.update({('busy', g): xs_dl.index(g) for g in GROWTH})
    ys_ul = sorted({0.0} | {rv * x for rv in MIXES.values() for x in xs_dl})
    yi = {(mx, lv, g): ys_ul.index(MIXES[mx] * xs_dl[xi[(lv, g)]]) for mx in MIXES for lv in ('low', 'medium', 'busy') for g in GROWTH}
    lev = {}       # (dir, rule, dp) -> {'L0', 'L1', 'agg'}
    raw = {}
    for dp in DPS:
        for rule in B.DL_STATES:
            if rule == 'off' and dp != DPS[0]:
                lev[('DL', 'off', dp)] = lev[('DL', 'off', DPS[0])]
                continue
            st = state('DL', rule, dp)
            ev = B.eval_state(U, ctx, st, np.array(xs_dl) * T50, check=make_check(st), quantiles=False)
            for s_ in ev['sols'].values():
                track(s_)
                e3['monotone_load_bad'] += int(np.sum(np.diff(s_['L'], axis=0) < -1e-12))
                e3['zero_traffic_bad'] += int(np.any(s_['L'][xs_dl.index(0.0)] != 0.0))
            L0 = ev['sols'][0]['L']
            L1 = ev['sols'][1]['L'] if 1 in ev['sols'] else np.zeros_like(L0)
            lev[('DL', rule, dp)] = {'L0': L0, 'L1': L1, 'agg': ev['agg']}
            raw[f'DL|{dp}|{rule}|L0'], raw[f'DL|{dp}|{rule}|L1'] = L0, L1
    for rule in ('off', 'on_ulca'):
        st = state('UL', rule)
        ev = B.eval_state(U, ctx, st, np.array(ys_ul) * T50, check=make_check(st), quantiles=False)
        for s_ in ev['sols'].values():
            track(s_)
            e3['monotone_load_bad'] += int(np.sum(np.diff(s_['L'], axis=0) < -1e-12))
            e3['zero_traffic_bad'] += int(np.any(s_['L'][ys_ul.index(0.0)] != 0.0))
        L0 = ev['sols'][0]['L']
        L1 = ev['sols'][1]['L'] if 1 in ev['sols'] else np.zeros_like(L0)
        lev[('UL', rule, 0.0)] = {'L0': L0, 'L1': L1, 'agg': ev['agg']}
        raw[f'UL|{rule}|L0'], raw[f'UL|{rule}|L1'] = L0, L1
    lev[('UL', 'on_scell_dl', 0.0)] = lev[('UL', 'off', 0.0)]   # B7b identity (P3.2b gate)
    ev = st = None
    validation['E3_new_levels'] = dict(e3, passed=bool(e3['not_converged'] == 0 and e3['max_residual'] <= 1e-9
                                                       and e3['closure_max_rel'] <= 1e-12 and e3['occupancy_identity_max_abs'] <= 1e-9
                                                       and e3['load_range_bad'] == 0 and e3['workload_comparisons'] > 0
                                                       and e3['workload_mismatches'] == 0 and e3['monotone_load_bad'] == 0
                                                       and e3['zero_traffic_bad'] == 0))
    print(f'levels solved ({time.time() - t_g:.0f} s); E3 {"PASS" if validation["E3_new_levels"]["passed"] else "FAIL"}', flush=True)
    # ---- all-user x* (D21) and E4
    x_all = {}
    for dp in DPS:
        for rule in B.DL_STATES:
            if rule == 'off' and dp != DPS[0]:
                x_all[f'DL|{dp}|off'] = x_all[f'DL|{DPS[0]}|off']
                continue
            x_all[f'DL|{dp}|{rule}'] = bisect_all(U, ctx, state('DL', rule, dp), T50, B.X_MAX)
    for rule in ('off', 'on_ulca'):
        x_all[f'UL|{rule}'] = bisect_all(U, ctx, state('UL', rule), T50, B.Y_MAX)
    x_adm['UL|on_scell_dl'] = x_adm['UL|off']
    x_all['UL|on_scell_dl'] = x_all['UL|off']
    e4 = {'identity_max_abs': 0.0, 'monotone_all_bad': 0, 'all_above_admitted': 0}
    for key, rec in lev.items():
        prev = -1.0
        for a in rec['agg']:
            n_ = a['not_admitted_share']
            e4['identity_max_abs'] = max(e4['identity_max_abs'], abs(a['outage_all_users'] - (n_ + (1.0 - n_) * a['outage_admitted_pooled'])))
            e4['monotone_all_bad'] += int(a['outage_all_users'] < prev - 1e-12)
            prev = a['outage_all_users']
    for k in x_all:
        e4['all_above_admitted'] += int(x_all[k]['x_star'] > x_adm[k]['x_star'])
    validation['E4_all_user'] = dict(e4, passed=bool(e4['identity_max_abs'] <= 1e-12 and e4['monotone_all_bad'] == 0
                                                     and e4['all_above_admitted'] == 0))
    U = None
    # ---- per-level service and powers
    T_SC = {k: t_scell_s(v) for k, v in SMTC_MS.items()}

    def lvl_states(pol, lvl, dp):
        """(DL key, UL key) of the radio state serving a level: the policy's on-state where the 7.2 GHz cell is on."""
        p = POLICIES[pol]
        if lvl in p['on_levels']:
            return ('DL', 'on', dp), ('UL', p['ul'], 0.0)
        return ('DL', 'off', dp), ('UL', 'off', 0.0)

    def service(dl_key, ul_key, mx, lvl, g):
        a_d = lev[dl_key]['agg'][xi[(lvl, g)]]
        a_u = lev[ul_key]['agg'][yi[(mx, lvl, g)]]
        return {'all_user': a_d['outage_all_users'] <= EPS and a_u['outage_all_users'] <= EPS,
                'admitted': a_d['outage_admitted_pooled'] <= EPS and a_u['outage_admitted_pooled'] <= EPS,
                'dl_only_all_user': a_d['outage_all_users'] <= EPS}, a_d, a_u

    def xstar_both(dl_key, ul_key, mx, crit):
        tab = x_all if crit in ('all_user', 'dl_only_all_user') else x_adm
        xd = tab[f'DL|{dl_key[2]}|{dl_key[1]}']['x_star']
        if crit == 'dl_only_all_user':
            return xd
        xu = tab[f'UL|{ul_key[1]}']['x_star'] / MIXES[mx]
        return min(xd, xu)

    def g_star(pol, mx, dp, crit):
        vals = []
        for lvl in TRAFFIC_LEVELS:
            dk, uk = lvl_states(pol, lvl, dp)
            f = 1.0 if lvl == 'busy' else x30
            vals.append(xstar_both(dk, uk, mx, crit) / f)
        return min(vals)

    cache = {}

    def p35_cells(case35, dl_key, lvl, g):
        k = ('p35', case35, dl_key, lvl, g)
        if k not in cache:
            cache[k] = en.p35(case35, lev[dl_key]['L0'][xi[(lvl, g)]])
        return cache[k]

    def p72_cells(hw, dl_key, ul_key, mx, lvl, g):
        k = ('p72', hw, dl_key, ul_key, mx, lvl, g)
        if k not in cache:
            xd = lev[dl_key]['L1'][xi[(lvl, g)]]
            xu = lev[ul_key]['L1'][yi[(mx, lvl, g)]] if ul_key[1] == 'on_ulca' else np.zeros(K)
            cache[k] = en.p72(hw, xd, xu)
        return cache[k]

    e5 = {'scenarios': 0, 'coverage_max_abs_s': 0.0, 'sequence_max_rel': 0.0, 'short_sleep': 0, 'e_add_count_bad': 0,
          'service_72_outside_on': 0.0}
    rows = []

    def deep_state(hw, scen, cat, set_name, bound):
        lad = dict(en.ladder(hw, scen, cat, set_name)['deep'])
        if bound:     # optimistic bound: zero transition time and zero E_add (D24)
            lad['t_s'], lad['e_add_j'] = 0.0, 0.0
        return lad

    def evaluate(pol, mx, g, dp, hw, case35, scen, cat, set_name, wake, smtc):
        t_tr = en.tr['transition_time_s'][cat]['deep']
        t_sc = T_SC[smtc]
        segs, _, gaps = schedule(pol, wake, t_tr, t_sc)
        lad = deep_state(hw, scen, cat, set_name, POLICIES[pol]['bound'])
        aux = en.p_aux(hw, scen)
        p_act = en.p_empty(hw) + aux

        def p_on(lvl):
            dk, uk = lvl_states(pol, lvl, dp)
            return p72_cells(hw, dk, uk, mx, lvl, g) + aux
        e72, n_sl = ledger_72(segs, p_on, np.full(K, p_act), lad)
        e72 = np.full(K, float(e72)) if np.ndim(e72) == 0 else np.asarray(e72, dtype=float)
        # 3.5 GHz cell and delivered traffic, per segment: the policy's state where the 7.2 GHz cell is on, else the
        # 3.5-GHz-only state (sleep, activation and reactive-gap seconds alike)
        e35 = np.zeros(K)
        bits = {'DL': 0.0, 'UL': 0.0}
        served72_outside_on = 0.0
        for kind, lvl, a, b in segs:
            dt = b - a
            if kind == 'on':
                dk, uk = lvl_states(pol, lvl, dp)
            else:
                dk, uk = ('DL', 'off', dp), ('UL', 'off', 0.0)
            e35 = e35 + p35_cells(case35, dk, lvl, g) * dt
            a_d = lev[dk]['agg'][xi[(lvl, g)]]
            a_u = lev[uk]['agg'][yi[(mx, lvl, g)]]
            x_ = xs_dl[xi[(lvl, g)]]
            bits['DL'] += a_d['traffic_fractions']['served'] * x_ * T50 * dt
            bits['UL'] += a_u['traffic_fractions']['served'] * MIXES[mx] * x_ * T50 * dt
            if kind != 'on':
                served72_outside_on += a_d['traffic_fractions']['offered_on_7.2GHz'] + a_u['traffic_fractions']['offered_on_7.2GHz']
        # E5 checks
        cover = sum(b - a for _, _, a, b in segs)
        e5['scenarios'] += 1
        e5['coverage_max_abs_s'] = max(e5['coverage_max_abs_s'], abs(cover - DAY_S))
        ref = sequence_check(segs, lambda lv_: float(np.mean(p_on(lv_))), p_act, lad)
        e5['sequence_max_rel'] = max(e5['sequence_max_rel'], EM.rel_diff(float(np.mean(e72)), ref))
        sleeps = [s for s in segs if s[0] == 'sleep']
        if sleeps:
            d_sleep = sum(b - a for _, _, a, b in sleeps)
            e5['short_sleep'] += int(d_sleep < lad['t_s'])
        e5['e_add_count_bad'] += int(n_sl != (1 if sleeps else 0))
        e5['service_72_outside_on'] = max(e5['service_72_outside_on'], served72_outside_on)
        site = site_sum(e72 + e35, R)
        e_trans = n_sl * lad['e_add_j'] + sum((b - a) for k_, _, a, b in segs if k_ == 'act') * p_act
        e72_mean = float(np.mean(e72))
        feas = {c: True for c in CRITERIA}
        for lvl in TRAFFIC_LEVELS:
            dk, uk = lvl_states(pol, lvl, dp)
            ok, _, _ = service(dk, uk, mx, lvl, g)
            for c in CRITERIA:
                feas[c] = feas[c] and bool(ok[c])
        gap_s = sum(b - a for _, a, b in gaps)
        gap_viol = {}
        for c in CRITERIA:
            v = 0.0
            for lv_, a, b in gaps:
                ok, _, _ = service(('DL', 'off', dp), ('UL', 'off', 0.0), mx, lv_, g)
                v += 0.0 if ok[c] else (b - a)
            gap_viol[c] = v
        e_site = ci95(site)
        tot_bits = bits['DL'] + bits['UL']
        return {'policy': pol, 'mix': mx, 'g': g, 'dl_offset_db': dp, 'hw': hw, 'case35': case35, 'scenario': scen,
                'category': cat, 'set': set_name, 'wake': wake, 'smtc': smtc,
                'E_site_kWh': {k: v / 3.6e6 for k, v in e_site.items()},
                'E35_site_kWh': float(np.mean(site_sum(e35, R))) / 3.6e6, 'E72_site_kWh': float(np.mean(site_sum(e72, R))) / 3.6e6,
                'E_transition_site_J': N_SECT * e_trans, 'transition_share_of_72': (e_trans / e72_mean) if e72_mean > 0 else 0.0,
                'bits_delivered_site_Gbit': tot_bits / 1e9, 'J_per_Mbit': (e_site['mean'] / tot_bits * 1e6) if tot_bits > 0 else None,
                'feasible': feas, 'reactive_gap_s': gap_s, 'gap_violation_s': gap_viol, 'sleep_periods': n_sl}

    t_l = time.time()
    hw_primary = HW_KEY[DP_PRIMARY]
    for pol in POLICIES:
        for mx in MIXES:
            for g in GROWTH:
                for dp in DPS:
                    for scen in SCEN:
                        for cat in CATS:
                            for wake in WAKE:
                                rows.append(evaluate(pol, mx, g, dp, HW_KEY[dp], 'right', scen, cat, 'set1', wake, 'primary'))
                for cat in CATS:   # sensitivities at the primary hardware, S0
                    rows.append(evaluate(pol, mx, g, DP_PRIMARY, hw_primary, 'right', 'S0', cat, 'set3', 'proactive', 'primary'))
                    for wake in WAKE:
                        rows.append(evaluate(pol, mx, g, DP_PRIMARY, hw_primary, 'right', 'S0', cat, 'set1', wake, 'sensitivity'))
                rows.append(evaluate(pol, mx, g, DP_PRIMARY, hw_primary, 'left', 'S0', 'cat1', 'set1', 'proactive', 'primary'))
                for hw in HW_MRF:
                    rows.append(evaluate(pol, mx, g, DP_PRIMARY, hw, 'right', 'S0', 'cat1', 'set1', 'proactive', 'primary'))
    validation['E5_ledger'] = dict(e5, passed=bool(e5['scenarios'] > 0 and e5['coverage_max_abs_s'] <= 1e-9
                                                   and e5['sequence_max_rel'] <= 1e-12 and e5['short_sleep'] == 0
                                                   and e5['e_add_count_bad'] == 0 and e5['service_72_outside_on'] == 0.0))
    print(f'ledger: {len(rows)} scenarios ({time.time() - t_l:.0f} s); E5 {"PASS" if validation["E5_ledger"]["passed"] else "FAIL"}', flush=True)
    validation['E6_causality'] = causality_check()

    # ---- E7 overhead consistency
    def key_of(r_):
        return (r_['policy'], r_['mix'], r_['g'], r_['dl_offset_db'], r_['hw'], r_['case35'], r_['category'], r_['set'], r_['wake'], r_['smtc'])
    by = {}
    for r_ in rows:
        by.setdefault(key_of(r_), {})[r_['scenario']] = r_
    e7 = {'compared': 0, 'order_bad': 0, 'increment_max_rel': 0.0}
    for k, sc in by.items():
        if len(sc) < 3:
            continue
        pol, mx, g, dp, hw, case35, cat, set_name, wake, smtc = k
        e7['compared'] += 1
        e0_, e1_, e2_ = (sc[s]['E_site_kWh']['mean'] for s in SCEN)
        e7['order_bad'] += int(not (e0_ < e1_ < e2_))
        segs, _, _ = schedule(pol, wake, en.tr['transition_time_s'][cat]['deep'], T_SC[smtc])
        bnd = POLICIES[pol]['bound']
        l0, l1 = deep_state(hw, 'S0', cat, set_name, bnd), deep_state(hw, 'S1', cat, set_name, bnd)
        aux = en.p_aux(hw, 'S1')
        inc = 0.0
        for kind, _, a, b in segs:
            inc += (aux if kind in ('on', 'act') else (l1['p_w'] - l0['p_w'])) * (b - a)
        inc += (l1['e_add_j'] - l0['e_add_j']) * (1 if any(s[0] == 'sleep' for s in segs) else 0)
        e7['increment_max_rel'] = max(e7['increment_max_rel'], EM.rel_diff((e1_ - e0_) * 3.6e6, N_SECT * inc))
    validation['E7_overhead'] = dict(e7, passed=bool(e7['compared'] > 0 and e7['order_bad'] == 0 and e7['increment_max_rel'] <= 1e-9))
    validation['E8_toy_fixtures'] = toy_fixtures()
    for k in ('E4_all_user', 'E6_causality', 'E7_overhead', 'E8_toy_fixtures'):
        print(k, 'PASS' if validation[k]['passed'] else 'FAIL', flush=True)
    gates_ok = all(v['passed'] for v in validation.values() if isinstance(v, dict)) and not smoke
    validation['smoke_run'] = smoke
    validation['all_checks_passed'] = gates_ok

    # ---- summary
    def prim(r_):
        return (r_['dl_offset_db'] == DP_PRIMARY and r_['hw'] == hw_primary and r_['case35'] == 'right' and r_['category'] == 'cat1'
                and r_['set'] == 'set1' and r_['wake'] == 'proactive' and r_['smtc'] == 'primary')
    hw_rows = [r_ for r_ in rows if r_['hw'] == HW_KEY[r_['dl_offset_db']] and r_['case35'] == 'right' and r_['category'] == 'cat1'
               and r_['set'] == 'set1' and r_['wake'] == 'proactive' and r_['smtc'] == 'primary']
    best = {}
    for r_ in hw_rows:
        if POLICIES[r_['policy']]['bound']:
            continue
        for c in CRITERIA:
            k = f"{r_['mix']}|{r_['g']}|{r_['dl_offset_db']}|{r_['scenario']}|{c}"
            if r_['feasible'][c] and (k not in best or r_['E_site_kWh']['mean'] < best[k]['E_site_kWh']):
                best[k] = {'policy': r_['policy'], 'E_site_kWh': r_['E_site_kWh']['mean']}
    for k, v in best.items():
        mx, g, dp, scen, c = k.split('|')
        aon = [r_ for r_ in hw_rows if r_['policy'] == 'aon_ulsel' and r_['mix'] == mx and r_['g'] == float(g)
               and r_['dl_offset_db'] == float(dp) and r_['scenario'] == scen][0]
        v['saving_vs_aon_ulsel'] = 1.0 - v['E_site_kWh'] / aon['E_site_kWh']['mean']
        v['aon_ulsel_feasible'] = aon['feasible'][c]
    sens = {}
    pidx = {(r_['policy'], r_['mix'], r_['g']): r_ for r_ in rows if prim(r_) and r_['scenario'] == 'S0'}
    for r_ in rows:
        if r_['scenario'] != 'S0' or prim(r_) or r_['dl_offset_db'] != DP_PRIMARY:
            continue
        lab = (f"hw={r_['hw']}|case35={r_['case35']}|{r_['category']}|{r_['set']}|{r_['wake']}|smtc={r_['smtc']}")
        p_ = pidx[(r_['policy'], r_['mix'], r_['g'])]
        d = r_['E_site_kWh']['mean'] / p_['E_site_kWh']['mean'] - 1.0
        s_ = sens.setdefault(lab, {'max_abs_rel_change_E_site': 0.0, 'rows': 0})
        s_['max_abs_rel_change_E_site'] = max(s_['max_abs_rel_change_E_site'], abs(d))
        s_['rows'] += 1
    gst = {f'{pol}|{mx}|{dp}|{c}': g_star(pol, mx, dp, c) for pol in POLICIES for mx in MIXES for dp in DPS for c in CRITERIA}
    levels_out = {}
    for (d, rule, dp), rec in lev.items():
        if d == 'DL' and rule == 'off' and dp != DPS[0]:
            continue
        tv = xs_dl if d == 'DL' else ys_ul
        levels_out[f'{d}|{rule}|{dp}'] = [dict({kk: vv for kk, vv in a.items() if kk != 'traffic_bps'}, x=t_) for a, t_ in zip(rec['agg'], tv)]

    # predictions
    def busy_site_power(pol, scen):
        dk, uk = lvl_states(pol, 'busy', DP_PRIMARY)
        p35 = p35_cells('right', dk, 'busy', 1.0)
        aux = en.p_aux(hw_primary, scen)
        if 'busy' in POLICIES[pol]['on_levels']:
            p72 = p72_cells(hw_primary, dk, uk, MIX_PRIMARY, 'busy', 1.0) + aux
        else:
            p72 = np.full(K, en.ladder(hw_primary, scen, 'cat1', 'set1')['deep']['p_w'])
        return float(np.mean(site_sum(p35 + p72, R)))
    r1 = busy_site_power('aon_ulsel', 'S0') / busy_site_power('35only', 'S0')
    r2 = busy_site_power('aon_ulsel', 'S2') / busy_site_power('35only', 'S2')
    sav = {'S0': [], 'S2': []}
    for scen in ('S0', 'S2'):
        for mx in MIXES:
            for g in GROWTH:
                a_ = [r_ for r_ in rows if prim(r_) and r_['scenario'] == scen and r_['mix'] == mx and r_['g'] == g]
                aon = [r_ for r_ in a_ if r_['policy'] == 'aon_ulsel'][0]
                sch = [r_ for r_ in a_ if r_['policy'] == 'sch_medbusy_ulsel'][0]
                if aon['feasible']['all_user'] and sch['feasible']['all_user']:
                    sav[scen].append({'mix': mx, 'g': g, 'saving': 1.0 - sch['E_site_kWh']['mean'] / aon['E_site_kWh']['mean']})
    p3_ok = (len(sav['S0']) > 0 and all(0.03 <= v['saving'] <= 0.08 for v in sav['S0'])
             and len(sav['S2']) > 0 and all(v['saving'] >= 0.10 for v in sav['S2']))
    trans_max = max(r_['transition_share_of_72'] for r_ in rows if not POLICIES[r_['policy']]['bound'] and r_['set'] == 'set1')
    summary = {
        'n_realizations': R, 'isd': ISD, 'case': CASE, 'labels': conf['policy_labels'],
        'calibration': {'T_bps_per_site': {str(ell): v['T_bps'] for ell, v in cal.items()}, 'x_medium_over_busy': x30},
        'constants': {
            'hardware': {hw: {'P_empty_w': en.p_empty(hw), 'P_mu_w': en.p_mu(hw),
                              'P_aux_w': {s: en.p_aux(hw, s) for s in SCEN},
                              'deep_sleep': {f'{s}|{c}|{st_}': en.ladder(hw, s, c, st_)['deep'] for s in SCEN for c in CATS for st_ in SETS}}
                         for hw in list(HW_KEY.values()) + list(HW_MRF)},
            'P35_w_at_load0': {c: en.p35(c, 0.0) for c in CASES35},
            't_scell_s': T_SC, 'tr_deep_transition_s': {c: en.tr['transition_time_s'][c]['deep'] for c in CATS},
            'traffic_dl_x': xs_dl, 'traffic_ul_y': ys_ul},
        'x_star': {'admitted': x_adm, 'all_user': x_all},
        'g_star': gst,
        'levels': levels_out,
        'energy_rows_hw_primary_settings': hw_rows,
        'best_feasible_policy': best,
        'sensitivity_vs_primary_S0': sens,
        'predictions': {
            'E_P1_offload_near_neutral_S0': {'ratio': r1, 'held': bool(0.90 <= r1 <= 1.10)},
            'E_P2_overhead_decides': {'ratio_S2': r2, 'held': bool(r2 >= 1.25)},
            'E_P3_low_period_sleep': {'savings': sav, 'held': bool(p3_ok)},
            'basis': 'ISD 200, C, 50 dBm, M_RF 256, 3.5 GHz right, cat1, set1, proactive wake, SMTC 20 ms'},
        'expectations_not_scored': {
            'transitions_max_share_of_72': trans_max, 'transitions_negligible': bool(trans_max < 1e-3),
            'dl_all_user_equals_admitted': all(x_all[k]['x_star'] == x_adm[k]['x_star'] for k in x_all if k.startswith('DL')),
            'ai_era_today_infeasible': {p: not [r_ for r_ in rows if prim(r_) and r_['scenario'] == 'S0' and r_['mix'] == 'genai_2025'
                                                and r_['g'] == 1.0 and r_['policy'] == p][0]['feasible']['all_user']
                                        for p in ('35only', 'aon_dlonly', 'sch_busy_dlonly', 'sch_medbusy_dlonly')}},
        'decision': conf['decision_rules']['all_gates_pass' if gates_ok else 'gate_fails'],
    }
    summary['wall_time_s'] = time.time() - t0
    np.savez_compressed(outdir / 'raw' / f'levels_isd{ISD}.npz', **raw)
    with gzip.open(outdir / 'raw' / 'energy_table.json.gz', 'wt', encoding='utf-8') as fh:
        json.dump(rows, fh, default=float)
    (outdir / 'summary.json').write_text(json.dumps(summary, indent=1, default=float), encoding='utf-8')
    (outdir / 'validation.json').write_text(json.dumps(validation, indent=2, default=float), encoding='utf-8')
    manifest = {'generated_utc': datetime.now(timezone.utc).isoformat(), 'tool': 'scripts/p3_2c_energy_ledger.py',
                'git_head_at_run': args.git_head, 'python': platform.python_version(), 'numpy': np.__version__, 'scipy': scipy.__version__,
                'config_sha256': sha256(args.config),
                'input_sha256': {'p3_1_energy_models/manifest.json': sha256(P31_DIR / 'manifest.json'),
                                 'p3_2a_coupled_feasibility/manifest.json': sha256(A.OUTDIR / 'manifest.json'),
                                 'p3_2b_traffic_resources/manifest.json': sha256(B.OUTDIR / 'manifest.json')},
                'outputs_sha256': {'summary.json': sha256(outdir / 'summary.json'),
                                   f'raw/levels_isd{ISD}.npz': sha256(outdir / 'raw' / f'levels_isd{ISD}.npz'),
                                   'raw/energy_table.json.gz': sha256(outdir / 'raw' / 'energy_table.json.gz')},
                'all_checks_passed': gates_ok}
    (outdir / 'manifest.json').write_text(json.dumps(manifest, indent=2), encoding='utf-8')
    for k, v in validation.items():
        if isinstance(v, dict):
            print(k, 'PASS' if v['passed'] else 'FAIL', flush=True)
    if not smoke:
        print('predictions', json.dumps(summary['predictions'], default=float))
    print('P3.2c', 'GATES PASSED' if gates_ok else 'GATES FAILED', f'{time.time() - t0:.0f} s')
    sys.exit(0 if gates_ok else 1)


if __name__ == '__main__':
    main()
