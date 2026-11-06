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
    # Triviality diagnostic: if the per-slot greedy oracle already equals the
    # multi-slot DP optimum, switching costs do not couple the slots and the
    # instance cannot discriminate between controllers.
    greedy_equals_dp = []
    for seed in range(args.seeds):
        slots = create_synthetic_trace(seed=seed, nslots=args.slots)
        ps = policies(slots, hw)
        greedy_equals_dp.append(ps['offline_clairvoyant_bound'] is not None and
                                ps['threshold_current_slot_oracle'] == ps['offline_clairvoyant_bound'])
        for name, schedule in ps.items():
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
            'online_information_timing': 'decision_t_uses_only_observation_through_t_minus_1',
            'oracle_information_timing': 'current_slot_realization_known',
            'service_model_warning': 'aggregate slot-average capacity, not packet-level QoS',
            'wake_payback_s': hw.wake_j / (hw.fr3_active_w - hw.fr3_sleep_w)
                              if hw.fr3_active_w > hw.fr3_sleep_w else None,
            'greedy_oracle_equals_dp_seeds': f'{sum(greedy_equals_dp)}/{len(greedy_equals_dp)}',
            'triviality_warning': (
                'per-slot greedy oracle equals the DP optimum on every seed: switching '
                'costs are too small relative to slot_s to couple slots; this instance '
                'cannot discriminate between controllers') if all(greedy_equals_dp) else None,
            'policies': list(ps)}
    (outdir / 'run_manifest.json').write_text(json.dumps(meta, indent=2))
    print('SYNTHETIC ABSTRACT SOFTWARE TEST — NOT 6G MEASUREMENTS')
    print(f'Created {outdir / "results.csv"}')
    print(f'Created {outdir / "schedules.csv"}')
    print(f'Created {outdir / "run_manifest.json"}')
    payback = meta['wake_payback_s']
    payback_txt = 'n/a' if payback is None else f'{payback:.2f} s'
    print(f"Greedy current-slot oracle == DP optimum on {meta['greedy_oracle_equals_dp_seeds']} seeds"
          f" (wake payback {payback_txt} vs slot {hw.slot_s:.0f} s)")
    if meta['triviality_warning']:
        print('WARNING: ' + meta['triviality_warning'])
    for row in rows[:5]:
        print(row)


if __name__ == '__main__':
    main()
