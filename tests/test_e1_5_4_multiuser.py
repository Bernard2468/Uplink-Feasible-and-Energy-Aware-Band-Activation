"""E1.5.4 analysis helpers: TB/DCI bookkeeping, per-user SE, H18 split, thresholds, regression rewrite."""
import gzip
import json
import math
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import e1_5_4_multiuser as m  # noqa: E402


def dci(dir_, rnti, slot, sym, nsym, mask, t='2'):
    return {'dir': dir_, 'rnti': str(rnti), 't_frame': '1', 't_subframe': '0', 't_slot': str(slot),
            'sym_start': str(sym), 'num_sym': str(nsym), 'rbg_bitmask': mask, 'type': t}


def tb(dir_, ue, t, rb=51, sym=1, size=100, rv='0', corrupt='0', mcs=5):
    return {'dir': dir_, 'ue': ue, 'time_ns': str(int(t)), 'rb_assigned': str(rb), 'num_sym': str(sym),
            'tb_size': str(size), 'rv': rv, 'corrupt': corrupt, 'mcs': str(mcs)}


class DciTests(unittest.TestCase):

    def test_conflicts(self):
        a = dci('UL', 1, 0, 0, 12, '11000')
        b = dci('UL', 2, 0, 0, 12, '00110')      # same symbols, disjoint RBs
        c = dci('UL', 3, 0, 5, 2, '01000')       # overlaps a in symbols AND RBs
        d = dci('UL', 4, 0, 12, 1, '11111')      # after a/b/c in symbols
        e = dci('DL', 1, 0, 0, 12, '11000')      # other direction
        rb, viol, share = m.dci_conflicts([a, b, c, d, e], one_ue_per_symbol=True)
        self.assertEqual(rb, 1)
        self.assertEqual(share, 2)               # a-b, b-c (disjoint RBs, symbols overlap)
        self.assertEqual(viol, 2)
        self.assertEqual(m.dci_conflicts([a, b], one_ue_per_symbol=False), (0, 0, 1))

    def test_rb_list_and_unique(self):
        self.assertEqual(m.rb_list('01101'), [1, 2, 4])
        rows = [{'dir': 'DL', 'rnti': '1', 'time_ns': '5'}, {'dir': 'DL', 'rnti': '1', 'time_ns': '5'},
                {'dir': 'UL', 'rnti': '1', 'time_ns': '5'}]
        self.assertEqual(len(m.unique_tbs(rows)), 2)


class AttributionTests(unittest.TestCase):

    def test_latest_establishment(self):
        rrc = [{'time_ns': '10', 'imsi': '1', 'rnti': '3'}, {'time_ns': '50', 'imsi': '2', 'rnti': '3'}]
        who = m.attribute_ul(rrc, {'1': 0, '2': 1})
        self.assertEqual(who(3, 20), 0)
        self.assertEqual(who('3', 60), 1)
        self.assertIsNone(who(3, 5))
        self.assertIsNone(who(9, 60))


class MetricTests(unittest.TestCase):

    def test_per_user_se_and_share(self):
        w = [0.0, 1.0]
        tbs = [tb('UL', 0, 0.1e9, size=100), tb('UL', 1, 0.2e9, size=100, corrupt='1'),
               tb('UL', 1, 0.3e9, size=100, rv='1'), tb('UL', 0, 1.5e9, size=999)]
        out = m.per_ue_metrics(tbs, w, 2)['UL']
        res1 = 51 * 12 * 30e3 * 1 * 0.5e-3 / 14
        self.assertAlmostEqual(out['res_dir'], 3 * res1, places=9)
        self.assertAlmostEqual(out['ue'][0]['se_user'], 800 / (3 * res1), places=9)
        self.assertAlmostEqual(out['ue'][1]['se_user'], 800 / (3 * res1), places=9)
        self.assertAlmostEqual(out['ue'][0]['share'], 1 / 3, places=12)
        self.assertEqual(out['ue'][1]['bler_first_tx'], 1.0)
        self.assertEqual(out['ue'][1]['retx_share'], 0.5)

    def test_part_s_normalisation(self):
        tbs = [tb('DL', 0, 0.5e9, rb=5, sym=13, size=1000)]
        q = m.per_ue_metrics(tbs, [0.0, 1.0], 1, part_s=True)['DL']['ue'][0]
        res = 5 * 12 * 30e3 * 13 * 0.5e-3 / 14
        self.assertAlmostEqual(q['se_user'], 8000 / (res * 51 / 5), places=9)

    def test_h18_split(self):
        ue = [tb('UL', 0, 1, rb=5), tb('UL', 0, 2, rb=20, corrupt='1'), tb('UL', 0, 3, rb=20),
              tb('UL', 0, 4, rb=3, rv='1'), tb('UL', 0, 5, rb=10, corrupt='1')]
        s = m.h18_split({0: ue}, [0.0, 1.0])
        self.assertEqual((s['n_up'], s['fail_up'], s['n_down'], s['fail_down']), (2, 2, 1, 0))


class CurveTests(unittest.TestCase):

    def test_threshold_and_interp(self):
        c = [(0.0, 0.05), (2.0, 0.10), (4.0, 0.20)]
        self.assertAlmostEqual(m.threshold(c, 0.15), 3.0)
        self.assertIsNone(m.threshold(c, 0.5))
        self.assertAlmostEqual(m.interp_curve(c, 1.0), 0.075)
        self.assertIsNone(m.interp_curve(c, 5.0))

    def test_gzip_deterministic(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td)
            (p / 'a.csv').write_text('x,y\n1,2\n')
            m.gzip_copy(p / 'a.csv', p / 'a1.gz')
            m.gzip_copy(p / 'a.csv', p / 'a2.gz')
            self.assertEqual((p / 'a1.gz').read_bytes(), (p / 'a2.gz').read_bytes())
            self.assertEqual(gzip.decompress((p / 'a1.gz').read_bytes()), b'x,y\n1,2\n')


class CommandTests(unittest.TestCase):

    def test_rewrite_keeps_tokens(self):
        man = json.loads((m.E153_RESULTS / 'manifest.json').read_text())
        ref = next(r for r in man['runs'] if r['tag'] == 't2_L93')['cmd']
        new = m.rewrite_cmd(ref, Path('/tmp/x'), losses=True)
        self.assertEqual(new[3], '--cwd=/tmp/x')
        a, b = ref[4].split(' '), new[4].split(' ')
        self.assertEqual(len(a), len(b))
        self.assertEqual([x for x, y in zip(a, b) if x != y], [t for t in a if t.startswith('--lossFile=')])
        self.assertIn('--lossFile=/tmp/x/losses.csv', b)
        man1 = json.loads((m.E1521_RESULTS / 'manifest.json').read_text())
        ref1 = next(r for r in man1['runs'] if r['tag'] == 'v1_a_f7p2_o2o_cb_b30')['cmd']
        self.assertIn('--linksFile=/tmp/y/links.csv', m.rewrite_cmd(ref1, Path('/tmp/y'), links=True)[4])
        with self.assertRaises(SystemExit):
            m.rewrite_cmd(ref1, Path('/tmp/y'))            # links path left pointing at the old directory

    def test_link_args(self):
        c = json.loads(m.E151_CONFIG.read_text())['common']
        st = json.loads(m.E153_CONFIG.read_text())['setup']
        a = m.link_args(c, st, Path('/tmp/r'), m.TDMA, 2, ['--dataRbs=5'])
        self.assertIn('--scheduler=ns3::NrMacSchedulerTdmaPF', a)
        self.assertTrue(all(' ' not in x for x in a))
        for k in ('--errorModel=ns3::NrEesmIrT2', '--resTrace=1', '--RngRun=2', '--dataRbs=5', '--simTime=1.0',
                  '--dlRate=40000000.0', '--ulRate=20000000.0'):
            self.assertIn(k, a)


class ConfigTests(unittest.TestCase):

    def test_config_and_analytic(self):
        conf = json.loads(m.CONFIG.read_text())
        for k in ('H_T', 'H_TC', 'H_O', 'S', 'S_C', 'M'):
            self.assertIn(k, conf['parts'])
        for k in ('R6a', 'R6b', 'R6c', 'R6d', 'E0', 'RES', 'V', 'POP', 'BEAM', 'FIX'):
            self.assertIn(k, conf['gates'])
        self.assertEqual(len(m.L_GRID), 39)
        self.assertEqual(conf['common']['times']['window_s'], [0.3, 1.0])
        s3a = json.loads((m.E153A_RESULTS / 'summary.json').read_text())['stage_b']
        pts = {d: [tuple(p) for p in s3a['curves'][f't2_{d}'] if -10 <= p[0] <= 35] for d in m.DIRS}
        f3ul = m.e153.required_snr_empirical(pts['UL'], 0.15 * 51 / 5) - 10 * math.log10(51 / 5)
        self.assertAlmostEqual(f3ul, -1.265, places=2)
        self.assertAlmostEqual(m.e153.required_snr_empirical(pts['DL'], 2.25), 12.916, places=2)


if __name__ == '__main__':
    unittest.main()
