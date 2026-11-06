# Setup: local-first hybrid research environment

Verified route as of 2026-11-06. Every version below was checked against the
upstream repository or PyPI on that date (see docs/VERSION_LOCK.md).

## Where things live

| What | Location | Why |
|---|---|---|
| Project code, docs, results (single master copy, git repo) | Windows: `Desktop\Uplink-Feasible and Energy-Aware Band Activation` = WSL: `/mnt/c/Users/FJ/Desktop/Uplink-Feasible and Energy-Aware Band Activation` | One copy only; editable from Windows and runnable from WSL |
| ns-3.48 + 5G-LENA v5.1 build | WSL: `~/6g-external/ns-3-dev` | Linux filesystem is much faster; ns-3 must not be built in a path with spaces |
| NIST SioLENA reference (later, Gate D) | WSL: `~/6g-external/siolena` | Separate ns-3.44 / NR v4.0 tree; never overlay on v5.1 |
| Python environment | WSL: `~/.venvs/cmwave` | One shared env (see below) |
| Large GPU ray-tracing jobs | Google Colab (GPU runtime) | No NVIDIA GPU locally; outputs downloaded into `data/` with a manifest |

## Step 1 - WSL2 + Ubuntu 22.04 (Windows, admin)

Ubuntu 22.04 is chosen because the 5G-LENA v5.1 release notes list it as tested
(g++ 11/12). Python 3.12 is added from the deadsnakes PPA (Step 2) because the
official ns-3.48 Sionna guide (`doc/installation/source/sionna-rt.rst`) states
3.12 is the tested version. Ubuntu's system `python3` stays 3.10 and must not be
replaced (apt depends on it).

```powershell
wsl --install -d Ubuntu-22.04 --no-launch
```

Restart Windows if asked. Then open **Ubuntu 22.04** from the Start menu once and
create your Linux username and password (remember the password: `sudo` asks for it).

WSL memory: `C:\Users\FJ\.wslconfig` sets `memory=10GB` and `swap=8GB`. The
default (half of RAM, ~7 GB) can kill the ns-3 compile. After editing it, run
`wsl --shutdown`.

## Step 2 - System packages (Ubuntu)

```bash
sudo apt-get update
sudo apt-get install -y git build-essential cmake ninja-build python3 python3-venv \
  python3-pip python3-dev pkg-config libc6-dev sqlite3 libsqlite3-dev \
  libeigen3-dev libgsl-dev llvm unzip software-properties-common
sudo add-apt-repository -y ppa:deadsnakes/ppa
sudo apt-get update
sudo apt-get install -y python3.12 python3.12-venv python3.12-dev
# LLVM 18 runtime for Sionna RT on the CPU (official LLVM apt repository)
curl -fsSL https://apt.llvm.org/llvm-snapshot.gpg.key | sudo tee /etc/apt/trusted.gpg.d/apt.llvm.org.asc >/dev/null
echo "deb https://apt.llvm.org/jammy/ llvm-toolchain-jammy-18 main" | sudo tee /etc/apt/sources.list.d/llvm-toolchain-jammy-18.list
sudo apt-get update
sudo apt-get install -y libllvm18
```

Why LLVM 18: Dr.Jit requires LLVM >= 15, but with Ubuntu 22.04's LLVM 15.0.7
(installed by Mesa) the Sionna smoke test aborts with
`LLVM ERROR: Cannot select ... fminimum`. LLVM 18.1.8 is used instead;
`env/setup_python_env.sh` pins it via `DRJIT_LIBLLVM_PATH` in the venv.

`llvm` is required: without an NVIDIA GPU, Sionna RT runs on the Dr.Jit LLVM (CPU)
backend. `python3.12-dev` is required to compile cppyy and for ns-3 to find the
Python development files.

## Step 3 - Gate A: Python foundation checks (Ubuntu)

```bash
cd "/mnt/c/Users/FJ/Desktop/Uplink-Feasible and Energy-Aware Band Activation"
bash scripts/first_run.sh
```

PASS = all unit tests OK and the final line
`PASS: Python foundation checks complete.` Outputs: `results/verification/*.log`,
`results/environment_check.json`.

## Step 4 - Shared Python environment (Ubuntu)

```bash
bash env/setup_python_env.sh
```

Why ONE environment and not a separate Sionna env: 5G-LENA v5.1 compiles the
optional example `cttc-nr-demo-sionna-rt` only if `cppyy`, `sionna.rt` and
`pybind11` are importable by the `python3` that ns-3 sees at configure time
(`nr/examples/CMakeLists.txt`). Versions follow the official ns-3.48 Sionna
guide exactly: `sionna-rt==1.2.0`, `pybind11==2.11.1`, `cppyy==3.5.0`, with
numpy 1.26.4 (see docs/VERSION_LOCK.md). The script fails if any differ.

The venv also provides **CMake 3.31.10**: ns-3.48 requires CMake >= 3.25, but
Ubuntu 22.04 ships 3.22.1. Always build ns-3 with the venv active (the install
script does this, and refuses to start if CMake is older than 3.25). The script installs
`env/requirements.txt`, writes the exact resolved versions to
`env/requirements-lock.txt` and ends with `IMPORTS OK`. cppyy compiles from
source: expect 10-30 minutes.

## Step 5 - Gate B: Sionna RT smoke test (Ubuntu)

```bash
source ~/.venvs/cmwave/bin/activate
python scripts/sionna_rt_smoke.py
```

PASS = `Sionna-RT smoke test PASSED`. If it reports an LLVM/Dr.Jit error, check
`echo $DRJIT_LIBLLVM_PATH` (should be `/usr/lib/x86_64-linux-gnu/libLLVM-18.so.1`)
and `python -c "import drjit as dr; print(dr.detail.llvm_version())"` (should be 18.x).
A backend error does not mean the ray-tracing model is wrong.

## Step 6 - Gate C: ns-3.48 + 5G-LENA v5.1 (Ubuntu)

Measured build time: about 4.7 hours on a 4-core Ryzen 5 3500U with `JOBS=4`
(2402 targets). Keep the laptop plugged in and prevent sleep.

```bash
bash env/install_5glena_5_1.sh
```

The script activates `~/.venvs/cmwave` (so the Sionna example is detected),
configures with `--enable-python-bindings` (required by the official ns-3 Sionna
guide; without it the NR Sionna example fails with `Python.h: No such file`),
reuses an existing source tree only if it is a clean ns-3.48 + 5g-lena-v5.1.y
checkout (then rebuilds from clean),
installs missing system packages only if needed, builds with 4 parallel jobs
(`JOBS=2 bash ...` if memory runs out), runs the official `cttc-nr-demo`, records
both commit IDs in `build_versions.txt`, reports whether `cttc-nr-demo-sionna-rt`
was built, and logs everything to `~/6g-external/build_5glena_5_1_<timestamp>.log`.
Keep the complete log if anything fails.

Then verify the Sionna integration (venv active), first with ns-3's own
verification program (expected output: per-step SNR lines and `[SUCCESS]`),
then the 5G-LENA example:

```bash
source ~/.venvs/cmwave/bin/activate
cd ~/6g-external/ns-3-dev
./ns3 run sionna-rt-channel-example
./ns3 run "cttc-nr-demo-sionna-rt --PrintHelp"
```

See https://cttc-lena.gitlab.io/nr/html/cttc-nr-demo-sionna-rt_8cc.html

## Uplink-critical ns-3 fix (MR !2929): applied automatically

Unmodified ns-3.48 evaluates the antenna element pattern with un-swapped angles
on reversed-direction links of the 3GPP channel model (typically the uplink).
The upstream regression test fails on ns-3.48 with a **17.2 dB** channel-power
mismatch. `env/install_5glena_5_1.sh` therefore applies the local backport in
`env/patches/` on branch `ns-3.48+mr2929-backport` and runs the
`three-gpp-channel` test suite after every build. Never apply MR !2883 (3GPP
channel speed-up) without MR !2929. Full analysis: docs/VERSION_LOCK.md.
https://gitlab.com/nsnam/ns-3-dev/-/merge_requests/2929

## Step 7 (later) - Gate D: NIST SioLENA reference

```bash
bash env/install_siolena_reference.sh
```

Based on ns-3.44 + 5G-LENA v4.0, built in its own directory. Best-effort
reference build; not API-compatible with v5.1.

## Legacy channel generator

https://github.com/wineslab/sionna-channel-generator pins **Sionna 0.19.2**
(older TensorFlow-based API). If used, give it its own separate venv. It is not
interchangeable with sionna-rt 1.2.0; port deliberately and compare path delays,
gains, angle conventions, carrier frequency and device ordering.

## Google Colab (GPU ray tracing)

```python
!pip install sionna-rt==1.2.0 numpy==1.26.4
# Restart the runtime if prompted, then:
!python scripts/sionna_rt_smoke.py
```

Use the same pinned version as locally. Run one identical small scene both
locally (CPU/LLVM) and on Colab (GPU) and compare path gains: this is a
cross-backend consistency check. Colab sessions are temporary and the GPU type
is not guaranteed: download every output, with its configuration and seed.

## Common errors

- `$'\r': command not found` -> a script got Windows line endings. `.gitattributes`
  forces LF; fix an affected file with `sed -i 's/\r$//' <file>`.
- `ModuleNotFoundError: sionna` -> the venv is not activated.
- `LLVM`/`DrJit` backend error -> `llvm` apt package missing or not found; see Step 5.
- `CMake ... Eigen3 OFF` -> install `libeigen3-dev`, reconfigure ns-3.
- `c++: fatal error: Killed signal terminated program cc1plus` -> out of memory;
  rerun with `JOBS=2`, check `.wslconfig`.
- `Build targets don't show nr` -> wrong checkout location or NR version mismatch.
- `No feasible schedule` -> intended model behavior for an impossible demand trace;
  check inputs, units and whether demand is attainable even with FR3 active.
- `FR3 policy looks better than low-band-only` -> first check whether the latter
  violates constraints. Comparing only raw energy is scientifically misleading.
