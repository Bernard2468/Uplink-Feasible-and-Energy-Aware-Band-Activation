#!/usr/bin/env bash
# Install the project-owned ns-3 programs (ns3/scratch/cmwave/*.cc) into the
# verified ns-3 tree and build ONLY their targets. Run inside WSL:
#   bash scripts/e1_5_install_ns3_program.sh
# Rules (docs/E1_5_VALIDATION_PROTOCOL.md): no NR or ns-3 source file is edited;
# the installed copy must be byte-identical to the repo copy.
#
# Hazard 12 (found 2026-11-07): in this tree, ANY CMake re-run (explicit, or by
# ninja when the set of scratch *.cc files changes) regenerates the PCH target
# and recompiles the whole tree (about 1200 objects). Therefore:
#   * an existing target is rebuilt WITHOUT re-running CMake (editing a .cc file
#     keeps the globs unchanged, so only that program recompiles);
#   * a NEW program file needs ALLOW_RECONFIGURE=1 and accepts a full rebuild;
#   * the build is refused if its dry run shows anything except this program.
set -euo pipefail
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
NS3="${NS3_DIR:-$HOME/6g-external/ns-3-dev}"
SRC="$REPO/ns3/scratch/cmwave"
DST="$NS3/scratch/cmwave"
LOGDIR="$HOME/cmwave-runs/build-logs"
mkdir -p "$LOGDIR"

test -d "$NS3/cmake-cache" || { echo "ns-3 cache not found: $NS3/cmake-cache"; exit 1; }
test "$(git -C "$NS3" rev-parse --short=8 HEAD)" = "d083a17a" || { echo "ns-3 is not at d083a17a"; exit 1; }
test "$(git -C "$NS3/contrib/nr" rev-parse --short=8 HEAD)" = "cedceadd" || { echo "NR is not at cedceadd"; exit 1; }
if [ -n "$(git -C "$NS3" status --porcelain --untracked-files=no)" ] || \
   [ -n "$(git -C "$NS3/contrib/nr" status --porcelain --untracked-files=no)" ]; then
  echo "tracked files modified in ns-3 or NR: refusing to build"; exit 1
fi

# The set of .cc files must not change unless a reconfigure is explicitly allowed.
new_files=0
for f in "$SRC"/*.cc; do
  [ -f "$DST/$(basename "$f")" ] || new_files=1
done
if [ -d "$DST" ]; then
  for f in "$DST"/*.cc; do
    [ -f "$SRC/$(basename "$f")" ] || { echo "stale program in $DST: $(basename "$f")"; exit 1; }
  done
fi
if [ "$new_files" = 1 ] && [ "${ALLOW_RECONFIGURE:-0}" != 1 ]; then
  echo "new program file(s): CMake must re-run, which rebuilds the whole tree (hazard 12)."
  echo "Re-run with ALLOW_RECONFIGURE=1 to accept that."; exit 1
fi

mkdir -p "$DST"
cp "$SRC"/*.cc "$DST"/
for f in "$SRC"/*.cc; do
  a=$(sha256sum "$f" | cut -c1-64); b=$(sha256sum "$DST/$(basename "$f")" | cut -c1-64)
  [ "$a" = "$b" ] || { echo "copy mismatch: $f"; exit 1; }
  echo "installed $(basename "$f") sha256=$a"
done

cd "$NS3"
if [ "$new_files" = 1 ]; then
  cmake -S . -B cmake-cache > "$LOGDIR/cmake_rescan_$(date -u +%Y%m%dT%H%M%S).log" 2>&1
fi
for f in "$SRC"/*.cc; do
  name=$(basename "$f" .cc)
  target="scratch_cmwave_${name}"
  # (capture first: 'grep -q' on a pipe would SIGPIPE ninja and trip pipefail)
  all_targets=$(ninja -C cmake-cache -t targets all)
  grep -q "^${target}:" <<< "$all_targets" || { echo "target $target unknown to ninja"; exit 1; }
  log="$LOGDIR/build_${name}_$(date -u +%Y%m%dT%H%M%S).log"
  if [ "$new_files" = 1 ]; then
    # Explicitly allowed reconfigure: a full rebuild is expected (hazard 12).
    ninja -C cmake-cache -j 4 "$target" > "$log" 2>&1 || { tail -40 "$log"; exit 1; }
    echo "built $target after reconfigure: $(grep -c 'Building CXX object' "$log" || true) objects (log $log)"
  else
    bash "$REPO/scripts/ns3_guarded_build.sh" "$target" "scratch_cmwave_${name}\.dir/cmwave/${name}\.cc\.o" "$log" \
      || { tail -20 "$log"; exit 1; }
  fi
done
