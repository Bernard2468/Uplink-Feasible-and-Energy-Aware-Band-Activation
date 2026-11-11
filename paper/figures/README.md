# Publication figures

All eight figures are generated only from recorded results, under the rules
in `docs/PHASE_3_ENERGY_PROTOCOL.md` §10.4–10.9. File stems keep the
working IDs for traceability.

| File stem | Final figure | Working ID | Built by |
|---|---|---|---|
| `fig1_network_layout_w1` | 1. Munich-anchored 19-site network layout (one panel) | 1 | `model_figures.py` |
| `fig2_interference_feasibility_w2` | 2. Directional interference and the resulting UL/DL feasibility (one panel) | 2 (c) | `model_figures.py` |
| `fig3_power_energy_w4` | 3. Base-station power consumption and hardware-overhead sensitivity | 4 | `model_figures.py` |
| `fig4_activation_w5` | 4. Adaptive activation patterns and switching behaviour | 5 | `frozen_figures.py` |
| `fig5_heldout_savings_w6` | 5. Held-out energy savings and service reliability | 6 | `frozen_figures.py` |
| `fig6_dl_only_w7` | 6. Consequences of downlink-only energy optimization | 7 | `frozen_figures.py` |
| `fig7_capacity_boundary_w3w8` | 7. Sustainable traffic capacity and network infeasibility boundary | 3 + 8 | `frozen_figures.py` |
| `fig8_comparison_robustness_w9` | 8. Controller comparison, oracle gap and hardware robustness | 9 | `frozen_figures.py` |

Each figure is a vector PDF and a 600 dpi PNG at its final IEEE width:
- Figures 1 and 2: single column, 3.5 in;
- Figures 3–8: double column, 7.16 in.

The controller is not drawn: it is presented as Algorithm 1 in the text
(§10.9).

## Figures 4–8 (frozen P3.3 / P3.4 outputs)

```
python -I scripts/figures/frozen_figures.py .
python -I scripts/figures/verify_frozen_figures.py .
```

The first command:
- checks the input hashes against the experiment manifests, before and
  after plotting;
- writes the figures, `source_data/fig{4..8}_*.csv` (every plotted number
  with its source key), `plotted_values.json` (values read back from the
  rendered artists), `caption_facts.json`, `captions.md` and
  `manifest.json`.

The second recomputes all of it independently and writes
`verification_report.json` and `verification_report.txt`.

## Figures 1–3 (Phase 2 E1c.2/E1c.3, P3.1, P3.2c, frozen P3.4 parameters)

```
python -I scripts/figures/model_figures.py .
python -I scripts/figures/verify_model_figures.py .
```

`model_figures.py` imports the style and checks of `frozen_figures.py`
unchanged. It writes the figures, `source_data/fig{1,2,3}_*.csv`,
`plotted_values_fig1-3.json`, `caption_facts_fig1-3.json`,
`captions_fig1-3.md` and `manifest_fig1-3.json`.
`verify_model_figures.py` writes `verification_report_fig1-3.json` and
`verification_report_fig1-3.txt`. It imports neither build script. It
evaluates the frozen E1c threshold function and the P3.1 power models
directly.

Run all commands from the repository root, in the project's Python
environment (matplotlib 3.11.2).
