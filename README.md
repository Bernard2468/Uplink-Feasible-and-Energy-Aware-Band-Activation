# cmWave 6G Co-Sited Network Research Starter — v0.2

**Scientific status:** SOFTWARE FOUNDATION ONLY. This archive does not include 3GPP-calibrated channels,
ray-tracing results, actual 5G-LENA packet simulations, FR3 hardware measurements, or validated 6G results.
All packaged output is synthetic and should not be used as evidence of energy savings or throughput.

## Architecture

**New in v0.2:** phase-1 protocol in `docs/PHASE_1.md`, updated version notes in
`docs/VERSION_LOCK.md`, privacy-minimal hardware report and current 5G-LENA
Sionna example guidance.

1. `cmwave/` — small, auditable abstract-capacity control policies, feasibility and energy accounting.
2. `scripts/run_baselines.py` — synthetic software sanity checks, five seeded traces by default.
3. `tests/` — unit tests including exact-DP optimality check against all feasible heuristics.
4. `scripts/sionna_rt_smoke.py` — optional real path calculation using installed Sionna RT.
5. `env/install_5glena_5_1.sh` — **separate** ns-3.48 + 5G-LENA 5.1 environment.
6. `env/install_siolena_reference.sh` — **separate** NIST SioLENA reference environment.
7. `docs/RESEARCH_DESIGN.md` — methods, data lineage, independent scientific gates.
8. `docs/SETUP.md` — Windows/Linux/Colab instructions and failure diagnoses.

## Zero-dependency quick start (Python 3.10+)

```bash
cd "Uplink-Feasible and Energy-Aware Band Activation"   # the project root
python3 -m unittest discover -s tests -v
python3 scripts/run_baselines.py --seeds 5 --slots 48
python3 scripts/check_environment.py --save
```

View `results/synthetic_smoke/results.csv`, `schedules.csv` and `run_manifest.json`.
The zero-dependency environment makes it possible to validate optimization and bookkeeping
before installing GPU simulation tools.

## Critical compatibility note: the published channel generator is **legacy Sionna**

The existing public Sionna-to-SioLENA generator at
https://github.com/wineslab/sionna-channel-generator explicitly pins **Sionna 0.19.2**,
which predates the current Sionna RT 2.2 API. It requires a separate environment.
DO NOT run it with modern Sionna RT without an intentional and tested port. Our standalone
`sionna_rt_smoke.py` uses the **modern** Sionna RT API (sionna-rt 1.2.0, the version
ns-3.48 was tested with), not the legacy generator. See docs/VERSION_LOCK.md.

## Modern integrated Sionna example

5G-LENA v5.1 ships optional `cttc-nr-demo-sionna-rt`, which is compiled only
when Sionna RT dependencies are available. Verify this modern example before
committing to a custom channel bridge. The NIST path remains a separate comparison.

## Why not simply concatenate Sionna and ns-3?

Sionna RT produces multipath/channel information; 5G-LENA simulates system/packet behavior.
NIST's SioLENA (https://github.com/usnistgov/siolena) implements a real ray-trace data interface,
but bundles ns-3.44/5G-LENA 4.0. This must not be assumed ABI/API compatible with ns-3.48/5G-LENA 5.1.
Run the official examples independently before modifying either codebase.

## Provenance links (verified November 2026)

- ns-3.48 official Sionna RT guide: https://gitlab.com/nsnam/ns-3-dev/-/blob/ns-3.48/doc/installation/source/sionna-rt.rst
- Sionna RT tutorial: https://nvlabs.github.io/sionna/rt/tutorials/Introduction.html
- 5G-LENA official installation: https://cttc-lena.gitlab.io/nr/html/
- 5G-LENA NR v5.1 release: https://cttc-lena.gitlab.io/nr/html/md__2builds_2cttc-lena_2nr_2_r_e_l_e_a_s_e___n_o_t_e_s.html
- NIST SioLENA: https://github.com/usnistgov/siolena
- NIST-associated channel generator (legacy Sionna 0.19.2): https://github.com/wineslab/sionna-channel-generator
- NR v5.1 FDD reversed-direction channel fix: https://gitlab.com/nsnam/ns-3-dev/-/merge_requests/2929
- NIST CCNC 2026: https://www.nist.gov/publications/enabling-site-specific-cellular-network-simulation-through-ray-tracing-driven-ns-3

## Stage gates

A. Python tests pass on user's machine (9 unit tests passed in package-builder environment).
B. Sionna RT smoke test completes in a documented environment.
C. Official 5G-LENA v5.1 example passes and baseline is reproduced.
D. NIST SioLENA example passes and trace reader is independently inspected.
E. Same 3.5 and 7.x GHz scenarios validated under comparable configurations.
F. Custom energy/controller results cross-checked against packet-level traffic and independent baselines.
G. Multi-seed, site-transfer, sensitivity, ablation and provenance complete before paper claims.
