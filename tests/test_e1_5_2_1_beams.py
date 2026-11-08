"""E1.5.2-1 analysis: independent DirectPath / codebook / bound, checks S1-D1, N1, V1, statistics."""
import csv
import math
import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import e1_5_2_1_beams as e  # noqa: E402
import e1_5_1d_tdma_diagnostic as diag  # noqa: E402
import e1_5_1_deterministic as e151  # noqa: E402

CONF = e151.json.loads((ROOT / 'configs/e1/e1_5_2_1_beams.json').read_text())


def write(path, header, rows):
    with open(path, 'w', newline='') as fh:
        w = csv.writer(fh, lineterminator='\n')
        w.writerow(header)
        w.writerows(rows)


class GeometryTests(unittest.TestCase):

    def test_element_locations_bearing(self):
        d0 = e.element_locations(2, 3, 0.0)
        np.testing.assert_allclose(d0[:, 0], 0.0)
        np.testing.assert_allclose(d0[:, 1], [0, 0.5, 1.0, 0, 0.5, 1.0])
        np.testing.assert_allclose(d0[:, 2], [0, 0, 0, 0.5, 0.5, 0.5])
        d90 = e.element_locations(2, 3, 90.0)                # panel y' axis turns to -x
        np.testing.assert_allclose(d90[:, 0], -d0[:, 1], atol=1e-15)
        np.testing.assert_allclose(d90[:, 1], 0.0, atol=1e-15)
        d270 = e.element_locations(2, 3, 270.0)              # wrapped to -90 deg: same orientation
        np.testing.assert_allclose(d270[:, 0], d0[:, 1], atol=1e-15)

    def test_ns3_azimuth_truncation(self):
        step = 2 * math.pi / e.NS3_INT_RANGE
        self.assertEqual(e.ns3_wrap_to_pi(0.0), 0.0)
        for a in np.random.default_rng(3).uniform(-math.pi, math.pi, 200):
            q = e.ns3_wrap_to_pi(float(a))
            self.assertLessEqual(abs(q - a), step * 1.0001)
            self.assertTrue(abs(q) <= abs(a) + 1e-15)               # truncation toward zero
        w = e.direct_path_vector([0, 0, 25], [100, 50, 1.5], 16, 32, 30.0)
        w0 = e.direct_path_vector([0, 0, 25], [100, 50, 1.5], 16, 32, 30.0, ns3_azimuth=False)
        self.assertLess(np.max(np.abs(np.angle(w / w0))), 1e-8)
        self.assertGreater(np.max(np.abs(np.angle(w / w0))), 0.0)

    def test_direct_path_is_matched_on_transmit_side(self):
        gnb, ue = [0.0, 0.0, 25.0], [80.0, 40.0, 1.5]
        w = e.direct_path_vector(gnb, ue, 8, 16, 30.0)
        self.assertAlmostEqual(float(np.vdot(w, w).real), 1.0, places=12)
        zen, az = e.ant.link_angles(gnb, ue)
        th, ph = math.radians(zen[0]), math.radians(az[0])
        r = np.array([math.sin(th) * math.cos(ph), math.sin(th) * math.sin(ph), math.cos(th)])
        a = np.exp(2j * math.pi * (e.element_locations(8, 16, 30.0) @ r))   # ray phase on side s
        self.assertAlmostEqual(abs(np.sum(w * a)) ** 2, 128.0, places=9)
        self.assertLess(abs(np.sum(np.conj(w) * a)) ** 2, 128.0 / 10)        # receive-side form: mismatched


class CodebookTests(unittest.TestCase):

    def test_unit_norm_and_orthogonal_subgrid(self):
        W = e.codebook_matrix(4, 8, 4)
        self.assertEqual(W.shape, (32, 16 * 32))
        np.testing.assert_allclose(np.sum(np.abs(W) ** 2, axis=0), 1.0, atol=1e-12)
        ph = 4 * 8
        sub = [b for b in range(W.shape[1]) if (b % ph) % 4 == 0 and (b // ph) % 4 == 0]
        self.assertEqual(len(sub), 32)
        G = W[:, sub].conj().T @ W[:, sub]
        np.testing.assert_allclose(G, np.eye(32), atol=1e-12)

    def test_matched_beam_wins(self):
        W = e.codebook_matrix(4, 8, 4)
        b = 37
        h = np.tile(np.sqrt(32) * np.conj(W[:, b]), (5, 1))   # 5 RBs, flat channel on beam b
        gains = np.mean(np.abs(h @ W) ** 2, axis=0)
        self.assertEqual(int(np.argmax(gains)), b)
        self.assertAlmostEqual(float(gains[b]), 32.0, places=9)
        R = (h.conj().T @ h) / 5
        self.assertAlmostEqual(float(np.linalg.eigvalsh(R)[-1]), 32.0, places=9)

    def test_dump_roundtrip(self):
        rng = np.random.default_rng(1)
        h = rng.normal(size=(3, 4)) + 1j * rng.normal(size=(3, 4))
        with tempfile.TemporaryDirectory() as t:
            d = Path(t)
            np.stack([h.real, h.imag], axis=-1).astype('<f8').tofile(d / 'e1521_h.bin')
            write(d / 'e1521_h_index.csv', ['link_index', 'copy', 'k', 'n', 'offset_bytes'], [[0, 0, 3, 4, 0]])
            np.testing.assert_array_equal(e.read_dump(d)[(0, 0)], h)


def real_row(l, c, scale=1.0, n_cl=12, **kw):
    r = {'link_index': l, 'copy': c, 'n_clusters': n_cl, 'gnb_is_s': 1, 'g_ns3_dp': 10.0, 'g_dp': 10.0,
         'g_cb': 20.0 * scale, 'cb_p': 1, 'cb_q': 2, 'g_cb1': 15.0, 'cb1_p': 0, 'cb1_q': 0, 'g_eigpo': 22.0,
         'g_eig': 25.0, 'g_mrt': 40.0, 'g_ns3_cb': 20.0 * scale, 'g_ns3_eig': 25.0, 'g_eigvec': 25.0,
         'eig_iters': 20, 'eig_certified': 1}
    r.update(kw)
    return {k: str(v) for k, v in r.items()}


LINKS = [{'name': 'ueA', 'los': 1, 'geom_los': 1, 'bearing_deg': 30.0, 'py_gain_db': 30.0},
         {'name': 'ueB', 'los': 0, 'geom_los': 0, 'bearing_deg': 150.0, 'py_gain_db': 28.0}]
INFO = {'frequency_hz_readback': '3500000000', 'scenario_readback': 'UMa', 'num_rbs': '51', 'rng_run': '2',
        'eig_uncertified': '0'}
RUN = {'tag': 't', 'frequency_hz': 3.5e9, 'stratum': 'o2o', 'array': 'a'}


def realset(**kw):
    return {(l, c): real_row(l, c, n_cl=(12 if l == 0 else 20), **kw) for l in range(2) for c in range(3)}


class BeamsChecksTests(unittest.TestCase):

    def test_consistent_run_passes(self):
        res = e.beams_checks(realset(), True, INFO, LINKS, RUN, CONF)
        self.assertTrue(all(v['passed'] for v in res.values()), res)

    def test_each_failure_is_caught(self):
        self.assertFalse(e.beams_checks(realset(g_ns3_dp=10.0001), True, INFO, LINKS, RUN, CONF)
                         ['I1_reconstruction']['passed'])
        self.assertFalse(e.beams_checks(realset(g_ns3_cb=20.0001), True, INFO, LINKS, RUN, CONF)
                         ['I2_codebook_in_ns3']['passed'])
        self.assertFalse(e.beams_checks(realset(eig_certified=0), True, INFO, LINKS, RUN, CONF)
                         ['I3_eigen_in_ns3']['passed'])
        self.assertFalse(e.beams_checks(realset(g_eigvec=25.001, g_ns3_eig=25.001), True, INFO, LINKS, RUN, CONF)
                         ['I3_eigen_in_ns3']['passed'])
        self.assertFalse(e.beams_checks(realset(g_eigpo=25.01), True, INFO, LINKS, RUN, CONF)
                         ['D1_dominance']['passed'])
        self.assertFalse(e.beams_checks(realset(g_cb1=21.0), True, INFO, LINKS, RUN, CONF)
                         ['D1_dominance']['passed'])
        self.assertFalse(e.beams_checks(realset(g_mrt=24.0), True, INFO, LINKS, RUN, CONF)
                         ['D1_dominance']['passed'])
        self.assertFalse(e.beams_checks(realset(), False, INFO, LINKS, RUN, CONF)['S1_bookkeeping']['passed'])
        bad = dict(INFO, rng_run='1')
        self.assertFalse(e.beams_checks(realset(), True, bad, LINKS, RUN, CONF)['S1_bookkeeping']['passed'])

    def test_cluster_limits_by_stratum(self):
        rs = realset()
        self.assertTrue(e.beams_checks(rs, True, INFO, LINKS, RUN, CONF)['S1_bookkeeping']['n_clusters_ok'])
        o2i = dict(RUN, stratum='o2i')                       # NLOS link with 20 clusters: not the O2I table
        self.assertFalse(e.beams_checks(rs, True, INFO, LINKS, o2i, CONF)['S1_bookkeeping']['n_clusters_ok'])

    def test_read_real_duplicates_and_completeness(self):
        with tempfile.TemporaryDirectory() as t:
            d = Path(t)
            rows = list(realset().values())
            hdr = list(rows[0].keys())
            write(d / 'e1521_real.csv', hdr, [[r[k] for k in hdr] for r in rows])
            real, complete = e.read_real(d, 2, 3)
            self.assertTrue(complete)
            self.assertFalse(e.read_real(d, 2, 4)[1])
            write(d / 'e1521_real.csv', hdr, [[r[k] for k in hdr] for r in rows + rows[:1]])
            with self.assertRaises(SystemExit):
                e.read_real(d, 2, 3)


class StatisticsTests(unittest.TestCase):

    def test_bootstrap_deterministic_and_degenerate(self):
        v = [1.0, 2.0, 3.0, 4.0, 10.0]
        self.assertEqual(e.bootstrap_median_ci(v), e.bootstrap_median_ci(v))
        lo, hi = e.bootstrap_median_ci(v)
        self.assertLessEqual(lo, 3.0)
        self.assertGreaterEqual(hi, 3.0)
        self.assertEqual(e.bootstrap_median_ci([2.5] * 9), [2.5, 2.5])

    def test_spearman(self):
        self.assertAlmostEqual(e.spearman([1, 2, 3, 4], [10, 20, 30, 45]), 1.0)
        self.assertAlmostEqual(e.spearman([1, 2, 3, 4], [4, 3, 2, 1]), -1.0)
        np.testing.assert_allclose(e.ranks([5, 1, 5, 2]), [3.5, 1, 3.5, 2])

    def test_position_table_and_strata(self):
        rs = realset()
        rs[(0, 1)] = real_row(0, 1, scale=2.0, g_cb1=15.0, g_ns3_cb=40.0, g_eig=45.0, g_eigvec=45.0, g_ns3_eig=45.0)
        tab = e.position_table(rs, LINKS, 3, {'ueA': 100.0, 'ueB': 200.0})
        a = tab[0]
        self.assertAlmostEqual(a['cb']['median_db'], 10 * math.log10(20.0), places=12)
        self.assertAlmostEqual(a['cb']['mean_lin_db'], 10 * math.log10(80.0 / 3), places=12)
        self.assertAlmostEqual(a['cb']['deficit_db'], 30.0 - 10 * math.log10(20.0), places=12)
        self.assertAlmostEqual(a['delta_cb_dp_db'], 10 * math.log10(2.0), places=12)
        self.assertEqual(a['cb_mode_beam'], {'p': 1, 'q': 2, 'share': 1.0, 'distinct_beams': 1})
        s = e.stratum_summaries(RUN, tab)
        self.assertEqual(set(s), {'o2o_los', 'o2o_nlos'})
        self.assertEqual(s['o2o_los']['deficit_db']['dp']['n_positions'], 1)
        s2 = e.stratum_summaries(dict(RUN, stratum='o2i'), tab)
        self.assertEqual(set(s2), {'o2i', 'o2i_geom_los', 'o2i_geom_nlos'})
        self.assertEqual(s2['o2i']['deficit_db']['dp']['n_positions'], 2)


class N1V1Tests(unittest.TestCase):

    def test_n1(self):
        links = [{'los': 1, 'o2i': 0}, {'los': 0, 'o2i': 0}, {'los': 0, 'o2i': 1}]
        with tempfile.TemporaryDirectory() as t:
            d = Path(t)
            write(d / 'e152_real.csv', ['link_index', 'copy', 'gain_lin', 'gnb_is_s'],
                  [[0, 0, 1.0, 1], [0, 1, 1.1, 1], [1, 0, 2.0, 1], [2, 0, 0.98, 1]])
            r = e.n1_check(d, links)
            self.assertTrue(r['los']['passed'] and r['o2i']['passed'])
            self.assertFalse(r['nlos']['passed'])

    def test_v1_identity_and_flags(self):
        c = e151.json.loads((ROOT / 'configs/e1/e1_5_1_deterministic.json').read_text())['common']
        cfg = diag.carrier_cfg(c, 7.2e9)
        links = [{'name': 'ueA', 'loss_db': 100.0}, {'name': 'ueB', 'loss_db': 120.0}]
        snr = e.lb.downlink(np.array([l['loss_db'] for l in links]), cfg)['snr_db']
        gains = [300.0, 40.0]
        hdr = ['ue_index', 'rnti', 'time_ns', 'sinr_lin', 'sinr_min_lin', 'rb_assigned', 'mcs', 'tb_size', 'rank',
               'rv', 'num_sym', 'corrupt', 'tbler']
        bhdr = ['ue_index', 'name', 'beam', 'gain_lin', 'gnb_is_s', 'gnb_elems', 'bm_vector_match',
                'channel_generated_s']
        with tempfile.TemporaryDirectory() as t:
            d = Path(t)
            write(d / 'e15_run_info.csv', ['key', 'value'], [['channel_model_frequency_hz_readback', '7200000000']])
            write(d / 'e1521_nr_beam.csv', bhdr, [[i, links[i]['name'], 'eig', gains[i], 1, 128, 1, 0] for i in range(2)])
            rows = [[i, i + 1, k, 10 ** (snr[i] / 10) * gains[i], 0, 51, 5, 100, 1, 0, 12, 0, 0]
                    for i in range(2) for k in range(11)]
            write(d / 'e15_dl_tb.csv', hdr, rows + rows[:2])
            res, _ = e.v1_check(d, links, cfg)
            self.assertTrue(res['passed'], res)
            self.assertEqual((res['tbs'], res['min_tbs_per_ue']), (22, 11))
            write(d / 'e1521_nr_beam.csv', bhdr, [[i, links[i]['name'], 'eig', gains[i], 1, 128, int(i == 0), 0]
                                                  for i in range(2)])
            self.assertFalse(e.v1_check(d, links, cfg)[0]['passed'])
            write(d / 'e1521_nr_beam.csv', bhdr, [[i, links[i]['name'], 'eig', gains[i], 1, 128, 1, 0.1 * i]
                                                  for i in range(2)])
            self.assertFalse(e.v1_check(d, links, cfg)[0]['passed'])
            write(d / 'e1521_nr_beam.csv', bhdr, [[i, links[i]['name'], 'eig', gains[i], 1, 128, 1, 0] for i in range(2)])
            rows[0][5] = 40
            write(d / 'e15_dl_tb.csv', hdr, rows)
            self.assertFalse(e.v1_check(d, links, cfg)[0]['all_tbs_51_rbs'])


class LinksTests(unittest.TestCase):

    def test_strata_conditions(self):
        med, _ = e151.median_losses()
        _, o2o = e.links_for(3.5e9, 8, 16, med, 'o2o')
        _, o2i = e.links_for(3.5e9, 8, 16, med, 'o2i')
        self.assertEqual(sum(l['los'] for l in o2o), 9)
        self.assertTrue(all(l['los'] == 0 and l['o2i'] == 1 for l in o2i))     # hazard 13
        self.assertEqual([l['geom_los'] for l in o2i], [l['los'] for l in o2o])
        with self.assertRaises(ValueError):
            e.links_for(3.5e9, 8, 16, med, 'indoor')

    def test_config_is_complete(self):
        self.assertEqual(CONF['realizations_per_position'], 200)
        self.assertEqual(CONF['rng']['RngRun'], 2)
        self.assertEqual(len(CONF['beams_runs']), 6)
        tags = {(r['array'], r['frequency_hz'], r['stratum']) for r in CONF['beams_runs']}
        self.assertEqual(len(tags), 6)
        for k in ('R1_previous_modes_unchanged', 'S1_bookkeeping', 'I1_reconstruction', 'I2_codebook_in_ns3',
                  'I3_eigen_in_ns3', 'I4_independent_recompute', 'D1_dominance', 'N1_normalisation',
                  'V1_nr_custom_beams'):
            self.assertIn(k, CONF['checks'])


if __name__ == '__main__':
    unittest.main()
