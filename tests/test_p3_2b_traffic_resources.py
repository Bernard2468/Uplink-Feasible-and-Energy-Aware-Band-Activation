"""P3.2b traffic and resource layer on E1c.3's synthetic 7-site layout and on analytic toy groups: engine extension
bitwise equal to P3.2a, sector grouping equal to the explicit engine, rates and constants, load-coupled fixed point
(convergence, uniqueness, monotonicity, closure), calibration, bisection, association rules, independent reference."""
import copy
import json
import sys
import unittest
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'scripts'))
sys.path.insert(0, str(ROOT / 'tests'))
import p3_2b_traffic_resources as e  # noqa: E402
from test_e1c2_interference import model_context  # noqa: E402
from test_e1c3_directional import synthetic_layout  # noqa: E402

A, m, M2 = e.A, e.m, e.M2
N3 = e.N_SECT


def layout_terms(seed_layout, r, cnames=('A_equal_elements', 'C_deployment'), n_cand=300):
    """Engine terms of the synthetic layout for realization r (P3.2a test construction without the reference)."""
    old = M2.N_CAND
    M2.N_CAND = n_cand
    try:
        conf14, maps, table, pkt_off, sel_r, cases, cxs = model_context()
        data = synthetic_layout(np.random.default_rng(seed_layout))
        s = data['sites']
        sxyz = np.column_stack([s['site_x'], s['site_y'], s['site_h']])
        S = len(sxyz)
        freqs = cxs['A_equal_elements']['freqs']
        mix = conf14['mixes']['M2412']['weights']
        seed, isd = 77, 200
        cand = M2.draw_candidates(M2.rng_for(seed, isd, r, 0), 300, 500, M2.N_CAND, S, mix)
        cb = M2.candidate_block(cand, data, sxyz, data['pools'], freqs)
        cb['Elin'], cb['Llin'] = 10.0 ** (cb['E'] / 10.0), 10.0 ** (-cb['L'] / 10.0)
        act_dl, act_ul = m.draw_activity(M2.rng_for(seed, isd, r, m.STREAM_ACT), N3 * S)
        syn, _ = m.synthetic_targets(M2.rng_for(seed, isd, r, m.STREAM_SYN), s, np.zeros(3), isd)
        out = {}
        for cname in cnames:
            case = cases[cname]
            Gc = M2.candidate_gains(cb, case, table)
            ui = M2.ul_interference(cb, Gc, cxs[cname], cand['usel'], S)
            cc = m.case_beam_context(cb, Gc, ui, case, cxs[cname], data, sxyz, syn, act_dl, act_ul)
            per = {}
            for st in e.MIX_STATES:
                si = m.STATES.index(st)
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
                T = A.engine_terms(b, G, L, 10.0 ** (-L / 10.0), cxs[cname], va, cc)
                per[st] = (T, mix[st] / b['n'])
            out[cname] = {'per': per, 'cx': cxs[cname], 'case': case}
        return out
    finally:
        M2.N_CAND = old


def toy_group(s_db, w, cells, R, G=None, noise=1.0, cap=1.0, se=None, d='DL', act=(1.0, 1.0, 1.0), rescue=False,
              g_adm=-1e9):
    n = len(s_db)
    se = se or (lambda x, dd: np.log2(1.0 + 10.0 ** (np.asarray(x) / 10.0)))
    act = np.asarray(act, dtype=float)
    cells = np.asarray(cells)
    return {'r': cells // N3, 'cell': cells, 'K': R * N3, 'R': R, 'idx': np.arange(n), 's': np.asarray(s_db, dtype=float),
            'G': np.zeros((n, N3)) if G is None else np.asarray(G, dtype=float), 'w': np.asarray(w, dtype=float),
            'noise': noise, 'act': act, 'cell_on': np.tile(act > 0, R), 'd': 0.0, 'dir': d, 'cap': cap, 'se': se,
            'g_adm': g_adm, 'rescue': rescue}


class Synthetic(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.runs = [layout_terms(11, r) for r in (0, 1)]
        conf14, maps, table, pkt_off, sel_r, cases, cxs = model_context()
        cls.cases = cases
        parts = {c: [] for c in cls.runs[0]}
        for r, run in enumerate(cls.runs):
            for c, rec in run.items():
                for st in e.MIX_STATES:
                    T, w = rec['per'][st]
                    parts[c].append((r, w, e.group_terms(T, rec['cx'])))
        cls.U = {c: e.population(p, 2) for c, p in parts.items()}
        cls.ctx = {c: e.Ctx(cls.runs[0][c]['cx'], cases[c]['carriers']) for c in cls.U}

    def test_extension_bitwise(self):
        for run in self.runs:
            for c, rec in run.items():
                for T, _ in rec['per'].values():
                    for pat in ((1, 1, 1), (0, 0, 0), (1, 0, 1)):
                        w72 = e.periodic(pat, T['T3'])
                        for dp in ((0.0, -3.0) if c == 'C_deployment' else (0.0,)):
                            ref = A.engine_sinr(T, rec['cx'], w72, None, dp)
                            ext = e.engine_sinr_lc(T, rec['cx'], w72, (None, w72), (None, w72), dp)
                            for fi in range(2):
                                self.assertTrue(np.array_equal(ext['dl'][fi], ref['dl'][fi]))
                                for p in e.PROFILES:
                                    self.assertTrue(np.array_equal(ext['ul'][p][fi], ref['ul'][p][fi]))

    def test_grouping_equals_engine(self):
        rng = np.random.default_rng(4)
        for c, rec in self.runs[0].items():
            cx = rec['cx']
            for T, _ in rec['per'].values():
                grp = e.group_terms(T, cx)
                for pat in ((1, 1, 1), (1, 0, 0), (0, 1, 1)):
                    pat = np.asarray(pat, dtype=float)
                    Ld, Lu = rng.random((2, N3)), rng.random((2, N3))
                    dp = -3.0 if c == 'C_deployment' else 0.0
                    wd, wu = (Ld[0], pat * Ld[1]), (Lu[0], pat * Lu[1])
                    ext = e.engine_sinr_lc(T, cx, e.periodic(pat, T['T3']),
                                           tuple(e.periodic(v, T['T3']) for v in wd), tuple(e.periodic(v, T['T3']) for v in wu), dp)
                    own = pat[grp['sec']] > 0
                    for fi in range(2):
                        gd = e.grouped_sinr(grp['dl'][fi]['s'], 10.0 ** (cx['n_dl'][fi] / 10.0), grp['dl'][fi]['G'], list(wd[fi]),
                                            0.0 if fi == 0 else dp)
                        gu = e.grouped_sinr(grp['ul'][fi]['s'], 10.0 ** (cx['n_ul'][fi]['F2'] / 10.0), grp['ul'][fi]['G'],
                                            list(wu[fi]), 0.0)
                        msk = np.ones(T['n'], dtype=bool) if fi == 0 else own
                        self.assertLess(float(np.max(np.abs(gd[msk] - ext['dl'][fi][msk]))), 1e-9)
                        self.assertLess(float(np.max(np.abs(gu[msk] - ext['ul']['F2'][fi][msk]))), 1e-9)

    def test_constants_and_admission(self):
        self.assertAlmostEqual(e.cap_factor(273, 'DL'), 273 * 12 * 30e3 * 52 / 70 * 0.86, places=6)
        self.assertAlmostEqual(e.cap_factor(546, 'UL'), 546 * 12 * 30e3 * 16 / 70 * 0.92, places=6)
        self.assertAlmostEqual(e.required_rate(100e6, 'DL'), 0.225 * 100e6 * 52 / 70, places=6)
        self.assertAlmostEqual(e.required_rate(100e6, 'UL'), 0.15 * 100e6 * 16 / 70, places=6)
        reg = json.loads(e.CONFIG.read_text(encoding='utf-8'))['amendment_1']['admission_points']
        for ctx in self.ctx.values():       # amendment 1: the registered points, exactly
            for d in e.DIRS:
                self.assertEqual(ctx.g_adm[d], reg[d]['snr_db'])
                self.assertEqual(ctx.se_adm[d], reg[d]['se'])
        ctx = self.ctx['C_deployment']
        self.assertEqual(ctx.bw_op35, 100e6)
        self.assertEqual(ctx.g_adm['UL'], ctx.floor['UL'])                    # the UL snr_min is on the plateau
        self.assertGreater(ctx.g_adm['DL'], ctx.floor['DL'])                  # the DL snr_min point is the BLER floor
        self.assertLess(float(ctx.se(np.array([ctx.floor['DL']]), 'DL')[0]), e.PLATEAU_MIN_SE)
        for d in e.DIRS:
            self.assertEqual(float(ctx.se(np.array([ctx.g_adm[d]]), d)[0]), ctx.se_adm[d])
            self.assertGreaterEqual(ctx.se_adm[d], e.PLATEAU_MIN_SE)
            below = np.linspace(ctx.floor[d] - 3.0, ctx.g_adm[d] - 1e-9, 50)
            self.assertTrue(np.all(ctx.se(below, d) < ctx.se_adm[d]))
            above = np.linspace(ctx.g_adm[d], ctx.g_adm[d] + 2.0, 50)
            self.assertTrue(np.all(e.admitted(above, ctx.se, d, ctx.se_adm[d], ctx.g_adm[d], 273, False)))
            self.assertFalse(np.any(e.admitted(below, ctx.se, d, ctx.se_adm[d], ctx.g_adm[d], 273, False)))

    def test_rescue_rates(self):
        ctx = self.ctx['C_deployment']
        d, ga = 'UL', ctx.g_adm['UL']
        g = np.array([ga - 6.0, ga - 1.0, ga, ga + 3.0])
        full = ctx.se(g, d)
        r = e.se_rate(g, ctx.se, d, ga, True)
        ld = e.se_load(g, ctx.se, d, ga, True)
        self.assertTrue(np.allclose(r[2:], full[2:], rtol=0, atol=1e-15))
        self.assertTrue(np.allclose(r[:2], ctx.se_adm[d] * 10.0 ** ((g[:2] - ga) / 10.0), rtol=1e-14))
        self.assertTrue(np.all(ld[:3] == ctx.se_adm[d]))
        self.assertTrue(np.array_equal(e.admitted(g, ctx.se, d, ctx.se_adm[d], ga, 546, True),
                                       g + 10.0 * np.log10(546) >= ga))

    def test_noise_limited_closed_form(self):
        se = lambda x, dd: np.log2(1.0 + 10.0 ** (np.asarray(x) / 10.0))  # noqa: E731
        s_db = np.array([10.0, 3.0, 20.0, 7.0, 15.0, 1.0])
        w = np.array([0.1, 0.2, 0.15, 0.25, 0.2, 0.1])
        cells = np.array([0, 0, 1, 2, 4, 5])
        g = toy_group(s_db, w, cells, 2, cap=10.0, se=se)
        traffic = np.array([0.0, 5.0, 20.0, 400.0])
        sol = e.solve(g, traffic)
        expect = traffic[:, None] * np.bincount(cells, weights=w / se(s_db, 'DL'), minlength=6)[None, :] / 10.0
        self.assertTrue(np.allclose(sol['L'], np.minimum(1.0, expect), rtol=1e-13, atol=0))
        self.assertTrue(np.allclose(sol['rho'], expect, rtol=1e-13, atol=0))
        fl = e.flows_at(g, sol, 1, 5.0, 1.0)
        C = 10.0 * se(s_db, 'DL')
        self.assertTrue(np.allclose(fl['theta'], C * (1.0 - expect[1][cells]), rtol=1e-13))
        fl3 = e.flows_at(g, sol, 3, 400.0, 1.0)            # saturated cells: throughput 0, served = offered / rho
        sat = expect[3][cells] >= 1.0
        self.assertTrue(np.all(fl3['theta'][sat] == 0.0))
        self.assertTrue(np.allclose(fl3['served'][sat], 400.0 * w[sat] / expect[3][cells][sat], rtol=1e-13))

    def test_bisection_known_answer(self):
        se = lambda x, dd: np.full(np.shape(x), 2.0)     # noqa: E731  constant SE: L = T sum w / (cap 2)
        g = toy_group(np.zeros(4), np.full(4, 0.25), np.array([0, 0, 0, 0]), 1, cap=1.0, se=se)
        U = {'r': np.zeros(4, dtype=np.int64), 'w': np.full(4, 0.25), 'R': 1}
        st = {'car': np.zeros(4, dtype=int), 'groups': {0: g}, 'dir': 'DL'}

        class C_:
            req = {'DL': 1.0, 'UL': 1.0}
        # theta = 2 (1 - T / 2) >= 1  <=>  T <= 1
        res = e.bisect_traffic(U, C_, st, 1.0, hi=4.0, tol=1e-6)
        self.assertEqual(res['status'], 'bracketed')
        self.assertLess(abs(res['x_star'] - 1.0), 2e-6)
        self.assertLessEqual(res['x_star'], 1.0)

    def test_fixed_point_properties(self):
        for c in self.U:
            U, ctx = self.U[c], self.ctx[c]
            T50 = e.calibrate(U, ctx, 0.5)
            self.assertLess(abs(T50['residual']), 1e-9)
            g0 = e.build_state(U, ctx, 'DL', 'off', np.zeros(N3), 0.0, False)['groups'][0]
            self.assertLess(abs(float(e.solve(g0, [T50['T_bps']])['L'].mean()) - 0.5), 1e-9)
            tr = T50['T_bps'] * np.array([0.25, 1.0, 2.0, 4.0])
            for d, rule in (('DL', 'off'), ('DL', 'on'), ('UL', 'off'), ('UL', 'on_ulca')):
                for rescue in ((False, True) if d == 'UL' else (False,)):
                    a72 = np.zeros(N3) if rule == 'off' else np.ones(N3)
                    st = e.build_state(U, ctx, d, rule, a72, -3.0 if c == 'C_deployment' else 0.0, rescue)
                    traffic = tr * (0.3 if d == 'UL' else 1.0)
                    for fi, g in st['groups'].items():
                        hi_, lo_ = e.solve(g, traffic), e.solve(g, traffic, start=0.0)
                        self.assertTrue(hi_['converged'] and lo_['converged'])
                        self.assertLess(hi_['res'], 1e-9)
                        self.assertLess(float(np.max(np.abs(hi_['L'] - lo_['L']))), 1e-8)
                        self.assertTrue(np.all(np.diff(hi_['L'], axis=0) >= -1e-12))
                        self.assertTrue(np.all((hi_['L'] >= 0) & (hi_['L'] <= 1)))
                        for b, tb in enumerate(traffic):
                            fl = e.flows_at(g, hi_, b, tb, ctx.req[d])
                            self.assertTrue(np.allclose(fl['served'] + fl['unserved'], fl['offered'], rtol=1e-12, atol=0))
                            on = g['cell_on']
                            self.assertLess(float(np.max(np.abs(fl['occ_from_served'][on] - hi_['L'][b][on]))), 1e-9)
                    ev = e.eval_state(U, ctx, st, traffic)
                    outs = [a['outage_admitted_pooled'] for a in ev['agg']]
                    self.assertTrue(np.all(np.diff(outs) >= -1e-12))

    def test_assignment_rules(self):
        for c in self.U:
            U, ctx = self.U[c], self.ctx[c]
            off = e.assign(U, ctx, 'UL', np.zeros(N3), 0.0, 'off', False)
            sc = e.assign(U, ctx, 'UL', np.ones(N3), 0.0, 'on_scell_dl', False)
            self.assertTrue(np.array_equal(off, sc))
            self.assertFalse(np.any(off == 1))
            on = e.assign(U, ctx, 'DL', np.array([1.0, 0.0, 1.0]), 0.0, 'on', False)
            self.assertFalse(np.any((on == 1) & (U['sec'] == 1)))
            gam0 = e.fb_sinr(U, ctx, 'DL', 0, np.ones(N3), 0.0)
            gam1 = e.fb_sinr(U, ctx, 'DL', 1, np.array([1.0, 0.0, 1.0]), 0.0)
            C0 = ctx.cap['DL'][0] * ctx.se(gam0, 'DL') * (ctx.se(gam0, 'DL') >= ctx.se_adm['DL'])
            C1 = ctx.cap['DL'][1] * ctx.se(gam1, 'DL') * (ctx.se(gam1, 'DL') >= ctx.se_adm['DL'])
            to72 = (U['sec'] != 1) & (C1 > 0) & (C1 >= C0)
            self.assertTrue(np.array_equal(on == 1, to72))
            # 3.5 GHz DL load with 7.2 on is not larger than all off
            T = e.calibrate(U, ctx, 0.5)['T_bps']
            l_off = e.solve(e.build_state(U, ctx, 'DL', 'off', np.zeros(N3), 0.0, False)['groups'][0], [T])['L']
            l_on = e.solve(e.build_state(U, ctx, 'DL', 'on', np.ones(N3), 0.0, False)['groups'][0], [T])['L']
            self.assertTrue(np.all(l_on <= l_off + 1e-12))

    def test_reference_check(self):
        c = 'C_deployment'
        U, ctx = self.U[c], self.ctx[c]
        T = e.calibrate(U, ctx, 0.5)['T_bps']
        Tr = {st: self.runs[0][c]['per'][st] for st in e.MIX_STATES}
        for d, rule, rescue, traffic in (('DL', 'off', False, T), ('DL', 'on', False, 3.0 * T),
                                          ('UL', 'on_ulca', False, 0.4 * T), ('UL', 'on_ulca', True, 1.2 * T)):
            a72 = np.zeros(N3) if rule == 'off' else np.ones(N3)
            st = e.build_state(U, ctx, d, rule, a72, -3.0 if d == 'DL' else 0.0, rescue)
            ev = e.eval_state(U, ctx, st, [traffic], quantiles=False, keep_gam=True)
            self.assertIsNone(e.eval_state(U, ctx, st, [traffic], quantiles=False)['sols'][0]['gam'])
            res = e.reference_check(Tr, self.runs[0][c]['cx'], ctx, U, st, ev['sols'], traffic)
            self.assertGreater(res['users_checked'], 0)
            self.assertLess(res['max_rel_diff_offered_load'], 1e-9)
            self.assertEqual(res['outage_mismatches'], 0)

    def test_weighted_quantiles(self):
        v = np.array([3.0, 1.0, 2.0, 4.0])
        w = np.array([0.1, 0.4, 0.4, 0.1])
        self.assertEqual(e.weighted_quantiles(v, w, (0.05, 0.5, 0.95)), [1.0, 2.0, 4.0])


class AmendmentGate(unittest.TestCase):
    """Gate B9's comparator on a minimal summary: passes when only UL quantities move as the amendment implies,
    fails on any change that the UL admission point cannot cause."""

    @staticmethod
    def pair():
        h = {'DL': {'not_admitted_share': 0.0, 'share_between_snr_min_and_admission': 0.0, 'share_in_tr36942_low_end': 0.0},
             'UL': {'not_admitted_share': 0.3, 'share_between_snr_min_and_admission': 0.1, 'share_in_tr36942_low_end': 0.15}}
        cr = {'constants': {'n_prb': [273, 546], 'se_admission': {'DL': 0.19695, 'UL': 0.19716},
                            'snr_admission_db': {'DL': -0.6387, 'UL': 0.3613}, 'snr_min_db': {'DL': -1.6387, 'UL': -0.6387}},
              'n_users_total': 10, 'calibration': {'T_bps_per_site': {'0.3': 1.0, '0.5': 2.0}, 'x_medium_over_busy': 0.5},
              'grid_x': [0.5, 1.0], 'grid_y_ul': [0.1], 'hazard15_labels_3.5GHz_full_buffer': h,
              'DL': {'0.0|off': {'x_star': {'x_star': 1.5}, 'grid': [{'x': 0.5, 'outage_admitted_pooled': 0.0}]}},
              'UL': {'off': {'y_star': {'x_star': 0.3}}},
              'sustainable_traffic': {'0.0|off+off|ericsson_typical': {'x_star_DL': 1.5, 'x_star_UL': 2.7, 'x_star_both': 1.5,
                                                                       'binding': 'DL'}}}
        s1 = {'isd': {'200': {'victims': {'outdoor_points': 3, 'indoor_samples': 4}, 'cases': {'C_deployment': cr}}}}
        s2 = copy.deepcopy(s1)
        c2 = s2['isd']['200']['cases']['C_deployment']
        c2['constants']['se_admission']['UL'], c2['constants']['snr_admission_db']['UL'] = 0.19651, -0.6387
        c2['hazard15_labels_3.5GHz_full_buffer']['UL'].update(not_admitted_share=0.3 - 0.1, share_between_snr_min_and_admission=0.0)
        c2['UL']['off']['y_star']['x_star'] = 0.25
        c2['sustainable_traffic']['0.0|off+off|ericsson_typical'].update(x_star_UL=2.25)
        raw1 = {'200': {'C_deployment|DL|0.0|off|0|L': np.arange(6.0), 'C_deployment|UL|off|0|L': np.ones(3)}}
        raw2 = {'200': {'C_deployment|DL|0.0|off|0|L': np.arange(6.0), 'C_deployment|UL|off|0|L': np.zeros(3)}}
        return s1, s2, raw1, raw2

    def test_passes_when_only_ul_moves(self):
        s1, s2, raw1, raw2 = self.pair()
        r = e.amendment_check(s1, s2, raw1, raw2)
        self.assertTrue(r['passed'], r)
        self.assertEqual((r['raw_dl_arrays_compared'], r['cases_compared']), (1, 1))

    def test_fails_on_any_dl_or_label_change(self):
        def mutated(fn):
            s1, s2, raw1, raw2 = self.pair()
            fn(s2['isd']['200']['cases']['C_deployment'], raw2['200'])
            return e.amendment_check(s1, s2, raw1, raw2)['passed']
        self.assertFalse(mutated(lambda c, r: c['DL']['0.0|off']['x_star'].update(x_star=1.5 + 1e-15)))
        self.assertFalse(mutated(lambda c, r: c['DL']['0.0|off']['grid'][0].update(outage_admitted_pooled=1e-300)))
        self.assertFalse(mutated(lambda c, r: c['sustainable_traffic']['0.0|off+off|ericsson_typical'].update(x_star_DL=1.4)))
        self.assertFalse(mutated(lambda c, r: c['calibration'].update(x_medium_over_busy=0.51)))
        self.assertFalse(mutated(lambda c, r: c['constants']['se_admission'].update(DL=0.2)))
        self.assertFalse(mutated(lambda c, r: c['hazard15_labels_3.5GHz_full_buffer']['DL'].update(not_admitted_share=1e-9)))
        self.assertFalse(mutated(lambda c, r: c['hazard15_labels_3.5GHz_full_buffer']['UL'].update(not_admitted_share=0.3)))
        self.assertFalse(mutated(lambda c, r: c['hazard15_labels_3.5GHz_full_buffer']['UL'].update(
            share_between_snr_min_and_admission=1e-9)))
        self.assertFalse(mutated(lambda c, r: c['hazard15_labels_3.5GHz_full_buffer']['UL'].update(share_in_tr36942_low_end=0.14)))
        self.assertFalse(mutated(lambda c, r: r.update({'C_deployment|DL|0.0|off|0|L': np.arange(6.0) + 1e-12})))
        self.assertFalse(mutated(lambda c, r: r.pop('C_deployment|DL|0.0|off|0|L')))


if __name__ == '__main__':
    unittest.main()
