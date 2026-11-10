"""Milan Telecom Italia traces for P3.3 / P3.4 (P3.0 revision 5, D31).

Source: "Telecommunications - SMS, Call, Internet - MI", Harvard Dataverse doi:10.7910/DVN/EGZHFV (v1.3), Barlacchi et
al., Sci. Data 2, 150055 (2015), ODbL 1.0. Columns (tab-separated, no header): square id (1..10000), time interval start
[ms since the Unix epoch], country code, SMS-in, SMS-out, call-in, call-out, Internet activity ("number of CDRs
generated inside a given Square id during a given Time interval"). Missing activity fields are empty.

The raw files (external/milan_telecom/, git-ignored, read-only) are never modified. This module reads one daily file
into a dense (10000 x 144) array of Internet activity summed over country codes, aligned to the local day
(LOCAL_OFFSET_MS), and implements the registered square selection, normalization and quantization.
"""
import hashlib
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / 'external' / 'milan_telecom'
N_SQ, SLOTS_DAY, SLOT_MS = 10000, 144, 600_000
LOCAL_OFFSET_MS = 3_600_000          # CET = UTC+1 in November 2013 (no DST); checked on every file (gate F5)
DEV_DATES = ('2013-11-04', '2013-11-05', '2013-11-06', '2013-11-07', '2013-11-08', '2013-11-09', '2013-11-10')
TEST_DATES = ('2013-11-18', '2013-11-19', '2013-11-20', '2013-11-21', '2013-11-22', '2013-11-23', '2013-11-24')
N_KEEP, N_DEV_SQ = 50, 25
REF_WINDOW = 6                       # 6 slots = 60 min: the busiest hour
X_STEP, X_MAX = 0.025, 5.0
WEEKEND = (5, 6)                     # day index in the week (Mon = 0)


def filename(date):
    return f'sms-call-internet-mi-{date}.txt'


def sha256(p):
    h = hashlib.sha256()
    with open(p, 'rb') as fh:
        for chunk in iter(lambda: fh.read(1 << 22), b''):
            h.update(chunk)
    return h.hexdigest()


def local_midnight_utc_ms(date):
    d = datetime.strptime(date, '%Y-%m-%d').replace(tzinfo=timezone.utc)
    return int(d.timestamp() * 1000) - LOCAL_OFFSET_MS


def read_day(path, date):
    """Dense Internet activity (N_SQ, 144) of the local day, summed over country codes, and a presence mask (True where
    at least one row with a non-empty Internet field exists). Returns also the file's coverage diagnostics."""
    df = pd.read_csv(path, sep='\t', header=None, usecols=[0, 1, 7], names=['sq', 't', 'internet'],
                     dtype={'sq': np.int64, 't': np.int64, 'internet': np.float64}, engine='c')
    t0 = local_midnight_utc_ms(date)
    rel = df['t'].to_numpy() - t0
    slot = rel // SLOT_MS
    sq = df['sq'].to_numpy()
    val = df['internet'].to_numpy()
    cov = {'rows': int(len(df)), 't_min_ms': int(df['t'].min()), 't_max_ms': int(df['t'].max()),
           'slot_min': int(slot.min()), 'slot_max': int(slot.max()), 'misaligned_rows': int(np.sum(rel % SLOT_MS != 0)),
           'square_min': int(sq.min()), 'square_max': int(sq.max()), 'internet_empty_rows': int(np.isnan(val).sum()),
           'internet_negative_rows': int(np.sum(val < 0))}
    ok = (slot >= 0) & (slot < SLOTS_DAY) & (sq >= 1) & (sq <= N_SQ)
    cov['rows_outside_day'] = int(np.sum(~ok))
    has = ~np.isnan(val)
    idx = (sq[ok & has] - 1) * SLOTS_DAY + slot[ok & has]
    act = np.bincount(idx, weights=val[ok & has], minlength=N_SQ * SLOTS_DAY).reshape(N_SQ, SLOTS_DAY)
    pres = np.bincount(idx, minlength=N_SQ * SLOTS_DAY).reshape(N_SQ, SLOTS_DAY) > 0
    cov['distinct_slots'] = int(len(np.unique(slot[ok])))
    return act, pres, cov


def read_week(dates):
    acts, pres, covs = [], [], {}
    for d in dates:
        a, p, c = read_day(DATA / filename(d), d)
        acts.append(a)
        pres.append(p)
        covs[d] = c
    return np.stack(acts, axis=1).reshape(N_SQ, -1), np.stack(pres, axis=1).reshape(N_SQ, -1), covs


def select_squares(act_week, pres_week, seed):
    """D31: candidates have Internet activity > 0 in every slot of the week; the N_KEEP with the highest mean activity
    are kept and split into N_DEV_SQ development and N_KEEP - N_DEV_SQ test squares by a seeded permutation.
    Returns 1-based square ids (sorted within each split)."""
    cand = np.flatnonzero(np.all(pres_week & (act_week > 0), axis=1))
    mean = act_week[cand].mean(axis=1)
    order = np.lexsort((cand, -mean))            # highest mean first; ties by square id
    keep = cand[order[:N_KEEP]]
    perm = np.random.default_rng(seed).permutation(N_KEEP)
    dev = np.sort(keep[perm[:N_DEV_SQ]]) + 1
    test = np.sort(keep[perm[N_DEV_SQ:]]) + 1
    return dev, test, {'candidates': int(len(cand)), 'kept_ids': (keep + 1).tolist()}


def a_ref(series):
    """Busiest hour of a reference week: the maximum mean over REF_WINDOW consecutive slots."""
    c = np.convolve(np.asarray(series, dtype=float), np.ones(REF_WINDOW) / REF_WINDOW, mode='valid')
    return float(c.max())


def x_grid():
    return np.arange(int(round(X_MAX / X_STEP)) + 1) / (1.0 / X_STEP)


def quantize(x):
    """Nearest grid index (half up), clipped at X_MAX. Returns (indices, number clipped)."""
    x = np.asarray(x, dtype=float)
    n_grid = int(round(X_MAX / X_STEP))
    i = np.floor(x / X_STEP + 0.5).astype(np.int64)
    clipped = int(np.sum(i > n_grid))
    return np.minimum(i, n_grid), clipped


def day_type(slot_index_week):
    """0 = weekday, 1 = weekend, from the slot index within the Monday-start week."""
    return (np.asarray(slot_index_week) // SLOTS_DAY % 7 >= WEEKEND[0]).astype(np.int64)


def seasonal_profile(norm_traces):
    """S_d(tau): mean normalized activity per day type (weekday, weekend) and slot of day over the given traces
    (n_traces, 1008)."""
    n = norm_traces.shape[1]
    d = day_type(np.arange(n))
    tau = np.arange(n) % SLOTS_DAY
    S = np.zeros((2, SLOTS_DAY))
    for dt in (0, 1):
        for s in range(SLOTS_DAY):
            S[dt, s] = norm_traces[:, (d == dt) & (tau == s)].mean()
    return S


def forecast(x_prev, t_prev, h, S):
    """Seasonal persistence: x_hat(t_prev + 1 + h) = x(t_prev) S(t_prev + 1 + h) / S(t_prev) (h >= 0)."""
    t_prev = np.asarray(t_prev)
    tgt = t_prev + 1 + h
    s_now = S[day_type(t_prev), t_prev % SLOTS_DAY]
    s_tgt = S[day_type(tgt), tgt % SLOTS_DAY]
    return np.asarray(x_prev, dtype=float) * s_tgt / s_now
