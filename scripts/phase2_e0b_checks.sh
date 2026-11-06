#!/usr/bin/env bash
# Phase 2, Gate E0b (see docs/PHASE_2_RADIO_PROTOCOL.md, section 2).
#  1. The NR band frequency must reach the Sionna RT scene: run the official
#     example with SionnaRtChannelModel logging at 3.5 and 7.2 GHz and require
#     "Scene created: frequency=<band>" (NR sets it fail-safe; default 500 MHz).
#  2. Sionna RT LOS gain must equal free-space gain and scale by 6.265 dB from
#     3.5 to 7.2 GHz, at the gNB/UE positions printed in that log.
#  3. Overload test for the 28 GHz reference: with Flow 2 offered 41 Mbps
#     instead of 102.4 Mbps (lambdaBe 4000), both flows should be delivered
#     almost completely if the reference loss is single-carrier overload.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
NS3="${NS3:-$HOME/6g-external/ns-3-dev}"
OUT="${OUT:-$HOME/6g-external/phase2_sionna/e0b}"
DEST="$ROOT/results/phase2/e0b_checks"
set +u; source "$HOME/.venvs/cmwave/bin/activate"; set -u
mkdir -p "$OUT" "$DEST"
cd "$NS3"
fail=0

for f in 3.5e9 7.2e9; do
  tag="freqlog_${f}"
  NS_LOG="SionnaRtChannelModel=level_info" ./ns3 run --no-build \
    "cttc-nr-demo-sionna-rt --centralFrequencyBand1=$f --numerologyBwp1=1 --simTime=410ms --outputDir=$OUT --simTag=$tag" \
    > "$OUT/$tag.log" 2>&1 || { echo "$tag: run FAILED"; fail=1; continue; }
  want=$(python -c "print(float('$f'))")
  { grep -E "Setting operating frequency|Scene created|exceeds the configured maximum" "$OUT/$tag.log" || true; } \
    | sed -E 's/tx@.*//' | sort | uniq -c > "$OUT/$tag.summary"
  scene_freqs=$({ grep -oE "Scene created: frequency=[^ ]+" "$OUT/$tag.log" || true; } | sed 's/.*=//' | sort -u)
  n_scenes=$(grep -c "Scene created" "$OUT/$tag.log" || true)
  ok=$(python - "$want" $scene_freqs <<'EOF'
import sys
want = float(sys.argv[1]); got = [float(x) for x in sys.argv[2:]]
print('yes' if got and all(abs(g - want) < 1 for g in got) else 'no')
EOF
)
  echo "$tag: scenes_created=$n_scenes scene_frequencies=[$(echo $scene_freqs)] expected=$want match=$ok" | tee -a "$OUT/checks.txt"
  [ "$ok" = yes ] || fail=1
  if grep -q "exceeds the configured maximum" "$OUT/$tag.log"; then
    echo "$tag: WARNING path-count limit hit (maxDepth was reduced)" | tee -a "$OUT/checks.txt"; fail=1
  fi
done

# Positions from the 7.2 GHz log: ns-3 prints vectors as x:y:z.
POS=$({ grep -oE "tx@[-+0-9.e]+:[-+0-9.e]+:[-+0-9.e]+ rx@[-+0-9.e]+:[-+0-9.e]+:[-+0-9.e]+" "$OUT/freqlog_7.2e9.log" || true; } \
      | tr ' ' '\n' | sed 's/^..@//' | sort -u)
echo "positions seen: $(echo $POS)" | tee -a "$OUT/checks.txt"
GNB=$(echo "$POS" | awk -F: '$3 > 5' | head -1)
UES=$(echo "$POS" | awk -F: '$3 <= 5')
[ -n "$GNB" ] && [ -n "$UES" ] || { echo "could not parse positions" | tee -a "$OUT/checks.txt"; exit 1; }
RXARGS=()
for u in $UES; do RXARGS+=(--rx ${u//:/ }); done
python "$ROOT/scripts/phase2_frequency_scaling_check.py" --tx ${GNB//:/ } "${RXARGS[@]}" \
  --out "$OUT/frequency_scaling_check.json" 2>&1 | tee "$OUT/frequency_scaling_check.txt" || fail=1

# Overload test (28 GHz reference configuration, only lambdaBe reduced).
tag=reference_28ghz_lambdaBe4000
./ns3 run --no-build "cttc-nr-demo-sionna-rt --lambdaBe=4000 --outputDir=$OUT --simTag=$tag" \
  > "$OUT/$tag.log" 2>&1 || { echo "$tag: run FAILED"; fail=1; }
echo "$tag: done" | tee -a "$OUT/checks.txt"

cp -f "$OUT"/* "$DEST"/ 2>/dev/null || true
echo "E0b overall: $([ $fail = 0 ] && echo PASSED || echo FAILED)" | tee -a "$DEST/checks.txt"
exit $fail
