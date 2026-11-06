#!/usr/bin/env python3
"""Run synthetic software validation, never paper-grade results."""
import argparse
import csv
import json
import platform
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from cmwave.control import Energy, evaluate, policies
from cmwave.scenarios import create_synthetic_trace


def main():
    parser = argparse.ArgumentParser(description='Synthetic control smoke test, NOT research evidence')
    parser.add_argument('--seeds', type=int, default=5)
    parser.add_argument('--slots', type=int, default=48)
    parser.add_argument('--output', default=str(ROOT / 'results' / 'synthetic_smoke'))
    args = parser.parse_args()
    if args.seeds < 1 or args.slots < 1:
        parser.error('--seeds and --slots must be positive')
    outdir = Path(args.output)
    outdir.mkdir(parents=True, exist_ok=True)
    hw = Energy()
    rows = []
    traces = []
    for seed in range(args.seeds):
        slots = create_synthetic_trace(seed=seed, nslots=args.slots)
        for name, schedule in policies(slots, hw).items():
            if schedule is None:
                rows.append({'seed': seed, 'policy': name, 'energy_j': '',
                             'violations': '', 'violation_fraction': '', 'wake_events': '', 'feasible': False,
                             'status': 'no_feasible_schedule'})
                continue
            ev = evaluate(slots, schedule, hw)
            rows.append({'seed': seed, 'policy': name, 'energy_j': round(ev['energy_j'], 3),
                         'violations': ev['constraint_violations'],
                         'violation_fraction': round(ev['violation_fraction'], 6),
                         'wake_events': ev['wake_events'], 'feasible': ev['feasible'],
                         'status': 'ok'})
            traces.extend({'seed': seed, 'policy': name, 'slot': i, 'fr3_active': state,
                           'ul_demand_mbps': slots[i].ul_demand_mbps,
                           'dl_demand_mbps': slots[i].dl_demand_mbps}
                          for i, state in enumerate(schedule))
    for file, data in [('results.csv', rows), ('schedules.csv', traces)]:
        with (outdir / file).open('w', encoding='utf-8', newline='') as f:
            writer = csv.DictWriter(f, fieldnames=list(data[0].keys()))
            writer.writeheader()
            writer.writerows(data)
    meta = {'data_classification': 'SYNTHETIC_ABSTRACT_SOFTWARE_SMOKE_TEST_ONLY',
            'empirical_6g_result': False,
            'not_for_publication_performance_claims': True,
            'generated_utc': datetime.now(timezone.utc).isoformat(),
            'python': platform.python_version(), 'seeds': args.seeds,
            'slots_per_seed': args.slots, 'initial_fr3_state': 'sleep',
            'energy_model': hw.__dict__, 'channel_model': 'none',
            'phy_mac_model': 'none', 'trace_source': 'synthetic_test_generator',
            'policies': list(policies(create_synthetic_trace(0, 1), hw))}
    (outdir / 'run_manifest.json').write_text(json.dumps(meta, indent=2))
    print('SYNTHETIC ABSTRACT SOFTWARE TEST — NOT 6G MEASUREMENTS')
    print(f'Created {outdir / "results.csv"}')
    print(f'Created {outdir / "schedules.csv"}')
    print(f'Created {outdir / "run_manifest.json"}')
    for row in rows[:5]:
        print(row)


if __name__ == '__main__':
    main()
