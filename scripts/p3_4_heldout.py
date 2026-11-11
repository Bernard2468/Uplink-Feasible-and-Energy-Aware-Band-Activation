"""P3.4: held-out test of the frozen P3.3 controllers (P3.0 revision 5, D33; pre-registered in configs/p3/p3_4_heldout.json).

Frozen inputs (P3.3 run 2, results of record): the tuned parameters (controller | mix | S), the seasonal-persistence
forecast and its quantiles, the development x* thresholds, the maximum-capacity fallbacks, the development radio and
energy tables (the controllers' internal model), the development T_0.5 and the test squares' a_ref.
Held-out data, read here for the first time: the test week (18-24 November 2013) of the 25 test squares, and radio
realizations 100-199 of the same seed and streams. The controllers decide with the frozen development model; every
outcome is scored on the test radio table. Nothing is tuned.
Scope: ISD 200, case C, the primary 50 dBm radio (DL offset -3 dB) and the 14 energy scenarios that use it.
Gates G0-G10; predictions H1, H2, H4, Q3 (config). Outputs in --outdir: summary.json, validation.json, manifest.json;
raw/test_table_isd200.npz, raw/runs.json.gz (git-ignored).
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
from pathlib import Path

import numpy as np
import scipy

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'scripts'))
import p3_3_controllers as P  # noqa: E402
import p3_3_traces as TR  # noqa: E402

C, B, EM = P.C, P.B, P.EM
CONFIG = ROOT / 'configs/p3/p3_4_heldout.json'
OUTDIR = ROOT / 'results/phase3/p3_4_heldout'
P33_DIR = P.OUTDIR
R_START, R_STOP = 100, 200
DP = -3.0
DPI = P.DPS.index(DP)
SCENARIOS = [s for s in P.SCENARIOS if s['dp'] == DP]                         # 14 scenarios on the primary radio
PRIMARY_S = ('primary_S0', 'primary_S1', 'primary_S2')
CONTROLLERS = P.CONTROLLERS + ('oracle_budget', 'oracle_test')
EXTRA_ON = PRIMARY_S                                                            # oracle_budget, oracle_test: primary scenarios only
BOOT_REPS, BOOT_SEED = 2000, 20261112
BUDGET = P.BUDGET
LAMBDA_LOG10 = (-3.0, 9.0)
LAMBDA_ITERS = 50
H1_CELLS = [(mx, g, s) for mx in P.MIX_KEYS for g in (1.0, 2.0) for s in ('S0', 'S2')]
H1_MIN_SAVING = {'S0': 0.03, 'S2': 0.10}
H2_CELLS = [('genai_2025', g, s) for g in (1.0, 1.5, 2.0) for s in ('S0', 'S1', 'S2')]
H2_MIN_DIFF = 0.01
Q3_MAX_GAP = {'S0': 0.01, 'S2': 0.05}


def sha256(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


# ---------------------------------------------------------------- held-out radio realizations (gate G1)
def build_population_range(conf2b, conf14, r0, n, log=None):
    """The P3.2b population of realizations r0 .. r0 + n - 1: p3_2c.build_population with the realization index of every
    random stream offset by r0 (the builder reaches the realization index only through M2.rng_for). Every call is
    logged for gate G1."""
    M2 = B.M2
    orig = M2.rng_for

    def shifted(seed, isd, r, stream):
        if log is not None:
            log.append((r + r0, stream))
        return orig(seed, isd, r + r0, stream)
    M2.rng_for = shifted
    try:
        return C.build_population(conf2b, conf14, n)
    finally:
        M2.rng_for = orig


# ---------------------------------------------------------------- decision model vs evaluation model
def simulate_split(ctrl, xi, dec, ev, per_real=True):
    """P3.3 simulate() with the controller deciding on dec (the frozen development model) and every outcome scored on
    ev (the test radio and energy tables). With dec is ev it performs the same operations as P.simulate (gate G3)."""
    n, T = xi.shape
    ctrl.reset(n, dec, xi if ctrl.is_oracle else None)
    kp = np.full(n, P.FULL, dtype=np.int64)
    R = ev.Pw.shape[2]
    E = np.zeros((n, R)) if per_real else np.zeros(n)
    out = {'viol_s': {c: np.zeros(n) for c in P.CRITS}, 'unavoid_s': {c: np.zeros(n) for c in P.CRITS},
           'bits': np.zeros(n), 'n_wake': np.zeros(n), 'n_sleep': np.zeros(n), 'gap_s': np.zeros(n)}
    ks = np.zeros((n, T), dtype=np.int8)
    rows = np.arange(n)
    for t in range(T):
        k = np.asarray(ctrl.decide(t, xi[:, :t]), dtype=np.int64)
        i = xi[:, t]
        nw, ns = P.NW[kp, k], P.NS[kp, k]
        up = nw > 0
        gap = np.where(up, ev.tw, 0.0)
        if per_real:
            Pk, Pp = ev.Pw[k, i, :], ev.Pw[kp, i, :]
            E += (Pk * (P.SLOT_S - gap)[:, None] + np.where(up[:, None], Pp * ev.tw + (nw * (ev.p_act - ev.p_deep) * ev.t_sc)[:, None], 0.0)
                  + (ns * ev.e_add)[:, None])
        else:
            E += ev.slot_energy_mean(k, kp, i)
        for c in P.CRITS:
            av = ev.anyc[c][i]
            fk, fp = ~ev.comp[c][k, i], ~ev.comp[c][kp, i]
            out['viol_s'][c] += np.where(av, (P.SLOT_S - gap) * fk + gap * fp, 0.0)
            out['unavoid_s'][c] += np.where(av, 0.0, P.SLOT_S)
        x = ev.xg[i]
        out['bits'] += (ev.sDL[k, i] * (P.SLOT_S - gap) + ev.sDL[kp, i] * gap) * x * ev.T50
        out['bits'] += (ev.sUL[k, i] * (P.SLOT_S - gap) + ev.sUL[kp, i] * gap) * ev.rho * x * ev.T50
        out['n_wake'] += nw
        out['n_sleep'] += ns
        out['gap_s'] += gap
        ks[rows, t] = k
        kp = k
    out['E_J'] = E
    out['k'] = ks
    return out


class OracleBudget:
    """Perfect-foresight benchmark with the online controllers' allowance (review point D): minimum energy subject to an
    avoidable all-user violation of at most BUDGET of the run's time, per run, on the decision model. Lagrangian
    relaxation: for each lambda an exact DP of energy + lambda x violation seconds (slots and wake gaps; the
    maximum-capacity pattern where no pattern complies); bisection on log10(lambda). The best plan within the budget is
    an upper bound on the constrained optimum; max over the evaluated lambda of (DP value - lambda x budget) is a lower
    bound (weak duality)."""
    is_oracle = True

    def __init__(self, share=BUDGET, iters=LAMBDA_ITERS):
        self.share, self.iters = share, iters

    def reset(self, n, model, full):
        full = np.asarray(full)
        T = full.shape[1]
        budget = self.share * T * P.SLOT_S
        i = full.T
        kk = np.arange(P.NP)
        k = kk.reshape(1, 1, 1, P.NP)
        kp = kk.reshape(1, 1, P.NP, 1)
        ii = i[..., None, None]
        Ecost = model.slot_energy_mean(k, kp, ii)
        up = P.NW[kp, k] > 0
        gap = np.where(up, model.tw, 0.0)
        comp = model.comp['all_user']
        av = model.anyc['all_user'][i][..., None, None]
        V = np.where(av, (P.SLOT_S - gap) * ~comp[k, ii] + gap * ~comp[kp, ii], 0.0)
        allowed = np.where(model.anyc['all_user'][i][..., None], True, kk == model.maxcap['all_user'])
        rows = np.arange(n)

        def solve(lam):
            ks, val = P.dp_plan(Ecost + lam[None, :, None, None] * V, allowed, P.FULL)
            prev = np.concatenate([np.full((n, 1), P.FULL), ks[:, :-1]], axis=1)
            tt = np.arange(T)[None, :]
            e_tot = Ecost[tt, rows[:, None], prev, ks].sum(axis=1)
            v_tot = V[tt, rows[:, None], prev, ks].sum(axis=1)
            return ks, val, e_tot, v_tot
        lo, hi = np.full(n, LAMBDA_LOG10[0]), np.full(n, LAMBDA_LOG10[1])
        best_e = np.full(n, np.inf)
        best_plan = np.zeros((n, T), dtype=np.int64)
        lb = np.full(n, -np.inf)
        ks, val, e_tot, v_tot = solve(10.0 ** hi)
        feas_hi = v_tot <= budget
        upd = feas_hi & (e_tot < best_e)
        best_e = np.where(upd, e_tot, best_e)
        best_plan[upd] = ks[upd]
        lb = np.maximum(lb, val - 10.0 ** hi * budget)
        for _ in range(self.iters):
            mid = 0.5 * (lo + hi)
            ks, val, e_tot, v_tot = solve(10.0 ** mid)
            lb = np.maximum(lb, val - 10.0 ** mid * budget)
            feas = v_tot <= budget
            upd = feas & (e_tot < best_e)
            best_e = np.where(upd, e_tot, best_e)
            best_plan[upd] = ks[upd]
            hi = np.where(feas, mid, hi)
            lo = np.where(feas, lo, mid)
        # runs where even the largest lambda exceeds the budget keep the zero-violation oracle's lexicographic plan
        fallback = ~np.isfinite(best_e)
        if fallback.any():
            o = P.Oracle('all_user')
            o.reset(n, model, full)
            best_plan[fallback] = o.plan[fallback]
        self.plan = best_plan
        self.ub_energy, self.lb_energy, self.budget_infeasible = best_e, lb, fallback

    def decide(self, t, past):
        return self.plan[:, t]


def make_controller(name, params, S, qtab):
    if name == 'oracle_budget':
        return OracleBudget()
    if name == 'oracle_test':
        return P.Oracle('all_user')
    return P.make_controller(name, params, S, qtab)


# ---------------------------------------------------------------- paired statistics (review: squares and realizations crossed)
def pigeonhole_ratio(num, den, rng, reps=BOOT_REPS):
    """95 % pigeonhole-bootstrap interval of 1 - mean(num) / mean(den) for (squares, realizations) matrices; squares and
    realizations resampled independently (crossed design)."""
    S, R = num.shape
    ws = rng.multinomial(S, np.full(S, 1.0 / S), size=reps)
    wr = rng.multinomial(R, np.full(R, 1.0 / R), size=reps)
    a = np.einsum('bs,sr,br->b', ws, num, wr)
    b = np.einsum('bs,sr,br->b', ws, den, wr)
    v = 1.0 - a / b
    return {'point': float(1.0 - num.mean() / den.mean()), 'lo': float(np.percentile(v, 2.5)), 'hi': float(np.percentile(v, 97.5))}


def pigeonhole_gap(e1, e2, ea, rng, reps=BOOT_REPS):
    """(mean e1 - mean e2) / mean ea with its 95 % pigeonhole-bootstrap interval."""
    S, R = e1.shape
    ws = rng.multinomial(S, np.full(S, 1.0 / S), size=reps)
    wr = rng.multinomial(R, np.full(R, 1.0 / R), size=reps)
    d = np.einsum('bs,sr,br->b', ws, e1 - e2, wr) / np.einsum('bs,sr,br->b', ws, ea, wr)
    return {'point': float((e1.mean() - e2.mean()) / ea.mean()), 'lo': float(np.percentile(d, 2.5)), 'hi': float(np.percentile(d, 97.5))}


def square_bootstrap_diff(v1, v2, secs, rng, reps=BOOT_REPS):
    """Difference of pooled violation shares (v1 - v2) / secs over squares, 95 % square-bootstrap interval."""
    S = len(v1)
    ws = rng.multinomial(S, np.full(S, 1.0 / S), size=reps)
    d = ws @ (v1 - v2) / (ws @ secs)
    return {'point': float((v1 - v2).sum() / secs.sum()), 'lo': float(np.percentile(d, 2.5)), 'hi': float(np.percentile(d, 97.5))}


# ---------------------------------------------------------------- tables
def model_tables(oDLa, oDLm, sDL, oULa, oULm, sUL, xg, T50, thr_both, thr_dl, maxcap):
    """The table dict P.Model expects (one DL offset at index 0)."""
    od_a, od_m = oDLa[:, None, :, :], oDLm[:, None, :, :]
    ou_a, ou_m = oULa[:, :, None, :], oULm[:, :, None, :]
    nM, nD, G = oULa.shape[1], oDLa.shape[1], oDLa.shape[2]
    comp = {'all_user': (od_a <= P.EPS) & (ou_a <= P.EPS), 'admitted': (od_m <= P.EPS) & (ou_m <= P.EPS),
            'dl_only_all_user': np.broadcast_to(od_a <= P.EPS, (P.NP, nM, nD, G)).copy()}
    return {'xg': xg, 'T50': T50, 'comp': comp, 'anyc': {c: v.any(axis=0) for c, v in comp.items()}, 'maxcap': maxcap,
            'sDL': sDL, 'sUL': sUL, 'thr_both': thr_both, 'thr_dl': thr_dl}


def energy_scenario(en_mod, sc, Pw):
    hw = sc['hw']
    aux = en_mod.p_aux(hw, sc['S'])
    deep = en_mod.ladder(hw, sc['S'], sc['cat'], sc['set'])['deep']
    return {'Pw': Pw, 'p_act': en_mod.p_empty(hw) + aux, 'p_deep': deep['p_w'], 'e_add': deep['e_add_j'],
            't_tr': en_mod.tr['transition_time_s'][sc['cat']]['deep'], 't_sc': P.T_SCELL}


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
    n_real = R_STOP - R_START
    if smoke:
        if Path(args.outdir).resolve() == OUTDIR.resolve():
            raise SystemExit('smoke runs must not write to the measured-run directory')
        n_real = int(args.smoke_realizations)
    outdir = Path(args.outdir)
    (outdir / 'raw').mkdir(parents=True, exist_ok=True)
    validation = {}
    rng_boot = np.random.default_rng(BOOT_SEED)
    # ---- G0 provenance, frozen inputs, constants
    inp = conf['inputs']
    man33 = json.loads((P33_DIR / 'manifest.json').read_text())
    s33 = json.loads((P33_DIR / 'summary.json').read_text())
    v33 = json.loads((P33_DIR / 'validation.json').read_text())
    frozen = s33['frozen_for_p3_4']
    prov = {'p3_3_manifest': sha256(P33_DIR / 'manifest.json') == inp['p3_3']['manifest_sha256'] and bool(man33['all_checks_passed'])
            and man33['git_head_at_run'] == inp['p3_3']['git_head'],
            'p3_3_outputs': all(sha256(P33_DIR / k) == v for k, v in man33['outputs_sha256'].items()),
            'p3_3_config': sha256(P.CONFIG) == inp['p3_3']['config_sha256'] == man33['config_sha256'],
            'frozen_params_equal_config': conf['frozen']['params'] == frozen['params'],
            'frozen_test_squares_equal_config': conf['frozen']['test_squares'] == frozen['test_squares'],
            'trace_files_sha256': all(TR.sha256(TR.DATA / f) == h for f, h in conf['traces']['files'].items())}
    consts = {'realizations': (conf['radio']['r_start'], conf['radio']['r_stop']) == (R_START, R_STOP),
              'dl_offset': conf['radio']['dl_offset_db'] == DP,
              'scenarios': [s['name'] for s in SCENARIOS] == conf['scenarios'],
              'controllers': tuple(conf['controllers']) == CONTROLLERS,
              'bootstrap': (conf['statistics']['reps'], conf['statistics']['seed']) == (BOOT_REPS, BOOT_SEED),
              'lambda': (tuple(conf['oracle_budget']['log10_lambda_range']), conf['oracle_budget']['iterations']) == (LAMBDA_LOG10, LAMBDA_ITERS),
              'predictions': (conf['predictions']['H1']['min_saving'] == H1_MIN_SAVING and conf['predictions']['H2']['min_difference'] == H2_MIN_DIFF
                              and conf['predictions']['Q3']['max_gap'] == Q3_MAX_GAP),
              'dates': tuple(conf['traces']['test_dates']) == TR.TEST_DATES}
    validation['G0_provenance'] = {'inputs': prov, 'constants': consts, 'passed': bool(all(prov.values()) and all(consts.values()))}
    if not validation['G0_provenance']['passed']:
        (outdir / 'validation.json').write_text(json.dumps(validation, indent=2, default=float), encoding='utf-8')
        raise SystemExit('G0 failed')
    print('G0 PASS', flush=True)
    # ---- frozen development model
    z33 = dict(np.load(P33_DIR / 'raw' / 'table_isd200.npz'))
    xg = z33['xg']
    T50 = float(frozen['T50_bps'])
    thr_both = np.asarray(frozen['thresholds_x_star_dev']['both'])[:, :, DPI:DPI + 1]
    thr_dl = np.asarray(frozen['thresholds_x_star_dev']['dl'])[:, DPI:DPI + 1]
    maxcap = {c: np.asarray(v)[:, DPI:DPI + 1] for c, v in frozen['fallback_pattern_index'].items()}
    dev_tab = model_tables(z33['oDLa'][:, DPI:DPI + 1], z33['oDLm'][:, DPI:DPI + 1], z33['sDL'][:, DPI:DPI + 1], z33['oULa'], z33['oULm'],
                           z33['sUL'], xg, T50, thr_both, thr_dl, maxcap)
    en_mod = C.Energy(conf1)
    dev_energy = {sc['name']: energy_scenario(en_mod, sc, z33[f"Pw_{sc['name']}"]) for sc in SCENARIOS}
    S_prof = np.asarray(frozen['seasonal_profile'])
    qtab = {float(a): q for a, q in frozen['forecast_log_error_quantiles'].items()}
    z33 = None

    def ctrl_for(name, mx, S_):
        key = f'{name}|{mx}|{S_}'
        return make_controller(name, frozen['params'].get(key, {}), S_prof, qtab)

    def dev_model(mx, scn):
        return P.Model(dev_tab, P.MIX_KEYS.index(mx), 0, dev_energy[scn])
    # ---- G3 frozen reproduction: the development traces and P3.3's development results, exactly
    t_g3 = time.time()
    act, pres, _ = TR.read_week(TR.DEV_DATES)
    dev_sq = np.asarray(s33['traces']['dev_squares'])
    aref = {int(k): float(v) for k, v in s33['traces']['a_ref'].items()}
    norm_dev = np.stack([act[s - 1] / aref[int(s)] for s in dev_sq])
    act = pres = None
    xi_dev = np.concatenate([TR.quantize(g * norm_dev)[0] for g in P.GROWTH], axis=0)
    run_g_dev = np.repeat(np.array(P.GROWTH), len(dev_sq))
    g3 = {'dev_trace_sha256_equal': hashlib.sha256(xi_dev.tobytes()).hexdigest() == v33['F9_fairness']['trace_sha256'],
          'compared': 0, 'energy_max_rel': 0.0, 'violation_max_abs': 0.0}
    for name in P.CONTROLLERS:
        for mx, scn in (('genai_2025', 'primary_S0'), ('ericsson_typical', 'primary_S2')):
            md = dev_model(mx, scn)
            o = simulate_split(ctrl_for(name, mx, scn.split('_')[1]), xi_dev, md, md, per_real=True)
            for g in P.GROWTH:
                sel = run_g_dev == g
                ref = s33['results_dev'][f'{name}|{mx}|{g}|{scn}']
                e_mine = float((o['E_J'][sel] / 7 / 3.6e6).mean(axis=1).mean())
                v_mine = float(o['viol_s']['all_user'][sel].sum() / (sel.sum() * xi_dev.shape[1] * P.SLOT_S))
                g3['compared'] += 1
                g3['energy_max_rel'] = max(g3['energy_max_rel'], EM.rel_diff(e_mine, ref['E_site_kWh_day']['by_square']['mean']))
                g3['violation_max_abs'] = max(g3['violation_max_abs'], abs(v_mine - ref['avoidable_violation_share']['all_user']))
    g3['passed'] = bool(g3['dev_trace_sha256_equal'] and g3['compared'] == 2 * len(P.CONTROLLERS) * len(P.GROWTH)
                        and g3['energy_max_rel'] <= 1e-12 and g3['violation_max_abs'] <= 1e-12)
    g3['seconds'] = time.time() - t_g3
    validation['G3_frozen_reproduction'] = g3
    xi_dev = None
    print(f'G3 {"PASS" if g3["passed"] else "FAIL"} ({g3["seconds"]:.0f} s)', flush=True)
    # ---- held-out traces: the test week of the test squares (first read)
    t_tr = time.time()
    act, pres, cov = TR.read_week(TR.TEST_DATES)
    test_sq = np.asarray(frozen['test_squares'])
    aref_t = {int(k): float(v) for k, v in frozen['a_ref_test_squares'].items()}
    missing = {int(s): int(np.sum(~pres[s - 1] | (act[s - 1] <= 0))) for s in test_sq}
    norm_test = np.stack([act[s - 1] / aref_t[int(s)] for s in test_sq])
    act = pres = None
    xi_runs, clipped = {}, {}
    for g in P.GROWTH:
        ii, nclip = TR.quantize(g * norm_test)
        xi_runs[g], clipped[str(g)] = ii, nclip
    xi_test = np.concatenate([xi_runs[g] for g in P.GROWTH], axis=0)
    run_g = np.repeat(np.array(P.GROWTH), len(test_sq))
    run_sq = np.tile(test_sq, len(P.GROWTH))
    g2 = {'coverage': cov, 'all_days_aligned': all(c['slot_min'] == 0 and c['slot_max'] == 143 and c['rows_outside_day'] == 0
                                                     and c['misaligned_rows'] == 0 and c['distinct_slots'] == 144 for c in cov.values()),
          'test_squares': len(test_sq), 'slots_without_activity': missing, 'clipped_slots': clipped,
          'trace_sha256': hashlib.sha256(xi_test.tobytes()).hexdigest(), 'seconds': time.time() - t_tr}
    g2['passed'] = bool(g2['all_days_aligned'] and len(test_sq) == TR.N_KEEP - TR.N_DEV_SQ
                        and not set(test_sq.tolist()) & set(dev_sq.tolist()))
    validation['G2_test_traces'] = g2
    print(f'test traces: G2 {"PASS" if g2["passed"] else "FAIL"} ({g2["seconds"]:.0f} s)', flush=True)
    # ---- held-out radio realizations
    log = []
    U0, _, _ = build_population_range(conf2b, conf14, 0, 2)
    U0ref, _, _ = C.build_population(conf2b, conf14, 2)
    g1 = {'offset0_bitwise': all(np.array_equal(U0[k_], U0ref[k_]) for k_ in ('r', 'sec', 'w', 'cell'))
          and all(np.array_equal(U0[d][fi][f], U0ref[d][fi][f]) for d in ('dl', 'ul') for fi in range(2) for f in ('s', 'G'))}
    U0 = U0ref = None
    r0 = R_START if not smoke else R_START
    U, ctx, cx = build_population_range(conf2b, conf14, r0, n_real, log)
    called = sorted({r for r, _ in log})
    g1.update({'realizations_drawn': [called[0], called[-1]] if called else None, 'n_realizations': len(called),
               'calls_per_realization': sorted({sum(1 for r, _ in log if r == rr) for rr in called}),
               'disjoint_from_development': bool(called) and min(called) >= 100})
    g1['passed'] = bool(g1['offset0_bitwise'] and called == list(range(r0, r0 + n_real)) and g1['calls_per_realization'] == [6]
                        and g1['disjoint_from_development'])
    validation['G1_heldout_population'] = g1
    R, K = n_real, n_real * P.N_SECT
    G = len(xg)
    # ---- test table (8 patterns, DL at -3 dB, UL both mixes)
    f3 = {'solves': 0, 'not_converged': 0, 'max_residual': 0.0, 'closure_max_rel': 0.0, 'occupancy_identity_max_abs': 0.0,
          'load_range_bad': 0, 'workload_mismatches': 0, 'workload_comparisons': 0, 'monotone_load_bad': 0, 'zero_traffic_bad': 0}

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

    def state(d, p):
        a72 = np.array(P.PATTERNS[p], dtype=float)
        if d == 'DL':
            return B.build_state(U, ctx, 'DL', 'off' if not a72.any() else 'on', a72, DP, False)
        return B.build_state(U, ctx, 'UL', 'off' if not a72.any() else 'on_ulca', a72, 0.0, False)

    def solve(st, traffic):
        ev = B.eval_state(U, ctx, st, traffic, check=make_check(st), quantiles=False)
        for s_ in ev['sols'].values():
            f3['solves'] += 1
            f3['not_converged'] += int(not s_['converged'])
            f3['max_residual'] = max(f3['max_residual'], s_['res'])
            f3['monotone_load_bad'] += int(np.sum(np.diff(s_['L'], axis=0) < -1e-12))
            f3['zero_traffic_bad'] += int(np.any(s_['L'][0] != 0.0))
        L0 = ev['sols'][0]['L']
        L1 = ev['sols'][1]['L'] if 1 in ev['sols'] else np.zeros_like(L0)
        return {'L0': L0, 'L1': L1, 'agg': ev['agg'], 'car': st['car'],
                'oa': np.array([a['outage_all_users'] for a in ev['agg']]), 'om': np.array([a['outage_admitted_pooled'] for a in ev['agg']]),
                's': np.array([a['traffic_fractions']['served'] for a in ev['agg']])}
    t_tab = time.time()
    DLt, ULt = {}, {}
    for p in P.KS:
        DLt[p] = solve(state('DL', p), xg * T50)
        for mx in P.MIX_KEYS:
            ULt[(p, mx)] = solve(state('UL', p), xg * P.MIXES[mx] * T50)
    table_seconds = time.time() - t_tab
    print(f'test table solved ({table_seconds:.0f} s)', flush=True)
    validation['G4_fixed_point'] = dict(f3, passed=bool(f3['not_converged'] == 0 and f3['max_residual'] <= 1e-9 and f3['closure_max_rel'] <= 1e-12
                                                       and f3['occupancy_identity_max_abs'] <= 1e-9 and f3['load_range_bad'] == 0
                                                       and f3['workload_comparisons'] > 0 and f3['workload_mismatches'] == 0
                                                       and f3['monotone_load_bad'] == 0 and f3['zero_traffic_bad'] == 0))
    g5 = {'identity_max_abs': 0.0, 'monotone_all_bad': 0}
    for rec in list(DLt.values()) + list(ULt.values()):
        g5['monotone_all_bad'] += int(np.sum(np.diff(rec['oa']) < -1e-12))
        for a in rec['agg']:
            n_ = a['not_admitted_share']
            g5['identity_max_abs'] = max(g5['identity_max_abs'], abs(a['outage_all_users'] - (n_ + (1.0 - n_) * a['outage_admitted_pooled'])))
    validation['G5_all_user'] = dict(g5, passed=bool(g5['identity_max_abs'] <= 1e-12 and g5['monotone_all_bad'] == 0))
    cell_sec = np.arange(K) % P.N_SECT
    g6 = {'off_sector_72_load_nonzero': 0, 'off_sector_users_on_72': 0, 'on_sector_72_load_zero_at_traffic': 0}
    for p in P.KS:
        pat = np.array(P.PATTERNS[p])
        off = pat[cell_sec] == 0
        for rec in [DLt[p]] + [ULt[(p, mx)] for mx in P.MIX_KEYS]:
            g6['off_sector_72_load_nonzero'] += int(np.sum(rec['L1'][:, off] != 0.0))
            g6['off_sector_users_on_72'] += int(np.sum((rec['car'] == 1) & (pat[U['sec']] == 0)))
        if pat.any():
            g6['on_sector_72_load_zero_at_traffic'] += int(np.sum(DLt[p]['L1'][1:, ~off].mean(axis=1) <= 0.0))
    validation['G6_activation_coupling'] = dict(g6, passed=bool(all(v == 0 for v in g6.values())))
    U = None
    nM = len(P.MIX_KEYS)
    oDLa = np.stack([DLt[p]['oa'] for p in P.KS])[:, None, :]
    oDLm = np.stack([DLt[p]['om'] for p in P.KS])[:, None, :]
    sDL = np.stack([DLt[p]['s'] for p in P.KS])[:, None, :]
    oULa = np.stack([[ULt[(p, mx)]['oa'] for mx in P.MIX_KEYS] for p in P.KS])
    oULm = np.stack([[ULt[(p, mx)]['om'] for mx in P.MIX_KEYS] for p in P.KS])
    sUL = np.stack([[ULt[(p, mx)]['s'] for mx in P.MIX_KEYS] for p in P.KS])
    test_tab = model_tables(oDLa, oDLm, sDL, oULa, oULm, sUL, xg, T50, thr_both, thr_dl, maxcap)   # thresholds unused in scoring
    ulred = P.ul_reduction_w(conf1)
    test_energy = {}
    for sc in SCENARIOS:
        hw = sc['hw']
        aux = en_mod.p_aux(hw, sc['S'])
        deep = en_mod.ladder(hw, sc['S'], sc['cat'], sc['set'])['deep']
        Pw = np.zeros((P.NP, nM, G, R))
        for p in P.KS:
            on_cell = np.array(P.PATTERNS[p])[cell_sec] == 1
            for mi, mx in enumerate(P.MIX_KEYS):
                p35 = en_mod.p35(sc['case35'], DLt[p]['L0'])
                if sc['ulsens']:
                    p35 = p35 - ulred * (1.0 - ULt[(p, mx)]['L0'])
                p72 = np.where(on_cell[None, :], en_mod.p72(hw, DLt[p]['L1'], ULt[(p, mx)]['L1']) + aux, deep['p_w'])
                Pw[p, mi] = (p35 + p72).reshape(G, R, P.N_SECT).sum(axis=2)
        test_energy[sc['name']] = energy_scenario(en_mod, sc, Pw)

    def test_model(mx, scn):
        return P.Model(test_tab, P.MIX_KEYS.index(mx), 0, test_energy[scn])
    # ---- evaluation: decisions on the frozen development model, outcomes on the test tables
    t_ev = time.time()
    sims, budget_info = {}, {}
    for sc in SCENARIOS:
        for mx in P.MIX_KEYS:
            dec, ev = dev_model(mx, sc['name']), test_model(mx, sc['name'])
            for name in CONTROLLERS:
                if name in ('oracle_budget', 'oracle_test') and sc['name'] not in EXTRA_ON:
                    continue
                ctl = ctrl_for(name, mx, sc['S'])
                o = simulate_split(ctl, xi_test, ev if name == 'oracle_test' else dec, ev, per_real=True)
                sims[(name, mx, sc['name'])] = o
                if name == 'oracle_budget':
                    budget_info[(mx, sc['name'])] = {'ub': ctl.ub_energy, 'lb': ctl.lb_energy, 'infeasible': ctl.budget_infeasible}
    print(f'evaluation done ({time.time() - t_ev:.0f} s)', flush=True)
    # ---- results
    secs_run = xi_test.shape[1] * P.SLOT_S
    results, runs_out = {}, []
    for (name, mx, scn), o in sims.items():
        aon = sims[('aon', mx, scn)]
        for g in P.GROWTH:
            sel = run_g == g
            E = o['E_J'][sel] / 7 / 3.6e6
            Ea = aon['E_J'][sel] / 7 / 3.6e6
            secs = sel.sum() * secs_run
            share = {c: float(o['viol_s'][c][sel].sum() / secs) for c in P.CRITS}
            unav = {c: float(o['unavoid_s'][c][sel].sum() / secs) for c in P.CRITS}
            share_aon = float(aon['viol_s']['all_user'][sel].sum() / secs)
            both_ok = share['all_user'] <= BUDGET and share_aon <= BUDGET
            sav = pigeonhole_ratio(E, Ea, rng_boot)
            bits = o['bits'][sel].sum()
            results[f'{name}|{mx}|{g}|{scn}'] = {
                'E_site_kWh_day': float(E.mean()), 'saving_vs_aon': sav, 'saving_qualified': bool(both_ok),
                'avoidable_violation_share': share, 'unavoidable_share': unav,
                'total_failure_share_all_user': share['all_user'] + unav['all_user'], 'aon_budget_met': share_aon <= BUDGET,
                'transitions_per_day': float((o['n_wake'][sel] + o['n_sleep'][sel]).mean() / 7),
                'gap_s_per_day': float(o['gap_s'][sel].mean() / 7),
                'J_per_Mbit': float(o['E_J'][sel].mean(axis=1).sum() / bits * 1e6) if bits > 0 else None,
                'mean_cells_on': float(P.N_ON[o['k'][sel].astype(np.int64)].mean()),
                'pattern_share': {str(P.PATTERNS[p_]): float(np.mean(o['k'][sel] == p_)) for p_ in P.KS}}
            if scn in PRIMARY_S:
                for j in np.flatnonzero(sel):
                    runs_out.append({'controller': name, 'mix': mx, 'g': g, 'square': int(run_sq[j]), 'scenario': scn,
                                     'E_kWh_day': float(o['E_J'][j].mean() / 7 / 3.6e6),
                                     'viol_share': {c: float(o['viol_s'][c][j] / secs_run) for c in P.CRITS}})
    for (mx, scn), bi in budget_info.items():
        for g in P.GROWTH:
            sel = run_g == g
            r_ = results[f'oracle_budget|{mx}|{g}|{scn}']
            r_['lagrangian_bracket_kWh_day_dev_model'] = {'ub': float(bi['ub'][sel][np.isfinite(bi['ub'][sel])].mean() / 7 / 3.6e6)
                                                          if np.isfinite(bi['ub'][sel]).any() else None,
                                                          'lb': float(bi['lb'][sel].mean() / 7 / 3.6e6), 'runs_budget_infeasible': int(bi['infeasible'][sel].sum())}
    # paired comparisons (descriptive and for the predictions)
    paired = {}
    for (mx, scn) in {(k[1], k[2]) for k in sims}:
        for g in P.GROWTH:
            sel = run_g == g
            e = {c: sims[(c, mx, scn)]['E_J'][sel] / 7 / 3.6e6 for c in CONTROLLERS if (c, mx, scn) in sims}
            pr = {}
            for a_, b_ in (('mpc', 'exh'), ('mpc', 'thr_ul'), ('mpc', 'oracle'), ('mpc', 'oracle_budget'), ('mpc', 'oracle_test'), ('exh', 'oracle')):
                if a_ in e and b_ in e:
                    pr[f'{a_}-{b_}'] = pigeonhole_gap(e[a_], e[b_], e['aon'], rng_boot)
            v = {c: sims[(c, mx, scn)]['viol_s']['all_user'][sel] for c in ('thr_dl', 'thr_ul', 'mpc', 'exh')}
            secs_vec = np.full(int(sel.sum()), secs_run)
            pr['viol thr_dl-thr_ul'] = square_bootstrap_diff(v['thr_dl'], v['thr_ul'], secs_vec, rng_boot)
            pr['viol mpc-exh'] = square_bootstrap_diff(v['mpc'], v['exh'], secs_vec, rng_boot)
            paired[f'{mx}|{g}|{scn}'] = pr
    # ---- predictions
    h1 = {}
    for mx, g, S_ in H1_CELLS:
        r_ = results[f'mpc|{mx}|{g}|primary_{S_}']
        ok = (r_['saving_vs_aon']['point'] >= H1_MIN_SAVING[S_] and r_['saving_vs_aon']['lo'] > 0.0
              and r_['avoidable_violation_share']['all_user'] <= BUDGET)
        h1[f'{mx}|{g}|{S_}'] = {'saving': r_['saving_vs_aon'], 'violation': r_['avoidable_violation_share']['all_user'], 'passed': bool(ok)}
    h2 = {}
    for mx, g, S_ in H2_CELLS:
        d = paired[f'{mx}|{g}|primary_{S_}']['viol thr_dl-thr_ul']
        h2[f'{mx}|{g}|{S_}'] = {'difference': d, 'thr_dl_dl_only_violation': results[f'thr_dl|{mx}|{g}|primary_{S_}']['avoidable_violation_share']['dl_only_all_user'],
                                'passed': bool(d['point'] >= H2_MIN_DIFF and d['lo'] > 0.0)}
    u = {mx: {g: results[f'aon|{mx}|{g}|primary_S0']['unavoidable_share']['all_user'] for g in P.GROWTH} for mx in P.MIX_KEYS}
    h4 = {'unavoidable_share': u,
          'conditions': {'genai_2025 g=2.0 <= 1%': u['genai_2025'][2.0] <= BUDGET, 'genai_2025 g=2.5 >= 1%': u['genai_2025'][2.5] >= BUDGET,
                         'ericsson_typical g=3.0 <= 1%': u['ericsson_typical'][3.0] <= BUDGET}}
    h4['passed'] = bool(all(h4['conditions'].values()))
    q3 = {}
    for mx, g, S_ in H1_CELLS:
        d = paired[f'{mx}|{g}|primary_{S_}']['mpc-oracle']
        q3[f'{mx}|{g}|{S_}'] = {'gap': d, 'passed': bool(d['point'] <= Q3_MAX_GAP[S_])}
    predictions = {'H1': {'cells': h1, 'held': bool(all(v['passed'] for v in h1.values()))},
                   'H2': {'cells': h2, 'held': bool(all(v['passed'] for v in h2.values()))},
                   'H4': dict(h4, held=h4['passed']),
                   'Q3': {'cells': q3, 'held': bool(all(v['passed'] for v in q3.values()))}}
    # ---- G7 ledger (independent scalar loop, on the test model)
    rng = random.Random(BOOT_SEED + 7)
    tab_np = {'xg': xg, 'T50': T50, 'comp': test_tab['comp'], 'sDL': test_tab['sDL'], 'sUL': test_tab['sUL']}
    g7 = {'runs_checked': 0, 'energy_max_rel': 0.0, 'viol_max_abs_s': 0.0, 'bits_max_rel': 0.0, 'transition_mismatches': 0}
    keys_all = list(sims)
    for _ in range(100):
        name, mx, scn = keys_all[rng.randrange(len(keys_all))]
        j = rng.randrange(xi_test.shape[0])
        o = sims[(name, mx, scn)]
        ref = P.reference_run(o['k'][j], xi_test[j], tab_np, P.MIX_KEYS.index(mx), 0, test_energy[scn])
        g7['runs_checked'] += 1
        g7['energy_max_rel'] = max(g7['energy_max_rel'], EM.rel_diff(float(o['E_J'][j].mean()), ref['E_J']))
        g7['viol_max_abs_s'] = max(g7['viol_max_abs_s'], max(abs(float(o['viol_s'][c][j]) - ref['viol_s'][c]) for c in P.CRITS))
        g7['bits_max_rel'] = max(g7['bits_max_rel'], EM.rel_diff(float(o['bits'][j]), ref['bits']))
        g7['transition_mismatches'] += int(o['n_wake'][j] != ref['n_wake'] or o['n_sleep'][j] != ref['n_sleep'])
    g7['passed'] = bool(g7['runs_checked'] == 100 and g7['energy_max_rel'] <= 1e-9 and g7['viol_max_abs_s'] <= 1e-6
                        and g7['bits_max_rel'] <= 1e-9 and g7['transition_mismatches'] == 0)
    validation['G7_ledger'] = g7
    # ---- G8 causality on the held-out traces (frozen controllers)
    g8 = {'checks': 0, 'violations': 0}
    dec = dev_model('genai_2025', 'primary_S0')
    for name in P.CONTROLLERS:
        if name in ('oracle', 'oracle_dl'):
            continue
        rows_, cuts_ = [], []
        for _ in range(20):
            base = xi_test[rng.randrange(xi_test.shape[0])].copy()
            rows_.append(base)
            cuts_.append(None)
            for cut in sorted(rng.sample(range(1, base.shape[0]), 5)):
                pert = base.copy()
                pert[cut:] = np.array([rng.randrange(G) for _ in range(base.shape[0] - cut)])
                rows_.append(pert)
                cuts_.append(cut)
        o = simulate_split(ctrl_for(name, 'genai_2025', 'S0'), np.stack(rows_), dec, dec, per_real=False)
        for r0_ in range(0, len(rows_), 6):
            for q_ in range(1, 6):
                cut = cuts_[r0_ + q_]
                g8['checks'] += 1
                g8['violations'] += int(not np.array_equal(o['k'][r0_, :cut + 1], o['k'][r0_ + q_, :cut + 1]))
    g8['passed'] = bool(g8['checks'] > 0 and g8['violations'] == 0)
    validation['G8_causality'] = g8
    # ---- G9 budget oracle: bracket valid, plans within the budget on the decision model
    g9 = {'cells': 0, 'lb_above_ub': 0, 'runs': 0, 'runs_budget_infeasible': 0}
    for (mx, scn), bi in budget_info.items():
        fin = np.isfinite(bi['ub'])
        g9['cells'] += 1
        g9['runs'] += int(len(bi['ub']))
        g9['lb_above_ub'] += int(np.sum(bi['lb'][fin] > bi['ub'][fin] * (1 + 1e-9) + 1e-6))
        g9['runs_budget_infeasible'] += int(bi['infeasible'].sum())
    g9['passed'] = bool(g9['cells'] == len(EXTRA_ON) * len(P.MIX_KEYS) and g9['lb_above_ub'] == 0)
    validation['G9_budget_oracle'] = g9
    # ---- G10 decision and offline times
    g10 = {'test_table_seconds': table_seconds, 'per_controller': {}}
    dec = dev_model('genai_2025', 'primary_S0')
    row = xi_runs[2.0][:1]
    for name in CONTROLLERS:
        ctl = ctrl_for(name, 'genai_2025', 'S0')
        t_r = time.perf_counter()
        ctl.reset(1, dec, row if ctl.is_oracle else None)
        t_reset = time.perf_counter() - t_r
        ts = []
        for t in range(row.shape[1]):
            t_d = time.perf_counter()
            ctl.decide(t, row[:, :t])
            ts.append(time.perf_counter() - t_d)
        g10['per_controller'][name] = {'reset_ms': t_reset * 1e3, 'decide_median_ms': float(np.median(ts) * 1e3),
                                       'decide_p99_ms': float(np.quantile(ts, 0.99) * 1e3)}
    g10.update({'machine': platform.platform(), 'processor': platform.processor(), 'passed': len(g10['per_controller']) == len(CONTROLLERS)})
    validation['G10_times'] = g10
    gates_ok = all(v['passed'] for v in validation.values() if isinstance(v, dict)) and not smoke
    validation['smoke_run'] = smoke
    validation['all_checks_passed'] = gates_ok
    summary = {'n_realizations': R, 'realizations': [R_START, R_START + R], 'scope': {'dl_offset_db': DP, 'scenarios': [s['name'] for s in SCENARIOS]},
               'labels': conf['labels'], 'holdout': conf['holdout'],
               'test_traces': {'squares': test_sq.tolist(), 'slots_without_activity': missing, 'clipped_slots': clipped},
               'test_table_seconds': table_seconds, 'predictions': predictions if not smoke else None,
               'results_test': results, 'paired': paired,
               'decision': conf['decision_rules']['all_gates_pass' if gates_ok else 'gate_fails']}
    summary['wall_time_s'] = time.time() - t0
    np.savez_compressed(outdir / 'raw' / 'test_table_isd200.npz', xg=xg, oDLa=oDLa, oDLm=oDLm, sDL=sDL, oULa=oULa, oULm=oULm, sUL=sUL,
                        **{f'Pw_{k}': v['Pw'] for k, v in test_energy.items()},
                        **{f'DL_L0_p{p}': DLt[p]['L0'] for p in P.KS}, **{f'DL_L1_p{p}': DLt[p]['L1'] for p in P.KS},
                        **{f'UL_L0_p{p}_{mx}': ULt[(p, mx)]['L0'] for p in P.KS for mx in P.MIX_KEYS},
                        **{f'UL_L1_p{p}_{mx}': ULt[(p, mx)]['L1'] for p in P.KS for mx in P.MIX_KEYS})
    with gzip.open(outdir / 'raw' / 'runs.json.gz', 'wt', encoding='utf-8') as fh:
        json.dump(runs_out, fh)
    (outdir / 'summary.json').write_text(json.dumps(summary, indent=1, default=float), encoding='utf-8')
    (outdir / 'validation.json').write_text(json.dumps(validation, indent=2, default=float), encoding='utf-8')
    manifest = {'generated_utc': datetime.now(timezone.utc).isoformat(), 'tool': 'scripts/p3_4_heldout.py', 'git_head_at_run': args.git_head,
                'python': platform.python_version(), 'numpy': np.__version__, 'scipy': scipy.__version__, 'pandas': TR.pd.__version__,
                'config_sha256': sha256(args.config),
                'input_sha256': {'p3_3/manifest.json': sha256(P33_DIR / 'manifest.json'), **{f'traces/{f}': h for f, h in conf['traces']['files'].items()}},
                'outputs_sha256': {'summary.json': sha256(outdir / 'summary.json'), 'raw/test_table_isd200.npz': sha256(outdir / 'raw' / 'test_table_isd200.npz'),
                                   'raw/runs.json.gz': sha256(outdir / 'raw' / 'runs.json.gz')},
                'all_checks_passed': gates_ok}
    (outdir / 'manifest.json').write_text(json.dumps(manifest, indent=2), encoding='utf-8')
    for k, v in validation.items():
        if isinstance(v, dict):
            print(k, 'PASS' if v['passed'] else 'FAIL', flush=True)
    if not smoke:
        print('predictions held:', {k: v['held'] for k, v in predictions.items()})
    print('P3.4', 'GATES PASSED' if gates_ok else 'GATES FAILED', f'{time.time() - t0:.0f} s')
    sys.exit(0 if gates_ok else 1)


if __name__ == '__main__':
    main()
