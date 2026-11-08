"""E1.5.4-0 analysis: TB-DCI join, own-beam identity and hypothesis H1 on synthetic files."""
import csv
import math
import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import e1_5_4_0_ofdma_diagnostic as e  # noqa: E402
import e1_5_1d_tdma_diagnostic as diag  # noqa: E402
import e1_5_1_deterministic as e151  # noqa: E402

C = e151.json.loads((ROOT / 'configs/e1/e1_5_1_deterministic.json').read_text())['common']
CFG = diag.carrier_cfg(C, 7.2e9)
LINKS = [{'name': 'u0', 'loss_db': 100.0}, {'name': 'u1', 'loss_db': 110.0}]
SNR = 10 ** (e.lb.downlink(np.array([100.0, 110.0]), CFG)['snr_db'] / 10)


def write(path, header, rows):
    with open(path, 'w', newline='') as fh:
        w = csv.writer(fh, lineterminator='\n')
        w.writerow(header)
        w.writerows(rows)


def gains():
    rng = np.random.default_rng(7)
    return rng.uniform(1.0, 100.0, size=(2, 2, e.N_RB))


def make(d, g, *, beam_of_group=0, first_seq_ue=0, mac_first_ue=None, cosched=True, bad_rb=False):
    """Slots 0..11: if cosched, UE0 gets RBs 0-24 and UE1 RBs 25-50 in the same symbols,
    SINR generated with the beam of UE `beam_of_group`; otherwise one UE per slot, own beam.
    DCI send order starts with `first_seq_ue`, MAC allocation order with `mac_first_ue`."""
    mac_first_ue = first_seq_ue if mac_first_ue is None else mac_first_ue
    dci, tb, sched = [], [], []
    m0 = '1' * 25 + '0' * 26
    m1 = '0' * 25 + '1' * 26
    full = '1' * 51
    for s in range(12):
        if cosched:
            order = [(0, m0), (1, m1)] if first_seq_ue == 0 else [(1, m1), (0, m0)]
            for seq, u in enumerate([mac_first_ue, 1 - mac_first_ue]):
                sched.append([s * 500000 - 1000000, 0, 0, s, seq, u + 1, 1, 13, 10, 1000, 0])
            for seq, (u, m) in enumerate(order):
                dci.append([s * 500000, 0, 0, s, seq, u + 1, 1, 13, 10, 0, m])
                mask = np.array([c == '1' for c in m])
                sinr = SNR[u] * np.mean(g[u, beam_of_group, mask])
                rb = int(mask.sum()) + (1 if bad_rb and u == 1 else 0)
                tb.append([u, u + 1, s * 500000 + 400000, 0, 0, s, 1, 13, repr(float(sinr)), rb, 10, 0, 0])
        else:
            u = s % 2
            sched.append([s * 500000 - 1000000, 0, 0, s, 0, u + 1, 1, 13, 10, 1000, 0])
            dci.append([s * 500000, 0, 0, s, 0, u + 1, 1, 13, 10, 0, full])
            sinr = SNR[u] * np.mean(g[u, u, :])
            tb.append([u, u + 1, s * 500000 + 400000, 0, 0, s, 1, 13, repr(float(sinr)), 51, 10, 0, 0])
    tb = tb + tb[:2]                                        # duplicate packet rows (erratum 1)
    write(d / 'e15od_dci.csv', ['time_ns', 'frame', 'subframe', 'slot', 'seq_in_slot', 'rnti', 'sym_start',
                                'num_sym', 'mcs', 'rv', 'rbg_bitmask'], dci)
    write(d / 'e15od_dl_sched.csv', ['time_ns', 'frame', 'subframe', 'slot', 'seq_in_slot', 'rnti', 'sym_start',
                                     'num_sym', 'mcs', 'tb_size', 'rv'], sched)
    write(d / 'e15od_dl_tb.csv', ['ue_index', 'rnti', 'time_ns', 'frame', 'subframe', 'slot', 'sym_start',
                                  'num_sym', 'sinr_lin', 'rb_assigned', 'mcs', 'rv', 'corrupt'], tb)
    write(d / 'e15od_rb_gain.csv', ['ue_index', 'beam_ue_index', 'rb', 'gain_lin'],
          [[j, b, k, repr(float(g[j, b, k]))] for j in range(2) for b in range(2) for k in range(e.N_RB)])


class EvaluateTests(unittest.TestCase):

    def test_first_ue_beam_explains_group(self):
        g = gains()
        with tempfile.TemporaryDirectory() as t:
            d = Path(t)
            make(d, g, beam_of_group=0, first_seq_ue=0)
            st = e.evaluate(d, LINKS, CFG)
            self.assertTrue(e.join_ok(st))
            self.assertEqual((st['tbs'], st['trace_rows']), (24, 26))
            self.assertEqual(st['groups_two_or_more_ues'], 12)
            self.assertTrue(e.h1_ok(st), st)
            self.assertEqual(st['own_ok_cosched'], 12)           # UE0 on its own beam
            self.assertEqual(st['own_fail_cosched'], 12)         # UE1 on UE0's beam
            self.assertFalse(e.own_all_ok(st))

    def test_h1b_fails_when_beam_is_not_first(self):
        g = gains()
        with tempfile.TemporaryDirectory() as t:
            d = Path(t)
            make(d, g, beam_of_group=0, first_seq_ue=1)
            st = e.evaluate(d, LINKS, CFG)
            self.assertEqual(st['h1a_fail_tbs'], 0)
            self.assertEqual(st['h1b_fail_tbs'], 24)
            self.assertTrue(e.h1a_ok(st))
            self.assertFalse(e.h1_ok(st))

    def test_mac_order_not_dci_order_identifies_beam(self):
        g = gains()
        with tempfile.TemporaryDirectory() as t:
            d = Path(t)
            make(d, g, beam_of_group=0, first_seq_ue=1, mac_first_ue=0)
            st = e.evaluate(d, LINKS, CFG)
            self.assertEqual((st['h1b_fail_tbs'], st['h1b_dci_order_fail_tbs']), (0, 24))
            self.assertTrue(e.h1_ok(st))

    def test_dci_without_mac_allocation_breaks_join(self):
        g = gains()
        with tempfile.TemporaryDirectory() as t:
            d = Path(t)
            make(d, g)
            rows = e151.read_csv(d / 'e15od_dl_sched.csv')[2:]
            write(d / 'e15od_dl_sched.csv', list(rows[0].keys()), [list(r.values()) for r in rows])
            st = e.evaluate(d, LINKS, CFG)
            self.assertEqual(st['J3_dci_without_mac_allocation'], 2)
            self.assertFalse(e.join_ok(st))

    def test_no_single_beam_fails_h1a(self):
        g = gains()
        with tempfile.TemporaryDirectory() as t:
            d = Path(t)
            make(d, g, beam_of_group=0)
            rows = e151.read_csv(d / 'e15od_dl_tb.csv')     # UE0 now on UE1's beam: no common beam
            for r in rows:
                if r['ue_index'] == '0':
                    m = np.arange(51) < 25
                    r['sinr_lin'] = repr(float(SNR[0] * np.mean(g[0, 1, m])))
            write(d / 'e15od_dl_tb.csv', list(rows[0].keys()), [list(r.values()) for r in rows])
            st = e.evaluate(d, LINKS, CFG)
            self.assertEqual(st['h1a_fail_tbs'], 24)

    def test_alone_own_beam_and_join_failure(self):
        g = gains()
        with tempfile.TemporaryDirectory() as t:
            d = Path(t)
            make(d, g, cosched=False)
            st = e.evaluate(d, LINKS, CFG)
            self.assertTrue(e.own_all_ok(st))
            self.assertEqual(st['groups_two_or_more_ues'], 0)
            make(d, g, bad_rb=True)
            st = e.evaluate(d, LINKS, CFG)
            self.assertEqual(st['J2_rb_mismatch'], 12)
            self.assertFalse(e.join_ok(st))


    def test_retransmissions_without_mac_record(self):
        """Amendment 1: a retransmission DCI (rv > 0) has no DlScheduling record; J3 ignores it
        and its group is not evaluable for H1b, while H1a still covers it."""
        g = gains()
        with tempfile.TemporaryDirectory() as t:
            d = Path(t)
            make(d, g)
            dci = e151.read_csv(d / 'e15od_dci.csv')
            for r in dci[:2]:                                 # slot 0: both DCIs are retransmissions
                r['rv'] = '1'
            write(d / 'e15od_dci.csv', list(dci[0].keys()), [list(r.values()) for r in dci])
            sch = e151.read_csv(d / 'e15od_dl_sched.csv')[2:]
            write(d / 'e15od_dl_sched.csv', list(sch[0].keys()), [list(r.values()) for r in sch])
            st = e.evaluate(d, LINKS, CFG)
            self.assertEqual((st['J3_dci_without_mac_allocation'], st['J3_retx_dcis_without_mac_record']), (0, 2))
            self.assertEqual(st['h1b_not_evaluable_groups'], 1)
            self.assertTrue(e.join_ok(st) and e.h1_ok(st))


class InputTests(unittest.TestCase):

    def test_pair_rule_and_args(self):
        links = [{'los': 1, 'bearing_deg': 30.0, 'py_azimuth_deg': a} for a in (10.0, 50.0, -20.0)] + \
                [{'los': 0, 'bearing_deg': 30.0, 'py_azimuth_deg': 80.0}]
        self.assertEqual(e.k1_pair(links), ((1, 2), 70.0))
        a = e.replace_arg(['--x=1', '--linksFile=/old', '--y=2'], 'linksFile', '/new')
        self.assertEqual(a, ['--x=1', '--y=2', '--linksFile=/new'])
        with self.assertRaises(SystemExit):
            e.replace_arg(['--x=1'], 'linksFile', '/new')

    def test_config(self):
        conf = e151.json.loads((ROOT / 'configs/e1/e1_5_4_0_ofdma_diagnostic.json').read_text())
        for k in ('R2_replay_identical', 'J1_join', 'J2_rb_count', 'J3_mac_allocation', 'H1a_K0_K1',
                  'H1b_K0_K1_reported', 'K1_power', 'P2_K2', 'P3_K3', 'P4_K4'):
            self.assertIn(k, conf['checks'])


if __name__ == '__main__':
    unittest.main()
