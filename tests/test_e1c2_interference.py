"""E1c.2 engine: vectorized UMa / hE against pathloss_3gpp, lattice cells, histogram quantiles, classes,
and the full SINR pipeline against the independent scalar implementation on a synthetic layout."""
import json
import math
import sys
import unittest
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'scripts'))
import e1c2_interference as m  # noqa: E402
from cmwave.radio import pathloss_3gpp as pl3  # noqa: E402

FLOORS = [3.0 * (n - 1) + 1.5 for n in range(1, 9)]


def model_context():
    conf14 = json.loads(m.e155.E14_CONFIG.read_text(encoding='utf-8'))
    s153 = json.loads((m.e155.E153_DIR / 'summary.json').read_text())
    s153a = json.loads((m.e155.E153A_DIR / 'summary.json').read_text())
    s1521 = json.loads((m.e155.E1521_DIR / 'summary.json').read_text())
    s154 = json.loads((m.e155.E154_DIR / 'summary.json').read_text())
    maps = m.e155.build_mappings(s153a, s153)
    table = m.e155.gain_table(s1521)
    pkt_off = m.e155.packet_offsets(s154)
    sel_r = {d: v['factor'] for d, v in m.e155.selectivity_factors(s154).items()}
    cases = {c: conf14['cases'][c] for c in m.CASES}
    cxs = {c: m.case_context(cases[c], maps, pkt_off, sel_r) for c in m.CASES}
    return conf14, maps, table, pkt_off, sel_r, cases, cxs


class PathLossAndHe(unittest.TestCase):

    def test_he_components_match_scalar(self):
        rng = np.random.default_rng(3)
        d2 = rng.uniform(1.0, 700.0, 400)
        h = rng.choice(FLOORS, 400)
        d2 = np.r_[d2, 18.0, 18.0001, 50.0]
        h = np.r_[h, 22.5, 22.5, 13.5]
        vals, probs = m.he_components(d2, h)
        self.assertTrue(np.allclose(probs.sum(axis=-1), 1.0, atol=1e-15))
        for j in range(len(d2)):
            ref = pl3.uma_he_distribution(float(d2[j]), float(h[j])) if h[j] >= 13.0 else [(1.0, 1.0)]
            nz = probs[j] > 0
            self.assertEqual(int(nz.sum()), len(ref))
            for (he, p), v, q in zip(ref, vals[j][nz], probs[j][nz]):
                self.assertEqual(he, v)
                self.assertAlmostEqual(p, q, places=14)

    def test_uma_pl_matches_scalar(self):
        rng = np.random.default_rng(4)
        for _ in range(600):
            h = float(rng.choice(FLOORS))
            hb = float(rng.choice([25.0, 27.0, 31.4]))
            d2 = float(rng.uniform(0.5, 900.0))
            d3 = math.hypot(d2, hb - h)
            los = bool(rng.random() < 0.5)
            f = float(rng.choice([3.5e9, 7.2e9]))
            comps = pl3.uma_he_distribution(d2, h) if h >= 13.0 else [(1.0, 1.0)]
            for he, _ in comps:
                ref = m.s_pathloss(d2, d3, f, hb, h, he, los)
                vec = float(m.uma_pl(np.array(d2), np.array(d3), f, hb, h, np.array(he), np.array(los)))
                self.assertLess(abs(ref - vec), 1e-10)

    def test_he_index_matches_scalar_draw(self):
        rng = np.random.default_rng(5)
        d2 = rng.uniform(20.0, 600.0, 300)
        h = rng.choice(FLOORS, 300)
        u = rng.random(300)
        u[:5] = [0.0, 0.999999999999, 0.5, 1e-300, 0.25]
        vals, probs = m.he_components(d2, h)
        idx = m.he_index(u, probs)
        for j in range(300):
            self.assertEqual(vals[j, idx[j]], m.s_he(float(d2[j]), float(h[j]), float(u[j])))
            self.assertGreater(probs[j, idx[j]], 0.0)

    def test_he_frequencies(self):
        vals, probs = m.he_components(np.array([150.0]), np.array([22.5]))
        u = np.random.default_rng(6).random(400000)
        idx = m.he_index(u, np.repeat(probs, len(u), axis=0))
        freq = np.bincount(idx, minlength=m.N_HE) / len(u)
        self.assertTrue(np.all(np.abs(freq - probs[0]) < 5 * np.sqrt(probs[0] * (1 - probs[0]) / len(u)) + 1e-12))


class LatticeAndHelpers(unittest.TestCase):

    def test_lattice_rings(self):
        pts, ring = m.lattice_points(np.array([10.0, -5.0]), 200.0, 2)
        self.assertEqual(len(pts), 19)
        self.assertEqual(np.bincount(ring).tolist(), [1, 6, 12])
        self.assertEqual(m.nearest_ring(pts, np.array([10.0, -5.0]), 200.0).tolist(), ring.tolist())

    def test_cell_boundary_and_area(self):
        o = np.array([0.0, 0.0])
        mid = np.array([[100.0, 0.0], [99.9, 0.0], [100.1, 0.0]])
        self.assertEqual(m.nearest_ring(mid, o, 200.0).tolist(), [0, 0, 1])
        rng = np.random.default_rng(7)
        xy = rng.uniform(-300, 300, (400000, 2))
        frac = float(np.mean(m.nearest_ring(xy, o, 200.0) == 0))
        hexa = math.sqrt(3) / 2 * 200.0 ** 2
        self.assertAlmostEqual(frac, hexa / 600.0 ** 2, delta=0.002)

    def test_target_power(self):
        p = m.ul_target_power(8.0, -100.0, np.array([90.0, 150.0]), np.array([20.0, 20.0]), 23.0)
        self.assertEqual(p.tolist(), [-22.0, 23.0])
        self.assertEqual(float(m.ul_target_power(np.inf, -100.0, 90.0, 20.0, 23.0)), 23.0)

    def test_hist_quantiles(self):
        h = np.zeros(m.NB, dtype=np.int64)
        v = np.random.default_rng(8).normal(10.0, 5.0, 200000)
        m.hist_add(h, v)
        q = m.hist_quantiles(h)
        ref = np.quantile(v, [0.05, 0.5, 0.95])
        self.assertTrue(np.all(np.abs(np.array(q) - ref) < m.HIST[2]))

    def test_class_flags_partition(self):
        _, _, _, _, _, _, cxs = model_context()
        cx = cxs['A_equal_elements']
        rng = np.random.default_rng(9)
        res = {'dl': [rng.uniform(-10, 30, 5000) for _ in range(2)],
               'ul': {p: [rng.uniform(-15, 20, 5000) for _ in range(2)] for p in m.PROFILES}}
        fl = m.class_flags(res, cx)
        for p in m.PROFILES:
            tot = sum(fl[p][n].astype(int) for n in m.PARTITION)
            self.assertTrue(np.all(tot == 1))
            d35, u35, d72, u72 = (fl[p]['DL_3.5GHz'], fl[p]['UL_3.5GHz'], fl[p]['DL_7.2GHz'], fl[p]['UL_7.2GHz'])
            self.assertTrue(np.array_equal(fl[p]['K3_7p2_dl_without_any_ul'], d72 & ~u72 & ~u35))
            self.assertTrue(np.array_equal(fl[p][m.e14.K6], d72 & u72 & ~(d35 & u35)))
            self.assertTrue(np.array_equal(d72, res['dl'][1] >= cx['th'][1][(p, 'DL')]))


def synthetic_data(rng, isd=200.0):
    """7 sites (ring 0-1), random outdoor points and indoor samples, random LOS / facade / d2D-in."""
    origin = np.array([0.0, 0.0])
    pts, ring = m.lattice_points(origin, isd, 1)
    pts = pts + np.r_[[0.0, 0.0], rng.uniform(-20, 20, 2 * (len(pts) - 1))].reshape(-1, 2)
    S = len(pts)
    sites = {'site_x': pts[:, 0], 'site_y': pts[:, 1], 'site_h': np.r_[27.0, np.full(S - 1, 25.0)]}
    no, ni = 300, 500
    oxy = rng.uniform(-300, 300, (no, 2))
    ixy = rng.uniform(-300, 300, (ni, 2))
    mask = {'x': oxy[:, 0], 'y': oxy[:, 1], 'z': np.full(no, 1.5)}
    geo = {'x': ixy[:, 0], 'y': ixy[:, 1], 'h_ut': rng.choice(FLOORS, ni)}
    sites.update({'los_out': rng.random((no, S)) < 0.3, 'facade_los': rng.random((ni, S)) < 0.2,
                  'din': rng.uniform(0.5, 30.0, (ni, S))})
    victims = {'out': np.arange(0, no, 3), 'ind': np.arange(0, ni, 4)}
    pools = {'out': np.arange(no), 'ind': np.arange(ni)}
    return {'mask': mask, 'geo': geo, 'sites': sites, 'pools': pools, 'victims': victims}


class PipelineAgainstScalar(unittest.TestCase):

    def test_engine_equals_scalar_on_synthetic_layout(self):
        conf14, maps, table, pkt_off, sel_r, cases, cxs = model_context()
        rng = np.random.default_rng(10)
        data = synthetic_data(rng)
        s = data['sites']
        sxyz = np.column_stack([s['site_x'], s['site_y'], s['site_h']])
        S = len(sxyz)
        freqs = cxs['A_equal_elements']['freqs']
        mix = conf14['mixes']['M2412']['weights']
        seed, isd, r = 99, 200, 0
        old = m.N_CAND
        m.N_CAND = 400
        try:
            cand = m.draw_candidates(m.rng_for(seed, isd, r, 0), len(data['pools']['out']), len(data['pools']['ind']),
                                     m.N_CAND, S, mix)
            cb = m.candidate_block(cand, data, sxyz, data['pools'], freqs)
            case = cases['A_equal_elements']
            Gc = m.candidate_gains(cb, case, table)
            ui = m.ul_interference(cb, Gc, cxs['A_equal_elements'], cand['usel'], S)
            self.assertGreater(int(np.sum(ui['sel'] >= 0)), 10)
            cache = {}
            checked = 0
            for si, st in enumerate(m.STATES):
                kind = 'out' if st in ('outdoor', 'in_car') else 'ind'
                idx = data['victims'][kind]
                if kind == 'out':
                    b = m.link_block(sxyz, data['mask']['x'][idx], data['mask']['y'][idx], data['mask']['z'][idx],
                                     s['los_out'][idx], np.zeros((len(idx), S)), freqs)
                else:
                    b = m.link_block(sxyz, data['geo']['x'][idx], data['geo']['y'][idx], data['geo']['h_ut'][idx],
                                     s['facade_los'][idx], s['din'][idx], freqs)
                G = m.case_gains(b, case, table, kind)
                pen, sig, sue = m.state_terms(st, b, freqs)
                z, u, x = m.draw_ue_terms(m.rng_for(seed, isd, r, 1 + si), b['n'], S)
                L, _ = m.losses(b, pen, sig, sue, z, u, x)
                res = m.evaluate_case(b, G, L, 10.0 ** (-L / 10.0), ui['I'], cxs['A_equal_elements'])
                for vi in range(0, b['n'], max(1, b['n'] // 6)):
                    ref = m.scalar_primary(data, case, table, cxs['A_equal_elements'], seed, isd, r, st, vi, mix,
                                           cache)
                    eng = res['primary']
                    self.assertEqual(ref['k'], int(eng['k'][vi]))
                    self.assertEqual(ref['t'], int(eng['t'][vi]))
                    for fi in range(2):
                        self.assertLess(abs(ref['dl'][fi] - eng['dl'][fi][vi]), 1e-9)
                        for p in m.PROFILES:
                            self.assertLess(abs(ref['ul'][p][fi] - eng['ul'][p][fi][vi]), 1e-9)
                    checked += 1
                # interference lowers SINR; the noise-only settings equal S - N
                prim, nm = res['primary'], res['noise_multi']
                for fi in range(2):
                    self.assertTrue(np.all(prim['dl'][fi] <= nm['dl'][fi] + 1e-12))
                    self.assertTrue(np.all(res['eta0p2']['dl'][fi] >= prim['dl'][fi] - 1e-12))
                    self.assertTrue(np.all(res['ul_full']['ul']['F2'][fi] <= prim['ul']['F2'][fi] + 1e-12))
                self.assertTrue(np.array_equal(res['iso72']['dl'][1], nm['dl'][1]))
                self.assertTrue(np.array_equal(res['iso72']['dl'][0], prim['dl'][0]))
                self.assertTrue(np.all(res['single_noise']['k'] == 0))
            self.assertGreater(checked, 20)
        finally:
            m.N_CAND = old

    def test_single_site_noise_limit_equals_e1c1_link_budget(self):
        conf14, maps, table, pkt_off, sel_r, cases, cxs = model_context()
        rng = np.random.default_rng(11)
        data = synthetic_data(rng)
        s = data['sites']
        sxyz = np.column_stack([s['site_x'], s['site_y'], s['site_h']])
        S = len(sxyz)
        freqs = cxs['A_equal_elements']['freqs']
        idx = data['victims']['out']
        mk = data['mask']
        d2 = np.hypot(mk['x'][idx] - sxyz[0, 0], mk['y'][idx] - sxyz[0, 1])
        mask = {'x': mk['x'][idx], 'y': mk['y'][idx], 'z': mk['z'][idx], 'los': s['los_out'][idx, 0].astype(np.uint8),
                'd2d': d2, 'd3d': np.hypot(d2, sxyz[0, 2] - mk['z'][idx])}
        rows = m.e1c.outdoor_rows(mask, sxyz[0], freqs)
        b = m.link_block(sxyz, mk['x'][idx], mk['y'][idx], mk['z'][idx], s['los_out'][idx], np.zeros((len(idx), S)),
                         freqs)
        for cname in m.CASES:
            G = m.case_gains(b, cases[cname], table, 'out')
            ev = m.e1c.evaluate(cases[cname], 'in_car', rows, m.VAR, maps, table, pkt_off, sel_r)
            pen, sig, sue = m.state_terms('in_car', b, freqs)
            n = b['n']
            L, _ = m.losses(b, pen, sig, sue, np.zeros((n, S)), np.zeros((n, S)), np.zeros(n))
            res = m.evaluate_case(b, G, L, 10.0 ** (-L / 10.0), None, cxs[cname],
                                  settings={'single_noise': m.SETTINGS['single_noise']})['single_noise']
            for fi in range(2):
                a = ev['aux'][fi]
                cfg = a['cfg']
                snr_dl = cfg.gnb_power_dbm + a['g_dl'] - a['L0'] - m.lb.noise_dbm(cfg.occupied_bw_hz, cfg.nf_ue_db)
                snr_ul = cfg.ue_power_dbm + a['g_ul'] - a['L0'] - m.lb.noise_dbm(cfg.occupied_bw_hz, cfg.nf_gnb_db)
                self.assertLess(float(np.max(np.abs(res['dl'][fi] - snr_dl))), 1e-9)
                self.assertLess(float(np.max(np.abs(res['ul']['F2'][fi] - snr_ul))), 1e-9)


if __name__ == '__main__':
    unittest.main()
