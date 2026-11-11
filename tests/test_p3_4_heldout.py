"""P3.4 held-out evaluation on toy tables: the decision/evaluation split reduces to P3.3's simulator, the budget-matched
oracle (Lagrangian bracket, budget, zero-budget limit), the paired bootstrap intervals."""
import sys
import unittest
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'scripts'))
sys.path.insert(0, str(ROOT / 'tests'))
import p3_4_heldout as e  # noqa: E402
from test_p3_3_controllers import toy  # noqa: E402

P = e.P


class Split(unittest.TestCase):

    def test_split_equals_p33_simulate(self):
        tab, en = toy(G=8, R=3, seed=4)
        md = P.Model(tab, 1, 0, en)
        xi = np.random.default_rng(1).integers(0, 8, (5, 30))
        for ctrl_f in (lambda: P.Fixed(P.FULL), lambda: P.Threshold(0.1, 2, 'both'), lambda: P.Oracle('all_user'),
                       lambda: P.MPC(0.9, 3, np.ones((2, 144)), [0.1] * P.H_MAX)):
            a = P.simulate(ctrl_f(), xi, md, per_real=True)
            b = e.simulate_split(ctrl_f(), xi, md, md, per_real=True)
            self.assertTrue(np.array_equal(a['E_J'], b['E_J']))
            self.assertTrue(np.array_equal(a['k'], b['k']))
            for c in P.CRITS:
                self.assertTrue(np.array_equal(a['viol_s'][c], b['viol_s'][c]))

    def test_split_scores_on_the_evaluation_model(self):
        tab, en = toy(G=8, R=3, seed=4)
        tab2, en2 = toy(G=8, R=3, seed=5)
        dec, ev = P.Model(tab, 0, 0, en), P.Model(tab2, 0, 0, en2)
        xi = np.random.default_rng(2).integers(0, 8, (3, 20))
        o = e.simulate_split(P.Oracle('all_user'), xi, dec, ev)
        ref = P.simulate(Replay(o['k']), xi, ev)          # the same decisions scored on ev
        self.assertTrue(np.allclose(o['E_J'], ref['E_J'], rtol=1e-14, atol=0))
        for c in P.CRITS:
            self.assertTrue(np.array_equal(o['viol_s'][c], ref['viol_s'][c]))


class Replay:
    is_oracle = False

    def __init__(self, ks):
        self.ks = np.asarray(ks, dtype=np.int64)

    def reset(self, n, model, full):
        pass

    def decide(self, t, past):
        return self.ks[:, t]


class BudgetOracle(unittest.TestCase):

    def test_bracket_and_budget(self):
        rs = np.random.default_rng(7)
        for seed in range(6):
            tab, en = toy(G=6, R=2, seed=seed)
            md = P.Model(tab, 0, 0, en)
            xi = rs.integers(0, 6, (4, 40))
            ob = e.OracleBudget(share=0.05, iters=30)
            ob.reset(4, md, xi)
            budget = 0.05 * 40 * P.SLOT_S
            o = P.simulate(Replay(ob.plan), xi, md, per_real=False)
            for j in range(4):
                if not ob.budget_infeasible[j]:
                    self.assertLessEqual(float(o['viol_s']['all_user'][j]), budget + 1e-6)
                    self.assertLessEqual(ob.lb_energy[j], ob.ub_energy[j] * (1 + 1e-9) + 1e-6)
                    self.assertLessEqual(abs(float(o['E_J'][j]) - ob.ub_energy[j]), 1e-6 * ob.ub_energy[j])
            zo = P.Oracle('all_user')
            zo.reset(4, md, xi)
            oz = P.simulate(Replay(zo.plan), xi, md, per_real=False)
            for j in range(4):           # a positive budget can only lower the energy
                if not ob.budget_infeasible[j] and float(oz['viol_s']['all_user'][j]) == 0.0:
                    self.assertLessEqual(ob.ub_energy[j], float(oz['E_J'][j]) * (1 + 1e-12))


class Statistics(unittest.TestCase):

    def test_pigeonhole(self):
        rs = np.random.default_rng(3)
        num = rs.uniform(90, 100, (25, 100))
        den = rs.uniform(100, 110, (25, 100))
        r = e.pigeonhole_ratio(num, den, np.random.default_rng(1), reps=500)
        self.assertAlmostEqual(r['point'], 1 - num.mean() / den.mean(), places=14)
        self.assertLess(r['lo'], r['point'])
        self.assertGreater(r['hi'], r['point'])
        same = e.pigeonhole_ratio(den, den, np.random.default_rng(1), reps=200)
        self.assertEqual((same['point'], same['lo'], same['hi']), (0.0, 0.0, 0.0))
        g = e.pigeonhole_gap(num, num, den, np.random.default_rng(1), reps=200)
        self.assertEqual((g['point'], g['lo'], g['hi']), (0.0, 0.0, 0.0))
        d = e.square_bootstrap_diff(np.full(25, 60.0), np.full(25, 0.0), np.full(25, 6000.0), np.random.default_rng(1), reps=200)
        self.assertAlmostEqual(d['point'], 0.01, places=14)
        self.assertAlmostEqual(d['lo'], 0.01, places=14)

    def test_constants(self):
        self.assertEqual(len(e.SCENARIOS), 14)
        self.assertTrue(all(s['dp'] == -3.0 for s in e.SCENARIOS))
        self.assertEqual(len(e.H1_CELLS), 8)
        self.assertEqual(len(e.H2_CELLS), 9)
        self.assertEqual((e.R_START, e.R_STOP), (100, 200))


if __name__ == '__main__':
    unittest.main()
