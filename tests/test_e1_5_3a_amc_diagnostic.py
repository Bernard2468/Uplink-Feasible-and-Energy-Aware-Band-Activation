"""E1.5.3a analysis helpers: AMC rule, Poisson-binomial tail, point classes, curve checks, arguments."""
import json
import math
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import e1_5_3a_amc_diagnostic as a  # noqa: E402


class RuleTests(unittest.TestCase):

    def test_first_fail_minus_one(self):
        self.assertEqual(a.rule_first_fail({0: 0.0, 1: 0.05, 2: 0.2, 3: 0.0}, 3), 1)   # stops at first failure
        self.assertEqual(a.rule_first_fail({0: 0.5, 1: 0.0}, 1), 0)                   # MCS 0 fails -> 0
        self.assertEqual(a.rule_first_fail({0: 0.0, 1: 0.1, 2: 0.0}, 2), 2)           # none > 0.1 -> max
        self.assertEqual(a.rule_first_fail({0: 0.0, 1: 0.1000001}, 1), 0)


class PoissonBinomialTests(unittest.TestCase):

    def test_equals_binomial_for_equal_p(self):
        n, p = 40, 0.13
        for k in (0, 1, 5, 12, 40):
            ref = sum(math.comb(n, j) * p ** j * (1 - p) ** (n - j) for j in range(k, n + 1))
            self.assertAlmostEqual(a.poisson_binomial_sf([p] * n, k), ref, places=12)

    def test_mixed(self):
        # two TBs: P(K >= 1) = 1 - (1 - .2)(1 - .5)
        self.assertAlmostEqual(a.poisson_binomial_sf([0.2, 0.5], 1), 0.6, places=15)
        self.assertAlmostEqual(a.poisson_binomial_sf([0.2, 0.5], 2), 0.1, places=15)


class ClassifyTests(unittest.TestCase):

    def test_classes(self):
        self.assertEqual(a.classify_point([0.01] * 10, [0.85] * 10, 8), 'MISMATCH')
        self.assertEqual(a.classify_point([0.01] * 300, [0.09] * 300, 33), 'SAMPLING')
        self.assertEqual(a.classify_point([0.01] * 300, [0.0] * 300, 33), 'UNEXPLAINED')     # impossible count
        self.assertEqual(a.classify_point([0.2] * 10, [0.85] * 10, 8), 'UNEXPLAINED')        # AMC knew


class CurveTests(unittest.TestCase):

    def test_non_monotone_and_pmono(self):
        c = [(0, 1.0), (1, 2.0), (2, 1.9), (3, 3.0), (4, 2.0)]
        self.assertEqual([p[0] for p in a.non_monotone_points(c)], [2, 4])
        self.assertEqual([v[2] for v in a.pmono_violations(c)], [4])        # 2.0 -> 1.9 is a 5% drop

    def test_f3_pointwise(self):
        c = [(0.0, 1.0), (5.0, 3.0), (10.0, 2.0), (15.0, 4.0)]
        bad = a.f3_pointwise(c, shift_db=5.0)
        self.assertEqual([b[0] for b in bad], [5.0])                         # SE(10) = 2 < SE(5) = 3
        self.assertEqual(a.f3_pointwise([(0.0, 1.0), (1.0, 2.0)], 5.0), [])  # clamped at the top end

    def test_first_tx_se(self):
        se = a.first_tx_se(6007, 0.0, 51, 12)
        self.assertAlmostEqual(se, 8 * 6007 / (51 * 12 * 30e3 * 12 * 0.5e-3 / 14), places=12)
        self.assertAlmostEqual(a.first_tx_se(6007, 0.25, 51, 12), 0.75 * se, places=12)

    def test_window_first_tx(self):
        rows = [{'time_ns': '199999999', 'rv': '0'}, {'time_ns': '200000000', 'rv': '0'},
                {'time_ns': '300000000', 'rv': '1'}, {'time_ns': '600000000', 'rv': '0'}]
        self.assertEqual(len(a.window_first_tx(rows, [0.2, 0.6])), 1)


class ConfigTests(unittest.TestCase):

    def test_args_reproduce_e153_commands(self):
        c151 = json.loads(a.E151_CONFIG.read_text())
        c153 = json.loads(a.E153_CONFIG.read_text())
        man = json.loads((a.E153_RESULTS / 'manifest.json').read_text())
        runs = {r['tag']: r for r in man['runs']}
        for tag in a.R4B_TAGS:
            cmd = runs[tag]['cmd']
            rd = Path(cmd[3].split('=', 1)[1])
            em = next(e['type'] for e in c153['setup']['error_models'] if e['tag'] == tag.split('_')[0])
            mine = ' '.join([a.e151.PROGRAM] + a.e153_args(c151['common'], c153['setup'], em, rd))
            self.assertEqual(mine, cmd[4])

    def test_config(self):
        conf = json.loads(a.CONFIG.read_text())
        for k in ('A0_tb_size', 'A1_mcs', 'A2_tbler', 'A3_classification', 'A4_rule'):
            self.assertIn(k, conf['checks_A'])
        for k in ('B0_mcs_consistency', 'R4a_link_mode_unchanged', 'R4b_e153_reproduced', 'E0_error_model',
                  'V_snr_identity', 'F1_fixed_mcs', 'C_coverage'):
            self.assertIn(k, conf['checks_B'])
        self.assertEqual(conf['inputs']['window_s'],
                         json.loads(a.E153_CONFIG.read_text())['setup']['window_s'])
        self.assertAlmostEqual(a.F3_SHIFT_DB, 10.0860, places=3)


if __name__ == '__main__':
    unittest.main()
