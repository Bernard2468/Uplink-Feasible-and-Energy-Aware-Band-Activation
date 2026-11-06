"""Synthetic data generator for tests ONLY. No RF or measured channel model."""
from __future__ import annotations
import random
from .control import Slot


def create_synthetic_trace(seed: int, nslots: int = 48) -> list[Slot]:
    if nslots < 1:
        raise ValueError('nslots must be positive')
    r = random.Random(seed)
    slots = []
    for t in range(nslots):
        phase = t % 24
        # Illustrative traffic phases. Caps enforce synthetic feasibility;
        # NOT a claim about measured cellular demand or throughput.
        if phase < 6:
            ul_nom, dl_nom = 5.0, 14.0
        elif phase < 12:
            ul_nom, dl_nom = 9.0, 26.0
        elif phase < 18:
            ul_nom, dl_nom = 15.5, 44.0
        else:
            ul_nom, dl_nom = 8.0, 22.0
        ul = max(0, min(19.0, ul_nom + r.uniform(-2, 2)))
        dl = max(0, min(60.0, dl_nom + r.uniform(-6, 6)))
        slots.append(Slot(ul, dl, 11.5, 30.0, 8.0, 34.0))
    return slots
