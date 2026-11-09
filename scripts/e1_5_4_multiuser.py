#!/usr/bin/env python3
"""E1.5.4: ten-UE packet-level test of the F2 / F3 per-user service models.

Implements configs/e1/e1_5_4_multiuser.json. Run inside WSL with the project venv.
Outputs in --outdir: validation.json, summary.json, per_ue.csv, manifest.json; raw/ git-ignored.
"""
import argparse
import csv
import gzip
import json
import math
import platform
import shutil
import subprocess
import sys
import time
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'scripts'))
from cmwave.radio import link_budget as lb  # noqa: E402
import e1_5_1_deterministic as e151  # noqa: E402
import e1_5_1d_tdma_diagnostic as diag  # noqa: E402
import e1_5_2_0_conventions as e1520  # noqa: E402
import e1_5_3_link_mapping as e153  # noqa: E402
import e1_5_3a_amc_diagnostic as e153a  # noqa: E402

CONFIG = ROOT / 'configs/e1/e1_5_4_multiuser.json'
E151_CONFIG = ROOT / 'configs/e1/e1_5_1_deterministic.json'
E153_CONFIG = ROOT / 'configs/e1/e1_5_3_link_mapping.json'
E153_RESULTS = ROOT / 'results/phase2/e1_5_3_link_mapping'
E153A_RESULTS = ROOT / 'results/phase2/e1_5_3a_amc_diagnostic'
E1521_RESULTS = ROOT / 'results/phase2/e1_5_2_1_beams'
OUTDIR = ROOT / 'results/phase2/e1_5_4_multiuser'
EM = 'ns3::NrEesmIrT2'
N_RB = 51
F3_RB = 5
K_UE = 10
TARGET = {'DL': 0.225, 'UL': 0.15}
DIRS = ('DL', 'UL')
TOL_DB = 0.01
L_GRID = [float(x) for x in range(96, 135)]
TDMA = 'ns3::NrMacSchedulerTdmaPF'
OFDMA = 'ns3::NrMacSchedulerOfdmaPF'
DATA = '2'
GZ_FILES = ('e154_tb.csv', 'e154_dci.csv')
KEEP = ('losses.csv', 'links.csv', 'e154_tb.csv', 'e154_dci.csv', 'e154_rb_gain.csv', 'e154_mask.csv',
        'e15_run_info.csv', 'e153_error_model.csv', 'e15_rrc_events.csv', 'e15_rnti_map.csv', 'e1521_nr_beam.csv',
        'run.log')


# ---------------------------------------------------------------- pure helpers (unit-tested)
def unique_tbs(rows):
    """One row per TB: the TB trace fires once per packet of the burst (E1.5.1 erratum 1)."""
    out, seen = [], set()
    for r in rows:
        k = (r['dir'], r['rnti'], r['time_ns'])
        if k not in seen:
            seen.add(k)
            out.append(r)
    return out


def rb_list(mask):
    return [k for k, c in enumerate(mask) if c == '1']


def dci_key(dir_, rnti, frame, subframe, slot, sym):
    return (dir_, str(rnti), str(frame), str(subframe), str(slot), str(sym))


def dci_conflicts(dcis, one_ue_per_symbol):
    """Pairs of DATA DCIs of one direction and target slot that overlap in symbols and RBs (always a
    failure) and, if one_ue_per_symbol, pairs of different UEs overlapping in symbols at all."""
    by = defaultdict(list)
    for r in dcis:
        by[(r['dir'], r['t_frame'], r['t_subframe'], r['t_slot'])].append(r)
    rb_clash, sym_share = 0, 0
    for v in by.values():
        for i in range(len(v)):
            a = v[i]
            sa = (int(a['sym_start']), int(a['sym_start']) + int(a['num_sym']))
            for j in range(i + 1, len(v)):
                b = v[j]
                sb = (int(b['sym_start']), int(b['sym_start']) + int(b['num_sym']))
                if max(sa[0], sb[0]) >= min(sa[1], sb[1]):
                    continue
                if any(x == '1' and y == '1' for x, y in zip(a['rbg_bitmask'], b['rbg_bitmask'])):
                    rb_clash += 1
                elif a['rnti'] != b['rnti']:
                    sym_share += 1
    return rb_clash, (sym_share if one_ue_per_symbol else 0), sym_share


def attribute_ul(rrc_rows, imsi_to_ue):
    """(rnti, time_ns) -> ue_index: the latest RRC establishment of that RNTI before the time."""
    est = defaultdict(list)
    for r in rrc_rows:
        est[r['rnti']].append((int(r['time_ns']), imsi_to_ue[r['imsi']]))
    for v in est.values():
        v.sort()

    def who(rnti, t):
        best = None
        for te, ue in est.get(str(rnti), []):
            if te <= t:
                best = ue
        return best
    return who


def per_ue_metrics(tbs, window, n_ue, part_s=False):
    """tbs: unique TBs with keys dir, ue (int or None), time_ns, rb_assigned, num_sym, tb_size, rv,
    corrupt. Returns {dir: {'res_dir': .., 'ue': [dict per UE]}}."""
    lo, hi = int(window[0] * 1e9), int(window[1] * 1e9)
    out = {}
    for d in DIRS:
        w = [r for r in tbs if r['dir'] == d and lo <= int(r['time_ns']) < hi]
        res = lambda r: int(r['rb_assigned']) * 12 * e153.SCS * int(r['num_sym']) * e153.T_SYM  # noqa: E731
        res_dir = sum(res(r) for r in w)
        ues = []
        for u in range(n_ue):
            x = [r for r in w if r['ue'] == u]
            bits = sum(8 * int(r['tb_size']) for r in x if r['corrupt'] == '0')
            res_u = sum(res(r) for r in x)
            first = [r for r in x if r['rv'] == '0']
            if part_s:
                se = bits / (res_u * N_RB / F3_RB) if res_u > 0 else 0.0
            else:
                se = bits / res_dir if res_dir > 0 else 0.0
            ues.append({'ue': u, 'tbs': len(x), 'bits': bits, 'mbps': bits / (window[1] - window[0]) / 1e6,
                        'se_user': se, 'share': res_u / res_dir if res_dir > 0 else 0.0,
                        'first_tx': len(first), 'bler_first_tx': (sum(r['corrupt'] == '1' for r in first) / len(first))
                        if first else None,
                        'retx_share': (sum(r['rv'] != '0' for r in x) / len(x)) if x else None,
                        'sym_hist': dict(sorted(Counter(int(r['num_sym']) for r in x).items())),
                        'rb_hist': dict(sorted(Counter(int(r['rb_assigned']) for r in x).items())),
                        'mcs_hist': dict(sorted(Counter(int(r['mcs']) for r in x).items())),
                        'satisfied': se >= TARGET[d]})
        out[d] = {'res_dir': res_dir, 'ue': ues, 'unattributed': sum(1 for r in w if r['ue'] is None)}
    return out


def h18_split(ul_tbs_by_ue, window):
    """First-transmission UL TBs in the window with an earlier UL TB of the same UE: (n, fails) for
    rb > previous rb and rb <= previous rb."""
    lo, hi = int(window[0] * 1e9), int(window[1] * 1e9)
    up, down = [0, 0], [0, 0]
    for tbs in ul_tbs_by_ue.values():
        s = sorted(tbs, key=lambda r: int(r['time_ns']))
        for prev, r in zip(s, s[1:]):
            if r['rv'] != '0' or not lo <= int(r['time_ns']) < hi:
                continue
            tgt = up if int(r['rb_assigned']) > int(prev['rb_assigned']) else down
            tgt[0] += 1
            tgt[1] += r['corrupt'] == '1'
    return {'n_up': up[0], 'fail_up': up[1], 'n_down': down[0], 'fail_down': down[1],
            'bler_up': up[1] / up[0] if up[0] else None, 'bler_down': down[1] / down[0] if down[0] else None}


def threshold(curve, target):
    return e153.required_snr_empirical(sorted(curve), target)


def interp_curve(curve, x):
    """Piecewise-linear value, None outside the curve's SNR range."""
    pts = sorted(curve)
    if not pts or x < pts[0][0] or x > pts[-1][0]:
        return None
    return float(np.interp(x, [p[0] for p in pts], [p[1] for p in pts]))


def gzip_copy(src, dst):
    with open(src, 'rb') as fi, open(dst, 'wb') as fo:
        with gzip.GzipFile(fileobj=fo, mode='wb', mtime=0, filename='') as gz:
            shutil.copyfileobj(fi, gz)


# ---------------------------------------------------------------- run building
def link_args(c, st, rd, scheduler, rng=1, extra=()):
    return [f'--frequency={st["carrier_hz"]!r}', f'--bandwidth={c["bandwidth_hz"]!r}',
            f'--numerology={c["numerology"]}', f'--rbOverhead={c["rb_overhead"]!r}',
            f'--gnbTxPower={c["gnb_tx_power_dbm"]!r}', f'--ueTxPower={c["ue_tx_power_dbm"]!r}',
            f'--nfGnb={c["nf_gnb_db"]!r}', f'--nfUe={c["nf_ue_db"]!r}', f'--lossFile={rd / "losses.csv"}',
            f'--scheduler={scheduler}', '--dlRate=40000000.0', '--ulRate=20000000.0', '--packetSize=1000',
            '--ulPacketSize=1000', '--appStart=0.1', '--simTime=1.0', f'--tddPattern={c["tdd_pattern"]}',
            '--enableRlf=0', f'--errorModel={EM}', '--resTrace=1', f'--RngRun={rng}'] + list(extra)


def fading_args(c, rd, f, gnb, rows, cols, scheduler, rng, extra=()):
    return ['--mode=nrfading', f'--linksFile={rd / "links.csv"}', f'--frequency={f!r}',
            f'--bandwidth={c["bandwidth_hz"]!r}', f'--numerology={c["numerology"]}',
            f'--rbOverhead={c["rb_overhead"]!r}', f'--gnbX={gnb[0]!r}', f'--gnbY={gnb[1]!r}', f'--gnbZ={gnb[2]!r}',
            f'--RngRun={rng}', f'--gnbTxPower={c["gnb_tx_power_dbm"]!r}', f'--nfGnb={c["nf_gnb_db"]!r}',
            f'--nfUe={c["nf_ue_db"]!r}', f'--gnbRows={rows}', f'--gnbCols={cols}', '--bearingDeg=30.0',
            f'--scheduler={scheduler}', '--dlRate=40000000.0', '--fadingUlRate=20000000.0', '--packetSize=1000',
            '--appStart=0.1', '--simTime=1.0', f'--tddPattern={c["tdd_pattern"]}', '--gnbBeam=cb',
            '--oversampling=4', f'--errorModel={EM}', '--resTrace=1'] + list(extra)


def run_one(ns3, spec):
    rd = spec['rd']
    cmd = ['./ns3', 'run', '--no-build', f'--cwd={rd}', ' '.join([e151.PROGRAM] + spec['args'])]
    t1 = time.time()
    p = subprocess.run(cmd, cwd=ns3, capture_output=True, text=True)
    rt = time.time() - t1
    (rd / 'run.log').write_text(f'cmd: {cmd}\nexit: {p.returncode}\nruntime_s: {rt:.1f}\n--- stdout\n'
                                f'{p.stdout}\n--- stderr\n{p.stderr}\n', encoding='utf-8')
    return cmd, p.returncode, rt


def execute(ns3, specs, outdir, runs_meta, workers):
    with ThreadPoolExecutor(max_workers=workers) as ex:
        res = list(ex.map(lambda s: run_one(ns3, s), specs))
    failed = []
    for s, (cmd, rc, rt) in zip(specs, res):
        h = {}
        dst = outdir / 'raw' / s['tag']
        shutil.rmtree(dst, ignore_errors=True)
        dst.mkdir(parents=True)
        for f in KEEP:
            src = s['rd'] / f
            if src.exists():
                h[f] = e151.sha256(src)
                if f in GZ_FILES:
                    gzip_copy(src, dst / (f + '.gz'))
                else:
                    shutil.copy2(src, dst / f)
        runs_meta.append({'tag': s['tag'], 'cmd': cmd, 'exit_code': rc, 'runtime_s': rt, 'raw_sha256': h})
        print(f'{s["tag"]}: exit {rc}, {rt:.0f} s', flush=True)
        if rc != 0:
            failed.append(s['tag'])
    return failed


# ---------------------------------------------------------------- per-run analysis
def analyse_run(spec, c, cfg, window):
    rd = spec['rd']
    n_ue = spec['n_ue']
    gates = {}
    info = {r['key']: r['value'] for r in e151.read_csv(rd / 'e15_run_info.csv')}
    inst = e151.read_csv(rd / 'e153_error_model.csv')
    types = [info.get('error_model_gnb_dl_amc'), info.get('error_model_gnb_ul_amc')] + \
        [r['error_model_instance'] for r in inst]
    gates['E0'] = all(t == EM for t in types) and len(inst) == n_ue + 1
    imsi_to_ue = {r['imsi']: int(r['ue_index']) for r in e151.read_csv(rd / 'e15_rnti_map.csv')}
    rrc = e151.read_csv(rd / 'e15_rrc_events.csv')
    est = {imsi_to_ue[r['imsi']]: int(r['time_ns']) for r in rrc if r['imsi'] in imsi_to_ue}
    gates['POP'] = len(imsi_to_ue) == n_ue and all(u in est and est[u] < window[0] * 1e9 for u in range(n_ue))
    who = attribute_ul(rrc, imsi_to_ue)
    tbs = unique_tbs(e151.read_csv(rd / 'e154_tb.csv'))
    for r in tbs:
        r['ue'] = int(r['ue_index']) if r['dir'] == 'DL' else who(r['rnti'], int(r['time_ns']))
    dcis = [r for r in e151.read_csv(rd / 'e154_dci.csv') if r['type'] == DATA]
    idx = defaultdict(list)
    for r in dcis:
        idx[dci_key(r['dir'], r['rnti'], r['t_frame'], r['t_subframe'], r['t_slot'], r['sym_start'])].append(r)
    lo, hi = int(window[0] * 1e9), int(window[1] * 1e9)
    win = [r for r in tbs if lo <= int(r['time_ns']) < hi]
    unmatched, outside_mask, worst = 0, 0, {'DL': 0.0, 'UL': 0.0}
    gains = None
    if spec['mode'] == 'nrfading':
        gains = defaultdict(list)
        for r in e151.read_csv(rd / 'e154_rb_gain.csv'):
            gains[(int(r['ue_index']), r['dir'])].append(float(r['gain_lin']))
    noise = {'DL': -174 + 10 * math.log10(cfg.prb_bw_hz) + c['nf_ue_db'],
             'UL': -174 + 10 * math.log10(cfg.prb_bw_hz) + c['nf_gnb_db']}
    p_rb_dl = c['gnb_tx_power_dbm'] - 10 * math.log10(N_RB)
    for r in win:
        m = idx.get(dci_key(r['dir'], r['rnti'], r['frame'], r['subframe'], r['slot'], r['sym_start']), [])
        if len(m) != 1 or r['ue'] is None:
            unmatched += 1
            continue
        dci = m[0]
        rbs = rb_list(dci['rbg_bitmask'])
        if len(rbs) != int(r['rb_assigned']) or dci['num_sym'] != r['num_sym'] or dci['mcs'] != r['mcs']:
            unmatched += 1
            continue
        if spec['part'] in ('S', 'S_C') and any(k >= F3_RB for k in rbs):
            outside_mask += 1
        L = spec['loss'][r['ue']]
        p_rb = p_rb_dl if r['dir'] == 'DL' else c['ue_tx_power_dbm'] - 10 * math.log10(len(rbs))
        g = gains[(r['ue'], r['dir'])] if gains is not None else None
        lin = [10 ** ((p_rb - L - noise[r['dir']]) / 10) * (g[k] if g is not None else 1.0) for k in rbs]
        pred = sum(lin) / len(lin)
        worst[r['dir']] = max(worst[r['dir']], abs(10 * math.log10(float(r['sinr_lin'])) - 10 * math.log10(pred)))
    tdma = spec['scheduler'] == TDMA
    rb_clash, sym_violation, sym_share = dci_conflicts(dcis, tdma)
    gates['RES'] = unmatched == 0 and rb_clash == 0 and sym_violation == 0 and outside_mask == 0
    if spec['part'] in ('S', 'S_C'):
        mk = e151.read_csv(rd / 'e154_mask.csv')
        okm = len(mk) == 1 and mk[0]['time_ns'] == '100000000' and mk[0]['dl_usable'] == '5' and \
            mk[0]['ul_usable'] == '5' and mk[0]['dl_mask_len'] == '51' and mk[0]['ul_mask_len'] == '51' and \
            mk[0]['dl_first_usable'] == '0' and mk[0]['dl_last_usable'] == '4'
        win_dci = [x for x in dcis if lo <= int(x['time_ns']) < hi]
        okm = okm and all(max(rb_list(x['rbg_bitmask'])) < F3_RB for x in win_dci)
        gates['RES'] = gates['RES'] and okm
    gates['V'] = worst['DL'] <= TOL_DB and worst['UL'] <= TOL_DB
    if spec['mode'] == 'nrfading':
        beam = e151.read_csv(rd / 'e1521_nr_beam.csv')
        gates['BEAM'] = len(beam) == n_ue and all(b['bm_vector_match'] == '1' and b['gnb_is_s'] == '1' for b in beam)
    if spec.get('fixed'):
        fx = spec['fixed']
        bad = sum(1 for r in tbs if int(r['mcs']) != fx[r['dir']])
        gates['FIX'] = bad == 0 and info.get('sched_fixed_mcs_dl') == '1' and info.get('sched_fixed_mcs_ul') == '1' \
            and info.get('sched_starting_mcs_dl') == str(fx['DL']) and info.get('sched_starting_mcs_ul') == str(fx['UL'])
    met = per_ue_metrics(tbs, window, n_ue, part_s=spec['part'] in ('S', 'S_C'))
    ul_by_ue = defaultdict(list)
    for r in tbs:
        if r['dir'] == 'UL' and r['ue'] is not None:
            ul_by_ue[r['ue']].append(r)
    detail = {'unmatched_tbs': unmatched, 'rb_clash_pairs': rb_clash, 'tdma_symbol_share_violations': sym_violation,
              'symbol_sharing_pairs_disjoint_rbs': sym_share, 'outside_mask': outside_mask,
              'max_abs_sinr_err_db': worst, 'h18': h18_split(ul_by_ue, window)}
    return gates, met, detail


# ---------------------------------------------------------------- regression gates R6
def rewrite_cmd(cmd_ref, rd, links=False, losses=False):
    """A recorded ./ns3 run command moved to directory rd: the program string with its --linksFile /
    --lossFile pointing into rd; every other token unchanged (a token naming the old run directory
    aborts)."""
    old_cwd = cmd_ref[3].split('=', 1)[1]
    new = []
    for a in cmd_ref[4].split(' '):
        if links and a.startswith('--linksFile='):
            a = f'--linksFile={rd / "links.csv"}'
        elif losses and a.startswith('--lossFile='):
            a = f'--lossFile={rd / "losses.csv"}'
        elif old_cwd in a:
            raise SystemExit(f'unexpected path argument {a}')
        new.append(a)
    return ['./ns3', 'run', '--no-build', f'--cwd={rd}', ' '.join(new)]


def compare_rerun(ns3, cmd_ref, sha_ref, rd, links_src=None, losses_src=None):
    """Re-execute a recorded command in a fresh directory; compare data files (not run.log)."""
    shutil.rmtree(rd, ignore_errors=True)
    rd.mkdir(parents=True)
    if links_src:
        shutil.copy2(links_src, rd / 'links.csv')
    if losses_src:
        shutil.copy2(losses_src, rd / 'losses.csv')
    cmd = rewrite_cmd(cmd_ref, rd, links=bool(links_src), losses=bool(losses_src))
    p = subprocess.run(cmd, cwd=ns3, capture_output=True, text=True)
    differ = [f for f, h in sha_ref.items() if f != 'run.log' and
              (not (rd / f).exists() or e151.sha256(rd / f) != h)]
    return p.returncode, differ


def r6_gates(ns3, outdir, work, git_head):
    out = {'R6a': e153a.rerun_e1520(outdir, git_head, name='r6a_e1520_rerun')}
    print('R6a', 'PASS' if out['R6a']['passed'] else 'FAIL', flush=True)
    for gate, man_path, tags, raw in (
            ('R6b', E153_RESULTS / 'manifest.json', ('t2_L93', 't2_L113', 't1_L113'), E153_RESULTS / 'raw'),
            ('R6c', E153A_RESULTS / 'manifest.json', ('b_t2_L93', 'b_t1_L113'), E153A_RESULTS / 'raw'),
            ('R6d', E1521_RESULTS / 'manifest.json',
             ('v1_a_f7p2_o2o_cb_b30', 'v1_a_f7p2_o2o_cb_b150', 'v1_a_f7p2_o2o_cb_b270'), E1521_RESULTS / 'raw')):
        runs = {r['tag']: r for r in json.loads(man_path.read_text())['runs']}
        differ, codes = [], {}
        for t in tags:
            ref = runs[t]
            rc, d = compare_rerun(ns3, ref['cmd'], ref['raw_sha256'], work / gate.lower() / t,
                                  links_src=(raw / t / 'links.csv') if gate == 'R6d' else None,
                                  losses_src=(raw / t / 'losses.csv') if gate != 'R6d' else None)
            codes[t] = rc
            differ += [f'{t}/{f}' for f in d]
        out[gate] = {'runs': list(tags), 'exit_codes': codes, 'differing_files': differ,
                     'passed': not differ and all(v == 0 for v in codes.values())}
        print(gate, 'PASS' if out[gate]['passed'] else 'FAIL', flush=True)
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
    st = dict(c153['setup'])
    window = conf['common']['times']['window_s']
    workers = 4
    ns3 = Path(args.ns3_dir)
    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    prog_sha = e151.check_stack(c151, ns3)
    t0 = time.time()
    work = Path.home() / 'cmwave-runs/e1_5_4'
    shutil.rmtree(work, ignore_errors=True)
    work.mkdir(parents=True)
    runs_meta = []
    validation, summary = {}, {}

    def finish(passed, decision):
        summary['decision'] = decision
        (outdir / 'validation.json').write_text(json.dumps({'checks': validation, 'all_checks_passed': passed},
                                                           indent=2, default=e151.json_default), encoding='utf-8')
        (outdir / 'summary.json').write_text(json.dumps(summary, indent=2, default=e151.json_default),
                                             encoding='utf-8')
        man = {'generated_utc': datetime.now(timezone.utc).isoformat(), 'tool': 'scripts/e1_5_4_multiuser.py',
               'git_head_at_run': args.git_head, 'python': platform.python_version(), 'numpy': np.__version__,
               'config_sha256': e151.sha256(args.config), 'program_sha256': prog_sha,
               'ns3_commit': c151['stack']['ns3_commit'], 'nr_commit': c151['stack']['nr_commit'],
               'wall_time_s': time.time() - t0, 'runs': runs_meta, 'all_checks_passed': passed}
        (outdir / 'manifest.json').write_text(json.dumps(man, indent=2, default=e151.json_default), encoding='utf-8')

    # ---- R6 regression gates
    r6 = r6_gates(ns3, outdir, work, args.git_head)
    validation.update(r6)
    if not all(v['passed'] for v in r6.values()):
        finish(False, {'note': 'R6 failed: stop and diagnose (pre-registered)'})
        raise SystemExit('R6 failed: stop')

    # ---- analytic references (E1.5.3a C-AMC T2 curves)
    s3a = json.loads((E153A_RESULTS / 'summary.json').read_text())['stage_b']
    pts = {d: [tuple(p) for p in s3a['curves'][f't2_{d}'] if -10 <= p[0] <= 35] for d in DIRS}
    f3_shift = 10 * math.log10(N_RB / F3_RB)
    analytic = {'F2': {d: e153.required_snr_empirical(pts[d], TARGET[d] * K_UE) for d in DIRS},
                'F3': {'UL': e153.required_snr_empirical(pts['UL'], TARGET['UL'] * N_RB / F3_RB) - f3_shift,
                       'DL': e153.required_snr_empirical(pts['DL'], TARGET['DL'] * N_RB / F3_RB)}}
    summary['analytic_thresholds_db'] = analytic
    cfg = diag.carrier_cfg(c, st['carrier_hz'])

    def snr(L, d, n=N_RB):
        if d == 'DL':
            return float(lb.downlink(np.array([L]), cfg)['snr_db'][0])
        return c['ue_tx_power_dbm'] - L - float(lb.noise_dbm(n * cfg.prb_bw_hz, c['nf_gnb_db']))

    # ---- replay for the C-AMC MCS of H_TC and S_C
    keys = {L: {'DL': repr(10 ** (snr(L, 'DL') / 10)), 'UL51': repr(10 ** (snr(L, 'UL') / 10)),
                'UL5': repr(10 ** (snr(L, 'UL', F3_RB) / 10))} for L in L_GRID}
    rd = work / 'replay'
    rd.mkdir(parents=True)
    allk = sorted({k for v in keys.values() for k in v.values()}, key=float)
    (rd / 'replay.csv').write_text('key,sinr_lin\n' + ''.join(f'{k},{k}\n' for k in allk), encoding='utf-8')
    diag.write_losses(rd, {'u0': 100.0})
    a = link_args(c, st, rd, TDMA) + [f'--amcReplay={rd / "replay.csv"}']
    cmd, rc, rt = run_one(ns3, {'rd': rd, 'args': a})
    h = {f: e151.sha256(rd / f) for f in ('replay.csv', 'e153a_amc_choice.csv', 'e153a_tbler.csv') if (rd / f).exists()}
    dst = outdir / 'raw' / 'replay'
    shutil.rmtree(dst, ignore_errors=True)
    shutil.copytree(rd, dst)
    runs_meta.append({'tag': 'replay', 'cmd': cmd, 'exit_code': rc, 'runtime_s': rt, 'raw_sha256': h})
    if rc != 0:
        raise SystemExit('replay failed')
    choice, tbl = e153a.read_replay(rd)
    max_mcs = next(iter(choice.values()))['max_mcs']
    fixed_h, fixed_s = {}, {}
    for L in L_GRID:
        k = keys[L]
        fixed_h[L] = {'DL': choice[k['DL']]['dl_mimo_mcs'], 'UL': choice[k['UL51']]['ul_mimo_mcs']}
        fixed_s[L] = {'DL': e153a.rule_first_fail({m: tbl[(k['DL'], F3_RB, 12, m)][2] for m in range(max_mcs + 1)},
                                                  max_mcs),
                      'UL': e153a.rule_first_fail({m: tbl[(k['UL5'], F3_RB, 12, m)][2] for m in range(max_mcs + 1)},
                                                  max_mcs)}
    summary['fixed_mcs'] = {'H_TC': {f'L{L:.0f}': v for L, v in fixed_h.items()},
                            'S_C': {f'L{L:.0f}': v for L, v in fixed_s.items()}}

    # ---- run specs: parts H and S
    specs = []

    def link_spec(tag, part, losses, scheduler, rng=1, extra=(), fixed=None, meta=None):
        rdir = work / tag
        rdir.mkdir(parents=True)
        diag.write_losses(rdir, {f'u{i}': L for i, L in enumerate(losses)})
        ex = list(extra)
        if fixed:
            ex += [f'--fixedMcsDl={fixed["DL"]}', f'--fixedMcsUl={fixed["UL"]}']
        return {'tag': tag, 'part': part, 'mode': 'link', 'rd': rdir, 'scheduler': scheduler, 'n_ue': len(losses),
                'loss': list(losses), 'fixed': fixed, 'meta': meta or {},
                'args': link_args(c, st, rdir, scheduler, rng, ex)}

    for L in L_GRID:
        specs.append(link_spec(f'H_T_L{L:.0f}', 'H_T', [L] * K_UE, TDMA, meta={'L': L}))
        specs.append(link_spec(f'H_TC_L{L:.0f}', 'H_TC', [L] * K_UE, TDMA, fixed=fixed_h[L], meta={'L': L}))
        specs.append(link_spec(f'H_O_L{L:.0f}', 'H_O', [L] * K_UE, OFDMA, meta={'L': L}))
        specs.append(link_spec(f'S_L{L:.0f}', 'S', [L], TDMA, extra=['--dataRbs=5'], meta={'L': L}))
        specs.append(link_spec(f'S_C_L{L:.0f}', 'S_C', [L], TDMA, extra=['--dataRbs=5'], fixed=fixed_s[L],
                               meta={'L': L}))

    # ---- part M
    med, _ = e151.median_losses()
    m = conf['parts']['M']
    drops = {}
    for mc in m['configs']:
        gnb, links = e1520.build_links(mc['frequency_hz'], mc['rows'], mc['cols'], med)
        sec = [l for l in links if l['bearing_deg'] == 30.0]
        sel = sorted(sec, key=lambda l: l['loss_db'])[-K_UE:]
        sel = [l for l in sec if l in sel]          # keep E1.1 order
        if len(sel) != K_UE:
            raise SystemExit(f'{mc["tag"]}: drop has {len(sel)} UEs')
        drops[mc['tag']] = (gnb, sel)
    summary['drops'] = {t: [{'name': l['name'], 'los': l['los'], 'loss_db': l['loss_db']} for l in v[1]]
                        for t, v in drops.items()}

    def fading_spec(tag, part, mc, seed, scheduler, extra=()):
        gnb, sel = drops[mc['tag']]
        rdir = work / tag
        rdir.mkdir(parents=True)
        e1520.write_links(rdir / 'links.csv', sel)
        return {'tag': tag, 'part': part, 'mode': 'nrfading', 'rd': rdir, 'scheduler': scheduler, 'n_ue': K_UE,
                'loss': [l['loss_db'] for l in sel], 'fixed': None, 'meta': {'config': mc['tag'], 'seed': seed},
                'args': fading_args(c, rdir, mc['frequency_hz'], gnb, mc['rows'], mc['cols'], scheduler, seed,
                                    extra)}

    for mc in m['configs']:
        for seed in m['seeds']:
            specs.append(fading_spec(f'A_{mc["tag"]}_s{seed}', 'A', mc, seed, TDMA))
    for mc in m['configs'][:2]:                           # a_f3p5, a_f7p2 carry the two carriers' losses
        for seed in m['seeds']:
            losses = [l['loss_db'] for l in drops[mc['tag']][1]]
            specs.append(link_spec(f'Dt_{mc["tag"][2:]}_s{seed}', 'Dt', losses, TDMA, seed,
                                   meta={'config': mc['tag'], 'seed': seed}))
            specs.append(link_spec(f'Do_{mc["tag"][2:]}_s{seed}', 'Do', losses, OFDMA, seed,
                                   meta={'config': mc['tag'], 'seed': seed}))
    specs.append(fading_spec('B_a_f7p2_s1', 'B', m['configs'][1], 1, OFDMA, ['--sharedBeam=1']))
    failed = execute(ns3, specs, outdir, runs_meta, workers)
    if failed:
        finish(False, {'note': f'runs failed: {failed}'})
        raise SystemExit(f'runs failed: {failed}')

    # ---- arms Ag and C from arm A's gains
    specs2 = []
    gain_rep = {}
    for mc in m['configs']:
        for seed in m['seeds']:
            ra = work / f'A_{mc["tag"]}_s{seed}'
            g = defaultdict(list)
            for r in e151.read_csv(ra / 'e154_rb_gain.csv'):
                g[(int(r['ue_index']), r['dir'])].append(float(r['gain_lin']))
            loss_a = [l['loss_db'] for l in drops[mc['tag']][1]]
            leff = [loss_a[u] - 10 * math.log10(np.mean(g[(u, 'DL')])) for u in range(K_UE)]
            gain_rep[f'{mc["tag"]}_s{seed}'] = {
                'gain_db': [10 * math.log10(np.mean(g[(u, 'DL')])) for u in range(K_UE)],
                'max_abs_ul_dl_mean_gain_db': max(abs(10 * math.log10(np.mean(g[(u, 'UL')]) / np.mean(g[(u, 'DL')])))
                                                  for u in range(K_UE))}
            specs2.append(link_spec(f'Ag_{mc["tag"]}_s{seed}', 'Ag', leff, TDMA, seed,
                                    meta={'config': mc['tag'], 'seed': seed}))
            specs2.append(link_spec(f'C_{mc["tag"]}_s{seed}', 'C', leff, OFDMA, seed,
                                    meta={'config': mc['tag'], 'seed': seed}))
    summary['arm_A_gains'] = gain_rep
    failed = execute(ns3, specs2, outdir, runs_meta, workers)
    if failed:
        finish(False, {'note': f'runs failed: {failed}'})
        raise SystemExit(f'runs failed: {failed}')
    specs += specs2

    # ---- analysis
    gate_fail = defaultdict(list)
    per_run, per_ue_rows = {}, []
    for s in specs:
        gates, met, det = analyse_run(s, c, cfg, window)
        for gname, ok in gates.items():
            if not ok:
                gate_fail[gname].append(s['tag'])
        if s['mode'] == 'nrfading':
            # effective loss per direction: prescribed loss minus the mean per-RB beam gain
            g = defaultdict(list)
            for r in e151.read_csv(s['rd'] / 'e154_rb_gain.csv'):
                g[(int(r['ue_index']), r['dir'])].append(float(r['gain_lin']))
            snrs = {d: [snr(s['loss'][u] - 10 * math.log10(np.mean(g[(u, d)])), d) for u in range(s['n_ue'])]
                    for d in DIRS}
        else:
            snrs = {d: [snr(L, d) for L in s['loss']] for d in DIRS}
        per_run[s['tag']] = {'part': s['part'], 'meta': s['meta'], 'gates': gates, 'detail': det,
                             'metrics': met, 'snr_db': snrs, 'fixed': s['fixed']}
        for d in DIRS:
            for q in met[d]['ue']:
                per_ue_rows.append([s['tag'], s['part'], s['meta'].get('config', ''), s['meta'].get('seed', ''),
                                    d, q['ue'], repr(snrs[d][q['ue']]), repr(q['se_user']), repr(q['share']),
                                    repr(q['mbps']), q['tbs'], '' if q['bler_first_tx'] is None else repr(q['bler_first_tx']),
                                    int(q['satisfied'])])
    for gname in ('E0', 'RES', 'V', 'POP', 'BEAM', 'FIX'):
        applicable = [t for t, r in per_run.items() if gname in r['gates']]
        validation[gname] = {'runs_checked': len(applicable), 'failed_runs': gate_fail[gname],
                             'passed': not gate_fail[gname] and len(applicable) > 0}
    validation['V']['max_abs_err_db'] = max(max(r['detail']['max_abs_sinr_err_db'].values()) for r in per_run.values())
    with open(outdir / 'per_ue.csv', 'w', newline='', encoding='utf-8') as fh:
        w = csv.writer(fh, lineterminator='\n')
        w.writerow(['tag', 'part', 'config', 'seed', 'dir', 'ue', 'snr_db', 'se_user', 'share', 'mbps', 'tbs',
                    'bler_first_tx', 'satisfied'])
        w.writerows(per_ue_rows)
    gates_ok = all(v['passed'] for v in validation.values())

    # ---- curves, thresholds, predictions
    curves = defaultdict(dict)
    for t, r in per_run.items():
        if r['part'] in ('H_T', 'H_TC', 'H_O', 'S', 'S_C'):
            for d in DIRS:
                ses = [q['se_user'] for q in r['metrics'][d]['ue']]
                curves[r['part']].setdefault(d, []).append((r['snr_db'][d][0], float(np.mean(ses)), float(min(ses))))
    thr = {p: {d: threshold([(x, y) for x, y, _ in v[d]], TARGET[d]) for d in DIRS} for p, v in curves.items()}
    thr_min = {p: {d: threshold([(x, z) for x, _, z in v[d]], TARGET[d]) for d in DIRS} for p, v in curves.items()}
    summary.update({'curves': curves, 'thresholds_db': thr, 'thresholds_min_ue_db': thr_min, 'per_run': per_run})

    def within(a, b):
        return a is not None and b is not None and abs(a - b) <= 1.0

    shares = [q['share'] for t, r in per_run.items() if r['part'] in ('H_T', 'H_TC')
              for d in DIRS for q in r['metrics'][d]['ue'] if r['metrics'][d]['res_dir'] > 0]
    pred = {'P1_tdma_equal_share': {'min': min(shares), 'max': max(shares),
                                    'held': all(0.09 <= x <= 0.11 for x in shares)},
            'P2_F2': {d: {'packet': thr['H_TC'][d], 'analytic': analytic['F2'][d]} for d in DIRS},
            'P3_F3': {d: {'packet': thr['S_C'][d], 'analytic': analytic['F3'][d]} for d in DIRS},
            'P4_dynamic_ofdma': {'H_O_UL': thr['H_O']['UL'], 'S_UL': thr['S']['UL']}}
    pred['P2_F2']['held'] = all(within(pred['P2_F2'][d]['packet'], pred['P2_F2'][d]['analytic']) for d in DIRS)
    pred['P3_F3']['held'] = all(within(pred['P3_F3'][d]['packet'], pred['P3_F3'][d]['analytic']) for d in DIRS)
    pred['P4_dynamic_ofdma']['held'] = within(thr['H_O']['UL'], thr['S']['UL'])
    ratios = defaultdict(list)
    for mc in m['configs']:
        for seed in m['seeds']:
            ra, rg = per_run[f'A_{mc["tag"]}_s{seed}'], per_run[f'Ag_{mc["tag"]}_s{seed}']
            for d in DIRS:
                for qa, qg in zip(ra['metrics'][d]['ue'], rg['metrics'][d]['ue']):
                    if qg['se_user'] > 0:
                        ratios[(mc['tag'], d)].append(qa['se_user'] / qg['se_user'])
    p5 = {f'{k[0]}_{k[1]}': {'median': float(np.median(v)), 'min': float(min(v)), 'max': float(max(v)), 'n': len(v)}
          for k, v in ratios.items()}
    pred['P5_selectivity'] = dict(p5, held=all(x['median'] < 1 for x in p5.values()))
    h = [r['detail']['h18'] for t, r in per_run.items() if r['part'] == 'H_O' and r['snr_db']['UL'][0] >= 0]
    nu, fu = sum(x['n_up'] for x in h), sum(x['fail_up'] for x in h)
    nd, fd = sum(x['n_down'] for x in h), sum(x['fail_down'] for x in h)
    bu, bd = (fu / nu if nu else None), (fd / nd if nd else None)
    pred['P6_H18'] = {'n_up': nu, 'fail_up': fu, 'n_down': nd, 'fail_down': fd, 'bler_up': bu, 'bler_down': bd,
                      'held': bu is not None and bd is not None and bu - bd >= 0.10}
    summary['predictions'] = pred

    # ---- reported: mixed drops against the homogeneous curves and the analytic classification
    rep = {}
    for t, r in per_run.items():
        if r['part'] not in ('A', 'Ag', 'C', 'Dt', 'Do', 'B'):
            continue
        ref_part = 'H_T' if r['part'] in ('A', 'Ag', 'Dt') else 'S'
        model = 'F2' if r['part'] in ('A', 'Ag', 'Dt') else 'F3'
        rr = {}
        for d in DIRS:
            cur = [(x, y) for x, y, _ in curves[ref_part][d]]
            ues = []
            conf_m = Counter()
            for q in r['metrics'][d]['ue']:
                x = r['snr_db'][d][q['ue']]
                ref = interp_curve(cur, x)
                pred_sat = x >= analytic[model][d]
                conf_m[(int(pred_sat), int(q['satisfied']))] += 1
                ues.append({'ue': q['ue'], 'snr_db': x, 'se_user': q['se_user'], 'share': q['share'],
                            'ref_curve': ref, 'ratio': (q['se_user'] / ref) if ref else None})
            rr[d] = {'ues': ues, 'confusion_pred_meas': {f'{k[0]}{k[1]}': v for k, v in conf_m.items()},
                     'unserved': [q['ue'] for q in r['metrics'][d]['ue'] if q['tbs'] == 0],
                     'zero_throughput': [q['ue'] for q in r['metrics'][d]['ue'] if q['bits'] == 0]}
        rep[t] = rr
    summary['mixed'] = rep

    decision = {
        'gates_passed': gates_ok,
        'F2_for_E1_5_5': ('analytic F2 (C-AMC), H_TC as sensitivity' if pred['P2_F2']['held']
                          else 'H_TC packet thresholds primary, analytic F2 as sensitivity'),
        'F3_for_E1_5_5': ('analytic F3 (C-AMC), S_C as sensitivity' if pred['P3_F3']['held']
                          else 'S_C packet thresholds primary, analytic F3 as sensitivity'),
        'dynamic_ofdma': ('5G-LENA OfdmaPF UL realises F3 within 1 dB' if pred['P4_dynamic_ofdma']['held']
                          else 'hazard 18: 5G-LENA OfdmaPF UL does not realise F3; F3 = static partition bound'),
        'selectivity_sensitivity_needed': any(x['median'] < 0.9 for k, x in p5.items() if isinstance(x, dict))}
    if not gates_ok:
        decision = {'gates_passed': False, 'note': 'a gate failed: stop and diagnose; predictions not evaluated'}
    finish(gates_ok, decision)
    for k, v in validation.items():
        print(k, 'PASS' if v['passed'] else 'FAIL', flush=True)
    print('E1.5.4', 'GATES PASSED' if gates_ok else 'GATES FAILED (see validation.json)')
    sys.exit(0 if gates_ok else 1)


if __name__ == '__main__':
    main()
