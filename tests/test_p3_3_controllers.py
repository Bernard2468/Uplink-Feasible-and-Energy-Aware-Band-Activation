"""P3.3 controllers and traces on hand-checkable fixtures: Milan file parsing (synthetic file), square selection,
normalization, quantization, seasonal-persistence forecast, accounting (simulator vs the independent reference loop),
DP oracle vs brute force, threshold hysteresis, causality of every non-oracle controller, time-of-day rule,
UL-proportional 3.5 GHz receive power, config constants."""
import json
import math
import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'scripts'))
import p3_3_controllers as e  # noqa: E402
import p3_3_traces as TR  # noqa: E402

CONF = json.loads(e.CONFIG.read_text(encoding='utf-8'))
CONF1 = json.loads(e.C.P31_CONFIG.read_text(encoding='utf-8'))


def toy(G=8, R=3, seed=0):
    """A consistent toy table and energy scenario (two mixes, three DL offsets)."""
    rs = np.random.default_rng(seed)
    nM, nD, P = 2, 3, e.NP
    xg = np.arange(G) * 0.5
    oDL = np.sort(rs.uniform(0, 0.1, (P, nD, G)), axis=2)
    oUL = np.sort(rs.uniform(0, 0.1, (P, nM, G)), axis=2)
    comp = {c: (oDL[:, None] <= 0.05) & (oUL[:, :, None] <= 0.05) for c in ('all_user', 'admitted')}
    comp['dl_only_all_user'] = np.broadcast_to(oDL[:, None] <= 0.05, (P, nM, nD, G)).copy()
    thr_both = rs.uniform(0.5, 3, (P, nM, nD))
    thr_both[e.FULL] = 3.2                                # all-on has the most capacity (others <= 3.0)
    thr_both[0] = 0.4
    thr_dl = rs.uniform(0.5, 3, (P, nD))
    thr_dl[e.FULL] = 3.2
    thr_dl[0] = 0.4
    tab = {'xg': xg, 'T50': 1e8, 'comp': comp, 'anyc': {c: v.any(axis=0) for c, v in comp.items()},
           'maxcap': {c: np.full((nM, nD), e.FULL) for c in comp}, 'sDL': rs.uniform(0.9, 1, (P, nD, G)),
           'sUL': rs.uniform(0.9, 1, (P, nM, G)), 'thr_both': thr_both, 'thr_dl': thr_dl}
    en = {'Pw': rs.uniform(1500, 4000, (P, nM, G, R)), 'p_act': 170.0, 'p_deep': 3.0, 'e_add': 494.5, 't_tr': 10.0, 't_sc': 0.105}
    return tab, en


class Seq:
    """A controller that plays a fixed pattern sequence (accounting test of arbitrary, non-nested transitions)."""
    is_oracle = False

    def __init__(self, seq):
        self.seq = np.asarray(seq)

    def reset(self, n, model, full):
        self.n = n

    def decide(self, t, past):
        return np.full(self.n, int(self.seq[t]))


class Traces(unittest.TestCase):

    def test_grid_quantize_aref(self):
        xg = TR.x_grid()
        self.assertEqual(len(xg), 201)
        self.assertEqual(xg[0], 0.0)
        self.assertEqual(xg[-1], 5.0)
        i, nclip = TR.quantize([0.0, 0.0124, 0.0125, 1.0, 4.99, 5.2])
        self.assertEqual(i.tolist(), [0, 0, 1, 40, 200, 200])
        self.assertEqual(nclip, 1)
        s = np.zeros(20)
        s[5:11] = 6.0
        s[11] = 12.0
        self.assertAlmostEqual(TR.a_ref(s), (6.0 * 5 + 12.0) / 6.0)

    def test_read_day_synthetic(self):
        date = '2013-11-04'
        t0 = TR.local_midnight_utc_ms(date)
        self.assertEqual(t0, 1383519600000)              # 2013-11-03T23:00Z = local midnight (CET)
        lines = [f'1\t{t0}\t39\t0.1\t\t\t\t2.5', f'1\t{t0}\t0\t\t\t\t\t1.5', f'1\t{t0 + 600000}\t39\t\t\t\t\t',
                 f'10000\t{t0 + 143 * 600000}\t39\t\t\t\t\t4.0']
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / 'f.txt'
            p.write_text('\n'.join(lines) + '\n')
            act, pres, cov = TR.read_day(p, date)
        self.assertEqual(act[0, 0], 4.0)
        self.assertTrue(pres[0, 0])
        self.assertFalse(pres[0, 1])          # a row with an empty Internet field is not presence
        self.assertEqual(act[9999, 143], 4.0)
        self.assertEqual(cov['rows_outside_day'], 0)
        self.assertEqual(cov['misaligned_rows'], 0)
        self.assertEqual(cov['internet_empty_rows'], 1)

    def test_select_squares(self):
        rs = np.random.default_rng(1)
        act = rs.uniform(1, 2, (200, 1008))
        act[:10] *= 10.0                      # the busiest ten
        pres = np.ones_like(act, dtype=bool)
        pres[0, 5] = False                    # incomplete: excluded although busiest
        old = TR.N_KEEP, TR.N_DEV_SQ
        TR.N_KEEP, TR.N_DEV_SQ = 10, 5
        try:
            dev, test, info = TR.select_squares(act, pres, 7)
            dev2, test2, _ = TR.select_squares(act, pres, 7)
        finally:
            TR.N_KEEP, TR.N_DEV_SQ = old
        self.assertEqual(info['candidates'], 199)
        self.assertNotIn(1, set(dev.tolist()) | set(test.tolist()))
        self.assertTrue(set(range(2, 11)) <= set(dev.tolist()) | set(test.tolist()))
        self.assertEqual(len(dev), 5)
        self.assertEqual(len(test), 5)
        self.assertFalse(set(dev.tolist()) & set(test.tolist()))
        self.assertTrue(np.array_equal(dev, dev2) and np.array_equal(test, test2))

    def test_seasonal_forecast_exact_on_seasonal_trace(self):
        day = 1.0 + np.sin(np.arange(144) / 144 * 2 * np.pi) ** 2
        tr = np.concatenate([day * (1.0 if d < 5 else 0.6) for d in range(7)])[None, :]
        S = TR.seasonal_profile(tr)
        for t_prev in (10, 143, 700, 1000):
            for h in range(4):
                f = TR.forecast(tr[0, t_prev], t_prev, h, S)
                tgt = (t_prev + 1 + h) % 1008
                self.assertAlmostEqual(float(f), tr[0, tgt], places=12)


class Accounting(unittest.TestCase):

    def test_simulator_equals_reference(self):
        tab, en = toy()
        rs = np.random.default_rng(3)
        for mi in (0, 1):
            for dpi in (0, 2):
                md = e.Model(tab, mi, dpi, en)
                xi = rs.integers(0, 8, (4, 40))
                for ctrl in (e.Fixed(e.FULL), e.Fixed(0), e.Fixed(3), e.TimeOfDay(), e.Threshold(0.1, 2, 'both'), e.Oracle('all_user'),
                             Seq(rs.integers(0, e.NP, 40))):
                    o = e.simulate(ctrl, xi, md, per_real=True)
                    om = e.simulate(ctrl, xi, md, per_real=False)
                    for j in range(4):
                        ref = e.reference_run(o['k'][j], xi[j], tab, mi, dpi, en)
                        self.assertLessEqual(e.EM.rel_diff(float(o['E_J'][j].mean()), ref['E_J']), 1e-12)
                        self.assertLessEqual(e.EM.rel_diff(float(om['E_J'][j]), ref['E_J']), 1e-12)
                        self.assertLessEqual(e.EM.rel_diff(float(o['bits'][j]), ref['bits']), 1e-12)
                        for c in e.CRITS:
                            self.assertAlmostEqual(float(o['viol_s'][c][j]), ref['viol_s'][c], places=6)
                        self.assertEqual(o['n_wake'][j], ref['n_wake'])
                        self.assertEqual(o['n_sleep'][j], ref['n_sleep'])

    def test_slot_energy_by_hand(self):
        tab, en = toy(G=2, R=1)
        md = e.Model(tab, 0, 0, en)
        P = md.Pm
        tw = 10.105
        # wake (1,0,0) -> (1,1,1): gap tw at the old pattern, plus 2 waking cells at (p_act - p_deep) for t_sc
        exp = P[7, 1] * (600 - tw) + P[1, 1] * tw + 2 * (170.0 - 3.0) * 0.105
        self.assertAlmostEqual(float(md.slot_energy_mean(np.int64(7), np.int64(1), np.int64(1))), exp, places=9)
        # sleep (1,1,1) -> (0,0,0): one E_add per cell entering sleep, no gap
        self.assertAlmostEqual(float(md.slot_energy_mean(np.int64(0), np.int64(7), np.int64(0))), P[0, 0] * 600 + 3 * 494.5, places=9)
        # swap (1,0,0) -> (0,0,1), make-before-break: one cell wakes (gap at the old pattern), one sleeps after it (one E_add)
        exp = P[3, 0] * (600 - tw) + P[1, 0] * tw + 1 * (170.0 - 3.0) * 0.105 + 1 * 494.5
        self.assertAlmostEqual(float(md.slot_energy_mean(np.int64(3), np.int64(1), np.int64(0))), exp, places=9)

    def test_transition_counts(self):
        self.assertEqual(int(e.NW[1, 3]), 1)
        self.assertEqual(int(e.NS[1, 3]), 1)
        self.assertEqual(int(e.NW[0, 7]), 3)
        self.assertEqual(int(e.NS[7, 0]), 3)
        self.assertEqual(int(e.NW[4, 7]) + int(e.NS[4, 7]), 1)
        self.assertTrue(all(int(e.NW[p, p]) == 0 and int(e.NS[p, p]) == 0 for p in e.KS))
        self.assertEqual([e.CANON[k] for k in range(4)], [0, 1, 4, 7])
        self.assertEqual(e.PATTERNS[e.FULL], (1, 1, 1))


class Optimization(unittest.TestCase):

    def test_dp_equals_brute_force(self):
        rs = np.random.default_rng(5)
        for _ in range(60):
            tab, en = toy(G=5, R=2, seed=int(rs.integers(1e6)))
            md = e.Model(tab, int(rs.integers(2)), int(rs.integers(3)), en)
            row = rs.integers(0, 5, 3)
            o = e.Oracle('all_user')
            o.reset(1, md, row[None, :])
            bf = e.brute_force(md, row, 'all_user')
            self.assertLessEqual(e.EM.rel_diff(float(o.value[0]), bf), 1e-12)
            for t in range(3):           # the plan uses allowed patterns only; fallback = the maximum-capacity pattern
                i = row[t]
                k = o.plan[0, t]
                self.assertTrue(md.comp['all_user'][k, i] if md.anyc['all_user'][i] else k == md.maxcap['all_user'])


class Controllers(unittest.TestCase):

    def test_threshold_hysteresis(self):
        tab, en = toy()
        md = e.Model(tab, 0, 0, en)
        th = md.thr['both']
        c = e.Threshold(0.0, 2, 'both')
        c.reset(1, md, None)
        hi_i = len(md.xg) - 1
        seq = [hi_i, hi_i, 0, 0, 0]
        ks = [int(c.decide(0, np.zeros((1, 0), dtype=int))[0])]
        past = []
        for t, i in enumerate(seq, start=1):
            past.append(i)
            ks.append(int(c.decide(t, np.array([past]))[0]))
        self.assertTrue(np.all(md.xg[hi_i] > th))         # the top grid point exceeds every threshold: maximum capacity
        self.assertEqual(ks[0], e.FULL)
        self.assertEqual(ks[1], e.FULL)
        self.assertEqual(ks[3], ks[2])                     # one slot below: hysteresis holds
        self.assertEqual(ks[4], 0)                         # two consecutive slots with x = 0 and H = 2: (0,0,0)
        x = np.array([0.45])                               # target: fewest cells covering, ties to the higher threshold
        cover = [p for p in e.KS if x[0] <= th[p]]
        self.assertEqual(int(c.target(x)[0]), min(cover, key=lambda p: (e.N_ON[p], -th[p], p)))

    def test_tod(self):
        c = e.TimeOfDay()
        c.reset(2, None, None)
        self.assertEqual(c.decide(35, None).tolist(), [0, 0])
        self.assertEqual(c.decide(36, None).tolist(), [e.FULL, e.FULL])
        self.assertEqual(c.decide(144, None).tolist(), [0, 0])

    def test_causality(self):
        tab, en = toy(G=8)
        md = e.Model(tab, 1, 1, en)
        S = np.ones((2, 144))
        q = [0.1] * e.H_MAX
        rs = np.random.default_rng(9)
        for ctrl_f in (lambda: e.Threshold(0.1, 3, 'both'), lambda: e.Threshold(0.0, 1, 'dl'), lambda: e.MPC(0.9, 1, S, q),
                       lambda: e.MPC(0.9, 3, S, q), lambda: e.TimeOfDay(), lambda: e.Fixed(5)):
            base = rs.integers(0, 8, (1, 60))
            o1 = e.simulate(ctrl_f(), base, md, per_real=False)
            for cut in (1, 17, 40):
                pert = base.copy()
                pert[0, cut:] = rs.integers(0, 8, 60 - cut)
                o2 = e.simulate(ctrl_f(), pert, md, per_real=False)
                self.assertTrue(np.array_equal(o1['k'][0, :cut + 1], o2['k'][0, :cut + 1]))


class Energy(unittest.TestCase):

    def test_ul_reduction_by_hand(self):
        p_trx = 0.18 * 100 ** 0.5 * 3.125 ** 0.5
        hand = 0.25 * (64 * p_trx * 0.5 / 0.81 + 0.07 * 100 * 8 * 0.25 / 0.855)
        self.assertLess(abs(e.ul_reduction_w(CONF1) - hand), 1e-9)
        self.assertLess(abs(hand - 35.52), 0.01)

    def test_config_constants(self):
        self.assertEqual(CONF['scenarios'], e.SCENARIOS)
        self.assertEqual(tuple(CONF['controllers']), e.CONTROLLERS)
        self.assertEqual({int(k): tuple(v) for k, v in CONF['patterns'].items()}, e.PATTERNS)
        self.assertEqual(CONF['tuning']['tune_scenarios'], e.TUNE_SCEN)
        self.assertEqual(CONF['gates']['pass_rule'], 'F0-F11')
        self.assertEqual({k: {a: tuple(b) for a, b in v.items()} for k, v in CONF['tuning']['grids'].items()}, e.TUNE)
        self.assertEqual(tuple(CONF['growth']), e.GROWTH)
        self.assertEqual(len(e.SCENARIOS), 20)
        self.assertEqual(tuple(CONF['traces']['dev_dates']), TR.DEV_DATES)
        self.assertEqual(tuple(CONF['traces']['test_dates']), TR.TEST_DATES)
        self.assertEqual(CONF['timing']['t_scell_s'], e.T_SCELL)
        self.assertTrue(math.isclose(e.T_SCELL, 0.105))


if __name__ == '__main__':
    unittest.main()
