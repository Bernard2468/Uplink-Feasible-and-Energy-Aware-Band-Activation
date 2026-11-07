"""E1.4 outdoor-to-indoor (O2I) building and in-car penetration loss.

Source: TR 38.901 V16.1.0 clause 7.4.3 (identical to ITU-R M.2412-0 Annex 1
section 3.2, channel model B, Table A1-8, and section 3.3).

Building, eq. (7.4-2):  PL = PL_b + PL_tw + PL_in + N(0, sigma_P^2), where PL_b is
the outdoor path loss with d3D replaced by d3D-out + d3D-in.
    PL_tw = PL_npi - 10 log10( sum_i p_i 10^(-L_i / 10) ),  PL_npi = 5 dB
    low-loss model:  30 % standard glass + 70 % concrete,  sigma_P = 4.4 dB
    high-loss model: 70 % IRR glass + 30 % concrete,       sigma_P = 6.5 dB
    PL_in = 0.5 d2D-in, d2D-in = min of two independent U(0, 25 m) draws (UMa)
Material losses L = a + b f (f in GHz), Table 7.4.3-1.
Car, eq. (7.4-4):  PL = PL_b + N(9, 5^2) dB, applicable at least 0.6-60 GHz.

O2I links use the UMa O2I shadow-fading std of Table 7.5-6 Part-1 (7 dB).

NOT used: the legacy model of Table 7.4.3-3 (20 dB wall loss below 6 GHz). The
TR restricts it to single-frequency simulations below 6 GHz; in a two-carrier
study it would give the 3.5 GHz carrier a larger wall loss than the
frequency-dependent model gives at 7.2 GHz (12.7 dB low-loss).
"""
from __future__ import annotations

import math

import numpy as np

MATERIAL_LOSS_DB = {             # (a, b): L = a + b * f_GHz, Table 7.4.3-1
    'standard_glass': (2.0, 0.2),
    'irr_glass': (23.0, 0.3),
    'concrete': (5.0, 4.0),
    'wood': (4.85, 0.12),
}
PL_NPI_DB = 5.0                  # Table 7.4.3-2 (non-perpendicular incidence term)
BUILDING_MODELS = {              # Table 7.4.3-2
    'low': {'mix': (('standard_glass', 0.3), ('concrete', 0.7)), 'sigma_p_db': 4.4},
    'high': {'mix': (('irr_glass', 0.7), ('concrete', 0.3)), 'sigma_p_db': 6.5},
}
INDOOR_LOSS_DB_PER_M = 0.5       # PL_in = 0.5 d2D-in
D2D_IN_MAX_M = 25.0              # UMa and UMi-Street Canyon
CAR_MU_DB = 9.0                  # clause 7.4.3.2 (metallised windows: 20 dB, not used)
CAR_SIGMA_DB = 5.0
O2I_SF_STD_DB = 7.0              # Table 7.5-6 Part-1, UMa, O2I column
O2I_SF_CORR_DIST_M = 7.0         # Table 7.5-6 Part-1, UMa, O2I column


def material_loss_db(material: str, fc_hz: float) -> float:
    a, b = MATERIAL_LOSS_DB[material]
    return a + b * fc_hz / 1e9


def wall_loss_db(model: str, fc_hz: float) -> float:
    """PL_tw of the low- or high-loss building model at carrier fc_hz."""
    if not 0.5e9 <= fc_hz <= 100e9:
        raise ValueError('TR 38.901 is specified for 0.5-100 GHz')
    mix = BUILDING_MODELS[model]['mix']
    if abs(sum(p for _, p in mix) - 1.0) > 1e-12:
        raise ValueError('material proportions must sum to 1')
    s = sum(p * 10.0 ** (-material_loss_db(m, fc_hz) / 10.0) for m, p in mix)
    return PL_NPI_DB - 10.0 * math.log10(s)


def sigma_p_db(model: str) -> float:
    return BUILDING_MODELS[model]['sigma_p_db']


def indoor_distance_m(u1, u2, d_max_m: float = D2D_IN_MAX_M):
    """d2D-in from two independent U(0, 1) draws: d_max * min(u1, u2)."""
    u1, u2 = np.asarray(u1, dtype=float), np.asarray(u2, dtype=float)
    if np.any((u1 < 0) | (u1 > 1) | (u2 < 0) | (u2 > 1)):
        raise ValueError('uniform draws must lie in [0, 1]')
    return d_max_m * np.minimum(u1, u2)


def indoor_loss_db(d2d_in_m):
    return INDOOR_LOSS_DB_PER_M * np.asarray(d2d_in_m, dtype=float)
