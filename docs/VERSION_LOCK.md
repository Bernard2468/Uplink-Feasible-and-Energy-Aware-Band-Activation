# Version lock and Sionna integration notes — 2026-11-06

Every entry below was checked directly against the upstream repository, PyPI,
or the installed software on the research machine, not taken from summaries.
Exact Python package versions: `env/requirements-lock.txt`.

## Locked research environment

| Layer | Version | Source / reason |
|---|---|---|
| Host | Windows 11 (10.0.26300), WSL 3.0.3, kernel 6.18.54.1 | Ryzen 5 PRO 3500U, 14 GB RAM (WSL capped at 10 GB + 8 GB swap), no NVIDIA GPU |
| Linux | Ubuntu 22.04.5 LTS | Listed as tested in the 5G-LENA v5.1 release notes |
| Compiler | g++ 11.4.0 | Tested by 5G-LENA maintainers on 22.04 |
| CMake | 3.31.10 (pip, in venv) | ns-3.48 requires >= 3.25; Ubuntu 22.04's 3.22.1 is too old |
| Ninja | 1.10.1 | Ubuntu package |
| Python | 3.12.16 (deadsnakes PPA) | The version the ns-3.48 Sionna guide says was tested; system Python 3.10.12 left untouched |
| ns-3 | tag `ns-3.48` = commit **d2add90b452d600cfb4859baed8e9ea633519447** (2026-06-02); 422ef9dc is the annotated tag object, not the commit | NR v5.1 "compatible with ns-3.48" (`ns-3.49` exists but is not the NR-supported pair) |
| 5G-LENA | branch `5g-lena-v5.1.y` = commit **cedceadda17392c90587fb9400eb9b1f8c236713** | Released 2026-08-06 |
| ns-3 patches applied | **One**: local backport of MR !2929 = commit **d083a17a3fb98f527a2711c2d9de3b69d841f895** on branch `ns-3.48+mr2929-backport` (parent: ns-3.48 d2add90b). Patch file: `env/patches/0001-spectrum-backport-ns-3-MR-2929-fa33c33cf-to-ns-3.48.patch` | Required: unmodified ns-3.48 breaks channel reciprocity with directional antennas (see "MR !2929") |
| ns-3 configure | `--enable-examples --enable-tests --enable-python-bindings` | Python bindings are required by the official ns-3 Sionna guide (see below) |
| sionna-rt | 1.2.0 (mitsuba 3.7.1, drjit 1.2.0) | Version tested by ns-3.48; used for BOTH standalone ray tracing and ns-3 |
| pybind11 | 2.11.1 | Version tested by ns-3.48 |
| cppyy | 3.5.0 | Pinned in ns-3.48 `pyproject.toml` and the Sionna guide |
| numpy | 1.26.4 | ns-3 tested set has numpy < 2.0 (via `sionna==1.2.0`); pybind11 2.11 predates NumPy 2 support |
| OR-Tools | 9.14.6206 | Newest release allowing numpy 1.x (9.15 requires numpy >= 2.0.2) |
| HiGHS (highspy) | 1.15.1 | MILP benchmark solver |
| Dr.Jit LLVM | libllvm18 1:18.1.8 (apt.llvm.org, jammy-18) | Pinned via `DRJIT_LIBLLVM_PATH` in the venv's activate script |

## Official ns-3 Sionna guide (the source of truth for the integration)

`ns-3-dev/doc/installation/source/sionna-rt.rst` (ns-3.48) says: Python 3.12
("the version we have tested"), `python3.12-dev`, a venv with
`sionna==1.2.0 sionna-rt==1.2.0 pybind11==2.11.1 cppyy==3.5.0`, the venv active
for every `./ns3` command, and `./ns3 configure --enable-examples --enable-tests
--enable-python-bindings`. Configure must print
"Sionna-RT support enabled: all required dependencies were found."
Verification program: `./ns3 run sionna-rt-channel-example`.

Single deliberate deviation: the full `sionna==1.2.0` package (TensorFlow) is not
installed. The ns-3 C++ code imports only `sionna.rt` and `numpy`
(`src/spectrum/model/sionna-rt-channel-model.cc`); numpy is pinned to 1.26.4 to
match the constraint that package would impose.

## Problems found and fixed during setup (2026-11-06)

1. **CMake too old.** ns-3.48 has `cmake_minimum_required(VERSION 3.25..3.25)`;
   Ubuntu 22.04 ships 3.22.1. Fix: `cmake==3.31.10` in the venv; the build
   script refuses to start with CMake < 3.25.
2. **Python 3.10 cannot run sionna-rt 2.2.0.** Its metadata declares
   `requires_python >= 3.10`, but `sionna/rt/path_solvers/paths.py` imports
   `typing.Self` (3.11+). Moot after moving to sionna-rt 1.2.0 on Python 3.12.
3. **Wrong LLVM picked by Dr.Jit.** Dr.Jit requires LLVM >= 15 and auto-selected
   `libLLVM-15.so.1` (15.0.7, installed with Mesa), which aborts on x86 with
   `LLVM ERROR: Cannot select ... fminimum`. Fix: libllvm18, pinned with
   `DRJIT_LIBLLVM_PATH=/usr/lib/x86_64-linux-gnu/libLLVM-18.so.1`.
4. **`Python.h: No such file or directory` building `cttc-nr-demo-sionna-rt`.**
   ns-3 searches for the Python development files only when Python bindings are
   enabled (`build-support/macros-and-definitions.cmake`); otherwise the Python
   include path reaches only targets inside `src/spectrum` (directory-scoped
   `link_libraries`). Fix: configure with `--enable-python-bindings`, as the
   official guide requires.
5. **Smoke-test API.** sionna-rt 1.2.0 `PathSolver()` takes no constructor
   arguments (`deterministic=` exists only in 2.x). Fixed in
   `scripts/sionna_rt_smoke.py`; reproducibility comes from the fixed `seed`.
6. **OR-Tools / numpy conflict.** OR-Tools 9.15 requires numpy >= 2.0.2. Fix:
   OR-Tools 9.14.6206.
7. **Reversed-direction field-pattern bug in ns-3.48** (uplink-relevant, up to
   17.2 dB in the regression test). Fix: local backport of MR !2929 (section below).
8. **Commit ID.** `git ls-remote` lists the annotated tag object (422ef9dc) for
   `ns-3.48`; the source commit is d2add90b. Records use the commit.

## Reproducibility of Sionna RT on CPU (measured)

Munich scene, 7.2 GHz, max_depth=2, seed=17, 4 repeated runs: identical path set
(10 paths, same delays), max |delta tau| = 5.7e-14 s, max relative |delta a| =
5.3e-5, total path gain differs by <= 5.7e-6 dB. Not bit-identical because
multi-threaded float32 reductions run in a non-deterministic order on the LLVM
backend; physically equivalent. Physical sanity: total path gain -86.61 dB vs
free-space loss -88.27 dB over the same 84 m distance.

## Other verified facts

| Item | Verified fact |
|---|---|
| 5G-LENA Sionna example | `examples/cttc-nr-demo-sionna-rt.cc`: default 1 gNB, 2 UEs, one band at 28 GHz; built only if `cppyy`, `sionna.rt`, `pybind11` are importable at configure time, and not on Windows |
| ns-3 Sionna model | Part of ns-3.48 itself: `src/spectrum/model/sionna-rt-channel-model.{h,cc}`, `sionna-rt-spectrum-propagation-loss-model.{h,cc}` |
| Upstream fixes | No ns-3 commit after ns-3.48 changes the Sionna build setup (checked 2026-11-06) |
| NIST SioLENA | `usnistgov/siolena` VERSION = 3.44; bundles 5G-LENA v4.0. Separate tree, never overlaid |
| Legacy generator | `wineslab/sionna-channel-generator` pins Sionna 0.19.2 (TensorFlow API) |
| Newest Sionna | sionna / sionna-rt 2.2.0 exist (2026-09-29) but are NOT what ns-3.48 was tested with |
| qiskit-optimization | GitHub repo archived (last push 2026-07-12). Quantum is future work only |
| Prior art | Peschiera, Yun, Lee, Van der Perre, Rottenberg, "A Parametric Power Model of Upper Mid-Band (FR3) Base Stations for 6G", arXiv:2510.10647 (2025-10-12) |

## MR !2929: reversed-direction field-pattern bug (affects uplink) - backported

**What the bug is.** `ThreeGppChannelModel` (TR 38.901 statistical channel)
generates cluster angles once per node pair, in node-id order, and reuses them
when `GetChannel` is queried in the opposite order (a "reversed-direction" link,
in typical NR scenarios the uplink, because gNBs are created before UEs). It
swaps departure/arrival angles for the array phase terms, but in
`GetNewChannel` the per-element field pattern read the raw, un-swapped
`channelParams->m_ray{Aoa,Zoa,Aod,Zod}Radian` arrays. With a directional element
(e.g. 3GPP outdoor pattern) the NLOS clusters are then weighted by the wrong
part of the pattern. Isotropic elements and the LOS ray are not affected.

**Timeline (from the ns-3 git history and GitLab API).** ns-3.48 commit
2026-06-02. MR !2883 ("further speed up 3GPP channel", commit ae5529988) merged
2026-06-26 and rewrote these lines. MR !2929 (fix fa33c33cf, "spectrum: Fix element
pattern on reversed-direction 3GPP channels") merged 2026-06-27. Neither is in ns-3.48.
NR v5.1 release notes: "FDD users should apply ... MR 2929 ... TDD users are not affected".

**Evidence that ns-3.48 itself is affected (2026-11-06).** A first reading of
`git blame` (which attributes the lines to the MR !2883 refactor) suggested
ns-3.48 was clean; that was wrong. Running the upstream regression test
`ThreeGppReversedDirectionFieldPatternTest` against UNMODIFIED ns-3.48 model code:
**FAIL, endpoint-order power mismatch 17.2089 dB** (tolerance +-1 dB). The 25
pre-existing three-gpp-channel tests pass. Log:
`results/verification/mr2929_diagnosis.log`. The defect in ns-3.48 is at
`three-gpp-channel-model.cc` lines 4105-4106 and 4112-4113.

**Backport.** The upstream commit does not apply (it targets post-MR !2883 code),
so the equivalent change was made: those 4 reads now use the direction-corrected
locals `rayAoaRadian`, `rayZoaRadian`, `rayAodRadian`, `rayZodRadian` (the same
arrays the phase terms already use). The upstream regression test is included
unchanged (167 lines). No other line in `GetNewChannel` reads the raw arrays
except the 8 swap assignments. Committed with a neutral local identity.

**Verification after backport.** `three-gpp-channel`: 26/26 cases PASS, including
the regression test. `cttc-nr-demo` output byte-identical before/after (its
scenario does not exercise this path). Logs:
`results/verification/mr2929_backport_verification.log`,
`results/verification/cttc-nr-demo_after_mr2929.txt`.

**Rules for this project.** Use this patched tree for every 3GPP-channel result.
Do NOT apply MR !2883 (speed-up) without MR !2929. The install script applies the
patch automatically and runs `three-gpp-channel` after every build. The Sionna RT
channel model is a separate code path and is not affected by this bug.

## Primary sources

- https://cttc-lena.gitlab.io/nr/html/ (release notes, example docs)
- https://gitlab.com/nsnam/ns-3-dev/-/blob/ns-3.48/doc/installation/source/sionna-rt.rst
- https://gitlab.com/nsnam/ns-3-dev/-/merge_requests/2929
- https://github.com/usnistgov/siolena

## Validation status

| Gate | Status |
|---|---|
| A: Python unit tests (in WSL) | PASSED 2026-11-06 |
| B: Sionna RT smoke test, 7.2 GHz (sionna-rt 1.2.0, CPU/LLVM 18) | PASSED 2026-11-06 |
| C: ns-3.48 + 5G-LENA v5.1 build + `cttc-nr-demo` | PASSED 2026-11-06: 2402/2402 targets, 0 errors, 281 min (4 jobs). Configure: Python 3.12.16 with Development.Embed found, "Sionna-RT support enabled", Python bindings ON; `cttc-nr-demo-sionna-rt` built |
| C: `cttc-nr-demo` output | Flow 1: 10.24 Mbps offered, 10.237 Mbps delivered, 5998/6000 packets, mean delay 0.276 ms. Flow 2: 102.4 Mbps offered, 102.229 Mbps delivered, 5990/6000 packets, mean delay 0.901 ms (FlowMonitor, i.e. from packet events; undelivered packets may be in flight at simulation end) |
| C: reproducibility | Two runs with the default seed: byte-identical output. `--RngRun=2` changes results (seed controls randomness). Logs: `results/verification/cttc-nr-demo_run{1,2}.txt` |
| ns-3 `sionna-rt-channel-example` (official Sionna verification) | PASSED: `[SUCCESS]`, 2 SNR samples (28.99, 28.70 dB; Rx power -93.46, -93.75 dBm), banner shows venv Python 3.12.16, 53 s on CPU. Output format matches the guide; the guide's sample numbers (23.41 dB at -67.52 dBm) imply a different noise level, so they are illustrative, not a numerical reference. Physical check of these values is part of the radio-sanity milestone |
| MR !2929 regression test | FAIL on unmodified ns-3.48 (17.2 dB); PASS after backport (26/26 three-gpp-channel cases) |
| Regression: all spectrum / antenna / nr-* test suites after backport | PASSED: **121/121 suites, 0 failures** (fullness QUICK, 178 min, HEAD d083a17a). Breakdown: 107 `nr-*` (incl. `nr-uplink-power-control-test`, `nr-ul-scheduling-test`, `nr-ul-sr-retransmission`), 4 `three-gpp-*` (channel, propagation-loss, NTN propagation-loss, V2V channel-condition), 4 `spectrum-*`, 5 antenna models (incl. `uniform-planar-array-test`), 1 `sionna-rt-channel-model`. Log: `results/verification/regression_suites_after_mr2929.log` |
| NR `cttc-nr-demo-sionna-rt` | Built; not yet run |

The Python unit tests validate only the small abstract-capacity model, not radio behavior.
