#!/usr/bin/env python3
"""E1.5.3: single-UE NR link mapping, 5G-LENA SNR -> SE per used resource vs TR 36.942.

Implements configs/e1/e1_5_3_link_mapping.json. Run inside WSL with the project venv.
Outputs in --outdir: validation.json, summary.json (curves, fits), manifest.json;
raw files in raw/ (git-ignored).
"""
import argparse
import json
import math
import platform
import shutil
import subprocess
import sys
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'scripts'))
from cmwave.radio import link_budget as lb  # noqa: E402
import e1_5_1_deterministic as e151  # noqa: E402
import e1_5_1d_tdma_diagnostic as diag  # noqa: E402

CONFIG = ROOT / 'configs/e1/e1_5_3_link_mapping.json'
E151_CONFIG = ROOT / 'configs/e1/e1_5_1_deterministic.json'
E1520_RESULTS = ROOT / 'results/phase2/e1_5_2_0_conventions'
OUTDIR = ROOT / 'results/phase2/e1_5_3_link_mapping'
SCS = 30e3
T_SYM = 0.5e-3 / 14
TOL_DB = 0.01
FILES = ('losses.csv', 'e15_dl_tb.csv', 'e15_ul_tb.csv', 'e15_run_info.csv', 'e153_error_model.csv', 'run.log')
F2_TARGET = {'DL': 2.25, 'UL': 1.5}


# ---------------------------------------------------------------- per run
def tb_rows(rundir, direction):
    f, key = ('e15_dl_tb.csv', e151.DL_TB_KEY) if direction == 'DL' else ('e15_ul_tb.csv', e151.UL_TB_KEY)
    tbs, rows = e151.unique_tbs(e151.read_csv(rundir / f), key)
    return tbs, rows


def run_metrics(tbs, window, snr_ref_fn):
    """SE per used resource and the SINR identity over the TBs of one direction."""
    lo, hi = int(window[0] * 1e9), int(window[1] * 1e9)
    win = [r for r in tbs if lo <= int(r['time_ns']) < hi]
    bits = sum(8 * int(r['tb_size']) for r in win if r['corrupt'] == '0')
    res = sum(int(r['rb_assigned']) * 12 * SCS * int(r['num_sym']) * T_SYM for r in win)
    first = [r for r in win if r['rv'] == '0']
    worst = 0.0
    snrs = []
    for r in tbs:
        ref = snr_ref_fn(int(r['rb_assigned']))
        snrs.append(ref)
        worst = max(worst, abs(10 * math.log10(float(r['sinr_lin'])) - ref))
    win_s = window[1] - window[0]
    return {'tbs_window': len(win), 'tbs_all': len(tbs), 'se_used': bits / res if res > 0 else 0.0,
            'decoded_bits': bits, 'resource_hz_s': res,
            'se_incl_tdd_ctrl': bits / (51 * 12 * SCS * win_s),
            'bler_first_tx': (sum(r['corrupt'] == '1' for r in first) / len(first)) if first else None,
            'retx_share': (sum(r['rv'] != '0' for r in win) / len(win)) if win else None,
            'mcs_hist': dict(sorted(Counter(int(r['mcs']) for r in win).items())),
            'max_mcs_all': max((int(r['mcs']) for r in tbs), default=None),
            'snr_db': float(np.median(snrs)) if snrs else None, 'max_abs_sinr_err_db': worst}


# ---------------------------------------------------------------- fits
def tr36942(snr_db, alpha, thr_max, snr_min_db):
    s = np.asarray(snr_db, dtype=float)
    se = np.minimum(alpha * np.log2(1 + 10 ** (s / 10)), thr_max)
    return np.where(s >= snr_min_db, se, 0.0)


def fit_curve(points, thr_max):
    """points: list of (snr_db, se) fit points; thr_max: max SE over ALL runs (pre-registered).
    TR 36.942 form (alpha, Thr_max, SNR_min) and the Shannon-gap form."""
    pts = sorted(points)
    s = np.array([p[0] for p in pts])
    se = np.array([p[1] for p in pts])
    thr_max = float(thr_max)
    m = (se > 0) & (se <= 0.9 * thr_max)
    c = np.log2(1 + 10 ** (s[m] / 10))
    alpha = float(np.sum(se[m] * c) / np.sum(c * c)) if m.any() else None
    pos = s[se > 0]
    snr_min = float(pos.min()) if pos.size else None
    out = {'n_points': int(len(pts)), 'n_fit_points': int(m.sum()), 'thr_max': thr_max, 'alpha': alpha,
           'snr_min_db': snr_min}
    if alpha is not None:
        fit = tr36942(s[m], alpha, thr_max, snr_min)
        out['max_abs_err'] = float(np.max(np.abs(se[m] - fit)))
        out['rms_err'] = float(np.sqrt(np.mean((se[m] - fit) ** 2)))
        gammas = np.arange(0.0, 15.0001, 0.01)
        sse = [np.sum((se[m] - np.log2(1 + 10 ** ((s[m] - gm) / 10))) ** 2) for gm in gammas]
        g = float(gammas[int(np.argmin(sse))])
        out['gap_db'] = g
        out['gap_max_abs_err'] = float(np.max(np.abs(se[m] - np.log2(1 + 10 ** ((s[m] - g) / 10)))))
    return out


def required_snr_tr(target, alpha, thr_max, snr_min):
    """SNR [dB] at which alpha log2(1+snr) reaches target (None if above Thr_max)."""
    if alpha is None or target > thr_max:
        return None
    return max(10 * math.log10(2 ** (target / alpha) - 1), snr_min)


def required_snr_empirical(points, target):
    """Lowest SNR at which the piecewise-linear empirical curve reaches target."""
    pts = sorted(points)
    for (s0, e0), (s1, e1) in zip(pts, pts[1:]):
        if e0 >= target:
            return s0
        if e0 < target <= e1:
            return s0 + (target - e0) / (e1 - e0) * (s1 - s0)
    return None


# ---------------------------------------------------------------- main
def r3_check(outdir, git_head):
    r3 = outdir / 'r3_e1520_rerun'
    p = subprocess.run([sys.executable, str(ROOT / 'scripts/e1_5_2_0_conventions.py'), '--outdir', str(r3),
                        '--git-head', str(git_head)], capture_output=True, text=True)
    (outdir / 'r3_stdout.txt').write_text(p.stdout + '\n--- stderr\n' + p.stderr, encoding='utf-8')
    ref = {r['tag']: r for r in json.loads((E1520_RESULTS / 'manifest.json').read_text())['runs']}
    new = {r['tag']: r for r in json.loads((r3 / 'manifest.json').read_text())['runs']} \
        if (r3 / 'manifest.json').exists() else {}
    differ, n = [], 0
    for tag in sorted(set(ref) | set(new)):
        ha, hb = ref.get(tag, {}).get('raw_sha256', {}), new.get(tag, {}).get('raw_sha256', {})
        for f in sorted(set(ha) | set(hb)):
            if f == 'run.log':
                continue
            n += 1
            if ha.get(f) != hb.get(f):
                differ.append(f'{tag}/{f}')
    val = json.loads((r3 / 'validation.json').read_text()) if (r3 / 'validation.json').exists() else {}
    ok = p.returncode == 0 and len(new) == 21 and n > 0 and not differ and bool(val.get('all_checks_passed'))
    return {'exit_code': p.returncode, 'runs': len(new), 'files_compared': n, 'differing_files': differ,
            'passed': bool(ok)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--config', default=str(CONFIG))
    ap.add_argument('--outdir', default=str(OUTDIR))
    ap.add_argument('--ns3-dir', default=str(Path.home() / '6g-external/ns-3-dev'))
    ap.add_argument('--git-head', default=None)
    args = ap.parse_args()
    conf = json.loads(Path(args.config).read_text(encoding='utf-8'))
    c151 = json.loads(E151_CONFIG.read_text(encoding='utf-8'))
    c = c151['common']
    st = conf['setup']
    ns3 = Path(args.ns3_dir)
    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    prog_sha = e151.check_stack(c151, ns3)
    t0 = time.time()
    validation = {'R3_link_mode_unchanged': r3_check(outdir, args.git_head)}
    print('R3', 'PASS' if validation['R3_link_mode_unchanged']['passed'] else 'FAIL', flush=True)
    if not validation['R3_link_mode_unchanged']['passed']:
        (outdir / 'validation.json').write_text(json.dumps({'checks': validation, 'all_checks_passed': False},
                                                           indent=2, default=e151.json_default), encoding='utf-8')
        raise SystemExit('R3 failed: stop')
    cfg = diag.carrier_cfg(c, st['carrier_hz'])
    g = st['loss_grid_db']
    losses = [float(x) for x in np.arange(g['start'], g['stop'] + g['step'] / 2, g['step'])]
    work = Path.home() / 'cmwave-runs/e1_5_3'
    runs_meta, per_run = [], {}
    e0_fail, v_fail, m_info = [], [], {}
    for em in st['error_models']:
        for L in losses:
            tag = f"{em['tag']}_L{L:.0f}"
            rd = work / tag
            shutil.rmtree(rd, ignore_errors=True)
            rd.mkdir(parents=True)
            diag.write_losses(rd, {'u0': L})
            a = [f'--frequency={st["carrier_hz"]!r}', f'--bandwidth={c["bandwidth_hz"]!r}',
                 f'--numerology={c["numerology"]}', f'--rbOverhead={c["rb_overhead"]!r}',
                 f'--gnbTxPower={c["gnb_tx_power_dbm"]!r}', f'--ueTxPower={c["ue_tx_power_dbm"]!r}',
                 f'--nfGnb={c["nf_gnb_db"]!r}', f'--nfUe={c["nf_ue_db"]!r}', f'--lossFile={rd / "losses.csv"}',
                 f'--scheduler={st["scheduler"]}', '--dlRate=300000000.0', '--ulRate=100000000.0',
                 '--packetSize=1000', '--ulPacketSize=1000', f'--appStart={st["app_start_s"]!r}',
                 f'--simTime={st["sim_time_s"]!r}', f'--tddPattern={c["tdd_pattern"]}', '--enableRlf=0',
                 f'--errorModel={em["type"]}']
            cmd = ['./ns3', 'run', '--no-build', f'--cwd={rd}', ' '.join([e151.PROGRAM] + a)]
            t1 = time.time()
            p = subprocess.run(cmd, cwd=ns3, capture_output=True, text=True)
            rt = time.time() - t1
            (rd / 'run.log').write_text(f'cmd: {cmd}\nexit: {p.returncode}\nruntime_s: {rt:.1f}\n--- stdout\n'
                                        f'{p.stdout}\n--- stderr\n{p.stderr}\n', encoding='utf-8')
            h = {}
            dst = outdir / 'raw' / tag
            shutil.rmtree(dst, ignore_errors=True)
            dst.mkdir(parents=True)
            for f in FILES:
                if (rd / f).exists():
                    shutil.copy2(rd / f, dst / f)
                    h[f] = e151.sha256(dst / f)
            runs_meta.append({'tag': tag, 'cmd': cmd, 'exit_code': p.returncode, 'runtime_s': rt, 'raw_sha256': h})
            print(f'{tag}: exit {p.returncode}, {rt:.0f} s', flush=True)
            if p.returncode != 0:
                raise SystemExit(f'{tag} failed; see {rd / "run.log"}')
            info = {r['key']: r['value'] for r in e151.read_csv(rd / 'e15_run_info.csv')}
            inst = e151.read_csv(rd / 'e153_error_model.csv')
            types = [info['error_model_gnb_dl_amc'], info['error_model_gnb_ul_amc']] + \
                [r['error_model_instance'] for r in inst]
            if any(t != em['type'] for t in types):
                e0_fail.append(tag)
            res = {'loss_db': L, 'model': em['tag']}
            dl_ref = float(lb.downlink(np.array([L]), cfg)['snr_db'][0])
            for d, fn in (('DL', lambda n: dl_ref),
                          ('UL', lambda n: c['ue_tx_power_dbm'] - L - float(lb.noise_dbm(n * cfg.prb_bw_hz,
                                                                                         c['nf_gnb_db'])))):
                tbs, _ = tb_rows(rd, d)
                mtr = run_metrics(tbs, st['window_s'], fn)
                res[d] = mtr
                if mtr['max_abs_sinr_err_db'] > TOL_DB:
                    v_fail.append(f'{tag}/{d}')
            per_run[tag] = res
    # checks
    validation['E0_error_model'] = {'failed_runs': e0_fail, 'passed': not e0_fail}
    validation['V_snr_identity'] = {'failed': v_fail,
                                    'max_abs_err_db': max(max(r['DL']['max_abs_sinr_err_db'], r['UL']['max_abs_sinr_err_db'])
                                                          for r in per_run.values()),
                                    'passed': not v_fail}
    mres = {}
    for em in st['error_models']:
        top = 27 if em['tag'] == 't2' else 28
        rs = [r for r in per_run.values() if r['model'] == em['tag']]
        maxmcs = max(max(r['DL']['max_mcs_all'] or 0, r['UL']['max_mcs_all'] or 0) for r in rs)
        low = min(rs, key=lambda r: r['loss_db'])['DL']
        share = low['mcs_hist'].get(top, 0) / max(1, low['tbs_window'])
        mres[em['tag']] = {'max_mcs': maxmcs, 'top_mcs_share_lowest_loss_dl': share,
                           'passed': maxmcs <= top and share >= 0.9}
    validation['M_mcs_table'] = dict(mres, passed=all(x['passed'] for x in mres.values()))
    cov = [f'{t}/{d}' for t, r in per_run.items() for d in ('DL', 'UL')
           if r[d]['snr_db'] is not None and -10 <= r[d]['snr_db'] <= 35 and r[d]['tbs_window'] < 50]
    validation['C_coverage'] = {'failed': cov, 'passed': not cov}
    # curves and fits
    summary = {'per_run': per_run, 'curves': {}, 'fits': {}, 'required_snr_db': {}, 'tr36942': lb.TRUNCATED_SHANNON}
    for em in st['error_models']:
        for d in ('DL', 'UL'):
            rs = [r for r in per_run.values() if r['model'] == em['tag'] and r[d]['snr_db'] is not None]
            curve = sorted((r[d]['snr_db'], r[d]['se_used']) for r in rs)
            summary['curves'][f'{em["tag"]}_{d}'] = curve
            pts = sorted((r[d]['snr_db'], r[d]['se_used']) for r in rs
                         if -10 <= r[d]['snr_db'] <= 35 and r[d]['tbs_window'] >= 50)
            fit = fit_curve(pts, max(e_ for _, e_ in curve))
            summary['fits'][f'{em["tag"]}_{d}'] = fit
            tr = lb.TRUNCATED_SHANNON[d]
            tgt = F2_TARGET[d]
            summary['required_snr_db'][f'{em["tag"]}_{d}'] = {
                'target_se': tgt,
                'tr36942': required_snr_tr(tgt, tr['alpha'], tr['thr_max'], tr['snr_min_db']),
                'fitted_form': required_snr_tr(tgt, fit['alpha'], fit['thr_max'], fit['snr_min_db'] or -99),
                'empirical': required_snr_empirical(pts, tgt)}
    prim = {}
    for d in ('DL', 'UL'):
        f = summary['fits'][f't2_{d}']
        use_fit = f.get('max_abs_err') is not None and f['max_abs_err'] <= 0.3
        prim[d] = {'form': 'tr36942_fitted' if use_fit else 'empirical_t2', 'alpha': f['alpha'],
                   'thr_max': f['thr_max'], 'snr_min_db': f['snr_min_db'], 'fit_max_abs_err': f.get('max_abs_err')}
    summary['primary_mapping'] = prim
    passed = all(v['passed'] for v in validation.values())
    (outdir / 'validation.json').write_text(json.dumps({'checks': validation, 'all_checks_passed': passed},
                                                       indent=2, default=e151.json_default), encoding='utf-8')
    (outdir / 'summary.json').write_text(json.dumps(summary, indent=2, default=e151.json_default), encoding='utf-8')
    manifest = {'generated_utc': datetime.now(timezone.utc).isoformat(), 'tool': 'scripts/e1_5_3_link_mapping.py',
                'git_head_at_run': args.git_head, 'python': platform.python_version(), 'numpy': np.__version__,
                'config_sha256': e151.sha256(args.config), 'program_sha256': prog_sha,
                'ns3_commit': c151['stack']['ns3_commit'], 'nr_commit': c151['stack']['nr_commit'],
                'wall_time_s': time.time() - t0, 'runs': runs_meta, 'all_checks_passed': passed}
    (outdir / 'manifest.json').write_text(json.dumps(manifest, indent=2, default=e151.json_default), encoding='utf-8')
    for k, v in validation.items():
        print(k, 'PASS' if v['passed'] else 'FAIL', flush=True)
    print('E1.5.3', 'PASSED' if passed else 'FAILED (see validation.json)')
    sys.exit(0 if passed else 1)


if __name__ == '__main__':
    main()
