# E1.5 packet-level validation protocol

Version 1, 2026-11-07. Written after the E1.4 results and an independent audit.
Each stage is pre-registered in its own commit (config, code, tests, acceptance
criteria) BEFORE its first run. Failed runs are kept. Tolerances are never
changed after results are seen. A stage starts only after the previous stage
passed.

## 1. Purpose

E1.1-E1.4 are an analytic model: geometry-conditioned TR 38.901 losses, ideal
array gains and a TR 36.942 rate mapping. E1.5 tests how much of the E1.4
picture survives a standard-conformant NR stack (5G-LENA v5.1 on ns-3.48,
verified build `d083a17a` / NR `cedceadd`). Agreement is not assumed. A
quantified disagreement, with its cause, is also a result.

## 2. Audit points and decisions

| Audit point | Verdict | Action |
|---|---|---|
| The DL/UL gap is not 19 dB once UE receive arrays are used | Correct. Only a chat summary claimed "the 19 dB gap stays"; the committed E1.4 pre-registration and results already give 25.02 dB (A, B) and 34.02 / 37.03 dB (C). The gNB array alone leaves the gap at 19.00 dB (S1) | Never quote 19 dB outside the isotropic/1-Rx case |
| Validate the realised beamforming gain, not 10 log10(N) | Correct. 5G-LENA forms one analog beam per port over the panel elements; it is not N independent ports | Stage E1.5.2; regime A first, then B; C only after a cost check |
| Keep link closure separate from the 10-user service level | Correct, and sharper than stated: F2 assumes TIME sharing, but NR UL is OFDMA, where a power-limited UE concentrates 23 dBm on about N/10 PRBs (up to 10 dB more SNR per PRB). F2 may overstate UL failure | Stage E1.5.0 adds the analytic F3 'OFDMA share' surrogate; stage E1.5.4 tests F2 vs F3 with TDMA and OFDMA schedulers |
| TR 38.901 Release 19 (7-24 GHz) | Verified: V19.5.0 (2026-09) exists. Of the inputs of E1.2-E1.4, only the IRR glass loss changed (high-loss buildings); UMa fast-fading LSPs also changed | E1.5.0 part A reruns E1.4 with V19.5.0; E1.5.2 states that ns-3.48 implements the pre-Release-19 fast-fading tables |
| 27 positions x 200 realizations are not 5,400 independent observations | Correct | Positions are the independent units: uncertainty by resampling positions (stratified by distance bin) in every E1.5 comparison and in E1c |
| Keep the installation intact; own the experiment code | Agreed | Program source in `ns3/scratch/cmwave/`, copied into `~/6g-external/ns-3-dev/scratch/cmwave/` with its SHA-256 checked before every run; no NR or ns-3 file is edited; runs write only to their own output directory |

## 3. Facts verified in the 5G-LENA source (NR `cedceadd`)

| Item | Default | Consequence |
|---|---|---|
| RB count | floor(BW (1 - RbOverhead) / (12 SCS)), `RbOverhead` = 0.04: 53 RBs at 20 MHz | Set `RbOverhead` so that 20 MHz -> 51 and 100 MHz -> 273 RBs (TS 38.101-1); otherwise the DL SNR is biased by 10 log10(53/51) = 0.17 dB |
| gNB power allocation | `UniformPowerAllocUsed` | Set `UniformPowerAllocBw` (power over all RBs, as in the Python DL model) |
| UE power | `TxPower` = 2 dBm, `EnableUplinkPowerControl` = false | Set 23 dBm; the UE spreads it over its allocated RBs (`UniformPowerAllocUsed`), as in the Python UL model |
| Noise | -174 dBm/Hz + NF over each 360 kHz RB | Identical to Python |
| Per-TB SINR | `RxPacketTraceUe/Gnb`: m_sinr = LINEAR mean over the TB's RBs; m_rbAssignedNum, m_mcs, m_tbSize, m_corrupt, m_tbler | The UL comparison uses the recorded RB count |
| Custom channel | `BandwidthPartInfo::SetChannel`; NR runs with path loss only and no fading model (as in `test/ul-scheduling-test.cc`) | Stage 1 imposes our losses with `MatrixPropagationLossModel` |
| ns-3 UMa below 6 GHz | 7 dB shadowing for every link; legacy O2I model (hazard 11) | Shadowing and building loss are always disabled in ns-3; losses are imposed |

## 4. Stages

### E1.5.0 Analytic pre-checks (Python)
Config `configs/e1/e1_5_0_surrogates.json`.
- **Part A:** E1.4 rerun with TR 38.901 V19.5.0 building materials.
- **Part B:** F3 'OFDMA share' surrogate (each of 10 users holds floor(N_PRB/10) PRBs continuously; UL per-user SE >= 0.15) next to F2 (time share).

F3 is a prediction; E1.5.4 decides between F2 and F3.

### E1.5.1 Deterministic received power and SNR (first acceptance milestone)
**Setup:**
- 5G-LENA and Python, same per-link loss, transmit power, occupied bandwidth and noise figure.
- Isotropic 1x1 antennas, no fast fading, no shadowing, no interference.
- 20 MHz, 30 kHz SCS, 51 RBs, M.2412 Configuration A powers, at 3.5 and 7.2 GHz.
- Losses: the 27 E1.2 median losses per carrier plus a sweep covering every SNR regime.
- DL and UL traffic, so that full and partial RB allocations occur.

**Acceptance, fixed now:**
- (V1) DL: every received data TB has |10 log10(m_sinr) - SNR_python| <= 0.01 dB.
- (V2) UL: every TB has |10 log10(m_sinr) - SNR_python(n = m_rbAssignedNum)| <= 0.01 dB.
- (V3) The RB count reported by ns-3 equals 51.
- (V4) The DL received power per RB (`RxDataTrace`) is within 0.01 dB of Python.
- (V5) Every link whose Python SNR is >= -5 dB in a direction delivers at least 10 TBs in that direction. Links below are reported, not dropped.

**Not tested here:** MCS, BLER, throughput (stage 3).

### E1.5.2 Antenna realisation
**Setup:**
- TR 38.901 UMa fast fading in ns-3 (`ThreeGppSpectrumPropagationLossModel`).
- Channel condition fixed to each link's geometric state.
- Large-scale loss still imposed (no ns-3 path loss or shadowing).

**Arrays:** regime A (8x16 at both carriers), then B (16x32 at 7.2 GHz); UE 4 elements.

**Beamforming:** direct-path (closest to the E1.4 steering) and codebook cell-scan.

**Measured:**
- Realised gain = ns-3 SNR - isotropic Python SNR, compared with the E1.4 ideal gain.
- Results are split by LOS/NLOS, carrier and regime.

Tolerances are fixed after E1.5.1, before the first E1.5.2 run.

**Limitations to report:**
- ns-3.48 fast-fading tables predate Release 19.
- Regime C (1024 elements, 200 MHz) runs only after a cost test on this machine.

### E1.5.3 Single-UE packet level
UL and DL traffic at increasing offered loads. Recorded:
- delivered packets, throughput and delay;
- MCS, allocated RBs, HARQ retransmissions, TBLER, SINR.

Packets still queued at the end of the simulation are counted separately from radio losses (as in E0). Outputs:
- the empirical SNR-to-throughput curve of 5G-LENA against the TR 36.942 mapping (alpha, Thr_max);
- a calibrated mapping, if they differ, which then replaces the E-UTRA-era values in later stages.

### E1.5.4 Ten-UE sector
10 UEs per sector (M.2412), full buffer, TDMA and OFDMA schedulers. Per-user throughput distributions and 5th percentiles test whether F2 (time share) or F3 (OFDMA share) predicts per-user service feasibility.

### E1.5.5 Disagreement analysis
For each E1.4 class:
- which classifications survive E1.5;
- which change, and why: beamforming gain, coding/HARQ overhead, power control, scheduling, fading.

Reported by frequency, distance, LOS/NLOS, antenna regime and indoor state.

## 5. Results log

### E1.5.0: PASSED (2026-11-07)
Pre-registration `cefc482`; run from that commit (23 s); checks S1-S6 pass.
Files: `results/phase2/e1_5_0_surrogates/`.
- S1: the V16.1.0 evaluation reproduces every fraction of the committed E1.4
  summary exactly.
- S2: V19.5.0 changes no fraction outside the high-loss states (425 changed
  fractions, all in `indoor_high`).
- S5: no record is UL-feasible under F2 but not under F3; zero violations of
  the proved bound.

Part A, TR 38.901 V19.5.0. High-loss wall loss is 27.50 / 32.43 dB (V16.1.0:
26.85 / 31.46 dB).
- In high-loss buildings K4 rises by 1.2-1.4 points and K1 falls by
  1.6 points (A, B, C, F2).
- In the M.2412 mix every class moves by 0.3 points or less.
- Decision, by the rule fixed before the run: V16.1.0 remains the frozen
  baseline of E1.2-E1.4, and V19.5.0 is the reference from E1c onward.

Part B, multiple-access surrogate (M.2412 mix, V16.1.0):

| Regime | UL feasible 3.5 / 7.2 GHz, F2 -> F3 | K1, F2 -> F3 | K2, F2 -> F3 | K3, F2 -> F3 | K4 |
|---|---|---|---|---|---|
| R0 isotropic | 0.349 / 0.243 -> 0.481 / 0.380 | 0.243 -> 0.380 | 0.106 -> 0.096 | 0.150 -> 0.023 | 0.501 |
| A equal elements | 0.702 / 0.592 -> 0.832 / 0.732 | 0.592 -> 0.732 | 0.110 -> 0.100 | 0.206 -> 0.076 | 0.092 |
| B equal aperture | 0.702 / 0.682 -> 0.832 / 0.808 | 0.682 -> 0.808 | 0.020 -> 0.023 | 0.246 -> 0.116 | 0.052 |
| C vendor configuration | 0.598 / 0.534 -> 0.737 / 0.678 | 0.534 -> 0.678 | 0.064 -> 0.060 | 0.359 -> 0.219 | 0.044 |

Interpretation:
- The service-level class "DL works but no UL meets the service level" (K3)
  depends strongly on the multiple-access assumption. Under OFDMA sharing it
  shrinks by 12.7-14.0 points in every regime.
- The cross-band class K2 changes by at most 1 point.
- The multiple-access assumption is therefore the largest open uncertainty
  in the E1.4 service-level results: larger than the antenna regime or the
  TR 38.901 version. E1.5.4 must decide it with real NR schedulers.
- Until then, K3 under F2 is an upper bound, and K3 under F3 is the analytic
  value for full-buffer equal-share OFDMA.

### Build incident before E1.5.1 (2026-11-07): whole-tree rebuild, stack re-verified

What happened:
- Installing the first project program re-ran CMake on the existing cache.
- That regenerated the precompiled-header target, so ninja recompiled
  1,154 objects (24 min, -j4) before linking the program (hazard 12,
  `docs/PHASE_2_RADIO_PROTOCOL.md` section 3).
- The install script's dry-run guard did not catch it: a ninja dry run stops
  at "Re-running CMake" and lists nothing.
- Log: `results/verification/ns3_full_rebuild_20261107_hazard12.log`.

What did not change:
- Sources: ns-3 `d083a17a` and NR `cedceadd`, tracked files unmodified.
- The CMake cache and the "default" build profile.

Re-verification:
- The default `cttc-nr-demo` was rebuilt alone, through the new guard. Its
  output is byte-identical to the Phase-1 reference
  `results/verification/cttc-nr-demo_after_mr2929.txt` (SHA-256
  `8005997bb9313c98e39788426b2b5f40ea2948b1cbab8217c15c6f7e3a97cc4d`).

Prevention, `scripts/ns3_guarded_build.sh`:
1. An exact, side-effect-free glob check before ninja starts. A mismatch
   means a reconfigure, so the build is refused.
2. A watcher that kills ninja's whole process group if CMake re-runs or an
   unexpected object is reported. Tested: no compiler survives the abort.

`scripts/e1_5_install_ns3_program.sh`:
- uses the guard;
- needs `ALLOW_RECONFIGURE=1` for a new program file;
- now builds the E1.5 program with exactly 1 object compiled.

Not re-run: the 121 regression suites. Their test libraries were not
rebuilt, and a rebuild of the libraries from the same sources and options
does not change the code under test.

### E1.5.1: FAILED as pre-registered (coverage, TDMA only); SNR identity established (2026-11-07)

Run history (all evidence kept in `results/phase2/e1_5_1_deterministic/`):

| Run | Code | Outcome |
|---|---|---|
| 1 | d67997d | Crashed writing validation.json (numpy.bool_). No result. Raw files showed instrument defects: DL starved by the all-flexible TDD default, RNTI churn, UE RB count read before attach |
| amendment 1 | 2d8cd06 | TDD DDDSU, RLF off, time-resolved RNTI attribution, RB count after attach, 1.0 s traffic |
| 2 | 2d8cd06 | Aborted at start: literal quotes around the TDD pattern; fixed in 2534cb8 |
| 3 | 2534cb8 | V0/V1/V3/V4 PASS; V2 FAIL (TDMA), V5 FAIL (all). UL overload (36 x 2 Mbit/s) starved UEs, which fell back to random access |
| amendment 2 | 75d3eee | UL load 50 kbit/s per UE (100-byte packets); stop rule: another V2/V5 failure keeps the stage FAILED |
| 4 (final) | 75d3eee | OFDMA runs (3.5 and 7.2 GHz): V0-V5 all PASS. TDMA runs: V5 FAIL in both, V2 FAIL at 7.2 GHz |

Verdict: **FAILED** under the pre-registered criteria (V5 in both TDMA runs,
V2 in `f7p2_tdma`). Per the stop rule, no further amendment is made to make
it pass.

What run 4 establishes (every compared TB, all four runs; counts corrected by
erratum 1 below):
- **DL:** 31,100 TBs compared. |5G-LENA SNR - Python SNR| <= 3.1e-14 dB, and
  the received power per RB agrees within 2.9e-14 dB.
- **UL:** 24,732 distinct TBs compared (46,251 trace rows), |error| <=
  3.6e-14 dB. The OFDMA runs cover 32-36 distinct allocation sizes from 1 to
  51 RBs, so the "UE power spread over the allocated RBs" model is confirmed
  at every allocation size.
- **Configuration readback:** 51 RBs at the gNB and every UE; powers and
  noise figures as configured.
- **Coverage:** at both carriers, every link with SNR >= -5 dB is compared
  in both directions in the OFDMA runs (V5 PASS there).
- **Range:** losses 72-160 dB, SNR from +66 dB down to -21.6 dB. Links
  below -5 dB still deliver DL TBs.

Consequence: the E1.3/E1.4 power, noise and SNR model is exactly the one
5G-LENA uses for a prescribed loss. Any E1.4 vs 5G-LENA difference in later
stages must come from fading, beamforming, MCS/HARQ or scheduling, not from
power or noise accounting.

Why TDMA fails:
- With 36 UEs, 5G-LENA's `NrMacSchedulerTdmaRR` starves a fixed group of UEs
  in UL even at 1.8 Mbit/s total offered load. At 7.2 GHz, ue00-ue14 get 0-4
  distinct UL TBs although their 1-PRB UL SNR is 29-58 dB, while ue20 gets
  664 (corrected by erratum 1; first reported as 0-8 and 1,328 trace rows).
- The starved UEs then fall back to random access (2 TBs with RNTIs 135/136
  without RRC establishment -> V2).
- `NrMacSchedulerOfdmaRR` serves every UE.
- This is recorded as an open observation, not yet as a 5G-LENA defect: the
  cause is not identified. Candidate: symbol-per-beam allocation with one
  beam per UE position.

Implications:
- E1.5.2 SNR and beamforming measurements use `NrMacSchedulerOfdmaRR`.
- Before E1.5.4 uses TDMA results, the TDMA UL starvation must be explained
  (a dedicated diagnostic varying UE count, positions and beam grouping).
- The DL MCS-vs-SNR data in `summary.json` (e.g. MCS 28 at >= ~20 dB, 14 at
  7.8 dB, 4 at -1.6 dB) are an input to E1.5.3, not a result of E1.5.1.

#### E1.5.1 erratum 1 (2026-11-07): trace rows are packets, not TBs

Found while starting the TDMA diagnostic, after the results commit `ed58ddc`.

Defect:
- `NrSpectrumPhy::ProcessReceivedPacketBurst` (NR `cedceadd`,
  `nr-spectrum-phy.cc` lines 1806-1846) fires `RxPacketTraceGnb` /
  `RxPacketTraceUe` once per non-empty packet of the received burst, each
  time with the fields of the TB that carried it.
- A UL TB that carries two MAC packets (for example data and a buffer status
  report) therefore appears twice, with identical fields. 5,591 + 5,799 +
  4,903 + 5,226 UL TBs appear twice in run 4; no DL TB does.
- The analysis counted rows. That inflated the UL TB counts and the UL TB
  counts used by V5.

Correction:
- `scripts/e1_5_1_deterministic.py` keeps one row per TB, keyed by
  (RNTI, time) in UL and (UE, RNTI, time) in DL. A UE has at most one TB per
  slot and direction at rank 1.
- Rows that share a key must agree in every field, otherwise the analysis
  stops. None disagreed.
- Two new tests cover this.
- Run 4 was re-analysed with `--reanalyse`, without a new simulation. Every
  raw file was first checked against its SHA-256 in the run-4 manifest.
- The first-reported files are kept: `validation_run4_as_first_reported.json`
  and `summary_run4_as_first_reported.json.gz`. `manifest.json` records the
  re-analysis.

Effect:
- **No pass/fail result changes.** The verdict stays FAILED (V5 in both TDMA
  runs, V2 in `f7p2_tdma`). The OFDMA runs still pass V5: every required UE
  has at least 119 (7.2 GHz) or 161 (3.5 GHz) distinct UL TBs, and at least
  28 DL TBs.
- One V5 detail changes: at 3.5 GHz the TDMA UL failures are ue07-ue10, not
  ue08-ue10 (ue07 has 7 distinct UL TBs, logged as 14 rows). At 7.2 GHz the
  list is unchanged (ue00-ue14, sweep_130dB, sweep_140dB).
- Error bounds are unchanged: a duplicate row has the same SNR as its TB.
- UL TBs compared: 24,732, not 46,251. Per run: 5,881 / 7,068 / 5,274 / 6,509
  (f3p5_tdma / f3p5_ofdma / f7p2_tdma / f7p2_ofdma).
- The run-3 UL counts quoted in amendment 2 of the config (13,312 / 23,287 /
  12,923 / 17,868) are trace rows, not TBs. The config is not edited: its
  SHA-256 is pinned in the run manifests.

Rule from now on: every E1.5 analysis counts TBs by key, never by trace rows.

Observation for E1.5.4: in DL, `NrMacSchedulerOfdmaRR` gave the whole band
to one UE in every DL slot (1,201 of 1,201 TBs at 51 RBs at 3.5 GHz). Its DL
comparator rotates an RNTI deque, so DL service rotates across slots, a
time share. In UL the same scheduler compares RBGs assigned in the current
slot and splits the band within each slot.

### E1.5.1-D: TDMA UL starvation diagnostic (PRE-REGISTERED 2026-11-07; all predictions held)

Config `configs/e1/e1_5_1d_tdma_diagnostic.json`, script
`scripts/e1_5_1d_tdma_diagnostic.py`, tests `tests/test_e1_5_1d_diagnostic.py`.
Committed before the first diagnostic run.

Why now: E1.5.1 failed. The stop rule holds the sequence until the cause is
found and recorded. E1.5.4 also needs a time-share scheduler that actually
shares.

**Source facts (NR `cedceadd`):**
- UL TDMA RR ranks UEs by the RBGs assigned in the current slot only, so it
  keeps no UL memory across slots. DL TDMA RR rotates an RNTI deque.
- The TDMA loop gives one full-band symbol at a time to the first UE in a
  stable-sorted vector. The vector follows the iteration order of `m_ueMap`,
  an `unordered_map` keyed by RNTI. DirectPath beamforming puts every UE in
  one beam.
- A scheduling request only marks a 17-byte bootstrap grant as owed. That
  grant is served by the same ordered data loop.
- UL HARQ retransmissions are scheduled before new data.

**Hypotheses:**
- **H1, saturation.** TDMA grants span the full band, so the UE's 23 dBm is
  spread over 51 RBs (full-band SNR = 1-PRB SNR - 17.1 dB). Weak UEs get
  MCS-0 TBs of at most 19 bytes that often fail, stay backlogged, and fill
  every UL slot.
- **H2, fixed order.** Backlogged UEs are served in one fixed order, not in
  rotation.
- **H3, starvation.** UEs late in that order get no UL symbol, not even
  their bootstrap grant, whatever their link quality.

**Runs** (unchanged program, E1.5.1 common settings):

| Tag | Carrier | Scheduler | UEs | Scheduler log |
|---|---|---|---|---|
| f7p2_tdma_rep, f3p5_tdma_rep | 7.2 / 3.5 GHz | TdmaRR | all 36 (replicates run 4) | yes |
| f7p2_tdma_feasible, f3p5_tdma_feasible | 7.2 / 3.5 GHz | TdmaRR | TDMA-feasible only | no |
| f7p2_tdmapf, f3p5_tdmapf | 7.2 / 3.5 GHz | TdmaPF | all 36 | no |

Definitions:
- **TDMA-feasible:** full-band UL SNR >= -5 dB. That is 25 UEs at 7.2 GHz
  and 26 at 3.5 GHz.
- **Steady state:** scheduling or air time in [0.5, 1.4) s.
- **Served window:** a 100 ms window with at least one non-corrupt distinct
  UL TB.

**Predictions:**
- **P0:** both replications reproduce every run-4 output file byte for byte.
- **P2:** within each interval between scheduler UE insertions, all
  steady-state UL data events are consistent with one total order
  (acyclic precedence graph).
- **P3:** in at least 95% of steady-state UL data events, every available
  symbol is assigned.
- **P4:** every TDMA-feasible UE that failed V5 in run 4 meets two
  conditions:
  - it is backlogged (UL buffer > 0, or a bootstrap grant owed) in at least
    90% of steady-state UL scheduling events;
  - it gets a UL symbol in at most 1% of steady-state UL data events.
- **P5:** with only TDMA-feasible UEs, every UE is served in all 9 windows.
- **P6:** under TdmaPF with all 36 UEs, every TDMA-feasible UE is served in
  all 9 windows.

**Decision rules:**
- P2-P4 hold at both carriers: H1-H3 are the recorded cause, and the stop
  rule is satisfied. E1.5.1 stays FAILED.
- P6 holds at both carriers: TdmaRR is not used for UL in any later stage.
  E1.5.4 realises the time share with TdmaPF; its own pre-registration fixes
  the OFDMA counterpart.
- Any failed prediction is reported as failed and analysed. Nothing is
  re-run with changed thresholds.

V5 lesson: V5 used the 1-PRB UL SNR for both schedulers. A TDMA grant always
spans the full band, so for TDMA the full-band SNR applies. The E1.5.1
verdict does not change: the starved UEs have full-band UL SNRs of
12.1-41.0 dB (7.2 GHz) and 18.4-39.5 dB (3.5 GHz). Every later coverage
criterion states the SNR that fits its scheduler.

Instrument check, done before the pre-registration commit:
- One 3-UE, 0.6 s TdmaRR run, not a diagnostic configuration, fixed the log
  levels and confirmed the line formats and volume.
- The parser reproduces the `grep` counts of that log exactly (480 UL
  scheduling events, 58 UL data events, 329 symbols).

#### E1.5.1-D results: ALL PREDICTIONS HELD (2026-11-07)

Run from the pre-registration commit `8914e47` (4 min 39 s). Files:
`results/phase2/e1_5_1d_tdma_diagnostic/`; scheduler logs in `raw/`
(git-ignored, SHA-256 in the manifest).

| Prediction | 7.2 GHz | 3.5 GHz |
|---|---|---|
| P0 replication | all 8 output files byte-identical to run 4 | identical |
| P2 fixed order | 3 epochs (scheduler insertions of random-access RNTIs 135-139 at 1.057 / 1.067 s); 445 + 8 + 266 data events; no cycle | 1 epoch, 720 data events, 126 precedence pairs, no cycle |
| P3 saturation | 719 of 719 steady-state UL data events assign every symbol | 720 of 720 |
| P4 backlog without service | ue00-ue14: backlogged in 100% of 720 UL scheduling events; given a UL symbol in 0 of 719 data events (ue14: 1) | ue07-ue10: backlogged in >= 99.86%; given a symbol in 7 / 0 / 0 / 0 of 720 (ue07: 0.97%, just under the 1% threshold) |
| P5 TDMA-feasible UEs only (TdmaRR) | 25 of 25 UEs served in all 9 windows, >= 64 UL TBs each | 26 of 26, >= 64 each |
| P6 TdmaPF, all 36 UEs | 25 of 25 TDMA-feasible UEs served in all 9 windows, >= 62 UL TBs each | 26 of 26, >= 64 each |

The non-TDMA-feasible UEs in run 4 (sweep_130dB, sweep_140dB at 7.2 GHz)
were excluded from P4 as pre-registered.

Order observed: the steady-state first-assignment order follows the
iteration order of the scheduler's UE map, close to descending RNTI. At
3.5 GHz it ends with `..., ue04, sweep_150dB, ue05, ue06, ue07`; ue08-ue10
never receive a symbol. The full orders are in `summary.json`.

Verdict:
- Hypotheses H1-H3 are the recorded cause of the E1.5.1 TDMA failure:
  1. Full-band TDMA grants let UEs that full-band TDMA cannot serve saturate
     every UL slot.
  2. `NrMacSchedulerTdmaRR` keeps no UL memory across slots, so backlogged
     UEs are served in one fixed order.
  3. UEs at the end of that order get no UL symbol, not even their bootstrap
     grant, although their full-band UL SNR is 12-41 dB.
- The stop rule is satisfied. E1.5.1 itself stays FAILED as pre-registered.
- This is a property of the UL path of 5G-LENA's TDMA round-robin
  scheduler under saturation. It is not a radio-model error: SNR identity
  holds for every TB that is scheduled.

Further observations (reported, not tested):
- UL symbols spent on HARQ retransmissions: 47% (7.2 GHz) and 30% (3.5 GHz)
  under TdmaRR with all 36 UEs, and 0.3% / 1.7% with TDMA-feasible UEs
  only.
- Under TdmaPF with all 36 UEs, the share is 51% / 46%. Almost all of it
  comes from UEs whose full-band TBs fail 99-100% of the time (7.2 GHz:
  ue24-ue26 and sweep_140dB-160dB). Those UEs never deliver a TB in most
  windows, yet occupy about half of the UL symbols.

Consequences, by the pre-registered decision rules:
- `NrMacSchedulerTdmaRR` is not used for UL in any later stage.
- E1.5.4 realises the time share with `NrMacSchedulerTdmaPF`. Its
  pre-registration fixes the OFDMA counterpart.
- E1.5.4 must also state how UEs that are not feasible in a direction are
  handled. Under full-band TDMA they consume about half of the UL symbols
  without delivering data. Admission or link-adaptation rules belong in its
  design and cannot be left to defaults.
- E1.5.2 measures DL SNR, where the TDMA and OFDMA RR schedulers rotate
  correctly. It may use either one.

## 6. Order and stop rules
E1c (area maps) and the energy controller do not start until E1.5.1-E1.5.4 have passed or their disagreements are explained. A stage that fails stops the sequence until the cause is found and recorded.
