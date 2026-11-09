#!/usr/bin/env python3
"""E1c.2 geometry: a 19-site hexagonal layout around site 0 in the Sionna scene, and the link
geometry from every E1.1 outdoor point and every E1c indoor sample to every site.

Implements the geometry part of configs/e1/e1c2_interference.json. Run inside WSL (project venv).
Sites (per ISD): hexagonal lattice anchored at site 0 (lattice vectors (ISD, 0) and
(ISD/2, ISD sqrt(3)/2)), rings 0-2 (19 sites, ITU-R M.2412 section 8.3 layout without wrap-around).
  in-scene lattice point (> 50 m inside the scene bbox): the nearest rooftop point (2 m search grid,
    roof >= 6 m) within 0.2 ISD, else a ground mast at the lattice point; antenna height
    max(25 m, roof + 1 m) (M.2412 Table 5(b): BS antenna height 25 m; site 0 = the E1.1 gNB);
  lattice point outside: a virtual site at the lattice point, 25 m, every link NLOS (the scene has no
    LOS grid point beyond 200 m from site 0; E1.1 census).
Links: outdoor point -> LOS by the E1.1 segment test; indoor sample -> facade toward the site
(horizontal ray, E1c rule), d2D-in and facade LOS (virtual sites: facade NLOS). A sample whose
facade toward some in-scene site lies beyond that site (inside the site's building) is flagged.
Outputs in --outdir: e1c2_sites_isd<ISD>.npz, e1c2_geometry_manifest.json (checks S1-S4).
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
from e1a_select_positions import ray_distance, ray_hits  # noqa: E402

ISDS = (200.0, 500.0)
MARGIN_M = 50.0
SNAP_FRAC = 0.2
SNAP_GRID_M = 2.0
MIN_ROOF_M = 6.0
H_BS_MIN = 25.0
STEP_OUT_M = 0.05
UE_H = 1.5


def sha256(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def ring_of(i, j):
    return max(abs(i), abs(j), abs(i + j))


def lattice(tx, isd):
    a1, a2 = np.array([isd, 0.0]), np.array([isd / 2, isd * math.sqrt(3) / 2])
    out = []
    for i in range(-2, 3):
        for j in range(-2, 3):
            if ring_of(i, j) <= 2:
                out.append((i, j, tx[:2] + i * a1 + j * a2))
    out.sort(key=lambda t: (ring_of(t[0], t[1]), t[0], t[1]))
    return out


def roof_height(ms, xy):
    o = np.column_stack([xy, np.full(len(xy), 300.0)])
    return 300.0 - ray_distance(ms, o, [0, 0, -1])


def place_sites(ms, tx, isd, lo, hi):
    sites = []
    for i, j, p in lattice(tx, isd):
        if i == 0 and j == 0:
            sites.append({'i': 0, 'j': 0, 'ring': 0, 'x': float(tx[0]), 'y': float(tx[1]), 'h': float(tx[2]),
                          'kind': 'site0', 'roof': float(roof_height(ms, tx[None, :2])[0]), 'snap_m': 0.0})
            continue
        inside = bool(np.all(p > lo + MARGIN_M) and np.all(p < hi - MARGIN_M))
        if not inside:
            sites.append({'i': i, 'j': j, 'ring': ring_of(i, j), 'x': float(p[0]), 'y': float(p[1]), 'h': H_BS_MIN,
                          'kind': 'virtual', 'roof': 0.0, 'snap_m': 0.0})
            continue
        rho = SNAP_FRAC * isd
        g = np.arange(-rho, rho + 1e-9, SNAP_GRID_M)
        X, Y = np.meshgrid(g, g, indexing='ij')
        d = np.hypot(X, Y).ravel()
        cand = p[None, :] + np.column_stack([X.ravel(), Y.ravel()])
        cand, d = cand[d <= rho], d[d <= rho]
        h = roof_height(ms, cand)
        ok = h >= MIN_ROOF_M
        if ok.any():
            k = int(np.argmin(np.where(ok, d, np.inf)))
            sites.append({'i': i, 'j': j, 'ring': ring_of(i, j), 'x': float(cand[k, 0]), 'y': float(cand[k, 1]),
                          'h': float(max(H_BS_MIN, h[k] + 1.0)), 'kind': 'rooftop', 'roof': float(h[k]),
                          'snap_m': float(d[k])})
        else:
            sites.append({'i': i, 'j': j, 'ring': ring_of(i, j), 'x': float(p[0]), 'y': float(p[1]), 'h': H_BS_MIN,
                          'kind': 'mast', 'roof': float(roof_height(ms, p[None, :])[0]), 'snap_m': 0.0})
    return sites


def outdoor_los(ms, pts, site):
    s = np.array([site['x'], site['y'], site['h']])
    v = s[None, :] - pts
    d = np.linalg.norm(v, axis=1)
    return ~ray_hits(ms, pts, v / d[:, None], d - 0.05)


def indoor_links(ms, Q, site):
    """Facade toward the site (horizontal ray at the UE height), d2D-in, facade LOS, and whether the
    facade lies beyond the site (sample inside the site's building)."""
    s = np.array([site['x'], site['y'], site['h']])
    vh = s[None, :2] - Q[:, :2]
    dh = np.hypot(vh[:, 0], vh[:, 1])
    under = dh < 1e-9          # sample directly beneath the antenna: inside the site's building (run 1)
    safe = np.where(under, 1.0, dh)
    u = np.column_stack([np.where(under, 1.0, vh[:, 0] / safe), np.where(under, 0.0, vh[:, 1] / safe),
                         np.zeros(len(Q))])
    d_in = ray_distance(ms, Q, u)
    beyond = under | ~(d_in < dh)
    F = Q + (np.where(np.isfinite(d_in), d_in, 0.0) + STEP_OUT_M)[:, None] * u
    v = s[None, :] - F
    dv = np.linalg.norm(v, axis=1)
    los = ~ray_hits(ms, F, v / dv[:, None], dv - 0.05)
    if site['kind'] == 'virtual':
        los = np.zeros(len(Q), dtype=bool)
    return d_in, los, beyond, dh


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--positions', default=str(ROOT / 'configs/e1/positions_munich_tx0.json'))
    ap.add_argument('--e11-dir', default=str(ROOT / 'results/phase2/e1_geometry'))
    ap.add_argument('--e1c-dir', default=str(ROOT / 'results/phase2/e1c_geometry'))
    ap.add_argument('--outdir', default=str(ROOT / 'results/phase2/e1c2_geometry'))
    args = ap.parse_args()
    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    pos = json.loads(Path(args.positions).read_text(encoding='utf-8'))
    tx = np.array(pos['gnb']['position'], dtype=float)
    e11 = json.loads((Path(args.e11_dir) / 'site_tx0_manifest.json').read_text())
    e1c = json.loads((Path(args.e1c_dir) / 'site_tx0_indoor_manifest.json').read_text())
    mpath, gpath = Path(args.e11_dir) / 'site_tx0_mask.npz', Path(args.e1c_dir) / 'site_tx0_indoor.npz'
    if not (e11['all_checks_passed'] and e1c['all_checks_passed'] and sha256(mpath) == e11['files'][mpath.name]
            and sha256(gpath) == e1c['files'][gpath.name]):
        raise SystemExit('E1.1 / E1c geometry not validated or changed')
    with np.load(mpath) as z:
        mask = {k: z[k] for k in z.files}
    with np.load(gpath) as z:
        geo = {k: z[k] for k in z.files}
    scene = load_scene(getattr(rt.scene, pos['scene']), merge_shapes=True)
    ms = scene.mi_scene
    bb = ms.bbox()
    lo, hi = np.array([bb.min[0], bb.min[1]]), np.array([bb.max[0], bb.max[1]])
    P = np.column_stack([mask['x'], mask['y'], mask['z']])
    Q = np.column_stack([geo['x'], geo['y'], geo['h_ut']])
    checks, files, summary = {}, {}, {}
    for isd in ISDS:
        sites = place_sites(ms, tx, isd, lo, hi)
        S = len(sites)
        los_o = np.zeros((len(P), S), dtype=bool)
        din = np.zeros((len(Q), S))
        los_i = np.zeros((len(Q), S), dtype=bool)
        beyond = np.zeros((len(Q), S), dtype=bool)
        dh = np.zeros((len(Q), S))
        for k, s in enumerate(sites):
            los_o[:, k] = outdoor_los(ms, P, s) if s['kind'] != 'virtual' else False
            din[:, k], los_i[:, k], b, dh[:, k] = indoor_links(ms, Q, s)
            beyond[:, k] = b if s['kind'] != 'virtual' else False
        # S1: site 0 column reproduces E1.1 LOS and the E1c facade geometry exactly
        s1 = (np.array_equal(los_o[:, 0], mask['los'].astype(bool)) and np.array_equal(din[:, 0], geo['d2d_in'])
              and np.array_equal(los_i[:, 0].astype(np.uint8), geo['facade_los']))
        checks[f'S1_site0_reproduced_isd{isd:.0f}'] = {'passed': bool(s1)}
        # S2: placement rule
        ok2 = (len(sites) == 19 and sites[0]['kind'] == 'site0' and abs(sites[0]['h'] - max(H_BS_MIN, sites[0]['roof'] + 1)) < 1e-9
               and all(s['snap_m'] <= SNAP_FRAC * isd + 1e-9 for s in sites)
               and all(s['h'] >= H_BS_MIN for s in sites))
        checks[f'S2_placement_isd{isd:.0f}'] = {'passed': bool(ok2), 'kinds': {k: sum(s['kind'] == k for s in sites)
                                                                               for k in ('site0', 'rooftop', 'mast', 'virtual')}}
        # Exclusions (run 1, S3 failed): (a) the sample lies inside a site's building (beneath the
        # antenna, or its facade toward that site lies beyond the site): served through the roof,
        # outside the O2I model; (b) its horizontal ray toward some site leaves the building mesh
        # without a wall hit (mesh opening): no facade.
        in_bldg = beyond.any(axis=1)
        no_facade = (~np.isfinite(din)).any(axis=1) & ~in_bldg
        excl = in_bldg | no_facade
        # S3: every kept indoor sample has a finite facade toward every site; few exclusions
        ok3 = bool(np.isfinite(din[~excl]).all() and excl.mean() <= 0.05 and no_facade.sum() <= 10)
        checks[f'S3_finite_facades_isd{isd:.0f}'] = {'passed': ok3, 'excluded_in_site_building': int(in_bldg.sum()),
                                                      'excluded_no_facade': int(no_facade.sum()),
                                                      'excluded_fraction': float(excl.mean())}
        out = outdir / f'e1c2_sites_isd{isd:.0f}.npz'
        np.savez_compressed(out, site_x=np.array([s['x'] for s in sites]), site_y=np.array([s['y'] for s in sites]),
                            site_h=np.array([s['h'] for s in sites]), site_ring=np.array([s['ring'] for s in sites]),
                            site_kind=np.array([s['kind'] for s in sites]), site_i=np.array([s['i'] for s in sites]),
                            site_j=np.array([s['j'] for s in sites]), los_out=los_o, din=np.where(np.isfinite(din), din, -1.0),
                            facade_los=los_i, in_site_building=beyond, indoor_excluded=excl)
        files[out.name] = sha256(out)
        summary[f'isd{isd:.0f}'] = {'sites': sites, 'indoor_samples_in_a_site_building': int(in_bldg.sum()),
                                    'indoor_samples_without_facade': int(no_facade.sum()),
                                    'outdoor_los_fraction_per_site': los_o.mean(axis=0).round(4).tolist()}
    # S4: reproducible (second evaluation of the ISD 200 site placement)
    again = place_sites(ms, tx, ISDS[0], lo, hi)
    checks['S4_placement_reproducible'] = {'passed': again == summary['isd200']['sites']}
    passed = all(v['passed'] for v in checks.values())
    manifest = {'generated_utc': datetime.now(timezone.utc).isoformat(), 'tool': 'scripts/e1c2_geometry_sites.py',
                'versions': {'python': platform.python_version(), 'sionna_rt': rt.__version__, 'mitsuba': mi.__version__,
                             'drjit': dr.__version__},
                'rules': {'margin_m': MARGIN_M, 'snap_frac': SNAP_FRAC, 'snap_grid_m': SNAP_GRID_M,
                          'min_roof_m': MIN_ROOF_M, 'h_bs_min_m': H_BS_MIN},
                'inputs_sha256': {mpath.name: sha256(mpath), gpath.name: sha256(gpath)},
                'summary': summary, 'files': files, 'checks': checks, 'all_checks_passed': passed}
    (outdir / 'e1c2_geometry_manifest.json').write_text(json.dumps(manifest, indent=2), encoding='utf-8')
    for k, v in checks.items():
        print(f"{k:34s} {'PASS' if v['passed'] else 'FAIL'} {v.get('kinds', '')}")
    print({k: v['indoor_samples_in_a_site_building'] for k, v in summary.items()})
    print('E1c.2 GEOMETRY', 'PASSED' if passed else 'CHECKS FAILED')
    sys.exit(0 if passed else 1)


if __name__ == '__main__':
    main()
