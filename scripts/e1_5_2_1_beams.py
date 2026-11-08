#!/usr/bin/env python3
"""E1.5.2-1: realised gNB array gain in the TR 38.901 UMa channel.

Implements configs/e1/e1_5_2_1_beams.json. Run inside WSL with the project venv.
On every stored channel realization: NR DirectPath, the best beam of an oversampled
DFT codebook, and the frequency-flat single-beam bound (largest eigenvalue).
Checks R1, S1, I1-I4, D1, N1, V1; statistics with positions as the independent
units. Outputs in --outdir: validation.json, summary.json, manifest.json,
realizations.csv.gz; raw files in raw/ (git-ignored).

  python scripts/e1_5_2_1_beams.py --git-head <sha>      # run and analyse
  python scripts/e1_5_2_1_beams.py --reanalyse           # analyse raw/ again (hash-checked)
"""
import argparse
import csv
import gzip
import io
import json
import math
import platform
import shutil
import subprocess
import sys
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'scripts'))
from cmwave.radio import antenna as ant  # noqa: E402
from cmwave.radio import link_budget as lb  # noqa: E402
import e1_5_1_deterministic as e151  # noqa: E402
import e1_5_1d_tdma_diagnostic as diag  # noqa: E402
import e1_5_2_0_conventions as e1520  # noqa: E402

CONFIG = ROOT / 'configs/e1/e1_5_2_1_beams.json'
E151_CONFIG = ROOT / 'configs/e1/e1_5_1_deterministic.json'
E1520_RESULTS = ROOT / 'results/phase2/e1_5_2_0_conventions'
OUTDIR = ROOT / 'results/phase2/e1_5_2_1_beams'
BEAMS = ('dp', 'cb', 'cb1', 'eigpo', 'eig', 'mrt')
PAIRS = (('cb', 'dp'), ('eig', 'cb'), ('eig', 'dp'), ('mrt', 'eig'), ('cb', 'cb1'), ('eig', 'eigpo'))
BEAMS_FILES = ('links.csv', 'e1521_real.csv', 'e1521_det.csv', 'e1521_info.csv', 'e1521_h.bin',
               'e1521_h_index.csv', 'run.log')
CHAN_FILES = ('links.csv', 'e152_det.csv', 'e152_real.csv', 'e152_info.csv', 'run.log')
NR_FILES = ('links.csv', 'e15_dl_tb.csv', 'e1521_nr_beam.csv', 'e15_run_info.csv', 'e15_rrc_events.csv',
            'e15_rb_after_attach.csv', 'run.log')
BOOT_SEED = 15211
BOOT_N = 2000
TOL_DB = 1e-6


def db(x):
    return 10.0 * math.log10(x)


# ---------------------------------------------------------------- inputs
def links_for(frequency_hz, rows, cols, medians, stratum):
    """E1.5.2-0 links (E1.4 sector, Python angles and gain) with the stratum's condition."""
    gnb, links = e1520.build_links(frequency_hz, rows, cols, medians)
    for l in links:
        l['geom_los'] = l['los']
        if stratum == 'o2i':
            l['los'], l['o2i'] = 0, 1   # hazard 13: never LOS together with O2I
        elif stratum != 'o2o':
            raise ValueError(f'unknown stratum {stratum}')
    return gnb, links


def common_args(c, frequency_hz, gnb, rng_run):
    return [f'--frequency={frequency_hz!r}', f'--bandwidth={c["bandwidth_hz"]!r}',
            f'--numerology={c["numerology"]}', f'--rbOverhead={c["rb_overhead"]!r}',
            f'--gnbX={gnb[0]!r}', f'--gnbY={gnb[1]!r}', f'--gnbZ={gnb[2]!r}', f'--RngRun={rng_run}']


def fresh(path):
    shutil.rmtree(path, ignore_errors=True)
    path.mkdir(parents=True)
    return path


# ---------------------------------------------------------------- independent DirectPath (I4)
def element_locations(rows, cols, bearing_deg, spacing=0.5):
    """UniformPlanarArray::GetElementLocation (downtilt 0), in wavelengths, GCS."""
    a = math.radians(float(ant.wrap_deg(bearing_deg)))
    n = np.arange(rows * cols)
    yp = spacing * (n % cols)
    zp = spacing * (n // cols)
    return np.stack([-math.sin(a) * yp, math.cos(a) * yp, zp], axis=1)


NS3_INT_RANGE = 100000000000


def ns3_wrap_to_pi(a):
    """ns-3 WrapToPi (src/antenna/model/angles.cc): the angle is TRUNCATED to a grid of
    2 pi / 1e11 (int64 conversion), so Angles::GetAzimuth() differs from atan2 by up to
    6.3e-11 rad. C++ integer division and remainder truncate toward zero."""
    b = int(a / (2 * math.pi) * NS3_INT_RANGE)
    b = int(math.fmod(b, NS3_INT_RANGE))
    if b < -NS3_INT_RANGE // 2:
        b += NS3_INT_RANGE
    elif b >= NS3_INT_RANGE // 2:
        b -= NS3_INT_RANGE
    return b * (2 * math.pi) / NS3_INT_RANGE


def direct_path_vector(gnb, ue, rows, cols, bearing_deg, ns3_azimuth=True):
    """NR CreateDirectPathBfv: exp(-j 2 pi r.d) / sqrt(N), r the unit vector gNB -> UE, with
    the angles of ns-3's Angles(v) (azimuth atan2 then WrapToPi, inclination acos(z / |v|)).
    ns3_azimuth=False gives the exact geometric direction (reported only)."""
    dx, dy, dz = (float(u) - float(g0) for u, g0 in zip(ue, gnb))
    ph = math.atan2(dy, dx)
    if ns3_azimuth:
        ph = ns3_wrap_to_pi(ph)
    th = math.acos(dz / math.sqrt(dx * dx + dy * dy + dz * dz))
    r = np.array([math.sin(th) * math.cos(ph), math.sin(th) * math.sin(ph), math.cos(th)])
    d = element_locations(rows, cols, bearing_deg)
    return np.exp(-2j * math.pi * (d @ r)) / math.sqrt(rows * cols)


def codebook_matrix(rows, cols, o):
    """Explicit N x B oversampled 2D DFT codebook; element n = row cols + col, beam b = q (o cols) + p."""
    ph, pv = o * cols, o * rows
    wh = np.exp(-2j * np.pi * ((np.outer(np.arange(cols), np.arange(ph)) % ph) / ph))
    wv = np.exp(-2j * np.pi * ((np.outer(np.arange(rows), np.arange(pv)) % pv) / pv))
    return np.einsum('rq,cp->rcqp', wv, wh).reshape(rows * cols, ph * pv) / math.sqrt(rows * cols)


def read_dump(rawdir):
    idx = e151.read_csv(rawdir / 'e1521_h_index.csv')
    raw = np.fromfile(rawdir / 'e1521_h.bin', dtype='<f8')
    out = {}
    for e in idx:
        l, cp, k, n, off = (int(e[f]) for f in ('link_index', 'copy', 'k', 'n', 'offset_bytes'))
        z = raw[off // 8: off // 8 + 2 * k * n]
        if z.size != 2 * k * n:
            raise SystemExit(f'{rawdir}: truncated h dump')
        out[(l, cp)] = (z[0::2] + 1j * z[1::2]).reshape(k, n)
    return out


def i4_check(rawdir, real, links, gnb, rows, cols, o, tol=1e-9, tol_eig=1e-8):
    """Independent numpy recomputation from the dumped per-RB channels."""
    W = codebook_matrix(rows, cols, o)
    ph, pv = o * cols, o * rows
    sub = np.array([(b % ph) % o == 0 and (b // ph) % o == 0 for b in range(ph * pv)])
    worst = defaultdict(float)
    dump = read_dump(rawdir)
    for (l, cp), h in dump.items():
        r = real[(l, cp)]
        lk = links[l]
        ue = [lk['x'], lk['y'], lk['z']]
        w = direct_path_vector(gnb, ue, rows, cols, lk['bearing_deg'])
        g_dp = float(np.mean(np.abs(h @ w) ** 2))
        w_exact = direct_path_vector(gnb, ue, rows, cols, lk['bearing_deg'], ns3_azimuth=False)
        worst_exact = max(worst.get('reported_dp_exact_geometry', 0.0),
                          abs(float(np.mean(np.abs(h @ w_exact) ** 2)) / float(r['g_dp']) - 1))
        g_all = np.mean(np.abs(h @ W) ** 2, axis=0)
        sel = int(r['cb_q']) * ph + int(r['cb_p'])
        sel1 = int(r['cb1_q']) * ph + int(r['cb1_p'])
        R = (h.conj().T @ h) / h.shape[0]
        lam = float(np.linalg.eigvalsh(R)[-1])
        mrt = float(np.mean(np.sum(np.abs(h) ** 2, axis=1)))
        rel = {'dp': abs(g_dp / float(r['g_dp']) - 1),
               'cb_max': abs(g_all.max() / float(r['g_cb']) - 1),
               'cb_selected': abs(g_all[sel] / g_all.max() - 1),
               'cb1_max': abs(g_all[sub].max() / float(r['g_cb1']) - 1),
               'cb1_selected': abs(g_all[sel1] / g_all[sub].max() - 1),
               'eig': abs(lam / float(r['g_eig']) - 1),
               'mrt': abs(mrt / float(r['g_mrt']) - 1)}
        for k, v in rel.items():
            worst[k] = max(worst[k], v)
        worst['reported_dp_exact_geometry'] = worst_exact
    passed = len(dump) == len(links) and all(v <= (tol_eig if k == 'eig' else tol) for k, v in worst.items()
                                             if not k.startswith('reported_'))
    return {'realizations': len(dump), 'max_rel_error': dict(worst), 'passed': bool(passed)}


# ---------------------------------------------------------------- checks on one beams run
def read_real(rawdir, n_links, copies):
    rows = e151.read_csv(rawdir / 'e1521_real.csv')
    real = {}
    for r in rows:
        k = (int(r['link_index']), int(r['copy']))
        if k in real:
            raise SystemExit(f'{rawdir}: duplicate realization {k}')
        real[k] = r
    complete = sorted(real) == [(l, c) for l in range(n_links) for c in range(copies)]
    return real, complete


def g(r, beam):
    return float(r[f'g_{beam}'])


def beams_checks(real, complete, info, links, run, conf):
    """S1, I1, I2, I3, D1 for one beams run."""
    s1_cluster = True
    for (l, _), r in real.items():
        n_cl = int(r['n_clusters'])
        lim = 24 if (run['stratum'] == 'o2o' and links[l]['los'] == 0) else 16
        s1_cluster &= 1 <= n_cl <= lim
    s1 = {'complete': complete, 'rows': len(real),
          'gnb_is_s_all': all(int(r['gnb_is_s']) == 1 for r in real.values()),
          'frequency_ok': float(info['frequency_hz_readback']) == run['frequency_hz'],
          'scenario_ok': info['scenario_readback'] == 'UMa', 'num_rbs_ok': int(info['num_rbs']) == 51,
          'rng_run_ok': int(info['rng_run']) == conf['rng']['RngRun'],
          'eig_uncertified': int(info['eig_uncertified']), 'n_clusters_ok': bool(s1_cluster)}
    s1['passed'] = bool(complete and s1['gnb_is_s_all'] and s1['frequency_ok'] and s1['scenario_ok']
                        and s1['num_rbs_ok'] and s1['rng_run_ok'] and s1['eig_uncertified'] == 0
                        and s1['n_clusters_ok'])
    i1 = max(abs(db(g(r, 'dp') / g(r, 'ns3_dp'))) for r in real.values())
    i2 = max(abs(db(g(r, 'ns3_cb') / g(r, 'cb'))) for r in real.values())
    i3a = max(abs(db(g(r, 'ns3_eig') / g(r, 'eigvec'))) for r in real.values())
    i3b = max(abs(db(g(r, 'eigvec') / g(r, 'eig'))) for r in real.values())
    cert = all(int(r['eig_certified']) == 1 for r in real.values())
    d_bound = max(max(g(r, b) for b in ('dp', 'cb', 'cb1', 'eigpo', 'eigvec')) / g(r, 'eig') for r in real.values())
    d_mrt = max(g(r, 'eig') / g(r, 'mrt') for r in real.values())
    d_sub = all(g(r, 'cb1') <= g(r, 'cb') for r in real.values())
    return {
        'S1_bookkeeping': s1,
        'I1_reconstruction': {'max_abs_db': i1, 'passed': bool(i1 <= TOL_DB)},
        'I2_codebook_in_ns3': {'max_abs_db': i2, 'passed': bool(i2 <= TOL_DB)},
        'I3_eigen_in_ns3': {'max_abs_db_ns3_vs_eigvec': i3a, 'max_abs_db_eigvec_vs_eig': i3b,
                            'all_certified': cert, 'passed': bool(cert and i3a <= TOL_DB and i3b <= TOL_DB)},
        'D1_dominance': {'max_ratio_beam_over_bound': d_bound, 'max_ratio_eig_over_mrt': d_mrt,
                         'cb1_le_cb_all': d_sub,
                         'passed': bool(d_bound <= 1 + 2e-9 and d_mrt <= 1 + 1e-12 and d_sub)},
    }


# ---------------------------------------------------------------- statistics
def bootstrap_median_ci(values, n=BOOT_N, seed=BOOT_SEED):
    v = np.asarray(values, dtype=float)
    if v.size == 0:
        return None
    rng = np.random.default_rng(seed)
    meds = np.median(v[rng.integers(0, v.size, size=(n, v.size))], axis=1)
    return [float(np.percentile(meds, 2.5)), float(np.percentile(meds, 97.5))]


def summarise(values):
    v = np.asarray(values, dtype=float)
    return {'n_positions': int(v.size), 'median': float(np.median(v)), 'ci95': bootstrap_median_ci(v),
            'min': float(v.min()), 'max': float(v.max())}


def ranks(x):
    x = np.asarray(x, dtype=float)
    order = np.argsort(x, kind='mergesort')
    r = np.empty(len(x))
    i = 0
    while i < len(x):
        j = i
        while j + 1 < len(x) and x[order[j + 1]] == x[order[i]]:
            j += 1
        r[order[i:j + 1]] = (i + j) / 2.0 + 1
        i = j + 1
    return r


def spearman(x, y):
    if len(x) < 3:
        return None
    return float(np.corrcoef(ranks(x), ranks(y))[0, 1])


def position_table(real, links, copies, d2d):
    """Per position: G_x (median dB), mean-linear dB, p5, p95, deficit, paired deltas."""
    out = []
    for l, lk in enumerate(links):
        rs = [real[(l, c)] for c in range(copies)]
        row = {'link_index': l, 'name': lk['name'], 'geom_los': lk['geom_los'], 'bearing_deg': lk['bearing_deg'],
               'd2d_m': d2d[lk['name']], 'e14_gain_db': lk['py_gain_db'],
               'n_clusters_median': float(np.median([int(r['n_clusters']) for r in rs]))}
        for b in BEAMS:
            gl = np.array([g(r, b) for r in rs])
            gd = 10 * np.log10(gl)
            row[b] = {'median_db': float(np.median(gd)), 'mean_lin_db': float(10 * np.log10(gl.mean())),
                      'p5_db': float(np.percentile(gd, 5)), 'p95_db': float(np.percentile(gd, 95)),
                      'deficit_db': float(lk['py_gain_db'] - np.median(gd))}
        for x, y in PAIRS:
            dd = [db(g(r, x) / g(r, y)) for r in rs]
            row[f'delta_{x}_{y}_db'] = float(np.median(dd))
        cbs = defaultdict(int)
        for r in rs:
            cbs[(int(r['cb_p']), int(r['cb_q']))] += 1
        (p, q), cnt = max(cbs.items(), key=lambda kv: (kv[1], -kv[0][1], -kv[0][0]))
        row['cb_mode_beam'] = {'p': p, 'q': q, 'share': cnt / copies, 'distinct_beams': len(cbs)}
        out.append(row)
    return out


def strata_of(run, table):
    if run['stratum'] == 'o2o':
        return {'o2o_los': [r for r in table if r['geom_los'] == 1],
                'o2o_nlos': [r for r in table if r['geom_los'] == 0]}
    return {'o2i': table, 'o2i_geom_los': [r for r in table if r['geom_los'] == 1],
            'o2i_geom_nlos': [r for r in table if r['geom_los'] == 0]}


def stratum_summaries(run, table):
    out = {}
    for s, rows in strata_of(run, table).items():
        e = {'deficit_db': {b: summarise([r[b]['deficit_db'] for r in rows]) for b in BEAMS},
             'realised_median_db': {b: summarise([r[b]['median_db'] for r in rows]) for b in BEAMS},
             'paired_delta_db': {f'{x}_minus_{y}': summarise([r[f'delta_{x}_{y}_db'] for r in rows])
                                 for x, y in PAIRS},
             'spearman_deficit_vs_d2d': {b: spearman([r['d2d_m'] for r in rows], [r[b]['deficit_db'] for r in rows])
                                         for b in BEAMS}}
        out[s] = e
    return out


def config_comparisons(tables, runs):
    """Paired by position: (a, 7.2) - (a, 3.5) and (b, 7.2) - (a, 7.2), per stratum."""
    by = {(r['array'], r['frequency_hz'], r['stratum']): r['tag'] for r in runs}
    out = {}
    for stratum in ('o2o', 'o2i'):
        for name, (x, y) in {'a7p2_minus_a3p5': (('a', 7.2e9), ('a', 3.5e9)),
                             'b7p2_minus_a7p2': (('b', 7.2e9), ('a', 7.2e9))}.items():
            tx, ty = by[x + (stratum,)], by[y + (stratum,)]
            ta, tb = tables[tx], tables[ty]
            for s, rows in strata_of({'stratum': stratum}, ta).items():
                names = {r['name'] for r in rows}
                da = {r['name']: r for r in ta if r['name'] in names}
                dbb = {r['name']: r for r in tb if r['name'] in names}
                out.setdefault(name, {})[s] = {
                    b: summarise([da[n][b]['deficit_db'] - dbb[n][b]['deficit_db'] for n in sorted(names)])
                    for b in BEAMS}
    return out


def gain_model(summ, runs):
    """Decision rule: D_cb per stratum, carrier, array (+ dp / eig sensitivities)."""
    out = []
    for r in runs:
        for s, e in summ[r['tag']].items():
            if s not in ('o2o_los', 'o2o_nlos', 'o2i'):
                continue
            out.append({'array': r['array'], 'frequency_hz': r['frequency_hz'], 'stratum': s,
                        'D_cb_db': e['deficit_db']['cb']['median'], 'D_cb_ci95': e['deficit_db']['cb']['ci95'],
                        'D_dp_db': e['deficit_db']['dp']['median'], 'D_eig_db': e['deficit_db']['eig']['median'],
                        'n_positions': e['deficit_db']['cb']['n_positions']})
    return out


# ---------------------------------------------------------------- N1, V1
def n1_check(rawdir, links, tol=0.25):
    acc = defaultdict(list)
    for r in e151.read_csv(rawdir / 'e152_real.csv'):
        l = links[int(r['link_index'])]
        key = 'o2i' if l['o2i'] else ('los' if l['los'] else 'nlos')
        acc[key].append(float(r['gain_lin']))
    out = {k: {'realizations': len(v), 'mean_gain_db': db(float(np.mean(v)))} for k, v in acc.items()}
    for v in out.values():
        v['passed'] = bool(abs(v['mean_gain_db']) <= tol)
    return out


def v1_check(rawdir, links, cfg, tol=0.01):
    info = {r['key']: r['value'] for r in e151.read_csv(rawdir / 'e15_run_info.csv')}
    beam = {int(r['ue_index']): r for r in e151.read_csv(rawdir / 'e1521_nr_beam.csv')}
    loss = np.array([l['loss_db'] for l in links])
    snr_iso = lb.downlink(loss, cfg)['snr_db']
    tbs, rows = e151.unique_tbs(e151.read_csv(rawdir / 'e15_dl_tb.csv'), e151.DL_TB_KEY)
    worst, count, rb_ok = 0.0, defaultdict(int), True
    for r in tbs:
        i = int(r['ue_index'])
        ref = snr_iso[i] + db(float(beam[i]['gain_lin']))
        worst = max(worst, abs(db(float(r['sinr_lin'])) - ref))
        count[i] += 1
        rb_ok &= int(r['rb_assigned']) == 51
    n = len(links)
    res = {'tbs': len(tbs), 'trace_rows': rows, 'max_abs_error_db': worst,
           'gnb_is_s_all': all(int(beam[i]['gnb_is_s']) == 1 for i in range(n)),
           'bm_vector_match_all': all(int(beam[i]['bm_vector_match']) == 1 for i in range(n)),
           'channel_generated_at_0_all': all(float(beam[i]['channel_generated_s']) == 0.0 for i in range(n)),
           'all_tbs_51_rbs': bool(rb_ok), 'min_tbs_per_ue': min(count.get(i, 0) for i in range(n)),
           'frequency_readback_ok': float(info['channel_model_frequency_hz_readback']) == cfg.frequency_hz}
    res['passed'] = bool(worst <= tol and res['gnb_is_s_all'] and res['bm_vector_match_all']
                         and res['channel_generated_at_0_all'] and rb_ok and res['min_tbs_per_ue'] >= 10
                         and res['frequency_readback_ok'])
    return res, {links[i]['name']: db(float(beam[i]['gain_lin'])) for i in range(n)}


# ---------------------------------------------------------------- R1
def r1_check(outdir, git_head):
    r1dir = outdir / 'r1_e1520_rerun'
    cmd = [sys.executable, str(ROOT / 'scripts/e1_5_2_0_conventions.py'), '--outdir', str(r1dir),
           '--git-head', str(git_head)]
    t0 = time.time()
    p = subprocess.run(cmd, capture_output=True, text=True)
    (outdir / 'r1_stdout.txt').write_text(p.stdout + '\n--- stderr\n' + p.stderr, encoding='utf-8')
    ref = {r['tag']: r for r in json.loads((E1520_RESULTS / 'manifest.json').read_text())['runs']}
    new = {r['tag']: r for r in json.loads((r1dir / 'manifest.json').read_text())['runs']} \
        if (r1dir / 'manifest.json').exists() else {}
    differ, n = [], 0
    for tag in sorted(set(ref) | set(new)):
        ha, hb = ref.get(tag, {}).get('raw_sha256', {}), new.get(tag, {}).get('raw_sha256', {})
        for f in sorted(set(ha) | set(hb)):
            if f == 'run.log':
                continue
            n += 1
            if ha.get(f) != hb.get(f):
                differ.append(f'{tag}/{f}')
    val = json.loads((r1dir / 'validation.json').read_text()) if (r1dir / 'validation.json').exists() else {}
    res = {'exit_code': p.returncode, 'runs': len(new), 'files_compared': n, 'differing_files': differ,
           'e1520_all_checks_passed': bool(val.get('all_checks_passed')), 'runtime_s': time.time() - t0}
    res['passed'] = bool(p.returncode == 0 and len(new) == len(ref) == 21 and not differ and n > 0
                         and res['e1520_all_checks_passed'])
    return res


# ---------------------------------------------------------------- main
def execute(conf, c, outdir, ns3, medians, args):
    """Run every program instance; raw files are copied to outdir/raw/<tag>/."""
    work = Path.home() / 'cmwave-runs/e1_5_2_1'
    raw = outdir / 'raw'
    runs_meta = []
    rng_run = conf['rng']['RngRun']
    copies = conf['realizations_per_position']
    for r in conf['n1_runs']:
        gnb, links = links_for(r['frequency_hz'], 1, 1, medians, r['stratum'])
        rd = fresh(work / r['tag'])
        e1520.write_links(rd / 'links.csv', links)
        a = ['--mode=chan', f'--linksFile={rd / "links.csv"}'] + common_args(c, r['frequency_hz'], gnb, rng_run) + \
            ['--gnbRows=1', '--gnbCols=1', '--gnbElement=iso', f'--copies={copies}', '--gnbRole=s', '--gnbWeights=dp']
        cmd, code, rt = e1520.run_program(ns3, rd, a)
        h = e1520.keep(rd, raw / r['tag'], CHAN_FILES)
        runs_meta.append({'tag': r['tag'], 'cmd': cmd, 'exit_code': code, 'runtime_s': rt, 'raw_sha256': h})
        print(f"{r['tag']}: exit {code}, {rt:.0f} s", flush=True)
        if code != 0:
            raise SystemExit(f"{r['tag']} failed; see {rd / 'run.log'}")
    for r in conf['beams_runs']:
        arr = conf['arrays'][r['array']]
        gnb, links = links_for(r['frequency_hz'], arr['rows'], arr['cols'], medians, r['stratum'])
        rd = fresh(work / r['tag'])
        e1520.write_links(rd / 'links.csv', links)
        a = ['--mode=beams', f'--linksFile={rd / "links.csv"}'] + common_args(c, r['frequency_hz'], gnb, rng_run) + \
            [f'--gnbRows={arr["rows"]}', f'--gnbCols={arr["cols"]}', f'--copies={copies}',
             f'--oversampling={conf["oversampling"]}', f'--dumpCopies={conf["dump_copies"]}']
        cmd, code, rt = e1520.run_program(ns3, rd, a)
        h = e1520.keep(rd, raw / r['tag'], BEAMS_FILES)
        runs_meta.append({'tag': r['tag'], 'cmd': cmd, 'exit_code': code, 'runtime_s': rt, 'raw_sha256': h})
        print(f"{r['tag']}: exit {code}, {rt:.0f} s", flush=True)
        if code != 0:
            raise SystemExit(f"{r['tag']} failed; see {rd / 'run.log'}")
    s = conf['nr_runs']['settings']
    for r in conf['nr_runs']['configs']:
        arr = conf['arrays'][r['array']]
        gnb, links = links_for(r['frequency_hz'], arr['rows'], arr['cols'], medians, r['stratum'])
        for bearing in ant.TR38901_SECTOR_BEARINGS_DEG:
            sub = [l for l in links if l['bearing_deg'] == bearing]
            if not sub:
                continue
            tag = f"{r['tag']}_b{int(bearing)}"
            rd = fresh(work / tag)
            e1520.write_links(rd / 'links.csv', sub)
            a = ['--mode=nrfading', f'--linksFile={rd / "links.csv"}'] + \
                common_args(c, r['frequency_hz'], gnb, s['rng_run']) + \
                [f'--gnbTxPower={c["gnb_tx_power_dbm"]!r}', f'--nfGnb={c["nf_gnb_db"]!r}',
                 f'--nfUe={c["nf_ue_db"]!r}', f'--gnbRows={arr["rows"]}', f'--gnbCols={arr["cols"]}',
                 f'--bearingDeg={bearing!r}', f'--scheduler={s["scheduler"]}',
                 f'--dlRate={s["dl_rate_bps_per_ue"]!r}', f'--packetSize={s["packet_size_bytes"]}',
                 f'--appStart={s["app_start_s"]!r}', f'--simTime={s["sim_time_s"]!r}',
                 f'--tddPattern={c["tdd_pattern"]}', f'--gnbBeam={r["beam"]}',
                 f'--oversampling={conf["oversampling"]}']
            cmd, code, rt = e1520.run_program(ns3, rd, a)
            h = e1520.keep(rd, raw / tag, NR_FILES)
            runs_meta.append({'tag': tag, 'cmd': cmd, 'exit_code': code, 'runtime_s': rt, 'raw_sha256': h})
            print(f'{tag}: exit {code}, {rt:.0f} s', flush=True)
    return runs_meta


def analyse(conf, c, outdir, medians, runs_meta, r1):
    raw = outdir / 'raw'
    validation = {'R1_previous_modes_unchanged': r1}
    summary = {'per_position': {}, 'strata': {}, 'run_resources': {}}
    copies = conf['realizations_per_position']
    geo = json.loads(e1520.POSITIONS.read_text(encoding='utf-8'))
    d2d = {p['id']: p['d2d_m'] for p in geo['positions']}
    exits = {m['tag']: m['exit_code'] for m in runs_meta}

    n1 = {}
    for r in conf['n1_runs']:
        _, links = links_for(r['frequency_hz'], 1, 1, medians, r['stratum'])
        n1[r['tag']] = n1_check(raw / r['tag'], links) if exits.get(r['tag']) == 0 else {'exit_code': exits.get(r['tag'])}
    n1_ok = all('exit_code' not in x and all(v['passed'] for v in x.values()) for x in n1.values())
    validation['N1_normalisation'] = dict(n1, passed=bool(n1) and n1_ok)

    per_check = defaultdict(dict)
    tables, all_real = {}, []
    for r in conf['beams_runs']:
        arr = conf['arrays'][r['array']]
        gnb, links = links_for(r['frequency_hz'], arr['rows'], arr['cols'], medians, r['stratum'])
        rd = raw / r['tag']
        info = {x['key']: x['value'] for x in e151.read_csv(rd / 'e1521_info.csv')}
        real, complete = read_real(rd, len(links), copies)
        for k, v in beams_checks(real, complete, info, links, r, conf).items():
            per_check[k][r['tag']] = v
        per_check['I4_independent_recompute'][r['tag']] = i4_check(rd, real, links, gnb, arr['rows'], arr['cols'],
                                                                    conf['oversampling'])
        if complete:
            tables[r['tag']] = position_table(real, links, copies, d2d)
            summary['per_position'][r['tag']] = tables[r['tag']]
            summary['strata'][r['tag']] = stratum_summaries(r, tables[r['tag']])
        summary['run_resources'][r['tag']] = {'runtime_s': next(m['runtime_s'] for m in runs_meta
                                                                if m['tag'] == r['tag']),
                                              'peak_rss_kb': int(info['peak_rss_kb']),
                                              'codebook_beams': (conf['oversampling'] ** 2) * arr['rows'] * arr['cols']}
        for (l, cp), x in sorted(real.items()):
            all_real.append([r['tag'], l, cp] + [x[f] for f in REAL_COLUMNS])
    for k in ('S1_bookkeeping', 'I1_reconstruction', 'I2_codebook_in_ns3', 'I3_eigen_in_ns3',
              'I4_independent_recompute', 'D1_dominance'):
        validation[k] = dict(per_check[k], passed=bool(per_check[k]) and all(v['passed']
                                                                             for v in per_check[k].values()))

    s = conf['nr_runs']['settings']
    v1, v1_gain = {}, {}
    for r in conf['nr_runs']['configs']:
        arr = conf['arrays'][r['array']]
        _, links = links_for(r['frequency_hz'], arr['rows'], arr['cols'], medians, r['stratum'])
        cfg = diag.carrier_cfg(c, r['frequency_hz'])
        for bearing in ant.TR38901_SECTOR_BEARINGS_DEG:
            sub = [l for l in links if l['bearing_deg'] == bearing]
            if not sub:
                continue
            tag = f"{r['tag']}_b{int(bearing)}"
            if exits.get(tag) != 0:
                v1[tag] = {'exit_code': exits.get(tag), 'passed': False}
                continue
            v1[tag], v1_gain[tag] = v1_check(raw / tag, sub, cfg)
    validation['V1_nr_custom_beams'] = dict(v1, passed=bool(v1) and all(x['passed'] for x in v1.values()))
    summary['nr_beam_gain_db'] = v1_gain

    if len(tables) == len(conf['beams_runs']):
        summary['config_comparisons'] = config_comparisons(tables, conf['beams_runs'])
        summary['gain_model'] = gain_model(summary['strata'], conf['beams_runs'])
        crn = {}
        for stratum in ('o2o', 'o2i'):
            a_ = e151.read_csv(raw / f'a_f7p2_{stratum}' / 'e1521_real.csv')
            b_ = e151.read_csv(raw / f'b_f7p2_{stratum}' / 'e1521_real.csv')
            crn[stratum] = all(x['n_clusters'] == y['n_clusters'] for x, y in zip(a_, b_)) and len(a_) == len(b_)
        summary['common_random_numbers_a_b_7p2_same_n_clusters'] = crn
    passed = all(v['passed'] for v in validation.values())
    return validation, summary, passed, all_real


REAL_COLUMNS = ('n_clusters', 'gnb_is_s', 'g_ns3_dp', 'g_dp', 'g_cb', 'cb_p', 'cb_q', 'g_cb1', 'cb1_p', 'cb1_q',
                'g_eigpo', 'g_eig', 'g_mrt', 'g_ns3_cb', 'g_ns3_eig', 'g_eigvec', 'eig_iters', 'eig_certified')


def write_outputs(outdir, validation, summary, passed, all_real):
    (outdir / 'validation.json').write_text(json.dumps({'checks': validation, 'all_checks_passed': passed},
                                                       indent=2, default=e151.json_default), encoding='utf-8')
    (outdir / 'summary.json').write_text(json.dumps(summary, indent=2, default=e151.json_default), encoding='utf-8')
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator='\n')
    w.writerow(('run', 'link_index', 'copy') + REAL_COLUMNS)
    w.writerows(all_real)
    with open(outdir / 'realizations.csv.gz', 'wb') as fh:
        with gzip.GzipFile(fileobj=fh, mode='wb', mtime=0, filename='') as gz:
            gz.write(buf.getvalue().encode('utf-8'))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--config', default=str(CONFIG))
    ap.add_argument('--outdir', default=str(OUTDIR))
    ap.add_argument('--ns3-dir', default=str(Path.home() / '6g-external/ns-3-dev'))
    ap.add_argument('--git-head', default=None)
    ap.add_argument('--reanalyse', action='store_true')
    args = ap.parse_args()
    conf = json.loads(Path(args.config).read_text(encoding='utf-8'))
    c151 = json.loads(E151_CONFIG.read_text(encoding='utf-8'))
    c = c151['common']
    outdir = Path(args.outdir)
    medians, real_sha = e151.median_losses()

    if args.reanalyse:
        man = json.loads((outdir / 'manifest.json').read_text(encoding='utf-8'))
        if e151.sha256(args.config) != man['config_sha256']:
            raise SystemExit('config changed since the run')
        for m in man['runs']:
            for f, h in m['raw_sha256'].items():
                if e151.sha256(outdir / 'raw' / m['tag'] / f) != h:
                    raise SystemExit(f'raw file changed: {m["tag"]}/{f}')
        r1 = json.loads((outdir / 'validation.json').read_text())['checks']['R1_previous_modes_unchanged']
        validation, summary, passed, all_real = analyse(conf, c, outdir, medians, man['runs'], r1)
        write_outputs(outdir, validation, summary, passed, all_real)
        man['reanalysis'] = {'generated_utc': datetime.now(timezone.utc).isoformat(), 'git_head': args.git_head,
                             'all_checks_passed': passed}
        (outdir / 'manifest.json').write_text(json.dumps(man, indent=2, default=e151.json_default), encoding='utf-8')
        print('E1.5.2-1 reanalysis:', 'PASSED' if passed else 'FAILED')
        sys.exit(0 if passed else 1)

    ns3 = Path(args.ns3_dir)
    outdir.mkdir(parents=True, exist_ok=True)
    prog_sha = e151.check_stack(c151, ns3)
    t0 = time.time()
    r1 = r1_check(outdir, args.git_head)
    print('R1', 'PASS' if r1['passed'] else 'FAIL', flush=True)
    if not r1['passed']:
        validation = {'R1_previous_modes_unchanged': r1}
        (outdir / 'validation.json').write_text(json.dumps({'checks': validation, 'all_checks_passed': False},
                                                           indent=2, default=e151.json_default), encoding='utf-8')
        raise SystemExit('R1 failed: stop (see validation.json)')
    runs_meta = execute(conf, c, outdir, ns3, medians, args)
    validation, summary, passed, all_real = analyse(conf, c, outdir, medians, runs_meta, r1)
    write_outputs(outdir, validation, summary, passed, all_real)
    manifest = {'generated_utc': datetime.now(timezone.utc).isoformat(), 'tool': 'scripts/e1_5_2_1_beams.py',
                'git_head_at_run': args.git_head, 'python': platform.python_version(), 'numpy': np.__version__,
                'config_sha256': e151.sha256(args.config), 'program_sha256': prog_sha,
                'ns3_commit': c151['stack']['ns3_commit'], 'nr_commit': c151['stack']['nr_commit'],
                'e1_2_realizations_sha256': real_sha, 'positions_sha256': e151.sha256(e1520.POSITIONS),
                'realizations_csv_gz_sha256': e151.sha256(outdir / 'realizations.csv.gz'),
                'wall_time_s': time.time() - t0, 'runs': runs_meta, 'all_checks_passed': passed}
    (outdir / 'manifest.json').write_text(json.dumps(manifest, indent=2, default=e151.json_default), encoding='utf-8')
    for k, v in validation.items():
        print(k, 'PASS' if v['passed'] else 'FAIL', flush=True)
    print('E1.5.2-1', 'PASSED' if passed else 'FAILED (see validation.json)')
    sys.exit(0 if passed else 1)


if __name__ == '__main__':
    main()
