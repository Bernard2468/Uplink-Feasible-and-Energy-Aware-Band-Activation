"""E1c.3 engine: array pattern, specular calibration, synthetic targets, and the per-beam pipeline against
E1c.2 (a = 0) and against the independent scalar implementation on a synthetic layout."""
import math
import sys
import unittest
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'scripts'))
sys.path.insert(0, str(ROOT / 'tests'))
import e1c3_directional as m  # noqa: E402
from test_e1c2_interference import FLOORS, model_context  # noqa: E402

M2 = m.m2


class Pattern(unittest.TestCase):

    def test_closed_form_equals_phasor_sum(self):
        res = m.h2_check(np.random.default_rng(1), n=4000)
        self.assertTrue(res['passed'], res)

    def test_peak_mean_and_limit(self):
        for rows, cols in ((8, 16), (16, 32)):
            g = m.array_power_gain(np.array([70.0, 95.0]), np.array([10.0, -40.0]), np.array([70.0, 95.0]),
                                   np.array([10.0, -40.0]), 30.0, rows, cols)
            self.assertTrue(np.allclose(g, rows * cols, rtol=0, atol=1e-9))
        x = np.array([1e-12, -3e-10, 2 * np.pi])
        self.assertTrue(np.allclose(m.dirichlet_power(x, 16), 16.0, atol=1e-6))
        rng = np.random.default_rng(2)
        u = rng.uniform(-np.pi, np.pi, 400000)
        w = rng.uniform(-np.pi, np.pi, 400000)
        mean = float(np.mean(m.dirichlet_power(u, 8) * m.dirichlet_power(w, 16)))
        self.assertAlmostEqual(mean, 1.0, delta=0.05)

    def test_pattern_symmetry(self):
        rng = np.random.default_rng(3)
        ze, zs = rng.uniform(30, 150, 100), rng.uniform(30, 150, 100)
        ae, as_ = rng.uniform(-180, 180, 100), rng.uniform(-180, 180, 100)
        g1 = m.array_power_gain(ze, ae, zs, as_, 150.0, 8, 16)
        g2 = m.array_power_gain(zs, as_, ze, ae, 150.0, 8, 16)
        self.assertTrue(np.allclose(g1, g2, rtol=1e-12, atol=1e-12))


class Calibration(unittest.TestCase):

    def test_specular_fraction(self):
        for n in (128, 512):
            for d in (0.0, 0.6, 5.05, 6.9, 10.0):
                a = float(m.specular_fraction(n, d))
                self.assertAlmostEqual(10 * math.log10(a * n + 1 - a), 10 * math.log10(n) - d, places=10)
                self.assertTrue(0.0 <= a <= 1.0)
        with self.assertRaises(ValueError):
            m.specular_fraction(128, -1.0)
        with self.assertRaises(ValueError):
            m.specular_fraction(128, 22.0)

    def test_h3_on_real_table(self):
        _, _, table, _, _, cases, _ = model_context()
        res = m.h3_check(cases, table)
        self.assertTrue(res['passed'], res)

    def test_activity(self):
        uni = np.array([0.1, 0.4, 0.6, 0.99])
        self.assertEqual(m.activity({'act': 'bern', 'eta': 0.5}, uni).tolist(), [1.0, 1.0, 0.0, 0.0])
        self.assertEqual(m.activity({'act': 'mean', 'eta': 0.5}, uni).tolist(), [0.5] * 4)
        self.assertEqual(m.activity({'act': 'bern', 'eta': 1.0}, uni).tolist(), [1.0] * 4)


def synthetic_layout(rng, isd=200.0):
    ij = [(0, 0), (1, 0), (0, 1), (-1, 1), (-1, 0), (0, -1), (1, -1)]
    a1, a2 = np.array([isd, 0.0]), np.array([isd / 2, isd * math.sqrt(3) / 2])
    pts = np.array([i * a1 + j * a2 for i, j in ij]) + np.r_[[0.0, 0.0], rng.uniform(-20, 20, 12)].reshape(-1, 2)
    S = len(pts)
    sites = {'site_x': pts[:, 0], 'site_y': pts[:, 1], 'site_h': np.r_[27.0, np.full(S - 1, 25.0)],
             'site_i': np.array([p[0] for p in ij]), 'site_j': np.array([p[1] for p in ij])}
    no, ni = 300, 500
    oxy, ixy = rng.uniform(-300, 300, (no, 2)), rng.uniform(-300, 300, (ni, 2))
    mask = {'x': oxy[:, 0], 'y': oxy[:, 1], 'z': np.full(no, 1.5)}
    geo = {'x': ixy[:, 0], 'y': ixy[:, 1], 'h_ut': rng.choice(FLOORS, ni)}
    sites.update({'los_out': rng.random((no, S)) < 0.3, 'facade_los': rng.random((ni, S)) < 0.2,
                  'din': rng.uniform(0.5, 30.0, (ni, S))})
    return {'mask': mask, 'geo': geo, 'sites': sites, 'pools': {'out': np.arange(no), 'ind': np.arange(ni)},
            'victims': {'out': np.arange(0, no, 3), 'ind': np.arange(0, ni, 4)}}


class SyntheticTargets(unittest.TestCase):

    def test_inside_cell_and_sector(self):
        rng = np.random.default_rng(4)
        data = synthetic_layout(rng)
        s = data['sites']
        tg, fb = m.synthetic_targets(np.random.default_rng(5), s, np.zeros(2), 200.0)
        self.assertEqual(fb, 0)
        sxyz = np.column_stack([s['site_x'], s['site_y'], s['site_h']])
        for t in range(len(tg)):
            k, sct = divmod(t, m.N_SECT)
            ring = M2.nearest_ring(tg[t:t + 1, :2], np.zeros(2) + 0.0, 200.0, rings=4)
            c = s['site_i'][k] * np.array([200.0, 0]) + s['site_j'][k] * np.array([100.0, 100.0 * math.sqrt(3)])
            self.assertLessEqual(math.hypot(*(tg[t, :2] - c)), 200.0 / math.sqrt(3) + 1e-9)
            self.assertEqual(int(ring[0]), max(abs(s['site_i'][k]), abs(s['site_j'][k]), abs(s['site_i'][k] + s['site_j'][k])))
            zen, az = m.ant.link_angles(sxyz[k], tg[t])
            e = m.ant.element_gain_dbi(zen[:, None], az[:, None] - m.BEARINGS)
            self.assertEqual(int(np.argmax(e, axis=1)[0]), sct)


class Pipeline(unittest.TestCase):

    def run_layout(self, seed_layout, r):
        conf14, maps, table, pkt_off, sel_r, cases, cxs = model_context()
        rng = np.random.default_rng(seed_layout)
        data = synthetic_layout(rng)
        s = data['sites']
        sxyz = np.column_stack([s['site_x'], s['site_y'], s['site_h']])
        S = len(sxyz)
        freqs = cxs['A_equal_elements']['freqs']
        mix = conf14['mixes']['M2412']['weights']
        seed, isd = 77, 200
        cand = M2.draw_candidates(M2.rng_for(seed, isd, r, 0), 300, 500, M2.N_CAND, S, mix)
        cb = M2.candidate_block(cand, data, sxyz, data['pools'], freqs)
        cb['Elin'], cb['Llin'] = 10.0 ** (cb['E'] / 10.0), 10.0 ** (-cb['L'] / 10.0)
        act_dl, act_ul = m.draw_activity(M2.rng_for(seed, isd, r, m.STREAM_ACT), m.N_SECT * S)
        syn, _ = m.synthetic_targets(M2.rng_for(seed, isd, r, m.STREAM_SYN), s, np.zeros(3), isd)
        out = {}
        for cname in m.CASES:
            case = cases[cname]
            Gc = M2.candidate_gains(cb, case, table)
            ui = M2.ul_interference(cb, Gc, cxs[cname], cand['usel'], S)
            cc = m.case_beam_context(cb, Gc, ui, case, cxs[cname], data, sxyz, syn, act_dl, act_ul)
            out[('n_synthetic', cname)] = cc['n_synthetic']
            for si, st in enumerate(m.STATES):
                kind = 'out' if st in ('outdoor', 'in_car') else 'ind'
                idx = data['victims'][kind]
                src = data['mask'] if kind == 'out' else data['geo']
                b = M2.link_block(sxyz, src['x'][idx], src['y'][idx], src['z' if kind == 'out' else 'h_ut'][idx],
                                  s['los_out' if kind == 'out' else 'facade_los'][idx],
                                  np.zeros((len(idx), S)) if kind == 'out' else s['din'][idx], freqs)
                b['Elin'] = 10.0 ** (b['E'] / 10.0)
                G = M2.case_gains(b, case, table, kind)
                va = [m.specular_from_gains(b['E'], G[fi]['ul'], r_ * c_) for fi, (r_, c_) in enumerate(m.case_dims(case))]
                pen, sig, sue = M2.state_terms(st, b, freqs)
                z, u, x = M2.draw_ue_terms(M2.rng_for(seed, isd, r, 1 + si), b['n'], S)
                L, _ = M2.losses(b, pen, sig, sue, z, u, x)
                Llin = 10.0 ** (-L / 10.0)
                ref = M2.evaluate_case(b, G, L, Llin, ui['I'], cxs[cname])
                pb = m.evaluate_pb(b, G, L, Llin, cxs[cname], va, cc)
                out[(cname, st)] = (ref, pb, data, case, cxs[cname], table, mix, seed, isd)
        return out

    def test_avg_limit_equals_e1c2(self):
        old = M2.N_CAND
        M2.N_CAND = 300
        try:
            out = self.run_layout(11, 0)
        finally:
            M2.N_CAND = old
        for key, val in out.items():
            if key[0] == 'n_synthetic':
                continue
            ref, pb = val[0], val[1]
            for fi in range(2):
                self.assertLess(float(np.max(np.abs(pb['avg_pb']['dl'][fi] - ref['primary']['dl'][fi]))), 1e-9)
                for p in m.PROFILES:
                    self.assertLess(float(np.max(np.abs(pb['avg_pb']['ul'][p][fi] - ref['primary']['ul'][p][fi]))), 1e-9)
                # mean activity at eta 1 and Bernoulli at eta 1 coincide; iso72 removes 7.2 GHz interference
                self.assertTrue(np.array_equal(pb['sd_iso72']['dl'][1], ref['noise_multi']['dl'][1]))
                self.assertTrue(np.all(pb['sd_stress']['dl'][fi] <= pb['sd']['dl'][fi] + 1e-12))
                self.assertTrue(np.all(pb['sd_ul_off']['ul']['F2'][fi] >= pb['sd']['ul']['F2'][fi] - 1e-12))

    def test_engine_equals_scalar(self):
        self.check_scalar(300, 12, expect_synthetic=False)

    def test_engine_equals_scalar_sparse_candidates(self):
        """Few candidates: some TRxPs serve none and point their DL beam at a synthetic target."""
        self.check_scalar(12, 13, expect_synthetic=True)

    def check_scalar(self, n_cand, layout_seed, expect_synthetic):
        old = M2.N_CAND
        M2.N_CAND = n_cand
        try:
            out = self.run_layout(layout_seed, 1)
            if expect_synthetic:
                self.assertGreater(out[('n_synthetic', 'A_equal_elements')], 0)
            cache, checked = {}, 0
            for key, val in out.items():
                if key[0] != 'A_equal_elements':
                    continue
                cname, st = key
                ref, pb, data, case, cx, table, mix, seed, isd = val
                n = len(pb['sd']['dl'][0])
                for vi in range(0, n, max(1, n // 5)):
                    sc = m.scalar_pb(data, case, table, cx, seed, isd, 1, st, vi, mix, cache, np.zeros(3))
                    for sn in ('sd', 'sd_stress'):
                        self.assertEqual(sc['k'], int(pb[sn]['k'][vi]))
                        for fi in range(2):
                            self.assertLess(abs(sc[sn]['dl'][fi] - pb[sn]['dl'][fi][vi]), 1e-9)
                            for p in m.PROFILES:
                                self.assertLess(abs(sc[sn]['ul'][p][fi] - pb[sn]['ul'][p][fi][vi]), 1e-9)
                    checked += 1
            self.assertGreater(checked, 15)
        finally:
            M2.N_CAND = old


if __name__ == '__main__':
    unittest.main()
