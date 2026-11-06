#!/usr/bin/env python3
"""E1.2: geometry-conditioned TR 38.901 UMa propagation on the frozen E1.1 links.

Input : results/phase2/e1_geometry/<site>_links.csv + manifest (hash-checked).
Output: realizations.csv.gz (one row per realization, link, carrier),
        links_summary.csv (deterministic path loss per link and carrier),
        validation.json, manifest.json.
Checks:
  P1 deterministic path loss equals an independent re-evaluation of the
     TR 38.901 formulas from the geometry file
  P2 frequency gap per link is realization-independent and equals
     20 log10(f2/f1) for NLOS links and LOS links inside the breakpoint
  P3 pooled shadowing std per state within 0.5 dB of 4 dB (LOS) / 6 dB (NLOS)
  P4 LOS state of every row equals the frozen geometry
Reported (not a gate): deterministic UMa path loss vs the E1a.1 ray-traced
gain on the links where ray tracing was shown valid (LOS; near-NLOS).
"""
import argparse
import csv
import gzip
import hashlib
import io
import json
import math
import platform
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from cmwave.radio import pathloss_3gpp as pl3  # noqa: E402
from cmwave.radio import uma_hybrid as uh  # noqa: E402

RT_VALID_NLOS = ('ue03', 'ue11')  # near-NLOS links within ~3 dB of TR 38.901 in E1a.1


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--geometry-dir', default=str(ROOT / 'results/phase2/e1_geometry'))
    ap.add_argument('--site-id', default='site_tx0')
    ap.add_argument('--carriers', type=float, nargs=2, default=[3.5e9, 7.2e9])
    ap.add_argument('--realizations', type=int, default=200)
    ap.add_argument('--seed', type=int, default=20261107)
    ap.add_argument('--rt-links', default=str(ROOT / 'results/phase2/e1a_propagation/links.csv'))
    ap.add_argument('--outdir', default=str(ROOT / 'results/phase2/e1_3gpp_propagation'))
    args = ap.parse_args()
    f1, f2 = sorted(args.carriers)
    gdir, outdir = Path(args.geometry_dir), Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)

    gman = json.loads((gdir / f'{args.site_id}_manifest.json').read_text(encoding='utf-8'))
    links_csv = gdir / f'{args.site_id}_links.csv'
    if not gman['all_checks_passed'] or sha256(links_csv) != gman['files'][links_csv.name]:
        raise SystemExit('geometry not frozen or links file changed since E1.1')
    with links_csv.open(encoding='utf-8') as fh:
        links = [{k: (float(v) if k in ('x_m', 'y_m', 'z_m', 'h_ut_m', 'h_bs_m', 'd2d_m', 'd3d_m')
                      else v) for k, v in r.items()} for r in csv.DictReader(fh)]

    rows = uh.compute_links(links, [f1, f2], args.realizations, args.seed)
    checks = {}

    # P1: independent re-evaluation of the formulas.
    p1 = []
    for r in rows[: 2 * len(links)]:          # realization 0 covers every link/carrier
        l = next(x for x in links if x['ue_id'] == r['ue_id'])
        fn = pl3.uma_los if l['los_state'] == 'LOS' else pl3.uma_nlos
        ref = fn(l['d2d_m'], l['d3d_m'], r['frequency_hz'], l['h_bs_m'], l['h_ut_m'])
        if abs(ref - r['pathloss_db']) > 1e-9:
            p1.append({'ue': r['ue_id'], 'f': r['frequency_hz'], 'model': r['pathloss_db'],
                       'reference': ref})
    checks['P1_pathloss_reevaluation'] = {'passed': not p1, 'mismatches': p1}

    # P2: frequency gap per link.
    by = {(r['realization'], r['ue_id'], r['frequency_hz']): r for r in rows}
    gaps, p2 = {}, []
    for l in links:
        g = np.array([by[(k, l['ue_id'], f2)]['total_loss_db'] - by[(k, l['ue_id'], f1)]['total_loss_db']
                      for k in range(args.realizations)])
        gaps[l['ue_id']] = float(g[0])
        dbp = pl3.breakpoint_distance(f1, l['h_bs_m'], l['h_ut_m'])
        expect_exact = l['los_state'] == 'NLOS' or l['d2d_m'] <= dbp
        if np.ptp(g) > 1e-9 or (expect_exact and abs(g[0] - uh.frequency_gap_db(f1, f2)) > 1e-9):
            p2.append({'ue': l['ue_id'], 'gap_db': float(g[0]), 'spread_db': float(np.ptp(g))})
    checks['P2_frequency_gap'] = {'passed': not p2, 'violations': p2,
                                  'expected_gap_db': uh.frequency_gap_db(f1, f2)}

    # P3: pooled shadowing std per state (carrier f1 rows).
    p3 = {}
    for s in uh.STATES:
        sf = np.array([r['shadowing_db'] for r in rows
                       if r['los_state'] == s and r['frequency_hz'] == f1])
        p3[s] = {'n': int(sf.size), 'mean_db': float(sf.mean()), 'std_db': float(sf.std(ddof=1)),
                 'target_std_db': uh.SF_STD_DB[s],
                 'passed': abs(float(sf.std(ddof=1)) - uh.SF_STD_DB[s]) <= 0.5}
    checks['P3_shadowing_std'] = {'passed': all(v['passed'] for v in p3.values()), 'by_state': p3}

    # P4: LOS state preserved.
    state = {l['ue_id']: l['los_state'] for l in links}
    checks['P4_state_from_geometry'] = {'passed': all(r['los_state'] == state[r['ue_id']]
                                                      for r in rows)}

    # Reported: deterministic UMa vs E1a.1 ray tracing on validated links.
    rt_cmp = []
    if Path(args.rt_links).exists():
        with open(args.rt_links, encoding='utf-8') as fh:
            rt = [r for r in csv.DictReader(fh) if r['run'] == 'S2']
        for l in links:
            if not (l['los_state'] == 'LOS' or l['ue_id'] in RT_VALID_NLOS):
                continue
            for f in (f1, f2):
                m = [r for r in rt if r['ue'] == l['ue_id'] and float(r['freq_hz']) == f]
                if m and m[0]['total_gain_db']:
                    pl_rt = -float(m[0]['total_gain_db'])
                    pl_m = by[(0, l['ue_id'], f)]['pathloss_db']
                    rt_cmp.append({'ue': l['ue_id'], 'state': l['los_state'], 'frequency_hz': f,
                                   'rt_pathloss_db': pl_rt, 'uma_median_pathloss_db': pl_m,
                                   'rt_minus_uma_db': pl_rt - pl_m})

    # Byte-deterministic gzip (mtime 0, no embedded file name): reruns give identical files.
    with open(outdir / 'realizations.csv.gz', 'wb') as raw, \
            gzip.GzipFile(filename='', mode='wb', fileobj=raw, mtime=0) as gz, \
            io.TextIOWrapper(gz, encoding='utf-8', newline='') as fh:
        w = csv.DictWriter(fh, fieldnames=['site_id'] + list(rows[0].keys()), lineterminator='\n')
        w.writeheader()
        for r in rows:
            w.writerow({'site_id': args.site_id, **r})
    with (outdir / 'links_summary.csv').open('w', newline='', encoding='utf-8') as fh:
        w = csv.writer(fh, lineterminator='\n')
        w.writerow(['site_id', 'ue_id', 'los_state', 'distance_2d_m', 'distance_3d_m',
                    'pathloss_db_f1', 'pathloss_db_f2', 'gap_db', 'shadowing_std_db'])
        for l in links:
            w.writerow([args.site_id, l['ue_id'], l['los_state'], l['d2d_m'], l['d3d_m'],
                        by[(0, l['ue_id'], f1)]['pathloss_db'], by[(0, l['ue_id'], f2)]['pathloss_db'],
                        gaps[l['ue_id']], uh.SF_STD_DB[l['los_state']]])

    passed = all(v['passed'] for v in checks.values())
    (outdir / 'validation.json').write_text(json.dumps(
        {'checks': checks, 'all_checks_passed': passed,
         'reported_rt_comparison_validated_links': rt_cmp}, indent=2), encoding='utf-8')
    manifest = {
        'generated_utc': datetime.now(timezone.utc).isoformat(),
        'tool': 'scripts/e1_propagation_3gpp.py', 'model_version': uh.MODEL_VERSION,
        'python': platform.python_version(), 'numpy': np.__version__,
        'site_id': args.site_id, 'geometry_manifest_sha256': sha256(gdir / f'{args.site_id}_manifest.json'),
        'links_csv_sha256': sha256(links_csv), 'carriers_hz': [f1, f2],
        'realizations': args.realizations, 'seed': args.seed,
        'realizations_sha256': sha256(outdir / 'realizations.csv.gz'),
        'shadowing': {'std_db': uh.SF_STD_DB, 'corr_dist_m': uh.SF_CORR_DIST_M,
                      'multi_frequency': 'one normal variate per link shared by all carriers (TR 38.901 7.6.5)'},
        'departures_from_standard': ['LOS state from scene geometry, not Table 7.4.2-1',
                                     'gNB height 27 m (UMa nominal 25 m)'],
        'scope': 'outdoor large-scale propagation only; no antennas, powers, noise, O2I',
        'all_checks_passed': passed}
    (outdir / 'manifest.json').write_text(json.dumps(manifest, indent=2), encoding='utf-8')

    for k, v in checks.items():
        print(f"{k:28s} {'PASS' if v['passed'] else 'FAIL'}")
    for s, v in p3.items():
        print(f"  shadowing {s}: n={v['n']} mean {v['mean_db']:+.2f} dB std {v['std_db']:.2f} dB "
              f"(target {v['target_std_db']})")
    if rt_cmp:
        for st in ('LOS', 'NLOS'):
            for f in (f1, f2):
                d = [c['rt_minus_uma_db'] for c in rt_cmp if c['state'] == st and c['frequency_hz'] == f]
                if d:
                    print(f"  RT - UMa median, {st} {f/1e9:.1f} GHz: mean {np.mean(d):+.2f} dB, "
                          f"range [{min(d):+.2f}, {max(d):+.2f}] (n={len(d)})")
    print('E1.2 PROPAGATION', 'PASSED' if passed else 'FAILED')
    sys.exit(0 if passed else 1)


if __name__ == '__main__':
    main()
