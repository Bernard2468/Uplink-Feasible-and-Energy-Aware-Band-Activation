# Publication figures

Figures 4–8 are generated only from the frozen P3.3 / P3.4 outputs. Figures
1–3 will follow under the same rules. The rules and the record are in
`docs/PHASE_3_ENERGY_PROTOCOL.md` §10.4–10.6.

| File stem | Final figure | Working ID |
|---|---|---|
| `fig4_activation_w5` | 4. Adaptive activation patterns and switching behaviour | 5 |
| `fig5_heldout_savings_w6` | 5. Held-out energy savings and service reliability | 6 |
| `fig6_dl_only_w7` | 6. Consequences of downlink-only energy optimization | 7 |
| `fig7_capacity_boundary_w3w8` | 7. Sustainable traffic capacity and network infeasibility boundary | 3 + 8 |
| `fig8_comparison_robustness_w9` | 8. Controller comparison, oracle gap and hardware robustness | 9 |

Run both commands from the repository root, in the project's Python
environment (matplotlib 3.11.2):

```
python -I scripts/figures/frozen_figures.py .
python -I scripts/figures/verify_frozen_figures.py .
```

The first command:
- checks the input hashes against the experiment manifests, before and
  after plotting;
- writes the PDF and 600 dpi PNG files at final IEEE double-column width
  (7.16 in);
- writes `source_data/*.csv` (every plotted number with its source key),
  `plotted_values.json` (values read back from the rendered artists),
  `caption_facts.json`, `captions.md` and `manifest.json`.

The second command recomputes all of it independently and writes
`verification_report.json` and `verification_report.txt`.
