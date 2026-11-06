"""Validated finite-horizon baseline control with explicit feasibility checks.

This module is a *software and optimization smoke test*. Rates/capacities are
abstract synthetic Mbps, not measured or modeled RF throughputs. On/off costs
and wake delay are illustrative assumptions, not calibrated hardware values.
"""
from __future__ import annotations
from dataclasses import dataclass
from math import isfinite
from typing import Optional

@dataclass(frozen=True)
class Slot:
    ul_demand_mbps: float
    dl_demand_mbps: float
    low_ul_capacity_mbps: float
    low_dl_capacity_mbps: float
    fr3_ul_capacity_mbps: float
    fr3_dl_capacity_mbps: float

    def __post_init__(self):
        if any((not isfinite(v) or v < 0) for v in vars(self).values()):
            raise ValueError('Slot fields must be finite, nonnegative numbers')

@dataclass(frozen=True)
class Energy:
    slot_s: float = 60.0
    base_w: float = 110.0
    fr3_active_w: float = 75.0
    fr3_sleep_w: float = 3.0
    wake_j: float = 600.0
    sleep_j: float = 0.0
    wake_delay_s: float = 3.0

    def __post_init__(self):
        if any(not isfinite(v) or v < 0 for v in vars(self).values()):
            raise ValueError('Energy fields must be finite, nonnegative numbers')
        if self.slot_s <= 0 or self.wake_delay_s >= self.slot_s:
            raise ValueError('Require slot_s>0 and 0<=wake_delay_s<slot_s')
        if self.fr3_active_w < self.fr3_sleep_w:
            raise ValueError('Active FR3 power must not be below sleep power')


def capacities(slot: Slot, previous: int, active: int, energy: Energy):
    """Two-direction aggregate *abstract* capacities for a transition."""
    if previous not in (0, 1) or active not in (0, 1):
        raise ValueError('states must be binary')
    available = 1.0 - (energy.wake_delay_s / energy.slot_s if previous == 0 and active == 1 else 0.0)
    return (slot.low_ul_capacity_mbps + active * available * slot.fr3_ul_capacity_mbps,
            slot.low_dl_capacity_mbps + active * available * slot.fr3_dl_capacity_mbps)


def feasible(slot: Slot, previous: int, active: int, energy: Energy) -> bool:
    ul, dl = capacities(slot, previous, active, energy)
    # Numeric noise only: demands and caps come from deterministic floats.
    return slot.ul_demand_mbps <= ul + 1e-9 and slot.dl_demand_mbps <= dl + 1e-9


def transition_j(previous: int, active: int, energy: Energy) -> float:
    if previous not in (0, 1) or active not in (0, 1):
        raise ValueError('states must be binary')
    watts = energy.base_w + (energy.fr3_active_w if active else energy.fr3_sleep_w)
    switching = energy.wake_j if previous == 0 and active == 1 else (energy.sleep_j if previous == 1 and active == 0 else 0.0)
    return watts * energy.slot_s + switching


def evaluate(slots: list[Slot], schedule: list[int], energy: Energy) -> dict:
    if len(slots) != len(schedule):
        raise ValueError('schedule length must equal slots length')
    previous = 0  # Starts from sleep. Initial state is part of experiment definition.
    total_j = 0.0
    violations = 0
    wakes = 0
    energies = []
    for slot, state in zip(slots, schedule):
        if state not in (0, 1):
            raise ValueError('schedule must be binary')
        if not feasible(slot, previous, state, energy):
            violations += 1
        cost = transition_j(previous, state, energy)
        total_j += cost
        energies.append(cost)
        wakes += (previous == 0 and state == 1)
        previous = state
    return {'energy_j': total_j, 'constraint_violations': violations,
            'violation_fraction': violations / len(slots) if slots else 0.0,
            'wake_events': wakes, 'feasible': violations == 0, 'slot_energy_j': energies}


def threshold_online(slots: list[Slot], energy: Energy) -> list[int]:
    """Online capacity-triggered heuristic. Does not foresee future demand."""
    prev = 0
    schedule = []
    for slot in slots:
        action = 0 if feasible(slot, prev, 0, energy) else 1
        schedule.append(action)
        prev = action
    return schedule


def hysteresis_online(slots: list[Slot], energy: Energy, off_margin: float = 0.78) -> list[int]:
    """Online conservative shut-down policy (uses only current demand)."""
    if not (0 < off_margin <= 1):
        raise ValueError('off_margin must be in (0,1]')
    prev = 0
    schedule = []
    for slot in slots:
        off_can_serve = feasible(slot, prev, 0, energy)
        if not off_can_serve:
            action = 1
        elif prev == 1 and (slot.ul_demand_mbps > off_margin * slot.low_ul_capacity_mbps or
                                slot.dl_demand_mbps > off_margin * slot.low_dl_capacity_mbps):
            action = 1
        else:
            action = 0
        schedule.append(action)
        prev = action
    return schedule


def offline_optimum(slots: list[Slot], energy: Energy) -> Optional[list[int]]:
    """Exact DP optimum for the *given entire future trace* (clairvoyant benchmark).

    Minimize total electrical + transition energy with ZERO infeasible slots.
    Important: This is an offline lower bound, not a feasible online controller.
    """
    if not slots:
        return []
    costs = {0: 0.0}  # initial state is 0 / inactive
    paths = {0: []}
    for slot in slots:
        next_costs = {}
        next_paths = {}
        for prev in costs:
            for action in (0, 1):
                if not feasible(slot, prev, action, energy):
                    continue
                cost = costs[prev] + transition_j(prev, action, energy)
                if action not in next_costs or cost < next_costs[action] - 1e-9:
                    next_costs[action] = cost
                    next_paths[action] = paths[prev] + [action]
        if not next_costs:
            return None
        costs, paths = next_costs, next_paths
    best_final = min(costs, key=costs.get)
    return paths[best_final]


def policies(slots: list[Slot], energy: Energy) -> dict[str, Optional[list[int]]]:
    return {
        'low_band_only': [0] * len(slots),
        'always_fr3_on': [1] * len(slots),
        'threshold_online': threshold_online(slots, energy),
        'hysteresis_online': hysteresis_online(slots, energy),
        'offline_clairvoyant_bound': offline_optimum(slots, energy),
    }
