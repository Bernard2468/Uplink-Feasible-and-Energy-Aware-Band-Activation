#!/usr/bin/env bash
# Build ONE ns-3 target without risking a whole-tree rebuild (hazard 12).
#   usage: bash scripts/ns3_guarded_build.sh <ninja-target> <allowed-object-regex> <logfile>
# 1. CMake's glob check is run on a side-effect-free copy of VerifyGlobs.cmake
#    (the original touches a stamp on mismatch). A mismatch means ninja would
#    re-run CMake, which regenerates the PCH and recompiles the tree: refuse.
#    (A ninja dry run cannot answer this: the glob check is always dirty, so a
#    dry run stops at "Re-running CMake" and lists nothing.)
# 2. The real build is watched line by line and its whole process group is
#    killed if CMake re-runs or an object outside <allowed-object-regex> is
#    reported. With non-terminal output ninja reports an edge when it FINISHES,
#    so this watcher only limits damage to the jobs already in flight (tested
#    2026-11-07: abort leaves no compiler running and the target unbuilt). The
#    protection against the whole-tree rebuild is the exact glob check in step 1.
set -uo pipefail
NS3="${NS3_DIR:-$HOME/6g-external/ns-3-dev}"
target="$1"
allowed="$2"
log="$3"
cd "$NS3"
tmp=$(mktemp)
sed '/TOUCH_NOCREATE/d' cmake-cache/CMakeFiles/VerifyGlobs.cmake > "$tmp"
if cmake -P "$tmp" 2>&1 | grep -q "GLOB mismatch"; then
  rm -f "$tmp"
  echo "glob mismatch: building $target would re-run CMake (full rebuild, hazard 12): refusing"
  exit 2
fi
rm -f "$tmp"
pidfile=$(mktemp)
: > "$log"
# ninja runs in its own session (setsid), so an abort kills the whole process
# group: ninja AND the compilers it started (killing ninja alone lets in-flight
# compiles finish).
{ setsid ninja -C cmake-cache -j 4 "$target" 2>&1 & echo $! > "$pidfile"; wait $!; echo "__NINJA_EXIT__ $?"; } |
while IFS= read -r line; do
  echo "$line" >> "$log"
  case "$line" in
    "__NINJA_EXIT__ "*) exit "${line#__NINJA_EXIT__ }" ;;
    *"Re-running CMake"*)
      kill -TERM -- "-$(cat "$pidfile")" 2>/dev/null; echo "ABORTED: CMake re-run" | tee -a "$log"; exit 3 ;;
    *"Building CXX object"*)
      if ! [[ "$line" =~ $allowed ]]; then
        kill -TERM -- "-$(cat "$pidfile")" 2>/dev/null
        echo "ABORTED: unexpected object: $line" | tee -a "$log"; exit 3
      fi ;;
  esac
done
status=${PIPESTATUS[1]}
rm -f "$pidfile"
echo "target $target: exit $status, $(grep -c 'Building CXX object' "$log" || true) objects compiled (log $log)"
exit "$status"
