import math
import unittest

import numpy as np

from cmwave.radio import link_budget as lb

CFG = lb.CarrierConfig(frequency_hz=3.5e9, channel_bw_hz=20e6, scs_hz=30e3, n_prb=51,
                       gnb_power_dbm=44.0, ue_power_dbm=23.0, nf_gnb_db=5.0, nf_ue_db=7.0,
                       gain_gnb_dbi=0.0, gain_ue_dbi=0.0)


class LinkBudgetTests(unittest.TestCase):

    def test_tr36942_table_a2_values(self):
        # Oracle: TR 36.942 V19.0.0 Table A.2 (printed to 2 decimals).
        table = {(-11, 'DL'): 0.0, (-10, 'DL'): 0.08, (0, 'DL'): 0.60, (10, 'DL'): 2.08,
                 (22, 'DL'): 4.39, (23, 'DL'): 4.40, (-11, 'UL'): 0.0, (-10, 'UL'): 0.06,
                 (0, 'UL'): 0.40, (10, 'UL'): 1.38, (14, 'UL'): 1.88, (15, 'UL'): 2.00}
        for (snr, d), want in table.items():
            self.assertAlmostEqual(float(lb.mapped_se(snr, d)), want, delta=0.005, msg=(snr, d))

    def test_mapping_properties(self):
        snr = np.linspace(-30, 40, 7001)
        for d in ('DL', 'UL'):
            se = lb.mapped_se(snr, d)
            self.assertTrue(np.all(np.diff(se) >= 0))
            self.assertTrue(np.all(se <= lb.shannon_se(snr) + 1e-12))
            self.assertTrue(np.all(se[snr < -10] == 0))
            self.assertLessEqual(se.max(), lb.TRUNCATED_SHANNON[d]['thr_max'])

    def test_occupied_bandwidth_and_noise(self):
        self.assertAlmostEqual(CFG.occupied_bw_hz, 51 * 12 * 30e3)            # 18.36 MHz
        self.assertAlmostEqual(float(lb.noise_dbm(CFG.occupied_bw_hz, 7.0)),
                               -174 + 10 * math.log10(18.36e6) + 7, places=12)

    def test_downlink_hand_value(self):
        # loss 120 dB: rx = 44 - 120 = -76 dBm; noise = -174 + 72.6386 + 7 = -94.3614 dBm.
        d = lb.downlink([120.0], CFG)
        noise = -174 + 10 * math.log10(18.36e6) + 7
        self.assertAlmostEqual(d['rx_power_dbm'][0], -76.0, places=12)
        self.assertAlmostEqual(d['snr_db'][0], -76.0 - noise, places=9)
        se = min(0.6 * math.log2(1 + 10 ** ((-76.0 - noise) / 10)), 4.4)
        self.assertAlmostEqual(d['rate_bps'][0], se * 18.36e6, delta=1e-3)

    def test_uplink_hand_value_and_allocation(self):
        # loss 120 dB: rx = 23 - 120 = -97 dBm. 1 PRB noise = -174 + 10log10(360e3) + 5.
        u = lb.uplink([120.0], CFG)
        n1 = -174 + 10 * math.log10(360e3) + 5
        self.assertAlmostEqual(u['snr_1prb_db'][0], -97.0 - n1, places=9)
        rates = [n * 360e3 * float(lb.mapped_se(-97.0 - (-174 + 10 * math.log10(n * 360e3) + 5), 'UL'))
                 for n in range(1, 52)]
        self.assertAlmostEqual(u['rate_bps'][0], max(rates), delta=1e-6)
        self.assertEqual(u['best_n_prb'][0], 1 + int(np.argmax(rates)))

    def test_uplink_narrowing_helps_at_cell_edge(self):
        # Full-band SNR below -10 dB but 1-PRB SNR above: the best allocation is narrower.
        loss = 23 - (-174 + 10 * math.log10(18.36e6) + 5) + 12.0   # full-band SNR = -12 dB
        u = lb.uplink([loss], CFG)
        self.assertEqual(u['rate_full_bps'][0], 0.0)
        self.assertGreater(u['rate_bps'][0], 0.0)
        self.assertLess(u['best_n_prb'][0], 51)

    def test_no_rate_reports_zero_prbs(self):
        u = lb.uplink([300.0], CFG)
        self.assertEqual(u['rate_bps'][0], 0.0)
        self.assertEqual(u['best_n_prb'][0], 0)

    def test_config_validation(self):
        with self.assertRaises(ValueError):
            lb.CarrierConfig(3.5e9, 20e6, 30e3, 60, 44, 23, 5, 7, 0, 0)   # 60 PRB > 20 MHz


if __name__ == '__main__':
    unittest.main()
