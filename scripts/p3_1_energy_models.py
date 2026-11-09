"""P3.1: source-reproduced hardware energy models of both layers and the 7.2 GHz cell sleep ladder.

Pre-registered in configs/p3/p3_1_energy_models.json (docs/PHASE_3_ENERGY_PROTOCOL.md, P3.0 rev 1).
- 3.5 GHz: Peschiera et al. arXiv 2505.15445v1 (IEEE TCOM 2026), compact model (5) with the closed forms of
  Sec. III-D and the parameters of Golard et al. (PIMRC 2024) Table II.
- 7.2 GHz: Peschiera et al. FR3 model. Pre-registered as arXiv 2510.10647v1 printed; AMENDMENT 1 (after run 1):
  the project uses the published ICASSP 2026 version (Lirias manuscript + the authors' code), version 'published';
  'v1' is kept to re-record run 1's failed reproduction.
- Cell sleep ladder: TR 38.864 V18.1.0 Tables 5.1-3/4/5, attached at the FR3 micro-sleep state (D4).
Units: W, s, J. Pure Python (math only).

Run: python scripts/p3_1_energy_models.py --git-head <sha> --source KEY=PATH ... (all keys of config 'sources').
Outputs in --outdir: summary.json, validation.json, manifest.json.
"""
import argparse
import hashlib
import itertools
import json
import math
import platform
import re
import sys
import time
import zipfile
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
CONFIG = REPO / 'configs' / 'p3' / 'p3_1_energy_models.json'
OUTDIR = REPO / 'results' / 'phase3' / 'p3_1_energy_models'
REL_EXACT = 1e-12


def dbm_to_w(dbm):
    return 10.0 ** ((dbm - 30.0) / 10.0)


def w_to_dbm(w):
    return 10.0 * math.log10(w) + 30.0


def rel_diff(a, b):
    return abs(a - b) / max(abs(a), abs(b), 1e-300)


# ---------------------------------------------------------------- PA (shared by both models)

def pa_power(p, pa, static_form='printed'):
    """Instantaneous consumption [W] of ONE PA at output p [W].
    2510.10647 eq. (17) = 2505.15445 Sec. III-A: xi Pmax^a/eta + (1-xi) Pmax^(1-a) p^a / eta ('printed');
    Golard eq. (4) has the static term xi Pmax/eta ('golard'). The dynamic terms agree (alpha = 1 - n)."""
    pmax, eta, alpha, xi = pa['p_max_w'], pa['eta_max'], pa['alpha'], pa['xi']
    if not (p >= 0.0) or p > pmax * (1.0 + 1e-12):
        raise ValueError(f'PA output {p} W outside [0, P_max = {pmax} W]')
    if static_form == 'printed':
        static = xi * pmax ** alpha / eta
    elif static_form == 'golard':
        static = xi * pmax / eta
    else:
        raise ValueError(static_form)
    return static + (1.0 - xi) * pmax ** (1.0 - alpha) * p ** alpha / eta


def golard_eta_max(p_max_w, f_hz, g):
    """Golard eq. (5): eta_tech / (1 + a (Pmax/P*) (f/f*)^2 + b (P*/Pmax))."""
    pa = g['pa']
    return pa['eta_tech'] / (1.0 + pa['a'] * (p_max_w / g['p_star_w']) * (f_hz / g['f_star_hz']) ** 2
                             + pa['b'] * (g['p_star_w'] / p_max_w))


# ---------------------------------------------------------------- sub-6 GHz model (2505.15445 + Golard)

def _sub6_common(cfg, g, static_form):
    proto = g['protocol'][cfg['protocol']]
    alpha = 1.0 - g['pa']['n']
    eta = golard_eta_max(cfg['p_max_w'], cfg['f_hz'], g)
    pa = {'p_max_w': cfg['p_max_w'], 'eta_max': eta, 'alpha': alpha, 'xi': g['pa']['xi']}
    psc = g['psc']
    eff = {'pa': psc['eta_supply'] * psc['eta_cool_pa'], 'afe': psc['eta_supply'] * psc['eta_cool_afe'],
           'dbb': psc['eta_supply'] * psc['eta_cool_bb']}
    afe, dbb = g['afe'], g['dbb']
    bb = cfg['B_hz'] / g['b_star_hz']
    p_misc = afe['p_misc_ref_w'][cfg['ru']] * bb ** afe['alpha11'] * (cfg['M'] * cfg['p_max_w'] / g['p_star_w']) ** afe['alpha12']
    p_trx = afe['p_trx_ref_w'] * bb ** afe['alpha21'] * (cfg['p_max_w'] / g['p_star_w']) ** afe['alpha22']
    return proto, alpha, eta, pa, eff, p_misc, p_trx, bb


def sub6_dbb(cfg, g, n_c, proto, bb):
    """2505.15445 eq. (9) with Golard eq. (11): P_link/N_C^alpha3 + P_phy (B/B*)^alpha4 K [(tdl+tul) + (2-tdl-tul) d_idle];
    all K spatial layers active."""
    dbb = g['dbb']
    tdl, tul = proto['tau_dl'], proto['tau_ul']
    return (dbb['p_link_ref_w'] / n_c ** dbb['alpha3']
            + dbb['p_phy_ref_w'] * bb ** dbb['alpha4'] * cfg['K'] * ((tdl + tul) + (2.0 - tdl - tul) * dbb['delta_idle_phy']))


def sub6_params(cfg, g, delta_dtx, delta_idle_trx, static_form, n_c):
    """Closed forms of 2505.15445 Sec. III-D: gamma, alpha, P0, P1, P_sleep [W] for one configuration."""
    proto, alpha, eta, pa, eff, p_misc, p_trx, bb = _sub6_common(cfg, g, static_form)
    tdl, tul, tsig, zeta = proto['tau_dl'], proto['tau_ul'], proto['tau_sig'], proto['zeta_sig']
    M, pmax, xi = cfg['M'], cfg['p_max_w'], g['pa']['xi']
    d_idle, d_sleep = g['pa']['delta_idle'], g['pa']['delta_sleep']
    p0w = pa_power(0.0, pa, static_form)
    gamma = tdl * (1.0 - tsig) / eff['pa'] * (1.0 - xi) * pmax ** (1.0 - alpha) / eta
    p0 = M * tdl * (1.0 - tsig) / eff['pa'] * p0w * (1.0 - delta_dtx)
    pa1 = (tdl * tsig * pa_power(zeta * pmax, pa, static_form) + tdl * (1.0 - tsig) * p0w * delta_dtx
           + (1.0 - tdl) * p0w * d_idle - p0w * d_sleep)
    d_sl_trx = g['afe']['delta_sleep_trx']
    afe1 = p_trx * ((tdl + tul) + (2.0 - tdl - tul) * delta_idle_trx - 2.0 * d_sl_trx)
    afe_sleep = p_misc + 2.0 * M * p_trx * d_sl_trx
    dbb = sub6_dbb(cfg, g, n_c, proto, bb)
    p1 = M * (pa1 / eff['pa'] + afe1 / eff['afe'])
    psleep = M * d_sleep / eff['pa'] * p0w + afe_sleep / eff['afe'] + dbb / eff['dbb']
    return {'alpha': alpha, 'gamma': gamma, 'P0': p0, 'P1': p1, 'Psleep': psleep, 'eta_max': eta,
            'parts': {'p_misc_w': p_misc, 'p_trx_w': p_trx, 'dbb_w': dbb, 'pa_static_w': p0w}}


def sub6_compact(par, M, load, m_a, p_a):
    """2505.15445 eq. (5)."""
    return load * m_a * (par['P0'] / M + par['gamma'] * p_a ** par['alpha']) + m_a / M * par['P1'] + par['Psleep']


def sub6_direct(cfg, g, delta_dtx, delta_idle_trx, static_form, n_c, load, m_a, p_a, parts=False):
    """Direct per-state sum of 2505.15445 Sec. III-A..D (written without the closed forms): M_a active PAs in
    signalling / data / DTX / idle, M - M_a PAs asleep; AFE misc + active and sleeping TX/RX chains; DBB; PSC."""
    proto, alpha, eta, pa, eff, p_misc, p_trx, bb = _sub6_common(cfg, g, static_form)
    tdl, tul, tsig, zeta = proto['tau_dl'], proto['tau_ul'], proto['tau_sig'], proto['zeta_sig']
    M = cfg['M']
    one_pa = (tdl * tsig * pa_power(zeta * cfg['p_max_w'], pa, static_form)
              + tdl * (1.0 - tsig) * load * pa_power(p_a, pa, static_form)
              + tdl * (1.0 - tsig) * (1.0 - load) * pa_power(0.0, pa, static_form) * delta_dtx
              + (1.0 - tdl) * pa_power(0.0, pa, static_form) * g['pa']['delta_idle'])
    pa_tot = m_a * one_pa + (M - m_a) * pa_power(0.0, pa, static_form) * g['pa']['delta_sleep']
    afe_tot = (p_misc + m_a * p_trx * ((tdl + tul) + (2.0 - tdl - tul) * delta_idle_trx)
               + 2.0 * (M - m_a) * p_trx * g['afe']['delta_sleep_trx'])
    dbb_tot = sub6_dbb(cfg, g, n_c, proto, bb)
    out = {'pa': pa_tot / eff['pa'], 'afe': afe_tot / eff['afe'], 'dbb': dbb_tot / eff['dbb']}
    total = out['pa'] + out['afe'] + out['dbb']
    return (total, out) if parts else total


def sub6_published_params(pub, case):
    return {'alpha': pub['alpha'], 'gamma': pub['gamma'], 'P0': pub['P0_w'][case], 'P1': pub['P1_w'][case],
            'Psleep': pub['Psleep_w']}


# ---------------------------------------------------------------- FR3 model (2510.10647), closed form

def fr3_hardware(conf, fc_hz, b_hz, m_ant, m_rf, k, p_t_w, pa, signalling, upsilon, r_dl_full, r_ul_full,
                 version='v1', rate_rule='scaled', df_hz=None):
    """Collect every input of the FR3 model for one hardware point.
    version 'v1' = arXiv v1 as printed; 'published' = ICASSP 2026 (amendment 1): static term xi Pmax/eta, data phase
    x tau (1 - tau_sig), one LNA per antenna, eta_s/c 0.81. rate_rule 'scaled' (R_full x load) or 'fixed' (the code's).
    df_hz None: subcarrier spacing scales with B (fixed FFT size)."""
    fr = conf['fr3_model']
    ver = fr['versions'][version]
    return {'fc': fc_hz, 'B': b_hz, 'M_ant': m_ant, 'M_RF': m_rf, 'K': k, 'P_T': p_t_w, 'pa': dict(pa),
            'signalling': signalling, 'upsilon': upsilon, 'r_dl_full': r_dl_full, 'r_ul_full': r_ul_full,
            'dig': fr['digital'], 'ana': fr['analog'], 'pa_delta': {'micro': fr['pa']['delta_micro'], 'idle': fr['pa']['delta_idle']},
            'frame': fr['frame'], 'version': version, 'static_form': ver['static_form'], 'frame_mode': ver['frame'],
            'lna': ver['lna'], 'eta_sc': dict(ver['eta_sc']), 'rate_rule': rate_rule, 'df_hz': df_hz}


def upsilon_published(conf, fc_hz):
    """Samples per coherence block by the authors' code rule: (c / dist_max) (lambda_c / (2 v))."""
    r = conf['fr3_model']['upsilon_rule_published']
    return (3e8 / r['dist_max_m']) * ((3e8 / fc_hz) / (2.0 * r['velocity_mps']))


def fr3_active_powers(hw, x_dl, x_ul):
    """Active (working) powers [W] of the digital and analog parts in DL and UL (Sec. 3.1-3.2)."""
    d, a = hw['dig'], hw['ana']
    B, m_rf, m_ant, K = hw['B'], hw['M_RF'], hw['M_ant'], hw['K']
    fs1 = d['mu'] * B
    df = hw['df_hz'] if hw['df_hz'] else d['df_ref_hz'] * B / d['b_ref_hz']
    fs2 = d['q_fft'] * df
    bt = d['bt_factor'] * B
    mt = math.ceil(m_rf / d['fpga']['share_rf_chains'])
    e_f, e_a = d['fpga']['eta_gops_per_w'] * 1e9, d['asic']['eta_gops_per_w'] * 1e9
    s_f, s_a = d['fpga']['p_static_w'], d['asic']['p_static_w']
    if hw['rate_rule'] == 'scaled':
        r_dl, r_ul = hw['r_dl_full'] * x_dl, hw['r_ul_full'] * x_ul
    else:
        r_dl, r_ul = hw['r_dl_full'], hw['r_ul_full']
    enc = s_a + fs1 / e_a * (14.0 / (3.0 * 8.0)) * (r_dl / bt)
    dec = s_a + fs1 / e_a * (5.0 * 35.0 / (2.0 * 3.0)) * (r_ul / bt)
    xi_pre = 2.0 * K + (K ** 3 / (3.0 * m_rf) + 3.0 * K ** 2 + K) / hw['upsilon']
    pre = mt * s_f + fs1 / e_f * m_rf * xi_pre
    comb = mt * s_f + fs1 / e_f * m_rf * (2.0 * K)
    fft = s_a + fs2 / e_a * m_rf * (1.5 * math.log2(d['q_fft']))
    dpd = s_a + fs2 / e_a * m_rf * 50.0
    bbf = mt * s_f + fs2 / e_f * m_rf * (20.0 * 4.0)
    dig_dl = enc + pre + fft + dpd + bbf
    dig_ul = dec + comb + fft + bbf
    p_dac = 1.5e-5 * 2 ** a['dac_bits'] + 1.5e-12 * a['dac_bits'] * a['dac_fs_hz']
    p_adc = a['adc_walden_j'] * a['adc_fs_hz'] * 2 ** a['adc_bits']
    p_mix, p_ps, p_lna = a['xi_mixer'] * hw['fc'], a['xi_ps'] * B, a['xi_lna'] * B
    m_ps = 0 if m_rf == m_ant else m_ant / m_rf
    n_lna = m_ant / m_rf if hw['lna'] == 'per_antenna' else 1.0
    ana_dl = a['p_lo_w'] + m_rf * (2 * p_dac + 2 * a['p_rf_filter_w'] + 2 * p_mix + m_ps * p_ps)
    ana_ul = a['p_lo_w'] + m_rf * (2 * p_adc + 2 * a['p_rf_filter_w'] + 2 * p_mix + m_ps * p_ps + n_lna * p_lna)
    return {'dig_dl': dig_dl, 'dig_ul': dig_ul, 'ana_dl': ana_dl, 'ana_ul': ana_ul,
            'enc_dec_dynamic_w': (enc - s_a) + (dec - s_a), 'm_ps': m_ps, 'fs2_hz': fs2}


def _frame_avg(p, x, tau, tau_sig, d_micro, d_idle, frame='printed'):
    """Eqs. (6), (12) as printed. frame='consistent' (diagnostic only) gives the data term the factor (1 - tau_sig),
    so the phase fractions sum to 1."""
    data = x * tau * p if frame == 'printed' else x * tau * (1.0 - tau_sig) * p
    return data + tau * tau_sig * p + tau * (1.0 - x) * (1.0 - tau_sig) * p * d_micro + (1.0 - tau) * p * d_idle


def fr3_power(hw, x_dl, x_ul, static_form=None, tau_sig=None, parts=False, frame=None):
    """Frame-averaged consumption, eq. (5). tau_sig=None uses the frame value; 0 gives the micro-sleep anchor.
    static_form / frame default to the hardware point's version."""
    static_form = static_form or hw['static_form']
    frame = frame or hw['frame_mode']
    fr = hw['frame']
    ts = fr['tau_sig'] if tau_sig is None else tau_sig
    tdl, tul = fr['tau_dl'], fr['tau_ul']
    act = fr3_active_powers(hw, x_dl, x_ul)
    dd, ad = hw['dig'], hw['ana']
    dig = (_frame_avg(act['dig_dl'], x_dl, tdl, ts, dd['delta_micro'], dd['delta_idle'], frame)
           + _frame_avg(act['dig_ul'], x_ul, tul, ts, dd['delta_micro'], dd['delta_idle'], frame))
    ana = (_frame_avg(act['ana_dl'], x_dl, tdl, ts, ad['delta_micro'], ad['delta_idle'], frame)
           + _frame_avg(act['ana_ul'], x_ul, tul, ts, ad['delta_micro'], ad['delta_idle'], frame))
    p_a = hw['P_T'] / hw['M_ant']
    p_sig = fr['zeta_sig'] * (hw['pa']['p_max_w'] if hw['signalling'] == 'reference' else p_a)
    pp = lambda p: pa_power(p, hw['pa'], static_form)  # noqa: E731
    data_frac = x_dl * tdl if frame == 'printed' else x_dl * tdl * (1.0 - ts)
    one = (data_frac * pp(p_a) + tdl * ts * pp(p_sig)
           + tdl * (1.0 - x_dl) * (1.0 - ts) * pp(0.0) * hw['pa_delta']['micro'] + (1.0 - tdl) * pp(0.0) * hw['pa_delta']['idle'])
    pa_avg = hw['M_ant'] * one
    e = hw['eta_sc']
    out = {'dig': dig / e['dig'], 'ana': ana / e['ana'], 'pa': pa_avg / e['pa'], 'pa_raw': pa_avg,
           'enc_dec_dynamic_bound_w': act['enc_dec_dynamic_w'] / e['dig']}
    total = out['dig'] + out['ana'] + out['pa']
    return (total, out) if parts else total


def fr3_dl_instant(hw, static_form=None):
    """Instantaneous power of a full-load DL data symbol: DL chains active, UL chains idle, every PA at P_a."""
    static_form = static_form or hw['static_form']
    act = fr3_active_powers(hw, 1.0, 0.0)
    e = hw['eta_sc']
    pa = hw['M_ant'] * pa_power(hw['P_T'] / hw['M_ant'], hw['pa'], static_form)
    return ((act['dig_dl'] + act['dig_ul'] * hw['dig']['delta_idle']) / e['dig']
            + (act['ana_dl'] + act['ana_ul'] * hw['ana']['delta_idle']) / e['ana'] + pa / e['pa'])


# ---------------------------------------------------------------- FR3 model, independent phase ledger (J2a)

def fr3_ledger(hw, x_dl, x_ul, static_form=None, tau_sig=None):
    """Second implementation: enumerate (direction, phase, time fraction) and the state of every component group in
    that phase, then sum fraction x power. Written separately from fr3_power; it recomputes every component power
    itself instead of calling fr3_active_powers."""
    static_form = static_form or hw['static_form']
    d, a, fr = hw['dig'], hw['ana'], hw['frame']
    ts = fr['tau_sig'] if tau_sig is None else tau_sig
    B, m_rf, m_ant, K = hw['B'], hw['M_RF'], hw['M_ant'], hw['K']
    # --- component powers, recomputed
    f1 = d['mu'] * B
    f2 = d['q_fft'] * (hw['df_hz'] if hw['df_hz'] else (d['df_ref_hz'] / d['b_ref_hz']) * B)
    groups = -(-m_rf // d['fpga']['share_rf_chains'])
    fpga_w_per_op = 1.0 / (d['fpga']['eta_gops_per_w'] * 1e9)
    asic_w_per_op = 1.0 / (d['asic']['eta_gops_per_w'] * 1e9)
    load_dl, load_ul = (x_dl, x_ul) if hw['rate_rule'] == 'scaled' else (1.0, 1.0)
    se_dl = hw['r_dl_full'] * load_dl / (d['bt_factor'] * B)
    se_ul = hw['r_ul_full'] * load_ul / (d['bt_factor'] * B)
    blocks_dl = [d['asic']['p_static_w'] + f1 * asic_w_per_op * (7.0 / 12.0) * se_dl,
                 groups * d['fpga']['p_static_w'] + f1 * fpga_w_per_op * m_rf * (2 * K + (K ** 3 / (3 * m_rf) + 3 * K * K + K) / hw['upsilon']),
                 d['asic']['p_static_w'] + f2 * asic_w_per_op * m_rf * 1.5 * math.log(d['q_fft'], 2),
                 d['asic']['p_static_w'] + f2 * asic_w_per_op * m_rf * 50.0,
                 groups * d['fpga']['p_static_w'] + f2 * fpga_w_per_op * m_rf * 80.0]
    blocks_ul = [d['asic']['p_static_w'] + f1 * asic_w_per_op * (175.0 / 6.0) * se_ul,
                 groups * d['fpga']['p_static_w'] + f1 * fpga_w_per_op * m_rf * 2 * K,
                 blocks_dl[2], blocks_dl[4]]
    dac = 1.5e-5 * (1 << a['dac_bits']) + 1.5e-12 * a['dac_bits'] * a['dac_fs_hz']
    adc = a['adc_walden_j'] * a['adc_fs_hz'] * (1 << a['adc_bits'])
    n_ps = 0.0 if m_rf == m_ant else m_ant / m_rf
    chain_common = 2 * a['p_rf_filter_w'] + 2 * a['xi_mixer'] * hw['fc'] + n_ps * a['xi_ps'] * B
    lnas_total = m_ant if hw['lna'] == 'per_antenna' else m_rf
    ana_w = {'DL': a['p_lo_w'] + m_rf * (2 * dac + chain_common),
             'UL': a['p_lo_w'] + m_rf * (2 * adc + chain_common) + lnas_total * a['xi_lna'] * B}
    dig_w = {'DL': sum(blocks_dl), 'UL': sum(blocks_ul)}
    p_a = hw['P_T'] / m_ant
    pa_out = {'data': p_a, 'sig': fr['zeta_sig'] * (hw['pa']['p_max_w'] if hw['signalling'] == 'reference' else p_a),
              'micro': 0.0, 'idle': 0.0}
    pa_red = {'data': 1.0, 'sig': 1.0, 'micro': hw['pa_delta']['micro'], 'idle': hw['pa_delta']['idle']}
    red = {'dig': {'data': 1.0, 'sig': 1.0, 'micro': d['delta_micro'], 'idle': d['delta_idle']},
           'ana': {'data': 1.0, 'sig': 1.0, 'micro': a['delta_micro'], 'idle': a['delta_idle']}}
    tau = {'DL': fr['tau_dl'], 'UL': fr['tau_ul']}
    load = {'DL': x_dl, 'UL': x_ul}
    acc = {'dig': 0.0, 'ana': 0.0, 'pa': 0.0}
    for direction in ('DL', 'UL'):
        t, x = tau[direction], load[direction]
        data_frac = x * t if hw['frame_mode'] == 'printed' else x * t * (1 - ts)
        phases = [('data', data_frac), ('sig', t * ts), ('micro', t * (1 - x) * (1 - ts)), ('idle', 1 - t)]
        for ph, frac in phases:
            acc['dig'] += frac * dig_w[direction] * red['dig'][ph]
            acc['ana'] += frac * ana_w[direction] * red['ana'][ph]
            if direction == 'DL':
                acc['pa'] += frac * m_ant * pa_power(pa_out[ph], hw['pa'], static_form) * pa_red[ph]
    e = hw['eta_sc']
    return acc['dig'] / e['dig'] + acc['ana'] / e['ana'] + acc['pa'] / e['pa']


# ---------------------------------------------------------------- authors' code, re-implemented (J2c, amendment 1)

CODE_R_DL = [v * 1e9 * 0.93 for v in (1.6, 3.64, 6.06, 8.10, 10.93, 12.10, 13.44)]
CODE_R_UL = [v * 1e9 * 0.93 for v in (1.02, 1.25, 1.5, 1.68, 1.98, 2.18, 2.35)]
CODE_M_VEC = [16, 32, 64, 128, 256, 512, 1024]


def fr3_code_reimpl(m_rf, m_ant, x):
    """Formulas of the authors' Fig_2a.py (totals) and Fig_2b.py (PA), github.com/e-peschiera/FR3-power-model commit
    255ed68, with their constants hard-coded here as in those files (read, not executed). Fig_2a.py's eta_DPD (2e12) is
    used; Fig_2b.py's differs (0.2e12) but affects only digital power, which Fig_2b does not plot."""
    K, f_c, B = 8, 10e9, 400e6
    f_sI, f_sII, B_t = 0.9 * B, 4096 * 120e3, 0.9 * B
    tdl, tds = 0.75, 1 / 14
    tul, tus = 1 - tdl, 1 / 14
    zeta, P_max, xi, eta_pa, alpha = 1 / 12, 0.1, 0.1, 0.15, 0.75
    eta_sc = 0.81
    i = CODE_M_VEC.index(m_rf)
    ups = (3e8 / 300) * ((3e8 / f_c) / (2 * 30))
    mps = 0 if m_rf == m_ant else m_ant / m_rf
    st = 1 * math.ceil(m_rf / 32)
    p_enc = 0.1 + 1 / 2e12 * (f_sI * 14 / (3 * 8) * CODE_R_DL[i] / B_t)
    p_dec = 0.1 + 1 / 2e12 * (f_sI * (5 * 35) / (2 * 3) * CODE_R_UL[i] / B_t)
    p_pre = st + 1 / 0.2e12 * m_rf * (f_sI * 2 * K + f_sI / ups * (K ** 3 / (3 * m_rf) + 3 * K ** 2 + K))
    p_com = st + 1 / 0.2e12 * m_rf * f_sI * 2 * K
    p_ifft = 0.1 + 1 / 2e12 * m_rf * f_sII * 3 / 2 * math.log2(4096)
    p_dpd = 0.1 + 1 / 2e12 * m_rf * f_sII * 50
    p_fbb = st + 1 / 0.2e12 * m_rf * f_sII * 20 * 4
    p_dl, p_ul = p_enc + p_pre + p_ifft + p_dpd + p_fbb, p_dec + p_com + p_ifft + p_fbb
    dig = ((x * tdl * (1 - tds) * p_dl + tdl * tds * p_dl + tdl * (1 - x) * (1 - tds) * p_dl * 0.5 + (1 - tdl) * p_dl * 0.25)
           + (x * tul * (1 - tus) * p_ul + tul * tus * p_ul + tul * (1 - x) * (1 - tus) * p_ul * 0.5 + (1 - tul) * p_ul * 0.25))
    p_dac = 1.5e-5 * 2 ** 8 + 1.5e-12 * 8 * 5e9
    p_adc = 70e-15 * 5e9 * 2 ** 8
    p_mix, p_ps, p_lna = 2.5e-13 * f_c, 3.5e-11 * B, 2.7e-11 * B
    a_dl = 40e-3 + m_rf * (2 * p_dac + 2 * 5e-3 + 2 * p_mix + mps * p_ps)
    a_ul = 40e-3 + m_rf * (2 * p_adc + 2 * 5e-3 + 2 * p_mix + mps * p_ps + m_ant / m_rf * p_lna)
    ana = ((x * tdl * (1 - tds) * a_dl + tdl * tds * a_dl + tdl * (1 - x) * (1 - tds) * a_dl * 0.75 + (1 - tdl) * a_dl * 0.5)
           + (x * tul * (1 - tds) * a_ul + tul * tus * a_ul + tul * (1 - x) * (1 - tus) * a_ul * 0.75 + (1 - tul) * a_ul * 0.5))

    def p_pa(p):
        return xi * P_max / eta_pa + (1 - xi) * P_max ** (1 - alpha) * p ** alpha / eta_pa

    pa1 = (x * tdl * (1 - tds) * p_pa(100 * m_ant / 1024 / m_ant) + tdl * tds * p_pa(zeta * P_max)
           + tdl * (1 - x) * (1 - tds) * p_pa(0) * 0.5 + (1 - tdl) * p_pa(0) * 0.25)
    pa = m_ant * pa1
    return {'dig': dig / eta_sc, 'ana': ana / eta_sc, 'pa': pa / eta_sc, 'tot': (dig + ana + pa) / eta_sc}


# ---------------------------------------------------------------- TR 38.864 ladder and energy ledger

def ladder(p_mu, tr, cat, set_name):
    """States attached at the micro-sleep anchor (D4). Powers W, T s, E_add J."""
    rp = tr['relative_power'][cat][set_name]
    w = p_mu / rp['micro']
    out = {'w_per_unit_w': w}
    for st in ('light', 'deep'):
        out[st] = {'p_w': w * rp[st], 't_s': tr['transition_time_s'][cat][st],
                   'e_add_j': w * tr['transition_energy_rel_ms'][cat][st] * 1e-3}
    return out


def ladder_dl_anchor(p_dl_inst, tr, cat, set_name):
    rp = tr['relative_power'][cat][set_name]
    w = p_dl_inst / rp['active_dl']
    out = {'w_per_unit_w': w, 'micro_w': w * rp['micro']}
    for st in ('light', 'deep'):
        out[st] = {'p_w': w * rp[st], 't_s': tr['transition_time_s'][cat][st],
                   'e_add_j': w * tr['transition_energy_rel_ms'][cat][st] * 1e-3}
    return out


def breakeven_s(p_empty, st):
    """Shortest sleep that saves energy against staying on and empty: max(T, E_add / (P_empty - P_sleep))."""
    gap = p_empty - st['p_w']
    if gap <= 0:
        return math.inf
    return max(st['t_s'], st['e_add_j'] / gap)


def sequence_energy(seq, states):
    """Energy [J] of a state sequence [(name, duration_s)]. states[name] = {'p_w', 'sleep', 't_s', 'e_add_j'}.
    TR 38.864 cl. 5.1 rules: every second is in exactly one state; a sleep lasts at least its total transition time;
    a non-sleep state separates two sleeps; energy = sum P D + sum over sleeps of E_add."""
    total, prev_sleep = 0.0, False
    for name, dur in seq:
        st = states[name]
        if not (dur > 0.0) or not math.isfinite(dur):
            raise ValueError(f'non-positive or non-finite duration in {name}')
        if st['sleep']:
            if dur < st['t_s']:
                raise ValueError(f'sleep {name} of {dur} s shorter than its transition time {st["t_s"]} s')
            if prev_sleep:
                raise ValueError('two adjacent sleep states')
            total += st['e_add_j']
        total += st['p_w'] * dur
        prev_sleep = st['sleep']
    return total


WORKED = {'states': {'on': {'p_w': 100.0, 'sleep': False, 't_s': 0.0, 'e_add_j': 0.0},
                     'light': {'p_w': 20.0, 'sleep': True, 't_s': 0.006, 'e_add_j': 0.5},
                     'deep': {'p_w': 5.0, 'sleep': True, 't_s': 0.05, 'e_add_j': 2.0}},
          'seq': [('on', 10.0), ('deep', 60.0), ('on', 1.0), ('light', 0.5), ('on', 2.0)],
          'energy_j': 1612.5}


# ---------------------------------------------------------------- D10

def d10_table(d10):
    rows = {}
    for dbm in (53, 50, 43):
        for n in (1024, 256):
            per = dbm_to_w(dbm) / n
            rows[f'{dbm}_{n}'] = {'per_pa_w': per, 'sige_feasible': per <= 0.1 * (1 + 1e-12)}
    sige = {str(n): w_to_dbm(0.1 * n) for n in (1024, 256)}
    ok_pa = all(rel_diff(rows[k]['per_pa_w'], v) <= 1e-5 for k, v in d10['per_pa_w'].items())
    ok_sige = all(abs(sige[k] - v) <= 1e-3 for k, v in d10['sige_max_total_dbm'].items())
    return {'rows': rows, 'sige_max_total_dbm': sige, 'matches_expected': ok_pa and ok_sige,
            'sige_infeasible_at_53': (not rows['53_1024']['sige_feasible']) and (not rows['53_256']['sige_feasible'])}


# ---------------------------------------------------------------- TR .docx parsing (J1)

def docx_tables_after(docx_path, captions):
    """Rows (cell texts) of the first table after each caption. Reads XML text only; nothing is executed."""
    with zipfile.ZipFile(docx_path) as z:
        xml = z.read('word/document.xml').decode('utf-8', errors='replace')
    out = {}
    for cap in captions:
        # amendment 1: a caption may be split over several XML runs ('Table 5.1-3' and ':'), so allow tags anywhere
        rx = re.compile(r'(?:<[^>]+>)*'.join(re.escape(ch) for ch in cap))  # tags only BETWEEN characters
        hits = list(rx.finditer(xml))
        j = xml.find('<w:tbl>', hits[0].end()) if len(hits) == 1 else -1
        if j < 0:
            out[cap] = None
            continue
        k = xml.find('</w:tbl>', j)
        rows = []
        for row in re.findall(r'<w:tr[ >].*?</w:tr>', xml[j:k], flags=re.S):
            rows.append([''.join(re.findall(r'<w:t[^>]*>([^<]*)</w:t>', c)).strip()
                         for c in re.findall(r'<w:tc>.*?</w:tc>', row, flags=re.S)])
        out[cap] = rows
    return out


def _num(s):
    s = s.strip()
    if s.endswith(' ms'):
        return float(s[:-3]) * 1e-3
    if s.endswith(' s'):
        return float(s[:-2])
    return float(s)


def j1_check(docx_path, tr):
    exp = tr['docx_expected_rows']
    got = docx_tables_after(docx_path, list(exp))
    labels = {'Deep sleep', 'Light sleep', 'Micro sleep', 'Active DL', 'Active UL'}
    res = {}
    for cap, rows in exp.items():
        parsed = got[cap]
        if parsed is None:
            res[cap] = {'found': False, 'passed': False}
            continue
        sel = [r for r in parsed if (r and r[0] in labels) or (len(r) > 1 and r[0] == '' and r[1] == 'Set 1')]
        res[cap] = {'found': True, 'selected_rows': sel, 'passed': sel == rows}
    # numeric tables of the config equal the expected strings
    t3 = {r[0]: r[1:] for r in exp['Table 5.1-3:'][1:]}
    num_ok = True
    for cat, sl in (('cat1', slice(0, 3)), ('cat2', slice(3, 6))):
        for state, key in (('Micro sleep', 'micro'), ('Active DL', 'active_dl'), ('Active UL', 'active_ul')):
            for s_name, v in zip(('set1', 'set2', 'set3'), t3[state][sl]):
                num_ok &= tr['relative_power'][cat][s_name][key] == _num(v)
        for state, key, idx in (('Deep sleep', 'deep', 0 if cat == 'cat1' else 1), ('Light sleep', 'light', 0 if cat == 'cat1' else 1)):
            for s_name in ('set1', 'set2', 'set3'):
                num_ok &= tr['relative_power'][cat][s_name][key] == _num(t3[state][idx])
    for cap, key in (('Table 5.1-4:', 'transition_time_s'), ('Table 5.1-5:', 'transition_energy_rel_ms')):
        rows = {r[0]: r[1:] for r in exp[cap]}
        for state, sk in (('Deep sleep', 'deep'), ('Light sleep', 'light')):
            for i, cat in enumerate(('cat1', 'cat2')):
                num_ok &= abs(tr[key][cat][sk] - _num(rows[state][i])) <= 1e-12
    res['config_numbers_equal_strings'] = bool(num_ok)
    res['passed'] = all(v['passed'] for k, v in res.items() if isinstance(v, dict)) and bool(num_ok)
    return res


# ---------------------------------------------------------------- run

def sha256(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def r1_tolerance_ok(x, pub):
    return abs(x - pub) <= max(0.01 * abs(pub), 0.005)


def run_r1(conf):
    g, pubs = conf['golard_table2'], conf['sub6_table2_published']
    out = {}
    for sf, nc in itertools.product(('printed', 'golard'), (1, 3)):
        key = f'{sf}_nc{nc}'
        rows, all_ok, worst = {}, True, 0.0
        for name, cfg in pubs['configs'].items():
            vals = {}
            for case, dl in pubs['cases'].items():
                par = sub6_params(cfg, g, dl['delta_dtx'], dl['delta_idle_trx'], sf, nc)
                vals[f'P0_{case}'] = (par['P0'], cfg['P0_w'][case])
                vals[f'P1_{case}'] = (par['P1'], cfg['P1_w'][case])
                vals['gamma'] = (par['gamma'], cfg['gamma'])
                vals['Psleep'] = (par['Psleep'], cfg['Psleep_w'])
                vals['alpha'] = (par['alpha'], cfg['alpha'])
            row = {}
            for q, (x, pub) in vals.items():
                ok = r1_tolerance_ok(x, pub)
                all_ok &= ok if q != 'alpha' else (x == pub)
                row[q] = {'recomputed': x, 'published': pub, 'rel_err': (x - pub) / pub if pub else x, 'ok': ok}
                if q != 'alpha' and pub:
                    worst = max(worst, abs(x - pub) / abs(pub))
            rows[name] = row
        out[key] = {'configs': rows, 'all_within_tolerance': bool(all_ok), 'max_rel_err': worst}
    matching = [k for k, v in out.items() if v['all_within_tolerance']]
    return {'interpretations': out, 'matching': matching, 'identified': matching[0] if len(matching) == 1 else None,
            'passed': len(matching) >= 1}


def run_j2b(conf):
    g, pubs = conf['golard_table2'], conf['sub6_table2_published']
    worst, n = 0.0, 0
    for (name, cfg), (case, dl), sf, nc in itertools.product(pubs['configs'].items(), pubs['cases'].items(),
                                                             ('printed', 'golard'), (1, 3)):
        par = sub6_params(cfg, g, dl['delta_dtx'], dl['delta_idle_trx'], sf, nc)
        for load, m_a, frac in itertools.product((0.0, 0.3, 1.0), (cfg['K'] + 1, cfg['M']), (0.5, 1.0)):
            p_a = frac * cfg['p_max_w']
            a = sub6_compact(par, cfg['M'], load, m_a, p_a)
            b = sub6_direct(cfg, g, dl['delta_dtx'], dl['delta_idle_trx'], sf, nc, load, m_a, p_a)
            worst, n = max(worst, rel_diff(a, b)), n + 1
    return {'cases': n, 'max_rel_diff': worst, 'passed': worst <= REL_EXACT}


def reference_hw(conf, m_rf, m_ant, upsilon):
    """arXiv v1 reference hardware, as pre-registered (run 1)."""
    ref = conf['fr3_reference_reproduction']['hardware']
    un = conf['fr3_unstated_inputs']
    pa = conf['fr3_model']['pa']['sige']
    return fr3_hardware(conf, ref['fc_hz'], ref['B_hz'], m_ant, m_rf, ref['K'], 100.0 * m_ant / 1024.0, pa,
                        'reference', upsilon, un['rates']['r_dl_full_bps'], un['rates']['r_ul_full_bps'], version='v1')


def reference_hw_pub(conf, m_rf, m_ant):
    """Published (ICASSP 2026) reference hardware: the code's rates (fixed per M_RF), upsilon rule and 120 kHz."""
    ref = conf['fr3_published_reference']
    hw0, rates = ref['hardware'], ref['rates_gbps_times_0p93']
    i = ref['m_rf_vec'].index(m_rf)
    return fr3_hardware(conf, hw0['fc_hz'], hw0['B_hz'], m_ant, m_rf, hw0['K'], 100.0 * m_ant / 1024.0,
                        conf['fr3_model']['pa']['sige'], 'reference', upsilon_published(conf, hw0['fc_hz']),
                        rates['dl'][i] * 1e9 * rates['factor'], rates['ul'][i] * 1e9 * rates['factor'],
                        version='published', rate_rule='fixed', df_hz=hw0['df_hz'])


def run_r2pub(conf):
    ref = conf['fr3_published_reference']
    out, ok_all = {}, True
    for t in ref['targets']:
        if 'M_RF' in t:
            val = fr3_power(reference_hw_pub(conf, t['M_RF'], 1024), t['x'], t['x'])
            _, parts = fr3_power(reference_hw_pub(conf, t['M_RF'], 1024), t['x'], t['x'], parts=True)
        else:
            _, parts = fr3_power(reference_hw_pub(conf, t['M_ant'], t['M_ant']), t['x'], t['x'], parts=True)
            val = parts['pa']
        tol = 0.02 * t['published_w'] if t['tol'].startswith('rel') else 0.1
        ok = abs(val - t['published_w']) <= tol
        ok_all &= ok
        out[t['id']] = {'recomputed_w': val, 'published_w': t['published_w'], 'rel_err': val / t['published_w'] - 1,
                        'ok': ok, 'parts_w': parts}
    return dict(out, passed=bool(ok_all))


def run_j2c(conf):
    worst, n = 0.0, 0
    for m_rf in conf['fr3_published_reference']['m_rf_vec']:
        for x in (0.0, 0.3, 1.0):
            a = fr3_power(reference_hw_pub(conf, m_rf, 1024), x, x)
            worst, n = max(worst, rel_diff(a, fr3_code_reimpl(m_rf, 1024, x)['tot'])), n + 1
            _, pa = fr3_power(reference_hw_pub(conf, m_rf, m_rf), x, x, parts=True)
            worst, n = max(worst, rel_diff(pa['pa'], fr3_code_reimpl(m_rf, m_rf, x)['pa'])), n + 1
    return {'cases': n, 'max_rel_diff': worst, 'passed': worst <= REL_EXACT}


def run_r1u(r1):
    row = r1['interpretations']['golard_nc1']['configs']['64T64R']
    return {'values': row, 'passed': all(v['ok'] for k, v in row.items() if k != 'alpha')}


def run_r2(conf, forms):
    rr = conf['fr3_reference_reproduction']
    ups = conf['fr3_unstated_inputs']['upsilon_coh']
    res = {}
    for sf in forms:
        t_out, tot_ok = {}, True
        for t in rr['targets'][:4]:
            hw = reference_hw(conf, t['M_RF'], 1024, ups['value'])
            val = fr3_power(hw, t['x_dl'], t['x_ul'], sf)
            sens = {str(u): fr3_power(reference_hw(conf, t['M_RF'], 1024, u), t['x_dl'], t['x_ul'], sf) for u in ups['sensitivity']}
            ok = abs(val - t['published_w']) <= 0.02 * t['published_w']
            tot_ok &= ok
            _, parts = fr3_power(hw, t['x_dl'], t['x_ul'], sf, parts=True)
            t_out[t['id']] = {'recomputed_w': val, 'published_w': t['published_w'], 'rel_err': val / t['published_w'] - 1,
                              'ok': ok, 'parts_w': parts, 'upsilon_sensitivity_w': sens}
        pa_ok = {}
        for interp in ('primary', 'alternative'):
            oks = []
            for t in rr['targets'][4:]:
                hw = reference_hw(conf, 16, t['M_ant'], ups['value'])
                _, parts = fr3_power(hw, t['x_dl'], 0.3, sf, parts=True)
                val = parts['pa'] if interp == 'primary' else parts['pa_raw']
                ok = (abs(val - t['published_w']) <= 0.02 * t['published_w']) if t['id'] == 'T5' else (abs(val - t['published_w']) <= 0.5)
                oks.append(ok)
                t_out[f"{t['id']}_{interp}"] = {'recomputed_w': val, 'published_w': t['published_w'], 'ok': ok}
            pa_ok[interp] = all(oks)
        # time-consistent diagnostic (not a pass route): data term with (1 - tau_sig)
        diag = {}
        for t in rr['targets'][:4]:
            hw = reference_hw(conf, t['M_RF'], 1024, ups['value'])
            diag[t['id']] = fr3_power(hw, t['x_dl'], t['x_ul'], sf, frame='consistent')
        res[sf] = {'targets': t_out, 'totals_ok': bool(tot_ok), 'pa_ok': pa_ok,
                   'diagnostic_time_consistent_w': diag,
                   'passed': bool(tot_ok and (pa_ok['primary'] or pa_ok['alternative']))}
    return res


def project_points(conf, eta_3p5):
    lay = conf['project']['layer_7p2']
    un = conf['fr3_unstated_inputs']
    sige = conf['fr3_model']['pa']['sige']
    scale = lay['B_hz'] / conf['fr3_model']['digital']['b_ref_hz']
    pts = {}
    for pname, pp in lay['power_points'].items():
        p_t = dbm_to_w(pp['p_t_dbm'])
        if pp['pa'] == 'sized':
            etas = [(('eta%.2f' % e) if not isinstance(e, str) else 'eta3p5', e if not isinstance(e, str) else eta_3p5) for e in pp['eta_max']]
            pas = [(lab, {'p_max_w': p_t / lay['M_ant'], 'eta_max': ev, 'alpha': pp['alpha'], 'xi': pp['xi']}) for lab, ev in etas]
        else:
            pas = [('SiGe', dict(sige))]
        for (lab, pa), m_rf in itertools.product(pas, [lay['m_rf_primary']] + lay['m_rf_sensitivity']):
            key = f'{pname}_{lab}_MRF{m_rf}'
            pts[key] = fr3_hardware(conf, lay['fc_hz'], lay['B_hz'], lay['M_ant'], m_rf, lay['K'], p_t, pa, 'project',
                                    upsilon_published(conf, lay['fc_hz']), un['rates']['r_dl_full_bps'] * scale,
                                    un['rates']['r_ul_full_bps'] * scale, version='published')
    return pts


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--config', default=str(CONFIG))
    ap.add_argument('--outdir', default=str(OUTDIR))
    ap.add_argument('--git-head', default=None)
    ap.add_argument('--source', action='append', default=[], help='KEY=PATH for every key of config sources')
    args = ap.parse_args()
    t0 = time.time()
    conf = json.loads(Path(args.config).read_text(encoding='utf-8'))
    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    validation, summary = {}, {}
    # ---- J0 sources
    paths = dict(s.split('=', 1) for s in args.source)
    j0 = {}
    for key, src in conf['sources'].items():
        p = paths.get(key)
        h = sha256(p) if p and Path(p).is_file() else None
        j0[key] = {'sha256': h, 'expected': src['sha256'], 'ok': h == src['sha256']}
    validation['J0_sources'] = dict(j0, passed=all(v['ok'] for v in j0.values()))
    print('J0', validation['J0_sources']['passed'], flush=True)
    # ---- J1 TR transcription
    validation['J1_tr_transcription'] = j1_check(paths['tr38864_docx'], conf['tr38864']) if paths.get('tr38864_docx') else {'passed': False}
    print('J1', validation['J1_tr_transcription']['passed'], flush=True)
    tr = conf['tr38864']
    # ---- J2b, R1 sub-6
    validation['J2b_sub6_compact_vs_direct'] = run_j2b(conf)
    r1 = run_r1(conf)
    validation['R1_sub6_table2'] = r1
    print('J2b', validation['J2b_sub6_compact_vs_direct']['passed'], 'R1', r1['passed'], r1['matching'], flush=True)
    validation['R1u_sub6_used_config'] = run_r1u(r1)
    print('R1u', validation['R1u_sub6_used_config']['passed'], flush=True)
    # ---- R2 (arXiv v1, recorded) and R2pub / J2c (published version, amendment 1)
    forms = ['printed']
    r2 = run_r2(conf, forms)
    validation['R2_fr3_published'] = {'by_static_form': r2, 'passed': any(v['passed'] for v in r2.values()),
                                      'note': 'arXiv v1 targets; recorded, excluded from the pass rule by amendment 1'}
    print('R2 (v1, recorded)', validation['R2_fr3_published']['passed'], flush=True)
    validation['J2c_fr3_vs_authors_code'] = run_j2c(conf)
    validation['R2pub_fr3_published'] = run_r2pub(conf)
    print('J2c', validation['J2c_fr3_vs_authors_code']['passed'], 'R2pub', validation['R2pub_fr3_published']['passed'], flush=True)
    # ---- project: 3.5 GHz layer
    g = conf['golard_table2']
    pub64 = conf['sub6_table2_published']['configs']['64T64R']
    lay3 = conf['project']['layer_3p5']
    eta_3p5 = golard_eta_max(pub64['p_max_w'], pub64['f_hz'], g)
    l3 = {}
    for case in (lay3['case_primary'], lay3['case_sensitivity']):
        par = sub6_published_params(pub64, case)
        l3[case] = {str(L): sub6_compact(par, pub64['M'], L, pub64['M'], pub64['p_max_w']) for L in lay3['loads']}
    decomp = None
    if validation['R1u_sub6_used_config']['passed']:
        # amendment 1: decomposition under (golard, N_C = 1), which reproduces all six 64T64R values
        cs = conf['sub6_table2_published']['cases'][lay3['case_primary']]
        decomp = {str(L): sub6_direct(pub64, g, cs['delta_dtx'], cs['delta_idle_trx'], 'golard', 1, L, pub64['M'], pub64['p_max_w'], parts=True)[1]
                  for L in lay3['loads']}
    summary['layer_3p5'] = {'P_w_by_case_and_load': l3, 'decomposition_primary_case': decomp, 'eta_max_golard_eq5': eta_3p5}
    # ---- project: 7.2 GHz layer
    lay7 = conf['project']['layer_7p2']
    pts = project_points(conf, eta_3p5)
    loads = [(x, x) for x in lay7['loads_equal_dl_ul']] + [tuple(v) for v in lay7['loads_extra']]
    j2a_worst, j2a_n = 0.0, 0
    for t in conf['fr3_reference_reproduction']['targets']:
        hw = reference_hw(conf, t.get('M_RF', 16), t.get('M_ant', 1024), conf['fr3_unstated_inputs']['upsilon_coh']['value'])
        for sf in forms:
            xd, xu = t['x_dl'], t.get('x_ul', 0.3)
            j2a_worst = max(j2a_worst, rel_diff(fr3_power(hw, xd, xu, sf), fr3_ledger(hw, xd, xu, sf)))
            j2a_n += 1
    for m_rf in conf['fr3_published_reference']['m_rf_vec']:
        for hw in (reference_hw_pub(conf, m_rf, 1024), reference_hw_pub(conf, m_rf, m_rf)):
            for x in (0.0, 0.3, 1.0):
                j2a_worst = max(j2a_worst, rel_diff(fr3_power(hw, x, x), fr3_ledger(hw, x, x)))
                j2a_n += 1
    project_form = 'golard'  # amendment 1: published version
    p7, j4_ok, j4_fail = {}, True, []
    for key, hw in pts.items():
        rec = {'P_T_w': hw['P_T'], 'P_a_w': hw['P_T'] / hw['M_ant'], 'pa_params': hw['pa'], 'M_RF': hw['M_RF'],
               'M_PS': fr3_active_powers(hw, 0, 0)['m_ps'], 'P_w': {}}
        for xd, xu in loads:
            val, parts = fr3_power(hw, xd, xu, project_form, parts=True)
            rec['P_w'][f'{xd}_{xu}'] = val
            if (xd, xu) in ((1.0, 1.0), (0.3, 0.3)):
                rec[f'parts_w_{xd}_{xu}'] = parts
            j2a_worst = max(j2a_worst, rel_diff(val, fr3_ledger(hw, xd, xu, project_form)))
            j2a_n += 1
        p_mu = fr3_power(hw, 0.0, 0.0, project_form, tau_sig=0.0)
        j2a_worst = max(j2a_worst, rel_diff(p_mu, fr3_ledger(hw, 0.0, 0.0, project_form, tau_sig=0.0)))
        j2a_n += 1
        p_empty = rec['P_w']['0.0_0.0']
        p_dl = fr3_dl_instant(hw, project_form)
        rec.update({'P_mu_w': p_mu, 'P_empty_w': p_empty, 'P_dl_inst_w': p_dl, 'ratio_mu_to_dl_inst': p_mu / p_dl})
        rec['ladder'], rec['breakeven_s'] = {}, {}
        for cat in tr['transition_time_s']:
            for s_name in (conf['project']['ladder']['set_primary'], conf['project']['ladder']['set_sensitivity']):
                lad = ladder(p_mu, tr, cat, s_name)
                rec['ladder'][f'{cat}_{s_name}'] = lad
                rec['breakeven_s'][f'{cat}_{s_name}'] = {st: breakeven_s(p_empty, lad[st]) for st in ('light', 'deep')}
                ok = (0 < lad['deep']['p_w'] < lad['light']['p_w'] < p_mu < p_empty
                      and lad['deep']['e_add_j'] >= 0 and lad['light']['e_add_j'] >= 0)
                if not ok:
                    j4_ok, j4_fail = False, j4_fail + [f'{key} {cat}_{s_name} ladder order']
            rec['ladder_dl_anchor'] = rec.get('ladder_dl_anchor', {})
            rec['ladder_dl_anchor'][f'{cat}_set1'] = ladder_dl_anchor(p_dl, tr, cat, 'set1')
        eq = [rec['P_w'][f'{x}_{x}'] for x in lay7['loads_equal_dl_ul']]
        if not (all(v > 0 for v in eq) and all(b >= a for a, b in zip(eq, eq[1:]))):
            j4_ok, j4_fail = False, j4_fail + [f'{key} load monotonicity']
        if rec['P_a_w'] > hw['pa']['p_max_w'] * (1 + 1e-12):
            j4_fail.append(f'{key} P_a > P_max')
        p7[key] = rec
    summary['layer_7p2'] = {'version': 'published (ICASSP 2026; amendment 1)', 'static_form': project_form, 'points': p7,
                            'upsilon_coh': upsilon_published(conf, lay7['fc_hz']),
                            'primary_point': '53dBm_assumedPA_eta0.15_MRF256'}
    summary['fr3_published_reference'] = {k: v for k, v in validation['R2pub_fr3_published'].items() if k != 'passed'}
    validation['J2a_fr3_two_implementations'] = {'cases': j2a_n, 'max_rel_diff': j2a_worst, 'passed': j2a_worst <= REL_EXACT}
    # 3.5 GHz monotonicity / positivity
    for case, vals in l3.items():
        seq = [vals[str(L)] for L in lay3['loads']]
        if not (all(v > 0 for v in seq) and all(b >= a for a, b in zip(seq, seq[1:]))):
            j4_ok, j4_fail = False, j4_fail + [f'3p5 {case} load monotonicity']
    # ledger worked example and illegal sequences
    led_ok = abs(sequence_energy(WORKED['seq'], WORKED['states']) - WORKED['energy_j']) <= 1e-9
    illegal = [[('on', 1.0), ('deep', 0.01)], [('on', 1.0), ('light', 1.0), ('deep', 1.0)], [('on', 0.0)]]
    rejected = []
    for s in illegal:
        try:
            sequence_energy(s, WORKED['states'])
            rejected.append(False)
        except ValueError:
            rejected.append(True)
    j4_ok = j4_ok and led_ok and all(rejected)
    validation['J4_state_consistency'] = {'ledger_worked_example_ok': led_ok, 'illegal_rejected': rejected,
                                          'failures': j4_fail, 'passed': bool(j4_ok)}
    # ---- J3 D10
    d10 = d10_table(conf['project']['d10_expected'])
    pa_viol = [k for k in j4_fail if 'P_a > P_max' in k]
    validation['J3_rf_pa_consistency'] = dict(d10, project_points_violating=pa_viol,
                                              passed=bool(d10['matches_expected'] and d10['sige_infeasible_at_53'] and not pa_viol))
    summary['d10'] = d10
    # ---- J5 units
    lad = ladder(2.0 * 55, {'relative_power': {'c': {'s': {'micro': 55, 'light': 25, 'deep': 1}}},
                            'transition_time_s': {'c': {'light': 0.006, 'deep': 0.05}},
                            'transition_energy_rel_ms': {'c': {'light': 90, 'deep': 1000}}}, 'c', 's')
    validation['J5_units'] = {'w_per_unit': lad['w_per_unit_w'], 'e_add_light_j': lad['light']['e_add_j'],
                              'passed': abs(lad['w_per_unit_w'] - 2.0) <= 1e-12 and abs(lad['light']['e_add_j'] - 0.18) <= 1e-12}
    for k in ('J2a_fr3_two_implementations', 'J3_rf_pa_consistency', 'J4_state_consistency', 'J5_units'):
        print(k, validation[k]['passed'], flush=True)
    # amendment 1 pass rule: R1 and R2 (v1) are recorded but excluded
    rule = ['J0_sources', 'J1_tr_transcription', 'J2a_fr3_two_implementations', 'J2b_sub6_compact_vs_direct',
            'J2c_fr3_vs_authors_code', 'J3_rf_pa_consistency', 'J4_state_consistency', 'J5_units',
            'R1u_sub6_used_config', 'R2pub_fr3_published']
    gates_ok = all(validation[k]['passed'] for k in rule)
    validation['pass_rule_amendment_1'] = rule
    validation['recorded_not_in_rule'] = {k: validation[k]['passed'] for k in ('R1_sub6_table2', 'R2_fr3_published')}
    validation['all_checks_passed'] = gates_ok
    summary['runtime_s'] = time.time() - t0
    (outdir / 'summary.json').write_text(json.dumps(summary, indent=1, default=float), encoding='utf-8')
    (outdir / 'validation.json').write_text(json.dumps(validation, indent=2, default=float), encoding='utf-8')
    manifest = {'generated_utc': datetime.now(timezone.utc).isoformat(), 'tool': 'scripts/p3_1_energy_models.py',
                'git_head_at_run': args.git_head, 'python': platform.python_version(),
                'config_sha256': sha256(args.config), 'source_sha256': {k: v['sha256'] for k, v in j0.items()},
                'outputs_sha256': {'summary.json': sha256(outdir / 'summary.json'),
                                   'validation.json': sha256(outdir / 'validation.json')},
                'all_checks_passed': gates_ok}
    (outdir / 'manifest.json').write_text(json.dumps(manifest, indent=2), encoding='utf-8')
    print('GATES', 'PASSED' if gates_ok else 'FAILED', f'{time.time() - t0:.1f} s', flush=True)
    return 0 if gates_ok else 1


if __name__ == '__main__':
    sys.exit(main())
