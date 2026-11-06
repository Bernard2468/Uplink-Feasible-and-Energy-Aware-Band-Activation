# Phase 2: integrated radio validation (protocol)

Origin: v0.3 protocol (2026-11-06), revised the same day after its claims were
checked against the example source, the ns-3.48 Sionna RT model source and
actual runs. Results of each gate are recorded in section 6.

## 0. Rules carried over from Phase 1

- Reuse the verified build in `~/6g-external/ns-3-dev`: ns-3 `d083a17a`
  (ns-3.48 + MR !2929 backport), NR `cedceadd`. Never rebuild for a run.
- Every run records versions, the exact command line, exit status and runtime.
  `scripts/phase2_sionna_gate.sh` does this automatically.
- A failed run keeps its log and stops the gate. Missing results are never
  replaced by estimates.
- Claims are limited to what a gate measured. Software gates make no claims
  about 6G performance, coverage or uplink feasibility.

## 1. Gate E0: official integrated example, unmodified

Facts verified in `contrib/nr/examples/cttc-nr-demo-sionna-rt.cc` (NR `cedceadd`):

| Property | Value |
|---|---|
| Traffic | Downlink only: `UdpClient` on the remote host, `UdpServer` on UEs |
| Topology | 1 gNB at 10 m height; 2 UEs at 1.5 m height dropped in a 3 m x 3 m area |
| Ray tracing | `simple_street_canyon_with_cars`; `maxDepth=1`, LOS only; no reflection, diffraction or refraction; seed 49 |
| Channel update | `SionnaRtChannelModel::UpdatePeriod` = 50 ms |
| Antennas | Isotropic elements; gNB 4x8, UE 2x4; ideal direct-path beamforming |
| Carrier | 28 GHz, 50 MHz, numerology 4 (240 kHz SCS, not valid for FR1 data); `totalTxPower` 35 dBm |
| Time | `simTime` 500 ms, applications start at 400 ms: 100 ms of traffic |

The example's own frequency check `NS_ABORT_IF(f < 0.5e9 && f > 100e9)` can
never fire (`&&` instead of `||`). The effective range check is the assertion
in `SionnaRtChannelModel::SetFrequency` (0.5 to 100 GHz).

Runs (`bash scripts/phase2_sionna_gate.sh`):

| Tag | Arguments | Purpose |
|---|---|---|
| `reference_28ghz` | none | Reproduce the official example |
| `reference_28ghz_rerun` | none | Run-to-run determinism |
| `trial_3p5ghz_mu1` | `--centralFrequencyBand1=3.5e9 --numerologyBwp1=1` | Stack runs at 3.5 GHz |
| `trial_7p2ghz_mu1` | `--centralFrequencyBand1=7.2e9 --numerologyBwp1=1` | Stack runs at 7.2 GHz |

Numerology 1 (30 kHz SCS) is used for both trials because it is valid FR1
data numerology and the common choice for 3.5 GHz TDD carriers; 3.5 and 7.2
GHz must share one numerology to be comparable. Everything else stays at the
example defaults (50 MHz, 35 dBm, same arrays).

Pass criteria: `help.txt` lists every flag used; every run exits 0; the
FlowMonitor output exists and reports both flows with offered, received,
lost packets, throughput and delay; the rerun is identical (or the difference
is explained).

What E0 cannot show: anything about coverage (UEs are a few metres from the
gNB), uplink (no uplink traffic), or a fair frequency comparison (same element
count means a 4.2x smaller physical aperture at 7.2 GHz than at 3.5 GHz).

## 2. Gate E0b: the frequency dimension is physically correct

1. **The band frequency reaches the ray tracer.** NR sets the Sionna scene
   frequency with `SetAttributeFailSafe`, which fails silently, and the model
   default is 500 MHz. Run the example with
   `NS_LOG="SionnaRtChannelModel=level_info"` at 3.5 and 7.2 GHz and require
   `Scene created: frequency=<band frequency>` in the log. Repeat this check
   for every new scenario program.
2. **Sionna RT scales correctly with frequency.**
   `scripts/phase2_frequency_scaling_check.py`, at the gNB/UE positions printed
   in that log: LOS-only gain between isotropic co-polarised elements must equal
   free-space gain (lambda/4 pi d)^2, and the 3.5 to 7.2 GHz gap must equal
   20 log10(7.2/3.5) = 6.265 dB, both within 0.01 dB. The same script reports
   (does not assert) the gap with reflections and diffraction enabled.

## 3. Hazards in the ns-3.48 Sionna RT channel model

Found by reading `src/spectrum/model/sionna-rt-channel-model.cc`. Not relevant
to the LOS-only E0 runs, but each must be controlled in E1:

1. **Silent, persistent ray-depth reduction.** If a link returns more paths than
   `MaxNumberOfPaths` (default 90), the model decrements `maxDepth` on the
   shared configuration and recomputes. The reduction then applies to every
   later link of that channel object. It is reported only by `NS_LOG_WARN`,
   whose text ("excess paths will be ignored") misdescribes the action.
   Control: set `MaxNumberOfPaths` well above the expected path count, enable
   `SionnaRtChannelModel=level_warn` in every E1 run, and fail the run if the
   warning appears.
2. **Element pattern fallback.** Only `ThreeGppAntennaModel` maps to Sionna's
   `tr38901` pattern; every other element type becomes `iso` without warning.
3. **Polarisation fallback.** Single polarisation with a slant other than 0 or
   90 degrees becomes `V`; dual polarisation with a non-zero slant becomes
   `cross`.
4. **Orientation convention.** ns-3 bearing/downtilt `(alpha, beta)` is passed
   as Sionna `(yaw, pitch, 0)`. The downtilt sign convention is unverified:
   test it before any non-zero tilt is used.
5. **Frequency is set fail-safe** (see E0b.1).
6. **Cost.** Sionna RT is called per link and per `UpdatePeriod`, so runtime
   grows with the number of links times the number of updates.

## 4. Experiment E1: co-sited 3.5/7.2 GHz uplink/downlink feasibility (design)

Question: with both layers on the same site, where is the 7.2 GHz layer usable
in the downlink but not in the uplink, and why? This is the physical basis of
hypothesis H2 and of the feasibility map (regions: FR3 unnecessary /
beneficial / required / useless because uplink-limited).

Design (two tools, as decided in Phase 1):

- **E1a, main engine (Python + Sionna RT):** path gains per UE location for
  both carriers in one site-specific scene, then documented downlink and uplink
  link budgets and a rate mapping. This produces the coverage-grade maps.
- **E1b, validation (5G-LENA + Sionna RT):** a project-owned copy of the example
  (not an edit of upstream code) with uplink and downlink traffic, NR PHY
  traces (SINR per transport block) and fixed UE positions from near to edge,
  LOS and NLOS. E1a predictions are accepted only if they match E1b at these
  points within a tolerance fixed before the comparison.

Fairness: three antenna regimes, always reported separately.
(A) equal element count, which isolates propagation; (B) equal physical
aperture, about 4.2x more elements at 7.2 GHz; (C) deployment-realistic
configurations from cited sources. Fixed radiated power, fixed EIRP and fixed
aperture are different comparisons and are never mixed.

Controls: same site, scene, UE positions, TDD pattern and numerology; seeds
and runtimes recorded. Every parameter (powers, noise figures, bandwidths,
UE power class, body loss, outdoor-to-indoor loss) carries a citation before
E1 runs. Candidate sources: 3GPP TR 38.901 (channel and O2I models),
TS 38.101-1 (UE power classes), and the 3GPP Release 19 7-24 GHz studies.

## 5. Abstract Python controller: status after v0.3

- `threshold_online` and `hysteresis_online` are causal (decision at slot t uses
  observations up to t-1). The current-slot rule is kept only as the labelled
  oracle `threshold_current_slot_oracle`.
- Violations are counted and reported next to energy: equal energy with more
  violations is a worse policy, not an equal one.
- **Open: the default instance is still trivial.** With the default energy
  parameters the current-slot greedy oracle equals the DP optimum on 5/5 seeds
  (wake payback 8.33 s vs 60 s slots). `scripts/run_baselines.py` now prints a
  warning whenever this happens. No controller comparison is meaningful until
  Phase 3 grounds the energy model (3GPP TR 38.864 network energy saving power
  model: sleep states, transition times and energies) and ties the decision
  interval to a real mechanism (e.g. SCell activation or cell DTX/DRX).

## 6. Results log

### Gate E0: PASSED (2026-11-06)

Stack: ns-3 `d083a17a`, NR `cedceadd`, Python 3.12.16, sionna-rt 1.2.0,
mitsuba 3.7.1, drjit 1.2.0, LLVM 18.1.8; ns-3 tree clean. CPU only (Ryzen 5
3500U, WSL2). Files: `results/phase2/e0_sionna_gate/`.

`help.txt` lists every flag used. All runs exit 0.

| Run | Runtime | Flow 1 (10.24 Mbps offered) | Flow 2 (102.4 Mbps offered) |
|---|---|---|---|
| `reference_28ghz` | 326 s | 997/1000 pkts, 10.21 Mbps, 0.32 ms | 506/1000 pkts, 51.81 Mbps, 17.87 ms |
| `reference_28ghz_rerun` | 294 s | byte-identical output | byte-identical output |
| `trial_3p5ghz_mu1` | 251 s | 985/1000, 10.09 Mbps, 1.773 ms | 985/1000, 100.86 Mbps, 1.813 ms |
| `trial_7p2ghz_mu1` | 274 s | 985/1000, 10.09 Mbps, 1.775 ms | 985/1000, 100.86 Mbps, 1.829 ms |

Interpretation:

- The missing packets in the uncongested flows are end-of-simulation
  truncation, not radio loss: at 10,000 packets/s, 3 packets = 0.3 ms and 15
  packets = 1.5 ms of traffic still in flight, consistent with the mean delays.
- 3.5 and 7.2 GHz deliver identical throughput: UEs a few metres from the gNB
  saturate the MCS at both carriers. This is the expected null result. E0
  cannot measure frequency effects, which is why E1 is required.
- The 28 GHz reference loses half of Flow 2 with a growing queue (17.9 ms mean
  delay). Hypothesis: offered load (112.6 Mbps) exceeds one 50 MHz carrier at
  numerology 4 (the example defaults to a single band). Tested in E0b.

### Gate E0b: PASSED (2026-11-06)

`bash scripts/phase2_e0b_checks.sh`, 683 s. Files: `results/phase2/e0b_checks/`.

1. **Frequency reaches the ray tracer.** Each logged run shows the model
   constructed at its 500 MHz default and then set to the band frequency. All
   26 scene creations per run used the band carrier (3.5e9 and 7.2e9 Hz
   respectively). No path-count warning.
2. **Frequency scaling is exact.** gNB at (0, 0, 10) m; UEs at
   (2.094, 2.352, 1.5) and (2.108, 2.077, 1.5) m (9.06 and 9.00 m):

   | UE | LOS gain 3.5 GHz | LOS gain 7.2 GHz | Free space (3.5 / 7.2) | Gap | With reflections + diffraction |
   |---|---|---|---|---|---|
   | 1 | -62.476 dB | -68.741 dB | -62.476 / -68.741 dB | 6.265 dB | gap 6.073 dB, 145/165 paths |
   | 2 | -62.414 dB | -68.680 dB | -62.414 / -68.680 dB | 6.265 dB | gap 6.086 dB, 145/165 paths |

   Expected gap 20 log10(7.2/3.5) = 6.265 dB; tolerance 0.01 dB.
3. **Overload confirmed.** 28 GHz reference with only `lambdaBe=4000`: Flow 2
   offered 40.96 Mbps delivers 398/400 packets at 0.51 ms mean delay (was
   506/1000 at 17.87 ms with 102.4 Mbps offered); Flow 1 unchanged (997/1000).
   The reference loss is offered load above single-carrier capacity, not a
   radio failure.

Consequence for E1: with reflections and diffraction (max depth 3), this small
scene already yields 145 to 165 paths per link (counted with single elements
in standalone Sionna RT), above the ns-3 default `MaxNumberOfPaths` of 90.
Hazard 1 (section 3) is therefore expected to trigger in E1 unless the limit is
raised and the warning is treated as a run failure.
