#!/usr/bin/env python3
"""E1a step 1: select fixed, reproducible outdoor UE positions in a Sionna scene.

Candidate points on a square grid at UE height are kept only if they are
outdoors and in the open, using Mitsuba ray tests on the scene geometry:
  * upward ray from the point hits nothing (no roof, overhang or building);
  * downward ray hits the ground at the expected height (point is on terrain);
  * horizontal rays in 8 directions hit nothing within `clearance_m`
    (keeps points away from walls and thin geometry gaps).
Each kept point is labelled geometric LOS if the straight segment to the gNB
is unobstructed. A stratified random sample (fixed seed) is drawn per distance
bin and LOS class. Output: a JSON file that later steps read; it is the single
source of truth for E1a/E1b positions and is version-controlled.
"""
import argparse
import json
import math
from datetime import datetime, timezone

import drjit as dr
import mitsuba as mi
import numpy as np
import sionna.rt as rt
from sionna.rt import load_scene


def ray_hits(mi_scene, origins, direction, maxt):
    """Vectorised occlusion test; origins (N,3) numpy, direction (3,) or (N,3)."""
    o = mi.Point3f(mi.Float(origins[:, 0]), mi.Float(origins[:, 1]), mi.Float(origins[:, 2]))
    d = np.broadcast_to(np.asarray(direction, dtype=float), origins.shape)
    ray = mi.Ray3f(o, mi.Vector3f(mi.Float(d[:, 0]), mi.Float(d[:, 1]), mi.Float(d[:, 2])))
    ray.maxt = mi.Float(np.broadcast_to(np.asarray(maxt, dtype=float), (origins.shape[0],)))
    return np.array(mi_scene.ray_test(ray), dtype=bool)


def ray_distance(mi_scene, origins, direction):
    o = mi.Point3f(mi.Float(origins[:, 0]), mi.Float(origins[:, 1]), mi.Float(origins[:, 2]))
    d = np.broadcast_to(np.asarray(direction, dtype=float), origins.shape)
    si = mi_scene.ray_intersect(mi.Ray3f(o, mi.Vector3f(mi.Float(d[:, 0]), mi.Float(d[:, 1]),
                                                         mi.Float(d[:, 2]))))
    t = np.array(si.t, dtype=float)
    t[~np.array(si.is_valid(), dtype=bool)] = np.inf
    return t


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--scene', default='munich')
    ap.add_argument('--tx', type=float, nargs=3, default=[8.5, 21.0, 27.0])
    ap.add_argument('--ue-height', type=float, default=1.5)
    ap.add_argument('--grid-m', type=float, default=4.0)
    ap.add_argument('--clearance-m', type=float, default=2.0)
    ap.add_argument('--bins', type=float, nargs='+', default=[20, 50, 100, 200, 300, 450, 600])
    ap.add_argument('--per-bin-class', type=int, default=3)
    ap.add_argument('--seed', type=int, default=20261106)
    ap.add_argument('--out', required=True)
    args = ap.parse_args()

    scene = load_scene(getattr(rt.scene, args.scene), merge_shapes=True)
    ms = scene.mi_scene
    tx = np.array(args.tx, dtype=float)
    bb = ms.bbox()
    lo = np.array([bb.min[0], bb.min[1]], dtype=float)
    hi = np.array([bb.max[0], bb.max[1]], dtype=float)

    # Site checks: open sky above the gNB, and its height above the surface below.
    up_tx = ray_hits(ms, tx[None, :], [0, 0, 1], np.inf)[0]
    below_tx = ray_distance(ms, tx[None, :], [0, 0, -1])[0]
    if up_tx:
        raise SystemExit('gNB position is under or inside geometry')

    rmax = args.bins[-1]
    xs = np.arange(tx[0] - rmax, tx[0] + rmax + 1e-9, args.grid_m)
    ys = np.arange(tx[1] - rmax, tx[1] + rmax + 1e-9, args.grid_m)
    gx, gy = np.meshgrid(xs, ys, indexing='ij')
    pts = np.stack([gx.ravel(), gy.ravel(), np.full(gx.size, args.ue_height)], axis=1)
    d2d = np.hypot(pts[:, 0] - tx[0], pts[:, 1] - tx[1])
    inside = ((d2d >= args.bins[0]) & (d2d < rmax)
              & (pts[:, 0] > lo[0] + 10) & (pts[:, 0] < hi[0] - 10)
              & (pts[:, 1] > lo[1] + 10) & (pts[:, 1] < hi[1] - 10))
    pts, d2d = pts[inside], d2d[inside]
    n_grid = len(pts)

    open_sky = ~ray_hits(ms, pts, [0, 0, 1], np.inf)
    t_down = ray_distance(ms, pts, [0, 0, -1])
    on_ground = np.abs(t_down - args.ue_height) < 0.05
    clear = np.ones(len(pts), dtype=bool)
    for k in range(8):
        a = k * math.pi / 4
        clear &= ~ray_hits(ms, pts, [math.cos(a), math.sin(a), 0.0], args.clearance_m)
    keep = open_sky & on_ground & clear
    pts, d2d = pts[keep], d2d[keep]

    vec = tx[None, :] - pts
    dist = np.linalg.norm(vec, axis=1)
    los = ~ray_hits(ms, pts, vec / dist[:, None], dist - 0.05)

    rng = np.random.default_rng(args.seed)
    chosen, census = [], []
    for b in range(len(args.bins) - 1):
        lo_b, hi_b = args.bins[b], args.bins[b + 1]
        in_bin = (d2d >= lo_b) & (d2d < hi_b)
        for cls, mask in (('LOS', los), ('NLOS', ~los)):
            idx = np.flatnonzero(in_bin & mask)
            census.append({'bin_m': [lo_b, hi_b], 'class': cls, 'candidates': int(idx.size)})
            pick = rng.choice(idx, size=min(args.per_bin_class, idx.size), replace=False) \
                if idx.size else []
            for i in sorted(pick):
                chosen.append({'bin_m': [lo_b, hi_b], 'geometric_class': cls,
                               'position': [round(float(v), 3) for v in pts[i]],
                               'd2d_m': round(float(d2d[i]), 3),
                               'd3d_m': round(float(dist[i]), 3)})
    for n, c in enumerate(chosen):
        c['id'] = f'ue{n:02d}'

    out = {
        'generated_utc': datetime.now(timezone.utc).isoformat(),
        'tool': 'scripts/e1a_select_positions.py', 'sionna_rt': rt.__version__,
        'scene': args.scene, 'scene_bbox_xy': [lo.tolist(), hi.tolist()],
        'gnb': {'position': tx.tolist(), 'height_above_surface_below_m': float(below_tx),
                'open_sky_above': True,
                'note': 'Sionna RT tutorial transmitter site; rooftop-mounted'},
        'selection': {'ue_height_m': args.ue_height, 'grid_m': args.grid_m,
                      'clearance_m': args.clearance_m, 'distance_bins_m': args.bins,
                      'per_bin_and_class': args.per_bin_class, 'seed': args.seed,
                      'grid_points_in_annulus': n_grid, 'outdoor_open_points': int(keep.sum()),
                      'los_definition': 'unobstructed straight segment UE->gNB in scene mesh'},
        'census': census, 'positions': chosen,
    }
    with open(args.out, 'w', encoding='utf-8') as fh:
        json.dump(out, fh, indent=2)
    print(f'grid {n_grid}, outdoor/open {int(keep.sum())}, LOS {int(los.sum())}; '
          f'gNB {below_tx:.2f} m above surface below')
    for c in census:
        print(f"  bin {c['bin_m']} {c['class']:4s}: {c['candidates']} candidates")
    print(f'selected {len(chosen)} positions -> {args.out}')


if __name__ == '__main__':
    main()
