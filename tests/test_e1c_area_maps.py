"""E1c.1 engine: exact class probabilities, thresholds, quadrature, UMa hE mixture."""
import json
import math
import sys
import unittest
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'scripts'))
import e1c_area_maps as m  # noqa: E402
from cmwave.radio import pathloss_3gpp as pl3  # noqa: E402


class GeometryHelpers(unittest.TestCase):

    def test_disk_radius(self):
        self.assertAlmostEqual(m.disk_radius(200.0), 105.0076, places=3)
        self.assertAlmostEqual(m.disk_radius(500.0), 262.5191, places=3)
        hexagon = 3 * math.sqrt(3) / 2 * (500 / math.sqrt(3)) ** 2
        self.assertAlmostEqual(math.pi * m.disk_radius(500.0) ** 2, hexagon, places=6)


class ProbabilityTests(unittest.TestCase):

    def test_interval_prob(self):
        self.assertAlmostEqual(float(m.interval_prob(-np.inf, 0.0)), 0.5, places=15)
        self.assertAlmostEqual(float(m.interval_prob(-1.0, 1.0)), 0.682689492137, places=11)
        self.assertEqual(float(m.interval_prob(1.0, 1.0)), 0.0)
        self.assertEqual(float(m.interval_prob(2.0, 1.0)), 0.0)
        self.assertAlmostEqual(float(m.interval_prob(10.0, np.inf)) / 7.61985302416e-24, 1.0, places=9)

    def test_class_probs_against_integration(self):
        rng = np.random.default_rng(1)
        z = np.linspace(-12, 12, 480001)
        pdf = np.exp(-z ** 2 / 2) / math.sqrt(2 * math.pi) * (z[1] - z[0])
        for _ in range(40):
            mm = rng.normal(0, 2, size=4)
            mm[rng.random(4) < 0.1] = rng.choice([-np.inf, np.inf])
            marg = {(0, 'DL'): np.array([mm[0]]), (0, 'UL'): np.array([mm[1]]),
                    (1, 'DL'): np.array([mm[2]]), (1, 'UL'): np.array([mm[3]])}
            cp = m.class_probs(marg)
            d35, u35, d72, u72 = (z <= mm[0]), (z <= mm[1]), (z <= mm[2]), (z <= mm[3])
            ref = {'K1_both_carriers_both_directions': d35 & u35 & d72 & u72,
                   'K2_7p2_dl_only_with_3p5_ul': d72 & ~u72 & u35,
                   'K3_7p2_dl_without_any_ul': d72 & ~u72 & ~u35,
                   'K4_7p2_dl_not_feasible': ~d72, m.K5: ~d72 & u72,
                   m.e14.K6: d72 & u72 & ~(d35 & u35)}
            for k, ind in ref.items():
                self.assertAlmostEqual(float(cp[k][0]), float(np.sum(pdf[ind])), places=4, msg=k)
            self.assertAlmostEqual(float(sum(cp[n][0] for n in m.PARTITION)), 1.0, places=12)

    def test_margins(self):
        s = np.array([0.0, 10.0])
        self.assertTrue(np.all(np.isneginf(m.margins(s, np.inf, np.array([4.0, 4.0])))))
        self.assertTrue(np.all(np.isposinf(m.margins(s, -np.inf, np.array([4.0, 4.0])))))
        self.assertEqual(list(m.margins(s, 2.0, np.array([4.0, 2.0]))), [-0.5, 4.0])


class ThresholdTests(unittest.TestCase):

    def test_snr_threshold(self):
        fn = lambda s, d: np.clip(np.asarray(s, dtype=float) / 10.0, 0, 3)  # noqa: E731
        self.assertAlmostEqual(m.snr_threshold(fn, 'UL', 1.5), 15.0, places=9)
        self.assertAlmostEqual(m.snr_threshold(fn, 'UL', 1.5, factor=0.5), 30.0, places=9)
        self.assertTrue(np.isposinf(m.snr_threshold(fn, 'UL', 5.0)))
        self.assertTrue(np.isneginf(m.snr_threshold(lambda s, d: np.ones_like(s), 'UL', 0.5)))
        t = m.snr_threshold(fn, 'UL', 1.5)
        self.assertGreaterEqual(float(fn(np.array([t]), 'UL')[0]), 1.5)
        self.assertLess(float(fn(np.array([t - 1e-9]), 'UL')[0]), 1.5)

    def test_stoch_nodes(self):
        x, w = m.stoch_din_nodes(24)
        self.assertAlmostEqual(float(w.sum()), 1.0, places=12)
        self.assertAlmostEqual(float(np.sum(w * x)), 25 / 3, places=10)            # E[25 min(U1, U2)]
        self.assertTrue(np.all((x > 0) & (x < 25)))


class UmaHeightTests(unittest.TestCase):

    def test_he_distribution(self):
        self.assertEqual(pl3.uma_he_distribution(300.0, 10.5), [(1.0, 1.0)])
        self.assertEqual(pl3.uma_he_distribution(10.0, 22.5), [(1.0, 1.0)])          # d2D <= 18 m: C = 0
        d = pl3.uma_he_distribution(300.0, 22.5)
        self.assertEqual([h for h, _ in d], [1.0, 12.0, 15.0, 18.0, 21.0])
        self.assertAlmostEqual(sum(w for _, w in d), 1.0, places=15)
        c = ((22.5 - 13) / 10) ** 1.5 * 1.25 * 3 ** 3 * math.exp(-2)
        self.assertAlmostEqual(d[0][1], 1 / (1 + c), places=15)
        self.assertEqual([h for h, _ in pl3.uma_he_distribution(300.0, 13.5)], [1.0, 12.0])
        with self.assertRaises(ValueError):
            pl3.uma_he_distribution(300.0, 25.5)

    def test_he_functions_match_original(self):
        for d2 in (30.0, 200.0, 580.0):
            for h in (1.5, 4.5, 10.5):
                d3 = math.hypot(d2, 27 - h)
                for f in (3.5e9, 7.2e9):
                    self.assertEqual(pl3.uma_los_he(d2, d3, f, 27.0, h, 1.0), pl3.uma_los(d2, d3, f, 27.0, h))
                    self.assertEqual(pl3.uma_nlos_he(d2, d3, f, 27.0, h, 1.0), pl3.uma_nlos(d2, d3, f, 27.0, h))
        # beyond the breakpoint hE matters (tall floor, far UE, 3.5 GHz)
        d3 = math.hypot(580.0, 27 - 22.5)
        self.assertNotEqual(pl3.uma_los_he(580.0, d3, 3.5e9, 27.0, 22.5, 21.0),
                            pl3.uma_los_he(580.0, d3, 3.5e9, 27.0, 22.5, 1.0))


class ConfigTests(unittest.TestCase):

    def test_config(self):
        conf = json.loads(m.CONFIG.read_text())
        self.assertEqual(set(conf['variants']), set(m.VARIANTS))
        for g in ('C0_inputs', 'C2_engine_exact', 'C3_e155_agreement', 'C4_probabilities', 'C5_mapping'):
            self.assertIn(g, conf['gates'])
        self.assertTrue(conf['population']['radii_m']['isd200'].startswith('105.01'))
        self.assertTrue(conf['population']['radii_m']['isd500'].startswith('262.52'))


if __name__ == '__main__':
    unittest.main()
