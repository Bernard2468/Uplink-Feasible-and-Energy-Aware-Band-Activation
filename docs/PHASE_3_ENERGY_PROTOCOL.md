# Phase 3: energy model and band-activation control (protocol)

Status: **P3.0 design proposal, revisions 1-3 (2026-11-09/10); P3.1 complete (run 2, amendment 1); P3.2a complete (run 2, amendment 1); P3.2b complete (run 2, amendment 1); P3.2c complete (run 1, revision 4); revision 5 and P3.3 pre-registered; P3.3 complete (run 2, amendment 1); P3.4 (held-out test) complete (run 1): all gates G0–G10 passed, H1, H2, H4 and Q3 held; publication-readiness audit recorded (§10); figure stage complete, Figures 1–8 (§10.6, §10.8; Figures 1 and 2 revised to single panels, §10.9; Figures 2 and 7 in colour, §10.10, §10.12); manuscript draft (§10.11); the author's final version in LaTeX, single column (§10.13, §10.14).** Revision 1
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

### Revision 4 (2026-11-10, after P3.2b, before P3.2c)

These decisions were fixed before any P3.2c output. They answer the ChatGPT
audit of P3.2b and its P3.2c review (§7).

**D20: presentation labels: carrier selection, not carrier aggregation.**
- **What the code does.** `assign()` gives each user one carrier per
  direction, chosen statically by full-buffer peak rate (D19). No user
  transmits on both carriers at once. There is no UE power split, and no
  CA signalling or capability limit.
- **Labels used in tables, figures and text from now on:**

  | Code name | Label |
  |---|---|
  | `on` (DL) | 7.2 GHz DL carrier selection |
  | `on_ulca` (UL) | 7.2 GHz UL enabled (UL carrier selection) |
  | `on_scell_dl` (UL) | DL-only 7.2 GHz carrier (UL stays on 3.5 GHz) |

- **The code names are kept** so that every run reproduces.
- **P3.2b's text keeps its wording as the record.** Its "UL CA gain" reads as
  the gain from enabling 7.2 GHz UL carrier selection.
- **Per-UE simultaneous UL CA is not modelled.** It needs per-UE
  multi-carrier scheduling, UE power sharing and capability limits. Whether
  it would raise or lower the gain is not claimed.

**D21: two service criteria; the all-user one is primary for energy.**
- **(a) Admitted-user** pooled outage ≤ 5% (D17, as registered). It is kept
  to reproduce P3.2b and to compare with it.
- **(b) All-user** outage ≤ 5% (new). A user that cannot be admitted counts
  as in outage, and its offered demand stays in the ledger as unservable.
  - Exactly, out_all = n + (1 − n)·out_adm, where n is the not-admitted
    share. So (b) is out_adm ≤ (0.05 − n)/(1 − n).
  - Example: case C at ISD 200, UL with 3.5 GHz only. n = 2.197%, so the
    admitted users keep a 2.866% budget.
- **(b) is primary for every energy comparison from P3.2c on.** An energy
  saving is reported only between policies that both meet the same
  criterion in every level of the day. Otherwise the comparison is
  reported as a service–energy trade-off.
- **Neither criterion is "M.2410 compliance".**
  - The target rate comes from M.2410's 5th-percentile SE (D17).
  - The evaluation is this model's own: flow-level, with static association.
    It is not the M.2412 full-buffer method, which samples all user
    locations.

**D22: ISD 500 leaves the energy stages.**
- **Why.** Under (b), ISD 500 fails at any traffic: 21.7% of users cannot be
  admitted in the UL, with or without 7.2 GHz.
- **Consequence.** P3.2c and P3.3 run at ISD 200 only.
- **How ISD 500 is reported.** As a coverage boundary (an H4 regime), from
  P3.2b's outputs, with open item 6.
- This removes about three quarters of the compute.

**D23: wake delay = TR transition time + SCell activation delay.** This
replaces D7's max with a sum: UE SCell activation needs the SCell's SSBs,
which a sleeping cell sends only once its wake-up is complete.
- **Source:** TS 38.133 V19.6.0 (38133-j60, SHA-256 a4203e77…) cl. 8.3.2.
  - A 7.2 GHz cell after deep sleep is an *unknown* SCell. Activation is
    inter-band.
  - FR1 rule: T_activation = T_FirstSSB_MAX + T_SMTC_MAX + 2·T_rs + T_Δ + 5 ms.
  - With SMTC period P, T_FirstSSB_MAX ≤ P and T_SMTC_MAX = T_rs = P.
    T_Δ = 0, because the SSB is not 12 PRB wide. So T_activation ≤ 4P + 5 ms.
  - The CSI report is due by T_HARQ + T_activation + T_CSI_reporting. The
    spec gives these two terms no number; one SMTC period P is allowed for
    them together (an assumption).
  - Hence **T_SCell = 5P + 5 ms**.

  | Use | SMTC period P | T_SCell |
  |---|---|---|
  | Primary | 20 ms (the SSB period a UE assumes for initial access) | 105 ms |
  | Sensitivity | 160 ms (the longest SMTC period) | 805 ms |

- **Label:** 7.2 GHz lies above FR1 (7.125 GHz), and TS 38.133 defines no
  FR3 requirement. The FR1 rule is used as an extrapolation, like D4's TR
  ladder.
- **This closes open item 3.**

**D24: P3.2c scope.**
- **Network.** ISD 200, case C. The 7.2 GHz hardware matches the radio's DL
  power (D10):

  | DL offset | Hardware | Role |
  |---|---|---|
  | −3 dB | 50 dBm, SiGe | Primary |
  | 0 dB | 53 dBm, assumed PA, η 0.15 | Sensitivity |
  | −10 dB | 43 dBm, SiGe | Sensitivity |

  - M_RF = 256. The M_RF = 64 / 128 / 1024 rows are energy only and
    labelled "service architecture not matched" (D3).
  - The 3.5 GHz layer is the measurement-anchored model, "with µDTX / AFE
    idle" (right); "without" (left) is a sensitivity.
- **Day.** The ETSI ES 202 706-1 Table 3 durations, in a fixed order:

  | Level | Hours | Traffic |
  |---|---|---|
  | Low | 00–06 | Zero (SSB/RMSI only, D18) |
  | Medium | 06–16 | x = g·x_30 |
  | Busy | 16–24 | x = g |

  - The order is an assumption. Any contiguous order gives the schedules
    below one wake and one sleep per day.
- **Growth** g ∈ {0.5, 0.75, …, 4.0} (15 values). This covers the P3.2b
  decision rule's span (g = 1 to ≥ 3.719).
- **Mixes:** 10/90 (primary) and 26/74 (stress).
- **Policies** (time-of-day schedules, not controllers). Controllers are
  P3.3's.

  | Policy | 7.2 GHz state |
  |---|---|
  | `35only` | 3.5 GHz only; 7.2 GHz in deep sleep all day, with no transitions |
  | `aon_ulsel`, `aon_dlonly` | On all day, with UL enabled or DL only |
  | `sch_busy_*` | On in the busy level, asleep otherwise |
  | `sch_medbusy_*` | On in medium and busy, asleep in low |
  | `bound_*` | Each schedule with zero transition time, zero E_add and zero T_SCell; labelled "optimistic bound", never a deployable result |

- **Sleep.** A sleeping 7.2 GHz cell uses deep sleep, the minimum-energy
  state for multi-hour sleeps (P3.1 break-even ≤ 10 s). Both TR categories
  (Set 1) are reported; Set 3 is a sensitivity.
- **Wake** (D4, D23).
  - **Proactive (primary).** The schedule issues the wake request
    T_TR + T_SCell before the level boundary. It is a time-of-day rule and
    uses no traffic information.
  - **Reactive (sensitivity).** The request is issued at the boundary.
    Until the SCell is active, the level's traffic is carried by the
    3.5-GHz-only state. Those seconds are reported, and flagged as a
    violation where that state fails the criterion.
  - The 7.2 GHz cell serves nothing while asleep, waking or activating.
    While activating it draws P_empty.

**D25: energy mapping.**
- **Per cell, per level.**
  - The 3.5 GHz power is the 1.3 model at its DL occupancy L_3.5,DL. The
    1.3 model has no UL-load term.
  - The 7.2 GHz power is the FR3 model at (L_7.2,DL, L_7.2,UL). L_7.2,UL = 0
    for a DL-only carrier.
  - The loads are P3.2b's realized occupancies (D7) for the state the policy
    is in.
- **Overhead S0–S2** (D11), per hardware point:
  - S1 = 17 W·(N_PA·P_max / 1 W)^0.25 / 0.81 + 90 W / 0.855;
  - S2 = P_3.5(0) − P_empty,7.2.
  - P_aux is added in every in-frame state, and the sleep anchor becomes
    P_µ + P_aux.
- **Site sums.** Site power is the sum over the three sectors' cells (D1).
- **Uncertainty.** Confidence intervals are over the 100 realizations,
  which are the clusters. The three sectors of a realization are
  correlated.
- **Energy per bit.** Daily site energy divided by the bits delivered (DL +
  UL).

**D26: stage plan.**
- P3.4's held-out evaluation is merged into P3.3. Its held-out seeds are
  registered in P3.3's pre-registration, so the two need one measured run.
- Gates passed by P3.2a/P3.2b are not repeated. P3.2c inherits them through
  one bitwise reproduction gate (E2).

### Revision 5 (2026-11-10, after P3.2c, before P3.3)

These decisions were fixed before any P3.3 output. They answer the ChatGPT
review of P3.2c (§8).

**D27: three information classes.** Every controller belongs to exactly
one:

| Class | Knows at the decision for slot t |
|---|---|
| Offline oracle (benchmark only) | The whole realized traffic trace |
| Online | The realized traffic of slots < t, and models fitted on development data only |
| Rule | The realized traffic of slot t − 1 only, without forecasts |

P3.2c's "best feasible schedule" is relabelled a **g-aware offline
benchmark**: it was chosen knowing the day's growth level and each
schedule's feasibility.

**D28: the savings reference.**
- Savings are measured against always-on (all 7.2 GHz cells on, UL
  enabled).
- A saving is reported only where the controller and always-on both meet the
  violation budget (D32).
- The 3.5-GHz-only network is reported only as the cost of missing capacity,
  never as an equal-service baseline.

**D29: a 3.5 GHz UL-load sensitivity** (adds to D25; the primary model is
unchanged).
- **The issue.** The measurement-anchored 3.5 GHz model keeps its receive
  chains and UL PHY processing fully active in every UL slot, whatever the
  UL load.
- **The sensitivity ("UL-proportional receive power")** applies the model's
  own idle-reduction factors in UL slots without scheduled data:

  ΔP = −(1 − L_3.5,UL)·τ_UL·[M·P_TRX·(1 − δ_idle,TRX) / η_AFE + P_PHY·(B/B*)·K·(1 − δ_idle,PHY) / η_DBB]

  - The terms are Golard's parameters as used in P3.1: M = 64, τ_UL = 0.25,
    δ_idle,TRX = 0.5, δ_idle,PHY = 0.75.
  - The reduction is at most about 35.5 W per 3.5 GHz cell, at zero UL load.
  - It is applied to the "right" case only; the "left" case has no idle
    mode.
- **Label:** sensitivity, not a measured value.

**D30: activation patterns; all-on is not assumed to give the most
capacity.**
- **The four patterns.** In the typical-site frame, an activation is a
  pattern of the three sector indices. There are four canonical nested
  patterns, k = 0–3 sectors on:

  | k | Pattern |
  |---|---|
  | 0 | (0,0,0) |
  | 1 | (1,0,0) |
  | 2 | (1,1,0) |
  | 3 | (1,1,1) |

- **Rotations.** The other patterns are rotations of these. They are
  equivalent in distribution, by the three-sector symmetry. Their spread is
  reported as a diagnostic.
- **Every pattern is solved exactly**, so interference and association
  follow it (D8, D13).
- **Capacity.** x* is computed per pattern under both criteria. The
  pattern with the most capacity at each traffic level is reported.
- **Limit (D15).** A pattern applies to every site at once. P3.3 has no
  cell-specific control.

**D31: traffic over time: the Milan Telecom Italia traces.**
- **Source.**
  - "Telecommunications – SMS, Call, Internet – MI", Harvard Dataverse
    doi:10.7910/DVN/EGZHFV, version 1.3; Barlacchi et al., Sci. Data 2,
    150055 (2015); ODbL 1.0.
  - The variable is "Internet traffic activity", the number of CDRs per
    235 m square and 10-minute interval, summed over country codes.
  - It is a proxy for the traffic's time profile, not a byte count.
- **Files.**
  - 14 daily files, fetched unchanged by `scripts/p3_3_fetch_milan.py`.
  - Each is verified against Dataverse's size and MD5, hashed with SHA-256,
    and kept read-only in the git-ignored `external/milan_telecom/`.
  - Only the hashes are committed.
- **Development week:** Mon 4 – Sun 10 November 2013.
- **Test week:** Mon 18 – Sun 24 November 2013. Its files are hashed in
  P3.3 and read only in P3.4.
- **Squares** (selected from development data only):
  1. Candidates are squares with internet activity > 0 in every one of the
     1008 development slots.
  2. The 50 with the highest mean activity are kept: dense urban.
  3. They are split into 25 development and 25 test squares by a seeded
     permutation.
- **Traffic.** x_t = g·a_t / a_ref.
  - a_ref is a square's busiest hour of the development week: the maximum
    6-slot mean.
  - For test squares, a_ref also comes from the development week, so no
    test-week data enter it.
  - x is quantized to the nearest point of a 0.025 grid and clipped at 5.0
    (clipping is counted). Every controller and the ledger use the
    quantized value.
- **Growth** g ∈ {0.5, 1.0, …, 3.5}.
- **Mixes:** 10/90 and 26/74, constant over time (D18).
- **Runs.** Each square runs as one continuous week (1008 slots).
  Controllers carry their state across midnight. Every run starts with all
  cells on.
- **The local time convention** is checked on the development files and
  required of the test files.

**D32: controllers, timing and the violation budget.**
- **Timing.**
  - Decisions are taken at 10-minute slot boundaries (Δ = 600 s ≥ every
    T_TR, D7).
  - A cell that is woken is unavailable for T_wake = T_TR + T_SCell at the
    start of the slot (D4, D23). During that time the previous pattern
    serves the slot's traffic.
  - A cell put to sleep stops serving at the boundary.
- **Controllers:**

  | Controller | Class | Decision |
  |---|---|---|
  | `aon` | — | k = 3 always (the savings reference) |
  | `off` | — | k = 0 always (capacity cost only) |
  | `tod` | Rule (time of day) | k = 3 from 06:00 to 24:00, else 0 (P3.2c `sch_medbusy`) |
  | `thr_ul` | Rule | Capacity thresholds with hysteresis. The target is the smallest k with x_{t−1}·(1 + m) ≤ x*_all,both(k). Steps up at once; steps down only after H consecutive slots below the lower threshold |
  | `thr_dl` | Rule, UL-agnostic | As `thr_ul`, with x*_all,DL(k) only; tuned on DL-only compliance |
  | `exh` | Online | Minimum forecast slot energy among the patterns compliant at the forecast's upper bound (interference-aware exhaustive search) |
  | `mpc` | Online (proposed) | Dynamic programming over a horizon of H slots on the upper-bound forecast, with transition energies; applies the first action |
  | `oracle` | Offline | Dynamic programming over the realized week. Minimum energy subject to compliance in every slot where some pattern complies; otherwise the least-outage pattern |
  | `oracle_dl` | Offline, UL-agnostic | As `oracle`, with DL-only compliance |

- **Forecast** (fitted on development squares only): seasonal persistence.
  - x̂_{t+h} = x_{t−1}·S_d(τ_{t+h}) / S_d(τ_{t−1}), where S_d is the mean
    normalized profile of day type d (weekday or weekend) by slot of day.
  - The upper bound is x̂·exp(q_α,h), where q_α,h is the α-quantile of the
    development log-errors at horizon h.
- **Violation budget.**
  - A slot is **unavoidable** if no pattern is compliant at its traffic.
  - Avoidable violation time counts 600 s for each other slot whose chosen
    pattern fails the criterion, plus the wake-gap seconds where the
    previous pattern fails.
  - The budget is an avoidable violation share ≤ 1% of the time.
- **Tuning** (development only, per controller and mix). The parameter grid
  is:

  | Controller | Parameters |
  |---|---|
  | `thr_*` | m ∈ {0, 0.05, 0.1, 0.15, 0.2, 0.3, 0.4, 0.5} × H ∈ {1, 2, 3, 6} |
  | `exh` | α ∈ {0.5, 0.9, 0.95, 0.99, 0.999} |
  | `mpc` | α × H ∈ {1, 3, 6} |

  - **Selection rule:** the minimum mean development energy in the primary
    scenario, subject to the budget (the DL-only budget for the UL-agnostic
    controller). If no setting meets it, the one with the least violation is
    chosen and flagged.
  - The selected parameters are frozen and applied unchanged to every
    scenario. `exh`, `mpc` and the oracles use each scenario's own energy
    model.
- **Reported, together, per controller × mix × g × scenario:**
  - energy per site-day, with intervals clustered by square and, separately,
    over radio realizations;
  - the saving vs `aon` (D28);
  - avoidable and unavoidable violation shares under all three criteria;
  - transitions and gap seconds;
  - J per delivered bit;
  - the measured decision time (median and 99th percentile; machine stated).

**D33: P3.3 and P3.4 are separate measured runs.** This revises D26.
- **P3.3 (development):**
  - builds the radio table on the 100 P3.2b realizations;
  - fits the forecast;
  - tunes and freezes the controllers;
  - reports development results, which are exploratory and not scored.
- **P3.4 (held-out test):** pre-registered after P3.3 and before any test
  data are read. It contains:
  - the frozen parameters;
  - numeric predictions for H1, H2 and H4 and for Q3;
  - test squares × test week × 100 new radio realizations (r = 100–199,
    the same seed and streams);
  - the development T_0.5, frozen.

  The test results cannot influence any design choice.

## 3. Stages and gates

| Stage | Content | Gate (to be pre-registered) |
|---|---|---|
| P3.1 | Hardware energy models of both layers; state ladder | Source versions and hashes recorded; 1.3 Table II recomputed from 1.4 (both static-term forms reported); 1.2's totals (740 / 1658 / 300 / 1044 W, PA 262 W) reproduced, or, where an input is not stated in the source, labelled "not independently reproducible" under a rule fixed before the run (no input tuned to force agreement); TR tables transcribed and checked against the .docx; D10 feasibility; D4 state consistency (powers ≥ 0, ordered deep < light < micro, legal transitions, D ≥ T, energy in J); unit tests on small worked examples |
| P3.2 | Split into P3.2a (coupled radio engine), P3.2b (traffic and resources), P3.2c (time-dependent energy); see D12 | P3.2a: gates Q0–Q7. P3.2b: gates B0–B8 (closure, calibration, identical workloads, fixed-point uniqueness, independent reference). P3.2c: state-machine and energy-ledger gates, causality, no service while asleep or waking, P_aux S0–S2 |
| P3.3 | Controllers and baselines on development data (D27–D33) | Gates F0–F10 (provenance, radio reproduction, activation coupling, fixed point, all-user identity, traces, ledger, causality, DP = brute force, fairness, decision time); F11 (run 1's evidence) added by amendment 1 |
| P3.4 | Held-out test of the frozen controllers (D33) | Gates G0–G10; paired comparisons, confidence intervals; H1, H2, H4 and Q3 scored as pre-registered |

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

### P3.2c time-dependent energy and service ledger (PRE-REGISTERED 2026-11-10)

Config `configs/p3/p3_2c_energy_ledger.json`, code
`scripts/p3_2c_energy_ledger.py`, tests `tests/test_p3_2c_energy_ledger.py`.
It imports the P3.2b layer and the P3.1 energy models unchanged, and
implements D20–D25.

**What it computes.**
1. **The radio states.** It rebuilds the P3.2b population (ISD 200, case C,
   100 realizations) and solves every state at every traffic level of the
   day. The states are:
   - DL: 3.5 GHz only, and with 7.2 GHz carrier selection at each of the
     three DL offsets;
   - UL: 3.5 GHz only, and with 7.2 GHz UL enabled, for both mixes.
2. **Per level and state**, per cell: the loads, the admitted-user and
   all-user outage, and the offered, served, unserved and unservable
   traffic.
3. **Sustainable traffic x*** under both criteria, per state, by bisection
   as in P3.2b. The criteria are admitted-user (registered, P3.2b) and
   all-user (new).
4. **Per policy**, its sustainable growth g*:
   - g* = min(x*_both of the busy-level state, x*_both of the medium-level
     state / x_30);
   - computed under three criteria: all-user, admitted-user, and a DL-only
     all-user view (the UL-agnostic evaluation).
5. **The daily energy ledger** per site, per policy × mix × g × hardware ×
   S0/S1/S2 × TR category × wake mode:
   - 3.5 GHz and 7.2 GHz energy;
   - transitions and E_add;
   - activation intervals;
   - reactive-gap seconds;
   - delivered bits, and J per bit.

   Sensitivities (primary hardware, S0, Cat 1, proactive wake unless
   stated):
   - M_RF 64 / 128 / 1024;
   - the 3.5 GHz "left" case;
   - Set 3 ratios;
   - SMTC 160 ms (both categories and wake modes).

**Gates** (pass rule: E0–E8):

| Gate | Check |
|---|---|
| E0 | P3.1, P3.2a and P3.2b manifests passed, and their configs and outputs match their hashes. P3.2b's result of record is run 2, with its manifest hash in the config. Code constants equal the config (day, grid, mixes, ε, hardware map, S1 rule, T_SCell rule) |
| E1 | Power functions reproduce P3.1's tabulated values to ≤ 1e-12 relative: 3.5 GHz (both cases, six loads); 7.2 GHz (three hardware points, eight load pairs, P_µ, P_empty, both ladders' p, T and E_add for Cat 1/2 × Set 1/3). S2's 7.2 GHz no-load power equals P_3.5(0) to ≤ 1e-12 |
| E2 | Reproduction of P3.2b run 2 (ISD 200, C), bitwise: the calibration (T_0.3, T_0.5); every raw load array on P3.2b's grids (DL off/on at three offsets; UL off, UL enabled, DL-only); and the admitted-user x* of every state |
| E3 | On the new levels: every fixed point converged (residual ≤ 1e-9); closure ≤ 1e-12; occupancy identity ≤ 1e-9; 0 ≤ L ≤ 1; offered per-user workload bitwise identical across states; loads non-decreasing in traffic |
| E4 | All-user consistency: out_all = n + (1 − n)·out_adm to ≤ 1e-12 at every level and state; all-user x* ≤ admitted x*; out_all non-decreasing in traffic |
| E5 | Ledger, for every policy and scenario: the 7.2 GHz cell's intervals cover 86 400 s exactly once (≤ 1e-9 s); the energy equals an independent recomputation with P3.1's `sequence_energy` (≤ 1e-12 relative); every sleep ≥ its T; one E_add per sleep period; zero 7.2 GHz service in sleep, wake and activation intervals |
| E6 | Causality: every decision is a function of the time of day. With the traffic after each boundary replaced, every decision before it is unchanged. Reactive requests never precede the traffic change |
| E7 | Overhead: E(S0) < E(S1) < E(S2) for every policy with a 7.2 GHz cell. E(S1) − E(S0) equals an independent sum of the P_aux increments over in-frame, sleep and E_add terms (≤ 1e-9 relative) |
| E8 | In-run toy fixtures with hand-computed answers: a two-level toy day (joules); a sleep shorter than T is rejected; a reactive gap of exactly T_TR + T_SCell |

**Predictions.** All at ISD 200, case C, 50 dBm, M_RF 256, the 3.5 GHz right
case, Cat 1, proactive wake.
- **E-P1 (offload is near energy-neutral in the busy hour, S0).** At g = 1
  and 10/90:

  0.90 ≤ P̄_site,busy(`aon_ulsel`) / P̄_site,busy(`35only`) ≤ 1.10

  P̄ is the mean over realizations of the site power in the busy level.
  For `35only` it includes the 7.2 GHz deep-sleep power.
- **E-P2 (overhead decides it).** The same ratio under S2 is ≥ 1.25.
- **E-P3 (value of sleeping in the low period).** Measured as
  1 − E(`sch_medbusy_ulsel`) / E(`aon_ulsel`), at every g of the grid where
  both are all-user-feasible, for both mixes:
  - between 0.03 and 0.08 under S0;
  - ≥ 0.10 under S2.

**Expectations, not scored.**
- Transitions (E_add and activation intervals) are below 0.1% of the
  7.2 GHz layer's daily energy for every schedule, scenario, category and
  wake mode.
- DL all-user x* equals admitted x*, because no DL user is unadmitted at
  ISD 200.
- At 26/74 and g = 1, `35only` and every DL-only policy are
  all-user-infeasible. This follows from P3.2b's x*_UL(off) = 0.901; it is
  a consequence, not a test.

**Decision rule.** If all gates pass:
- P3.2c's energy mapping, state machine and ledger are P3.3's energy layer;
- the all-user criterion is P3.3's primary service constraint;
- P3.3's controllers are compared with the best P3.2c schedule (the
  minimum-energy all-user-feasible schedule at each g) as the non-adaptive
  reference;
- if E-P1 and E-P2 both hold, the busy-hour energy case for 7.2 GHz is
  reported as overhead-dependent (H4), and P3.3 reports S0–S2 for every
  result.

**Declared before registration.**
- Every P3.1 and P3.2b output (both runs) and TS 38.133 cl. 8.3.2 had been
  read.
- Hand estimates made while designing the predictions, from P3.1's tables
  and an assumed busy-hour 7.2 GHz DL load of about 0.15 (no P3.2c output
  was computed):

  | Quantity | Estimate |
  |---|---|
  | E-P1 ratio | ≈ 0.97 |
  | S2 ratio | ≈ 1.5 |
  | Saving from sleeping in the low period, S0 | ≈ 4.5% |
  | Saving from sleeping in the low period, S2 | ≈ 12% |
  | S1 at the primary point | 172.0 W |

- **Tests:** 14, on hand-checkable fixtures. The full suite passes.
- **One smoke run** (2 realizations, scratch folder, 26 s).
  - Every gate E0–E8 was computable and passed. In smoke mode E2 is code
    path only, because no 2-realization reference exists.
  - The smoke flag forced the overall verdict to fail.
  - It printed no result values, and its output files were not opened.
- **A key-existence check of E2's P3.2b inputs:** 26 raw arrays and 8 x*
  entries, with the expected shapes. It computed no new result.

#### P3.2c run 1 (commit d391b2f): all gates E0–E8 PASSED; result of record (2026-11-10, 996 s)

Outputs are in `results/phase3/p3_2c_energy_ledger/`: summary, validation,
manifest and `run1_stdout.txt`.
- The raw arrays (`levels_isd200.npz`, `energy_table.json.gz`) are
  git-ignored. Their hashes in the manifest match the files on disk.
- The manifest's config hash (2110b881…) is the committed config's.

| Gate | Result |
|---|---|
| E0 | 9 provenance checks and 10 constant checks pass |
| E1 | 216 P3.1 values reproduced with max relative difference 0.0. S2 parity exact. S1 = 172.03 W at the primary point |
| E2 | P3.2b run 2 reproduced bitwise: calibration, 26 raw load arrays (0 mismatches), 8 admitted x* |
| E3 | 23 batched solves converged (residual ≤ 1.7e-11). Closure ≤ 2.1e-16; occupancy identity ≤ 1.7e-11. 246 workload comparisons, 0 mismatches. Loads monotone; zero traffic gives L = 0 |
| E4 | out_all = n + (1 − n)·out_adm to 1.9e-13. Monotone. All-user x* ≤ admitted x* everywhere |
| E5 | 15 180 scenarios. The day is covered exactly (0 s error). Ledger = `sequence_energy` to 5.3e-16. No short sleeps; one E_add per sleep; zero 7.2 GHz service outside on intervals |
| E6 | `schedule()` takes no traffic input. 88 derivations, 0 violations |
| E7 | 3960 comparisons: S0 < S1 < S2 strictly. The S1 increment matches the independent sum to 9.7e-14 |
| E8 | 374 403 J and 374 451 J exact; short sleep rejected; reactive gap 0.155 s |

**Checked after the run, outside the gates.** 96 daily site energies were
recomputed from the raw per-cell loads and P3.1's functions, with the day
timing written by hand and none of the ledger code. They agree to 2.4e-16.
The E-P1/E-P2 ratios reproduce exactly.

**Predictions** (registered; ISD 200, C, 50 dBm, M_RF 256, right case, Cat 1,
proactive wake):

| Prediction | Result | Verdict |
|---|---|---|
| E-P1: busy-hour site power, 7.2 GHz on / 3.5 GHz only, g = 1, 10/90, S0, in [0.90, 1.10] | 3036.1 W / 3015.9 W = **1.007** | **Held** |
| E-P2: the same ratio under S2 ≥ 1.25 | 4657.9 W / 3045.4 W = **1.530** | **Held** |
| E-P3: low-period sleep saving vs always-on, every all-user-feasible g, both mixes: S0 in [3%, 8%]; S2 ≥ 10% | S0 3.6–4.6%; S2 10.3–12.0% | **Held** |

- **Why E-P1 is near neutral.** With 7.2 GHz on, the best-carrier rule
  moves almost all DL traffic off the 3.5 GHz layer: its DL load falls from
  0.500 to 0.0002. The 3.5 GHz PA saving offsets the 7.2 GHz cell, which
  runs at DL load 0.220 and UL load 0.105.
- **Decision rule triggered.** E-P1 and E-P2 both hold, so the busy-hour
  energy case for 7.2 GHz is **overhead-dependent (H4)**. P3.3 reports
  S0–S2 for every result.

**Unscored expectations, all met.**
- Transitions are at most 9.4e-5 of the 7.2 GHz layer's daily energy over
  every non-bound scenario, including Set 3.
- DL all-user x* equals admitted x*.
- At 26/74 and g = 1, `35only` and every DL-only policy are
  all-user-infeasible.

**Overhead and sleep constants** (50 dBm):

| Quantity | S0 | S1 | S2 |
|---|---|---|---|
| P_aux | 0 | 172.03 W | 540.62 W |
| Deep sleep, Cat 1 / Cat 2 (Set 1) | 2.91 / 29.09 W | 6.04 / 60.37 W | 12.74 / 127.38 W |
| E_add, Cat 1 / Cat 2 | 2.91 / 494.5 J | 6.04 / 1026.2 J | 12.74 / 2165.5 J |

**Sustainable traffic, admitted (P3.2b) vs all-user (D21):**

| State | Admitted | All-user |
|---|---|---|
| DL, 3.5 GHz only | 1.4453 | 1.4453 |
| DL, 7.2 GHz selection, −3 dB | 3.7188 | 3.7188 |
| UL y*, 3.5 GHz only | 0.3164 | 0.3008 |
| UL y*, 7.2 GHz UL enabled | 0.8203 | 0.7734 |

**Sustainable growth g* per policy** (50 dBm):

| Policy | 10/90 all-user | 10/90 admitted | 26/74 all-user | 26/74 admitted | DL-only view |
|---|---|---|---|---|---|
| `35only` | 1.445 | 1.445 | 0.856 | 0.901 | 1.445 |
| `aon_ulsel`, `sch_medbusy_ulsel` | 3.719 | 3.719 | 2.201 | 2.335 | 3.719 |
| `aon_dlonly`, `sch_medbusy_dlonly` | 2.707 | 2.848 | 0.856 | 0.901 | 3.719 |
| `sch_busy_ulsel` | 2.275 | 2.275 | 1.348 | 1.418 | 2.275 |
| `sch_busy_dlonly` | 2.275 | 2.275 | 0.856 | 0.901 | 2.275 |

The all-user criterion lowers the AI-era (26/74) limits by 5–6%. It also
lowers the 10/90 DL-only-carrier limit from 2.848 to 2.707.

**Daily site energy** (kWh, S0, 50 dBm). ✓ marks all-user-feasible, ✗
infeasible; the best feasible policy is in bold.

| Mix, g | `35only` | `aon_ulsel` | `sch_busy_ulsel` | `sch_medbusy_ulsel` | Best feasible |
|---|---|---|---|---|---|
| 10/90, 0.5 | 57.19 ✓ | 66.38 ✓ | 59.55 ✓ | 63.34 ✓ | **`35only`** |
| 10/90, 1.0 | 63.67 ✓ | 69.15 ✓ | 63.84 ✓ | 66.11 ✓ | **`35only`** (0.2% below `sch_busy_dlonly`) |
| 10/90, 1.5 | 70.96 ✗ | 71.93 ✓ | 68.41 ✓ | 68.90 ✓ | **`sch_busy_dlonly`** 68.37 |
| 10/90, 2.0 | 76.87 ✗ | 74.74 ✓ | 73.26 ✓ | 71.70 ✓ | **`sch_medbusy_dlonly`** 71.60 |
| 10/90, 3.0 | 82.48 ✗ | 80.41 ✓ | 81.99 ✗ | 77.37 ✓ | **`sch_medbusy_ulsel`** |
| 10/90, 4.0 | ✗ | ✗ | ✗ | ✗ | none (g > 3.719) |
| 26/74, 1.0 | 63.67 ✗ | 69.25 ✓ | 63.90 ✓ | 66.22 ✓ | **`sch_busy_ulsel`** |
| 26/74, 2.0 | 76.87 ✗ | 74.96 ✓ | 73.38 ✗ | 71.92 ✓ | **`sch_medbusy_ulsel`** |
| 26/74, ≥ 2.25 | ✗ | ✗ | ✗ | ✗ | none (g > 2.201) |

**Saving of the best feasible schedule vs always-on** (`aon_ulsel`):

| Mix | S0 | S1 | S2 |
|---|---|---|---|
| 10/90 | 13.8% (g 0.5), 7.9% (g 1), 3.6–5.0% (g 1.5–3.5) | 27.1%, 21.6%, 6.4–13.8% | 45.0%, 40.4%, 10.3–26.2% |
| 26/74 | 13.9% (g 0.5), 7.7% (g 1), 4.1–4.2% (g 1.5–2) | 27.2%, 16.5%, 7.0–7.2% | 45.0%, 28.5%, 11.1–11.3% |

**Findings of record.**
- **Feasibility decides when 7.2 GHz is needed.**
  - It must be on in the busy level beyond g = 1.445 (10/90) or g = 0.856
    (26/74, all-user).
  - It must also be on in the medium level beyond g = 2.275 (10/90) or
    g = 1.348 (26/74).
  - Nothing is feasible beyond g = 3.719 (10/90) or g = 2.201 (26/74).
  - At 26/74 the 3.5 GHz layer alone already fails today's busy hour.
- **Energy decides between the feasible schedules, and the answer depends
  on the overhead.**
  - Under S0, a loaded 7.2 GHz layer is near energy-neutral (E-P1). At high
    load it lowers site energy: at 10/90, g = 2, always-on uses 74.74 kWh
    against 76.87 kWh for 3.5 GHz only, which is infeasible there anyway.
  - So under S0, waking 7.2 GHz for the medium level pays for itself at
    10/90 from g ≈ 1.75: `sch_medbusy` beats `sch_busy`.
  - Under S1 and S2, every on-hour costs energy. The best schedule keeps
    7.2 GHz asleep in every level that feasibility allows. For example, at
    10/90 under S1/S2 it is `35only` up to g = 1.25.
- **The schedule choice at intermediate growth is hardware-dependent**
  (post-hoc analysis of the registered sensitivity rows, not a registered
  test). Under S0, in 0–7 of the 20 feasible (mix, g) points, the best
  policy differs from the primary's. This happens at 43 or 53 dBm, at
  M_RF 64/128/1024, with Cat 2, and in the 3.5 GHz left case (one point).
  All such changes fall in g = 0.75–2.25. The feasible sets never change.
  - This follows from E-P1: near energy-neutrality, small hardware changes
    flip the choice.
  - At low g (`35only`) and at high g (`sch_medbusy_ulsel`) the choice is
    robust.
- **UL enabled vs DL-only carrier is an energy tie.**
  - Where both are feasible, the difference is ≤ 0.2%. The DL-only carrier
    is slightly cheaper, because the 3.5 GHz model has no UL-load term:
    moving UL traffic to 7.2 GHz adds 7.2 GHz power and saves none.
  - This is a model asymmetry and is stated. The choice between them is set
    by feasibility.
- **UL-agnostic evaluation (precursor of H2; derived from the registered
  outputs, scored in P3.3).** The minimum-energy schedule under the DL-only
  view, against the UL-aware (all-user) optimum:
  - At 26/74 it claims feasibility up to g = 3.719, where no UL-feasible
    schedule exists beyond 2.201 (1.69×).
  - Where both exist, it claims an extra saving of 0.2–1.0% (S0), 4.3–7.4%
    (S1) and 14.5–16.9% (S2). For example, at S2, g = 1 it picks `35only` at
    64.38 kWh against `sch_busy_ulsel` at 77.34 kWh. The saving is bought
    with a UL violation.
  - At 10/90 the DL-only view errs only beyond g = 2.707, by ≤ 0.22%.
- **Transitions are negligible in energy, not in service.**
  - Reactive and proactive wake, and SMTC 20 vs 160 ms, change daily energy
    by < 1e-5 relative.
  - A reactive wake leaves a 0.155 s gap (Cat 1) or 10.105 s gap (Cat 2) per
    day, served by the 3.5 GHz layer alone. It is a QoS violation wherever
    that state is infeasible, e.g. `sch_busy_ulsel` at 10/90, g = 2.
- **Energy per delivered bit** falls with load. At 10/90, S0: 7.79 J/Mbit
  (`35only`, g = 1); 4.39 J/Mbit (`sch_medbusy_ulsel`, g = 2); 3.16 J/Mbit
  (g = 3).
- **Sensitivities of absolute energy** (S0, max change over 330 rows):

  | Sensitivity | Max change |
  |---|---|
  | 3.5 GHz "left" case | 21.7% (P_3.5(0) = 891.8 vs 712.2 W) |
  | M_RF 1024 | 35.7% |
  | M_RF 64 / 128 | 9.2% / 6.2% |
  | Cat 2 deep sleep | 3.3% |
  | Cat 2 + Set 3 | 6.3% |
  | Set 3 | 0.16% |
  | Reactive wake, SMTC 160 ms | < 1e-5 |

**Limitations**, stated wherever these results are used:
- The best-carrier rule leaves the 3.5 GHz DL nearly idle when 7.2 GHz is
  on. A load-balancing association could change both energy and capacity.
- The 3.5 GHz model has no UL-load term.
- Homogeneous sectors (D15).
- Time-of-day schedules only.
- The ETSI step profile, with its order assumed.
- The FR1 → 7.2 GHz extrapolations: the TR ladder and TS 38.133.
- Flow-level traffic.
- Confidence intervals are over realizations. Near-ties (< 0.2%) are not
  resolved by paired tests here; P3.3 uses paired comparisons.

**Decision** (registered rule; all gates passed):
- P3.2c's energy mapping, state machine and ledger are P3.3's energy layer;
- the all-user criterion is P3.3's primary service constraint;
- P3.3's controllers are compared with the minimum-energy all-user-feasible
  P3.2c schedule at each g;
- S0–S2 are reported for every P3.3 result.

### P3.3 band-activation controllers on development data (PRE-REGISTERED 2026-11-10)

Config `configs/p3/p3_3_controllers.json`. Code:
- `scripts/p3_3_controllers.py`;
- `scripts/p3_3_traces.py`;
- `scripts/p3_3_fetch_milan.py` (data fetch).

Tests `tests/test_p3_3_controllers.py`. It imports the P3.2c energy layer, and
through it P3.2b and P3.1, unchanged. It implements D27–D33.

**What it computes.**
1. **Radio table.** The four nested patterns (D30) are solved on the
   P3.2b population over x = 0–5.0 in steps of 0.025:
   - DL at the three 7.2 GHz DL offsets;
   - UL for both mixes;
   - admitted and all-user x* per pattern;
   - the pattern with the most capacity, and a rotation diagnostic.
2. **Energy tables** for 20 scenarios. Each gives the site power per
   realization, pattern and traffic level:
   - primary 50 dBm × S0–S2;
   - 53 and 43 dBm × S0–S2;
   - Cat 2 × S0–S2;
   - the D29 UL-load sensitivity × S0–S2;
   - the 3.5 GHz "left" case, Set 3, and M_RF 64 / 128 / 1024 (S0).
3. **Traces** (development week only, D31): the 25 development squares ×
   7 growth levels give 175 week-long runs per mix. The seasonal-persistence
   forecast and its error quantiles are fitted on them.
4. **The nine controllers of D32.**
   - The four tunable ones are tuned by the registered rule and frozen.
   - All are evaluated in every scenario and mix.
   - The decision time is measured.

**Gates** (pass rule F0–F10):

| Gate | Check |
|---|---|
| F0 | P3.1, P3.2b and P3.2c manifests and outputs match their hashes; P3.2c config hash. All 14 trace files present with their SHA-256. Code constants equal the config |
| F1 | P3.2c's level batches recomputed bitwise (6 arrays); calibration equal; the 12 x* of patterns 0 and 3 equal P3.2c's. Table vs P3.2c at shared x: mean load within 1e-8, all-user outage within 1e-5 (a boundary user can flip between solver batches) |
| F2 | Off-sector 7.2 GHz loads exactly 0; no off-sector user on 7.2 GHz; on-sector 7.2 GHz DL load > 0 at every x > 0 |
| F3 | Fixed point, closure, occupancy identity, load range, identical workloads, monotone loads, zero traffic (as E3) |
| F4 | out_all = n + (1 − n)·out_adm; monotone; all-user x* ≤ admitted x* (as E4) |
| F5 | Every development file aligned to the local day; selection reproducible; selected squares complete; quantization error ≤ step/2; 25 + 25 disjoint squares |
| F6 | 100 random runs recomputed by an independent scalar loop: energy and bits ≤ 1e-9 relative; violation seconds ≤ 1e-6 s; transitions equal |
| F7 | Causality: for every non-oracle controller, decisions up to slot t are unchanged when the traffic of slots ≥ t is replaced (20 runs × 5 cuts) |
| F8 | Oracle DP = brute force over 4⁶ sequences on 200 random instances and 50 real windows (≤ 1e-12) |
| F9 | One trace array for every controller (hash recorded); only the oracles receive the full trace |
| F10 | Reset and per-decision times recorded for every controller, with the machine |

**No predictions are scored in P3.3** (D33). The development results are
exploratory. P3.4's numeric predictions are registered after P3.3 and
before any test data are read.

**Decision rule.** If all gates pass, the following are frozen into P3.4's
pre-registration:
- the tuned parameters;
- the forecast model and its quantiles;
- the development x* thresholds;
- the test squares' a_ref;
- the development T_0.5.

**Declared before registration.**
- All P3.1, P3.2b and P3.2c outputs had been read. The Milan data paper was
  read for the variable definitions.
- One development file (2013-11-04) was opened to confirm the time
  convention:
  - CET local days: 2013-11-03T23:00Z to 2013-11-04T22:50Z;
  - 144 aligned slots, 5 639 488 rows, 10 000 squares.
- No other trace content was inspected. No square selection, normalization
  or controller output was computed.
- The test-week files were downloaded and verified (size, MD5, SHA-256)
  only.
- The guestbook email used for the download is the user's, given with the
  user's consent. It is passed to the fetch script as an environment
  variable and is not stored in the repository.
- **Tests:** 12, on hand-checkable fixtures. The full suite (227) passes.
- **A code-path smoke on 14 synthetic Milan-format files** (60 squares,
  scratch folder, 416 s). Every gate F0–F10 was computable and passed; the
  smoke flag forced the verdict to fail. It led to two changes before
  registration:
  - the `mpc` and `oracle` stage costs were vectorized and F7 was batched.
    This affects run time only: the results are identical, and F8 checks the
    DP;
  - the F1 table-vs-P3.2c tolerance was split, because a boundary user can
    flip between solver batches.
- **One smoke run on the real files** (2 realizations, scratch folder,
  523 s).
  - Every gate F0–F10 was computable and passed; F1 is code path only.
  - The verdict was forced to fail.
  - It printed only gate lines, and its outputs were not opened.

#### P3.3 run 1 (commit b6cd066): gates F0–F10 PASSED; three design errors found in verification (2026-11-10, 10 281 s)

Evidence is kept in `results/phase3/p3_3_controllers/run1/` (manifest
SHA-256 33a6c7ea…). Raw arrays are git-ignored, with their hashes in the
manifest.

**Gates.**
- F0: all 14 trace hashes verified.
- F1: 6 P3.2c arrays bitwise; 12 x* exact; table vs P3.2c load 7.9e-13,
  outage 0.
- F2: no off-sector 7.2 GHz load or user.
- F3: 3618 workload comparisons, residual ≤ 2.0e-11.
- F4: identity to 1.8e-13.
- F5: 9998 complete candidate squares; 2 and 3 clipped slots at g = 3 and
  3.5.
- F6: 100 runs, energy to 1.1e-14.
- F7: 700 checks, 0 violations.
- F8: 250 instances, 3.8e-16.
- F9 and F10: recorded. Median decision time 0.05 ms (`thr`), 0.25 ms
  (`exh`), 0.33 ms (`mpc`); 45 ms per week for `oracle`.

**Found while verifying the outputs.** Each of the following is a design
error that no gate could catch.
1. **D30's symmetry assumption is false.** The registered rotation
   diagnostic shows the three sectors are not equivalent. The users are
   points of a real building map, and the UL-weak users cluster in sector
   index 2.

   | Pattern | UL all-user outage, 26/74, x = 1 | DL all-user outage, x = 2 |
   |---|---|---|
   | (1,0,0) | 37.7% | 53.9% |
   | (0,1,0) | 36.7% | 46.3% |
   | (0,0,1) | **4.0%** | **21.5%** |
   | (1,1,0) | 30.5% | — |
   | (0,1,1) | 3.0% | — |
   | (1,0,1) | 3.4% | — |

   The canonical chain (1,0,0) → (1,1,0) leaves exactly that sector off. The
   controllers were denied the most useful partial patterns.
2. **The fallback "least worst-direction outage" pattern is pathological.**
   It applied where no pattern complies, in the oracle, and at the MPC's
   upper bound.
   - Near saturation both directions are about 99% in outage, and the rule
     picks k = 1 by 0.1 point: 0.9888 against 0.9905 for all-on at 26/74,
     x = 3.5. That leaves the DL unserved where all-on would comply.
   - As a result, raising the MPC's quantile *increased* its violations:
     α = 0.9 gave 1.4%, α = 0.999 gave 13.9%.
   - `oracle` shows 9.1% DL-only violations at 26/74, g = 3.5.
3. **Tuning in S0 alone selects the grid edge.**
   - At 10/90, S0 energy falls as the threshold margin grows: waking
     7.2 GHz early saves energy at high load (P3.2c E-P1). The rule
     therefore picked m = 0.5, the largest value.
   - That margin is then far too cautious under S1/S2.
   - `exh` and `mpc` missed the budget at 26/74 (1.39% and 1.28%) because
     of error 2.

**Not affected:**
- the radio table of the four canonical patterns;
- the traces;
- the gates;
- the conclusion that all-on is the maximum-capacity *canonical* pattern.
  The canonical patterns' x*_both,all is 1.445 / 1.574 / 1.766 / 3.719 at
  10/90, and 0.856 / 0.878 / 0.901 / 2.201 at 26/74 (−3 dB).

Run 1's development results are superseded by run 2 and are not reported
as findings.

#### P3.3 Amendment 1 (registered before run 2)

Recorded in the config (`amendment_1`). Every change follows from a
run-1 finding above, on development data only. P3.4's test data are
untouched.

1. **All eight sector patterns** (revises D30).
   - Every pattern is solved exactly.
   - Patterns are indexed (0,0,0), (1,0,0), (0,1,0), (0,0,1), (1,1,0),
     (1,0,1), (0,1,1), (1,1,1).
   - **Transitions are make-before-break.**
     - When any cell wakes, the previous pattern serves for
       T_wake = T_TR + T_SCell. Cells leaving it go to sleep after the
       gap.
     - When no cell wakes, the cells leaving sleep at the boundary.
     - Energy: Pw[new]·(600 − gap) + [n_wake > 0]·(Pw[old]·T_wake +
       n_wake·(P_act − P_deep)·T_SCell) + n_sleep·E_add.
     - For nested transitions this is run 1's formula.
2. **Fallback = the maximum-capacity pattern.** It is used where no pattern
   complies (`oracle`, and `mpc` at its upper bound): the largest
   x*_all,both, or x*_all,DL for the DL-only criterion. It replaces the
   least-outage pattern.
3. **Tuning per overhead scenario.**
   - Each tunable controller is tuned per mix and per S0/S1/S2 at the
     primary hardware. The parameters of the matching S are used in every
     scenario.
   - The `thr` margin grid gains 0.75 and 1.0.
4. **Threshold rule for non-nested patterns.**
   - The target is the pattern with the fewest cells on whose threshold
     covers x_{t−1}(1 + m); ties go to the higher x*. If none covers it, the
     maximum-capacity pattern.
   - If the current pattern does not cover x_{t−1}(1 + m), the rule switches
     at once.
   - Otherwise it moves to a pattern with fewer cells only after H
     consecutive slots in which one covers it. It then takes the
     highest-capacity target of that streak.
   - For nested patterns this is run 1's rule.
5. **F8 brute force over 8⁴ sequences** (T = 4): 200 random instances and
   50 real windows. 8⁶ is too slow.
6. **Reuse of run 1's verified table.**
   - The four canonical patterns' tables (loads, outages, served fractions)
     and their x* are loaded from run 1. The raw file's SHA-256 is checked
     against run 1's manifest.
   - Only the four rotated patterns are solved anew; F3/F4 apply to them.
   - **New gate F11:**
     - run 1's evidence is intact (manifest hash, every output hash);
     - recomputing the all-on DL state (−3 dB) and the all-on UL state
       (26/74) reproduces run 1's arrays bitwise;
     - every reused array has the expected shape.
- **Pass rule:** F0–F11.
- **Result of record:** run 2. Run 1 stays as evidence.
- **Not blind.** Run 1's development results were seen before this
  amendment.
- **Checks before run 2.**
  - **Tests:** 13, including make-before-break swaps, transition counts and
    the maximum-capacity fallback. The full suite (228) passes.
  - **Reuse path:** a key and shape check found all 46 run-1 arrays and 32 x*
    entries; run 1's manifest, git head, config and table hashes match.
  - **One smoke run on the real files** (2 realizations, scratch folder,
    942 s).
    - Every gate F0–F11 was computable and passed. F1 and F11 are code path
      only, because reuse needs 100 realizations.
    - The verdict was forced to fail.
    - It printed only gate lines, and its outputs were not opened.

#### P3.3 run 2 (commit f3093a3): all gates F0–F11 PASSED; result of record (2026-11-11, 10 007 s)

Outputs are in `results/phase3/p3_3_controllers/`. The raw files
(`table_isd200.npz`, `runs.json.gz`) are git-ignored; their hashes in the
manifest match. The manifest's config hash (0937434c…) is the committed
config's. Run 1 stays in `run1/`.

| Gate | Result |
|---|---|
| F0 | 9 provenance checks and 11 constant checks pass, amendment included; all 14 trace hashes verified |
| F1 | 6 P3.2c arrays bitwise; 12 x* exact; table vs P3.2c: load 7.9e-13, outage 0; calibration equal |
| F2 | Across all 8 patterns: no off-sector 7.2 GHz load, no off-sector user on 7.2 GHz, on-sector load > 0 at every x > 0 |
| F3 | 44 batched solves converged (residual ≤ 2.0e-11); closure ≤ 2.2e-16; occupancy ≤ 2.0e-11; 4422 workload comparisons, 0 mismatches |
| F4 | Identity to 1.8e-13 on the 20 solved states; outage monotone in every pattern; all-user x* ≤ admitted x* |
| F5 | 9998 complete candidate squares; 2 and 3 clipped slots at g = 3 and 3.5 |
| F6 | 100 runs: energy to 1.1e-14, violation seconds and bits exact, transitions equal |
| F7 | 700 checks, 0 violations |
| F8 | 250 instances over 8⁴ sequences, 2.5e-16 |
| F9 | One trace array (hash recorded); only the oracles see the full trace |
| F10 | Median per decision: `thr` 0.06 ms, `exh` 0.21 ms, `mpc` 0.31 ms (99th percentile 0.84 ms); `oracle` 39 ms per site-week. WSL2, x86-64 laptop |
| F11 | Run 1's evidence intact; 10 spot-recomputed arrays bitwise equal to run 1's |

**Capacity of the eight patterns** (all-user, 50 dBm; x* in units of the
busy hour):

| Pattern | DL x* | UL y* | x*_both, 10/90 | x*_both, 26/74 |
|---|---|---|---|---|
| (0,0,0) | 1.445 | 0.3008 | 1.445 | 0.856 |
| (1,0,0) | 1.574 | 0.3086 | 1.574 | 0.878 |
| (0,1,0) | 1.590 | 0.3125 | 1.590 | 0.889 |
| (0,0,1) | 1.879 | 0.3711 | 1.879 | 1.056 |
| (1,1,0) | 1.766 | 0.3164 | 1.766 | 0.901 |
| (1,0,1) | 2.188 | 0.3867 | 2.188 | 1.101 |
| (0,1,1) | 2.203 | 0.4375 | 2.203 | 1.245 |
| (1,1,1) | 3.719 | 0.7734 | 3.719 | 2.201 |

- **All-on is the maximum-capacity pattern** everywhere, so it is the
  fallback in every case.
- **Partial activation adds little capacity** (at most 1.5× the 3.5 GHz
  layer alone).
- **Sector index 2 is decisive:** (0,0,1) and (0,1,1) are the strongest
  one- and two-sector patterns.
- **Usage follows from this.** The controllers use (0,0,1) and (0,1,1) for
  up to 38% of the time and almost never use (1,0,0) or (1,1,0). At 10/90,
  S0, g = 1.5, `oracle` uses (0,0,1) 23.5% and (0,1,1) 14.8% of the time.

**Tuned and frozen parameters** (all within the 1% budget on development
data):

| Controller | 10/90, S0 | 10/90, S1/S2 | 26/74, S0–S2 |
|---|---|---|---|
| `thr_ul` | m 0.75, H 1 | m 0.2, H 1 | m 0.2, H 1 |
| `thr_dl` | m 0.75, H 1 | m 0.2, H 1 | m 0.75 (S0), 0.2 (S1/S2) |
| `exh` | α 0.5 | α 0.9 | α 0.9 |
| `mpc` | α 0.5, H 3 | α 0.9, H 3 | α 0.9, H 3 |

**Development results.** These are exploratory: they are not scored, and
the held-out test is P3.4.

Saving vs always-on, `mpc` / `oracle`, at 50 dBm. Savings are qualified,
i.e. both controllers meet the 1% budget:

| Mix, g | S0 | S1 | S2 |
|---|---|---|---|
| 10/90, 1 | 10.9 / 11.0% | 24.3 / 24.4% | 42.7 / 42.7% |
| 10/90, 2 | 5.7 / 5.8% | 15.9 / 16.4% | 31.2 / 33.5% |
| 26/74, 1 | 10.8 / 11.1% | 22.7 / 24.0% | 38.9 / 41.5% |
| 26/74, 2 | 5.5 / 5.8% | 11.9 / 13.0% | 20.7 / 23.0% |

- **The online controllers come close to the oracle.**
  - The gap is ≤ 0.3 point in S0 and ≤ 2.6 points in S2 (Q3).
  - `thr_ul` and `exh` are within 0.5 point of `mpc`.
  - `mpc`'s avoidable all-user violations are 0.46–1.00% at 26/74, 0–0.95%
    at 10/90.
- **Energy per bit.** At 10/90, g = 1: 9.73 J/Mbit (`mpc`) against
  10.92 (`aon`). At 26/74: 8.05 against 9.02.
- **Transitions.** 10–27 transitions per site-day and ≤ 2 gap seconds per
  site-day.
- **The UL-agnostic rule `thr_dl` at 26/74 (H2).**
  - Its DL-only violations stay ≤ 1.1%, but its all-user violations are:

    | Overhead | g = 1 | g = 1.5 | g = 2 | g = 2.5 |
    |---|---|---|---|---|
    | S0 | 2.6% | 9.6% | 7.5% | 5.7% |
    | S1/S2 | 7.1% | 37.0% | 45.1% | 36.0% |

  - It claims extra saving over `thr_ul` of 0.2–0.4% (S0) and 5.8–15.0% (S2)
    by violating the UL.
  - `oracle_dl` (UL-agnostic, perfect foresight) violates 14–49% at
    g = 1.5–2.
- **Hardware sensitivity.**
  - At 10/90, g = 1, S0, the `mpc` saving scales with the 7.2 GHz no-load
    power:

    | Hardware | Saving |
    |---|---|
    | 43 dBm | 7.8% |
    | 50 dBm | 10.9% |
    | 53 dBm | 19.1% |
    | M_RF 64 | 3.6% |
    | M_RF 1024 | 33.6% |

  - The D29 UL-load sensitivity, Set 3 and the "left" case change it by
    ≤ 0.9 point. Cat 2 lowers it to 8.3%.
- **The service boundary (H4).** At 26/74 beyond g ≈ 2.2, no pattern meets
  the all-user criterion for 5.5% (g = 2.5), 19.7% (g = 3) and 31.8%
  (g = 3.5) of the time. Savings there exist only on the avoidable slots.

**Decision** (registered rule; all gates passed). The following are frozen
and go into P3.4's pre-registration:
- the parameters above;
- the forecast model and its quantiles;
- the development x* thresholds;
- the maximum-capacity fallbacks;
- the test squares' a_ref;
- the development T_0.5.

**Corrections (2026-11-11, found while verifying P3.4; the text above is
left as written).** Three statements in this section are corrected in
"P3.4 run 1, Corrections to the P3.3 record":
- the 1% budget holds for the tuning average over g, not in every cell;
- `mpc`'s 26/74 violation at g = 1.5 is 1.0042%, not ≤ 1%;
- the hardware sensitivity omitted the violations at g ≥ 1.5 under
  M_RF 1024 and 53 dBm.

No verdict changes.

### P3.4 held-out test of the frozen controllers (PRE-REGISTERED 2026-11-11)

Config `configs/p3/p3_4_heldout.json`, code `scripts/p3_4_heldout.py`,
tests `tests/test_p3_4_heldout.py`. It imports P3.3's controllers, model
and accounting unchanged. **Nothing is tuned.**

**Held-out data, read for the first time by the measured run.**

| Holdout | What is held out | Disclosure |
|---|---|---|
| Temporal | The traffic of 18–24 November 2013, the test week | — |
| Radio | Realizations 100–199 (same seed and streams) | The controllers decide on the frozen development model; every outcome is scored on the test radio table |
| Spatial | The 25 test squares, excluded from tuning, the forecast fit and the thresholds | Their selection used their development-week mean activity, and their a_ref is their own development-week busiest hour (historical site information). A strict spatial test with normalization from development squares only is not part of P3.4 |

**Scope.** The primary 50 dBm radio and its 14 energy scenarios. The 53 and
43 dBm radio sensitivities were development-only.

**Controllers.**
- P3.3's nine controllers.
- `oracle_budget`: perfect foresight with the *same* 1% avoidable-violation
  allowance per run as the online controllers (review point D).
  - It is a Lagrangian DP: energy + λ × violation seconds, with bisection
    on log₁₀ λ ∈ [−3, 9] (50 steps).
  - The best plan within the budget is an upper bound. The maximum over the
    evaluated λ of (DP value − λ × budget) is a lower bound, by weak
    duality.
- `oracle_test`: perfect foresight *and* the test radio table, a
  full-information reference.
- `oracle_budget` and `oracle_test` run in the primary S0–S2 scenarios.

**Statistics.** All comparisons are paired: the same squares, week and
realizations.
- **Energy ratios and gaps:** a pigeonhole bootstrap that resamples test
  squares and realizations independently (crossed design; 2000 replicates,
  seed 20261112).
- **Violation differences:** a bootstrap over test squares, because the
  compliance tables are pooled over realizations.
- **Slots and realizations are never treated as independent weeks.**

**Predictions** (scored as registered and reported whether they hold or
fail):
- **H1 (energy with service).** Each of 8 cells must pass:
  - mpc at 50 dBm, both mixes, g ∈ {1, 2}, S0 and S2;
  - saving vs `aon` ≥ 3% (S0) or ≥ 10% (S2);
  - its 95% pigeonhole interval above 0;
  - mpc's avoidable all-user violation share ≤ 1%.
- **H2 (the UL is necessary).** Each of 9 cells must pass:
  - 26/74, g ∈ {1, 1.5, 2}, S0–S2;
  - avoidable all-user violation share of `thr_dl` minus `thr_ul` ≥ 1
    percentage point;
  - its 95% square-bootstrap interval above 0.
- **H4 (feasibility boundary).** All three must hold. The unavoidable
  all-user share is:
  - at 26/74, ≤ 1% at g = 2.0 and ≥ 1% at g = 2.5 (the boundary lies in
    (2.0, 2.5]);
  - at 10/90, ≤ 1% at g = 3.0.
- **Q3 (oracle gap).** In each H1 cell, (E_mpc − E_oracle)/E_aon ≤ 1 point
  (S0) or ≤ 5 points (S2).
  - Here `oracle` is the zero-violation perfect-foresight benchmark on the
    same frozen model.
  - The gaps to `oracle_budget` and `oracle_test` are reported alongside,
    not scored.
- **Not claimed:** that mpc beats `exh` or `thr_ul` (review point A).
  Their paired differences are reported.

The predictions are development-informed. The development values (mpc,
g = 1 / g = 2):

| Mix | S0 saving | S2 saving |
|---|---|---|
| 10/90 | 10.9 / 5.7% | 42.7 / 31.2% |
| 26/74 | 10.8 / 5.5% | 38.9 / 20.7% |

In development, the H2 differences were +2.1 to +44 points, the H4
unavoidable shares were 0.2% (g = 2) and 5.5% (g = 2.5) at 26/74 and 0.1%
at 10/90 (g = 3), and the gaps were ≤ 0.3 / 2.6 points.

**Gates** (pass rule G0–G10):

| Gate | Check |
|---|---|
| G0 | P3.3 run 2 manifest, git head, config and output hashes; frozen parameters and test squares equal the config; 14 trace hashes; constants |
| G1 | The offset builder with offset 0 equals p3_2c's builder bitwise; the test population draws exactly realizations 100–199, 6 random-stream calls each |
| G2 | Test-week files aligned to the local day; 25 test squares disjoint from development; slots without activity and clipped slots reported |
| G3 | With the frozen model, the development traces' hash equals P3.3 F9's, and every controller reproduces P3.3's development energy and violation exactly (≤ 1e-12) for 26/74 S0 and 10/90 S2 at all 7 g |
| G4–G6 | The test table: fixed point and closure, all-user identity, activation coupling (as P3.3 F3, F4, F2) |
| G7 | 100 test runs recomputed by the independent scalar loop |
| G8 | Causality of the frozen controllers on the held-out traces (100 checks each) |
| G9 | Budget oracle: lower bound ≤ upper bound in every run; runs where the budget cannot be met are counted |
| G10 | Offline test-table time and online decision times, with the machine |

**Reported together** (review: energy, feasibility, decision time and
robustness side by side):
- energy per site-day and the saving with its interval, qualified only where
  both controllers meet the budget;
- avoidable, unavoidable and total failure shares under all three criteria;
- transitions, gap seconds, J per delivered Mbit, cells on and pattern
  shares;
- the paired gaps and the violation differences;
- the offline table time against the online decision time.

**Limitations, stated with every P3.4 result:**
- Milan's 2013 CDR activity tests robustness to real temporal demand
  patterns; it is not 2026 6G traffic and not byte volume.
- Seven held-out days cannot establish seasonal generalization.
- The decision space is eight network-wide sector-index patterns (D15),
  not independent control of 57 sectors. The sub-millisecond decision time
  must not be extrapolated to that problem.
- The online time excludes the offline construction of the feasibility
  table (hours on a laptop), which is reported separately.
- All results are conditional on the radio, hardware, traffic and service
  models of P3.1–P3.3.

**Decision rule.**
- **If all gates pass**, P3.4 is Phase 3's held-out result of record. Next
  is the publication-readiness audit.
- **If a gate fails:** stop, keep the evidence and diagnose. Any change made
  after test outcomes are seen is disclosed, and the affected results no
  longer count as an untouched held-out test.

**Declared before registration.**
- Every P3.3 development result was seen.
- The test-week files were only downloaded and hashed.
- No test-week content and no realization 100–199 had been read.
- **Tests:** 5, on toy tables. They check:
  - that the split simulator reduces exactly to P3.3's;
  - that it scores on the evaluation model;
  - the budget oracle's bracket and budget;
  - the bootstrap intervals.
- **Full suite:** 233 tests pass.
- **One smoke run before registration** (2 realizations, scratch folder,
  541 s).
  - It used the 7 real development files and 7 *synthetic* test-week files,
    so no test-week content was read.
  - Every gate G0–G10 was computable and passed.
  - G3 passed on the real development data: the frozen model reproduces
    every P3.3 development result to ≤ 1e-12.
  - The verdict was forced to fail.
  - It printed only gate lines, and its outputs were not opened.

#### P3.4 run 1 (commit e55fd3a): all gates G0–G10 PASSED; H1, H2, H4 and Q3 held; result of record (2026-11-11, 11 320 s)

Outputs are in `results/phase3/p3_4_heldout/`. The raw files
(`test_table_isd200.npz`, `runs.json.gz`) are git-ignored; their hashes in
the manifest match. The manifest's config hash (1d229f02…) equals the
config in the registration commit e55fd3a, and the four scripts are
unchanged since that commit. Nothing was tuned, and no change was made
after the run.

Independent check, by a separate read-only script:
- all output, input and trace hashes;
- no CR bytes in the committed outputs;
- the four predictions re-scored from the per-cell results, using the
  thresholds in the committed config, with the same verdicts;
- pooled energies and violation shares equal the means of the 25
  per-square runs (462 keys; ≤ 4.4e-16).

| Gate | Result |
|---|---|
| G0 | 6 provenance checks and 8 constant checks pass; P3.3 run 2 manifest, outputs and config hashes; frozen parameters and test squares equal the config; all 14 trace hashes |
| G1 | Offset-0 builder bitwise equal to P3.2c's; realizations 100–199 drawn, 6 random-stream calls each, disjoint from development |
| G2 | Every test-week file aligned to the local day; 25 test squares disjoint from development; no slot without activity in any test square; 1 and 2 clipped slots at g = 3 and 3.5 |
| G3 | Development trace hash equal to P3.3 F9; 126 energy and violation values reproduced exactly (maximum difference 0) |
| G4 | 45 batched solves converged (residual ≤ 2.0e-11); closure ≤ 2.2e-16; occupancy ≤ 2.0e-11; 4824 workload comparisons, 0 mismatches; loads monotone |
| G5 | Identity to 1.8e-13; outage monotone in every pattern |
| G6 | No off-sector 7.2 GHz load or user; on-sector load > 0 at every x > 0 |
| G7 | 100 test runs: energy to 9.8e-15; violation seconds and bits exact; transitions equal |
| G8 | 700 checks, 0 violations |
| G9 | 6 cells (2 mixes × S0–S2) × 7 g × 25 squares = 1050 runs: lower bound ≤ upper bound in all; budget met in all on the decision model; bracket width ≤ 1.8e-4 relative |
| G10 | Test table 10 448 s (2.9 h, offline). Median (99th percentile) per decision: `thr` 0.06–0.07 (0.20–0.22) ms, `exh` 0.22 (0.78) ms, `mpc` 0.30 (0.93) ms; `oracle` 41 ms and `oracle_budget` 2.5 s per site-week. WSL2, x86-64 laptop |

**Scored predictions.** All four held.

**H1 (energy with service): held, 8 / 8 cells.** These are `mpc` savings vs
`aon`, with the 95% pigeonhole interval and `mpc`'s avoidable all-user
violation share:

| Mix, g | S0 (≥ 3%) | S2 (≥ 10%) |
|---|---|---|
| 10/90, 1 | 10.38% [9.60, 11.15]; 0.004% | 42.20% [41.61, 42.82]; 0.008% |
| 10/90, 2 | 4.97% [3.98, 6.11]; 0.040% | 29.58% [27.71, 31.51]; 0.754% |
| 26/74, 1 | 10.22% [9.49, 11.03]; 0.500% | 37.63% [36.44, 38.95]; 0.500% |
| 26/74, 2 | 4.76% [3.76, 5.88]; 0.683% | 18.92% [15.60, 22.51]; 0.683% |

**H2 (the UL is necessary): held, 9 / 9 cells.** At 26/74, the avoidable
all-user violation share of `thr_dl` minus that of `thr_ul`, in percentage
points, with the 95% square-bootstrap interval:

| g | S0 | S1 | S2 |
|---|---|---|---|
| 1 | 2.44 [1.94, 2.96] | 8.62 [6.31, 10.83] | 8.62 [6.52, 10.68] |
| 1.5 | 8.07 [6.67, 9.45] | 38.80 [33.05, 44.38] | 38.80 [32.93, 44.81] |
| 2 | 5.84 [4.50, 7.27] | 46.12 [38.98, 52.95] | 46.12 [39.03, 53.14] |

On its own DL-only criterion `thr_dl` violates ≤ 0.90% in these cells. The
UL-agnostic rule therefore looks compliant while failing the all-user
criterion.

**H4 (feasibility boundary): held.** The unavoidable all-user share is:
- at 26/74: 0.46% at g = 2.0 and 7.53% at g = 2.5;
- at 10/90: 0.08% at g = 3.0.

**Q3 (oracle gap): held, 8 / 8 cells.** These are (E_mpc − E_oracle)/E_aon
in points, with the 95% interval:

| Mix, g | S0 (≤ 1) | S2 (≤ 5) |
|---|---|---|
| 10/90, 1 | 0.07 [0.05, 0.08] | 0.07 [0.06, 0.08] |
| 10/90, 2 | 0.12 [0.10, 0.14] | 2.65 [2.30, 2.99] |
| 26/74, 1 | 0.31 [0.26, 0.36] | 3.06 [2.62, 3.48] |
| 26/74, 2 | 0.27 [0.23, 0.32] | 2.35 [1.95, 2.79] |

- These are reported alongside, not scored. In the same cells, the gap to
  `oracle_budget` is ≤ 3.72 points and the gap to `oracle_test` is
  ≤ 3.06 points.
- The S2 gaps are up to 0.4 point larger than in development, where they
  were ≤ 2.6 points.

**Development vs test.** Savings vs `aon`, `mpc` / `oracle`, in %. Both
sides use the pooled ratio 1 − E_c/E_aon on which H1 is scored. P3.3's
development table reports the mean of per-square savings, which differs
by ≤ 0.13 point.

| Mix, g | S0 dev → test | S1 dev → test | S2 dev → test |
|---|---|---|---|
| 10/90, 1 | 10.91 / 10.97 → 10.38 / 10.45 | 24.33 / 24.36 → 23.81 / 23.84 | 42.66 / 42.71 → 42.20 / 42.27 |
| 10/90, 2 | 5.65 / 5.77 → 4.97 / 5.09 | 15.80 / 16.30 → 14.77 / 15.34 | 31.08 / 33.47 → 29.58 / 32.23 |
| 26/74, 1 | 10.80 / 11.05 → 10.22 / 10.52 | 22.68 / 23.93 → 21.81 / 23.28 | 38.85 / 41.48 → 37.63 / 40.69 |
| 26/74, 2 | 5.45 / 5.71 → 4.76 / 5.03 | 11.79 / 12.89 → 10.67 / 11.81 | 20.62 / 22.90 → 18.92 / 21.27 |

- **Savings fell by 0.45–1.70 points in every cell, and the oracle's fell
  by a similar amount (0.44–1.62 points).** The drop therefore comes
  mainly from the data, not from the online controllers. The development
  values are on different squares, a different week and different
  realizations; the three effects are not separated.
- **For the test squares, the test week has higher peaks relative to the
  frozen a_ref.**
  - The busiest hour is a median 3.7% higher, in 14 of 25 squares.
  - Slots above the reference busy hour rose from 1.13% to 1.96%.
  - Mean normalized traffic is 0.8% lower, because one square fell to
    37% of its development activity. The median square is 1.9% busier.
- **The violation shares carried over.** For `mpc` at 26/74, g = 1–2:
  0.46–1.004% in development, 0.50–0.79% on test.
- **The H4 unavoidable shares rose**, consistent with the higher peaks:
  - 26/74: 0.23 → 0.46% at g = 2 and 5.48 → 7.53% at g = 2.5;
  - 10/90: 0.07 → 0.08% at g = 3.
- **The H2 differences moved by −0.62 to +3.07 points.** They fell at S0
  (g = 1.5 and 2) and rose at S1/S2.

**Held-out observations** (reported, not scored):
- **Radio holdout.**
  - `oracle` has zero avoidable violations on its decision model and
    0–0.61% on the test table.
  - `oracle_budget` uses its full 1% on the decision model. On the test
    table it exceeds 1% in 8 of 42 cells: 10/90, S1/S2, g ≥ 2, at
    1.38–2.04%. A plan that spends the whole allowance on the model is not
    robust to the radio holdout. `mpc` stayed at 0.75–0.95% in those
    cells.
  - Knowing the test table (`oracle_test` vs `oracle`) changes the saving
    by −0.02 to +0.22 point.
- **Budget compliance across all 14 scenarios** (each controller in
  7 g × 2 mixes × 14 scenarios = 196 cells):

  | Controller | Cells within the 1% budget, test | Development |
  |---|---|---|
  | `mpc` | 191 | 179 |
  | `exh` | 137 | 158 |
  | `thr_ul` | 136 | 108 |
  | `thr_dl` (all-user) | 94 | 94 |
  | `tod` | 84 | 112 |

  `mpc`'s five failures are all at 10/90, `mrf1024_S0`, g ≥ 1.5
  (1.31–7.87%). They are present in development too (1.31–7.83%); see the
  corrections below.
- **Paired differences** (review point A; there is no superiority claim).
  Over the 12 primary cells with g ∈ {1, 2}:
  - `mpc − exh` is 0.00 to +0.57 point of E_aon, and `mpc − thr_ul` is
    −0.02 to +0.50 point. `mpc` spends at most about half a point more
    energy.
  - In exchange, its avoidable violation share is 0 to 0.21 point lower
    than `exh`'s (`viol mpc − exh` −0.21 to 0.00).
- **Operations** (`mpc`, primary cells):
  - 1.0–28.5 transitions per site-day and ≤ 2.03 gap seconds per
    site-day;
  - partial patterns are used up to 34% of the time;
  - J per delivered Mbit: 9.16 against 10.22 (`aon`) at 10/90, g = 1, S0,
    and 7.58 against 8.45 at 26/74.
  - At 26/74, g = 2, the total all-user failure share is 1.14%: 0.68%
    avoidable plus 0.46% unavoidable.

**Corrections to the P3.3 record** (found while verifying P3.4; they change
no P3.3 or P3.4 verdict):
1. **The 1% budget is a tuning average, not a per-cell guarantee.** P3.3
   run 2 says the tuned parameters are "all within the 1% budget on
   development data". By the D32 selection rule, the budget applies to the
   violation share averaged over the seven g in the primary scenario.
   Individual (g, scenario) cells can exceed it. In development, 27 of 280
   `mpc` cells do.
2. **The `mpc` violation range rounds an exceedance.** P3.3 run 2 gives
   `mpc`'s violations at 26/74 as "0.46–1.00%". The g = 1.5 value is
   1.0042%, so that cell's saving is unqualified (D28).
3. **The hardware sensitivity omitted its high-load violations.** It
   reported savings at g = 1 only (M_RF 1024: 33.6%; 53 dBm: 19.1%). At
   10/90 and g ≥ 1.5, the S0-tuned forecast quantile (α = 0.5) breaks the
   budget under `mrf1024_S0` and `hw53_S0`: `mpc` violates 1.31–7.83% and
   `exh` 1.35–8.40%. The test reproduces this (`mpc` 1.31–7.87% under
   `mrf1024_S0`).
   - The parameters were selected on the primary energy model, whose
     7.2 GHz cell draws 171.6 W with no load and 760.5 W at full load.
   - Both sensitivities make the 7.2 GHz layer much more power-hungry:
     - M_RF 1024 draws 492.9 W with no load;
     - 53 dBm draws 214.8 W with no load and 1341.9 W at full load
       (assumed PA η 0.15).
   - Under them, the same α keeps far fewer cells on. At 10/90, g = 2:
     0.32 against 1.23 on test, 0.27 against 1.14 in development. More
     slots then fall above the chosen pattern's capacity.
   - **The two development results are bitwise identical,** with the same
     pattern sequence. This is not an error:
     - at ISD 200 the DL is interference-limited, so DL x* barely depends
       on the DL power offset: at 53 dBm it equals the 50 dBm value in
       seven of the eight patterns, and all-on is 3.738 against 3.719;
     - both energy models lead `mpc` to the same decisions.
   - **The frozen parameters are hardware-specific.** No budget-compliant
     saving is claimed for those cells.

**Limitations** (as registered): Milan 2013 CDR activity, not 2026 6G
traffic or bytes; seven held-out days; eight network-wide sector-index
patterns, not 57 independent sectors; the online decision time excludes
the 2.9 h offline table; everything is conditional on the P3.1–P3.3
models. Added by this run: the frozen tuning is hardware-specific
(correction 3).

**Decision** (registered rule; all gates passed). P3.4 run 1 is Phase 3's
held-out result of record. Next is the publication-readiness audit.

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
3. **P3.3, secondary-cell activation delay** from TS 38.133. *Closed by D23
   (2026-11-10):* the FR1 rule of cl. 8.3.2, extrapolated to 7.2 GHz.
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
   - *Resolved by D22 (2026-11-10):* under the all-user criterion ISD 500
     fails at any traffic. It leaves the energy stages and is reported as a
     coverage boundary.
7. **The frozen tuning is hardware-specific** (found while verifying
   P3.4).
   - At 10/90 and g ≥ 1.5, the S0-tuned α = 0.5 breaks the 1% budget under
     the M_RF 1024 and 53 dBm energy models (`mpc` 1.3–7.9%), in
     development and on test.
   - **For the publication audit to decide:**
     - (a) state it as a limitation and claim no saving in those cells; or
     - (b) define per-hardware tuning on development data, as a new
       registered stage.
   - Option (b) cannot be validated on the test week, which has now been
     read. Unread Milan weeks remain (25 Nov – 22 Dec 2013; the dataset
     runs from 1 Nov 2013 to 1 Jan 2014) and could serve as a fresh
     held-out set.
   - *Decided 2026-11-11 (§10.3): option (a).*

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

## 7. Audit of P3.2b and review of the P3.2c design (ChatGPT, 2026-11-10) and response

| Point | Verdict | Action |
|---|---|---|
| `on_ulca` assigns one UL carrier per user; it is not per-UE UL CA | **Correct** (`assign()`, line 218) | D20. The same holds for the DL `on` state, which the review did not mention |
| Admitted-user outage is not the M.2412 all-location measure; report all-user outage too | **Correct** | D21. All-user ≤ 5% is primary for energy. P3.2b already recorded the all-user outage per grid point, but no criterion used it |
| ISD 200: admitted budget (0.05 − 0.0219694)/(1 − 0.0219694) = 0.02866; ISD 500 cannot meet 5% all-user | **Correct** (arithmetic checked) | D21, D22 |
| Do not overwrite P3.2b's registered metric or verdicts | Agreed | P3.2b's section is the record; D20 and D21 apply prospectively |
| S2 must use P_empty, not P_micro; recompute the sleep anchors for each scenario | Agreed (already D11) | E1 checks that S2's no-load power equals P_3.5(0) |
| Include UE SCell activation; do not assume zero without a source | Agreed | D23, from TS 38.133 cl. 8.3.2 (FR1 rule, extrapolated) |
| Recompute association, interference and outage for the actual activation pattern; never reuse all-on loads | Agreed (already D8) | Each state is solved by the P3.2b engine; E2 checks the reproduction bitwise |
| The engine is homogeneous by sector index, not 57 independent controllers | **Correct** (D15's stated limit) | Kept as a stated limit. Heterogeneity is a P3.3 design question |
| Gates C0–C12 | Adopted in substance as E0–E8 | C5 (coupling) was passed by P3.2a's Q gates and is inherited through E2. C6's queues do not exist in a flow-level model, so conservation is E3's closure. C8 (causality) is trivial for time-of-day schedules: it is E6 here and substantive in P3.3. C12's clustered intervals are over realizations (D25) |
| Baselines: 3.5 only, always on, fixed schedules, an optimistic no-transition bound | Agreed | D24 |
| Energy saving only where both policies meet the same service constraint | Agreed (already the research design's rule) | D21 |
| Separate geometry randomness from traffic randomness | Agreed | P3.2c has no traffic randomness (flow-level means, ETSI step profile). It applies in P3.3 |
| "Joules per correctly delivered bit" | Partly adopted | J per delivered bit is reported, with each policy's feasibility flags beside it; a bit delivered to a user below R is still delivered |
| Git: the archive does not prove the remote state | Agreed | The user pushes; `git log origin/main..main` lists what is unpushed |

## 8. Review of P3.2c and the P3.3 design (ChatGPT, 2026-11-10) and response

| Point | Verdict | Action |
|---|---|---|
| (A) Separate the offline best schedule from a realizable controller | **Correct.** P3.2c's best schedule was chosen knowing g and each schedule's feasibility | D27: three information classes; the P3.2c best schedule is relabelled a g-aware offline benchmark |
| (B) Never measure savings against an infeasible baseline | Agreed. P3.2c's savings were against `aon_ulsel`, which is feasible wherever any schedule is; 35-only was used only where feasible (E-P1 at 10/90, g = 1) | D28 makes it a rule: savings only where the controller and `aon` both meet the budget |
| (C) Keep S0–S2 in every primary result | Agreed (already P3.2c's decision rule) | Every P3.3 scenario set includes S0–S2 |
| (D) The 3.5 GHz model's missing UL-load term is a research risk | **Correct** | D29: a UL-proportional receive-power sensitivity built from the model's own idle factors (≤ 35.5 W per cell) |
| All-on may not be the maximum-capacity configuration | **Correct, and checked rather than assumed** | D30: four nested patterns, each solved exactly; the maximum-capacity pattern is reported |
| Controller set: always-on/off, threshold + hysteresis, interference-aware greedy, classical solver, proposed robust controller, offline oracle | Adopted | D32. With four nested patterns, exhaustive search is exact and cheap, so `exh` is the exhaustive per-slot optimizer and greedy coincides with it. The solver is the exact DP (`oracle`, and `mpc` over its horizon), verified against brute force (F8) |
| Novelty must come from the 3.5/7.2 GHz UL feasibility, directional interference, hardware and switching, not from an algorithm's name | Agreed | No novelty is claimed for the DP or MPC |
| "L2S: Learning to Optimize Sleep Strategy…", IEEE TVT 2026, as prior art | **Not verified.** A search found no paper with this title or name | Not cited until located on IEEE Xplore |
| Validation gates (causality, radio consistency, coupling, service integrity, energy, transitions, exhaustive search, fairness, robustness, reproducibility) | Adopted | F0–F10. Robustness across S0–S2, mixes and independent demand is P3.4's held-out design |
| Keep P3.3 (development) and P3.4 (frozen, held-out) separate | **Agreed; reverses D26** | D33: two measured runs. P3.4's test squares, test week and realizations 100–199 are read only after the freeze |
| Report energy, feasibility, decision time and robustness together | Agreed | D32 reporting list; F10 |
| ITU-R M.2412 counts all user locations | Agreed (already D21) | All-user is primary; admitted-user is reported alongside |

## 9. Audit of P3.3 and review of the P3.4 design (ChatGPT, 2026-11-11) and response

| Point | Verdict | Action |
|---|---|---|
| (A) MPC does not consistently beat the online exhaustive baseline | **Correct** (`thr_ul` and `exh` are within 0.5 point of `mpc`) | No prediction claims MPC superiority; every controller is reported with paired differences. The contribution is the UL-feasible, interference-aware framework and its energy–service map |
| (B) The decision time excludes the feasibility-table construction; eight patterns are not 57 independent sectors | **Correct** | Offline table time and online decision time are reported separately; the decision space is stated as a limitation |
| (C) The test squares' normalization uses their own development-week history | **Correct, disclosed** | Holdouts named precisely: temporal (test week), radio (realizations 100–199), spatial (squares excluded from tuning; their historical a_ref and their selection by development-week activity disclosed) |
| (D) The oracle allows no avoidable violation, while the online controllers may use up to 1% | **Correct** | `oracle_budget` added: the same 1% allowance per run, Lagrangian DP with certified upper and lower bounds; Q3 stays registered against the zero-violation oracle, with the other two gaps reported alongside |
| Numerical H1/H2/H4/Q3 predictions with fixed cells, metrics and pass rules | Adopted | H1 (≥ 3% S0 / ≥ 10% S2, interval above 0, violations ≤ 1%), H2 (≥ 1 point, interval above 0), H4 (boundary in (2.0, 2.5] at 26/74; none below 3.0 at 10/90), Q3 (≤ 1 / 5 points) |
| Paired differences; squares and time dependence; realizations are not independent weeks | Adopted | Pigeonhole bootstrap over squares × realizations; bootstrap over squares for violation shares |
| Report all controllers under S0–S2 with avoidable, unavoidable and total failures | Adopted | Results table and paired comparisons |
| Preserve failures; do not tune on held-out outcomes | Adopted (already the practice) | Decision rule |
| Milan 2013 data are temporal-demand robustness, not 2026 6G traffic; seven days are not seasonal | **Correct** | Stated as limitations |
| The upload had results only, not the source; claims verified against summaries | Noted | The source, configs and tests are in the repository; every gate in P3.4 re-executes the frozen code |

## 10. Audit of P3.4 and publication-readiness review (ChatGPT, 2026-11-11) and response

The review came as three messages, all on 2026-11-11:
1. an audit of P3.4 with a publication-readiness verdict;
2. a nine-figure plan;
3. after my response, its acceptance of the corrections, an eight-figure
   structure and the figure production rules.

Every claim was checked against the frozen outputs with read-only scripts.
The P3.4 outputs (`summary.json`, `raw/runs.json.gz`) were matched to their
manifest hashes, and the P3.3 summary, the configs and this protocol were
also used. One correction I made in my response was itself wrong. ChatGPT
accepted it, and 10.2 records it and its correction.

**Verdict adopted:** P3.4 stays frozen as Phase 3's confirmatory result. The
evidence supports drafting the manuscript, not yet an unconditional
submission. The figure stage starts from the frozen outputs.

### 10.1 Claims confirmed

| Claim | Check |
|---|---|
| 11 of 11 gates G0–G10; output hashes | `validation.json`; SHA-256 of all three outputs against `manifest.json` |
| 11,550 per-square records | `raw/runs.json.gz`: 11 controllers × 2 mixes × 7 g × 3 primary scenarios × 25 squares |
| 10.38% at 10/90, g = 1, S0, recomputed from per-square energies | 1 − ΣE_mpc / ΣE_aon = 0.10379 |
| H2 differences 2.44–46.12 points (26/74) | `paired` and `results_test`; the nine registered cells |
| H4 unavoidable shares at 26/74: 0.46%, 7.53%, 22.77%, 34.97% (g = 2.0, 2.5, 3.0, 3.5) | `aon` rows; the share is identical for every controller and scenario |
| mpc meets the budget in 191 of 196 cells; the five failures are 1.31, 5.27, 7.87, 7.44 and 6.08% | All five are at 10/90, `mrf1024_S0`, g = 1.5–3.5 (the audit omitted the mix) |
| At 26/74, g = 2, S2: 0.683% avoidable, 0.456% unavoidable, 1.139% total | `total_failure_share_all_user` is a stored field; the P3.4 record already gives it |
| mpc does not always beat `exh` on energy (29.58 vs 30.11% at 10/90, g = 2, S2; 18.92 vs 19.34% at 26/74) | Both meet the budget there (`exh` 0.961% and 0.841%) |
| Median online decision 0.296 ms; offline test table 10,448 s | `decision`, `test_table_seconds` |
| E1c.3, ISD 200, case A: 7.2 GHz DL feasibility 0.995 (averaged interference) vs 0.962 (directional) | `PHASE_2_RADIO_PROTOCOL.md`, E1c.3 results table |
| Source freeze not reconstructable from the results-only upload | Checked in the repository instead: the four scripts and the config at run time are byte-identical to the registration commit e55fd3a |
| Already in the record before this audit | All-user total failure (§9); carrier selection, not carrier aggregation (D20); Milan activity is not 6G throughput (§9); no retuning on the test week (open item 7) |

### 10.2 Incorrect statements and corrected interpretations

| # | Statement (source) | Corrected interpretation |
|---|---|---|
| 1 | "19-site Munich network geometry" (ChatGPT figure plan) | **Substantially correct.** See row 2 |
| 2 | "Phase 3's 19 sites are a synthetic hexagonal layout; Munich is only the single-site ray-tracing scene" (**my** response; ChatGPT accepted it) | **Wrong.** E1c.2 (Phase 2) anchors the M.2412 hexagonal lattice (ISD 200 m, rings 0–2, no wrap-around) at the Munich-scene gNB, which is site 0 at 27 m. In-scene lattice points snap to the nearest roof (≥ 6 m) within 0.2·ISD: at ISD 200, 17 rooftop sites and 1 mast. Each link's LOS/NLOS state, facade and indoor depth come from the Munich scene geometry. Path loss is TR 38.901 UMa (a geometry-conditioned hybrid, `E1_PARAMETERS.md`). Ray tracing is used only in the single-site E1a checks. The controller works in D15's typical-site frame, with sector-index patterns applied to every site. Figure 1(a) therefore shows the scene-anchored 19-site layout from `results/phase2/e1c2_geometry/` |
| 3 | Subtitle "A Site-Specific Optimization Framework", presented as the original title (ChatGPT) | Not in any repository title, and **dropped**. The reason is not the one in my response: the layout *is* scene-anchored (row 2). But path loss is statistical, and the controller optimizes one homogeneous typical-site pattern, not specific sites. "Site-specific" would imply site-specific channels and site-level optimization. Title: "Uplink-Feasible and Energy-Aware Band Activation for Co-Sited 3.5/7 GHz 6G Networks" |
| 4 | "2.0× today's reference demand" (ChatGPT) | g scales each square's trace: x_t = g·a_t/a_ref. a_ref is the square's busiest development-week hour (maximum 6-slot mean). x = 1 is T_0.5, the offered DL traffic per site at which the 3.5-GHz-only network's mean 3.5 GHz DL occupancy is 0.5, the ETSI ES 202 706-1 busy-hour level (D18). Development T_0.5 = 513.6 Mbit/s per site (ISD 200, case C), frozen. It is a calibrated busy-hour load scale applied to 2013 activity, not "today's demand" |
| 5 | "Fewer than 1% of operating periods experience any service failure" would be the reader's misreading; the audit called the total a "total service failure" (ChatGPT) | The metric is a share of **time**. A 10-minute slot fails when more than 5% of all users are in DL or UL outage (ε = 0.05, D21), and wake-gap seconds are added. The avoidable share counts slots that some pattern could serve. The unavoidable share counts slots no pattern can serve. Total = avoidable + unavoidable. Users in outage within passing slots are not counted. The paper wording is "share of 10-minute intervals in which more than 5% of users are in outage", never "service failure rate" alone |
| 6 | "L2S, IEEE TVT 2026" as closest prior art (ChatGPT; second time, see §8) | Not found by two searches on 2026-11-11 (none in §8 either). **Excluded** until a DOI or IEEE Xplore record is given. The other three cited works were verified on 2026-11-09 (1.2, 1.6) |
| 7 | Figure font Garamond (ChatGPT) | Times-compatible: STIX General, which ships with matplotlib 3.11.2 and is designed to match Times. TrueType (Type 42) embedding in PDF, and IEEE column widths (3.5 in single, 7.16 in double) |
| 8 | Figure 7(a) "uplink service violations" (ChatGPT) | H2's metric is the all-user (DL + UL) avoidable violation share. The UL-agnostic controller is also shown as its own DL-only check sees it. No UL-only violation share is stored, so none is plotted |
| 9 | Figure 9(a) as an energy–service "Pareto" comparison (ChatGPT) | A scatter of frozen operating points. A Pareto frontier needs a parameter sweep and a nondominance analysis. P3.3's tuning log has a sweep on development data, averaged over g, but it is not a test-data frontier and is not presented as one |
| 10 | "54 additional compliant cells" as a strength (my count; not a claim of the audit) | True but threshold-driven: in those 54 cells `exh` violates 1.02–1.23% and mpc 0.79–0.96%. Where both meet the budget (137 cells), `exh` saves 0.07 point more on average (at most 0.57). No cell has `exh` meeting the budget while mpc does not. The defensible statement is "energy within 0.57 point of `exh`, with a slightly lower violation share" |

### 10.3 Decisions

- **Open item 7: option (a).** The five failures stay in every table and
  figure, marked as failures, and no saving is claimed in those cells.
  Per-hardware retuning, if wanted later, needs its own registered stage and
  a fresh, unread Milan week.
- **No superiority claim** over `exh` or `thr_ul` (10.2, row 10; §9 A).
- **Contributions** (what the evidence supports):
  - UL-blind activation can look compliant to itself while failing the
    all-user target (H2);
  - interference coupling and hardware architecture change the activation
    decisions;
  - an online controller comes close to a perfect-foresight reference inside
    a stated feasibility boundary (Q3, H4).
- **Not claimed as novel:** base-station sleeping, FR3 energy modelling, DP
  or MPC.
- **Limitations stated:** the hardware-specific tuning; eight sector-index
  patterns, not 57 independent sectors; Milan 2013 activity as demand
  shape; one test week; the model-based (not measured) 7.2 GHz hardware;
  S1/S2 as assumed overhead.

### 10.4 Figure plan (final, eight figures)

| Final | Title | Panels | Working ID | Source |
|---|---|---|---|---|
| 1 | Dual-band network architecture and proposed control framework | 1 × 2 | 1 | E1c.2 geometry (Munich-anchored layout); controller structure |
| 2 | Directional interference and UL/DL radio feasibility | 1 × 2 | 2 (c, d) | E1c.2/E1c.3; P3.2b network-frame capacities (3.5 GHz only, DL-only 7.2 GHz carrier, UL enabled) |
| 3 | Base-station power consumption and hardware-overhead sensitivity | 1 × 2 | 4 | P3.1, P3.2c |
| 4 | Adaptive activation patterns and switching behaviour | 1 × 2 | 5 | P3.4 `pattern_share`, `mean_cells_on`, `transitions_per_day` |
| 5 | Held-out energy savings and service reliability | 2 × 2 | 6 | P3.4 |
| 6 | Consequences of downlink-only energy optimization | 1 × 2 | 7 | P3.4 |
| 7 | Sustainable traffic capacity and network infeasibility boundary | 1 × 2 | 3 + 8 | P3.3 typical-site capacity table (all eight patterns); P3.4 and P3.3 unavoidable shares |
| 8 | Controller comparison, oracle gap and hardware robustness | 2 × 2 | 9 | P3.4 `results_test` and `paired` |

- **Supplement:** the beamforming deficits (LOS vs NLOS; codebook vs
  DirectPath), propagation and link-mapping validation, energy-model
  reproduction, timing, and the other sensitivities.
- **Figure 7(a) uses the controllers' own frame.** The typical-site table
  (D15) gives 2.20 for all-on at 26/74, against 2.33 in P3.2b's network
  frame. Panel (b)'s boundary is computed in the typical-site frame, so the
  two frames are not mixed in one figure. The network-frame three-way
  comparison goes to Figure 2.
  - **Correction (2026-11-11, recorded before Figures 1–3 were built; see
    10.7, row 1).** The bullet above is wrong about the cause. P3.2b is
    already in the typical-site frame: D15 was fixed before P3.2b, and
    `scripts/p3_2b_traffic_resources.py` implements it. The difference
    between 2.33 and 2.20 is the service criterion, not the frame. 2.3347
    is the admitted-user x* (D17, P3.2b) and 2.2013 the all-user x* (D21),
    both for all-on at 26/74 in the same P3.2c record. Figure 7 and the
    controllers use the all-user criterion, so Figure 7 is unaffected.
- **First stage: Figures 4–8**, from the frozen P3.3 and P3.4 outputs only.
  Figures 1–3 follow from the earlier validated outputs.

### 10.5 Figure production rules (adopted)

- Generated only from recorded results. Plotting is read-only: inputs are
  hash-checked against their manifests before and after.
- Vector PDF and 600 dpi PNG at final printed size, IEEE column widths.
- STIX General (Times-compatible) with TrueType embedding. Every text
  ≥ 7 pt at printed size, checked in code.
- **Fixed encodings.** Each controller has one colour and one marker across
  all figures, and identity never relies on colour alone. The palette was
  validated for colour-vision deficiency. Mixes are encoded by marker fill
  and line style. Scenarios S0–S2 use one ordinal ramp.
- Panel descriptions (a)–(d) sit under each panel. The full caption sits
  under the figure.
- The 1% line is labelled "1% avoidable-violation budget". Unavoidable and
  total shares stay distinct.
- Failed configurations are shown and marked, never dropped.
- Confidence intervals are drawn where stored: the 95% pigeonhole intervals
  of savings and of paired gaps.
- No per-slot record exists, so no time-series is reconstructed. A timeline,
  if wanted, would be a separate development-data visualization.
- **Automated verification.** Every plotted value is read back from the
  rendered artists and compared with values recomputed independently from
  the frozen summaries. Caption numbers are checked the same way.
- **Records.** A manifest holds the input, output and script hashes,
  software and font versions, and the git head. Rendering is checked for
  byte-identical output.

### 10.6 Figures 4–8 from the frozen outputs (2026-11-11): 90 of 90 checks passed

**Files** (`paper/figures/`, generated at commit 62edb2b):
- the five figures as PDF and 600 dpi PNG, 7.16 in wide;
- `captions.md`;
- `source_data/*.csv` (579 rows, each with its source key);
- `plotted_values.json`;
- `caption_facts.json`;
- `manifest.json` (SHA-256 a61d6a8a…);
- `verification_report.json` / `.txt`.

**Reproduce:**
`python -I scripts/figures/frozen_figures.py .`, then
`python -I scripts/figures/verify_frozen_figures.py .`. A rebuild in a
fresh process is byte-identical.

**Verification:**
- **Independent recomputation.** The verifier recomputes the following from
  the frozen summaries and matches them exactly: all 1,080 values read back
  from the rendered artists, all 579 source-data rows, every caption number
  and nine caption constants. Examples of the constants are 25 squares,
  realizations 100–199, 2,000 resamples and T_0.5 = 513.6 Mbit/s.
- **Other checks.**
  - The tagged artists are exactly the expected set.
  - Each controller has one colour across figures.
  - The PDFs embed only STIX TrueType fonts, with no Type 3.
  - Every text is ≥ 7 pt, with no overlap and no text inside another
    panel.
  - The inputs are unchanged.
- **Mutation test** (scratch copy). Six planted errors were all caught by
  their own content checks: a value off by 0.01 point, a missing artist, a
  dropped hatch, a wrong colour, a wrong caption number and a wrong CSV
  value.

**Found while building, and recorded here:**
1. **Identical decisions across energy scenarios.** At 26/74, `mpc` takes
   identical decisions under S0, S1 and S2: equal pattern shares,
   transitions, gap seconds and violations at every g. At 10/90, S1 = S2.
   - Only the energy accounting differs, so the savings differ.
   - The figures draw one line per group of identical decisions, and the
     captions state the groups.
2. **The first heatmap render had a wrong colour scale.** Its anchors did not
   match the 1% midpoint, so compliant cells at 0.5–0.95% showed red.
   - The numbers were right, so the value checks could not catch it; it was
     found by inspecting the render.
   - The scale is now centred on the budget, and a build-time check requires
     every cell to fall on the correct side of the budget colour.
3. **Omissions from two panels.** Figure 8(a) omits `oracle_test`,
   `oracle_dl` and 3.5-GHz-only operation, and Figure 6(b) omits `oracle_dl`.
   This keeps identity distinguishable with the eight validated colours.
   - Their values are in the P3.4 summary, and `oracle_test` appears in
     Figure 8(b).

**Remaining figure work:** Figures 1–3 from the E1c.2/E1c.3, P3.1, P3.2b
and P3.2c outputs, under the same rules.

### 10.7 Figures 1–3: decisions recorded before the build (2026-11-11)

ChatGPT's GO for Figures 1–3 (2026-11-11) asks for:
- **Figure 1:** the actual Munich-positioned 19-site geometry and the
  controller architecture (E1c site coordinates; the frozen
  implementation);
- **Figure 2:** directional versus averaged interference, and the resulting
  UL/DL feasibility (E1c.2, E1c.3);
- **Figure 3:** base-station power, and daily energy across S0–S2 (P3.1,
  P3.2c).

The following were checked and fixed before any Figure 1–3 code was
written.

| # | Point | Decision |
|---|---|---|
| 1 | §10.4 attributes 2.33 vs 2.20 to the frame | **My error**, corrected under §10.4. The gap is the criterion: admitted-user vs all-user. P3.2b is already in the typical-site frame |
| 2 | §10.4 puts P3.2b's three-way capacity comparison in Figure 2 | That placement rested on row 1's error. Under the primary all-user criterion (P3.2c `g_star`, 50 dBm), at 10/90 and 26/74: 3.5 GHz only 1.445 / 0.856; DL-only 7.2 GHz carrier 2.707 / 0.856; UL enabled 3.719 / 2.201. The first and third are already Figure 7(a)'s (0,0,0) and (1,1,1) bars. **Figure 2 follows the GO table** and shows E1c.2/E1c.3 only. The DL-only-carrier result goes in the text: 2.707 at 10/90; at 26/74, no gain over 3.5 GHz only |
| 3 | SINR distributions (working Figure 2(c)) | E1c.3 recorded only the 1st, 5th, 50th and 95th percentiles per setting (`quantiles_db_1_5_50_95`). The 0.05 dB histograms behind them were not saved, and the raw files hold per-victim feasibility counts, not SINR. **Figure 2(a) plots the four recorded percentiles; no distribution is drawn between them** |
| 4 | F2 threshold line in Figure 2(a) | E1c.2/E1c.3 apply `e1c_area_maps.thresholds()` to E1.5.3a's C-AMC T2 curve. For F2 the threshold depends on neither case nor carrier. The plotted values are E1.5.4's recorded analytic thresholds (12.92 dB DL, 8.66 dB UL). The verifier must reproduce them with the frozen E1c function |
| 5 | Traffic in Figure 3(b) | P3.2c's ledger uses the ETSI ES 202 706-1 three-level day, not the Milan traces of Figures 4–8: low 00–06 h (zero traffic), medium 06–16 h (g·x_30), busy 16–24 h (g). The caption says so |
| 6 | Curves in Figure 3(a) | The P3.1 powers recorded at the tabulated occupancies appear affine in occupancy, so straight lines through the recorded points would be exact. The verifier must confirm affinity before the lines are accepted |
| 7 | Map in Figure 1(a) | Drawn from recorded geometry only: the E1c.2 site file (ISD 200), the E1.1 outdoor mask and the E1c indoor samples. The two sample sets lie on one 4 m lattice and do not overlap; buildings are the indoor points. No scene mesh is read |

### 10.8 Figures 1–3 from the validated outputs (2026-11-11): 279 of 279 checks passed

**Files** (`paper/figures/`, generated at commit a88db7d):
- the three figures as PDF and 600 dpi PNG, 7.16 in wide;
- `captions_fig1-3.md`;
- `source_data/fig{1,2,3}_*.csv` (200 rows, each with its source key);
- `plotted_values_fig1-3.json`;
- `caption_facts_fig1-3.json`;
- `manifest_fig1-3.json` (SHA-256 7a0c8f08…);
- `verification_report_fig1-3.json` / `.txt`.

**Reproduce:** `python -I scripts/figures/model_figures.py .`, then
`python -I scripts/figures/verify_model_figures.py .`.
- A rebuild in a fresh process is byte-identical.
- The shared style module is unchanged since Figures 4–8 (a939b8f0…).

**What the figures show.**
- **Figure 1(a):** the E1c.2 layout in the Munich scene. It shows site 0
  (27 m), 17 rooftop sites and 1 mast (all at 25 m), the sector boresights
  (30°, 150° and 270°, numbered 1–3 as the pattern bits), the nominal cells
  and the buildings of the 4 m sample grid.
- **Figure 1(b):** the MPC loop as implemented, with its offline inputs.
- **Figure 2(a):** the four recorded percentiles of the case-A 7.2 GHz SINR,
  with the F2 thresholds.
- **Figure 2(b):** F2 feasibility per carrier and direction under the four
  interference models, in cases A and C.
- **Figure 3(a):** P3.1 power per cell, with S1 and S2 added.
- **Figure 3(b):** P3.2c daily site energy at 10/90, for 3.5 GHz only and
  always on under S0–S2.

**Verification** (`verify_model_figures.py`, which imports neither build
script):
- **Independent recomputation.** All 1,224 values read back from the
  rendered artists, all 200 source-data rows and every caption number are
  recomputed from the experiment files and match.
- **Placement and markers.** Every Figure 2 marker sits on the rendered row
  and panel labelled for it, and every artist lies in its own panel.
- **Model facts.**
  - Site placement: masts on their lattice points, rooftop sites within
    0.2·ISD.
  - The bold hexagon is the Voronoi cell of site 0. The scored users equal
    E1c.3's recorded victims exactly (935 outdoor, 5,756 indoor).
  - The map raster equals the recorded geometry (30,888 open outdoor points,
    as in E1.1).
  - The F2 thresholds are reproduced by the frozen E1c function (within
    1e-9 dB).
  - E1c.3's averaged model equals E1c.2's values exactly.
  - The S1 and S2 rules of D25 hold, and the sleep powers scale with
    P_aux.
- **Figure 1(b) against the code.** `p3_3_controllers.py` equals its blob at
  the P3.4 run commit (e55fd3a). The MPC source contains each step that the
  diagram and caption state. The fallback pattern is (1, 1, 1) for every mix
  and offset.
- **Other checks.** Input hashes against their manifests, fonts (STIX
  TrueType only) and PNG size.

**Mutation test** (scratch copy). Nine planted errors were all caught by
their own content checks:
- a value off by 0.01 kWh;
- a missing site marker;
- an infeasible marker drawn filled;
- a wrong S2 colour;
- the false caption range of finding 1;
- a CSV value off by 1e-4;
- two swapped row labels;
- the diagram claiming H = 6;
- an altered map raster.

My first CSV mutation was malformed, because it split on commas that
appear inside a field. It was rerun with a CSV parser.

**Found while building, and recorded here:**
1. **A false caption claim, caught before release.**
   - My first Figure 3(b) caption said always-on uses less energy than
     3.5 GHz only "from g = 1.75". That is true only for g = 1.75–3.25.
   - From g = 3.5, the 3.5-GHz-only energy stays at 82.83 kWh and always-on
     is dearer again.
   - Found by reading the rendered figure. The build now requires the range
     to be contiguous and recomputes it, and the mutation test includes this
     error.
2. **10.7, row 6 was wrong for the 7.2 GHz model.**
   - Its recorded powers deviate from a straight line by up to 0.73 mW, so
     the verifier refused the affinity claim.
   - Evaluated densely (10,001 points) with the frozen P3.1 function, the
     drawn segments are within 0.18 mW of the model.
   - The 3.5 GHz model is exactly affine.
   - The caption states both facts.
3. **Ties on the cell boundary.** 9 outdoor and 212 indoor victims lie
   exactly on the vertical edges of site 0's hexagon (x = −91.5 and
   108.5 m), and E1c.2 gives these ties to site 0.
   - The first build check tested the strict interior and flagged them.
   - The check is now boundary-aware and two-sided: no victim outside, and
     no non-victim strictly inside.
4. **S2 almost coincides with the 3.5 GHz line in Figure 3(a)** (within
   8.6 W), as the parity rule implies. The overlap is annotated, not
   hidden.

**Figure stage complete:** Figures 1–8, 369 of 369 automated checks
(90 + 279).

### 10.9 Revision of Figures 1 and 2: single-panel figures (user decision, 2026-11-11)

The user judged that the two-panel Figures 1 and 2 of 10.8 read as
illustrations rather than technical figures. They asked for single-panel
figures without (a)/(b) labels, as in leading journals. Decided:

- **Figure 1: one standalone network-layout figure** (single column,
  3.5 in). It is the Munich map that was panel (a).
  - File stem `fig1_network_layout_w1` replaces `fig1_architecture_w1`,
    which is deleted.
- **The controller diagram (old 1(b)) leaves the figures.** The controller
  goes into the text as Algorithm 1 (pseudocode).
  - When Algorithm 1 is written, each of its lines must be checked against
    the frozen `p3_3_controllers.py` (its blob at e55fd3a). The eight code
    claims verified for the diagram in 10.8 are the starting point.
- **Figure 2: one panel** (single column): the recorded case-A 7.2 GHz SINR
  percentiles under the four interference models, with the F2 thresholds.
  - My refinement, adopted: the share of user positions meeting F2 is
    printed beside each row. The figure therefore still carries the
    feasibility result: DL 0.995 (averaged) to 0.962 (directional) and 0.919
    (pure pattern).
  - The other values of the old panel (b), for 3.5 GHz and case C, go to the
    text or a table. They are in the E1c.3 results of
    `PHASE_2_RADIO_PROTOCOL.md`.
- **Figure 3 is unchanged:** two panels, double column.
- **§10.4's plan:** Figures 1 and 2 change from 1 × 2 to one panel each.

The scripts change accordingly (`model_figures.py`,
`verify_model_figures.py`), and the verifier gains three checks:
- every F2 share sits on its model's row;
- every share is consistent with its recorded percentiles relative to the
  threshold;
- the captions of Figures 1 and 2 carry no panel labels.

**Result** (generated at commit 008870b; `manifest_fig1-3.json` SHA-256
867e762f…):
- **Checks:** 193 of 193 pass. 1,141 plotted values, 176 source-data rows
  and every caption number are recomputed independently.
- **Rebuild:** byte-identical. Figure 3 is byte-identical to its 10.8
  version.
- **Mutation test:** 12 of 12 planted errors caught by their own content
  checks. The three new ones are:
  - a share moved to the wrong row;
  - a share changed from 0.962 to 0.972;
  - an "(a)" label added back to the Figure 2 caption.
- **The 10.8 count (279) is superseded.** The figure stage now stands at
  Figures 1–8, 283 of 283 checks (90 + 193).

**Regression found and fixed.** From 6b1e075 on,
`verify_frozen_figures.py` (Figures 4–8) crashed.
- **Cause:** it read every CSV in `source_data/`, including the Figure 1–3
  files added in 10.8. I had not re-run it after adding them.
- **Fix:** it now reads only the source-data files that its own manifest
  lists.
- **Re-run:** the same 90 checks pass on the same 1,080 values and 579 rows.
  Only the verifier hash in its report changed, and Figures 4–8 and their
  manifest are untouched.
- **Lesson:** after adding files to a shared output folder, re-run every
  verifier that reads that folder.

### 10.10 Figure 7 in colour and Figure 8 on one grid (user request, 2026-11-13)

**Figure 7 was grey.**
- **Cause:** I applied 10.5's rule "mixes are encoded by marker fill and
  line style" literally. That rule assumes colour is already taken by the
  controllers, but Figure 7 shows no controllers, so it ended up grey.
- **Change:** both panels now colour the two UL/DL mixes: 10/90 plum
  `#9b2f8f`, 26/74 bronze `#8f6400`.
  - In (a) these are the bars; in (b) the lines, markers, x* lines and
    their labels.
  - The DL-only limit stays black, and test vs development keeps filled and
    solid vs open and dashed.
- **Why these colours:** all eight palette hues belong to controllers.
  Reusing one would make a mix read as a controller, for example MPC blue
  for 10/90.
  - Checked with the dataviz validator. The pair passes every gate:
    normal-vision ΔE 23.8, CVD 18.4 (deutan).
  - Tritan separation is 7.3. That is legal with secondary encoding, which
    Figure 7 has: fixed bar position in (a), and circle vs square markers in
    (b).
  - Each colour is at least 15 OKLab ΔE from every controller colour and
    from the S0–S2 blues. The closest pairs are plum to violet (16.0) and
    bronze to green (16.4). Teal, olive and brown candidates failed this
    test or the chroma floor.
- **Rule amended:** mixes take these two colours only in figures without
  controller colours. Elsewhere they keep marker fill, line style or panel.

**Figure 8 was misaligned.**
- **Cause:** the top row and the bottom row used two independent grid
  layouts. Panel (c), with two heatmaps and a colour bar, was wider than
  (a), which shifted and narrowed (d).
- **Change:** one explicit grid, in inches.
  - (a) and (c) share left and right edges (0.97 and 3.97 in); (c)'s colour
    bar now fits inside that column, with its label moved to the gutter.
  - (b) and (d) share theirs (4.97 and 7.05 in).
  - Each row shares top and bottom.
  - (b)'s legend moved below the "Q3 limit (S0)" label, which it crowded.
- **Build check:** the build asserts the alignment, and the manifest
  records the edges.

**Verification additions** (`verify_frozen_figures.py`):
- every Figure 7 mix artist (22) carries its mix colour;
- the DL-only limit is black;
- no mix colour equals a controller or scenario colour;
- the manifest records the same mix colours;
- the recorded Figure 8 edges align.

No plotted value, source-data row or caption changes. Figures 4–6 are
byte-identical.

### 10.11 Manuscript draft (2026-11-13)

The user asked for the draft to follow their outline, with the references
they supplied cited in IEEE style, numbered in order of first citation.
`paper/manuscript/` holds the IEEEtran draft: 12 pages, Figures 1–8,
Tables I–III and Algorithm 1.

**How numbers enter the text.** No result is typed into `main.tex`.
- **Generation.** `scripts/paper/paper_numbers.py` writes 172 macros from
  the frozen outputs. Each input is hash-checked against its manifest, and
  P3.4's `validation.json` against its committed blob.
- **Kinds of macro.** Each macro is one of:
  - a verified figure-caption fact;
  - a value at a key path;
  - a stated aggregate.
- **Verification.** `scripts/paper/verify_manuscript.py` (77 checks, all
  passed):
  - recomputes every non-figure macro with its own code;
  - rejects any typed number that is not on an allowlist of design
    constants with justifications;
  - checks the qualitative claims of the text against the data;
  - checks Algorithm 1 and the model equations against the frozen
    controller code (its blob at e55fd3a);
  - checks the references, the figure hashes, the abstract length
    (≤ 250 words) and a clean compile with every float before the
    references.
- **Mutation test.** 7 of 7 planted errors were caught: a wrong value, a
  typed number, an undefined macro, a missing entry, a swapped figure, a
  non-ASCII initial and an unknown citation.

**References.** Every DOI and preprint was checked against its record:
- 28 DOIs against Crossref (26 supplied, 2 added);
- the dataset DOI against DataCite;
- the two preprints against arXiv.

For the supplied entries, titles, authors, volumes, issues and pages match
the supplied list. Changes and additions:
- **[10] L2S (IEEE TVT 2026) exists.** Crossref confirms vol. 75, no. 9,
  pp. 20896–20911 (DOI 10.1109/TVT.2026.3681243). The exclusion in
  10.2, row 6 is withdrawn; it is cited.
- **[30] TR 38.901:** V19.5.0 (2026-09) instead of V16.1.0, because the
  results use V19.5.0 from E1c onward.
- **[35] Milan dataset:** version 1.3 (DataCite; the files fetched), not
  "Version 1".
- **Page numbers added:** ICASSP 2026, pp. 21476–21480; CCNC 2026,
  pp. 1–6.
- **Seven methods sources added, each in the version that was read:**
  - Golard et al., PIMRC 2024;
  - Siomina and Yuan, IEEE TWC 2012;
  - Bonald et al., CWI PNA-E0302;
  - ETSI ES 202 706-1 V1.7.1;
  - 3GPP TS 38.133 V19.6.0 and TS 38.306 V19.4.0;
  - the Ericsson Mobility Report GenAI article (June 2025).
- **TS 38.214:** no version or date was supplied; to be confirmed by the
  author.

**Claims corrected while drafting, before any were committed:**
1. "MPC's violation share is never higher than exh's" is false. Of the
   137 cases in which both meet the budget, it is higher in one (10/90,
   g = 3, Cat 2 sleep, S0). The text now gives the count.
2. In Fig. 8(a), the time-of-day schedule does not only "save little". It
   also exceeds the budget (2.96%), and the text says so.
3. "The test-week busy hour is higher in most squares" came from a
   verification-time analysis of the raw traces, not from the summaries.
   It was removed. The text keeps only the dev-to-test drop shared by
   MPC (0.45–1.70 points) and the oracle (0.44–1.62).
4. A UE transmits "at most 23 dBm" only in power class 3; the text says
   so.
5. "FR3 favored for cell-edge users" in [11] is relative to FR2 only.
6. The co-location finding is attributed to [18] alone.

**Decided by the drafting** (the user's single-panel decision, 10.9): the
controller appears as Algorithm 1. Each of its lines is checked against
the frozen code.

**Open items:**
- the affiliation and acknowledgment;
- the version of TS 38.214;
- the supplementary material referred to in Section IV.A.

### 10.12 Figure 2 in colour (user request, 2026-11-13)

**Figure 2 was grey.** The four interference models were drawn as a grey
ramp, from light grey (no interference) to black (pure pattern). The user
asked for colour on the data marks only, clearly distinguishable.

**Change:** one hue per model, applied to its range line, end ticks, open
5th-percentile marker and filled median, in both the DL and UL groups:
- no interference: aqua `#199e70`;
- beam-averaged: blue `#2a78d6`;
- directional: orange `#eb6834`;
- pure pattern: plum `#9b2f8f`.

Unchanged: the text, tick labels, the F2-share column, the glyph legend
(it explains the glyphs, not the models) and the F2 threshold lines, which
stay neutral grey. Geometry, values and caption are unchanged; the caption
names no colours.

**Why these colours.**
- Checked with the dataviz validator on the white page, all six pairs:
  - worst normal-vision ΔE 21.6 (blue–aqua; floor 15);
  - worst CVD ΔE 8.4 (protan, orange–aqua; target 8);
  - every colour at least 3:1 against white (3.20–6.57).
- Rejected sets:
  - blue, orange, green, violet: blue–violet normal-vision ΔE 10.5;
  - blue, orange, green, red: orange–red ΔE 7.1;
  - any set with green `#008300` and orange: protan ΔE 3.2;
  - the palette aqua `#1baf7a`: contrast 2.82:1, too light for thin
    lines. Its darker palette step `#199e70` is used instead.
- **Colour reuse.** All four colours already appear in the paper's palette,
  for controllers (blue, orange, and aqua in its lighter step `#1baf7a`)
  and for the 10/90 mix (plum, Figure 7). Any four clear colours would repeat palette hues.
  Figure 2 shows neither controllers nor mixes, and each row is named by
  its tick label, so identity never rests on colour. 10.10's rule (mixes
  take plum and bronze only in figures without controller colours) is
  unaffected.

**Verification additions** (`verify_model_figures.py`, now 200 checks). The
verifier recomputes the colour metrics itself (OKLab; Machado–Oliveira–
Fernandes 2009 CVD matrices; WCAG contrast) and reproduces the validator's
21.6 and 8.4. New checks:
- each model is drawn in its registered colour, and the manifest records
  the same colours;
- open 5th-percentile markers and filled medians, both in the row colour;
- the threshold lines stay neutral;
- chroma and lightness band;
- the all-pairs ΔE gates;
- contrast against white.

Figures 1 and 3 are byte-identical. In the manuscript, `numbers.tex` is
unchanged; only Figure 2 and the recorded hashes change.

**Correction (commit 6e3a92b).** `numbers.json` recorded a stale hash of
this protocol. I ran the manuscript verification, then added 10.11, then
committed. The verifier's "numbers.json records the same input hashes"
check would have failed on that commit; no macro value was affected (the
one protocol-derived macro, the 235 m square size, is read from D31, which
10.11 did not change). From now on, protocol entries are written before the
numbers are regenerated and verified, and the verifier is re-run on the
committed tree.

### 10.13 The author's final version transcribed to LaTeX: decisions before the build (2026-11-13)

The author supplied a final version of the paper, `Uplink-Feasible and
Energy-Aware Band Activation.docx` (SHA-256 `3f31a283…d6120f`) with its PDF
(`57a293da…62ff`), and asked for it to replace the content of the LaTeX
draft: same text, equations, tables, figures and references, laid out in
IEEEtran like the draft, with nothing missed.

**What was checked before transcription.**
- Every number in the final version was recomputed from the frozen outputs
  (E1c.3, P3.1, P3.2b/c, P3.3, P3.4). All agree except one: the S1 no-load
  power of the 7.2 GHz cell is 171.565 + 172.027 = 343.59 W, not
  343.60 W (the sum of the two rounded values).
- The method statements were checked against the configs and code: TDD
  52/70 and 16/70; overheads 0.14/0.08; T_TR 50 ms + T_SCell 105 ms; SMTC
  20 ms; traffic grid 0.025 to 5; 6-slot reference; weekday/weekend
  profiles; threshold rule (fewest cells, step up at once, hold H slots);
  time-of-day 06:00–24:00; all-on initial state; pigeonhole bootstrap
  (squares × realizations, 2,000 draws; violation differences over
  squares); shadowing shared by sectors and carriers of a site and
  independent across sites; D_0.5 per site. All hold.
- Figures 2–9 of the final version are Figures 1–8 of `paper/figures/`
  (pixel comparison, mean absolute difference ≤ 0.003). The LaTeX uses the
  vector PDFs. Figure 1 is new: a conceptual illustration supplied by the
  author (`word/media/image1.jpeg`, `deb72d2f…a4cff1`), used unchanged.

**Author decisions (asked before the build; all four as recommended).**
1. Nine small errors are fixed; everything else is word for word:
   343.60 → 343.59 W; eq. (14) gains its minimum over the causal policy
   class Π_causal of the expected sum of interval energies E_t^(h), the
   symbols its own text defines; the stray "t" in eq. (16) is removed;
   O(H|A|2) → O(H|A|²); "It's" → "Its"; the missing "—" in Table 1's note
   is restored; "Fig. 1." → "Fig. 1" mid-sentence; reference [1] gets its
   first author's initial (A. Bazzi); reference [31] (TS 38.101-1, cited
   nowhere) is cited at the 23 dBm UE ceiling in III.B, which keeps the
   author's numbering [1]–[36].
2. Two figure descriptions are corrected. The second sentence of the
   Fig. 3 caption and the first sentence of V.A's second paragraph described
   beam geometry, which Fig. 3 does not show; they now describe its
   percentiles, F2 shares and thresholds. "computational," is removed from
   the last sentence of V.G (Fig. 9 has no timing data).
3. Notation: the violation budget β (clashing with the path gain β of
   eq. (4)) becomes η; the directional weight α_{i,u} of eq. (3) (clashing
   with the forecast quantile level α) becomes κ_{i,u}.
4. References: [30] TR 38.901 V19.5.0 (Sept. 2026), the version used; [35]
   dataset version 1.3, the version fetched; pages added to [6]
   (pp. 21476–21480) and [12] (pp. 1–6). [32] TS 38.214 still has no
   version.

**Transcription rules.**
- Results are macros generated from the frozen outputs, as before;
  every macro renders exactly the author's number (or the corrected
  343.59).
- The text is otherwise the author's, including wording I would have
  changed. Remaining points are reported to the author, not edited:
  - the Fig. 1 image has a typo ("antema"), uses α, β and φ as angles that
    the text does not define, and depicts multipath, which the model does
    not include;
  - IEEE requires AI-generated images to be disclosed in the
    acknowledgments, if Fig. 1 is one;
  - Table III calls the held-out realization indices "seeds";
  - L^O2I is described as building penetration only, but in-car users have
    a car penetration loss.
- IEEEtran conventions replace Word formatting: "Abstract—", "Index
  Terms—", TABLE I–IV in Roman numerals, and automatic figure, table,
  equation and reference numbering. The numbering equals the author's.
- A transcription check compares the LaTeX source, with macros expanded,
  against the text of the .docx, word for word. Every difference must be
  one of the decisions above.

### 10.14 The author's final version in LaTeX: results (2026-11-13)

`paper/manuscript/main.tex` is now the author's final version: 7 sections,
27 equations, Tables I–IV, Algorithm 1, Figures 1–9 and references
[1]–[36], word for word except the decisions of 10.13 and the one below.

**One more error, found during the build (same policy as decision 1).**
V.E said that at 26/74 the DL-only rule "remains within the 1% budget
according to its own downlink evaluation". This holds for every scenario at
g ≤ 2, which covers the nine H2 cells that Fig. 7 shows. At g = 2.5–3.5,
under S1 and S2, its own check reaches 1.048–1.076%. The sentence now reads
"Under the 26/74 traffic composition and g ≤ 2, …". The verifier checks
both halves: the claim at g ≤ 2, and that the qualification is needed.

**Layout (author decision).**
- **First build: two columns** like the draft, with figures and tables as
  top-of-page floats. The author found them displaced: Fig. 1 inside the
  contribution list, and Fig. 9 after the Conclusion.
- **Second attempt: still two columns.** Single-column floats were pinned
  after their paragraphs, and full-width floats moved to pages of their
  subsections. Every figure stayed within one page of its paragraph, but Fig. 5
  still followed the V.D heading, and Figs. 6 and 7 shared a page.
- **Why two columns cannot work here.** Subsections V.B–V.G hold a third
  to half a page of text each, while their figures are 2.7–6.3 in tall. A
  full-width figure can only sit at a page top or bottom, so no two-column
  layout keeps every figure after its paragraph and before the next
  heading.
- **Strips tried and reverted.** Full-width strips (`cuted`) placed inline
  ran off the page and displaced text between consecutive strips.
- **Decision: single column, like the .docx** (IEEEtran `journal,onecolumn`,
  11 pt, 24 pages).
  - Every figure, table and the algorithm is pinned with [H] directly after
    the paragraph the .docx places it after.
  - The full-width figures are shown at their natural width (7.16 in).
    Figs. 2 and 3 are shown at 4.5 in and Fig. 1 at 0.78 of the line.

**How placement is checked.** `\label{anchor:<float>}` marks, in main.tex,
the end of the paragraph each float follows in the .docx.
`scripts/paper/float_placement.py` requires every float to be:
- pinned;
- directly after its anchor in the source;
- on its anchor's page, or at the top of the next page when the rest of
  that page cannot hold it (as in Word).

It also requires numbering in page order. The verifier also places each
float at its anchor when checking that references are numbered in order of
first citation.

**Transcription check.** `scripts/paper/compare_author_final.py` compares
main.tex, with its macros expanded, against the .docx: 12,669 against
12,704 tokens.
- The running text is compared in order.
- The 14 floats are compared block by block in the .docx order.
- Each formula is reduced to one token holding its symbols, so a changed
  symbol, index or number inside a formula shows as a difference.
- All 35 differences are documented. They are the decisions of 10.13, the
  error above, and IEEEtran formatting:
  - "Section IV-D" cross-references;
  - table captions in small caps;
  - g set as a math symbol in two places;
  - a final period at the end of the Table IV note.

**Numbers.** 189 macros render every result the text states. The generator
writes only the macros the text uses; 70 more are computed and not written.
The verifier recomputes each one by its own route where one exists, for
example:
- the mean of active groups from the pattern shares;
- x* from min(x*_DL, y*_UL/ρ);
- the unavoidable share as total minus avoidable;
- the H2 points as the difference of the two shares.

**References.** The bibliography is now the author's list in main.tex
(thebibliography); `references.bib` is removed.
- Each entry with a Crossref or arXiv record agrees with it: DOI or arXiv
  number, year, volume, issue, pages, title and authors.
- The standards, reports, white paper and dataset entries read as decided.

**Verification.**
- `verify_manuscript.py`: 140 of 140 checks pass. They include the claims
  of the new text against the data, the configs and the frozen code. Among
  them:
  - shadowing shared by the sectors and carriers of a site;
  - weekday and weekend profiles;
  - the threshold rule;
  - the all-on initial state;
  - the pigeonhole bootstrap;
  - the 1,024-RF-chain configuration keeping fewer groups active;
  - the oracle's zero violation on the development model.
- **Mutation test.** All 9 planted errors were caught:
  - a changed word;
  - a changed number;
  - a typed result;
  - a changed formula;
  - a deleted sentence;
  - a changed caption;
  - swapped references;
  - Fig. 9 moved after the conclusion;
  - Fig. 5 moved into V.D.
- The figure verifications are unchanged: 95/95 and 200/200.

**Not committed.** The author's .docx and PDF stay untracked in the
repository root. The transcription report records the .docx hash, and the
verifier reruns the comparison whenever the .docx is present.

**Remaining points for the author:**
- the affiliation, the contact details and an acknowledgment;
- the version of TS 38.214;
- the Fig. 1 image:
  - a typo ("antema");
  - angles α, β and φ that the text does not define;
  - multipath that the model does not include;
  - if it is AI-generated, IEEE requires a disclosure in the
    acknowledgments;
- "Held-out realization seeds 100–199" in Table III: these are realization
  indices;
- L^O2I is described as building penetration only, but in-car users have a
  car penetration loss;
- the supplementary material.
