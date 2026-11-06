import unittest
from itertools import product
from cmwave.control import Energy, Slot, capacities, evaluate, feasible, offline_optimum, transition_j, policies
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

    def test_input_validation(self):
        with self.assertRaises(ValueError):
            Energy(wake_delay_s=60)
        with self.assertRaises(ValueError):
            Slot(-1, 2, 3, 4, 5, 6)
        with self.assertRaises(ValueError):
            evaluate([self.s], [0, 1], self.e)


if __name__ == '__main__':
    unittest.main()
