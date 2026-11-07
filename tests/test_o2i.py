import math
import unittest

import numpy as np

from cmwave.radio import o2i


class O2ITests(unittest.TestCase):

    def test_material_losses(self):
        # TR 38.901 Table 7.4.3-1, f in GHz.
        self.assertAlmostEqual(o2i.material_loss_db('standard_glass', 3.5e9), 2.7, places=12)
        self.assertAlmostEqual(o2i.material_loss_db('irr_glass', 7.2e9), 25.16, places=12)
        self.assertAlmostEqual(o2i.material_loss_db('concrete', 3.5e9), 19.0, places=12)
        self.assertAlmostEqual(o2i.material_loss_db('concrete', 7.2e9), 33.8, places=12)
        self.assertAlmostEqual(o2i.material_loss_db('wood', 3.5e9), 5.27, places=12)

    def test_wall_loss_values(self):
        # Hand values from Table 7.4.3-2 (also written into the pre-registration).
        self.assertAlmostEqual(o2i.wall_loss_db('low', 3.5e9), 12.697503, places=6)
        self.assertAlmostEqual(o2i.wall_loss_db('low', 7.2e9), 13.659470, places=6)
        self.assertAlmostEqual(o2i.wall_loss_db('high', 3.5e9), 26.849786, places=6)
        self.assertAlmostEqual(o2i.wall_loss_db('high', 7.2e9), 31.461631, places=6)
        f = 7.2
        want = 5 - 10 * math.log10(0.7 * 10 ** (-(23 + 0.3 * f) / 10) + 0.3 * 10 ** (-(5 + 4 * f) / 10))
        self.assertAlmostEqual(o2i.wall_loss_db('high', 7.2e9), want, places=12)

    def test_wall_loss_grows_with_frequency(self):
        for m in ('low', 'high'):
            self.assertGreater(o2i.wall_loss_db(m, 7.2e9), o2i.wall_loss_db(m, 3.5e9))
        # Why the legacy Table 7.4.3-3 model (20 dB below 6 GHz) is not used in a
        # two-carrier study: it would exceed the low-loss value at 7.2 GHz.
        self.assertGreater(20.0, o2i.wall_loss_db('low', 7.2e9))

    def test_indoor_distance(self):
        self.assertAlmostEqual(float(o2i.indoor_distance_m(0.2, 0.6)), 5.0, places=12)
        rng = np.random.default_rng(3)
        d = o2i.indoor_distance_m(rng.random(200_000), rng.random(200_000))
        self.assertTrue(np.all((d >= 0) & (d <= 25)))
        self.assertAlmostEqual(float(d.mean()), 25 / 3, delta=0.05)
        self.assertAlmostEqual(float(np.median(d)), 25 * (1 - 1 / math.sqrt(2)), delta=0.05)
        self.assertAlmostEqual(float(o2i.indoor_loss_db(10.0)), 5.0, places=12)
        with self.assertRaises(ValueError):
            o2i.indoor_distance_m(1.2, 0.5)

    def test_tr38901_v19_irr_glass(self):
        # V19.5.0 Table 7.4.3-1: L_IRR = 25.4 + 0.11 f (Release 19, 7-24 GHz study); rest unchanged.
        self.assertAlmostEqual(o2i.material_loss_db('irr_glass', 7.2e9, '19.5.0'), 25.4 + 0.792, places=12)
        for f in (3.5e9, 7.2e9):
            self.assertEqual(o2i.wall_loss_db('low', f, '19.5.0'), o2i.wall_loss_db('low', f))
            fg = f / 1e9
            want = 5 - 10 * math.log10(0.7 * 10 ** (-(25.4 + 0.11 * fg) / 10)
                                       + 0.3 * 10 ** (-(5 + 4 * fg) / 10))
            self.assertAlmostEqual(o2i.wall_loss_db('high', f, '19.5.0'), want, places=12)
        self.assertAlmostEqual(o2i.wall_loss_db('high', 3.5e9, '19.5.0'), 27.50, delta=0.01)
        self.assertAlmostEqual(o2i.wall_loss_db('high', 7.2e9, '19.5.0'), 32.43, delta=0.01)
        self.assertEqual(o2i.wall_loss_db('high', 3.5e9), o2i.wall_loss_db('high', 3.5e9, '16.1.0'))

    def test_constants(self):
        self.assertEqual(o2i.sigma_p_db('low'), 4.4)
        self.assertEqual(o2i.sigma_p_db('high'), 6.5)
        self.assertEqual((o2i.CAR_MU_DB, o2i.CAR_SIGMA_DB), (9.0, 5.0))
        self.assertEqual(o2i.O2I_SF_STD_DB, 7.0)
        with self.assertRaises(ValueError):
            o2i.wall_loss_db('low', 0.1e9)


if __name__ == '__main__':
    unittest.main()
