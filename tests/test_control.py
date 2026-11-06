import unittest
from itertools import product
from cmwave.control import Energy, Slot, capacities, evaluate, feasible, offline_optimum, transition_j, policies, threshold_online, hysteresis_online, threshold_current_slot_oracle
from cmwave.scenarios import create_synthetic_trace


class CoreTests(unittest.TestCase):
    def setUp(self):
        self.e = Energy(slot_s=60, base_w=100, fr3_active_w=60,
                        fr3_sleep_w=2, wake_j=200, sleep_j=0, wake_delay_s=6)
        self.s = Slot(ul_demand_mbps=5, dl_demand_mbps=10,
                      low_ul_capacity_mbps=10, low_dl_capacity_mbps=20,
                      fr3_ul_capacity_mbps=8, fr3_dl_capacity_mbps=30)

    def test_fri_capacity_wake_decrease(self):
        wake_ul, wake_dl = capacities(self.s, 0, 1, self.e)
        steady_ul, steady_dl = capacities(self.s, 1, 1, self.e)
        self.assertAlmostEqual(wake_ul, 17.2)
        self.assertAlmostEqual(wake_dl, 47.0)
        self.assertLess(wake_ul, steady_ul)

    def test_sleep_energy(self):
        self.assertEqual(transition_j(0, 0, self.e), 102 * 60)
        self.assertEqual(transition_j(0, 1, self.e), 160 * 60 + 200)
        self.assertEqual(transition_j(1, 1, self.e), 160 * 60)

    def test_single_slot_off_optimal(self):
        self.assertEqual(offline_optimum([self.s], self.e), [0])

    def test_uplink_guard_stops_false_feasibility(self):
        busy = Slot(15, 10, 10, 20, 8, 30)
        self.assertFalse(feasible(busy, 0, 0, self.e))
        self.assertTrue(feasible(busy, 0, 1, self.e))

    def test_impossible_case_returns_none(self):
        bad = Slot(50, 10, 10, 20, 8, 30)
        self.assertIsNone(offline_optimum([bad], self.e))

    def test_determinism(self):
        self.assertEqual(create_synthetic_trace(17), create_synthetic_trace(17))
        self.assertNotEqual(create_synthetic_trace(17), create_synthetic_trace(18))

    def test_dp_never_worse_than_other_feasible_policies(self):
        for seed in range(10):
            slots = create_synthetic_trace(seed)
            ps = policies(slots, self.e)
            opt = ps['offline_clairvoyant_bound']
            self.assertIsNotNone(opt)
            opt_eval = evaluate(slots, opt, self.e)
            self.assertTrue(opt_eval['feasible'])
            for name, schedule in ps.items():
                if name == 'offline_clairvoyant_bound':
                    continue
                result = evaluate(slots, schedule, self.e)
                if result['feasible']:
                    self.assertLessEqual(opt_eval['energy_j'], result['energy_j'] + 1e-6)

    def test_dp_matches_exhaustive_search_small_traces(self):
        # Independent 2^8 enumeration checks the DP implementation itself.
        for seed in range(4):
            slots = create_synthetic_trace(seed, nslots=8)
            schedules = list(product((0, 1), repeat=len(slots)))
            candidates = [evaluate(slots, list(schedule), self.e)['energy_j']
                          for schedule in schedules
                          if evaluate(slots, list(schedule), self.e)['feasible']]
            dp_schedule = offline_optimum(slots, self.e)
            if candidates:
                self.assertIsNotNone(dp_schedule)
                self.assertAlmostEqual(evaluate(slots, dp_schedule, self.e)['energy_j'], min(candidates))
            else:
                self.assertIsNone(dp_schedule)


    def test_online_policies_do_not_see_current_slot_realization(self):
        quiet = Slot(2, 3, 10, 20, 8, 30)
        busy = Slot(14, 40, 10, 20, 8, 30)
        # No observation before slot zero; cannot respond to current demand.
        for policy in (threshold_online, hysteresis_online):
            self.assertEqual(policy([quiet], self.e), [0])
            self.assertEqual(policy([busy], self.e), [0])
            # At t=1 the controllers can respond to slot 0, not slot 1.
            self.assertEqual(policy([quiet, busy], self.e)[1],
                             policy([quiet, quiet], self.e)[1])
            self.assertNotEqual(policy([busy, quiet], self.e)[1],
                                policy([quiet, quiet], self.e)[1])

    def test_current_slot_policy_is_honestly_labeled_oracle(self):
        busy = Slot(14, 40, 10, 20, 8, 30)
        self.assertEqual(threshold_current_slot_oracle([busy], self.e), [1])
        self.assertEqual(threshold_online([busy], self.e), [0])
        self.assertFalse(evaluate([busy], threshold_online([busy], self.e), self.e)['feasible'])
        self.assertIn('threshold_current_slot_oracle', policies([busy], self.e))

    def test_high_switch_cost_creates_nontrivial_multi_slot_tradeoff(self):
        # Illustrative synthetic cost only: NOT a calibrated 7 GHz radio model.
        slow = Energy(slot_s=60, base_w=110, fr3_active_w=75,
                      fr3_sleep_w=3, wake_j=5000, wake_delay_s=3)
        busy = Slot(14, 35, 10, 20, 8, 30)
        quiet = Slot(3, 5, 10, 20, 8, 30)
        trace = [busy, quiet, busy]
        oracle_greedy = threshold_current_slot_oracle(trace, slow)
        opt = offline_optimum(trace, slow)
        self.assertEqual(oracle_greedy, [1, 0, 1])
        self.assertEqual(opt, [1, 1, 1])
        self.assertLess(evaluate(trace, opt, slow)['energy_j'],
                        evaluate(trace, oracle_greedy, slow)['energy_j'])

    def test_online_outages_are_counted_not_silently_discarded(self):
        quiet = Slot(2, 3, 10, 20, 8, 30)
        busy = Slot(14, 40, 10, 20, 8, 30)
        trace = [quiet, busy, quiet]
        result = evaluate(trace, threshold_online(trace, self.e), self.e)
        self.assertFalse(result['feasible'])
        self.assertEqual(result['constraint_violations'], 1)
        self.assertAlmostEqual(result['violation_fraction'], 1/3)

    def test_input_validation(self):
        with self.assertRaises(ValueError):
            Energy(wake_delay_s=60)
        with self.assertRaises(ValueError):
            Slot(-1, 2, 3, 4, 5, 6)
        with self.assertRaises(ValueError):
            evaluate([self.s], [0, 1], self.e)


if __name__ == '__main__':
    unittest.main()
