"""Publication Figures 1-3 from the validated Phase 2 and Phase 3 model outputs.

Usage:  python -I scripts/figures/model_figures.py <repo root>

Figure 1 (working 1): the Munich-anchored 19-site layout (E1c.2 geometry, E1.1 mask, E1c indoor samples) and the
  structure of the proposed controller (scripts/p3_3_controllers.py, parameters frozen in configs/p3/p3_4_heldout.json).
Figure 2 (working 2(c, d)): directional versus averaged interference and the resulting UL/DL feasibility (E1c.3, which
  reproduces E1c.2 exactly).
Figure 3 (working 4): base-station power (P3.1) and daily energy across S0-S2 (P3.2c).

Style, helpers and layout checks are those of scripts/figures/frozen_figures.py, imported unchanged (its own inputs
are therefore hash-checked as well). Read-only on every experiment output: each input is hash-checked before and
after plotting, against the manifest that records it or, for the one file no manifest hashes, against the committed
git blob. Writes paper/figures/:
  fig1_architecture_w1, fig2_interference_feasibility_w2, fig3_power_energy_w4  (.pdf vector, .png 600 dpi)
  source_data/fig{1,2,3}_*.csv, plotted_values_fig1-3.json, caption_facts_fig1-3.json, captions_fig1-3.md,
  manifest_fig1-3.json
Checked by scripts/figures/verify_model_figures.py, an independent recomputation.
Rules: docs/PHASE_3_ENERGY_PROTOCOL.md §10.4-10.5 and §10.7.
"""
import hashlib
import json
import math
import platform
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import frozen_figures as FF  # noqa: E402  (shared style and helpers; argv[1] is the repository root)

import matplotlib  # noqa: E402
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
from matplotlib.colors import BoundaryNorm, ListedColormap  # noqa: E402
from matplotlib.lines import Line2D  # noqa: E402
from matplotlib.patches import FancyBboxPatch, Patch, Rectangle  # noqa: E402
from matplotlib.collections import LineCollection  # noqa: E402

ROOT, OUT, sha = FF.ROOT, FF.OUT, FF.sha
INK, INK2, MUTED, SCN_COL = FF.INK, FF.INK2, FF.MUTED, FF.SCN_COL
rec, tag, line, describe, hci, vci = FF.rec, FF.tag, FF.line, FF.describe, FF.hci, FF.vci
P2, P3 = ROOT / 'results/phase2', ROOT / 'results/phase3'
E1C3, P31, P32C = P2 / 'e1c3_directional', P3 / 'p3_1_energy_models', P3 / 'p3_2c_energy_ledger'
CFG33, CFG34, CFG14 = ROOT / 'configs/p3/p3_3_controllers.json', ROOT / 'configs/p3/p3_4_heldout.json', \
    ROOT / 'configs/e1/e1_4_antennas_o2i.json'
SITES = P2 / 'e1c2_geometry/e1c2_sites_isd200.npz'
MASK = P2 / 'e1_geometry/site_tx0_mask.npz'
INDOOR = P2 / 'e1c_geometry/site_tx0_indoor.npz'
S154 = P2 / 'e1_5_4_multiuser/summary.json'
SUFFIX = 'fig1-3'


# ---------------------------------------------------------------- inputs
def git_blob_equal(rel):
    """The working-tree file equals its committed blob at HEAD (used where no manifest records the file)."""
    run = lambda *a: subprocess.run(['git', '-C', str(ROOT), *a], capture_output=True, text=True)  # noqa: E731
    head, work = run('rev-parse', f'HEAD:{rel}').stdout.strip(), run('hash-object', rel).stdout.strip()
    if not head or head != work:
        raise SystemExit(f'{rel}: not identical to the committed blob ({work} vs {head})')
    return head


def check_inputs():
    j = lambda p: json.loads(Path(p).read_text())  # noqa: E731
    m_c3 = j(E1C3 / 'manifest.json')
    m31, m32c = j(P31 / 'manifest.json'), j(P32C / 'manifest.json')
    m33, m34 = j(P3 / 'p3_3_controllers/manifest.json'), j(P3 / 'p3_4_heldout/manifest.json')
    m14 = j(P2 / 'e1_4_antennas_o2i/manifest.json')
    pairs = [(E1C3 / 'summary.json', m_c3['outputs_sha256']['summary.json']),
             (E1C3 / 'raw/counts_isd200.npz', m_c3['outputs_sha256']['raw/counts_isd200.npz']),
             (SITES, m_c3['input_sha256']['e1c2_geometry/e1c2_sites_isd200.npz']),
             (MASK, m_c3['input_sha256']['e1_geometry/site_tx0_mask.npz']),
             (INDOOR, m_c3['input_sha256']['e1c_geometry/site_tx0_indoor.npz']),
             (P2 / 'e1_5_4_multiuser/manifest.json', m_c3['input_sha256']['e1_5_4_multiuser/manifest.json']),
             (CFG14, m14['config_sha256']),
             (P31 / 'summary.json', m31['outputs_sha256']['summary.json']),
             (P31 / 'manifest.json', m32c['input_sha256']['p3_1_energy_models/manifest.json']),
             (P32C / 'summary.json', m32c['outputs_sha256']['summary.json']),
             (CFG33, m33['config_sha256']),
             (CFG34, m34['config_sha256'])]
    out = {}
    for p, h in pairs:
        got = sha(p)
        if got != h:
            raise SystemExit(f'input hash mismatch: {p} {got} != recorded {h}')
        out[p.relative_to(ROOT).as_posix()] = got
    for p in (E1C3 / 'manifest.json', P32C / 'manifest.json', P2 / 'e1_4_antennas_o2i/manifest.json'):
        out[p.relative_to(ROOT).as_posix()] = sha(p)
    out[S154.relative_to(ROOT).as_posix()] = sha(S154)
    out['git_blob:' + S154.relative_to(ROOT).as_posix()] = git_blob_equal(S154.relative_to(ROOT).as_posix())
    return out


INPUTS_BEFORE = check_inputs()
C3S = json.loads((E1C3 / 'summary.json').read_text())
S31 = json.loads((P31 / 'summary.json').read_text())
S32C = json.loads((P32C / 'summary.json').read_text())
C33 = json.loads(CFG33.read_text())
C34 = json.loads(CFG34.read_text())
C14 = json.loads(CFG14.read_text())
TH154 = json.loads(S154.read_text())['analytic_thresholds_db']['F2']
_z = np.load(SITES)
GEO = {k: _z[k] for k in ('site_x', 'site_y', 'site_h', 'site_ring', 'site_kind', 'site_i', 'site_j')}
_z = np.load(MASK)
OX, OY, GNB = _z['x'], _z['y'], _z['gnb']
_z = np.load(INDOOR)
IX, IY = _z['x'], _z['y']
_z = np.load(E1C3 / 'raw/counts_isd200.npz')
VICT = {'out': _z['victim_out_index'], 'ind': _z['victim_ind_index']}
del _z

FACTS = {}
CHECKS = {}          # build-time checks recorded in the manifest
AXES_REG = {}        # figure -> {name: axes} whose category ticks are read back for the verifier
ISD = 200.0
STEP = 4.0


# ---------------------------------------------------------------- Figure 1 (working 1)
def lattice_center(i, j):
    return (float(GNB[0]) + i * ISD + j * ISD / 2.0, float(GNB[1]) + j * ISD * math.sqrt(3.0) / 2.0)


def hexagon(cx, cy):
    """Voronoi cell of the lattice point: vertices at 30 + 60 k degrees, circumradius ISD / sqrt(3)."""
    a = np.radians(30.0 + 60.0 * np.arange(7))
    r_ = ISD / math.sqrt(3.0)
    return cx + r_ * np.cos(a), cy + r_ * np.sin(a)


def scene_raster():
    """Class per 4 m lattice cell: 0 not sampled, 1 open outdoor point (E1.1 mask), 2 building (E1c indoor point)."""
    x0, y0 = float(min(OX.min(), IX.min())), float(min(OY.min(), IY.min()))
    x1, y1 = float(max(OX.max(), IX.max())), float(max(OY.max(), IY.max()))
    nx, ny = int(round((x1 - x0) / STEP)) + 1, int(round((y1 - y0) / STEP)) + 1
    C = np.zeros((ny, nx), dtype=np.int8)
    for xs, ys, v in ((OX, OY, 1), (IX, IY, 2)):
        ci, ri = np.rint((xs - x0) / STEP).astype(np.int64), np.rint((ys - y0) / STEP).astype(np.int64)
        if not (np.array_equal(x0 + STEP * ci, xs) and np.array_equal(y0 + STEP * ri, ys)):
            raise SystemExit('scene samples are not on one 4 m lattice')
        if v == 2 and np.any(C[ri, ci] == 1):
            raise SystemExit('an indoor point coincides with an outdoor point')
        C[ri, ci] = v
    ext = (x0 - STEP / 2, x1 + STEP / 2, y0 - STEP / 2, y1 + STEP / 2)
    return C, ext, (x0, y0)


def in_polygon(px, py, vx, vy, tol=1e-6):
    """Strictly inside (even-odd rule) and on the boundary (within tol metres of an edge) of the closed polygon vx, vy."""
    inside = np.zeros(len(px), dtype=bool)
    dmin = np.full(len(px), np.inf)
    for k in range(len(vx) - 1):
        xa, ya, xb, yb = vx[k], vy[k], vx[k + 1], vy[k + 1]
        cond = (ya > py) != (yb > py)
        xc = xa + (py - ya) * (xb - xa) / np.where(yb != ya, yb - ya, 1.0)
        inside ^= cond & (px < xc)
        ex, ey = xb - xa, yb - ya
        u = np.clip(((px - xa) * ex + (py - ya) * ey) / (ex * ex + ey * ey), 0.0, 1.0)
        dmin = np.minimum(dmin, np.hypot(px - (xa + u * ex), py - (ya + u * ey)))
    on = dmin <= tol
    return inside & ~on, on


BOXES = []


def box(ax, gid, x, y, w, h, text, fs=7.0, ec=INK2, fc='white', lw=0.7):
    p = FancyBboxPatch((x, y), w, h, boxstyle='round,pad=0,rounding_size=0.035', fc=fc, ec=ec, lw=lw, zorder=2)
    ax.add_patch(p)
    t = ax.text(x + w / 2, y + h / 2, text, ha='center', va='center', fontsize=fs, linespacing=1.18, zorder=3)
    tag(t, f'f1b|box|{gid}')
    BOXES.append((gid, p, t))
    return (x, y, w, h)


def arrow(ax, xy_from, xy_to, color=INK2):
    ax.annotate('', xy=xy_to, xytext=xy_from, arrowprops=dict(arrowstyle='-|>,head_length=0.32,head_width=0.16',
                                                              lw=0.75, color=color, shrinkA=0, shrinkB=0), zorder=4)


def fig1():
    fid = 'fig1_architecture_w1'
    W_, H_ = FF.DOUBLE, 3.95
    fig = plt.figure(figsize=(W_, H_))
    # ---- (a) map
    XL, YL = (-507.0, 524.0), (-456.0, 498.0)
    map_w = 3.0
    map_h = map_w * (YL[1] - YL[0]) / (XL[1] - XL[0])
    ax = fig.add_axes([0.072, 0.145, map_w / W_, map_h / H_])
    C, ext, org = scene_raster()
    cmap = ListedColormap(['#ecebe6', '#ffffff', '#bdbbb4'])
    im = ax.imshow(C, origin='lower', extent=ext, cmap=cmap, norm=BoundaryNorm([-0.5, 0.5, 1.5, 2.5], 3),
                   interpolation='nearest', zorder=0)
    arr = np.asarray(im.get_array()).astype(np.int8)
    CHECKS['fig1_raster'] = {'shape': list(C.shape), 'lattice_origin_m': list(org), 'cell_m': STEP,
                             'counts': {'not_sampled': int((C == 0).sum()), 'outdoor': int((C == 1).sum()),
                                        'building': int((C == 2).sum())},
                             'sha256': hashlib.sha256(C.tobytes()).hexdigest(),
                             'artist_array_sha256': hashlib.sha256(arr.tobytes()).hexdigest()}
    rec(fid, 'a', 'scene raster', 'cells (not sampled / outdoor / building)', 'count',
        f"{(C == 0).sum()}/{(C == 1).sum()}/{(C == 2).sum()}",
        source='E1.1 site_tx0_mask.npz x,y (outdoor); E1c site_tx0_indoor.npz x,y (building); 4 m lattice')
    # nominal cells
    ii, jj = GEO['site_i'], GEO['site_j']
    for k in range(len(ii)):
        cx, cy = lattice_center(int(ii[k]), int(jj[k]))
        hx, hy = hexagon(cx, cy)
        bold = (int(ii[k]), int(jj[k])) == (0, 0)
        line(ax, f'f1a|hex|{int(ii[k])},{int(jj[k])}', hx, hy, color=INK if bold else INK2,
             lw=1.5 if bold else 0.55, zorder=2 if bold else 1)
        rec(fid, 'a', f'nominal cell ({int(ii[k])},{int(jj[k])})', 'lattice point', 'm', f'{cx!r},{cy!r}',
            source=f'E1c.2 lattice: site_x[0], site_y[0] + i (ISD, 0) + j (ISD/2, ISD sqrt3/2); i={int(ii[k])}, '
                   f'j={int(jj[k])}')
    # the bold hexagon is the scored region: every victim lies inside it or on its boundary (E1c.2 gives ties to
    # site 0, the lower ring), and every sampled point strictly inside it is a victim
    hx0, hy0 = hexagon(*lattice_center(0, 0))
    chk = {}
    for nm, X, Y in (('outdoor', OX, OY), ('indoor', IX, IY)):
        vic = VICT['out' if nm == 'outdoor' else 'ind']
        strict, on = in_polygon(X, Y, hx0, hy0)
        is_v = np.zeros(len(X), dtype=bool)
        is_v[vic] = True
        chk[nm] = {'victims': int(is_v.sum()), 'victims_strictly_inside': int((is_v & strict).sum()),
                   'victims_on_boundary': int((is_v & on).sum()), 'victims_outside': int((is_v & ~strict & ~on).sum()),
                   'non_victims_strictly_inside': int((~is_v & strict).sum())}
    chk['passed'] = all(chk[n]['victims_outside'] == 0 and chk[n]['non_victims_strictly_inside'] == 0
                        for n in ('outdoor', 'indoor'))
    CHECKS['fig1_victims_inside_bold_cell'] = chk
    # sectors
    bear = [float(b) for b in C14['cases']['A_equal_elements']['sector_bearings_deg']]
    L = 38.0
    segs = [[(float(x), float(y)), (float(x) + L * math.cos(math.radians(b)), float(y) + L * math.sin(math.radians(b)))]
            for x, y in zip(GEO['site_x'], GEO['site_y']) for b in bear]
    lc = LineCollection(segs, colors=INK, linewidths=0.8, zorder=3)
    ax.add_collection(lc, autolim=False)
    tag(lc, 'f1a|sectors')
    for s, b in enumerate(bear):
        d = L + 17.0
        t = ax.text(float(GEO['site_x'][0]) + d * math.cos(math.radians(b)),
                    float(GEO['site_y'][0]) + d * math.sin(math.radians(b)), str(s + 1), ha='center', va='center',
                    fontsize=7.5, zorder=5,
                    bbox=dict(boxstyle='circle,pad=0.12', fc='white', ec=INK, lw=0.5))
        tag(t, f'f1a|seclabel|{s}')
        rec(fid, 'a', f'sector {s + 1} (index {s})', 'boresight bearing', 'deg', b,
            source=f'configs/e1/e1_4_antennas_o2i.json cases.A_equal_elements.sector_bearings_deg[{s}]')
    # sites
    sty = {'site0': dict(marker='*', ms=10, mfc=INK, mec='white', mew=0.5),
           'rooftop': dict(marker='o', ms=4.6, mfc=INK, mec='white', mew=0.5),
           'mast': dict(marker='^', ms=5.6, mfc='white', mec=INK, mew=1.0)}
    kinds = [str(k) for k in GEO['site_kind']]
    for kd in ('rooftop', 'mast', 'site0'):
        idx = [n for n, k in enumerate(kinds) if k == kd]
        line(ax, f'f1a|sites|{kd}', [float(GEO['site_x'][n]) for n in idx], [float(GEO['site_y'][n]) for n in idx],
             ls='none', color=INK, zorder=6, **sty[kd])
        for n in idx:
            rec(fid, 'a', f'site {n} ({kd}, ring {int(GEO["site_ring"][n])})', 'position x, y; height', 'm',
                f'{float(GEO["site_x"][n])!r},{float(GEO["site_y"][n])!r}; {float(GEO["site_h"][n])!r}',
                source=f'E1c.2 e1c2_sites_isd200.npz site_x/site_y/site_h/site_kind[{n}]')
    # scale bar
    xb, yb = -486.0, -426.0
    line(ax, 'f1a|scalebar', [xb, xb + 200.0], [yb, yb], color=INK, lw=1.2, solid_capstyle='butt', zorder=6)
    ax.text(xb + 100.0, yb + 9.0, '200 m', ha='center', va='bottom', fontsize=7.5, zorder=6)
    ax.set_xlim(*XL)
    ax.set_ylim(*YL)
    ax.set_aspect('equal', adjustable='box')
    ax.grid(False)
    for sp in ax.spines.values():
        sp.set_visible(True)
        sp.set_linewidth(0.6)
    ax.set_xticks([-400, -200, 0, 200, 400])
    ax.set_yticks([-400, -200, 0, 200, 400])
    ax.set_xlabel('$x$ (m), Munich scene coordinates')
    ax.set_ylabel('$y$ (m)')
    hh = [Line2D([], [], ls='none', color=INK, label='Site 0 (27 m)', **sty['site0']),
          Line2D([], [], ls='none', color=INK, label='Rooftop site (25 m)', **sty['rooftop']),
          Line2D([], [], ls='none', color=INK, label='Mast (25 m)', **sty['mast']),
          Line2D([], [], color=INK, lw=0.8, label='Sector boresight'),
          Line2D([], [], color=INK2, lw=0.55, label='Nominal cell'),
          Line2D([], [], color=INK, lw=1.5, label='Site 0 cell (scored users)'),
          Patch(facecolor='#bdbbb4', edgecolor='none', label='Building'),
          Patch(facecolor='#ffffff', edgecolor=INK2, linewidth=0.4, label='Open outdoor'),
          Patch(facecolor='#ecebe6', edgecolor='none', label='Not sampled')]
    ax.legend(handles=hh, ncol=3, loc='lower left', bbox_to_anchor=(-0.02, 1.005), columnspacing=0.8,
              handlelength=1.5, handletextpad=0.35, fontsize=7, labelspacing=0.3)
    describe(fig, [ax], '(a) Munich-anchored 19-site layout, ISD 200 m')

    # ---- (b) controller structure
    bx0 = 0.555
    axb = fig.add_axes([bx0, 0.10, 0.985 - bx0, 0.885])
    Wd, Hd = (0.985 - bx0) * W_, 0.885 * H_
    axb.set_xlim(0, Wd)
    axb.set_ylim(0, Hd)
    axb.set_xticks([])
    axb.set_yticks([])
    axb.axis('off')
    frozen = C34['frozen']['params']
    Hs = sorted({v['H'] for k, v in frozen.items() if k.startswith('mpc|')})
    if len(Hs) != 1:
        raise SystemExit('the MPC horizon is not the same in every cell')
    Hm = Hs[0]
    xo0, xo1 = 0.24, 1.98            # online column
    xf0, xf1 = 2.16, Wd - 0.02       # offline column
    top = Hd - 0.03
    # containers
    for (x0_, y0_, x1_, y1_, lab) in ((xo0 - 0.09, 1.00, xo1 + 0.06, top, 'Online, every 10-min slot $t$'),
                                      (xf0 - 0.07, 1.00, xf1 + 0.0, top, 'Offline (development\nweek), then frozen')):
        axb.add_patch(Rectangle((x0_, y0_), x1_ - x0_, y1_ - y0_, fc='none', ec=MUTED, lw=0.6, ls=(0, (3, 2)),
                                zorder=1))
        t = axb.text(x0_ + 0.05, y1_ - 0.05, lab, ha='left', va='top', fontsize=7, style='italic', color=INK2,
                     linespacing=1.1)
        tag(t, f'f1b|container|{lab.split()[0].strip(",")}')
    rows = {}
    rows['C1'] = box(axb, 'C1', xo0, 2.98, xo1 - xo0, 0.26, 'Measured traffic $x_{t-1}$ of the previous slot')
    rows['C2'] = box(axb, 'C2', xo0, 2.30, xo1 - xo0, 0.48,
                     'Forecast $\\hat{x}_{t+h}$, $h$ = ' + ', '.join(str(h) for h in range(Hm)) +
                     ', by seasonal\npersistence, and upper bound $\\hat{x}_{t+h}\\,e^{q_{\\alpha,h}}$')
    rows['C3'] = box(axb, 'C3', xo0, 1.44, xo1 - xo0, 0.66,
                     f'Dynamic program over $H$ = {Hm} slots:\nminimum forecast energy, using only\npatterns '
                     'feasible at the upper bound\n(if none is feasible: all cells on)')
    rows['C4'] = box(axb, 'C4', xo0, 1.08, xo1 - xo0, 0.18, 'Apply the first pattern $p_t$')
    for a_, b_ in (('C1', 'C2'), ('C2', 'C3'), ('C3', 'C4')):
        xa, ya, wa, ha_ = rows[a_]
        xb_, yb_, wb, hb = rows[b_]
        arrow(axb, (xa + wa / 2, ya), (xb_ + wb / 2, yb_ + hb))
    rows['B1'] = box(axb, 'B1', xf0, 2.30, xf1 - xf0, 0.56, 'Forecast model\nweekday/weekend\nprofile; log-error\n'
                     'quantiles $q_{\\alpha,h}$')
    rows['B2'] = box(axb, 'B2', xf0, 1.68, xf1 - xf0, 0.52, 'Radio table\nall-user DL + UL\noutage per pattern\n'
                     'and traffic')
    rows['B3'] = box(axb, 'B3', xf0, 1.08, xf1 - xf0, 0.52, 'Energy table\nsite power per\npattern and traffic,\n'
                     'S0, S1, S2')
    arrow(axb, (xf0, 2.30 + 0.28), (xo1, 2.30 + 0.24))
    arrow(axb, (xf0, 1.68 + 0.26), (xo1, 1.44 + 0.46))
    arrow(axb, (xf0, 1.08 + 0.26), (xo1, 1.44 + 0.18))
    # network
    t = axb.text(xo0 - 0.09, 0.80, 'Network: 19 sites × 3 sectors', ha='left', va='center', fontsize=7,
                 style='italic', color=INK2)
    tag(t, 'f1b|container|network')
    rows['N1'] = box(axb, 'N1', xo0, 0.05, 0.86, 0.60, '3.5 GHz layer\n(all 57 cells)\nalways on')
    rows['N2'] = box(axb, 'N2', xo0 + 0.96, 0.05, Wd - 0.02 - (xo0 + 0.96), 0.60,
                     '7.2 GHz layer (57 cells): pattern $p_t$,\none on/off bit per sector 1, 2, 3, at\nevery site; '
                     'a woken cell serves after\n$T_\\mathrm{TR} + T_\\mathrm{SCell}$')
    arrow(axb, (xo0 + 1.34, 1.08), (xo0 + 1.34, 0.65))
    # feedback: the traffic carried in slot t is the measurement of the next decision
    xr = 0.07
    line(axb, 'f1b|feedback', [xo0, xr, xr], [0.35, 0.35, 2.98 + 0.13], color=INK2, lw=0.75, zorder=4)
    arrow(axb, (xr, 2.98 + 0.13), (xo0, 2.98 + 0.13))
    t = axb.text(xr - 0.035, 1.75, 'traffic of slot $t$, measured', rotation=90, ha='right', va='center', fontsize=7,
                 color=INK2)
    tag(t, 'f1b|feedback_label')
    describe(fig, [axb], '(b) Proposed controller (MPC) and its inputs')
    AXES_REG[fid] = {'a': ax, 'b': axb}
    FACTS['f1_n_rooftop'] = str(kinds.count('rooftop'))
    FACTS['f1_n_mast'] = str(kinds.count('mast'))
    FACTS['f1_site0_h'] = f"{float(GEO['site_h'][0]):g}"
    FACTS['f1_other_h'] = '/'.join(sorted({f'{float(h):g}' for h, k in zip(GEO['site_h'], kinds) if k != 'site0'}))
    FACTS['f1_bearings'] = ', '.join(f'{b:g}°' for b in bear)
    FACTS['f1_victims_out'] = f"{C3S['isd']['200']['victims']['outdoor_points']:,}"
    FACTS['f1_victims_ind'] = f"{C3S['isd']['200']['victims']['indoor_samples']:,}"
    FACTS['f1_H'] = str(Hm)
    al = {k.split('|')[1] + '|' + k.split('|')[2]: v['alpha'] for k, v in frozen.items() if k.startswith('mpc|')}
    by_v = {}
    for k, v in sorted(al.items()):
        by_v.setdefault(v, []).append(k)
    maj = max(by_v, key=lambda v: len(by_v[v]))
    cell = lambda k: f"{'10/90' if k.startswith('ericsson') else '26/74'} under {k.split('|')[1]}"  # noqa: E731
    FACTS['f1_alpha'] = ('α = ' + '; '.join(f"{v:g} at {', '.join(cell(k) for k in ks)}"
                                           for v, ks in sorted(by_v.items()) if v != maj) + f', and {maj:g} otherwise')
    FACTS['f1_slot_min'] = f"{C33['timing']['slot_s'] / 60:g}"
    FACTS['f1_eps'] = f"{100 * C33['epsilon']:g}"
    return fid, fig


# ---------------------------------------------------------------- Figure 2 (working 2(c, d))
MODELS = [('noise_multi', 'No interference'), ('primary', 'Beam-averaged'), ('sd', 'Directional'),
          ('geo', 'Pure pattern')]
MOD_LEG = {'noise_multi': 'No interference', 'primary': 'Beam-averaged (E1c.2)', 'sd': 'Directional (primary)',
           'geo': 'Pure array pattern'}
MOD_Z = {'primary': 4, 'sd': 5, 'geo': 6, 'noise_multi': 7}      # the hollow marker on top stays visible
MOD_COL = {'noise_multi': '#b9b7af', 'primary': '#86847d', 'sd': '#4a4945', 'geo': '#0b0b0b'}
MOD_MK = {'noise_multi': 'o', 'primary': 's', 'sd': 'D', 'geo': '^'}
LINKS = [('DL_3.5GHz', 'DL 3.5 GHz'), ('DL_7.2GHz', 'DL 7.2 GHz'), ('UL_3.5GHz', 'UL 3.5 GHz'),
         ('UL_7.2GHz', 'UL 7.2 GHz')]
CASES = [('A_equal_elements', 'Case A: 8×16 arrays at both carriers, 20 MHz'),
         ('C_deployment', 'Case C: deployment (used in Phase 3)')]


def st200(sn):
    return C3S['isd']['200']['settings'][sn]


def fig2():
    fid = 'fig2_interference_feasibility_w2'
    fig = plt.figure(figsize=(FF.DOUBLE, 2.95))
    gs = fig.add_gridspec(2, 2, width_ratios=[1.0, 1.0], left=0.128, right=0.985, top=0.79, bottom=0.215,
                          wspace=0.47, hspace=0.12)
    th = {'DL': TH154['DL'], 'UL_F2': TH154['UL']}
    axs = []
    for r_, (mt, lab) in enumerate((('DL', 'DL, 7.2 GHz'), ('UL_F2', 'UL, 7.2 GHz'))):
        ax = fig.add_subplot(gs[r_, 0])
        axs.append(ax)
        for k, (sn, name) in enumerate(MODELS):
            q = st200(sn)['A_equal_elements']['quantiles_db_1_5_50_95']['M2412']['7.2GHz'][mt]
            y = 3 - k
            col = MOD_COL[sn]
            line(ax, f'f2a|{mt}|{sn}|range', [q[0], q[3]], [y, y], color=col, lw=1.0, zorder=2)
            line(ax, f'f2a|{mt}|{sn}|ends', [q[0], q[3]], [y, y], ls='none', marker='|', ms=5.5, mew=1.0, color=col,
                 zorder=3)
            line(ax, f'f2a|{mt}|{sn}|p5', [q[1]], [y], ls='none', marker='o', ms=4.0, mfc='white', mec=col, mew=0.9,
                 color=col, zorder=4)
            line(ax, f'f2a|{mt}|{sn}|p50', [q[2]], [y], ls='none', marker='o', ms=4.0, mfc=col, mec=col, color=col,
                 zorder=4)
            for pq, v in zip((1, 5, 50, 95), q):
                rec(fid, 'a', f'{lab}, {name}', f'{pq}th percentile of SINR (SNR without interference)', 'dB', v,
                    scn='ISD 200, case A, M.2412 mix', source=f'E1c.3 isd.200.settings.{sn}.A_equal_elements.'
                    f'quantiles_db_1_5_50_95.M2412.7.2GHz.{mt}[{(1, 5, 50, 95).index(pq)}]')
        line(ax, f'f2a|{mt}|threshold', [th[mt], th[mt]], [-0.6, 3.6], color=INK2, lw=0.8, ls=FF.BUDGET_LS,
             zorder=1.5)
        rec(fid, 'a', f'{lab}, F2 threshold', 'SINR threshold', 'dB', th[mt],
            source=f'E1.5.4 summary analytic_thresholds_db.F2.{"DL" if mt == "DL" else "UL"} (= E1c thresholds())')
        ax.set_ylim(-0.6, 3.6)
        ax.set_yticks([3, 2, 1, 0])
        ax.set_yticklabels([n for _, n in MODELS])
        ax.set_xlim(-14, 82)
        ax.set_xticks([-10, 0, 10, 20, 30, 40, 50, 60, 70, 80])
        ax.grid(axis='y', visible=False)
        ax.yaxis.set_label_position('right')
        ax.set_ylabel(lab, rotation=270, va='bottom', labelpad=4)
        if r_ == 0:
            ax.tick_params(labelbottom=False)
        else:
            ax.set_xlabel('SINR (dB); SNR without interference')
    hh = [Line2D([], [], color=INK2, lw=1.0, marker='|', ms=5.5, mew=1.0, label='1st to 95th percentile'),
          Line2D([], [], ls='none', marker='o', ms=4.0, mfc='white', mec=INK2, mew=0.9, label='5th percentile'),
          Line2D([], [], ls='none', marker='o', ms=4.0, mfc=INK2, mec=INK2, label='Median'),
          Line2D([], [], color=INK2, lw=0.8, ls=FF.BUDGET_LS, label='F2 threshold')]
    axs[0].legend(handles=hh, ncol=2, loc='lower left', bbox_to_anchor=(-0.02, 1.02), columnspacing=0.9,
                  handlelength=1.8, handletextpad=0.4)
    describe(fig, axs, '(a) 7.2 GHz SINR percentiles, case A')

    axb = fig.add_subplot(gs[:, 1])
    ypos, ylab = [], []
    y = 11.0
    se_max = 0.0
    for c, clab in CASES:
        tag(axb.text(0.8415, y + 0.05, clab, ha='left', va='center', fontsize=7.5, color=INK), f'f2b|case|{c}')
        y -= 1.0
        for lk, ll in LINKS:
            for k, (sn, name) in enumerate(MODELS):
                v = st200(sn)[c]['mixes']['M2412']['F2'][lk]
                se = st200(sn)[c]['mixes_mc_se']['M2412']['F2'][lk]
                se_max = max(se_max, se)
                hci(axb, f'f2b|{c}|{lk}|{sn}|se', [y], [v - se], [v + se], MOD_COL[sn], cap=0.0)
                line(axb, f'f2b|{c}|{lk}|{sn}', [v], [y], ls='none', marker=MOD_MK[sn], ms=4.4 if sn != 'geo' else 4.8,
                     mfc='white' if sn == 'noise_multi' else MOD_COL[sn], mec=MOD_COL[sn] if sn != 'noise_multi'
                     else '#86847d', mew=0.8, color=MOD_COL[sn], zorder=MOD_Z[sn])
                rec(fid, 'b', f'{clab.split(":")[0]}, {ll}, {name}', 'share of user positions meeting F2', 'fraction', v,
                    lo=v - se, hi=v + se, scn='ISD 200, M.2412 mix; interval = 1 Monte-Carlo SE',
                    source=f'E1c.3 isd.200.settings.{sn}.{c}.mixes.M2412.F2.{lk} (SE: mixes_mc_se)')
            ypos.append(y)
            ylab.append(ll)
            y -= 1.0
        y -= 0.4
    axb.set_yticks(ypos)
    axb.set_yticklabels(ylab)
    axb.set_ylim(y + 0.7, 11.55)
    axb.set_xlim(0.84, 1.003)
    axb.set_xticks([0.85, 0.90, 0.95, 1.00])
    axb.set_xticklabels(['0.85', '0.90', '0.95', '1.00'])
    axb.grid(axis='y', visible=False)
    axb.set_xlabel('Share of user positions meeting F2')
    hb = [Line2D([], [], ls='none', marker=MOD_MK[sn], ms=4.4, mfc='white' if sn == 'noise_multi' else MOD_COL[sn],
                 mec=MOD_COL[sn] if sn != 'noise_multi' else '#86847d', mew=0.8, label=MOD_LEG[sn]) for sn, _ in MODELS]
    axb.legend(handles=hb, ncol=2, loc='lower left', bbox_to_anchor=(-0.02, 1.005), columnspacing=0.9,
               handlelength=1.0, handletextpad=0.4)
    describe(fig, [axb], '(b) UL/DL feasibility (F2) by interference model')
    AXES_REG[fid] = {'a|DL': axs[0], 'a|UL_F2': axs[1], 'b': axb}
    f = lambda sn, c, lk: st200(sn)[c]['mixes']['M2412']['F2'][lk]  # noqa: E731
    qd = lambda sn, i: st200(sn)['A_equal_elements']['quantiles_db_1_5_50_95']['M2412']['7.2GHz']['DL'][i]  # noqa
    FACTS['f2_th_dl'] = f"{TH154['DL']:.2f}"
    FACTS['f2_th_ul'] = f"{TH154['UL']:.2f}"
    a72 = [f"{f(sn, 'A_equal_elements', 'DL_7.2GHz'):.3f}" for sn in ('primary', 'sd', 'geo')]
    FACTS['f2_a_dl72'] = f'{a72[0]} (averaged) to {a72[1]} (directional) and {a72[2]} (pure pattern)'
    FACTS['f2_p1_dl'] = '{}, {} and {}'.format(*(f'{qd(sn, 0):.1f}' for sn in ('primary', 'sd', 'geo')))
    FACTS['f2_p50_dl'] = '{}, {} and {}'.format(*(f'{qd(sn, 2):.1f}' for sn in ('primary', 'sd', 'geo')))
    FACTS['f2_ul_max'] = f"{100 * max(abs(f(sn, c, lk) - f('primary', c, lk)) for sn in ('sd', 'geo') for c, _ in CASES for lk in ('UL_3.5GHz', 'UL_7.2GHz')):.1f}"
    FACTS['f2_c_dl72'] = '{}, {} and {}'.format(*(f"{f(sn, 'C_deployment', 'DL_7.2GHz'):.3f}"
                                                 for sn in ('primary', 'sd', 'geo')))
    FACTS['f2_dl35_sd'] = '{} (A) and {} (C)'.format(*(f"{f('sd', c, 'DL_3.5GHz'):.3f}" for c, _ in CASES))
    FACTS['f2_se_max'] = f'{se_max:.4f}'
    FACTS['f2_victims'] = (f"{C3S['isd']['200']['victims']['outdoor_points']:,} outdoor points and "
                           f"{C3S['isd']['200']['victims']['indoor_samples']:,} indoor samples")
    FACTS['f2_nreal'] = str(C3S['n_realizations'])
    return fid, fig


# ---------------------------------------------------------------- Figure 3 (working 4)
HW = '50dBm_SiGe_SiGe_MRF256'
LOADS = ('0.0', '0.05', '0.15', '0.3', '0.5', '1.0')


def energy_rows():
    rows = {}
    for row in S32C['energy_rows_hw_primary_settings']:
        if (row['policy'] in ('35only', 'aon_ulsel') and row['mix'] == 'ericsson_typical'
                and row['dl_offset_db'] == -3.0 and row['hw'] == HW and row['case35'] == 'right'
                and row['category'] == 'cat1' and row['set'] == 'set1' and row['wake'] == 'proactive'
                and row['smtc'] == 'primary'):
            key = (row['policy'], row['scenario'], float(row['g']))
            if key in rows:
                raise SystemExit(f'duplicate energy row {key}')
            rows[key] = row
    return rows


def fig3():
    fid = 'fig3_power_energy_w4'
    fig = plt.figure(figsize=(FF.DOUBLE, 2.95))
    gs = fig.add_gridspec(1, 2, width_ratios=[1.0, 1.0], left=0.075, right=0.985, top=0.70, bottom=0.215, wspace=0.25)
    ax = fig.add_subplot(gs[0, 0])
    L = [float(k) for k in LOADS]
    p35 = S31['layer_3p5']['P_w_by_case_and_load']
    pt = S31['layer_7p2']['points'][HW]
    paux = S32C['constants']['hardware'][HW]['P_aux_w']
    line(ax, 'f3a|35|right', L, [p35['right'][k] for k in LOADS], color=INK, marker='o', ms=3.4, lw=1.1, zorder=4)
    line(ax, 'f3a|35|left', L, [p35['left'][k] for k in LOADS], color=INK, marker='o', ms=3.4, mfc='white', lw=0.9,
         ls=(0, (3, 1.5)), zorder=4)
    for k in LOADS:
        for case in ('right', 'left'):
            rec(fid, 'a', f'3.5 GHz 64T64R, {case} case', 'power per cell', 'W', p35[case][k], g=k,
                source=f'P3.1 layer_3p5.P_w_by_case_and_load.{case}["{k}"]')
    mk = {'S0': 'o', 'S1': 's', 'S2': '^'}
    for S in ('S0', 'S1', 'S2'):
        ys = [pt['P_w'][f'{k}_{k}'] + paux[S] for k in LOADS]
        line(ax, f'f3a|72|{S}', L, ys, color=SCN_COL[S], marker=mk[S], ms=3.6, lw=1.1, zorder=5 if S == 'S2' else 3)
        for k, v in zip(LOADS, ys):
            rec(fid, 'a', f'7.2 GHz 50 dBm SiGe, M_RF 256, {S}', 'power per cell (DL = UL occupancy)', 'W', v, scn=S,
                g=k, source=f'P3.1 layer_7p2.points.{HW}.P_w["{k}_{k}"] + P3.2c constants.hardware.{HW}.P_aux_w.{S}')
    s2_at = 0.2
    s2_y = pt['P_w']['0.0_0.0'] + paux['S2'] + s2_at * (pt['P_w']['1.0_1.0'] - pt['P_w']['0.0_0.0'])
    t = ax.annotate('7.2 GHz S2 almost coincides with\n3.5 GHz, idle modes (equal at $L$ = 0)',
                    xy=(s2_at, s2_y), xytext=(0.0, 1475), ha='left', va='top', fontsize=7, color=INK2,
                    linespacing=1.1, arrowprops=dict(arrowstyle='-', lw=0.6, color=INK2, shrinkA=1, shrinkB=1))
    tag(t, 'f3a|s2_note')
    ax.set_xlim(-0.03, 1.03)
    ax.set_ylim(0, 1500)
    ax.set_xticks([0, 0.2, 0.4, 0.6, 0.8, 1.0])
    ax.set_xlabel('Resource occupancy $L$')
    ax.set_ylabel('Power per cell (W)')
    ha = [Line2D([], [], color=INK, marker='o', ms=3.4, lw=1.1, label='3.5 GHz, idle modes (primary)'),
          Line2D([], [], color=INK, marker='o', ms=3.4, mfc='white', lw=0.9, ls=(0, (3, 1.5)),
                 label='3.5 GHz, no idle modes')]
    ha += [Line2D([], [], color=SCN_COL[S], marker=mk[S], ms=3.6, lw=1.1,
                  label=f'7.2 GHz, {S}' + (' (published model)' if S == 'S0' else
                                            f' (+{paux[S]:.0f} W assumed)')) for S in ('S0', 'S1', 'S2')]
    ax.legend(handles=ha, ncol=2, loc='lower left', bbox_to_anchor=(-0.02, 1.02), columnspacing=0.8,
              handlelength=2.0, handletextpad=0.4)
    describe(fig, [ax], '(a) Power per cell vs occupancy')

    axb = fig.add_subplot(gs[0, 1])
    rows = energy_rows()
    G = sorted({k[2] for k in rows})
    series = [('35only', 'S0', INK2, 'o', '3.5 GHz only, S0'),
              ('aon_ulsel', 'S0', SCN_COL['S0'], 'o', '7.2 GHz always on, S0'),
              ('aon_ulsel', 'S1', SCN_COL['S1'], 's', '7.2 GHz always on, S1'),
              ('aon_ulsel', 'S2', SCN_COL['S2'], '^', '7.2 GHz always on, S2')]
    ci_half = 0.0
    for pol, S, col, m_, lab in series:
        ys = [rows[(pol, S, g)]['E_site_kWh']['mean'] for g in G]
        feas = [rows[(pol, S, g)]['feasible']['all_user'] for g in G]
        line(axb, f'f3b|{pol}|{S}', G, ys, color=col, lw=1.1, zorder=3)
        gf = [g for g, f_ in zip(G, feas) if f_]
        gi = [g for g, f_ in zip(G, feas) if not f_]
        line(axb, f'f3b|{pol}|{S}|feasible', gf, [rows[(pol, S, g)]['E_site_kWh']['mean'] for g in gf], ls='none',
             marker=m_, ms=3.8, mfc=col, mec=col, color=col, zorder=4)
        line(axb, f'f3b|{pol}|{S}|infeasible', gi, [rows[(pol, S, g)]['E_site_kWh']['mean'] for g in gi], ls='none',
             marker=m_, ms=3.8, mfc='white', mec=col, mew=0.9, color=col, zorder=4)
        lo = [rows[(pol, S, g)]['E_site_kWh']['lo'] for g in G]
        hi = [rows[(pol, S, g)]['E_site_kWh']['hi'] for g in G]
        vci(axb, f'f3b|{pol}|{S}|ci', G, lo, hi, col, cap=0.0)
        for g, v, a_, b_, f_ in zip(G, ys, lo, hi, feas):
            ci_half = max(ci_half, v - a_, b_ - v)
            rec(fid, 'b', lab, 'daily site energy (both layers, 3 sectors)', 'kWh', v, lo=a_, hi=b_, mix='10/90',
                scn=S, g=g, ctrl=pol,
                source=f'P3.2c energy_rows_hw_primary_settings[policy={pol}, mix=ericsson_typical, g={g}, '
                       f'dl_offset_db=-3.0, hw={HW}, case35=right, scenario={S}, category=cat1, set=set1, '
                       f'wake=proactive, smtc=primary].E_site_kWh; feasible.all_user={f_}')
    gs_ = {p: S32C['g_star'][f'{p}|ericsson_typical|-3.0|all_user'] for p in ('35only', 'aon_ulsel')}
    for p, txt in (('35only', '3.5 GHz only'), ('aon_ulsel', 'always on')):
        line(axb, f'f3b|gstar|{p}', [gs_[p], gs_[p]], [50, 142], color=INK2, lw=0.8, ls=(0, (1, 1.5)), zorder=1.5)
        t = axb.text(gs_[p] - 0.05, 140, f'limit, {txt}:\n$g$ = {gs_[p]:.2f}', ha='right', va='top', fontsize=7,
                     color=INK2, linespacing=1.1)
        tag(t, f'f3b|gstar_label|{p}')
        rec(fid, 'b', f'all-user limit, {txt}', 'sustainable growth g*', 'g', gs_[p], mix='10/90',
            source=f'P3.2c g_star["{p}|ericsson_typical|-3.0|all_user"]')
    axb.set_xlim(0.35, 4.15)
    axb.set_ylim(50, 142)
    FF.gtick(axb, (0.5, 1, 1.5, 2, 2.5, 3, 3.5, 4))
    axb.set_xlabel('Traffic growth $g$')
    axb.set_ylabel('Daily energy per site (kWh)')
    hb = [Line2D([], [], color=c_, marker=m_, ms=3.8, lw=1.1, label=l_) for _, _, c_, m_, l_ in series]
    hb.append(Line2D([], [], ls='none', marker='o', ms=3.8, mfc='white', mec=INK2, mew=0.9,
                     label='Open: fails all-user criterion'))
    axb.legend(handles=hb, ncol=2, loc='lower left', bbox_to_anchor=(-0.02, 1.02), columnspacing=0.8,
               handlelength=2.0, handletextpad=0.4)
    describe(fig, [axb], '(b) Daily site energy, UL/DL 10/90, ETSI day profile')
    AXES_REG[fid] = {'a': ax, 'b': axb}
    E = lambda p, S, g: rows[(p, S, g)]['E_site_kWh']['mean']  # noqa: E731
    FACTS['f3_p35_0'] = f"{p35['right']['0.0']:.1f}"
    FACTS['f3_p35_0_left'] = f"{p35['left']['0.0']:.1f}"
    FACTS['f3_p72_0'] = f"{pt['P_w']['0.0_0.0']:.1f}"
    FACTS['f3_s1'] = f"{paux['S1']:.1f}"
    FACTS['f3_s2'] = f"{paux['S2']:.1f}"
    FACTS['f3_ul_term'] = f"{pt['P_w']['0.0_1.0'] - pt['P_w']['0.0_0.0']:.1f}"
    FACTS['f3_s2_gap'] = f"{max(abs(pt['P_w'][f'{k}_{k}'] + paux['S2'] - p35['right'][k]) for k in LOADS):.1f}"
    ds = S32C['constants']['hardware'][HW]['deep_sleep']
    FACTS['f3_deep'] = ', '.join(f"{ds[f'{S}|cat1|set1']['p_w']:.1f}" for S in ('S0', 'S1', 'S2'))
    FACTS['f3_d_g1'] = f"{100 * (E('aon_ulsel', 'S0', 1.0) / E('35only', 'S0', 1.0) - 1):.1f}"
    less = [g for g in G if E('aon_ulsel', 'S0', g) < E('35only', 'S0', g)]
    if less != [g for g in G if less[0] <= g <= less[-1]]:
        raise SystemExit(f'always-on is cheaper than 3.5 GHz only on a non-contiguous set of g: {less}')
    after = [g for g in G if g > less[-1]]
    if not all(E('aon_ulsel', 'S0', g) > E('35only', 'S0', g) for g in after):
        raise SystemExit('always-on is not dearer than 3.5 GHz only at every g above the cheaper range')
    if any(f_ for g in G if g > gs_['35only'] for f_ in [rows[('35only', 'S0', g)]['feasible']['all_user']]):
        raise SystemExit('3.5 GHz only feasible above its g*')
    FACTS['f3_less'] = f'{less[0]:g}–{less[-1]:g}'
    FACTS['f3_more_from'] = f'{after[0]:g}'
    flat = sorted({round(E('35only', 'S0', g), 9) for g in after})
    if flat[-1] - flat[0] > 1e-6:
        raise SystemExit(f'3.5-GHz-only energy is not constant above the cheaper range: {flat}')
    FACTS['f3_35_flat'] = f'{flat[0]:.2f}'
    add = {S: sorted({round(E('aon_ulsel', S, g) - E('aon_ulsel', 'S0', g), 9) for g in G}) for S in ('S1', 'S2')}
    for S in ('S1', 'S2'):
        if add[S][-1] - add[S][0] > 1e-6:
            raise SystemExit(f'always-on {S} increment is not constant over g: {add[S]}')
    FACTS['f3_add'] = ' and '.join(f'{add[S][0]:.2f}' for S in ('S1', 'S2'))
    d35 = {S: [E('35only', S, g) - E('35only', 'S0', g) for g in G] for S in ('S1', 'S2')}
    FACTS['f3_d35'] = ' and '.join(f'{max(d35[S]):.2f}' for S in ('S1', 'S2'))
    FACTS['f3_g35'] = f"{gs_['35only']:.2f}"
    FACTS['f3_gaon'] = f"{gs_['aon_ulsel']:.2f}"
    FACTS['f3_ci'] = f'{ci_half:.2f}'
    FACTS['f3_g_range'] = f'{G[0]:g}–{G[-1]:g}'
    FACTS['f3_x30'] = f"{S32C['calibration']['x_medium_over_busy']:.3f}"
    FACTS['f3_nreal'] = str(S32C['n_realizations'])
    return fid, fig


# ---------------------------------------------------------------- checks and output
def box_checks(fig):
    """Every diagram text lies inside its box with at least 1.5 pt clearance."""
    fig.canvas.draw()
    rnd = fig.canvas.get_renderer()
    bad = []
    for gid, p, t in BOXES:
        bp, bt = p.get_window_extent(rnd), t.get_window_extent(rnd)
        pad = 1.5 * fig.dpi / 72
        if not (bt.x0 >= bp.x0 + pad and bt.x1 <= bp.x1 - pad and bt.y0 >= bp.y0 + pad and bt.y1 <= bp.y1 - pad):
            bad.append(gid)
    return {'n_boxes': len(BOXES), 'text_outside_box': bad}


CAPTIONS = {
    'fig1_architecture_w1': (
        'Fig. 1. Dual-band network and proposed control framework. (a) The 19-site layout at an inter-site distance '
        '(ISD) of 200 m in the Munich scene of Sionna RT: the ITU-R M.2412 hexagonal lattice (rings 0–2, no '
        'wrap-around) anchored at the scene\'s base station (site 0, {f1_site0_h} m). Every other lattice point is '
        'moved to the nearest roof within 0.2·ISD ({f1_n_rooftop} rooftop sites) or, where there is none, given a '
        'ground mast ({f1_n_mast} site); their antennas are at {f1_other_h} m. The map is the scene geometry on the '
        '4 m sample grid within 600 m of site 0: buildings (grey), open outdoor points (white), and grid points '
        'that are neither (light tint; outside the sampled area or excluded by the sampling rules). Each site has '
        'three co-sited sectors with boresights at {f1_bearings} (TR 38.901), numbered '
        '1–3 as the bits of an activation pattern (labels at site 0); each sector has a 3.5 GHz and a 7.2 GHz cell. '
        'Thin lines are the nominal hexagonal cells. Users are scored in site 0\'s cell (bold; '
        '{f1_victims_out} outdoor points and {f1_victims_ind} indoor samples) with their line-of-sight state, '
        'facade and indoor depth toward every site taken from the scene; path loss is TR 38.901 UMa. (b) The '
        'online controller and its offline inputs. Every {f1_slot_min} minutes it forecasts the traffic of the '
        'next {f1_H} slots, keeps the activation patterns whose all-user DL + UL outage stays within {f1_eps}% at '
        'the forecast\'s α-quantile upper bound (all cells on if no pattern does), and chooses by dynamic '
        'programming the sequence with the least forecast energy, with a penalty on wake-gap violations. The '
        'first pattern is applied to the 7.2 GHz cells of every site. α was tuned on development data and frozen '
        '({f1_alpha}). The 3.5 GHz layer is always on.'),
    'fig2_interference_feasibility_w2': (
        'Fig. 2. Directional interference and UL/DL radio feasibility at ISD 200 m with all cells on at full load '
        '(users in site 0\'s cell, {f2_victims}, M.2412 mix of 20% in-car and 80% indoor users; {f2_nreal} '
        'realizations). (a) Recorded 1st, 5th, 50th and 95th percentiles of the 7.2 GHz SINR in case A under four '
        'interference models: none (19-site association, noise only); averaged over beam directions (E1c.2); '
        'directional, with each interfering beam steered to its own scheduled user and the angular spread '
        'calibrated to the measured beamforming deficit (primary, E1c.3); and the pure array pattern. Only these '
        'four percentiles were recorded; no distribution is drawn between them. Dashed lines: the F2 service '
        'thresholds, {f2_th_dl} dB DL and {f2_th_ul} dB UL. The DL 1st percentile is {f2_p1_dl} dB under the '
        'averaged, directional and pure-pattern models, with medians of {f2_p50_dl} dB. (b) Share of user '
        'positions meeting F2 (the M.2410 Dense Urban 5th-percentile user spectral efficiency with 10 users per '
        'cell) per '
        'carrier and direction, in case A and in case C, the deployment configuration on which Phase 3 builds '
        '(100/200 MHz, 16×32 array at 7.2 GHz; here at 53 dBm, Phase 3\'s primary point is 50 dBm). Error bars '
        'are one Monte-Carlo standard error (at most {f2_se_max}), smaller than the markers. In case A the '
        '7.2 GHz DL share falls from {f2_a_dl72}, while every UL share stays within {f2_ul_max} points of the '
        'averaged model\'s. In case C the larger 7.2 GHz array keeps the DL share at {f2_c_dl72}, whereas the '
        '3.5 GHz DL share under directional interference is {f2_dl35_sd}.'),
    'fig3_power_energy_w4': (
        'Fig. 3. Base-station power and hardware-overhead sensitivity. (a) Power per cell against resource '
        'occupancy L from the source-reproduced models, at the recorded points (markers) joined by straight '
        'segments; the 3.5 GHz model is affine in L, and the 7.2 GHz model departs from the segments by less '
        'than 1 mW. Shown are the measurement-anchored 3.5 GHz 64T64R model with its idle modes (µDTX and AFE '
        'idle; primary; {f3_p35_0} W at no load) and without them ({f3_p35_0_left} W), and the published FR3 model '
        'at 7.2 GHz (50 dBm, SiGe power amplifiers, M_RF = 256; {f3_p72_0} W at no load) with '
        'equal DL and UL occupancy (the UL term adds at most {f3_ul_term} W). S1 and S2 add an assumed equipment '
        'overhead in every in-frame (not sleeping) state, and the sleep powers scale with it: {f3_s1} W (measured '
        'components the FR3 model omits) and {f3_s2} W (the '
        'no-load power raised to the 3.5 GHz layer\'s {f3_p35_0} W), so the S2 line stays within {f3_s2_gap} W '
        'of the 3.5 GHz idle-mode line. S1 and S2 are stress tests, not measured FR3 values. Deep sleep (Cat 1) '
        'draws {f3_deep} W under S0, S1, S2. (b) Daily energy per site (three '
        'sectors, both layers) at UL/DL 10/90 over the ETSI ES 202 706-1 day: no traffic 00–06 h, medium '
        'level (g·{f3_x30}) 06–16 h and busy level (g) 16–24 h, g = {f3_g_range}. Open markers fail the '
        'all-user criterion in some level of the day; dotted lines mark the limits g = {f3_g35} (3.5 GHz only) '
        'and g = {f3_gaon} (always on). The 95% intervals over the {f3_nreal} radio realizations are at most '
        '±{f3_ci} kWh. Under S0, always-on uses {f3_d_g1}% more energy than 3.5 GHz only at g = 1, less at '
        'g = {f3_less}, where 3.5 GHz only already fails, and more again from g = {f3_more_from}, where the '
        '3.5-GHz-only energy stays at {f3_35_flat} kWh. S1 and S2 add {f3_add} kWh per day. The 3.5-GHz-only '
        'curve (7.2 GHz cells in deep sleep all day) is drawn for S0; under S1 and S2 it is higher by at most '
        '{f3_d35} kWh (deep-sleep power).'),
}


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / 'source_data').mkdir(exist_ok=True)
    plotted, outputs, layout, descs = {}, {}, {}, {}
    for build in (fig1, fig2, fig3):
        fid, fig = build()
        FF.place_descriptions(fig)
        descs[fid] = [t for _, t, _ in fig._descs]
        layout[fid] = FF.layout_checks(fig)
        if fid.startswith('fig1'):
            CHECKS['fig1_boxes'] = box_checks(fig)
        plotted[fid] = FF.readback(fig)
        if fid in AXES_REG:
            plotted[fid]['_axes'] = {n: {'yticks': [float(v) for v in a.get_yticks()],
                                         'yticklabels': [t.get_text() for t in a.get_yticklabels()],
                                         'ylabel': a.get_ylabel(),
                                         'gids': sorted({o.get_gid() for o in a.findobj() if o.get_gid()})}
                                     for n, a in AXES_REG[fid].items()}
        for fmt in ('pdf', 'png'):
            b1, b2 = FF.render(fig, fmt), FF.render(fig, fmt)
            if b1 != b2:
                raise SystemExit(f'{fid}.{fmt}: rendering is not byte-identical')
            (OUT / f'{fid}.{fmt}').write_bytes(b1)
            outputs[f'{fid}.{fmt}'] = hashlib.sha256(b1).hexdigest()
        plt.close(fig)
        FF.write_csv(OUT / 'source_data' / f'{fid}.csv', FF.SOURCE[fid])
        outputs[f'source_data/{fid}.csv'] = sha(OUT / 'source_data' / f'{fid}.csv')
        print(f'{fid}: min font {layout[fid]["min_font_pt"]} pt, {layout[fid]["n_texts"]} texts, '
              f'{len(plotted[fid])} tagged artists, problems: {layout[fid]["problems"] or "none"}', flush=True)
    caps = ['# Figure captions, Figures 1–3', '',
            'Generated by `scripts/figures/model_figures.py` from the validated Phase 2 (E1c.2, E1c.3) and Phase 3 '
            '(P3.1, P3.2c) outputs, the frozen P3.4 parameters and the P3.3 code; every number in braces in the '
            f'source template is a value in `caption_facts_{SUFFIX}.json`, recomputed by '
            '`scripts/figures/verify_model_figures.py`.', '']
    for fid, txt in CAPTIONS.items():
        caps += [f'## {fid}', '', '**Panel descriptions (under each panel):** ' + ' | '.join(descs[fid]), '',
                 txt.format(**FACTS), '']
    (OUT / f'captions_{SUFFIX}.md').write_text('\n'.join(caps), encoding='utf-8', newline='\n')
    (OUT / f'caption_facts_{SUFFIX}.json').write_text(json.dumps(FACTS, indent=1, ensure_ascii=False) + '\n',
                                                      encoding='utf-8', newline='\n')
    (OUT / f'plotted_values_{SUFFIX}.json').write_text(json.dumps(plotted, separators=(',', ':')) + '\n',
                                                       encoding='utf-8', newline='\n')
    for f in (f'captions_{SUFFIX}.md', f'caption_facts_{SUFFIX}.json', f'plotted_values_{SUFFIX}.json'):
        outputs[f] = sha(OUT / f)
    if check_inputs() != INPUTS_BEFORE or FF.check_inputs() != FF.INPUTS_BEFORE:
        raise SystemExit('inputs changed during plotting')
    head = subprocess.run(['git', '-C', str(ROOT), 'rev-parse', 'HEAD'], capture_output=True, text=True).stdout.strip()
    ok_layout = all(not v['problems'] and v['min_font_pt'] >= FF.MIN_PT for v in layout.values())
    ok_checks = CHECKS['fig1_victims_inside_bold_cell']['passed'] and not CHECKS['fig1_boxes']['text_outside_box']
    manifest = {
        'tool': 'scripts/figures/model_figures.py',
        'script_sha256': sha(Path(__file__)),
        'shared_style_module': {'path': 'scripts/figures/frozen_figures.py', 'sha256': sha(Path(FF.__file__))},
        'git_head_at_run': head,
        'python': platform.python_version(), 'numpy': np.__version__, 'matplotlib': matplotlib.__version__,
        'font': {'family': 'STIXGeneral', 'file': Path(FF.FONT_PATH).name, 'sha256': sha(FF.FONT_PATH),
                 'pdf_fonttype': 42},
        'width_in': FF.DOUBLE,
        'input_sha256': INPUTS_BEFORE,
        'shared_module_input_sha256': FF.INPUTS_BEFORE,
        'inputs_unchanged_after_plotting': True,
        'deterministic_rendering': True,
        'outputs_sha256': outputs,
        'layout_checks': layout,
        'build_checks': CHECKS,
        'all_layout_checks_passed': bool(ok_layout and ok_checks),
    }
    (OUT / f'manifest_{SUFFIX}.json').write_text(json.dumps(manifest, indent=2) + '\n', encoding='utf-8', newline='\n')
    print('layout checks passed' if manifest['all_layout_checks_passed'] else 'LAYOUT PROBLEMS (see manifest)')
    print(json.dumps(CHECKS, indent=1)[:1500])


if __name__ == '__main__':
    main()
