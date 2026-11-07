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

## 5. Order and stop rules
E1c (area maps) and the energy controller do not start until E1.5.1-E1.5.4 have passed or their disagreements are explained. A stage that fails stops the sequence until the cause is found and recorded.
