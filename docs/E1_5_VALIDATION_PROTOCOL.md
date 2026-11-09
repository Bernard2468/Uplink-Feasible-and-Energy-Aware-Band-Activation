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

Split on 2026-11-07, after reading the NR and ns-3 beamforming code (section 5):
- **E1.5.2-0, conventions.** Deterministic gains vs E1.4, channel
  normalisation, and the transmit/receive role of NR's DirectPath vector
  under ns-3 #1314. Also tests that NR TB SINR equals the channel-level
  computation.
- **E1.5.2-1, gNB realised gain.** Measured with the instrument validated
  by E1.5.2-0. Pre-registered after it.
- **E1.5.2b, UE receive gain.** Needs a matched UE combiner if E1.5.2-0
  confirms the receive-side mismatch.

The original plan below still applies to E1.5.2-1.

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

### E1.5.2-0: antenna and beamforming conventions (PRE-REGISTERED 2026-11-07; PASSED in run 3 after amendments 1-2)

Config `configs/e1/e1_5_2_0_conventions.json`, script
`scripts/e1_5_2_0_conventions.py`, tests
`tests/test_e1_5_2_0_conventions.py`. Committed before the first run.

**Why.** E1.4 assumes ideal steering of the gNB array toward the
geometric UE direction. Before measuring how much of that gain survives the
TR 38.901 channel, the instrument itself must be right. Reading the code
raised one specific doubt:
- ns-3 commit `80c131f7f` (issue #1314, in our tree) conjugates the
  receive-side weights: the effective channel is w_u^H H w_s.
- NR's `CreateDirectPathBfv` returns the same e^{-j2π r·d}/√N form for the
  gNB and the UE. NR updated only its realistic beamforming for #1314.
- By that reading, DirectPath is matched on the transmit side "s" of the
  stored channel matrix and anti-matched on the receive side "u".
- Which antenna is "s" is set by the first direction in which the pair's
  channel is generated.
- The large-scale parameters do not depend on it: ns-3 assigns them in
  node-ID order, and the gNB is node 0 in both new modes.

**Instrument.** Two new modes of the project program; mode `link`
(E1.5.1) is unchanged, which check R0 verifies:
- `chan`: computes the gNB array gain in ns-3's TR 38.901 UMa channel
  (`ThreeGppChannelModel` and `ThreeGppSpectrumPropagationLossModel`, the
  objects 5G-LENA uses), without the NR stack. It uses 20 co-located UE
  copies per link, which are independent realizations.
- `nrfading`: the NR stack with the same fast fading, the imposed E1.2
  loss and one gNB sector. After the run it recomputes every link's gain on
  the same channel objects and records the gNB's role.

Geometry: the 27 E1.1 links, the E1.4 serving sector, conditions from
E1.1, all O2O. UE: one isotropic element.

**Checks** (thresholds fixed now):

| Check | Requirement |
|---|---|
| R0 | mode `link` rerun of E1.5.1 `f3p5_ofdma` byte-identical to run 4 |
| A0 | ns-3 angles = Python within 1e-6 deg; DirectPath transmit-side array factor = 10 log10 N within 1e-6 dB; element + array gain = E1.4 gain within 0.01 dB (regimes A and B) |
| N0 | isotropic gNB and UE: mean linear gain within ±0.25 dB of 0 dB, LOS and NLOS separately, both carriers |
| B0.0 | every realization reports the requested gNB role |
| B0.1 | role s, DirectPath: median LOS deficit (E1.4 gain - realised) <= 2 dB |
| B0.2 | role u, conjugated DirectPath: median LOS deficit <= 2 dB |
| B0.3 | role u, DirectPath: median LOS [realised(s) - realised(u)] >= 3 dB |
| V0 | every NR DL TB: SINR = SNR_iso + gain recomputed on the same channel, within 0.01 dB; gNB is "s" for every link; >= 10 TBs per UE; frequency read back |

B0 is evaluated at 3.5 and 7.2 GHz (regime A). V0 runs regimes A (3.5 and
7.2 GHz) and B (7.2 GHz), one NR run per sector.

**Decision rules:**
- R0, A0, N0 and V0 pass: E1.5.2-1 may use mode `chan`, with NR spot
  checks.
- B0.1-B0.3 hold: the receive-side mismatch is established. Every later
  configuration states which side each array is on, UE arrays use a
  matched combiner, and the finding is written up for the 5G-LENA
  maintainers. Filing it is the user's decision.
- B0.3 fails: the code reading was wrong; recorded; no compensation is
  used.
- R0, A0, N0 or V0 fails: stop and diagnose.

Instrument checks done before the commit (declared in the config):
- the program was rebuilt (1 object, no warnings);
- mode `link` reproduced run 4 byte for byte;
- a synthetic 2-link smoke run of both modes passed.

The first `nrfading` smoke run aborted with an ns-3 assertion. My condition
model gave UE-UE pairs an order-dependent condition. It was fixed before
the commit: UE-UE pairs now get a fixed NLOS/O2O condition.

Amendment 1 (after run 1, instrument only):
- **What happened.** Run 1 (`6dd5b6a`) stopped at the first `chan` run.
  `UniformPlanarArray` accepts `BearingAngle` only in [-π, π], and the
  program passed the 270° sector as 4.71 rad. The smoke runs had used only
  0°, 45° and 90°.
- **What was evaluated.** Only R0, which passed.
- **The fix.** Bearings are wrapped to [-180°, 180°): 270° becomes -90°,
  the same orientation.
- **What did not change.** All checks and thresholds.
- **Evidence.** `results/phase2/e1_5_2_0_conventions/run1_failure.txt`.

Amendment 2 (after run 2):
- **What happened.** Run 2 (`58872cc`) passed R0, A0, N0 and B0.0-B0.3,
  and failed V0 in all 9 NR runs, with errors up to 35-67 dB.
- **Diagnosis** (`scripts/e1_5_2_0_run2_v0_diagnostic.py`):
  - The 217 DL TBs that span the full band and are alone in their slot
    match the channel-level gain within 3.2e-14 dB.
  - The other 4,282 are OFDMA TBs co-scheduled in the same symbols on
    partial bands. Their errors span -53.7 to +13.2 dB, and many decode at
    MCS 28 although their reported SINR is about 50 dB below the full-band
    value.
  - V0 presumes one UE per transmission over the full band.
    `NrMacSchedulerOfdmaRR` with DirectPath beams breaks that premise:
    every UE has the empty beam ID, so the scheduler treats them all as one
    beam.
- **The fix.** The NR runs use `NrMacSchedulerTdmaRR`. Its DL gives every
  TB the full band and one UE per symbol group, and its DL round robin
  rotates.
- **What did not change.** The V0 criterion, its tolerance and every other
  check.
- **Prediction stated before run 3:** every DL TB spans 51 RBs, V0 passes,
  and the other checks reproduce run 2.
- **Open item before E1.5.4:** what the reported SINR of co-scheduled OFDMA
  TBs under DirectPath means.

#### E1.5.2-0 results: PASSED in run 3 (2026-11-07)

Run 3 from `135f288` (1 min 46 s). Files:
`results/phase2/e1_5_2_0_conventions/`. The prediction stated before run 3
held in full:
- R0, A0, N0 and B0 reproduce run 2 identically (deterministic).
- Every NR DL TB spans 51 RBs.
- V0 passes.

| Check | Result |
|---|---|
| R0 | mode `link` byte-identical to E1.5.1 run 4 (8/8 files) |
| A0 | ns-3 angles = Python within 3.4e-9 deg; DirectPath transmit-side array factor = 10 log10 N within 5e-14 dB; element + array gain = E1.4 gain within 1.4e-9 dB (regimes A and B, 27 links) |
| N0 | isotropic: mean gain -0.10 / -0.09 dB (LOS / NLOS, 3.5 GHz) and +0.08 / -0.09 dB (7.2 GHz), 180 / 360 realizations |
| B0.0 | requested gNB role in every realization |
| B0.1 | role s, DirectPath: median LOS deficit 0.62 dB (3.5 GHz), 0.40 dB (7.2 GHz) |
| B0.2 | role u, conjugated DirectPath: identical to B0.1 (0.62 / 0.40 dB) |
| B0.3 | role u, DirectPath: median LOS loss vs role s **33.5 dB** (3.5 GHz) and **35.9 dB** (7.2 GHz) |
| V0 | 8,476 DL TBs in 9 NR runs: \|TB SINR - (SNR_iso + channel-level gain)\| <= 2.8e-14 dB; gNB is "s" for every link; >= 81 TBs per UE |

What is established:
1. **The ns-3 antenna objects implement E1.4's gain model exactly.** This
   covers the TR 38.901 element, the sector bearings, the angle conventions
   and the array gain 10 log10 N.
2. **The channel-level computation (mode `chan`) reproduces 5G-LENA's DL TB
   SINR to machine precision** whenever a TB has the full band and its own
   beam. E1.5.2-1 can therefore use it for many realizations.
3. **ns-3 / 5G-LENA receive-side mismatch.** The NR DirectPath vector is
   matched on the transmit side of the stored channel matrix and
   anti-matched on the receive side, losing 33-36 dB in LOS. Its conjugate
   is exactly matched on the receive side.
   - In the NR runs here the gNB was always the transmit side, because the
     first transmission was DL. Its DirectPath gain is therefore correct.
   - Any array on the receive side does need the conjugate. That includes
     a multi-element UE in DL.
   - Consequence: UE arrays in E1.5.2b use a matched combiner, and every
     configuration states each array's role.
   - A draft issue for the 5G-LENA maintainers is in
     `docs/upstream/5g-lena-directpath-rx-mismatch.md`; filing it is the
     user's decision.
4. **OFDMA co-scheduling under DirectPath** (amendment 2) is an open item
   before E1.5.4.

Reported, not tested: DirectPath realised gNB gain vs E1.4 ideal steering
(deficit = E1.4 gain - realised, median over links, 20 realizations per
link).

| Regime, carrier | LOS (9 links) | NLOS (18 links) |
|---|---|---|
| A 8x16, 3.5 GHz | 0.62 dB (0.39-0.92) | 10.05 dB (7.57-13.63) |
| A 8x16, 7.2 GHz | 0.40 dB (0.03-0.66) | 9.76 dB (5.76-12.98) |
| B 16x32, 7.2 GHz | 0.51 dB (0.14-0.83) | 13.65 dB (9.16-22.93) |

These numbers are a preview, not a result:
- DirectPath steers toward the geometric direction, which in NLOS is not
  where the energy arrives.
- E1.5.2-1 measures the gain with a codebook beam search and with the
  best single beam as an upper bound. It also uses more realizations,
  includes O2I, and resamples positions for uncertainty.
- E1.4's S3 sensitivity assumed a 4.5 dB NLOS loss. The DirectPath preview
  is about twice that for 8x16 arrays, and larger again for 16x32.

### E1.5.2-1: realised gNB gain (PRE-REGISTERED 2026-11-08; PASSED in run 1)

Config `configs/e1/e1_5_2_1_beams.json`, script `scripts/e1_5_2_1_beams.py`,
tests `tests/test_e1_5_2_1_beams.py`. Committed before the first measured run.
The config holds every number; this section explains the design.

**Question.**
- How much of E1.4's ideal steering gain survives the TR 38.901 channel, by
  stratum, carrier and array?
- How much does a deployable beam choice recover relative to NR's
  geometric DirectPath beam?
- How far is that choice from the best single frequency-flat beam?

**Instrument: new mode `beams`.** On each stored channel realization the
program rebuilds the per-RB channel vector of the gNB array from ns-3's
channel matrix, h_i[n] = Σ_c H(0, n, c) e^{-j2π f_i τ_c}. That is exactly
what ns-3's spectrum model applies. All beams are then evaluated on the
same realization:

| Beam | Definition | Role |
|---|---|---|
| dp | NR DirectPath, geometric steering | lower reference |
| cb | best of an oversampled 2D DFT codebook (4x per dimension: 2,048 beams for A, 8,192 for B), selected with exact CSI | primary: deployable analog beam, oracle selection |
| cb1 | best of the orthogonal subgrid | reported |
| eig | largest eigenvalue of R = (1/K) Σ conj(h_i) h_i^T, the best unit-norm frequency-flat beam, any amplitudes | upper bound for one analog beam |
| eigpo | phase-only version of that eigen beam | reported |
| mrt | (1/K) Σ ‖h_i‖², per-RB matched filter | ceiling of frequency-selective digital precoding |

Every number that enters a result is reproduced twice:
- by ns-3 itself, with the beam installed (I1-I3);
- by an independent numpy implementation, from a dump of h (I4).

The eigen beam is also installed in full NR runs (V1), which shows the
bound is attained inside the model.

**Strata.**
- O2O: the E1.1 geometric class (9 LOS, 18 NLOS).
- O2I: all 27 positions with the NLOS + O2I condition.
  - Hazard 13 (source reading): for LOS + O2I, ns-3.48 UMa reads the 6x6
    O2I correlation matrix in the 7-parameter LOS order and adds a
    K = 0 dB LOS ray. That combination is therefore never generated.
  - Building penetration loss is a large-scale loss. It does not change
    the angular structure, so low- and high-loss buildings have the same
    gain and form one stratum. They differ only in the link budget
    (E1.5.5).

**Design.**
- Arrays A (3.5 and 7.2 GHz) and B (7.2 GHz) × 2 strata = 6 runs.
- 200 realizations per position.
- RngRun 2, so none of the E1.5.2-0 preview realizations is reused.
- The runs of one stratum share random-number streams. Arrays A and B at
  7.2 GHz therefore see the same clusters at each realization.
- The two carriers are not correlated as in TR 38.901 cl. 7.6.5 (ns-3
  does not implement it). Carrier comparisons are paired by position only.

**Checks** (full text in the config; thresholds fixed now):

| Check | Requirement |
|---|---|
| R1 | E1.5.2-0 rerun with the new build: all 21 runs byte-identical to run 3, all checks pass |
| S1 | every realization present once, gNB on side s, readbacks, eigenvalues certified, cluster counts fit the table |
| I1 | rebuilt channel = ns-3 PSD for DirectPath, within 1e-6 dB |
| I2 | selected codebook beam installed in ns-3: within 1e-6 dB |
| I3 | eigen beam installed in ns-3 attains the bound: within 1e-6 dB |
| I4 | numpy recomputation (DirectPath from geometry, explicit codebook matrix, `eigvalsh`) within 1e-9 relative (eigenvalue 1e-8) |
| D1 | no beam exceeds the bound; bound ≤ per-RB MRT; subgrid ≤ full grid |
| N1 | isotropic gain normalised within ±0.25 dB per carrier and stratum |
| V1 | 15 NR runs with codebook or eigen beams: every DL TB SINR = SNR_iso + beam gain within 0.01 dB; beam still installed; channel generated before the run |

**Statistics.**
- Per position: the median over realizations of the gain in dB (E1.4 used
  median losses), plus the mean and the 5th and 95th percentiles.
- Deficit: D = G_E1.4 − G.
- Beam comparisons are paired on the same realization.
- Stratum summary: the median over positions, with a 95% bootstrap CI that
  resamples positions (2,000 resamples, seed 15211). Positions are the
  independent units; 27 × 200 draws are not 5,400 sites.

**Decision rules.**
- All checks pass: E1.5.5 and E1c use G_E1.4 − D_cb for regimes A and B,
  per stratum, carrier and array.
  - DirectPath and the eigen bound give the sensitivity range.
  - The CI of D_cb is carried as uncertainty.
- Regime C keeps E1.4's assumed 4.5 dB until it is measured.
- No minimum improvement is required. The 33-36 dB receive-side artefact of
  E1.5.2-0 is never used as a physical gain.
- Any check fails: stop.

**Changes from the original E1.5.2 plan (section 4).**
- The UE 4-element array moves to E1.5.2b.
- The codebook is a standard oversampled DFT grid instead of NR's CellScan:
  - it can be evaluated on the stored channel;
  - it is finer than CellScan's default angular step;
  - V1 shows any beam can be installed in NR without source changes.

**ChatGPT draft (2026-11-08), evaluated point by point.**

| Draft point | Decision |
|---|---|
| Pair all beams on one stored realization | Adopted, enforced by construction |
| Label exact-CSI codebook selection as an oracle | Adopted |
| Eigenvalue bound, labelled unconstrained | Adopted and strengthened: the eigenvalue is certified (Cholesky), its beam is installed in ns-3 and NR, and the per-RB MRT is added, because a frequency-flat bound does not bound digital per-subband precoding |
| ≥ 100 realizations | 200 (array B: about 6 min per run) |
| Indoor low-loss and high-loss as distinct strata | Changed: penetration loss does not enter the gain, so one O2I stratum; the loss difference belongs to the link budget |
| Same seed for cross-frequency comparisons | Partly: ns-3 lacks cl. 7.6.5 correlation; streams are shared but carriers are compared by position only |
| DirectPath as a codebook candidate | Not done: DirectPath needs the UE position, while the codebook is measurement-based. They are reported separately and paired |
| E1.5.2-0 output reproduced within 0.01 dB | Strengthened to byte identity (R1) |
| Dominance checks | Adopted (D1), plus I1-I4 |
| NR validation of the selected beams | Done without NR source changes (V1) |
| Feasibility-label changes | Deferred to E1.5.5, which owns the reclassification |
| No minimum-improvement criterion | Adopted |
| SINR identity is model-internal, not physical validation | Agreed; the paper will say "simulator-internal verification" |
| OFDMA diagnostic before E1.5.4 | Adopted as its own stage, pre-registered separately |
| Upstream issue: check duplicates, minimal reproducer, user's consent | Agreed |

**Found before the commit** (instrument and ns-3 facts, all recorded in the
config):
- **Hazard 13**, described above.
- **ns-3 azimuth quantization.** `Angles` truncates the azimuth to a grid
  of 2π/10¹¹ rad (`WrapToPi`).
  - The first end-to-end test on a synthetic geometry failed I4 on the
    DirectPath term: 3.6e-9 relative against a 1e-9 tolerance.
  - An 80-bit recomputation located the cause in NR's angle, not in the
    Python code.
  - The Python check now mirrors ns-3, agreeing within 2.9e-15. The
    tolerance is unchanged, and the exact-geometry difference (< 2e-8 dB)
    is reported.

#### E1.5.2-1 results: PASSED in run 1 (2026-11-08)

Run from `108524d` in 20 min; the array-B runs took 5.3 min each with
1.3 GB peak memory. Files are in `results/phase2/e1_5_2_1_beams/`, with all
32,400 realizations in `realizations.csv.gz`. Every check passed on the
first run, with no amendment.

| Check | Result |
|---|---|
| R1 | E1.5.2-0 rerun: 21 runs, 98/98 files byte-identical, all checks pass |
| S1 | 6 × 5,400 realizations complete; gNB on side s; every eigenvalue certified |
| I1 | rebuilt channel vs ns-3, DirectPath: max 9.8e-14 dB |
| I2 | codebook beam installed in ns-3: max 1.8e-14 dB |
| I3 | eigen beam installed in ns-3 vs bound: max 2.1e-9 dB |
| I4 | numpy recomputation: DirectPath 8.5e-15, codebook 8.9e-16, eigenvalue 8.3e-10 relative; same beam selected |
| D1 | largest beam/bound ratio 1 + 4.7e-10 |
| N1 | isotropic mean gain −0.08 to +0.03 dB (limit ±0.25) |
| V1 | 15 NR runs, 13,280 DL TBs with codebook or eigen beams: max \|error\| 2.8e-14 dB; ≥ 81 TBs per UE |

Deficit against E1.4's ideal steering gain, in dB (median over positions, 95%
position-bootstrap CI):

| Array, carrier | Stratum | DirectPath | Codebook (primary) | Eigen bound | Per-RB MRT |
|---|---|---|---|---|---|
| A 8x16, 3.5 GHz | LOS (9) | 0.43 [0.41, 0.45] | 0.60 [0.51, 0.68] | 0.36 [0.28, 0.39] | 0.19 [-0.28, 0.28] |
| A 8x16, 3.5 GHz | NLOS (18) | 12.56 [11.94, 13.31] | 5.05 [3.98, 5.89] | 3.99 [2.81, 4.75] | 0.78 [-0.28, 1.69] |
| A 8x16, 3.5 GHz | O2I (27) | 10.38 [9.26, 10.72] | 4.79 [3.85, 5.29] | 3.03 [2.11, 3.49] | 0.35 [-0.41, 0.87] |
| A 8x16, 7.2 GHz | LOS (9) | 0.47 [0.43, 0.50] | 0.58 [0.52, 0.73] | 0.36 [0.34, 0.40] | 0.17 [-0.16, 0.24] |
| A 8x16, 7.2 GHz | NLOS (18) | 11.14 [10.69, 11.65] | 5.16 [4.01, 5.83] | 3.93 [2.93, 4.63] | 0.73 [-0.26, 1.51] |
| A 8x16, 7.2 GHz | O2I (27) | 9.37 [8.87, 9.79] | 4.71 [3.96, 5.37] | 3.06 [2.31, 3.62] | 0.39 [-0.31, 0.81] |
| B 16x32, 7.2 GHz | LOS (9) | 0.48 [0.44, 0.50] | 0.59 [0.51, 0.71] | 0.38 [0.26, 0.40] | 0.13 [-0.25, 0.29] |
| B 16x32, 7.2 GHz | NLOS (18) | 17.01 [14.70, 17.90] | 6.26 [5.55, 7.29] | 4.36 [3.52, 5.18] | 0.61 [-0.37, 1.49] |
| B 16x32, 7.2 GHz | O2I (27) | 15.42 [13.34, 16.54] | 6.90 [5.96, 7.23] | 3.37 [2.83, 3.99] | 0.23 [-0.35, 0.71] |

Paired on the same realization, in dB (median over positions, 95% CI):

| Array, carrier | Stratum | codebook − DirectPath | eigen − codebook | MRT − eigen | orthogonal-grid deficit |
|---|---|---|---|---|---|
| A 8x16, 3.5 GHz | LOS (9) | -0.14 [-0.25, -0.09] | 0.21 [0.13, 0.33] | 0.18 [0.06, 0.49] | 2.03 [0.64, 3.30] |
| A 8x16, 3.5 GHz | NLOS (18) | 7.29 [6.33, 8.73] | 0.82 [0.78, 0.89] | 2.97 [2.88, 3.01] | 6.25 [5.35, 7.22] |
| A 8x16, 3.5 GHz | O2I (27) | 4.95 [4.02, 6.00] | 1.53 [1.45, 1.69] | 2.51 [2.38, 2.55] | 5.97 [5.49, 6.58] |
| A 8x16, 7.2 GHz | LOS (9) | -0.15 [-0.23, -0.09] | 0.21 [0.13, 0.31] | 0.14 [0.05, 0.43] | 2.10 [0.58, 3.35] |
| A 8x16, 7.2 GHz | NLOS (18) | 5.97 [5.32, 7.17] | 0.90 [0.81, 0.99] | 2.98 [2.86, 3.01] | 6.34 [5.42, 7.75] |
| A 8x16, 7.2 GHz | O2I (27) | 3.69 [3.33, 4.75] | 1.57 [1.50, 1.67] | 2.49 [2.43, 2.59] | 6.13 [5.45, 7.08] |
| B 16x32, 7.2 GHz | LOS (9) | -0.15 [-0.25, -0.06] | 0.21 [0.16, 0.32] | 0.16 [0.05, 0.47] | 2.90 [0.78, 4.56] |
| B 16x32, 7.2 GHz | NLOS (18) | 9.59 [8.88, 12.51] | 1.80 [1.74, 1.95] | 3.41 [3.36, 3.65] | 8.28 [7.26, 8.97] |
| B 16x32, 7.2 GHz | O2I (27) | 7.78 [5.64, 10.17] | 3.15 [3.03, 3.44] | 3.02 [2.93, 3.06] | 8.63 [7.76, 9.01] |

Differences in deficit between configurations, paired by position, in dB:

| Comparison | Stratum | DirectPath | Codebook | Eigen bound |
|---|---|---|---|---|
| A: 7.2 minus 3.5 GHz | LOS | 0.04 [-0.00, 0.07] | 0.03 [-0.00, 0.05] | 0.01 [-0.02, 0.06] |
| A: 7.2 minus 3.5 GHz | NLOS | -1.41 [-1.84, -1.03] | 0.03 [-0.13, 0.12] | -0.04 [-0.28, 0.08] |
| A: 7.2 minus 3.5 GHz | O2I | -0.75 [-1.03, -0.62] | 0.07 [-0.04, 0.11] | 0.10 [-0.02, 0.17] |
| 7.2 GHz: B minus A | LOS | 0.02 [-0.01, 0.04] | 0.06 [-0.12, 0.09] | -0.00 [-0.04, 0.01] |
| 7.2 GHz: B minus A | NLOS | 5.47 [4.26, 7.01] | 1.42 [1.11, 1.55] | 0.58 [0.46, 0.62] |
| 7.2 GHz: B minus A | O2I | 5.96 [4.28, 7.32] | 2.09 [1.86, 2.18] | 0.45 [0.27, 0.55] |

The tables are generated from `summary.json`, not typed by hand.

What is established (simulator-internal, TR 38.901 pre-Release-19 tables, 20 MHz):
1. **LOS.** E1.4's ideal steering gain holds within 0.6 dB for every beam
   type. The 4x-oversampled codebook loses 0.15 dB to DirectPath through
   grid scalloping. An orthogonal (non-oversampled) grid loses 2-3 dB.
2. **NLOS and O2I, one analog beam.**
   - The codebook beam falls 4.7-5.2 dB short of the ideal for 8x16 arrays
     and 6.3-6.9 dB for 16x32.
   - The frequency-flat bound is only 0.8-1.6 dB (A) and 1.8-3.2 dB (B)
     better, so finer beam search cannot recover much more.
   - DirectPath falls 9-17 dB short. Geometric steering is not a valid
     NLOS model.
3. **The channel has the energy; one beam cannot collect it.** The per-RB
   matched filter comes within 0.1-0.8 dB of the ideal gain in every
   stratum. The loss comes from one frequency-flat beam (one direction over
   20 MHz), not from the channel. A digital, frequency-selective precoder
   with exact CSI would recover it; an analog single-beam gNB cannot.
4. **No frequency dependence at equal element count.** With the codebook or
   the bound, the deficit at 7.2 GHz equals that at 3.5 GHz within 0.1 dB
   (CIs within ±0.3 dB). E1.4's carrier comparison in regime A is therefore
   unchanged by realised beamforming. DirectPath alone is 0.8-1.4 dB
   better at 7.2 GHz (smaller angular spreads in the tables), which is
   irrelevant once DirectPath is not used.
5. **Equal aperture costs more realised gain.**
   - Array B loses 1.4 dB (NLOS) and 2.1 dB (O2I) more of its ideal gain
     than A.
   - Its ideal 6.02 dB advantage over A at 7.2 GHz therefore becomes about
     4.6 dB in NLOS and 3.9 dB in O2I (codebook; 5.4-5.6 dB with the bound).
   - Narrower beams capture a smaller share of the angular spread. In LOS
     the advantage is intact.
6. **The codebook deficit has no distance trend in NLOS** (Spearman
   |ρ| ≤ 0.15), so the per-stratum model of the decision rule fits.
   - DirectPath's NLOS deficit falls with distance (ρ = −0.70 to −0.94).
     The TR 38.901 ZOD offset biases geometric steering most at short
     range.
   - In O2I at geometric-LOS positions, the codebook deficit rises with
     distance (ρ ≈ 0.85, 9 positions). This is a caveat for E1c.
7. **E1.4's S3 assumption** (4.5 dB NLOS loss, for regime C) has the size of
   the measured regime-A codebook deficit. Regime C itself is not measured.

**Decision applied (pre-registered rule).** E1.5.5 and E1c use G_E1.4 −
D_cb, in dB. Sensitivities: DirectPath (lower) and the eigen bound (upper);
the CIs above carry the uncertainty.

| | LOS | NLOS | O2I |
|---|---|---|---|
| A, 3.5 GHz | 0.60 | 5.05 | 4.79 |
| A, 7.2 GHz | 0.58 | 5.16 | 4.71 |
| B, 7.2 GHz | 0.59 | 6.26 | 6.90 |

**Limits of these numbers.**
- Selection uses exact CSI: no beam-sweep overhead, measurement noise or
  CSI ageing.
- Single polarization, one port, the E1.4 serving sector.
- 20 MHz. A wider band averages a frequency-flat beam over more frequency
  selectivity, so deployment bandwidths (E1c) may lose somewhat more; this
  is not measured here.
- Pre-Release-19 fast-fading tables.
- The 27 positions are not an area sample.

### E1.5.4-0: OFDMA co-scheduling under DirectPath (PRE-REGISTERED 2026-11-08; AMENDED after run 1; run 2 NOT established, mechanism explained)

Config `configs/e1/e1_5_4_0_ofdma_diagnostic.json`, script
`scripts/e1_5_4_0_ofdma_diagnostic.py`, tests
`tests/test_e1_5_4_0_ofdma_diagnostic.py`. This resolves the open item of
E1.5.2-0 amendment 2 before any multi-UE stage.

**Why.** In run 2, 4,282 OFDMA DL TBs that shared their symbols with other
UEs were 54 dB below to 13 dB above the full-band gain of the UE's own beam.
That comparison alone does not show a PHY error; it may be the wrong
reference. Reading the source gave a specific, testable mechanism:
- the schedulers key UEs by BeamId;
- DirectPath gives every UE the same empty BeamId, so OFDMA puts several UEs
  of one sector into the same symbols;
- the gNB PHY changes its analog beam at most once per time instant, to the
  UE of the first allocation it processes. The code comment reads "we hope
  that the scheduler has scheduled UEs within the same beam".

**Hypothesis H1.** For every TB, there is one co-scheduled UE b such that:
- every TB of the group satisfies SINR = SNR_iso × the mean, over the TB's
  own RBs, of the per-RB gain of the receiving UE with UE b's beam, within
  0.01 dB (H1a, decision-relevant);
- b is the UE of the group's first allocation in the MAC's allocation order
  (H1b, reported).

**Instrument.** Mode `nrfading` with `--ofdmaDiag=1` writes:
- every DL DCI with its RBG bitmask;
- every MAC DL allocation in list order (`DlScheduling` trace);
- every TB with its slot and first symbol;
- after the run, the per-RB gain of every UE under every UE's stored beam.

`--gnbBeam=dpid` keeps the DirectPath vectors but gives each UE its own
BeamId.

**Cases.**
- K0: replay of the 9 run-2 runs. They must be byte-identical (R2), so the
  explanation applies to the failed evidence itself.
- K1: two line-of-sight UEs in different directions.
- K2: the K1 pair with distinct BeamIds.
- K3: two co-located UEs, so their beams are identical.
- K4: the K0 runs with distinct BeamIds.

Predictions:
- H1a holds in K0 and K1.
- K1 can discriminate: there are at least 10 shared groups, and at least one
  TB fails the own-beam identity.
- K2 and K4 never put two UEs in the same symbols, and every TB matches its
  own beam.
- K3 shares symbols, and every TB matches its own beam.

**Seen before this commit (declared in the config).** Smoke runs on a
synthetic 2-UE geometry already showed H1a, and the K2 and K3 behaviour.
They also showed that the first DCI in *send* order does not identify b: it
failed in 454 of 922 co-scheduled TBs. The reason is that the PHY sorts its
control-message list by pointer value. H1b was therefore restated against
the MAC allocation order and made non-decision-relevant before this commit.
A rerun showed the MAC order identifies b in only 345 of 461 synthetic
groups, so which member's beam is used remains an open detail. The
consequence follows from H1a alone.

**Decision.** If H1a and all four predictions hold:
- co-scheduled DirectPath TB SINR is a correct computation of a
  mis-configured transmission (another UE's beam), not a measurement of the
  UE's own link. It is never used.
- E1.5.4 uses distinct BeamIds.
- E1.5.4 must state the beam architecture: 5G-LENA's PHY has one analog
  beam per symbol, so frequency multiplexing of UEs in different directions
  (E1.5.0's F3 surrogate) needs a digital or hybrid gNB that this PHY does
  not model.

Otherwise, OFDMA SINR under DirectPath stays unvalidated and is not used.
If R2 or the TB-DCI join fails, stop.

Amendment 1 (after run 1, `132dc5c`):
- **What happened.** R2 passed (9 replays byte-identical to run 2), as did
  K1_power, P2 and P3. The join check and P4 failed. H1a was reported FAIL
  only because the code made it depend on the join check; its per-TB result
  was not inspected before this amendment.
- **J3 diagnosis.** Every DCI without a MAC record is a HARQ
  retransmission, and every retransmission lacks one: the MAC fires
  `DlScheduling` only for new data (`m_ndi == 1`). J1 (TB-DCI) and J2 (RB
  counts) passed on every TB.
- **P4 diagnosis: 5G-LENA hazard 14.**
  - The three K4 runs of the 30° sector (13 UEs, 13 distinct BeamIds) had
    no DL allocation at all, with exit 0 and no message.
  - The OFDMA scheduler's load-based symbols-per-beam step divides integers
    (`double tmp = symAvail / bufTotal`) and then hands out symbols one at
    a time. With 13 active beams and 12 data symbols, one beam gets 0
    symbols and the slot carries no DL data.
  - Direct test on the same links: 13 UEs give 0 DL TBs, 12 UEs 1,484 and
    11 UEs 1,297. With logging on, the 13-beam run crashes (SIGSEGV)
    inside the scheduler's own log line.
- **Changes.**
  - J3 applies to new-data DCIs only.
  - H1a, P2-P4 depend on J1, J2 and J3 only.
  - H1b is evaluated on groups without retransmissions (still reported
    only).
  - P4 keeps its failure on record. P4a applies P4's conditions to the K4
    runs that scheduled data plus a new case K4b (the 30° runs with their
    first 12 links). P4a replaces P4 in the decision.
- **What did not change.** H1a and its tolerance, R2, J1, J2, K1_power, P2,
  P3, cases K0-K4, and the consequences.
- **Prediction stated before run 2.** K0-K4 reproduce run 1; K4b serves
  every UE with no shared groups and own-beam identity on every TB.
- **Evidence.** `validation_run1_failed.json`,
  `manifest_run1_failed.json`, `summary_run1.json.gz`; `raw/run1_failed/`
  is kept locally.

#### E1.5.4-0 results (run 2): formally NOT established (P4a); run-2 discrepancy explained (2026-11-08)

Run 2 from `df030b5` (1.6 min). Files are in
`results/phase2/e1_5_4_0_ofdma_diagnostic/`. Run 2 reproduced run 1 for
K0-K4, as predicted.

| Check | Result |
|---|---|
| R2 | 9 replays byte-identical to E1.5.2-0 run 2 (5 files each) |
| J1-J3 | every TB joined to its DCI and RB count; every new-data DCI has its MAC record; 670 retransmission DCIs lack one, as diagnosed |
| **H1a** (K0, K1) | **holds for every TB:** 4,499 replayed run-2 TBs and 938 K1 TBs, max error 4.3e-14 dB |
| K1_power | 461 two-UE groups (pair ue01, ue08, 62° apart); own-beam identity fails on 461 TBs, by up to 29.6 dB |
| P2 (K2) | no shared groups; 954/954 TBs match their own beam |
| P3 (K3) | 461 shared groups; 938/938 TBs match their own beam |
| P4 (as registered) | FAILED: the three 30° K4 runs carried no DL data (hazard 14) |
| **P4a** (amendment 1) | **FAILED in 1 of 9 runs.** K4b, array B, 30° sector: 475 TBs, all matching their own beam, but 11 of 12 UEs were never scheduled |
| H1b (reported) | the MAC order misidentifies the winning beam for 977 TBs, the DCI send order for 3,149 |

**Verdict.** By the pre-registered rule the stage is NOT established,
because P4a failed. The not-established branch applies: OFDMA TB SINR under
DirectPath stays unvalidated and is not used in E1.5.4. No further
amendment is made to reach a pass.

What is established nonetheless, each by a passed check:
1. **The 4,282 run-2 discrepancies are fully explained** (H1a, 0
   exceptions, 4.3e-14 dB).
   - In every group of co-scheduled TBs, each TB's SINR is the SINR of its
     UE under one co-scheduled UE's beam, over the TB's own RBs.
   - Against the UE's own beam the error spans −67 to +19 dB in K0.
   - NR's SINR computation is correct. The transmission is mis-beamed,
     because DirectPath gives every UE the same BeamId and the gNB holds
     one analog beam per symbol.
2. **Distinct BeamIds remove the mis-beaming.** Over 7,849 TBs (K2, the 6
   K4 runs with data, K4b), every TB matches its own beam within 0.01 dB
   and no two UEs share symbols.
3. **Co-scheduling within one physical beam is computed correctly** (P3).
4. **Hazard 14: 5G-LENA OFDMA with per-UE beams.**
   - 13 beams in a sector: no DL data at all.
   - 12 beams with array B: 11 of 12 UEs never scheduled. With array A,
     12 beams served every UE.
   - Located so far: integer division in the load-based symbols-per-beam
     split, beams left with one symbol, and the leftover symbol always
     going to the same beam. The full root cause is not isolated.
   - It fails silently (exit 0) and crashes with logging on.

**Consequences for E1.5.4** (to be fixed in its own pre-registration):
- OFDMA with DirectPath's shared BeamId is never used.
- OFDMA with per-UE BeamIds is not a reliable multiple-access model in this
  5G-LENA version when a sector has many beams (hazard 14).
- With one analog beam per symbol, UEs in different directions can only be
  time-multiplexed. Frequency multiplexing across directions, which
  E1.5.0's F3 surrogate assumes, needs a digital or hybrid gNB that this
  PHY does not model. E1.5.4 must therefore decide how F3 is tested, for
  example with an explicitly labelled isotropic (beamless) counterfactual,
  or analytically.
- The TDMA arm (TdmaPF, per-UE beams, one UE per symbol group) is not
  affected.

**Upstream.** Two further items exist, separate from the receive-side
DirectPath issue: DirectPath with OFDMA (shared empty BeamId), and hazard
14. Filing them is the user's decision.

### E1.5.3: single-UE NR link mapping (PRE-REGISTERED 2026-11-08; PASSED in run 1)

Config `configs/e1/e1_5_3_link_mapping.json`, script
`scripts/e1_5_3_link_mapping.py`, tests `tests/test_e1_5_3_link_mapping.py`.
This narrows the plan of section 4 to the quantity E1.3-E1.5.5 need: the
SNR-to-SE mapping. Offered-load and delay curves are not needed for the
feasibility question.

**Why.** E1.3 and E1.4 use TR 36.942's E-UTRA-era mapping (UL α 0.4, capped
at 2.0 bit/s/Hz). Under F2, uplink feasibility needs link SE 1.5, which
takes 10.95 dB with α 0.4. The UL verdicts hinge on this curve.

**Hazard found while preparing.** 5G-LENA's default error model, for both
link adaptation and decoding, is `NrLteMiErrorModel`, an LTE model. The
NR LDPC models must be set explicitly. The program's new `--errorModel`
option sets them for the gNB DL and UL AMC, the UE's CQI and both decoders.
Every run reads all four back.

**Design.**
- One UE, isotropic, AWGN (mode `link`, E1.5.1-verified).
- 75 losses from 76 to 150 dB, each run carrying saturating DL and UL
  together: DL SNR −9.6 to +64 dB, UL −32.6 to +41 dB.
- Two error models: `NrEesmIrT2` (MCS table 2, 256QAM, primary) and
  `NrEesmIrT1` (64QAM, sensitivity).
- TdmaPF scheduler; measurement window 0.2-0.6 s.
- **Metric:** decoded TB bits divided by all the data resources used (RBs
  × symbols, including corrupt TBs and retransmissions). This is E1.3's
  normalisation: the rate while the direction's resources are in use,
  without the TDD duty cycle or control overhead.

**Fit.**
- Thr_max: the maximum measured SE.
- α: least squares on the points with 0 < SE ≤ 0.9 Thr_max.
- SNR_min: the lowest grid SNR with SE > 0.
- Reported: the Shannon-gap form, and the SNR needed for the F2 link SE
  (2.25 DL, 1.5 UL) under each mapping.

**Checks.**

| Check | Requirement |
|---|---|
| R3 | E1.5.2-0 byte-identical (includes the E1.5.1 link-mode rerun) |
| E0 | all four error-model readbacks equal the requested model |
| V | every TB's SINR = Python SNR within 0.01 dB |
| M | MCS ≤ 27 (T2) or ≤ 28 (T1), and the top MCS on ≥ 90% of DL TBs at the lowest loss |
| C | ≥ 50 TBs per direction for every point in [−10, 35] dB |

**Decision.** If all checks pass, E1.5.5 and E1c use the T2 mapping in
TR 36.942 form with the fitted α, Thr_max and SNR_min, provided its maximum
fit error is ≤ 0.3 bit/s/Hz; otherwise the empirical curve. T1 and TR
36.942 are sensitivities. Disagreement with TR 36.942 is a result, not a
failure.

**Limits.** AWGN only (fading enters through E1.5.2-1), one layer, ideal
control channels, CQI from the exact SINR, no UL power control.

#### E1.5.3 results: PASSED in run 1 (2026-11-09)

Run from `ffc7e9b`: 150 runs plus R3, in 15 min. Files are in
`results/phase2/e1_5_3_link_mapping/`.

| Check | Result |
|---|---|
| R3 | E1.5.2-0 rerun: 98/98 files byte-identical |
| E0 | every run: gNB DL/UL AMC and both decoders are the requested model |
| V | every TB's SINR = Python SNR, max error 3.0e-14 dB |
| M | T2: max MCS 27, 100% MCS 27 at the lowest loss; T1: max 28, 100% |
| C | every point in [−10, 35] dB has ≥ 50 TBs per direction |

| Mapping | Direction | α | Thr_max (bit/s/Hz) | SNR_min (dB) | max fit error | Shannon gap (dB) |
|---|---|---|---|---|---|---|
| TR 36.942 (E1.3) | DL | 0.6 | 4.4 | -10.0 | - | - |
| 5G-LENA T2 (256QAM) | DL | 0.579 | 6.32 | -1.6 | 0.65 | 8.19 |
| 5G-LENA T1 (64QAM) | DL | 0.539 | 4.74 | -1.6 | 0.55 | 7.57 |
| TR 36.942 (E1.3) | UL | 0.4 | 2.0 | -10.0 | - | - |
| 5G-LENA T2 (256QAM) | UL | 0.571 | 6.32 | -0.6 | 1.74 | 8.48 |
| 5G-LENA T1 (64QAM) | UL | 0.550 | 4.74 | -0.6 | 0.80 | 6.99 |

SNR needed for the F2 link SE, in dB:

| Direction | target link SE | TR 36.942 | T2 fitted | T2 empirical | T1 fitted | T1 empirical |
|---|---|---|---|---|---|---|
| DL | 2.25 | 10.95 | 11.40 | 13.08 | 12.31 | 13.08 |
| UL | 1.5 | 10.95 | 7.15 | 8.66 | 7.50 | 8.66 |

Empirical T2 curve, SE in bit/s/Hz per used resource:

| SNR (dB) | 0.4 | 3.4 | 6.4 | 9.4 | 12.4 | 15.4 | 18.4 | 21.4 | 24.4 | 27.4 | ≥ 30.4 |
|---|---|---|---|---|---|---|---|---|---|---|---|
| DL | 0.20 | 0.32 | 0.75 | 1.63 | 2.05 | 2.83 | 3.85 | 4.36 | 5.31 | 5.51 | 6.32 |
| UL | 0.20 | 0.51 | 1.00 | 1.63 | 2.19 | 3.07 | 3.36 | 4.46 | 5.29 | 5.49 | 6.32 |

The tables are generated from `summary.json`. Below −1.6 dB (DL) and
−0.6 dB (UL) the SE is 0.

What is established (5G-LENA v5.1, simulator-internal, AWGN, one layer):
1. **The TR 36.942 mapping misstates NR in opposite ways at the two ends.**
   - Top end: NR reaches 6.32 bit/s/Hz (256QAM, MCS 27, with DMRS and CP
     overheads) against TR's caps of 4.4 (DL) and 2.0 (UL). The UL cap of
     2.0 is the most restrictive E1.3 assumption.
   - Low end: 5G-LENA stops at about −1 dB against TR's −10 dB.
2. **F2 thresholds move.**
   - UL: 8.66 dB instead of 10.95 dB, i.e. 2.3 dB easier.
   - DL: 13.08 dB instead of 10.95 dB, i.e. 2.1 dB harder.
   - The TR 36.942 functional form fits poorly (maximum error 0.65 DL and
     1.74 UL against the 0.3 limit), so by the pre-registered rule the
     empirical T2 curve is primary.
3. **The low-SNR end is a 5G-LENA model limit, not NR physics (hazard 15).**
   - The MCS-0 table (base graph 2, code block 1544 bits) starts at
     −1.88 dB with BLER 0.996 and reaches BLER 0.1 near −1 dB. Below its
     first point the lookup returns BLER 1 (`nr-eesm-error-model.cc`).
   - HARQ IR is modelled as moving to an equivalent lower-rate MCS of the
     same modulation (`NrEesmIr::GetMcsEq`). MCS 0 has none, and the
     combined effective SINR is an average, not an energy sum. So
     retransmissions give **no gain at MCS 0**: at −3.6 dB all four
     redundancy versions failed.
   - For Table 2, MCS 0 and 1 of base graph 1 have no data at all (a
     placeholder).
   - The rate-0.12 QPSK Shannon limit is −7.5 dB. Real NR, with HARQ
     energy combining and repetition, operates well below −1 dB.
4. **Link adaptation misses its 10% target at some SNRs (hazard 16, not
   diagnosed).** Above 0 dB, first-transmission BLER exceeds 0.01 at
   18 points (T2: 7 UL and 2 DL; T1: 6 UL and 3 DL):
   - in T2, UL BLER is 0.875 at 26.4 dB (MCS 26) and 0.29 at 25.4 dB, and
     DL BLER is 0.30 at 25.4 dB;
   - in T1, UL BLER is 0.30 at 6.4 dB;
   - the affected points dip below lower-SNR points, so the empirical
     curve is not monotone (e.g. T2 UL: 4.57 bit/s/Hz at 25.4 dB but 3.26
     at 26.4 dB);
   - the AMC and the decoder use the same error-model type (E0), so the
     cause lies inside 5G-LENA's MCS selection (a code-block-size or
     effective-SINR mismatch is plausible). It was not isolated tonight;
   - a real link-adaptation loop (OLLA) would remove such dips, so they
     understate NR at those points. E1.5.5's pre-registration must decide
     whether a monotone envelope is used as a sensitivity.

**Decision applied (pre-registered).** E1.5.5 and E1c use the empirical T2
curve as the primary mapping per direction; T1 and TR 36.942 are
sensitivities. Because of hazard 15, E1.5.5 must:
- report every UL and DL coverage classification under both the primary
  curve and TR 36.942's low-SNR end;
- label any link that fails only between −10 and −1 dB as dependent on the
  error-model limit, not as an NR result.

E1.5.5's pre-registration fixes how this is done. The paper must not
attribute cell-edge losses to NR physics on the strength of this curve.

#### E1.5.3 errata (2026-11-09)

Neither erratum changes a computed number.

1. **Config `grid_note`.** The note in `configs/e1/e1_5_3_link_mapping.json`
   swaps the two noise figures:
   - The true offsets are DL SNR = 138.36 − L and UL full-band SNR =
     119.36 − L.
   - The grid therefore covers DL −11.6 to +62.4 dB and UL −30.6 to
     +43.4 dB.
   - Every SNR in the results came from the link-budget code, not from the
     note. The config file is left as committed.
2. **Count of BLER points.** Item 4 above is correct as written: 18 points
   above 0 dB have first-transmission BLER > 0.01. Of these, **12 exceed
   the 10% target**: T2 UL 5, T2 DL 2, T1 UL 4, T1 DL 1. The end-of-session
   report and the project notes wrongly said that link adaptation misses
   10% at 18 points. All 9 non-monotone points are among the 12. (Pointed
   out by the ChatGPT E1.5.4 memo; recounted from `summary.json`.)

### Review of the ChatGPT E1.5.4 decision memo (2026-11-09)

Each point was checked against the data, the pinned source or the standard
before it was accepted.

| Memo point | Verdict | Action |
|---|---|---|
| 12 of the 18 BLER points exceed 10% | Correct | E1.5.3 erratum 2 |
| A non-monotone curve undermines the F2 → F3 dominance argument | Partly correct. On the raw T2 UL curve, a shift of exactly +10.00 dB loses rate once (16.4 → 26.4 dB: 3.33 vs 3.26 bit/s/Hz). At the F3 shift of 10.09 dB (5 of 51 PRBs), computed in E1.5.3a, no point fails on the raw or C-AMC curves. *[Corrected after E1.5.3a: the pre-registration text gave the +10.00 dB failure as a +10.09 dB failure.]* The F2/F3 *classification* is not affected: F2-feasible UL needs ≥ 8.66 dB, and the raw curve's minimum above 18.7 dB is 3.26 > 1.5 | E1.5.3a reports both checks; E1.5.5 re-verifies S5 under each mapping |
| Diagnose the BLER anomaly (E1.5.3a) before E1.5.5 | Agree. Source reading found a specific mechanism (below) | E1.5.3a. A deterministic replay of every TB replaces the proposed re-simulation of 18 points under varied loads, which would only sample what the replay computes exactly |
| Compare raw curve, fixed-MCS envelope and TR low end; no silent substitution | Agree | E1.5.3a measures fixed-MCS link adaptation at the TS 38.214 reference and reports an analytic envelope. The choice of primary mapping is pre-registered before any E1.5.5 result |
| Low-SNR floor is an error-model limit, not a standards outage | Already recorded (hazard 15) | Unchanged |
| UL OFDMA: the header says DL only, but the code has `AssignULRBG` | Correct and important. At `cedceadd`, `AssignULRBG` (`nr-mac-scheduler-ofdma.cc:620`) gives UEs of the **same beam** disjoint RBGs, and `CreateUlDci` places them in the same symbols. The header comment (`nr-mac-scheduler-ofdma.h:43`) is stale. Across beams, symbols are still divided in time | E1.5.4 must show UL frequency sharing in the UL DCI bitmasks, not infer it |
| The analog codebook + TdmaPF network is the "primary deployable" configuration | Disagree with the framing. 5G-LENA models one analog beam per symbol, a mmWave architecture. FR1 massive-MIMO radios beamform digitally per transceiver | E1.5.4 treats the gNB architecture as a stated dimension with two bounds: analog (time share across beams, F2) and digital FDMA (F3) |
| Digital beamforming only as an analytic reference | Disagree for **FDMA**. With orthogonal RBs in one cell, unchanged per-RB power and constant-modulus DFT beams, each UE's SNR equals its single-UE codebook-beam SNR, and per-element power is unchanged. A 5G-LENA run with isotropic arrays and per-UE loss L − G_cb reproduces this at packet level. Agree that MU-MIMO on shared RBs needs an interference and power model and is excluded | E1.5.4 design after E1.5.3a |
| Same-beam OFDMA arm and beamless arm | Same-beam DL co-scheduling is already established (E1.5.4-0 H1a: co-scheduled TBs carry the installed beam's gain to 4e-14 dB on 4,499 + 938 TBs). The beamless arm is the G = 0 case of the gain-folded FDMA arm | Merged into E1.5.4 |
| Reproduce the 2–13 beam scheduler failures separately | Useful for upstream report 3; not on the paper's critical path | After E1.5.4 |
| Gating checks: complete UE timelines, unserved UEs in denominators, throughput vs SE, spatial resampling | Agree | E1.5.4 pre-registration |
| Upstream priority: receive-side DirectPath, many-beam starvation, shared BeamId | Agree. A fourth report is added if E1.5.3a confirms H16 | User's decision |
| Repository hygiene before push | Agree. `git diff --check 76e0e72..HEAD` is clean. No e-mail address or credential is in any tracked file; the WSL user name appears in run paths of manifests | Push is the user's step |

### E1.5.3a: hazard 16 diagnosis and standard-consistent link adaptation (PRE-REGISTERED 2026-11-09)

Config: `configs/e1/e1_5_3a_amc_diagnostic.json`. Script:
`scripts/e1_5_3a_amc_diagnostic.py`. Tests:
`tests/test_e1_5_3a_amc_diagnostic.py`.

**Hypothesis H16 (from the source, NR `cedceadd`).**
- `NrAmc::CreateCqiFeedbackSiso` produces both the UE's DL CQI
  (`nr-ue-phy.cc:1429`) and the gNB's UL MCS
  (`nr-mac-scheduler-cqi-management.cc:79`).
- It accepts an MCS if a transport block **sized for one OFDM symbol** has
  TBLER ≤ 0.1: `CalculateTbSize(mcs, 1, nRBs)`, `nr-amc.cc:229`.
- The transmitted TBs span 10–13 symbols, so they are 10–13 times larger and
  are segmented into more and longer LDPC code blocks, with steeper BLER
  curves. Their TBLER can be far above 0.1.
- The DL MCS is further quantized through the 4-bit CQI
  (`GetMcsFromCqi`).
- 5G-LENA's own MIMO path (`NrAmc::GetMcs`) sizes the TB for 12 symbols
  (`NR_AMC_NUM_SYMBOLS_DEFAULT`). This is what TS 38.214 defines:
  - §5.2.2.5.1, CSI reference resource: "The number of PDSCH and DM-RS
    symbols is equal to 12";
  - §5.2.2.1: the CQI is the highest index whose transport block "could be
    received with a transport block error probability not exceeding 0.1".

**Seen before registration.**
- The source lines above.
- E1.5.3 raw traces at t2 L89–L95, L99–L100 (UL) and L113–L116 (DL). For
  example, t2 L93 UL: MCS 26, decoder TBLER 0.8227 (12 symbols) and 0.8835
  (13 symbols), 147 of 168 first transmissions corrupt.
- One smoke replay of those two SINRs, which reproduced the trace TB sizes
  and TBLERs. Nothing else.

**Stage A: replay (no simulation).** For each error model, one program
call (`--amcReplay`) takes the scheduler's own DL and UL `NrAmc` objects and
a decoding error model of the same type. It evaluates every SINR that
occurs in E1.5.3 TBs, plus the link-budget SINR of every grid point,
and gives:
- the SISO AMC choice and the DL CQI-to-MCS step;
- the MIMO-path (12-symbol) choice;
- for 51 RB × {1, 2, 10, 12, 13} and 5 RB × {12, 13} symbols, and every
  MCS: the TB size and the first-transmission TBLER (SISO and MIMO error-model
  calls).

| Check | Requirement (all first-transmission window TBs of E1.5.3) |
|---|---|
| A0 TB size | trace TB size = replayed size for its RBs, symbols and MCS |
| A1 MCS | UL MCS = SISO AMC at its SINR; DL MCS = GetMcsFromCqi(SISO CQI) at its SINR |
| A2 TBLER | trace TBLER = replayed TBLER to 1e-9 (SISO or MIMO call; reported which) |
| A3 classes | every point above 0 dB with first-transmission BLER > 0.1 is either **MISMATCH** (1-symbol TBLER of the used MCS ≤ 0.1 < mean TBLER of the transmitted TBs) or **SAMPLING** (mean transmitted TBLER ≤ 0.1, and the exact Poisson-binomial probability of at least the observed failures ≥ 0.001); none is UNEXPLAINED. The class of each non-monotone point is reported |
| A4 rule | the 12-symbol rule computed in Python from the replayed table = the program's MIMO-path MCS at every SINR |

H16 is confirmed only if A0–A4 all pass. Otherwise E1.5.3a stops, and
E1.5.5 does not start.

**Stage B: C-AMC link mapping.** C-AMC is link adaptation consistent with
TS 38.214's CQI definition: the 12-symbol reference TB over the full band,
TBLER ≤ 0.1, without 4-bit CQI quantization. MCS choice and outer-loop
adaptation are gNB implementation.
- It re-runs the E1.5.3 grid (75 losses × T2, T1) with all E1.5.3 arguments
  unchanged.
- The DL and UL MCS are fixed (`--fixedMcsDl/Ul`) to the C-AMC MCS at each
  point's link-budget SINR.

Checks:

| Check | Requirement |
|---|---|
| B0 | C-AMC MCS at the link-budget SINR = at every E1.5.3 trace SINR of that point |
| R4a | E1.5.2-0 rerun: passes, 98 data files byte-identical (link mode unchanged) |
| R4b | E1.5.3 runs t2_L93, t2_L113, t1_L113 rerun: data files byte-identical to E1.5.3 |
| E0, V, C | as E1.5.3 |
| F1 | read-back FixedMcs = 1 and starting MCS = requested; every TB of the run carries the fixed MCS |

Predictions are scored but do not gate the decision:
- **P_mono:** no drop between consecutive points larger than 10% of SE.
- **P_bler:** first-transmission BLER ≤ 0.1 + 3σ wherever the MCS is > 0.

Reported only:
- an analytic first-transmission envelope (ideal MCS choice, no HARQ);
- the 5-RB (F3 allocation) C-AMC curve and its shift from 51 RB;
- the F2 required SNR under C-AMC;
- the pointwise F3 ≥ F2 check on the raw and C-AMC curves.

**Decision (fixed now, before any E1.5.5 result).** If H16 is confirmed
and B0, R4a, R4b, E0, V, F1 and C all pass:
- **E1.5.5 and E1c use the C-AMC T2 curve as the primary mapping.**
- Sensitivities: the raw E1.5.3 T2 curve (labelled "5G-LENA v5.1 SISO AMC,
  1-symbol reference"), C-AMC T1, and TR 36.942.
- Hazard 15 handling is unchanged.
- This replaces E1.5.3's choice of primary mapping. It does so because
  E1.5.3 found an anomaly its own rule did not foresee, not because of any
  downstream outcome.
- A fourth upstream report is drafted (filing is the user's decision).

Otherwise: stop and diagnose.

**Limits.** AWGN, one UE, one layer, ideal control channels. C-AMC is link
adaptation at its BLER target on the standard's reference resource; it is
not an OLLA model.

#### E1.5.3a results: PASSED in run 1; H16 confirmed (2026-11-09)

Run from `488193d` in 34 min (program SHA-256 `49d8825a8547…`):
- Stage A replay: 250 SINRs per model, about 1 s each;
- R4a and R4b;
- 150 Stage B runs.

Files are in `results/phase2/e1_5_3a_amc_diagnostic/`.

| Check | Result |
|---|---|
| A0 TB size | every first-transmission window TB, T2 and T1: 0 mismatches |
| A1 MCS | every first-transmission window TB: 0 mismatches (UL SISO AMC; DL SISO CQI → MCS) |
| A2 TBLER | replayed = trace TBLER **exactly** (absolute difference 0.0), by both the SISO and the MIMO call |
| A3 classes | all 12 points above 0 dB with BLER > 0.1 are MISMATCH; none is SAMPLING or UNEXPLAINED; all 9 non-monotone points are MISMATCH |
| A4 rule | Python 12-symbol rule = program MIMO-path MCS at all 250 SINRs per model |
| B0 | C-AMC MCS identical at the link-budget and every trace SINR |
| R4a / R4b | 98/98 E1.5.2-0 files and all E1.5.3 files of 3 runs byte-identical |
| E0, V, F1, C | pass; SINR identity 3.0e-14 dB; every TB carries the fixed MCS |

**The mechanism, point by point.** Each point is a first-transmission BLER
above 10%, all classified MISMATCH. "Assumed" is the TBLER the SISO AMC
computed for a 1-symbol TB; "transmitted" is the replayed TBLER of the TBs
actually sent.

| Point | SNR (dB) | first-tx TBs | BLER observed | MCS | TBLER assumed (1 symbol) | TBLER of transmitted TBs |
|---|---|---|---|---|---|---|
| T2 DL | 25.36 | 371 | 0.296 | 25 | 0.0922 | 0.3189 |
| T2 DL | 27.36 | 421 | 0.145 | 27 | 0.0208 | 0.1809 |
| T2 UL | 13.36 | 285 | 0.109 | 12 | 0.0426 | 0.1141 |
| T2 UL | 18.36 | 273 | 0.154 | 17 | 0.0159 | 0.1752 |
| T2 UL | 25.36 | 244 | 0.291 | 25 | 0.0922 | 0.3189 |
| T2 UL | 26.36 | 168 | 0.875 | 26 | 0.0127 | 0.8520 |
| T2 UL | 27.36 | 275 | 0.149 | 27 | 0.0208 | 0.1686 |
| T1 DL | 23.36 | 434 | 0.111 | 28 | 0.0258 | 0.1225 |
| T1 UL | 6.36 | 180 | 0.300 | 10 | 0.0949 | 0.3066 |
| T1 UL | 13.36 | 285 | 0.109 | 19 | 0.0426 | 0.1141 |
| T1 UL | 18.36 | 273 | 0.154 | 24 | 0.0159 | 0.1752 |
| T1 UL | 23.36 | 287 | 0.101 | 28 | 0.0258 | 0.1225 |

Hazard 16 is therefore diagnosed:
- 5G-LENA's SISO AMC tests a 1-symbol TB and transmits 10–13-symbol TBs.
- Every anomalous point is this mismatch. None is sampling noise, even the
  three points with BLER just above 0.10.

**C-AMC T2 curve** (SE per used resource, bit/s/Hz; generated from
`summary.json`):

| SNR (dB) | 0.4 | 3.4 | 6.4 | 9.4 | 12.4 | 15.4 | 18.4 | 21.4 | 24.4 | 27.4 | ≥ 30.4 |
|---|---|---|---|---|---|---|---|---|---|---|---|
| DL raw E1.5.3 | 0.20 | 0.32 | 0.75 | 1.63 | 2.05 | 2.83 | 3.85 | 4.36 | 5.31 | 5.51 | 6.32 |
| DL C-AMC | 0.20 | 0.51 | 1.00 | 1.63 | 2.19 | 3.08 | 3.59 | 4.55 | 5.31 | 6.11 | 6.32 |
| UL raw E1.5.3 | 0.20 | 0.51 | 1.00 | 1.63 | 2.19 | 3.07 | 3.36 | 4.46 | 5.29 | 5.49 | 6.32 |
| UL C-AMC | 0.20 | 0.51 | 1.00 | 1.63 | 2.19 | 3.07 | 3.59 | 4.46 | 5.29 | 6.11 | 6.32 |

SNR needed for the F2 link SE, in dB:

| Direction | target link SE | TR 36.942 | raw E1.5.3 T2 | C-AMC T2 | C-AMC T1 |
|---|---|---|---|---|---|
| DL | 2.25 | 10.95 | 13.08 | 12.92 | 12.95 |
| UL | 1.5 | 10.95 | 8.66 | 8.66 | 8.66 |

What is established:
1. **The F2 thresholds barely move.** UL is unchanged at 8.66 dB, and DL
   drops by 0.16 dB to 12.92 dB. The E1.5.3 conclusion stands: against
   TR 36.942, UL is 2.3 dB easier and DL about 2 dB harder.
2. **The dips are gone.**
   - The T2 C-AMC curves are monotone in both directions, and DL and UL now
     agree within 0.09 bit/s/Hz at every point (largest gap at 21.4 dB).
   - C-AMC is higher than the raw curve at 16 (DL) and 6 (UL) of 35 points
     between 0 and 35 dB, by up to 2.64 bit/s/Hz (UL, 26.4 dB).
   - It is lower at two DL points, by 0.03 and 0.26 (13.4 and 18.4 dB). The
     cause is hazard 17, below.
3. **Prediction scores.**
   - P_mono held for all four curves. T1 DL has one 0.5% dip at 12.4 dB,
     within the tolerance.
   - P_bler held except at one point: T1 DL, 1.36 dB, BLER 0.143 against a
     bound of 0.138. There MCS 3 meets the target on the 12-symbol
     reference (TBLER 0.027), but the S-slot's 10-symbol TBs have TBLER
     0.309 (53 of 160 failed, against 27 of 401 for 13-symbol TBs).
4. **F3 pointwise check.** SE(x + 10.09 dB) ≥ SE(x) holds at every point of
   the raw and C-AMC curves, for both models and directions.
5. **Reported only.**
   - The analytic first-transmission envelope (ideal MCS choice, no HARQ) is
     at most 0.25 bit/s/Hz above C-AMC (T2 DL, 18.4 dB; ≤ 7% relative in
     T2 DL).
   - The TR 36.942 form still fits poorly (maximum error 0.50), so the
     empirical curve remains the form used.
   - For 5-RB TBs (the F3 allocation), C-AMC needs 0.18 dB more SNR than
     for 51 RB to reach SE 1.5: 8.84 vs 8.66 dB, analytic first
     transmission, both models.

**New 5G-LENA property: hazard 17 (recorded, not diagnosed further).**
- At fixed SINR and MCS, the EESM TBLER is **not monotone in TB size**.
  For example, T2 at 18.36 dB, MCS 17, 51 RB gives TBLER 0.061 (10
  symbols), 0.401 (12) and 0.0036 (13). The likely cause is code-block
  segmentation and table selection by code-block size; this was not
  isolated.
- Consequences:
  - no single reference TB size bounds the BLER of every allocation shape;
  - the 12-symbol rule is one MCS step conservative or optimistic at a few
    points (the two DL points and the P_bler point above);
  - P_mono confirms the effect is bounded: no drop above 10% anywhere.

**Decision applied (pre-registered).**
- E1.5.5 and E1c use the **C-AMC T2 curve** as the primary mapping.
- Sensitivities:
  - the raw E1.5.3 T2 curve, labelled "5G-LENA v5.1 SISO AMC, 1-symbol
    reference";
  - C-AMC T1;
  - TR 36.942.
- Hazard 15 handling is unchanged.
- E1.5.5's pre-registration must also fix whether F3 uses the 5-RB curve
  (+0.18 dB) and how the F3 ≥ F2 check S5 is re-verified.
- Upstream report 4 is drafted in
  `docs/upstream/5g-lena-siso-amc-one-symbol-tb.md`; filing is the user's
  decision.
- **Consequence for E1.5.4.** Its multi-UE runs use the stock AMC, so they
  carry the 1-symbol sizing. Its predictions must use the mapping the
  stack actually applies. The Stage A replay can compute that mapping for
  any allocation shape.

**E1.5.3 erratum 3 (found 2026-11-09 while preparing E1.5.4).** No result
is affected.
- The E1.5.3 config's `scheduler` field reads `ns3::NrMacSchedulerTdmaPF
  (single UE: every RB of every data symbol it is given)`. The whole string
  was passed on the command line.
- ns-3 split it at the spaces and ignored the extra words.
- `e15_run_info.csv` records `ns3::NrMacSchedulerTdmaPF` in all 150 E1.5.3
  runs and all 152 E1.5.3a runs (checked).
- E1.5.4 passes the TypeId alone.

**Caveat on C-AMC (from the ChatGPT v2 review, accepted).**
- TS 38.214's 12-symbol reference resource defines the **DL CQI**. It does
  not say that every transmission occupies 12 symbols.
- UL MCS selection is a gNB implementation choice.
- C-AMC is therefore a reference-resource-based link-adaptation model,
  applied in both directions. It is not the optimal MCS for every
  allocation shape (see hazard 17).

### Review of the ChatGPT E1.5.4 pre-registration review v2 (2026-11-09)

| Point | Verdict | Action |
|---|---|---|
| Four arms: A analog TDMA, B same-beam OFDMA, C gain-equivalent digital FDMA, D beamless | Agree. B is added as a diagnostic; its DL part was already shown in E1.5.4-0, and the UL part is new | Arms A, Ag (gain-folded A), C, Dt/Do, B |
| C must be labelled a gain-equivalent abstraction (no RF chains, CSI, calibration, beam frequency selectivity or inter-cell interference) | Agree | Label in config and results; no energy inference from C |
| F3 target 0.15 × 51/5 = 1.53 vs F2 1.50; the 0.18 dB 5-RB effect is separate | Agree. Already the E1.5.0 definition (`n · PRB_bw · SE / W ≥ 0.15`, n ≤ 5) | Analytic F3 thresholds use 1.53 (UL) and 2.295 (DL) |
| Stock AMC as primary in packet runs; C-AMC only as a separate, labelled sensitivity; no cross-comparison | Agree in principle, with one practical limit: `FixedMcs` is cell-wide, so C-AMC can only run when all UEs share one SNR | C-AMC runs are the homogeneous cell (H_TC) and the single-UE static partition (S_C). Each packet result is compared only with an analytic value under the same MCS rule (P2, P3). Stock runs (H_T, H_O, S, mixed drops) are compared with each other |
| Check the resource identity in DCIs; UL frequency sharing only counts with overlapping symbols and disjoint RBs | Agree. The new trace records every DL/UL DCI with its target slot (K0/K2) and RBG mask | Gate RES on every TB and every DCI pair; TDMA runs must show zero symbol sharing between UEs |
| Physical accounting: DL total power, UL ≤ 23 dBm on the granted RBs, no double-counted gain | Agree | Gate V checks every TB's SINR against the power-on-granted-RBs prediction, to 0.01 dB; gain-folded arms have isotropic gNBs |
| Full population; zero-throughput UEs kept | Agree | Gate POP; per-UE output lists every UE |
| Matched geometry, realizations, seeds and load across arms | Agree | Ag and C take arm A's per-UE gain from the same configuration and seed |
| Cluster/bootstrap by location; 27 positions only as a development set | Agree. E1.5.4 validates a model with one 10-UE drop per configuration; it makes no population claim | No population statistics in E1.5.4 |
| Failed arms stay NOT ESTABLISHED; failed raw outputs kept | Agree | Stop rule on any gate |

**What the smoke runs changed** (declared in the config, before
registration):
1. **5G-LENA's TdmaPF splits every slot among all UEs.** It hands out one
   symbol at a time to the best PF metric. With 10 UEs the TBs are 1–2
   symbols × 51 RBs.
   - The analog arm therefore runs with TB shapes different from the
     12–13 symbols of E1.5.3.
   - P2 compares H_TC with the analytic F2 under the same MCS rule.
2. **5G-LENA's dynamic UL OFDMA (OfdmaPF) does not behave like F3.**
   - Most UL slots go to one UE.
   - RB counts swing between 1 and 51.
   - First-transmission BLER is about 22%.
   - Hypothesis H18: the UL MCS comes from the per-RB SINR of the previous
     allocation. A larger new allocation spreads the UE's 23 dBm more
     thinly.
   - F3 is therefore tested at packet level as a **static partition** (part
     S: one UE, data on RBs 0–4, the exact F3 allocation). Dynamic OFDMA is
     a separate prediction (P4), expected to fail.
3. **A 5-RB carrier never attaches.** Part S therefore uses the 51-RB
   carrier with notched-RBG masks, applied from the application start.

### E1.5.4: ten-UE F2/F3 test (PRE-REGISTERED 2026-11-09)

- Config: `configs/e1/e1_5_4_multiuser.json`.
- Script: `scripts/e1_5_4_multiuser.py`.
- Tests: `tests/test_e1_5_4_multiuser.py`.

**Common settings.**
- E1.5.1 settings, `NrEesmIrT2`.
- Saturating traffic per UE: DL 40 Mbit/s, UL 20 Mbit/s.
- Simulation 1.0 s; window 0.3–1.0 s.

**Per-user SE.** Decoded bits of the UE divided by the direction's used data
resources of the whole cell, which is the E1.3/E1.5.3 normalization. Part S
instead uses (51/5) × the UE's own resources. The per-user targets are
0.15 (UL) and 0.225 (DL).

| Part | Set-up | Role |
|---|---|---|
| H_T | 10 identical UEs, L = 96–134 dB (step 1), isotropic, TdmaPF, stock AMC | packet-level analog time share |
| H_TC | as H_T, MCS fixed by the C-AMC rule | F2 under the same MCS rule as E1.5.5 |
| H_O | as H_T, OfdmaPF | 5G-LENA dynamic FDMA |
| S / S_C | one UE, data on RBs 0–4, same L grid, stock / C-AMC (5-RB 12-symbol rule) | F3 static partition |
| M: A | 30° sector, 10 highest-loss positions; 3.5 GHz 8×16, 7.2 GHz 8×16, 7.2 GHz 16×32; seeds 1–3; fading channel, codebook beams, TdmaPF | analog, physical |
| M: Ag / C | gain-folded A (per-UE mean beam gain), TdmaPF / OfdmaPF | analog vs digital-FDMA abstraction |
| M: Dt / Do | losses without any gain, TdmaPF / OfdmaPF | beamless counterfactual |
| M: B | arm A at 7.2 GHz 8×16, shared beam, OfdmaPF | same-beam diagnostic |

**Gates** (any failure stops the stage):

| Gate | Requirement |
|---|---|
| R6a–R6d | Byte-identical reruns, which show the new options leave the old paths unchanged: E1.5.2-0 (link mode and nrfading DirectPath); 3 E1.5.3 runs; 2 E1.5.3a fixed-MCS runs; 3 E1.5.2-1 codebook-beam runs |
| E0 | Error model correct everywhere |
| RES | Every TB matches one DCI. No RB clashes. Zero cross-UE symbol sharing in TDMA runs. Part S data stays on RBs 0–4 |
| V | Every TB's SINR matches the prediction to 0.01 dB, with per-RB beam gains in nrfading runs |
| POP | Every UE is attached, and present in the output |
| BEAM | Arms A and B hold their installed beams |
| FIX | Fixed-MCS runs use the fixed MCS on every TB |

**Predictions** (scored):

| Prediction | Statement |
|---|---|
| P1 | TDMA shares in [0.09, 0.11] in H_T and H_TC |
| P2 | H_TC thresholds within 1 dB of analytic F2: UL 8.66 dB, DL 12.92 dB |
| P3 | S_C thresholds within 1 dB of analytic F3: UL −1.27 dB full-band, DL 13.34 dB |
| P4 | H_O UL threshold within 1 dB of S (expected to fail) |
| P5 | Median per-UE SE ratio A/Ag < 1 (frequency selectivity costs SE) |
| P6 | H18: first-transmission UL BLER is at least 0.10 higher after an RB-count increase than otherwise, pooled over H_O runs with UL SNR ≥ 0 dB |

**Decisions for E1.5.5 (fixed now).**
- **F2:** analytic C-AMC F2 if P2 holds; otherwise the H_TC packet
  thresholds.
- **F3:** analytic C-AMC F3 if P3 holds; otherwise the S_C thresholds.
  F3 is labelled "static frequency partition with UE power concentration;
  digital/hybrid gNB; gain-equivalent abstraction".
- **If P4 fails:** hazard 18 is recorded. F3 is a scheduler-dependent bound
  that dynamic OfdmaPF does not deliver.
- **If any P5 median is below 0.9:** E1.5.5 carries a frequency-selectivity
  sensitivity.

**Limits.**
- One cell, no inter-cell interference.
- Full buffer, static channels, one layer, UE 1×1.
- Ideal control channels.
- One drop per configuration from the 27-position development set.

#### E1.5.4 results (run 2): all gates PASSED; P2, P3, P5, P6 held; P1 and P4 failed (2026-11-09)

**Run 1 was interrupted, not failed.**
- It ran from `dacf586` and passed R6a–R6d.
- It was then killed when the Claude Code session ended, with 73 of 217
  first-batch runs finished (none failed).
- Its runs are reported only after a whole batch completes, so no measured
  result was produced or seen. Its log is kept as
  `run1_interrupted_stdout.txt`.

**Run 2** is the same commit and configuration, restarted from scratch in a
detached session.
- 4 regression gates (R6a–R6d) and 235 measured runs (217 + 18), in
  35 min.
- Program SHA-256 `606911a5866f…`.
- Files: `results/phase2/e1_5_4_multiuser/` (validation, summary,
  per_ue.csv, manifest; raw local).

| Gate | Result |
|---|---|
| R6a–R6d | all byte-identical: E1.5.2-0 98/98 files; 3 E1.5.3 runs; 2 E1.5.3a fixed-MCS runs; 3 E1.5.2-1 codebook runs |
| E0, POP | all 235 runs |
| RES | all 235 runs: every window TB matched one DCI; no RB clash; zero cross-UE symbol sharing under TdmaPF; part S data only on RBs 0–4 |
| V | all 235 runs: maximum SINR error 2.8e-14 dB, including UL with real arrays and codebook beams (arm A) |
| BEAM, FIX | 10 nrfading runs; 78 fixed-MCS runs |

Thresholds are full-band SNR in dB at which the mean per-user SE reaches
0.15 (UL) or 0.225 (DL). All values are generated from `summary.json`.

| Part | UL threshold, mean UE (min UE) | DL threshold, mean UE (min UE) |
|---|---|---|
| H_T: 10-UE TdmaPF, stock AMC | 8.83 (8.85) | 13.28 (13.36) |
| H_TC: 10-UE TdmaPF, C-AMC | 8.83 (8.84) | 13.47 (13.53) |
| H_O: 10-UE OfdmaPF (dynamic), stock AMC | 19.41 (19.51) | 13.25 (13.32) |
| S: static 5-RB partition, stock AMC | -0.02 (-0.02) | 14.32 (14.32) |
| S_C: static 5-RB partition, C-AMC | -1.02 (-1.02) | 12.70 (12.70) |
| analytic F2 (C-AMC) | 8.66 | 12.92 |
| analytic F3 (C-AMC) | -1.26 | 13.34 |

| Prediction | Measured | Verdict |
|---|---|---|
| P1 TDMA shares in [0.09, 0.11] | range 0.067-0.132 | FAILED |
| P2 H_TC vs F2 within 1 dB | UL +0.17 dB, DL +0.55 dB | held |
| P3 S_C vs F3 within 1 dB | UL +0.24 dB, DL -0.64 dB | held |
| P4 H_O UL vs S UL within 1 dB | 19.41 vs -0.02 dB (+19.44) | FAILED (expected) |
| P5 median A/Ag < 1 | 0.9932-0.9995 over 6 configuration-directions | held |
| P6 H18 BLER gap >= 0.10 | 0.402 (3839/9557) after an RB increase vs 0.054 (1059/19563) | held |

Mixed drop: per-UE prediction (F2 for A, Ag, Dt; F3 for C, Do; analytic
C-AMC) against measured satisfaction (90 UE-cases per arm with beams, 60
beamless):

| Arm | direction | predicted & delivered | predicted, not delivered | not predicted, delivered | neither |
|---|---|---|---|---|---|
| A | UL | 67 | 7 | 0 | 16 |
| A | DL | 90 | 0 | 0 | 0 |
| Ag | UL | 71 | 3 | 0 | 16 |
| Ag | DL | 90 | 0 | 0 | 0 |
| C | UL | 51 | 38 | 0 | 1 |
| C | DL | 90 | 0 | 0 | 0 |
| Dt | UL | 30 | 0 | 0 | 30 |
| Dt | DL | 36 | 0 | 3 | 21 |
| Do | UL | 30 | 3 | 0 | 27 |
| Do | DL | 33 | 3 | 0 | 24 |
| B | UL | 3 | 2 | 0 | 5 |
| B | DL | 5 | 0 | 0 | 5 |

What is established (5G-LENA v5.1, single cell, full buffer):

1. **F2 holds at packet level.** In a 10-UE TdmaPF cell, the per-user SE
   reaches the F2 targets within 0.17 dB (UL) and 0.55 dB (DL) of the
   analytic C-AMC thresholds. This holds although 5G-LENA splits every slot
   into 1–2-symbol TBs, not the 12–13-symbol TBs of E1.5.3.
   - Stock and C-AMC agree in UL (8.83 dB both).
   - In mixed-SNR cells, the gain-folded TDMA arm Ag matches the
     homogeneous-cell curve: median per-UE ratio 1.000 (DL) and 0.997 (UL).
     PF gives equal time shares whatever the other UEs' SNRs.
2. **F3 holds at packet level as a static partition.** One UE holding RBs
   0–4 continuously, with its 23 dBm concentrated there, reaches the F3
   targets within 0.24 dB (UL) and 0.64 dB (DL) of the analytic thresholds
   under the same MCS rule.
   - The UL gain over F2 is real at packet level: about 9.8 dB lower UL
     threshold (−1.02 vs 8.83 dB).
3. **5G-LENA's dynamic UL OFDMA does not deliver F3 (hazard 18).**
   - OfdmaPF needs 19.4 dB for the UL target: 10.6 dB **worse** than TDMA
     and 19.4 dB worse than the static partition. DL is unaffected (13.25 dB).
   - Mechanism (P6, scored): first-transmission UL BLER is 0.40 after a
     UE's RB count grows and 0.054 otherwise. This fits the UL MCS being
     taken from the per-RB SINR of the previous, smaller allocation.
   - In the mixed drop, OFDMA (arm C) delivers the UL target to 51 of the
     89 UE-cases that F3 predicts. TDMA (Ag) delivers 71 of the 74 that F2
     predicts.
   - So the F3 benefit depends on the scheduler. A real NR scheduler would
     need power-headroom-aware UL link adaptation (or semi-static
     allocation), which this 5G-LENA version lacks.
4. **The analog stack with real arrays matches the gain-folded model in
   median.**
   - A/Ag median per-UE SE ratio is 0.993–0.9995, and UL and DL mean beam
     gains are identical (|difference| 0 dB in all 9 drops). So by the
     pre-registered rule, E1.5.5 needs no selectivity sensitivity.
   - *Post hoc, not pre-registered:* for UEs not at saturation, the A/Ag UL
     ratio has a median of 0.76–0.91 and goes down to 0. In the confusion
     table A loses 4 more UL UEs than Ag (7 vs 3 predicted-but-not-delivered).
   - Frequency selectivity therefore matters for **marginal UL links**.
     E1.5.5's pre-registration should carry it as a sensitivity for links
     near the UL threshold.
5. **P1 failed only where UL delivers nothing.** All 28 violating
   run-directions are UL at full-band SNR ≤ −1.6 dB, where every UL
   transmission fails (first-transmission BLER 0.965–1.0) and per-user SE is
   0. There, TdmaPF shares spread to 0.067–0.132. Wherever UL data is
   delivered, shares stay within [0.09, 0.11]. The prediction stays FAILED
   as registered; it changes no threshold.
6. **Arm B (shared beam, OFDMA, diagnostic).**
   - DL and UL frequency multiplexing occurs on disjoint RBs: 10,368
     symbol-sharing DCI pairs, with RES and V passing.
   - Every UE receives grants. The 5 UEs far from the shared beam (UL SNR
     −12 to −41 dB) deliver nothing in UL, and 3 of them nothing in DL.
   - PF still gives them equal shares, so the in-beam UEs reach only
     0.26–0.37 (DL) per-user SE. This is the cost of one analog beam serving
     a spread-out cell.

**Decisions applied (pre-registered).**
- **E1.5.5 F2:** analytic C-AMC thresholds (UL 8.66, DL 12.92 dB). H_TC
  (8.83 / 13.47 dB) is the sensitivity.
- **E1.5.5 F3:** analytic C-AMC thresholds (UL −1.26 full-band, DL
  13.34 dB). S_C (−1.02 / 12.70 dB) is the sensitivity.
  - F3 is labelled "static frequency partition with UE power concentration;
    digital or hybrid gNB; gain-equivalent abstraction".
  - It is a scheduler-dependent bound: 5G-LENA's dynamic OfdmaPF does not
    reach it (hazard 18).
- **Selectivity:** no sensitivity is required by the pre-registered median
  rule. Item 4 recommends one for marginal UL links in E1.5.5's own
  pre-registration.

**Hazard 18 (recorded).** 5G-LENA v5.1 `NrMacSchedulerOfdmaPF` uplink,
full buffer, 10 UEs in one beam group:
- RB counts per UE swing between 1 and 51;
- first-transmission BLER is 0.40 after an RB-count increase;
- the per-user UL SE target needs 19.4 dB, against 8.8 dB with TdmaPF.

It is a candidate fifth upstream report (draft not yet written).

### E1.5.5: reclassification of the E1.4 records (PRE-REGISTERED 2026-11-09)

- Config: `configs/e1/e1_5_5_reclassification.json`.
- Script: `scripts/e1_5_5_reclassification.py`.
- Tests: `tests/test_e1_5_5_reclassification.py`.
- `cmwave/radio/link_budget.py` gains an optional `se_fn`. With the default,
  E1.3/E1.4/E1.5.0 are unchanged: a unit test checks array equality, and
  gate X1 checks every E1.4 fraction.

**What changes relative to E1.4.** Each input below is the pre-registered
decision of the E1.5 stage named in brackets.

| Input | E1.4 | E1.5.5 primary (V2) | Sensitivities |
|---|---|---|---|
| gNB gain | ideal steering | E1.4 gain − D_cb per array, carrier and stratum (E1.5.2-1). O2O LOS/NLOS for outdoor and in-car, O2I for indoor. Regime C: 4.5 dB on outdoor-NLOS links, since C is unmeasured (E1.5.2-1 rule) | D_dp, D_eig, D_cb CI bounds; regime C with matched measured arrays |
| SNR → SE | TR 36.942 | C-AMC T2 curve, piecewise linear, 0 below its floor (E1.5.3a) | C-AMC T1, raw 5G-LENA T2, TR 36.942, C-AMC with TR's low end (hazard 15) |
| F1 floor | −10 dB | the mapping's floor: DL −1.64, UL −0.64 dB | TR's −10 dB (variant m_trlow) |
| F2 | as defined | unchanged definition (E1.5.4 P2 held) | E1.5.4 packet thresholds (H_TC) |
| F3 | E1.5.0 (DL = F2) | UL as E1.5.0. DL uses the PRB-granular share n_max/N, as E1.5.4's analytic F3 (E1.5.4 P3 held) | E1.5.4 packet thresholds (S_C) |
| Other | — | — | selectivity factor r_UL 0.757, r_DL 0.946 (E1.5.4 post hoc), regimes A and B; O2I V19.5.0 |

**Settled before registration (curve only, no record evaluated).**
- For every mapping and N_PRB 51, 273 and 546, the E1.3 "best over n PRBs"
  UL allocation never passes the F2 or F3 threshold when the full
  allocation fails.
- So E1.4's definition and the E1.5.4-validated full-band and n = 5
  definitions classify identically. Gate X7 checks this for every record.

**Gates** (any failure stops the stage):

| Gate | Requirement |
|---|---|
| X0 | Every input stage validated; E1.4 config and summary hashes match |
| X1 | V0 (TR, ideal gain) reproduces every E1.4 fraction exactly. Dry run: 0 differences |
| X2 | V0 reproduces every E1.5.0 F3 fraction exactly. Dry run: 0 differences |
| X3 | Every mapping is exact at its grid points and 0 below its floor; C-AMC T2 is non-decreasing |
| X4 | Every applied gain equals E1.4 − D, recomputed independently |
| X5 | Classes K1–K4, K6 partition every record; K6 = 0 in shared states |
| X6 | The E1.5.0 F2 ⇒ F3 bound holds for every variant with one monotone SE function |
| X7 | Allocation identity (above) |
| X8 | Mix weights valid |

**Predictions** (scored):

| Prediction | Statement (M2412 mix) |
|---|---|
| P1 | F2 UL feasibility falls from V0 to V2 for A and B at both carriers. The 4.7–6.9 dB NLOS/O2I deficit outweighs the 2.3 dB easier UL threshold in an 80%-indoor mix |
| P2 | F2 7.2 GHz DL feasibility falls for A and B |
| P3 | Under V2, K3 is smaller with F3 than with F2 for A, B and C |

**Reported.**
- Every variant × case × state × profile.
- Transition matrices V0 → V2.
- Attribution in both orders:
  - V0 → V1 (gain) → V2 (mapping);
  - V0 → V1b (mapping) → V2 (gain).
- The hazard-15 label: records that close only with TR's low end.
- A sensitivity table, `sensitivity.csv`.

**Decision (fixed now).** If every gate passes, V2 is the E1.5
classification for E1c and the paper. E1.4's numbers are superseded and
kept as V0.

**Limits.**
- Single site, SNR not SINR, link-budget level.
- UE MRC as an ideal mean-SNR gain; E1.5's packet runs used 1 Rx.
- Regime C's gain and its 100/200 MHz behaviour are not measured.
- C-AMC was measured at 20 MHz with 51-RB TBs.
- The F1 floors were measured on 51-RB TBs but are applied to 1-PRB SNR.
- The 27 positions × 200 realizations are not area-weighted.

## 6. Order and stop rules
E1c (area maps) and the energy controller do not start until E1.5.1-E1.5.4 have passed or their disagreements are explained. A stage that fails stops the sequence until the cause is found and recorded.
