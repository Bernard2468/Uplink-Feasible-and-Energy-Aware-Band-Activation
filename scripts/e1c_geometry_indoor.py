#!/usr/bin/env python3
"""E1c geometry: building-resolved indoor UE samples for one site of a Sionna RT scene.

Implements the geometry part of configs/e1/e1c_area_maps.json. Run inside WSL with the project
venv. Uses the E1.1 grid construction (scripts/e1a_select_positions.py screen_grid) unchanged and
Mitsuba ray tests on the scene mesh:
  indoor grid point  = on the ground (downward hit at the UE height), covered (upward hit: roof)
                       and enclosed (8 horizontal rays at 1.5 m hit within 300 m);
  roof height H      = upward hit distance + UE height (ground at z = 0);
  floors             = n_fl = 1..N_fl, N_fl = min(floor(H / 3 m), 8); hUT = 3 (n_fl - 1) + 1.5 m
                       (TR 38.901 V16.1.0 Table 7.2-1 UMa indoor UT height, as cited in the E1.4
                       config; N_fl is the building's own floor count, capped at the table's 8;
                       UMa path loss is valid to hUT = 22.5 m);
  facade             = first wall hit by the horizontal ray from the UE toward the gNB;
                       d2D-in = its distance; the O2I link's outdoor LOS state = unobstructed
                       segment from 5 cm outside that wall to the gNB (the E1.1 LOS definition).
Outputs in --outdir: <site>_indoor.npz, <site>_indoor_manifest.json (checks C1a-C1d).
"""
import argparse
import hashlib
import json
import math
import platform
import sys
from datetime import datetime, timezone
from pathlib import Path

import drjit as dr
import mitsuba as mi
import numpy as np
import sionna.rt as rt
from sionna.rt import load_scene

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from e1a_select_positions import ray_distance, ray_hits, screen_grid  # noqa: E402

FLOOR_M = 3.0
MAX_FLOORS = 8
ENCLOSE_M = 300.0
STEP_OUT_M = 0.05


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def annulus_grid(ms, tx, ue_height, grid_m, rmin, rmax):
    """The E1.1 grid in the annulus, built exactly as screen_grid builds it."""
    bb = ms.bbox()
    lo = np.array([bb.min[0], bb.min[1]], dtype=float)
    hi = np.array([bb.max[0], bb.max[1]], dtype=float)
    xs = np.arange(tx[0] - rmax, tx[0] + rmax + 1e-9, grid_m)
    ys = np.arange(tx[1] - rmax, tx[1] + rmax + 1e-9, grid_m)
    gx, gy = np.meshgrid(xs, ys, indexing='ij')
    pts = np.stack([gx.ravel(), gy.ravel(), np.full(gx.size, ue_height)], axis=1)
    d2d = np.hypot(pts[:, 0] - tx[0], pts[:, 1] - tx[1])
    inside = ((d2d >= rmin) & (d2d < rmax)
              & (pts[:, 0] > lo[0] + 10) & (pts[:, 0] < hi[0] - 10)
              & (pts[:, 1] > lo[1] + 10) & (pts[:, 1] < hi[1] - 10))
    return pts[inside], d2d[inside]


def indoor_samples(ms, tx, ue_height, grid_m, rmin, rmax):
    pts, d2d = annulus_grid(ms, tx, ue_height, grid_m, rmin, rmax)
    t_up = ray_distance(ms, pts, [0, 0, 1])
    t_down = ray_distance(ms, pts, [0, 0, -1])
    on_ground = np.abs(t_down - ue_height) < 0.05
    covered = np.isfinite(t_up)
    enclosed = np.ones(len(pts), dtype=bool)
    for k in range(8):
        a = k * math.pi / 4
        t = ray_distance(ms, pts, [math.cos(a), math.sin(a), 0.0])
        enclosed &= np.isfinite(t) & (t < ENCLOSE_M)
    ind = on_ground & covered & enclosed
    P, D2 = pts[ind], d2d[ind]
    roof = t_up[ind] + ue_height
    nfl = np.minimum(np.floor(roof / FLOOR_M).astype(int), MAX_FLOORS)
    keep = nfl >= 1
    P, D2, roof, nfl = P[keep], D2[keep], roof[keep], nfl[keep]
    rep = np.repeat(np.arange(len(P)), nfl)
    floor = np.concatenate([np.arange(1, n + 1) for n in nfl])
    h = FLOOR_M * (floor - 1) + ue_height
    Q = P[rep].copy()
    Q[:, 2] = h
    vh = tx[None, :2] - Q[:, :2]
    dh = np.hypot(vh[:, 0], vh[:, 1])
    u = np.column_stack([vh / dh[:, None], np.zeros(len(Q))])
    d_in = ray_distance(ms, Q, u)
    # Run 1 (C1d failed): 46 samples at 8 points of the gNB's own building (roof 26 m, gNB 1 m
    # above it) have their facade beyond the gNB's horizontal position. They are served through
    # the roof, which the TR 38.901 O2I model does not cover: excluded and counted.
    same_bldg = ~(d_in < dh)
    n_excl, excl_d2d_max = int(same_bldg.sum()), float(D2[rep][same_bldg].max()) if same_bldg.any() else 0.0
    keep_s = ~same_bldg
    Q, u, d_in, rep, floor, h = Q[keep_s], u[keep_s], d_in[keep_s], rep[keep_s], floor[keep_s], h[keep_s]
    F = Q + (d_in + STEP_OUT_M)[:, None] * u
    v = tx[None, :] - F
    dv = np.linalg.norm(v, axis=1)
    los = ~ray_hits(ms, F, v / dv[:, None], dv - 0.05)
    counts = {'grid_points_in_annulus': int(len(pts)), 'on_ground': int(on_ground.sum()),
              'covered': int(covered.sum()), 'enclosed': int(enclosed.sum()),
              'indoor_points': int(ind.sum()), 'indoor_points_with_floor': int(keep.sum()),
              'excluded_same_building_samples': n_excl, 'excluded_same_building_max_d2d_m': excl_d2d_max,
              'indoor_samples': int(len(Q))}
    return {'x': Q[:, 0], 'y': Q[:, 1], 'h_ut': h, 'floor': floor, 'n_floors': nfl[rep], 'roof_h': roof[rep],
            'point_id': rep, 'd2d': D2[rep], 'd2d_in': d_in, 'facade_los': los.astype(np.uint8), 'gnb': tx}, counts


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--positions', default=str(ROOT / 'configs/e1/positions_munich_tx0.json'))
    ap.add_argument('--e11-dir', default=str(ROOT / 'results/phase2/e1_geometry'))
    ap.add_argument('--site-id', default='site_tx0')
    ap.add_argument('--outdir', default=str(ROOT / 'results/phase2/e1c_geometry'))
    args = ap.parse_args()
    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    pos = json.loads(Path(args.positions).read_text(encoding='utf-8'))
    sel = pos['selection']
    tx = np.array(pos['gnb']['position'], dtype=float)
    e11 = json.loads((Path(args.e11_dir) / f'{args.site_id}_manifest.json').read_text())
    mask_path = Path(args.e11_dir) / f'{args.site_id}_mask.npz'
    if not e11['all_checks_passed'] or sha256(mask_path) != e11['files'][mask_path.name]:
        raise SystemExit('E1.1 geometry not validated or mask changed')
    mask = np.load(mask_path)
    scene = load_scene(getattr(rt.scene, pos['scene']), merge_shapes=True)
    ms = scene.mi_scene
    b = sel['distance_bins_m']
    checks = {}
    # C1a: the E1.1 open outdoor mask is reproduced bit-identically by the same grid code
    pts, d2d, d3d, los, n_grid = screen_grid(ms, tx, sel['ue_height_m'], sel['grid_m'], sel['clearance_m'],
                                             b[0], b[-1])
    same = (np.array_equal(pts[:, 0], mask['x']) and np.array_equal(pts[:, 1], mask['y'])
            and np.array_equal(pts[:, 2], mask['z']) and np.array_equal(d3d, mask['d3d'])
            and np.array_equal(los.astype(np.uint8), mask['los']))
    checks['C1a_e11_mask_reproduced'] = {'passed': bool(same and n_grid == e11['counts']['grid_points_in_annulus']),
                                         'n_grid': int(n_grid)}
    # C1b: indoor extraction reproducible
    s1, c1 = indoor_samples(ms, tx, sel['ue_height_m'], sel['grid_m'], b[0], b[-1])
    s2, _ = indoor_samples(ms, tx, sel['ue_height_m'], sel['grid_m'], b[0], b[-1])
    checks['C1b_indoor_reproducible'] = {'passed': all(np.array_equal(s1[k], s2[k]) for k in s1)}
    # C1c: grid consistency (indoor points are grid points of the annulus, disjoint from the outdoor mask)
    outdoor_keys = {(round(x, 6), round(y, 6)) for x, y in zip(mask['x'], mask['y'])}
    ind_keys = {(round(x, 6), round(y, 6)) for x, y in zip(s1['x'], s1['y'])}
    checks['C1c_disjoint_from_outdoor'] = {'passed': not (outdoor_keys & ind_keys) and c1['grid_points_in_annulus'] == n_grid,
                                           'overlap': len(outdoor_keys & ind_keys)}
    # C1d: sample sanity
    ok = (np.all(s1['n_floors'] >= 1) and np.all(s1['n_floors'] <= MAX_FLOORS)
          and np.all(s1['h_ut'] <= s1['roof_h'] - 1.5 + 1e-9) and np.all(s1['h_ut'] <= 22.5)
          and np.all(np.isfinite(s1['d2d_in'])) and np.all(s1['d2d_in'] >= 0)
          and np.all(s1['d2d_in'] < s1['d2d']) and np.all(s1['d2d'] >= b[0]) and np.all(s1['d2d'] < b[-1]))
    checks['C1d_sample_sanity'] = {'passed': bool(ok)}
    # C1e: the same-building exclusion stays what it was introduced for (run 1): few samples, near the site
    checks['C1e_same_building_exclusion'] = {
        'passed': c1['excluded_same_building_samples'] <= 0.001 * (c1['indoor_samples'] + c1['excluded_same_building_samples'])
        and c1['excluded_same_building_max_d2d_m'] <= 50.0,
        'excluded': c1['excluded_same_building_samples'], 'max_d2d_m': c1['excluded_same_building_max_d2d_m']}
    out = outdir / f'{args.site_id}_indoor.npz'
    np.savez_compressed(out, **s1)
    stats = {'roof_h_percentiles_0_5_25_50_75_95_100': np.percentile(s1['roof_h'][np.r_[True, np.diff(s1['point_id']) != 0]],
                                                                      [0, 5, 25, 50, 75, 95, 100]).round(2).tolist(),
             'floors_hist': {int(k): int(v) for k, v in zip(*np.unique(s1['floor'], return_counts=True))},
             'd2d_in_percentiles_0_25_50_75_95_100': np.percentile(s1['d2d_in'], [0, 25, 50, 75, 95, 100]).round(2).tolist(),
             'facade_los_fraction_by_floor': {int(f): float(s1['facade_los'][s1['floor'] == f].mean())
                                              for f in np.unique(s1['floor'])}}
    passed = all(v['passed'] for v in checks.values())
    manifest = {'generated_utc': datetime.now(timezone.utc).isoformat(), 'tool': 'scripts/e1c_geometry_indoor.py',
                'site_id': args.site_id, 'scene': pos['scene'], 'gnb_position_m': tx.tolist(),
                'versions': {'python': platform.python_version(), 'sionna_rt': rt.__version__,
                             'mitsuba': mi.__version__, 'drjit': dr.__version__},
                'rules': {'floor_m': FLOOR_M, 'max_floors': MAX_FLOORS, 'enclose_m': ENCLOSE_M,
                          'step_out_m': STEP_OUT_M},
                'e1_1_manifest_sha256': sha256(Path(args.e11_dir) / f'{args.site_id}_manifest.json'),
                'counts': c1, 'stats': stats, 'files': {out.name: sha256(out)}, 'checks': checks,
                'all_checks_passed': passed}
    (outdir / f'{args.site_id}_indoor_manifest.json').write_text(json.dumps(manifest, indent=2), encoding='utf-8')
    for k, v in checks.items():
        print(f"{k:30s} {'PASS' if v['passed'] else 'FAIL'}")
    print('counts', c1)
    print('E1c GEOMETRY', 'PASSED' if passed else 'CHECKS FAILED')
    sys.exit(0 if passed else 1)


if __name__ == '__main__':
    main()
