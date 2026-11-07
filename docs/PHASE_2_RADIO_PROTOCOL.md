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

Found in sionna-rt 1.2.0 itself during E1a (apply to standalone Python AND to
ns-3, which calls the same solver):

7. **Only first-order diffraction** (`path_solvers/sb_candidate_generator.py`:
   "Only first order diffraction is supported"), sampled with probability 0.2
   per valid wedge. Paths needing two or more diffractions do not exist.
8. **Wedges are detected within one mesh.** Edges where two different materials
   meet (Munich: metal roofs, marble walls) are free edges and are diffracted
   only with `edge_diffraction=True`.
9. **Slab reflection is frequency-selective.** Reflection and transmission use
   the ITU-R P.2040 single-layer slab model with the material thickness
   (Munich default 0.1 m for every material), including internal multiple
   reflections.
10. **Process stability.** Repeated solves in one Python process aborted in
    Dr.Jit's thread pool (`nanothread queue.cpp:357 remain == 1`); each solve
    now runs in a fresh process.

Found in the ns-3.48 TR 38.901 propagation loss model during E1.4
(`src/propagation/model/three-gpp-propagation-loss-model.cc`):

11. **Model switch at 6 GHz.** Below 6 GHz, `ThreeGppUmaPropagationLossModel`
    uses a shadowing std of 7 dB for EVERY link (`GetShadowingStd`: 7.0 if
    `m_frequency < 6e9`, otherwise 4 / 6 dB for LOS / NLOS), and the O2I loss
    switches to the legacy single-frequency model (`GetO2iSub6GhzPenetrationLoss`;
    TR 38.901 Table 7.4.3-3) instead of the frequency-dependent low/high-loss
    model used at 7.2 GHz. A 3.5 / 7.2 GHz pair in ns-3 therefore differs in
    shadowing statistics and O2I model, not only in frequency. Control for
    E1.5: disable ns-3 shadowing (`ShadowingEnabled=false`) and building
    penetration (`BuildingPenetrationLossesEnabled=false`), and impose the
    E1.2/E1.4 losses, which use one model at both carriers.

Found in the ns-3 build system during E1.5.1 (2026-11-07):

12. **Any CMake re-run rebuilds the whole tree.** Re-running CMake (explicitly,
    or by ninja when the set of scratch `*.cc` files changes) regenerates the
    precompiled-header target, which every object depends on. Adding the first
    project program therefore recompiled about 1,200 objects. Same sources,
    same cache and the same "default" profile, so the libraries are
    functionally identical; this is re-verified, not assumed (E1.5 results
    log). Control: `scripts/e1_5_install_ns3_program.sh` rebuilds an existing
    program without CMake, requires `ALLOW_RECONFIGURE=1` for a new program
    file, and refuses a build whose dry run shows anything but the program.
    All later E1.5 stages are modes of the same program file.

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

### E1a.1 propagation at fixed positions: CHECKS FAILED, cause identified (2026-11-06)

Setup. Munich scene, gNB at the Sionna tutorial site (8.5, 21, 27) m, 1 m above
a roof with open sky (verified by ray test). 27 outdoor UE positions at 1.5 m
from `scripts/e1a_select_positions.py` (seed 20261106, 4 m grid, open sky,
on ground, 2 m wall clearance; stratified by distance bin and geometric LOS),
stored in `configs/e1/positions_munich_tx0.json`. Single isotropic elements.
`scripts/e1a_propagation.py`, outputs in `results/phase2/e1a_propagation/`.

Scene LOS statistics (all 30,888 open outdoor grid points vs TR 38.901 UMa,
area-weighted per bin): 20-50 m 0.64 vs 0.78; 50-100 m 0.66 vs 0.46;
100-200 m 0.15 vs 0.20; 200-300 m 0.00 vs 0.09; 300-450 m 0.00 vs 0.05;
450-600 m 0.00 vs 0.035. Raising the gNB to 30 or 35 m removes rooftop
self-shadowing but not the far-range NLOS (`site_shadowing.txt`): it is caused
by the city geometry.

Pre-registered checks: C1 (LOS exactness) PASS, C4 (monotone) PASS;
C2 FAIL (10 of 18 NLOS positions without paths at one or both carriers),
C3 FAIL with 3x rays (84% within 0.5 dB, max 5.8 dB), C5 reciprocity FAIL
(71% within 0.5 dB, max 22.7 dB). Thresholds were not changed.

Valid results (LOS links, 9 positions, converged):
- Ray tracing agrees with the TR 38.901 UMa LOS median: mean residual +0.02 dB
  (3.5 GHz) and -0.74 dB (7.2 GHz).
- LOS frequency gap 3.5 -> 7.2 GHz (incoherent total gain): 5.50 dB mean with
  the scene's 0.1 m walls, versus 6.31 / 6.47 / 6.20 dB with 0.2 / 0.3 / 0.5 m
  walls (free space and TR 38.901: 6.27 dB). The 0.1 m slab makes 7.2 GHz
  reflections relatively too strong (hazard 9): a scene-parameter artefact.

Diagnosis of the NLOS failure (`scripts/e1a_diffraction_diagnostic.py`,
`results/phase2/e1a_diffraction_diagnostic/`, 18 NLOS positions):

| Variant (3.5 GHz unless noted) | NLOS links with paths |
|---|---|
| MAIN (edge diffraction off), z = 27 m, 1e6 rays | 8/18 |
| + edge diffraction | 11/18 |
| + edge diffraction, 4 m mast (z = 30 m) | 12/18 |
| same with 5e6 rays (3.5 and 7.2 GHz) | 14/18 |

Up to about 80 m, NLOS gains agree with the TR 38.901 UMa NLOS median within
about 3 dB (ue03, ue11). Beyond about 150 m, every variant is 15 to 60 dB below
the median or finds nothing, and results still change with the ray count.
NLOS beyond 100 m covers about 96% of the open outdoor area of this site.
Cause: hazards 7 and 8 (single diffraction; stochastic discovery of weak
paths). Conclusion: with sionna-rt 1.2.0 on CPU, the path solver is not a
valid propagation engine for street-level NLOS macro coverage at this site.
The same applies to E1b, which uses the same solver inside ns-3 (NLOS UEs
would receive no channel). Decision on the propagation engine: section 7.

## 7. Decision: propagation engine for E1 (2026-11-06)

**DECIDED (user, with independent concurrence): geometry-conditioned TR 38.901
UMa as the primary large-scale propagation model; Sionna RT 1.2.0 retained,
unchanged, for diagnostics and selected-link validation.** This is a
methodological revision prompted by the E1a.1 failure of the NLOS path,
convergence and reciprocity criteria (section 6). Those results are preserved
unchanged in `results/phase2/e1a_propagation/` and
`results/phase2/e1a_diffraction_diagnostic/`. Model sources and departures
from the standard: `docs/E1_PARAMETERS.md`.

Revised E1 sequence (each step gated by its own checks):

- **E1.1 Freeze the geometry.** Munich scene, gNB (8.5, 21, 27) m, the 27
  existing UE positions, and a deterministic LOS/NLOS mask over all open
  outdoor grid points, with the geometry tool versions recorded. Both carriers
  use the same geometry.
- **E1.2 Propagation model.** TR 38.901 UMa path loss by geometric state;
  shadow fading with UMa std, exponential spatial correlation (37 m LOS,
  50 m NLOS, separate LOS and NLOS fields) and one shared normal variate per
  link across carriers (clause 7.6.5). Shadowing is kept separate from the
  deterministic geometry; ray-traced and 3GPP losses are never added for the
  same mechanism.
- **E1.3 Link budgets.** Outdoor, single element, equal bandwidth first; DL and
  UL computed independently (noise = -174 + 10 log10(B) + NF of the receiver).
  SINR only once interferers are modelled.
- **E1.4 Antennas and deployment.** Antenna regimes A/B/C separately; then
  TR 38.901 O2I loss and the M.2412 indoor share, with stated indoor geometry.
- **E1.5 ns-3 validation.** Fixed-position UL and DL packet simulations with
  the same channel states; tolerances fixed before comparison. ns-3 draws
  shadowing independently per propagation-model instance (one per carrier):
  E1.5 must disable ns-3 shadowing and impose the shared E1.2 values, or the
  carriers get different random shadowing.

Non-negotiable controls: single-site results are a demonstration, not a
general macro-cell result (more sites in E1c); shared geometry and seeds across
carriers; no "beneficial", "required" or "energy-optimal" labels in E1, which
measures physical feasibility only.

### E1.1 geometry frozen: PASSED (2026-11-06)

`scripts/e1_geometry.py` -> `results/phase2/e1_geometry/` (mask .npz, links
.csv, manifest with SHA-256 of every file). 30,888 open outdoor grid points
(826 LOS) and the 27 fixed positions. Checks: G1 LOS state of the 27
positions recomputed = stored; G2 all 27 lie on the grid; G3 per-bin census
identical; G4 mask bit-identical on recomputation. The position-selection
code was refactored into one shared function first; regenerating the
positions reproduced the committed file exactly. ue04 and ue05 share a
distance (36.22 m) by grid symmetry but are distinct points; no duplicates.

### E1.2 propagation model: PASSED (2026-11-06)

`cmwave/radio/uma_hybrid.py` (7 unit tests; 28/28 project tests pass) and
`scripts/e1_propagation_3gpp.py` -> `results/phase2/e1_3gpp_propagation/`
(200 realizations, seed 20261107). Checks: P1 path loss equals an independent
re-evaluation of the TR 38.901 formulas; P2 the 3.5 -> 7.2 GHz gap is
realization-independent and equals 6.2653 dB on all 27 links (all LOS links
lie inside the breakpoint distance, about 607 m at 3.5 GHz); P3 pooled
shadowing std 3.92 dB LOS (n=1800) and 6.01 dB NLOS (n=3600); P4 LOS state
preserved. Reported: on the links where ray tracing was shown valid, ray
tracing minus UMa median is +0.02 / -0.74 dB (LOS, n=9, 3.5 / 7.2 GHz) and
+2.40 / +2.81 dB (near-NLOS ue03 and ue11).

### E1.3 single-link budgets: PASSED (2026-11-06)

Pre-registered in `configs/e1/e1_3_link_budget.json` (commit 472e094, before
any result): parameters with sources, TR 36.942 V19.0.0 Table A.1 rate
mapping, feasibility profiles F1 (link closure, SNR >= -10 dB, UL on 1 PRB;
primary) and F2 (M.2410-equivalent, derived: SE >= 2.25 DL / 1.5 UL
bit/s/Hz), classes K1-K5, checks L1-L8. The first run FAILED check L1: for
uplink rows without any achievable rate the record paired the 1-PRB SNR with
full-band noise (error exactly 10 log10(N_PRB)); a reporting bug, verdicts
unaffected (`validation_run1_failed_L1.json` kept). Fixed in 13360ff with a
regression test; results regenerated from committed code are byte-identical.
All checks L1-L7 pass in all three cases; 37/37 unit tests pass; an
independent hand calculation reproduces every median SNR within 7e-10 dB.
Files: `results/phase2/e1_3_link_budget/` (64,800 records).

Structure of the main case (equal 20 MHz, isotropic, no interference):
DL SNR = 138.36 - L; UL SNR on 1 PRB = 136.44 - L; UL SNR on the full band =
119.36 - L (L = total loss, dB). The full-band DL/UL difference is 19.00 dB
(21 dB transmit power minus 2 dB noise figure); the carrier difference is the
model's 6.27 dB. Under F2 both directions need SNR >= 10.95 dB, so the 7.2 GHz
downlink works up to L(7.2) = 127.4 dB while the uplink needs about 108.4 dB:
K2 ("7.2 GHz DL usable only with the 3.5 GHz UL") is a loss window exactly
6.27 dB wide, and K3 ("7.2 GHz DL works, no UL meets F2 on either carrier")
a window 12.7 dB wide.

| Case, profile | DL 3.5 | UL 3.5 | DL 7.2 | UL 7.2 | K1 | K2 | K3 | K4 |
|---|---|---|---|---|---|---|---|---|
| main 20 MHz, F1 | 1.000 | 0.999 | 0.995 | 0.990 | 0.990 | 0.005 | 0.000 | 0.005 |
| main 20 MHz, F2 | 0.860 | 0.577 | 0.756 | 0.528 | 0.528 | 0.049 | 0.180 | 0.244 |
| 7.2 GHz NF of Config B, F2 | 0.860 | 0.577 | 0.707 | 0.510 | 0.510 | 0.066 | 0.131 | 0.293 |
| equal 100 MHz, F2 | 0.855 | 0.519 | 0.751 | 0.454 | 0.454 | 0.064 | 0.232 | 0.249 |

K5 (7.2 GHz UL feasible without its DL) is 0 everywhere; K1-K4 partition all
outcomes. F2 by distance (main): K2 peaks at 50-100 m (10.4%); K3 dominates
at 200-300 m (64.2%); K4 reaches 96.5% at 450-600 m.

What E1.3 shows: link closure (F1) is not the limit in this baseline; at a
service level (F2) the uplink is the binding direction on BOTH carriers, and
the region where a 3.5 GHz uplink rescues the 7.2 GHz downlink is narrow
(6.27 dB of loss) compared with the region where no uplink meets the service
level. Widening the band at constant PSD (100 MHz) enlarges K3: the UE's
fixed power is spread over more spectrum.

What E1.3 does NOT show (stated with every use of these numbers):
fractions are over 27 distance-stratified positions x 200 shadowing
realizations, NOT area-weighted coverage (E1c); single isotropic elements, so
no array gain (E1.4 will shift every threshold); no interference (SNR, not
SINR); no O2I loss; E-UTRA-era rate mapping (E1.5 calibrates); F2 is a derived
equivalence, not an ITU requirement; the 6.27 dB carrier gap is a model
assumption.

### E1.4 antenna regimes and O2I deployment: PASSED after amendment 1 (2026-11-07)

Pre-registration: `configs/e1/e1_4_antennas_o2i.json`, `cmwave/radio/antenna.py`,
`cmwave/radio/o2i.py`, the optional per-link gain in `cmwave/radio/link_budget.py`
(E1.3 regenerated byte-identically after the change), `scripts/e1_antennas_o2i.py`,
14 new unit tests (51/51 pass). Sources and labels: `docs/E1_PARAMETERS.md`.

Design. Same 27 links x 200 realizations, F1/F2 profiles and K1-K5 classes
as E1.3; all cases share one set of random draws (seed 20261108).
- Antenna model: TR 38.901 element (8 dBi, 65 deg), three sectors
  (30/150/270 deg), ideal steering of every element toward the geometric UE
  direction (upper bound; E1.5 measures the realised gain), UE MRC
  10 log10(n_rx) in DL, UE at 23 dBm e.i.r.p. in UL.
- Regimes, never pooled: A equal element count (8x16 per polarisation at
  both carriers); B equal gNB aperture (16x32 at 7.2 GHz, 6.02 of the
  6.27 dB gap); C the Nokia 2024 vendor evaluation configuration (100 MHz /
  53 dBm / 8x16 / 4 Rx at 3.5 GHz; 200 MHz / 53 dBm / 16x32 / 8 Rx at 7 GHz).
  Reference R0 (isotropic) must reproduce E1.3 exactly. Sensitivities: UE
  1 Rx (S1), 43 dBm at 7.2 GHz (S2), 4.5 dB NLOS beamforming loss (S3), fixed
  4-element subarrays with 10 deg tilt (S4), sectors rotated 60 deg (S5).
- Deployment states, reported separately: outdoor, in-car (9 +- 5 dB),
  indoor low-loss and high-loss (TR 38.901 Table 7.4.3-2 at BOTH carriers,
  O2I shadowing 7 dB, ground floor), plus indoor states with carrier-
  independent penetration and shadowing variates. M.2412 mix (20% in-car,
  64% low-loss, 16% high-loss) as an exact weighted combination.
- Checks N1-N10 with fixed tolerances (identities, reciprocity, E1.3
  reproduction, gain bounds, analytic carrier offsets, O2I components, draw
  statistics, partition, mapping, UL allocation).

Stated before the run (analytic, not pass criteria): carrier SNR offset
6.27 dB (A, outdoor), 0.24 dB (B and C, outdoor), +0.96 / +4.61 dB more for
low / high-loss buildings; DL minus full-band UL SNR 19.00 dB (R0), 25.02 dB
(A, B), 34.02 / 37.03 dB (C at 3.5 / 7.2 GHz).

Run history. Pre-registration committed in 7f3d402. Run 1 FAILED check N8 in
every case: classes K1-K4 did not cover all outcomes. Every uncovered outcome
lay in the carrier-independent sensitivity states, where 7.2 GHz can be the
better carrier on a realization (7.2 GHz DL and UL feasible, 3.5 GHz UL not);
there were none in the four shared states or the mixes. This was a defect of
the pre-registered class list, not of the code. Amendment 1 (bfff84b) adds
class K6 (7.2 GHz DL and UL feasible while 3.5 GHz is not feasible in both
directions) and makes N8 stricter: K1, K2, K3, K4, K6 must partition every
outcome AND K6 must be 0 in the shared states. Nothing else changed; K1-K5 are
identical in both runs. Kept: `validation_run1_failed_N8.json`,
`run1_partition_diagnostic.txt`.

Run 2 (code bfff84b, 45 s): all checks N1-N10 PASS in all 9 cases. Highlights:
- R0 reproduces E1.3 on all 21,600 records (SNR within 5e-10 dB, no flag
  mismatch, identical class fractions).
- Carrier offsets equal the pre-stated values within 7e-14 dB.
- K6 = 0 in every shared state; 335-650 outcomes per case in the independent
  states.
- A second run is byte-identical. Raw records (1,166,400 rows) are in `raw/`
  (git-ignored, SHA-256 in `manifest.json`).

Files: `results/phase2/e1_4_antennas_o2i/`.

Results, M.2412 deployment mix (20% in-car, 64% low-loss, 16% high-loss
indoor), fractions of link-realizations:

| Case | Profile | DL 3.5 | UL 3.5 | DL 7.2 | UL 7.2 | K1 | K2 | K3 | K4 |
|---|---|---|---|---|---|---|---|---|---|
| R0 isotropic | F2 | 0.597 | 0.349 | 0.499 | 0.243 | 0.243 | 0.106 | 0.150 | 0.501 |
| A equal elements | F1 | 0.999 | 0.994 | 0.992 | 0.976 | 0.976 | 0.016 | 0.000 | 0.008 |
| A equal elements | F2 | 0.962 | 0.702 | 0.908 | 0.592 | 0.592 | 0.110 | 0.206 | 0.092 |
| B equal aperture | F1 | 0.999 | 0.994 | 0.997 | 0.989 | 0.989 | 0.005 | 0.003 | 0.003 |
| B equal aperture | F2 | 0.962 | 0.702 | 0.948 | 0.682 | 0.682 | 0.020 | 0.246 | 0.052 |
| C vendor configuration | F1 | 0.999 | 0.994 | 0.998 | 0.989 | 0.989 | 0.005 | 0.003 | 0.002 |
| C vendor configuration | F2 | 0.968 | 0.598 | 0.956 | 0.534 | 0.534 | 0.064 | 0.359 | 0.044 |

K5 and K6 are 0 in every row. Per deployment state under F2, K2 is:
- regime A: 7.9% outdoor, 8.6% in-car, 10.4% indoor low-loss, 16.9% indoor
  high-loss;
- regime B: 0.3%, 0.4%, 1.2% and 7.1%.

By distance under F2 (mix), K3 is the largest class beyond 300 m in every
regime (A 54.9 / 52.7%, B 67.5 / 68.6%, C 82.1 / 78.5% at 300-450 / 450-600 m)
and already at 200-300 m in A (37.5%) and C (69.0%). In B at 200-300 m, K1
leads (50.2%, K3 41.3%).

Sensitivities (M.2412 mix, F2; shifts are relative to the parent case):
- S1, UE 1 Rx instead of 4 (relative to A): K1 and K2 unchanged; K3 drops
  from 20.6 to 14.6% and K4 rises from 9.2 to 15.1%. The UE receive array
  helps only the DL.
- S2, 43 dBm at 7.2 GHz (75 dBm e.i.r.p. per 100 MHz): K3 drops from 35.9 to
  29.0% and K4 rises from 4.4 to 11.2%.
- S3, 4.5 dB NLOS beamforming loss: K1 drops from 53.4 to 48.2%.
- S4, fixed 4-element subarrays: K1 drops to 49.2%.
- S5, sectors rotated 60 deg: K1 drops from 59.2 to 56.1% (orientation
  effect of about 3 points).
- Independent O2I variates per carrier change the indoor classes by up to
  6.4 points. B indoor low-loss K2 rises from 1.2 to 7.5%, and K6 appears
  (1.4-5.6%). The near-zero K2 of regime B therefore depends on fully correlated
  building loss across carriers.

What E1.4 shows:
1. With realistic arrays, link closure (F1) holds for 97.6-98.9% of
   outcomes on all four links.
2. At the M.2410-equivalent service level (F2), the 7.2 GHz DL is feasible
   for 91-96% of outcomes, but the UL meets F2 for only 60-70% at 3.5 GHz
   and 53-68% at 7.2 GHz. The binding constraint is the UE uplink on BOTH
   carriers.
3. The outcome where a 3.5 GHz UL makes an otherwise infeasible 7.2 GHz
   carrier usable (K2) is 2-11% of the mix, depending on the antenna regime.
   It nearly vanishes under equal aperture outdoors (the 6.02 dB array gain
   cancels 6.02 of the 6.27 dB gap). Under equal aperture it survives
   mainly in high-loss buildings, because wall loss grows with frequency.
4. The larger class is K3: the 7.2 GHz DL works but no UL meets F2 on either
   carrier (21-36%). It grows when the DL/UL asymmetry grows (UE 4-8 Rx;
   53 dBm gNB in C), and F2 normalises the UL by bandwidth, so the 200 MHz
   carrier in C must carry twice the absolute UL rate. Band choice cannot
   remove K3; it is set by the UE power, building loss and distance.

What E1.4 does NOT show (stated with every use of these numbers):
- The fractions are over 27 distance-stratified positions, not area-weighted
  (E1c).
- Ideal beamforming and MRC are upper bounds (E1.5 measures the realised
  gain).
- No interference (SNR, not SINR).
- Indoor UEs are on the ground floor only.
- Regime C is a vendor evaluation configuration, adapted from 7 to 7.2 GHz.
- F2 is a derived equivalence.
- The 6.27 dB outdoor gap and the fully correlated O2I variates are model
  assumptions; the second is tested by the independent-variate sensitivity.

### Original options considered

Recommended (adopted): geometry-aware 3GPP model as the main engine. TR 38.901 UMa path
loss with the LOS/NLOS state of every location taken deterministically from
the scene geometry (not drawn at random), shadow fading correlated across the
two carriers, and frequency-dependent O2I loss; the same model in ns-3 for E1b
(ThreeGpp channel with the MR !2929 fix). Sionna RT is kept where it is shown
valid: site geometry and LOS map, converged LOS and near-NLOS links
(validation), and the wall-thickness frequency-selectivity analysis.

Alternative (not adopted): keep ray tracing as the main engine with much larger
ray counts (a GPU only for speed; CPU ray tracing works) and possibly
sionna-rt 2.x. Not recommended without first
showing convergence and multi-diffraction support; it also breaks version
parity with ns-3.48, which is tested with sionna-rt 1.2.0.
