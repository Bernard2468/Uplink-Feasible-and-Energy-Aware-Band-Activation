#!/usr/bin/env python3
"""Phase 2, Gate E0b: physical frequency-scaling check of Sionna RT 1.2.0.

For a LOS-only path between single isotropic, co-polarised (V) elements, the
received/transmitted power ratio must equal free-space path gain
(lambda / (4 pi d))^2 exactly, at every frequency. Hence between two carriers
f1 < f2 the LOS path gain must drop by 20*log10(f2/f1) dB
(3.5 -> 7.2 GHz: 6.265 dB). Both properties are ASSERTED (tolerance 0.01 dB).

A second, informative configuration enables reflections and diffraction. Its
frequency gap is reported but not asserted: material parameters are
frequency dependent, so there is no closed-form expectation.

Positions default to the gNB/UE positions of the official
cttc-nr-demo-sionna-rt run (taken from its SionnaRtChannelModel log).
Usage (venv active):
  python scripts/phase2_frequency_scaling_check.py --tx 0 0 10 --rx 1.2 0.8 1.5 --out file.json
"""
import argparse
import json
import math
import sys

import numpy as np
import sionna.rt as rt
from sionna.rt import PathSolver, PlanarArray, Receiver, Transmitter, load_scene

C0 = float(rt.scene.speed_of_light)  # the exact constant Sionna RT itself uses


def path_gain_db(scene_name, freq_hz, tx, rx, full):
    scene = load_scene(getattr(rt.scene, scene_name), merge_shapes=False)
    scene.frequency = freq_hz
    scene.tx_array = PlanarArray(num_rows=1, num_cols=1, pattern='iso', polarization='V')
    scene.rx_array = PlanarArray(num_rows=1, num_cols=1, pattern='iso', polarization='V')
    scene.add(Transmitter(name='tx', position=list(tx)))
    scene.add(Receiver(name='rx', position=list(rx)))
    if full:
        kwargs = dict(max_depth=3, los=True, specular_reflection=True, diffuse_reflection=False,
                      refraction=True, diffraction=True, edge_diffraction=False)
    else:  # identical to the official example's solver configuration
        kwargs = dict(max_depth=1, los=True, specular_reflection=False, diffuse_reflection=False,
                      refraction=False, diffraction=False, edge_diffraction=False)
    paths = PathSolver()(scene=scene, synthetic_array=False, seed=49, **kwargs)
    a, _tau = paths.cir(out_type='numpy')
    a = np.asarray(a)
    n_paths = int(np.count_nonzero(np.abs(a) > 0))
    gain = float(np.sum(np.abs(a) ** 2))
    return (10 * math.log10(gain) if gain > 0 else float('-inf')), n_paths


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--scene', default='simple_street_canyon_with_cars')
    ap.add_argument('--tx', type=float, nargs=3, required=True)
    ap.add_argument('--rx', type=float, nargs=3, action='append', required=True)
    ap.add_argument('--f1', type=float, default=3.5e9)
    ap.add_argument('--f2', type=float, default=7.2e9)
    ap.add_argument('--out', default=None)
    args = ap.parse_args()

    expected_gap = 20 * math.log10(args.f2 / args.f1)
    report = {'sionna_rt': rt.__version__, 'scene': args.scene, 'tx': args.tx,
              'f1_hz': args.f1, 'f2_hz': args.f2, 'expected_los_gap_db': expected_gap,
              'tolerance_db': 0.01, 'links': []}
    ok = True
    for rx in args.rx:
        d = math.dist(args.tx, rx)
        link = {'rx': rx, 'distance_m': d}
        for tag, f in (('f1', args.f1), ('f2', args.f2)):
            fspl_db = 20 * math.log10(C0 / f / (4 * math.pi * d))
            los_db, n_los = path_gain_db(args.scene, f, args.tx, rx, full=False)
            full_db, n_full = path_gain_db(args.scene, f, args.tx, rx, full=True)
            link[tag] = {'free_space_gain_db': fspl_db, 'los_only_gain_db': los_db,
                         'los_only_paths': n_los, 'los_minus_free_space_db': los_db - fspl_db,
                         'full_gain_db': full_db, 'full_paths': n_full}
            if n_los != 1 or abs(los_db - fspl_db) > report['tolerance_db']:
                ok = False
        link['los_gap_db'] = link['f1']['los_only_gain_db'] - link['f2']['los_only_gain_db']
        link['full_gap_db'] = link['f1']['full_gain_db'] - link['f2']['full_gain_db']
        if abs(link['los_gap_db'] - expected_gap) > report['tolerance_db']:
            ok = False
        report['links'].append(link)
        print(f"rx={rx} d={d:.2f} m | LOS gain f1 {link['f1']['los_only_gain_db']:.3f} dB "
              f"(FSPL {link['f1']['free_space_gain_db']:.3f}), f2 {link['f2']['los_only_gain_db']:.3f} dB "
              f"(FSPL {link['f2']['free_space_gain_db']:.3f}) | LOS gap {link['los_gap_db']:.3f} dB "
              f"(expected {expected_gap:.3f}) | full-RT gap {link['full_gap_db']:.3f} dB "
              f"[{link['f1']['full_paths']}/{link['f2']['full_paths']} paths]")
    report['passed'] = ok
    if args.out:
        with open(args.out, 'w', encoding='utf-8') as fh:
            json.dump(report, fh, indent=2)
    print('FREQUENCY SCALING CHECK', 'PASSED' if ok else 'FAILED')
    sys.exit(0 if ok else 1)


if __name__ == '__main__':
    main()
