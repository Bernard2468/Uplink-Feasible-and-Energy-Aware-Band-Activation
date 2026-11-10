"""P3.2c energy and service ledger on hand-checkable fixtures: SCell activation delay, schedules (coverage, sleep
periods, wake modes, bound), ledger against P3.1's sequence_energy, toy joules, power reproduction of P3.1, overhead
rules, all-user outage and its bisection on analytic toy groups, causality, intervals, config constants."""
import json
import math
import sys
import unittest
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'scripts'))
sys.path.insert(0, str(ROOT / 'tests'))
import p3_2c_energy_ledger as e  # noqa: E402
from test_p3_2b_traffic_resources import toy_group  # noqa: E402

EM = e.EM
CONF = json.loads(e.CONFIG.read_text(encoding='utf-8'))
CONF1 = json.loads(e.P31_CONFIG.read_text(encoding='utf-8'))
S1SUM = json.loads((e.P31_DIR / 'summary.json').read_text())


class Ctx_:
    req = {'DL': 1.0, 'UL': 1.0}


class Schedules(unittest.TestCase):

    def test_scell_delay_rule(self):
        self.assertEqual(e.t_scell_s(20.0), 0.105)
        self.assertEqual(e.t_scell_s(160.0), 0.805)
        self.assertEqual(CONF['wake']['scell_activation']['t_scell_s'], {'primary': 0.105, 'sensitivity': 0.805})

    def test_coverage_and_structure(self):
        for pol, p in e.POLICIES.items():
            for wake in e.WAKE:
                for t_tr in (0.05, 10.0):
                    for t_sc in (0.105, 0.805):
                        segs, dec, gaps = e.schedule(pol, wake, t_tr, t_sc)
                        self.assertEqual(segs[0][2], 0.0)
                        self.assertEqual(segs[-1][3], e.DAY_S)
                        for s0, s1 in zip(segs, segs[1:]):
                            self.assertEqual(s0[3], s1[2])
                        self.assertTrue(all(b > a for _, _, a, b in segs))
                        kinds = [s[0] for s in segs]
                        if not p['on_levels']:
                            self.assertEqual(set(kinds), {'hold'})
                            continue
                        if len(p['on_levels']) == 3:
                            self.assertEqual(set(kinds), {'on'})
                            continue
                        t_b = min(lv[1] for lv in e.LEVELS if lv[0] in p['on_levels'])
                        first_on = min(a for k, _, a, _ in segs if k == 'on')
                        sleep_end = max(b for k, _, _, b in segs if k == 'sleep')
                        if p['bound']:
                            self.assertNotIn('act', kinds)
                            self.assertEqual(first_on, t_b)
                            self.assertEqual(gaps, [])
                        elif wake == 'proactive':
                            self.assertAlmostEqual(first_on, t_b, places=12)
                            self.assertAlmostEqual(sleep_end, t_b - t_sc, places=9)
                            self.assertEqual(gaps, [])
                            req = [t for t, a in dec if a == 'wake_request'][0]
                            self.assertAlmostEqual(req, t_b - t_sc - t_tr, places=9)
                        else:
                            self.assertAlmostEqual(first_on, t_b + t_tr + t_sc, places=9)
                            self.assertAlmostEqual(gaps[0][2] - gaps[0][1], t_tr + t_sc, places=9)
                        # exactly one sleep period, starting at 00:00
                        starts = [i for i, k in enumerate(kinds) if k == 'sleep' and (i == 0 or kinds[i - 1] != 'sleep')]
                        self.assertEqual(starts, [0])

    def test_short_sleep_rejected(self):
        pols = {'t': {'on_levels': ['on'], 'ul': None, 'bound': False}}
        with self.assertRaises(ValueError):
            e.schedule('t', 'proactive', 0.05, 0.0, (('off', 0.0, 0.01), ('on', 0.01, 10.0)), pols)
        with self.assertRaises(ValueError):    # an on block that does not end the day is not a registered schedule
            e.schedule('t', 'proactive', 0.05, 0.0, (('on', 0.0, 5.0), ('off', 5.0, 10.0)), pols)

    def test_toy_fixtures(self):
        out = e.toy_fixtures()
        self.assertTrue(out['passed'], out)
        self.assertEqual(out['no_activation_j'], 374403.0)
        self.assertEqual(out['activation_1s_j'], 374451.0)

    def test_ledger_equals_sequence_energy(self):
        rng = np.random.default_rng(3)
        for pol in e.POLICIES:
            for wake in e.WAKE:
                segs, _, _ = e.schedule(pol, wake, 10.0, 0.805)
                p_lvl = {lv[0]: rng.uniform(100, 900, 6) for lv in e.LEVELS}
                st = {'p_w': 30.0, 't_s': 0.0 if e.POLICIES[pol]['bound'] else 10.0, 'e_add_j': 0.0 if e.POLICIES[pol]['bound'] else 500.0}
                p_act = np.full(6, 170.0)
                led, n_sl = e.ledger_72(segs, lambda lv: p_lvl[lv], p_act, st)
                led = np.full(6, float(led)) if np.ndim(led) == 0 else led
                ref = e.sequence_check(segs, lambda lv: float(np.mean(p_lvl[lv])), 170.0, st)
                self.assertLessEqual(EM.rel_diff(float(np.mean(led)), ref), 1e-12)
                self.assertEqual(n_sl, 1 if any(s[0] == 'sleep' for s in segs) else 0)

    def test_causality(self):
        out = e.causality_check()
        self.assertTrue(out['passed'], out)


class EnergyModels(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.en = e.Energy(CONF1)

    def test_reproduces_p31(self):
        out = e.power_reproduction(self.en, CONF1, S1SUM)
        self.assertTrue(out['passed'], out)
        self.assertGreater(out['compared'], 150)

    def test_s1_rule_by_hand(self):
        hand = 17.0 * (1024 * 0.1) ** 0.25 / (0.9 * 0.9) + 90.0 / (0.9 * 0.95)
        self.assertLess(abs(self.en.p_aux(e.HW_KEY[-3.0], 'S1') - hand), 1e-9)
        self.assertLess(abs(hand - 172.0), 0.05)
        hand53 = 17.0 * (1024 * 0.19484983544617956) ** 0.25 / 0.81 + 90.0 / 0.855
        self.assertLess(abs(self.en.p_aux(e.HW_KEY[0.0], 'S1') - hand53), 1e-6)

    def test_s2_parity_and_ladder_scaling(self):
        for hw in e.HW_KEY.values():
            self.assertLessEqual(EM.rel_diff(self.en.p_empty(hw) + self.en.p_aux(hw, 'S2'), self.en.p35('right', 0.0)), 1e-12)
            for cat in e.CATS:
                d0 = self.en.ladder(hw, 'S0', cat, 'set1')['deep']
                d2 = self.en.ladder(hw, 'S2', cat, 'set1')['deep']
                k = (self.en.p_mu(hw) + self.en.p_aux(hw, 'S2')) / self.en.p_mu(hw)
                self.assertLessEqual(EM.rel_diff(d2['p_w'], k * d0['p_w']), 1e-12)
                self.assertLessEqual(EM.rel_diff(d2['e_add_j'], k * d0['e_add_j']), 1e-12)
                self.assertEqual(d2['t_s'], d0['t_s'])

    def test_vectorized_powers(self):
        L = np.array([0.0, 0.05, 0.3, 1.0])
        p = self.en.p35('right', L)
        for i, x in enumerate(L):
            self.assertEqual(p[i], self.en.p35('right', float(x)))
        q = self.en.p72(e.HW_KEY[-3.0], L, L[::-1])
        for i in range(4):
            self.assertLessEqual(EM.rel_diff(q[i], self.en.p72(e.HW_KEY[-3.0], float(L[i]), float(L[::-1][i]))), 1e-15)


class AllUser(unittest.TestCase):
    """Analytic toy: constant per-user SE, cap 1, R = 1, req 1: L = T sum_adm w / SE; user i in outage iff SE_i (1 - L) < 1."""

    def toy(self, se_adm, w_adm, w_not):
        n = len(se_adm)
        se_map = {float(i): float(s) for i, s in enumerate(se_adm)}
        se = lambda x, dd: np.vectorize(lambda v: se_map[float(v)])(np.asarray(x))  # noqa: E731
        g = toy_group(np.arange(n, dtype=float), w_adm, np.zeros(n, dtype=int), 1, G=np.zeros((n, 3)), noise=1.0, cap=1.0, se=se)
        # SINR is s - 10 log10(noise) = s when noise = 1 and no interference: the SE map is keyed by s
        U = {'r': np.zeros(n + 1, dtype=np.int64), 'w': np.concatenate([w_adm, [w_not]]), 'R': 1}
        st = {'car': np.concatenate([np.zeros(n, dtype=int), [-1]]), 'groups': {0: g}, 'dir': 'DL'}
        return U, st

    def test_identity_and_bisection(self):
        se_adm = np.array([1.5, 4.0])
        w_adm = np.array([0.03, 0.93])
        U, st = self.toy(se_adm, w_adm, 0.04)
        for T in (0.5, 1.0, 1.4, 2.0, 3.0):
            o_all = e.outage_all_at(U, Ctx_, st, T)
            o_adm = e.B.outage_at(U, Ctx_, st, T)
            self.assertLess(abs(o_all - (0.04 + 0.96 * o_adm)), 1e-15)
        # user 0 fails at T > (1/3)/0.2525, user 1 at T > 0.75/0.2525 (L = 0.2525 T)
        x_all = e.bisect_all(U, Ctx_, st, 1.0, 8.0)
        x_adm = e.B.bisect_traffic(U, Ctx_, st, 1.0, hi=8.0)
        self.assertEqual(x_all['status'], 'bracketed')
        self.assertTrue(x_all['bracket'][0] <= (1.0 / 3.0) / 0.2525 <= x_all['bracket'][1])
        self.assertTrue(x_adm['bracket'][0] <= 0.75 / 0.2525 <= x_adm['bracket'][1])
        self.assertLess(x_all['x_star'], x_adm['x_star'])

    def test_coverage_floor_infeasible(self):
        U, st = self.toy(np.array([4.0]), np.array([0.8]), 0.2)
        self.assertEqual(e.bisect_all(U, Ctx_, st, 1.0, 8.0)['status'], 'qos_infeasible_unloaded')
        self.assertEqual(e.B.bisect_traffic(U, Ctx_, st, 1.0, hi=8.0)['status'], 'bracketed')


class Misc(unittest.TestCase):

    def test_ci95(self):
        v = np.array([1.0, 2.0, 3.0, 4.0])
        c = e.ci95(v)
        h = 3.182446305284263 * float(np.std(v, ddof=1)) / 2.0
        self.assertAlmostEqual(c['mean'], 2.5, places=15)
        self.assertAlmostEqual(c['hi'] - c['mean'], h, places=12)
        self.assertTrue(math.isnan(e.ci95([1.0])['lo']))

    def test_config_constants(self):
        self.assertEqual(CONF['policies'], e.POLICIES)
        self.assertEqual(tuple(CONF['day']['growth_grid']), e.GROWTH)
        self.assertEqual([(lv['name'], lv['start_s'], lv['end_s']) for lv in CONF['day']['levels']], list(e.LEVELS))
        self.assertEqual({float(k): v for k, v in CONF['hardware']['map_dl_offset_to_p3_1_point'].items()}, e.HW_KEY)
        self.assertEqual(sum(b - a for _, a, b in e.LEVELS), e.DAY_S)
        self.assertEqual([lv[2] - lv[1] for lv in e.LEVELS], [6 * 3600.0, 10 * 3600.0, 8 * 3600.0])


if __name__ == '__main__':
    unittest.main()
