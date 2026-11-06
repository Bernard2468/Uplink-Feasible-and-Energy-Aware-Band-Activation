"""E1.2: geometry-conditioned TR 38.901 UMa large-scale propagation.

The LOS/NLOS state of every link is an INPUT (frozen scene geometry, E1.1);
it is never drawn from the TR 38.901 LOS probability. Per link and carrier:

    total_loss_db = pathloss_db(state, d2d, d3d, fc, h_bs, h_ut) + shadowing_db

Shadow fading (TR 38.901, sources in docs/E1_PARAMETERS.md):
  * zero-mean normal, std 4 dB (LOS) / 6 dB (NLOS), UMa Table 7.4.1-1;
  * spatially correlated, exponential, correlation distance 37 m (LOS) /
    50 m (NLOS), Table 7.5-6; LOS and NLOS are separate, independent fields
    and each link uses the field of its geometric state;
  * ONE standard-normal variate per link, shared by all carriers (clause
    7.6.5); the UMa std does not depend on frequency, so the shadowing in dB is
    identical at every carrier.
Path loss is a positive loss in dB; received power = tx power + gains - loss.
Propagation is reciprocal: the same loss applies to uplink and downlink.
"""
from __future__ import annotations

import math

import numpy as np

from . import pathloss_3gpp as pl3

MODEL_VERSION = 'e1.2-uma-geometry-conditioned-v1'
SF_STD_DB = {'LOS': 4.0, 'NLOS': 6.0}          # TR 38.901 Table 7.4.1-1 (UMa)
SF_CORR_DIST_M = {'LOS': 37.0, 'NLOS': 50.0}   # TR 38.901 Table 7.5-6 (UMa)
STATES = ('LOS', 'NLOS')
MAX_POINTS_PER_STATE = 5000  # dense Cholesky limit; maps need a field generator


def deterministic_pathloss_db(d2d: float, d3d: float, fc_hz: float, h_bs: float,
                              h_ut: float, los_state: str) -> float:
    if los_state == 'LOS':
        return pl3.uma_los(d2d, d3d, fc_hz, h_bs, h_ut)
    if los_state == 'NLOS':
        return pl3.uma_nlos(d2d, d3d, fc_hz, h_bs, h_ut)
    raise ValueError(f'unknown LOS state {los_state!r}')


def shadowing_normals(xy: np.ndarray, states: list[str], n_real: int,
                      seed: int) -> np.ndarray:
    """Standard-normal shadowing variates, shape (n_real, n_points).

    xy: (n_points, 2) horizontal UE positions [m]; states: 'LOS'/'NLOS' per point.
    Draw order is fixed (LOS field, then NLOS field) so results depend only on
    (xy, states, n_real, seed).
    """
    xy = np.asarray(xy, dtype=float)
    states = list(states)
    if xy.ndim != 2 or xy.shape[1] != 2 or len(states) != xy.shape[0]:
        raise ValueError('xy must be (n, 2) with one state per point')
    if n_real < 1:
        raise ValueError('n_real must be >= 1')
    bad = set(states) - set(STATES)
    if bad:
        raise ValueError(f'unknown LOS states {bad}')
    rng = np.random.default_rng(seed)
    out = np.empty((n_real, len(states)))
    for state in STATES:
        idx = np.array([i for i, s in enumerate(states) if s == state], dtype=int)
        if idx.size == 0:
            continue
        if idx.size > MAX_POINTS_PER_STATE:
            raise ValueError('too many points for a dense Cholesky factorisation')
        p = xy[idx]
        dist = np.hypot(p[:, None, 0] - p[None, :, 0], p[:, None, 1] - p[None, :, 1])
        cov = np.exp(-dist / SF_CORR_DIST_M[state])
        chol = np.linalg.cholesky(cov + 1e-10 * np.eye(idx.size))
        white = rng.standard_normal((n_real, idx.size))
        out[:, idx] = white @ chol.T
    return out


def compute_links(links: list[dict], carriers_hz: list[float], n_real: int,
                  seed: int) -> list[dict]:
    """One row per (realization, link, carrier).

    links: dicts with ue_id, x_m, y_m, d2d_m, d3d_m, h_bs_m, h_ut_m, los_state.
    """
    xy = np.array([[l['x_m'], l['y_m']] for l in links], dtype=float)
    states = [l['los_state'] for l in links]
    normals = shadowing_normals(xy, states, n_real, seed)
    pl = {(j, f): deterministic_pathloss_db(l['d2d_m'], l['d3d_m'], f, l['h_bs_m'],
                                            l['h_ut_m'], l['los_state'])
          for j, l in enumerate(links) for f in carriers_hz}
    rows = []
    for r in range(n_real):
        for j, l in enumerate(links):
            sf = SF_STD_DB[l['los_state']] * float(normals[r, j])
            for f in carriers_hz:
                rows.append({'realization': r, 'ue_id': l['ue_id'], 'frequency_hz': f,
                             'los_state': l['los_state'], 'distance_2d_m': l['d2d_m'],
                             'distance_3d_m': l['d3d_m'], 'pathloss_db': pl[(j, f)],
                             'shadowing_db': sf, 'shadowing_normal': float(normals[r, j]),
                             'total_loss_db': pl[(j, f)] + sf, 'seed': seed,
                             'model_version': MODEL_VERSION})
    return rows


def frequency_gap_db(f1_hz: float, f2_hz: float) -> float:
    """Model frequency gap for NLOS links and LOS links inside the breakpoint."""
    return 20.0 * math.log10(f2_hz / f1_hz)
