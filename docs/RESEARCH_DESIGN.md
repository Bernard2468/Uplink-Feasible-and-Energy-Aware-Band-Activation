# Research methods: pre-registration draft

## Work status

Not a published method, validated empirical dataset, or final novelty claim.
This document separates planned research from executable software smoke tests.

## Practical explanation

A co-sited 3.5 GHz base layer carries essential service. A 7.x GHz capacity layer
can be woken up when a location and traffic load justify additional capacity.
The operator wants low energy while ensuring that actual uplink and downlink
traffic are delivered. Switching on may take time and energy. A 7 GHz layer may
have appealing downlink capacity yet be limited by device transmit power on uplink.

## Hypotheses (falsifiable)

H1: Under matched radio assumptions and non-uniform demand, a feasible adaptive
7.x GHz activation policy can reduce *total system electrical energy* versus
always-on dual-band operation without breaching UL/DL service targets.
H2: A UL-agnostic activation rule can report optimistic energy savings but exceed
UL QoS outage targets in UE-power-limited scenarios.
H3: Transfer from simplified channels to site-specific channel traces produces
measurable degradation for policies not robust to channel mismatch.
H4 (conditional): After subtracting hardware/switching/controller overheads, some
traffic/geometry regimes show no feasible net benefit from activating FR3.

H1 need not hold in every scenario. H2 and H3 require pre-specified comparisons.
A null/negative result can be useful if it clearly bounds the validity region.

## Key research questions

RQ1: For fixed existing cell sites, what range of user locations, DL/UL offered
load and radio configuration allows beneficial FR3 activation?
RQ2: How does UE-limited UL change the optimal activation and band-association policy?
RQ3: How sensitive are the conclusions to power-model assumptions, channel
uncertainty, aperture, bandwidth, EIRP, array/RF-chain choice, wake-up delay and switching energy?
RQ4: Can a causal online method approach the clairvoyant optimum while meeting
outage and fairness constraints across unseen scenes?

## Planned system model

- Fixed sites; 3.5 GHz base layer; 7.x GHz candidate capacity layer.
- Separate UL and DL budgets; UE power constraints; interference; mobility.
- Explicit binary layer activation and temporal switching costs.
- Control at a time scale justified by radio activation delay.
- A **fully documented assumption table**, including RF hardware power state curves.
- 7.x GHz chosen as test center inside the upper mid-band study range; **not a
  claim of allocated commercial spectrum in all jurisdictions**.
- Uplink and downlink resources/bands specified explicitly. Initial baseline is
  asymmetric dual-band UL/DL association, subject to 5G-LENA model support.

## Conceptual objective

min E[sum_t P_net(t)*delta_t + sum_t E_transition(t)]
subject to (per user, per direction) P(delivered_rate_{u,t} < required_rate_u) <= epsilon_u;
also application latency/loss when present, finite RB/antenna resources, UE limits,
legal EIRP, and controller decision latency.

For actual publication, specify the probability measure (over link fading,
traffic, user location or model uncertainty), the observation window, UL/DL
traffic classes and how outage is counted. Aggregate capacity is **not** a
substitute for user-level QoS constraints.

## Methodological comparisons

**Bandwidth:** matched-bandwidth to isolate frequency/radio effects; realistic
added-spectrum to quantify network capacity uplift.
**Hardware:** constant physical aperture vs constant element count; analog,
hybrid, fully digital where supported; EIRP/power restrictions explicitly matched.
**Traffic:** fixed demand trace, per-UE traffic distributions, mobility patterns,
separate UL/DL user loads.
**Propagation:** 3GPP TR 38.901 statistical and site-specific RT traces;
independent validation/calibration when actual measured traces available.
**Energy:** inactive, data, signaling/idle states; PA, RF chain, baseband,
cooling/power supply where relevant; wake energy/delay and controller costs.

## Strong baselines

1. Low-band-only (may be QoS-infeasible; cannot compare as valid energy competitor).
2. Always-on 3.5 + 7.x GHz (same delivered-traffic requirement).
3. Online threshold + hysteresis with UL and DL feasibility guards.
4. Model-based mixed-integer optimization or exact DP where problem size permits.
5. Clairvoyant offline controller **only an oracle lower bound**; compare fairly
   against online policies and state it sees future information.
6. Optional constrained RL/controller only if it beats strong model-based and
   heuristic alternatives on held-out scenes under consistent feasibility rules.

## Scientific acceptance gates

1. **API smoke tests:** independent imports/builds without custom patches.
2. **Model sanity:** path-loss trends, antenna gains, UL budget, noise/interference,
   units, energy conservation and finite power state accounting.
3. **Calibration:** validate 3GPP/RT behavior and hardware state assumptions
   against credible published measurements/data (not fabricated calibration).
4. **Cross-simulator check:** compare same PHY abstraction/link conditions,
   documented assumptions, reference traffic and channel trace lineage.
5. **Statistical inference:** multiple independent traffic/channel/site seeds,
   paired comparisons, confidence intervals, effect sizes, and site holdout.
6. **Ablation:** remove uplink guard; wake cost; RF-chain model; site specificity;
   uncertainty handling. Keep all baselines on exact same resources/constraints.
7. **Failure boundaries:** report regimes of no benefit and violations, including
   unforeseen wake-up overhead or high UL traffic.
8. **Artifacts:** input scenes, datasets/licenses, commits, configs, logs,
   seed manifest, tests and plotting scripts for every figure and table.

## Trace-based 5G-LENA caveat

NIST SioLENA repo is ns-3.44 and 5G-LENA 4.0 with a specialized input format.
The latest CTTC v5.1 is ns-3.48. Do not assume directly compatible C++ APIs.
Reproduce older SioLENA examples first, then choose controlled porting or
independent scenarios as a separate engineering work package.

## Novelty candidates that require literature verification

The contribution should not be another DQN routing/band-selection example.
A stronger possible contribution is a **feasibility envelope** identifying
traffic/channel/power regions where FR3 activation is beneficial, mandatory,
or infeasible, plus a robust online control algorithm and hardware-aware
transition accounting. Consider formalizing a lower bound: for an off interval
of T seconds, sleeping rather than staying active has energy benefit only when
`(P_active - P_sleep)*T > E_sleep + E_wake`, assuming another band handles
traffic and ignoring additional transient QoS costs. This is a *simplified
necessary tradeoff*, not a universal theorem for a real cellular system.

Published prior-art anchors to evaluate:
- Peschiera et al., FR3 base-station parametric power model, arXiv
  2510.10647v1 (Oct 2025); https://arxiv.org/abs/2510.10647. *Corrected
  2026-11-09: an earlier version of this file said "ICASSP 2026", which is
  not verified. The arXiv text states no venue.*
- López-Pérez, Piovesan, Geraci, multi-layer 6G capacity and power with the
  upper mid-band, arXiv 2411.09660 (ICC 2025); and López-Pérez, Piovesan,
  Bernabè, arXiv 2601.13993 (real deployment data). These are the closest
  prior art: DL-only, snapshot, always-on FR3 layer. See
  `docs/PHASE_3_ENERGY_PROTOCOL.md` §1.4.
- Ropitault et al., site-specific ray-tracing-driven ns-3 (CCNC 2026);
  https://www.nist.gov/publications/enabling-site-specific-cellular-network-simulation-through-ray-tracing-driven-ns-3
- Upper-mid-band spectrum for 6G surveys and cmWave large-array trials.

Avoid claiming novelty until a structured 2023–2026 related-work audit identifies
precisely what was not previously solved, with matching assumptions/baselines.

## Not paper-ready claims

No '99% QoS', '30% energy reduction', 'near-optimal RL' or 'verified hardware
power saving' claims without validated data. Seeded synthetic tests only verify
internal program logic and output schema.
