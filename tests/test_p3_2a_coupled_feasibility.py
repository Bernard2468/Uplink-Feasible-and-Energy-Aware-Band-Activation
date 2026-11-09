"""P3.2a engine on E1c.3's synthetic 7-site layout: all-on / serving-only bitwise equal to E1c.3 'sd' / 'sd_iso72';
masked states equal the independent scalar path; leave-one-out equals explicit masks; nesting, all-off, weights."""
import sys
import unittest
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'scripts'))
sys.path.insert(0, str(ROOT / 'tests'))
import p3_2a_coupled_feasibility as e  # noqa: E402
from test_e1c2_interference import model_context  # noqa: E402
from test_e1c3_directional import synthetic_layout  # noqa: E402

m, M2 = e.m, e.M2


def run_layout(seed_layout, r, n_cand=300):
    old = M2.N_CAND
    M2.N_CAND = n_cand
    try:
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
        out = []
        for cname in ('A_equal_elements', 'C_deployment'):
            case = cases[cname]
            Gc = M2.candidate_gains(cb, case, table)
            ui = M2.ul_interference(cb, Gc, cxs[cname], cand['usel'], S)
            cc = m.case_beam_context(cb, Gc, ui, case, cxs[cname], data, sxyz, syn, act_dl, act_ul)
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
                T = e.engine_terms(b, G, L, Llin, cxs[cname], va, cc)
                ref = m.evaluate_pb(b, G, L, Llin, cxs[cname], va, cc)
                out.append({'c': cname, 'st': st, 'T': T, 'ref': ref, 'cx': cxs[cname], 'case': case, 'table': table,
                            'data': data, 'mix': mix, 'seed': seed, 'isd': isd, 'r': r, 'S': S})
        return out
    finally:
        M2.N_CAND = old


def same(a, b):
    return (all(np.array_equal(a['dl'][fi], b['dl'][fi]) for fi in range(2))
            and all(np.array_equal(a['ul'][p][fi], b['ul'][p][fi]) for p in m.PROFILES for fi in range(2)))


class Engine(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.runs = run_layout(11, 0)
        cls.sparse = run_layout(13, 1, n_cand=12)       # some TRxPs without a candidate (synthetic DL targets)

    def test_all_on_and_serving_only_bitwise(self):
        for rec in self.runs + self.sparse:
            T3 = rec['T']['T3']
            self.assertTrue(same(e.engine_sinr(rec['T'], rec['cx'], np.ones(T3)), rec['ref']['sd']))
            self.assertTrue(same(e.engine_sinr(rec['T'], rec['cx'], np.zeros(T3), True), rec['ref']['sd_iso72']))

    def test_all_off_and_own_rule(self):
        for rec in self.runs:
            T, cx = rec['T'], rec['cx']
            off, on = e.engine_sinr(T, cx, np.zeros(T['T3'])), e.engine_sinr(T, cx, np.ones(T['T3']))
            self.assertTrue(np.all(np.isneginf(off['dl'][1])) and not np.any(off['own72']))
            self.assertTrue(np.array_equal(off['dl'][0], on['dl'][0]))
            w = np.ones(T['T3'])
            w[:3] = 0.0                                   # site 0 off
            res = e.engine_sinr(T, cx, w)
            served0 = T['tv'] < 3
            self.assertTrue(np.all(np.isneginf(res['dl'][1][served0])) and np.all(np.isfinite(res['dl'][1][~served0])))

    def test_weights_scale_interference(self):
        rec = self.runs[1]
        T, cx = rec['T'], rec['cx']
        half = e.engine_sinr(T, cx, np.full(T['T3'], 0.5))
        i_full = 10.0 ** (cx['p_dl'][1] / 10.0) * T['wf_dl'][1].sum(axis=1)
        expect = T['s_dl'][1] - 10.0 * np.log10(10.0 ** (cx['n_dl'][1] / 10.0) + 0.5 * i_full)
        self.assertLess(float(np.max(np.abs(half['dl'][1] - expect))), 1e-9)

    def test_nesting(self):
        rng = np.random.default_rng(3)
        for rec in self.runs:
            T, cx = rec['T'], rec['cx']
            U = rng.random(T['T3'])
            site0 = (np.arange(T['T3']) < 3).astype(float)
            prev = None
            for q in (0.0, 0.3, 0.6, 1.0):
                res = e.engine_sinr(T, cx, np.maximum((U < q).astype(float), site0), None, -3.0)
                if prev is not None:
                    o = prev['own72']
                    self.assertTrue(np.all(res['own72'][o]))
                    self.assertTrue(np.all(res['dl'][1][o] <= prev['dl'][1][o] + 1e-12))
                    for p in m.PROFILES:
                        self.assertTrue(np.all(res['ul'][p][1][o] <= prev['ul'][p][1][o] + 1e-12))
                prev = res

    def test_leave_one_out_equals_explicit(self):
        for rec in self.runs[:4] + self.sparse[:2]:
            T, cx = rec['T'], rec['cx']
            for dp in (0.0, -3.0):
                lf = e.loo_flags(T, cx, dp)
                for t in range(T['T3']):
                    w = np.ones(T['T3'])
                    w[t] = 0.0
                    res = e.engine_sinr(T, cx, w, None, dp)
                    for d, p in e.LOO_FLAGS:
                        v = res['dl'][1] if d == 'DL' else res['ul'][p][1]
                        self.assertTrue(np.array_equal(v >= cx['th'][1][(p, d)], lf[(d, p)][1][:, t]))
                on = e.engine_sinr(T, cx, np.ones(T['T3']), None, dp)
                for d, p in e.LOO_FLAGS:
                    v = on['dl'][1] if d == 'DL' else on['ul'][p][1]
                    self.assertTrue(np.array_equal(v >= cx['th'][1][(p, d)], lf[(d, p)][0]))

    def test_engine_equals_scalar(self):
        rng = np.random.default_rng(5)
        checked = 0
        old = M2.N_CAND
        try:
            for runs, n_cand in ((self.runs, 300), (self.sparse, 12)):
                M2.N_CAND = n_cand                      # the scalar path redraws the candidates with N_CAND
                checked += self._scalar_checks(runs, rng)
        finally:
            M2.N_CAND = old
        self.assertGreater(checked, 60)

    def _scalar_checks(self, runs, rng):
        checked = 0
        if True:
            caches = {}
            for rec in runs:
                T, cx = rec['T'], rec['cx']
                cache = caches.setdefault(rec['c'], {})
                for vi in range(0, T['n'], max(1, T['n'] // 4)):
                    for w, dp in ((np.ones(T['T3']), 0.0), ((rng.random(T['T3']) < 0.5).astype(float), -3.0),
                                  (np.r_[np.ones(3), np.zeros(T['T3'] - 3)], 0.0)):
                        if rec['c'] != 'C_deployment':
                            dp = 0.0
                        eng = e.engine_sinr(T, cx, w, None, dp)
                        sc = e.scalar_masked(rec['data'], rec['case'], rec['table'], cx, rec['seed'], rec['isd'], rec['r'],
                                             rec['st'], vi, rec['mix'], cache, np.zeros(3), w, dp)
                        self.assertEqual((sc['k'], sc['t']), (int(eng['k'][vi]), int(eng['t'][vi])))
                        for fi in range(2):
                            self.assertLessEqual(e.scalar_diff(sc['dl'][fi], float(eng['dl'][fi][vi])), 1e-9)
                            for p in m.PROFILES:
                                self.assertLessEqual(e.scalar_diff(sc['ul'][p][fi], float(eng['ul'][p][fi][vi])), 1e-9)
                        checked += 1
                    ref = m.scalar_pb(rec['data'], rec['case'], rec['table'], cx, rec['seed'], rec['isd'], rec['r'], rec['st'],
                                      vi, rec['mix'], cache, np.zeros(3))['sd']
                    one = e.scalar_masked(rec['data'], rec['case'], rec['table'], cx, rec['seed'], rec['isd'], rec['r'],
                                          rec['st'], vi, rec['mix'], cache, np.zeros(3), np.ones(T['T3']), 0.0)
                    for fi in range(2):
                        self.assertLessEqual(abs(one['dl'][fi] - ref['dl'][fi]), 1e-12)
                        for p in m.PROFILES:
                            self.assertLessEqual(abs(one['ul'][p][fi] - ref['ul'][p][fi]), 1e-12)
        return checked

    def test_scenario_states_and_rings(self):
        sites = {'site_i': np.array([0, 1, 0, -1, 2, -2]), 'site_j': np.array([0, 0, 1, 1, -1, 2])}
        self.assertEqual(e.site_rings(sites).tolist(), [0, 1, 1, 1, 2, 2])
        T3 = 18
        site_of = np.arange(T3) // 3
        st = e.scenario_states(T3, site_of, e.site_rings(sites)[site_of], np.linspace(0, 0.99, T3))
        self.assertEqual(st['site0_only'][0][:3].tolist(), [1, 1, 1])
        self.assertEqual(float(st['site0_only'][0][3:].sum()), 0.0)
        self.assertEqual(st['ring2_off'][0].tolist(), [1.0] * 12 + [0.0] * 6)
        self.assertTrue(np.all(st['q0.25'][0] <= st['q0.5'][0]) and np.all(st['q0.5'][0] <= st['q0.75'][0]))
        self.assertIs(st['serving_trxp_only'][1], True)


if __name__ == '__main__':
    unittest.main()
