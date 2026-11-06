#!/usr/bin/env bash
# Separate older NIST environment; DO NOT overlay it on modern 5G-LENA 5.1.
# The upstream NIST repo bundles 5G-LENA v4.0 and ns-3.44.
set -euo pipefail
BASE="${1:-$HOME/6g-external}"
mkdir -p "$BASE"
case "$BASE" in *" "*) echo "BASE path must not contain spaces: $BASE" >&2; exit 1;; esac
JOBS="${JOBS:-4}"
LOG="$BASE/build_siolena_$(date +%Y%m%d_%H%M%S).log"
echo "Full build log: $LOG"
exec > >(tee -a "$LOG") 2>&1
PKGS=(git build-essential cmake ninja-build python3 pkg-config
      libc6-dev sqlite3 libsqlite3-dev libeigen3-dev libgsl-dev)
MISSING=()
for p in "${PKGS[@]}"; do dpkg -s "$p" >/dev/null 2>&1 || MISSING+=("$p"); done
if [ "${#MISSING[@]}" -gt 0 ]; then
  sudo apt-get update
  sudo apt-get install -y "${MISSING[@]}"
fi
cd "$BASE"
if [ -e siolena ]; then
  echo 'Refusing to overwrite existing siolena. Choose a new directory.' >&2
  exit 1
fi
git clone https://github.com/usnistgov/siolena.git
cd siolena
./ns3 configure --enable-examples --enable-tests --build-profile=optimized
./ns3 build -j "$JOBS"
./ns3 run 'traces-channel-example --PrintHelp'
./ns3 run traces-channel-example
git rev-parse HEAD > build_version.txt
printf 'SUCCESS: NIST reference scenario ran. Version logged in %s\n' "$PWD/build_version.txt"
