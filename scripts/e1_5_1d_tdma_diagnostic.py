#!/usr/bin/env python3
"""E1.5.1-D: cause of the UL starvation under NrMacSchedulerTdmaRR in E1.5.1 run 4.

Implements configs/e1/e1_5_1d_tdma_diagnostic.json. Run inside WSL with the project
venv. Uses the unchanged E1.5.1 program. Two runs replicate E1.5.1 run 4 with the
NR scheduler log enabled (stored gzipped in <outdir>/raw/<tag>/, git-ignored);
four runs are counterfactuals (TDMA-feasible UEs only; proportional fair).
Outputs in --outdir: validation.json, summary.json, manifest.json.
"""
import argparse
import csv
import gzip
import json
import math
import os
import platform
import re
import shutil
import subprocess
import sys
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'scripts'))
from cmwave.radio import link_budget as lb  # noqa: E402
import e1_5_1_deterministic as e151  # noqa: E402

E151_CONFIG = ROOT / 'configs/e1/e1_5_1_deterministic.json'
E151_RESULTS = ROOT / 'results/phase2/e1_5_1_deterministic'
NS_LOG = ('NrMacSchedulerTdma=debug|info|prefix_time|prefix_func:'
          'NrMacSchedulerNs3=debug|info|prefix_time|prefix_func')
DATA_FILES = ('e15_dl_tb.csv', 'e15_ul_tb.csv', 'e15_dl_rxpower.csv', 'e15_rnti_map.csv', 'e15_rrc_events.csv',
              'e15_rb_after_attach.csv', 'e15_run_info.csv', 'losses.csv')
N_PRB = 51

# ---------------------------------------------------------------- scheduler log
RE_TIME = re.compile(r'^\+?(\d+(?:\.\d+)?)s\s')
RE_SCHED_UL = re.compile(r'DoScheduleUl\(\): Scheduling UL ')
RE_LCG = re.compile(r'ComputeActiveUe\(\): UE (\d+) UL LCG (\d+) bytes (\d+)')
RE_ASSIGNING = re.compile(r'AssignRBGTDMA\(\): Assigning RBG in (UL|DL), # beams active flows: (\d+), # sym: (\d+)')
RE_ASSIGNED = re.compile(r'AssignRBGTDMA\(\): Assigned \d+ (UL|DL) RBG \(= 1 SYM\) to UE (\d+) ')
RE_ALL_COVERED = re.compile(r'AssignRBGTDMA\(\): All the UE already have their resources allocated')
RE_CREATING = re.compile(r'DoCschedUeConfigReq\(\): Creating user, .* and ue (\d+) ')
RE_OWING = re.compile(r'DoScheduleUlSr\(\): Owing a \d+ byte bootstrap grant to UE (\d+) ')


def parse_scheduler_log(lines):
    """Return UL scheduling events, UE insertion times and bootstrap-owed events.

    Each UL scheduling event: {'t', 'lcg': {rnti: bytes}, 'owed': set(rnti),
    'data': None or {'sym_avail', 'assigned': [rnti per symbol], 'all_covered'}}.
    'owed' holds the RNTIs owed a bootstrap grant during the event: those owed and not
    yet given a UL symbol when the event starts, plus those added by the event's own
    scheduling-request processing (DoScheduleUlSr runs inside DoScheduleUl).
    """
    events, creations = [], []
    owed = set()
    cur = None
    mode = None
    for line in lines:
        m = RE_TIME.match(line)
        if not m:
            continue
        t = float(m.group(1))
        if RE_SCHED_UL.search(line):
            cur = {'t': t, 'lcg': {}, 'owed': set(owed), 'data': None}
            events.append(cur)
            continue
        mm = RE_CREATING.search(line)
        if mm:
            creations.append((t, int(mm.group(1))))
            continue
        mm = RE_OWING.search(line)
        if mm:
            r = int(mm.group(1))
            owed.add(r)
            if cur is not None:
                cur['owed'].add(r)
            continue
        mm = RE_LCG.search(line)
        if mm and cur is not None:
            r = int(mm.group(1))
            cur['lcg'][r] = cur['lcg'].get(r, 0) + int(mm.group(3))
            continue
        mm = RE_ASSIGNING.search(line)
        if mm:
            mode = mm.group(1)
            if mode == 'UL':
                if cur is None or cur['data'] is not None:
                    raise ValueError(f'UL data assignment outside a UL scheduling event at t={t}')
                cur['data'] = {'sym_avail': int(mm.group(3)), 'assigned': [], 'all_covered': False}
            continue
        mm = RE_ASSIGNED.search(line)
        if mm:
            if mm.group(1) != mode:
                raise ValueError(f'assignment direction {mm.group(1)} inside a {mode} loop at t={t}')
            if mode == 'UL':
                r = int(mm.group(2))
                cur['data']['assigned'].append(r)
                owed.discard(r)
            continue
        if RE_ALL_COVERED.search(line) and mode == 'UL':
            cur['data']['all_covered'] = True
    return events, creations


def first_assignment_order(assigned):
    seen, order = set(), []
    for r in assigned:
        if r not in seen:
            seen.add(r)
            order.append(r)
    return order


def find_cycle(edges):
    """Return a list of nodes forming a directed cycle, or None (iterative DFS)."""
    graph = defaultdict(set)
    for a, b in edges:
        graph[a].add(b)
    colour = {}
    for start in list(graph):
        if colour.get(start):
            continue
        stack = [(start, iter(sorted(graph[start])))]
        path = [start]
        colour[start] = 1
        while stack:
            node, it = stack[-1]
            nxt = next(it, None)
            if nxt is None:
                colour[node] = 2
                stack.pop()
                path.pop()
                continue
            c = colour.get(nxt, 0)
            if c == 1:
                return path[path.index(nxt):] + [nxt]
            if c == 0:
                colour[nxt] = 1
                stack.append((nxt, iter(sorted(graph[nxt]))))
                path.append(nxt)
    return None


def order_check(events, creations, t0, t1):
    """P2: within each epoch the first-assignment orders admit one total order."""
    bounds = sorted({t for t, _ in creations if t0 <= t < t1})
    edges_by_epoch = defaultdict(set)
    n_events = defaultdict(int)
    for e in events:
        if e['data'] is None or not (t0 <= e['t'] < t1):
            continue
        epoch = sum(1 for b in bounds if b <= e['t'])
        order = first_assignment_order(e['data']['assigned'])
        n_events[epoch] += 1
        edges_by_epoch[epoch].update(zip(order, order[1:]))
    result = []
    for epoch in sorted(n_events):
        cyc = find_cycle(edges_by_epoch[epoch])
        result.append({'epoch': epoch, 'data_events': n_events[epoch], 'precedence_pairs': len(edges_by_epoch[epoch]),
                       'cycle': cyc})
    return {'epoch_bounds_s': bounds, 'epochs': result,
            'passed': bool(result) and all(r['cycle'] is None for r in result)}


def global_order(events, t0, t1):
    """Topological order of the steady-state precedence graph (reported, not tested)."""
    edges, nodes = set(), set()
    for e in events:
        if e['data'] is None or not (t0 <= e['t'] < t1):
            continue
        order = first_assignment_order(e['data']['assigned'])
        nodes.update(order)
        edges.update(zip(order, order[1:]))
    indeg = {n: 0 for n in nodes}
    succ = defaultdict(set)
    for a, b in edges:
        if b not in succ[a]:
            succ[a].add(b)
            indeg[b] += 1
    ready = sorted(n for n in nodes if indeg[n] == 0)
    out = []
    while ready:
        n = ready.pop(0)
        out.append(n)
        for m in sorted(succ[n]):
            indeg[m] -= 1
            if indeg[m] == 0:
                ready.append(m)
        ready.sort()
    return out if len(out) == len(nodes) else None


def saturation(events, t0, t1):
    """P3: share of steady-state UL data events in which every available symbol is assigned."""
    data = [e['data'] for e in events if e['data'] is not None and t0 <= e['t'] < t1]
    full = sum(1 for d in data if len(d['assigned']) == d['sym_avail'])
    return {'data_events': len(data), 'all_symbols_assigned': full, 'share': full / len(data) if data else None}


def backlog_and_service(events, rnti, t0, t1):
    """P4 inputs for one RNTI: share of UL scheduling events backlogged, share of data events served."""
    sched = [e for e in events if t0 <= e['t'] < t1]
    data = [e for e in sched if e['data'] is not None]
    backlogged = sum(1 for e in sched if e['lcg'].get(rnti, 0) > 0 or rnti in e['owed'])
    served = sum(1 for e in data if rnti in e['data']['assigned'])
    return {'ul_scheduling_events': len(sched), 'backlogged': backlogged,
            'backlogged_share': backlogged / len(sched) if sched else None,
            'ul_data_events': len(data), 'served': served, 'served_share': served / len(data) if data else None}


# ---------------------------------------------------------------- TB traces
def attribution(rundir):
    rb_after = e151.read_csv(rundir / 'e15_rb_after_attach.csv')
    ue_of_imsi = {int(r['imsi']): int(r['ue_index']) for r in rb_after}
    estab = defaultdict(list)
    for r in e151.read_csv(rundir / 'e15_rrc_events.csv'):
        estab[int(r['rnti'])].append((int(r['time_ns']), int(r['imsi'])))
    for v in estab.values():
        v.sort()

    def ue_for(rnti, t_ns):
        best = None
        for t, imsi in estab.get(rnti, ()):
            if t <= t_ns:
                best = imsi
        return ue_of_imsi.get(best) if best is not None else None
    return ue_for, {int(r['ue_index']): int(r['rnti']) for r in rb_after}


def ul_tb_stats(rundir, n_ue, t0, t1, width):
    """Per UE: distinct UL TBs, corrupt TBs, retransmissions, symbols, served windows."""
    ue_for, rnti_after_attach = attribution(rundir)
    tbs, rows = e151.unique_tbs(e151.read_csv(rundir / 'e15_ul_tb.csv'), e151.UL_TB_KEY)
    n_win = int(round((t1 - t0) / width))
    per = {i: {'ul_tb': 0, 'corrupt': 0, 'retx': 0, 'symbols': 0, 'windows': set()} for i in range(n_ue)}
    unmapped, sym_total, sym_retx = 0, 0, 0
    for r in tbs:
        t_ns = int(r['time_ns'])
        i = ue_for(int(r['rnti']), t_ns)
        nsym = int(r['num_sym'])
        sym_total += nsym
        if int(r['rv']) > 0:
            sym_retx += nsym
        if i is None:
            unmapped += 1
            continue
        u = per[i]
        u['ul_tb'] += 1
        u['symbols'] += nsym
        u['corrupt'] += int(r['corrupt'])
        u['retx'] += int(int(r['rv']) > 0)
        t = t_ns * 1e-9
        if not int(r['corrupt']) and t0 <= t < t1:
            u['windows'].add(min(int((t - t0) / width), n_win - 1))
    for u in per.values():
        u['served_windows'] = len(u.pop('windows'))
    return per, {'distinct_ul_tbs': len(tbs), 'trace_rows': rows, 'unmapped_tbs': unmapped,
                 'ul_symbols': sym_total, 'ul_symbols_retx': sym_retx, 'n_windows': n_win,
                 'rnti_after_attach': rnti_after_attach}


# ---------------------------------------------------------------- runs
def carrier_cfg(c, f):
    return lb.CarrierConfig(frequency_hz=f, channel_bw_hz=c['bandwidth_hz'], scs_hz=c['scs_hz'], n_prb=N_PRB,
                            gnb_power_dbm=c['gnb_tx_power_dbm'], ue_power_dbm=c['ue_tx_power_dbm'],
                            nf_gnb_db=c['nf_gnb_db'], nf_ue_db=c['nf_ue_db'], gain_gnb_dbi=0.0, gain_ue_dbi=0.0)


def build_cmd(c, run, rundir):
    """Identical argument construction to e1_5_1_deterministic.run_one."""
    args = [f'--frequency={run["frequency_hz"]!r}', f'--bandwidth={c["bandwidth_hz"]!r}',
            f'--numerology={c["numerology"]}', f'--rbOverhead={c["rb_overhead"]!r}',
            f'--gnbTxPower={c["gnb_tx_power_dbm"]!r}', f'--ueTxPower={c["ue_tx_power_dbm"]!r}',
            f'--nfGnb={c["nf_gnb_db"]!r}', f'--nfUe={c["nf_ue_db"]!r}', f'--lossFile={rundir / "losses.csv"}',
            f'--scheduler={run["scheduler"]}', f'--dlRate={c["dl_rate_bps_per_ue"]!r}',
            f'--ulRate={c["ul_rate_bps_per_ue"]!r}', f'--packetSize={c["packet_size_bytes"]}',
            f'--ulPacketSize={c["ul_packet_size_bytes"]}',
            f'--appStart={c["app_start_s"]!r}', f'--simTime={c["sim_time_s"]!r}',
            f'--tddPattern={c["tdd_pattern"]}', f'--enableRlf={int(c["enable_rlf"])}']
    return ['./ns3', 'run', '--no-build', f'--cwd={rundir}', ' '.join([e151.PROGRAM] + args)]


def write_losses(rundir, losses_by_ue):
    with open(rundir / 'losses.csv', 'w', newline='', encoding='utf-8') as fh:
        w = csv.writer(fh, lineterminator='\n')
        w.writerow(['ue_index', 'loss_db', 'name'])
        for i, n in enumerate(losses_by_ue):
            w.writerow([i, repr(losses_by_ue[n]), n])


def execute(c, run, ns3, losses_by_ue, outdir):
    rundir = Path.home() / 'cmwave-runs/e1_5_1d' / run['tag']
    if rundir.exists():
        shutil.rmtree(rundir)
    rundir.mkdir(parents=True)
    write_losses(rundir, losses_by_ue)
    cmd = build_cmd(c, run, rundir)
    env = dict(os.environ)
    env.pop('NS_LOG', None)
    if run['scheduler_log']:
        env['NS_LOG'] = NS_LOG
    t0 = time.time()
    with open(rundir / 'stdout.txt', 'w') as out, open(rundir / 'stderr.log', 'w') as err:
        p = subprocess.run(cmd, cwd=ns3, env=env, stdout=out, stderr=err)
    runtime = time.time() - t0
    (rundir / 'run.log').write_text(f'cmd: {cmd}\nexit: {p.returncode}\nruntime_s: {runtime:.1f}\n'
                                    f'NS_LOG: {env.get("NS_LOG", "")}\n', encoding='utf-8')
    dst = outdir / 'raw' / run['tag']
    if dst.exists():
        shutil.rmtree(dst)
    dst.mkdir(parents=True)
    hashes = {}
    for f in DATA_FILES + ('run.log', 'stdout.txt'):
        if (rundir / f).exists():
            shutil.copy2(rundir / f, dst / f)
            hashes[f] = e151.sha256(dst / f)
    if run['scheduler_log']:
        with open(rundir / 'stderr.log', 'rb') as src, open(dst / 'scheduler_log.txt.gz', 'wb') as fh:
            with gzip.GzipFile(filename='', fileobj=fh, mode='wb', mtime=0) as gz:
                shutil.copyfileobj(src, gz)
        hashes['scheduler_log.txt.gz'] = e151.sha256(dst / 'scheduler_log.txt.gz')
    elif (rundir / 'stderr.log').exists():
        shutil.copy2(rundir / 'stderr.log', dst / 'stderr.log')
        hashes['stderr.log'] = e151.sha256(dst / 'stderr.log')
    return {'tag': run['tag'], 'exit_code': p.returncode, 'runtime_s': runtime, 'cmd': cmd, 'raw_sha256': hashes,
            'dir': dst, 'rundir': rundir}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--config', default=str(ROOT / 'configs/e1/e1_5_1d_tdma_diagnostic.json'))
    ap.add_argument('--outdir', default=str(ROOT / 'results/phase2/e1_5_1d_tdma_diagnostic'))
    ap.add_argument('--ns3-dir', default=str(Path.home() / '6g-external/ns-3-dev'))
    ap.add_argument('--git-head', default=None)
    args = ap.parse_args()
    conf = json.loads(Path(args.config).read_text(encoding='utf-8'))
    c151 = json.loads(E151_CONFIG.read_text(encoding='utf-8'))
    c = c151['common']
    ns3 = Path(args.ns3_dir)
    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    prog_sha = e151.check_stack(c151, ns3)
    ref_manifest = json.loads((E151_RESULTS / 'manifest.json').read_text(encoding='utf-8'))
    if prog_sha != ref_manifest['program_sha256']:
        raise SystemExit('program differs from the one used in E1.5.1 run 4')
    ref_runs = {r['tag']: r for r in ref_manifest['runs']}
    ref_val = json.loads((E151_RESULTS / 'validation.json').read_text(encoding='utf-8'))['per_run']
    med, real_sha = e151.median_losses()
    t0, t1 = conf['definitions']['steady_state_s']
    width = 0.1

    validation, summary, runs_meta = {}, {}, []
    for run in conf['runs']:
        f = run['frequency_hz']
        all_ues = dict(sorted(med[f].items()))
        all_ues.update({f'sweep_{int(x)}dB': float(x) for x in c151['ues']['sweep_losses_db']})
        cfg = carrier_cfg(c, f)
        names_all = list(all_ues)
        snr_full = lb.uplink(np.array([all_ues[n] for n in names_all]), cfg)['snr_full_db']
        feasible = {n: float(s) for n, s in zip(names_all, snr_full) if s >= -5.0}
        losses_by_ue = all_ues if run['ues'] == 'all_36' else {n: all_ues[n] for n in names_all if n in feasible}
        names = list(losses_by_ue)
        if run.get('replicates'):
            ref = ref_runs[run['replicates']]
            rundir = Path.home() / 'cmwave-runs/e1_5_1d' / run['tag']
            old_dir = Path.home() / 'cmwave-runs/e1_5_1' / run['replicates']
            expected = [x.replace(str(old_dir), str(rundir)) for x in ref['cmd']]
            if build_cmd(c, run, rundir) != expected:
                raise SystemExit(f'{run["tag"]}: command differs from the run-4 command')
        meta = execute(c, run, ns3, losses_by_ue, outdir)
        runs_meta.append({k: meta[k] for k in ('tag', 'exit_code', 'runtime_s', 'cmd', 'raw_sha256')})
        print(f"{run['tag']}: exit {meta['exit_code']}, {meta['runtime_s']:.0f} s", flush=True)
        if meta['exit_code'] != 0:
            validation[run['tag']] = {'run_failed': {'exit_code': meta['exit_code'], 'passed': False}}
            continue
        per, tot = ul_tb_stats(meta['dir'], len(names), t0, t1, width)
        snr_of = dict(zip(names_all, snr_full.tolist()))
        per_ue = {names[i]: dict(per[i], loss_db=losses_by_ue[names[i]], ul_snr_full_db=snr_of[names[i]],
                                 tdma_feasible=names[i] in feasible) for i in range(len(names))}
        v, s = {}, {'ul_totals': tot, 'per_ue': per_ue}
        if run.get('replicates'):
            ref = ref_runs[run['replicates']]
            diff = [fn for fn in DATA_FILES if meta['raw_sha256'].get(fn) != ref['raw_sha256'].get(fn)]
            v['P0_replication'] = {'files_compared': list(DATA_FILES), 'differing_files': diff, 'passed': not diff}
            with gzip.open(meta['dir'] / 'scheduler_log.txt.gz', 'rt', encoding='utf-8', errors='replace') as fh:
                events, creations = parse_scheduler_log(fh)
            v['P2_fixed_order'] = order_check(events, creations, t0, t1)
            sat = saturation(events, t0, t1)
            v['P3_saturation'] = dict(sat, threshold=0.95, passed=bool(sat['share'] is not None and sat['share'] >= 0.95))
            rnti_of = tot['rnti_after_attach']
            failed_v5 = ref_val[run['replicates']]['V5_coverage']['ul_below_10_tbs']
            targets = [n for n in failed_v5 if n in feasible]
            p4 = {}
            for n in targets:
                r = backlog_and_service(events, rnti_of[names.index(n)], t0, t1)
                r['passed'] = bool(r['backlogged_share'] is not None and r['backlogged_share'] >= 0.90
                                   and r['served_share'] is not None and r['served_share'] <= 0.01)
                p4[n] = r
            v['P4_backlog_without_service'] = {
                'run4_v5_ul_failures': failed_v5, 'tdma_feasible_targets': targets,
                'not_tdma_feasible_excluded': [n for n in failed_v5 if n not in feasible], 'per_ue': p4,
                'passed': bool(targets) and all(x['passed'] for x in p4.values())}
            order = global_order(events, t0, t1)
            ue_of_rnti = {rnti_of[i]: names[i] for i in range(len(names))}
            s['scheduler_log'] = {'ul_scheduling_events': len(events), 'ue_insertions': creations,
                                  'steady_state_first_assignment_order':
                                  None if order is None else [ue_of_rnti.get(r, f'rnti{r}') for r in order]}
        elif run['ues'] == 'tdma_feasible':
            short = {n: u['served_windows'] for n, u in per_ue.items() if u['served_windows'] < tot['n_windows']}
            v['P5_no_saturation_no_starvation'] = {'ues': len(names), 'not_served_in_all_windows': short,
                                                   'passed': not short}
        else:
            short = {n: u['served_windows'] for n, u in per_ue.items()
                     if u['tdma_feasible'] and u['served_windows'] < tot['n_windows']}
            v['P6_pf_memory'] = {'tdma_feasible_ues': sum(u['tdma_feasible'] for u in per_ue.values()),
                                 'not_served_in_all_windows': short, 'passed': not short}
        validation[run['tag']] = v
        summary[run['tag']] = s
        print('   ' + ', '.join(f"{k} {'PASS' if x['passed'] else 'FAIL'}" for k, x in v.items()), flush=True)

    passed = all(x['passed'] for v in validation.values() for x in v.values())
    (outdir / 'validation.json').write_text(json.dumps({'per_run': validation, 'all_predictions_held': passed},
                                                       indent=2, default=e151.json_default), encoding='utf-8')
    (outdir / 'summary.json').write_text(json.dumps(summary, indent=2, default=e151.json_default), encoding='utf-8')
    manifest = {'generated_utc': datetime.now(timezone.utc).isoformat(), 'tool': 'scripts/e1_5_1d_tdma_diagnostic.py',
                'git_head_at_run': args.git_head, 'python': platform.python_version(), 'numpy': np.__version__,
                'config_sha256': e151.sha256(args.config), 'e1_5_1_config_sha256': e151.sha256(E151_CONFIG),
                'program_sha256': prog_sha, 'ns3_commit': c151['stack']['ns3_commit'],
                'nr_commit': c151['stack']['nr_commit'], 'e1_2_realizations_sha256': real_sha,
                'runs': runs_meta, 'all_predictions_held': passed}
    (outdir / 'manifest.json').write_text(json.dumps(manifest, indent=2, default=e151.json_default), encoding='utf-8')
    print('E1.5.1-D', 'ALL PREDICTIONS HELD' if passed else 'AT LEAST ONE PREDICTION FAILED')
    sys.exit(0 if passed else 1)


if __name__ == '__main__':
    main()
