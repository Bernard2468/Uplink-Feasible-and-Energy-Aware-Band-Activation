#!/usr/bin/env bash
# Phase 2, Gate E0: run the OFFICIAL 5G-LENA example cttc-nr-demo-sionna-rt
# UNMODIFIED on the verified ns-3.48 + MR !2929 + NR v5.1 build.
#
# This is a SOFTWARE gate, not a scientific experiment. Facts verified in the
# example source (contrib/nr/examples/cttc-nr-demo-sionna-rt.cc, NR cedceadd):
#   - traffic is DOWNLINK only (UdpClient on the remote host, UdpServer on UEs);
#   - 1 gNB at 10 m height, 2 UEs at 1.5 m dropped in a 3 m x 3 m area;
#   - Sionna RT path solver: maxDepth=1, LOS only (no reflection/diffraction);
#   - isotropic elements, gNB 4x8, UE 2x4; 50 MHz; totalTxPower 35 dBm;
#   - default 28 GHz with numerology 4 (240 kHz SCS, not valid for FR1 data).
# Runs:
#   reference_28ghz         unmodified defaults (reproduces the official example)
#   reference_28ghz_rerun   same again (run-to-run determinism check)
#   trial_3p5ghz_mu1        3.5 GHz, numerology 1 (30 kHz SCS)
#   trial_7p2ghz_mu1        7.2 GHz, numerology 1 (30 kHz SCS)
# The two trials only check that the integrated stack runs at FR1/FR3
# frequencies. Same element count is NOT same aperture, the UEs are a few
# metres from the gNB and traffic is DL only: no coverage or uplink claim
# can be drawn from them.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
NS3="${NS3:-$HOME/6g-external/ns-3-dev}"
OUT="${OUT:-$HOME/6g-external/phase2_sionna}"
DEST="$ROOT/results/phase2/e0_sionna_gate"
case "$OUT" in *" "*) echo "OUT must not contain spaces: $OUT" >&2; exit 1;; esac
set +u; source "$HOME/.venvs/cmwave/bin/activate"; set -u
mkdir -p "$OUT" "$DEST"
cd "$NS3"

{ echo "date_utc $(date -u +%Y-%m-%dT%H:%M:%SZ)"
  echo "ns3 $(git rev-parse HEAD) ($(git branch --show-current))"
  echo "nr $(git -C contrib/nr rev-parse HEAD)"
  echo "python $(python --version 2>&1) at $(command -v python)"
  python -c 'import sionna.rt, mitsuba, drjit as dr; print("sionna-rt", sionna.rt.__version__, "mitsuba", mitsuba.__version__, "drjit", dr.__version__, "llvm", dr.detail.llvm_version())'
  echo "DRJIT_LIBLLVM_PATH ${DRJIT_LIBLLVM_PATH:-unset}"
  echo "ns3_tree_clean $([ -z "$(git status --porcelain --untracked-files=no)" ] && echo yes || echo NO)"
} > "$OUT/versions.txt" 2>&1

./ns3 run --no-build "cttc-nr-demo-sionna-rt --PrintHelp" > "$OUT/help.txt" 2>&1

run() {
  local tag="$1"; shift
  local start rc
  start=$(date +%s)
  if ./ns3 run --no-build "cttc-nr-demo-sionna-rt $* --outputDir=$OUT --simTag=$tag" \
       > "$OUT/$tag.log" 2>&1; then rc=0; else rc=$?; fi
  echo "$tag exit=$rc elapsed_s=$(( $(date +%s) - start )) args='$*'" | tee -a "$OUT/runs.txt"
}
: > "$OUT/runs.txt"
run reference_28ghz
run reference_28ghz_rerun
run trial_3p5ghz_mu1 --centralFrequencyBand1=3.5e9 --numerologyBwp1=1
run trial_7p2ghz_mu1 --centralFrequencyBand1=7.2e9 --numerologyBwp1=1

if cmp -s "$OUT/reference_28ghz" "$OUT/reference_28ghz_rerun"; then
  echo "determinism: reference_28ghz output IDENTICAL on rerun" | tee -a "$OUT/runs.txt"
else
  echo "determinism: reference_28ghz output DIFFERS on rerun" | tee -a "$OUT/runs.txt"
fi
cp -f "$OUT"/* "$DEST"/ 2>/dev/null || true
echo "Copied outputs to $DEST"
