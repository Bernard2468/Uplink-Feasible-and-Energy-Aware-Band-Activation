#!/usr/bin/env python3
"""E1.5.0: analytic pre-checks before packet-level validation.

Implements configs/e1/e1_5_0_surrogates.json:
  part A  E1.4 rerun with TR 38.901 V19.5.0 O2I materials (vs the V16.1.0 baseline)
  part B  F3 'OFDMA share' per-user uplink surrogate next to F2 'time share'
Reuses the E1.4 functions (scripts/e1_antennas_o2i.py) unchanged. Outputs in
--outdir: summary.json, validation.json, manifest.json.
"""
import argparse
import csv
import gzip
import json
import platform
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'scripts'))
import e1_antennas_o2i as e14  # noqa: E402
import e1_link_budget as e13  # noqa: E402
from cmwave.radio import link_budget as lb  # noqa: E402

VERSIONS = ('16.1.0', '19.5.0')
UNCHANGED_BY_V19 = ('outdoor', 'in_car', 'indoor_low', 'indoor_low_indep')


def class_and_feasible(res, f1, f2, profile, freqs):
    k = e14.classes14(res, f1, f2, profile)
    return ({f'{d}_{f/1e9:.1f}GHz': float(np.mean(res[f][d][profile])) for f in freqs for d in ('DL', 'UL')},
            {name: float(np.mean(v)) for name, v in k.items()}, k)


def mix(per_state, weights, key):
    first = next(iter(weights))
    return {n: sum(weights[s] * per_state[s][key][n] for s in weights) for n in per_state[first][key]}


def evaluate_version(conf, version, inputs, k_users, e13_inputs, f3_ul_min):
    gman, links, prop, bins = inputs
    c = dict(conf)
    c['o2i_tr38901_version'] = version
    states, geo, draws, _, tw_used = e14.build_states(c, gman, links, prop)
    freqs = geo['freqs']
    f1, f2 = freqs
    out_all, e14_checks, f3, s4 = {}, {}, {}, {'allocation_violations': 0, 'dominance_violations': 0,
                                               'f2_yes_f3_no_ul_records': 0}
    part6 = {'exclusive_and_exhaustive': True, 'k6_shared': 0}
    for cname, case in conf['cases'].items():
        out = e14.evaluate(case, states, geo, conf['thresholds'])
        out_all[cname] = out
        e14_checks[cname] = e14.run_checks(cname, case, out, states, geo, tw_used, e13_inputs[0], e13_inputs[1])
        f3[cname] = {}
        for state in e14.STATES:
            res3 = {}
            for f in freqs:
                r = out[state][f]
                cfg = r['cfg']
                n_max = cfg.n_prb // k_users
                ul3 = lb.uplink(states[state]['loss'][f], cfg, gain_db=r['g_ul'], n_max_prb=n_max)
                f3_ul = ul3['se_norm_bps_hz'] >= f3_ul_min
                ul_full = r['UL']
                s4['allocation_violations'] += int(np.sum((ul3['best_n_prb'] > n_max)
                                                          | (ul3['rate_bps'] > ul_full['rate_bps'] + 1e-9)))
                lost = ul_full['F2'] & ~f3_ul
                s4['f2_yes_f3_no_ul_records'] += int(np.sum(lost))
                s4['dominance_violations'] += int(np.sum(lost & (ul_full['best_n_prb'] <= 10 * n_max)))
                res3[f] = {'DL': {'F3': r['DL']['F2']}, 'UL': {'F3': f3_ul}}
            ff, cf, k = class_and_feasible(res3, f1, f2, 'F3', freqs)
            part6['exclusive_and_exhaustive'] &= bool(np.all(np.sum([k[n] for n in e14.PARTITION], axis=0) == 1))
            if state in e14.SHARED_STATES:
                part6['k6_shared'] += int(np.sum(k[e14.K6]))
            f3[cname][state] = {'feasible_fraction': ff, 'class_fraction': cf}
    summ = {cname: e14.summarise(cname, case, out_all[cname], geo, bins, conf['mixes'])
            for cname, case in conf['cases'].items()}
    for cname in conf['cases']:
        summ[cname]['F3_ofdma_share'] = {'per_state': f3[cname],
                                         'mixes': {m: {'feasible_fraction': mix(f3[cname], w['weights'], 'feasible_fraction'),
                                                       'class_fraction': mix(f3[cname], w['weights'], 'class_fraction')}
                                                   for m, w in conf['mixes'].items()}}
    return summ, e14_checks, s4, part6, tw_used


def equal_fractions(a, b, states=None):
    """Float equality of every F1/F2 fraction of two E1.4-style summaries."""
    diffs = 0
    for cname in a:
        for state, st in a[cname]['per_state'].items():
            if states is not None and state not in states:
                continue
            for p in ('F1', 'F2'):
                for key in ('feasible_fraction', 'class_fraction'):
                    for n, x in st[p][key].items():
                        diffs += int(x != b[cname]['per_state'][state][p][key][n])
                for bn, row in st[p]['by_distance_bin'].items():
                    for n, x in row.items():
                        diffs += int(x != b[cname]['per_state'][state][p]['by_distance_bin'][bn][n])
        if states is None:
            for m, mm in a[cname]['mixes'].items():
                for p in ('F1', 'F2'):
                    for n, x in mm[p]['class_fraction'].items():
                        diffs += int(x != b[cname]['mixes'][m][p]['class_fraction'][n])
    return diffs


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--config', default=str(ROOT / 'configs/e1/e1_5_0_surrogates.json'))
    ap.add_argument('--e14-config', default=str(ROOT / 'configs/e1/e1_4_antennas_o2i.json'))
    ap.add_argument('--e14-dir', default=str(ROOT / 'results/phase2/e1_4_antennas_o2i'))
    ap.add_argument('--outdir', default=str(ROOT / 'results/phase2/e1_5_0_surrogates'))
    ap.add_argument('--git-head', default=None)
    args = ap.parse_args()
    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    conf5 = json.loads(Path(args.config).read_text(encoding='utf-8'))
    conf = json.loads(Path(args.e14_config).read_text(encoding='utf-8'))
    e14man = json.loads(Path(args.e14_dir, 'manifest.json').read_text(encoding='utf-8'))
    if (not e14man['all_checks_passed'] or e13.sha256(args.e14_config) != e14man['config_sha256']
            or e13.sha256(Path(args.e14_dir, 'summary.json')) != e14man['outputs_sha256']['summary.json']):
        raise SystemExit('E1.4 results not validated or config/summary changed')
    e14_summary = json.loads(Path(args.e14_dir, 'summary.json').read_text(encoding='utf-8'))
    pb = conf5['part_B_multiple_access_surrogate']
    k_users = int(pb['users_per_trxp'])
    f3_ul_min = float(pb['F3_ofdma_share']['thresholds']['UL_se_norm_min_per_user'])

    eargs = argparse.Namespace(geometry_dir=str(ROOT / 'results/phase2/e1_geometry'),
                               propagation_dir=str(ROOT / 'results/phase2/e1_3gpp_propagation'),
                               e13_dir=str(ROOT / 'results/phase2/e1_3_link_budget'))
    gman, links, pman, prop, e3man, e3rec, e3dir = e14.load_inputs(eargs)
    with Path(eargs.geometry_dir, 'site_tx0_links.csv').open(encoding='utf-8') as fh:
        bins = {r['ue_id']: r['distance_bin_m'] for r in csv.DictReader(fh)}
    e13_ref = {}
    with gzip.open(e3rec, 'rt', encoding='utf-8') as fh:
        for r in csv.DictReader(fh):
            if r['case'] == 'main_eqbw20':
                e13_ref[(r['ue_id'], int(r['realization']), float(r['frequency_hz']), r['direction'])] = {
                    'snr_db': float(r['snr_db']), 'rate_bps': float(r['rate_bps']),
                    'ul_snr_1prb_db': float(r['ul_snr_1prb_db']) if r['ul_snr_1prb_db'] else None,
                    'F1': r['feasible_F1_link_closure'] == 'True', 'F2': r['feasible_F2_m2410_equivalent'] == 'True'}
    e13_inputs = (e13_ref, json.loads((e3dir / 'summary.json').read_text(encoding='utf-8')))
    inputs = (gman, links, prop, bins)

    results, checks = {}, {}
    for version in VERSIONS:
        summ, e14c, s4, part6, tw = evaluate_version(conf, version, inputs, k_users, e13_inputs, f3_ul_min)
        results[version] = summ
        checks[version] = {'e14_checks_all_pass': all(x['passed'] for c in e14c.values() for x in c.values()),
                           'S4_f3_allocation': {**{k: v for k, v in s4.items() if k == 'allocation_violations'},
                                                'passed': s4['allocation_violations'] == 0},
                           'S5_f3_dominance': {'f2_yes_f3_no_ul_records': s4['f2_yes_f3_no_ul_records'],
                                               'violations_of_proved_bound': s4['dominance_violations'],
                                               'passed': s4['dominance_violations'] == 0},
                           'S6_f3_partition': {**part6, 'passed': part6['exclusive_and_exhaustive'] and part6['k6_shared'] == 0},
                           'pl_tw_used_db': {f'{m}_{f/1e9:.1f}GHz': v for (m, f), v in tw.items()}}
    s1 = equal_fractions(results['16.1.0'], e14_summary)
    s2 = equal_fractions(results['19.5.0'], results['16.1.0'], states=UNCHANGED_BY_V19)
    changed_high = equal_fractions(results['19.5.0'], results['16.1.0'], states=('indoor_high',))
    validation = {
        'S1_v16_reproduction': {'differing_fractions': s1, 'passed': s1 == 0},
        'S2_v19_scope': {'differing_fractions_in_unchanged_states': s2,
                         'differing_fractions_in_indoor_high': changed_high, 'passed': s2 == 0},
        'S3_v19_e14_checks': {'passed': checks['19.5.0']['e14_checks_all_pass']},
        'S4_f3_allocation': {v: checks[v]['S4_f3_allocation'] for v in VERSIONS},
        'S5_f3_dominance': {v: checks[v]['S5_f3_dominance'] for v in VERSIONS},
        'S6_f3_partition': {v: checks[v]['S6_f3_partition'] for v in VERSIONS},
        'v16_e14_checks_all_pass': checks['16.1.0']['e14_checks_all_pass'],
        'pl_tw_used_db': {v: checks[v]['pl_tw_used_db'] for v in VERSIONS}}
    passed = (validation['S1_v16_reproduction']['passed'] and validation['S2_v19_scope']['passed']
              and validation['S3_v19_e14_checks']['passed'] and validation['v16_e14_checks_all_pass']
              and all(validation[s][v]['passed'] for s in ('S4_f3_allocation', 'S5_f3_dominance', 'S6_f3_partition')
                      for v in VERSIONS))
    validation['all_checks_passed'] = passed

    # Compact tables: M.2412 mix and per state, F2 vs F3, both versions.
    compact = {}
    for v in VERSIONS:
        compact[v] = {}
        for cname, s in results[v].items():
            row = {'F1_mix': s['mixes']['M2412']['F1'], 'F2_mix': s['mixes']['M2412']['F2'],
                   'F3_mix': s['F3_ofdma_share']['mixes']['M2412'],
                   'F2_per_state': {st: s['per_state'][st]['F2'] for st in e14.SHARED_STATES},
                   'F3_per_state': {st: s['F3_ofdma_share']['per_state'][st] for st in e14.SHARED_STATES}}
            for p in ('F2_per_state',):
                for st in row[p]:
                    row[p][st] = {k: row[p][st][k] for k in ('feasible_fraction', 'class_fraction')}
            compact[v][cname] = row
    (outdir / 'summary.json').write_text(json.dumps({'compact': compact, 'full': results}, indent=2), encoding='utf-8')
    (outdir / 'validation.json').write_text(json.dumps(validation, indent=2), encoding='utf-8')
    manifest = {'generated_utc': datetime.now(timezone.utc).isoformat(), 'tool': 'scripts/e1_5_0_surrogates.py',
                'git_head_at_run': args.git_head or e13.git_head(), 'python': platform.python_version(),
                'numpy': np.__version__, 'config_sha256': e13.sha256(args.config),
                'e1_4_config_sha256': e13.sha256(args.e14_config),
                'e1_4_manifest_sha256': e13.sha256(Path(args.e14_dir, 'manifest.json')),
                'outputs_sha256': {'summary.json': e13.sha256(outdir / 'summary.json')},
                'all_checks_passed': passed}
    (outdir / 'manifest.json').write_text(json.dumps(manifest, indent=2), encoding='utf-8')

    for name, x in validation.items():
        if isinstance(x, dict) and 'passed' in x:
            print(f"{name}: {'PASS' if x['passed'] else 'FAIL'}")
        elif isinstance(x, dict) and all(isinstance(y, dict) and 'passed' in y for y in x.values()):
            print(f"{name}: " + ', '.join(f"{v} {'PASS' if y['passed'] else 'FAIL'}" for v, y in x.items()))
    K = ('K1', 'K2', 'K3', 'K4', 'K6')
    for v in VERSIONS:
        for cname in ('R0_isotropic_e13', 'A_equal_elements', 'B_equal_aperture', 'C_deployment'):
            for p in ('F2_mix', 'F3_mix'):
                x = compact[v][cname][p]
                print(f"V{v} {cname:18s} {p}: UL3.5={x['feasible_fraction']['UL_3.5GHz']:.3f} "
                      f"UL7.2={x['feasible_fraction']['UL_7.2GHz']:.3f} | "
                      + ' '.join(f"{n.split('_')[0]}={val:.3f}" for n, val in x['class_fraction'].items()
                                 if n.split('_')[0] in K))
    print('E1.5.0 SURROGATES', 'PASSED' if passed else 'FAILED')
    sys.exit(0 if passed else 1)


if __name__ == '__main__':
    main()
