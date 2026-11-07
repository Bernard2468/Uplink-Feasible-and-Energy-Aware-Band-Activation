"""E1.5.2-0 analysis: conventions (A0), normalisation (N0), roles (B0), NR identity (V0)."""
import csv
import math
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import e1_5_2_0_conventions as e  # noqa: E402
import e1_5_1d_tdma_diagnostic as diag  # noqa: E402
import e1_5_1_deterministic as e151  # noqa: E402

LINKS = [
    {'name': 'ueA', 'los': 1, 'bearing_deg': 30.0, 'loss_db': 100.0, 'py_zenith_deg': 100.0,
     'py_azimuth_deg': 179.5, 'py_gain_db': 25.0},
    {'name': 'ueB', 'los': 0, 'bearing_deg': 150.0, 'loss_db': 120.0, 'py_zenith_deg': 95.0,
     'py_azimuth_deg': -60.0, 'py_gain_db': 20.0},
]


def det_rows(dgain=0.0, daz=0.0):
    af = 10 * math.log10(128)
    return [{'link_index': '0', 'name': 'ueA', 'bearing_deg': '30', 'zenith_deg': '100.0',
             'azimuth_deg': str(179.5 + daz), 'elem_gain_db': str(25.0 - af + dgain),
             'af_s_db': str(af), 'af_u_db': '3.0', 'n_elems': '128'},
            {'link_index': '1', 'name': 'ueB', 'bearing_deg': '150', 'zenith_deg': '95.0',
             'azimuth_deg': '-60.0', 'elem_gain_db': str(20.0 - af), 'af_s_db': str(af), 'af_u_db': '1.0',
             'n_elems': '128'}]


class A0Tests(unittest.TestCase):

    def test_exact_values_pass(self):
        res, per = e.a0_check(det_rows(), LINKS, 128)
        self.assertTrue(res['passed'])
        self.assertAlmostEqual(per[0]['ns3_gain_db'], 25.0, places=9)

    def test_gain_error_fails(self):
        res, _ = e.a0_check(det_rows(dgain=0.02), LINKS, 128)
        self.assertFalse(res['passed'])
        self.assertAlmostEqual(res['max_abs_error']['gain_db'], 0.02, places=9)

    def test_azimuth_wrap_and_error(self):
        rows = det_rows()
        rows[0]['azimuth_deg'] = str(179.5 - 360.0)          # same direction, other branch
        self.assertTrue(e.a0_check(rows, LINKS, 128)[0]['passed'])
        res, _ = e.a0_check(det_rows(daz=1e-3), LINKS, 128)
        self.assertFalse(res['passed'])

    def test_mismatched_rows_abort(self):
        rows = det_rows()
        rows[1]['bearing_deg'] = '270'
        with self.assertRaises(SystemExit):
            e.a0_check(rows, LINKS, 128)


class RealisedTests(unittest.TestCase):

    def test_mean_linear_and_flags(self):
        real = [{'link_index': '0', 'copy': '0', 'gain_lin': '1.0', 'gnb_is_s': '1'},
                {'link_index': '0', 'copy': '1', 'gain_lin': '3.0', 'gnb_is_s': '1'},
                {'link_index': '1', 'copy': '0', 'gain_lin': '0.5', 'gnb_is_s': '1'}]
        g, samples, flags = e.realised(real, 2)
        self.assertAlmostEqual(g[0], 10 * math.log10(2.0), places=12)
        self.assertEqual(flags, {1})
        with self.assertRaises(SystemExit):
            e.realised(real[:2], 2)

    def test_n0_and_medians(self):
        samples = {0: [1.0, 1.1, 0.9], 1: [2.0, 2.0]}
        r = e.n0_check(samples, LINKS)
        self.assertTrue(r['LOS']['passed'])
        self.assertFalse(r['NLOS']['passed'])                 # 3 dB off
        d = e.deficits({0: 24.0, 1: 15.0}, LINKS)
        self.assertEqual(e.median_los(d, LINKS), (1.0, 1))


class V0Tests(unittest.TestCase):

    def test_identity_and_roles(self):
        cfg = diag.carrier_cfg(e151.json.loads((ROOT / 'configs/e1/e1_5_1_deterministic.json').read_text())['common'],
                               3.5e9)
        snr = e.lb.downlink(e.np.array([l['loss_db'] for l in LINKS]), cfg)['snr_db']
        with tempfile.TemporaryDirectory() as t:
            d = Path(t)
            gains = [200.0, 50.0]

            def write(name, header, rows):
                with open(d / name, 'w', newline='') as fh:
                    w = csv.writer(fh, lineterminator='\n')
                    w.writerow(header)
                    w.writerows(rows)
            write('e15_run_info.csv', ['key', 'value'], [['channel_model_frequency_hz_readback', '3500000000']])
            write('e152_nr_direct.csv', ['ue_index', 'name', 'gain_lin', 'gnb_is_s', 'gnb_elems'],
                  [[i, LINKS[i]['name'], gains[i], 1, 128] for i in range(2)])
            hdr = ['ue_index', 'rnti', 'time_ns', 'sinr_lin', 'sinr_min_lin', 'rb_assigned', 'mcs', 'tb_size', 'rank',
                   'rv', 'num_sym', 'corrupt', 'tbler']
            rows = [[i, i + 1, k, 10 ** (snr[i] / 10) * gains[i], 0, 51, 5, 100, 1, 0, 12, 0, 0]
                    for i in range(2) for k in range(12)]
            write('e15_dl_tb.csv', hdr, rows + rows[:3])
            res, g = e.v0_check(d, LINKS, cfg)
            self.assertTrue(res['passed'])
            self.assertEqual((res['tbs'], res['trace_rows'], res['min_tbs_per_ue']), (24, 27, 12))
            write('e152_nr_direct.csv', ['ue_index', 'name', 'gain_lin', 'gnb_is_s', 'gnb_elems'],
                  [[i, LINKS[i]['name'], gains[i] * 1.01, 1 - i, 128] for i in range(2)])
            res, _ = e.v0_check(d, LINKS, cfg)
            self.assertFalse(res['passed'])
            self.assertFalse(res['gnb_is_s_all'])
            self.assertAlmostEqual(res['max_abs_error_db'], 10 * math.log10(1.01), places=9)


class LinksTests(unittest.TestCase):

    def test_real_links(self):
        med, _ = e151.median_losses()
        gnb, links = e.build_links(7.2e9, 8, 16, med)
        self.assertEqual(gnb, [8.5, 21.0, 27.0])
        self.assertEqual(len(links), 27)
        self.assertTrue({l['bearing_deg'] for l in links} <= {30.0, 150.0, 270.0})
        self.assertTrue(all(l['loss_db'] == med[7.2e9][l['name']] for l in links))
        _, links_b = e.build_links(7.2e9, 16, 32, med)
        for a, b in zip(links, links_b):                    # same sector, gain + 10 log10(4)
            self.assertEqual(a['bearing_deg'], b['bearing_deg'])
            self.assertAlmostEqual(b['py_gain_db'] - a['py_gain_db'], 10 * math.log10(4), places=9)


if __name__ == '__main__':
    unittest.main()
