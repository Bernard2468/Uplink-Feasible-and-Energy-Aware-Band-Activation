#!/usr/bin/env python3
"""E1.5.2-0: antenna and beamforming conventions of ns-3.48 / 5G-LENA v5.1 vs E1.4.

Implements configs/e1/e1_5_2_0_conventions.json. Run inside WSL with the project
venv. Checks R0 (mode link unchanged), A0 (deterministic conventions), N0
(channel normalisation), B0 (transmit/receive role of the DirectPath vector) and
V0 (NR TB SINR = channel-level computation). Outputs in --outdir:
validation.json, summary.json, manifest.json; raw files in raw/ (git-ignored).
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

E151_CONFIG = ROOT / 'configs/e1/e1_5_1_deterministic.json'
E151_RESULTS = ROOT / 'results/phase2/e1_5_1_deterministic'
POSITIONS = ROOT / 'configs/e1/positions_munich_tx0.json'
LINKS_HEADER = ['link_index', 'name', 'x', 'y', 'z', 'los', 'o2i', 'bearing_deg', 'loss_db']


# ---------------------------------------------------------------- inputs
def build_links(frequency_hz, rows, cols, medians):
    """The 27 E1.1 links with E1.4 serving sector, Python angles and gain."""
    geo = json.loads(POSITIONS.read_text(encoding='utf-8'))
    gnb = geo['gnb']['position']
    panel = ant.GnbPanel(rows=rows, cols=cols)
    out = []
    for p in geo['positions']:
        zen, az = ant.link_angles(gnb, p['position'])
        g, idx = ant.gnb_gain_dbi(panel, zen, az)
        out.append({'name': p['id'], 'x': p['position'][0], 'y': p['position'][1], 'z': p['position'][2],
                    'los': int(p['geometric_class'] == 'LOS'), 'o2i': 0,
                    'bearing_deg': ant.TR38901_SECTOR_BEARINGS_DEG[int(idx[0])],
                    'loss_db': medians[frequency_hz][p['id']],
                    'py_zenith_deg': float(zen[0]), 'py_azimuth_deg': float(az[0]), 'py_gain_db': float(g[0])})
    return gnb, out


def write_links(path, links):
    with open(path, 'w', newline='', encoding='utf-8') as fh:
        w = csv.writer(fh, lineterminator='\n')
        w.writerow(LINKS_HEADER)
        for i, l in enumerate(links):
            w.writerow([i, l['name'], repr(l['x']), repr(l['y']), repr(l['z']), l['los'], l['o2i'],
                        repr(l['bearing_deg']), repr(l['loss_db'])])


def run_program(ns3, rundir, args):
    cmd = ['./ns3', 'run', '--no-build', f'--cwd={rundir}', ' '.join([e151.PROGRAM] + args)]
    t0 = time.time()
    p = subprocess.run(cmd, cwd=ns3, capture_output=True, text=True)
    rt = time.time() - t0
    (rundir / 'run.log').write_text(f'cmd: {cmd}\nexit: {p.returncode}\nruntime_s: {rt:.1f}\n'
                                    f'--- stdout\n{p.stdout}\n--- stderr\n{p.stderr}\n', encoding='utf-8')
    return cmd, p.returncode, rt


def keep(rundir, dst, files):
    if dst.exists():
        shutil.rmtree(dst)
    dst.mkdir(parents=True)
    h = {}
    for f in files:
        if (rundir / f).exists():
            shutil.copy2(rundir / f, dst / f)
            h[f] = e151.sha256(dst / f)
    return h


# ---------------------------------------------------------------- analysis
def a0_check(det, links, n_elems):
    """Deterministic conventions: angles, transmit-side array factor, total gain."""
    worst = {'zenith_deg': 0.0, 'azimuth_deg': 0.0, 'af_s_db': 0.0, 'gain_db': 0.0}
    nominal = 10 * math.log10(n_elems)
    per = []
    for r, l in zip(det, links):
        if r['name'] != l['name'] or float(r['bearing_deg']) != l['bearing_deg'] or int(r['n_elems']) != n_elems:
            raise SystemExit(f'det row does not match link {l["name"]}')
        dz = abs(float(r['zenith_deg']) - l['py_zenith_deg'])
        da = abs(float(ant.wrap_deg(float(r['azimuth_deg']) - l['py_azimuth_deg'])))
        daf = abs(float(r['af_s_db']) - nominal)
        g = float(r['elem_gain_db']) + float(r['af_s_db'])
        dg = abs(g - l['py_gain_db'])
        worst = {'zenith_deg': max(worst['zenith_deg'], dz), 'azimuth_deg': max(worst['azimuth_deg'], da),
                 'af_s_db': max(worst['af_s_db'], daf), 'gain_db': max(worst['gain_db'], dg)}
        per.append({'name': l['name'], 'ns3_gain_db': g, 'e14_gain_db': l['py_gain_db'],
                    'af_u_db': float(r['af_u_db']), 'af_s_db': float(r['af_s_db'])})
    passed = (worst['zenith_deg'] <= 1e-6 and worst['azimuth_deg'] <= 1e-6 and worst['af_s_db'] <= 1e-6
              and worst['gain_db'] <= 0.01)
    return {'max_abs_error': worst, 'passed': bool(passed)}, per


def realised(real, n_links):
    """Per link: 10 log10 of the mean linear gain over copies; and the gnb_is_s flags."""
    acc = defaultdict(list)
    flags = set()
    for r in real:
        acc[int(r['link_index'])].append(float(r['gain_lin']))
        flags.add(int(r['gnb_is_s']))
    if sorted(acc) != list(range(n_links)):
        raise SystemExit('missing links in realizations')
    return {i: 10 * math.log10(np.mean(v)) for i, v in acc.items()}, {i: v for i, v in acc.items()}, flags


def n0_check(samples, links, tol=0.25):
    out = {}
    for cls, val in (('LOS', 1), ('NLOS', 0)):
        pooled = [g for i, v in samples.items() if links[i]['los'] == val for g in v]
        m = 10 * math.log10(np.mean(pooled))
        out[cls] = {'realizations': len(pooled), 'mean_gain_db': m, 'passed': bool(abs(m) <= tol)}
    return out


def deficits(g_real, links):
    return {i: links[i]['py_gain_db'] - g for i, g in g_real.items()}


def median_los(values, links):
    v = [x for i, x in values.items() if links[i]['los'] == 1]
    return float(np.median(v)), len(v)


def v0_check(rundir, links, cfg, tol=0.01):
    """NR DL TB SINR vs SNR_iso + 10 log10 g_direct for every TB."""
    info = {r['key']: r['value'] for r in e151.read_csv(rundir / 'e15_run_info.csv')}
    direct = {int(r['ue_index']): r for r in e151.read_csv(rundir / 'e152_nr_direct.csv')}
    loss = np.array([l['loss_db'] for l in links])
    snr_iso = lb.downlink(loss, cfg)['snr_db']
    tbs, rows = e151.unique_tbs(e151.read_csv(rundir / 'e15_dl_tb.csv'), e151.DL_TB_KEY)
    worst, count = 0.0, defaultdict(int)
    for r in tbs:
        i = int(r['ue_index'])
        ref = snr_iso[i] + 10 * math.log10(float(direct[i]['gain_lin']))
        worst = max(worst, abs(10 * math.log10(float(r['sinr_lin'])) - ref))
        count[i] += 1
    roles_ok = all(int(direct[i]['gnb_is_s']) == 1 for i in range(len(links)))
    min_tbs = min(count.get(i, 0) for i in range(len(links)))
    f_ok = float(info['channel_model_frequency_hz_readback']) == cfg.frequency_hz
    return {'tbs': len(tbs), 'trace_rows': rows, 'max_abs_error_db': worst, 'gnb_is_s_all': roles_ok,
            'min_tbs_per_ue': min_tbs, 'frequency_readback_ok': f_ok,
            'passed': bool(worst <= tol and roles_ok and min_tbs >= 10 and f_ok)}, \
        {links[i]['name']: float(10 * math.log10(float(direct[i]['gain_lin']))) for i in range(len(links))}


# ---------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--config', default=str(ROOT / 'configs/e1/e1_5_2_0_conventions.json'))
    ap.add_argument('--outdir', default=str(ROOT / 'results/phase2/e1_5_2_0_conventions'))
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
    work = Path.home() / 'cmwave-runs/e1_5_2_0'
    validation, summary, runs_meta = {}, {}, []

    # R0: mode link unchanged (rerun of E1.5.1 f3p5_ofdma)
    ref = {r['tag']: r for r in json.loads((E151_RESULTS / 'manifest.json').read_text())['runs']}['f3p5_ofdma']
    run = next(r for r in c151['runs'] if r['tag'] == 'f3p5_ofdma')
    ues = dict(sorted(medians[run['frequency_hz']].items()))
    ues.update({f'sweep_{int(x)}dB': float(x) for x in c151['ues']['sweep_losses_db']})
    rundir = work / 'r0_link_f3p5_ofdma'
    shutil.rmtree(rundir, ignore_errors=True)
    rundir.mkdir(parents=True)
    diag.write_losses(rundir, ues)
    cmd = diag.build_cmd(c, run, rundir)
    p = subprocess.run(cmd, cwd=ns3, capture_output=True, text=True)
    differ = [f for f in diag.DATA_FILES if not (rundir / f).exists()
              or e151.sha256(rundir / f) != ref['raw_sha256'][f]]
    validation['R0_link_mode_unchanged'] = {'exit_code': p.returncode, 'differing_files': differ,
                                            'passed': p.returncode == 0 and not differ}
    runs_meta.append({'tag': 'r0_link_f3p5_ofdma', 'cmd': cmd, 'exit_code': p.returncode})
    print('R0', 'PASS' if validation['R0_link_mode_unchanged']['passed'] else 'FAIL', flush=True)

    # mode chan runs
    chan = {}
    for r in conf['chan_runs']:
        gnb, links = build_links(r['frequency_hz'], r['rows'], r['cols'], medians)
        rundir = work / r['tag']
        shutil.rmtree(rundir, ignore_errors=True)
        rundir.mkdir(parents=True)
        write_links(rundir / 'links.csv', links)
        a = [f'--mode=chan', f'--linksFile={rundir / "links.csv"}', f'--frequency={r["frequency_hz"]!r}',
             f'--bandwidth={c["bandwidth_hz"]!r}', f'--numerology={c["numerology"]}',
             f'--rbOverhead={c["rb_overhead"]!r}', f'--gnbX={gnb[0]!r}', f'--gnbY={gnb[1]!r}',
             f'--gnbZ={gnb[2]!r}', f'--gnbRows={r["rows"]}', f'--gnbCols={r["cols"]}',
             f'--gnbElement={r["element"]}', f'--copies={conf["copies"]}', f'--gnbRole={r["role"]}',
             f'--gnbWeights={r["weights"]}']
        cmd, code, rt = run_program(ns3, rundir, a)
        h = keep(rundir, outdir / 'raw' / r['tag'], ['links.csv', 'e152_det.csv', 'e152_real.csv', 'e152_info.csv',
                                                     'run.log'])
        runs_meta.append({'tag': r['tag'], 'cmd': cmd, 'exit_code': code, 'runtime_s': rt, 'raw_sha256': h})
        print(f"{r['tag']}: exit {code}, {rt:.0f} s", flush=True)
        if code != 0:
            raise SystemExit(f'{r["tag"]} failed; see {rundir / "run.log"}')
        info = {x['key']: x['value'] for x in e151.read_csv(rundir / 'e152_info.csv')}
        if float(info['frequency_hz_readback']) != r['frequency_hz'] or info['scenario_readback'] != 'UMa' \
                or int(info['num_rbs']) != 51:
            raise SystemExit(f'{r["tag"]}: channel model readback mismatch {info}')
        det = e151.read_csv(rundir / 'e152_det.csv')
        g_real, samples, flags = realised(e151.read_csv(rundir / 'e152_real.csv'), len(links))
        chan[r['tag']] = {'run': r, 'links': links, 'det': det, 'g_real': g_real, 'samples': samples,
                          'flags': flags}

    # A0
    a0 = {}
    for tag, n in (('a_f3p5_s_dp', 128), ('a_f7p2_s_dp', 128), ('b_f7p2_s_dp', 512)):
        res, per = a0_check(chan[tag]['det'], chan[tag]['links'], n)
        a0[tag] = res
        summary.setdefault('deterministic', {})[tag] = per
    validation['A0_conventions'] = dict(a0, passed=all(x['passed'] for x in a0.values()))

    # N0
    n0 = {tag: n0_check(chan[tag]['samples'], chan[tag]['links']) for tag in ('n0_f3p5', 'n0_f7p2')}
    validation['N0_normalisation'] = dict(n0, passed=all(v['passed'] for x in n0.values() for v in x.values()))

    # B0
    role_ok = {tag: (chan[tag]['flags'] == ({1} if chan[tag]['run']['role'] == 's' else {0}))
               for tag in chan if chan[tag]['run']['element'] == '3gpp'}
    validation['B0_0_roles'] = {'per_run': role_ok, 'passed': all(role_ok.values())}
    b1, b2, b3 = {}, {}, {}
    for f in ('f3p5', 'f7p2'):
        L = chan[f'a_{f}_s_dp']['links']
        d_s = deficits(chan[f'a_{f}_s_dp']['g_real'], L)
        d_uc = deficits(chan[f'a_{f}_u_dpconj']['g_real'], L)
        diff = {i: chan[f'a_{f}_s_dp']['g_real'][i] - chan[f'a_{f}_u_dp']['g_real'][i] for i in range(len(L))}
        m1, n = median_los(d_s, L)
        m2, _ = median_los(d_uc, L)
        m3, _ = median_los(diff, L)
        b1[f] = {'median_los_deficit_db': m1, 'los_links': n, 'passed': bool(m1 <= 2.0)}
        b2[f] = {'median_los_deficit_db': m2, 'los_links': n, 'passed': bool(m2 <= 2.0)}
        b3[f] = {'median_los_s_minus_u_db': m3, 'los_links': n, 'passed': bool(m3 >= 3.0)}
        per = []
        for i, l in enumerate(L):
            row = {'name': l['name'], 'los': l['los'], 'bearing_deg': l['bearing_deg'], 'e14_gain_db': l['py_gain_db']}
            for tag in (f'a_{f}_s_dp', f'a_{f}_u_dp', f'a_{f}_u_dpconj', f'a_{f}_s_dpconj'):
                row[f'g_real_{tag[len(f) + 3:]}_db'] = chan[tag]['g_real'][i]
            per.append(row)
        summary.setdefault('realised_regime_a', {})[f] = per
    validation['B0_1_matched_s'] = dict(b1, passed=all(x['passed'] for x in b1.values()))
    validation['B0_2_matched_u'] = dict(b2, passed=all(x['passed'] for x in b2.values()))
    validation['B0_3_antimatched_u'] = dict(b3, passed=all(x['passed'] for x in b3.values()))
    Lb = chan['b_f7p2_s_dp']['links']
    summary['realised_regime_b_f7p2_s_dp'] = [{'name': l['name'], 'los': l['los'], 'e14_gain_db': l['py_gain_db'],
                                               'g_real_db': chan['b_f7p2_s_dp']['g_real'][i]}
                                              for i, l in enumerate(Lb)]
    summary['normalisation'] = {tag: {chan[tag]['links'][i]['name']: g for i, g in chan[tag]['g_real'].items()}
                                for tag in ('n0_f3p5', 'n0_f7p2')}
    print('A0', 'PASS' if validation['A0_conventions']['passed'] else 'FAIL',
          'N0', 'PASS' if validation['N0_normalisation']['passed'] else 'FAIL',
          'B0', [k for k in ('B0_0_roles', 'B0_1_matched_s', 'B0_2_matched_u', 'B0_3_antimatched_u')
                 if validation[k]['passed']], flush=True)

    # V0: NR stack
    s = conf['nrfading_runs']['settings']
    v0 = {}
    for r in conf['nrfading_runs']['configs']:
        gnb, links = build_links(r['frequency_hz'], r['rows'], r['cols'], medians)
        cfg = diag.carrier_cfg(c, r['frequency_hz'])
        for bearing in ant.TR38901_SECTOR_BEARINGS_DEG:
            sub = [l for l in links if l['bearing_deg'] == bearing]
            if not sub:
                continue
            tag = f"{r['tag']}_b{int(bearing)}"
            rundir = work / tag
            shutil.rmtree(rundir, ignore_errors=True)
            rundir.mkdir(parents=True)
            write_links(rundir / 'links.csv', sub)
            a = [f'--mode=nrfading', f'--linksFile={rundir / "links.csv"}', f'--frequency={r["frequency_hz"]!r}',
                 f'--bandwidth={c["bandwidth_hz"]!r}', f'--numerology={c["numerology"]}',
                 f'--rbOverhead={c["rb_overhead"]!r}', f'--gnbTxPower={c["gnb_tx_power_dbm"]!r}',
                 f'--nfGnb={c["nf_gnb_db"]!r}', f'--nfUe={c["nf_ue_db"]!r}', f'--gnbX={gnb[0]!r}',
                 f'--gnbY={gnb[1]!r}', f'--gnbZ={gnb[2]!r}', f'--gnbRows={r["rows"]}', f'--gnbCols={r["cols"]}',
                 f'--bearingDeg={bearing!r}', f'--scheduler={s["scheduler"]}',
                 f'--dlRate={s["dl_rate_bps_per_ue"]!r}', f'--packetSize={s["packet_size_bytes"]}',
                 f'--appStart={s["app_start_s"]!r}', f'--simTime={s["sim_time_s"]!r}',
                 f'--tddPattern={c["tdd_pattern"]}']
            cmd, code, rt = run_program(ns3, rundir, a)
            h = keep(rundir, outdir / 'raw' / tag, ['links.csv', 'e15_dl_tb.csv', 'e152_nr_direct.csv',
                                                    'e15_run_info.csv', 'e15_rrc_events.csv',
                                                    'e15_rb_after_attach.csv', 'run.log'])
            runs_meta.append({'tag': tag, 'cmd': cmd, 'exit_code': code, 'runtime_s': rt, 'raw_sha256': h})
            print(f'{tag}: exit {code}, {rt:.0f} s', flush=True)
            if code != 0:
                v0[tag] = {'exit_code': code, 'passed': False}
                continue
            res, g_direct = v0_check(rundir, sub, cfg)
            v0[tag] = res
            summary.setdefault('nr_direct_gain_db', {})[tag] = g_direct
    validation['V0_nr_identity'] = dict(v0, passed=bool(v0) and all(x['passed'] for x in v0.values()))
    print('V0', 'PASS' if validation['V0_nr_identity']['passed'] else 'FAIL', flush=True)

    passed = all(v['passed'] for v in validation.values())
    (outdir / 'validation.json').write_text(json.dumps({'checks': validation, 'all_checks_passed': passed},
                                                       indent=2, default=e151.json_default), encoding='utf-8')
    (outdir / 'summary.json').write_text(json.dumps(summary, indent=2, default=e151.json_default), encoding='utf-8')
    manifest = {'generated_utc': datetime.now(timezone.utc).isoformat(), 'tool': 'scripts/e1_5_2_0_conventions.py',
                'git_head_at_run': args.git_head, 'python': platform.python_version(), 'numpy': np.__version__,
                'config_sha256': e151.sha256(args.config), 'program_sha256': prog_sha,
                'ns3_commit': c151['stack']['ns3_commit'], 'nr_commit': c151['stack']['nr_commit'],
                'e1_2_realizations_sha256': real_sha, 'positions_sha256': e151.sha256(POSITIONS),
                'runs': runs_meta, 'all_checks_passed': passed}
    (outdir / 'manifest.json').write_text(json.dumps(manifest, indent=2, default=e151.json_default), encoding='utf-8')
    print('E1.5.2-0', 'PASSED' if passed else 'FAILED (see validation.json)')
    sys.exit(0 if passed else 1)


if __name__ == '__main__':
    main()
