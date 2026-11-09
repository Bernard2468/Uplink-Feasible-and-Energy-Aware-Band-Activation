"""P3.1 logic tests on SYNTHETIC parameters only (no published target is evaluated here): PA law forms, Golard
efficiency law, sub-6 closed forms vs direct sum, FR3 closed form vs phase ledger, frame fractions, TR ladder and
units, the energy ledger rules, D10 arithmetic and the .docx table parser."""
import copy
import json
import math
import random
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import p3_1_energy_models as m  # noqa: E402

CONF = json.loads(m.CONFIG.read_text(encoding='utf-8'))


def synthetic_golard(rng):
    g = copy.deepcopy(CONF['golard_table2'])
    g['pa'].update(xi=rng.uniform(0.05, 0.3), n=rng.choice([0.25, 0.5]), eta_tech=rng.uniform(0.2, 0.6),
                   a=rng.uniform(1e-5, 1e-3), b=rng.uniform(0.2, 2.0), delta_idle=rng.uniform(0.1, 0.5),
                   delta_sleep=rng.uniform(0.0, 0.2))
    g['afe'].update(alpha12=rng.uniform(0, 0.5), p_trx_ref_w=rng.uniform(0.05, 0.4), delta_sleep_trx=rng.uniform(0.2, 0.8))
    g['afe']['p_misc_ref_w'] = {'RRU': rng.uniform(5, 30), 'AAU': rng.uniform(5, 30)}
    g['dbb'].update(p_link_ref_w=rng.uniform(20, 120), p_phy_ref_w=rng.uniform(0.01, 0.1), delta_idle_phy=rng.uniform(0.3, 1.0))
    return g


def synthetic_cfg(rng, protocol):
    return {'ru': rng.choice(['RRU', 'AAU']), 'protocol': protocol, 'M': rng.choice([4, 16, 32]), 'K': 2,
            'f_hz': rng.uniform(0.7e9, 4e9), 'B_hz': rng.uniform(10e6, 80e6), 'p_max_w': rng.uniform(1.0, 30.0)}


def synthetic_hw(rng, m_rf, m_ant, signalling):
    pa = {'p_max_w': rng.uniform(0.05, 0.5), 'eta_max': rng.uniform(0.1, 0.4), 'alpha': 0.75, 'xi': rng.uniform(0.05, 0.3)}
    p_t = pa['p_max_w'] * m_ant * rng.uniform(0.2, 1.0)
    return m.fr3_hardware(CONF, rng.uniform(3e9, 15e9), rng.uniform(50e6, 300e6), m_ant, m_rf, rng.choice([2, 4, 6]),
                          p_t, pa, signalling, rng.uniform(30, 500), rng.uniform(1e8, 5e9), rng.uniform(1e7, 1e9))


class PaLaw(unittest.TestCase):

    def test_forms_and_bounds(self):
        pa = {'p_max_w': 2.0, 'eta_max': 0.3, 'alpha': 0.6, 'xi': 0.2}
        self.assertAlmostEqual(m.pa_power(0.0, pa, 'printed'), 0.2 * 2.0 ** 0.6 / 0.3, places=14)
        self.assertAlmostEqual(m.pa_power(0.0, pa, 'golard'), 0.2 * 2.0 / 0.3, places=14)
        self.assertAlmostEqual(m.pa_power(2.0, pa, 'golard'), 2.0 / 0.3, places=14)  # full output: P_max / eta
        self.assertLess(m.pa_power(0.5, pa), m.pa_power(1.0, pa))
        for bad in (-0.1, 2.1):
            with self.assertRaises(ValueError):
                m.pa_power(bad, pa)

    def test_golard_efficiency_law(self):
        g = copy.deepcopy(CONF['golard_table2'])
        g['pa'].update(a=0.0, b=0.0)
        self.assertEqual(m.golard_eta_max(5.0, 3e9, g), g['pa']['eta_tech'])
        g['pa'].update(a=1e-4, b=1.0)
        peak = math.sqrt(1.0 / (1e-4 * 2.0 ** 2))  # 50 W at 2 GHz
        e = [m.golard_eta_max(p, 2e9, g) for p in (0.8 * peak, peak, 1.25 * peak)]
        self.assertTrue(e[1] > e[0] and e[1] > e[2])
        self.assertGreater(m.golard_eta_max(10.0, 1e9, g), m.golard_eta_max(10.0, 6e9, g))


class Sub6(unittest.TestCase):

    def test_compact_equals_direct(self):
        rng = random.Random(7)
        for _ in range(40):
            g = synthetic_golard(rng)
            cfg = synthetic_cfg(rng, rng.choice(['LTE_FDD', 'NR_TDD']))
            dd, dt = rng.uniform(0, 1), rng.uniform(0, 1)
            for sf, nc in ((s, n) for s in ('printed', 'golard') for n in (1, 3)):
                par = m.sub6_params(cfg, g, dd, dt, sf, nc)
                for load in (0.0, 0.37, 1.0):
                    for m_a in (cfg['K'] + 1, cfg['M']):
                        p_a = rng.uniform(0, 1) * cfg['p_max_w']
                        a = m.sub6_compact(par, cfg['M'], load, m_a, p_a)
                        b = m.sub6_direct(cfg, g, dd, dt, sf, nc, load, m_a, p_a)
                        self.assertLessEqual(m.rel_diff(a, b), 1e-12)

    def test_interpretation_effects(self):
        rng = random.Random(3)
        g, cfg = synthetic_golard(rng), synthetic_cfg(rng, 'NR_TDD')
        base = m.sub6_params(cfg, g, 0.25, 0.5, 'printed', 1)
        nc3 = m.sub6_params(cfg, g, 0.25, 0.5, 'printed', 3)
        gol = m.sub6_params(cfg, g, 0.25, 0.5, 'golard', 1)
        for k in ('gamma', 'P0', 'P1'):
            self.assertEqual(base[k], nc3[k])
        self.assertLess(nc3['Psleep'], base['Psleep'])
        self.assertEqual(base['gamma'], gol['gamma'])
        self.assertNotEqual(base['P0'], gol['P0'])
        self.assertNotEqual(base['P1'], gol['P1'])
        self.assertEqual(m.sub6_params(cfg, g, 1.0, 1.0, 'printed', 1)['P0'], 0.0)  # no muDTX -> P0 = 0


class Fr3(unittest.TestCase):

    def test_closed_form_equals_ledger(self):
        rng = random.Random(11)
        for m_rf, m_ant in ((8, 64), (16, 64), (64, 64), (32, 256), (256, 1024), (1024, 1024)):
            for signalling in ('reference', 'project'):
                hw = synthetic_hw(rng, m_rf, m_ant, signalling)
                for sf in ('printed', 'golard'):
                    for xd, xu in ((0, 0), (0.2, 0.7), (1, 1), (1, 0)):
                        self.assertLessEqual(m.rel_diff(m.fr3_power(hw, xd, xu, sf), m.fr3_ledger(hw, xd, xu, sf)), 1e-12)
                    self.assertLessEqual(m.rel_diff(m.fr3_power(hw, 0, 0, sf, tau_sig=0.0),
                                                    m.fr3_ledger(hw, 0, 0, sf, tau_sig=0.0)), 1e-12)

    def test_frame_fractions_as_printed(self):
        for x, tau, s in ((0.3, 0.75, 1 / 14), (1.0, 0.6, 0.1), (0.0, 0.75, 0.2)):
            self.assertAlmostEqual(m._frame_avg(1.0, x, tau, s, 1.0, 1.0), 1.0 + tau * x * s, places=14)
            self.assertAlmostEqual(m._frame_avg(1.0, x, tau, s, 1.0, 1.0, frame='consistent'), 1.0, places=14)

    def test_architecture_and_monotone(self):
        rng = random.Random(5)
        hw = synthetic_hw(rng, 16, 64, 'project')
        self.assertEqual(m.fr3_active_powers(hw, 0, 0)['m_ps'], 4.0)
        hw_fd = synthetic_hw(rng, 64, 64, 'project')
        self.assertEqual(m.fr3_active_powers(hw_fd, 0, 0)['m_ps'], 0)
        vals = [m.fr3_power(hw, x, x) for x in (0, 0.1, 0.5, 1)]
        self.assertTrue(all(b > a for a, b in zip(vals, vals[1:])))
        self.assertLess(m.fr3_power(hw, 0, 0, tau_sig=0.0), vals[0])

    def test_pa_count_is_antenna_count(self):
        rng = random.Random(9)
        hw = synthetic_hw(rng, 16, 64, 'project')
        _, p1 = m.fr3_power(hw, 0.5, 0.5, parts=True)
        hw2 = dict(hw, M_ant=128, P_T=hw['P_T'] * 2)
        _, p2 = m.fr3_power(hw2, 0.5, 0.5, parts=True)
        self.assertAlmostEqual(p2['pa'], 2 * p1['pa'], places=9)


class LadderAndLedger(unittest.TestCase):

    def test_ratios_and_units(self):
        tr = CONF['tr38864']
        lad = m.ladder(110.0, tr, 'cat1', 'set1')
        self.assertAlmostEqual(lad['w_per_unit_w'], 2.0, places=14)
        self.assertAlmostEqual(lad['light']['p_w'], 50.0, places=12)
        self.assertAlmostEqual(lad['deep']['p_w'], 2.0, places=12)
        self.assertAlmostEqual(lad['light']['e_add_j'], 2.0 * 90 * 1e-3, places=14)
        self.assertAlmostEqual(lad['deep']['e_add_j'], 2.0 * 1000 * 1e-3, places=14)
        self.assertEqual(lad['deep']['t_s'], 0.05)
        lad2 = m.ladder(11.0, tr, 'cat2', 'set1')
        self.assertAlmostEqual(lad2['light']['p_w'], 11.0 * 2.1 / 5.5, places=12)
        self.assertEqual(lad2['deep']['t_s'], 10.0)

    def test_sequence_energy_rules(self):
        self.assertAlmostEqual(m.sequence_energy(m.WORKED['seq'], m.WORKED['states']), m.WORKED['energy_j'], places=9)
        st = m.WORKED['states']
        for bad in ([('on', 1.0), ('deep', 0.01)], [('on', 1.0), ('light', 1.0), ('deep', 1.0)], [('on', 0.0)],
                    [('on', float('nan'))]):
            with self.assertRaises(ValueError):
                m.sequence_energy(bad, st)

    def test_breakeven(self):
        st = {'p_w': 10.0, 't_s': 0.05, 'e_add_j': 9.0}
        self.assertAlmostEqual(m.breakeven_s(100.0, st), 0.1, places=12)
        self.assertEqual(m.breakeven_s(100.0, dict(st, e_add_j=0.0)), 0.05)
        self.assertEqual(m.breakeven_s(5.0, st), math.inf)


class Units(unittest.TestCase):

    def test_dbm(self):
        self.assertEqual(m.dbm_to_w(50.0), 100.0)
        self.assertAlmostEqual(m.w_to_dbm(m.dbm_to_w(53.0)), 53.0, places=12)

    def test_d10_arithmetic(self):
        res = m.d10_table(CONF['project']['d10_expected'])
        self.assertTrue(res['matches_expected'])
        self.assertTrue(res['sige_infeasible_at_53'])
        self.assertTrue(res['rows']['43_256']['sige_feasible'])
        self.assertTrue(res['rows']['50_1024']['sige_feasible'])


class DocxParser(unittest.TestCase):

    def _docx(self, body):
        d = tempfile.mkdtemp()
        p = Path(d) / 't.docx'
        with zipfile.ZipFile(p, 'w') as z:
            z.writestr('word/document.xml', '<w:document><w:body>' + body + '</w:body></w:document>')
        return p

    def test_rows_after_caption(self):
        row = lambda cells: '<w:tr>' + ''.join(f'<w:tc><w:p><w:r><w:t>{c}</w:t></w:r></w:p></w:tc>' for c in cells) + '</w:tr>'  # noqa: E731
        body = ('<w:tbl>' + row(['x', 'y']) + '</w:tbl><w:p><w:r><w:t>Table 9.9-1: Cap</w:t></w:r></w:p>'
                + '<w:tbl>' + row(['Deep sleep', '1', '2']) + row(['Light sleep', '3 ms', '4 s']) + '</w:tbl>')
        got = m.docx_tables_after(self._docx(body), ['Table 9.9-1:', 'Table 0:'])
        self.assertEqual(got['Table 9.9-1:'], [['Deep sleep', '1', '2'], ['Light sleep', '3 ms', '4 s']])
        self.assertIsNone(got['Table 0:'])
        dup = self._docx('<w:p><w:r><w:t>Table 1: a</w:t></w:r></w:p><w:p><w:r><w:t>Table 1: b</w:t></w:r></w:p>')
        self.assertIsNone(m.docx_tables_after(dup, ['Table 1:'])['Table 1:'])
        self.assertAlmostEqual(m._num('640 ms'), 0.64, places=12)
        self.assertEqual(m._num('10 s'), 10.0)


if __name__ == '__main__':
    unittest.main()
