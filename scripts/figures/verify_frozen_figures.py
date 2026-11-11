"""Independent verification of the frozen-results figures (Figures 4-8).

Usage:  python -I scripts/figures/verify_frozen_figures.py <repo root>

Shares no code with frozen_figures.py. From the frozen P3.3 / P3.4 summaries
and the P3.4 config it recomputes:
  - every value read back from the rendered artists (paper/figures/plotted_values.json),
    and requires the tagged artists to be exactly the expected set;
  - every row of paper/figures/source_data/*.csv, by resolving its source key;
  - every caption number (caption_facts.json) and the fixed caption constants;
and checks input and output hashes, controller colours across figures, the
fonts embedded in the PDFs (no Type 3, STIX only), the PNG size (600 dpi at
7.16 in) and the recorded layout and colour-encoding checks.
Writes paper/figures/verification_report.json and verification_report.txt.
"""
import csv
import hashlib
import json
import re
import struct
import sys
from pathlib import Path

ROOT = Path(sys.argv[1]).resolve()
FIG = ROOT / 'paper' / 'figures'
P34, P33 = ROOT / 'results/phase3/p3_4_heldout', ROOT / 'results/phase3/p3_3_controllers'
CFGP = ROOT / 'configs/p3/p3_4_heldout.json'
sha = lambda p: hashlib.sha256(Path(p).read_bytes()).hexdigest()
CHECKS = []


def check(name, ok, detail=''):
    CHECKS.append({'check': name, 'passed': bool(ok), 'detail': detail})
    return ok


def close(a, b, tol=1e-9):
    return abs(a - b) <= tol * max(1.0, abs(a), abs(b))


# ------------------------------------------------------------------ inputs and outputs
m4 = json.loads((P34 / 'manifest.json').read_text())
m3 = json.loads((P33 / 'manifest.json').read_text())
check('input: P3.4 summary.json matches its manifest', sha(P34 / 'summary.json') == m4['outputs_sha256']['summary.json'])
check('input: P3.3 manifest matches the P3.4 manifest', sha(P33 / 'manifest.json') == m4['input_sha256']['p3_3/manifest.json'])
check('input: P3.3 summary.json matches its manifest', sha(P33 / 'summary.json') == m3['outputs_sha256']['summary.json'])
check('input: P3.4 config matches the P3.4 manifest', sha(CFGP) == m4['config_sha256'])
S4 = json.loads((P34 / 'summary.json').read_text())
S3 = json.loads((P33 / 'summary.json').read_text())
CFG = json.loads(CFGP.read_text())
R, PAIRED, DEV = S4['results_test'], S4['paired'], S3['results_dev']
MF = json.loads((FIG / 'manifest.json').read_text())
for f, h in MF['outputs_sha256'].items():
    check(f'output hash: {f}', sha(FIG / f) == h)
check('figure manifest records the same input hashes', all(sha(ROOT / k) == v for k, v in MF['input_sha256'].items()))
check('figure script hash recorded', sha(ROOT / MF['tool']) == MF['script_sha256'])
check('inputs unchanged during plotting; rendering byte-identical',
      MF['inputs_unchanged_after_plotting'] and MF['deterministic_rendering'])
lay = MF['layout_checks']
check('layout: no text overlaps, nothing outside the figure, no text inside another panel',
      all(not v['problems'] for v in lay.values()), json.dumps({k: v['problems'] for k, v in lay.items()}))
check('layout: every text >= 7 pt at printed size', all(v['min_font_pt'] >= 7.0 for v in lay.values()),
      json.dumps({k: v['min_font_pt'] for k, v in lay.items()}))
enc = MF['encoding_checks']['fig8_comparison_robustness_w9']
check('heatmap colours: every cell on the correct side of the budget colour; neutral at 1%',
      not enc['heatmap_cells_on_wrong_side_of_budget_colour'] and enc['max_channel_error_at_budget'] <= 0.02)

# ------------------------------------------------------------------ independent recomputation
G = [0.5, 1.0, 1.5, 2.0, 2.5, 3.0, 3.5]
MIX = ['ericsson_typical', 'genai_2025']
SS = ['S0', 'S1', 'S2']
ROWS8 = ['primary_S0', 'primary_S1', 'primary_S2', 'cat2_S0', 'cat2_S1', 'cat2_S2', 'ulsens_S0', 'ulsens_S1',
         'ulsens_S2', 'left_S0', 'set3_S0', 'mrf64_S0', 'mrf128_S0', 'mrf1024_S0']
res = lambda c, m, g, s: R[f'{c}|{m}|{g}|{s}']
av = lambda c, m, g, s, k='all_user': res(c, m, g, s)['avoidable_violation_share'][k]
E = lambda c, m, g, s: res(c, m, g, s)['E_site_kWh_day']


def saving_from_energy(c, m, g, s):
    return 1 - E(c, m, g, s) / E('aon', m, g, s)


# the stored saving point is the pooled energy ratio (checked for every cell used below)
for k, rr in R.items():
    c, m, g, s = k.split('|')
    if c not in ('aon',) and rr.get('saving_vs_aon'):
        if not close(rr['saving_vs_aon']['point'], saving_from_energy(c, m, float(g), s), 1e-12):
            check(f'stored saving equals 1 - E/E_aon: {k}', False)
check('stored savings equal 1 - E/E_aon in every cell', not any(not c_['passed'] for c_ in CHECKS
                                                               if c_['check'].startswith('stored saving equals')))


def decided_alike(m, a, b):
    ks = ('pattern_share', 'transitions_per_day', 'gap_s_per_day', 'avoidable_violation_share', 'mean_cells_on')
    return all(res('mpc', m, g, f'primary_{a}')[k] == res('mpc', m, g, f'primary_{b}')[k] for g in G for k in ks)


GROUPS = {}
for m in MIX:
    gr = []
    for s in SS:
        hit = next((x for x in gr if decided_alike(m, x[0], s)), None)
        (hit.append(s) if hit is not None else gr.append([s]))
    GROUPS[m] = gr

EXP = {}       # gid -> expected dict


def exp(gid, **kw):
    assert gid not in EXP, gid
    EXP[gid] = kw


ones = lambda p: sum(int(t) for t in p.strip('()').split(','))
# Figure 4
for m in MIX:
    for g in G:
        ps = res('mpc', m, g, 'primary_S0')['pattern_share']
        by = [100 * sum(v for p, v in ps.items() if ones(p) == k) for k in range(4)]
        for k in range(4):
            exp(f'f4a|{m}|{g}|{k}', h=by[k], y0=sum(by[:k]))
    for grp in GROUPS[m]:
        t = ''.join(grp)
        exp(f'f4b|cells|{m}|{t}', x=G, y=[res('mpc', m, g, f'primary_{grp[0]}')['mean_cells_on'] for g in G])
        exp(f'f4b|trans|{m}|{t}', x=G, y=[res('mpc', m, g, f'primary_{grp[0]}')['transitions_per_day'] for g in G])
# Figure 5
for m in MIX:
    for s in SS:
        sv = [res('mpc', m, g, f'primary_{s}')['saving_vs_aon'] for g in G]
        exp(f'f5|sav|{m}|{s}', x=G, y=[100 * saving_from_energy('mpc', m, g, f'primary_{s}') for g in G])
        exp(f'f5|ci|{m}|{s}', segs_v=[(g, 100 * v['lo'], 100 * v['hi']) for g, v in zip(G, sv)])
    for grp in GROUPS[m]:
        exp(f'f5|viol|{m}|{"".join(grp)}', x=G, y=[100 * av('mpc', m, g, f'primary_{grp[0]}') for g in G])
    exp(f'f5|budget|{m}', y=[1.0, 1.0])
    un = [g for g in G if res('aon', m, g, 'primary_S0')['unavoidable_share']['all_user'] > 0.01]
    if un:
        exp(f'f5|unavband|{m}', band=(min(un), max(un)))
# Figure 6
mx6 = 'genai_2025'
for g in (1.0, 1.5, 2.0):
    for grp in (['S0'], ['S1', 'S2']):
        s, t = f'primary_{grp[0]}', ''.join(grp)
        for s2 in grp[1:]:
            same = all(av(c, mx6, g, s, k) == av(c, mx6, g, f'primary_{s2}', k)
                       for c in ('thr_dl', 'thr_ul', 'mpc') for k in ('all_user', 'dl_only_all_user'))
            check(f'Fig. 6(a): S1 and S2 identical at g = {g}', same)
        dl, al = 100 * av('thr_dl', mx6, g, s, 'dl_only_all_user'), 100 * av('thr_dl', mx6, g, s)
        exp(f'f6a|span|{g}|{t}', x=[dl, al])
        exp(f'f6a|thr_dl_dlcheck|{g}|{t}', x=[dl])
        exp(f'f6a|thr_dl|{g}|{t}', x=[al])
        exp(f'f6a|thr_ul|{g}|{t}', x=[100 * av('thr_ul', mx6, g, s)])
        exp(f'f6a|mpc|{g}|{t}', x=[100 * av('mpc', mx6, g, s)])
exp('f6a|budget', x=[1.0, 1.0])
for s in ('S0', 'S2'):
    for c in ('thr_dl', 'thr_ul', 'mpc', 'oracle'):
        for g in (1.0, 1.5, 2.0):
            rr = res(c, mx6, g, f'primary_{s}')
            ok = rr['saving_qualified']
            exp(f'f6b|{s}|{c}|{g}', h=100 * saving_from_energy(c, mx6, g, f'primary_{s}'), hatched=not ok)
            exp(f'f6b|ci|{s}|{c}|{g}', segs_v_y=[(100 * rr['saving_vs_aon']['lo'], 100 * rr['saving_vs_aon']['hi'])])
            if not ok:
                exp(f'f6b|txt|{s}|{c}|{g}', text=f'{100 * av(c, mx6, g, f"primary_{s}"):.1f}%')
# Figure 7
pat = S3['patterns']
for i in range(8):
    ps = str(tuple(pat[str(i)]))
    for m in MIX:
        exp(f'f7a|both|{m}|{i}', h=S3['max_capacity_pattern'][f'{m}|-3.0']['x_star_both_all_by_pattern'][ps])
    v = S3['x_star_per_pattern'][f'DL|{i}|-3.0|all']['x_star']
    exp(f'f7a|dl|{i}', y=[v, v])
u4 = lambda m, g: 100 * res('aon', m, g, 'primary_S0')['unavoidable_share']['all_user']
for m in MIX:
    exp(f'f7b|test|{m}', x=G, y=[u4(m, g) for g in G])
    exp(f'f7b|dev|{m}', x=G, y=[100 * DEV[f'aon|{m}|{g}|primary_S0']['unavoidable_share']['all_user'] for g in G])
    xs = S3['max_capacity_pattern'][f'{m}|-3.0']['x_star_both_all_by_pattern']['(1, 1, 1)']
    exp(f'f7b|xstar|{m}', x=[xs, xs])
exp('f7b|h4', y=[1.0, 1.0])
for m, g in (('genai_2025', 2.0), ('genai_2025', 2.5), ('ericsson_typical', 3.0)):
    exp(f'f7b|txt|{m}|{g}', text=f'{u4(m, g):.2f}%')
# Figure 8
sm, sg, ss = 'genai_2025', 2.0, 'primary_S2'
for c in ('mpc', 'exh', 'thr_ul', 'thr_dl', 'tod', 'oracle', 'oracle_budget'):
    exp(f'f8a|{c}', x=[100 * av(c, sm, sg, ss)], y=[100 * saving_from_energy(c, sm, sg, ss)])
exp('f8a|budget', x=[1.0, 1.0])
for s in ('S0', 'S2'):
    for m in MIX:
        for g in (1.0, 2.0):
            key = f'{m}|{g}|primary_{s}'
            for ref in ('oracle', 'oracle_budget', 'oracle_test'):
                gap = (E('mpc', m, g, f'primary_{s}') - E(ref, m, g, f'primary_{s}')) / E('aon', m, g, f'primary_{s}')
                pr = PAIRED[key][f'mpc-{ref}']
                check(f'paired gap equals energy recomputation: {key} {ref}', close(gap, pr['point'], 1e-12))
                exp(f'f8b|gap|{ref}|{key}', x=[100 * gap])
                exp(f'f8b|ci|{ref}|{key}', segs_h_x=[(100 * pr['lo'], 100 * pr['hi'])])
    q = 100 * CFG['predictions']['Q3']['max_gap'][s]
    exp(f'f8b|limit|{s}', x=[q, q])
Z = []
for i, s in enumerate(ROWS8):
    row = []
    for j, m in enumerate(MIX):
        row += [100 * av('mpc', m, g, s) for g in G] + ([None] if j == 0 else [])
        for k, g in enumerate(G):
            if not res('mpc', m, g, s)['saving_qualified']:
                exp(f'f8c|fail|{s}|{m}|{g}', rect=(k + j * 8 - 0.5, i - 0.5))
                exp(f'f8c|txt|{s}|{m}|{g}', text=f'{100 * av("mpc", m, g, s):.1f}')
    Z.append(row)
exp('f8c|grid', z=Z)
for s in ROWS8:
    for m in MIX:
        rr = res('mpc', m, 2.0, s)
        exp(f'f8d|sav|{m}|{s}', x=[100 * saving_from_energy('mpc', m, 2.0, s)])
        exp(f'f8d|ci|{m}|{s}', segs_h_x=[(100 * rr['saving_vs_aon']['lo'], 100 * rr['saving_vs_aon']['hi'])])
        if not rr['saving_qualified']:
            exp(f'f8d|fail|{m}|{s}', x=[100 * saving_from_energy('mpc', m, 2.0, s)])
            exp(f'f8d|txt|{m}|{s}', text=f'fails budget ({100 * av("mpc", m, 2.0, s):.2f}%)')

# ------------------------------------------------------------------ compare with the rendered artists
PV = json.loads((FIG / 'plotted_values.json').read_text())
plotted = {gid: v for fig in PV.values() for gid, v in fig.items()}
missing, extra = sorted(set(EXP) - set(plotted)), sorted(set(plotted) - set(EXP))
check('every expected artist is plotted, and nothing else is tagged', not missing and not extra,
      f'missing {missing[:8]} extra {extra[:8]}')
bad = []
nvals = 0


def cmp_list(gid, a, b):
    global nvals
    if len(a) != len(b):
        bad.append(f'{gid}: length {len(a)} != {len(b)}')
        return
    for p, q in zip(a, b):
        nvals += 1
        if not close(p, q):
            bad.append(f'{gid}: {p} != {q}')


for gid, e in EXP.items():
    if gid not in plotted:
        continue
    v = plotted[gid]
    if 'x' in e:
        cmp_list(gid, v['x'], e['x'])
    if 'y' in e:
        cmp_list(gid, v['y'], e['y'])
    if 'h' in e:
        cmp_list(gid, [v['h']], [e['h']])
    if 'y0' in e:
        cmp_list(gid, [v['y0']], [e['y0']])
    if 'hatched' in e and bool(v['hatch']) != e['hatched']:
        bad.append(f'{gid}: hatch {v["hatch"]!r}, expected hatched={e["hatched"]}')
    if 'segs_v' in e:
        got = [(s[0][0], s[0][1], s[1][1]) for s in v['segs']]
        cmp_list(gid, [t for s in got for t in s], [t for s in e['segs_v'] for t in s])
    if 'segs_v_y' in e:
        cmp_list(gid, [t for s in v['segs'] for t in (s[0][1], s[1][1])], [t for s in e['segs_v_y'] for t in s])
    if 'segs_h_x' in e:
        cmp_list(gid, [t for s in v['segs'] for t in (s[0][0], s[1][0])], [t for s in e['segs_h_x'] for t in s])
    if 'band' in e:
        cmp_list(gid, [v['x0'] + 0.15, v['x0'] + v['w'] - 0.15], list(e['band']))
    if 'rect' in e:
        cmp_list(gid, [v['x0'], v['y0'], v['w'], v['h']], [e['rect'][0], e['rect'][1], 1.0, 1.0])
    if 'text' in e:
        nvals += 1
        if v['s'] != e['text']:
            bad.append(f'{gid}: text {v["s"]!r} != {e["text"]!r}')
    if 'z' in e:
        for i, (rz, re_) in enumerate(zip(v['z'], e['z'])):
            for p, q in zip(rz, re_):
                nvals += 1
                if (p is None) != (q is None) or (p is not None and not close(p, q)):
                    bad.append(f'{gid}[{i}]: {p} != {q}')
check(f'plotted values equal the independent recomputation ({nvals} values)', not bad, '; '.join(bad[:10]))

# controller colours: one colour per controller wherever colour encodes the controller (Figs. 6, 8)
PAL = {'mpc': '#2a78d6', 'exh': '#eb6834', 'thr_ul': '#1baf7a', 'thr_dl': '#e34948', 'tod': '#eda100',
       'oracle': '#008300', 'oracle_budget': '#e87ba4', 'oracle_test': '#6250d6'}


def eff(v):
    if v['kind'] == 'rect':
        return v['edgecolor']
    if v.get('mfc') not in (None, '#ffffff', 'none') and v['marker'] not in ('None', ''):
        return v['mfc']
    return v.get('mec') or v['color']


colour_bad = []
for gid, v in plotted.items():
    p = gid.split('|')
    ctrl = None
    if p[0] == 'f6a' and p[1] in ('span', 'thr_dl_dlcheck', 'thr_dl'):
        ctrl = 'thr_dl'
    elif p[0] == 'f6a' and p[1] in ('thr_ul', 'mpc'):
        ctrl = p[1]
    elif p[0] == 'f6b' and p[1] != 'ci' and p[1] != 'txt':
        ctrl = p[2]
    elif p[0] == 'f8a' and p[1] in PAL:
        ctrl = p[1]
    elif p[0] == 'f8b' and p[1] == 'gap':
        ctrl = p[2]
    elif p[0] == 'f8d' and p[1] == 'sav':
        ctrl = 'mpc'
    if ctrl and eff(v) != PAL[ctrl]:
        colour_bad.append(f'{gid}: {eff(v)} != {PAL[ctrl]}')
check('controller colours identical across figures', not colour_bad, '; '.join(colour_bad[:6]))

# ------------------------------------------------------------------ source-data CSVs
TOK = re.compile(r'\.([A-Za-z_][A-Za-z0-9_]*)|\["([^"]+)"\]')


def resolve(src):
    if src.startswith('configs/p3/p3_4_heldout.json '):
        obj = CFG
        for k in src.split(' ', 1)[1].split('.'):
            obj = obj[k]
        return obj
    m = re.match(r'(P3\.4|P3\.3) (\w+)(.*)$', src)
    obj = (S4 if m.group(1) == 'P3.4' else S3)[m.group(2)]
    for a, b in TOK.findall(m.group(3)):
        obj = obj[a or b]
    return obj


csv_bad, nrows = [], 0
for fp in sorted((FIG / 'source_data').glob('*.csv')):
    with open(fp, encoding='utf-8', newline='') as f:
        for row in csv.DictReader(f):
            nrows += 1
            if not row['source']:
                csv_bad.append(f'{fp.name}: row without a source')
                continue
            obj = resolve(row['source'])
            unit, val = row['unit'], row['value']
            scale = 100.0 if unit in ('%', 'points') else 1.0
            if unit == 'bool':
                ok = (val == 'True') == bool(obj)
            elif row['quantity'] == 'share of time':
                k = int(row['series'].split()[0])
                ok = close(float(val), 100 * sum(v for p, v in obj.items() if ones(p) == k))
            elif isinstance(obj, dict):
                ok = close(float(val), scale * obj['point'])
                if row['lo']:
                    ok = ok and close(float(row['lo']), scale * obj['lo']) and close(float(row['hi']), scale * obj['hi'])
            else:
                ok = close(float(val), scale * float(obj))
            if not ok:
                csv_bad.append(f'{fp.name}: {row["series"]} {row["g"]} {row["source"]}')
check(f'source-data rows equal their source keys ({nrows} rows)', not csv_bad and nrows > 0, '; '.join(csv_bad[:6]))

# ------------------------------------------------------------------ caption facts and constants
F = json.loads((FIG / 'caption_facts.json').read_text())
cap = (FIG / 'captions.md').read_text(encoding='utf-8')
mpcp = [(m, g, s) for m in MIX for g in G for s in SS]
used = sorted({p.strip('()').replace(', ', '') for m, g, s in mpcp
               for p, v in res('mpc', m, g, f'primary_{s}')['pattern_share'].items() if v > 0})
grp_txt = '; '.join(f'{lab}: ' + ('; '.join(' = '.join(x) for x in GROUPS[m] if len(x) > 1) or 'none')
                    for m, lab in zip(MIX, ('10/90', '26/74')))
h2 = []
for g in (1.0, 1.5, 2.0):
    for s in SS:
        d = av('thr_dl', mx6, g, f'primary_{s}') - av('thr_ul', mx6, g, f'primary_{s}')
        check(f'H2 difference equals recomputation: g {g} {s}',
              close(d, PAIRED[f'{mx6}|{g}|primary_{s}']['viol thr_dl-thr_ul']['point'], 1e-12))
        h2.append(100 * d)
fails = sorted(100 * av('mpc', m, g, s) for m in MIX for g in G for s in ROWS8 if not res('mpc', m, g, s)['saving_qualified'])
PLAIN = {'primary': 'primary', 'cat2': 'Cat 2 sleep', 'ulsens': 'UL-load term', 'left': '"left" 3.5 GHz case',
         'set3': 'Set 3 ratios', 'mrf64': 'M_RF = 64', 'mrf128': 'M_RF = 128', 'mrf1024': 'M_RF = 1024'}
fwhere = sorted({f'{"10/90" if m == MIX[0] else "26/74"}, {PLAIN[s.rsplit("_", 1)[0]]}, {s.rsplit("_", 1)[1]}'
                 for m in MIX for g in G for s in ROWS8 if not res('mpc', m, g, s)['saving_qualified']})
dE = []
for k in R:
    c, m, g, s = k.split('|')
    if c == 'mpc' and R[k]['saving_qualified'] and R[f'exh|{m}|{g}|{s}']['saving_qualified']:
        dE.append(100 * (saving_from_energy('exh', m, float(g), s) - saving_from_energy('mpc', m, float(g), s)))
q3 = {s: max(100 * (E('mpc', m, g, f'primary_{s}') - E('oracle', m, g, f'primary_{s}')) / E('aon', m, g, f'primary_{s}')
             for m in MIX for g in (1.0, 2.0)) for s in ('S0', 'S2')}
gbud = max(100 * (E('mpc', m, g, f'primary_{s}') - E('oracle_budget', m, g, f'primary_{s}')) / E('aon', m, g, f'primary_{s}')
           for m in MIX for g in (1.0, 2.0) for s in ('S0', 'S2'))
xa = lambda m: S3['max_capacity_pattern'][f'{m}|-3.0']['x_star_both_all_by_pattern']['(1, 1, 1)']
expect_facts = {
    'f4_patterns_used': ', '.join(used),
    'f4_max_transitions': f'{max(res("mpc", m, g, f"primary_{s}")["transitions_per_day"] for m, g, s in mpcp):.1f}',
    'f45_same_decisions': grp_txt,
    'f5_max_violation': f'{max(100 * av("mpc", m, g, f"primary_{s}") for m, g, s in mpcp):.2f}',
    'f5_h1_S0': '{:.2f}–{:.2f}'.format(*(lambda v: (min(v), max(v)))(
        [100 * saving_from_energy('mpc', m, g, 'primary_S0') for m in MIX for g in (1.0, 2.0)])),
    'f5_h1_S2': '{:.2f}–{:.2f}'.format(*(lambda v: (min(v), max(v)))(
        [100 * saving_from_energy('mpc', m, g, 'primary_S2') for m in MIX for g in (1.0, 2.0)])),
    'f6_h2_range': f'{min(h2):.2f}–{max(h2):.2f}',
    'f6_dlcheck_max': f'{max(100 * av("thr_dl", mx6, g, f"primary_{s}", "dl_only_all_user") for g in (1.0, 1.5, 2.0) for s in SS):.2f}',
    'f7_xstar_all_on_1090': f'{xa("ericsson_typical"):.2f}',
    'f7_xstar_all_on_2674': f'{xa("genai_2025"):.2f}',
    'f7_xstar_dl_all_on': f"{S3['x_star_per_pattern']['DL|7|-3.0|all']['x_star']:.2f}",
    'f7_u_2674_g2': f'{u4("genai_2025", 2.0):.2f}', 'f7_u_2674_g25': f'{u4("genai_2025", 2.5):.2f}',
    'f7_u_1090_g3': f'{u4("ericsson_typical", 3.0):.2f}',
    'f8_n_fail': str(len(fails)), 'f8_fail_range': f'{fails[0]:.2f}–{fails[-1]:.2f}', 'f8_fail_where': '; '.join(fwhere),
    'f8_both_qualified': str(len(dE)), 'f8_exh_minus_mpc_mean': f'{sum(dE) / len(dE):.2f}',
    'f8_exh_minus_mpc_max': f'{max(dE):.2f}', 'f8_q3_max_S0': f'{q3["S0"]:.2f}', 'f8_q3_max_S2': f'{q3["S2"]:.2f}',
    'f8_gap_budget_max': f'{gbud:.2f}',
}
fb = [f'{k}: {F.get(k)!r} != {v!r}' for k, v in expect_facts.items() if F.get(k) != v]
check('caption facts equal the independent recomputation', not fb and set(F) == set(expect_facts), '; '.join(fb))
check('every caption fact appears in captions.md, no unfilled placeholder',
      all(v in cap for v in F.values()) and not re.search(r'\{[a-z0-9_]+\}', cap))
tt = S4['test_traces']
consts = {
    '25 test squares': len(tt['squares']) == 25,
    '100 radio realizations, 100-199 (stored half-open as [100, 200))':
        S4['n_realizations'] == 100 and S4['realizations'] == [100, 200],
    '2,000 resamples': CFG['statistics']['reps'] == 2000,
    'test week 18-24 November 2013': '18-24 November 2013' in S4['holdout']['temporal'],
    'T_0.5 = 513.6 Mbit/s': f"{S3['frozen_for_p3_4']['T50_bps'] / 1e6:.1f}" == '513.6',
    'all 42 primary mpc cells meet the budget': sum(res('mpc', m, g, f'primary_{s}')['saving_qualified']
                                                    for m, g, s in mpcp) == 42 == len(mpcp),
    '196 scenario cells': sum(1 for k in R if k.startswith('mpc|')) == 196,
    'Q3 limits 1 and 5 points': CFG['predictions']['Q3']['max_gap'] == {'S0': 0.01, 'S2': 0.05},
    'H2 registered at 26/74, g in {1, 1.5, 2}, S0-S2': sorted(CFG['predictions']['H2']['cells']) == sorted(
        f'genai_2025|{g}|{s}' for g in (1.0, 1.5, 2.0) for s in SS),
}
for k, ok in consts.items():
    check(f'caption constant: {k}', ok)

# ------------------------------------------------------------------ PDF fonts and PNG size
for fid in PV:
    pdf = (FIG / f'{fid}.pdf').read_bytes()
    fonts = sorted(set(re.findall(rb'/BaseFont /([A-Za-z0-9+\-]+)', pdf)))
    names = [f.decode() for f in fonts]
    check(f'{fid}.pdf: no Type 3 fonts, TrueType embedded, STIX only',
          b'/Type3' not in pdf and b'/FontFile2' in pdf and names and all('STIX' in n for n in names), ', '.join(names))
    png = (FIG / f'{fid}.png').read_bytes()
    w, h = struct.unpack('>II', png[16:24])
    i = png.find(b'pHYs')
    ppm = struct.unpack('>I', png[i + 4:i + 8])[0] if i > 0 else 0
    check(f'{fid}.png: 7.16 in at 600 dpi', w == 4296 and abs(ppm * 0.0254 - 600) < 0.5, f'{w} x {h} px')

ok_all = all(c_['passed'] for c_ in CHECKS)
report = {'all_checks_passed': ok_all, 'n_checks': len(CHECKS), 'n_plotted_values': nvals, 'n_source_rows': nrows,
          'figure_manifest_sha256': sha(FIG / 'manifest.json'), 'verifier_sha256': sha(Path(__file__)), 'checks': CHECKS}
(FIG / 'verification_report.json').write_text(json.dumps(report, indent=1, ensure_ascii=False) + '\n', encoding='utf-8',
                                              newline='\n')
lines = [f'{"PASS" if c_["passed"] else "FAIL"}  {c_["check"]}' + (f'  [{c_["detail"]}]' if c_['detail'] and not c_['passed'] else '')
         for c_ in CHECKS]
summary = (f'{"ALL CHECKS PASSED" if ok_all else "SOME CHECKS FAILED"}: {sum(c_["passed"] for c_ in CHECKS)} / {len(CHECKS)}; '
           f'{nvals} plotted values and {nrows} source-data rows recomputed independently')
(FIG / 'verification_report.txt').write_text('\n'.join(lines + ['', summary]) + '\n', encoding='utf-8', newline='\n')
print('\n'.join(l for l in lines if l.startswith('FAIL')))
print(summary)
sys.exit(0 if ok_all else 1)
