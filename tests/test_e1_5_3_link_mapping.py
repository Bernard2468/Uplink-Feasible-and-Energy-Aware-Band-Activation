"""E1.5.3 analysis: SE per used resource, TR 36.942 fit, required SNR."""
import math
import sys
import unittest
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import e1_5_3_link_mapping as e  # noqa: E402
import e1_5_1_deterministic as e151  # noqa: E402


def tb(t_ns, size, rb=51, sym=12, corrupt='0', rv='0', mcs=10, sinr_db=10.0):
    return {'time_ns': str(int(t_ns)), 'tb_size': str(size), 'rb_assigned': str(rb), 'num_sym': str(sym),
            'corrupt': corrupt, 'rv': rv, 'mcs': str(mcs), 'sinr_lin': repr(10 ** (sinr_db / 10))}


class MetricTests(unittest.TestCase):

    def test_se_counts_decoded_bits_over_all_used_resources(self):
        tbs = [tb(0.3e9, 1000), tb(0.31e9, 1000, corrupt='1'), tb(0.32e9, 1000, rv='1'), tb(0.1e9, 9999)]
        m = e.run_metrics(tbs, [0.2, 0.6], lambda n: 10.0)
        res = 3 * 51 * 12 * e.SCS * 12 * e.T_SYM
        self.assertAlmostEqual(m['se_used'], 2 * 8000 / res, places=12)   # corrupt TB: resources, no bits
        self.assertEqual((m['tbs_window'], m['tbs_all']), (3, 4))
        self.assertAlmostEqual(m['bler_first_tx'], 0.5)
        self.assertAlmostEqual(m['retx_share'], 1 / 3)
        self.assertLess(m['max_abs_sinr_err_db'], 1e-12)
        m2 = e.run_metrics(tbs, [0.2, 0.6], lambda n: 10.02)
        self.assertAlmostEqual(m2['max_abs_sinr_err_db'], 0.02, places=9)


class FitTests(unittest.TestCase):

    def test_fit_recovers_parameters(self):
        snr = np.arange(-10, 36, 1.0)
        se = e.tr36942(snr, 0.7, 6.0, -8.0)
        pts = list(zip(snr, se))
        f = e.fit_curve(pts, 6.0)
        self.assertAlmostEqual(f['alpha'], 0.7, places=9)
        self.assertEqual(f['snr_min_db'], -8.0)
        self.assertLess(f['max_abs_err'], 1e-9)
        self.assertEqual(f['thr_max'], 6.0)

    def test_gap_form(self):
        snr = np.arange(-5, 30, 1.0)
        se = np.log2(1 + 10 ** ((snr - 3.0) / 10))
        f = e.fit_curve(list(zip(snr, se)), 100.0)
        self.assertAlmostEqual(f['gap_db'], 3.0, places=6)

    def test_required_snr(self):
        # TR 36.942 UL: 1.5 = 0.4 log2(1 + snr) -> 10.95 dB
        self.assertAlmostEqual(e.required_snr_tr(1.5, 0.4, 2.0, -10.0), 10 * math.log10(2 ** 3.75 - 1), places=12)
        self.assertIsNone(e.required_snr_tr(2.5, 0.4, 2.0, -10.0))
        pts = [(0.0, 0.5), (2.0, 1.0), (4.0, 2.0)]
        self.assertAlmostEqual(e.required_snr_empirical(pts, 1.5), 3.0)
        self.assertIsNone(e.required_snr_empirical(pts, 3.0))

    def test_config(self):
        conf = e151.json.loads((ROOT / 'configs/e1/e1_5_3_link_mapping.json').read_text())
        g = conf['setup']['loss_grid_db']
        self.assertEqual(len(np.arange(g['start'], g['stop'] + g['step'] / 2, g['step'])), 75)
        self.assertEqual([m['type'] for m in conf['setup']['error_models']], ['ns3::NrEesmIrT2', 'ns3::NrEesmIrT1'])
        for k in ('R3_link_mode_unchanged', 'E0_error_model', 'V_snr_identity', 'M_mcs_table', 'C_coverage'):
            self.assertIn(k, conf['checks'])


if __name__ == '__main__':
    unittest.main()
