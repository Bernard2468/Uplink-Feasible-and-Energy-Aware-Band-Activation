"""P3.2a: coupled radio-feasibility engine.

Exact recomputation of every victim's SINR and UL/DL feasibility for any on/off (or activity-weighted) state of the
7.2 GHz cells, on the unchanged E1c.3 model (setting 'sd'). Pre-registered in configs/p3/p3_2a_coupled_feasibility.json.
scripts/e1c3_directional.py and scripts/e1c2_interference.py are imported, not modified.

Engine: engine_terms() builds, per victim, the serving signal and the per-transmitter interference terms exactly as
E1c.3's evaluate_pb does for 'sd'; engine_sinr() applies a 7.2 GHz state w72 (0 = off) and the D13 serving rule.
Outputs in --outdir: summary.json, validation.json, manifest.json; raw/counts_isd<ISD>.npz (git-ignored).
"""
import argparse
import cmath
import hashlib
import json
import math
import platform
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'scripts'))
import e1c3_directional as m  # noqa: E402

M2 = m.m2
CONFIG = ROOT / 'configs/p3/p3_2a_coupled_feasibility.json'
OUTDIR = ROOT / 'results/phase3/p3_2a_coupled_feasibility'
E1C3_DIR, E1C2_DIR = m.OUTDIR, m.E1C2_DIR
ISDS, CASES, STATES, PROFILES = M2.ISDS, M2.CASES, M2.STATES, M2.PROFILES
PARTITION, FEAS, NAMES, N_SECT, BEARINGS = M2.PARTITION, M2.FEAS, M2.NAMES, M2.N_SECT, M2.BEARINGS
STREAM_ACTV = 30
Q_LEVELS = (0.25, 0.5, 0.75)
DP = {'A_equal_elements': (0.0,), 'B_equal_aperture': (0.0,), 'C_deployment': (0.0, -3.0, -10.0)}
LOO_KEYS = (('A_equal_elements', 0.0), ('B_equal_aperture', 0.0), ('C_deployment', 0.0), ('C_deployment', -3.0))
LOO_FLAGS = (('DL', 'F2'), ('DL', 'F3'), ('UL', 'F2'), ('UL', 'F3'))
CHAIN = ('site0_only', 'q0.25', 'q0.5', 'q0.75', 'all_on')
SCEN = ('all_on', 'all_off', 'serving_trxp_only', 'site0_only', 'ring2_off', 'q0.25', 'q0.5', 'q0.75')
S0_NAMES = ('S0', 'S0_DL_7.2GHz', 'S0_UL_7.2GHz')
EXTRA = ('OWN72_ON',) + S0_NAMES
ALLN = list(NAMES) + list(EXTRA)                      # amendment 1: NAMES is a list, EXTRA a tuple (run 1 crashed)
PAIRS = {'site0_only_minus_all_on': (('site0_only', 0.0), ('all_on', 0.0)),
         'ring2_off_minus_all_on': (('ring2_off', 0.0), ('all_on', 0.0)),
         'q0.5_minus_all_on': (('q0.5', 0.0), ('all_on', 0.0)),
         'all_on_50dBm_minus_53dBm': (('all_on', -3.0), ('all_on', 0.0)),
         'site0_only_50dBm_minus_53dBm': (('site0_only', -3.0), ('site0_only', 0.0)),
         'all_on_43dBm_minus_53dBm': (('all_on', -10.0), ('all_on', 0.0)),
         'ring2_off_minus_all_on_50dBm': (('ring2_off', -3.0), ('all_on', -3.0)),
         'site0_only_minus_all_on_50dBm': (('site0_only', -3.0), ('all_on', -3.0))}
PAIR_NAMES = ('DL_7.2GHz', 'UL_7.2GHz', 'S0_DL_7.2GHz', 'S0_UL_7.2GHz', 'S0')
PAIR_PROFILES = ('F2', 'F3')
G4_PAIRS, G4_REALIZATIONS = 60, (0, 1)
G4_SCEN = ('all_on', 'q0.5', 'ring2_off', 'site0_only')


# ---------------------------------------------------------------- engine
def site_rings(sites):
    i, j = np.asarray(sites['site_i']), np.asarray(sites['site_j'])
    return np.maximum.reduce([np.abs(i), np.abs(j), np.abs(i + j)])


def engine_terms(b, G, L, Llin, cx, va, cc):
    """Per-victim terms of E1c.3 'sd' (eta 1, Bernoulli activity, UL target power), built with the same operations
    and order as e1c3_directional.evaluate_pb so that the all-on SINR is bitwise identical."""
    n, S, F = L.shape
    T3 = S * N_SECT
    rows = np.arange(n)
    kk = np.argmax(G[0]['ul'] - L[:, :, 0], axis=1)
    ss = b['sb'][rows, kk]
    tv = N_SECT * kk + ss
    notown = np.ones((n, T3), dtype=bool)
    notown[rows, tv] = False
    elin = b['Elin'].reshape(n, T3)
    dims = cc['dims']
    zs, as_ = cc['zen_s'].reshape(S, N_SECT), cc['az_s'].reshape(S, N_SECT)
    gdl = {d: m.array_power_gain(b['zen'][..., None], b['az'][..., None], zs[None], as_[None], BEARINGS, *d).reshape(n, T3)
           for d in set(dims)}
    have, u = cc['have'], cc['u']
    nU = len(have)
    keep = have[None, :] != tv[:, None]
    gul = {d: m.array_power_gain(cc['czen'][u[None, :], kk[:, None]], cc['caz'][u[None, :], kk[:, None]],
                                 b['zen'][rows, kk][:, None], b['az'][rows, kk][:, None], BEARINGS[ss][:, None], *d)
           for d in set(dims)}
    erx = cc['cElin'][u[None, :], kk[:, None], ss[:, None]]
    s_dl = [cx['p_dl'][fi] + G[fi]['dl'][rows, kk] - L[rows, kk, fi] for fi in range(F)]
    s_ul = [cx['p_ue'][fi] + G[fi]['ul'][rows, kk] - L[rows, kk, fi] for fi in range(F)]
    st = m.PB['sd']
    act_dl, act_ul = m.activity(st, cc['act_dl']), m.activity(st, cc['act_ul'])
    wf_dl, wf_ul = [], []
    for fi in range(F):
        a = np.repeat(m.beam_fraction(st['beam'], va[fi]), N_SECT, axis=1)
        base = elin * np.repeat(Llin[:, :, fi], N_SECT, axis=1) * notown
        fac = a * gdl[dims[fi]] + (1.0 - a)
        w = base * act_dl[None, :]
        wf_dl.append(w * fac)
        prof = {}
        if nU:
            au = m.beam_fraction(st['beam'], cc['ca'][fi][u[None, :], kk[:, None]])
            baseu = erx * cc['cLlin'][u[None, :], kk[:, None], fi] * keep
            facu = au * gul[dims[fi]] + (1.0 - au)
            for pk in ('F2', 'F3'):
                pw = 10.0 ** (cc['P_target'][fi][pk] / 10.0)
                wu = baseu * (pw * act_ul[have])[None, :]
                prof[pk] = wu * facu
        wf_ul.append(prof)
    return {'n': n, 'T3': T3, 'k': kk, 'tv': tv, 'have': have, 'nU': nU, 's_dl': s_dl, 's_ul': s_ul,
            'wf_dl': wf_dl, 'wf_ul': wf_ul}


def masked_sum(wf, w):
    """Sum of interference terms over transmitters with weights w ((T,) global or (n, T) per victim); None = all 1."""
    if w is None:
        return wf.sum(axis=1)
    w = np.asarray(w, dtype=float)
    return (wf * (w[None, :] if w.ndim == 1 else w)).sum(axis=1)


def own_on(T, w72):
    w = np.asarray(w72, dtype=float)
    return (w[T['tv']] if w.ndim == 1 else w[np.arange(T['n']), T['tv']]) > 0.0


def engine_sinr(T, cx, w72, own72=None, dp72=0.0):
    """SINR [dB] per carrier: DL, and UL per profile. The 3.5 GHz layer keeps E1c.3's terms; on 7.2 GHz each
    transmitter's DL term and its scheduled UE's UL term are scaled by w72; the DL power of every TRxP is offset by
    dp72 dB. own72 (default: the victim's own TRxP has w72 > 0) False -> no 7.2 GHz service (SINR -inf)."""
    own = own_on(T, w72) if own72 is None else np.broadcast_to(np.asarray(own72, dtype=bool), (T['n'],))
    dl, ul = [], {p: [] for p in PROFILES}
    for fi in range(2):
        w = None if fi == 0 else w72
        d = 0.0 if fi == 0 else dp72
        i_dl = 10.0 ** ((cx['p_dl'][fi] + d) / 10.0) * masked_sum(T['wf_dl'][fi], w)
        x = (T['s_dl'][fi] + d) - 10.0 * np.log10(10.0 ** (cx['n_dl'][fi] / 10.0) + i_dl)
        dl.append(x if fi == 0 else np.where(own, x, -np.inf))
        for p in PROFILES:
            i_ul = 0.0
            if T['nU']:
                wu = None
                if w is not None:
                    wa = np.asarray(w, dtype=float)
                    wu = wa[T['have']] if wa.ndim == 1 else wa[:, T['have']]
                i_ul = masked_sum(T['wf_ul'][fi]['F3' if p == 'F3' else 'F2'], wu)
                if p == 'F1':
                    i_ul = i_ul / cx['n_prb'][fi]
            y = T['s_ul'][fi] - 10.0 * np.log10(10.0 ** (cx['n_ul'][fi][p] / 10.0) + i_ul)
            ul[p].append(y if fi == 0 else np.where(own, y, -np.inf))
    return {'dl': dl, 'ul': ul, 'k': T['k'], 't': T['tv'], 'own72': own}


def loo_flags(T, cx, dp72):
    """7.2 GHz flags with each transmitter t switched off in turn from all-on (column t), and the all-on flags."""
    n, T3 = T['n'], T['T3']
    rows = np.arange(n)
    plin = 10.0 ** ((cx['p_dl'][1] + dp72) / 10.0)
    wf = T['wf_dl'][1]
    i_all = wf.sum(axis=1)
    i_loo = np.maximum(i_all[:, None] - wf, 0.0)
    nd = 10.0 ** (cx['n_dl'][1] / 10.0)
    s = T['s_dl'][1] + dp72
    vals = {'DL': (s - 10.0 * np.log10(nd + plin * i_all), s[:, None] - 10.0 * np.log10(nd + plin * i_loo))}
    vals['DL'][1][rows, T['tv']] = -np.inf
    for p in PAIR_PROFILES:
        nu = 10.0 ** (cx['n_ul'][1][p] / 10.0)
        if T['nU']:
            wfu = T['wf_ul'][1][p]
            iu = wfu.sum(axis=1)
            a_all = T['s_ul'][1] - 10.0 * np.log10(nu + iu)
            loo = np.repeat(a_all[:, None], T3, axis=1)
            loo[:, T['have']] = T['s_ul'][1][:, None] - 10.0 * np.log10(nu + np.maximum(iu[:, None] - wfu, 0.0))
        else:
            a_all = T['s_ul'][1] - 10.0 * np.log10(nu + 0.0)
            loo = np.repeat(a_all[:, None], T3, axis=1)
        loo[rows, T['tv']] = -np.inf
        vals[('UL', p)] = (a_all, loo)
    out = {}
    for d, p in LOO_FLAGS:
        th = cx['th'][1][(p, d)]
        a_all, loo = vals['DL'] if d == 'DL' else vals[('UL', p)]
        out[(d, p)] = (a_all >= th, loo >= th)
    return out


def scenario_states(T3, site_of, ring_of, U):
    site0 = (site_of == 0).astype(float)
    st = {'all_on': (np.ones(T3), None), 'all_off': (np.zeros(T3), None),
          'serving_trxp_only': (np.zeros(T3), True), 'site0_only': (site0, None),
          'ring2_off': ((ring_of <= 1).astype(float), None)}
    for q in Q_LEVELS:
        st[f'q{q}'] = (np.maximum((U < q).astype(float), site0), None)
    return st


def flags_with_extra(res, cx):
    fl = M2.class_flags(res, cx)
    s0 = res['k'] == 0
    own = res['own72']
    for p in PROFILES:
        fl[p]['OWN72_ON'] = own
        fl[p]['S0'] = s0
        fl[p]['S0_DL_7.2GHz'] = s0 & fl[p]['DL_7.2GHz']
        fl[p]['S0_UL_7.2GHz'] = s0 & fl[p]['UL_7.2GHz']
    return fl


# ---------------------------------------------------------------- independent scalar path (gate Q4)
def scalar_masked(data, case, table, cx, seed, isd, r, state, vi, mix, cache, origin, w72, dp72, own72=None):
    """scalar_pb's 'sd' path re-implemented with 7.2 GHz weights per TRxP and the DL offset (explicit phasor sums)."""
    M2.scalar_primary(data, case, table, cx, seed, isd, r, state, vi, mix, cache)
    cand, ues, sel = cache[(isd, r)]
    s = data['sites']
    S = len(s['site_x'])
    sites = [(float(s['site_x'][k]), float(s['site_y'][k]), float(s['site_h'][k])) for k in range(S)]
    freqs = [float(c['frequency_hz']) for c in sorted(case['carriers'], key=lambda c: c['frequency_hz'])]
    syn, _ = m.synthetic_targets(M2.rng_for(seed, isd, r, m.STREAM_SYN), s, origin, isd)
    kind = 'out' if state in ('outdoor', 'in_car') else 'ind'
    z, u, x = M2.draw_ue_terms(M2.rng_for(seed, isd, r, 1 + STATES.index(state)), len(data['victims'][kind]), S)
    ue = M2.s_ue(data, kind, int(data['victims'][kind][vi]))
    lk = [[None] * S for _ in freqs]
    gk = [[None] * S for _ in freqs]
    ang = [None] * S
    for fi, f in enumerate(freqs):
        for k in range(S):
            Lv, zen, az = M2.s_link(ue, sites[k], f, state, z[vi, k], u[vi, k], x[vi], k)
            lk[fi][k], gk[fi][k], ang[k] = Lv, M2.s_gains(case, fi, zen, az, ue[3][k], kind, table), (zen, az)
    ks = max(range(S), key=lambda k: (gk[0][k][0] - lk[0][k], -k))
    sv = gk[0][ks][2]
    t_v = N_SECT * ks + sv
    own = (float(w72[t_v]) > 0.0) if own72 is None else bool(own72)
    steer = {}
    for t in range(N_SECT * S):
        tx = ues[sel[t]][0][:3] if t in sel else tuple(float(v) for v in syn[t])
        sx, sy, sh = sites[t // N_SECT]
        steer[t] = (math.degrees(math.atan2(math.hypot(tx[0] - sx, tx[1] - sy), tx[2] - sh)),
                    math.degrees(math.atan2(tx[1] - sy, tx[0] - sx)))
    bear = (30.0, 150.0, 270.0)
    out = {'dl': [], 'ul': {p: [] for p in PROFILES}, 'k': ks, 't': t_v}
    for fi, f in enumerate(freqs):
        d = dp72 if fi == 1 else 0.0
        p_dl = cx['p_dl'][fi] + d
        i_dl = 0.0
        for k in range(S):
            a_k, n_el, rr, cc_ = m.s_spec(case, fi, ue[3][k], kind, table)
            for sct in range(N_SECT):
                t = N_SECT * k + sct
                if t == t_v:
                    continue
                g = m.s_pattern(ang[k][0], ang[k][1], steer[t][0], steer[t][1], bear[sct], rr, cc_)
                wgt = float(w72[t]) if fi == 1 else 1.0
                i_dl += wgt * 10.0 ** ((p_dl + gk[fi][k][3][sct] - lk[fi][k]) / 10.0) * (a_k * g + 1.0 - a_k)
        nb = cx['n_prb'][fi] * 12 * 30e3
        n_dl = 10.0 ** (M2.s_noise(nb, 7.0) / 10.0)
        v_dl = p_dl + gk[fi][ks][1] - lk[fi][ks] - 10.0 * math.log10(n_dl + i_dl)
        out['dl'].append(v_dl if (fi == 0 or own) else -math.inf)
        noise = {'F1': M2.s_noise(12 * 30e3, 5.0), 'F2': M2.s_noise(nb, 5.0), 'F3': M2.s_noise(cx['n_max'][fi] * 12 * 30e3, 5.0)}
        iu = {'F2': 0.0, 'F3': 0.0}
        for t in sorted(sel):
            if t == t_v:
                continue
            mi = sel[t]
            ue_i, st_i, kind_i = ues[mi]
            k_own = t // N_SECT
            l_own, zo, ao = M2.s_link(ue_i, sites[k_own], f, st_i, cand['z'][mi, k_own], cand['u'][mi, k_own], cand['x'][mi], k_own)
            g_own = M2.s_gains(case, fi, zo, ao, ue_i[3][k_own], kind_i, table)[0]
            l_rx, zr, ar = M2.s_link(ue_i, sites[ks], f, st_i, cand['z'][mi, ks], cand['u'][mi, ks], cand['x'][mi], ks)
            e_rx = M2.s_element(zr, ar - bear[sv])
            a_u, n_el, rr, cc_ = m.s_spec(case, fi, ue_i[3][ks], kind_i, table)
            g = m.s_pattern(zr, ar, ang[ks][0], ang[ks][1], bear[sv], rr, cc_)
            wgt = float(w72[t]) if fi == 1 else 1.0
            for p in ('F2', 'F3'):
                pw = min(23.0, cx['th'][fi][(p, 'UL')] + noise[p] + l_own - g_own)
                iu[p] += wgt * 10.0 ** ((pw + e_rx - l_rx) / 10.0) * (a_u * g + 1.0 - a_u)
        s_ul = 23.0 + gk[fi][ks][0] - lk[fi][ks]
        for p in PROFILES:
            i_ul = iu['F3' if p == 'F3' else 'F2'] * (1.0 / cx['n_prb'][fi] if p == 'F1' else 1.0)
            v = s_ul - 10.0 * math.log10(10.0 ** (noise[p] / 10.0) + i_ul)
            out['ul'][p].append(v if (fi == 0 or own) else -math.inf)
    return out


def scalar_diff(a, b):
    if math.isinf(a) or math.isinf(b):
        return 0.0 if a == b else math.inf
    return abs(a - b)


# ---------------------------------------------------------------- aggregation
def mix_fractions(counts, keys, nv, n_real, mixes):
    """counts[(..., st, p, name)] per-victim arrays -> per-state fractions, mixes and per-victim MC SE (E1c.3)."""
    out = {}
    for key in keys:
        per_state, se_state = {}, {}
        for st in STATES:
            per_state[st], se_state[st] = {}, {}
            for p in PROFILES:
                fr, se = {}, {}
                for n in ALLN:
                    v = counts[key + (st, p, n)] / n_real
                    fr[n] = float(v.mean())
                    se[n] = math.sqrt(float(np.sum(v * (1 - v))) / n_real) / nv[st]
                per_state[st][p], se_state[st][p] = fr, se
        out[key] = {'per_state': per_state,
                    'mixes': {mn: {p: {n: sum(w * per_state[s_][p][n] for s_, w in mm['weights'].items()) for n in ALLN}
                                   for p in PROFILES} for mn, mm in mixes.items()},
                    'mixes_mc_se': {mn: {p: {n: math.sqrt(sum(w ** 2 * se_state[s_][p][n] ** 2 for s_, w in mm['weights'].items()))
                                             for n in ALLN} for p in PROFILES} for mn, mm in mixes.items()}}
    return out


def pair_fractions(pairs, cases_of_pair, nv, n_real, mix):
    out = {}
    for (pn, c), _ in cases_of_pair.items():
        res = {}
        for p in PAIR_PROFILES:
            res[p] = {}
            for n in PAIR_NAMES:
                d_st, se_st = {}, {}
                for st in mix:
                    n10, n01 = pairs[(pn, c, st, p, n)]
                    dv = (n10.astype(float) - n01) / n_real
                    var = (n10.astype(float) + n01) / n_real - dv ** 2
                    d_st[st] = float(dv.mean())
                    se_st[st] = math.sqrt(float(np.sum(var)) / n_real) / nv[st]
                res[p][n] = {'diff': sum(w * d_st[s_] for s_, w in mix.items()),
                             'se': math.sqrt(sum(w ** 2 * se_st[s_] ** 2 for s_, w in mix.items()))}
        out.setdefault(pn, {})[c] = res
    return out


def sha256(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


# ---------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--config', default=str(CONFIG))
    ap.add_argument('--outdir', default=str(OUTDIR))
    ap.add_argument('--git-head', default=None)
    ap.add_argument('--smoke-realizations', type=int, default=None,
                    help='amendment 1: code-path smoke test on N realizations; never a measured run (forced FAIL, '
                         'outdir must differ from the default)')
    args = ap.parse_args()
    t0 = time.time()
    conf = json.loads(Path(args.config).read_text(encoding='utf-8'))
    conf3 = json.loads(m.CONFIG.read_text(encoding='utf-8'))
    conf14 = json.loads(M2.e155.E14_CONFIG.read_text(encoding='utf-8'))
    seed, n_real = int(conf['settings']['seed']), int(conf['settings']['realizations'])
    if seed != int(conf3['settings']['seed']) or n_real != int(conf3['settings']['realizations']):
        raise SystemExit('P3.2a must use the E1c.3 seed and realization count')
    smoke = args.smoke_realizations is not None
    if smoke:
        if Path(args.outdir).resolve() == OUTDIR.resolve():
            raise SystemExit('smoke runs must not write to the measured-run directory')
        n_real = int(args.smoke_realizations)
    outdir = Path(args.outdir)
    (outdir / 'raw').mkdir(parents=True, exist_ok=True)
    validation = {}
    # ---- Q0 inputs
    man3 = json.loads((E1C3_DIR / 'manifest.json').read_text())
    man2 = json.loads((E1C2_DIR / 'manifest.json').read_text())
    raw3 = {isd: E1C3_DIR / 'raw' / f'counts_isd{isd}.npz' for isd in ISDS}
    q0 = (man3['all_checks_passed'] and man2['all_checks_passed']
          and sha256(m.CONFIG) == man3['config_sha256']
          and sha256(E1C3_DIR / 'summary.json') == man3['outputs_sha256']['summary.json']
          and all(sha256(p) == man3['outputs_sha256'][f'raw/{p.name}'] for p in raw3.values())
          and all(sha256(M2.R / k) == v for k, v in man3['input_sha256'].items() if (M2.R / k).exists())
          and all((M2.R / k).exists() for k in man3['input_sha256']))
    in_sha = {'e1c3_directional/manifest.json': sha256(E1C3_DIR / 'manifest.json'),
              'e1c3_directional/summary.json': sha256(E1C3_DIR / 'summary.json'),
              **{f'e1c3_directional/raw/{p.name}': sha256(p) for p in raw3.values()}}
    validation['Q0_inputs'] = {'input_sha256': in_sha, 'passed': bool(q0)}
    if not q0:
        raise SystemExit('Q0 failed')
    s3 = json.loads((E1C3_DIR / 'summary.json').read_text())
    s153 = json.loads((M2.e155.E153_DIR / 'summary.json').read_text())
    s153a = json.loads((M2.e155.E153A_DIR / 'summary.json').read_text())
    s1521 = json.loads((M2.e155.E1521_DIR / 'summary.json').read_text())
    s154 = json.loads((M2.e155.E154_DIR / 'summary.json').read_text())
    maps = M2.e155.build_mappings(s153a, s153)
    table = M2.e155.gain_table(s1521)
    pkt_off = M2.e155.packet_offsets(s154)
    sel_r = {d: v['factor'] for d, v in M2.e155.selectivity_factors(s154).items()}
    cases = {c: conf14['cases'][c] for c in CASES}
    mixes = conf14['mixes']
    mix = mixes['M2412']['weights']
    cxs = {c: M2.case_context(cases[c], maps, pkt_off, sel_r) for c in CASES}
    freqs = cxs['A_equal_elements']['freqs']
    with np.load(M2.E11_DIR / 'site_tx0_mask.npz') as z:
        mask = {k: z[k] for k in z.files}
    with np.load(M2.GEO_DIR / 'site_tx0_indoor.npz') as z:
        geo = {k: z[k] for k in z.files}
    gnb = np.array(mask['gnb'], dtype=float)
    summary = {'n_realizations': n_real, 'scenarios': list(SCEN), 'power_offsets_db': DP, 'isd': {}}
    gate = {k: {} for k in ('Q1a', 'Q1b', 'Q2', 'Q3', 'Q4', 'Q5', 'Q6', 'Q7')}
    for isd in ISDS:
        t_isd = time.time()
        with np.load(M2.SITES_DIR / f'e1c2_sites_isd{isd}.npz') as z:
            sites = {k: z[k] for k in z.files}
        S = len(sites['site_x'])
        T3 = N_SECT * S
        sxyz = np.column_stack([sites['site_x'], sites['site_y'], sites['site_h']]).astype(float)
        rings = site_rings(sites)
        site_of = np.arange(T3) // N_SECT
        ring_of = rings[site_of]
        if not (sites['site_i'][0] == 0 and sites['site_j'][0] == 0 and np.sum(rings == 0) == 1):
            raise SystemExit('site 0 is not the anchor')
        ring_o = M2.nearest_ring(np.column_stack([mask['x'], mask['y']]), gnb, isd)
        ring_i = M2.nearest_ring(np.column_stack([geo['x'], geo['y']]), gnb, isd)
        keep_i = ~sites['indoor_excluded']
        victims = {'out': np.flatnonzero(ring_o == 0), 'ind': np.flatnonzero((ring_i == 0) & keep_i)}
        pools = {'out': np.flatnonzero(ring_o <= 2), 'ind': np.flatnonzero((ring_i <= 2) & keep_i)}
        data = {'mask': mask, 'geo': geo, 'sites': sites, 'pools': pools, 'victims': victims}
        vb = {}
        for kind, idx in victims.items():
            if kind == 'out':
                vb[kind] = M2.link_block(sxyz, mask['x'][idx], mask['y'][idx], mask['z'][idx], sites['los_out'][idx],
                                         np.zeros((len(idx), S)), freqs)
            else:
                vb[kind] = M2.link_block(sxyz, geo['x'][idx], geo['y'][idx], geo['h_ut'][idx], sites['facade_los'][idx],
                                         sites['din'][idx], freqs)
            vb[kind]['Elin'] = 10.0 ** (vb[kind]['E'] / 10.0)
        vg = {kind: {c: M2.case_gains(vb[kind], cases[c], table, kind) for c in CASES} for kind in vb}
        va = {kind: {c: [m.specular_from_gains(vb[kind]['E'], vg[kind][c][fi]['ul'], n)
                         for fi, n in enumerate(r_ * c_ for r_, c_ in m.case_dims(cases[c]))] for c in CASES} for kind in vb}
        sterm = {st: M2.state_terms(st, vb['out' if st in ('outdoor', 'in_car') else 'ind'], freqs) for st in STATES}
        nv = {st: vb['out' if st in ('outdoor', 'in_car') else 'ind']['n'] for st in STATES}
        print(f'ISD {isd}: victims {[len(v) for v in victims.values()]}, rings {np.bincount(rings).tolist()}', flush=True)
        scen_keys = [(sc, dp, c) for c in CASES for dp in DP[c] for sc in SCEN]
        counts = {k + (st, p, n): np.zeros(nv[st], dtype=np.int16)
                  for k in scen_keys for st in STATES for p in PROFILES for n in ALLN}
        pair_cases = {(pn, c): ab for pn, ab in PAIRS.items() for c in CASES
                      if all(dp in DP[c] for (_, dp) in ab)}
        pairs = {(pn, c, st, p, n): (np.zeros(nv[st], dtype=np.int16), np.zeros(nv[st], dtype=np.int16))
                 for (pn, c) in pair_cases for st in mix for p in PAIR_PROFILES for n in PAIR_NAMES}
        loo_d = {(c, dp, st, f): np.zeros((n_real, T3)) for c, dp in LOO_KEYS for st in STATES for f in LOO_FLAGS}
        loo_served = {(c, dp, st): np.zeros((n_real, T3)) for c, dp in LOO_KEYS for st in STATES}
        q1a_bad, q2_bad, q3_bad, q5_bad, q6_bad = 0, 0, 0, 0, 0
        q7_bad, q7_n = 0, 0
        rg4 = np.random.default_rng(seed + 6)
        g4 = []
        for _ in range(G4_PAIRS):
            c = ('A_equal_elements', 'C_deployment')[int(rg4.integers(2))]
            st = STATES[int(rg4.integers(len(STATES)))]
            g4.append({'r': int(rg4.choice(G4_REALIZATIONS)), 'st': st, 'c': c, 'vi': int(rg4.integers(nv[st])),
                       'sc': G4_SCEN[int(rg4.integers(len(G4_SCEN)))], 'dp': float(DP[c][int(rg4.integers(min(2, len(DP[c]))))])})
        q7_t = np.r_[[0, 1, 2], np.random.default_rng(seed + 7).choice(np.arange(3, T3), 3, replace=False)]
        for r in range(n_real):
            cand = M2.draw_candidates(M2.rng_for(seed, isd, r, 0), len(pools['out']), len(pools['ind']), M2.N_CAND, S, mix)
            cb = M2.candidate_block(cand, data, sxyz, pools, freqs)
            cb['Elin'], cb['Llin'] = 10.0 ** (cb['E'] / 10.0), 10.0 ** (-cb['L'] / 10.0)
            act_dl, act_ul = m.draw_activity(M2.rng_for(seed, isd, r, m.STREAM_ACT), T3)
            syn, _ = m.synthetic_targets(M2.rng_for(seed, isd, r, m.STREAM_SYN), sites, gnb, isd)
            U = M2.rng_for(seed, isd, r, STREAM_ACTV).random(T3)
            states72 = scenario_states(T3, site_of, ring_of, U)
            cc = {}
            for c in CASES:
                Gc = M2.candidate_gains(cb, cases[c], table)
                ui = M2.ul_interference(cb, Gc, cxs[c], cand['usel'], S)
                cc[c] = m.case_beam_context(cb, Gc, ui, cases[c], cxs[c], data, sxyz, syn, act_dl, act_ul)
            for si, st in enumerate(STATES):
                kind = 'out' if st in ('outdoor', 'in_car') else 'ind'
                b = vb[kind]
                z, u, x = M2.draw_ue_terms(M2.rng_for(seed, isd, r, 1 + si), b['n'], S)
                pen, sig, sue = sterm[st]
                L, _ = M2.losses(b, pen, sig, sue, z, u, x)
                Llin = 10.0 ** (-L / 10.0)
                for c in CASES:
                    T = engine_terms(b, vg[kind][c], L, Llin, cxs[c], va[kind][c], cc[c])
                    ref = m.evaluate_pb(b, vg[kind][c], L, Llin, cxs[c], va[kind][c], cc[c],
                                        settings={'sd': m.PB['sd'], 'sd_iso72': m.PB['sd_iso72']})
                    flags, sinr = {}, {}
                    for dp in DP[c]:
                        for sc in SCEN:
                            w, own = states72[sc]
                            res = engine_sinr(T, cxs[c], w, own, dp)
                            sinr[(sc, dp)] = res
                            if dp == 0.0 and sc in ('all_on', 'serving_trxp_only'):
                                rr_ = ref['sd' if sc == 'all_on' else 'sd_iso72']
                                same = (all(np.array_equal(res['dl'][fi], rr_['dl'][fi]) for fi in range(2))
                                        and all(np.array_equal(res['ul'][p][fi], rr_['ul'][p][fi]) for p in PROFILES for fi in range(2)))
                                if sc == 'all_on':
                                    q1a_bad += int(not same)
                                else:
                                    q2_bad += int(not same)
                            fl = flags_with_extra(res, cxs[c])
                            flags[(sc, dp)] = fl
                            for p in PROFILES:
                                for n in ALLN:
                                    counts[(sc, dp, c, st, p, n)] += fl[p][n]
                    # Q3 all_off
                    for dp in DP[c]:
                        off, on = sinr[('all_off', dp)], sinr[('all_on', dp)]
                        q3_bad += int(any(np.any(flags[('all_off', dp)][p][n]) for p in PROFILES
                                          for n in ('DL_7.2GHz', 'UL_7.2GHz', 'OWN72_ON', 'S0_DL_7.2GHz', 'S0_UL_7.2GHz')))
                        q3_bad += int(not (np.array_equal(off['dl'][0], on['dl'][0])
                                           and all(np.array_equal(off['ul'][p][0], on['ul'][p][0]) for p in PROFILES)))
                    # Q5 nesting
                    for dp in DP[c]:
                        chain = [(CHAIN[i], CHAIN[i + 1]) for i in range(len(CHAIN) - 1)] + [('ring2_off', 'all_on')]
                        for lo, hi in chain:
                            a, bb = sinr[(lo, dp)], sinr[(hi, dp)]
                            o = a['own72']
                            q5_bad += int(np.sum(o & ~bb['own72']))
                            q5_bad += int(np.sum(o & ~(bb['dl'][1] <= a['dl'][1] + 1e-12)))
                            for p in PROFILES:
                                q5_bad += int(np.sum(o & ~(bb['ul'][p][1] <= a['ul'][p][1] + 1e-12)))
                    # Q6 power offsets (C)
                    if len(DP[c]) > 1:
                        for sc in SCEN:
                            v = [sinr[(sc, dp)] for dp in DP[c]]          # 0, -3, -10
                            ok = all(np.all((v[i + 1]['dl'][1] <= v[i]['dl'][1] + 1e-12) | ~np.isfinite(v[i]['dl'][1]))
                                     for i in range(len(v) - 1))
                            ok &= all(np.array_equal(v[i]['dl'][0], v[0]['dl'][0])
                                      and all(np.array_equal(v[i]['ul'][p][fi], v[0]['ul'][p][fi]) for p in PROFILES for fi in range(2))
                                      for i in range(1, len(v)))
                            q6_bad += int(not ok)
                    # pairs
                    if st in mix:
                        for (pn, cp), ((sa, da), (sb, db)) in pair_cases.items():
                            if cp != c:
                                continue
                            for p in PAIR_PROFILES:
                                for n in PAIR_NAMES:
                                    fa, fb = flags[(sa, da)][p][n], flags[(sb, db)][p][n]
                                    n10, n01 = pairs[(pn, c, st, p, n)]
                                    n10 += fa & ~fb
                                    n01 += ~fa & fb
                    # leave-one-out
                    for (cl, dpl) in LOO_KEYS:
                        if cl != c:
                            continue
                        lf = loo_flags(T, cxs[c], dpl)
                        for f in LOO_FLAGS:
                            a_all, loo = lf[f]
                            loo_d[(c, dpl, st, f)][r] = (loo.astype(float) - a_all[:, None]).mean(axis=0)
                        loo_served[(c, dpl, st)][r] = np.bincount(T['tv'], minlength=T3) / T['n']
                        if r == 0:
                            for t in q7_t:
                                w = np.ones(T3)
                                w[t] = 0.0
                                res = engine_sinr(T, cxs[c], w, None, dpl)
                                for d, p in LOO_FLAGS:
                                    th = cxs[c]['th'][1][(p, d)]
                                    v = res['dl'][1] if d == 'DL' else res['ul'][p][1]
                                    q7_bad += int(not np.array_equal(v >= th, lf[(d, p)][1][:, t]))
                                    q7_n += 1
                    # G4 engine values
                    for rec in g4:
                        if rec['r'] == r and rec['st'] == st and rec['c'] == c:
                            res = sinr[(rec['sc'], rec['dp'])]
                            vi = rec['vi']
                            rec['eng'] = {'dl': [float(res['dl'][fi][vi]) for fi in range(2)],
                                          'ul': {p: [float(res['ul'][p][fi][vi]) for fi in range(2)] for p in PROFILES},
                                          'k': int(res['k'][vi]), 't': int(res['t'][vi])}
                            rec['w72'] = states72[rec['sc']][0]
            if r % 10 == 9:
                print(f'ISD {isd}: realization {r + 1}/{n_real}, {time.time() - t_isd:.0f} s', flush=True)
        # ---- Q1b: all_on counts vs E1c.3 raw 'sd'
        with np.load(raw3[isd]) as z:
            raw_e1c3 = {k: z[k] for k in z.files}
        sd_keys = [k for k in raw_e1c3 if k.startswith('sd|')]
        q1b_bad = [k for k in sd_keys if not np.array_equal(
            raw_e1c3[k], counts[('all_on', 0.0) + tuple(k.split('|')[1:])].astype(raw_e1c3[k].dtype))]
        victims_ok = (np.array_equal(victims['out'], raw_e1c3['victim_out_index'])
                      and np.array_equal(victims['ind'], raw_e1c3['victim_ind_index']))
        res_isd = mix_fractions(counts, scen_keys, nv, n_real, mixes)
        iso_diff = max(abs(res_isd[('serving_trxp_only', 0.0, c)]['mixes'][mn][p][n]
                           - s3['isd'][str(isd)]['settings']['sd_iso72'][c]['mixes'][mn][p][n])
                       for c in CASES for mn in mixes for p in PROFILES for n in NAMES)
        gate['Q1a'][f'isd{isd}'] = {'arrays_not_bitwise_equal': q1a_bad, 'passed': q1a_bad == 0}
        gate['Q1b'][f'isd{isd}'] = {'raw_arrays_compared': len(sd_keys), 'mismatches': q1b_bad[:10], 'victims_match': bool(victims_ok),
                                    'passed': bool(sd_keys and not q1b_bad and victims_ok)}
        gate['Q2'][f'isd{isd}'] = {'arrays_not_bitwise_equal': q2_bad, 'max_mix_diff_vs_e1c3_summary': iso_diff,
                                   'passed': q2_bad == 0 and iso_diff <= 1e-12}
        gate['Q3'][f'isd{isd}'] = {'violations': q3_bad, 'passed': q3_bad == 0}
        gate['Q5'][f'isd{isd}'] = {'violations': q5_bad, 'passed': q5_bad == 0}
        gate['Q6'][f'isd{isd}'] = {'violations': q6_bad, 'passed': q6_bad == 0}
        gate['Q7'][f'isd{isd}'] = {'transmitters': q7_t.tolist(), 'comparisons': q7_n, 'mismatches': q7_bad,
                                   'passed': q7_n > 0 and q7_bad == 0}
        # ---- Q4 scalar
        # one scalar cache per case: scalar_primary keys its cache by (isd, r) only, but the interferer selection
        # depends on the case's gains (E1c.3 used case A only)
        caches, worst, assoc_bad, worst_ref = {c: {} for c in CASES}, 0.0, 0, 0.0
        for rec in g4:
            case = cases[rec['c']]
            cache = caches[rec['c']]
            sc = scalar_masked(data, case, table, cxs[rec['c']], seed, isd, rec['r'], rec['st'], rec['vi'], mix, cache, gnb,
                               rec['w72'], rec['dp'])
            e = rec['eng']
            assoc_bad += int(sc['k'] != e['k'] or sc['t'] != e['t'])
            d = [scalar_diff(sc['dl'][fi], e['dl'][fi]) for fi in range(2)]
            d += [scalar_diff(sc['ul'][p][fi], e['ul'][p][fi]) for p in PROFILES for fi in range(2)]
            worst = max(worst, max(d))
            if rec['sc'] == 'all_on' and rec['dp'] == 0.0:
                one = scalar_masked(data, case, table, cxs[rec['c']], seed, isd, rec['r'], rec['st'], rec['vi'], mix, cache,
                                    gnb, np.ones(T3), 0.0)
                ref = m.scalar_pb(data, case, table, cxs[rec['c']], seed, isd, rec['r'], rec['st'], rec['vi'], mix, cache, gnb)['sd']
                worst_ref = max(worst_ref, max([abs(one['dl'][fi] - ref['dl'][fi]) for fi in range(2)]
                                               + [abs(one['ul'][p][fi] - ref['ul'][p][fi]) for p in PROFILES for fi in range(2)]))
        n_ref = sum(1 for rec in g4 if rec['sc'] == 'all_on' and rec['dp'] == 0.0)
        gate['Q4'][f'isd{isd}'] = {'pairs': len(g4), 'association_mismatches': assoc_bad, 'max_abs_diff_db': worst,
                                   'copy_vs_e1c3_scalar_pairs': n_ref, 'copy_vs_e1c3_scalar_max_db': worst_ref,
                                   'passed': assoc_bad == 0 and worst <= 1e-9 and worst_ref <= 1e-12 and n_ref > 0}
        print('Q1a', gate['Q1a'][f'isd{isd}']['passed'], 'Q1b', gate['Q1b'][f'isd{isd}']['passed'], 'Q2', gate['Q2'][f'isd{isd}']['passed'],
              'Q3', gate['Q3'][f'isd{isd}']['passed'], 'Q4', gate['Q4'][f'isd{isd}']['passed'], f'{worst:.2e} dB',
              'Q5', gate['Q5'][f'isd{isd}']['passed'], 'Q6', gate['Q6'][f'isd{isd}']['passed'], 'Q7', gate['Q7'][f'isd{isd}']['passed'],
              flush=True)
        # ---- leave-one-out map
        loo = {}
        for c, dp in LOO_KEYS:
            per_t = []
            for t in range(T3):
                rec = {'t': t, 'site': int(site_of[t]), 'sector': int(t % N_SECT), 'ring': int(ring_of[t])}
                srv = sum(w * loo_served[(c, dp, s_)][:, t] for s_, w in mix.items())
                rec['served_share'] = float(srv.mean())
                for f in LOO_FLAGS:
                    dmix = sum(w * loo_d[(c, dp, s_, f)][:, t] for s_, w in mix.items())
                    rec[f'{f[0]}_{f[1]}'] = {'mean': float(dmix.mean()), 'se': float(dmix.std(ddof=1) / math.sqrt(n_real))}
                per_t.append(rec)
            loo[f'{c}|{dp}'] = per_t
        summary['isd'][str(isd)] = {
            'victims': {'outdoor_points': len(victims['out']), 'indoor_samples': len(victims['ind'])},
            'sites_per_ring': np.bincount(rings).tolist(),
            'scenarios': {f'{sc}|{dp}|{c}': {k: v for k, v in res_isd[(sc, dp, c)].items() if k != 'per_state'}
                          for (sc, dp, c) in scen_keys},
            'pairs_m2412': pair_fractions(pairs, pair_cases, nv, n_real, mix),
            'leave_one_out_m2412': loo}
        raw = {'victim_out_index': victims['out'], 'victim_ind_index': victims['ind']}
        for (sc, dp, c, st, p, n), v in counts.items():
            if sc in ('all_on', 'site0_only', 'ring2_off', 'q0.5') and n in FEAS + EXTRA:
                raw[f'{sc}|{dp}|{c}|{st}|{p}|{n}'] = v
        np.savez_compressed(outdir / 'raw' / f'counts_isd{isd}.npz', **raw)
        print(f'ISD {isd} done in {time.time() - t_isd:.0f} s', flush=True)
    names = {'Q1a': 'Q1a_all_on_bitwise', 'Q1b': 'Q1b_all_on_counts', 'Q2': 'Q2_serving_only', 'Q3': 'Q3_all_off',
             'Q4': 'Q4_scalar', 'Q5': 'Q5_nesting', 'Q6': 'Q6_power_offset', 'Q7': 'Q7_leave_one_out'}
    for k, nm in names.items():
        validation[nm] = dict(gate[k], passed=all(v['passed'] for v in gate[k].values()))
    gates_ok = all(v['passed'] for v in validation.values()) and not smoke
    validation['smoke_run'] = smoke
    validation['all_checks_passed'] = gates_ok
    # ---- predictions and decision (ISD 200, C, M2412, F2, site-0-served subset)
    pr = summary['isd']['200']['pairs_m2412']
    c = 'C_deployment'

    def cond(sc, dp, n):
        mx = summary['isd']['200']['scenarios'][f'{sc}|{dp}|{c}']['mixes']['M2412']['F2']
        return mx[n] / mx['S0']

    g_ring2 = cond('ring2_off', -3.0, 'S0_DL_7.2GHz') - cond('all_on', -3.0, 'S0_DL_7.2GHz')
    g_all = cond('site0_only', -3.0, 'S0_DL_7.2GHz') - cond('all_on', -3.0, 'S0_DL_7.2GHz')
    loss_on = cond('all_on', 0.0, 'S0_DL_7.2GHz') - cond('all_on', -3.0, 'S0_DL_7.2GHz')
    loss_iso = cond('site0_only', 0.0, 'S0_DL_7.2GHz') - cond('site0_only', -3.0, 'S0_DL_7.2GHz')
    d_ring2_ul = cond('ring2_off', -3.0, 'S0_UL_7.2GHz') - cond('all_on', -3.0, 'S0_UL_7.2GHz')
    summary['predictions'] = {
        'P1_ring2_small': {'gain_ring2_off': g_ring2, 'gain_site0_only': g_all, 'held': g_ring2 <= 0.25 * g_all},
        'P2_power_matters_less_under_interference': {'loss_53_to_50_all_on': loss_on, 'loss_53_to_50_site0_only': loss_iso,
                                                     'held': loss_on < loss_iso},
        'basis': 'conditional on being served by site 0 (S0_X / S0), M2412, F2, ISD 200, case C'}
    adm = abs(g_ring2) <= 0.01 and abs(d_ring2_ul) <= 0.01
    summary['decision_D14_cluster_scope'] = {'ring2_off_minus_all_on_DL': g_ring2, 'ring2_off_minus_all_on_UL': d_ring2_ul,
                                             'cluster_scoring_admissible': adm,
                                             'decision': conf['decision_rules']['D14_cluster_scope']}
    summary['decision'] = conf['decision_rules']['all_gates_pass' if gates_ok else 'gate_fails']
    summary['wall_time_s'] = time.time() - t0
    (outdir / 'summary.json').write_text(json.dumps(summary, indent=1, default=float), encoding='utf-8')
    (outdir / 'validation.json').write_text(json.dumps(validation, indent=2, default=float), encoding='utf-8')
    manifest = {'generated_utc': datetime.now(timezone.utc).isoformat(), 'tool': 'scripts/p3_2a_coupled_feasibility.py',
                'git_head_at_run': args.git_head, 'python': platform.python_version(), 'numpy': np.__version__,
                'config_sha256': sha256(args.config), 'input_sha256': in_sha,
                'outputs_sha256': {'summary.json': sha256(outdir / 'summary.json'),
                                   **{f'raw/counts_isd{isd}.npz': sha256(outdir / 'raw' / f'counts_isd{isd}.npz') for isd in ISDS}},
                'all_checks_passed': gates_ok}
    (outdir / 'manifest.json').write_text(json.dumps(manifest, indent=2), encoding='utf-8')
    for k, v in validation.items():
        if isinstance(v, dict):
            print(k, 'PASS' if v['passed'] else 'FAIL')
    print('P3.2a', 'GATES PASSED' if gates_ok else 'GATES FAILED', f'{time.time() - t0:.0f} s')
    sys.exit(0 if gates_ok else 1)


if __name__ == '__main__':
    main()
