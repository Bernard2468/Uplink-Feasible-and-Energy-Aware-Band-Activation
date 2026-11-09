"""E1.5.5 helpers: mappings, gain model, E1.5.4-derived inputs, link-budget default path."""
import json
import sys
import unittest
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'scripts'))
import e1_5_5_reclassification as m  # noqa: E402
from cmwave.radio import antenna as ant  # noqa: E402
from cmwave.radio import link_budget as lb  # noqa: E402


def load(d):
    return json.loads((d / 'summary.json').read_text())


class MappingTests(unittest.TestCase):

    def setUp(self):
        self.curves = {'DL': [(-3.0, 0.0), (-1.0, 0.2), (1.0, 0.6), (3.0, 1.0)],
                       'UL': [(-2.0, 0.0), (0.0, 0.5), (2.0, 0.9)]}
        self.smin = {'DL': -1.0, 'UL': 0.0}

    def test_curve_fn(self):
        fn = m.curve_fn(self.curves, self.smin)
        self.assertEqual(float(fn(np.array([1.0]), 'DL')[0]), 0.6)
        self.assertAlmostEqual(float(fn(np.array([0.0]), 'DL')[0]), 0.4)
        self.assertEqual(float(fn(np.array([-1.5]), 'DL')[0]), 0.0)          # below floor: 0, not interpolated
        self.assertEqual(float(fn(np.array([10.0]), 'DL')[0]), 1.0)          # above grid: last value
        self.assertEqual(fn(np.zeros((2, 3)), 'UL').shape, (2, 3))
        lo = m.curve_fn(self.curves, self.smin, low_tr=True)
        self.assertAlmostEqual(float(lo(np.array([-5.0]), 'UL')[0]), float(lb.mapped_se(-5.0, 'UL')), places=15)
        self.assertEqual(float(lo(np.array([-11.0]), 'UL')[0]), 0.0)

    def test_shift_scale(self):
        fn = m.curve_fn(self.curves, self.smin)
        sh = m.shifted(fn, {'DL': 1.0, 'UL': 0.0})
        self.assertEqual(float(sh(np.array([2.0]), 'DL')[0]), 0.6)
        sc = m.scaled(fn, {'DL': 0.5, 'UL': 1.0})
        self.assertEqual(float(sc(np.array([3.0]), 'DL')[0]), 0.5)

    def test_real_mappings(self):
        maps = m.build_mappings(load(m.E153A_DIR), load(m.E153_DIR))
        self.assertEqual(set(maps), {'tr36942', 'camc_t2', 'camc_t1', 'raw_t2', 'camc_t2_trlow'})
        fn, floors, _ = maps['camc_t2']
        self.assertAlmostEqual(floors['UL'], -0.639, places=2)
        self.assertAlmostEqual(floors['DL'], -1.639, places=2)
        self.assertAlmostEqual(float(fn(np.array([100.0]), 'DL')[0]), 6.316, places=2)
        self.assertEqual(maps['camc_t2_trlow'][1], m.TR_FLOOR)


class GainTests(unittest.TestCase):

    def setUp(self):
        self.t = m.gain_table(load(m.E1521_DIR))

    def test_table_and_lookup(self):
        self.assertEqual(len(self.t), 9)
        st = np.array(['o2o_los', 'o2i', 'o2o_nlos', 'o2i'])
        d = m.lookup(self.t, 'a', 3.5e9, st, 'D_cb_db')
        self.assertAlmostEqual(d[0], 0.5985, places=3)
        self.assertAlmostEqual(d[1], 4.7941, places=3)
        self.assertAlmostEqual(d[2], 5.0516, places=3)
        lo = m.lookup(self.t, 'b', 7.2e9, st, ('D_cb_ci95', 0))
        self.assertAlmostEqual(lo[1], 5.9624, places=3)

    def test_strata_array(self):
        los = np.array(['LOS', 'NLOS'])
        self.assertEqual(list(m.strata_of('in_car', los)), ['o2o_los', 'o2o_nlos'])
        self.assertEqual(list(m.strata_of('indoor_low_indep', los)), ['o2i', 'o2i'])
        self.assertEqual(m.array_of(ant.GnbPanel(rows=8, cols=16)), 'a')
        self.assertEqual(m.array_of(ant.GnbPanel(rows=16, cols=32)), 'b')
        self.assertIsNone(m.array_of(ant.GnbPanel(isotropic=True)))
        self.assertIsNone(m.array_of(ant.GnbPanel(rows=4, cols=4)))

    def test_gains_for(self):
        geo = {'los': np.array(['LOS', 'NLOS', 'NLOS'])}
        case_a = {'regime': 'A', 'nlos_bf_loss_db': 0.0}
        case_c = {'regime': 'C (sensitivity)', 'nlos_bf_loss_db': 4.5}
        pa = ant.GnbPanel(rows=8, cols=16)
        bf, d, ok = m.gains_for(case_a, pa, 7.2e9, 'outdoor', geo, {'gain': 'cb'}, self.t)
        self.assertTrue(ok)
        self.assertEqual(list(bf), [0.0, 0.0, 0.0])
        self.assertAlmostEqual(d[0], self.t[('a', 7.2e9, 'o2o_los')]['D_cb_db'], places=12)
        self.assertAlmostEqual(d[2], self.t[('a', 7.2e9, 'o2o_nlos')]['D_cb_db'], places=12)
        bf, d, _ = m.gains_for(case_c, pa, 3.5e9, 'indoor_low', geo, {'gain': 'cb'}, self.t)
        self.assertEqual(list(bf), [0.0, 4.5, 4.5])
        self.assertEqual(list(d), [0.0, 0.0, 0.0])
        bf, d, _ = m.gains_for(case_c, pa, 3.5e9, 'indoor_low', geo, {'gain': 'cb', 'c_rule': 'matched'}, self.t)
        self.assertEqual(list(bf), [0.0, 0.0, 0.0])
        self.assertAlmostEqual(d[0], self.t[('a', 3.5e9, 'o2i')]['D_cb_db'], places=12)
        bf, d, _ = m.gains_for({'regime': 'C', 'nlos_bf_loss_db': 0.0}, pa, 3.5e9, 'outdoor', geo,
                               {'gain': 'e14'}, self.t)
        self.assertEqual(list(bf) + list(d), [0.0] * 6)


class E154InputTests(unittest.TestCase):

    def test_selectivity_and_offsets(self):
        s = load(m.E154_DIR)
        sel = m.selectivity_factors(s)
        self.assertAlmostEqual(sel['UL']['factor'], 0.757, places=3)
        self.assertAlmostEqual(sel['DL']['factor'], 0.946, places=3)
        self.assertNotIn('b_f7p2', sel['DL']['medians'])             # one UE only: excluded
        f2, f3 = m.packet_offsets(s)
        self.assertAlmostEqual(f2['UL'], 0.166, places=2)
        self.assertAlmostEqual(f2['DL'], 0.550, places=2)
        self.assertAlmostEqual(f3['UL'], 0.241, places=2)
        self.assertAlmostEqual(f3['DL'], -0.636, places=2)


class LinkBudgetDefaultTests(unittest.TestCase):

    def test_default_path_unchanged(self):
        cfg = lb.CarrierConfig(frequency_hz=3.5e9, channel_bw_hz=20e6, scs_hz=30e3, n_prb=51, gnb_power_dbm=44.0,
                               ue_power_dbm=23.0, nf_gnb_db=5.0, nf_ue_db=7.0, gain_gnb_dbi=0.0, gain_ue_dbi=0.0)
        loss = np.linspace(80, 160, 401)
        for cap in (None, 5):
            a, b = lb.uplink(loss, cfg, n_max_prb=cap), lb.uplink(loss, cfg, n_max_prb=cap, se_fn=lb.mapped_se)
            for k in a:
                self.assertTrue(np.array_equal(a[k], b[k]), k)
        a, b = lb.downlink(loss, cfg), lb.downlink(loss, cfg, se_fn=lb.mapped_se)
        for k in a:
            self.assertTrue(np.array_equal(a[k], b[k]), k)


class LabelConfigTests(unittest.TestCase):

    def test_label_and_config(self):
        k = {n: np.array([i == j for j in range(5)]) for i, n in enumerate(m.e14.PARTITION)}
        self.assertEqual(list(m.label_of(k)), [0, 1, 2, 3, 4])
        conf = json.loads(m.CONFIG.read_text())
        self.assertEqual(set(conf['variants']), set(m.VARIANTS))
        for g in ('X0_inputs', 'X1_e14_reproduction', 'X2_e150_reproduction', 'X3_mapping', 'X4_gain',
                  'X5_partition', 'X6_dominance', 'X7_allocation_identity', 'X8_mixes'):
            self.assertIn(g, conf['gates'])
        self.assertTrue(set(m.X6_GATED) <= set(m.VARIANTS))
        self.assertNotIn('pkt', m.X6_GATED)


if __name__ == '__main__':
    unittest.main()
