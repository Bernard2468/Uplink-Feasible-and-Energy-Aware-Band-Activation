#!/usr/bin/env python3
"""Diagnostic: is far-range NLOS caused by the gNB's own rooftop (self-shadowing)?
Casts rays FROM the gNB toward outdoor ground points and records where the first
obstruction lies (horizontal distance from the gNB)."""
import math
import sys

import mitsuba as mi
import numpy as np
import sionna.rt as rt
from sionna.rt import load_scene

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[1]))
from scripts.e1a_select_positions import ray_distance, ray_hits  # noqa: E402

scene = load_scene(rt.scene.munich, merge_shapes=True)
ms = scene.mi_scene
for tz in (27.0, 30.0, 35.0):
    tx = np.array([8.5, 21.0, tz])
    rng = np.random.default_rng(0)
    r = rng.uniform(150, 600, 20000); a = rng.uniform(0, 2 * math.pi, 20000)
    pts = np.stack([tx[0] + r * np.cos(a), tx[1] + r * np.sin(a), np.full(r.size, 1.5)], 1)
    open_ = ~ray_hits(ms, pts, [0, 0, 1], np.inf) & (np.abs(ray_distance(ms, pts, [0, 0, -1]) - 1.5) < 0.05)
    pts, r = pts[open_], r[open_]
    v = pts - tx[None, :]; d = np.linalg.norm(v, axis=1); u = v / d[:, None]
    t = ray_distance(ms, np.repeat(tx[None, :], len(pts), 0), u)
    blocked = t < d - 0.05
    hdist = t * np.hypot(u[:, 0], u[:, 1])
    for lo, hi in ((150, 200), (200, 300), (300, 450), (450, 600)):
        m = (r >= lo) & (r < hi)
        b = blocked & m
        own = b & (hdist < 15)
        print(f'tx z={tz:4.1f}  d2D {lo}-{hi} m: outdoor {m.sum():5d}  LOS {np.sum(m & ~blocked):4d} '
              f'({100*np.mean(~blocked[m]):4.1f}%)  blocked within 15 m of gNB: '
              f'{100*own.sum()/max(b.sum(),1):5.1f}% of blocked')
