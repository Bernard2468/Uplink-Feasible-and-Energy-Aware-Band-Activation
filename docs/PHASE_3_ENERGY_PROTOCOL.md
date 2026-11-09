# Phase 3: energy model and band-activation control (protocol)

Status: **P3.0 design proposal (2026-11-09).** Nothing in this file is
pre-registered yet. Each stage below gets its own config, code, tests and
pre-registration commit before its first measured run, as in Phase 2.

## 0. What Phase 3 must answer

From `docs/RESEARCH_DESIGN.md`, narrowed by the Phase 2 results:

- **Q1 (energy).** When does activating the 7.2 GHz layer of a co-sited
  3.5/7.2 GHz TRxP deliver enough additional *useful* service (user-level
  UL and DL targets met) to justify its full operating energy, including
  idle, sleep and transition costs?
- **Q2 (uplink).** How much energy saving, and how much service, does a
  UL-agnostic activation rule claim that a UL-feasible rule does not?
  This is hypothesis H2 of the research design.
- **Q3 (control).** How close does a causal online controller come to the
  clairvoyant optimum under the same constraints?
- **Q4 (boundaries).** In which load, geometry (ISD 200 / 500), hardware
  category and power-model regimes is there no net benefit (H4)?

Phase 2 supplies, per location and per realization, the feasibility class of
each UE (K1–K6, E1c.2/E1c.3) and its SINR per carrier and direction. Phase 3
does not change the radio model.

## 1. Sources verified for Phase 3 (2026-11-09)

Every value below was read from the primary document. Each file was
downloaded into an isolated folder; hashes are recorded so the reading can
be repeated.

### 1.1 3GPP TR 38.864 V18.1.0 (Study on network energy savings for NR)

Source: `https://www.3gpp.org/ftp/Specs/archive/38_series/38.864/38864-i10.zip`.
SHA-256 `99770bf7a80e9ae26184e6c76f6c421464fcb8dd9bb56e5cf7d0f2bba5c08e1f`.
Clause 5.1 says these values are "for RAN1 evaluation purpose".

Table 5.1-1, reference configurations:

| | Set 1 (FR1) | Set 2 (FR1) | Set 3 (FR2) |
|---|---|---|---|
| Duplex | TDD | FDD | TDD |
| System BW | 100 MHz | 20 MHz | 100 MHz |
| SCS | 30 kHz | 15 kHz | 120 kHz |
| DL TxRUs / UL RxRUs | 64 / 64 | 32 / 32 | 2 / 2 |
| Total DL power | 55 dBm | 49 dBm | 33 dBm |

Table 5.1-3, relative power P. Units are relative within the table, with
deep sleep = 1:

| State | Cat 1 Set 1 | Cat 1 Set 2 | Cat 1 Set 3 | Cat 2 Set 1 | Cat 2 Set 2 | Cat 2 Set 3 |
|---|---|---|---|---|---|---|
| Deep sleep | 1 | 1 | 1 | 1 | 1 | 1 |
| Light sleep | 25 | 25 | 25 | 2.1 | 2.1 | 2.1 |
| Micro sleep | 55 | 50 | 38 | 5.5 | 5 | 3 |
| Active DL | 280 | 200 | 152 | 32 | 26 | 17.6 |
| Active UL | 110 | 90 | 80 | 6.5 | 5.8 | 4.2 |

Tables 5.1-4 and 5.1-5, total transition time T and additional transition
energy E (relative power × ms). Both include ramping down and up:

| State | T, Cat 1 | T, Cat 2 | E, Cat 1 | E, Cat 2 |
|---|---|---|---|---|
| Deep sleep | 50 ms | 10 s | 1000 | 17000 |
| Light sleep | 6 ms | 640 ms | 90 | 1088 |
| Micro sleep | 0 | 0 | 0 | 0 |

During a transition, the relative power of the sleep state is consumed.

**Scaling (active DL).** P_DL = P_static + P_dyn, with:
- P_static = P3, the micro-sleep power (baseline; optional 1.5·P3);
- P_dyn = s_a·A·(P4 − P_static) + (s_f·s_p/η)·(1 − A)·(P4 − P_static);
- A = 0.4 (baseline; optional 0.1 and 0.7);
- η = 1, or 0.76 when s_f·s_p < 0.5 (the two-value option).

s_a is the fraction of active TxRUs, s_f the ratio of RF bandwidth to the
maximum system bandwidth, and s_p the ratio of PSD per TxRU.

**Scaling (active UL).** P_UL = P3 + s_a·(P5 − P3).

**Other rules:**
- Each additional contiguous intra-band CC is scaled by 0.7.
- There is always a non-sleep mode between adjacent sleep modes.
- Slot energy is the sum over symbols.

Annex A and B (evaluation assumptions):
- Traffic: FTP model 3 (0.5 MB packets, 200 ms mean inter-arrival), FTP3
  IM (0.1 MB, 2 s) or VoIP.
- Load L is the % of resources used for UE-specific PDSCH/PUSCH: idle (0),
  low (≤ 15), light (15–30), medium (30–50).
- SLS: 3D-UMa with low-loss O2I; 80% indoor; **ISD 500 m**, 7×3 sectors
  with wrap-around; RSRP attachment; 25 m BS; NF 5 dB; 8 dBi elements;
  TDD DDDSU at 30 kHz (Set 1).

### 1.2 Peschiera et al., arXiv 2510.10647v1 (FR3 parametric BS power model)

Fetched 2026-11-09 (PDF SHA-256 recorded in the P3.1 config).

It is an arXiv v1. The text says the code "will be made available upon
acceptance"; no acceptance is stated. **`docs/RESEARCH_DESIGN.md` called it
"ICASSP 2026"; that is not verified and must not be cited as such.**

The model:
- Frame-averaged consumption, eq. (5): P_cons = P_dig/η_dig + P_ana/η_ana +
  P_PA/η_PA, with η_s/c = 0.8 each.
- Phases, eqs. (6), (12), (16): data, signalling (τ_sig = 1/14),
  micro-sleep and idle.
- Reduction factors:
  - digital 0.5 (micro) and 0.25 (idle);
  - analog 0.75 and 0.5;
  - PA 0.5 and 0.25.
- PA, eq. (17): P_PA,1(p) = ξ·P_max^α/η_max + (1 − ξ)·P_max^(1−α)·p^α/η_max.
  The SiGe values are P_max = 0.1 W (8 dB back-off), η_max = 0.15,
  α = 0.75, ξ = 0.1.
- Per-component figures of merit: DAC, ADC, mixer, phase shifter, LNA,
  LO = 40 mW, RF filter = 5 mW, FFT size 4096, DPD Ξ = 50, BB filter.

Reported totals (10 GHz, 400 MHz, 1024 antennas, 100 W, K = 8, τ_DL = 0.75):

| Load | 16 RF chains | 1024 RF chains |
|---|---|---|
| Full | 740 W | 1658 W |
| 30% | 300 W | 1044 W |

### 1.3 Peschiera et al., arXiv 2505.15445, Table II (sub-6 GHz, measurement-anchored)

The compact model, eq. (5):
P_cons = (N_a/N)·M_a·(P0/M + γ·P_a^α) + (M_a/M)·P1 + P_sleep.

It is derived from Golard et al. (PIMRC 2024, DOI
10.1109/PIMRC59610.2024.10817448). That paper is validated on operator
measurements of 4G/5G BSs in Belgium within 10–20%.

The configuration that matches **our case C 3.5 GHz radio** is 64T64R AAU,
TDD 5G NR, 3.5 GHz, 100 MHz, P_max 3.125 W per PA (= 200 W = 53 dBm):

| α | γ | P0 [W] | P1 [W] | P_sleep [W] |
|---|---|---|---|---|
| 0.75 | 3.50 | {0, 53.92} | {341.57, 161.95} | 550.23 |

In P0 and P1, the left value is without µDTX / AFE idle and the right value
is with them.

### 1.4 Closest prior art (for positioning; read 2026-11-09)

- **López-Pérez, Piovesan, Geraci**, "Capacity and power consumption of
  multi-layer 6G networks using the upper mid-band" (arXiv 2411.09660;
  ICC 2025 best paper in its category).
- **López-Pérez, Piovesan, Bernabè**, "Capacity and energy trade-offs in FR3
  6G networks using real deployment data" (arXiv 2601.13993).
  - Real 4G/5G deployment in China plus a synthetic 10 GHz layer.
  - Power from a proprietary ANN trained on radio measurements, with the
    6G radios scaled from 5G.
  - Throughput and power are **downlink only**, in a **snapshot**, with the
    6G layer **always on**.
  - Findings: up to 9.5× median throughput, up to +59% power; co-location
    gives limited gains at high energy cost.
- **What neither addresses**, and this work does: UE-power-limited **uplink
  feasibility** per location, a radio model validated against 5G-LENA
  (E1.5), and **activation control** with sleep and transition costs under
  user-level UL/DL targets.

## 2. Proposed model decisions (to be pre-registered in P3.1)

**D1. Absolute watts, not TR-relative units, for comparisons across
layers.**
- TR 38.864's relative units are normalised per reference configuration
  (deep sleep = 1 in every set).
- Its scaling factors are ≤ 1 relative to the reference, so it cannot
  express a larger FR3 radio.
- It therefore cannot compare a 3.5 GHz radio with a different 7.2 GHz
  radio by itself.

**D2. 3.5 GHz layer: the measurement-anchored sub-6 model (1.3), 64T64R
AAU.**
- It matches case C at 3.5 GHz (100 MHz, 53 dBm).
- The always-on 3.5 GHz layer is common to every policy. Its absolute level
  shifts totals but not the activation decision, except through load moved
  between layers.

**D3. 7.2 GHz layer: the FR3 component model (1.2)** with case C's 7.2 GHz
radio: 200 MHz, 53 dBm, 16×32 elements per polarisation.
- The number of RF chains (TRX) is not given by case C. 64 / 128 / 256 are
  proposed as a sensitivity, 128 primary. This matches the 128–256 TRX
  assumed in the prior art (1.4).
- The PA technology is a sensitivity.

**D4. Sleep ladder and transitions of the 7.2 GHz carrier from TR 38.864.**
- Absolute state power = (TR relative power of the state ÷ TR relative
  active-DL power) × the absolute full-load DL power of the D3 model.
  Transition energies are scaled by the same factor.
- Cat 1 (deep sleep 50 ms / light 6 ms) and Cat 2 (10 s / 640 ms) bracket
  the hardware.
- This mapping is an assumption and will be stated as one. It keeps the
  3GPP ratios and times while anchoring the level to an FR3 radio.

**D5. Primary radio case for Phase 3: C (deployment).** A and B isolate
radio physics at 20 MHz and 44 dBm; they are kept as radio sensitivities.

**D6. Geometry.** Both ISDs. ISD 500 is TR 38.864's energy-saving
geometry; ISD 200 is M.2412's. The ISD-500 sample limits of E1c.2/E1c.3
are stated wherever ISD-500 results are used.

**D7. Traffic and time scales.**
- Traffic: TR 38.864 FTP3 (0.5 MB, 200 ms inter-arrival per UE) at the
  Annex A load classes (idle, low, light, medium), on top of a daily
  profile whose source is to be selected and verified in P3.2.
- Decision interval: tied to the activation mechanism (7.2 GHz SCell
  activation, or cell DTX/DRX), and long relative to the TR transition
  times. Candidates are 1 s (Cat 1) and 60 s (Cat 2); to be fixed in P3.3.

**D8. Service model.** Each UE has DL and UL demand. The 3.5 GHz layer
serves both directions where feasible. The 7.2 GHz layer carries DL (and UL
where feasible) for the UEs whose class allows it (E1c.3 `sd`, load-matched
η). UL-infeasible UEs (K3) cannot be moved to 7.2 GHz UL; this is the
mechanism behind H2.

**D9. Baselines** (research design §Strong baselines):
- 3.5 GHz only, as a feasibility check, not a valid energy competitor when
  it misses targets;
- always-on;
- threshold + hysteresis, UL-feasible and UL-agnostic;
- DP or MILP optimum on the same instance;
- clairvoyant bound, labelled as an oracle.

## 3. Stages and gates

| Stage | Content | Gate (to be pre-registered) |
|---|---|---|
| P3.1 | Energy models of both layers; TR 38.864 ladder | Equations implemented from the sources; reproduce the published totals of 1.2 and the TR scaling identities |
| P3.2 | Traffic and service model fed by E1c.3 | Load accounting closes (served + unserved = offered); the per-UE class mix equals E1c.3's |
| P3.3 | Controllers and baselines | Causality checks; DP = brute force on small instances |
| P3.4 | Evaluation | Paired seeds, confidence intervals, held-out seeds; H1–H4 scored as pre-registered |

## 4. Open items before P3.1 can be pre-registered

1. Golard et al. (PIMRC 2024) was read on 2026-11-09 from the UCLouvain
   open-access PDF.
   - Its Table II gives the regression estimates from operator measurements
     and more than 100 datasheets (2023 equipment):
     - PA: ξ 0.12, η_tech 0.38;
     - AFE: P_misc 22 W (17 W for AAU), P_TRX 0.18 W;
     - DBB: P_link 90 W;
     - supply and cooling: η ≈ 0.90 / 0.90 / 0.90 / 0.95;
     - TDD: τ_DL 0.75, τ_sig 1/14, ζ_sig 1/12.
   - Datasheets match the measurements within ±10% above 10–30% load. The
     raw data are confidential.
   - **Remaining for P3.1:** recompute the 2505.15445 Table II parameters
     (P0, P1, P_sleep for 64T64R) from Golard's equations and Table II, as a
     gate.
2. Check whether 2510.10647's reported totals can be reproduced from the
   published equations. Some inputs, e.g. υ_coh, are not stated in the
   text. If they cannot, the gate becomes reproducing the PA and analog
   sub-totals, which are fully specified.
3. Choose and verify the daily load profile source (P3.2).
