# Draft issue for 5G-LENA: DirectPath beam anti-matched on the receive side since ns-3 #1314

Status: DRAFT. Not filed; filing is the repository owner's decision.

## Versions

- ns-3: ns-3.48 (our tree `d083a17a`, which contains `80c131f7f`, "spectrum: (fixes #1314)
  Use std::conj for RX beamforming in long-term component").
- 5G-LENA: `5g-lena-v5.1.y`, commit `cedceadd`.

## Summary

Since ns-3 #1314, `ThreeGppSpectrumPropagationLossModel` forms the long-term component as
w_u^H H w_s: the weights of the receive-side antenna `u` of the stored channel matrix are
conjugated. `CreateDirectPathBfv` (`model/beamforming-vector.cc`) returns
w = exp(-j 2π r̂·d) / √N for both the gNB and the UE (`ideal-beamforming-algorithm.cc`,
the calls at lines 381 and 387). With the channel's ray phase exp(+j 2π r̂·d):
- on the transmit side `s`, Σ w_k exp(+j 2π r̂·d_k) = √N, so the beam is matched;
- on the receive side `u`, Σ conj(w_k) exp(+j 2π r̂·d_k) = Σ exp(+j 4π r̂·d_k) / √N, so the
  beam is anti-matched.

5G-LENA's realistic beamforming was updated for #1314 (NR `02da6eef`). DirectPath was not.

## What we measured

Setup:
- TR 38.901 UMa, 3.5 and 7.2 GHz, prescribed per-link condition;
- gNB uniform planar array 8 x 16, TR 38.901 element, bearings 30/150/270 deg;
- UE with one isotropic element;
- 27 links (9 LOS), 20 independent realizations each;
- gain = Σ rx PSD / Σ tx PSD from `ThreeGppSpectrumPropagationLossModel::CalcRxPowerSpectralDensity`.

| gNB role in the stored matrix | gNB weights | median LOS gain vs ideal steering |
|---|---|---|
| s (generated in DL) | DirectPath | -0.62 dB (3.5 GHz), -0.40 dB (7.2 GHz) |
| u (generated in UL) | DirectPath | about 33.5 / 35.9 dB below the role-s case |
| u (generated in UL) | conj(DirectPath) | identical to the role-s case |

The deterministic array factor of the DirectPath vector, evaluated in the receive-side form
toward the LOS direction, predicts the same loss.

In full NR runs where the first transmission is DL, the gNB is always `s` (we checked
`ChannelMatrix::IsReverse` for every link), so the gNB's DirectPath gain is correct. A
multi-element UE receiving in DL is then on the `u` side, and its DirectPath combiner is
anti-matched.

We also checked that the NR DL TB SINR equals the channel-level gain recomputed on the same
objects, within 3e-14 dB, for 8,476 TBs (TDMA DL, full band).

## Expected

DirectPath should give the matched array gain at both ends, whichever end transmitted first.

## Possible fixes (for the maintainers to judge)

- Build the receive-side vector with the opposite sign, as ns-3's
  `three-gpp-channel-example.cc` now does (sign -1 for TX, +1 for RX). This is only correct
  if the receiving device is the `u` side of the stored matrix. That depends on which
  direction first generated the channel, so a role-aware choice is needed, for example by
  querying `ChannelMatrix::IsReverse`.
- Alternatively, make the long-term computation reciprocal with respect to which device
  holds which weights.

## Note

A related observation, not yet diagnosed: with DirectPath, all UEs share the empty BeamId,
so the OFDMA schedulers can co-schedule UEs in the same symbols. The reported SINR of such
partial-band TBs differs from the channel-level gain of the UE's own beam by -54 to +13 dB.
We will add a reproduction once it is understood.
