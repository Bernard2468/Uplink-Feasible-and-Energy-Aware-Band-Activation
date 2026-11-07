"""E1.3 single-link budgets (no interference: SNR, not SINR).

Definitions follow the pre-registration configs/e1/e1_3_link_budget.json.
All powers in dBm, gains in dBi, losses in dB (positive), bandwidths in Hz.

Downlink: gNB total power spread uniformly over the occupied bandwidth
N_PRB * 12 * SCS; the UE is allocated all PRBs; noise over that bandwidth with
the UE noise figure.
Uplink: the UE transmits its maximum power on n PRBs; noise over n PRBs with
the gNB noise figure; reported rate = max over n of n * PRB_bw * SE(SNR(n)).
E1.4: downlink() and uplink() accept an optional per-link antenna gain
(gain_db, direction-specific); without it the E1.3 computation is unchanged.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

THERMAL_NOISE_DBM_PER_HZ = -174.0   # ITU-R M.2412-0 Table 5(b)
SUBCARRIERS_PER_PRB = 12            # 3GPP TS 38.211
# 3GPP TR 36.942 V19.0.0 Annex A.1 Table A.1 (continuity: Table A.2).
TRUNCATED_SHANNON = {'DL': {'alpha': 0.6, 'snr_min_db': -10.0, 'thr_max': 4.4},
                     'UL': {'alpha': 0.4, 'snr_min_db': -10.0, 'thr_max': 2.0}}


@dataclass(frozen=True)
class CarrierConfig:
    frequency_hz: float
    channel_bw_hz: float
    scs_hz: float
    n_prb: int
    gnb_power_dbm: float     # TOTAL over the carrier (all antenna elements)
    ue_power_dbm: float      # maximum UE output power
    nf_gnb_db: float
    nf_ue_db: float
    gain_gnb_dbi: float
    gain_ue_dbi: float

    def __post_init__(self):
        if min(self.frequency_hz, self.channel_bw_hz, self.scs_hz) <= 0 or self.n_prb < 1:
            raise ValueError('frequency, bandwidth, SCS and N_PRB must be positive')
        if self.occupied_bw_hz > self.channel_bw_hz:
            raise ValueError('N_PRB * 12 * SCS exceeds the channel bandwidth')
        if min(self.nf_gnb_db, self.nf_ue_db) < 0:
            raise ValueError('noise figures must be non-negative')

    @property
    def prb_bw_hz(self) -> float:
        return SUBCARRIERS_PER_PRB * self.scs_hz

    @property
    def occupied_bw_hz(self) -> float:
        return self.n_prb * SUBCARRIERS_PER_PRB * self.scs_hz


def noise_dbm(bw_hz, nf_db):
    return THERMAL_NOISE_DBM_PER_HZ + 10.0 * np.log10(bw_hz) + nf_db


def shannon_se(snr_db):
    return np.log2(1.0 + 10.0 ** (np.asarray(snr_db, dtype=float) / 10.0))


def mapped_se(snr_db, direction: str):
    """TR 36.942 attenuated and truncated Shannon bound [bit/s/Hz]."""
    p = TRUNCATED_SHANNON[direction]
    snr = np.asarray(snr_db, dtype=float)
    se = np.minimum(p['alpha'] * shannon_se(snr), p['thr_max'])
    return np.where(snr >= p['snr_min_db'], se, 0.0)


def _link_gain(gain_db, loss, cfg: CarrierConfig):
    """Optional per-link antenna gain [dB] (E1.4). None keeps the E1.3 behaviour.

    A per-link gain replaces the scalar config gains, which must then be zero
    so that no gain is counted twice.
    """
    if gain_db is None:
        return None
    if cfg.gain_gnb_dbi != 0.0 or cfg.gain_ue_dbi != 0.0:
        raise ValueError('per-link gain given: config gains must be 0 (no double counting)')
    g = np.broadcast_to(np.asarray(gain_db, dtype=float), loss.shape)
    if not np.all(np.isfinite(g)):
        raise ValueError('per-link gains must be finite')
    return g


def downlink(loss_db, cfg: CarrierConfig, gain_db=None) -> dict:
    """gain_db: optional per-link gNB transmit + UE receive gain [dB]."""
    loss = np.asarray(loss_db, dtype=float)
    g = _link_gain(gain_db, loss, cfg)
    b = cfg.occupied_bw_hz
    rx = cfg.gnb_power_dbm + cfg.gain_gnb_dbi + cfg.gain_ue_dbi - loss
    if g is not None:
        rx = rx + g
    n = noise_dbm(b, cfg.nf_ue_db)
    snr = rx - n
    se = mapped_se(snr, 'DL')
    gain = np.full_like(loss, cfg.gain_gnb_dbi + cfg.gain_ue_dbi) if g is None else g.copy()
    return {'tx_power_dbm': np.full_like(loss, cfg.gnb_power_dbm), 'bandwidth_hz': np.full_like(loss, b),
            'rx_power_dbm': rx, 'noise_dbm': np.full_like(loss, n), 'snr_db': snr, 'se_bps_hz': se,
            'rate_bps': se * b, 'shannon_rate_bps': shannon_se(snr) * b,
            'antenna_gain_db': gain}


def uplink(loss_db, cfg: CarrierConfig, gain_db=None) -> dict:
    """gain_db: optional per-link UE transmit + gNB receive gain [dB]."""
    loss = np.asarray(loss_db, dtype=float)
    g = _link_gain(gain_db, loss, cfg)
    n_alloc = np.arange(1, cfg.n_prb + 1)
    b_alloc = n_alloc * cfg.prb_bw_hz                                  # (A,)
    rx = cfg.ue_power_dbm + cfg.gain_ue_dbi + cfg.gain_gnb_dbi - loss  # (N,)
    if g is not None:
        rx = rx + g
    noise = noise_dbm(b_alloc, cfg.nf_gnb_db)                          # (A,)
    snr = rx[:, None] - noise[None, :]                                 # (N, A)
    rate = mapped_se(snr, 'UL') * b_alloc[None, :]
    # First maximum -> smallest n. If no allocation has a rate, argmax is index 0
    # (1 PRB, the most favourable SNR); best_n_prb is then reported as 0, while the
    # evaluated bandwidth/noise/SNR all refer to that same 1-PRB allocation.
    best = np.argmax(rate, axis=1)
    rows = np.arange(loss.size)
    rate_max = rate[rows, best]
    has_rate = rate_max > 0
    return {'tx_power_dbm': np.full_like(loss, cfg.ue_power_dbm), 'rx_power_dbm': rx,
            'snr_1prb_db': snr[:, 0], 'noise_1prb_dbm': np.full_like(loss, noise[0]),
            'snr_full_db': snr[:, -1], 'noise_full_dbm': np.full_like(loss, noise[-1]),
            'best_n_prb': np.where(has_rate, n_alloc[best], 0),
            'eval_bw_hz': b_alloc[best], 'noise_eval_dbm': noise[best],
            'snr_best_db': snr[rows, best], 'rate_bps': rate_max,
            'rate_1prb_bps': rate[:, 0], 'rate_full_bps': rate[:, -1],
            'se_norm_bps_hz': rate_max / cfg.occupied_bw_hz,
            'shannon_rate_full_bps': shannon_se(snr[:, -1]) * cfg.occupied_bw_hz,
            'antenna_gain_db': (np.full_like(loss, cfg.gain_gnb_dbi + cfg.gain_ue_dbi)
                                if g is None else g.copy())}
