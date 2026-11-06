import math
import unittest

import numpy as np

from cmwave.radio import pathloss_3gpp as pl3
from cmwave.radio import uma_hybrid as uh

F1, F2 = 3.5e9, 7.2e9


def link(ue, x, y, state, h_bs=27.0, h_ut=1.5, gnb=(0.0, 0.0)):
    d2 = math.hypot(x - gnb[0], y - gnb[1])
    return {'ue_id': ue, 'x_m': x, 'y_m': y, 'd2d_m': d2,
            'd3d_m': math.hypot(d2, h_bs - h_ut), 'h_bs_m': h_bs, 'h_ut_m': h_ut,
            'los_state': state}


class UmaHybridTests(unittest.TestCase):

    def test_deterministic_pathloss_follows_state(self):
        l = link('a', 80.0, 0.0, 'LOS')
        self.assertEqual(uh.deterministic_pathloss_db(l['d2d_m'], l['d3d_m'], F1, 27, 1.5, 'LOS'),
                         pl3.uma_los(l['d2d_m'], l['d3d_m'], F1, 27, 1.5))
        self.assertEqual(uh.deterministic_pathloss_db(l['d2d_m'], l['d3d_m'], F1, 27, 1.5, 'NLOS'),
                         pl3.uma_nlos(l['d2d_m'], l['d3d_m'], F1, 27, 1.5))
        with self.assertRaises(ValueError):
            uh.deterministic_pathloss_db(80, 84, F1, 27, 1.5, 'los')

    def test_state_is_input_not_drawn(self):
        links = [link('a', 50, 0, 'LOS'), link('b', 300, 0, 'NLOS')]
        rows = uh.compute_links(links, [F1, F2], n_real=50, seed=3)
        for r in rows:
            self.assertEqual(r['los_state'], {'a': 'LOS', 'b': 'NLOS'}[r['ue_id']])

    def test_reproducible_and_seed_dependent(self):
        links = [link('a', 50, 0, 'LOS'), link('b', 300, 0, 'NLOS')]
        a = uh.compute_links(links, [F1, F2], 20, seed=7)
        b = uh.compute_links(links, [F1, F2], 20, seed=7)
        c = uh.compute_links(links, [F1, F2], 20, seed=8)
        self.assertEqual(a, b)
        self.assertNotEqual([r['shadowing_db'] for r in a], [r['shadowing_db'] for r in c])

    def test_shadowing_shared_across_carriers_and_gap_exact(self):
        # NLOS links and LOS links inside the breakpoint: gap = 20 log10(f2/f1) exactly.
        links = [link('n1', 200, 0, 'NLOS'), link('n2', 0, 450, 'NLOS'), link('l1', 60, 0, 'LOS')]
        rows = uh.compute_links(links, [F1, F2], 100, seed=1)
        by = {(r['realization'], r['ue_id'], r['frequency_hz']): r for r in rows}
        for r in range(100):
            for ue in ('n1', 'n2', 'l1'):
                a, b = by[(r, ue, F1)], by[(r, ue, F2)]
                self.assertEqual(a['shadowing_db'], b['shadowing_db'])
                self.assertAlmostEqual(b['total_loss_db'] - a['total_loss_db'],
                                       uh.frequency_gap_db(F1, F2), places=9)

    def test_marginal_shadowing_statistics(self):
        links = [link('l', 60, 0, 'LOS'), link('n', 400, 0, 'NLOS')]
        x = uh.shadowing_normals(np.array([[60, 0], [400, 0]]), ['LOS', 'NLOS'], 40000, seed=11)
        for j, state in enumerate(('LOS', 'NLOS')):
            sf = uh.SF_STD_DB[state] * x[:, j]
            self.assertLess(abs(sf.mean()), 0.1)
            self.assertLess(abs(sf.std() - uh.SF_STD_DB[state]), 0.1)

    def test_spatial_correlation_and_state_independence(self):
        xy = np.array([[0, 0], [50, 0], [0, 200], [37, 200]], dtype=float)
        states = ['NLOS', 'NLOS', 'LOS', 'LOS']
        x = uh.shadowing_normals(xy, states, 40000, seed=5)
        c = np.corrcoef(x.T)
        self.assertAlmostEqual(c[0, 1], math.exp(-1), delta=0.03)   # NLOS, 50 m apart
        self.assertAlmostEqual(c[2, 3], math.exp(-1), delta=0.03)   # LOS, 37 m apart
        self.assertAlmostEqual(c[0, 2], 0.0, delta=0.03)            # different fields

    def test_input_validation(self):
        with self.assertRaises(ValueError):
            uh.shadowing_normals(np.zeros((2, 2)), ['LOS'], 5, 1)
        with self.assertRaises(ValueError):
            uh.shadowing_normals(np.zeros((1, 2)), ['MAYBE'], 5, 1)
        with self.assertRaises(ValueError):
            uh.shadowing_normals(np.zeros((1, 2)), ['LOS'], 0, 1)


if __name__ == '__main__':
    unittest.main()
