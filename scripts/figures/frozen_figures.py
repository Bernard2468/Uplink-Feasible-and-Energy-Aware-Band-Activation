"""Publication Figures 4-8 from the frozen P3.3 / P3.4 outputs.

Usage:  python -I scripts/figures/frozen_figures.py <repo root>

Read-only on every experiment output: inputs are hash-checked against their
manifests before and after plotting. Writes paper/figures/:
  figN_<name>_w<working id>.pdf / .png   (vector PDF, 600 dpi PNG, final size)
  source_data/figN_*.csv                 (every plotted number, with its source key)
  plotted_values.json                    (values read back from the rendered artists)
  caption_facts.json, captions.md
  manifest.json                          (hashes, software, font, git head, layout checks)
Plotted numbers are checked by scripts/figures/verify_frozen_figures.py, an
independent recomputation. Rules: docs/PHASE_3_ENERGY_PROTOCOL.md §10.4-10.5.
"""
import csv
import hashlib
import io
import json
import platform
import subprocess
import sys
from pathlib import Path

import matplotlib

matplotlib.use('Agg')
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
from matplotlib import font_manager  # noqa: E402
from matplotlib.collections import LineCollection  # noqa: E402
from matplotlib.colors import LinearSegmentedColormap, TwoSlopeNorm, to_hex  # noqa: E402
from matplotlib.image import AxesImage  # noqa: E402
from matplotlib.lines import Line2D  # noqa: E402
from matplotlib.patches import Patch, Rectangle  # noqa: E402
from matplotlib.text import Text  # noqa: E402
from matplotlib.ticker import NullFormatter, NullLocator  # noqa: E402

ROOT = Path(sys.argv[1]).resolve()
OUT = ROOT / 'paper' / 'figures'
P34 = ROOT / 'results/phase3/p3_4_heldout'
P33 = ROOT / 'results/phase3/p3_3_controllers'
CFG34 = ROOT / 'configs/p3/p3_4_heldout.json'

# ---------------------------------------------------------------- inputs
sha = lambda p: hashlib.sha256(Path(p).read_bytes()).hexdigest()


def check_inputs():
    m4 = json.loads((P34 / 'manifest.json').read_text())
    m3 = json.loads((P33 / 'manifest.json').read_text())
    pairs = [(P34 / 'summary.json', m4['outputs_sha256']['summary.json']),
             (P33 / 'manifest.json', m4['input_sha256']['p3_3/manifest.json']),
             (P33 / 'summary.json', m3['outputs_sha256']['summary.json']),
             (CFG34, m4['config_sha256'])]
    rec_ = {}
    for p, h in pairs:
        got = sha(p)
        if got != h:
            raise SystemExit(f'input hash mismatch: {p} {got} != manifest {h}')
        rec_[p.relative_to(ROOT).as_posix()] = got
    rec_['results/phase3/p3_4_heldout/manifest.json'] = sha(P34 / 'manifest.json')
    return rec_


INPUTS_BEFORE = check_inputs()
S4 = json.loads((P34 / 'summary.json').read_text())
S3 = json.loads((P33 / 'summary.json').read_text())
CFG = json.loads(CFG34.read_text())
R, PAIRED, D3 = S4['results_test'], S4['paired'], S3['results_dev']

G = (0.5, 1.0, 1.5, 2.0, 2.5, 3.0, 3.5)
MIXES = ('ericsson_typical', 'genai_2025')
SC = ('S0', 'S1', 'S2')
SCN_ORDER = ['primary_S0', 'primary_S1', 'primary_S2', 'cat2_S0', 'cat2_S1', 'cat2_S2',
             'ulsens_S0', 'ulsens_S1', 'ulsens_S2', 'left_S0', 'set3_S0', 'mrf64_S0', 'mrf128_S0', 'mrf1024_S0']
assert set(SCN_ORDER) == {k.split('|')[3] for k in R}, 'scenario set changed'
assert set(G) == {float(k.split('|')[2]) for k in R}
BUDGET = 0.01


def r(c, mx, g, scn):
    return R[f'{c}|{mx}|{g}|{scn}']


def viol(c, mx, g, scn, crit='all_user'):
    return r(c, mx, g, scn)['avoidable_violation_share'][crit]


def same_decisions(mx, sa, sb):
    """mpc in primary_sa and primary_sb took identical decisions at every g (pattern shares, transitions, gap
    seconds and violation shares all equal)."""
    keys = ('pattern_share', 'transitions_per_day', 'gap_s_per_day', 'avoidable_violation_share', 'mean_cells_on')
    return all(r('mpc', mx, g, f'primary_{sa}')[k] == r('mpc', mx, g, f'primary_{sb}')[k] for g in G for k in keys)


def decision_groups(mx):
    groups = []
    for S in SC:
        for grp in groups:
            if same_decisions(mx, grp[0], S):
                grp.append(S)
                break
        else:
            groups.append([S])
    return [tuple(g_) for g_ in groups]


GROUPS = {mx: decision_groups(mx) for mx in MIXES}

# ---------------------------------------------------------------- style
INK, INK2, MUTED, GRID, AXIS = '#0b0b0b', '#52514e', '#898781', '#e1e0d9', '#52514e'
CRITICAL = '#d03b3b'
CTRL = {
    'mpc': dict(label='MPC (proposed)', color='#2a78d6', marker='o'),
    'exh': dict(label='Exhaustive online (H = 1)', color='#eb6834', marker='s'),
    'thr_ul': dict(label='Threshold, UL-aware', color='#1baf7a', marker='^'),
    'thr_dl': dict(label='Threshold, DL-only', color='#e34948', marker='v'),
    'tod': dict(label='Time-of-day schedule', color='#eda100', marker='D'),
    'oracle': dict(label='Oracle (zero violation)', color='#008300', marker='*'),
    'oracle_budget': dict(label='Oracle (1% budget)', color='#e87ba4', marker='P'),
    'oracle_test': dict(label='Oracle (test radio)', color='#6250d6', marker='X'),
}
SCN_COL = {'S0': '#6da7ec', 'S1': '#2a78d6', 'S2': '#104281'}


def grp_style(grp):
    if grp == ('S0', 'S1', 'S2'):
        return '#2a78d6', '^'
    if grp == ('S1', 'S2'):
        return '#104281', 's'
    if len(grp) == 1:
        return SCN_COL[grp[0]], {'S0': 'o', 'S1': 's', 'S2': '^'}[grp[0]]
    return '#2a78d6', 'D'


def grp_label(grp):
    return ', '.join(grp) + (' (same decisions)' if len(grp) > 1 else '')


MIXLAB = {'ericsson_typical': 'UL/DL 10/90', 'genai_2025': 'UL/DL 26/74'}
MIXS = {'ericsson_typical': '10/90', 'genai_2025': '26/74'}
MIX_LS = {'ericsson_typical': '-', 'genai_2025': '--'}
MIX_FILL = {'ericsson_typical': True, 'genai_2025': False}
NCELL_COL = ['#86b6ef', '#3987e5', '#1c5cab', '#0d366b']      # ordinal ramp, validated (--ordinal)
SCN_LAB = {'primary_S0': 'Primary, S0', 'primary_S1': 'Primary, S1', 'primary_S2': 'Primary, S2',
           'cat2_S0': 'Cat 2 sleep, S0', 'cat2_S1': 'Cat 2 sleep, S1', 'cat2_S2': 'Cat 2 sleep, S2',
           'ulsens_S0': 'UL-load term, S0', 'ulsens_S1': 'UL-load term, S1', 'ulsens_S2': 'UL-load term, S2',
           'left_S0': '"Left" 3.5 GHz, S0', 'set3_S0': 'Set 3 ratios, S0', 'mrf64_S0': r'$M_\mathrm{RF}$ = 64, S0',
           'mrf128_S0': r'$M_\mathrm{RF}$ = 128, S0', 'mrf1024_S0': r'$M_\mathrm{RF}$ = 1024, S0'}
PLAIN = {'primary': 'primary', 'cat2': 'Cat 2 sleep', 'ulsens': 'UL-load term', 'left': '"left" 3.5 GHz case',
         'set3': 'Set 3 ratios', 'mrf64': 'M_RF = 64', 'mrf128': 'M_RF = 128', 'mrf1024': 'M_RF = 1024'}


def plain_scn(s):
    base, S = s.rsplit('_', 1)
    return f'{PLAIN[base]}, {S}'


DOUBLE = 7.16          # IEEE double-column width, inches
MIN_PT = 7.0

FONT_PATH = font_manager.findfont(font_manager.FontProperties(family='STIXGeneral'), fallback_to_default=False)
plt.rcParams.update({
    'font.family': 'STIXGeneral', 'mathtext.fontset': 'stix', 'font.size': 8,
    'axes.labelsize': 8, 'axes.titlesize': 8, 'xtick.labelsize': 7.5, 'ytick.labelsize': 7.5,
    'legend.fontsize': 7.5, 'axes.edgecolor': AXIS, 'axes.linewidth': 0.6, 'axes.labelcolor': INK,
    'text.color': INK, 'xtick.color': AXIS, 'ytick.color': AXIS, 'xtick.labelcolor': INK, 'ytick.labelcolor': INK,
    'xtick.major.width': 0.6, 'ytick.major.width': 0.6, 'xtick.major.size': 2.5, 'ytick.major.size': 2.5,
    'xtick.minor.size': 1.5, 'ytick.minor.size': 1.5, 'xtick.minor.width': 0.4, 'ytick.minor.width': 0.4,
    'axes.spines.top': False, 'axes.spines.right': False, 'axes.grid': True, 'grid.color': GRID,
    'grid.linewidth': 0.5, 'grid.linestyle': '-', 'axes.axisbelow': True, 'lines.linewidth': 1.1,
    'lines.markersize': 4, 'legend.frameon': False, 'legend.handlelength': 2.2, 'legend.borderaxespad': 0.2,
    'pdf.fonttype': 42, 'ps.fonttype': 42, 'savefig.dpi': 600, 'figure.dpi': 100, 'hatch.linewidth': 0.6,
    'figure.facecolor': 'white', 'axes.facecolor': 'white', 'savefig.facecolor': 'white',
})

# ---------------------------------------------------------------- records and helpers
SOURCE = {}      # figure -> list of rows (CSV)
FACTS = {}       # caption facts: name -> formatted string
ENCODING = {}    # figure -> colour-encoding checks


def rec(fig, panel, series, quantity, unit, value, lo=None, hi=None, ctrl='', mix='', scn='', g='', source=''):
    SOURCE.setdefault(fig, []).append(dict(figure=fig, panel=panel, series=series, quantity=quantity, unit=unit,
                                           value=value, lo=lo, hi=hi, controller=ctrl, mix=mix, scenario=scn,
                                           g=g, source=source))


def tag(artist, gid):
    artist.set_gid(gid)
    return artist


def describe(fig, axes, text, row=0):
    fig._descs = getattr(fig, '_descs', []) + [(list(axes), text, row)]


def place_descriptions(fig, gap_pt=3.0):
    """Panel descriptions sit under each panel; panels in one row share one baseline."""
    fig.canvas.draw()
    rnd = fig.canvas.get_renderer()
    inv = fig.transFigure.inverted()
    low = {}
    for axes, _, row in fig._descs:
        y0 = min(a.get_tightbbox(rnd).y0 for a in axes)
        low[row] = min(low.get(row, y0), y0)
    for axes, text, row in fig._descs:
        x0 = min(a.get_position().x0 for a in axes)
        x1 = max(a.get_position().x1 for a in axes)
        yf = inv.transform((0, low[row]))[1] - gap_pt / 72 / fig.get_figheight()
        fig.text((x0 + x1) / 2, yf, text, ha='center', va='top', fontsize=8)


def line(ax, gid, x, y, **kw):
    (ln,) = ax.plot(x, y, **kw)
    return tag(ln, gid)


def vci(ax, gid, x, lo, hi, color, cap=0.04):
    lc = LineCollection([[(xi, l_), (xi, h_)] for xi, l_, h_ in zip(x, lo, hi)], colors=color, linewidths=0.7, zorder=2)
    ax.add_collection(lc, autolim=False)
    cl = LineCollection([[(xi - cap, v), (xi + cap, v)] for xi, l_, h_ in zip(x, lo, hi) for v in (l_, h_)],
                        colors=color, linewidths=0.7, zorder=2)
    ax.add_collection(cl, autolim=False)
    return tag(lc, gid)


def hci(ax, gid, y, lo, hi, color, cap=0.12):
    lc = LineCollection([[(l_, yi), (h_, yi)] for yi, l_, h_ in zip(y, lo, hi)], colors=color, linewidths=0.7, zorder=2)
    ax.add_collection(lc, autolim=False)
    caps = LineCollection([[(v, yi - cap), (v, yi + cap)] for yi, l_, h_ in zip(y, lo, hi) for v in (l_, h_)],
                          colors=color, linewidths=0.7, zorder=2)
    ax.add_collection(caps, autolim=False)
    return tag(lc, gid)


BUDGET_LS = (0, (4, 2))


def hbudget(ax, gid):
    return line(ax, gid, list(ax.get_xlim()), [1.0, 1.0], color=INK2, lw=0.8, ls=BUDGET_LS, zorder=1.5)


def vbudget(ax, gid):
    return line(ax, gid, [1.0, 1.0], list(ax.get_ylim()), color=INK2, lw=0.8, ls=BUDGET_LS, zorder=1.5)


def gtick(ax, values=G):
    ax.set_xticks(list(values))
    ax.set_xticklabels([f'{v:g}' for v in values])


# ---------------------------------------------------------------- Figure 4 (working 5): activation behaviour
def fig4():
    fid = 'fig4_activation_w5'
    fig = plt.figure(figsize=(DOUBLE, 2.7))
    gs = fig.add_gridspec(2, 2, width_ratios=[1.45, 1.0], left=0.065, right=0.975, top=0.85, bottom=0.25,
                          wspace=0.26, hspace=0.18)
    ax = fig.add_subplot(gs[:, 0])
    used = set()
    pos = {(mx, g): i + j * (len(G) + 1) for j, mx in enumerate(MIXES) for i, g in enumerate(G)}
    for mx in MIXES:
        for g in G:
            ps = r('mpc', mx, g, 'primary_S0')['pattern_share']
            assert abs(sum(ps.values()) - 1) < 1e-9
            by_k = [0.0] * 4
            for p, v in ps.items():
                by_k[sum(int(c) for c in p.strip('()').split(','))] += v
            bottom = 0.0
            for k in range(4):
                h = 100 * by_k[k]
                tag(ax.bar(pos[(mx, g)], h, bottom=bottom, width=0.78, color=NCELL_COL[k], edgecolor='white',
                           linewidth=0.5)[0], f'f4a|{mx}|{g}|{k}')
                rec(fid, 'a', f'{k} active 7.2 GHz cells', 'share of time', '%', h, ctrl='mpc', mix=mx,
                    scn='primary_S0', g=g, source=f'P3.4 results_test["mpc|{mx}|{g}|primary_S0"].pattern_share')
                bottom += h
    for mx in MIXES:
        for g in G:
            for S in SC:
                used |= {p for p, v in r('mpc', mx, g, f'primary_{S}')['pattern_share'].items() if v > 0}
    ax.set_ylim(0, 100)
    ax.set_yticks([0, 25, 50, 75, 100])
    ax.set_xticks([pos[(mx, g)] for mx in MIXES for g in G])
    ax.set_xticklabels([f'{g:g}' for mx in MIXES for g in G])
    ax.set_xlim(-0.6, pos[(MIXES[1], G[-1])] + 0.6)
    ax.grid(axis='x', visible=False)
    ax.set_ylabel('Share of time (%)')
    for mx in MIXES:
        mid = (pos[(mx, G[0])] + pos[(mx, G[-1])]) / 2
        ax.annotate(f'Traffic growth $g$, {MIXLAB[mx]}', xy=(mid, 0), xycoords=('data', 'axes fraction'),
                    xytext=(0, -14), textcoords='offset points', ha='center', va='top', fontsize=8)
    handles = [Patch(facecolor=NCELL_COL[k], edgecolor='white', label=lab) for k, lab in
               enumerate(['0 (3.5 GHz only)', '1 cell', '2 cells', '3 cells (all on)'])]
    ax.legend(handles=handles, ncol=4, loc='lower left', bbox_to_anchor=(0.0, 1.01), columnspacing=1.0,
              handlelength=1.2, title='Active 7.2 GHz cells per site', title_fontsize=7.5, alignment='left')
    describe(fig, [ax], '(a) Time share by active 7.2 GHz cells, MPC, S0')
    FACTS['f4_patterns_used'] = ', '.join(sorted(p.replace('(', '').replace(')', '').replace(', ', '') for p in used))

    axt, axb = fig.add_subplot(gs[0, 1]), fig.add_subplot(gs[1, 1])
    leg = []
    for mx in MIXES:
        for grp in GROUPS[mx]:
            scn = f'primary_{grp[0]}'
            cells = [r('mpc', mx, g, scn)['mean_cells_on'] for g in G]
            trans = [r('mpc', mx, g, scn)['transitions_per_day'] for g in G]
            col, _ = grp_style(grp)
            mk = 'o' if MIX_FILL[mx] else 's'
            kw = dict(color=col, ls=MIX_LS[mx], marker=mk, ms=3.5, mfc=col if MIX_FILL[mx] else 'white', mew=0.9,
                      lw=1.0)
            gtag = ''.join(grp)
            line(axt, f'f4b|cells|{mx}|{gtag}', list(G), cells, **kw)
            line(axb, f'f4b|trans|{mx}|{gtag}', list(G), trans, **kw)
            leg.append(Line2D([], [], label=f'{MIXS[mx]}, {", ".join(grp)}', **kw))
            for g, c_, t_ in zip(G, cells, trans):
                src = f'P3.4 results_test["mpc|{mx}|{g}|{scn}"]'
                rec(fid, 'b', '/'.join(grp), 'mean active 7.2 GHz cells', 'cells', c_, ctrl='mpc', mix=mx, scn=scn,
                    g=g, source=src + '.mean_cells_on')
                rec(fid, 'b', '/'.join(grp), 'sleep + wake transitions per site-day', '1/day', t_, ctrl='mpc', mix=mx,
                    scn=scn, g=g, source=src + '.transitions_per_day')
    for a in (axt, axb):
        a.set_xlim(0.3, 3.7)
        gtick(a)
    axt.set_xticklabels([])
    axt.set_ylim(0, 3)
    axt.set_yticks([0, 1, 2, 3])
    axt.set_ylabel('Active cells')
    tmax = max(r('mpc', mx, g, f'primary_{S}')['transitions_per_day'] for mx in MIXES for g in G for S in SC)
    assert tmax < 35
    axb.set_ylim(0, 35)
    axb.set_yticks([0, 10, 20, 30])
    axb.set_ylabel('Transitions / day')
    axb.set_xlabel('Traffic growth $g$')
    axt.legend(handles=leg, ncol=2, loc='lower left', bbox_to_anchor=(0.0, 1.01), columnspacing=1.0,
               handlelength=2.4)
    describe(fig, [axt, axb], '(b) Active cells and sleep/wake transitions, MPC')
    FACTS['f4_max_transitions'] = f'{tmax:.1f}'
    return fid, fig


def groups_fact():
    parts = []
    for mx in MIXES:
        multi = [' = '.join(g_) for g_ in GROUPS[mx] if len(g_) > 1]
        parts.append(f'{MIXS[mx]}: {"; ".join(multi) if multi else "none"}')
    return '; '.join(parts)


# ---------------------------------------------------------------- Figure 5 (working 6): held-out savings and violations
def fig5():
    fid = 'fig5_heldout_savings_w6'
    fig = plt.figure(figsize=(DOUBLE, 4.9))
    gs = fig.add_gridspec(2, 2, left=0.075, right=0.985, top=0.975, bottom=0.12, wspace=0.22, hspace=0.52)
    letters = iter('abcd')
    vmax = 0.0
    for j, mx in enumerate(MIXES):
        ax = fig.add_subplot(gs[0, j])
        for S in SC:
            scn = f'primary_{S}'
            sv = [r('mpc', mx, g, scn)['saving_vs_aon'] for g in G]
            assert all(r('mpc', mx, g, scn)['saving_qualified'] for g in G), 'unqualified primary mpc cell'
            y = [100 * s['point'] for s in sv]
            lo, hi = [100 * s['lo'] for s in sv], [100 * s['hi'] for s in sv]
            col, mk = SCN_COL[S], {'S0': 'o', 'S1': 's', 'S2': '^'}[S]
            vci(ax, f'f5|ci|{mx}|{S}', list(G), lo, hi, col)
            line(ax, f'f5|sav|{mx}|{S}', list(G), y, color=col, marker=mk, ms=3.5, lw=1.1, label=S, zorder=3)
            for g, v, l_, h_ in zip(G, y, lo, hi):
                rec(fid, 'ab'[j], S, 'saving vs all-on', '%', v, l_, h_, ctrl='mpc', mix=mx, scn=scn, g=g,
                    source=f'P3.4 results_test["mpc|{mx}|{g}|{scn}"].saving_vs_aon')
        ax.set_xlim(0.3, 3.7)
        gtick(ax)
        ax.set_ylim(0, 50)
        ax.set_yticks([0, 10, 20, 30, 40, 50])
        ax.set_xlabel('Traffic growth $g$')
        ax.set_ylabel('Saving vs all-on (%)')
        ax.legend(title='Energy scenario', title_fontsize=7.5, loc='upper right', ncol=3, columnspacing=1.0,
                  handlelength=2.0)
        describe(fig, [ax], f'({next(letters)}) Energy saving, {MIXLAB[mx]}', row=0)
    for j, mx in enumerate(MIXES):
        ax = fig.add_subplot(gs[1, j])
        ax.set_xlim(0.3, 3.7)
        ax.set_ylim(0, 1.3)
        unav = [g for g in G if r('aon', mx, g, 'primary_S0')['unavoidable_share']['all_user'] > BUDGET]
        if unav:
            band = Rectangle((min(unav) - 0.15, 0), max(unav) - min(unav) + 0.30, 1.3, facecolor='#f0efec',
                             edgecolor='none', zorder=0.5)
            ax.add_patch(tag(band, f'f5|unavband|{mx}'))
            ax.text(min(unav) - 0.10, 1.27, 'unavoidable share > 1%\n(Fig. 7)', ha='left', va='top', fontsize=7.5,
                    color=INK2)
        for grp in GROUPS[mx]:
            scn = f'primary_{grp[0]}'
            y = [100 * viol('mpc', mx, g, scn) for g in G]
            vmax = max(vmax, max(y))
            col, mk = grp_style(grp)
            line(ax, f'f5|viol|{mx}|{"".join(grp)}', list(G), y, color=col, marker=mk, ms=3.5, lw=1.1,
                 label=grp_label(grp), zorder=3)
            for g, v in zip(G, y):
                rec(fid, 'cd'[j], '/'.join(grp), 'avoidable violation share (all-user)', '%', v, ctrl='mpc', mix=mx,
                    scn=scn, g=g, source=f'P3.4 results_test["mpc|{mx}|{g}|{scn}"].avoidable_violation_share.all_user')
        hbudget(ax, f'f5|budget|{mx}')
        ax.text(0.35, 1.03, '1% avoidable-violation budget', ha='left', va='bottom', fontsize=7.5, color=INK2)
        gtick(ax)
        ax.set_yticks([0, 0.25, 0.5, 0.75, 1.0, 1.25])
        ax.set_xlabel('Traffic growth $g$')
        ax.set_ylabel('Avoidable violation share (%)')
        ax.legend(loc='lower right', bbox_to_anchor=(1.0, 0.16), handlelength=2.0)
        describe(fig, [ax], f'({next(letters)}) Avoidable violation share, {MIXLAB[mx]}', row=1)
    FACTS['f5_max_violation'] = f'{vmax:.2f}'
    FACTS['f45_same_decisions'] = groups_fact()
    for S in ('S0', 'S2'):
        v = [100 * r('mpc', mx, g, f'primary_{S}')['saving_vs_aon']['point'] for mx in MIXES for g in (1.0, 2.0)]
        FACTS[f'f5_h1_{S}'] = f'{min(v):.2f}–{max(v):.2f}'
    return fid, fig


# ---------------------------------------------------------------- Figure 6 (working 7): DL-only consequences
def fig6():
    fid = 'fig6_dl_only_w7'
    mx = 'genai_2025'
    fig = plt.figure(figsize=(DOUBLE, 3.25))
    gs = fig.add_gridspec(2, 2, width_ratios=[1.05, 1.0], left=0.115, right=0.985, top=0.735, bottom=0.21,
                          wspace=0.30, hspace=0.20)
    ax = fig.add_subplot(gs[:, 0])
    rows = [(g, grp) for g in (1.0, 1.5, 2.0) for grp in (('S0',), ('S1', 'S2'))]
    ylab = []
    for i, (g, grp) in enumerate(rows):
        yv = len(rows) - 1 - i
        scn = f'primary_{grp[0]}'
        vals = {'dl': viol('thr_dl', mx, g, scn, 'dl_only_all_user'), 'all': viol('thr_dl', mx, g, scn),
                'thr_ul': viol('thr_ul', mx, g, scn), 'mpc': viol('mpc', mx, g, scn)}
        for S in grp[1:]:
            assert vals == {'dl': viol('thr_dl', mx, g, f'primary_{S}', 'dl_only_all_user'),
                            'all': viol('thr_dl', mx, g, f'primary_{S}'), 'thr_ul': viol('thr_ul', mx, g, f'primary_{S}'),
                            'mpc': viol('mpc', mx, g, f'primary_{S}')}, 'S1 and S2 differ in Fig. 6(a)'
        assert min(vals.values()) > 0, 'zero value cannot be drawn on a log axis'
        p = {k: 100 * v for k, v in vals.items()}
        gtag = ''.join(grp)
        line(ax, f'f6a|span|{g}|{gtag}', [p['dl'], p['all']], [yv, yv], color=CTRL['thr_dl']['color'], lw=1.0, zorder=2)
        line(ax, f'f6a|thr_dl_dlcheck|{g}|{gtag}', [p['dl']], [yv], ls='none', marker='v', ms=5, mfc='white',
             mec=CTRL['thr_dl']['color'], mew=1.0, zorder=3)
        line(ax, f'f6a|thr_dl|{g}|{gtag}', [p['all']], [yv], ls='none', marker='v', ms=5,
             color=CTRL['thr_dl']['color'], zorder=3)
        line(ax, f'f6a|thr_ul|{g}|{gtag}', [p['thr_ul']], [yv + 0.25], ls='none', marker='^', ms=4.5,
             color=CTRL['thr_ul']['color'], zorder=3)
        line(ax, f'f6a|mpc|{g}|{gtag}', [p['mpc']], [yv - 0.25], ls='none', marker='o', ms=4,
             color=CTRL['mpc']['color'], zorder=3)
        ylab.append((yv, f'$g$ = {g:g}, {", ".join(grp)}'))
        for k, lab, crit, c_ in (('dl', 'thr_dl, own DL-only check', 'dl_only_all_user', 'thr_dl'),
                                 ('all', 'thr_dl, all-user (DL + UL)', 'all_user', 'thr_dl'),
                                 ('thr_ul', 'thr_ul, all-user', 'all_user', 'thr_ul'),
                                 ('mpc', 'mpc, all-user', 'all_user', 'mpc')):
            rec(fid, 'a', lab, 'avoidable violation share', '%', p[k], ctrl=c_, mix=mx, scn='/'.join(grp), g=g,
                source=f'P3.4 results_test["{c_}|{mx}|{g}|{scn}"].avoidable_violation_share.{crit}')
    ax.set_xscale('log')
    ax.set_xlim(1e-3, 100)
    ax.set_ylim(-0.6, len(rows) - 0.4)
    vbudget(ax, 'f6a|budget')
    ax.set_xticks([1e-3, 1e-2, 1e-1, 1, 10, 100])
    ax.set_xticklabels(['0.001', '0.01', '0.1', '1', '10', '100'])
    ax.xaxis.set_minor_formatter(NullFormatter())
    ax.set_yticks([y for y, _ in ylab])
    ax.set_yticklabels([t for _, t in ylab])
    ax.grid(axis='y', visible=False)
    ax.set_xlabel('Avoidable violation share (%), log scale')
    handles = [Line2D([], [], ls='none', marker='v', ms=5, mfc='white', mec=CTRL['thr_dl']['color'], mew=1.0,
                      label='Threshold, DL-only: its own DL-only check'),
               Line2D([], [], ls='none', marker='v', ms=5, color=CTRL['thr_dl']['color'],
                      label='Threshold, DL-only: all-user (DL + UL)'),
               Line2D([], [], ls='none', marker='^', ms=4.5, color=CTRL['thr_ul']['color'],
                      label='Threshold, UL-aware: all-user'),
               Line2D([], [], ls='none', marker='o', ms=4, color=CTRL['mpc']['color'], label='MPC: all-user'),
               Line2D([], [], color=INK2, lw=0.8, ls=BUDGET_LS, label='1% avoidable-violation budget')]
    ax.legend(handles=handles, ncol=1, loc='lower left', bbox_to_anchor=(-0.02, 1.01), handletextpad=0.4)
    describe(fig, [ax], '(a) Violation share: DL-only check vs all-user, UL/DL 26/74')
    h2 = [100 * PAIRED[f'{mx}|{g}|primary_{S}']['viol thr_dl-thr_ul']['point'] for g in (1.0, 1.5, 2.0) for S in SC]
    FACTS['f6_h2_range'] = f'{min(h2):.2f}–{max(h2):.2f}'
    dlmax = max(100 * viol('thr_dl', mx, g, f'primary_{S}', 'dl_only_all_user') for g in (1.0, 1.5, 2.0) for S in SC)
    FACTS['f6_dlcheck_max'] = f'{dlmax:.2f}'

    ctrls = ('thr_dl', 'thr_ul', 'mpc', 'oracle')
    w = 0.19
    axes_b = []
    for i_s, S in enumerate(('S0', 'S2')):
        axb = fig.add_subplot(gs[i_s, 1])
        axes_b.append(axb)
        scn = f'primary_{S}'
        lim, step = (18, 1.0) if S == 'S0' else (70, 3.0)
        for gi, g in enumerate((1.0, 1.5, 2.0)):
            for ci, c in enumerate(ctrls):
                x = gi + (ci - 1.5) * w
                rr = r(c, mx, g, scn)
                sv, ok, col = rr['saving_vs_aon'], rr['saving_qualified'], CTRL[c]['color']
                b = axb.bar(x, 100 * sv['point'], width=w * 0.88, color=col if ok else 'white', edgecolor=col,
                            linewidth=0.7, hatch=None if ok else '//////', zorder=2)[0]
                tag(b, f'f6b|{S}|{c}|{g}')
                vci(axb, f'f6b|ci|{S}|{c}|{g}', [x], [100 * sv['lo']], [100 * sv['hi']], INK2)
                assert 100 * sv['hi'] < lim * 0.8
                if not ok:
                    t = axb.text(x, 100 * sv['hi'] + step, f'{100 * viol(c, mx, g, scn):.1f}%', ha='center',
                                 va='bottom', rotation=90, fontsize=7, color=INK2)
                    tag(t, f'f6b|txt|{S}|{c}|{g}')
                rec(fid, 'b', c, 'saving vs all-on', '%', 100 * sv['point'], 100 * sv['lo'], 100 * sv['hi'], ctrl=c,
                    mix=mx, scn=scn, g=g, source=f'P3.4 results_test["{c}|{mx}|{g}|{scn}"].saving_vs_aon')
                rec(fid, 'b', c, 'budget met (saving qualified)', 'bool', bool(ok), ctrl=c, mix=mx, scn=scn, g=g,
                    source=f'P3.4 results_test["{c}|{mx}|{g}|{scn}"].saving_qualified')
        axb.set_xlim(-0.5, 2.5)
        axb.set_xticks([0, 1, 2])
        axb.grid(axis='x', visible=False)
        axb.set_ylim(0, lim)
        axb.set_yticks([0, 5, 10, 15] if S == 'S0' else [0, 20, 40, 60])
        axb.text(0.995, 0.97, S, transform=axb.transAxes, ha='right', va='top', fontsize=8)
        axb.set_ylabel('Saving (%)')
    axes_b[0].set_xticklabels([])
    axes_b[1].set_xticklabels(['$g$ = 1', '$g$ = 1.5', '$g$ = 2'])
    hb = [Patch(facecolor=CTRL[c]['color'], edgecolor=CTRL[c]['color'], label=CTRL[c]['label']) for c in ctrls]
    hb.append(Patch(facecolor='white', edgecolor=INK2, hatch='//////', label='fails the 1% budget'))
    axes_b[0].legend(handles=hb, ncol=2, loc='lower left', bbox_to_anchor=(-0.02, 1.01), columnspacing=0.8,
                     handlelength=1.4, handletextpad=0.4)
    describe(fig, axes_b, '(b) Saving vs all-on, UL/DL 26/74 (label: violation share)')
    return fid, fig


# ---------------------------------------------------------------- Figure 7 (working 3 + 8): capacity and boundary
def fig7():
    fid = 'fig7_capacity_boundary_w3w8'
    fig = plt.figure(figsize=(DOUBLE, 3.1))
    gs = fig.add_gridspec(1, 2, width_ratios=[1.0, 1.0], left=0.07, right=0.985, top=0.74, bottom=0.25, wspace=0.24)
    ax = fig.add_subplot(gs[0, 0])
    pats = [tuple(S3['patterns'][str(i)]) for i in range(8)]
    mix_col = {'ericsson_typical': INK2, 'genai_2025': '#b3b1a8'}
    w = 0.36
    for i, p in enumerate(pats):
        ps = str(p)
        for j, mx in enumerate(MIXES):
            v = S3['max_capacity_pattern'][f'{mx}|-3.0']['x_star_both_all_by_pattern'][ps]
            tag(ax.bar(i + (j - 0.5) * w, v, width=w * 0.92, color=mix_col[mx], edgecolor='white', linewidth=0.5,
                       zorder=2)[0], f'f7a|both|{mx}|{i}')
            rec(fid, 'a', f'x* DL+UL, {MIXLAB[mx]}', 'sustainable traffic', 'x/T_0.5', v, mix=mx,
                scn='50 dBm (dp -3 dB)', g=ps,
                source=f'P3.3 max_capacity_pattern["{mx}|-3.0"].x_star_both_all_by_pattern["{ps}"]')
        vdl = S3['x_star_per_pattern'][f'DL|{i}|-3.0|all']['x_star']
        line(ax, f'f7a|dl|{i}', [i - w, i + w], [vdl, vdl], color=INK, lw=1.4, solid_capstyle='butt', zorder=3)
        rec(fid, 'a', 'x* DL only (ignores UL)', 'sustainable traffic', 'x/T_0.5', vdl, scn='50 dBm (dp -3 dB)', g=ps,
            source=f'P3.3 x_star_per_pattern["DL|{i}|-3.0|all"].x_star')
    ax.set_xticks(range(8))
    ax.set_xticklabels([''.join(str(c) for c in p) for p in pats])
    ax.set_xlim(-0.6, 7.6)
    ax.set_ylim(0, 4)
    ax.set_yticks([0, 1, 2, 3, 4])
    ax.grid(axis='x', visible=False)
    ax.set_xlabel('Activation pattern (sectors 1, 2, 3; 1 = 7.2 GHz cell on)')
    ax.set_ylabel('Sustainable traffic $x^*$ ($\\times T_{0.5}$)')
    hh = [Patch(facecolor=mix_col[mx], edgecolor='white', label=f'{MIXLAB[mx]}, DL + UL') for mx in MIXES]
    hh.append(Line2D([], [], color=INK, lw=1.4, label='DL-only limit (ignores UL)'))
    ax.legend(handles=hh, ncol=2, loc='lower left', bbox_to_anchor=(-0.01, 1.01), columnspacing=0.9,
              handlelength=1.6, handletextpad=0.4)
    describe(fig, [ax], '(a) Sustainable traffic per activation pattern (typical site)')

    axb = fig.add_subplot(gs[0, 1])
    axb.set_yscale('symlog', linthresh=1.0, linscale=1.0)
    u = lambda mx_, g_: 100 * r('aon', mx_, g_, 'primary_S0')['unavoidable_share']['all_user']
    for mx in MIXES:
        col = INK if mx == 'genai_2025' else '#7a7872'
        mk = 's' if mx == 'genai_2025' else 'o'
        yt = [u(mx, g) for g in G]
        yd = [100 * D3[f'aon|{mx}|{g}|primary_S0']['unavoidable_share']['all_user'] for g in G]
        line(axb, f'f7b|test|{mx}', list(G), yt, color=col, marker=mk, ms=3.8, lw=1.1, zorder=3)
        line(axb, f'f7b|dev|{mx}', list(G), yd, color=col, marker=mk, ms=3.8, mfc='white', lw=0.9,
             ls=(0, (3, 1.5)), zorder=3)
        xs = S3['max_capacity_pattern'][f'{mx}|-3.0']['x_star_both_all_by_pattern']['(1, 1, 1)']
        line(axb, f'f7b|xstar|{mx}', [xs, xs], [0, 60], color=col, lw=0.8, ls=(0, (1, 1.5)), zorder=1.5)
        axb.text(xs - 0.04, 55 if mx == 'genai_2025' else 3.0, f'$g$ = {xs:.2f}', ha='right', va='top', fontsize=7.5,
                 color=col)
        for g, a_, b_ in zip(G, yt, yd):
            rec(fid, 'b', f'test, {MIXLAB[mx]}', 'unavoidable share (all-user)', '%', a_, mix=mx, scn='primary_S0',
                g=g, ctrl='aon', source=f'P3.4 results_test["aon|{mx}|{g}|primary_S0"].unavoidable_share.all_user')
            rec(fid, 'b', f'development, {MIXLAB[mx]}', 'unavoidable share (all-user)', '%', b_, mix=mx,
                scn='primary_S0', g=g, ctrl='aon',
                source=f'P3.3 results_dev["aon|{mx}|{g}|primary_S0"].unavoidable_share.all_user')
        rec(fid, 'b', f'g = x*(all on), {MIXLAB[mx]}', 'growth at which the development busy hour reaches x*', 'g',
            xs, mix=mx, source=f'P3.3 max_capacity_pattern["{mx}|-3.0"].x_star_both_all_by_pattern["(1, 1, 1)"]')
    for mx, g, dx, dy, ha, va in (('genai_2025', 2.0, -5, 3, 'right', 'bottom'),
                                  ('genai_2025', 2.5, 6, -7, 'left', 'top'),
                                  ('ericsson_typical', 3.0, -4, 4, 'right', 'bottom')):
        t = axb.annotate(f'{u(mx, g):.2f}%', xy=(g, u(mx, g)), xytext=(dx, dy), textcoords='offset points', ha=ha,
                         va=va, fontsize=7.5, color=INK2)
        tag(t, f'f7b|txt|{mx}|{g}')
    line(axb, 'f7b|h4', [0.3, 4.1], [1.0, 1.0], color=INK2, lw=0.8, ls=BUDGET_LS, zorder=1.5)
    axb.set_xlim(0.3, 4.1)
    axb.set_ylim(0, 60)
    gtick(axb, (0.5, 1, 1.5, 2, 2.5, 3, 3.5, 4))
    axb.set_yticks([0, 0.25, 0.5, 0.75, 1, 10, 50])
    axb.set_yticklabels(['0', '0.25', '0.5', '0.75', '1', '10', '50'])
    axb.yaxis.set_minor_locator(NullLocator())
    axb.set_xlabel('Traffic growth $g$')
    axb.set_ylabel('Unavoidable share (%)')
    hb = [Line2D([], [], color=INK, marker='s', ms=3.8, lw=1.1, label='26/74, test week'),
          Line2D([], [], color=INK, marker='s', ms=3.8, mfc='white', lw=0.9, ls=(0, (3, 1.5)), label='26/74, development'),
          Line2D([], [], color='#7a7872', marker='o', ms=3.8, lw=1.1, label='10/90, test week'),
          Line2D([], [], color='#7a7872', marker='o', ms=3.8, mfc='white', lw=0.9, ls=(0, (3, 1.5)),
                 label='10/90, development'),
          Line2D([], [], color=INK2, lw=0.8, ls=BUDGET_LS, label='1% (H4 threshold)'),
          Line2D([], [], color=INK2, lw=0.8, ls=(0, (1, 1.5)), label='busy hour at all-on $x^*$')]
    axb.legend(handles=hb, ncol=2, loc='lower left', bbox_to_anchor=(-0.02, 1.01), columnspacing=0.8,
               handlelength=1.9, handletextpad=0.3)
    describe(fig, [axb], '(b) Unavoidable share (linear below 1%, log above)')
    xa = S3['max_capacity_pattern']
    FACTS['f7_xstar_all_on_1090'] = f"{xa['ericsson_typical|-3.0']['x_star_both_all_by_pattern']['(1, 1, 1)']:.2f}"
    FACTS['f7_xstar_all_on_2674'] = f"{xa['genai_2025|-3.0']['x_star_both_all_by_pattern']['(1, 1, 1)']:.2f}"
    FACTS['f7_xstar_dl_all_on'] = f"{S3['x_star_per_pattern']['DL|7|-3.0|all']['x_star']:.2f}"
    FACTS['f7_u_2674_g2'] = f"{u('genai_2025', 2.0):.2f}"
    FACTS['f7_u_2674_g25'] = f"{u('genai_2025', 2.5):.2f}"
    FACTS['f7_u_1090_g3'] = f"{u('ericsson_typical', 3.0):.2f}"
    return fid, fig


# ---------------------------------------------------------------- Figure 8 (working 9): comparison and robustness
SCAT_CELL = ('genai_2025', 2.0, 'primary_S2')
SCAT_CTRL = ('mpc', 'exh', 'thr_ul', 'thr_dl', 'tod', 'oracle', 'oracle_budget')
# label offsets in points: (dx, dy, ha, va, leader line)
SCAT_LAB = {'oracle': (6, 6, 'left', 'bottom', False), 'oracle_budget': (2, 12, 'right', 'bottom', True),
            'mpc': (-6, 0, 'right', 'center', False), 'exh': (-3, -13, 'right', 'top', True),
            'thr_ul': (-2, -30, 'right', 'top', True), 'tod': (5, 0, 'left', 'center', False),
            'thr_dl': (-4, -6, 'right', 'top', False)}
SCAT_RELPOS = {'oracle_budget': (1.0, 0.0), 'exh': (1.0, 1.0), 'thr_ul': (1.0, 1.0)}
SCAT_TEXT = {'thr_dl': 'Threshold,\nDL-only'}
Q3_CELLS = [(mx, g, S) for S in ('S0', 'S2') for mx in MIXES for g in (1.0, 2.0)]
REFS = (('oracle', -0.22), ('oracle_budget', 0.0), ('oracle_test', 0.22))
HEAT_CMAP = LinearSegmentedColormap.from_list(
    'budget_diverging', [(0.0, '#1c5cab'), (0.25, '#86b6ef'), (0.5, '#f0efec'), (0.70, '#e34948'), (1.0, '#7d1a1a')])
HEAT_NORM = TwoSlopeNorm(vmin=0.0, vcenter=1.0, vmax=8.0)


def fig8():
    fid = 'fig8_comparison_robustness_w9'
    fig = plt.figure(figsize=(DOUBLE, 6.3))
    top = fig.add_gridspec(1, 4, width_ratios=[1.0, 0.45, 0.43, 1.05], left=0.075, right=0.985, top=0.955,
                           bottom=0.635, wspace=0.05)
    bot = fig.add_gridspec(1, 5, width_ratios=[1.55, 0.13, 0.05, 0.22, 0.95], left=0.135, right=0.985, top=0.46,
                           bottom=0.105, wspace=0.0)

    # (a) broken-axis scatter of frozen operating points
    mx, g, scn = SCAT_CELL
    axl, axr = fig.add_subplot(top[0, 0]), fig.add_subplot(top[0, 1])
    for c in SCAT_CTRL:
        rr = r(c, mx, g, scn)
        x, y = 100 * rr['avoidable_violation_share']['all_user'], 100 * rr['saving_vs_aon']['point']
        a = axl if x <= 1.25 else axr
        st = CTRL[c]
        line(a, f'f8a|{c}', [x], [y], ls='none', marker=st['marker'], ms=6.5 if st['marker'] == '*' else 4.8,
             color=st['color'], mec='white', mew=0.5, zorder=3)
        dx, dy, ha, va, lead = SCAT_LAB[c]
        a.annotate(SCAT_TEXT.get(c, st['label']), xy=(x, y), xytext=(dx, dy), textcoords='offset points', ha=ha,
                   va=va, fontsize=7,
                   color=INK, arrowprops=dict(arrowstyle='-', color=MUTED, lw=0.5, shrinkA=0, shrinkB=3,
                                              relpos=SCAT_RELPOS.get(c, (0.5, 0.5))) if lead else None)
        rec(fid, 'a', c, 'avoidable violation share', '%', x, ctrl=c, mix=mx, scn=scn, g=g,
            source=f'P3.4 results_test["{c}|{mx}|{g}|{scn}"].avoidable_violation_share.all_user')
        rec(fid, 'a', c, 'saving vs all-on', '%', y, ctrl=c, mix=mx, scn=scn, g=g,
            source=f'P3.4 results_test["{c}|{mx}|{g}|{scn}"].saving_vs_aon.point')
    axl.set_xlim(-0.08, 1.25)
    axr.set_xlim(1.5, 60)
    for a in (axl, axr):
        a.set_ylim(0, 36)
        a.set_yticks([0, 10, 20, 30])
    axr.tick_params(axis='y', length=0, labelleft=False)
    axr.spines['left'].set_visible(False)
    axl.set_xticks([0, 0.25, 0.5, 0.75, 1.0])
    axl.set_xticklabels(['0', '0.25', '0.5', '0.75', '1'])
    axr.set_xticks([20, 40, 60])
    vbudget(axl, 'f8a|budget')
    axl.text(1.0, 36.4, '1% budget', ha='center', va='bottom', fontsize=7, color=INK2)
    for a, xx, k in ((axl, 1.0, 1), (axr, 0.0, 1.0 / 0.45)):
        a.plot([xx - 0.012 * k, xx + 0.012 * k], [-0.03, 0.03], transform=a.transAxes, color=AXIS, clip_on=False,
               lw=0.6)
    axl.set_ylabel('Saving vs all-on (%)')
    axl.set_xlabel('Avoidable violation share (%)', x=0.72)
    describe(fig, [axl, axr], '(a) Operating points, UL/DL 26/74, $g$ = 2, S2', row=0)

    # (b) Q3 gaps with paired intervals
    axq = fig.add_subplot(top[0, 3])
    ylab = []
    for i, (mx_, g_, S) in enumerate(Q3_CELLS):
        yv = len(Q3_CELLS) - 1 - i
        key = f'{mx_}|{g_}|primary_{S}'
        for ref, off in REFS:
            pr = PAIRED[key][f'mpc-{ref}']
            st = CTRL[ref]
            line(axq, f'f8b|gap|{ref}|{key}', [100 * pr['point']], [yv + off], ls='none', marker=st['marker'],
                 ms=5.5 if st['marker'] == '*' else 4, color=st['color'], mec='white', mew=0.4, zorder=3)
            hci(axq, f'f8b|ci|{ref}|{key}', [yv + off], [100 * pr['lo']], [100 * pr['hi']], st['color'], cap=0.07)
            rec(fid, 'b', ref, 'gap (E_mpc - E_ref) / E_aon', 'points', 100 * pr['point'], 100 * pr['lo'],
                100 * pr['hi'], ctrl='mpc', mix=mx_, scn=f'primary_{S}', g=g_, source=f'P3.4 paired["{key}"]["mpc-{ref}"]')
        ylab.append((yv, f'{MIXS[mx_]}, $g$ = {g_:g}, {S}'))
    for S, (y0, y1) in (('S0', (3.6, 7.4)), ('S2', (-0.4, 3.4))):
        q3max = 100 * CFG['predictions']['Q3']['max_gap'][S]
        line(axq, f'f8b|limit|{S}', [q3max, q3max], [y0, y1], color=INK2, lw=0.8, ls=BUDGET_LS, zorder=1.5)
        axq.text(q3max + 0.08, y1 - 0.05, f'Q3 limit ({S})', ha='left', va='top', fontsize=7, color=INK2)
        rec(fid, 'b', f'Q3 limit {S}', 'registered max gap', 'points', q3max,
            source=f'configs/p3/p3_4_heldout.json predictions.Q3.max_gap.{S}')
    axq.set_ylim(-0.6, len(Q3_CELLS) - 0.4)
    axq.set_xlim(-0.2, 6.6)
    axq.set_xticks([0, 1, 2, 3, 4, 5, 6])
    axq.set_yticks([y for y, _ in ylab])
    axq.set_yticklabels([t for _, t in ylab])
    axq.grid(axis='y', visible=False)
    axq.set_xlabel('MPC energy gap (points of all-on energy)')
    hq = [Line2D([], [], ls='none', marker=CTRL[c]['marker'], ms=5.5 if c == 'oracle' else 4, color=CTRL[c]['color'],
                 label=CTRL[c]['label']) for c, _ in REFS]
    axq.legend(handles=hq, ncol=1, loc='upper right', handletextpad=0.3, handlelength=1.0)
    describe(fig, [axq], '(b) MPC gap to perfect-foresight references', row=0)

    # (c) heatmap of mpc avoidable violation share, all 196 cells
    axh, cax = fig.add_subplot(bot[0, 0]), fig.add_subplot(bot[0, 2])
    ncol = 2 * len(G) + 1
    Z = np.full((len(SCN_ORDER), ncol), np.nan)
    fails, enc_bad = [], []
    for i, scn_ in enumerate(SCN_ORDER):
        for j, mx_ in enumerate(MIXES):
            for k, g_ in enumerate(G):
                v = 100 * viol('mpc', mx_, g_, scn_)
                Z[i, k + j * (len(G) + 1)] = v
                rgb = HEAT_CMAP(HEAT_NORM(v))
                if (v < 1 and not rgb[2] > rgb[0]) or (v > 1 and not rgb[0] > rgb[2]):
                    enc_bad.append((scn_, mx_, g_, v))
                rec(fid, 'c', 'mpc', 'avoidable violation share', '%', v, ctrl='mpc', mix=mx_, scn=scn_, g=g_,
                    source=f'P3.4 results_test["mpc|{mx_}|{g_}|{scn_}"].avoidable_violation_share.all_user')
                if not r('mpc', mx_, g_, scn_)['saving_qualified']:
                    fails.append((i, k + j * (len(G) + 1), v, mx_, g_, scn_))
    mid = HEAT_CMAP(HEAT_NORM(1.0))
    mid_err = float(np.max(np.abs(np.array(mid[:3]) - np.array(matplotlib.colors.to_rgb('#f0efec')))))
    ENCODING[fid] = {'heatmap_cells_on_wrong_side_of_budget_colour': enc_bad,
                     'heatmap_colour_at_budget': to_hex(mid), 'neutral_anchor': '#f0efec',
                     'max_channel_error_at_budget': mid_err, 'heatmap_norm': 'TwoSlopeNorm(0, 1, 8)'}
    if enc_bad or mid_err > 0.02:
        raise SystemExit(f'heatmap colour encoding wrong: {enc_bad[:5]} mid {to_hex(mid)}')
    cm = HEAT_CMAP.copy()
    cm.set_bad('white')
    im = axh.imshow(np.ma.masked_invalid(Z), cmap=cm, norm=HEAT_NORM, aspect='auto', interpolation='nearest')
    tag(im, 'f8c|grid')
    for (i, j, v, mx_, g_, scn_) in fails:
        axh.add_patch(tag(Rectangle((j - 0.5, i - 0.5), 1, 1, fill=False, edgecolor=INK, linewidth=1.0, zorder=3),
                          f'f8c|fail|{scn_}|{mx_}|{g_}'))
        t = axh.text(j, i, f'{v:.1f}', ha='center', va='center', fontsize=7, color='white' if v > 3 else INK)
        tag(t, f'f8c|txt|{scn_}|{mx_}|{g_}')
    axh.set_yticks(range(len(SCN_ORDER)))
    axh.set_yticklabels([SCN_LAB[s] for s in SCN_ORDER])
    axh.set_xticks([k + j * (len(G) + 1) for j in range(2) for k in range(len(G))])
    axh.set_xticklabels([f'{g_:g}' for _ in range(2) for g_ in G])
    axh.tick_params(axis='both', length=0)
    axh.grid(False)
    for s in axh.spines.values():
        s.set_visible(False)
    for j, mx_ in enumerate(MIXES):
        axh.text(j * (len(G) + 1) + (len(G) - 1) / 2, -0.75, MIXLAB[mx_], ha='center', va='bottom', fontsize=8)
    axh.set_xlabel('Traffic growth $g$')
    cb = fig.colorbar(im, cax=cax)
    cb.set_ticks([0, 0.5, 1, 2, 4, 8])
    cb.set_ticklabels(['0', '0.5', '1', '2', '4', '8'])
    cb.ax.tick_params(labelsize=7.5, length=2)
    cb.outline.set_linewidth(0.5)
    cb.ax.axhline(1.0, color=INK, lw=0.9)
    cb.set_label('Avoidable violation share (%); line: 1% budget', fontsize=8)
    cb.ax.yaxis.set_label_position('left')
    describe(fig, [axh], '(c) MPC avoidable violation share, all 196 cells', row=1)

    # (d) mpc saving at g = 2 across energy scenarios, rows aligned with (c)
    axd = fig.add_subplot(bot[0, 4])
    gd = 2.0
    for i, scn_ in enumerate(SCN_ORDER):
        for mx_, off in (('ericsson_typical', -0.17), ('genai_2025', 0.17)):
            rr = r('mpc', mx_, gd, scn_)
            sv, ok = rr['saving_vs_aon'], rr['saving_qualified']
            hci(axd, f'f8d|ci|{mx_}|{scn_}', [i + off], [100 * sv['lo']], [100 * sv['hi']], CTRL['mpc']['color'],
                cap=0.09)
            line(axd, f'f8d|sav|{mx_}|{scn_}', [100 * sv['point']], [i + off], ls='none',
                 marker='o' if MIX_FILL[mx_] else 's', ms=3.8, color=CTRL['mpc']['color'],
                 mfc=CTRL['mpc']['color'] if MIX_FILL[mx_] else 'white', mew=0.9, zorder=3)
            if not ok:
                line(axd, f'f8d|fail|{mx_}|{scn_}', [100 * sv['point']], [i + off], ls='none', marker='x', ms=7,
                     color=CRITICAL, mew=1.2, zorder=4)
                t = axd.annotate(f'fails budget ({100 * viol("mpc", mx_, gd, scn_):.2f}%)',
                                 xy=(100 * sv['hi'], i + off), xytext=(5, 0), textcoords='offset points', ha='left',
                                 va='center', fontsize=7, color=CRITICAL)
                tag(t, f'f8d|txt|{mx_}|{scn_}')
            rec(fid, 'd', 'mpc', 'saving vs all-on', '%', 100 * sv['point'], 100 * sv['lo'], 100 * sv['hi'],
                ctrl='mpc', mix=mx_, scn=scn_, g=gd, source=f'P3.4 results_test["mpc|{mx_}|{gd}|{scn_}"].saving_vs_aon')
            rec(fid, 'd', 'mpc', 'budget met (saving qualified)', 'bool', bool(ok), ctrl='mpc', mix=mx_, scn=scn_,
                g=gd, source=f'P3.4 results_test["mpc|{mx_}|{gd}|{scn_}"].saving_qualified')
    axd.set_ylim(len(SCN_ORDER) - 0.5, -0.5)
    axd.set_yticks(range(len(SCN_ORDER)))
    axd.set_yticklabels([])
    axd.tick_params(axis='y', length=0)
    axd.grid(axis='y', visible=False)
    axd.set_xlim(0, 45)
    axd.set_xticks([0, 10, 20, 30, 40])
    axd.set_xlabel('MPC saving vs all-on (%)')
    hd = [Line2D([], [], ls='none', marker='o', ms=3.8, color=CTRL['mpc']['color'], label='UL/DL 10/90'),
          Line2D([], [], ls='none', marker='s', ms=3.8, color=CTRL['mpc']['color'], mfc='white', mew=0.9,
                 label='UL/DL 26/74'),
          Line2D([], [], ls='none', marker='x', ms=6, color=CRITICAL, mew=1.2, label='fails budget')]
    axd.legend(handles=hd, ncol=2, loc='lower left', bbox_to_anchor=(-0.02, 1.0), columnspacing=0.6,
               handletextpad=0.2, handlelength=1.0)
    describe(fig, [axd], '(d) MPC saving at $g$ = 2, rows as in (c)', row=1)

    fv = sorted(v for (_, _, v, _, _, _) in fails)
    FACTS['f8_n_fail'] = str(len(fails))
    FACTS['f8_fail_range'] = f'{fv[0]:.2f}–{fv[-1]:.2f}'
    FACTS['f8_fail_where'] = '; '.join(sorted({f'{MIXS[m]}, {plain_scn(s_)}' for (_, _, _, m, _, s_) in fails}))
    both, dE = 0, []
    for k in R:
        if k.startswith('mpc|'):
            ke = 'exh|' + k.split('|', 1)[1]
            if R[k]['saving_qualified'] and R[ke]['saving_qualified']:
                both += 1
                dE.append(100 * (R[ke]['saving_vs_aon']['point'] - R[k]['saving_vs_aon']['point']))
    FACTS['f8_both_qualified'] = str(both)
    FACTS['f8_exh_minus_mpc_mean'] = f'{np.mean(dE):.2f}'
    FACTS['f8_exh_minus_mpc_max'] = f'{max(dE):.2f}'
    for S in ('S0', 'S2'):
        gaps = [100 * PAIRED[f'{m}|{g_}|primary_{S}']['mpc-oracle']['point'] for m in MIXES for g_ in (1.0, 2.0)]
        FACTS[f'f8_q3_max_{S}'] = f'{max(gaps):.2f}'
    gb = [100 * PAIRED[f'{m}|{g_}|primary_{S}']['mpc-oracle_budget']['point'] for m in MIXES for g_ in (1.0, 2.0)
          for S in ('S0', 'S2')]
    FACTS['f8_gap_budget_max'] = f'{max(gb):.2f}'
    return fid, fig


# ---------------------------------------------------------------- read-back, checks, output
def readback(fig):
    out = {}
    for a in fig.findobj(lambda a: a.get_gid() is not None):
        gid = a.get_gid()
        if isinstance(a, Line2D):
            hexs = lambda c_: 'none' if isinstance(c_, str) and c_ == 'none' else to_hex(c_)
            v = {'kind': 'line', 'x': [float(t) for t in a.get_xdata()], 'y': [float(t) for t in a.get_ydata()],
                 'color': to_hex(a.get_color()), 'marker': str(a.get_marker()),
                 'mfc': hexs(a.get_markerfacecolor()), 'mec': hexs(a.get_markeredgecolor())}
        elif isinstance(a, Rectangle):
            v = {'kind': 'rect', 'x0': float(a.get_x()), 'y0': float(a.get_y()), 'w': float(a.get_width()),
                 'h': float(a.get_height()), 'hatch': a.get_hatch() or '', 'facecolor': to_hex(a.get_facecolor()),
                 'edgecolor': to_hex(a.get_edgecolor())}
        elif isinstance(a, LineCollection):
            v = {'kind': 'segments', 'segs': [[[float(p[0]), float(p[1])] for p in s] for s in a.get_segments()]}
        elif isinstance(a, AxesImage):
            z, m = np.ma.getdata(a.get_array()).astype(float), np.ma.getmaskarray(a.get_array())
            v = {'kind': 'grid', 'z': [[None if m[i, j] else float(z[i, j]) for j in range(z.shape[1])]
                                       for i in range(z.shape[0])]}
        elif isinstance(a, Text):
            v = {'kind': 'text', 's': a.get_text()}
        else:
            raise TypeError(f'{gid}: unsupported artist {type(a)}')
        assert gid not in out, f'duplicate gid {gid}'
        out[gid] = v
    return out


def layout_checks(fig):
    """Every text >= 7 pt, inside the figure, not overlapping another text, and not inside another panel."""
    fig.canvas.draw()
    rnd = fig.canvas.get_renderer()
    W, H = fig.bbox.width, fig.bbox.height
    owner = {}
    for ax in fig.axes:
        for t in ax.findobj(Text):
            owner.setdefault(t, ax)
        leg = ax.get_legend()
        if leg is not None:
            for t in list(leg.get_texts()) + [leg.get_title()]:
                owner[t] = ax
    texts = [t for t in fig.findobj(Text) if t.get_visible() and t.get_text().strip()]
    boxes, minpt, problems = [], 99.0, []
    for t in texts:
        bb = Text.get_window_extent(t, rnd)      # the text itself; an annotation's leader line is excluded
        if bb.width == 0 or bb.height == 0:
            continue
        minpt = min(minpt, t.get_fontsize())
        if bb.x0 < -0.5 or bb.y0 < -0.5 or bb.x1 > W + 0.5 or bb.y1 > H + 0.5:
            problems.append(f'outside figure: {t.get_text()!r}')
        for ax in fig.axes:
            if owner.get(t) is ax:
                continue
            ab = ax.get_window_extent(rnd)
            if min(bb.x1, ab.x1) - max(bb.x0, ab.x0) > 0.5 and min(bb.y1, ab.y1) - max(bb.y0, ab.y0) > 0.5:
                problems.append(f'text inside another panel: {t.get_text()!r}')
        boxes.append((t.get_text(), bb))
    for i in range(len(boxes)):
        for j in range(i + 1, len(boxes)):
            a_, b_ = boxes[i][1], boxes[j][1]
            if min(a_.x1, b_.x1) - max(a_.x0, b_.x0) > 0.5 and min(a_.y1, b_.y1) - max(a_.y0, b_.y0) > 0.5:
                problems.append(f'overlap: {boxes[i][0]!r} / {boxes[j][0]!r}')
    return {'min_font_pt': minpt, 'n_texts': len(boxes), 'problems': problems}


PDF_META = {'Creator': None, 'Producer': None, 'CreationDate': None}


def render(fig, fmt):
    b = io.BytesIO()
    if fmt == 'pdf':
        fig.savefig(b, format='pdf', metadata=PDF_META)
    else:
        fig.savefig(b, format='png', dpi=600, metadata={'Software': None})
    return b.getvalue()


def write_csv(path, rows):
    keys = ['figure', 'panel', 'series', 'quantity', 'unit', 'value', 'lo', 'hi', 'controller', 'mix', 'scenario',
            'g', 'source']
    with open(path, 'w', newline='', encoding='utf-8') as f:
        w = csv.DictWriter(f, fieldnames=keys, lineterminator='\n')
        w.writeheader()
        for row in rows:
            w.writerow({k: ('' if row[k] is None else (repr(row[k]) if isinstance(row[k], float) else row[k]))
                        for k in keys})


CAPTIONS = {
    'fig4_activation_w5': (
        'Fig. 4. Activation behaviour of the proposed MPC controller in the held-out week (25 test squares, '
        '18–24 November 2013, radio realizations 100–199). (a) Share of time with 0–3 active 7.2 GHz cells per '
        'site under the S0 energy model, for the 10/90 and 26/74 UL/DL mixes. Across all primary cells the '
        'controller used only the sector-index patterns {f4_patterns_used}. (b) Mean number of active 7.2 GHz '
        'cells (top) and sleep-plus-wake transitions per site-day (bottom; at most {f4_max_transitions}). '
        'Scenarios that led to identical decisions are drawn as one line ({f45_same_decisions}). The growth '
        'factor g scales each square so that its development-week busy hour offers g·T_0.5, where T_0.5 is the '
        'DL traffic per site at which the 3.5-GHz-only network is 50% occupied (the ETSI ES 202 706-1 busy-hour '
        'level; 513.6 Mbit/s at ISD 200 m).'),
    'fig5_heldout_savings_w6': (
        'Fig. 5. Held-out energy saving and service reliability of the proposed MPC controller. (a), (b) Saving '
        'relative to all-on operation, 1 − E/E_aon, with energy pooled over the 25 test squares and 100 radio '
        'realizations, under S0 (published hardware model) and S1, S2 (assumed added equipment overhead). Error '
        'bars are 95% pigeonhole bootstrap intervals (2,000 resamples; squares and realizations resampled '
        'independently). (c), (d) Avoidable violation share: the share of 10-minute intervals in which more than '
        '5% of all users are in DL or UL outage although some activation pattern would have met the target, '
        'plus the seconds of wake-up gaps in which the previous pattern fails. The dashed line is the 1% avoidable-violation budget. The shaded band in (d) marks growth levels where the '
        'unavoidable share (no pattern meets the target) exceeds 1% (Fig. 7). Scenarios with identical decisions '
        'share one line ({f45_same_decisions}). All 42 primary cells meet the budget (maximum '
        '{f5_max_violation}%). In the registered H1 cells (g = 1, 2) the saving is {f5_h1_S0}% under S0 and '
        '{f5_h1_S2}% under S2.'),
    'fig6_dl_only_w7': (
        'Fig. 6. Consequences of downlink-only activation at the 26/74 UL/DL mix (test week). (a) Avoidable '
        'violation share of the UL-agnostic threshold controller as its own DL-only check measures it (open, at '
        'most {f6_dlcheck_max}%) and under the all-user DL + UL criterion (filled), against the UL-aware '
        'threshold controller and MPC, in the registered H2 cells (log scale; S1 and S2 are identical). The '
        'all-user difference between the DL-only and UL-aware threshold controllers is {f6_h2_range} percentage '
        'points, with every 95% interval above zero. (b) Saving relative to all-on with 95% intervals under S0 '
        '(top) and S2 (bottom). Hatched bars fail the 1% avoidable-violation budget and are not valid savings; '
        'the number above a hatched bar is its avoidable violation share.'),
    'fig7_capacity_boundary_w3w8': (
        'Fig. 7. Sustainable traffic and the infeasibility boundary. (a) Sustainable traffic x*, the largest '
        'traffic (in units of T_0.5) at which at most 5% of all users are in DL or UL outage, for each '
        'sector-index activation pattern of the typical site (50 dBm, development radio table), at the two UL/DL '
        'mixes. The black bar is the DL-only limit, which ignores the UL. All-on sustains '
        '{f7_xstar_all_on_1090} at 10/90 and {f7_xstar_all_on_2674} at 26/74, against a DL-only limit of '
        '{f7_xstar_dl_all_on}. (b) Unavoidable share, the share of 10-minute intervals in which no pattern meets '
        'the target, in the test week (filled, solid) and the development week (open, dashed); the axis is '
        'linear below 1% and logarithmic above. Dotted lines mark the growth at which a square\'s '
        'development-week busy hour equals the all-on x*. At 26/74 the test share rises from {f7_u_2674_g2}% '
        '(g = 2.0) to {f7_u_2674_g25}% (g = 2.5); at 10/90 it is {f7_u_1090_g3}% at g = 3.0.'),
    'fig8_comparison_robustness_w9': (
        'Fig. 8. Controller comparison, optimality gap and hardware robustness (test week). (a) Frozen operating '
        'points of the online controllers and two perfect-foresight references at 26/74, g = 2, S2: saving '
        'relative to all-on against avoidable violation share (axis broken between 1.25% and 1.5%); points '
        'right of the dashed line fail the budget. This compares tuned operating points; it is not a Pareto '
        'frontier. Where MPC and the exhaustive controller both meet the '
        'budget ({f8_both_qualified} of 196 cells), the exhaustive controller saves {f8_exh_minus_mpc_mean} '
        'point more on average (at most {f8_exh_minus_mpc_max}). (b) Energy gap of MPC to the three '
        'perfect-foresight references in the registered Q3 cells, in percentage points of all-on energy, with '
        '95% paired intervals; dashed lines are the registered Q3 limits. The gap to the zero-violation oracle '
        'is at most {f8_q3_max_S0} (S0) and {f8_q3_max_S2} (S2) points, and at most {f8_gap_budget_max} points '
        'to the oracle with the same 1% allowance. (c) MPC avoidable violation share in all 196 scenario cells; '
        'the colour scale is centred on the 1% budget; outlined cells exceed it ({f8_n_fail} cells, '
        '{f8_fail_range}%, all at {f8_fail_where}). (d) MPC saving at g = 2 across energy scenarios with 95% '
        'intervals; the crossed point fails the budget and is not a valid saving. Scenario rows: primary '
        '(50 dBm, M_RF = 256, Cat 1 sleep, Set 1 ratios, 3.5 GHz "right" power case); Cat 2 sleep; the 3.5 GHz UL-load sensitivity (D29); the '
        '3.5 GHz "left" power case; Set 3 sleep ratios; M_RF = 64, 128, 1024.'),
}
DESCRIPTIONS = {}


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / 'source_data').mkdir(exist_ok=True)
    plotted, outputs, layout = {}, {}, {}
    for build in (fig4, fig5, fig6, fig7, fig8):
        fid, fig = build()
        place_descriptions(fig)
        DESCRIPTIONS[fid] = [t for _, t, _ in fig._descs]
        layout[fid] = layout_checks(fig)
        plotted[fid] = readback(fig)
        for fmt in ('pdf', 'png'):
            b1, b2 = render(fig, fmt), render(fig, fmt)
            if b1 != b2:
                raise SystemExit(f'{fid}.{fmt}: rendering is not byte-identical')
            (OUT / f'{fid}.{fmt}').write_bytes(b1)
            outputs[f'{fid}.{fmt}'] = hashlib.sha256(b1).hexdigest()
        plt.close(fig)
        write_csv(OUT / 'source_data' / f'{fid}.csv', SOURCE[fid])
        outputs[f'source_data/{fid}.csv'] = sha(OUT / 'source_data' / f'{fid}.csv')
        print(f'{fid}: min font {layout[fid]["min_font_pt"]} pt, {layout[fid]["n_texts"]} texts, '
              f'{len(plotted[fid])} tagged artists, problems: {layout[fid]["problems"] or "none"}', flush=True)
    caps = ['# Figure captions', '',
            'Generated by `scripts/figures/frozen_figures.py` from the frozen P3.3/P3.4 outputs; every number in '
            'braces in the source template is a value in `caption_facts.json`, recomputed by '
            '`scripts/figures/verify_frozen_figures.py`.', '']
    for fid, txt in CAPTIONS.items():
        caps += [f'## {fid}', '', '**Panel descriptions (under each panel):** ' + ' | '.join(DESCRIPTIONS[fid]), '',
                 txt.format(**FACTS), '']
    (OUT / 'captions.md').write_text('\n'.join(caps), encoding='utf-8', newline='\n')
    (OUT / 'caption_facts.json').write_text(json.dumps(FACTS, indent=1, ensure_ascii=False) + '\n', encoding='utf-8',
                                            newline='\n')
    (OUT / 'plotted_values.json').write_text(json.dumps(plotted, separators=(',', ':')) + '\n', encoding='utf-8',
                                             newline='\n')
    for f in ('captions.md', 'caption_facts.json', 'plotted_values.json'):
        outputs[f] = sha(OUT / f)
    if check_inputs() != INPUTS_BEFORE:
        raise SystemExit('inputs changed during plotting')
    head = subprocess.run(['git', '-C', str(ROOT), 'rev-parse', 'HEAD'], capture_output=True, text=True).stdout.strip()
    manifest = {
        'tool': 'scripts/figures/frozen_figures.py',
        'script_sha256': sha(Path(__file__)),
        'git_head_at_run': head,
        'python': platform.python_version(), 'numpy': np.__version__, 'matplotlib': matplotlib.__version__,
        'font': {'family': 'STIXGeneral', 'file': Path(FONT_PATH).name, 'sha256': sha(FONT_PATH), 'pdf_fonttype': 42},
        'width_in': DOUBLE,
        'decision_groups': {mx: [list(g_) for g_ in GROUPS[mx]] for mx in MIXES},
        'input_sha256': INPUTS_BEFORE,
        'inputs_unchanged_after_plotting': True,
        'deterministic_rendering': True,
        'outputs_sha256': outputs,
        'layout_checks': layout,
        'encoding_checks': ENCODING,
        'all_layout_checks_passed': all(not v['problems'] and v['min_font_pt'] >= MIN_PT for v in layout.values()),
    }
    (OUT / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n', encoding='utf-8', newline='\n')
    print('layout checks passed' if manifest['all_layout_checks_passed'] else 'LAYOUT PROBLEMS (see manifest)')


if __name__ == '__main__':
    main()
