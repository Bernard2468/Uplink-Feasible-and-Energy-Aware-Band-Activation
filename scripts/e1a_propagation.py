#!/usr/bin/env python3
"""E1a step 2: matched 3.5/7.2 GHz ray-traced propagation at fixed UE positions.

Antenna-free by design (single isotropic V-polarised elements at both ends):
this isolates the FREQUENCY effect of the propagation channel. Antenna regimes
and link budgets are separate, later steps (protocol section 4, safeguard 2).

Ray-tracing configurations (all with diffuse scattering off; the Munich scene
materials have scattering_coefficient 0):
  S0_los_only         direct path only (exactness check against free space)
  S1_specular         + specular reflections, max depth 5
  S2_spec_diffr       + diffraction                       <- MAIN configuration
  S3_spec_diffr_refr  + transmission through walls (buildings modelled as two
                        10 cm slabs with no interior loss: an optimistic bound)
plus MAIN repeated with another seed, with 3x the rays, with a 4 m mast
(gNB z = 30 m) and in the reverse (UE -> gNB) direction.

Checks (thresholds fixed before the first run; see docs/PHASE_2_RADIO_PROTOCOL.md):
  C1 geometric LOS <=> exactly one direct path equal to free space (0.01 dB)
  C2 every link has >= 1 path in MAIN at both carriers (reported)
  C3 convergence: >= 90% of links within 0.5 dB for other seed and 3x rays
  C4 more mechanisms never lose power: S0 <= S1 <= S2 (0.5 dB tolerance)
  C5 reciprocity: >= 90% of links within 0.5 dB, UE->gNB vs gNB->UE
Outputs: links.csv, summary.json, checks.json, manifest.json in --outdir, and
the raw per-path arrays of every solve in --outdir/raw/*.npz.

Every solve runs in a FRESH Python process (`--job`): repeated solves in one
process aborted inside Dr.Jit's thread pool (nanothread queue.cpp:357
"remain == 1", sionna-rt 1.2.0 / drjit 1.2.0, LLVM backend). Isolation also
guarantees that no JIT or memory state is shared between configurations.
A crashed job is retried at most twice; retries are recorded in the manifest.
"""
import argparse
import csv
import json
import math
import platform
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import sionna.rt as rt
from sionna.rt import PathSolver, PlanarArray, Receiver, Transmitter, load_scene

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from cmwave.radio import pathloss_3gpp as pl3  # noqa: E402

BASE = dict(los=True, diffuse_reflection=False, edge_diffraction=False)
CONFIGS = {
    'S0_los_only': dict(BASE, max_depth=0, specular_reflection=False, refraction=False,
                        diffraction=False),
    'S1_specular': dict(BASE, max_depth=5, specular_reflection=True, refraction=False,
                        diffraction=False),
    'S2_spec_diffr': dict(BASE, max_depth=5, specular_reflection=True, refraction=False,
                          diffraction=True),
    'S3_spec_diffr_refr': dict(BASE, max_depth=5, specular_reflection=True, refraction=True,
                               diffraction=True),
}
MAIN = 'S2_spec_diffr'
CODES = {1: 'S', 2: 'd', 4: 'T', 8: 'D'}   # specular, diffuse, transmission, diffraction
TOL_EXACT_DB, TOL_CONV_DB, CONV_FRACTION = 0.01, 0.5, 0.90


def solve(scene_name, freq, txs, rxs, cfg, seed, samples, thickness=None):
    """Return per-(tx, rx) path data. txs/rxs: lists of (name, xyz).

    thickness: if given, overrides the slab thickness [m] of every scene material.
    Sionna RT computes reflection/transmission with the ITU-R P.2040 single-layer
    slab model, so thin slabs make reflection frequency-selective.
    """
    scene = load_scene(getattr(rt.scene, scene_name), merge_shapes=True)
    if thickness is not None:
        for mat in scene.radio_materials.values():
            mat.thickness = thickness
            if abs(float(np.array(mat.thickness).ravel()[0]) - thickness) > 1e-6:
                raise RuntimeError(f'thickness override not applied to {mat.name}')
    scene.frequency = freq
    scene.tx_array = PlanarArray(num_rows=1, num_cols=1, pattern='iso', polarization='V')
    scene.rx_array = PlanarArray(num_rows=1, num_cols=1, pattern='iso', polarization='V')
    for name, p in txs:
        scene.add(Transmitter(name=name, position=list(p)))
    for name, p in rxs:
        scene.add(Receiver(name=name, position=list(p)))
    t0 = time.time()
    paths = PathSolver()(scene=scene, synthetic_array=True, seed=seed, samples_per_src=samples,
                         **cfg)
    elapsed = time.time() - t0
    ar, ai = (np.array(x) for x in paths.a)          # (rx, rx_ant, tx, tx_ant, P)
    gain = (ar ** 2 + ai ** 2)[:, 0, :, 0, :]          # (rx, tx, P)
    valid = np.array(paths.valid).astype(bool)          # (rx, tx, P)
    tau = np.array(paths.tau)                           # (rx, tx, P)
    inter = np.array(paths.interactions) if cfg['max_depth'] > 0 else None  # (depth, rx, tx, P)
    return gain, valid, tau, inter, elapsed


def run_job(job_json):
    """Worker entry point: one solve, results written to an .npz file."""
    job = json.loads(job_json)
    gain, valid, tau, inter, el = solve(job['scene'], job['freq'], job['txs'], job['rxs'],
                                        CONFIGS[job['config']], job['seed'], job['samples'],
                                        job.get('thickness'))
    np.savez_compressed(job['out'], gain=gain, valid=valid, tau=tau,
                        inter=inter if inter is not None else np.zeros((0,)),
                        has_inter=inter is not None, elapsed=el)


def solve_isolated(job, log):
    """Run one solve in a fresh process; retry at most twice on a crash."""
    attempts = []
    for attempt in range(3):
        t0 = time.time()
        proc = subprocess.run([sys.executable, str(Path(__file__).resolve()), '--job',
                               json.dumps(job)], capture_output=True, text=True)
        attempts.append({'returncode': proc.returncode, 'wall_s': round(time.time() - t0, 1),
                         'stderr_tail': proc.stderr.strip().splitlines()[-3:]})
        if proc.returncode == 0 and Path(job['out']).exists():
            break
    else:
        raise RuntimeError(f"solve failed 3 times: {job['config']} {job['freq']}: {attempts}")
    log.append({'out': Path(job['out']).name, 'attempts': attempts})
    d = np.load(job['out'])
    inter = d['inter'] if bool(d['has_inter']) else None
    return d['gain'], d['valid'], d['tau'], inter, float(d['elapsed'])


def link_metrics(gain, valid, tau, inter):
    """gain/valid/tau: (P,); inter: (depth, P) or None."""
    v = valid & (gain > 0)
    total = float(gain[v].sum())
    if inter is None:
        is_los = v.copy()
        has = lambda code: np.zeros_like(v)                  # noqa: E731
    else:
        is_los = v & np.all(inter == 0, axis=0)
        has = lambda code: v & np.any(inter == code, axis=0)  # noqa: E731
    out = {'n_paths': int(v.sum()), 'n_los_paths': int(is_los.sum()),
           'total_gain_db': 10 * math.log10(total) if total > 0 else None,
           'los_gain_db': (10 * math.log10(float(gain[is_los].sum()))
                           if is_los.any() else None)}
    if total > 0:
        out['frac_power_diffraction'] = float(gain[has(8)].sum()) / total
        out['frac_power_transmission'] = float(gain[has(4)].sum()) / total
        k = int(np.argmax(np.where(v, gain, -1.0)))
        out['strongest_gain_db'] = 10 * math.log10(float(gain[k]))
        out['strongest_signature'] = ('LOS' if inter is None or not np.any(inter[:, k])
                                      else ''.join(CODES.get(int(c), '?') for c in inter[:, k] if c))
        w = gain[v] / total
        t = tau[v]
        mean = float(np.sum(w * t))
        out['rms_delay_spread_ns'] = math.sqrt(max(float(np.sum(w * t * t)) - mean ** 2, 0.0)) * 1e9
    return out


def main():
    if len(sys.argv) == 3 and sys.argv[1] == '--job':
        run_job(sys.argv[2])
        return
    ap = argparse.ArgumentParser()
    ap.add_argument('--positions', required=True)
    ap.add_argument('--freqs', type=float, nargs='+', default=[3.5e9, 7.2e9])
    ap.add_argument('--seed', type=int, default=1)
    ap.add_argument('--samples', type=int, default=1_000_000)
    ap.add_argument('--outdir', required=True)
    args = ap.parse_args()
    if len(args.freqs) != 2:
        ap.error('exactly two carriers (f1 < f2) are compared')
    f1, f2 = sorted(args.freqs)
    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    pos = json.loads(Path(args.positions).read_text(encoding='utf-8'))
    scene_name = pos['scene']
    gnb = pos['gnb']['position']
    ues = pos['positions']
    ue_list = [(u['id'], u['position']) for u in ues]

    runs = [  # (run_id, config, seed, samples, gnb_z, direction, wall thickness override)
        ('S0', 'S0_los_only', args.seed, args.samples, gnb[2], 'dl', None),
        ('S1', 'S1_specular', args.seed, args.samples, gnb[2], 'dl', None),
        ('S2', MAIN, args.seed, args.samples, gnb[2], 'dl', None),
        ('S3', 'S3_spec_diffr_refr', args.seed, args.samples, gnb[2], 'dl', None),
        ('S2_seed2', MAIN, args.seed + 1, args.samples, gnb[2], 'dl', None),
        ('S2_3x_rays', MAIN, args.seed, 3 * args.samples, gnb[2], 'dl', None),
        ('S2_mast4m', MAIN, args.seed, args.samples, gnb[2] + 3.0, 'dl', None),
        ('S2_reverse', MAIN, args.seed, args.samples, gnb[2], 'ul', None),
        ('S2_wall0p2m', MAIN, args.seed, args.samples, gnb[2], 'dl', 0.2),
        ('S2_wall0p3m', MAIN, args.seed, args.samples, gnb[2], 'dl', 0.3),
        ('S2_wall0p5m', MAIN, args.seed, args.samples, gnb[2], 'dl', 0.5),
    ]
    rawdir = outdir / 'raw'
    rawdir.mkdir(exist_ok=True)
    rows, timings, job_log = [], [], []
    for run_id, cfg_name, seed, samples, gz, direction, thick in runs:
        for f in (f1, f2):
            g = [gnb[0], gnb[1], gz]
            gnb_list = [('gnb', g)]
            job = {'scene': scene_name, 'freq': f, 'config': cfg_name, 'seed': seed,
                   'samples': samples, 'thickness': thick,
                   'out': str(rawdir / f'{run_id}_{f/1e9:.1f}GHz.npz'),
                   'txs': gnb_list if direction == 'dl' else ue_list,
                   'rxs': ue_list if direction == 'dl' else gnb_list}
            gain, valid, tau, inter, el = solve_isolated(job, job_log)
            if direction == 'dl':
                pick = lambda i: (gain[i, 0], valid[i, 0], tau[i, 0],                 # noqa: E731
                                  None if inter is None else inter[:, i, 0])
            else:
                pick = lambda i: (gain[0, i], valid[0, i], tau[0, i],                 # noqa: E731
                                  None if inter is None else inter[:, 0, i])
            timings.append({'run': run_id, 'freq_hz': f, 'solve_s': round(el, 2)})
            print(f'{run_id:11s} {f/1e9:.1f} GHz solved in {el:6.1f} s', flush=True)
            for i, u in enumerate(ues):
                d3 = math.dist(g, u['position'])
                row = {'run': run_id, 'config': cfg_name, 'direction': direction, 'seed': seed,
                       'samples_per_src': samples, 'gnb_z_m': gz,
                       'wall_thickness_m': thick if thick is not None else 'scene_default_0.1',
                       'freq_hz': f, 'ue': u['id'],
                       'geometric_class': u['geometric_class'], 'd2d_m': u['d2d_m'],
                       'd3d_m': round(d3, 3), 'free_space_gain_db': -pl3.free_space_loss(d3, f)}
                row.update(link_metrics(*pick(i)))
                rows.append(row)

    fields = ['run', 'config', 'direction', 'seed', 'samples_per_src', 'gnb_z_m',
              'wall_thickness_m', 'freq_hz', 'ue',
              'geometric_class', 'd2d_m', 'd3d_m', 'n_paths', 'n_los_paths', 'free_space_gain_db',
              'los_gain_db', 'total_gain_db', 'strongest_gain_db', 'strongest_signature',
              'frac_power_diffraction', 'frac_power_transmission', 'rms_delay_spread_ns']
    with (outdir / 'links.csv').open('w', newline='', encoding='utf-8') as fh:
        w = csv.DictWriter(fh, fieldnames=fields, extrasaction='ignore')
        w.writeheader()
        w.writerows(rows)

    def get(run, f, ue, key='total_gain_db'):
        for r in rows:
            if r['run'] == run and r['freq_hz'] == f and r['ue'] == ue:
                return r.get(key)
        raise KeyError((run, f, ue))

    ids = [u['id'] for u in ues]
    cls = {u['id']: u['geometric_class'] for u in ues}
    checks = {}

    # C1: geometric LOS <=> exactly one direct path equal to free space.
    bad = []
    for f in (f1, f2):
        for ue in ids:
            n = get('S0', f, ue, 'n_los_paths')
            if cls[ue] == 'LOS':
                err = (get('S0', f, ue, 'los_gain_db') or -999) - get('S0', f, ue, 'free_space_gain_db')
                if n != 1 or abs(err) > TOL_EXACT_DB:
                    bad.append({'ue': ue, 'freq_hz': f, 'n_los_paths': n, 'error_db': err})
            elif n != 0:
                bad.append({'ue': ue, 'freq_hz': f, 'n_los_paths': n, 'class': 'NLOS'})
    checks['C1_los_exactness'] = {'passed': not bad, 'failures': bad}

    # C2: every link reachable in MAIN.
    empty = [{'ue': ue, 'freq_hz': f} for f in (f1, f2) for ue in ids if get('S2', f, ue) is None]
    checks['C2_all_links_have_paths'] = {'passed': not empty, 'links_without_paths': empty,
                                         'note': 'reported, not a physics failure'}

    def compare(run_a, run_b, label):
        diffs = []
        for f in (f1, f2):
            for ue in ids:
                a, b = get(run_a, f, ue), get(run_b, f, ue)
                if a is not None and b is not None:
                    diffs.append(abs(a - b))
        d = np.array(diffs)
        frac = float(np.mean(d <= TOL_CONV_DB)) if d.size else 0.0
        return {'label': label, 'links': int(d.size), 'fraction_within_tol': frac,
                'median_abs_db': float(np.median(d)) if d.size else None,
                'max_abs_db': float(d.max()) if d.size else None,
                'passed': bool(d.size) and frac >= CONV_FRACTION}

    checks['C3_convergence_seed'] = compare('S2', 'S2_seed2', 'MAIN vs other seed')
    checks['C3_convergence_rays'] = compare('S2', 'S2_3x_rays', 'MAIN vs 3x rays')
    checks['C5_reciprocity'] = compare('S2', 'S2_reverse', 'gNB->UE vs UE->gNB')

    # C4: S0 <= S1 <= S2 per link (incoherent power cannot drop when mechanisms are added).
    viol = []
    for f in (f1, f2):
        for ue in ids:
            vals = [get(r, f, ue) for r in ('S0', 'S1', 'S2')]
            num = [v if v is not None else -math.inf for v in vals]
            if num[1] < num[0] - TOL_CONV_DB or num[2] < num[1] - TOL_CONV_DB:
                viol.append({'ue': ue, 'freq_hz': f, 'S0_S1_S2_db': vals})
    checks['C4_monotone_mechanisms'] = {'passed': not viol, 'violations': viol}

    # Summary: frequency gap and residual against TR 38.901 UMa medians.
    summary = {'f1_hz': f1, 'f2_hz': f2,
               'free_space_gap_db': 20 * math.log10(f2 / f1), 'by_run': {}}
    for run_id, cfg_name, *_ in runs:
        per = {}
        for c in ('LOS', 'NLOS'):
            gaps, res1, res2 = [], [], []
            for u in ues:
                if u['geometric_class'] != c:
                    continue
                a, b = get(run_id, f1, u['id']), get(run_id, f2, u['id'])
                if a is None or b is None:
                    continue
                gaps.append(a - b)
                gz = next(r['gnb_z_m'] for r in rows if r['run'] == run_id)
                d3 = math.dist([gnb[0], gnb[1], gz], u['position'])
                model = pl3.uma_los if c == 'LOS' else pl3.uma_nlos
                h_ut = u['position'][2]
                res1.append(-a - model(u['d2d_m'], d3, f1, gz, h_ut))
                res2.append(-b - model(u['d2d_m'], d3, f2, gz, h_ut))
            stat = lambda x: ({'n': len(x), 'mean': float(np.mean(x)), 'median': float(np.median(x)),  # noqa: E731
                               'min': float(np.min(x)), 'max': float(np.max(x)),
                               'std': float(np.std(x, ddof=1)) if len(x) > 1 else None}
                              if x else {'n': 0})
            per[c] = {'gap_f1_minus_f2_db': stat(gaps),
                      'rt_minus_3gpp_uma_pathloss_db_f1': stat(res1),
                      'rt_minus_3gpp_uma_pathloss_db_f2': stat(res2)}
        summary['by_run'][run_id] = per

    # LOS probability of the scene vs TR 38.901 UMa (area-weighted bin average).
    lp = []
    for c_los, c_nlos in zip(pos['census'][0::2], pos['census'][1::2]):
        lo, hi = c_los['bin_m']
        n = c_los['candidates'] + c_nlos['candidates']
        dd = np.linspace(lo, hi, 400)
        model = float(np.average([pl3.uma_los_probability(d, 1.5) for d in dd], weights=dd))
        lp.append({'bin_m': [lo, hi], 'scene_los_fraction': c_los['candidates'] / n if n else None,
                   'outdoor_points': n, 'tr38901_uma_plos': model})
    summary['los_probability'] = lp

    checks_passed = all(v['passed'] for k, v in checks.items() if not k.startswith('C2'))
    manifest = {
        'generated_utc': datetime.now(timezone.utc).isoformat(),
        'tool': 'scripts/e1a_propagation.py', 'python': platform.python_version(),
        'sionna_rt': rt.__version__, 'scene': scene_name, 'positions_file': args.positions,
        'gnb': gnb, 'n_ues': len(ues), 'antennas': 'single isotropic element, V-pol, both ends',
        'configs': CONFIGS, 'main_config': MAIN, 'runs': [r[0] for r in runs],
        'timings': timings, 'jobs': job_log,
        'retries': sum(len(j['attempts']) - 1 for j in job_log),
        'tolerances_db': {'exact': TOL_EXACT_DB, 'convergence': TOL_CONV_DB,
                                              'fraction': CONV_FRACTION},
        'all_checks_passed_excluding_C2': checks_passed,
        'claims_scope': 'propagation only (no antennas, powers, noise or rates)'}
    for name, data in (('summary.json', summary), ('checks.json', checks),
                       ('manifest.json', manifest)):
        (outdir / name).write_text(json.dumps(data, indent=2), encoding='utf-8')

    for k, v in checks.items():
        extra = {x: v[x] for x in ('fraction_within_tol', 'median_abs_db', 'max_abs_db') if x in v}
        print(f"{k:28s} {'PASS' if v['passed'] else 'FAIL'} {extra if extra else ''}")
    for run_id in summary['by_run']:
        line = []
        for c in ('LOS', 'NLOS'):
            s = summary['by_run'][run_id][c]['gap_f1_minus_f2_db']
            if s['n']:
                line.append(f"{c} gap mean {s['mean']:.2f} [{s['min']:.2f},{s['max']:.2f}] dB (n={s['n']})")
        print(f'{run_id:11s} ' + ' | '.join(line))
    print('E1a PROPAGATION CHECKS', 'PASSED' if checks_passed else 'FAILED')
    sys.exit(0 if checks_passed else 1)


if __name__ == '__main__':
    main()
