import math
import unittest

import numpy as np

from cmwave.radio import antenna as ant


class AntennaTests(unittest.TestCase):

    def test_element_pattern_hand_values(self):
        # TR 38.901 Table 7.3-1: 8 dBi peak, 3 dB at +-32.5 deg, combined loss capped at 30 dB.
        self.assertAlmostEqual(float(ant.element_gain_dbi(90, 0)), 8.0, places=12)
        self.assertAlmostEqual(float(ant.element_gain_dbi(90, 32.5)), 5.0, places=12)
        self.assertAlmostEqual(float(ant.element_gain_dbi(122.5, 0)), 5.0, places=12)
        self.assertAlmostEqual(float(ant.element_gain_dbi(90, 180)), -22.0, places=12)
        want = 8 - (12 * (10 / 65) ** 2 + 12 * (40 / 65) ** 2)
        self.assertAlmostEqual(float(ant.element_gain_dbi(100, 40)), want, places=12)
        self.assertAlmostEqual(float(ant.element_gain_dbi(160, 120)), -22.0, places=12)
        self.assertAlmostEqual(float(ant.element_gain_dbi(90, 350)),
                               float(ant.element_gain_dbi(90, -10)), places=12)

    def test_subarray_factor_closed_form(self):
        # Oracle: Dirichlet kernel |sin(K psi / 2) / (K sin(psi / 2))|^2, psi = pi (cos th - cos th_t).
        k = 4
        for th in (92.0, 110.0, 125.0, 141.9):
            psi = math.pi * (math.cos(math.radians(th)) - math.cos(math.radians(100.0)))
            want = 10 * math.log10((math.sin(k * psi / 2) / (k * math.sin(psi / 2))) ** 2)
            self.assertAlmostEqual(float(ant.subarray_factor_db(th, k, 10.0)), want, places=9)
        self.assertAlmostEqual(float(ant.subarray_factor_db(100.0, 4, 10.0)), 0.0, places=12)
        self.assertTrue(np.all(ant.subarray_factor_db(np.linspace(90, 180, 91), 1, 0.0) == 0))
        self.assertLess(float(ant.subarray_factor_db(132.349, 4, 10.0)), -40.0)   # first null

    def test_nominal_gains_match_source(self):
        # Nokia 2024 Table 1: 29 dBi (8x16x2) and 35 dBi (16x32x2) with 8 dBi radiators.
        self.assertAlmostEqual(ant.GnbPanel(8, 16).nominal_gain_dbi, 29.0, delta=0.1)
        self.assertAlmostEqual(ant.GnbPanel(16, 32).nominal_gain_dbi, 35.0, delta=0.1)
        self.assertEqual(ant.GnbPanel(isotropic=True).nominal_gain_dbi, 0.0)

    def test_gnb_gain_and_sector(self):
        p = ant.GnbPanel(8, 16)
        g, s = ant.gnb_gain_dbi(p, np.array([90.0]), np.array([150.0]))
        self.assertAlmostEqual(g[0], 8 + 10 * math.log10(128), places=12)
        self.assertEqual(s[0], 1)
        # azimuth 200: offsets 170 / 50 / -70 deg from sectors 30 / 150 / 270 -> sector 150.
        g, s = ant.gnb_gain_dbi(p, np.array([100.0]), np.array([200.0]))
        self.assertEqual(s[0], 1)
        self.assertAlmostEqual(g[0], 8 - 12 * (10 / 65) ** 2 - 12 * (50 / 65) ** 2 + 10 * math.log10(128),
                               places=12)
        g, s = ant.gnb_gain_dbi(p, np.array([90.0]), np.array([90.0]))   # exact tie -> first sector
        self.assertEqual(s[0], 0)
        g, s = ant.gnb_gain_dbi(ant.GnbPanel(isotropic=True), np.array([120.0]), np.array([10.0]))
        self.assertEqual(g[0], 0.0)

    def test_gain_bound_and_carrier_independent_sector(self):
        rng = np.random.default_rng(1)
        th, ph = rng.uniform(90, 180, 2000), rng.uniform(-180, 180, 2000)
        for small, big in ((ant.GnbPanel(8, 16), ant.GnbPanel(16, 32)),
                           (ant.GnbPanel(8, 16, 4, 10.0), ant.GnbPanel(16, 32, 4, 10.0))):
            g1, s1 = ant.gnb_gain_dbi(small, th, ph)
            g2, s2 = ant.gnb_gain_dbi(big, th, ph)
            self.assertTrue(np.all(g1[np.isfinite(g1)] <= small.nominal_gain_dbi + 1e-12))
            np.testing.assert_array_equal(s1, s2)
            m = np.isfinite(g1)
            np.testing.assert_allclose(g2[m] - g1[m], 10 * math.log10(4), atol=1e-12)

    def test_rotated_sectors(self):
        p = ant.GnbPanel(8, 16, sector_bearings_deg=(90.0, 210.0, 330.0))
        g, s = ant.gnb_gain_dbi(p, np.array([90.0]), np.array([90.0]))
        self.assertEqual(s[0], 0)
        self.assertAlmostEqual(g[0], p.nominal_gain_dbi, places=12)

    def test_link_angles(self):
        z, a = ant.link_angles([8.5, 21.0, 27.0], [[8.5, 41.0, 1.5]])
        self.assertAlmostEqual(z[0], 90 + math.degrees(math.atan2(25.5, 20.0)), places=12)
        self.assertAlmostEqual(a[0], 90.0, places=12)

    def test_ue_gain_and_validation(self):
        self.assertEqual(ant.ue_rx_gain_db(1), 0.0)
        self.assertAlmostEqual(ant.ue_rx_gain_db(4), 10 * math.log10(4), places=12)
        with self.assertRaises(ValueError):
            ant.GnbPanel(8, 16, subarray_rows=3)
        with self.assertRaises(ValueError):
            ant.GnbPanel(8, 16, etilt_deg=10.0)
        with self.assertRaises(ValueError):
            ant.GnbPanel(2, 2, isotropic=True)
        with self.assertRaises(ValueError):
            ant.ue_rx_gain_db(0)


if __name__ == '__main__':
    unittest.main()
