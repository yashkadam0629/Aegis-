"""Pattern detectors. Each returns `Detection`s: investigative LEADS with evidence, never findings of wrongdoing.

All detectors accept a window [ws, we) (epoch seconds) and may read history before `ws` for baselines.
Detectors never see ground-truth labels. Account-type metadata is deliberately not used to suppress
detections; instead each detection is compared with the entity's own historical baseline.
"""
from __future__ import annotations

import time
from collections import Counter, deque
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from .index import TxnIndex

H = 3600.0
DAY = 86400.0


@dataclass
class Detection:
    detector: str
    entity_id: str
    accounts: list[str]
    txn_ids: list[str]
    start: pd.Timestamp
    end: pd.Timestamp
    strength: float                      # 0..1 detector-specific evidence strength (not a probability)
    evidence: dict = field(default_factory=dict)
    summary: str = ""
    path: list[str] = field(default_factory=list)   # ordered accounts for path-like patterns
    entity_type: str = "account"


def _ts(t: float) -> pd.Timestamp:
    return pd.Timestamp(t, unit="s")


def _clip(x: float, lo=0.0, hi=1.0) -> float:
    return float(min(hi, max(lo, x)))


# ------------------------------------------------------------------ cycles
def detect_cycles(idx: TxnIndex, ws: float, we: float, max_len: int = 5, window_h: float = 72.0, min_amount: float = 5000.0,
                  ret_lo: float = 0.55, ret_hi: float = 1.25, branch: int = 8, max_results: int = 500, timeout_s: float = 20.0) -> list[Detection]:
    """Temporal DFS: closed walks a->b->...->a (simple, 3..max_len hops) completed within `window_h`, with plausible value retention.
    2-hop reciprocal payments are excluded as trivial. Enumeration is bounded by window, branching cap and a time budget."""
    lo, hi = idx.window(ws, we)
    W = window_h * H
    deadline = time.monotonic() + timeout_s
    found: dict[tuple, Detection] = {}
    for i in range(lo, hi):
        if idx.amt[i] < min_amount:
            continue
        if time.monotonic() > deadline or len(found) >= max_results:
            break
        a0, t0 = idx.src[i], idx.ts[i]
        stack = [([i], {a0, idx.dst[i]})]
        while stack:
            if len(found) >= max_results or time.monotonic() > deadline:
                break
            path, vis = stack.pop()
            last = path[-1]
            cur, lt, la = idx.dst[last], idx.ts[last], idx.amt[last]
            rows = idx.out_between(cur, lt, min(t0 + W, we))
            if len(rows) == 0:
                continue
            rows = rows[(idx.amt[rows] >= max(min_amount, ret_lo * la)) & (idx.amt[rows] <= ret_hi * la)]
            if len(rows) > branch:
                rows = rows[np.argsort(np.abs(idx.amt[rows] - la))[:branch]]
            for r in rows:
                r = int(r)
                if idx.dst[r] == a0 and len(path) + 1 >= 3:
                    full = path + [r]
                    key = tuple(sorted(int(x) for x in full))
                    if key in found:
                        continue
                    accts = [str(idx.src[x]) for x in full]
                    canon = tuple(sorted(accts))
                    if any(k[0] == canon and abs(k[1] - t0) < W for k in (c.evidence["_key"] for c in found.values())):
                        continue
                    amts = idx.amt[full]
                    span = float(idx.ts[full[-1]] - t0)
                    retention = float(amts[-1] / amts[0])
                    strength = _clip(0.5 * _clip((retention - 0.5) / 0.5) + 0.5 * (1 - span / W))
                    found[key] = Detection(
                        "cycle", accts[0], accts, [str(idx.txid[x]) for x in full], _ts(t0), _ts(idx.ts[full[-1]]), strength,
                        {"length": len(full), "retention": round(retention, 4), "span_hours": round(span / H, 2),
                         "hop_amounts": [round(float(x), 2) for x in amts], "_key": (canon, t0)},
                        f"{len(full)}-hop circular flow {' → '.join(accts + [accts[0]])}; value returned {retention:.0%} of initial after {span / H:.1f}h",
                        path=accts + [accts[0]])
                elif idx.dst[r] not in vis and len(path) + 1 <= max_len - 1:
                    stack.append((path + [r], vis | {idx.dst[r]}))
    out = list(found.values())
    for d in out:
        d.evidence.pop("_key", None)
    return out


# ------------------------------------------------------------------ layered chains
def detect_chains(idx: TxnIndex, ws: float, we: float, gap_h: float = 24.0, ret_lo: float = 0.65, ret_hi: float = 1.05,
                  min_hops: int = 3, min_amount: float = 5000.0) -> list[Detection]:
    """Maximal temporal pass-through chains: each hop forwards (most of) a recently received amount within `gap_h`."""
    lo, hi = idx.window(ws, we)
    gap = gap_h * H
    recent: dict[str, deque] = {}
    clen = np.ones(hi - lo, dtype=int)
    pred = -np.ones(hi - lo, dtype=int)
    used_as_pred = np.zeros(hi - lo, dtype=bool)
    for i in range(lo, hi):
        a = idx.amt[i]
        if a < min_amount:
            continue
        best = None
        for (j, tj, aj) in recent.get(idx.src[i], ()):
            if idx.ts[i] - tj <= gap and ret_lo * aj <= a <= ret_hi * aj and idx.src[j] != idx.dst[i] and (best is None or clen[j - lo] > clen[best - lo]):
                best = j
        if best is not None:
            clen[i - lo] = clen[best - lo] + 1
            pred[i - lo] = best - lo
            used_as_pred[best - lo] = True
        dq = recent.setdefault(idx.dst[i], deque(maxlen=10))
        dq.append((i, idx.ts[i], a))
    out = []
    ends = [k for k in range(hi - lo) if clen[k] >= min_hops and not used_as_pred[k]]
    seen_starts = set()
    for k in sorted(ends, key=lambda k: -clen[k]):
        chain, c = [], k
        while c >= 0:
            chain.append(c + lo)
            c = pred[c]
        chain.reverse()
        if chain[0] in seen_starts:
            continue
        seen_starts.add(chain[0])
        amts = idx.amt[chain]
        accts = [str(idx.src[chain[0]])] + [str(idx.dst[x]) for x in chain]
        span = float(idx.ts[chain[-1]] - idx.ts[chain[0]])
        retention = float(amts[-1] / amts[0])
        hop_gap = np.diff(idx.ts[chain]) / H if len(chain) > 1 else np.array([0.0])
        strength = _clip(0.45 * _clip((len(chain) - 2) / 4) + 0.3 * _clip((retention - 0.5) / 0.5) + 0.25 * (1 - _clip(float(hop_gap.mean()) / gap_h)))
        out.append(Detection("layered_chain", accts[0], accts, [str(idx.txid[x]) for x in chain], _ts(idx.ts[chain[0]]), _ts(idx.ts[chain[-1]]), strength,
                             {"hops": len(chain), "retention": round(retention, 4), "span_hours": round(span / H, 2), "mean_hop_gap_hours": round(float(hop_gap.mean()), 2),
                              "hop_amounts": [round(float(x), 2) for x in amts]},
                             f"{len(chain)}-hop pass-through chain {' → '.join(accts)}; {retention:.0%} of initial value forwarded within {span / H:.1f}h",
                             path=accts))
    return out


# ------------------------------------------------------------------ shared beneficiary / fan-in / fan-out
def detect_fan(idx: TxnIndex, ws: float, we: float, direction: str = "in", win_h: float = 48.0, min_k: int = 5,
               history_days: float = 60.0, min_amount: float = 0.0) -> list[Detection]:
    """Unique counterparties converging on (in) or spreading from (out) one account within a window, compared with that
    account's own history so that habitual hubs (merchants, payroll, aggregators) score lower than sudden concentration."""
    rows_map = idx.in_rows if direction == "in" else idx.out_rows
    W = win_h * H
    out = []
    for acc, rows in rows_map.items():
        tsr = idx.ts[rows]
        a, b = np.searchsorted(tsr, ws, "left"), np.searchsorted(tsr, we, "left")
        if b - a < min_k:
            continue
        w_rows = rows[a:b]
        w_rows = w_rows[idx.amt[w_rows] >= min_amount]
        cp = idx.src[w_rows] if direction == "in" else idx.dst[w_rows]
        tt = idx.ts[w_rows]
        # history baseline: p95 of unique counterparties per 48h bin over prior `history_days`
        h0 = ws - history_days * DAY
        ha = np.searchsorted(tsr, h0, "left")
        hist_rows = rows[ha:a]
        hist_cp_set = set(idx.src[hist_rows] if direction == "in" else idx.dst[hist_rows])
        if len(hist_rows):
            bins = ((idx.ts[hist_rows] - h0) // W).astype(int)
            hcp = (idx.src[hist_rows] if direction == "in" else idx.dst[hist_rows])
            per_bin = pd.Series(hcp).groupby(bins).nunique()
            n_bins = int(history_days * DAY // W)
            vals = np.zeros(n_bins)
            vals[np.clip(per_bin.index.to_numpy(), 0, n_bins - 1)] = per_bin.values
            base = float(np.percentile(vals, 95))
        else:
            base = 0.0
        i = 0
        n = len(w_rows)
        taken_until = -1
        best_windows = []
        cnt: Counter = Counter()
        j = 0
        for i in range(n):  # sliding window [i, j)
            while j < n and tt[j] - tt[i] <= W:
                cnt[cp[j]] += 1
                j += 1
            if len(cnt) >= min_k:
                best_windows.append((len(cnt), i, j))
            cnt[cp[i]] -= 1
            if cnt[cp[i]] <= 0:
                del cnt[cp[i]]
        best_windows.sort(reverse=True)
        for k, i0, j0 in best_windows:
            if i0 < taken_until:
                continue
            taken_until = j0
            sel = w_rows[i0:j0]
            cps = set(cp[i0:j0])
            new_share = len(cps - hist_cp_set) / len(cps)
            ratio = k / max(1.0, base)
            amt_sum = float(idx.amt[sel].sum())
            strength = _clip(0.45 * _clip((k - min_k + 1) / 12) + 0.35 * _clip(np.log1p(ratio - 1 if ratio > 1 else 0) / np.log(10)) + 0.20 * new_share)
            members = sorted(str(c) for c in cps)
            name = "shared_beneficiary" if direction == "in" else "fan_out"
            out.append(Detection(name, str(acc), [str(acc)] + members, [str(idx.txid[x]) for x in sel], _ts(idx.ts[sel[0]]), _ts(idx.ts[sel[-1]]), strength,
                                 {"unique_counterparties": int(k), "n_transactions": len(sel), "total_value": round(amt_sum, 2), "window_hours": win_h,
                                  "baseline_p95_unique_per_window": round(base, 2), "concentration_vs_baseline": round(ratio, 2),
                                  "new_counterparty_share": round(new_share, 3), "direction": direction},
                                 f"{k} distinct accounts {'sent to' if direction == 'in' else 'received from'} {acc} within {win_h:.0f}h "
                                 f"(₹{amt_sum:,.0f} in {len(sel)} txns); {ratio:.1f}× its own historical 95th-percentile concentration; {new_share:.0%} are new counterparties",
                                 entity_type="account"))
    return out


# ------------------------------------------------------------------ behavioural: burst, dormant reactivation
def _daily_matrix(idx: TxnIndex, col: str = "out") -> tuple[pd.DataFrame, pd.DataFrame, int]:
    day = (idx.ts // DAY).astype(int)
    d0 = int(day.min()) if idx.n else 0
    df = pd.DataFrame({"acc": idx.src if col == "out" else idx.dst, "day": day - d0, "amt": idx.amt})
    cnt = df.groupby(["acc", "day"]).size().unstack(fill_value=0)
    val = df.groupby(["acc", "day"])["amt"].sum().unstack(fill_value=0.0)
    full = range(int(day.max() - d0) + 1) if idx.n else range(0)
    return cnt.reindex(columns=full, fill_value=0), val.reindex(columns=full, fill_value=0.0), d0


def detect_behavior(idx: TxnIndex, ws: float, we: float, min_count: int = 12, z_thresh: float = 8.0, dormant_days: int = 45,
                    dormant_min_count: int = 8, baseline_days: int = 28) -> list[Detection]:
    cnt, val, d0 = _daily_matrix(idx, "out")
    cnt_in, _, _ = _daily_matrix(idx, "in")
    cnt_in = cnt_in.reindex(index=cnt.index, fill_value=0)
    cmed = cnt.T.rolling(baseline_days, min_periods=10).median().shift(1).T
    q75 = cnt.T.rolling(baseline_days, min_periods=10).quantile(0.75).shift(1).T
    q25 = cnt.T.rolling(baseline_days, min_periods=10).quantile(0.25).shift(1).T
    sigma = ((q75 - q25) / 1.349).clip(lower=1.0)
    z = (cnt - cmed) / sigma
    activity = (cnt + cnt_in)
    prior_act = activity.T.rolling(dormant_days, min_periods=1).sum().shift(1).T
    d_lo, d_hi = int((ws // DAY) - d0), int((we // DAY) - d0)
    first_day = {a: int(np.argmax(cnt.loc[a].to_numpy() > 0)) for a in cnt.index[(cnt.to_numpy() > 0).any(axis=1)]}
    out: list[Detection] = []
    cols = [c for c in cnt.columns if d_lo <= c < d_hi]
    hits = (cnt[cols] >= min_count)
    for acc in hits.index[hits.any(axis=1)]:
        for c in hits.columns[hits.loc[acc]]:
            n = int(cnt.at[acc, c])
            pa = prior_act.at[acc, c] if c >= 1 else np.nan
            long_dormant = (not np.isnan(pa)) and pa <= 2 and c - first_day.get(acc, c) >= dormant_days and n >= dormant_min_count
            zz = float(z.at[acc, c]) if not np.isnan(z.at[acc, c]) else np.nan
            if not long_dormant and not (zz >= z_thresh):
                continue
            t0, t1 = (d0 + c) * DAY, (d0 + c + 1) * DAY
            rows = idx.out_between(acc, t0, t1 - 1)
            amts = idx.amt[rows]
            kind = "dormant_reactivation" if long_dormant else "burst"
            ev = {"day_count": n, "day_value": round(float(amts.sum()), 2), "baseline_median_daily_count": None if np.isnan(cmed.at[acc, c]) else float(cmed.at[acc, c]),
                  "robust_z": None if np.isnan(zz) else round(zz, 2), "prior_activity_days": dormant_days, "prior_activity_count": None if np.isnan(pa) else float(pa)}
            if long_dormant:
                strength = _clip(0.5 * _clip(n / 25) + 0.3 * _clip(float(amts.mean()) / 50000) + 0.2 * _clip(len(set(idx.dst[rows])) / 10))
                summ = f"Account active again after ≥{dormant_days} days with ≤2 transactions: {n} outgoing txns (₹{amts.sum():,.0f}) in one day"
            else:
                strength = _clip(0.6 * _clip((zz - z_thresh) / 20 + 0.3) + 0.4 * _clip(n / 40))
                summ = f"{n} outgoing txns in one day vs. own 28-day median {cmed.at[acc, c]:.0f}/day (robust z={zz:.1f}); ₹{amts.sum():,.0f} total"
            out.append(Detection(kind, str(acc), [str(acc)] + sorted(set(map(str, idx.dst[rows])))[:25], [str(idx.txid[r]) for r in rows], _ts(idx.ts[rows[0]]), _ts(idx.ts[rows[-1]]), strength, ev, summ))
    return _merge_adjacent(out)


def _merge_adjacent(dets: list[Detection]) -> list[Detection]:
    by = {}
    for d in sorted(dets, key=lambda x: x.start):
        k = (d.detector, d.entity_id)
        if k in by and (d.start - by[k].end) < pd.Timedelta(hours=30):
            m = by[k]
            m.txn_ids += d.txn_ids
            m.accounts = sorted(set(m.accounts) | set(d.accounts))
            m.end = max(m.end, d.end)
            m.strength = max(m.strength, d.strength)
            m.evidence["day_count"] = m.evidence.get("day_count", 0) + d.evidence.get("day_count", 0)
            m.evidence["day_value"] = round(m.evidence.get("day_value", 0) + d.evidence.get("day_value", 0), 2)
        else:
            by[k] = d
    return list(by.values())


# ------------------------------------------------------------------ structuring-like
def detect_structuring(idx: TxnIndex, ws: float, we: float, threshold: float = 50000.0, lower_frac: float = 0.8, min_count: int = 4, window_days: float = 7.0) -> list[Detection]:
    """Repeated payments just below a configurable reference amount. A signal to examine, not evidence of illegal structuring."""
    lo, hi = idx.window(ws, we)
    sel = np.arange(lo, hi)
    sel = sel[(idx.amt[sel] >= lower_frac * threshold) & (idx.amt[sel] < threshold)]
    by: dict[str, list[int]] = {}
    for r in sel:
        by.setdefault(idx.src[r], []).append(int(r))
    out = []
    W = window_days * DAY
    for acc, rows in by.items():
        if len(rows) < min_count:
            continue
        j = 0
        best = None
        for i in range(len(rows)):
            while idx.ts[rows[i]] - idx.ts[rows[j]] > W:
                j += 1
            if i - j + 1 >= min_count and (best is None or i - j + 1 > best[1] - best[0] + 1):
                best = (j, i)
        if not best:
            continue
        r = rows[best[0]:best[1] + 1]
        amts = idx.amt[r]
        total_win = len(idx.out_between(acc, idx.ts[r[0]], idx.ts[r[-1]]))
        share = len(r) / max(1, total_win)
        if share < 0.5:
            continue
        spread = float(np.std(amts / threshold))
        strength = _clip(0.5 * _clip(len(r) / 8) + 0.3 * (1 - _clip(spread / 0.1)) + 0.2 * share)
        out.append(Detection("structuring_like", str(acc), [str(acc)] + sorted(set(map(str, idx.dst[r]))), [str(idx.txid[x]) for x in r], _ts(idx.ts[r[0]]), _ts(idx.ts[r[-1]]), strength,
                             {"n_near_threshold": len(r), "threshold": threshold, "mean_fraction_of_threshold": round(float(amts.mean() / threshold), 3),
                              "share_of_account_outgoing": round(share, 3), "total_value": round(float(amts.sum()), 2)},
                             f"{len(r)} payments between {lower_frac:.0%} and 100% of the ₹{threshold:,.0f} reference amount within {(idx.ts[r[-1]] - idx.ts[r[0]]) / DAY:.1f} days "
                             f"({share:.0%} of the account's outgoing activity then)"))
    return out


# ------------------------------------------------------------------ coordinated behaviour
def detect_coordinated(idx: TxnIndex, ws: float, we: float, pop_cap: int = 40, min_shared: int = 2, co_hours: float = 6.0,
                       min_rounds: int = 3, min_group: int = 3, n_null: int = 3, seed: int = 11) -> list[Detection]:
    """Accounts that repeatedly pay the SAME (rare) counterparties at nearly the SAME times.

    Common counterparties alone are not coordination: popular counterparties (more than pop_cap distinct senders) are excluded
    and co-occurrence counts are compared with a null where each account's
    timestamps are circularly shifted by a random offset (preserving rates and counterparties, destroying timing)."""
    lo, hi = idx.window(ws, we)
    L = max(we - ws, 1.0)
    rows = np.arange(lo, hi)
    dst = idx.dst[rows]
    uniq, counts = np.unique(dst.astype(str), return_counts=True)
    senders_per_dst = {}
    for r in rows:
        senders_per_dst.setdefault(idx.dst[r], set()).add(idx.src[r])
    cand_dst = {d: s for d, s in senders_per_dst.items() if min_shared + 1 <= len(s) <= pop_cap}
    if not cand_dst:
        return []
    by_pair_dst: dict[tuple, np.ndarray] = {}
    tmp: dict[tuple, list] = {}
    for r in rows:
        k = (idx.src[r], idx.dst[r])
        if idx.dst[r] in cand_dst:
            tmp.setdefault(k, []).append(idx.ts[r])
    for k, v in tmp.items():
        by_pair_dst[k] = np.sort(np.array(v))
    # candidate account pairs: share >= min_shared rare destinations
    pair_dsts: dict[tuple, list] = {}
    for d, ss in cand_dst.items():
        ss = sorted(ss)
        for a in range(len(ss)):
            for b in range(a + 1, len(ss)):
                pair_dsts.setdefault((ss[a], ss[b]), []).append(d)
    pair_dsts = {p: ds for p, ds in pair_dsts.items() if len(ds) >= min_shared}
    rng = np.random.default_rng(seed)
    co = co_hours * H
    shifts = {a: rng.uniform(0.2, 0.8, n_null) * L for a in {x for p in pair_dsts for x in p}}

    def cooc(ta: np.ndarray, tb: np.ndarray) -> int:
        if len(ta) == 0 or len(tb) == 0:
            return 0
        pos = np.searchsorted(tb, ta)
        left = np.abs(ta - tb[np.clip(pos - 1, 0, len(tb) - 1)])
        right = np.abs(ta - tb[np.clip(pos, 0, len(tb) - 1)])
        return int((np.minimum(left, right) <= co).sum())

    edges = []
    for (a, b), ds in pair_dsts.items():
        obs = 0
        nul = np.zeros(n_null)
        for d in ds:
            ta, tb = by_pair_dst.get((a, d)), by_pair_dst.get((b, d))
            if ta is None or tb is None:
                continue
            obs += cooc(ta, tb)
            for k in range(n_null):
                tb_s = np.sort(((tb - ws + shifts[b][k]) % L) + ws)
                nul[k] += cooc(ta, tb_s)
        if obs >= min_rounds:
            w = float(np.sum([np.log(len(senders_per_dst[d]) ** -1 * 1000 + 1) for d in ds]))
            edges.append((a, b, obs, float(nul.mean()), len(ds), w))
    if not edges:
        return []
    import networkx as nx
    G = nx.Graph()
    for a, b, obs, nl, nd, w in edges:
        G.add_edge(a, b, obs=obs, null=nl, nd=nd, w=w)
    out = []
    for comp in nx.connected_components(G):
        if len(comp) < min_group:
            continue
        sub = G.subgraph(comp)
        obs = sum(d["obs"] for _, _, d in sub.edges(data=True))
        nl = sum(d["null"] for _, _, d in sub.edges(data=True))
        shared = set()
        for acc in comp:
            shared |= {d for (s_, d) in by_pair_dst if s_ == acc}
        shared = {d for d in shared if len(senders_per_dst[d] & comp) >= max(2, len(comp) // 2)}
        txn_pos = [int(r) for r in rows if idx.src[r] in comp and idx.dst[r] in shared]
        if not txn_pos:
            continue
        excess = obs / max(nl, 1.0)
        strength = _clip(0.4 * _clip((len(comp) - 2) / 6) + 0.4 * _clip(np.log(excess) / np.log(20)) + 0.2 * _clip(sub.number_of_edges() / (len(comp) * (len(comp) - 1) / 2)))
        members = sorted(map(str, comp))
        out.append(Detection("coordinated_group", members[0], members + sorted(map(str, shared)), [str(idx.txid[r]) for r in txn_pos], _ts(idx.ts[txn_pos[0]]), _ts(idx.ts[txn_pos[-1]]), strength,
                             {"n_members": len(comp), "shared_counterparties": sorted(map(str, shared))[:10], "observed_cooccurrences": int(obs),
                              "expected_by_chance": round(nl, 2), "excess_ratio": round(excess, 2), "co_window_hours": co_hours},
                             f"{len(comp)} accounts repeatedly paid the same {len(shared)} uncommon counterparties within {co_hours:.0f}h of each other: "
                             f"{obs} co-occurrences vs {nl:.1f} expected by chance under time-shifted null",
                             entity_type="group"))
    return out


def run_all(idx: TxnIndex, ws: float, we: float, structuring_threshold: float = 50000.0, timings: dict | None = None) -> list[Detection]:
    steps = [("cycle", lambda: detect_cycles(idx, ws, we)), ("layered_chain", lambda: detect_chains(idx, ws, we)),
             ("fan_in", lambda: detect_fan(idx, ws, we, "in")), ("fan_out", lambda: detect_fan(idx, ws, we, "out")),
             ("behavior", lambda: detect_behavior(idx, ws, we)), ("structuring", lambda: detect_structuring(idx, ws, we, structuring_threshold)),
             ("coordinated", lambda: detect_coordinated(idx, ws, we))]
    res: list[Detection] = []
    for name, fn in steps:
        t = time.perf_counter()
        res += fn()
        if timings is not None:
            timings[name] = round(time.perf_counter() - t, 3)
    return res
