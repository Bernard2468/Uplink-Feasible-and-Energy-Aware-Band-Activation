#!/usr/bin/env python3
"""E1.5.1: deterministic received power and SNR, 5G-LENA vs the Python link budget.

Implements configs/e1/e1_5_1_deterministic.json. Run inside WSL with the project
venv. For each run (carrier x scheduler) the prescribed losses are written, the
project-owned ns-3 program is executed in its own directory under
~/cmwave-runs/e1_5_1/<tag>, its trace files are copied to <outdir>/raw/<tag>/,
and every TB is compared with the Python reference. Outputs in --outdir:
summary.json, validation.json, manifest.json (raw/ is git-ignored; SHA-256 of
every raw file is in the manifest).
"""
import argparse
import csv
import gzip
import hashlib
import json
import math
import os
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
from cmwave.radio import link_budget as lb  # noqa: E402

PROGRAM = 'e15-link-validation'
RUN_FILES = ('e15_dl_tb.csv', 'e15_ul_tb.csv', 'e15_dl_rxpower.csv', 'e15_rnti_map.csv',
             'e15_rrc_events.csv', 'e15_rb_after_attach.csv', 'e15_run_info.csv', 'losses.csv', 'run.log')


def json_default(o):
    """NumPy scalars in check results (run 1 crashed on numpy.bool_)."""
    if isinstance(o, np.bool_):
        return bool(o)
    if isinstance(o, np.integer):
        return int(o)
    if isinstance(o, np.floating):
        return float(o)
    if isinstance(o, Path):
        return str(o)
    raise TypeError(f'not JSON serializable: {type(o).__name__}')


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def git(dirpath, *args):
    return subprocess.run(['git', '-C', str(dirpath), *args], capture_output=True, text=True,
                          check=True).stdout.strip()


def check_stack(conf, ns3):
    st = conf['stack']
    if git(ns3, 'rev-parse', '--short=8', 'HEAD') != st['ns3_commit']:
        raise SystemExit('ns-3 not at the verified commit')
    if git(ns3 / 'contrib/nr', 'rev-parse', '--short=8', 'HEAD') != st['nr_commit']:
        raise SystemExit('NR not at the verified commit')
    if git(ns3, 'status', '--porcelain', '--untracked-files=no') or \
            git(ns3 / 'contrib/nr', 'status', '--porcelain', '--untracked-files=no'):
        raise SystemExit('tracked files modified in ns-3 or NR')
    repo_src = ROOT / 'ns3/scratch/cmwave' / f'{PROGRAM}.cc'
    inst_src = ns3 / 'scratch/cmwave' / f'{PROGRAM}.cc'
    if sha256(repo_src) != sha256(inst_src):
        raise SystemExit('installed program differs from the repo copy: run scripts/e1_5_install_ns3_program.sh')
    return sha256(repo_src)


def median_losses():
    pdir = ROOT / 'results/phase2/e1_3gpp_propagation'
    man = json.loads((pdir / 'manifest.json').read_text(encoding='utf-8'))
    if not man['all_checks_passed'] or sha256(pdir / 'realizations.csv.gz') != man['realizations_sha256']:
        raise SystemExit('E1.2 propagation not validated or changed')
    out = defaultdict(dict)
    with gzip.open(pdir / 'realizations.csv.gz', 'rt', encoding='utf-8') as fh:
        for r in csv.DictReader(fh):
            out[float(r['frequency_hz'])][r['ue_id']] = float(r['pathloss_db'])
    return out, man['realizations_sha256']


def read_csv(path):
    with open(path, encoding='utf-8') as fh:
        return list(csv.DictReader(fh))


def run_one(conf, run, ns3, losses_by_ue, outdir):
    c = conf['common']
    tag = run['tag']
    rundir = Path.home() / 'cmwave-runs/e1_5_1' / tag
    if rundir.exists():
        shutil.rmtree(rundir)
    rundir.mkdir(parents=True)
    names = list(losses_by_ue)
    with open(rundir / 'losses.csv', 'w', newline='', encoding='utf-8') as fh:
        w = csv.writer(fh, lineterminator='\n')
        w.writerow(['ue_index', 'loss_db', 'name'])
        for i, n in enumerate(names):
            w.writerow([i, repr(losses_by_ue[n]), n])
    args = [f'--frequency={run["frequency_hz"]!r}', f'--bandwidth={c["bandwidth_hz"]!r}',
            f'--numerology={c["numerology"]}', f'--rbOverhead={c["rb_overhead"]!r}',
            f'--gnbTxPower={c["gnb_tx_power_dbm"]!r}', f'--ueTxPower={c["ue_tx_power_dbm"]!r}',
            f'--nfGnb={c["nf_gnb_db"]!r}', f'--nfUe={c["nf_ue_db"]!r}', f'--lossFile={rundir / "losses.csv"}',
            f'--scheduler={run["scheduler"]}', f'--dlRate={c["dl_rate_bps_per_ue"]!r}',
            f'--ulRate={c["ul_rate_bps_per_ue"]!r}', f'--packetSize={c["packet_size_bytes"]}',
            f'--appStart={c["app_start_s"]!r}', f'--simTime={c["sim_time_s"]!r}',
            f'--tddPattern={c["tdd_pattern"]}', f'--enableRlf={int(c["enable_rlf"])}']
    cmd = ['./ns3', 'run', '--no-build', f'--cwd={rundir}', ' '.join([PROGRAM] + args)]
    t0 = time.time()
    p = subprocess.run(cmd, cwd=ns3, capture_output=True, text=True)
    runtime = time.time() - t0
    (rundir / 'run.log').write_text(f'cmd: {cmd}\nexit: {p.returncode}\nruntime_s: {runtime:.1f}\n'
                                    f'--- stdout\n{p.stdout}\n--- stderr\n{p.stderr}\n', encoding='utf-8')
    dst = outdir / 'raw' / tag
    if dst.exists():
        shutil.rmtree(dst)
    dst.mkdir(parents=True)
    hashes = {}
    for f in RUN_FILES:
        if (rundir / f).exists():
            shutil.copy2(rundir / f, dst / f)
            hashes[f] = sha256(dst / f)
    return {'tag': tag, 'exit_code': p.returncode, 'runtime_s': runtime, 'cmd': cmd, 'raw_sha256': hashes,
            'names': names, 'dir': dst}


def analyse(conf, run, meta, losses_by_ue):
    c = conf['common']
    tol = conf['acceptance']['tolerance_db']
    d = meta['dir']
    info = {r['key']: r['value'] for r in read_csv(d / 'e15_run_info.csv')}
    names = meta['names']
    loss = np.array([losses_by_ue[n] for n in names])
    n_prb = 51
    cfg = lb.CarrierConfig(frequency_hz=run['frequency_hz'], channel_bw_hz=c['bandwidth_hz'], scs_hz=c['scs_hz'],
                           n_prb=n_prb, gnb_power_dbm=c['gnb_tx_power_dbm'], ue_power_dbm=c['ue_tx_power_dbm'],
                           nf_gnb_db=c['nf_gnb_db'], nf_ue_db=c['nf_ue_db'], gain_gnb_dbi=0.0, gain_ue_dbi=0.0)
    dl_py = lb.downlink(loss, cfg)['snr_db']
    ul1_py = lb.uplink(loss, cfg)['snr_1prb_db']
    rbpow_py = c['gnb_tx_power_dbm'] - 10 * math.log10(n_prb) - loss
    v = {}
    v['V0_readback'] = {
        'gnb_tx_power_dbm': float(info['gnb_tx_power_dbm']), 'ue_tx_power_dbm': float(info['ue_tx_power_dbm']),
        'gnb_noise_figure_db': float(info['gnb_noise_figure_db']), 'ue_noise_figure_db': float(info['ue_noise_figure_db']),
        'passed': (float(info['gnb_tx_power_dbm']) == c['gnb_tx_power_dbm']
                   and float(info['ue_tx_power_dbm']) == c['ue_tx_power_dbm']
                   and float(info['gnb_noise_figure_db']) == c['nf_gnb_db']
                   and float(info['ue_noise_figure_db']) == c['nf_ue_db'])}
    # Amendment 1: UE RB counts are read after attach (at application start).
    rb_after = read_csv(d / 'e15_rb_after_attach.csv')
    ue_rb = sorted({int(r['ue_rb_num']) for r in rb_after})
    v['V3_rb_count'] = {'gnb': int(info['gnb_rb_num']), 'ue_values_after_attach': ue_rb,
                        'passed': int(info['gnb_rb_num']) == n_prb and ue_rb == [n_prb]
                        and len(rb_after) == len(names)}
    # Amendment 1: UL TBs are attributed by (rnti, time) to the IMSI of the latest
    # RRC connection establishment of that RNTI at or before the TB.
    ue_of_imsi = {int(r['imsi']): int(r['ue_index']) for r in rb_after}
    estab = defaultdict(list)
    for r in read_csv(d / 'e15_rrc_events.csv'):
        estab[int(r['rnti'])].append((int(r['time_ns']), int(r['imsi'])))
    for lst in estab.values():
        lst.sort()

    def ue_for(rnti_, t_ns):
        best = None
        for t, imsi in estab.get(rnti_, ()):
            if t <= t_ns:
                best = imsi
        return ue_of_imsi.get(best) if best is not None else None
    dl = read_csv(d / 'e15_dl_tb.csv')
    ul = read_csv(d / 'e15_ul_tb.csv')
    per_ue = {i: {'name': names[i], 'loss_db': float(loss[i]), 'dl_snr_python_db': float(dl_py[i]),
                  'ul_snr_1prb_python_db': float(ul1_py[i]), 'dl_tb': 0, 'ul_tb': 0,
                  'dl_max_abs_err_db': None, 'ul_max_abs_err_db': None, 'dl_mcs': Counter(), 'ul_mcs': Counter(),
                  'ul_rb': Counter(), 'dl_corrupt': 0, 'ul_corrupt': 0} for i in range(len(names))}
    worst_dl, nonfinite = 0.0, 0
    for r in dl:
        i = int(r['ue_index'])
        s = float(r['sinr_lin'])
        if not s > 0:
            nonfinite += 1
            continue
        err = abs(10 * math.log10(s) - dl_py[i])
        u = per_ue[i]
        u['dl_tb'] += 1
        u['dl_max_abs_err_db'] = max(err, u['dl_max_abs_err_db'] or 0.0)
        u['dl_mcs'][int(r['mcs'])] += 1
        u['dl_corrupt'] += int(r['corrupt'])
        worst_dl = max(worst_dl, err)
    v['V1_dl_snr'] = {'tbs': len(dl), 'nonpositive_sinr_tbs': nonfinite, 'max_abs_error_db': worst_dl,
                      'passed': bool(len(dl) > 0 and nonfinite == 0 and worst_dl <= tol)}
    worst_ul, unmapped, nonfinite = 0.0, 0, 0
    for r in ul:
        i = ue_for(int(r['rnti']), int(r['time_ns']))
        if i is None:
            unmapped += 1
            continue
        s = float(r['sinr_lin'])
        if not s > 0:
            nonfinite += 1
            continue
        n = int(r['rb_assigned'])
        ref = c['ue_tx_power_dbm'] - loss[i] - float(lb.noise_dbm(n * cfg.prb_bw_hz, c['nf_gnb_db']))
        err = abs(10 * math.log10(s) - ref)
        u = per_ue[i]
        u['ul_tb'] += 1
        u['ul_max_abs_err_db'] = max(err, u['ul_max_abs_err_db'] or 0.0)
        u['ul_mcs'][int(r['mcs'])] += 1
        u['ul_rb'][n] += 1
        u['ul_corrupt'] += int(r['corrupt'])
        worst_ul = max(worst_ul, err)
    v['V2_ul_snr'] = {'tbs': len(ul), 'unmapped_rnti_tbs': unmapped, 'nonpositive_sinr_tbs': nonfinite,
                      'max_abs_error_db': worst_ul,
                      'passed': bool(len(ul) > 0 and unmapped == 0 and nonfinite == 0 and worst_ul <= tol)}
    worst4, missing4 = 0.0, []
    for r in read_csv(d / 'e15_dl_rxpower.csv'):
        i = int(r['ue_index'])
        if int(r['n_rb_samples']) == 0:
            missing4.append(i)
            continue
        worst4 = max(worst4, abs(float(r['min_rb_power_dbm']) - rbpow_py[i]),
                     abs(float(r['max_rb_power_dbm']) - rbpow_py[i]))
    need_dl = [i for i in per_ue if dl_py[i] >= -5.0]
    need_ul = [i for i in per_ue if ul1_py[i] >= -5.0]
    v['V4_dl_rx_power'] = {'max_abs_error_db': worst4, 'ues_without_samples': missing4,
                           'passed': bool(worst4 <= tol and not [i for i in missing4 if i in need_dl])}
    short_dl = [per_ue[i]['name'] for i in need_dl if per_ue[i]['dl_tb'] < 10]
    short_ul = [per_ue[i]['name'] for i in need_ul if per_ue[i]['ul_tb'] < 10]
    v['V5_coverage'] = {'dl_required': len(need_dl), 'ul_required': len(need_ul),
                        'dl_below_10_tbs': short_dl, 'ul_below_10_tbs': short_ul,
                        'passed': not short_dl and not short_ul}
    for u in per_ue.values():
        for k in ('dl_mcs', 'ul_mcs', 'ul_rb'):
            u[k] = dict(sorted(u[k].items()))
    return v, per_ue, info


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--config', default=str(ROOT / 'configs/e1/e1_5_1_deterministic.json'))
    ap.add_argument('--outdir', default=str(ROOT / 'results/phase2/e1_5_1_deterministic'))
    ap.add_argument('--ns3-dir', default=str(Path.home() / '6g-external/ns-3-dev'))
    ap.add_argument('--git-head', default=None)
    args = ap.parse_args()
    conf = json.loads(Path(args.config).read_text(encoding='utf-8'))
    ns3 = Path(args.ns3_dir)
    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    prog_sha = check_stack(conf, ns3)
    med, real_sha = median_losses()
    sweep = conf['ues']['sweep_losses_db']

    validation, summary, runs_meta = {}, {}, []
    for run in conf['runs']:
        f = run['frequency_hz']
        losses_by_ue = dict(sorted(med[f].items()))
        losses_by_ue.update({f'sweep_{int(x)}dB': float(x) for x in sweep})
        if len(losses_by_ue) != conf['ues']['total_per_run']:
            raise SystemExit('unexpected UE count')
        meta = run_one(conf, run, ns3, losses_by_ue, outdir)
        runs_meta.append({k: meta[k] for k in ('tag', 'exit_code', 'runtime_s', 'cmd', 'raw_sha256')})
        print(f"{run['tag']}: exit {meta['exit_code']}, {meta['runtime_s']:.0f} s", flush=True)
        if meta['exit_code'] != 0:
            validation[run['tag']] = {'run_failed': {'exit_code': meta['exit_code'], 'passed': False}}
            continue
        v, per_ue, info = analyse(conf, run, meta, losses_by_ue)
        validation[run['tag']] = v
        summary[run['tag']] = {'run_info': info, 'per_ue': per_ue}
        print('   ' + ', '.join(f"{k} {'PASS' if x['passed'] else 'FAIL'}" for k, x in v.items()), flush=True)
        print(f"   DL max err {v['V1_dl_snr']['max_abs_error_db']:.2e} dB over {v['V1_dl_snr']['tbs']} TBs; "
              f"UL max err {v['V2_ul_snr']['max_abs_error_db']:.2e} dB over {v['V2_ul_snr']['tbs']} TBs", flush=True)
    passed = all(x['passed'] for v in validation.values() for x in v.values())
    (outdir / 'validation.json').write_text(json.dumps({'per_run': validation, 'all_checks_passed': passed}, default=json_default,
                                                       indent=2), encoding='utf-8')
    (outdir / 'summary.json').write_text(json.dumps(summary, indent=2, default=json_default), encoding='utf-8')
    manifest = {'generated_utc': datetime.now(timezone.utc).isoformat(), 'tool': 'scripts/e1_5_1_deterministic.py',
                'git_head_at_run': args.git_head, 'python': platform.python_version(), 'numpy': np.__version__,
                'config_sha256': sha256(args.config), 'program_sha256': prog_sha,
                'ns3_commit': conf['stack']['ns3_commit'], 'nr_commit': conf['stack']['nr_commit'],
                'e1_2_realizations_sha256': real_sha, 'runs': runs_meta, 'all_checks_passed': passed}
    (outdir / 'manifest.json').write_text(json.dumps(manifest, indent=2, default=json_default), encoding='utf-8')
    print('E1.5.1 DETERMINISTIC', 'PASSED' if passed else 'FAILED')
    sys.exit(0 if passed else 1)


if __name__ == '__main__':
    main()
