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

### E1c.1 single-site area maps with the E1.5 model (PRE-REGISTERED 2026-11-09)

E1.5 is complete: E1.5.5 V2 is the validated classification. E1c is split:
- **E1c.1 (this stage)** is single-site, noise-limited and area-weighted,
  with building-resolved indoor UEs on every floor.
- **E1c.2** covers multiple sites and interference (SINR). It is
  pre-registered separately, because interference couples to Phase 3's
  activation decisions.

Files:
- Config: `configs/e1/e1c_area_maps.json`.
- Geometry: `scripts/e1c_geometry_indoor.py`, outputs in
  `results/phase2/e1c_geometry/`.
- Engine: `scripts/e1c_area_maps.py`.
- Tests: `tests/test_e1c_area_maps.py`.

**Geometry (run before registration; geometry only).** The tool rebuilds
the E1.1 grid with E1.1's own code and reproduces its 67,186 annulus points
and 30,888-point outdoor mask bit-identically. Indoor UEs:
- **indoor point:** a grid point that is on the ground, roofed and enclosed;
- **floors:** n = 1 … min(⌊H/3⌋, 8), with hUT = 3(n − 1) + 1.5 m (TR 38.901
  Table 7.2-1);
- **indoor distance d2D-in:** the geometric distance to the facade toward
  the gNB;
- **O2I outdoor LOS state:** the facade-to-gNB segment test.

Run 1 failed sanity check C1d. 46 samples, at 8 points inside the gNB's
own building, have their facade beyond the gNB. They would be served
through the roof, which the O2I model does not cover, so they are now
excluded and counted (check C1e).

Run 2 passes all checks:
- 29,579 indoor points and 146,426 (point, floor) samples;
- roof height median 16.7 m; d2D-in median 9.4 m;
- facade LOS 1% on floor 1, 13% on floor 6, 27% on floor 7.
The run-1 manifest is kept.

**Model.** The E1.5.5 primary model V2 is applied to every outdoor point
(states outdoor and in_car) and every indoor sample (states indoor_low and
indoor_high).
- O2I uses V19.5.0, the E1.5.0 decision for E1c onward.
- For hUT ≥ 13 m, TR 38.901's random effective environment height hE is
  handled as an exact finite mixture. New functions in
  `cmwave/radio/pathloss_3gpp.py`; the earlier functions are unchanged and a
  test checks they agree.

**Area.** Each sample stands for 16 m².
- Results are reported within the radius of a disk with the area of a
  hexagonal cell: 105.0 m for ISD 200 (M.2412 Dense Urban) and 262.5 m for
  ISD 500 (TR 38.901 UMa).
- They are also reported to 600 m, per distance bin and per floor.
- The E1.1 grid starts at 20 m.

**Engine: exact, no Monte Carlo.**
- Shadowing, car, building and O2I variates are normal and shared by both
  carriers and directions. So every feasibility event is {Z ≤ m} for one
  standard normal Z.
- Class probabilities are closed-form normal intervals.

**Gates:**

| Gate | Requirement |
|---|---|
| C0 | Inputs validated and hashed |
| C2 | The direct E1.5.5 pipeline agrees at m ± 1e-6 σ on 300 rows per variant, case and state |
| C3 | Agreement with the E1.5.5 Monte-Carlo records at the 27 positions: two-sided binomial p ≥ 1e-6 on every test, and pooled agreement within 5 SE. Dry run before registration: 8,100 tests, minimum p = 0.0014 |
| C4 | Classes partition to 1e-12 |
| C5 | Mappings non-decreasing |

K6 is reported, not required to be 0. Beyond the 3.5 GHz LOS breakpoint of
tall floors, 7.2 GHz can be the better carrier.

**Variants.**
- **primary.**
- **v16:** O2I V16.1.0.
- **g_dp, g_eig:** DirectPath and eigen-bound gains.
- **g_c_matched:** regime C with the matching measured array deficits.
- **pkt:** E1.5.4 packet thresholds.
- **sel:** E1.5.4 selectivity factor.
- **m_trlow:** TR's low end below the C-AMC floor (hazard 15, F1 only).
- **indoor_ground:** floor 1 only, as E1.4.
- **indoor_stoch_din:** TR 38.901's stochastic d2D-in.
- **e14_model:** E1.4's assumptions applied to the same area.

**Predictions** (scored):
- **P1:** K3 is smaller with F3 than with F2 at both radii, for A, B and C.
- **P2:** In regime A, indoor-low F2 UL at 7.2 GHz is better over all floors
  than on floor 1 only.

**Decision (fixed now).** If every gate passes, the primary area fractions
at the two radii, with the variant ranges, are the paper's single-site area
results and the spatial input of Phase 3.

**Limits.**
- One site, no interference.
- Geometric LOS from a city mesh, with no vegetation or vehicles.
- Building interiors are open: no internal walls beyond the TR
  0.5 dB/m term.
- D_cb was measured at hUT 1.5 m and is applied to every floor.
- Regime C is unmeasured.

#### E1c.1 results (run 3): all gates PASSED; P1 and P2 held (2026-11-09)

**Runs.** None of the runs below changed a definition.
- **Run 1 (`b7d5531`)** passed C5 and C3. I stopped it after 40 min in the
  sample loops: an NpzFile decompresses an array on every access. No
  result was produced; its log is kept.
- **Run 2 (`5bcc341`)** loads the arrays once. It passed every gate, but its
  raw map file listed indoor-point coordinates only for points with
  samples. 5 points lost every floor to the same-building rule, so the
  coordinates were misaligned with the maps.
- **Run 3 (`a666cd7`)** fixes the coordinates. Its `summary.json` and
  `validation.json` are byte-identical to run 2's, whose manifest and
  validation are kept.

Gates:
- C0, C2 (0 mismatches), C3 (minimum p 0.0014 over 8,100 tests), C4
  (partition error 2.2e-16) and C5 pass.
- K6 is at most 0.048 for a single sample and 0.000 in every mix.

Generated from `summary.json`. M2412 mix, primary model. UL is feasible at
both carriers; DL at 7.2 GHz. The radii are the disks with the area of
the cell at ISD 200 m (105.0 m) and ISD 500 m (262.5 m), plus the whole
20–600 m annulus. The E1.5.5 27-position values are shown for comparison:

| Regime | Profile | Radius | UL 3.5 | UL 7.2 | DL 7.2 | K1 | K2 | K3 | K4 | K6 |
|---|---|---|---|---|---|---|---|---|---|---|
| R0 | F2 | isd200 | 0.571 | 0.380 | 0.746 | 0.380 | 0.191 | 0.175 | 0.254 | 0.000 |
| R0 | F2 | isd500 | 0.164 | 0.094 | 0.298 | 0.094 | 0.070 | 0.134 | 0.702 | 0.000 |
| R0 | F2 | all | 0.039 | 0.021 | 0.080 | 0.021 | 0.019 | 0.040 | 0.920 | 0.000 |
| R0 | F2 | 27 positions (E1.5.5) | 0.381 | 0.275 | 0.474 | 0.275 | 0.106 | 0.092 | 0.526 | 0.000 |
| R0 | F3 | isd200 | 0.811 | 0.633 | 0.737 | 0.633 | 0.104 | 0.000 | 0.263 | 0.000 |
| R0 | F3 | isd500 | 0.355 | 0.204 | 0.289 | 0.204 | 0.084 | 0.000 | 0.711 | 0.000 |
| R0 | F3 | all | 0.100 | 0.050 | 0.077 | 0.050 | 0.026 | 0.000 | 0.923 | 0.000 |
| R0 | F3 | 27 positions (E1.5.5) | 0.511 | 0.413 | 0.468 | 0.413 | 0.055 | 0.000 | 0.532 | 0.000 |
| A | F2 | isd200 | 0.940 | 0.841 | 0.986 | 0.841 | 0.099 | 0.046 | 0.014 | 0.000 |
| A | F2 | isd500 | 0.644 | 0.450 | 0.870 | 0.450 | 0.194 | 0.226 | 0.130 | 0.000 |
| A | F2 | all | 0.282 | 0.148 | 0.647 | 0.148 | 0.134 | 0.365 | 0.353 | 0.000 |
| A | F2 | 27 positions (E1.5.5) | 0.669 | 0.558 | 0.841 | 0.558 | 0.111 | 0.171 | 0.159 | 0.000 |
| A | F3 | isd200 | 0.986 | 0.942 | 0.985 | 0.942 | 0.042 | 0.001 | 0.015 | 0.000 |
| A | F3 | isd500 | 0.840 | 0.698 | 0.865 | 0.698 | 0.139 | 0.028 | 0.135 | 0.000 |
| A | F3 | all | 0.555 | 0.352 | 0.637 | 0.352 | 0.201 | 0.084 | 0.363 | 0.000 |
| A | F3 | 27 positions (E1.5.5) | 0.798 | 0.698 | 0.835 | 0.698 | 0.099 | 0.037 | 0.165 | 0.000 |
| B | F2 | isd200 | 0.940 | 0.891 | 0.993 | 0.891 | 0.050 | 0.052 | 0.007 | 0.000 |
| B | F2 | isd500 | 0.644 | 0.557 | 0.907 | 0.557 | 0.087 | 0.263 | 0.093 | 0.000 |
| B | F2 | all | 0.282 | 0.218 | 0.737 | 0.218 | 0.063 | 0.455 | 0.263 | 0.000 |
| B | F2 | 27 positions (E1.5.5) | 0.669 | 0.615 | 0.882 | 0.615 | 0.055 | 0.213 | 0.118 | 0.000 |
| B | F3 | isd200 | 0.986 | 0.963 | 0.992 | 0.963 | 0.023 | 0.006 | 0.008 | 0.000 |
| B | F3 | isd500 | 0.840 | 0.773 | 0.903 | 0.773 | 0.067 | 0.063 | 0.097 | 0.000 |
| B | F3 | all | 0.555 | 0.465 | 0.728 | 0.465 | 0.090 | 0.174 | 0.272 | 0.000 |
| B | F3 | 27 positions (E1.5.5) | 0.798 | 0.753 | 0.878 | 0.753 | 0.045 | 0.080 | 0.122 | 0.000 |
| C | F2 | isd200 | 0.863 | 0.773 | 0.997 | 0.773 | 0.090 | 0.133 | 0.003 | 0.000 |
| C | F2 | isd500 | 0.460 | 0.352 | 0.937 | 0.352 | 0.108 | 0.477 | 0.063 | 0.000 |
| C | F2 | all | 0.152 | 0.103 | 0.809 | 0.103 | 0.049 | 0.657 | 0.191 | 0.000 |
| C | F2 | 27 positions (E1.5.5) | 0.574 | 0.512 | 0.918 | 0.512 | 0.061 | 0.344 | 0.082 | 0.000 |
| C | F3 | isd200 | 0.960 | 0.911 | 0.997 | 0.911 | 0.049 | 0.037 | 0.003 | 0.000 |
| C | F3 | isd500 | 0.715 | 0.608 | 0.936 | 0.608 | 0.107 | 0.220 | 0.064 | 0.000 |
| C | F3 | all | 0.359 | 0.258 | 0.806 | 0.258 | 0.101 | 0.447 | 0.194 | 0.000 |
| C | F3 | 27 positions (E1.5.5) | 0.710 | 0.650 | 0.916 | 0.650 | 0.059 | 0.207 | 0.084 | 0.000 |

What is established (one site, noise-limited, M2412 mix, primary model):

1. **The cell size decides the 7.2 GHz uplink question.**
   - **ISD 200 (Dense Urban):** the 7.2 GHz UL meets the per-user service
     level (F2) for 84% (A), 89% (B) and 77% (C) of the area, and DL for
     ≥ 98.6%. With a static frequency partition (F3), UL rises to 94%,
     96% and 91%.
   - **ISD 500:** F2 UL falls to 45% / 56% / 35%, and F3 UL to 70% / 77% /
     61%. "7.2 DL works but no UL meets the service level" (K3) covers
     23% / 26% / 48% of the area under F2, and 3% / 6% / 22% under F3.
2. **The 27 positions misstate the area.** They are distance-stratified,
   with 6 of the 27 at 450–600 m.
   - Their UL fractions lie between the ISD 200 and ISD 500 area values.
   - Their 7.2 GHz DL fractions are below both (A: 0.841 against 0.870 and
     0.986).
   - They must not be quoted as coverage.
3. **The asymmetric option (7.2 GHz DL with 3.5 GHz UL, K2) is
   substantial at ISD 500.** It covers 19% of the area (A, F2), against 11%
   at the 27 positions. Outdoors it is 30% of NLOS area and 0 in LOS.
4. **Upper floors matter.**
   - F2 UL at 7.2 GHz in low-loss buildings rises from 9.6% on floor 1 to
     70.6% on floor 8 (A, whole area).
   - E1.4's ground-floor assumption understates indoor UL: 32% against 46%
     over all floors (A, ISD 500, P2).
   - The geometric indoor distance (median 9.4 m) costs more than TR's
     stochastic one: 46% against 51%.
5. **E1.4's assumptions on the same area are optimistic**, as in E1.5.5.
   At ISD 500, A, F2: UL 7.2 is 0.519 → 0.450, DL 7.2 is 0.932 → 0.870, and
   K4 is 0.068 → 0.130.
6. **Uncertainty.** At ISD 500, the variant ranges are dominated by:
   - the beam gain (DirectPath / eigen bound): A F2 K1 0.323–0.492;
   - the indoor model (ground only / stochastic d2D-in).
   The O2I version does not affect low-loss buildings, and its effect
   overall is small.
7. **Hazard-15 label (F1 only).** At ISD 500, F1 UL 7.2 is 0.921 (A)
   against 0.971 with TR's low end. For the isotropic reference R0 it is
   0.602 against 0.800.

**Decision applied (pre-registered).** The primary area fractions at the
two radii, with the variant ranges, are the paper's single-site area
results and the spatial input of Phase 3. E1c.2 (multiple sites,
interference) is next and is pre-registered separately.

### E1c.2 multi-site SINR feasibility (PRE-REGISTERED 2026-11-09)

E1c.1 is single-site and noise-limited. M.2412 Dense Urban-eMBB requires
inter-site interference to be "explicitly modelled" on 19 hexagonal sites
of 3 TRxPs. E1c.2 adds the layout, best-server association and SINR.

Files:
- Config: `configs/e1/e1c2_interference.json` (normative).
- Geometry: `scripts/e1c2_geometry_sites.py`, outputs in
  `results/phase2/e1c2_geometry/`.
- Engine: `scripts/e1c2_interference.py`.
- Tests: `tests/test_e1c2_interference.py`.

**Sources checked before registration.** From the M.2412-0 Word file,
Table 5(b) Config A:
- BS height 25 m; 44 dBm per TRxP (20 MHz); UE 23 dBm;
- ISD 200 m; 80% indoor and 20% in car; 20% high-loss buildings;
- noise figures 5 / 7 dB; 10 UEs per TRxP; full buffer.

M.2412 gives no uplink power-control parameters. I could not verify
3GPP P0/α values, so none is used (see UL power below).

**Geometry (run before registration; geometry only).**
- Layout: a hexagonal lattice anchored at site 0, rings 0–2.
- Placement: each in-scene lattice point snaps to the nearest roof
  (≥ 6 m) within 0.2·ISD, otherwise a 25 m ground mast. Antenna height is
  max(25 m, roof + 1 m). Lattice points outside the scene become virtual
  sites with NLOS links.
- Link geometry: from every outdoor point and indoor sample to every site,
  with LOS and the facade and d2D-in toward each site.

Run 1 failed check S3. Indoor samples directly beneath a rooftop antenna
have no horizontal direction to it, and one floor-6 ray leaves the
building mesh. Such samples are now excluded and counted. Run 2 passes
S1–S4 (site 0 reproduces E1.1 and E1c exactly; placement rule;
finite facades; reproducible):

| ISD | Sites | Excluded indoor samples |
|---|---|---|
| 200 | site 0 (27 m), 17 rooftop, 1 mast; all others at 25 m | 3,886 (2.65%) |
| 500 | site 0, 6 rooftop (ring 1), 2 masts, 10 virtual (ring 2) | 1,291 (0.88%) |

The run-1 manifest is kept.

**Model** (the config is normative):
- **Victims:** samples in site 0's nominal hexagonal cell, weighted equally
  within each state, as E1c.1.
- **Association:** each victim is served by the site with the largest
  3.5 GHz received power with its beam (E1.4 gain − D, E1.5.5 V2) and its
  best sector. The same TRxP serves at 7.2 GHz.
- **DL interference:** every TRxP other than the serving one, including
  the serving site's other two sectors. Each transmits the case power with
  its sector's element gain toward the victim and 0 dB average array gain,
  scaled by the activity η.
- **UL interference:** one UE per other TRxP. Per realization, 3,000
  candidates are drawn from the 19 cells with the M.2412 state mix,
  associated like the victims, and each TRxP takes one of its own at
  random. They are received with the victim's sector element gain.
- **UL power:**
  - *target* (primary): each interferer's own noise-limited SNR equals the
    profile's threshold, capped at 23 dBm. This is a lower bound on the
    power an interference-limited network needs.
  - *full*: 23 dBm, the upper bound.
  - *off*: no UL interference.
  The victim always transmits 23 dBm, as in E1.3–E1c.1.
- **Random terms:** shadowing (or O2I shadowing) per link, shared by the
  carriers and by a site's sectors and independent across sites
  (TR 38.901 clause 7.5). Building or car variate per UE. hE drawn per
  link.
- **Thresholds:** E1c.1's F1/F2/F3 thresholds applied to SINR, with
  interference treated as noise and uniform over the band. The F2 and F3
  uplink rules keep their E1.5.5 form, because SINR(n) has the same
  dependence on the allocation n as SNR(n).

**Settings.** The primary is ISD 200, η = 1, UL target power and every
7.2 GHz layer on. One change at a time, for both ISDs and cases A, B, C,
with common random numbers:
- η 0.5 and 0.2;
- UL interference off, or at full power;
- `iso72`: only the serving TRxP's 7.2 GHz carrier on;
- `noise_multi`: 19-site association without interference;
- `single_noise`: site 0 serves, no interference (the G1 limit).

100 realizations, seed 20261110.

**Gates:**

| Gate | Requirement |
|---|---|
| G0 | Inputs validated and hashed |
| G1a | Site 0's link (loss, gains, σ, hE weights) equals the E1c.1 rows for every victim, case, state and carrier |
| G1 | `single_noise` fractions agree with the E1c.1 exact engine on the same victims within 5 SE, for every ISD, case, state, profile and class or flag |
| G2 | Vectorized UMa equals `pathloss_3gpp` for every victim link and hE component within 1e-9 dB |
| G3 | 50 pairs per ISD: association identical, and every SINR within 1e-9 dB of an independent scalar implementation with its own candidate association |
| G4 | Mappings non-decreasing; the classes partition every outcome |

Before registration, the unit tests ran the engine against the scalar
implementation on a synthetic 7-site layout (agreement within 1e-9 dB). A
timing run on synthetic data of realistic size took about 2.3 s and
0.9 GB per realization. No SINR of a real sample has been computed.

**Prediction** (scored):
- **P1:** primary, case A: the M.2412-mix median of the 7.2 GHz DL SINR is
  at least 3 dB below the median DL SNR of `noise_multi`.

**Decision (fixed now).** If every gate passes, the primary fractions,
with the η, UL power, activation, ISD and case ranges, are the paper's
interference-aware area results and the load-dependent input of Phase 3.
E1c.1 remains the noise-limited bound.

**Limits.**
- One 19-site cluster, no wrap-around. UEs beyond the layout edge attach to
  ring-2 sites.
- Interference is averaged over beam directions (0 dB array gain); η is a
  mean factor.
- The UL interferer power is a service-level rule, not a 3GPP P0/α set;
  `ul_full` bounds it.
- At ISD 500, ring 2 is virtual, and only the part of the layout within
  600 m has UE samples. Ring 2 and the outer part of ring 1 have no UL
  interferers, while ring 1's interferers come from the part of their
  cells nearer site 0.
- Single layer; ideal UE MRC; D_cb measured at hUT 1.5 m.

#### E1c.2 results (run 1): all gates PASSED; P1 held (2026-11-09)

**Run 1 (`4823444`)** took 280 s. Victims:

| ISD | Victims (outdoor / indoor) | Candidate pool (outdoor / indoor) |
|---|---|---|
| 200 | 935 / 5,756 | 16,678 / 96,854 |
| 500 | 4,541 / 38,921 | 30,888 / 145,135 |

At ISD 500 the pool is the whole geometry.

Gates:
- **G0** passes.
- **G1a:** site 0's link equals the E1c.1 rows: loss within 2.8e-14 dB, and
  gains, σ, hE weights and d2D exactly.
- **G1:** 324 tests per ISD. Maximum |difference|/SE is 1.71 (ISD 200) and
  2.64 (ISD 500).
- **G2:** 0.0 dB difference on 198,702 (ISD 200) and 1,149,196 (ISD 500)
  victim link components at both carriers.
- **G3:** 50 pairs per ISD, 0 association mismatches, 2.8e-14 dB.
- **G4:** mappings monotone, 0 partition errors.

Generated from `summary.json`. M2412 mix; Monte-Carlo SE ≤ 0.0004.
"E1c.1 (site 0 only)" is E1c.1's exact engine on the same victims.

F2:

| ISD | Case | Setting | UL 3.5 | UL 7.2 | DL 7.2 | K1 | K2 | K3 | K4 | K6 |
|---|---|---|---|---|---|---|---|---|---|---|
| 200 | A | E1c.1 (site 0 only) | 0.936 | 0.835 | 0.985 | 0.835 | 0.101 | 0.049 | 0.015 | 0.000 |
| 200 | A | noise_multi | 0.972 | 0.901 | 0.996 | 0.901 | 0.071 | 0.024 | 0.004 | 0.000 |
| 200 | A | **primary** | 0.970 | 0.896 | 0.995 | 0.896 | 0.073 | 0.025 | 0.005 | 0.000 |
| 200 | A | η 0.2 | 0.972 | 0.900 | 0.996 | 0.900 | 0.072 | 0.024 | 0.004 | 0.000 |
| 200 | A | ul_full | 0.749 | 0.719 | 0.995 | 0.718 | 0.031 | 0.245 | 0.005 | 0.001 |
| 200 | B | E1c.1 (site 0 only) | 0.936 | 0.886 | 0.992 | 0.886 | 0.051 | 0.056 | 0.008 | 0.000 |
| 200 | B | noise_multi | 0.972 | 0.933 | 0.999 | 0.933 | 0.039 | 0.027 | 0.001 | 0.000 |
| 200 | B | **primary** | 0.970 | 0.932 | 0.998 | 0.932 | 0.038 | 0.029 | 0.002 | 0.000 |
| 200 | B | η 0.2 | 0.972 | 0.933 | 0.999 | 0.933 | 0.039 | 0.027 | 0.001 | 0.000 |
| 200 | B | ul_full | 0.749 | 0.799 | 0.998 | 0.744 | 0.005 | 0.195 | 0.002 | 0.055 |
| 200 | C | E1c.1 (site 0 only) | 0.857 | 0.767 | 0.996 | 0.767 | 0.090 | 0.139 | 0.004 | 0.000 |
| 200 | C | noise_multi | 0.926 | 0.853 | 1.000 | 0.853 | 0.073 | 0.074 | 0.000 | 0.000 |
| 200 | C | **primary** | 0.922 | 0.851 | 1.000 | 0.851 | 0.071 | 0.078 | 0.000 | 0.000 |
| 200 | C | η 0.2 | 0.925 | 0.852 | 1.000 | 0.852 | 0.072 | 0.075 | 0.000 | 0.000 |
| 200 | C | ul_full | 0.733 | 0.781 | 1.000 | 0.723 | 0.010 | 0.209 | 0.000 | 0.057 |
| 500 | A | E1c.1 (site 0 only) | 0.639 | 0.447 | 0.866 | 0.447 | 0.192 | 0.228 | 0.134 | 0.000 |
| 500 | A | noise_multi | 0.744 | 0.521 | 0.931 | 0.521 | 0.222 | 0.187 | 0.069 | 0.000 |
| 500 | A | **primary** | 0.726 | 0.509 | 0.926 | 0.509 | 0.217 | 0.200 | 0.074 | 0.000 |
| 500 | A | η 0.2 | 0.740 | 0.519 | 0.930 | 0.519 | 0.221 | 0.190 | 0.070 | 0.000 |
| 500 | A | ul_full | 0.559 | 0.441 | 0.926 | 0.441 | 0.118 | 0.367 | 0.074 | 0.000 |
| 500 | B | E1c.1 (site 0 only) | 0.639 | 0.553 | 0.904 | 0.553 | 0.086 | 0.266 | 0.096 | 0.000 |
| 500 | B | noise_multi | 0.744 | 0.645 | 0.955 | 0.645 | 0.099 | 0.211 | 0.045 | 0.000 |
| 500 | B | **primary** | 0.726 | 0.639 | 0.954 | 0.639 | 0.087 | 0.228 | 0.046 | 0.000 |
| 500 | B | η 0.2 | 0.740 | 0.644 | 0.955 | 0.644 | 0.096 | 0.215 | 0.045 | 0.000 |
| 500 | B | ul_full | 0.559 | 0.558 | 0.954 | 0.532 | 0.027 | 0.369 | 0.046 | 0.026 |
| 500 | C | E1c.1 (site 0 only) | 0.457 | 0.350 | 0.935 | 0.350 | 0.107 | 0.478 | 0.065 | 0.000 |
| 500 | C | noise_multi | 0.535 | 0.405 | 0.974 | 0.405 | 0.131 | 0.439 | 0.026 | 0.000 |
| 500 | C | **primary** | 0.524 | 0.403 | 0.974 | 0.403 | 0.121 | 0.449 | 0.026 | 0.000 |
| 500 | C | η 0.2 | 0.533 | 0.404 | 0.974 | 0.404 | 0.129 | 0.441 | 0.026 | 0.000 |
| 500 | C | ul_full | 0.458 | 0.387 | 0.974 | 0.380 | 0.078 | 0.509 | 0.026 | 0.007 |

F3:

| ISD | Case | Setting | UL 3.5 | UL 7.2 | DL 7.2 | K1 | K2 | K3 | K4 | K6 |
|---|---|---|---|---|---|---|---|---|---|---|
| 200 | A | E1c.1 (site 0 only) | 0.985 | 0.939 | 0.984 | 0.939 | 0.044 | 0.001 | 0.016 | 0.000 |
| 200 | A | **primary** | 0.996 | 0.965 | 0.994 | 0.965 | 0.029 | 0.000 | 0.006 | 0.000 |
| 200 | A | ul_full | 0.749 | 0.728 | 0.994 | 0.727 | 0.022 | 0.244 | 0.006 | 0.002 |
| 200 | B | E1c.1 (site 0 only) | 0.985 | 0.961 | 0.991 | 0.961 | 0.023 | 0.007 | 0.009 | 0.000 |
| 200 | B | **primary** | 0.996 | 0.982 | 0.998 | 0.982 | 0.014 | 0.002 | 0.002 | 0.000 |
| 200 | B | ul_full | 0.749 | 0.806 | 0.998 | 0.747 | 0.002 | 0.191 | 0.002 | 0.059 |
| 200 | C | E1c.1 (site 0 only) | 0.957 | 0.907 | 0.996 | 0.907 | 0.050 | 0.039 | 0.004 | 0.000 |
| 200 | C | **primary** | 0.982 | 0.947 | 1.000 | 0.947 | 0.035 | 0.018 | 0.000 | 0.000 |
| 200 | C | ul_full | 0.744 | 0.830 | 1.000 | 0.744 | 0.000 | 0.170 | 0.000 | 0.086 |
| 500 | A | E1c.1 (site 0 only) | 0.836 | 0.693 | 0.862 | 0.693 | 0.140 | 0.029 | 0.138 | 0.000 |
| 500 | A | **primary** | 0.910 | 0.779 | 0.922 | 0.779 | 0.129 | 0.014 | 0.078 | 0.000 |
| 500 | A | ul_full | 0.608 | 0.568 | 0.922 | 0.566 | 0.041 | 0.313 | 0.078 | 0.001 |
| 500 | B | E1c.1 (site 0 only) | 0.836 | 0.768 | 0.901 | 0.768 | 0.067 | 0.065 | 0.099 | 0.000 |
| 500 | B | **primary** | 0.910 | 0.855 | 0.951 | 0.855 | 0.055 | 0.041 | 0.049 | 0.000 |
| 500 | B | ul_full | 0.608 | 0.667 | 0.951 | 0.602 | 0.006 | 0.278 | 0.049 | 0.065 |
| 500 | C | E1c.1 (site 0 only) | 0.710 | 0.603 | 0.934 | 0.603 | 0.107 | 0.224 | 0.066 | 0.000 |
| 500 | C | **primary** | 0.801 | 0.699 | 0.973 | 0.699 | 0.102 | 0.172 | 0.027 | 0.000 |
| 500 | C | ul_full | 0.594 | 0.620 | 0.973 | 0.574 | 0.020 | 0.333 | 0.027 | 0.046 |

SINR / SNR quantiles, case A, M2412 mix, 7.2 GHz (5 / 50 / 95 %, dB):

| ISD | Setting | DL | UL F2 | UL F3 |
|---|---|---|---|---|
| 200 | noise_multi | 27.2 / 51.5 / 76.7 | 2.2 / 26.5 / 51.6 | 12.3 / 36.6 / 61.7 |
| 200 | **primary** | 17.9 / 25.9 / 43.9 | 1.7 / 26.1 / 51.2 | 11.8 / 36.1 / 61.3 |
| 200 | ul_full | 17.9 / 25.9 / 43.9 | -9.0 / 17.0 / 43.1 | -8.5 / 17.8 / 44.2 |
| 500 | noise_multi | 10.0 / 34.4 / 63.4 | -15.0 / 9.3 / 38.4 | -4.9 / 19.4 / 48.5 |
| 500 | **primary** | 9.7 / 24.0 / 43.1 | -15.4 / 8.9 / 38.0 | -5.7 / 18.7 / 47.8 |
| 500 | ul_full | 9.7 / 24.0 / 43.1 | -19.5 / 6.6 / 36.5 | -16.5 / 11.4 / 42.0 |

Other reported quantities (case A):

| Quantity | ISD 200 | ISD 500 |
|---|---|---|
| Served by site 0: outdoor and in car | 0.866 | 0.748 |
| Served by site 0: indoor | 0.61 | 0.63 |
| UL interferers' target power at 7.2 GHz, F2: mean | 4.6 dBm | 15.7 dBm |
| Share of those interferers at 23 dBm | 11.6% | 52.5% |
| TRxPs without an UL interferer (of 57) | 0 | 21.0 (mostly ring 2) |

`iso72` equals `noise_multi` in the 7.2 GHz columns and the primary in
the 3.5 GHz columns, as defined. No histogram mass reached the end bins.

**P1 held.** The M.2412-mix median 7.2 GHz DL SINR is 25.9 dB, against an
SNR of 51.5 dB in `noise_multi`: 25.6 dB lower.

What is established (gates passed; conditional on the limits below):

1. **The 19-site layout raises feasibility above E1c.1.**
   - Best-server association adds +6.6 points of F2 UL 7.2 at ISD 200 and
     +7.4 at ISD 500 (case A, `noise_multi` against site 0 only).
   - Full-load interference with target UL power then removes 0.5 and 1.2
     points.
   - Net: primary A F2 UL 7.2 is 0.896 at ISD 200 and 0.509 at ISD 500,
     against E1c.1's 0.835 and 0.447 on the same victims.
   - **Correction of wording, not of a definition:** the pre-registered
     decision text calls E1c.1 "the noise-limited bound". It is the
     single-site reference; `noise_multi` is the noise-limited multi-site
     case, and E1c.1 lies below it.
2. **The DL is interference-limited but stays feasible.** SINR is 25.6 dB
   below SNR at the median, but the 5th percentile (17.9 dB at ISD 200)
   stays above the F2 DL threshold. F2 DL 7.2 is ≥ 0.995 at ISD 200 and
   0.926 / 0.954 / 0.974 (A / B / C) at ISD 500.
3. **UL power control, not load, decides the UL.**
   - With target power, UL interference costs at most 0.53 dB at the 5th
     percentile at ISD 200 (every case, carrier and profile).
   - With every interferer at 23 dBm, F2 UL falls to 0.749 (3.5 GHz) and
     0.719 (7.2 GHz) at ISD 200 (A), and K3 rises to 0.245.
   - Load matters little: η from 1 to 0.2 changes any fraction by ≤ 1.4
     points.
   - The [target, full] bracket is the main uncertainty of the UL result.
4. **When the UL is interference-limited, 7.2 GHz can beat 3.5 GHz.**
   At ISD 200 under `ul_full`, F2 UL 7.2 exceeds UL 3.5 for B (0.799
   against 0.749) and C (0.781 against 0.733), and K6 appears (B 0.055,
   C 0.057). The serving beam gain grows with the aperture; the
   beam-averaged interference does not. At ISD 500, where noise still
   matters, it does not happen (B 0.558 against 0.559).
5. **Activating 7.2 GHz on neighbouring TRxPs costs almost no
   feasibility here.** `iso72` against primary: ≤ 0.5 points at
   ISD 200 and ≤ 1.2 at ISD 500 (A, F2).

**Limits that bound these statements.**
- **Beam-averaged interference.** Interference uses 0 dB array gain, the
  average over beam directions. The model cannot produce the low-SINR
  tail of a neighbour's beam pointing at the victim. Statements 2, 4 and
  5 are therefore conditional on it. A per-beam model (each interfering
  TRxP steering toward its own scheduled UE, with the explicit array
  factor) is needed before "interference does not limit feasibility"
  can be claimed.
- **Target UL power is noise-limited,** a lower bound; `ul_full` is the
  upper bound.
- **The association gain relies on independent inter-site shadowing**
  (TR 38.901 clause 7.5). Inter-site correlation would shrink it.
- **ISD 500:** 21 of 57 TRxPs have no UL interferer, and ring-1
  interferers are drawn from the part of their cells nearer site 0.

**Decision applied (pre-registered).** All gates pass. The primary
fractions, with the η, UL power, activation, ISD and case ranges, are the
paper's interference-aware area results and the load-dependent input of
Phase 3, under the limits above. The per-beam interference model is the
recommended next step (E1c.3, to be pre-registered separately).

#### Audit of E1c.2 (ChatGPT, 2026-11-09) and response

| Point | Response |
|---|---|
| Run E1c.3 (directional interference) before Phase 3 | Agreed; it was the recommended next step |
| DL: each neighbour's beam toward its own scheduled UE; UL: the serving receive beam applied to unwanted UEs | Adopted |
| Resource overlap; explicit scheduling is not the same as a mean activity factor | Adopted: synchronized TDD, one scheduled UE per TRxP on the victim's PRBs, and Bernoulli activity reported beside the mean factor |
| Reuse E1c.2's positions and realizations | Adopted: E1c.2's random streams are reproduced exactly (gate H1a), so every comparison is paired |
| Actual scheduled beams as primary; worst-case alignment only as a separate stress test | Adopted (`sd_stress`) |
| The larger-array advantage under full-power UL is conditional | Agreed; the E1c.2 text already declared it conditional. Tested with explicit receive beams (`sd_ul_full`, `geo_ul_full`) |
| ISD 500 lacks outer interferers; treat it as a sensitivity | Agreed; it was secondary and is now labelled a qualified sensitivity. However, its gain over E1c.1 (+7.4 points) is already present in `noise_multi`, which has no interference at all. Missing interferers can bias only the 1.2-point interference loss, not that gain |
| Only two upstream drafts exist in `docs/upstream/` | **Correct, and I had misreported this.** Two drafts are written (DirectPath receive mismatch; SISO AMC). Reports 2 and 3 (OFDMA with a shared empty BeamId; hazard 14) are identified in `docs/E1_5_VALIDATION_PROTOCOL.md` but not drafted. Hazard 18 must first be classified as a defect or an unsupported configuration |
| Proposed E1c.3 protocol file | Not provided to me. The pre-registration below is written independently and covers its four requirements |
| One refinement of mine: angular spread | A pure geometric array pattern ignores the angular spread of NLOS links. E1.5.2-1 measured that a single beam toward an NLOS UE loses about 5 dB (8×16): about 70% of the power arrives outside the beam. A pure pattern therefore overstates both main-lobe collisions and nulls. The primary model is calibrated to that measurement; the pure pattern is kept as the most directive bracket |

### E1c.3 directional (per-beam) interference (PRE-REGISTERED 2026-11-09)

Files:
- Config: `configs/e1/e1c3_directional.json` (normative).
- Engine: `scripts/e1c3_directional.py`. It imports `scripts/e1c2_interference.py`
  unchanged.
- Tests: `tests/test_e1c3_directional.py`.

**Unchanged from E1c.2:** layout, victims, candidates, association,
propagation, random terms, serving gains, thresholds and UL power rules.
E1c.2's draws are reproduced exactly, so every comparison is paired.

**Beam model.**
- **Array:** each TRxP is a rows × cols half-wavelength UPA (8×16, or
  16×32 at 7.2 GHz for B and C), ideally steered toward its target.
  - DL: every TRxP steers toward its own scheduled UE (the E1c.2 candidate
    selected for it).
  - UL: every serving TRxP steers toward its victim.
- **Pattern:** g = |AF|²/N. It equals N at the target (the E1.4 serving
  gain) and averages 1 over steering directions (the E1c.2 value).
- **Link gain:** a beam's gain toward a link is element × [a·g + (1 − a)].
  - A fraction a of the link's power arrives from the geometric direction
    and sees the pattern. The rest is diffuse and sees the average.
  - a is calibrated per link so that a beam's gain toward its own target
    equals the V2 serving gain: a·N + 1 − a = N·10^(−D/10).
  - Resulting a for the 8×16 array: LOS ≈ 0.87, NLOS ≈ 0.30, O2I ≈ 0.33.
    For 16×32 NLOS: 0.23. The narrower beam captures less of the same
    angular spread, a consistency the calibration did not force.
- **Brackets:**
  - `avg` (a = 0): E1c.2.
  - `geo` (a = 1): the pure pattern, the heaviest tail.
  - `sd` (calibrated a): the primary.
- **TRxPs serving no candidate** (ISD 500 only) point at a uniform position
  in their own cell and sector; the count is reported.

**Settings.**
- All eight E1c.2 settings, recomputed.
- Per-beam settings:
  - `sd` (primary), `geo`, and `avg_pb` (gate H1b);
  - η 0.5 and 0.2, each with Bernoulli and with mean activity;
  - `sd_ul_off`, `sd_ul_full`, `geo_ul_full`;
  - `sd_iso72` (the effect of activating 7.2 GHz on neighbouring TRxPs);
  - `sd_stress`: the strongest interferer is beam-aligned with the victim.
    This is a stress test, not a network estimate.
- ISD 200 is primary; ISD 500 is a qualified sensitivity. Cases A, B, C;
  100 realizations; the E1c.2 seed, plus new streams for activity and
  synthetic targets.

**Gates:**

| Gate | Requirement |
|---|---|
| H0 | E1c.2 and its inputs validated and hashed (including E1c.2's raw counts) |
| H1a | E1c.2 reproduced: raw counts identical; every mix fraction within 1e-12 |
| H1b | `avg_pb` equals E1c.2 primary within 1e-9 dB (every victim, realization, carrier and profile) |
| H2 | Closed-form pattern equals the explicit phasor sum within 1e-9·max(g, 1) (20,000 pairs per array); peak N; grid mean 1 |
| H3 | The calibration identity holds within 1e-9 dB and 0 ≤ a ≤ 1 for every array, carrier and stratum |
| H4 | 50 pairs per ISD of `sd` and `sd_stress` (case A) within 1e-9 dB of an independent scalar implementation (explicit phasor sums) |
| H5 | Mappings non-decreasing; classes partition |

Before registration, the unit tests ran on synthetic layouts:
- the per-beam code with a = 0 equals E1c.2;
- `sd` and `sd_stress` equal the scalar implementation, including with
  sparse candidates so that synthetic targets are used.

A timing run on synthetic ISD-500-size data took about 23 s and 1.8 GB per
realization. No directional SINR of a real sample has been computed.

**Predictions** (scored; ISD 200, case A, M2412 mix, 7.2 GHz DL):
- **P1:** `sd` has a lower 5th-percentile SINR and a higher median SINR
  than `avg`: the same mean interference with a heavier tail.
- **P2:** The 1st-percentile SINR is ordered `geo` < `sd` < `avg`.

**Reported.**
- Fractions with Monte-Carlo SE.
- Paired differences with SE: `sd` − `avg`, `geo` − `avg`, `sd_ul_full` −
  `ul_full`, `sd` − `sd_iso72`, and Bernoulli − mean at each η.
- SINR quantiles (1 / 5 / 50 / 95 %).
- Case A vs B vs C under explicit beams.

**Decision (fixed now).** If every gate passes, `sd` becomes the primary
interference-aware result and the input of Phase 3, with [`avg`, `geo`]
as the beam-model range. A feasibility statement is called robust to the
beam model only where |`sd` − `avg`| ≤ 0.02 and |`geo` − `avg`| ≤ 0.02 for
the M2412 F2 and F3 UL/DL fractions at ISD 200 in every case; otherwise it
is reported with its range.

**Limits.**
- The two-component angular abstraction is calibrated to D_cb, which was
  measured at hUT 1.5 m at 27 positions; it is not a cluster-level channel.
- Interfering beams are steered ideally (no codebook quantization); one
  polarisation; single-antenna UE transmit.
- Scheduling is random, with no coordination or nulling; no mechanical
  downtilt.
- E1c.2's limits also apply.

#### E1c.3 results (run 1): all gates PASSED; P1 FAILED (median clause), P2 held (2026-11-09)

**Run 1 (`6d9b9b9`)** took 2,730 s.

Gates:
- **H0** passes.
- **H1a:** E1c.2 reproduced: 972 raw count arrays identical per ISD,
  maximum mix difference 0.0, same victims.
- **H1b:** 2.8e-14 dB.
- **H2:** 5.6e-14 relative; peak exact; grid-mean error 8.9e-16.
- **H3:** 3.6e-15 dB.
- **H4:** 50 pairs per ISD, 0 association mismatches, at most 7.8e-14 dB
  (explicit phasor sums, including ISD-500 synthetic targets).
- **H5:** 0 partition errors.

ISD 500 uses 21.0 synthetic DL targets per realization (the TRxPs without
candidates); there were no sampling fallbacks.

Generated from `summary.json`. ISD 200, M2412 mix, F2. `avg` = E1c.2
primary.

| Case | Setting | UL 3.5 | UL 7.2 | DL 3.5 | DL 7.2 | K1 | K2 | K3 | K4 | K6 |
|---|---|---|---|---|---|---|---|---|---|---|
| A | avg (E1c.2) | 0.970 | 0.896 | 1.000 | 0.995 | 0.896 | 0.073 | 0.025 | 0.005 | 0.000 |
| A | **sd** | 0.967 | 0.892 | 0.967 | 0.962 | 0.867 | 0.071 | 0.024 | 0.038 | 0.000 |
| A | geo | 0.964 | 0.886 | 0.923 | 0.919 | 0.828 | 0.067 | 0.024 | 0.081 | 0.001 |
| A | sd_stress | 0.937 | 0.830 | 0.467 | 0.466 | 0.429 | 0.027 | 0.009 | 0.534 | 0.000 |
| A | sd_iso72 | 0.967 | 0.901 | 0.967 | 0.996 | 0.875 | 0.067 | 0.029 | 0.004 | 0.026 |
| A | sd_ul_full | 0.731 | 0.702 | 0.967 | 0.962 | 0.689 | 0.029 | 0.243 | 0.038 | 0.001 |
| B | avg (E1c.2) | 0.970 | 0.932 | 1.000 | 0.998 | 0.932 | 0.038 | 0.029 | 0.002 | 0.000 |
| B | **sd** | 0.967 | 0.930 | 0.967 | 0.990 | 0.903 | 0.036 | 0.030 | 0.010 | 0.021 |
| B | geo | 0.964 | 0.928 | 0.923 | 0.967 | 0.862 | 0.035 | 0.031 | 0.033 | 0.040 |
| B | sd_ul_full | 0.731 | 0.797 | 0.967 | 0.990 | 0.713 | 0.005 | 0.193 | 0.010 | 0.080 |
| B | geo_ul_full | 0.748 | 0.824 | 0.923 | 0.967 | 0.704 | 0.008 | 0.154 | 0.033 | 0.102 |
| C | avg (E1c.2) | 0.922 | 0.851 | 1.000 | 1.000 | 0.851 | 0.071 | 0.078 | 0.000 | 0.000 |
| C | **sd** | 0.917 | 0.849 | 0.966 | 0.997 | 0.827 | 0.068 | 0.082 | 0.003 | 0.020 |
| C | geo | 0.911 | 0.846 | 0.926 | 0.988 | 0.796 | 0.064 | 0.085 | 0.012 | 0.043 |

ISD 500 (qualified sensitivity), M2412, F2, `avg` / **`sd`** / `geo`:

| Case | UL 7.2 | DL 3.5 | DL 7.2 |
|---|---|---|---|
| A | 0.509 / **0.496** / 0.483 | 0.981 / **0.901** / 0.805 | 0.926 / **0.850** / 0.763 |
| B | 0.639 / **0.630** / 0.615 | 0.981 / **0.901** / 0.805 | 0.954 / **0.926** / 0.855 |
| C | 0.403 / **0.400** / 0.397 | 0.987 / **0.910** / 0.824 | 0.974 / **0.964** / 0.938 |

DL SINR at 7.2 GHz, case A, M2412 mix (1 / 5 / 50 / 95 %, dB):

| ISD | avg | sd | geo |
|---|---|---|---|
| 200 | 14.9 / 17.9 / 25.9 / 43.9 | 9.2 / 13.9 / 25.4 / 44.5 | 5.1 / 10.4 / 27.5 / 47.8 |
| 500 | −0.8 / 9.7 / 24.0 / 43.1 | −0.9 / 7.6 / 21.7 / 42.2 | −1.2 / 4.6 / 20.0 / 43.2 |

Paired differences (ISD 200, A, F2; every SE ≤ 0.0003):
- `sd` − `avg`: DL 7.2 −0.033, UL 7.2 −0.005.
- `geo` − `avg`: DL 7.2 −0.076, UL 7.2 −0.011.
- `sd` − `sd_iso72` (activating 7.2 GHz on neighbouring TRxPs): DL 7.2
  −0.034, UL 7.2 −0.009. At ISD 500: −0.081 and −0.025.
- Bernoulli − mean activity at η 0.2: DL 7.2 −0.005 (−0.009 at ISD 500).

**Predictions.**
- **P1 FAILED.**
  - Its first clause held: the 5th percentile falls from 17.9 to 13.9 dB.
  - Its second clause failed: the `sd` median (25.4 dB) is not above the
    `avg` median (25.9 dB).
  - The prediction assumed per-beam interference has the same mean as the
    beam-averaged model. That premise is false. **Post-hoc diagnostic**
    (declared; realization 0, case A, 7.2 GHz DL; scratch script, no
    definition changed):
    - Weighted by interference power, the vertical array factor toward the
      victims averages 2.1–3.4 at both ISDs, not 1. Scheduled beams point at
      ground-level UEs, in the victims' own elevation band.
    - Indoors at ISD 200 the horizontal factor is also 2.9. 39% of those
      UEs are served by neighbouring sites, whose beams point into site 0's
      cell.
    - Mean interference relative to `avg` is 0.99 for in-car victims at
      ISD 200, but 1.9 indoors, and 2.9–3.4 at ISD 500.
  - E1c.2's average therefore underestimated mean interference, not only
    its tail.
- **P2 held.** 1st percentile: `geo` 5.1 < `sd` 9.2 < `avg` 14.9 dB.

**Decision rule applied (pre-registered).**
- `sd` is now the primary interference-aware result, with [`avg`, `geo`]
  as the beam-model range.
- Robust to the beam model (|`sd` − `avg`| and |`geo` − `avg`| ≤ 0.02,
  ISD 200):
  - every UL fraction (both carriers, F2 and F3, cases A, B, C);
  - DL 7.2 GHz in case C.
- Not robust, reported with ranges:
  - DL 3.5 GHz in every case (`sd` −0.033 to −0.037, `geo` −0.074 to
    −0.082);
  - DL 7.2 GHz in A (−0.033 / −0.076);
  - DL 7.2 GHz in B (`geo` −0.031).

What is established (gates passed):
1. **UL feasibility is robust to the beam model** at ISD 200: at most 1.1
   points in every case. The UL conclusions of E1c.2 stand: power control
   decides the UL, not beam directivity.
2. **The DL is beam-model-sensitive.**
   - Directional interference costs 3.3–3.7 points of DL feasibility at
     ISD 200 (`sd`, 8×16 arrays), and 7.4–8.2 in the `geo` bound.
   - At ISD 500 it costs 7.6–8.0 points (`sd`, A) and 16–18 (`geo`).
   - E1c.2's "DL stays feasible" holds only approximately at ISD 200.
3. **A larger 7.2 GHz array protects the DL and now outperforms 3.5 GHz.**
   - In B and C, DL 7.2 under `sd` is 0.990 / 0.997, against 0.967 for
     DL 3.5 (8×16 in every case).
   - K6 (7.2 GHz feasible in both directions while 3.5 GHz is not) appears
     at 0.020–0.021.
   - Under full-power UL, the larger-array UL advantage survives explicit
     receive beams: B UL 7.2 is 0.797 (`sd`) and 0.824 (`geo`), against
     UL 3.5 at 0.731 / 0.748. The audit's challenge is answered: the
     advantage persists and grows with directivity. In A there is none.
4. **Activating 7.2 GHz on neighbouring TRxPs is no longer free.** It costs
   3.4 points of 7.2 GHz DL feasibility at ISD 200 and 8.1 at ISD 500
   (case A), against ≤ 0.5 / 1.2 in E1c.2. Phase 3 must therefore model
   the coupling between neighbouring cells' activation decisions.
5. **Explicit collisions vs mean activity** differ by at most 0.6 points at
   ISD 200 and 1.1 at ISD 500 (any fraction, η 0.5 or 0.2). A mean factor
   is adequate for feasibility at partial load.
6. **Stress test (not a network estimate).** One beam-aligned strongest
   interferer halves DL feasibility (A, ISD 200: DL 7.2 from 0.962 to
   0.466). The DL is exposed to single collisions; coordinated beam
   management would matter.

**Limits** (in addition to the pre-registered ones):
- No mechanical downtilt, and ideal elevation steering. This is what
  concentrates the interferers' energy in the UE elevation band (the
  post-hoc diagnostic).
- ISD 500 relies on synthetic DL targets for 21 of 57 TRxPs.

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
