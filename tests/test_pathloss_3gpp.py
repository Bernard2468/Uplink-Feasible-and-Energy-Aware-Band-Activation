import math
import unittest

from cmwave.radio import pathloss_3gpp as pl


class PathLoss3gppTests(unittest.TestCase):
    """Expected values are computed by hand from TR 38.901 Table 7.4.1-1."""

    def test_uma_los_pl1_hand_value(self):
        # d3D = 100 m, 3.5 GHz: 28 + 22*2 + 20*log10(3.5)
        d2d = math.sqrt(100 ** 2 - (27 - 1.5) ** 2)
        self.assertAlmostEqual(pl.uma_los(d2d, 100.0, 3.5e9, 27.0, 1.5),
                               28 + 44 + 20 * math.log10(3.5), places=9)

    def test_breakpoint_hand_value(self):
        # 4 * (27-1) * (1.5-1) * 3.5e9 / c
        self.assertAlmostEqual(pl.breakpoint_distance(3.5e9, 27.0, 1.5),
                               52.0 * 3.5e9 / 299_792_458.0, places=6)

    def test_uma_los_continuous_at_breakpoint(self):
        f, hb, hu = 7.2e9, 27.0, 1.5
        dbp = pl.breakpoint_distance(f, hb, hu)
        d3 = lambda d2: math.hypot(d2, hb - hu)
        below = pl.uma_los(dbp, d3(dbp), f, hb, hu)
        above = pl.uma_los(dbp + 1e-6, d3(dbp + 1e-6), f, hb, hu)
        self.assertAlmostEqual(below, above, delta=0.05)

    def test_uma_nlos_never_below_los(self):
        for d2d in (20, 50, 100, 300, 1000):
            d3d = math.hypot(d2d, 25.5)
            self.assertGreaterEqual(pl.uma_nlos(d2d, d3d, 3.5e9, 27, 1.5),
                                    pl.uma_los(d2d, d3d, 3.5e9, 27, 1.5))

    def test_frequency_gaps(self):
        # UMa: 20 log10 f in both LOS(PL1) and NLOS -> 6.265 dB from 3.5 to 7.2 GHz.
        # UMi NLOS uses 21.3 log10 f -> 6.672 dB.
        d2d = 150.0
        d3d = math.hypot(d2d, 25.5)
        gap = lambda fn: fn(d2d, d3d, 7.2e9, 27, 1.5) - fn(d2d, d3d, 3.5e9, 27, 1.5)
        self.assertAlmostEqual(gap(pl.uma_nlos), 20 * math.log10(7.2 / 3.5), places=9)
        d3s = math.hypot(d2d, 8.5)
        gap_umi = (pl.umi_nlos(d2d, d3s, 7.2e9, 10, 1.5) - pl.umi_nlos(d2d, d3s, 3.5e9, 10, 1.5))
        self.assertAlmostEqual(gap_umi, 21.3 * math.log10(7.2 / 3.5), places=9)

    def test_free_space_matches_e0b_measurement(self):
        # E0b: Sionna LOS gain at 9.0581 m was -62.476 dB (3.5 GHz).
        d = math.dist((0, 0, 10), (2.09425, 2.35166, 1.5))
        self.assertAlmostEqual(-pl.free_space_loss(d, 3.5e9), -62.476, delta=0.001)

    def test_uma_los_probability_hand_values(self):
        self.assertEqual(pl.uma_los_probability(10.0, 1.5), 1.0)
        # d = 100 m, hUT = 1.5 m: 0.18 + exp(-100/63) * 0.82
        self.assertAlmostEqual(pl.uma_los_probability(100.0, 1.5),
                               0.18 + math.exp(-100 / 63) * 0.82, places=12)

    def test_rejects_out_of_scope_inputs(self):
        with self.assertRaises(ValueError):
            pl.uma_los(100, 101, 3.5e9, 27, 14.0)   # hE would be random
        with self.assertRaises(ValueError):
            pl.uma_los(100, 101, 120e9, 27, 1.5)    # outside 0.5-100 GHz
        with self.assertRaises(ValueError):
            pl.uma_los(100, 99, 3.5e9, 27, 1.5)     # d3D < d2D


if __name__ == '__main__':
    unittest.main()
