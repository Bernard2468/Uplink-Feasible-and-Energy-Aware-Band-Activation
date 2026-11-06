#!/usr/bin/env python3
"""E1.1: freeze the site geometry used by every later E1 step.

Writes, for one site (gNB) of a Sionna RT scene:
  <site>_mask.npz    every open outdoor grid point: x, y, z, d2d, d3d, los
  <site>_links.csv   the fixed UE positions with geometric LOS/NLOS state
  <site>_manifest.json  tool versions, parameters, file hashes and checks
The LOS state is DETERMINISTIC (unobstructed straight segment UE -> gNB in the
scene mesh) and is shared by both carriers. Grid parameters must equal those
of the positions file, so the fixed positions are members of the mask.

Checks:
  G1 recomputed LOS state of every fixed position equals the positions file
  G2 every fixed position is a point of the open outdoor grid
  G3 per-bin LOS/NLOS census equals the positions file census
  G4 recomputing the mask gives bit-identical arrays
"""
import argparse
import csv
import hashlib
import json
import platform
import sys
from datetime import datetime, timezone
from pathlib import Path

import drjit as dr
import mitsuba as mi
import numpy as np
import sionna.rt as rt
from sionna.rt import load_scene

sys.path.insert(0, str(Path(__file__).resolve().parent))
from e1a_select_positions import screen_grid  # noqa: E402


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--positions', required=True)
    ap.add_argument('--site-id', default='site_tx0')
    ap.add_argument('--outdir', required=True)
    args = ap.parse_args()
    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    pos = json.loads(Path(args.positions).read_text(encoding='utf-8'))
    sel = pos['selection']
    tx = np.array(pos['gnb']['position'], dtype=float)
    scene = load_scene(getattr(rt.scene, pos['scene']), merge_shapes=True)
    ms = scene.mi_scene
    bins = sel['distance_bins_m']

    def compute():
        return screen_grid(ms, tx, sel['ue_height_m'], sel['grid_m'], sel['clearance_m'],
                           bins[0], bins[-1])

    pts, d2d, d3d, los, n_grid = compute()
    pts2, d2d2, d3d2, los2, _ = compute()
    checks = {'G4_mask_reproducible': {
        'passed': bool(np.array_equal(pts, pts2) and np.array_equal(los, los2)
                       and np.array_equal(d3d, d3d2))}}

    # G2 + G1: fixed positions are grid members with the same LOS state.
    index = {(round(p[0], 3), round(p[1], 3)): i for i, p in enumerate(pts)}
    rows, g1, g2 = [], [], []
    for u in pos['positions']:
        key = (round(u['position'][0], 3), round(u['position'][1], 3))
        i = index.get(key)
        if i is None:
            g2.append(u['id'])
            continue
        state = 'LOS' if los[i] else 'NLOS'
        if state != u['geometric_class']:
            g1.append({'ue': u['id'], 'stored': u['geometric_class'], 'recomputed': state})
        rows.append({'site_id': args.site_id, 'ue_id': u['id'],
                     'x_m': u['position'][0], 'y_m': u['position'][1], 'z_m': u['position'][2],
                     'h_ut_m': u['position'][2], 'h_bs_m': float(tx[2]),
                     'd2d_m': round(float(d2d[i]), 4), 'd3d_m': round(float(d3d[i]), 4),
                     'los_state': state, 'distance_bin_m': f"{u['bin_m'][0]}-{u['bin_m'][1]}"})
    checks['G1_fixed_positions_los_state'] = {'passed': not g1 and not g2, 'mismatches': g1}
    checks['G2_fixed_positions_on_grid'] = {'passed': not g2, 'missing': g2}

    # G3: census per bin.
    census, mism = [], []
    for c in pos['census']:
        lo_b, hi_b = c['bin_m']
        m = (d2d >= lo_b) & (d2d < hi_b) & (los if c['class'] == 'LOS' else ~los)
        n = int(m.sum())
        census.append({'bin_m': [lo_b, hi_b], 'class': c['class'], 'points': n})
        if n != c['candidates']:
            mism.append({'bin_m': [lo_b, hi_b], 'class': c['class'], 'stored': c['candidates'],
                         'recomputed': n})
    checks['G3_census_matches'] = {'passed': not mism, 'mismatches': mism}

    mask_path = outdir / f'{args.site_id}_mask.npz'
    np.savez_compressed(mask_path, x=pts[:, 0], y=pts[:, 1], z=pts[:, 2], d2d=d2d, d3d=d3d,
                        los=los.astype(np.uint8), gnb=tx)
    links_path = outdir / f'{args.site_id}_links.csv'
    with links_path.open('w', newline='', encoding='utf-8') as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)

    passed = all(v['passed'] for v in checks.values())
    manifest = {
        'generated_utc': datetime.now(timezone.utc).isoformat(), 'tool': 'scripts/e1_geometry.py',
        'site_id': args.site_id, 'scene': pos['scene'], 'gnb_position_m': tx.tolist(),
        'versions': {'python': platform.python_version(), 'sionna_rt': rt.__version__,
                     'mitsuba': mi.__version__, 'drjit': dr.__version__},
        'positions_file': args.positions, 'positions_sha256': sha256(args.positions),
        'grid': {k: sel[k] for k in ('ue_height_m', 'grid_m', 'clearance_m', 'distance_bins_m')},
        'los_definition': sel['los_definition'],
        'counts': {'grid_points_in_annulus': n_grid, 'open_outdoor_points': int(len(pts)),
                   'los_points': int(los.sum()), 'fixed_positions': len(rows)},
        'census': census,
        'files': {mask_path.name: sha256(mask_path), links_path.name: sha256(links_path)},
        'checks': checks, 'all_checks_passed': passed,
    }
    (outdir / f'{args.site_id}_manifest.json').write_text(json.dumps(manifest, indent=2),
                                                          encoding='utf-8')
    for k, v in checks.items():
        print(f"{k:32s} {'PASS' if v['passed'] else 'FAIL'}")
    print(f"open outdoor points {len(pts)} (LOS {int(los.sum())}); fixed positions {len(rows)}")
    print('E1.1 GEOMETRY', 'FROZEN' if passed else 'CHECKS FAILED')
    sys.exit(0 if passed else 1)


if __name__ == '__main__':
    main()
