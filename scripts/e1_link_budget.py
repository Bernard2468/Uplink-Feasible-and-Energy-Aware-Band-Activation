#!/usr/bin/env python3
"""E1.3: uplink and downlink single-link budgets at 3.5 and 7.2 GHz.

Implements the pre-registration configs/e1/e1_3_link_budget.json on the E1.2
propagation realizations (hash-checked). Outputs in --outdir:
  records.csv.gz   one row per case, realization, UE, carrier, direction
  links_median.csv the same quantities at zero shadowing (median loss)
  summary.json     feasibility probabilities and classes K1-K5 per profile
  validation.json  checks L1-L7, manifest.json  provenance
"""
import argparse
import csv
import gzip
import hashlib
import io
import json
import math
import platform
import subprocess
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from cmwave.radio import link_budget as lb  # noqa: E402

TOL = 1e-9


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def git_head():
    try:
        return subprocess.run(['git', '-C', str(ROOT), 'rev-parse', 'HEAD'], capture_output=True,
                              text=True, check=True).stdout.strip()
    except Exception:  # noqa: BLE001
        return None


def write_gz_csv(path, fieldnames, rows):
    with open(path, 'wb') as raw, gzip.GzipFile(filename='', mode='wb', fileobj=raw, mtime=0) as gz, \
            io.TextIOWrapper(gz, encoding='utf-8', newline='') as fh:
        w = csv.DictWriter(fh, fieldnames=fieldnames, lineterminator='\n')
        w.writeheader()
        w.writerows(rows)


def fmt(v):
    if isinstance(v, (float, np.floating)):
        return repr(round(float(v), 9))
    if isinstance(v, (np.integer,)):
        return int(v)
    if isinstance(v, (np.bool_,)):
        return bool(v)
    return v


def evaluate_case(case, losses, thresholds):
    """losses: dict freq -> np.array of total loss (fixed link/realization order)."""
    out = {}
    for c in case['carriers']:
        cfg = lb.CarrierConfig(**c)
        f = cfg.frequency_hz
        dl = lb.downlink(losses[f], cfg)
        ul = lb.uplink(losses[f], cfg)
        best_bw = np.where(ul['best_n_prb'] > 0, ul['best_n_prb'], cfg.n_prb) * cfg.prb_bw_hz
        ul['bandwidth_hz'] = best_bw
        ul['noise_best_dbm'] = lb.noise_dbm(best_bw, cfg.nf_gnb_db)
        t1, t2 = thresholds['F1_link_closure'], thresholds['F2_m2410_equivalent']
        dl['F1'] = dl['snr_db'] >= t1['DL_snr_db_min']
        ul['F1'] = ul['snr_1prb_db'] >= t1['UL_snr_1prb_db_min']
        dl['F2'] = dl['se_bps_hz'] >= t2['DL_se_min']
        ul['F2'] = ul['se_norm_bps_hz'] >= t2['UL_se_norm_min']
        out[f] = {'cfg': cfg, 'DL': dl, 'UL': ul}
    return out


def classes(res, f1, f2, profile):
    d35, u35 = res[f1]['DL'][profile], res[f1]['UL'][profile]
    d72, u72 = res[f2]['DL'][profile], res[f2]['UL'][profile]
    return {'K1_both_carriers_both_directions': d35 & u35 & d72 & u72,
            'K2_7p2_dl_only_with_3p5_ul': d72 & ~u72 & u35,
            'K3_7p2_dl_without_any_ul': d72 & ~u72 & ~u35,
            'K4_7p2_dl_not_feasible': ~d72,
            'K5_7p2_ul_without_dl': ~d72 & u72}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--config', default=str(ROOT / 'configs/e1/e1_3_link_budget.json'))
    ap.add_argument('--propagation-dir', default=str(ROOT / 'results/phase2/e1_3gpp_propagation'))
    ap.add_argument('--geometry-links', default=str(ROOT / 'results/phase2/e1_geometry/site_tx0_links.csv'))
    ap.add_argument('--outdir', default=str(ROOT / 'results/phase2/e1_3_link_budget'))
    ap.add_argument('--git-head', default=None, help='commit id of the code being run')
    args = ap.parse_args()
    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    conf = json.loads(Path(args.config).read_text(encoding='utf-8'))
    pdir = Path(args.propagation_dir)
    pman = json.loads((pdir / 'manifest.json').read_text(encoding='utf-8'))
    real_path = pdir / 'realizations.csv.gz'
    if not pman['all_checks_passed'] or sha256(real_path) != pman['realizations_sha256']:
        raise SystemExit('E1.2 propagation not validated or realizations file changed')

    with gzip.open(real_path, 'rt', encoding='utf-8') as fh:
        prop = list(csv.DictReader(fh))
    freqs = sorted({float(r['frequency_hz']) for r in prop})
    f1, f2 = freqs
    keys = sorted({(int(r['realization']), r['ue_id']) for r in prop})
    idx = {k: i for i, k in enumerate(keys)}
    n = len(keys)
    loss = {f: np.full(n, np.nan) for f in freqs}
    pathloss = {f: np.full(n, np.nan) for f in freqs}
    shadow = np.full(n, np.nan)
    meta = {}
    for r in prop:
        i = idx[(int(r['realization']), r['ue_id'])]
        f = float(r['frequency_hz'])
        loss[f][i] = float(r['total_loss_db'])
        pathloss[f][i] = float(r['pathloss_db'])
        shadow[i] = float(r['shadowing_db'])
        meta[r['ue_id']] = (r['los_state'], float(r['distance_2d_m']), float(r['distance_3d_m']),
                            r['site_id'], r['model_version'], int(r['seed']))
    assert all(np.isfinite(loss[f]).all() for f in freqs)
    with open(args.geometry_links, encoding='utf-8') as fh:
        bins = {r['ue_id']: r['distance_bin_m'] for r in csv.DictReader(fh)}
    ue_of = np.array([k[1] for k in keys])

    thresholds = conf['thresholds']
    results, records, checks, summary, median_rows = {}, [], {}, {}, []
    for cname, case in conf['cases'].items():
        case_freqs = sorted(c['frequency_hz'] for c in case['carriers'])
        if case_freqs != freqs:
            raise SystemExit(f'case {cname} carriers {case_freqs} != propagation {freqs}')
        res = evaluate_case(case, loss, thresholds)
        results[cname] = res
        med = evaluate_case(case, pathloss, thresholds)    # zero shadowing

        for i, (real, ue) in enumerate(keys):
            los_state, d2, d3, site, mv, seed = meta[ue]
            for f in freqs:
                for d in ('DL', 'UL'):
                    x = res[f][d]
                    rec = {'case': cname, 'site_id': site, 'ue_id': ue, 'realization': real,
                           'seed': seed, 'model_version': mv, 'frequency_hz': f, 'direction': d,
                           'los_state': los_state, 'distance_2d_m': d2, 'distance_3d_m': d3,
                           'pathloss_db': pathloss[f][i], 'shadowing_db': shadow[i],
                           'total_loss_db': loss[f][i], 'tx_power_dbm': x['tx_power_dbm'][i],
                           'antenna_gain_db': x['antenna_gain_db'][i],
                           'bandwidth_hz': x['bandwidth_hz'][i], 'rx_power_dbm': x['rx_power_dbm'][i]}
                    if d == 'DL':
                        rec.update({'noise_dbm': x['noise_dbm'][i], 'snr_db': x['snr_db'][i],
                                    'ul_snr_1prb_db': '', 'n_prb_used': res[f]['cfg'].n_prb,
                                    'se_bps_hz': x['se_bps_hz'][i]})
                    else:
                        rec.update({'noise_dbm': x['noise_best_dbm'][i], 'snr_db': x['snr_best_db'][i],
                                    'ul_snr_1prb_db': x['snr_1prb_db'][i],
                                    'n_prb_used': x['best_n_prb'][i], 'se_bps_hz': x['se_norm_bps_hz'][i]})
                    rec.update({'rate_bps': x['rate_bps'][i], 'sinr_db': '',
                                'feasible_F1_link_closure': bool(x['F1'][i]),
                                'feasible_F2_m2410_equivalent': bool(x['F2'][i])})
                    records.append({k: fmt(v) for k, v in rec.items()})

        for ue in sorted(set(ue_of)):
            i = next(j for j, k in enumerate(keys) if k[1] == ue)
            row = {'case': cname, 'ue_id': ue, 'los_state': meta[ue][0], 'distance_2d_m': meta[ue][1],
                   'distance_bin_m': bins[ue]}
            for f, tag in ((f1, '3p5'), (f2, '7p2')):
                row[f'pathloss_db_{tag}'] = pathloss[f][i]
                row[f'dl_snr_db_{tag}'] = med[f]['DL']['snr_db'][i]
                row[f'ul_snr_1prb_db_{tag}'] = med[f]['UL']['snr_1prb_db'][i]
                row[f'ul_snr_full_db_{tag}'] = med[f]['UL']['snr_full_db'][i]
                row[f'dl_rate_mbps_{tag}'] = med[f]['DL']['rate_bps'][i] / 1e6
                row[f'ul_rate_mbps_{tag}'] = med[f]['UL']['rate_bps'][i] / 1e6
                row[f'ul_best_n_prb_{tag}'] = int(med[f]['UL']['best_n_prb'][i])
                for p in ('F1', 'F2'):
                    row[f'{p}_dl_{tag}'] = bool(med[f]['DL'][p][i])
                    row[f'{p}_ul_{tag}'] = bool(med[f]['UL'][p][i])
            median_rows.append({k: fmt(v) for k, v in row.items()})

        # ---- checks (L1-L7) ----
        c = {}
        l1 = 0.0
        for f in freqs:
            cfg, dl, ul = res[f]['cfg'], res[f]['DL'], res[f]['UL']
            l1 = max(l1, np.max(np.abs(dl['rx_power_dbm'] - (cfg.gnb_power_dbm + cfg.gain_gnb_dbi
                                                             + cfg.gain_ue_dbi - loss[f]))))
            l1 = max(l1, np.max(np.abs(dl['noise_dbm'] - (-174 + 10 * np.log10(cfg.occupied_bw_hz)
                                                          + cfg.nf_ue_db))))
            l1 = max(l1, np.max(np.abs(dl['snr_db'] - (dl['rx_power_dbm'] - dl['noise_dbm']))))
            l1 = max(l1, np.max(np.abs(ul['rx_power_dbm'] - (cfg.ue_power_dbm + cfg.gain_ue_dbi
                                                             + cfg.gain_gnb_dbi - loss[f]))))
            l1 = max(l1, np.max(np.abs(ul['snr_best_db'] - (ul['rx_power_dbm'] - ul['noise_best_dbm']))))
            l1 = max(l1, np.max(np.abs(ul['snr_1prb_db'] - (ul['rx_power_dbm'] - (-174 + 10 * np.log10(
                cfg.prb_bw_hz) + cfg.nf_gnb_db)))))
        c['L1_identities'] = {'max_abs_error_db': float(l1), 'passed': bool(l1 <= TOL)}
        # Same loss in both directions: (rx - tx power) must agree for DL and UL.
        l2 = max(float(np.max(np.abs((res[f]['DL']['rx_power_dbm'] - res[f]['cfg'].gnb_power_dbm)
                                     - (res[f]['UL']['rx_power_dbm'] - res[f]['cfg'].ue_power_dbm))))
                 for f in freqs)
        c['L2_reciprocity'] = {'max_abs_error_db': l2, 'passed': bool(l2 <= TOL)}
        if cname == 'main_eqbw20':
            gap = 20 * math.log10(f2 / f1)
            e_dl = np.max(np.abs(res[f1]['DL']['snr_db'] - res[f2]['DL']['snr_db'] - gap))
            e_ul = np.max(np.abs(res[f1]['UL']['snr_1prb_db'] - res[f2]['UL']['snr_1prb_db'] - gap))
            c['L3_frequency_offset'] = {'expected_db': gap, 'max_abs_error_db': float(max(e_dl, e_ul)),
                                        'passed': bool(max(e_dl, e_ul) <= TOL)}
            spreads = {}
            for f in freqs:
                off = res[f]['DL']['snr_db'] - res[f]['UL']['snr_1prb_db']
                spreads[f'{f:.3g}'] = {'offset_db': float(off[0]), 'spread_db': float(np.ptp(off))}
            c['L4_direction_offset'] = {'per_carrier': spreads,
                                        'passed': all(v['spread_db'] <= TOL for v in spreads.values())}
            l7 = []
            for p in ('F1', 'F2'):
                for d in ('DL', 'UL'):
                    viol = int(np.sum(res[f2][d][p] & ~res[f1][d][p]))
                    l7.append({'profile': p, 'direction': d, 'violations': viol})
            c['L7_monotone_frequency'] = {'detail': l7, 'passed': all(v['violations'] == 0 for v in l7)}
        l6 = all(np.all(res[f]['UL']['rate_bps'] >= res[f]['UL']['rate_1prb_bps'] - 1e-6)
                 and np.all(res[f]['UL']['rate_bps'] >= res[f]['UL']['rate_full_bps'] - 1e-6)
                 for f in freqs)
        c['L6_ul_allocation'] = {'passed': bool(l6)}
        grid = np.linspace(-40, 60, 10001)
        l5 = all(np.all(np.diff(lb.mapped_se(grid, d)) >= 0)
                 and np.all(lb.mapped_se(grid, d) <= lb.shannon_se(grid) + 1e-12)
                 and np.all(lb.mapped_se(grid, d)[grid < -10] == 0) for d in ('DL', 'UL'))
        c['L5_mapping'] = {'passed': bool(l5)}
        checks[cname] = c

        # ---- summary ----
        s = {'n_link_realizations': n, 'per_profile': {}}
        for p in ('F1', 'F2'):
            k = classes(res, f1, f2, p)
            part = (k['K1_both_carriers_both_directions'] | k['K2_7p2_dl_only_with_3p5_ul']
                    | k['K3_7p2_dl_without_any_ul'] | k['K4_7p2_dl_not_feasible'])
            prof = {'feasible_fraction': {f'{d}_{f/1e9:.1f}GHz': float(np.mean(res[f][d][p]))
                                          for f in freqs for d in ('DL', 'UL')},
                    'class_fraction': {name: float(np.mean(v)) for name, v in k.items()},
                    'classes_K1_to_K4_cover_all': bool(np.all(part)),
                    'by_distance_bin': {}, 'by_ue': {}}
            for b in sorted(set(bins.values()), key=lambda t: float(t.split('-')[0])):
                m = np.array([bins[u] == b for u in ue_of])
                prof['by_distance_bin'][b] = {name: float(np.mean(v[m])) for name, v in k.items()}
                prof['by_distance_bin'][b]['n_link_realizations'] = int(m.sum())
            for ue in sorted(set(ue_of)):
                m = ue_of == ue
                prof['by_ue'][ue] = {name: float(np.mean(v[m])) for name, v in k.items()}
            s['per_profile'][p] = prof
        summary[cname] = s

    fields = list(records[0].keys())
    write_gz_csv(outdir / 'records.csv.gz', fields, records)
    with (outdir / 'links_median.csv').open('w', newline='', encoding='utf-8') as fh:
        w = csv.DictWriter(fh, fieldnames=list(median_rows[0].keys()), lineterminator='\n')
        w.writeheader()
        w.writerows(median_rows)
    passed = all(v['passed'] for c in checks.values() for v in c.values())
    (outdir / 'validation.json').write_text(json.dumps({'checks': checks, 'all_checks_passed': passed},
                                                       indent=2), encoding='utf-8')
    (outdir / 'summary.json').write_text(json.dumps(summary, indent=2), encoding='utf-8')
    manifest = {
        'generated_utc': datetime.now(timezone.utc).isoformat(), 'tool': 'scripts/e1_link_budget.py',
        'git_head_at_run': args.git_head or git_head(), 'python': platform.python_version(),
        'numpy': np.__version__,
        'config': str(Path(args.config).relative_to(ROOT)), 'config_sha256': sha256(args.config),
        'propagation_manifest_sha256': sha256(pdir / 'manifest.json'),
        'realizations_sha256': pman['realizations_sha256'],
        'outputs_sha256': {'records.csv.gz': sha256(outdir / 'records.csv.gz'),
                           'links_median.csv': sha256(outdir / 'links_median.csv')},
        'n_records': len(records), 'all_checks_passed': passed}
    (outdir / 'manifest.json').write_text(json.dumps(manifest, indent=2), encoding='utf-8')

    for cname, c in checks.items():
        print(cname + ': ' + ', '.join(f"{k} {'PASS' if v['passed'] else 'FAIL'}" for k, v in c.items()))
    for cname, s in summary.items():
        for p, prof in s['per_profile'].items():
            ff = ' '.join(f'{k}={v:.3f}' for k, v in prof['feasible_fraction'].items())
            kk = ' '.join(f"{k.split('_')[0]}={v:.3f}" for k, v in prof['class_fraction'].items())
            print(f'{cname:20s} {p}: {ff} | {kk}')
    print('E1.3 LINK BUDGET', 'PASSED' if passed else 'FAILED')
    sys.exit(0 if passed else 1)


if __name__ == '__main__':
    main()
