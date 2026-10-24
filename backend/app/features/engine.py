"""Point-in-time (as-of) transaction features.

Every feature for transaction i is computed ONLY from transactions processed before i (sequence order,
ties broken by input order) plus static account metadata. This prevents future information leaking
into historical predictions.

The same `FeatureState` is used for batch scoring (a chronological sweep) and for the streaming
simulator, so incremental and batch results are identical by construction; `reference_features`
is an independent brute-force implementation used by the tests to verify that.

Zero-denominator policy: ratios add +1 smoothing to the denominator; statistics with too little history
(z-scores need >=5 prior outgoing txns, median gap needs >=3 prior gaps) are NaN (tree models handle NaN
natively; sklearn pipelines impute).
"""
from __future__ import annotations

import math
from collections import Counter, deque

import numpy as np
import pandas as pd

FEATURE_VERSION = "fv1"
H = 3600.0
WINDOWS = {"1h": 1 * H, "24h": 24 * H, "7d": 7 * 24 * H, "30d": 30 * 24 * H}
_W = list(WINDOWS.values())
TYPES = ["P2P_TRANSFER", "MERCHANT_PAYMENT", "BUSINESS_TRANSFER", "BILL_PAYMENT", "MERCHANT_SETTLEMENT"]
CHANNELS = ["UPI", "IMPS", "NEFT", "CARD", "WEB"]
STRUCT_THRESHOLD = 50000.0

TXN_FEATURES = ["amount", "log_amount", "hour", "dow", "is_weekend", "type_code", "channel_code", "near_threshold", "round_amount"]
BEHAVIOR_FEATURES = [
    "s_out_cnt_1h", "s_out_cnt_24h", "s_out_cnt_7d", "s_out_cnt_30d", "s_out_val_24h", "s_out_val_7d", "s_out_val_30d",
    "s_in_cnt_24h", "s_in_cnt_7d", "s_in_val_24h", "s_in_val_7d", "s_uniq_out_cp_24h", "s_uniq_out_cp_7d",
    "s_dormancy_h", "s_age_d", "s_amt_z", "s_burst_ratio", "s_med_gap_h", "s_inout_ratio_24h", "s_new_cp",
    "s_hist_out_n", "s_near_thr_cnt_7d", "s_avg_amt_30d", "amount_vs_avg30",
]
GRAPH_FEATURES = [
    "d_in_cnt_24h", "d_uniq_senders_24h", "d_uniq_senders_7d", "d_in_val_24h", "d_age_d", "d_dormancy_h",
    "d_burst_ratio", "d_new_sender", "recip_cnt", "cycle_close2", "cycle_close3", "chain_len",
    "s_uniq_senders_24h", "pass_through_ratio", "d_out_cnt_24h", "d_forward_ratio",
]
ALL_FEATURES = TXN_FEATURES + BEHAVIOR_FEATURES + GRAPH_FEATURES
FEATURE_GROUPS = {
    "A_transaction": TXN_FEATURES,
    "B_behavioral": TXN_FEATURES + BEHAVIOR_FEATURES,
    "C_graph": TXN_FEATURES + GRAPH_FEATURES,
    "D_combined": ALL_FEATURES,
}
FEATURE_DOCS = {
    "s_*": "source-account behaviour over trailing windows, strictly before the transaction",
    "d_*": "destination-account neighbourhood (fan-in / dormancy / forwarding) strictly before the transaction",
    "recip_cnt": "# prior txns where destination paid the source",
    "cycle_close2/3": "1 if this txn closes a 2-/3-hop cycle with a prior path destination->...->source within 7d",
    "chain_len": "position in a temporal pass-through chain (incoming ~ outgoing amount within 24h)",
    "pass_through_ratio": "value received by source in last 24h divided by this amount (smoothed)",
}


class _Win:
    """Sliding windows over one account/direction with O(1) amortised updates."""

    __slots__ = ("amt", "c7d", "c24", "cnt", "cp", "head", "ts", "val")

    def __init__(self):
        self.ts: list[float] = []
        self.amt: list[float] = []
        self.cp: list[str] = []
        self.head = [0, 0, 0, 0]
        self.cnt = [0, 0, 0, 0]
        self.val = [0.0, 0.0, 0.0, 0.0]
        self.c24: Counter = Counter()
        self.c7d: Counter = Counter()

    def advance(self, now: float):
        ts, amt, cp = self.ts, self.amt, self.cp
        for k in range(4):
            lim = now - _W[k]
            h = self.head[k]
            n = len(ts)
            while h < n and ts[h] <= lim:
                self.cnt[k] -= 1
                self.val[k] -= amt[h]
                if k == 1:
                    c = self.c24
                    c[cp[h]] -= 1
                    if c[cp[h]] <= 0:
                        del c[cp[h]]
                elif k == 2:
                    c = self.c7d
                    c[cp[h]] -= 1
                    if c[cp[h]] <= 0:
                        del c[cp[h]]
                h += 1
            self.head[k] = h

    def add(self, t: float, a: float, cp: str):
        self.ts.append(t)
        self.amt.append(a)
        self.cp.append(cp)
        for k in range(4):
            self.cnt[k] += 1
            self.val[k] += a
        self.c24[cp] += 1
        self.c7d[cp] += 1


class FeatureState:
    def __init__(self, created: dict[str, float] | None = None, structuring_threshold: float = STRUCT_THRESHOLD):
        self.created = created or {}
        self.thr = structuring_threshold
        self.out: dict[str, _Win] = {}
        self.inn: dict[str, _Win] = {}
        self.last_act: dict[str, float] = {}
        self.welford: dict[str, list[float]] = {}   # n, mean, M2 of log(outgoing amount)
        self.out_ts: dict[str, deque] = {}
        self.out_nbrs: dict[str, dict[str, float]] = {}   # account -> {counterparty: last_ts}  (insertion order = recency)
        self.in_senders: dict[str, set] = {}
        self.recent_in: dict[str, deque] = {}       # account -> deque[(ts, amt, chain)]
        self.pair_count: dict[tuple, int] = {}
        self.near_thr: dict[str, deque] = {}
        self.n_seen = 0

    def _w(self, store: dict, a: str) -> _Win:
        w = store.get(a)
        if w is None:
            w = store[a] = _Win()
        return w

    def process(self, s: str, d: str, t: float, a: float, ttype: str = "", channel: str = "", hour: int = 0, dow: int = 0) -> dict:
        so, si, di, do = self._w(self.out, s), self._w(self.inn, s), self._w(self.inn, d), self._w(self.out, d)
        for w in (so, si, di, do):
            w.advance(t)
        f: dict[str, float] = {}
        la = math.log1p(a)
        f["amount"], f["log_amount"], f["hour"], f["dow"] = a, la, hour, dow
        f["is_weekend"] = 1.0 if dow >= 5 else 0.0
        f["type_code"] = TYPES.index(ttype) if ttype in TYPES else len(TYPES)
        f["channel_code"] = CHANNELS.index(channel) if channel in CHANNELS else len(CHANNELS)
        f["near_threshold"] = 1.0 if 0.8 * self.thr <= a < self.thr else 0.0
        f["round_amount"] = 1.0 if a % 1000 == 0 else 0.0
        # ---- source behaviour
        f["s_out_cnt_1h"], f["s_out_cnt_24h"], f["s_out_cnt_7d"], f["s_out_cnt_30d"] = so.cnt
        f["s_out_val_24h"], f["s_out_val_7d"], f["s_out_val_30d"] = so.val[1], so.val[2], so.val[3]
        f["s_in_cnt_24h"], f["s_in_cnt_7d"] = si.cnt[1], si.cnt[2]
        f["s_in_val_24h"], f["s_in_val_7d"] = si.val[1], si.val[2]
        f["s_uniq_out_cp_24h"], f["s_uniq_out_cp_7d"] = len(so.c24), len(so.c7d)
        last = self.last_act.get(s)
        if last is None:
            c = self.created.get(s)
            f["s_dormancy_h"] = min((t - c) / H, 24 * 365.0) if c is not None and c <= t else 0.0
        else:
            f["s_dormancy_h"] = (t - last) / H
        c = self.created.get(s)
        f["s_age_d"] = max((t - c) / 86400.0, 0.0) if c is not None else np.nan
        n, mean, m2 = self.welford.get(s, (0, 0.0, 0.0))
        f["s_hist_out_n"] = n
        f["s_amt_z"] = (la - mean) / (math.sqrt(m2 / (n - 1)) + 0.25) if n >= 5 else np.nan
        base_daily = so.cnt[3] / 30.0
        f["s_burst_ratio"] = so.cnt[1] / (base_daily + 1.0)
        tsq = self.out_ts.get(s)
        if tsq is not None and len(tsq) >= 4:
            arr = np.diff(np.fromiter(tsq, float))
            f["s_med_gap_h"] = float(np.median(arr)) / H
        else:
            f["s_med_gap_h"] = np.nan
        f["s_inout_ratio_24h"] = (si.val[1] + 1.0) / (so.val[1] + 1.0)
        f["s_new_cp"] = 1.0 if self.pair_count.get((s, d), 0) == 0 else 0.0
        nt = self.near_thr.get(s)
        if nt:
            while nt and nt[0] <= t - _W[2]:
                nt.popleft()
        f["s_near_thr_cnt_7d"] = len(nt) if nt else 0
        f["s_avg_amt_30d"] = so.val[3] / so.cnt[3] if so.cnt[3] else np.nan
        f["amount_vs_avg30"] = a / f["s_avg_amt_30d"] if so.cnt[3] else np.nan
        # ---- graph / neighbourhood
        f["d_in_cnt_24h"], f["d_uniq_senders_24h"], f["d_uniq_senders_7d"] = di.cnt[1], len(di.c24), len(di.c7d)
        f["d_in_val_24h"] = di.val[1]
        cd = self.created.get(d)
        f["d_age_d"] = max((t - cd) / 86400.0, 0.0) if cd is not None else np.nan
        ld = self.last_act.get(d)
        f["d_dormancy_h"] = (t - ld) / H if ld is not None else (min((t - cd) / H, 24 * 365.0) if cd is not None and cd <= t else 0.0)
        f["d_burst_ratio"] = di.cnt[1] / (di.cnt[3] / 30.0 + 1.0)
        f["d_new_sender"] = 1.0 if s not in self.in_senders.get(d, ()) else 0.0
        f["recip_cnt"] = self.pair_count.get((d, s), 0)
        nbr_d = self.out_nbrs.get(d, {})
        t_back = nbr_d.get(s)
        f["cycle_close2"] = 1.0 if t_back is not None and t - t_back <= _W[2] else 0.0
        c3 = 0.0
        if nbr_d and not f["cycle_close2"]:
            for w_acc, t1 in list(nbr_d.items())[-30:]:
                if t - t1 <= _W[2] and w_acc != s:
                    t2 = self.out_nbrs.get(w_acc, {}).get(s)
                    if t2 is not None and t1 <= t2 <= t and t - t1 <= _W[2]:
                        c3 = 1.0
                        break
        f["cycle_close3"] = c3
        chain = 1
        for (tin, ain, cin) in self.recent_in.get(s, ()):
            if t - tin <= _W[1] and 0.6 * ain <= a <= 1.05 * ain and cin + 1 > chain:
                chain = cin + 1
        f["chain_len"] = chain
        f["s_uniq_senders_24h"] = len(si.c24)
        f["pass_through_ratio"] = (si.val[1] + 1.0) / (a + 1.0)
        f["d_out_cnt_24h"] = do.cnt[1]
        f["d_forward_ratio"] = (do.val[1] + 1.0) / (di.val[1] + 1.0)
        # ---- update state AFTER features are extracted
        so.add(t, a, d)
        di.add(t, a, s)
        self.last_act[s] = t
        self.last_act[d] = t
        wf = self.welford.setdefault(s, [0, 0.0, 0.0])
        wf[0] += 1
        delta = la - wf[1]
        wf[1] += delta / wf[0]
        wf[2] += delta * (la - wf[1])
        dq = self.out_ts.setdefault(s, deque(maxlen=11))
        dq.append(t)
        nb = self.out_nbrs.setdefault(s, {})
        nb.pop(d, None)
        nb[d] = t
        self.in_senders.setdefault(d, set()).add(s)
        self.pair_count[(s, d)] = self.pair_count.get((s, d), 0) + 1
        ri = self.recent_in.setdefault(d, deque(maxlen=5))
        ri.append((t, a, chain))
        if f["near_threshold"]:
            self.near_thr.setdefault(s, deque()).append(t)
        self.n_seen += 1
        return f


def compute_features(df: pd.DataFrame, created: dict[str, pd.Timestamp] | None = None, structuring_threshold: float = STRUCT_THRESHOLD) -> pd.DataFrame:
    """Batch driver: chronological sweep. `df` must be sorted by timestamp (it is re-sorted stably if not)."""
    df = df.sort_values("transaction_timestamp", kind="stable").reset_index(drop=True)
    ts = df["transaction_timestamp"].astype("datetime64[s]").astype("int64").to_numpy().astype(float)
    cr = {k: pd.Timestamp(v).value / 1e9 for k, v in (created or {}).items() if pd.notna(v)}
    st = FeatureState(cr, structuring_threshold)
    src, dst, amt = df["source_account_id"].tolist(), df["destination_account_id"].tolist(), df["amount"].to_numpy(float)
    tt, ch = df["transaction_type"].fillna("").tolist(), df["channel"].fillna("").tolist()
    dt = df["transaction_timestamp"].dt
    hrs, dws = dt.hour.to_numpy(), dt.dayofweek.to_numpy()
    rows = [st.process(src[i], dst[i], ts[i], float(amt[i]), tt[i], ch[i], int(hrs[i]), int(dws[i])) for i in range(len(df))]
    feats = pd.DataFrame(rows, columns=ALL_FEATURES)
    feats.insert(0, "transaction_id", df["transaction_id"].to_numpy())
    return feats


def reference_features(df: pd.DataFrame, i: int, created: dict[str, pd.Timestamp] | None = None) -> dict:
    """Brute-force recomputation of a subset of features for row `i` (df in processing order). Test oracle."""
    row = df.iloc[i]
    prior = df.iloc[:i]
    t = row["transaction_timestamp"]
    s, d = row["source_account_id"], row["destination_account_id"]
    out = {}
    for name, w in WINDOWS.items():
        lo = t - pd.Timedelta(seconds=w)
        o = prior[(prior["source_account_id"] == s) & (prior["transaction_timestamp"] > lo)]
        n_in = prior[(prior["destination_account_id"] == s) & (prior["transaction_timestamp"] > lo)]
        di = prior[(prior["destination_account_id"] == d) & (prior["transaction_timestamp"] > lo)]
        out[f"s_out_cnt_{name}"] = len(o)
        out[f"s_out_val_{name}"] = o["amount"].sum()
        out[f"s_in_cnt_{name}"] = len(n_in)
        out[f"s_in_val_{name}"] = n_in["amount"].sum()
        out[f"s_uniq_out_cp_{name}"] = o["destination_account_id"].nunique()
        out[f"d_in_cnt_{name}"] = len(di)
        out[f"d_uniq_senders_{name}"] = di["source_account_id"].nunique()
    out["s_new_cp"] = float(not ((prior["source_account_id"] == s) & (prior["destination_account_id"] == d)).any())
    out["recip_cnt"] = int(((prior["source_account_id"] == d) & (prior["destination_account_id"] == s)).sum())
    return out
