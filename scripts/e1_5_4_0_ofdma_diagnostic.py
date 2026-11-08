#!/usr/bin/env python3
"""E1.5.4-0: the SINR of OFDMA DL TBs co-scheduled under NR DirectPath beams.

Implements configs/e1/e1_5_4_0_ofdma_diagnostic.json. Run inside WSL with the project
venv. Every DL TB is joined with its DCI (RBG bitmask) and predicted from per-RB
gains of the receiving UE under each co-scheduled UE's stored beam. Cases K0 (replay
of E1.5.2-0 run 2), K1-K3 (two UEs) and K4 (replay with distinct BeamIds). Outputs
in --outdir: validation.json, summary.json, manifest.json; raw files in raw/.
"""
import argparse
import itertools
import json
import math
import platform
import shutil
import sys
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'scripts'))
from cmwave.radio import antenna as ant  # noqa: E402
from cmwave.radio import link_budget as lb  # noqa: E402
import e1_5_1_deterministic as e151  # noqa: E402
import e1_5_1d_tdma_diagnostic as diag  # noqa: E402
import e1_5_2_0_conventions as e1520  # noqa: E402

CONFIG = ROOT / 'configs/e1/e1_5_4_0_ofdma_diagnostic.json'
E151_CONFIG = ROOT / 'configs/e1/e1_5_1_deterministic.json'
E1520 = ROOT / 'results/phase2/e1_5_2_0_conventions'
OUTDIR = ROOT / 'results/phase2/e1_5_4_0_ofdma_diagnostic'
TOL_DB = 0.01
N_RB = 51
REPLAY_FILES = ('e15_dl_tb.csv', 'e152_nr_direct.csv', 'e15_rrc_events.csv', 'e15_rb_after_attach.csv',
                'e15_run_info.csv')
KEEP = ('links.csv',) + REPLAY_FILES + ('e15od_dci.csv', 'e15od_dl_sched.csv', 'e15od_dl_tb.csv',
                                        'e15od_rb_gain.csv', 'e1521_nr_beam.csv', 'run.log')
OD_TB_KEY = ('ue_index', 'rnti', 'time_ns')


# ---------------------------------------------------------------- reading
def read_links(path):
    out = []
    for r in e151.read_csv(path):
        out.append({'name': r['name'], 'x': float(r['x']), 'y': float(r['y']), 'z': float(r['z']),
                    'los': int(r['los']), 'o2i': int(r['o2i']), 'bearing_deg': float(r['bearing_deg']),
                    'loss_db': float(r['loss_db'])})
    return out


def read_dci(rundir):
    """DL DCIs keyed by (frame, subframe, slot, rnti, sym_start); duplicates abort."""
    out = {}
    for r in e151.read_csv(rundir / 'e15od_dci.csv'):
        k = (int(r['frame']), int(r['subframe']), int(r['slot']), int(r['rnti']), int(r['sym_start']))
        if k in out:
            raise SystemExit(f'{rundir}: two DL DCIs for {k}')
        out[k] = r
    return out


def read_sched(rundir):
    """MAC DL allocations (DlScheduling trace) keyed like the DCIs; order = MAC list order."""
    out = {}
    for r in e151.read_csv(rundir / 'e15od_dl_sched.csv'):
        k = (int(r['frame']), int(r['subframe']), int(r['slot']), int(r['rnti']), int(r['sym_start']))
        if k in out:
            raise SystemExit(f'{rundir}: two MAC DL allocations for {k}')
        out[k] = r
    return out


def read_rb_gain(rundir, n):
    g = np.full((n, n, N_RB), np.nan)
    for r in e151.read_csv(rundir / 'e15od_rb_gain.csv'):
        g[int(r['ue_index']), int(r['beam_ue_index']), int(r['rb'])] = float(r['gain_lin'])
    if np.isnan(g).any():
        raise SystemExit(f'{rundir}: incomplete per-RB gain table')
    return g


# ---------------------------------------------------------------- evaluation
def evaluate(rundir, links, cfg):
    """Join TBs with DCIs (J1, J2) and test own-beam identity and H1 on every TB."""
    n = len(links)
    snr = 10 ** (lb.downlink(np.array([l['loss_db'] for l in links]), cfg)['snr_db'] / 10)
    dci = read_dci(rundir)
    tbs, rows = e151.unique_tbs(e151.read_csv(rundir / 'e15od_dl_tb.csv'), OD_TB_KEY)
    gain = read_rb_gain(rundir, n)
    rnti_ue = {}
    for t in tbs:
        u, r = int(t['ue_index']), int(t['rnti'])
        if rnti_ue.setdefault(r, u) != u:
            raise SystemExit(f'{rundir}: RNTI {r} used by two UEs')
    j_fail, j2_fail = [], []
    joined = []
    for t in tbs:
        k = (int(t['frame']), int(t['subframe']), int(t['slot']), int(t['rnti']), int(t['sym_start']))
        d = dci.get(k)
        if d is None or int(d['num_sym']) != int(t['num_sym']):
            j_fail.append(k)
            continue
        mask = np.array([c == '1' for c in d['rbg_bitmask']])
        if mask.size != N_RB or int(mask.sum()) != int(t['rb_assigned']):
            j2_fail.append(k)
            continue
        joined.append((t, d, mask))
    sched = read_sched(rundir)
    # Amendment 1: NrGnbMac fires DlScheduling only for new data (m_ndi == 1), so J3 applies
    # to new-data DCIs (rv 0); retransmission DCIs without a MAC record are only counted.
    j3_fail = [k for k, d in dci.items() if int(d['rv']) == 0
               and (k not in sched or int(sched[k]['num_sym']) != int(d['num_sym']))]
    j3_retx = sum(1 for k, d in dci.items() if int(d['rv']) > 0 and k not in sched)
    groups = defaultdict(list)
    for k, d in dci.items():
        groups[k[:3] + (k[4],)].append(d)
    for g in groups.values():
        g.sort(key=lambda d: int(d['seq_in_slot']))
    mac_order = defaultdict(list)
    for k, r in sched.items():
        mac_order[k[:3] + (k[4],)].append(r)
    for g in mac_order.values():
        g.sort(key=lambda r: int(r['seq_in_slot']))

    def err(t, mask, b):
        j = int(t['ue_index'])
        pred = snr[j] * float(np.mean(gain[j, b, mask]))
        return abs(10 * math.log10(float(t['sinr_lin'])) - 10 * math.log10(pred))

    by_group = defaultdict(list)
    for t, d, mask in joined:
        by_group[(int(t['frame']), int(t['subframe']), int(t['slot']), int(t['sym_start']))].append((t, mask))
    st = {'tbs': len(tbs), 'trace_rows': rows, 'dl_dcis': len(dci), 'joined': len(joined),
          'J1_unmatched': len(j_fail), 'J2_rb_mismatch': len(j2_fail), 'J3_dci_without_mac_allocation': len(j3_fail),
          'J3_retx_dcis_without_mac_record': j3_retx, 'h1b_not_evaluable_groups': 0,
          'mac_allocations': len(sched), 'groups_with_tbs': len(by_group),
          'group_size_hist': {}, 'own_ok_alone': 0, 'own_fail_alone': 0, 'own_ok_cosched': 0,
          'own_fail_cosched': 0, 'own_err_cosched_db': [], 'h1a_fail_tbs': 0, 'h1b_fail_tbs': 0,
          'h1b_dci_order_fail_tbs': 0,
          'groups_two_or_more_ues': 0, 'max_err_h1_db': 0.0, 'max_err_own_alone_db': 0.0,
          'unknown_dci_rnti': 0, 'tbs_per_ue': defaultdict(int)}
    hist = defaultdict(int)
    for gk, members in by_group.items():
        dcis = groups[gk]
        ues = []
        for d in dcis:
            u = rnti_ue.get(int(d['rnti']))
            if u is None:
                st['unknown_dci_rnti'] += 1
            elif u not in ues:
                ues.append(u)
        hist[len(dcis)] += 1
        if len(set(ues)) >= 2:
            st['groups_two_or_more_ues'] += 1
        alone = len(dcis) == 1
        for t, mask in members:
            j = int(t['ue_index'])
            st['tbs_per_ue'][j] += 1
            e = err(t, mask, j)
            if alone:
                st['own_ok_alone' if e <= TOL_DB else 'own_fail_alone'] += 1
                st['max_err_own_alone_db'] = max(st['max_err_own_alone_db'], e)
            else:
                st['own_ok_cosched' if e <= TOL_DB else 'own_fail_cosched'] += 1
                st['own_err_cosched_db'].append(10 * math.log10(float(t['sinr_lin'])) -
                                                10 * math.log10(snr[j] * float(np.mean(gain[j, j, mask]))))
        # H1: one member beam explains every TB of the group
        cands = [b for b in ues if all(err(t, mask, b) <= TOL_DB for t, mask in members)]
        if not cands:
            st['h1a_fail_tbs'] += len(members)
            st['h1b_fail_tbs'] += len(members)
            st['h1b_dci_order_fail_tbs'] += len(members)
            continue
        if any(int(d['rv']) > 0 for d in dcis):
            # amendment 1: the MAC order is unknown for groups with a retransmission
            st['h1b_not_evaluable_groups'] += 1
            b = cands[0]
            st['max_err_h1_db'] = max(st['max_err_h1_db'], max(err(t, mask, b) for t, mask in members))
            continue
        mo = mac_order.get(gk)
        first = rnti_ue.get(int(mo[0]['rnti'])) if mo else None
        if first not in cands:
            st['h1b_fail_tbs'] += len(members)
        if rnti_ue.get(int(dcis[0]['rnti'])) not in cands:
            st['h1b_dci_order_fail_tbs'] += len(members)
        b = first if first in cands else cands[0]
        st['max_err_h1_db'] = max(st['max_err_h1_db'], max(err(t, mask, b) for t, mask in members))
    st['group_size_hist'] = {str(k): v for k, v in sorted(hist.items())}
    oe = np.array(st.pop('own_err_cosched_db'))
    st['own_err_cosched_db_range'] = [float(oe.min()), float(oe.max())] if oe.size else None
    st['tbs_per_ue'] = {str(k): v for k, v in sorted(st['tbs_per_ue'].items())}
    st['min_tbs_per_ue'] = min(st['tbs_per_ue'].get(str(i), 0) for i in range(n))
    return st


def join_ok(st):
    """J1, J2 and J3 (new-data DCIs, amendment 1). A run without TBs joins trivially; its
    TB count is checked by the case predictions."""
    return (st['J1_unmatched'] == 0 and st['J2_rb_mismatch'] == 0 and st['J3_dci_without_mac_allocation'] == 0
            and st['joined'] == st['tbs'])


def h1a_ok(st):
    return join_ok(st) and st['h1a_fail_tbs'] == 0 and st['unknown_dci_rnti'] == 0


def h1_ok(st):
    """H1a and H1b (MAC allocation order)."""
    return h1a_ok(st) and st['h1b_fail_tbs'] == 0


def own_all_ok(st):
    return join_ok(st) and st['own_fail_alone'] == 0 and st['own_fail_cosched'] == 0


# ---------------------------------------------------------------- inputs of K1-K3
def k1_pair(links):
    """Two O2O LOS links of the 30 deg sector with the largest azimuth separation."""
    cand = [i for i, l in enumerate(links) if l['los'] == 1 and l['bearing_deg'] == 30.0]
    if len(cand) < 2:
        raise SystemExit('fewer than two LOS links in the 30 deg sector')
    best, sep = None, -1.0
    for i, j in itertools.combinations(cand, 2):
        s = abs(float(ant.wrap_deg(links[i]['py_azimuth_deg'] - links[j]['py_azimuth_deg'])))
        if s > sep:
            best, sep = (i, j), s
    return best, sep


def run2_args(run):
    """Program arguments of a run-2 command (program name dropped)."""
    parts = run['cmd'][-1].split(' ')
    if parts[0] != e151.PROGRAM:
        raise SystemExit('unexpected run-2 command')
    return parts[1:]


def replace_arg(args, name, value):
    out = [a for a in args if not a.startswith(f'--{name}=')]
    if len(out) != len(args) - 1:
        raise SystemExit(f'argument {name} not found exactly once')
    return out + [f'--{name}={value}']


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
    c = c151['common']
    ns3 = Path(args.ns3_dir)
    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    prog_sha = e151.check_stack(c151, ns3)
    medians, real_sha = e151.median_losses()
    work = Path.home() / 'cmwave-runs/e1_5_4_0'
    raw = outdir / 'raw'
    m2 = json.loads((E1520 / 'manifest_run2_failed_V0.json').read_text(encoding='utf-8'))
    run2 = [r for r in m2['runs'] if '--mode=nrfading' in r['cmd'][-1]]
    if len(run2) != 9:
        raise SystemExit('expected 9 run-2 nrfading runs')
    runs_meta, validation, summary = [], {}, {}

    def go(tag, rd, a):
        cmd, code, rt = e1520.run_program(ns3, rd, a)
        h = e1520.keep(rd, raw / tag, KEEP)
        runs_meta.append({'tag': tag, 'cmd': cmd, 'exit_code': code, 'runtime_s': rt, 'raw_sha256': h})
        print(f'{tag}: exit {code}, {rt:.0f} s', flush=True)
        if code != 0:
            raise SystemExit(f'{tag} failed; see {rd / "run.log"}')

    # K0 and K4: run-2 commands
    for case, extra in (('K0', []), ('K4', ['--gnbBeam=dpid'])):
        res = {}
        for r in run2:
            tag = f"{case.lower()}_{r['tag']}"
            src = E1520 / 'raw/run2_failed' / r['tag'] / 'links.csv'
            if e151.sha256(src) != r['raw_sha256']['links.csv']:
                raise SystemExit(f'run-2 links file changed: {src}')
            rd = work / tag
            shutil.rmtree(rd, ignore_errors=True)
            rd.mkdir(parents=True)
            shutil.copy2(src, rd / 'links.csv')
            a = replace_arg(run2_args(r), 'linksFile', rd / 'links.csv') + ['--ofdmaDiag=1'] + extra
            go(tag, rd, a)
            links = read_links(rd / 'links.csv')
            f = float(next(x for x in a if x.startswith('--frequency=')).split('=')[1])
            st = evaluate(rd, links, diag.carrier_cfg(c, f))
            if case == 'K0':
                st['R2_differing_files'] = [fn for fn in REPLAY_FILES
                                            if e151.sha256(rd / fn) != r['raw_sha256'][fn]]
            res[tag] = st
        summary[case] = res
    validation['R2_replay_identical'] = {
        'differing': {t: s['R2_differing_files'] for t, s in summary['K0'].items() if s['R2_differing_files']},
        'passed': all(not s['R2_differing_files'] for s in summary['K0'].values())}

    # Amendment 1, K4b: the 30 deg sector (13 UEs) with its first 12 links, distinct BeamIds
    # (separates hazard 14, no DL data with more beams than data symbols, from P4's question)
    res = {}
    for r in run2:
        if not r['tag'].endswith('_b30'):
            continue
        tag = f"k4b_{r['tag']}"
        src = E1520 / 'raw/run2_failed' / r['tag'] / 'links.csv'
        rd = work / tag
        shutil.rmtree(rd, ignore_errors=True)
        rd.mkdir(parents=True)
        lines = src.read_text(encoding='utf-8').splitlines(keepends=True)
        if len(lines) != 14:
            raise SystemExit(f'{src}: expected 13 links in the 30 deg sector')
        (rd / 'links.csv').write_text(''.join(lines[:13]), encoding='utf-8', newline='')
        a = replace_arg(run2_args(r), 'linksFile', rd / 'links.csv') + ['--ofdmaDiag=1', '--gnbBeam=dpid']
        go(tag, rd, a)
        f = float(next(x for x in a if x.startswith('--frequency=')).split('=')[1])
        res[tag] = evaluate(rd, read_links(rd / 'links.csv'), diag.carrier_cfg(c, f))
    summary['K4b'] = res

    # K1-K3: two UEs
    gnb, links = e1520.build_links(7.2e9, 8, 16, medians)
    (i, j), sep = k1_pair(links)
    first = dict(links[i])
    k3 = [dict(first, name=first['name'] + '_a'), dict(first, name=first['name'] + '_b')]
    s = {'dlRate': 20e6, 'scheduler': 'ns3::NrMacSchedulerOfdmaRR'}
    cases = {'K1': ([links[i], links[j]], 'dp'), 'K2': ([links[i], links[j]], 'dpid'), 'K3': (k3, 'dp')}
    summary['K1_pair'] = {'names': [links[i]['name'], links[j]['name']], 'azimuth_separation_deg': sep}
    for case, (sub, beam) in cases.items():
        tag = case.lower()
        rd = work / tag
        shutil.rmtree(rd, ignore_errors=True)
        rd.mkdir(parents=True)
        e1520.write_links(rd / 'links.csv', sub)
        a = ['--mode=nrfading', f'--linksFile={rd / "links.csv"}', f'--frequency={7.2e9!r}',
             f'--bandwidth={c["bandwidth_hz"]!r}', f'--numerology={c["numerology"]}',
             f'--rbOverhead={c["rb_overhead"]!r}', f'--gnbTxPower={c["gnb_tx_power_dbm"]!r}',
             f'--nfGnb={c["nf_gnb_db"]!r}', f'--nfUe={c["nf_ue_db"]!r}', f'--gnbX={gnb[0]!r}',
             f'--gnbY={gnb[1]!r}', f'--gnbZ={gnb[2]!r}', '--gnbRows=8', '--gnbCols=16', '--bearingDeg=30.0',
             f'--scheduler={s["scheduler"]}', f'--dlRate={s["dlRate"]!r}', '--packetSize=1000',
             '--appStart=0.1', '--simTime=0.4', f'--tddPattern={c["tdd_pattern"]}', '--ofdmaDiag=1']
        if beam != 'dp':
            a.append(f'--gnbBeam={beam}')
        go(tag, rd, a)
        summary[case] = {tag: evaluate(rd, read_links(rd / 'links.csv'), diag.carrier_cfg(c, 7.2e9))}

    cases_all = ('K0', 'K1', 'K2', 'K3', 'K4', 'K4b')
    allst = [st for case in cases_all for st in summary[case].values()]
    validation['J1_J3_join'] = {'passed': all(join_ok(st) for st in allst),
                                'failures': {t: (st['J1_unmatched'], st['J2_rb_mismatch'],
                                                 st['J3_dci_without_mac_allocation'])
                                             for case in cases_all
                                             for t, st in summary[case].items() if not join_ok(st)},
                                'retx_dcis_without_mac_record': sum(st['J3_retx_dcis_without_mac_record']
                                                                    for st in allst)}
    validation['H1a_K0_K1'] = {'passed': all(h1a_ok(st) for case in ('K0', 'K1') for st in summary[case].values())}
    validation['H1b_K0_K1_reported'] = {
        'decision_relevant': False,
        'h1b_fail_tbs_mac_order': sum(st['h1b_fail_tbs'] for case in ('K0', 'K1') for st in summary[case].values()),
        'h1b_fail_tbs_dci_send_order': sum(st['h1b_dci_order_fail_tbs'] for case in ('K0', 'K1')
                                           for st in summary[case].values()),
        'passed': all(h1_ok(st) for case in ('K0', 'K1') for st in summary[case].values())}
    k1 = summary['K1']['k1']
    validation['K1_power'] = {'two_ue_groups': k1['groups_two_or_more_ues'], 'own_fail_cosched': k1['own_fail_cosched'],
                              'passed': k1['groups_two_or_more_ues'] >= 10 and k1['own_fail_cosched'] >= 1}
    k2 = summary['K2']['k2']
    validation['P2_K2'] = {'passed': k2['groups_two_or_more_ues'] == 0 and own_all_ok(k2)}
    k3s = summary['K3']['k3']
    validation['P3_K3'] = {'passed': k3s['groups_two_or_more_ues'] >= 10 and own_all_ok(k3s)}
    def p4(st):
        return st['groups_two_or_more_ues'] == 0 and own_all_ok(st) and st['min_tbs_per_ue'] >= 10

    validation['P4_K4'] = {'decision_relevant': False, 'superseded_by': 'P4a_amended (amendment 1)',
                           'failed_runs': [t for t, st in summary['K4'].items() if not p4(st)],
                           'passed': all(p4(st) for st in summary['K4'].values())}
    zero = [t for t, st in summary['K4'].items() if st['dl_dcis'] == 0]
    validation['hazard14_reported'] = {'decision_relevant': False, 'k4_runs_without_any_dl_dci': zero,
                                       'passed': not zero}
    p4a = {t: st for t, st in summary['K4'].items() if t not in zero}
    p4a.update(summary['K4b'])
    validation['P4a_amended'] = {'runs': sorted(p4a), 'failed_runs': [t for t, st in p4a.items() if not p4(st)],
                                 'passed': bool(p4a) and all(p4(st) for st in p4a.values())}
    k0 = summary['K0'].values()
    summary['K0_totals'] = {
        'tbs': sum(x['tbs'] for x in k0), 'alone_own_ok': sum(x['own_ok_alone'] for x in k0),
        'alone_own_fail': sum(x['own_fail_alone'] for x in k0),
        'cosched_own_ok': sum(x['own_ok_cosched'] for x in k0),
        'cosched_own_fail': sum(x['own_fail_cosched'] for x in k0),
        'h1a_fail': sum(x['h1a_fail_tbs'] for x in k0), 'h1b_fail': sum(x['h1b_fail_tbs'] for x in k0),
        'h1b_not_evaluable_groups': sum(x['h1b_not_evaluable_groups'] for x in k0),
        'groups_two_or_more_ues': sum(x['groups_two_or_more_ues'] for x in k0),
        'max_err_h1_db': max(x['max_err_h1_db'] for x in k0)}
    passed = all(v['passed'] for v in validation.values() if v.get('decision_relevant', True))
    (outdir / 'validation.json').write_text(json.dumps({'checks': validation, 'all_checks_passed': passed},
                                                       indent=2, default=e151.json_default), encoding='utf-8')
    (outdir / 'summary.json').write_text(json.dumps(summary, indent=2, default=e151.json_default), encoding='utf-8')
    manifest = {'generated_utc': datetime.now(timezone.utc).isoformat(), 'tool': 'scripts/e1_5_4_0_ofdma_diagnostic.py',
                'git_head_at_run': args.git_head, 'python': platform.python_version(), 'numpy': np.__version__,
                'config_sha256': e151.sha256(args.config), 'program_sha256': prog_sha,
                'ns3_commit': c151['stack']['ns3_commit'], 'nr_commit': c151['stack']['nr_commit'],
                'e1_2_realizations_sha256': real_sha, 'runs': runs_meta, 'all_checks_passed': passed}
    (outdir / 'manifest.json').write_text(json.dumps(manifest, indent=2, default=e151.json_default), encoding='utf-8')
    for k, v in validation.items():
        print(k, 'PASS' if v['passed'] else 'FAIL', flush=True)
    print('E1.5.4-0', 'mechanism ESTABLISHED' if passed else 'NOT established (see validation.json)')
    sys.exit(0 if passed else 1)


if __name__ == '__main__':
    main()
