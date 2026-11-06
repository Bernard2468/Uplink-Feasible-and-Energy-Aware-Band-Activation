#!/usr/bin/env bash
# Builds ns-3.48 + 5G-LENA 5g-lena-v5.1.y (the officially compatible pair,
# verified against the NR v5.1 release notes on 2026-11-06), with the local
# backport of ns-3 MR !2929 from env/patches/ applied on branch
# "ns-3.48+mr2929-backport" (see docs/VERSION_LOCK.md for why it is required).
# Run inside WSL Ubuntu 22.04 with env/setup_python_env.sh already done.
# Heavy compile: ~4.7 hours measured on a 4-core Ryzen 5 3500U with JOBS=4.
# Builds in the LINUX filesystem (default ~/6g-external): much faster than /mnt/c,
# and ns-3 must not be built in a path containing spaces.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PATCH_DIR="$ROOT/env/patches"
NS3_BRANCH="ns-3.48+mr2929-backport"
BASE="${1:-$HOME/6g-external}"
# Parallel compile jobs. Each g++ job on the nr module can need 1-2 GB RAM;
# with WSL capped at ~10 GB, 4 jobs is safe. Override: JOBS=2 bash ...
JOBS="${JOBS:-4}"
VENV="${VENV:-$HOME/.venvs/cmwave}"
case "$BASE" in *" "*) echo "BASE path must not contain spaces: $BASE" >&2; exit 1;; esac
mkdir -p "$BASE"
LOG="$BASE/build_5glena_5_1_$(date +%Y%m%d_%H%M%S).log"
echo "Full build log: $LOG"
# Send all remaining output to the terminal AND the log, so failures are preserved.
exec > >(tee -a "$LOG") 2>&1

# System packages: only call sudo if something is missing (keeps reruns non-interactive).
PKGS=(git build-essential cmake ninja-build python3 python3-venv python3-pip python3-dev
      pkg-config libc6-dev sqlite3 libsqlite3-dev libeigen3-dev libgsl-dev)
MISSING=()
for p in "${PKGS[@]}"; do dpkg -s "$p" >/dev/null 2>&1 || MISSING+=("$p"); done
if [ "${#MISSING[@]}" -gt 0 ]; then
  echo "Installing missing system packages: ${MISSING[*]}"
  sudo apt-get update
  sudo apt-get install -y "${MISSING[@]}"
fi

# The Sionna RT model and the cttc-nr-demo-sionna-rt example are compiled only if
# cppyy, sionna.rt and pybind11 are importable by the python3 seen at configure time.
if [ -f "$VENV/bin/activate" ]; then
  set +u; source "$VENV/bin/activate"; set -u
  echo "Using Python env $VENV ($(python --version))"
else
  echo "NOTE: $VENV not found -> Sionna RT support and cttc-nr-demo-sionna-rt will be skipped."
  echo "      Run env/setup_python_env.sh first if you want them included."
fi

# ns-3.48 requires CMake >= 3.25; Ubuntu 22.04's own cmake is 3.22.1.
# The shared venv provides cmake==3.31.10 (env/requirements.txt).
CMAKE_VER="$(cmake --version | head -1 | awk '{print $3}')"
if ! printf '3.25.0\n%s\n' "$CMAKE_VER" | sort -V -C; then
  echo "ERROR: CMake $CMAKE_VER found ($(command -v cmake)); ns-3.48 needs >= 3.25." >&2
  echo "Run env/setup_python_env.sh first (it installs cmake 3.31.10 into the venv)." >&2
  exit 1
fi
echo "Using CMake $CMAKE_VER from $(command -v cmake)"

PATCHES=("$PATCH_DIR"/*.patch)
[ -f "${PATCHES[0]}" ] || { echo "ERROR: no patches found in $PATCH_DIR" >&2; exit 1; }

apply_patches() {
  # Local commits use a neutral identity; nothing is pushed anywhere.
  git switch -c "$NS3_BRANCH"
  git -c user.name="cmwave6g local backport" -c user.email="noreply@localhost" am "${PATCHES[@]}"
}

cd "$BASE"
if [ -e ns-3-dev ]; then
  # Reuse an existing tree ONLY if it is clean and is either pristine ns-3.48 or
  # ns-3.48 plus exactly our patches on $NS3_BRANCH. Never overwrite sources.
  cd ns-3-dev
  NS3_COMMIT="$(git rev-parse 'ns-3.48^{commit}')"
  if [ "$(git -C contrib/nr rev-parse --abbrev-ref HEAD 2>/dev/null)" != "5g-lena-v5.1.y" ] \
     || [ -n "$(git status --porcelain --untracked-files=no)" ] \
     || [ -n "$(git -C contrib/nr status --porcelain --untracked-files=no)" ]; then
    echo "Existing $BASE/ns-3-dev has local changes or a wrong NR branch. Refusing to touch it." >&2
    exit 1
  fi
  if [ "$(git rev-parse HEAD)" = "$NS3_COMMIT" ]; then
    apply_patches
  elif [ "$(git branch --show-current)" = "$NS3_BRANCH" ] \
       && [ "$(git merge-base HEAD "$NS3_COMMIT")" = "$NS3_COMMIT" ] \
       && [ "$(git rev-list --count "$NS3_COMMIT"..HEAD)" = "${#PATCHES[@]}" ]; then
    echo "Patches already applied on $NS3_BRANCH."
  else
    echo "Existing $BASE/ns-3-dev is neither ns-3.48 nor ns-3.48 + our patches. Refusing to touch it." >&2
    exit 1
  fi
  echo "Reusing verified checkout in $PWD; cleaning previous build artefacts."
  ./ns3 clean
else
  git clone https://gitlab.com/nsnam/ns-3-dev.git
  cd ns-3-dev
  git checkout ns-3.48
  apply_patches
  mkdir -p contrib
  cd contrib
  git clone https://gitlab.com/cttc-lena/nr.git
  cd nr
  git checkout 5g-lena-v5.1.y
  cd ../..
fi
# --enable-python-bindings is required by the official ns-3.48 Sionna guide
# (doc/installation/source/sionna-rt.rst): only then does ns-3 locate the Python
# development headers and add them to all targets. Without it, the 5G-LENA
# example cttc-nr-demo-sionna-rt fails with "Python.h: No such file or directory".
CONFIGURE_FLAGS=(--enable-examples --enable-tests)
if [ -n "${VIRTUAL_ENV:-}" ]; then
  CONFIGURE_FLAGS+=(--enable-python-bindings)
fi
./ns3 configure "${CONFIGURE_FLAGS[@]}"
./ns3 build -j "$JOBS"
# Regression guard for the reversed-direction field-pattern bug (MR !2929).
./test.py --no-build -s three-gpp-channel
./ns3 run cttc-nr-demo
{ printf 'ns-3 %s (branch %s, base ns-3.48 %s)\n' "$(git rev-parse HEAD)" "$(git branch --show-current)" "$(git rev-parse 'ns-3.48^{commit}')"
  git log --format='  patch: %h %s' "ns-3.48^{commit}..HEAD"
  printf 'nr %s\n' "$(git -C contrib/nr rev-parse HEAD)"; } > build_versions.txt
if find build -type f -name '*cttc-nr-demo-sionna-rt*' | grep -q .; then
  echo 'cttc-nr-demo-sionna-rt: BUILT'
else
  echo 'cttc-nr-demo-sionna-rt: NOT built (optional Python dependencies not found at configure time)'
fi
printf 'SUCCESS: built, passed three-gpp-channel tests and ran the official NR demo. Versions: %s\n' "$PWD/build_versions.txt"
