#!/usr/bin/env python3
"""E1a diagnostic: can Sionna RT 1.2 reach the NLOS UE positions?

Background (found 2026-11-06, see docs/PHASE_2_RADIO_PROTOCOL.md section 6):
the MAIN configuration (diffraction=True, edge_diffraction=False) found no path
to most NLOS positions and implausibly weak paths to others. In sionna-rt 1.2.0
(path_solvers/sb_candidate_generator.py) only FIRST-ORDER diffraction is
supported, diffraction is sampled with probability 0.2, and wedges are detected
within one mesh; roof (metal) and wall (marble) edges belong to different
meshes and are therefore free edges, diffracted only with edge_diffraction=True.

This script solves the NLOS positions for one variant per process and prints,
per UE, total gain, path count, and the TR 38.901 UMa NLOS median for scale.
Usage: python scripts/e1a_diffraction_diagnostic.py POSITIONS FREQ_HZ GNB_Z SAMPLES EDGE(0/1) OUT.json
"""
import json
import math
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'scripts'))
from cmwave.radio import pathloss_3gpp as pl3  # noqa: E402
from e1a_propagation import CONFIGS, MAIN, link_metrics, solve  # noqa: E402


def main():
    pos_file, freq, gz, samples, edge, out = sys.argv[1:7]
    freq, gz, samples, edge = float(freq), float(gz), int(samples), bool(int(edge))
    pos = json.loads(Path(pos_file).read_text(encoding='utf-8'))
    gnb = list(pos['gnb']['position'][:2]) + [gz]
    ues = [u for u in pos['positions'] if u['geometric_class'] == 'NLOS']
    cfg = dict(CONFIGS[MAIN], edge_diffraction=edge)
    gain, valid, tau, inter, el = solve(pos['scene'], freq, [('gnb', gnb)],
                                        [(u['id'], u['position']) for u in ues], cfg, 1, samples)
    res = []
    for i, u in enumerate(ues):
        m = link_metrics(gain[i, 0], valid[i, 0], tau[i, 0], inter[:, i, 0])
        d3 = math.dist(gnb, u['position'])
        m.update({'ue': u['id'], 'd2d_m': u['d2d_m'],
                  'uma_nlos_median_gain_db': -pl3.uma_nlos(u['d2d_m'], d3, freq, gz, 1.5)})
        res.append(m)
    Path(out).write_text(json.dumps({'freq_hz': freq, 'gnb_z': gz, 'samples': samples,
                                     'edge_diffraction': edge, 'solve_s': el, 'links': res},
                                    indent=1), encoding='utf-8')
    print(f'freq {freq/1e9:.1f} GHz gnb_z {gz} samples {samples:.0e} edge_diffraction {edge}: '
          f'solve {el:.1f} s, links with paths {sum(r["n_paths"] > 0 for r in res)}/{len(res)}')


if __name__ == '__main__':
    main()
