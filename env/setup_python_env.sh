#!/usr/bin/env bash
# Creates the single shared Python environment used by the project code,
# Sionna RT and the 5G-LENA cttc-nr-demo-sionna-rt example.
# Run inside WSL Ubuntu 22.04 AFTER the system packages are installed
# (python3-venv, python3-dev, build-essential, cmake, llvm).
# The venv lives in the Linux filesystem (fast), not in the project folder.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV="${VENV:-$HOME/.venvs/cmwave}"
# Python 3.12 from the deadsnakes PPA (Ubuntu 22.04's own python3 is 3.10).
# sionna-rt 2.2.0 declares requires_python>=3.10 but its code imports
# typing.Self, which exists only in Python >= 3.11 (found 2026-11-06).
PY="${PYTHON:-python3.12}"

"$PY" -c 'import sys; assert sys.version_info >= (3, 11), "Python >= 3.11 required (sionna-rt uses typing.Self), found " + sys.version'
if [ -d "$VENV" ] && ! "$VENV/bin/python" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' 2>/dev/null; then
  echo "ERROR: existing $VENV uses Python < 3.11. Remove it first: rm -rf $VENV" >&2
  exit 1
fi
mkdir -p "$(dirname "$VENV")"
if [ ! -d "$VENV" ]; then
  "$PY" -m venv "$VENV"
fi

# Dr.Jit (Sionna's JIT engine) uses LLVM on the CPU when no NVIDIA GPU exists.
# It must be LLVM >= 18 here: Dr.Jit picked Ubuntu 22.04's LLVM 15.0.7 (pulled in
# by Mesa) and aborted with "LLVM ERROR: Cannot select ... fminimum" (2026-11-06).
# libllvm18 comes from apt.llvm.org (docs/SETUP.md, Step 2). Pinning the path
# in the venv's activate script makes the choice explicit and reproducible.
LLVM_LIB="${DRJIT_LIBLLVM_PATH:-/usr/lib/x86_64-linux-gnu/libLLVM-18.so.1}"
if [ ! -f "$LLVM_LIB" ]; then
  echo "ERROR: $LLVM_LIB not found. Install libllvm18 (docs/SETUP.md, Step 2)." >&2
  exit 1
fi
if ! grep -q 'DRJIT_LIBLLVM_PATH' "$VENV/bin/activate"; then
  printf '\n# Added by env/setup_python_env.sh: pin Dr.Jit to LLVM 18\nexport DRJIT_LIBLLVM_PATH="%s"\n' "$LLVM_LIB" >> "$VENV/bin/activate"
fi
set +u; source "$VENV/bin/activate"; set -u

python -m pip install --upgrade pip
# cppyy's backend is compiled from source here; this step can take 10-30 minutes.
python -m pip install -r "$ROOT/env/requirements.txt"
python -m pip check
python -m pip freeze > "$ROOT/env/requirements-lock.txt"

# Import check. Importing sionna.rt selects the Dr.Jit backend; on a machine
# without an NVIDIA GPU it must find LLVM, so this also verifies LLVM.
python - <<'EOF'
import importlib.metadata as md
import sionna.rt, mitsuba as mi, drjit as dr, cppyy, pybind11, numpy, scipy, highspy, ortools
print("Mitsuba variant:", mi.variant())
llvm = dr.detail.llvm_version()
print("Dr.Jit LLVM version:", ".".join(map(str, llvm)))
assert llvm >= (18, 0, 0), f"Dr.Jit loaded LLVM {llvm}; LLVM >= 18 required (see DRJIT_LIBLLVM_PATH)"
# Integration-critical versions must match the ns-3.48 Sionna guide exactly.
expected = {"sionna-rt": "1.2.0", "pybind11": "2.11.1", "cppyy": "3.5.0", "numpy": "1.26.4"}
for p in ("sionna-rt", "mitsuba", "drjit", "cppyy", "pybind11", "cmake", "numpy", "scipy", "highspy", "ortools"):
    print(f"  {p:10s} {md.version(p)}")
bad = {p: md.version(p) for p, v in expected.items() if md.version(p) != v}
assert not bad, f"Version mismatch vs ns-3 tested set: {bad}"
print("IMPORTS OK")
EOF
echo "cmake on PATH: $(command -v cmake) -> $(cmake --version | head -1)"
echo "Lock file written: $ROOT/env/requirements-lock.txt"
echo "Activate later with: source $VENV/bin/activate"
