"""The E1.5.1 comparator must accept exact values and reject small errors."""
import csv
import json
import math
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import e1_5_1_deterministic as e151  # noqa: E402

CONF = {'common': {'bandwidth_hz': 20e6, 'scs_hz': 30e3, 'gnb_tx_power_dbm': 44.0, 'ue_tx_power_dbm': 23.0,
                   'nf_gnb_db': 5.0, 'nf_ue_db': 7.0},
        'acceptance': {'tolerance_db': 0.01}}
RUN = {'tag': 't', 'frequency_hz': 3.5e9}
LOSSES = {'ue00': 100.0, 'ue01': 120.0, 'sweep_160dB': 160.0}


def write(path, header, rows):
    with open(path, 'w', newline='', encoding='utf-8') as fh:
        w = csv.writer(fh, lineterminator='\n')
        w.writerow(header)
        w.writerows(rows)


def make_run(d, dl_offset_db=0.0, rb_num=51):
    names = list(LOSSES)
    write(d / 'e15_run_info.csv', ['key', 'value'],
          [['gnb_rb_num', rb_num], ['ue_rb_num', rb_num], ['gnb_tx_power_dbm', 44.0], ['ue_tx_power_dbm', 23.0],
           ['gnb_noise_figure_db', 5.0], ['ue_noise_figure_db', 7.0]])
    write(d / 'e15_rnti_map.csv', ['ue_index', 'imsi', 'rnti'], [[i, i + 1, i + 10] for i in range(len(names))])
    write(d / 'e15_rb_after_attach.csv', ['ue_index', 'imsi', 'rnti', 'ue_rb_num'],
          [[i, i + 1, i + 10, rb_num] for i in range(len(names))])
    # UE 0 first holds RNTI 99 and is re-established with RNTI 10 at t = 5 ns; RNTI 99
    # is then reused by UE 2 at t = 7 ns (attribution must follow time).
    write(d / 'e15_rrc_events.csv', ['time_ns', 'imsi', 'cell_id', 'rnti'],
          [[0, 1, 1, 99], [5, 1, 1, 10], [0, 2, 1, 11], [0, 3, 1, 12], [7, 3, 1, 99]])
    hdr = ['ue_index', 'rnti', 'time_ns', 'sinr_lin', 'sinr_min_lin', 'rb_assigned', 'mcs', 'tb_size', 'rank',
           'rv', 'num_sym', 'corrupt', 'tbler']
    dl, ul, rp = [], [], []
    for i, n in enumerate(names):
        loss = LOSSES[n]
        snr_dl = 44 - loss - (-174 + 10 * math.log10(51 * 360e3) + 7) + dl_offset_db
        for k in range(12):
            dl.append([i, i + 10, k, 10 ** (snr_dl / 10), 0, 51, 5, 100, 1, 0, 12, 0, 0.0])
            nrb = 1 + (k % 51)
            snr_ul = 23 - loss - (-174 + 10 * math.log10(nrb * 360e3) + 5)
            ul.append([-1, i + 10, 10 + k, 10 ** (snr_ul / 10), 0, nrb, 3, 100, 1, 0, 12, 0, 0.0])
        pw = 44 - 10 * math.log10(51) - loss
        rp.append([i, 100, pw, pw])
    # RNTI-reuse case: at t = 8 ns RNTI 99 belongs to UE 2 (sweep_160dB), not UE 0.
    snr99 = 23 - LOSSES['sweep_160dB'] - (-174 + 10 * math.log10(1 * 360e3) + 5)
    ul.append([-1, 99, 8, 10 ** (snr99 / 10), 0, 1, 0, 10, 1, 0, 12, 1, 1.0])
    write(d / 'e15_dl_tb.csv', hdr, dl)
    write(d / 'e15_ul_tb.csv', hdr, ul)
    write(d / 'e15_dl_rxpower.csv', ['ue_index', 'n_rb_samples', 'min_rb_power_dbm', 'max_rb_power_dbm'], rp)
    return {'dir': d, 'names': names}


class ComparatorTests(unittest.TestCase):

    def test_exact_values_pass(self):
        with tempfile.TemporaryDirectory() as t:
            v, per_ue, _ = e151.analyse(CONF, RUN, make_run(Path(t)), LOSSES)
            for k in ('V0_readback', 'V1_dl_snr', 'V2_ul_snr', 'V3_rb_count', 'V4_dl_rx_power', 'V5_coverage'):
                self.assertTrue(v[k]['passed'], k)
            self.assertLess(v['V1_dl_snr']['max_abs_error_db'], 1e-9)
            self.assertLess(v['V2_ul_snr']['max_abs_error_db'], 1e-9)

    def test_rnti_attribution_follows_time_and_results_serialise(self):
        with tempfile.TemporaryDirectory() as t:
            v, per_ue, _ = e151.analyse(CONF, RUN, make_run(Path(t)), LOSSES)
            self.assertEqual(per_ue[2]['ul_tb'], 13)        # 12 regular + the reused-RNTI TB
            self.assertEqual(v['V2_ul_snr']['unmapped_rnti_tbs'], 0)
            self.assertTrue(v['V2_ul_snr']['passed'])
            json.dumps({'v': v, 'per_ue': per_ue}, default=e151.json_default)   # run 1 crashed here

    def test_small_dl_error_fails(self):
        with tempfile.TemporaryDirectory() as t:
            v, _, _ = e151.analyse(CONF, RUN, make_run(Path(t), dl_offset_db=0.02), LOSSES)
            self.assertFalse(v['V1_dl_snr']['passed'])
            self.assertAlmostEqual(v['V1_dl_snr']['max_abs_error_db'], 0.02, places=9)

    def test_wrong_rb_count_fails(self):
        with tempfile.TemporaryDirectory() as t:
            v, _, _ = e151.analyse(CONF, RUN, make_run(Path(t), rb_num=53), LOSSES)
            self.assertFalse(v['V3_rb_count']['passed'])


if __name__ == '__main__':
    unittest.main()
