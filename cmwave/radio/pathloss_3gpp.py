"""3GPP TR 38.901 Table 7.4.1-1 median path loss (UMa, UMi-Street Canyon).

Used ONLY as an independent statistical reference for ray-traced results, never
as the propagation model of the study. Formulas transcribed from, and checked
against, ns-3.48 src/propagation/model/three-gpp-propagation-loss-model.cc
(ThreeGppUmaPropagationLossModel, ThreeGppUmiStreetCanyonPropagationLossModel).

Conventions: distances in metres, carrier frequency in Hz, results in dB
(positive loss). Deterministic medians only: no shadow fading is drawn.
UMa effective environment height: TR 38.901 Note 1 gives hE = 1 m with
probability 1/(1 + C(d2D, hUT)) and C = 0 for hUT < 13 m, so hE = 1 m exactly
for the pedestrian heights used here. Taller UTs are rejected rather than
silently given a random hE.
"""
from __future__ import annotations

import math

C0 = 299_792_458.0  # m/s, identical to ns-3 M_C

# Shadow-fading standard deviations (dB), TR 38.901 Table 7.4.1-1.
SHADOWING_STD_DB = {
    ('uma', 'los'): 4.0, ('uma', 'nlos'): 6.0,
    ('umi', 'los'): 4.0, ('umi', 'nlos'): 7.82,
}


def _check(d2d: float, d3d: float, fc_hz: float, h_bs: float, h_ut: float) -> None:
    if not (d2d > 0 and d3d >= d2d and fc_hz > 0 and h_bs > h_ut > 0):
        raise ValueError('require d3d >= d2d > 0, fc > 0 and h_bs > h_ut > 0')
    if not 0.5e9 <= fc_hz <= 100e9:
        raise ValueError('TR 38.901 is specified for 0.5-100 GHz')


def breakpoint_distance(fc_hz: float, h_bs: float, h_ut: float, h_e: float = 1.0) -> float:
    """d'BP = 4 (hBS - hE)(hUT - hE) fc / c (TR 38.901 Table 7.4.1-1 Note 1)."""
    return 4.0 * (h_bs - h_e) * (h_ut - h_e) * fc_hz / C0


def uma_los(d2d: float, d3d: float, fc_hz: float, h_bs: float, h_ut: float) -> float:
    _check(d2d, d3d, fc_hz, h_bs, h_ut)
    if h_ut >= 13.0:
        raise ValueError('hUT >= 13 m makes hE random in UMa; not supported here')
    f_ghz = fc_hz / 1e9
    dbp = breakpoint_distance(fc_hz, h_bs, h_ut, 1.0)
    if d2d <= dbp:
        return 28.0 + 22.0 * math.log10(d3d) + 20.0 * math.log10(f_ghz)
    return (28.0 + 40.0 * math.log10(d3d) + 20.0 * math.log10(f_ghz)
            - 9.0 * math.log10(dbp ** 2 + (h_bs - h_ut) ** 2))


def uma_nlos(d2d: float, d3d: float, fc_hz: float, h_bs: float, h_ut: float) -> float:
    pl_nlos = (13.54 + 39.08 * math.log10(d3d) + 20.0 * math.log10(fc_hz / 1e9)
               - 0.6 * (h_ut - 1.5))
    return max(uma_los(d2d, d3d, fc_hz, h_bs, h_ut), pl_nlos)


def uma_c(d2d: float, h_ut: float) -> float:
    """TR 38.901 Table 7.4.1-1 Note 1: C(d2D, hUT) = 0 for hUT < 13 m, else
    ((hUT - 13)/10)^1.5 g(d2D), g = 0 for d2D <= 18 m, (5/4)(d2D/100)^3 exp(-d2D/150) otherwise."""
    if h_ut < 13.0:
        return 0.0
    g = 0.0 if d2d <= 18.0 else 1.25 * (d2d / 100.0) ** 3 * math.exp(-d2d / 150.0)
    return ((h_ut - 13.0) / 10.0) ** 1.5 * g


def uma_he_distribution(d2d: float, h_ut: float) -> list[tuple[float, float]]:
    """TR 38.901 Table 7.4.1-1 Note 1 (E1c): hE = 1 m with probability 1/(1 + C), otherwise
    discrete uniform over {12, 15, ..., hUT - 1.5}. Returns [(hE, probability)], probabilities sum
    to 1. Valid for 1.5 <= hUT <= 22.5 m."""
    if not 1.5 <= h_ut <= 22.5:
        raise ValueError('UMa hUT must lie in [1.5, 22.5] m')
    p1 = 1.0 / (1.0 + uma_c(d2d, h_ut))
    others = [12.0 + 3.0 * k for k in range(int(math.floor((h_ut - 1.5 - 12.0) / 3.0 + 1e-9)) + 1)] \
        if h_ut - 1.5 >= 12.0 else []
    if not others or p1 == 1.0:
        return [(1.0, 1.0)]
    q = (1.0 - p1) / len(others)
    return [(1.0, p1)] + [(h, q) for h in others]


def uma_los_he(d2d: float, d3d: float, fc_hz: float, h_bs: float, h_ut: float, h_e: float) -> float:
    """UMa LOS with an explicit effective environment height hE (E1c upper floors).
    Equals uma_los for hE = 1 m and hUT < 13 m."""
    _check(d2d, d3d, fc_hz, h_bs, h_ut)
    if not 1.5 <= h_ut <= 22.5 or not (0 < h_e < h_ut and h_e < h_bs):
        raise ValueError('require 1.5 <= hUT <= 22.5 m and 0 < hE < min(hUT, hBS)')
    f_ghz = fc_hz / 1e9
    dbp = breakpoint_distance(fc_hz, h_bs, h_ut, h_e)
    if d2d <= dbp:
        return 28.0 + 22.0 * math.log10(d3d) + 20.0 * math.log10(f_ghz)
    return (28.0 + 40.0 * math.log10(d3d) + 20.0 * math.log10(f_ghz)
            - 9.0 * math.log10(dbp ** 2 + (h_bs - h_ut) ** 2))


def uma_nlos_he(d2d: float, d3d: float, fc_hz: float, h_bs: float, h_ut: float, h_e: float) -> float:
    pl_nlos = (13.54 + 39.08 * math.log10(d3d) + 20.0 * math.log10(fc_hz / 1e9)
               - 0.6 * (h_ut - 1.5))
    return max(uma_los_he(d2d, d3d, fc_hz, h_bs, h_ut, h_e), pl_nlos)


def umi_los(d2d: float, d3d: float, fc_hz: float, h_bs: float, h_ut: float) -> float:
    _check(d2d, d3d, fc_hz, h_bs, h_ut)
    f_ghz = fc_hz / 1e9
    dbp = breakpoint_distance(fc_hz, h_bs, h_ut, 1.0)
    if d2d <= dbp:
        return 32.4 + 21.0 * math.log10(d3d) + 20.0 * math.log10(f_ghz)
    return (32.4 + 40.0 * math.log10(d3d) + 20.0 * math.log10(f_ghz)
            - 9.5 * math.log10(dbp ** 2 + (h_bs - h_ut) ** 2))


def umi_nlos(d2d: float, d3d: float, fc_hz: float, h_bs: float, h_ut: float) -> float:
    pl_nlos = (22.4 + 35.3 * math.log10(d3d) + 21.3 * math.log10(fc_hz / 1e9)
               - 0.3 * (h_ut - 1.5))
    return max(umi_los(d2d, d3d, fc_hz, h_bs, h_ut), pl_nlos)


def uma_los_probability(d2d: float, h_ut: float) -> float:
    """TR 38.901 Table 7.4.2-1 UMa LOS probability (ns-3 ComputePlos)."""
    if d2d <= 0 or h_ut <= 0:
        raise ValueError('distance and height must be positive')
    if d2d <= 18.0:
        return 1.0
    c = 0.0 if h_ut <= 13.0 else ((h_ut - 13.0) / 10.0) ** 1.5
    return ((18.0 / d2d + math.exp(-d2d / 63.0) * (1.0 - 18.0 / d2d))
            * (1.0 + c * 5.0 / 4.0 * (d2d / 100.0) ** 3 * math.exp(-d2d / 150.0)))


def free_space_loss(d3d: float, fc_hz: float) -> float:
    """Friis loss between isotropic antennas, 20 log10(4 pi d f / c)."""
    if d3d <= 0 or fc_hz <= 0:
        raise ValueError('distance and frequency must be positive')
    return 20.0 * math.log10(4.0 * math.pi * d3d * fc_hz / C0)


MODELS = {('uma', 'los'): uma_los, ('uma', 'nlos'): uma_nlos,
          ('umi', 'los'): umi_los, ('umi', 'nlos'): umi_nlos}
