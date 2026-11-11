"""Independent verification of Figures 1-3 (scripts/figures/model_figures.py).

Usage:  python -I scripts/figures/verify_model_figures.py <repo root>

Imports neither the figure script nor its style module. From the experiment outputs alone it recomputes:
  - every value read back from the rendered artists (plotted_values_fig1-3.json), and the tagged set itself;
  - the row and panel of every categorical marker and number (from the rendered tick labels, axes membership and
    text positions);
  - every source-data row (source_data/fig{1,2,3}_*.csv), by resolving its source key;
  - every caption number (caption_facts_fig1-3.json) and its presence in captions_fig1-3.md;
  - the model facts the figures rely on: the placement rule of the 19 sites, that the bold hexagon is exactly the
    scored region, the F2 thresholds through the frozen E1c functions, the consistency of each F2 share with the
    recorded percentiles, the P3.1 power models evaluated densely against the drawn segments, and the S1/S2 rules;
  - input hashes against their manifests, output hashes against the figure manifest, fonts, widths and PNG size.
Writes verification_report_fig1-3.json / .txt; exit status 1 if any check fails.
"""
import csv
import hashlib
import json
import math
import re
import struct
import subprocess
import sys
from pathlib import Path

import numpy as np

ROOT = Path(sys.argv[1]).resolve()
FIG = ROOT / 'paper' / 'figures'
SUF = 'fig1-3'
CHECKS = []


def check(name, ok, detail=''):
    CHECKS.append({'check': name, 'passed': bool(ok), 'detail': str(detail)[:600]})


def close(a, b, tol=1e-9):
    return abs(a - b) <= tol * max(1.0, abs(a), abs(b))


sha = lambda p: hashlib.sha256(Path(p).read_bytes()).hexdigest()  # noqa: E731
jl = lambda p: json.loads(Path(p).read_text(encoding='utf-8'))  # noqa: E731
P2, P3 = ROOT / 'results/phase2', ROOT / 'results/phase3'
PV = jl(FIG / f'plotted_values_{SUF}.json')
MAN = jl(FIG / f'manifest_{SUF}.json')
F = jl(FIG / f'caption_facts_{SUF}.json')
CAP = (FIG / f'captions_{SUF}.md').read_text(encoding='utf-8')
FIDS = ('fig1_network_layout_w1', 'fig2_interference_feasibility_w2', 'fig3_power_energy_w4')
WIDTH = {'fig1_network_layout_w1': 3.5, 'fig2_interference_feasibility_w2': 3.5, 'fig3_power_energy_w4': 7.16}
check('the three figures, and only they, are in the figure files', set(PV) == set(FIDS) == set(MAN['width_in']))

# ------------------------------------------------------------------ inputs
m_c3 = jl(P2 / 'e1c3_directional/manifest.json')
anchors = {
    'results/phase2/e1c3_directional/summary.json': m_c3['outputs_sha256']['summary.json'],
    'results/phase2/e1c3_directional/raw/counts_isd200.npz': m_c3['outputs_sha256']['raw/counts_isd200.npz'],
    'results/phase2/e1c2_geometry/e1c2_sites_isd200.npz': m_c3['input_sha256']['e1c2_geometry/e1c2_sites_isd200.npz'],
    'results/phase2/e1_geometry/site_tx0_mask.npz': m_c3['input_sha256']['e1_geometry/site_tx0_mask.npz'],
    'results/phase2/e1c_geometry/site_tx0_indoor.npz': m_c3['input_sha256']['e1c_geometry/site_tx0_indoor.npz'],
    'results/phase2/e1_5_4_multiuser/manifest.json': m_c3['input_sha256']['e1_5_4_multiuser/manifest.json'],
    'configs/e1/e1_4_antennas_o2i.json': jl(P2 / 'e1_4_antennas_o2i/manifest.json')['config_sha256'],
    'results/phase3/p3_1_energy_models/summary.json': jl(P3 / 'p3_1_energy_models/manifest.json')['outputs_sha256']['summary.json'],
    'results/phase3/p3_1_energy_models/manifest.json':
        jl(P3 / 'p3_2c_energy_ledger/manifest.json')['input_sha256']['p3_1_energy_models/manifest.json'],
    'results/phase3/p3_2c_energy_ledger/summary.json':
        jl(P3 / 'p3_2c_energy_ledger/manifest.json')['outputs_sha256']['summary.json'],
    'configs/p3/p3_3_controllers.json': jl(P3 / 'p3_3_controllers/manifest.json')['config_sha256'],
    'configs/p3/p3_4_heldout.json': jl(P3 / 'p3_4_heldout/manifest.json')['config_sha256'],
    'results/phase2/e1c2_interference/summary.json':
        jl(P2 / 'e1c2_interference/manifest.json')['outputs_sha256']['summary.json'],
}
for rel, h in anchors.items():
    check(f'input matches its recording manifest: {rel}', sha(ROOT / rel) == h)
git = lambda *a: subprocess.run(['git', '-C', str(ROOT), *a], capture_output=True, text=True).stdout.strip()  # noqa
s154_rel = 'results/phase2/e1_5_4_multiuser/summary.json'
check('E1.5.4 summary equals its committed blob (no manifest hashes it)',
      git('hash-object', s154_rel) == git('rev-parse', f'HEAD:{s154_rel}') != '')
rec_in = MAN['input_sha256']
check('figure manifest input hashes equal the files now',
      all(sha(ROOT / k) == v for k, v in rec_in.items() if not k.startswith('git_blob:')))
check('figure manifest records every anchored input',
      set(anchors) - {'results/phase2/e1c2_interference/summary.json'} <= set(rec_in))

C3 = jl(P2 / 'e1c3_directional/summary.json')
C2 = jl(P2 / 'e1c2_interference/summary.json')
S31 = jl(P3 / 'p3_1_energy_models/summary.json')
S32 = jl(P3 / 'p3_2c_energy_ledger/summary.json')
C14 = jl(ROOT / 'configs/e1/e1_4_antennas_o2i.json')
TH = jl(ROOT / s154_rel)['analytic_thresholds_db']['F2']
z = np.load(P2 / 'e1c2_geometry/e1c2_sites_isd200.npz')
SX, SY, SH, KIND = z['site_x'], z['site_y'], z['site_h'], [str(k) for k in z['site_kind']]
SI, SJ, RING, IEXCL = z['site_i'], z['site_j'], z['site_ring'], z['indoor_excluded']
z = np.load(P2 / 'e1_geometry/site_tx0_mask.npz')
OX, OY, GNB = z['x'], z['y'], z['gnb']
z = np.load(P2 / 'e1c_geometry/site_tx0_indoor.npz')
IX, IY = z['x'], z['y']
z = np.load(P2 / 'e1c3_directional/raw/counts_isd200.npz')
VO, VI = z['victim_out_index'], z['victim_ind_index']

# ------------------------------------------------------------------ expected artists
EXP = {f: {} for f in FIDS}
ISD = 200.0
LAT = lambda i, j: (GNB[0] + i * ISD + 0.5 * j * ISD, GNB[1] + j * ISD * math.sqrt(3) / 2)  # noqa: E731
R_HEX = ISD / math.sqrt(3)

# Figure 1: the network layout
f1 = EXP['fig1_network_layout_w1']
for i, j in zip(SI, SJ):
    cx, cy = LAT(int(i), int(j))
    ang = [math.radians(30 + 60 * k) for k in range(7)]
    f1[f'f1|hex|{int(i)},{int(j)}'] = {'x': [cx + R_HEX * math.cos(a) for a in ang],
                                       'y': [cy + R_HEX * math.sin(a) for a in ang]}
for kd in ('rooftop', 'mast', 'site0'):
    idx = [n for n, k in enumerate(KIND) if k == kd]
    f1[f'f1|sites|{kd}'] = {'x': [float(SX[n]) for n in idx], 'y': [float(SY[n]) for n in idx]}
BEAR = C14['cases']['A_equal_elements']['sector_bearings_deg']
for s in range(len(BEAR)):
    f1[f'f1|seclabel|{s}'] = {'s': str(s + 1)}
f1['f1|sectors'] = {'n': len(SX) * len(BEAR)}
f1['f1|scalebar'] = {'len': 200.0}
NS, NB = len(SX), len(BEAR)

# Figure 2: case A, 7.2 GHz, four interference models
f2 = EXP['fig2_interference_feasibility_w2']
MOD = ['noise_multi', 'primary', 'sd', 'geo']
MOD_TICK = {'noise_multi': 'No interference', 'primary': 'Beam-averaged', 'sd': 'Directional', 'geo': 'Pure pattern'}
DIRS = {'DL': ('DL_7.2GHz', 'DL, 7.2 GHz', 'DL'), 'UL_F2': ('UL_7.2GHz', 'UL, 7.2 GHz', 'UL')}
st = lambda sn: C3['isd']['200']['settings'][sn]  # noqa: E731
share = lambda sn, lk: st(sn)['A_equal_elements']['mixes']['M2412']['F2'][lk]  # noqa: E731
quant = lambda sn, mt: st(sn)['A_equal_elements']['quantiles_db_1_5_50_95']['M2412']['7.2GHz'][mt]  # noqa: E731
for mt, (lk, _, thk) in DIRS.items():
    for sn in MOD:
        q = quant(sn, mt)
        f2[f'f2|{mt}|{sn}|range'] = {'x': [q[0], q[3]], 'row': (mt, sn)}
        f2[f'f2|{mt}|{sn}|ends'] = {'x': [q[0], q[3]], 'row': (mt, sn)}
        f2[f'f2|{mt}|{sn}|p5'] = {'x': [q[1]], 'row': (mt, sn), 'hollow': True}
        f2[f'f2|{mt}|{sn}|p50'] = {'x': [q[2]], 'row': (mt, sn), 'hollow': False}
        f2[f'f2|{mt}|{sn}|share'] = {'s': f'{share(sn, lk):.3f}'}
    f2[f'f2|{mt}|threshold'] = {'x': [TH[thk]] * 2}
f2['f2|share_header'] = {'s': 'F2 share'}
a14 = C14['cases']['A_equal_elements']['carriers']
check('caption: case A = 8x16 arrays at both carriers, 20 MHz (E1.4 config)',
      len(a14) == 2 and all(cc['gnb_panel'] == {'rows': 8, 'cols': 16} and cc['channel_bw_hz'] == 20e6 for cc in a14))

# Figure 3
f3 = EXP['fig3_power_energy_w4']
HW = '50dBm_SiGe_SiGe_MRF256'
LOADS = sorted(S31['layer_3p5']['P_w_by_case_and_load']['right'], key=float)
p35 = S31['layer_3p5']['P_w_by_case_and_load']
pt = S31['layer_7p2']['points'][HW]
paux = S32['constants']['hardware'][HW]['P_aux_w']
SCN_COL = {'S0': '#6da7ec', 'S1': '#2a78d6', 'S2': '#104281'}
f3['f3a|35|right'] = {'x': [float(k) for k in LOADS], 'y': [p35['right'][k] for k in LOADS], 'color': '#0b0b0b'}
f3['f3a|35|left'] = {'x': [float(k) for k in LOADS], 'y': [p35['left'][k] for k in LOADS], 'color': '#0b0b0b'}
for S in ('S0', 'S1', 'S2'):
    f3[f'f3a|72|{S}'] = {'x': [float(k) for k in LOADS], 'y': [pt['P_w'][f'{k}_{k}'] + paux[S] for k in LOADS],
                         'color': SCN_COL[S]}
f3['f3a|s2_note'] = {'s': '7.2 GHz S2 almost coincides with\n3.5 GHz, idle modes (equal at $L$ = 0)'}
ROWS = {}
for row in S32['energy_rows_hw_primary_settings']:
    if (row['mix'], row['dl_offset_db'], row['hw'], row['case35'], row['category'], row['set'], row['wake'],
            row['smtc']) == ('ericsson_typical', -3.0, HW, 'right', 'cat1', 'set1', 'proactive', 'primary'):
        ROWS.setdefault((row['policy'], row['scenario'], float(row['g'])), []).append(row)
check('P3.2c: one energy row per (policy, scenario, g) in the primary setting', all(len(v) == 1 for v in ROWS.values()))
ROWS = {k: v[0] for k, v in ROWS.items()}
GG = sorted({k[2] for k in ROWS if k[0] == 'aon_ulsel'})
SER = [('35only', 'S0', '#52514e'), ('aon_ulsel', 'S0', SCN_COL['S0']), ('aon_ulsel', 'S1', SCN_COL['S1']),
       ('aon_ulsel', 'S2', SCN_COL['S2'])]
for pol, S, col in SER:
    E = [ROWS[(pol, S, g)]['E_site_kWh'] for g in GG]
    fe = [ROWS[(pol, S, g)]['feasible']['all_user'] for g in GG]
    f3[f'f3b|{pol}|{S}'] = {'x': GG, 'y': [e['mean'] for e in E], 'color': col}
    f3[f'f3b|{pol}|{S}|feasible'] = {'x': [g for g, q in zip(GG, fe) if q],
                                     'y': [e['mean'] for e, q in zip(E, fe) if q], 'hollow': False, 'color': col}
    f3[f'f3b|{pol}|{S}|infeasible'] = {'x': [g for g, q in zip(GG, fe) if not q],
                                       'y': [e['mean'] for e, q in zip(E, fe) if not q], 'hollow': True, 'color': col}
    f3[f'f3b|{pol}|{S}|ci'] = {'segs': [[[g, e['lo']], [g, e['hi']]] for g, e in zip(GG, E)]}
GS = {p: S32['g_star'][f'{p}|ericsson_typical|-3.0|all_user'] for p in ('35only', 'aon_ulsel')}
for p in GS:
    f3[f'f3b|gstar|{p}'] = {'x': [GS[p], GS[p]]}
    f3[f'f3b|gstar_label|{p}'] = {'contains': f'{GS[p]:.2f}'}

# ------------------------------------------------------------------ compare artists
nvals = 0
for fid in FIDS:
    pv = {k: v for k, v in PV[fid].items() if k != '_axes'}
    check(f'{fid}: tagged artists are exactly the expected set', set(pv) == set(EXP[fid]),
          f'missing {sorted(set(EXP[fid]) - set(pv))[:5]}, extra {sorted(set(pv) - set(EXP[fid]))[:5]}')
    for gid, e in EXP[fid].items():
        a = pv.get(gid)
        if a is None:
            continue
        ok, det = True, ''
        if 's' in e:
            ok = a.get('s') == e['s']
            det = f'{a.get("s")!r} != {e["s"]!r}'
            nvals += 1
        elif 'contains' in e:
            ok = e['contains'] in a.get('s', '')
        elif gid == 'f1|sectors':
            segs = a['segs']
            exp_start = [(float(x), float(y)) for x, y in zip(SX, SY) for _ in BEAR]
            exp_ang = [float(b) for _ in SX for b in BEAR]
            lens = [math.hypot(s_[1][0] - s_[0][0], s_[1][1] - s_[0][1]) for s_ in segs]
            ang = [math.degrees(math.atan2(s_[1][1] - s_[0][1], s_[1][0] - s_[0][0])) % 360 for s_ in segs]
            ok = (len(segs) == e['n'] and all(close(s_[0][0], p[0]) and close(s_[0][1], p[1]) for s_, p in
                                               zip(segs, exp_start))
                  and all(abs(((x - y + 180) % 360) - 180) < 1e-9 for x, y in zip(ang, exp_ang))
                  and max(lens) - min(lens) < 1e-9)
            nvals += 4 * len(segs)
        elif gid == 'f1|scalebar':
            ok = close(a['x'][1] - a['x'][0], 200.0) and a['y'][0] == a['y'][1]
        elif 'segs' in e:
            ok = len(a['segs']) == len(e['segs']) and all(close(p[0], q[0]) and close(p[1], q[1]) for s1, s2 in
                                                           zip(a['segs'], e['segs']) for p, q in zip(s1, s2))
            nvals += 4 * len(e['segs'])
        else:
            ok = len(a['x']) == len(e['x']) and all(close(x, y) for x, y in zip(a['x'], e['x']))
            if 'y' in e:
                ok = ok and len(a['y']) == len(e['y']) and all(close(x, y) for x, y in zip(a['y'], e['y']))
            if 'color' in e:
                ok = ok and a['color'] == e['color']
            if 'hollow' in e:
                ok = ok and (a['mfc'] == '#ffffff') == e['hollow']
            nvals += len(e['x']) + len(e.get('y', []))
            det = f"{a.get('x')} {a.get('y')} vs {e['x']} {e.get('y')}"
        check(f'{fid}: {gid}', ok, det)

# rows and panels of Figure 2, from the rendered ticks, axes membership and text positions
ax2 = PV['fig2_interference_feasibility_w2']['_axes']
check('fig2: exactly two panels, DL above UL (no further panels)', set(ax2) == {'DL', 'UL_F2'})
for mt, (lk, lab, _) in DIRS.items():
    a = ax2[mt]
    tick = dict(zip(a['yticklabels'], a['yticks']))
    ok = (a['ylabel'] == lab and set(tick) == set(MOD_TICK.values())
          and {g for g in a['gids']} == {g for g in EXP['fig2_interference_feasibility_w2'] if g.startswith(f'f2|{mt}|')}
          | ({'f2|share_header'} if mt == 'DL' else set()))
    for sn in MOD:
        for part in ('range', 'ends', 'p5', 'p50'):
            ys = PV['fig2_interference_feasibility_w2'][f'f2|{mt}|{sn}|{part}']['y']
            ok = ok and all(y == tick[MOD_TICK[sn]] for y in ys)
        x_, y_ = a['text_pos'][f'f2|{mt}|{sn}|share']
        ok = ok and y_ == tick[MOD_TICK[sn]] and x_ > 1.0
    check(f'fig2 {lab}: every percentile marker and F2 share sits on its model\'s labelled row, shares right of the '
          'plot', ok)
# every artist of Figures 1 and 3 lies in its own panel
ax1 = PV['fig1_network_layout_w1']['_axes']
check('fig1: one panel holding every tagged artist', set(ax1) == {'map'} and set(ax1['map']['gids']) == set(EXP['fig1_network_layout_w1']))
ax3 = PV['fig3_power_energy_w4']['_axes']
check('fig3: every tagged artist lies in its own panel (a, b)',
      all(all(g.startswith(f'f3{n}|') for g in ax3[n]['gids']) for n in ('a', 'b'))
      and sum(len(ax3[n]['gids']) for n in ('a', 'b')) == len(EXP['fig3_power_energy_w4']))

# ------------------------------------------------------------------ model facts behind the figures
dev = [math.hypot(SX[n] - LAT(int(SI[n]), int(SJ[n]))[0], SY[n] - LAT(int(SI[n]), int(SJ[n]))[1]) for n in range(NS)]
check('site 0 is the lattice origin and the E1.1 gNB (8.5, 21, 27 m)',
      KIND[0] == 'site0' and dev[0] == 0.0 and float(SH[0]) == float(GNB[2]) == 27.0)
check('every rooftop site within 0.2 ISD of its lattice point, masts on it',
      all(dev[n] <= 0.2 * ISD + 1e-9 for n in range(NS) if KIND[n] == 'rooftop')
      and all(dev[n] == 0.0 for n in range(NS) if KIND[n] == 'mast'), f'max {max(dev):.2f} m')
check('rings 0-2 with 1 + 6 + 12 sites', sorted(np.bincount(RING).tolist()) == [1, 6, 12])
check('every non-site-0 antenna at 25 m', all(float(SH[n]) == 25.0 for n in range(NS) if KIND[n] != 'site0'))
check('sector bearings identical in E1.4 cases A, B, C (the cases E1c.2/E1c.3 use)',
      all(C14['cases'][c]['sector_bearings_deg'] == BEAR == [30.0, 150.0, 270.0]
          for c in ('A_equal_elements', 'B_equal_aperture', 'C_deployment')))
hx, hy = PV['fig1_network_layout_w1']['f1|hex|0,0']['x'], PV['fig1_network_layout_w1']['f1|hex|0,0']['y']
nb = [LAT(*d) for d in ((1, 0), (0, 1), (-1, 1), (-1, 0), (0, -1), (1, -1))]
o = LAT(0, 0)
vor = all(sum(abs(math.hypot(x - n_[0], y - n_[1]) - math.hypot(x - o[0], y - o[1])) < 1e-9 for n_ in nb) == 2
          for x, y in zip(hx[:-1], hy[:-1]))
check('bold hexagon: each vertex equidistant from site 0 and exactly two neighbours (the Voronoi cell)', vor)
lat = [(i, j) for i in range(-4, 5) for j in range(-4, 5) if max(abs(i), abs(j), abs(i + j)) <= 4]


def nearest_is_site0(X, Y):
    d0 = np.hypot(X - o[0], Y - o[1])
    dn = np.full(len(X), np.inf)
    for i, j in lat:
        if (i, j) != (0, 0):
            c_ = LAT(i, j)
            dn = np.minimum(dn, np.hypot(X - c_[0], Y - c_[1]))
    return d0 <= dn + 1e-9


vo = np.flatnonzero(nearest_is_site0(OX, OY))
vi = np.flatnonzero(nearest_is_site0(IX, IY) & ~IEXCL)
check('scored users = the sampled points nearest to site 0 (ties to site 0), as recorded by E1c.3',
      np.array_equal(vo, np.sort(VO)) and np.array_equal(vi, np.sort(VI)), f'{len(vo)}/{len(VO)}, {len(vi)}/{len(VI)}')
apoth = ISD / 2
inside = lambda X, Y: np.all([np.abs((X - o[0]) * math.cos(math.radians(a)) + (Y - o[1]) * math.sin(math.radians(a)))
                              <= apoth + 1e-9 for a in (0, 60, 120)], axis=0)  # noqa: E731
check('every scored user inside or on the bold hexagon', inside(OX[VO], OY[VO]).all() and inside(IX[VI], IY[VI]).all())
bc = MAN['build_checks']['fig1_victims_inside_bold_cell']
check('build check agrees: every victim inside or on the cell, no non-victim strictly inside',
      bc['passed'] and bc['outdoor']['victims'] == len(VO) and bc['indoor']['victims'] == len(VI))
x0, y0 = float(min(OX.min(), IX.min())), float(min(OY.min(), IY.min()))
nx = int(round((max(OX.max(), IX.max()) - x0) / 4.0)) + 1
ny = int(round((max(OY.max(), IY.max()) - y0) / 4.0)) + 1
Cr = np.zeros((ny, nx), dtype=np.int8)
Cr[np.rint((OY - y0) / 4).astype(int), np.rint((OX - x0) / 4).astype(int)] = 1
Cr[np.rint((IY - y0) / 4).astype(int), np.rint((IX - x0) / 4).astype(int)] = 2
rb = MAN['build_checks']['fig1_raster']
h_ = hashlib.sha256(Cr.tobytes()).hexdigest()
check('map raster recomputed from the E1.1 mask and E1c indoor samples equals the drawn image',
      h_ == rb['sha256'] == rb['artist_array_sha256'], h_)
e11 = jl(P2 / 'e1_geometry/site_tx0_manifest.json')
check('map outdoor cells = E1.1 open outdoor points (30,888); scene = munich',
      int((Cr == 1).sum()) == e11['counts']['open_outdoor_points'] == rb['counts']['outdoor'] and e11['scene'] == 'munich')
# F2 thresholds through the frozen E1c functions
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'scripts'))
import e1c_area_maps as e1c  # noqa: E402
import e1_5_5_reclassification as e155  # noqa: E402
maps = e155.build_mappings(jl(e155.E153A_DIR / 'summary.json'), jl(e155.E153_DIR / 'summary.json'))
se2, _, _ = e1c.mappings_for(e1c.VARIANTS['primary'], {'regime': 'A'}, maps, None, None)
thr = {d: e1c.snr_threshold(se2, d, e1c.F2T[d]) for d in ('DL', 'UL')}
check('F2 thresholds: E1.5.4 record = frozen E1c thresholds() (within 1e-9 dB)',
      all(abs(thr[d] - TH[d]) < 1e-9 for d in thr), thr)
# each plotted F2 share is consistent with the recorded percentiles and the threshold (same population)
cons = []
for mt, (lk, _, thk) in DIRS.items():
    for sn in MOD:
        out = 1.0 - share(sn, lk)
        for pq, v in zip((0.01, 0.05, 0.50, 0.95), quant(sn, mt)):
            if v > TH[thk] + 0.05 and out > pq + 1e-3:
                cons.append(f'{mt} {sn}: P{pq} {v:.2f} > th but outage {out:.4f}')
            if v < TH[thk] - 0.05 and out < pq - 1e-3:
                cons.append(f'{mt} {sn}: P{pq} {v:.2f} < th but outage {out:.4f}')
check('every F2 share is consistent with its recorded percentiles relative to the threshold', not cons, cons[:3])
for lk in ('DL_7.2GHz', 'UL_7.2GHz'):
    check(f'E1c.3 averaged model reproduces E1c.2 exactly: {lk}',
          share('primary', lk) == C2['isd']['200']['settings']['primary']['A_equal_elements']['mixes']['M2412']['F2'][lk])
# P3.1 power models: affinity and the drawn segments against the frozen functions
lin = lambda xs, ys: max(abs(y - (ys[0] + (ys[-1] - ys[0]) * (x - xs[0]) / (xs[-1] - xs[0]))) for x, y in zip(xs, ys))  # noqa
Lx = [float(k) for k in LOADS]
check('P3.1 3.5 GHz powers affine in L (both cases; max deviation < 1e-9 W)',
      max(lin(Lx, [p35[c][k] for k in LOADS]) for c in ('right', 'left')) < 1e-9)
dev72 = lin(Lx, [pt['P_w'][f'{k}_{k}'] for k in LOADS])
cap3 = CAP.split('## fig3_power_energy_w4')[1]
check('P3.1 7.2 GHz powers are not exactly affine (off a straight line by > 1e-9 W), and the caption says so: '
      'no "both models are affine", the 1 mW bound stated', dev72 > 1e-9 and 'both models are affine' not in cap3
      and 'the 7.2 GHz model departs from the segments by less than 1 mW' in cap3, f'{dev72 * 1000:.3f} mW')
import p3_2c_energy_ledger as LED  # noqa: E402
conf1_path = ROOT / 'configs/p3/p3_1_energy_models.json'
check('P3.1 config matches the P3.1 manifest', sha(conf1_path) == jl(P3 / 'p3_1_energy_models/manifest.json')['config_sha256'])
en = LED.Energy(jl(conf1_path))
grid = np.linspace(0.0, 1.0, 10001)
chord = {}
for nm, fn, ys in (('3.5 right', lambda L: en.p35('right', L), [p35['right'][k] for k in LOADS]),
                   ('3.5 left', lambda L: en.p35('left', L), [p35['left'][k] for k in LOADS]),
                   ('7.2', lambda L: en.p72(HW, L, L), [pt['P_w'][f'{k}_{k}'] for k in LOADS])):
    model = np.array([fn(float(L)) for L in grid])
    chord[nm] = float(np.max(np.abs(model - np.interp(grid, Lx, ys))))
    check(f'frozen model reproduces the recorded P3.1 points: {nm}', all(close(fn(x), y, 1e-12) for x, y in zip(Lx, ys)))
check('drawn segments within 1 mW of the frozen power models at every occupancy (10,001-point grid)',
      max(chord.values()) < 1e-3, {k: f'{v * 1000:.4f} mW' for k, v in chord.items()})
check('3.5 GHz drawn segments are exact (model affine, < 1e-9 W)', chord['3.5 right'] < 1e-9 and chord['3.5 left'] < 1e-9)
check('P3.1 7.2 GHz model separable: P(1,1) = P(1,0) + P(0,1) - P(0,0)',
      close(pt['P_w']['1.0_1.0'], pt['P_w']['1.0_0.0'] + pt['P_w']['0.0_1.0'] - pt['P_w']['0.0_0.0'], 1e-12))
n_pa = pt['P_T_w'] / pt['P_a_w']
p_max = 10 ** (S31['d10']['sige_max_total_dbm']['1024'] / 10) / 1000 / 1024
s1 = 17.0 * (n_pa * p_max) ** 0.25 / 0.81 + 90.0 / 0.855
check('S1 overhead = 17 W (N_PA P_max)^0.25 / 0.81 + 90 W / 0.855 (D25)', close(paux['S1'], s1, 1e-9), f'{s1} vs {paux["S1"]}')
check('S2 overhead = P_3.5(0) - P_empty,7.2 (parity, D25)', close(paux['S2'], p35['right']['0.0'] - pt['P_empty_w'], 1e-12))
check('S0 overhead is zero', paux['S0'] == 0.0)
check('P3.2c P_empty = P3.1 P_empty and P(0, 0)', S32['constants']['hardware'][HW]['P_empty_w'] == pt['P_empty_w']
      == pt['P_w']['0.0_0.0'])
ds = S32['constants']['hardware'][HW]['deep_sleep']
pmu = S32['constants']['hardware'][HW]['P_mu_w']
check('sleep powers scale with the overhead: deep(S) = (P_mu + P_aux) deep(S0) / P_mu',
      all(close(ds[f'{S}|cat1|set1']['p_w'], (pmu + paux[S]) * ds['S0|cat1|set1']['p_w'] / pmu, 1e-9) for S in paux))

# ------------------------------------------------------------------ caption facts
Eg = lambda p, S, g: ROWS[(p, S, g)]['E_site_kWh']['mean']  # noqa: E731
qd = lambda sn, i: quant(sn, 'DL')[i]  # noqa: E731
less = [g for g in GG if Eg('aon_ulsel', 'S0', g) < Eg('35only', 'S0', g)]
more = [g for g in GG if g > less[-1]]
check('always-on cheaper than 3.5 GHz only (S0) on one contiguous range of g, dearer at every g above it',
      less == [g for g in GG if less[0] <= g <= less[-1]] and all(Eg('aon_ulsel', 'S0', g) > Eg('35only', 'S0', g)
                                                                    for g in more))
check('3.5 GHz only fails the all-user criterion at every g above its g* and meets it below',
      all(ROWS[('35only', 'S0', g)]['feasible']['all_user'] == (g <= GS['35only']) for g in GG))
check('always on fails the all-user criterion exactly above its g*',
      all(ROWS[('aon_ulsel', S, g)]['feasible']['all_user'] == (g <= GS['aon_ulsel']) for g in GG for S in paux))
inc = {S: [Eg('aon_ulsel', S, g) - Eg('aon_ulsel', 'S0', g) for g in GG] for S in ('S1', 'S2')}
check('always-on S1/S2 increments constant over g and equal to 3 cells x P_aux x 24 h',
      all(max(v) - min(v) < 1e-6 and close(v[0], 3 * paux[S] * 24 / 1000, 1e-6) for S, v in inc.items()),
      {S: (min(v), max(v)) for S, v in inc.items()})
ci = max(max(r['E_site_kWh']['mean'] - r['E_site_kWh']['lo'], r['E_site_kWh']['hi'] - r['E_site_kWh']['mean'])
         for (p, S, g), r in ROWS.items() if (p, S) in {(a, b) for a, b, _ in SER})
se_max = max(st(sn)['A_equal_elements']['mixes_mc_se']['M2412']['F2'][lk] for sn in MOD for lk, _, _ in DIRS.values())
expect = {
    'f1_n_rooftop': str(KIND.count('rooftop')), 'f1_n_mast': str(KIND.count('mast')), 'f1_site0_h': f'{float(SH[0]):g}',
    'f1_other_h': '25', 'f1_bearings': ', '.join(f'{b:g}°' for b in BEAR),
    'f1_victims_out': f'{len(VO):,}', 'f1_victims_ind': f'{len(VI):,}',
    'f2_th_dl': f"{TH['DL']:.2f}", 'f2_th_ul': f"{TH['UL']:.2f}",
    'f2_a_dl72': '{} (averaged) to {} (directional) and {} (pure pattern)'.format(
        *(f"{share(sn, 'DL_7.2GHz'):.3f}" for sn in ('primary', 'sd', 'geo'))),
    'f2_p1_dl': '{}, {} and {}'.format(*(f'{qd(sn, 0):.1f}' for sn in ('primary', 'sd', 'geo'))),
    'f2_p50_dl': '{}, {} and {}'.format(*(f'{qd(sn, 2):.1f}' for sn in ('primary', 'sd', 'geo'))),
    'f2_ul72_max': f"{100 * max(abs(share(sn, 'UL_7.2GHz') - share('primary', 'UL_7.2GHz')) for sn in ('sd', 'geo')):.1f}",
    'f2_se_max': f'{se_max:.4f}',
    'f2_victims': f'{len(VO):,} outdoor points and {len(VI):,} indoor samples', 'f2_nreal': str(C3['n_realizations']),
    'f3_p35_0': f"{p35['right']['0.0']:.1f}", 'f3_p35_0_left': f"{p35['left']['0.0']:.1f}",
    'f3_p72_0': f"{pt['P_empty_w']:.1f}", 'f3_s1': f"{paux['S1']:.1f}", 'f3_s2': f"{paux['S2']:.1f}",
    'f3_ul_term': f"{pt['P_w']['0.0_1.0'] - pt['P_w']['0.0_0.0']:.1f}",
    'f3_s2_gap': f"{max(abs(pt['P_w'][f'{k}_{k}'] + paux['S2'] - p35['right'][k]) for k in LOADS):.1f}",
    'f3_deep': ', '.join(f"{ds[f'{S}|cat1|set1']['p_w']:.1f}" for S in ('S0', 'S1', 'S2')),
    'f3_d_g1': f"{100 * (Eg('aon_ulsel', 'S0', 1.0) / Eg('35only', 'S0', 1.0) - 1):.1f}",
    'f3_less': f'{less[0]:g}–{less[-1]:g}', 'f3_more_from': f'{more[0]:g}',
    'f3_35_flat': f"{Eg('35only', 'S0', more[0]):.2f}",
    'f3_add': f"{inc['S1'][0]:.2f} and {inc['S2'][0]:.2f}",
    'f3_d35': ' and '.join(f"{max(Eg('35only', S, g) - Eg('35only', 'S0', g) for g in GG):.2f}" for S in ('S1', 'S2')),
    'f3_g35': f"{GS['35only']:.2f}", 'f3_gaon': f"{GS['aon_ulsel']:.2f}", 'f3_ci': f'{ci:.2f}',
    'f3_g_range': f'{GG[0]:g}–{GG[-1]:g}', 'f3_x30': f"{S32['calibration']['x_medium_over_busy']:.3f}",
    'f3_nreal': str(S32['n_realizations']),
}
fb = [f'{k}: {F.get(k)!r} != {v!r}' for k, v in expect.items() if F.get(k) != v]
check('caption facts equal the independent recomputation', not fb and set(F) == set(expect), '; '.join(fb))
check('every caption fact appears in captions_fig1-3.md, no unfilled placeholder',
      all(v in CAP for v in F.values()) and not re.search(r'\{[a-z0-9_]+\}', CAP))
check('captions of Figures 1 and 2 have no panel labels (single-panel figures)',
      all(not re.search(r'\([ab]\)', CAP.split(f'## {fid}')[1].split('## ')[0]) for fid in FIDS[:2]))
flat_after = {round(Eg('35only', 'S0', g), 6) for g in more}
check('3.5-GHz-only energy constant above the cheaper range (caption "stays at")', len(flat_after) == 1, flat_after)
mixes = json.loads((ROOT / 'configs/e1/e1c2_interference.json').read_text())
consts = {
    'M.2412 mix 20% in car, 80% indoor': 'in car 0.2, indoor low 0.64, indoor high 0.16' in json.dumps(mixes),
    'E1c.3: 100 realizations': C3['n_realizations'] == 100,
    'F2 = 2.25 / 1.5 bit/s/Hz at 10 users per cell (M.2410 0.225 / 0.15 per user)':
        e1c.F2T == {'DL': 2.25, 'UL': 1.5} and e1c.K_USERS == 10,
    'M_RF = 256 and 1024 PAs at 50 dBm': pt['M_RF'] == 256 and n_pa == 1024 and HW.startswith('50dBm'),
    'ETSI day: medium level g x_30 with x_30 from P3.2c': 0.6 < S32['calibration']['x_medium_over_busy'] < 0.7,
}
for k, ok in consts.items():
    check(f'caption constant: {k}', ok)

# ------------------------------------------------------------------ source data rows
num = lambda s: float(s) if s not in ('', None) else None  # noqa: E731
nrows = 0
for fid in FIDS:
    with open(FIG / 'source_data' / f'{fid}.csv', encoding='utf-8', newline='') as fh:
        rows = list(csv.DictReader(fh))
    bad = []
    for row in rows:
        src, val = row['source'], row['value']
        ok = None
        mm = re.match(r'^E1c\.2 e1c2_sites_isd200\.npz site_x/site_y/site_h/site_kind\[(\d+)\]$', src)
        if mm:
            n = int(mm.group(1))
            ok = (val == f'{float(SX[n])!r},{float(SY[n])!r}; {float(SH[n])!r}'
                  and f'({KIND[n]}, ring {int(RING[n])})' in row['series'])
        mm = re.match(r'^E1c\.2 lattice: .* i=(-?\d+), j=(-?\d+)$', src)
        if mm:
            cx, cy = LAT(int(mm.group(1)), int(mm.group(2)))
            x_, y_ = (float(t) for t in val.split(','))
            ok = close(x_, cx) and close(y_, cy)
        mm = re.match(r'^configs/e1/e1_4_antennas_o2i\.json cases\.A_equal_elements\.sector_bearings_deg\[(\d)\]$', src)
        if mm:
            ok = num(val) == BEAR[int(mm.group(1))]
        if src.startswith('E1.1 site_tx0_mask.npz'):
            ok = val == f"{int((Cr == 0).sum())}/{int((Cr == 1).sum())}/{int((Cr == 2).sum())}"
        mm = re.match(r'^E1c\.3 isd\.200\.settings\.(\w+)\.A_equal_elements\.quantiles_db_1_5_50_95\.M2412\.7\.2GHz\.(\w+)\[(\d)\]$', src)
        if mm:
            ok = num(val) == quant(mm.group(1), mm.group(2))[int(mm.group(3))]
        mm = re.match(r'^E1\.5\.4 summary analytic_thresholds_db\.F2\.(DL|UL)', src)
        if mm:
            ok = num(val) == TH[mm.group(1)]
        mm = re.match(r'^E1c\.3 isd\.200\.settings\.(\w+)\.(\w+)\.mixes\.M2412\.F2\.(\S+) \(SE: mixes_mc_se\)$', src)
        if mm:
            v = st(mm.group(1))[mm.group(2)]['mixes']['M2412']['F2'][mm.group(3)]
            se = st(mm.group(1))[mm.group(2)]['mixes_mc_se']['M2412']['F2'][mm.group(3)]
            ok = num(val) == v and close(num(row['lo']), v - se) and close(num(row['hi']), v + se)
        mm = re.match(r'^P3\.1 layer_3p5\.P_w_by_case_and_load\.(right|left)\["([\d.]+)"\]$', src)
        if mm:
            ok = num(val) == p35[mm.group(1)][mm.group(2)] and row['g'] == mm.group(2)
        mm = re.match(r'^P3\.1 layer_7p2\.points\.(\S+)\.P_w\["([\d.]+)_([\d.]+)"\] \+ P3\.2c constants\.hardware\.(\S+)\.P_aux_w\.(S\d)$', src)
        if mm:
            ok = (mm.group(1) == mm.group(4) == HW
                  and close(num(val), pt['P_w'][f'{mm.group(2)}_{mm.group(3)}'] + paux[mm.group(5)], 1e-12))
        mm = re.match(r'^P3\.2c energy_rows_hw_primary_settings\[(.*)\]\.E_site_kWh; feasible\.all_user=(True|False)$', src)
        if mm:
            kv = dict(p.split('=') for p in mm.group(1).split(', '))
            hits = [r for r in S32['energy_rows_hw_primary_settings'] if all(str(r[k]) == v_ for k, v_ in kv.items())]
            ok = (len(hits) == 1 and num(val) == hits[0]['E_site_kWh']['mean']
                  and num(row['lo']) == hits[0]['E_site_kWh']['lo'] and num(row['hi']) == hits[0]['E_site_kWh']['hi']
                  and str(hits[0]['feasible']['all_user']) == mm.group(2))
        mm = re.match(r'^P3\.2c g_star\["(.*)"\]$', src)
        if mm:
            ok = num(val) == S32['g_star'][mm.group(1)]
        if not ok:
            bad.append(src[:90])
    nrows += len(rows)
    check(f'{fid}: all {len(rows)} source-data rows equal the value at their source key', not bad, bad[:3])

# ------------------------------------------------------------------ encodings, outputs, fonts, sizes
pv45 = jl(FIG / 'plotted_values.json')['fig5_heldout_savings_w6']
check('S0-S2 colours in Figure 3 equal those of Figure 5',
      all(PV['fig3_power_energy_w4'][f'f3a|72|{S}']['color'] == pv45[f'f5|sav|ericsson_typical|{S}']['color']
          for S in ('S0', 'S1', 'S2')))
p2 = PV['fig2_interference_feasibility_w2']
mc = {sn: {p2[f'f2|{mt}|{sn}|{part}']['color'] for mt in DIRS for part in ('range', 'ends', 'p5', 'p50')} for sn in MOD}
check('Figure 2: one colour per interference model in both rows groups, four distinct colours',
      all(len(v) == 1 for v in mc.values()) and len({next(iter(v)) for v in mc.values()}) == 4, mc)
for k, h in MAN['outputs_sha256'].items():
    check(f'output hash matches figure manifest: {k}', sha(FIG / k) == h)
check('figure manifest: layout and build checks passed', MAN['all_layout_checks_passed'] is True
      and all(not v['problems'] and v['min_font_pt'] >= 7.0 for v in MAN['layout_checks'].values()))
check('figure manifest: shared style module unchanged since Figures 4-8',
      MAN['shared_style_module']['sha256'] == sha(ROOT / 'scripts/figures/frozen_figures.py')
      == jl(FIG / 'manifest.json')['script_sha256'])
for fid in FIDS:
    check(f'{fid}: width {WIDTH[fid]} in (IEEE {"single" if WIDTH[fid] < 4 else "double"} column)',
          close(MAN['width_in'][fid], WIDTH[fid], 1e-12))
    pdf = (FIG / f'{fid}.pdf').read_bytes()
    names = sorted({f.decode() for f in re.findall(rb'/BaseFont /([A-Za-z0-9+\-]+)', pdf)})
    check(f'{fid}.pdf: no Type 3 fonts, TrueType embedded, STIX only',
          b'/Type3' not in pdf and b'/FontFile2' in pdf and names and all('STIX' in n for n in names), ', '.join(names))
    png = (FIG / f'{fid}.png').read_bytes()
    w, h = struct.unpack('>II', png[16:24])
    i = png.find(b'pHYs')
    ppm = struct.unpack('>I', png[i + 4:i + 8])[0] if i > 0 else 0
    check(f'{fid}.png: {WIDTH[fid]} in at 600 dpi', w == round(WIDTH[fid] * 600) and abs(ppm * 0.0254 - 600) < 0.5,
          f'{w} x {h} px')

ok_all = all(c_['passed'] for c_ in CHECKS)
report = {'all_checks_passed': ok_all, 'n_checks': len(CHECKS), 'n_plotted_values': nvals, 'n_source_rows': nrows,
          'figure_manifest_sha256': sha(FIG / f'manifest_{SUF}.json'), 'verifier_sha256': sha(Path(__file__)),
          'checks': CHECKS}
(FIG / f'verification_report_{SUF}.json').write_text(json.dumps(report, indent=1, ensure_ascii=False) + '\n',
                                                     encoding='utf-8', newline='\n')
lines = [f'{"PASS" if c_["passed"] else "FAIL"}  {c_["check"]}' + (f'  [{c_["detail"]}]' if c_['detail'] and not
                                                                     c_['passed'] else '') for c_ in CHECKS]
summary = (f'{"ALL CHECKS PASSED" if ok_all else "SOME CHECKS FAILED"}: {sum(c_["passed"] for c_ in CHECKS)} / '
           f'{len(CHECKS)}; {nvals} plotted values and {nrows} source-data rows recomputed independently')
(FIG / f'verification_report_{SUF}.txt').write_text('\n'.join(lines + ['', summary]) + '\n', encoding='utf-8',
                                                    newline='\n')
print('\n'.join(l_ for l_ in lines if l_.startswith('FAIL')))
print(summary)
sys.exit(0 if ok_all else 1)
