# Phase 3: energy model and band-activation control (protocol)

Status: **P3.0 design proposal, revisions 1-3 (2026-11-09/10); P3.1 complete (run 2, amendment 1); P3.2a complete (run 2, amendment 1); P3.2b complete (run 2, amendment 1); P3.2c next.** Revision 1
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

**Published version (found in P3.1, Amendment 1).**
- **Sources:**
  - the ICASSP accepted manuscript on KU Leuven Lirias (SHA-256
    `212bcf8b…`);
  - the authors' code, `github.com/e-peschiera/FR3-power-model` commit
    `255ed68`, identical in `dramco-research/fr3-power-model-epeschiera-icassp26`
    commit `9e9fafe`. Its README says it generates Fig. 2 of the ICASSP paper.
- **Changes from v1:**
  - static PA term ξ·P_max/η_max;
  - data phase x·τ·(1 − τ_sig), so the frame fractions sum to 1;
  - one LNA per antenna;
  - υ_coh by the code's rule (500 at 10 GHz);
  - η_s/c 0.81 in the code. The text still says 0.8, but only 0.81
    produces the printed numbers.
- **Published totals:** 643 / 1389 W at full load and 253 / 849 W at 30%
  (16 / 1024 RF chains). PA: 3.3 W (16 antennas) to 209.9 W (1024).
  These are 13–19% below v1. **The project uses this version.**

### 1.3 Peschiera et al., sub-6 GHz measurement-anchored model

**Published version.** IEEE Transactions on Communications, vol. 74 (2026),
pp. 8961–8977, DOI 10.1109/TCOMM.2026.3693185.
- Verified 2026-11-09 from Crossref and the arXiv "Related DOI".
- Crossref gives the year only, so a publication day is not claimed.
- The ChatGPT review (2026-11-09) reports 13 May 2026 from IEEE Xplore.
  The Xplore page is not readable here, so this date is not independently
  verified.

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
- **Resolved by P3.1 run 1.**
  - Golard's form reproduces all 15 γ, P0 and P1 values of 1.3's Table II
    exactly. The printed ξ·P_max^α is a misprint.
  - The published ICASSP version of 1.2 prints Golard's form.

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

### Revision 2 (2026-11-09, after P3.1 and the ChatGPT review of P3.2)

These decisions were fixed before any P3.2 run.

**D10 (revised): the primary 7.2 GHz hardware is 50 dBm with the SiGe PA.**
- It is the only point where both the radio and the amplifier are sourced:
  - 100 W over 1024 element PAs at 0.0977 W each, within P_max = 0.1 W;
  - 256 RF chains.
  - This is the FR3 paper's own reference power density, with Nokia's TRX
    count.
- 53 dBm becomes a **hypothetical upgraded-PA configuration** (η_max 0.15
  and 0.287). 43 dBm is Nokia's lower level.
- The radio at 50 and 43 dBm comes from the P3.2a engine (DL power offset of
  −3 and −10 dB). E1c.3's case C is the 53 dBm point.
- This reverses rev 1's primary (53 dBm, assumed PA). The reason is that
  only 50 dBm needs no assumed amplifier.

**D11: added 7.2 GHz equipment overhead P_aux (sensitivity; used in
P3.2c).**
- **The published models stay unchanged as the reference.**
  P_7.2,adjusted = P_7.2,published + P_aux.
- **Three scenarios, each defined by a rule rather than a chosen number:**
  - **S0:** P_aux = 0, the published FR3 model.
  - **S1, measured missing components:** the parts of Golard's
    measurement-fitted model that have no counterpart in the FR3 model.
    - Miscellaneous AFE (synthesis, clock, control): P_misc = 17 W ×
      (N_PA·P_max / 1 W)^0.25.
    - Link-layer baseband: P_link = 90 W, with N_C = 1 as in the 3.5 GHz
      layer.
    - Each is divided by its η_s/c.
    - About 172 W at the primary point, to be computed by the P3.2c code.
  - **S2, parity stress test:** the 7.2 GHz no-load power equals the measured
    3.5 GHz no-load power (712.2 W). P_aux = 712.2 W − P_empty,7.2, about
    541 W at the primary point.
- **Labels:** these are stress tests, not measured 7.2 GHz values. A
  conclusion that changes between S0 and S2 is reported as
  "overhead-dependent".
- **ChatGPT's proposal** was +250 / +500 W. The rules above give similar
  magnitudes (about 172 / 541 W) that a reader can recompute.
- **State rule:**
  - P_aux is on in every in-frame state (data, signalling, micro sleep,
    TDD idle). Golard's misc functions are "always in working mode when the
    cell is active", and P_link is constant.
  - The sleep anchor includes it: P_µ,adj = P_µ + P_aux.
  - Light and deep sleep and E_add then follow the TR ratios of the total
    (D4).
  - A transitioning cell consumes its sleep-state power plus E_add and
    cannot serve (D4).

**D12: P3.2 is split into three separately pre-registered stages.** Their
results are kept apart from P3.3:

| Stage | Content | Must show |
|---|---|---|
| P3.2a | Coupled radio engine | Switching individual 7.2 GHz cells on or off changes interference, serving and feasibility exactly as the model says. All on reproduces E1c.3; all off removes 7.2 GHz service and interference |
| P3.2b | Traffic and resources | Offered, scheduled, delivered and unserved traffic and occupied resources are accounted separately. Load classes are realized occupancy (D7). Identical offered workloads across policies |
| P3.2c | Time-dependent energy | Active, idle, sleep, wake and switching energy without overlap. A sleeping or waking cell cannot serve. Decisions use only past information. P_aux scenarios S0–S2 |

**D13: serving rule.**
- A user's 7.2 GHz cell is the co-sited sector of its 3.5 GHz serving TRxP:
  intra-site carrier aggregation with a 3.5 GHz anchor. There is no
  cross-site 7.2 GHz association.
- So switching a cell changes serving decisions only for that cell's users,
  and interference for everyone.

**D14: scoring scope of P3.3.** E1c.3 scores users only in site 0's cell. A
coordinated controller needs neighbouring cells scored too, but ring-1 cells
would miss their outer interferers. P3.2a's pre-registered truncation rule
decides between scoring the 7-site cluster and site 0 only.

**Three electrical states are distinguished** (ChatGPT's review, TR 38.864):
- on with little or no traffic: the in-frame states, including micro sleep;
- asleep: light or deep;
- transitioning: unavailable, consuming the sleep power plus E_add.

### Revision 3 (2026-11-10, after P3.2a, before P3.2b)

These decisions were fixed before any P3.2b output on the real layout.

**D15: typical-site frame.**
- The users are E1c.3's victims in site 0's hexagonal cell, as before.
- A victim served by TRxP t counts as a user of the typical site's sector
  t mod 3. In a homogeneous network the lattice translation makes this
  exact, because every site has the same sector bearings.
- Each victim's SINR is still computed at its physical position, with the
  full 19-site environment (the P3.2a engine).
- Loads and 7.2 GHz activation are periodic in the sector index. One fixed
  point per realization, carrier and direction has three unknowns.
- **Why:** it scores every victim. Scoring a 7-site cluster (D14) would need
  victims in the ring-1 cells, which E1c.3 does not have. D14's truncation
  result still bounds the outer-ring effect at ISD 200 (≤ 0.04 points).
- **Limit:** no cell-to-cell traffic heterogeneity. P3.3 may add it as a
  mean-field mixture.

**D16: load-coupled flow-level resource model.**
- **Load coupling** (Siomina and Yuan, IEEE TWC 2012). Each TRxP's
  interference is multiplied by its realized occupancy: the DL load of its
  carrier, or the UL load of its cell applied to its scheduled interferer.
  3.5 / 7.2 GHz and DL / UL are four independent fixed points
  L = min(1, f(L)), with f_k = T Σ_u w_u / (cap · SE(γ_u(L))).
- **Flow level** (Bonald, Borst, Hegde and Proutière, CWI PNA-E0302, eq. 5).
  Time-fair processor sharing gives a flow throughput θ_u = C_u (1 − L_k) if
  the offered load ρ_k < 1, and 0 otherwise.
  - Served traffic is d · min(1, 1/ρ).
  - Unserved traffic is d · max(0, 1 − 1/ρ).
- **Peak rate:** C = SE × N_PRB × 12 × 30 kHz × TDD share × (1 − OH).
  - SE is C-AMC T2, in bits per Hz·s of occupied data resources, with DMRS
    and HARQ losses inside.
  - TDD is DDDSU with S = 10D:2G:2U (TR 38.864 Table B-1), so the DL share is
    52/70 and the UL share 16/70.
  - OH = 0.14 DL and 0.08 UL, from TS 38.306 V19.4.0 clause 4.1.2. These are
    FR1 values; the spec defines no FR3 value. Because OH partly
    double-counts DMRS it is conservative, and its effect is analytic.
- **UL access:** full-band time sharing (the F2 SINR). A fluid PRB-adaptive
  "rescue" for power-limited users is a sensitivity in case C.

**D17: QoS target.**
- The M.2410 Dense Urban 5th-percentile user SE (0.225 DL / 0.15 UL bit/s/Hz)
  becomes a minimum flow throughput: R = SE5 × operating bandwidth of the
  3.5 GHz layer × TDD share.
  - This follows M.2410 §4.4's effective bandwidth, the operating bandwidth
    normalized by the UL/DL ratio.
  - In case C this is 16.7 Mbit/s DL and 3.43 Mbit/s UL, on either carrier.
- **Criterion:** pooled outage of admitted users ≤ 5%. Users with no usable
  link are excluded (D8); the all-user outage is also reported.
- **Phase 2's F2** is this target at L = 0.9 without OH. F2 is therefore the
  heavy-load special case of D17.

**D18: traffic.**
- **Calibration.** The offered DL traffic per site T_ℓ is set so that the
  3.5-GHz-only network's mean 3.5 GHz DL occupancy equals the ETSI
  ES 202 706-1 level, ℓ = 0.3 (medium) or 0.5 (busy hour). This is done per
  ISD and case.
- **Units.** x = T / T_0.5, so x = g is busy-hour growth.
- **UL/DL volume ratio:**
  - 10/90 is primary: Ericsson's "typical 90-to-10 percent downlink-to-uplink
    ratio" (June 2025).
  - 26/74 is an AI-era stress: Ericsson's measured GenAI split applied to all
    traffic.
- **Spatial demand:** every user offers the same mean traffic.
- This closes open item 2. The UL source is a volume ratio, not a packet
  model, which flow-level insensitivity allows.

**D19: association and admission.**
- **DL, 7.2 GHz on:** a user goes to 7.2 GHz iff its own 7.2 GHz cell is on
  (D13), it is admitted there, and its full-buffer peak rate is at least the
  3.5 GHz one. Otherwise it stays on 3.5 GHz.
- **UL:** UL carrier aggregation uses the same rule. A DL-only SCell keeps the
  UL on 3.5 GHz.
- Association is static per state, at full-buffer SINR.
- **Admission.** A carrier and direction is usable only if the full-buffer SE
  reaches the C-AMC curve's first point above snr_min:
  - DL: 0.197 at −0.64 dB;
  - UL: 0.197 at −0.64 dB, which equals the UL snr_min.
- **Why the admission rule.** Between snr_min (−1.64 dB) and −0.64 dB the DL
  curve is 5G-LENA's MCS-0 BLER floor (SE 0.0016 at −1.64 dB, hazard 15).
  A time-fair model would charge such users up to 120 times the resources per
  bit, so a model artifact could drive the load.
- **Reported separately:** the share of users in that band and in TR 36.942's
  low end.

## 3. Stages and gates

| Stage | Content | Gate (to be pre-registered) |
|---|---|---|
| P3.1 | Hardware energy models of both layers; state ladder | Source versions and hashes recorded; 1.3 Table II recomputed from 1.4 (both static-term forms reported); 1.2's totals (740 / 1658 / 300 / 1044 W, PA 262 W) reproduced, or, where an input is not stated in the source, labelled "not independently reproducible" under a rule fixed before the run (no input tuned to force agreement); TR tables transcribed and checked against the .docx; D10 feasibility; D4 state consistency (powers ≥ 0, ordered deep < light < micro, legal transitions, D ≥ T, energy in J); unit tests on small worked examples |
| P3.2 | Split into P3.2a (coupled radio engine), P3.2b (traffic and resources), P3.2c (time-dependent energy); see D12 | P3.2a: gates Q0–Q7. P3.2b: gates B0–B8 (closure, calibration, identical workloads, fixed-point uniqueness, independent reference). P3.2c: state-machine and energy-ledger gates, causality, no service while asleep or waking, P_aux S0–S2 |
| P3.3 | Controllers and baselines | Causality checks; DP = brute force on small instances; Δ ≥ transition times; wake delay enforced |
| P3.4 | Evaluation | Paired seeds, confidence intervals, held-out seeds/geometry/load/hardware; H1–H4 scored as pre-registered |

### P3.1 source-reproduced hardware energy models (PRE-REGISTERED 2026-11-09)

Config `configs/p3/p3_1_energy_models.json`, code
`scripts/p3_1_energy_models.py` (pure Python), tests
`tests/test_p3_1_energy_models.py` (14, synthetic parameters only). This is a
model-construction stage; no scientific prediction is scored.

**What it computes:**
- **3.5 GHz:** P(L) of the published 64T64R Table II. Primary: the µDTX
  case.
- **7.2 GHz:** the FR3 model at 16 hardware points:
  - 53 dBm with the assumed PA (η_max 0.15 and the 3.5 GHz layer's η_max);
  - 50 dBm and 43 dBm with the SiGe PA;
  - each at M_RF 256 (primary), 64, 128 and 1024.
- **Per point:**
  - P_µ and P_empty;
  - the TR ladder (Cat 1/2 × Set 1/3);
  - the DL-anchored ladder (sensitivity);
  - break-even sleep durations.
- **The D10 table.**

**Interpretations fixed before the run:**
- The sub-6 recompute tries four interpretations: the static PA term
  (printed ξ·P_max^α vs Golard's ξ·P_max) × N_C ∈ {1, 3}. The one that
  reproduces Table II is "identified".
- The FR3 reference reproduction:
  - υ_coh = 200 (sensitivity 50 and 1000);
  - rates 13 / 2 Gbit/s × load, used only by the encoder and decoder, whose
    bound is reported;
  - the Fig. 2 PA bar read as the P_cons contribution, or, as the
    alternative, without η_s/c.
- The FR3 static form used for the project is the one under which R2
  passes.

**Gates (all must pass; a failure stops P3.1 for an amendment):**

| Gate | Check |
|---|---|
| J0 | The seven source files match their SHA-256 |
| J1 | TR Tables 5.1-3/4/5 parsed from the .docx equal the config |
| J2a | FR3 closed form = independent phase ledger (≤ 1e-12) |
| J2b | Sub-6 compact model = direct per-state sum (≤ 1e-12) |
| R1 | One interpretation reproduces all 18 Table II values within max(1%, 0.005) |
| R2 | FR3 totals 740 / 1658 / 300 / 1044 W within 2%, and PA 262 W (2%) and 4 W (±0.5 W) |
| J3 | D10 arithmetic; every project point has P_a ≤ P_max |
| J4 | Positive and ordered states, load-monotone power, legal transitions |
| J5 | Unit conversion |

**Not routes to a pass:** the υ_coh sensitivity and the time-consistent
frame variant (data term × (1 − τ_sig)) are diagnostics only.

**Declared before registration:** hand arithmetic while reading:
- γ of 64T64R ≈ 3.50, which depends on neither the static form nor N_C;
- a rough FR3 PA term of about 690 W at 100 W and full load;
- 64T64R P_misc ≈ 63.9 W and P_TRX ≈ 3.18 W.

No P0, P1, P_sleep or FR3 total was computed.

#### P3.1 run 1 (commit 31aa0a4): FAILED J1, R1 and R2 (2026-11-09)

Evidence is kept in `results/phase3/p3_1_energy_models/run1/`. J0, J2a,
J2b, J3, J4 and J5 passed.

| Gate | Result | Cause (diagnosed after the run) |
|---|---|---|
| J1 | Table 5.1-3 not found | **Our parser.** In the XML, "Table 5.1-3" and ":" are separate text runs. Tables 5.1-4/5 matched, and the config numbers equal the expected strings |
| R1 | 17 of 18 values | Under (Golard static term, N_C = 1), every γ, P0 and P1 and the P_sleep of 4T4R and 64T64R reproduce exactly. **8T8R P_sleep: 389.88 W against 363.78 W (+7.2%)**, matched by neither N_C. The printed static term reproduces nothing but γ |
| R2 | v1 totals at M_RF = 1024 miss | v1 printed equations: T1 747 / 740, T3 301 / 300, PA 264.9 / 262 and 4.1 / 4 (all ≤ 1.1%); **T2 1552.5 / 1658 (−6.4%), T4 925.2 / 1044 (−11.4%)**. υ_coh moves the totals by ≤ 6 W |

**R2 diagnosis.**
- The authors' code for the published version differs from v1 in the
  points listed in §1.2.
- A post-hoc reimplementation of that code (scratch diagnostic) gives
  643.1 / 1389.3 / 252.9 / 848.8 W and 209.9 / 3.28 W.
- The Lirias manuscript prints 643 / 1389 / 253 / 849 and 209.9 / 3.3.
- **So v1 is superseded.** Its fully digital totals are not explained by
  its own equations, and that remains unexplained and moot.

#### P3.1 Amendment 1 (pre-registered before run 2)

Recorded in the config (`amendments`).

- **J1:** captions are matched with XML tags allowed between characters.
- **FR3 version:** the project implements the **published** version.
  - η_s/c 0.81, equal to the 3.5 GHz layer's PA and AFE value (0.9 × 0.9).
  - One LNA per antenna.
  - υ_coh by the code's rule (≈ 694 at 7.2 GHz).
  - v1 is kept only to re-record its failed reproduction.
- **New gates:**
  - J2c: the published-mode implementation equals an independent
    reimplementation of the authors' code formulas, for all 7 M_RF × loads
    0 / 0.3 / 1 and the PA sweep, to 1e-12.
  - R2pub: the published totals within 2%, PA 209.9 W within 2%, 3.3 W
    within 0.1 W.
  - R1u: 64T64R, the only sub-6 configuration used, in all 6 values.
- **Pass rule:** J0–J5, J2c, R1u and R2pub.
  - The original R1 and R2 stay in the validation output as failures and
    are not re-scored.
- **Not blind:** R1u and R2pub were already seen in run 1 and in the
  diagnostic. Only J2c is a new test.
- **New sources under J0:** the Lirias PDF and the code files.

#### P3.1 run 2 (commit bc2d8af): all Amendment-1 gates PASSED (2026-11-09)

| Gate | Result |
|---|---|
| J0 | 11 source files match |
| J1 | TR tables equal the .docx |
| J2a | 192 cases, max rel. diff 3.2e-16 |
| J2b | 288 cases, max rel. diff 3.6e-16 |
| J2c | 42 cases, max rel. diff 2.0e-16 |
| R2pub | 643.13 / 1389.35 / 252.93 / 848.79 W (≤ 0.03%); PA 209.92 W and 3.28 W |
| R1u | 64T64R: γ 3.50, P0 {0, 53.92}, P1 {341.57, 161.95}, P_sleep 550.23 (exact to the printed decimals) |
| J3 | SiGe infeasible at 53 dBm with 1024 or 256 PAs; feasible at 50 dBm with 1024 PAs (0.0977 W each, not with 256); feasible at 43 dBm |
| J4, J5 | Pass |

The recorded R1 and R2 (v1) failures are bit-identical to run 1.

**3.5 GHz layer** (64T64R, case C; η_max 0.287 by Golard eq. 5):

| Load L | 0 | 0.15 | 0.3 | 0.5 | 1 |
|---|---|---|---|---|---|
| With µDTX (primary) | 712.2 W | 799.2 | 886.3 | 1002.4 | 1292.6 |
| Without µDTX | 891.8 W | 970.8 | 1049.7 | 1155.0 | 1418.3 |

At L = 0 the split is PA 36.2, AFE 456.0 and baseband 219.9 W. The AFE and
baseband are load-independent.

**7.2 GHz layer** (published FR3 model, M_RF 256, equal DL/UL load):

| Point | Empty | 0.15 | 0.3 | 1 | P_µ |
|---|---|---|---|---|---|
| 53 dBm, assumed PA η 0.15 (**primary**) | 214.8 W | 383.8 | 552.9 | 1341.9 | 195.0 |
| 53 dBm, assumed PA η 0.287 | 172.5 | 263.8 | 355.0 | 780.9 | 160.7 |
| 50 dBm, SiGe | 171.6 | 259.9 | 348.3 | 760.5 | 160.0 |
| 43 dBm, SiGe | 167.2 | 201.0 | 234.7 | 392.2 | 160.0 |

- **Primary point at full load:** PA 1175.0 W, analog 94.8 W, digital
  72.2 W.
- **M_RF** (primary power point, empty / full load):
  - 64: 131.6 / 1230.3 W;
  - 128: 159.3 / 1267.5 W;
  - 256: 214.8 / 1341.9 W;
  - 1024: 536.1 / 1775.3 W.
- **PA efficiency** dominates at high load: η 0.15 → 0.287 lowers full-load
  power from 1342 to 781 W.

**Sleep ladder at the primary point** (anchored at P_µ = 195.0 W, Set 1):

| | Light sleep | Deep sleep |
|---|---|---|
| Cat 1 | 88.6 W; T 6 ms; E_add 0.32 J | 3.54 W; T 50 ms; E_add 3.54 J |
| Cat 2 | 74.4 W; T 0.64 s; E_add 38.6 J | 35.5 W; T 10 s; E_add 603 J |

- **Break-even against staying on and empty** equals the transition time T
  in every Set-1 case: a sleep pays as soon as it is allowed. In Cat 2 with
  Set 3, light sleep needs 0.90 s, more than its T of 0.64 s.
- **The anchoring matters.**
  - The FR3 model's P_µ / P_DL,inst is 0.108, against TR Set 1's 0.196
    (Cat 1) and 0.172 (Cat 2).
  - The P3.0-style DL anchor would put micro sleep at 354.9 W (Cat 1),
    1.8× the component value.
  - So the D4 revision changes sleep powers by up to 1.8×. The DL anchor
    stays a reported sensitivity.

**What P3.1 establishes.**
- Both hardware models are implemented, and each reproduces its published
  source.
  - The 3.5 GHz layer reproduces the measurement-anchored parameters exactly.
  - The 7.2 GHz layer reproduces the ICASSP 2026 numbers within 0.03%, and
    agrees with the authors' code formulas to machine precision.
- The TR ladder is attached consistently.
- 53 dBm needs a PA beyond the sourced SiGe design. This is now explicit in
  every result.

**Key caveat found by P3.1 (open item 5).**
- The two layers are modelled by different kinds of model.
- The 3.5 GHz model is anchored to measurements of real equipment: 712 W
  with no load, of which AFE 456 W and baseband 220 W.
- The 7.2 GHz model is built bottom-up from components: 215 W with no load.
  Such models typically omit costs of real equipment (control, misc AFE,
  power-supply overhead beyond η, fans).
- **Comparing the two directly may therefore favour activating 7.2 GHz.**
- P3.2 must pre-register a sensitivity before any energy comparison: the
  7.2 GHz load-independent power raised to the 3.5 GHz measured
  load-independent level per unit of hardware.

### P3.2a coupled radio-feasibility engine (PRE-REGISTERED 2026-11-09)

Config `configs/p3/p3_2a_coupled_feasibility.json`, code
`scripts/p3_2a_coupled_feasibility.py`, tests
`tests/test_p3_2a_coupled_feasibility.py` (7). It imports the E1c.2/E1c.3
code unchanged.

**Engine.**
- Per user, the serving signal and each other transmitter's interference
  term are built exactly as E1c.3's `sd` builds them.
- A 7.2 GHz state w72 ∈ [0, 1]^57 (0 = off) scales each transmitter's DL
  term and its scheduled UE's UL term.
- A user whose own 7.2 GHz cell is off has no 7.2 GHz service (D13).
- The DL power offset applies network-wide (case C: 0 / −3 / −10 dB = 53 /
  50 / 43 dBm).
- Fractional weights (mean activity) are supported for P3.2b.

**Scenarios** (both ISDs, all cases, 100 realizations, the E1c.3 random
draws):
- all on;
- all off;
- serving cell only (the E1c.3 iso72 counterfactual);
- site 0 only;
- ring 2 off;
- nested random activation of the neighbours with q = 0.25 / 0.5 / 0.75
  (site 0 on);
- leave-one-out for every transmitter.

**Gates:**

| Gate | Check |
|---|---|
| Q0 | E1c.3 outputs and inputs match their manifests |
| Q1a | All on is bit-identical to E1c.3 `sd`, for every array |
| Q1b | Per-user counts equal E1c.3's raw `sd` counts |
| Q2 | Serving-only is bit-identical to `sd_iso72` |
| Q3 | All off has no 7.2 GHz service and the same 3.5 GHz SINR |
| Q4 | An independent scalar path agrees within 1e-9 dB on 60 random (state, mask, offset) pairs per ISD, cases A and C |
| Q5 | No user gains 7.2 GHz SINR when more cells switch on |
| Q6 | DL 7.2 GHz SINR is monotone in power; UL and 3.5 GHz are untouched |
| Q7 | Vectorized leave-one-out equals explicit masks |

**Predictions** (site-0-served users, ISD 200, C at 50 dBm, M.2412, F2):
- **P1:** switching ring 2 off recovers at most a quarter of what switching
  all neighbours off recovers in 7.2 GHz DL feasibility.
- **P2:** losing 3 dB of DL power costs less when the network is
  interference-limited (all on) than when site 0 is alone.

**Decision rule D14.** If ring 2 off moves 7.2 GHz DL and UL feasibility by
≤ 1 point each, P3.3 may score the 7-site cluster with ring 2 as an
interference guard. Otherwise it scores site 0 only.

**Declared before registration.**
- Tests on the synthetic 7-site layout only.
- A debugging run showed that E1c.2's scalar candidate cache is keyed by
  (ISD, realization) but depends on the case. P3.2a therefore keeps one cache
  per case.
- No P3.2a output on the real layout was computed.

#### P3.2a run 1 (commit a1919f3): crashed at setup; Amendment 1 (2026-11-09)

**The crash.** It happened before any computation: `NAMES` is a list and
`EXTRA` a tuple, so allocating the count arrays raised a TypeError. The log
is kept as `run1_stdout.txt`.

**Amendment 1.**
- One list `ALLN` replaces every `NAMES + EXTRA`. No logic changes.
- A labelled smoke mode (`--smoke-realizations N`) runs the full pipeline:
  - it refuses the measured-run folder;
  - it forces the result to "fail".

**Smoke run** (2 realizations, scratch folder).
- Every gate was computable.
- Q0, Q1a and Q3–Q7 passed.
- Q1b and Q2 failed, as they must: they compare against E1c.3's
  100-realization results. Q2 had no array mismatch.

**Declared: the smoke run printed preliminary prediction values.**
- P1: 0.00024 against 0.00185.
- P2: 0.00034 against 0.00030.
- D14: DL 0.00024, UL 0.00009.

Predictions, the D14 rule and every threshold are unchanged.

#### P3.2a run 2 (commit 86df67d): all gates PASSED (2026-11-09, 2603 s)

| Gate | Result |
|---|---|
| Q0 | Inputs pass |
| Q1a | All on is bit-identical to E1c.3 `sd` (0 differing arrays) |
| Q1b | 324 raw count arrays per ISD equal E1c.3's |
| Q2 | Serving-only is bit-identical to `sd_iso72`; summary difference 0.0 |
| Q3 | All off: 0 violations |
| Q4 | Scalar path within 7.8e-14 dB (ISD 200) and 9.9e-14 dB (ISD 500) on 60 pairs each, cases A and C. The copy equals E1c.3's scalar exactly. This extends E1c.3's scalar check, which only covered case A, to case C |
| Q5, Q6 | 0 violations |
| Q7 | 384 / 384 leave-one-out comparisons equal |

**Finding 1: a third of site 0's users are served by a neighbour.**
- Best-server association gives 65–66% of the users in site 0's nominal
  cell to site 0. About 31% go to ring-1 sectors and 3–4% to ring 2.
- Switching a neighbour's 7.2 GHz cell off removes 7.2 GHz service from
  users who are physically in site 0's area. This is the largest coupling
  effect.
- With site 0 alone, 7.2 GHz DL feasibility over all users falls from
  0.996 to 0.650 (ISD 200, C, 50 dBm).
- Controllers must therefore act per serving cell, as D13 implies.

**Finding 2: interference coupling through feasibility is small in the
primary configuration.**

Users served by site 0, M.2412, F2:

| Case | All on | q 0.75 / 0.5 / 0.25 | Site 0 only | Change |
|---|---|---|---|---|
| ISD 200, C 50 dBm, DL | 0.9977 | 0.9981 / 0.9986 / 0.9989 | 0.9994 | +0.17 points |
| ISD 200, C 50 dBm, UL | 0.8866 | 0.8871 / 0.8875 / 0.8878 | 0.8883 | +0.17 |
| ISD 200, A, DL | 0.9768 | 0.9821 / 0.9874 / 0.9916 | 0.9963 | +1.95 |
| ISD 200, A, UL | 0.9206 | — | 0.9254 | +0.48 |
| ISD 500, A, DL | 0.9031 | 0.9169 / 0.9293 / 0.9402 | 0.9496 | +4.65 |
| ISD 500, C 50 dBm, DL | 0.9689 | — | 0.9742 | +0.53 |

- Feasibility falls steadily as more neighbours switch on, nearly linearly
  in q.
- Case C's 16×32 arrays leave the F2 thresholds almost saturated. In the
  primary configuration the activation coupling therefore acts through
  service availability (Finding 1) and spectral efficiency (P3.2b), not
  through the feasible/infeasible boundary.
- E1c.3's "3–8 points" was the per-user serving-only counterfactual in
  case A, which includes neighbour-served cell-edge users. It is consistent:
  case A at ISD 200 gives 0.9618 against 0.9960 over all users.

**Finding 3: 50 dBm costs little at ISD 200.** 7.2 GHz DL feasibility
(case C, all on, all users):

| ISD | 53 dBm | 50 dBm (primary) | 43 dBm |
|---|---|---|---|
| 200 | 0.9967 | 0.9960 (−0.07 points) | 0.9901 (−0.65) |
| 500 | 0.9638 | 0.9505 (−1.3) | 0.9034 (−6.0) |

UL and the 3.5 GHz layer are unchanged by construction (Q6).

**Predictions.**
- **P1 held.** Ring 2 off gains 0.025 points, against 0.172 points for all
  neighbours off (a quarter would be 0.043).
- **P2 failed.** The 53 → 50 dBm loss is 0.035 points with all on and
  0.034 points with site 0 alone, the reverse of the predicted order.
  - Both are negligible.
  - At ISD 200 the few users that a 3 dB cut affects in case C are
    noise-limited whether or not the neighbours are on.

**Decision D14: cluster scoring is admissible for the primary point.**
- Ring 2 off − all on: DL +0.025 points and UL +0.038 points, against the
  1-point limit (users served by site 0, ISD 200, C at 50 dBm).
- P3.3 may score the 7-site cluster at ISD 200, with ring 2 as an
  interference guard and the truncation bias stated.
- **Caveat, outside the rule:** the same quantity at ISD 500, case A, is
  2.0 points (0.9031 → 0.9232). At ISD 500, P3.3 should score site 0 only
  or carry the bias explicitly.

**Limit of the leave-one-out map.**
- Single-transmitter effects are dominated by the switched cell's own users
  losing service. The ranking is site 0's sectors first, then the ring-1
  sectors facing site 0.
- The run did not separate the interference relief to other users, which
  would need a served/unserved split that was not pre-registered. So the
  map is not read as an interference ranking.

### P3.2b traffic and resource layer (PRE-REGISTERED 2026-11-10)

Config `configs/p3/p3_2b_traffic_resources.json`, code
`scripts/p3_2b_traffic_resources.py`, tests
`tests/test_p3_2b_traffic_resources.py` (10, on the synthetic layout and
analytic toy cells). It imports the P3.2a engine unchanged and implements
D15–D19.

**What it computes.** It covers two networks:
- 3.5 GHz only (all 7.2 GHz cells off);
- all on, with UL carrier aggregation or with a DL-only SCell.

For each network it computes, per carrier and direction:
- the load-coupled occupancy;
- the offered, served, unserved and not-admitted traffic;
- the flow throughputs and the admitted-user outage.

These are evaluated on a traffic grid. The grid is x ∈ {0.25, 0.5, 1, 1.5, 2,
3, 4, 6} plus the ETSI medium level, in units of the calibrated busy hour; the
UL grid is ρ·x for both UL/DL ratios.

**Sustainable traffic** x* is the largest x at which the admitted-user outage
is ≤ 5%:
- found by bisection, to 0.005;
- DL and UL separately; x*_both = min.

**Scope:**

| ISD | Cases (7.2 GHz DL offset) |
|---|---|
| 200 (primary) | A (0 dB), B (0 dB), C (0 / −3 / −10 dB = 53 / 50 / 43 dBm) |
| 500 (qualified) | C (0 / −3 / −10 dB) |

The UL rescue sensitivity covers case C at both ISDs. The primary point is
ISD 200, C at 50 dBm, UL/DL = 10/90.

**Gates:**

| Gate | Check |
|---|---|
| B0 | P3.2a / E1c.3 / E1c.2 manifests and hashes; code constants equal the config |
| B1 | The load-weighted engine with weights (1, w72) is bit-identical to P3.2a's engine (8 sector patterns, all offsets, realizations 0–1) |
| B2 | The sector-grouped SINR equals the explicit 57-TRxP weighting within 1e-9 dB, for random loads |
| B3 | Every fixed point converged (residual ≤ 1e-9); on realizations 0–9 the solution from L = 0 equals the one from L = 1 within 1e-8 (uniqueness) |
| B4 | Served + unserved = offered (1e-12); the occupancy recomputed from served traffic equals L (1e-9); 0 ≤ L ≤ 1 |
| B5 | Calibration residual ≤ 1e-9 |
| B6 | The offered traffic per user is bit-identical across states and power levels |
| B7 | Six identities and monotonicity checks: all-off DL independent of 7.2 GHz power; DL-only SCell UL = all-off UL bitwise; 3.5 GHz DL load with 7.2 on ≤ all off; loads and outage non-decreasing in traffic; zero traffic gives L = 0 and θ = C |
| B8 | An independent per-user Python loop on the ungrouped engine reproduces the offered loads (1e-9) and outage flags at 8 primary-point instances |

**Predictions** (ISD 200, C at 50 dBm):
- **P1:** with 3.5 GHz only and UL/DL = 10/90, the UL reaches the
  5th-percentile limit first (x*_UL < x*_DL).
- **P2:** at UL/DL = 26/74 with all cells on and UL CA, a DL-only evaluation
  overstates the sustainable traffic by at least 25% (x*_DL ≥ 1.25 x*_both).
- **P3:** at UL/DL = 26/74, UL CA on 7.2 GHz adds at least 20% sustainable
  traffic over a DL-only SCell.

**Decision rule.** If all gates pass, P3.2b is the traffic layer of P3.2c and
P3.3. P3.2c's growth sweep runs from g = 1 to at least the all-on UL-CA x*_both
of the primary point, with the 3.5-GHz-only x*_both marked.

**Declared before registration:**
- the synthetic-layout tests and one synthetic probe;
- the C-AMC floor values that motivated D19's admission rule;
- E1c.3's Bernoulli-minus-mean pairs (≤ 1.1 points), which bound the
  mean-interference approximation;
- two smoke runs on the real layout, each on 2 realizations in a scratch
  folder (127 s and 124 s):
  - every gate was computable and passed; the overall verdict was forced to
    fail by the smoke flag;
  - between them, one memory change: per-user SINR arrays are dropped after
    aggregation;
  - smoke mode prints no result values, and its output files were not opened.

#### P3.2b run 1 (commit 55353d6): gates B0–B8 PASSED; UL admission point off the register (2026-11-10, 5576 s)

Evidence is kept in `results/phase3/p3_2b_traffic_resources/run1/`. Raw
arrays are git-ignored, with their hashes in the manifest.

| Gate | Result |
|---|---|
| B0 | Inputs and constants pass |
| B1 | 240 (ISD 200) and 144 (ISD 500) engine comparisons, all bit-identical |
| B2 | Max 2.8e-14 dB over 1.49 M and 5.93 M user comparisons |
| B3 | 138 fixed points converged (residual ≤ 2.3e-11, ≤ 20 iterations); from L = 0 and from L = 1 they agree to 3.8e-11 over 18 360 loads |
| B4 | Closure ≤ 2.2e-16; occupancy identity ≤ 2.0e-11; no load outside [0, 1] |
| B5 | Calibration residuals pass |
| B6 | 432 workload comparisons, 0 mismatches |
| B7 | 0 violations of the six identities and monotonicity checks |
| B8 | 10 primary-point instances (4 DL, 6 UL); offered loads within 1.8e-14 relative; 0 outage mismatches |

**Registered prediction verdicts** (ISD 200, C at 50 dBm):

| Prediction | Run 1 values | Verdict |
|---|---|---|
| P1: x*_UL < x*_DL, 3.5 GHz only, 10/90 | x*_UL 3.059 against x*_DL 1.445 | **Failed** (the DL binds; the UL needs 2.1× more traffic) |
| P2: x*_DL ≥ 1.25 x*_both, all on with UL CA, 26/74 | 3.719 against 2.413 (ratio 1.54) | **Held** |
| P3: UL CA ≥ 1.2 × DL-only SCell, x*_both, 26/74 | 2.413 against 0.967 (ratio 2.49) | **Held** |

Run 1's sustainable UL traffic y* (units of T_0.5), kept for comparison with
run 2:

| ISD, case | off | on, UL CA | off, rescue | on, UL CA, rescue |
|---|---|---|---|---|
| 200, A | 0.4570 | 0.6328 | — | — |
| 200, B | 0.4570 | 0.7578 | — | — |
| 200, C | 0.3398 | 0.8477 | 0.2188 | 0.4063 |
| 500, C | 0.1641 | 0.2656 | 0 (fails unloaded) | 0 (fails unloaded) |

**Found while verifying the outputs, after the run.**
- The run's UL admission point is +0.36 dB (SE 0.19716). The registered
  text fixes it at the UL snr_min, −0.64 dB (SE 0.1965):
  - D19: "UL: 0.197 at −0.64 dB, which equals the UL snr_min";
  - the config's `model.admission` and `seen_before_registration`.
- **Cause.** The code took the C-AMC curve's first grid point *strictly*
  above snr_min. The registered unit test asserted g_adm > snr_min, so the
  test and the code agreed with each other and not with the text.
- **Why it matters.**
  - The UL curve has no BLER-floor point. Its SE is 0 below snr_min and
    0.1965 at it, and +0.36 dB lies on the same MCS-0 plateau (0.19716).
  - Run 1 therefore refused UL users whom D8 counts as servable and whom
    the rule was never meant to exclude.
  - Affected users (run 1's UL "share between snr_min and admission"):
    A and B 0.144%, C ISD 200 0.391%, C ISD 500 2.399%.
- **The DL is unaffected.** Its point (−0.64 dB) lies above the −1.64 dB
  BLER-floor point under either reading.
- **No gate could catch it.** B0 compared the TDD, OH, SE5, ε, ρ and grid
  constants, not the admission point.

#### P3.2b Amendment 1 (registered before run 2)

Recorded in the config (`amendment_1`).

- **Change.** The admission point is the C-AMC value at the first grid
  point at or above snr_min whose SE is on the MCS-0 plateau (SE ≥ 0.1; the
  DL floor point has SE 0.0016).
  - DL: −0.6387 dB, SE 0.19695 (unchanged).
  - UL: −0.6387 dB, SE 0.19651 (as registered).
  - Model, scope, grids, thresholds, gates B0–B8 and predictions are
    unchanged.
- **B0, added checks.**
  - Run 1's evidence is intact: manifest hash, git head, config hash, and
    every output hash.
  - Every case's admission points equal the registered values exactly.
- **New gate B9.** Run 2 must equal run 1 exactly wherever the UL admission
  point cannot reach:
  - every DL result and raw DL array;
  - calibration, grids, user counts, DL labels and every x*_DL.

  Per case, the UL not-admitted share must equal run 1's minus run 1's
  "between" share (to 1e-12), that share must be 0, and the TR 36.942
  low-end share must be unchanged.
- **Pass rule:** B0–B9.
- **Result of record:** run 2, for every P3.2b number and for the traffic
  layer of P3.2c and P3.3. Run 1 stays as evidence.
- **Predictions.**
  - Unchanged. Their registered verdicts are run 1's: P1 failed, P2 and P3
    held.
  - Run 2 recomputes them. A verdict that differs between the runs is
    reported as admission-sensitive, not substituted.
- **Not blind.**
  - Every run-1 result was seen before this amendment.
  - The direction of the UL effect is not fixed in advance: the added users
    add load, and they also enter the outage denominator.
- **Tests:** 12 (the admission test now asserts the registered values; two
  new tests check B9's comparator). The full suite passes (201 tests).
- **Smoke run** (2 realizations, scratch folder, 202 s).
  - Every gate B0–B9 was computable and passed. B9 ran as a
    self-comparison, to exercise the code path only.
  - The smoke flag forced the overall verdict to fail.
  - It printed no result values, and its output files were not opened.

#### P3.2b run 2 (commit 60b358b): all gates B0–B9 PASSED; result of record (2026-11-10, 8399 s)

Outputs are in `results/phase3/p3_2b_traffic_resources/`: summary,
validation, manifest and `run2_stdout.txt`.
- The raw arrays are git-ignored. Their hashes in the manifest
  (isd200 ccc5e860…, isd500 8ddf7112…) match the files on disk.
- The manifest's config hash (e4e23361…) is the committed config's.
- Run 1 is kept in `run1/` as evidence.

| Gate | Result |
|---|---|
| B0 | Constants equal the config. Run 1's evidence is intact. Both admission points equal the registered −0.6387 dB exactly |
| B1 | 240 (ISD 200) and 144 (ISD 500) engine comparisons, all bit-identical |
| B2 | Max 2.8e-14 dB over 1.49 M and 5.93 M user comparisons |
| B3 | 138 fixed points converged (residual ≤ 2.3e-11, ≤ 20 iterations). Solutions from L = 0 and from L = 1 agree to 3.8e-11 over 18 360 loads |
| B4 | Closure ≤ 2.2e-16; occupancy identity ≤ 2.0e-11; no load outside [0, 1] |
| B5 | Calibration residuals ≤ 4.4e-12 |
| B6 | 432 workload comparisons, 0 mismatches |
| B7 | 0 violations of the six identities and monotonicity checks |
| B8 | 10 primary-point instances (4 DL, 6 UL). Offered loads agree within 1.8e-14 relative; 0 outage mismatches. 11 693 UL users are checked, against 11 574 in run 1: the re-admitted users |
| B9 | The DL view equals run 1's. 48 raw DL arrays, 0 mismatches. The UL not-admitted share moved by exactly run 1's "between" share (to 3.6e-16), that share is now 0, and the TR 36.942 low-end share is unchanged (4 cases) |

**Wall time.** The run took 8399 s; run 1 took 5576 s. The slowdown was
irregular: case B's UL "off" step took 1588 s here against about 50 s in
run 1.
- B1 and B9 are bitwise comparisons, and both passed. The slowdown
  therefore changed no computed value.
- It is attributed to the host (sleep or contention), not to the code.

**Registered prediction verdicts** (ISD 200, C at 50 dBm). No verdict
differs between the runs, so none is admission-sensitive.

| Prediction | Run 1 | Run 2 (of record) | Verdict |
|---|---|---|---|
| P1: x*_UL < x*_DL, 3.5 GHz only, 10/90 | 3.059 against 1.445 (2.12×) | 2.848 against 1.445 (1.97×) | **Failed** in both runs: the DL binds |
| P2: x*_DL ≥ 1.25 x*_both, all on with UL CA, 26/74 | 3.719 against 2.413 (1.54) | 3.719 against 2.335 (1.59) | **Held** in both runs |
| P3: x*_both, UL CA ≥ 1.2 × DL-only SCell, 26/74 | 2.413 against 0.967 (2.49) | 2.335 against 0.901 (2.59) | **Held** in both runs |

**Calibration** (identical to run 1 by B9). T_0.5 is the per-site traffic at
which the busy-hour load is 0.5; x = 1 is that traffic.

| ISD, case | T_0.3 (Mbit/s per site) | T_0.5 (Mbit/s per site) | x_30 = T_0.3 / T_0.5 |
|---|---|---|---|
| 200, A and B | 60.72 | 95.32 | 0.637 |
| 200, C | 326.26 | 513.60 | 0.635 |
| 500, C | 283.47 | 437.75 | 0.648 |

The unscored expectation x_30 > 0.6 is met in every case.

**DL sustainable traffic x*_DL** (identical to run 1 by B9). The bisection
brackets are 0.0039 wide.

| ISD, case | 3.5 GHz only | All on, 0 dB | −3 dB | −10 dB | On/off | DL traffic on 7.2 GHz at x = 1 |
|---|---|---|---|---|---|---|
| 200, A | 1.434 | 1.918 | — | — | 1.34× | 0.799 |
| 200, B | 1.434 | 1.742 | — | — | 1.22× | 0.984 |
| 200, C | 1.445 | 3.738 | 3.719 | 3.656 | 2.57× at −3 dB | 1.000 / 1.000 / 0.996 |
| 500, C | 1.480 | 4.074 | 4.102 | 4.137 | 2.77× at −3 dB | 0.994 / 0.981 / 0.931 |

In case C, the best-carrier rule moves almost all DL traffic to 7.2 GHz and
leaves the 3.5 GHz DL nearly idle. This is a declared limit, and it makes
x*_DL(on) conservative. At ISD 500, x*_DL rises as the 7.2 GHz power falls:
the weaker 7.2 GHz carrier returns some users to 3.5 GHz, which balances
the two carriers.

**UL sustainable traffic y*** (units of T_0.5), run 2 with the change from
run 1:

| ISD, case | Off | On, UL CA | UL CA gain | Off, rescue | On, UL CA, rescue |
|---|---|---|---|---|---|
| 200, A | 0.4414 (−3.4%) | 0.6016 (−4.9%) | 1.36× (run 1 1.38×) | — | — |
| 200, B | 0.4414 (−3.4%) | 0.7188 (−5.2%) | 1.63× (1.66×) | — | — |
| 200, C | 0.3164 (−6.9%) | 0.8203 (−3.2%) | 2.59× (2.49×) | 0.2188 (same bracket) | 0.4063 (same bracket) |
| 500, C | 0.1055 (−35.7%) | 0.1563 (−41.2%) | 1.48× (1.62×) | 0 (fails unloaded) | 0 (fails unloaded) |

The DL-only SCell's UL equals the off state's UL bitwise (B7).

**Sustainable traffic x*_both, case C at −3 dB** (binding direction in
brackets; run 1 in square brackets where it differs):

| State | ISD 200, 10/90 | ISD 200, 26/74 | ISD 500, 10/90 | ISD 500, 26/74 |
|---|---|---|---|---|
| 3.5 GHz only | 1.445 (DL) | 0.901 (UL) [0.967] | 0.949 (UL) [1.477, tied] | 0.300 (UL) [0.467] |
| All on, UL CA | 3.719 (DL) | 2.335 (UL) [2.413] | 1.406 (UL) [2.391] | 0.445 (UL) [0.756] |
| All on, DL-only SCell | 2.848 (UL) [3.059] | 0.901 (UL) [0.967] | 0.949 (UL) [1.477] | 0.300 (UL) [0.467] |
| 3.5 GHz only, rescue | 1.445 (DL) | 0.623 (UL) | 0 | 0 |
| All on, UL CA, rescue | 3.656 (UL) | 1.156 (UL) | 0 | 0 |

These are the findings of record.
- **Today's mix (10/90), ISD 200.** With 3.5 GHz only, the DL binds (P1
  failed).
  - Turning on 7.2 GHz with a DL-only SCell moves the binding to the UL. It
    gives 1.97× the 3.5-GHz-only traffic.
  - UL CA lets the 7.2 GHz DL capacity be used: 2.57×, which is 31% more
    than the DL-only SCell.
- **AI-era mix (26/74), ISD 200.** The UL binds in every state.
  - A DL-only SCell adds no sustainable traffic (1.00×).
  - UL CA gives 2.59×.
  - A DL-only evaluation overstates the all-on network by 1.59×.
  - The 3.5-GHz-only network sustains 0.901 of the calibrated busy hour.
    At this mix its UL fails the QoS criterion at the traffic that loads the
    DL to 0.5.
- **ISD 500.** The UL binds in every state, at both mixes and in both runs.
  - Run 1's tie at 10/90 (1.477 against 1.480) is now UL by 1.56×.
  - Only the binding direction and the sign of the UL CA gain (1.48×) are
    robust here. The magnitudes depend on admission (next paragraph).
- **Rescue** (fluid PRB, case C). It lowers sustainable traffic at ISD 200
  and is infeasible unloaded at ISD 500.
  - Rescued users cannot reach R_UL even at zero load. At the lowest grid
    point (off state) the outage is 1.8% at ISD 200 and 23.5% at ISD 500.
  - At ISD 200 their outage uses part of the 5% budget before the load
    does, and they are the costliest users per bit.
  - The pooled admitted-user outage mixes coverage and capacity outage.
    This is reported as a limitation. The rescue stays a sensitivity, not
    the model.
- **Hazard-15 labels** (3.5 GHz, full buffer).

  | ISD, case | UL not admitted | of which in TR 36.942's range [−10 dB, snr_min) | DL not admitted |
  |---|---|---|---|
  | 200, A and B | 0.571% | 0.531% | 0 |
  | 200, C | 2.197% | 1.888% | 0 |
  | 500, C | 21.746% | 14.623% | 0.040% |

  At ISD 500, x* is conditional on the 78.3% of users that the UL can
  admit.

**Admission sensitivity of the UL (the amendment's effect).** Amendment 1
re-admitted the users between −0.64 and +0.36 dB, all of them on the MCS-0
plateau. There are 0.144% (A, B), 0.391% (C, ISD 200) and 2.399% (C, ISD 500)
of them. Every UL y* fell. The registered note had left the direction open.
Two mechanisms explain the fall, and both are confirmed from the outputs.
1. **Load.** The re-admitted users are the costliest per bit, so they raise
   the 3.5 GHz UL load at equal y (below saturation):

   | ISD | Off | UL CA |
   |---|---|---|
   | 200 | +6.4 to +7.5% | +20 to +23% |
   | 500 | +22 to +25% | +35 to +39% |

   Under UL CA the effect is larger because these users stay on 3.5 GHz.
2. **Outage.** In case C at 100 MHz, the MCS-0 plateau's peak rate is
   4.061 Mbit/s (SE 0.1965). R_UL is 3.429 Mbit/s, which is 84% of that
   rate.
   - A plateau user therefore meets R_UL only while its cell's load stays
     below 0.156.
   - At ISD 500 (off, y = 0.0556), the admitted-user outage rises from
     0.01% (run 1) to 2.16%.

At ISD 200 this moves y* by 3–7%, i.e. 4–10 bisection brackets, and no
verdict changes. At ISD 500, one 1 dB grid step at the admission point moves
y* by 36–41%. ISD 500 was registered as a qualified case, and its UL
magnitudes are governed by the cell-edge plateau users. They are reported
with the admitted share and with this sensitivity, and are not a headline
number (open item 6).

**Decision** (the registered rule; all gates passed). P3.2b, as run 2, is
the traffic layer of P3.2c and P3.3. P3.2c's growth sweep spans g = 1 to at
least the all-on UL-CA x*_both of the primary point, with the
3.5-GHz-only x*_both marked:

| Mix | Sweep to at least | 3.5-GHz-only x*_both marked |
|---|---|---|
| 10/90 (primary) | 3.719 | 1.445 |
| 26/74 (stress) | 2.335 | 0.901 |

## 4. Open items

1. **P3.1, FR3 inputs.** List every input of 1.2's digital model that the
   text does not state (e.g. υ_coh). For each, fix a value with its
   rationale before the run. If a published total then misses its
   tolerance, report it as "not independently reproducible". Never tune
   the input.
2. **P3.2, UL traffic source** (independent of DL) and the daily load
   profile source. *Closed by D18 (2026-11-10):* the ETSI ES 202 706-1 load
   levels and durations, and the Ericsson UL/DL volume ratios (10/90 primary,
   26/74 stress).
3. **P3.3, secondary-cell activation delay** from TS 38.133.
4. **Radio–hardware architecture caveat** (D2, D3): stated in the paper,
   with E1.4 S4 as its radio-side size.
5. **Model-class asymmetry** (found in P3.1 run 2). The 3.5 GHz model is
   measurement-anchored; the 7.2 GHz model is bottom-up (712 W against
   215 W with no load). Before any energy comparison, P3.2 must
   pre-register a sensitivity that scales the 7.2 GHz load-independent
   power to the measured level, with its rule fixed in advance.
6. **ISD 500 UL magnitudes depend on admission** (found in P3.2b run 2).
   - In case C, R_UL is 84% of the MCS-0 plateau's peak rate.
   - At ISD 500, one 1 dB step of the admission point (2.4% of users)
     moves y* by 36–41%. The binding direction and the sign of the UL CA
     gain do not move.
   - **Recommendation for P3.2c's registration.** ISD 500 enters only as
     a qualified, coverage-limited case, stated with its UL admitted share
     (78.3%). It carries one pre-registered sensitivity on the edge-user
     rule (R_UL or the outage percentile), with values fixed before the
     run. ISD 200 carries the headline numbers.

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

## 6. Review of P3.1 and the P3.2 design (ChatGPT, 2026-11-09) and response

| Point | Verdict | Action |
|---|---|---|
| Keep the published models unchanged; add equipment overhead as a labelled, state-dependent term | Agreed | D11 |
| S1 +250 W, S2 +500 W | Agreed in substance; values set by rule | S1 = Golard's misc + link-layer components (about 172 W); S2 = no-load parity with the measured 3.5 GHz radio (about 541 W) |
| Say what the overhead does in micro sleep, deep sleep and wake | Agreed | D11 state rule |
| Source-matched amplifier as the primary; 53 dBm as a hypothetical upgrade | Agreed; reverses rev 1 | D10 (revised): 50 dBm SiGe primary |
| ISD 200 primary; ISD 500 qualified | Agreed (already D6) | No change |
| P3.2 in three stages with the five checks | Agreed | D12. P3.2a covers all-on/all-off and single-cell changes; causality, wake delay and identical workloads are P3.2b/c and P3.3 gates |
| Three electrical states (low traffic, sleep, transitioning) | Agreed (already in D4) | Stated in revision 2 |
| P_site = P_3.5 + P_7.2 + P_shared, with no double counting | Agreed | D1: shared site terms are omitted and stated; each model's η counts once |
| IEEE TCOM date 13 May 2026 | Recorded, not verified here | §1.3 |
| Addition of mine: cluster scoring needs a truncation test | — | D14, decided by P3.2a's rule |
