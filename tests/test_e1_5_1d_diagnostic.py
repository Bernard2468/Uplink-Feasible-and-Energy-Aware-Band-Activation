"""E1.5.1-D: scheduler-log parser, order check, saturation, backlog and window counts."""
import csv
import json
import sys
import tempfile
import unittest
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import e1_5_1d_tdma_diagnostic as diag  # noqa: E402

P = '  [ CellId 1, bwpId 0] '


def sched_ul(t):
    return (f'+{t:.9f}s{P}NrMacSchedulerNs3:DoScheduleUl(): Scheduling UL FrameNum: 0 SubFrameNum: 2 SlotNum: 0 '
            'UL HARQ to retransmit: 0 Active Beams UL HARQ: 0 starting from (0, 13)')


def lcg(t, r, b, d='UL'):
    return f'+{t:.9f}s{P}NrMacSchedulerNs3:ComputeActiveUe(): UE {r} {d} LCG 2 bytes {b}'


def assigning(t, n, d='UL'):
    return f'+{t:.9f}s{P}NrMacSchedulerTdma:AssignRBGTDMA(): Assigning RBG in {d}, # beams active flows: 1, # sym: {n}'


def assigned(t, r, d='UL'):
    return (f'+{t:.9f}s{P}NrMacSchedulerTdma:AssignRBGTDMA(): Assigned 51 {d} RBG (= 1 SYM) to UE {r} '
            'total assigned up to now: 51 that corresponds to 51')


def covered(t):
    return f'+{t:.9f}s{P}NrMacSchedulerTdma:AssignRBGTDMA(): All the UE already have their resources allocated. Skipping the beam'


def owing(t, r):
    return f'+{t:.9f}s{P}NrMacSchedulerNs3:DoScheduleUlSr(): Owing a 17 byte bootstrap grant to UE {r} because of a SR'


def creating(t, r):
    return (f'+{t:.9f}s{P}NrMacSchedulerNs3:DoCschedUeConfigReq(): Creating user, beam [Sector: 0 elevation: 0] '
            f'and ue {r} assigned SRS periodicity 80 and offset 55')


def slot(t, order, n_sym, lcgs=(), owe=(), covered_at_end=False):
    lines = [sched_ul(t)] + [owing(t, r) for r in owe] + [lcg(t, r, b) for r, b in lcgs]
    lines += [lcg(t, 9, 500, 'DL'), assigning(t, 3, 'DL'), assigned(t, 9, 'DL')]   # interleaved DL
    lines += [assigning(t, n_sym)] + [assigned(t, r) for r in order]
    if covered_at_end:
        lines.append(covered(t))
    return lines


class ParserTests(unittest.TestCase):

    def test_parse_events(self):
        lines = [creating(0.017, 1), creating(0.017, 2), 'a line without a time prefix']
        lines += slot(0.6, [3, 2, 1, 3], 4, lcgs=[(3, 900), (2, 100)], owe=[1])
        lines += [sched_ul(0.6005)]                                   # UL event without data
        lines += slot(0.601, [2], 5, lcgs=[(2, 50)], covered_at_end=True)
        ev, cr = diag.parse_scheduler_log(lines)
        self.assertEqual(cr, [(0.017, 1), (0.017, 2)])
        self.assertEqual(len(ev), 3)
        self.assertEqual(ev[0]['lcg'], {3: 900, 2: 100})
        self.assertEqual(ev[0]['owed'], {1})
        self.assertEqual(ev[0]['data'], {'sym_avail': 4, 'assigned': [3, 2, 1, 3], 'all_covered': False})
        self.assertIsNone(ev[1]['data'])
        self.assertEqual(ev[1]['owed'], set())                        # UE 1 got a symbol in event 0
        self.assertTrue(ev[2]['data']['all_covered'])
        self.assertEqual(diag.first_assignment_order([3, 2, 1, 3, 2]), [3, 2, 1])

    def test_owed_persists_until_served(self):
        lines = slot(0.6, [5], 1, lcgs=[(5, 100)], owe=[7]) + [sched_ul(0.6005)] + slot(0.601, [5], 1)
        ev, _ = diag.parse_scheduler_log(lines)
        self.assertEqual([e['owed'] for e in ev], [{7}, {7}, {7}])
        r = diag.backlog_and_service(ev, 7, 0.5, 1.4)
        self.assertEqual((r['backlogged'], r['ul_scheduling_events'], r['served'], r['ul_data_events']), (3, 3, 0, 2))

    def test_direction_mismatch_raises(self):
        lines = [sched_ul(0.6), assigning(0.6, 2), assigned(0.6, 1, 'DL')]
        with self.assertRaises(ValueError):
            diag.parse_scheduler_log(lines)


class OrderAndSaturationTests(unittest.TestCase):

    def events(self, orders, creations_at=()):
        lines = [creating(t, 99) for t in creations_at]
        lines.sort()
        out = []
        for k, o in enumerate(orders):
            out += slot(0.6 + 0.001 * k, o, len(o))
        ev, cr = diag.parse_scheduler_log(lines + out)
        return ev, cr + [(t, 99) for t in creations_at if (t, 99) not in cr]

    def test_consistent_orders_pass(self):
        ev, cr = self.events([[3, 2, 1], [3, 1], [2, 1], [3, 2]])
        r = diag.order_check(ev, cr, 0.5, 1.4)
        self.assertTrue(r['passed'])
        self.assertEqual(diag.global_order(ev, 0.5, 1.4), [3, 2, 1])

    def test_rotation_is_detected(self):
        ev, cr = self.events([[3, 2, 1], [1, 3, 2]])
        r = diag.order_check(ev, cr, 0.5, 1.4)
        self.assertFalse(r['passed'])
        self.assertIsNotNone(r['epochs'][0]['cycle'])
        self.assertIsNone(diag.global_order(ev, 0.5, 1.4))

    def test_insertion_starts_a_new_epoch(self):
        ev, cr = self.events([[3, 2, 1], [1, 3, 2]], creations_at=[0.6005])
        r = diag.order_check(ev, cr, 0.5, 1.4)
        self.assertEqual(len(r['epochs']), 2)
        self.assertTrue(r['passed'])

    def test_saturation_share(self):
        lines = slot(0.6, [1, 2], 2) + slot(0.601, [1], 3, covered_at_end=True) + slot(0.4, [1], 1)
        ev, _ = diag.parse_scheduler_log(lines)
        s = diag.saturation(ev, 0.5, 1.4)
        self.assertEqual((s['data_events'], s['all_symbols_assigned'], s['share']), (2, 1, 0.5))


class TraceTests(unittest.TestCase):

    def test_windows_dedup_and_attribution(self):
        with tempfile.TemporaryDirectory() as t:
            d = Path(t)
            with open(d / 'e15_rb_after_attach.csv', 'w', newline='') as fh:
                w = csv.writer(fh, lineterminator='\n')
                w.writerows([['ue_index', 'imsi', 'rnti', 'ue_rb_num'], [0, 1, 10, 51], [1, 2, 11, 51]])
            with open(d / 'e15_rrc_events.csv', 'w', newline='') as fh:
                w = csv.writer(fh, lineterminator='\n')
                w.writerows([['time_ns', 'imsi', 'cell_id', 'rnti'], [0, 1, 1, 10], [0, 2, 1, 11]])
            hdr = ['ue_index', 'rnti', 'time_ns', 'sinr_lin', 'sinr_min_lin', 'rb_assigned', 'mcs', 'tb_size',
                   'rank', 'rv', 'num_sym', 'corrupt', 'tbler']
            rows = []
            for k in range(9):                       # UE 0: one good TB per window, each logged twice
                tb = [-1, 10, int((0.55 + 0.1 * k) * 1e9), 1.0, 1.0, 51, 5, 100, 1, 0, 1, 0, 0.0]
                rows += [tb, tb]
            rows.append([-1, 11, int(0.55e9), 1.0, 1.0, 51, 0, 19, 1, 0, 2, 1, 1.0])   # UE 1: corrupt only
            rows.append([-1, 11, int(0.65e9), 1.0, 1.0, 51, 0, 19, 1, 1, 2, 0, 0.0])   # UE 1: good retx
            rows.append([-1, 77, int(0.70e9), 1.0, 1.0, 51, 0, 19, 1, 0, 2, 0, 0.0])   # unknown RNTI
            with open(d / 'e15_ul_tb.csv', 'w', newline='') as fh:
                w = csv.writer(fh, lineterminator='\n')
                w.writerow(hdr)
                w.writerows(rows)
            per, tot = diag.ul_tb_stats(d, 2, 0.5, 1.4, 0.1)
            self.assertEqual(per[0]['ul_tb'], 9)
            self.assertEqual(per[0]['served_windows'], 9)
            self.assertEqual((per[1]['ul_tb'], per[1]['corrupt'], per[1]['retx'], per[1]['served_windows']),
                             (2, 1, 1, 1))
            self.assertEqual((tot['distinct_ul_tbs'], tot['trace_rows'], tot['unmapped_tbs']), (12, 21, 1))
            self.assertEqual((tot['ul_symbols'], tot['ul_symbols_retx']), (15, 2))


class CommandTests(unittest.TestCase):

    def test_replication_command_equals_run4(self):
        c151 = json.loads((ROOT / 'configs/e1/e1_5_1_deterministic.json').read_text(encoding='utf-8'))
        man = json.loads((ROOT / 'results/phase2/e1_5_1_deterministic/manifest.json').read_text(encoding='utf-8'))
        for rec in man['runs']:
            run = next(r for r in c151['runs'] if r['tag'] == rec['tag'])
            rundir = PurePosixPath(rec['cmd'][3].split('=', 1)[1])
            self.assertEqual(diag.build_cmd(c151['common'], run, rundir), rec['cmd'])


if __name__ == '__main__':
    unittest.main()
