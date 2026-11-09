#!/usr/bin/env python3
"""E1.5.3a: hazard 16 diagnosis by AMC replay (Stage A) and the C-AMC link mapping (Stage B).

Implements configs/e1/e1_5_3a_amc_diagnostic.json. Run inside WSL with the project venv.
Outputs in --outdir: validation.json, summary.json, manifest.json; raw files in raw/ (git-ignored).
"""
import argparse
import csv
import json
import math
import platform
import shutil
import subprocess
import sys
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'scripts'))
from cmwave.radio import link_budget as lb  # noqa: E402
import e1_5_1_deterministic as e151  # noqa: E402
import e1_5_1d_tdma_diagnostic as diag  # noqa: E402
import e1_5_3_link_mapping as e153  # noqa: E402

CONFIG = ROOT / 'configs/e1/e1_5_3a_amc_diagnostic.json'
E151_CONFIG = ROOT / 'configs/e1/e1_5_1_deterministic.json'
E153_CONFIG = ROOT / 'configs/e1/e1_5_3_link_mapping.json'
E153_RESULTS = ROOT / 'results/phase2/e1_5_3_link_mapping'
E1520_RESULTS = ROOT / 'results/phase2/e1_5_2_0_conventions'
OUTDIR = ROOT / 'results/phase2/e1_5_3a_amc_diagnostic'
N_RB = 51
F3_RB = 5
TARGET = 0.1
A2_TOL = 1e-9
SAMPLING_P = 1e-3
PMONO_REL = 0.1
R4B_TAGS = ('t2_L93', 't2_L113', 't1_L113')
R4B_FILES = ('losses.csv', 'e15_dl_tb.csv', 'e15_ul_tb.csv', 'e15_run_info.csv', 'e153_error_model.csv')
REPLAY_FILES = ('replay.csv', 'losses.csv', 'e153a_amc_choice.csv', 'e153a_tbler.csv', 'e15_run_info.csv', 'run.log')
DIRS = ('DL', 'UL')
DOMINANT = {'DL': (N_RB, 13), 'UL': (N_RB, 12)}
F3_SHIFT_DB = 10 * math.log10(N_RB / F3_RB)


# ---------------------------------------------------------------- pure helpers (unit-tested)
def rule_first_fail(tbler_by_mcs, max_mcs, target=TARGET):
    """NrAmc's loop: first MCS with TBLER > target, minus one (0 if MCS 0 fails); max_mcs if none fails."""
    for m in range(max_mcs + 1):
        if tbler_by_mcs[m] > target:
            return max(m - 1, 0)
    return max_mcs


def poisson_binomial_sf(ps, k):
    """P(K >= k) for K = sum of independent Bernoulli(p_i)."""
    dist = np.zeros(len(ps) + 1)
    dist[0] = 1.0
    for i, p in enumerate(ps):
        dist[1:i + 2] = dist[1:i + 2] * (1 - p) + dist[0:i + 1] * p
        dist[0] *= (1 - p)
    return float(min(1.0, dist[k:].sum()))


def classify_point(t1sym_used, t_act, n_fail):
    """t1sym_used: TBLER the SISO AMC assumed (1-symbol TB) for each TB's MCS; t_act: replayed TBLER of
    each transmitted TB; n_fail: observed corrupt first transmissions."""
    mean_act = float(np.mean(t_act))
    if max(t1sym_used) <= TARGET < mean_act:
        return 'MISMATCH'
    if mean_act <= TARGET and poisson_binomial_sf(t_act, n_fail) >= SAMPLING_P:
        return 'SAMPLING'
    return 'UNEXPLAINED'


def non_monotone_points(curve):
    """Points whose SE is below the maximum SE of all lower-SNR points."""
    out, best = [], -math.inf
    for s, se in sorted(curve):
        if se < best - 1e-12:
            out.append((s, se, best))
        best = max(best, se)
    return out


def pmono_violations(curve, rel=PMONO_REL):
    pts = sorted(curve)
    return [(s0, e0, s1, e1) for (s0, e0), (s1, e1) in zip(pts, pts[1:]) if e0 > 0 and e1 < (1 - rel) * e0]


def f3_pointwise(curve, shift_db=F3_SHIFT_DB):
    """Points x with SE(x + shift) < SE(x) (piecewise-linear, clamped at the grid ends)."""
    pts = sorted(curve)
    xs = np.array([p[0] for p in pts])
    ys = np.array([p[1] for p in pts])
    shifted = np.interp(xs + shift_db, xs, ys)
    return [(float(x), float(y), float(z)) for x, y, z in zip(xs, ys, shifted) if z < y - 1e-12]


def first_tx_se(tb_bytes, tbler, n_rb, n_sym):
    """Analytic first-transmission SE per used resource (no HARQ)."""
    return 8 * tb_bytes * (1 - tbler) / (n_rb * 12 * e153.SCS * n_sym * e153.T_SYM)


def window_first_tx(tbs, window):
    lo, hi = int(window[0] * 1e9), int(window[1] * 1e9)
    return [r for r in tbs if lo <= int(r['time_ns']) < hi and r['rv'] == '0']


def e153_args(c, st, em_type, rd):
    """E1.5.3 program arguments, identical to scripts/e1_5_3_link_mapping.py."""
    return [f'--frequency={st["carrier_hz"]!r}', f'--bandwidth={c["bandwidth_hz"]!r}',
            f'--numerology={c["numerology"]}', f'--rbOverhead={c["rb_overhead"]!r}',
            f'--gnbTxPower={c["gnb_tx_power_dbm"]!r}', f'--ueTxPower={c["ue_tx_power_dbm"]!r}',
            f'--nfGnb={c["nf_gnb_db"]!r}', f'--nfUe={c["nf_ue_db"]!r}', f'--lossFile={rd / "losses.csv"}',
            f'--scheduler={st["scheduler"]}', '--dlRate=300000000.0', '--ulRate=100000000.0',
            '--packetSize=1000', '--ulPacketSize=1000', f'--appStart={st["app_start_s"]!r}',
            f'--simTime={st["sim_time_s"]!r}', f'--tddPattern={c["tdd_pattern"]}', '--enableRlf=0',
            f'--errorModel={em_type}']


# ---------------------------------------------------------------- program calls
def run_program(ns3, rd, args):
    cmd = ['./ns3', 'run', '--no-build', f'--cwd={rd}', ' '.join([e151.PROGRAM] + args)]
    t1 = time.time()
    p = subprocess.run(cmd, cwd=ns3, capture_output=True, text=True)
    rt = time.time() - t1
    (rd / 'run.log').write_text(f'cmd: {cmd}\nexit: {p.returncode}\nruntime_s: {rt:.1f}\n--- stdout\n'
                                f'{p.stdout}\n--- stderr\n{p.stderr}\n', encoding='utf-8')
    return p.returncode, rt, cmd


def keep(rd, dst, files):
    shutil.rmtree(dst, ignore_errors=True)
    dst.mkdir(parents=True)
    h = {}
    for f in files:
        if (rd / f).exists():
            shutil.copy2(rd / f, dst / f)
            h[f] = e151.sha256(dst / f)
    return h


def read_replay(rd):
    choice = {r['key']: {k: (r[k] if k == 'key' or k == 'sinr_lin' else int(r[k])) for k in r}
              for r in e151.read_csv(rd / 'e153a_amc_choice.csv')}
    tbl = {}
    with open(rd / 'e153a_tbler.csv', newline='', encoding='utf-8') as fh:
        for r in csv.DictReader(fh):
            tbl[(r['key'], int(r['n_rb']), int(r['n_sym']), int(r['mcs']))] = (
                int(r['tb_size']), float(r['tbler_siso']), float(r['tbler_mimo']))
    return choice, tbl


def rerun_e1520(outdir, git_head, name='r4a_e1520_rerun'):
    rdir = outdir / name
    p = subprocess.run([sys.executable, str(ROOT / 'scripts/e1_5_2_0_conventions.py'), '--outdir', str(rdir),
                        '--git-head', str(git_head)], capture_output=True, text=True)
    (outdir / f'{name}_stdout.txt').write_text(p.stdout + '\n--- stderr\n' + p.stderr, encoding='utf-8')
    ref = {r['tag']: r for r in json.loads((E1520_RESULTS / 'manifest.json').read_text())['runs']}
    new = {r['tag']: r for r in json.loads((rdir / 'manifest.json').read_text())['runs']} \
        if (rdir / 'manifest.json').exists() else {}
    differ, n = [], 0
    for tag in sorted(set(ref) | set(new)):
        ha, hb = ref.get(tag, {}).get('raw_sha256', {}), new.get(tag, {}).get('raw_sha256', {})
        for f in sorted(set(ha) | set(hb)):
            if f == 'run.log':
                continue
            n += 1
            if ha.get(f) != hb.get(f):
                differ.append(f'{tag}/{f}')
    val = json.loads((rdir / 'validation.json').read_text()) if (rdir / 'validation.json').exists() else {}
    ok = p.returncode == 0 and len(new) == 21 and n > 0 and not differ and bool(val.get('all_checks_passed'))
    return {'exit_code': p.returncode, 'runs': len(new), 'files_compared': n, 'differing_files': differ,
            'passed': bool(ok)}


# ---------------------------------------------------------------- stage A
def stage_a_checks(model, tbs_by_run, per_run, choice, tbl, max_mcs, curves):
    """tbs_by_run[tag][d]: window first-tx TBs. Returns (checks, details)."""
    a0, a1, a2_siso, a2_mimo, missing = [], [], 0.0, 0.0, []
    for tag, by_d in tbs_by_run.items():
        for d, rows in by_d.items():
            for r in rows:
                s, rb, sym, m = r['sinr_lin'], int(r['rb_assigned']), int(r['num_sym']), int(r['mcs'])
                k = (s, rb, sym, m)
                if k not in tbl or s not in choice:
                    missing.append(f'{tag}/{d}/{k}')
                    continue
                size, ts, tm = tbl[k]
                if size != int(r['tb_size']):
                    a0.append(f'{tag}/{d}/t={r["time_ns"]}: {r["tb_size"]} vs {size}')
                exp = choice[s]['ul_siso_mcs'] if d == 'UL' else choice[s]['dl_mcs_from_cqi']
                if m != exp:
                    a1.append(f'{tag}/{d}/t={r["time_ns"]}: mcs {m} vs {exp}')
                tr = float(r['tbler'])
                a2_siso = max(a2_siso, abs(tr - ts))
                a2_mimo = max(a2_mimo, abs(tr - tm))
    checks = {
        'A0_tb_size': {'failed': a0[:50], 'n_failed': len(a0), 'missing_replay_rows': missing[:50],
                       'passed': not a0 and not missing},
        'A1_mcs': {'failed': a1[:50], 'n_failed': len(a1), 'passed': not a1 and not missing},
        'A2_tbler': {'max_abs_err_siso': a2_siso, 'max_abs_err_mimo': a2_mimo,
                     'matching_path': [p for p, e in (('siso', a2_siso), ('mimo', a2_mimo)) if e <= A2_TOL],
                     'passed': (a2_siso <= A2_TOL or a2_mimo <= A2_TOL) and not missing},
    }
    # A3: points with SNR > 0 and first-tx BLER > 0.1
    points = {}
    for tag, res in per_run.items():
        for d in DIRS:
            x = res[d]
            if x['snr_db'] is not None and x['snr_db'] > 0 and x['bler_first_tx'] is not None \
                    and x['bler_first_tx'] > TARGET:
                rows = tbs_by_run[tag][d]
                t1 = [tbl[(r['sinr_lin'], N_RB, 1, int(r['mcs']))][1] for r in rows]
                ta = [tbl[(r['sinr_lin'], int(r['rb_assigned']), int(r['num_sym']), int(r['mcs']))][1] for r in rows]
                nf = sum(r['corrupt'] == '1' for r in rows)
                points[f'{tag}/{d}'] = {
                    'snr_db': x['snr_db'], 'n_first_tx': len(rows), 'n_corrupt': nf,
                    'bler_obs': nf / len(rows), 'mcs_used': sorted({int(r['mcs']) for r in rows}),
                    'tbler_assumed_1sym_max': max(t1), 'tbler_transmitted_mean': float(np.mean(ta)),
                    'p_at_least_obs': poisson_binomial_sf(ta, nf), 'class': classify_point(t1, ta, nf)}
    unexpl = [k for k, v in points.items() if v['class'] == 'UNEXPLAINED']
    by_snr = {(t.split('_')[0], d, round(r[d]['snr_db'], 9)): f'{t}/{d}'
              for t, r in per_run.items() for d in DIRS if r[d]['snr_db'] is not None}
    nonmono = {}
    for d in DIRS:
        for s, se, best in non_monotone_points(curves[f'{model}_{d}']):
            key = by_snr.get((model, d, round(s, 9)))
            nonmono[key] = {'snr_db': s, 'se': se, 'max_se_lower_snr': best,
                            'class': points.get(key, {}).get('class', 'BLER<=0.1')}
    checks['A3_classification'] = {'n_points': len(points), 'unexplained': unexpl,
                                   'classes': dict(Counter(v['class'] for v in points.values())),
                                   'passed': not unexpl}
    # A4: Python 12-symbol rule == program MIMO-path MCS
    a4 = []
    for key, ch in choice.items():
        t = {m: tbl[(key, N_RB, 12, m)][2] for m in range(max_mcs + 1)}
        rmcs = rule_first_fail(t, max_mcs)
        if rmcs != ch['dl_mimo_mcs'] or rmcs != ch['ul_mimo_mcs']:
            a4.append(f'{key}: rule {rmcs} vs dl {ch["dl_mimo_mcs"]} ul {ch["ul_mimo_mcs"]}')
    checks['A4_rule'] = {'n_sinr': len(choice), 'failed': a4[:50], 'passed': not a4}
    return checks, {'points': points, 'non_monotone': nonmono}


# ---------------------------------------------------------------- analytics (reported only)
def point_table(choice, tbl, py_key, max_mcs):
    out = {}
    for d in DIRS:
        k = py_key[d]
        ch = choice[k]
        rb, sym = DOMINANT[d]
        siso = ch['ul_siso_mcs'] if d == 'UL' else ch['dl_siso_mcs']
        used = ch['ul_siso_mcs'] if d == 'UL' else ch['dl_mcs_from_cqi']
        camc = ch['ul_mimo_mcs'] if d == 'UL' else ch['dl_mimo_mcs']
        env = [(first_tx_se(tbl[(k, rb, sym, m)][0], tbl[(k, rb, sym, m)][1], rb, sym), m) for m in range(max_mcs + 1)]
        best = max(env)
        f3t = {m: tbl[(k, F3_RB, 12, m)][1] for m in range(max_mcs + 1)}
        f3m = rule_first_fail(f3t, max_mcs)
        out[d] = {'siso_mcs': siso, 'mcs_used_stock': used, 'c_amc_mcs': camc,
                  'tbler_stock_dominant': tbl[(k, rb, sym, used)][1],
                  'tbler_c_amc_dominant': tbl[(k, rb, sym, camc)][1],
                  'tbler_c_amc_13sym': tbl[(k, N_RB, 13, camc)][1],
                  'se_first_tx_stock': first_tx_se(tbl[(k, rb, sym, used)][0], tbl[(k, rb, sym, used)][1], rb, sym),
                  'se_first_tx_c_amc': first_tx_se(tbl[(k, rb, sym, camc)][0], tbl[(k, rb, sym, camc)][1], rb, sym),
                  'se_first_tx_envelope': best[0], 'envelope_mcs': best[1],
                  'f3_5rb_c_amc_mcs': f3m,
                  'f3_5rb_se_first_tx': first_tx_se(tbl[(k, F3_RB, 12, f3m)][0], f3t[f3m], F3_RB, 12),
                  'c_amc_51rb_se_first_tx_12sym': first_tx_se(tbl[(k, N_RB, 12, camc)][0],
                                                              tbl[(k, N_RB, 12, camc)][1], N_RB, 12)}
    return out


# ---------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--config', default=str(CONFIG))
    ap.add_argument('--outdir', default=str(OUTDIR))
    ap.add_argument('--ns3-dir', default=str(Path.home() / '6g-external/ns-3-dev'))
    ap.add_argument('--git-head', default=None)
    args = ap.parse_args()
    conf = json.loads(Path(args.config).read_text(encoding='utf-8'))
    c151 = json.loads(E151_CONFIG.read_text(encoding='utf-8'))
    c153 = json.loads(E153_CONFIG.read_text(encoding='utf-8'))
    c = c151['common']
    st = c153['setup']
    window = conf['inputs']['window_s']
    ns3 = Path(args.ns3_dir)
    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    prog_sha = e151.check_stack(c151, ns3)
    t0 = time.time()
    man153 = json.loads((E153_RESULTS / 'manifest.json').read_text())
    sum153 = json.loads((E153_RESULTS / 'summary.json').read_text())
    runs153 = {r['tag']: r for r in man153['runs']}
    for tag, r in runs153.items():
        for f in ('e15_dl_tb.csv', 'e15_ul_tb.csv'):
            if e151.sha256(E153_RESULTS / 'raw' / tag / f) != r['raw_sha256'][f]:
                raise SystemExit(f'E1.5.3 raw file changed: {tag}/{f}')
    cfg = diag.carrier_cfg(c, st['carrier_hz'])
    g = st['loss_grid_db']
    losses = [float(x) for x in np.arange(g['start'], g['stop'] + g['step'] / 2, g['step'])]

    def py_snr(L, d):
        if d == 'DL':
            return float(lb.downlink(np.array([L]), cfg)['snr_db'][0])
        return c['ue_tx_power_dbm'] - L - float(lb.noise_dbm(N_RB * cfg.prb_bw_hz, c['nf_gnb_db']))

    work = Path.home() / 'cmwave-runs/e1_5_3a'
    validation, summary, runs_meta = {}, {'stage_a': {}, 'points': {}, 'stage_b': {}}, []
    replay = {}
    # ------------------------------------------------ Stage A
    for em in st['error_models']:
        m = em['tag']
        tags = [f'{m}_L{L:.0f}' for L in losses]
        tbs_by_run, all_sinr = {}, set()
        trace_sinr = defaultdict(set)
        for tag in tags:
            tbs_by_run[tag] = {}
            for d in DIRS:
                tbs, _ = e153.tb_rows(E153_RESULTS / 'raw' / tag, d)
                tbs_by_run[tag][d] = window_first_tx(tbs, window)
                for r in tbs:
                    all_sinr.add(r['sinr_lin'])
                    trace_sinr[(tag, d)].add(r['sinr_lin'])
        py_key = {L: {d: repr(10 ** (py_snr(L, d) / 10)) for d in DIRS} for L in losses}
        keys = sorted(all_sinr | {k for v in py_key.values() for k in v.values()}, key=float)
        rd = work / f'replay_{m}'
        shutil.rmtree(rd, ignore_errors=True)
        rd.mkdir(parents=True)
        (rd / 'replay.csv').write_text('key,sinr_lin\n' + ''.join(f'{k},{k}\n' for k in keys), encoding='utf-8')
        diag.write_losses(rd, {'u0': 100.0})
        rc, rt, cmd = run_program(ns3, rd, e153_args(c, st, em['type'], rd) + [f'--amcReplay={rd / "replay.csv"}'])
        h = keep(rd, outdir / 'raw' / f'replay_{m}', REPLAY_FILES)
        runs_meta.append({'tag': f'replay_{m}', 'cmd': cmd, 'exit_code': rc, 'runtime_s': rt, 'raw_sha256': h})
        print(f'replay_{m}: exit {rc}, {rt:.1f} s, {len(keys)} SINRs', flush=True)
        if rc != 0:
            raise SystemExit(f'replay_{m} failed; see {rd / "run.log"}')
        choice, tbl = read_replay(rd)
        max_mcs = next(iter(choice.values()))['max_mcs']
        per_run = {t: sum153['per_run'][t] for t in tags}
        chk, det = stage_a_checks(m, tbs_by_run, per_run, choice, tbl, max_mcs, sum153['curves'])
        for k, v in chk.items():
            validation[f'{k}_{m}'] = v
        summary['stage_a'][m] = det
        summary['points'][m] = {f'L{L:.0f}': dict(point_table(choice, tbl, py_key[L], max_mcs),
                                                   snr_db={d: py_snr(L, d) for d in DIRS})
                                for L in losses}
        replay[m] = (choice, tbl, py_key, trace_sinr, max_mcs)
    h16 = all(v['passed'] for k, v in validation.items() if k[:2] in ('A0', 'A1', 'A2', 'A3', 'A4'))
    print('Stage A (H16)', 'CONFIRMED' if h16 else 'NOT CONFIRMED', flush=True)

    def finish(passed_all, decision):
        summary['decision'] = decision
        (outdir / 'validation.json').write_text(json.dumps({'checks': validation, 'all_checks_passed': passed_all},
                                                           indent=2, default=e151.json_default), encoding='utf-8')
        (outdir / 'summary.json').write_text(json.dumps(summary, indent=2, default=e151.json_default),
                                             encoding='utf-8')
        manifest = {'generated_utc': datetime.now(timezone.utc).isoformat(),
                    'tool': 'scripts/e1_5_3a_amc_diagnostic.py', 'git_head_at_run': args.git_head,
                    'python': platform.python_version(), 'numpy': np.__version__,
                    'config_sha256': e151.sha256(args.config), 'program_sha256': prog_sha,
                    'ns3_commit': c151['stack']['ns3_commit'], 'nr_commit': c151['stack']['nr_commit'],
                    'e153_manifest_sha256': e151.sha256(E153_RESULTS / 'manifest.json'),
                    'wall_time_s': time.time() - t0, 'runs': runs_meta, 'all_checks_passed': passed_all}
        (outdir / 'manifest.json').write_text(json.dumps(manifest, indent=2, default=e151.json_default),
                                              encoding='utf-8')
        for k, v in validation.items():
            print(k, 'PASS' if v['passed'] else 'FAIL', flush=True)

    if not h16:
        finish(False, {'H16_confirmed': False, 'c_amc_valid': None, 'primary_mapping': None,
                       'note': 'stop and diagnose (pre-registered)'})
        raise SystemExit('Stage A failed: stop')
    # ------------------------------------------------ Stage B: B0
    b0 = []
    camc = {}
    for em in st['error_models']:
        m = em['tag']
        choice, tbl, py_key, trace_sinr, max_mcs = replay[m]
        for L in losses:
            tag = f'{m}_L{L:.0f}'
            camc[tag] = {}
            for d in DIRS:
                col = 'ul_mimo_mcs' if d == 'UL' else 'dl_mimo_mcs'
                mc = choice[py_key[L][d]][col]
                camc[tag][d] = mc
                for s in trace_sinr[(tag, d)]:
                    if choice[s][col] != mc:
                        b0.append(f'{tag}/{d}: python {mc} vs trace {s} {choice[s][col]}')
    validation['B0_mcs_consistency'] = {'failed': b0[:50], 'n_failed': len(b0), 'passed': not b0}
    # R4a, R4b
    validation['R4a_link_mode_unchanged'] = rerun_e1520(outdir, args.git_head)
    print('R4a', 'PASS' if validation['R4a_link_mode_unchanged']['passed'] else 'FAIL', flush=True)
    differ = []
    for tag in R4B_TAGS:
        m, L = tag.split('_L')[0], float(tag.split('_L')[1])
        em_type = next(e['type'] for e in st['error_models'] if e['tag'] == m)
        rd = work / 'r4b' / tag
        shutil.rmtree(rd, ignore_errors=True)
        rd.mkdir(parents=True)
        diag.write_losses(rd, {'u0': L})
        rc, rt, cmd = run_program(ns3, rd, e153_args(c, st, em_type, rd))
        runs_meta.append({'tag': f'r4b_{tag}', 'cmd': cmd, 'exit_code': rc, 'runtime_s': rt,
                          'raw_sha256': {f: e151.sha256(rd / f) for f in R4B_FILES if (rd / f).exists()}})
        for f in R4B_FILES:
            if not (rd / f).exists() or e151.sha256(rd / f) != runs153[tag]['raw_sha256'][f]:
                differ.append(f'{tag}/{f}')
    validation['R4b_e153_reproduced'] = {'runs': list(R4B_TAGS), 'differing_files': differ,
                                         'passed': not differ}
    print('R4b', 'PASS' if not differ else 'FAIL', flush=True)
    # runs
    e0_fail, v_fail, f1_fail = [], [], []
    per_run = {}
    for em in st['error_models']:
        m = em['tag']
        for L in losses:
            tag = f'{m}_L{L:.0f}'
            rd = work / 'b' / tag
            shutil.rmtree(rd, ignore_errors=True)
            rd.mkdir(parents=True)
            diag.write_losses(rd, {'u0': L})
            fx = camc[tag]
            rc, rt, cmd = run_program(ns3, rd, e153_args(c, st, em['type'], rd) +
                                      [f'--fixedMcsDl={fx["DL"]}', f'--fixedMcsUl={fx["UL"]}'])
            h = keep(rd, outdir / 'raw' / f'b_{tag}', e153.FILES)
            runs_meta.append({'tag': f'b_{tag}', 'cmd': cmd, 'exit_code': rc, 'runtime_s': rt, 'raw_sha256': h})
            print(f'b_{tag}: exit {rc}, {rt:.0f} s, MCS DL {fx["DL"]} UL {fx["UL"]}', flush=True)
            if rc != 0:
                raise SystemExit(f'b_{tag} failed; see {rd / "run.log"}')
            info = {r['key']: r['value'] for r in e151.read_csv(rd / 'e15_run_info.csv')}
            inst = e151.read_csv(rd / 'e153_error_model.csv')
            types = [info['error_model_gnb_dl_amc'], info['error_model_gnb_ul_amc']] + \
                [r['error_model_instance'] for r in inst]
            if any(t != em['type'] for t in types):
                e0_fail.append(tag)
            ok_info = (info.get('sched_fixed_mcs_dl') == '1' and info.get('sched_fixed_mcs_ul') == '1' and
                       info.get('sched_starting_mcs_dl') == str(fx['DL']) and
                       info.get('sched_starting_mcs_ul') == str(fx['UL']))
            bad = [f'{d}:{r["mcs"]}' for d, f in (('DL', 'e15_dl_tb.csv'), ('UL', 'e15_ul_tb.csv'))
                   for r in e151.read_csv(rd / f) if int(r['mcs']) != fx[d]]
            if not ok_info or bad:
                f1_fail.append(f'{tag}: info_ok={ok_info}, wrong-MCS rows {len(bad)} {bad[:3]}')
            res = {'loss_db': L, 'model': m, 'fixed_mcs': fx}
            dl_ref = py_snr(L, 'DL')
            for d, fn in (('DL', lambda n: dl_ref),
                          ('UL', lambda n: c['ue_tx_power_dbm'] - L - float(lb.noise_dbm(n * cfg.prb_bw_hz,
                                                                                         c['nf_gnb_db'])))):
                tbs, _ = e153.tb_rows(rd, d)
                mtr = e153.run_metrics(tbs, window, fn)
                res[d] = mtr
                if mtr['max_abs_sinr_err_db'] > e153.TOL_DB:
                    v_fail.append(f'{tag}/{d}')
            per_run[tag] = res
    validation['E0_error_model'] = {'failed_runs': e0_fail, 'passed': not e0_fail}
    validation['V_snr_identity'] = {'failed': v_fail,
                                    'max_abs_err_db': max(max(r['DL']['max_abs_sinr_err_db'],
                                                              r['UL']['max_abs_sinr_err_db'])
                                                          for r in per_run.values()),
                                    'passed': not v_fail}
    validation['F1_fixed_mcs'] = {'failed': f1_fail, 'passed': not f1_fail}
    cov = [f'{t}/{d}' for t, r in per_run.items() for d in DIRS
           if r[d]['snr_db'] is not None and -10 <= r[d]['snr_db'] <= 35 and r[d]['tbs_window'] < 50]
    validation['C_coverage'] = {'failed': cov, 'passed': not cov}
    c_amc_valid = all(validation[k]['passed'] for k in ('B0_mcs_consistency', 'R4a_link_mode_unchanged',
                                                        'R4b_e153_reproduced', 'E0_error_model',
                                                        'V_snr_identity', 'F1_fixed_mcs', 'C_coverage'))
    # curves, predictions, reported quantities
    sb = summary['stage_b']
    sb['per_run'] = per_run
    sb['curves'], sb['fits'], sb['required_snr_db'], sb['predictions'], sb['f3_pointwise'] = {}, {}, {}, {}, {}
    for em in st['error_models']:
        m = em['tag']
        for d in DIRS:
            rs = [r for r in per_run.values() if r['model'] == m and r[d]['snr_db'] is not None]
            curve = sorted((r[d]['snr_db'], r[d]['se_used']) for r in rs)
            sb['curves'][f'{m}_{d}'] = curve
            pts = sorted((r[d]['snr_db'], r[d]['se_used']) for r in rs
                         if -10 <= r[d]['snr_db'] <= 35 and r[d]['tbs_window'] >= 50)
            sb['fits'][f'{m}_{d}'] = e153.fit_curve(pts, max(e_ for _, e_ in curve))
            raw_pts = [tuple(p) for p in sum153['curves'][f'{m}_{d}'] if -10 <= p[0] <= 35]
            sb['required_snr_db'][f'{m}_{d}'] = {
                'target_se': e153.F2_TARGET[d],
                'c_amc_empirical': e153.required_snr_empirical(pts, e153.F2_TARGET[d]),
                'raw_e153_empirical': sum153['required_snr_db'][f'{m}_{d}']['empirical'],
                'raw_e153_recomputed': e153.required_snr_empirical(raw_pts, e153.F2_TARGET[d])}
            pm = pmono_violations(curve)
            bl = [(t, r[d]['bler_first_tx'], r[d]['tbs_window']) for t, r in per_run.items()
                  if r['model'] == m and r['fixed_mcs'][d] > 0 and r[d]['bler_first_tx'] is not None]
            first_n = {t: sum(1 for x in window_first_tx(e153.tb_rows(work / 'b' / t, d)[0], window))
                       for t, _, _ in bl}
            pb = [f'{t}: {b:.3f} (n={first_n[t]})' for t, b, _ in bl
                  if first_n[t] >= 50 and b > TARGET + 3 * math.sqrt(0.09 / first_n[t])]
            sb['predictions'][f'{m}_{d}'] = {
                'P_mono': {'violations': pm, 'held': not pm},
                'P_bler': {'violations': pb, 'held': not pb},
                'non_monotone_points': non_monotone_points(curve)}
            sb['f3_pointwise'][f'{m}_{d}'] = {'shift_db': F3_SHIFT_DB, 'c_amc': f3_pointwise(curve),
                                              'raw_e153': f3_pointwise(sum153['curves'][f'{m}_{d}'])}
    passed = h16 and c_amc_valid
    finish(passed, {'H16_confirmed': h16, 'c_amc_valid': c_amc_valid,
                    'primary_mapping': 'c_amc_t2' if passed else None,
                    'rule': conf['decision_rules']['mapping_for_e1_5_5_and_e1c'] if passed
                    else conf['decision_rules']['otherwise']})
    print('E1.5.3a', 'PASSED' if passed else 'FAILED (see validation.json)')
    sys.exit(0 if passed else 1)


if __name__ == '__main__':
    main()
