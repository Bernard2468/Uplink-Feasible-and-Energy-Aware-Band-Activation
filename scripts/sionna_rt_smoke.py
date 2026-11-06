#!/usr/bin/env python3
"""Optional real Sionna-RT smoke test. No simulation claims.

Run in the shared project environment created by env/setup_python_env.sh
(Python 3.12, sionna-rt==1.2.0 = the version tested by ns-3.48). API checked
against installed sionna-rt 1.2.0 on 2026-11-06: PathSolver() takes no
constructor arguments (the `deterministic` flag exists only in 2.x), so
reproducibility comes from the fixed `seed` passed to the solver call.
This file is not runnable in a basic Python-only environment.
"""
import sys

try:
    import sionna.rt
    from sionna.rt import load_scene, PlanarArray, Transmitter, Receiver, PathSolver
except Exception as e:
    print('Sionna RT import failed. Verify the correct Python env, backend and dependencies.')
    print(f'{type(e).__name__}: {e}')
    sys.exit(1)

scene = load_scene(sionna.rt.scene.munich)
scene.frequency = 7.2e9  # Use 7.2 GHz as an experimental FR3 center frequency.
scene.tx_array = PlanarArray(num_rows=1, num_cols=1, pattern='iso', polarization='V')
scene.rx_array = PlanarArray(num_rows=1, num_cols=1, pattern='iso', polarization='V')
scene.add(Transmitter(name='tx', position=[8.5, 21, 27]))
scene.add(Receiver(name='rx', position=[44, 95, 1.5]))
solver = PathSolver()
paths = solver(scene=scene, max_depth=2, los=True, specular_reflection=True,
               diffuse_reflection=False, refraction=True, seed=17)
print('Sionna-RT smoke test PASSED: Paths object created at 7.2 GHz.')
print('Source coordinates:', paths.sources)
print('Target coordinates:', paths.targets)
print('This is not a dual-band throughput, energy, or QoS simulation.')
