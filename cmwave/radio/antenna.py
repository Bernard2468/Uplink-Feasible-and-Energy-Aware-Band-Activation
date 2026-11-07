"""E1.4 antenna gains toward a UE (link-budget level, no fast fading).

Definitions follow the pre-registration configs/e1/e1_4_antennas_o2i.json.

gNB. Element pattern of TR 38.901 V16.1.0 Table 7.3-1 (G_E,max 8 dBi, 65 deg
3 dB beamwidths, SLA_V = A_max = 30 dB). Three sectors with the bearings of
TR 38.901 Table 7.8-1 (30, 150, 270 deg). Each sector is a uniform planar
array of rows x cols elements per polarisation; only one polarisation is
counted for gain (the convention that reproduces the nominal 29 / 35 dBi of
8x16x2 / 16x32x2 arrays with 8 dBi elements). All TRX ports are steered ideally
toward the geometric UE direction: this is an UPPER BOUND (no CSI error, no
angular-spread loss). Optionally, each TRX feeds a fixed vertical subarray of
K elements with electrical downtilt (TR 38.901 clause 7.3.1 weights).

    G_gNB = G_E,max + A_E(theta, phi - bearing) + 10 log10(rows * cols) + SAF_K(theta)

SAF_K <= 0 dB is the subarray factor normalised to its peak (0 dB for K = 1).
The best sector (largest G_gNB) serves the UE; the pattern depends only on
angles, so the same sector serves the UE at every carrier.

UE. n_rx isotropic (0 dBi) receive elements with maximum-ratio combining:
+10 log10(n_rx) on the average downlink SNR. The uplink is transmitted at a
fixed e.i.r.p., so no transmit-array gain is added.

Angles follow TR 38.901 clause 7.1: zenith 0 deg = up, 90 deg = horizon,
azimuth counter-clockwise from +x. Mechanical downtilt is zero.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class ElementPattern:
    g_max_dbi: float = 8.0
    theta_3db_deg: float = 65.0
    phi_3db_deg: float = 65.0
    sla_v_db: float = 30.0
    a_max_db: float = 30.0


TR38901_ELEMENT = ElementPattern()          # TR 38.901 V16.1.0 Table 7.3-1
TR38901_SECTOR_BEARINGS_DEG = (30.0, 150.0, 270.0)   # TR 38.901 Table 7.8-1


@dataclass(frozen=True)
class GnbPanel:
    rows: int = 1
    cols: int = 1
    subarray_rows: int = 1           # K elements per TRX in elevation (1 = every element steerable)
    etilt_deg: float = 0.0           # electrical downtilt of each subarray, degrees below horizon
    sector_bearings_deg: tuple = TR38901_SECTOR_BEARINGS_DEG
    element: ElementPattern = TR38901_ELEMENT
    isotropic: bool = False          # True: 0 dBi in every direction (E1.3 reference)

    def __post_init__(self):
        if self.rows < 1 or self.cols < 1 or self.subarray_rows < 1:
            raise ValueError('rows, cols and subarray_rows must be >= 1')
        if self.rows % self.subarray_rows:
            raise ValueError('rows must be a multiple of subarray_rows')
        if self.subarray_rows == 1 and self.etilt_deg != 0.0:
            raise ValueError('electrical tilt is defined only for vertical subarrays (K > 1)')
        if self.isotropic and (self.rows, self.cols, self.subarray_rows) != (1, 1, 1):
            raise ValueError('an isotropic panel has a single element')
        if not self.sector_bearings_deg:
            raise ValueError('at least one sector bearing is required')

    @property
    def n_elements(self) -> int:
        return self.rows * self.cols

    @property
    def nominal_gain_dbi(self) -> float:
        """Peak gain G_E,max + 10 log10(rows * cols) (0 dBi if isotropic)."""
        if self.isotropic:
            return 0.0
        return self.element.g_max_dbi + 10.0 * math.log10(self.n_elements)


def wrap_deg(x):
    """Wrap angles to [-180, 180)."""
    return (np.asarray(x, dtype=float) + 180.0) % 360.0 - 180.0


def element_gain_dbi(zenith_deg, azimuth_local_deg, element: ElementPattern = TR38901_ELEMENT):
    """TR 38.901 Table 7.3-1: G_E,max + A(theta, phi) in dBi."""
    th = np.asarray(zenith_deg, dtype=float)
    ph = wrap_deg(azimuth_local_deg)
    a_v = -np.minimum(12.0 * ((th - 90.0) / element.theta_3db_deg) ** 2, element.sla_v_db)
    a_h = -np.minimum(12.0 * (ph / element.phi_3db_deg) ** 2, element.a_max_db)
    return element.g_max_dbi - np.minimum(-(a_v + a_h), element.a_max_db)


def subarray_factor_db(zenith_deg, k: int, etilt_deg: float, spacing_wavelengths: float = 0.5):
    """Normalised power factor of a K-element vertical subarray (<= 0 dB).

    TR 38.901 clause 7.3.1 weights w_m = exp(-j 2 pi (m-1) d_V cos(theta_etilt) / lambda) / sqrt(K),
    theta_etilt = 90 deg + downtilt. Evaluated as an explicit phasor sum.
    """
    th = np.asarray(zenith_deg, dtype=float)
    if k == 1:
        return np.zeros_like(th)
    th_t = math.radians(90.0 + etilt_deg)
    psi = 2.0 * math.pi * spacing_wavelengths * (np.cos(np.radians(th)) - math.cos(th_t))
    m = np.arange(k)
    s = np.exp(1j * psi[..., None] * m).sum(axis=-1)
    with np.errstate(divide='ignore'):
        return 10.0 * np.log10(np.abs(s) ** 2 / k ** 2)


def link_angles(gnb_xyz, ue_xyz):
    """Zenith and azimuth [deg] of the UE seen from the gNB (GCS)."""
    g = np.asarray(gnb_xyz, dtype=float)
    u = np.atleast_2d(np.asarray(ue_xyz, dtype=float))
    dx, dy, dz = u[:, 0] - g[0], u[:, 1] - g[1], u[:, 2] - g[2]
    d2 = np.hypot(dx, dy)
    zenith = np.degrees(np.arctan2(d2, dz))      # dz < 0 (UE below) -> zenith > 90 deg
    azimuth = np.degrees(np.arctan2(dy, dx))
    return zenith, azimuth


def gnb_gain_dbi(panel: GnbPanel, zenith_deg, azimuth_deg):
    """Gain of the best sector toward each direction and the index of that sector.

    Only the element term differs between sectors (array and subarray terms do
    not), so the sector is chosen on the element pattern alone: the choice is
    then identical at every carrier. Ties go to the first sector listed.
    """
    th = np.asarray(zenith_deg, dtype=float)
    ph = np.asarray(azimuth_deg, dtype=float)
    if th.shape != ph.shape:
        raise ValueError('zenith and azimuth must have the same shape')
    if panel.isotropic:
        return np.zeros_like(th), np.zeros(th.shape, dtype=int)
    elem = np.stack([element_gain_dbi(th, ph - b, panel.element) for b in panel.sector_bearings_deg])
    best = np.argmax(elem, axis=0)
    g_elem = np.take_along_axis(elem, best[None, ...], axis=0)[0]
    saf = subarray_factor_db(th, panel.subarray_rows, panel.etilt_deg)
    return g_elem + 10.0 * math.log10(panel.n_elements) + saf, best


def ue_rx_gain_db(n_rx: int) -> float:
    """MRC array gain on the average SNR of n_rx isotropic elements."""
    if n_rx < 1:
        raise ValueError('n_rx must be >= 1')
    return 10.0 * math.log10(n_rx)
