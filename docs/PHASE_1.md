# PHASE 1 — The first milestone: evidence before algorithms

**Status:** Detailed plan and software sanity checks; NOT a completed 6G experiment.

## The question (one sentence)

At fixed existing macro sites, for which traffic/propagation/hardware regimes does
selective activation of an added 7.2 GHz layer reduce whole-network electrical
energy while maintaining per-UE uplink and downlink service targets?

The choice of 7.2 GHz is an experimental center frequency, not an assumption of
commercial spectrum entitlement. We will later test frequency sensitivity and
regulatory limitations for the intended region.

## Freeze the central scientific distinction

- **Baseline:** 3.5 GHz base layer, same existing site layout.
- **Addition:** co-sited 7.2 GHz capacity layer; deactivate/reactivate it only when warranted.
- **Controller:** chooses activation *without* perfect advance knowledge of demand.
- **System outcomes:** delivered packets/UL and DL rates, outage, delays, energy,
  switching events and operational feasibility.
- **Future work only:** quantum optimization. It is neither simulated nor claimed here.

## Scientific novelty candidates to verify

1. A *feasibility/benefit map*: precisely where 7 GHz activation is necessary,
   optional-but-beneficial, always wasteful, or unable to meet service constraints.
2. A control algorithm that preserves **per-user UL + DL** feasibility and accounts
   for physically justified RF-chain, beam-management and switching overhead.
3. Controlled comparison between statistical and site-specific propagation where
   the *same controller* can fail due to model mismatch.

**These are hypotheses for a structured literature audit, not verified firsts.**

## Milestone 1 — Setup and scientific gates

1. Create a durable local repository and preserve raw logs, software versions,
   default example output and test results. Use Ubuntu/WSL2 or native Linux.
2. Execute `python3 -m unittest discover -s tests -v`: PASS only if all tests succeed.
3. Execute `python3 scripts/run_baselines.py --seeds 5 --slots 48`:
   record synthetic test output; do NOT publish these values as radio results.
4. Execute `python3 scripts/check_environment.py --save`:
   record available CPU/RAM/disk and toolchains.
5. Build and run the official 5G-LENA v5.1 basic `cttc-nr-demo` example.
6. Inspect whether the optional `cttc-nr-demo-sionna-rt` target was compiled;
   it builds only when Sionna RT dependencies are available. Run only after
   the official instructions and dependencies pass.
7. Record exact ns-3 and NR commits. Identify whether the FDD NLOS fix MR2929
   is present before *any* UL/FDD science claims.
8. Create a frozen control experiment with **one gNB, one UE, only 3.5 GHz**,
   then bring in one 7.2 GHz band under identical reproducible traffic.

## Scientific gates before results are called evidence

**Radio sanity:** path loss vs distance; UE transmit power, antennas, UL noise,
coverage; same channel assumptions under each policy; no unphysical rates.

**Traffic sanity:** offered traffic bytes distinguish from received bytes;
application throughput from actual delivered bytes/time; packet loss counted
by sequence IDs/drops; delays from packet timestamps; per-UE and per-direction.

**Energy sanity:** all power state values sourced/calibrated or labeled as
sensitivity assumptions; dimensional consistency P[W]×t[s]=E[J]; energy cost
for wake and controller included; wake-up delay affects service.

**Fairness sanity:** always-on benchmark and adaptive controller see identical
traffic/sites/PHY and same feasibility constraints; offline clairvoyant solver
is an *oracle bound* not an online apples-to-apples competitor; no exclusion
of failed high-load cases from reported denominators.

**Experimental robustness:** independent runs varying traffic/channel/location;
held-out urban scenes; 95% confidence intervals and paired differences;
transparent configuration and source code; preregistered primary comparisons.

## What should be DONE at the end of this milestone?

A passing test log, functioning official NR baseline, documented platform
versions and validated packet counters. No invented FR3 gains or benchmark
claims are permitted at this stage.
