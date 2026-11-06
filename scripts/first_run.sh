#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
mkdir -p results/verification
python3 -m unittest discover -s tests -v 2>&1 | tee results/verification/tests.log
python3 scripts/run_baselines.py --seeds 5 --slots 48 2>&1 | tee results/verification/synthetic_test.log
python3 scripts/check_environment.py --save 2>&1 | tee results/verification/environment.log
printf '\nPASS: Python foundation checks complete. No radio or packet-level model verified yet.\n'
