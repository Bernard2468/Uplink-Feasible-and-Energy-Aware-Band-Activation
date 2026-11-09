# Phase 3: energy model and band-activation control (protocol)

Status: **P3.0 design proposal, revision 1 (2026-11-09).** Revision 1
follows the ChatGPT audit of P3.0; §5 lists each audit point with its
verdict and the evidence. Nothing in this file is pre-registered yet. Each
stage below gets its own config, code, tests and pre-registration commit
before its first measured run, as in Phase 2.

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
- **Q4 (boundaries).** In which load, geometry, hardware and power-model
  regimes is there no net benefit (H4)?

Phase 2 supplies, per location and realization, each UE's link terms per
carrier and direction (E1c.2/E1c.3). Phase 3 does not change the radio
model. It re-evaluates it for each activation vector and transmit power
(D8). That re-evaluation is gated to reproduce E1c.3 exactly where they
coincide.

## 1. Sources verified for Phase 3 (2026-11-09)

Every value below was read from the primary document. Each file was
downloaded into an isolated folder; hashes are recorded so the reading can
be repeated.

### 1.1 3GPP TR 38.864 V18.1.0 (Study on network energy savings for NR)

Source: `https://www.3gpp.org/ftp/Specs/archive/38_series/38.864/38864-i10.zip`.
SHA-256 `99770bf7a80e9ae26184e6c76f6c421464fcb8dd9bb56e5cf7d0f2bba5c08e1f`.
Clause 5.1 says these values are "for RAN1 evaluation purpose". The
reference configurations are FR1 and FR2. **None is measured for a 7 GHz
radio.**

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
The additional energy E comes on top of that. Counting both is therefore
the TR's definition, not a double count.

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
- **Traffic** (Table A-2): FTP model 3 (0.5 Mbyte packets, 200 ms mean
  inter-arrival), FTP3 IM (0.1 Mbyte, 2 s) or VoIP.
  - "Parameter (e.g. packet size and arrival rate) adjustment can be
    optionally considered and reported."
  - At 0.5 Mbyte per 200 ms, one source offers 20 Mbit/s on average.
- **Load** (Table A-1): "a load (L)% of a cell is a percentage of resources
  used for UE specific PDSCH/PUSCH". Classes: idle (L = 0), low (≤ 15),
  light (15–30), medium (30–50). **L is a realized resource occupancy, not
  an offered rate.**
- **SLS** (Table B-1): 3D-UMa with low-loss O2I; 80% indoor; ISD 500 m,
  7×3 sectors with wrap-around; RSRP attachment; 25 m BS; NF 5 dB; 8 dBi
  elements; TDD DDDSU at 30 kHz (Set 1).

### 1.2 Peschiera et al., FR3 parametric BS power model

**Published version.** IEEE ICASSP 2026 (Barcelona, 3–8 May 2026),
pp. 21476–21480, DOI 10.1109/ICASSP55912.2026.11464669.
- Verified 2026-11-09 from the Crossref record of the DOI and from the
  "Related DOI" on arXiv 2510.10647.
- The IEEE Xplore text is not accessible here and was not read.

**Implemented version.** arXiv 2510.10647v1 (12 Oct 2025, the only
version). Differences from the published text are unknown and will be
stated as such.

*Correction history:* P3.0 said "not verified ... must not be cited as
such". That was an incomplete check: I read the arXiv PDF but not its
abstract page or Crossref.

The model:
- Frame-averaged consumption, eq. (5): P_cons = P_dig/η_dig + P_ana/η_ana +
  P_PA/η_PA, with η_s/c = 0.8 each.
- Phases, eqs. (6), (12), (16): data, signalling (τ_sig = 1/14),
  micro-sleep and idle. "Idle" is the TDD time of the other direction, not
  a cell sleep.
- Reduction factors:
  - digital 0.5 (micro) and 0.25 (idle);
  - analog 0.75 and 0.5;
  - PA 0.5 and 0.25.
- **Architecture (§3.2–3.3):**
  - one PA per antenna, P_a = P_T,DL/M_ant, and M_ant PAs;
  - hybrid partially connected beamforming has M_PS = M_ant/M_RF phase
    shifters per RF chain;
  - fully digital beamforming has M_RF = M_ant and M_PS = 0.
- **PA, eq. (17):** P_PA,1(p) = ξ·P_max^α/η_max + (1 − ξ)·P_max^(1−α)·p^α/η_max.
  - P_max is the "maximal PA output power considering a certain output power
    back-off (that we set to 8 dB) from the PA saturation power".
  - SiGe values, from a SiGe BiCMOS FR3 PA (Pecile et al., IEEE T-MTT
    2025): P_max = 0.1 W, η_max = 0.15, α = 0.75, ξ = 0.1.
  - The numerical example sets P_T,DL = 100·M_ant/1024 W "such that
    P_a ≈ 0.1 = P_max".
- **As printed, eqs. (12) and (16) count data and signalling over the same
  DL time.** The data term x̄·τ_DL·P has no (1 − τ_sig) factor, unlike
  2505.15445 eq. (6).
  - The frame fractions therefore sum to 1 + τ_DL·x̄·τ_sig: 1.054 at full
    load.
  - P3.1 implements the equations as printed (for reproduction) and states
    this property.
- Per-component figures of merit:
  - DAC, ADC, mixer, phase shifter and LNA have the published figures of
    merit;
  - LO = 40 mW, RF filter = 5 mW;
  - FFT size 4096, DPD Ξ = 50, and the published BB filter.

Reported totals (10 GHz, 400 MHz, 1024 antennas, 100 W, K = 8, τ_DL = 0.75):

| Load | 16 RF chains | 1024 RF chains |
|---|---|---|
| Full | 740 W | 1658 W |
| 30% | 300 W | 1044 W |

The PA alone at x̄_DL = 0.3 is 262 W for 1024 antennas. The best energy
efficiency is at 256 RF chains.

### 1.3 Peschiera et al., sub-6 GHz measurement-anchored model

**Published version.** IEEE Transactions on Communications, vol. 74 (2026),
pp. 8961–8977, DOI 10.1109/TCOMM.2026.3693185.
- Verified 2026-11-09 from Crossref and the arXiv "Related DOI".
- Crossref gives the year only, so a publication day is not claimed.

**Implemented version.** arXiv 2505.15445v1.

The compact model, eq. (5):
P_cons = (N_a/N)·M_a·(P0/M + γ·P_a^α) + (M_a/M)·P1 + P_sleep.

Closed forms (§III-D):
- γ = τ_DL·(1 − τ_sig)/η_s/c^PA · (1 − ξ)·P_max^(1−α)/η_max.
- P0/M = τ_DL·(1 − τ_sig)/η_s/c^PA · ξ·P_max^α/η_max · (1 − δ_dtx).
- P1/M = P̄_PA,1/η_s/c^PA + P̄_AFE,1/η_s/c^AFE.
- P_sleep = M·δ_sleep/η_s/c^PA · ξ·P_max^α/η_max + P̄_AFE,sleep/η_s/c^AFE +
  P̄_DBB/η_s/c^DBB.

**Name clash.** "P_sleep" here is the power with all antennas muted
(spatial-domain sleep, baseband on). It is not a TR 38.864 cell sleep
state.

It is derived from Golard et al. (PIMRC 2024, DOI
10.1109/PIMRC59610.2024.10817448). That paper is validated on operator
measurements of 4G/5G BSs in Belgium within 10–20%.

The configuration that matches **our case C 3.5 GHz radio** is 64T64R AAU,
TDD 5G NR, 3.5 GHz, 100 MHz, P_max 3.125 W per PA (64 PAs = 200 W =
53 dBm):

| α | γ | P0 [W] | P1 [W] | P_sleep [W] |
|---|---|---|---|---|
| 0.75 | 3.50 | {0, 53.92} | {341.57, 161.95} | 550.23 |

In P0 and P1, the left value is without µDTX / AFE idle and the right value
is with them.

### 1.4 Golard et al. (PIMRC 2024), the parameter source of 1.3

Read from the UCLouvain open-access PDF.

**PA, eq. (4):** P_PA(p) = ξ·P_max/η_max + (1 − ξ)·P_full^n·p^(1−n)/η_max.

**Discrepancy:** the static term is ξ·P_max/η_max, whereas both Peschiera
papers print ξ·P_max^α/η_max.
- The dynamic terms agree with α = 1 − n.
- The two forms differ by 25% at P_max = 3.125 W and by 78% at 0.1 W.
- P3.1 tests which form reproduces 1.3's Table II. This was not computed
  before writing this file.

**PA efficiency, eq. (5):**
η_max = η_tech / (1 + a·(P_max/P*)·(f/f*)² + b·(P*/P_max)).
- Table II values: η_tech = 0.38, a ≈ 1e-4, b ≈ 1, P* = 1 W, f* = 1 GHz.
- The paper attributes these to "LDMOS Doherty amplifiers" (2023
  equipment, sub-6 GHz).
- **Not valid for sub-watt FR3 element PAs.** At 0.1 W it gives
  η ≈ 0.035, against the measured 0.15 of 1.2's SiGe PA.

**Other Table II values:**
- PA reduction factors {δ_dtx, δ_idle, δ_sleep} = {1, 0.25, 0}; ξ = 0.12;
  n = 0.25.
- AFE: P_misc 22 W (17 W AAU), P_TRX 0.18 W.
- DBB: P_link 90 W, P_phy 0.07 W.
- Efficiencies: supply ≈ 0.90; cooling ≈ 0.90 (PA), 0.90 (AFE),
  0.95 (BB).
- NR TDD: τ_DL 0.75, τ_UL 0.25, τ_sig 1/14, ζ_sig 1/12.

Datasheets match the measurements within ±10% above 10–30% load. The raw
data are confidential.

### 1.5 Nokia white paper, the source of case C (hardware architecture)

"Coverage evaluation of 7-15 GHz bands from existing sites" (2024).
SHA-256 `c240137f…d88376`, as recorded in `docs/E1_PARAMETERS.md`.
Re-read 2026-11-09.

| | 3.5 GHz | 7 GHz |
|---|---|---|
| Radiators | 8×16×2 = 256 | 16×32×2 = 1024 |
| TRX | **64** (TRXU 2×16×2) | **256** (TRXU 4×32×2) |
| Feed | "4:1 aggregation of radiators in elevation, 10° electrical downtilt" | same |
| Max Tx power | 53 dBm | 53 dBm, and a 43 dBm level |
| EIRP (rank 1) | 82 dBm / 100 MHz | 88 dBm / 200 MHz |

- 82 = 53 + 29 and 88 = 53 + 35. **The 53 dBm is the total conducted
  power of the array.**
- Footnote 1: the configuration does "not reflect or anticipate product".
- P3.0 claimed "the number of RF chains (TRX) is not given by case C". That
  was wrong: our own `docs/E1_PARAMETERS.md` already recorded the 64 TRX
  and the 4:1 aggregation.

### 1.6 Closest prior art (for positioning; read 2026-11-09)

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
- **What neither addresses**, and this work does:
  - UE-power-limited **uplink feasibility** per location;
  - a radio model validated against 5G-LENA (E1.5);
  - **activation control** with sleep and transition costs under
    user-level UL/DL targets;
  - with E1c.3, the **coupling between cells' activation decisions** through
    directional interference (D8).

## 2. Model decisions, revision 1 (to be pre-registered in P3.1–P3.3)

**D1. Absolute watts and one accounting boundary.**
- The unit of account is the **cell** (one sector × one carrier).
  - Both source models are per cell and include their own baseband share.
  - 1.3 says "one cell, which corresponds to one sector and one frequency
    band".
- Site and network values are sums of cells. No site-level shared term
  (rectifier, site cooling, backhaul, pooled baseband) is added.
  - These terms are common to every policy while the 3.5 GHz layer stays
    on.
  - Their omission is stated. Supply and cooling losses inside each
    model's η are counted once, in that cell.
- **Units:** power in W, time in s, energy in J. TR relative energies are
  converted by D4's W-per-unit factor × 10⁻³.
- **Report** per cell (3.5 / 7.2), per site, and over the evaluated cells.
- TR 38.864 relative units are used only as ratios (D4).

**D2. 3.5 GHz layer: the measurement-anchored model (1.3), 64T64R AAU.**
- Its PA and TRX count match the hardware: M = 64 PAs at 3.125 W = 64 TRX,
  matching Nokia's 64 TRX feeding 4-radiator columns (1.5).
- **Load-dependent.** Its time-domain load N_a/N follows the traffic it
  actually carries. Moving traffic to 7.2 GHz lowers 3.5 GHz power.
- **Primary: "with µDTX / AFE idle".** TR 38.864 assumes micro sleep
  (transition 0), and the FR3 model has it too. "Without" is the
  sensitivity.
- **No cell sleep:** the layer is always on and anchors the UEs (D7).
- **P3.1 gate:** recompute 1.3's Table II (64T64R, 8T8R, 4T4R) from 1.4.
  This includes the static-term discrepancy.
- **Radio–hardware caveat.**
  - The radio primary steers every element (E1.4 convention).
  - Nokia's AAU uses fixed 4:1 vertical columns. E1.4 sensitivity S4 put
    that at −4.2 points of K1 in case C (53.4 → 49.2%).

**D3. 7.2 GHz layer: the FR3 component model (1.2) with case C's
architecture.** Each quantity is defined separately:
- Radiators: 1024 (Nokia 16×32×2).
- PAs: N_PA = 1024, one per radiator (1.2's convention).
- RF chains: **M_RF = 256** (Nokia's TRX count; also 1.2's maximum-EE
  point).
- Feed: hybrid partially connected, M_PS = 4 phase shifters per RF chain.
  This lets the whole array form one steered beam, which the radio model
  assumes.
- Conducted power: P_T (D10).
- PA parameters: D10.
- f_c = 7.2 GHz, B = 200 MHz; the component equations of 1.2 are used as
  published.
- **Sensitivities (energy only):** M_RF ∈ {64, 128, 1024 (fully digital)}.
  - The radio is not re-run for them. Fewer RF chains reduce
    multi-beam/MU capability, so these points are labelled "service
    architecture not matched".
  - Nokia's literal fixed columns (M_PS = 0) are the S4 counterpart.

**D4. State model: component states inside the frame, TR sleep ladder
attached at micro sleep.** This replaces P3.0's anchoring at full-load DL
power, which could contradict the component model's own micro-sleep power.
- **Inside the frame** (data, signalling, micro sleep, TDD idle): only the
  component models (1.2 for 7.2 GHz, 1.3 for 3.5 GHz).
- **Anchor.** P_µ is the FR3 model's power with x̄_DL = x̄_UL = 0 and no
  signalling (τ_sig → 0): every chain in micro sleep in its own direction
  and idle in the other. It is the deepest state both models define.
- **Cell sleep of the 7.2 GHz layer:**
  - P_light = P_µ·(P_light/P_micro)_TR and P_deep = P_µ·(P_deep/P_micro)_TR.
  - The ratios are from Set 1 (FR1 TDD): Cat 1 25/55 and 1/55; Cat 2
    2.1/5.5 and 1/5.5.
  - Sensitivity: Set 3 (FR2) ratios.
  - Labelled "TR 38.864 extrapolation to a 7 GHz radio".
- **Transitions:**
  - A sleep period of length D must satisfy D ≥ T (TR total transition
    time).
  - Its energy is P_sleep·D + E_add, with E_add = E_TR·(P_µ/P_micro,TR)·10⁻³ J.
  - Every second is in exactly one state.
  - There is a non-sleep mode between sleep modes.
- **Availability.** A cell is unavailable for service from its sleep
  decision until its wake-up completes. Primary: the full T after the wake
  request, because the TR does not split T into down and up. Sensitivity:
  T/2.
- **Sensitivity:** P3.0's DL-anchored ladder is reported only to show how
  much the anchoring matters.

**D5. Primary radio case: C, as a vendor evaluation configuration, not a
product (1.5).**
- A and B (20 MHz, 44 dBm) remain radio sensitivities.
- The C energy figures are accepted only after the PA-feasibility check
  (D10).

**D6. Geometry.**
- ISD 200 is primary.
- ISD 500 is a qualified sensitivity: TR 38.864's energy-saving geometry,
  with E1c.2/E1c.3's limits (virtual sites, synthetic DL targets for 21 of
  57 TRxPs, no wrap-around) stated wherever it is used.

**D7. Traffic and time scales.**
- **Load is realized occupancy.**
  - The load class is L, the share of resources used for UE-specific
    PDSCH/PUSCH after scheduling, as TR Annex A defines it.
  - FTP3 (0.5 Mbyte, 200 ms) is a per-UE source of 20 Mbit/s on average.
  - The number of UEs (or the arrival rate, which the TR allows adjusting
    if reported) is calibrated to reach each class.
  - Offered, scheduled, delivered and unserved traffic, and the realized L,
    are all reported.
- **DL and UL demands are independent processes.** The UL source model is
  an open item (§4).
- **Anchoring.** The 7.2 GHz cell is a secondary cell anchored on the
  3.5 GHz cell. UEs stay connected through 3.5 GHz, so the 7.2 GHz cell can
  sleep without dropping them.
- **Wake delay** = max(TR transition, secondary-cell activation delay). The
  latter is from TS 38.133, to be sourced in P3.3.
- **Decision interval Δ:**
  - Gate: Δ ≥ the transition time of every sleep state the controller may
    use in that run.
  - So Cat 2 deep sleep (10 s) needs Δ ≥ 10 s.
  - P3.0's candidates already respected this (1 s with Cat 1, whose deep
    sleep takes 50 ms; 60 s with Cat 2). It is now a gate.

**D8. Service feasibility conditional on the activation vector.**
- **Recomputed exactly per state, never frozen.**
  - Feasibility per UE, carrier and direction is recomputed from the stored
    per-link terms for each activation vector a of the 7.2 GHz cells, each
    7.2 GHz power level (D10) and each load.
  - It is not interpolated, and E1c.3's classes are not reused unchanged.
- **Interference follows activation.** A sleeping 7.2 GHz cell contributes
  neither DL interference nor UL interference from its UEs.
- **Engine gate (P3.2):**
  - with a = all on, reproduce E1c.3 `sd` per UE to ≤ 1e-9 dB;
  - with only the serving cell on, reproduce `sd_iso72`.
- **Unservable demand.**
  - Demand of UEs infeasible on every available carrier in a direction is
    counted as unservable in every policy, and reported separately.
  - QoS targets apply to servable demand. No policy is credited or blamed
    for unservable demand, and no zero-violation claim is made where
    service is physically impossible.
- **Wake delay** (D4, D7) applies: a cell cannot serve before it is awake.
- **Still to define in P3.2:** admission, hard vs soft QoS, and fairness.

**D9. Baselines.** Every policy uses the same causal observations, demand
forecasts, state machine and QoS accounting:
- 3.5 GHz only, as a feasibility check, not a valid energy competitor when
  it misses targets;
- always on;
- threshold + hysteresis, UL-feasible and UL-agnostic;
- **interference-aware greedy / local search over the activation vector**
  (new; it uses D8's coupled engine);
- DP or MILP optimum on small instances;
- clairvoyant bound, labelled as an oracle and never as a competitor.

**D10. RF output and PA capability are one physical quantity (new; hard
gate).**
- **Same quantity.** The link budget's conducted power and the PA model's
  output are the same: P_T = N_PA·P_a at full load. Feed losses are
  neglected, and that is stated.
- **Feasibility:** P_T/N_PA ≤ P_max, where P_max is the maximum average
  output after the 8 dB back-off (both sources).
- **The SiGe PA of 1.2** (P_max = 0.1 W):

  | Conducted power | Per PA, 1024 PAs | Per PA, 256 PAs | SiGe feasible? |
  |---|---|---|---|
  | 53 dBm (199.5 W) | 0.195 W | 0.779 W | no (limit with 1024 PAs: 102.4 W = 50.1 dBm) |
  | 50 dBm (100 W, 1.2's own example) | 0.098 W | 0.391 W | yes with 1024 PAs |
  | 43 dBm (20 W, Nokia's second level) | 0.019 W | 0.078 W | yes |

- **Power levels evaluated:**
  - **53 dBm (primary):** matches all Phase 2 radio results. No sourced
    7 GHz PA delivers it.
    - The PA is therefore *assumed*: an element PA sized to
      P_max = 0.195 W with 1.2's efficiency parameters (η_max 0.15, α 0.75,
      ξ 0.1).
    - η_max is swept up to the 3.5 GHz layer's own η_max from 1.4 eq. (5),
      as an optimistic bound.
    - Labelled "assumed PA".
  - **50 dBm:** source-exact. This is 1.2's own reference hardware.
  - **43 dBm:** source-exact SiGe, and Nokia's second level (E1.4 S2 was
    its single-site radio counterpart).
  - For both 50 and 43 dBm, the radio is re-evaluated by D8's engine.
    Changing the DL power shifts only the noise share of the DL SINR.
- **Conclusions must hold across power levels.** H1/H4 conclusions are
  reported for all three. A conclusion that changes sign between them is
  reported as hardware-dependent.

## 3. Stages and gates

| Stage | Content | Gate (to be pre-registered) |
|---|---|---|
| P3.1 | Hardware energy models of both layers; state ladder | Source versions and hashes recorded; 1.3 Table II recomputed from 1.4 (both static-term forms reported); 1.2's totals (740 / 1658 / 300 / 1044 W, PA 262 W) reproduced, or, where an input is not stated in the source, labelled "not independently reproducible" under a rule fixed before the run (no input tuned to force agreement); TR tables transcribed and checked against the .docx; D10 feasibility; D4 state consistency (powers ≥ 0, ordered deep < light < micro, legal transitions, D ≥ T, energy in J); unit tests on small worked examples |
| P3.2 | Coupled feasibility engine; traffic and service model | Engine reproduces E1c.3 `sd` and `sd_iso72`; load accounting closes (served + unserved = offered); realized L per class reported |
| P3.3 | Controllers and baselines | Causality checks; DP = brute force on small instances; Δ ≥ transition times; wake delay enforced |
| P3.4 | Evaluation | Paired seeds, confidence intervals, held-out seeds/geometry/load/hardware; H1–H4 scored as pre-registered |

## 4. Open items

1. **P3.1, FR3 inputs.** List every input of 1.2's digital model that the
   text does not state (e.g. υ_coh). For each, fix a value with its
   rationale before the run. If a published total then misses its
   tolerance, report it as "not independently reproducible". Never tune
   the input.
2. **P3.2, UL traffic source** (independent of DL) and the daily load
   profile source.
3. **P3.3, secondary-cell activation delay** from TS 38.133.
4. **Radio–hardware architecture caveat** (D2, D3): stated in the paper,
   with E1.4 S4 as its radio-side size.

## 5. Audit of P3.0 (ChatGPT, 2026-11-09) and response

| Point | Verdict | Evidence / action |
|---|---|---|
| FR3 paper is ICASSP 2026, DOI …11464669 | **Correct; my P3.0 note was wrong** | Crossref + arXiv "Related DOI"; 1.2, `RESEARCH_DESIGN.md` corrected |
| Sub-6 paper is IEEE TCOM 2026, DOI …3693185, "May 13, 2026" | **Venue and DOI correct; the date is unverified** | Crossref gives the year only; IEEE Xplore not readable here |
| E1c.3 integrity, P1 failure kept, P2 held | Agreed | No change |
| `avg`/`geo` are not rigorous bounds | **Correct** | Two-link counterexample; erratum added to the E1c.3 results |
| D1 boundary | Agreed | D1 rev 1 |
| D2 ports vs elements; load-varying 3.5 GHz power | Agreed | 64 PAs = 64 TRX, each feeding 4 radiators (1.3, 1.5); D2 rev 1 |
| D3 case C has 256 TRX | **Correct; my P3.0 statement was wrong** | Nokia Table 1 (1.5); M_RF = 256 primary |
| D5/D10 53 dBm exceeds the SiGe PA | **Correct** | P_max is after the 8 dB back-off (1.2); D10 table; 53 dBm kept as "assumed PA", plus 50 and 43 dBm source-exact |
| D4 risk of inconsistent idle powers | **Correct** | Ladder re-anchored at the component micro-sleep state (D4) |
| D4 transition energy "not both counted" | Clarified | TR defines both: sleep power during T plus additional E. Implemented once each, every second in exactly one state |
| D6 ISD 200 primary | Agreed | D6 |
| D7 offered rate is not load class | **Correct** | TR Annex A defines L as realized occupancy (1.1); D7 |
| D7 "no 1-s horizon with 10-s deep sleep" | **Not applicable as stated** | P3.0 paired 1 s with Cat 1 (50 ms deep sleep) and 60 s with Cat 2 (10 s). The constraint is adopted as a gate |
| D8 feasibility depends on neighbours' activation | **Correct, and central** | Exact per-state recomputation with a reproduction gate (D8) |
| D9 add interference-aware heuristic | Agreed | D9 |
| Git: archive refs do not prove the remote | Agreed; checked | `git fetch origin`: origin/main = main = 2976d79 on 2026-11-09 |

Not adopted: the audit's review file is not stored verbatim, because of the
unverified date and the D7 misreading above. This table is the record.
