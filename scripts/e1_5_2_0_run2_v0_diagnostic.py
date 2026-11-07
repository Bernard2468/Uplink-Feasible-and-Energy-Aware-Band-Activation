#!/usr/bin/env python3
"""E1.5.2-0 run 2: why V0 failed (OFDMA co-scheduling under DirectPath beams).

Reads the run-2 nrfading raw files in results/phase2/e1_5_2_0_conventions/raw/run2_failed/
(git-ignored; SHA-256 in manifest_run2_failed_V0.json) and splits the DL TBs into
'full band and alone in its slot' and the rest. For each TB, error = TB gain
(10 log10 m_sinr - SNR_iso) minus the full-band channel-level gain of the UE's own
DirectPath beam (e152_nr_direct.csv). Prints the split, the error percentiles and
the worst TBs of a_f7p2_b30 with the other TBs of their slot.
"""
import json
import math
import sys
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'scripts'))
from cmwave.radio import link_budget as lb  # noqa: E402
import e1_5_1_deterministic as e151  # noqa: E402
import e1_5_1d_tdma_diagnostic as diag  # noqa: E402

RAW = ROOT / 'results/phase2/e1_5_2_0_conventions/raw/run2_failed'
RUNS = [('a_f3p5', 3.5e9), ('a_f7p2', 7.2e9), ('b_f7p2', 7.2e9)]
SLOT_NS = 500000          # numerology 1


def tb_errors(tag, f, common):
    d = RAW / tag
    links = e151.read_csv(d / 'links.csv')
    direct = {int(r['ue_index']): float(r['gain_lin']) for r in e151.read_csv(d / 'e152_nr_direct.csv')}
    snr = lb.downlink(np.array([float(l['loss_db']) for l in links]), diag.carrier_cfg(common, f))['snr_db']
    tbs, _ = e151.unique_tbs(e151.read_csv(d / 'e15_dl_tb.csv'), e151.DL_TB_KEY)
    out = []
    for r in tbs:
        i = int(r['ue_index'])
        out.append((10 * math.log10(float(r['sinr_lin'])) - snr[i] - 10 * math.log10(direct[i]), r))
    return out


def main():
    common = json.loads((ROOT / 'configs/e1/e1_5_1_deterministic.json').read_text(encoding='utf-8'))['common']
    tot, worst_full, other = Counter(), 0.0, []
    for cfgtag, f in RUNS:
        for b in (30, 150, 270):
            errs = tb_errors(f'{cfgtag}_b{b}', f, common)
            per_slot = Counter(int(r['time_ns']) // SLOT_NS for _, r in errs)
            for e, r in errs:
                if int(r['rb_assigned']) == 51 and per_slot[int(r['time_ns']) // SLOT_NS] == 1:
                    tot['full_band_alone'] += 1
                    worst_full = max(worst_full, abs(e))
                else:
                    tot['other'] += 1
                    other.append(e)
    p = np.array(other)
    print(f'TBs {dict(tot)}; max |error| of full-band TBs alone in their slot: {worst_full:.2e} dB')
    print('other TBs: error percentiles 1 / 50 / 99 = %.2f / %.2f / %.2f dB; |error| <= 0.01 dB: %d'
          % (*np.percentile(p, [1, 50, 99]), int((np.abs(p) <= 0.01).sum())))
    errs = tb_errors('a_f7p2_b30', 7.2e9, common)
    by_slot = defaultdict(list)
    for e, r in errs:
        by_slot[int(r['time_ns']) // SLOT_NS].append(r)
    errs.sort(key=lambda x: x[0])
    print('a_f7p2_b30, most negative and most positive errors:')
    print('error_db ue rb sym mcs corrupt time_ms | other TBs in the slot (ue, rb, sym)')
    for e, r in errs[:8] + errs[-3:]:
        oth = [(o['ue_index'], o['rb_assigned'], o['num_sym']) for o in by_slot[int(r['time_ns']) // SLOT_NS]
               if o is not r]
        print(f"{e:8.2f} {r['ue_index']:>2} {r['rb_assigned']:>2} {r['num_sym']:>3} {r['mcs']:>3} {r['corrupt']:>7} "
              f"{int(r['time_ns']) / 1e6:9.4f} | {oth}")


if __name__ == '__main__':
    main()
