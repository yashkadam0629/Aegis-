"""Graph analytics: node features, communities, multi-hop tracing.

Cost notes (n=nodes, m=edges):
  degree / weighted degree / reciprocity ........ O(m)
  PageRank (scipy power iteration) ............... O(k*m), k~50-100
  Louvain communities ............................ ~O(m log n) in practice
  clustering coefficient ......................... O(sum deg^2) - computed only on subgraphs / capped nodes
  betweenness .................................... O(n*m) exact; approximated with k sampled sources (k<=100) and only on request
Global metrics are computed per (dataset, window) and cached by the caller (see services.cache).
"""
from __future__ import annotations

import time

import networkx as nx
import numpy as np
import pandas as pd

from .index import TxnIndex


def node_features(G: nx.DiGraph, with_pagerank: bool = True, with_betweenness: bool = False, betweenness_k: int = 100,
                  clustering_node_cap: int = 20000, seed: int = 7) -> pd.DataFrame:
    if G.number_of_nodes() == 0:
        return pd.DataFrame(columns=["account_id"])
    nodes = list(G.nodes)
    ind, outd = dict(G.in_degree()), dict(G.out_degree())
    win, wout = dict(G.in_degree(weight="total")), dict(G.out_degree(weight="total"))
    cin = {n: sum(d["count"] for _, _, d in G.in_edges(n, data=True)) for n in nodes}
    cout = {n: sum(d["count"] for _, _, d in G.out_edges(n, data=True)) for n in nodes}
    rec = {}
    for n in nodes:
        outs = set(G.successors(n))
        rec[n] = len(outs & set(G.predecessors(n))) / len(outs) if outs else 0.0
    df = pd.DataFrame({"account_id": nodes, "in_degree": [ind[n] for n in nodes], "out_degree": [outd[n] for n in nodes],
                       "in_value": [win[n] for n in nodes], "out_value": [wout[n] for n in nodes],
                       "in_txn": [cin[n] for n in nodes], "out_txn": [cout[n] for n in nodes], "reciprocity": [rec[n] for n in nodes]})
    df["counterparties"] = [len(set(G.successors(n)) | set(G.predecessors(n))) for n in nodes]
    df["fan_in"] = df["in_degree"] / (df["out_degree"] + 1)
    df["fan_out"] = df["out_degree"] / (df["in_degree"] + 1)
    df["flow_through"] = np.minimum(df["in_value"], df["out_value"]) / (np.maximum(df["in_value"], df["out_value"]) + 1.0)
    if with_pagerank:
        pr = nx.pagerank(G, weight="total", max_iter=100, tol=1e-6)
        df["pagerank"] = df["account_id"].map(pr)
    if G.number_of_nodes() <= clustering_node_cap:
        df["clustering"] = df["account_id"].map(nx.clustering(G.to_undirected()))
    if with_betweenness:
        k = min(betweenness_k, G.number_of_nodes())
        bc = nx.betweenness_centrality(G, k=k, seed=seed, weight=None)
        df["betweenness_approx"] = df["account_id"].map(bc)
    return df


def detect_communities(G: nx.DiGraph, resolution: float = 1.0, seed: int = 7, min_size: int = 3) -> list[set]:
    """Louvain on the undirected, value-weighted projection. Communities = densely connected groups, not criminal organisations."""
    if G.number_of_edges() == 0:
        return []
    U = nx.Graph()
    for u, v, d in G.edges(data=True):
        w = np.log1p(d["total"])
        if U.has_edge(u, v):
            U[u][v]["weight"] += w
        else:
            U.add_edge(u, v, weight=w)
    comms = nx.community.louvain_communities(U, weight="weight", resolution=resolution, seed=seed)
    return sorted([set(c) for c in comms if len(c) >= min_size], key=len, reverse=True)


def community_summaries(df: pd.DataFrame, communities: list[set], account_risk: dict[str, float] | None = None,
                        account_alert_types: dict[str, list[str]] | None = None, top_n: int = 5) -> list[dict]:
    out = []
    account_risk = account_risk or {}
    src, dst = df["source_account_id"], df["destination_account_id"]
    for i, comm in enumerate(communities):
        cs = list(comm)
        sin, din = src.isin(comm), dst.isin(comm)
        internal = df[sin & din]
        ext_out = df[sin & ~din]
        ext_in = df[~sin & din]
        tot = float(internal["amount"].sum() + ext_out["amount"].sum() + ext_in["amount"].sum())
        shared = ext_out["destination_account_id"].value_counts()
        shared = shared[shared >= 2].head(5)
        members = sorted(cs, key=lambda a: -account_risk.get(a, 0.0))[:top_n]
        types = pd.concat([internal["transaction_type"], ext_out["transaction_type"]]).value_counts().head(3)
        tl = pd.concat([internal["transaction_timestamp"], ext_out["transaction_timestamp"], ext_in["transaction_timestamp"]])
        density = len(internal.groupby(["source_account_id", "destination_account_id"])) / max(1, len(cs) * (len(cs) - 1))
        out.append({
            "community_id": f"C{i + 1:03d}", "n_accounts": len(cs), "n_transactions": int(len(internal) + len(ext_out) + len(ext_in)),
            "total_value": round(tot, 2), "internal_value": round(float(internal["amount"].sum()), 2),
            "external_out_value": round(float(ext_out["amount"].sum()), 2), "external_in_value": round(float(ext_in["amount"].sum()), 2),
            "internal_share": round(float(internal["amount"].sum()) / tot, 4) if tot else 0.0, "density": round(density, 4),
            "first_activity": tl.min().isoformat() if len(tl) else None, "last_activity": tl.max().isoformat() if len(tl) else None,
            "dominant_types": {k: int(v) for k, v in types.items()},
            "top_risk_accounts": [{"account_id": a, "risk": round(account_risk.get(a, 0.0), 3), "alert_types": (account_alert_types or {}).get(a, [])} for a in members],
            "shared_counterparties": [{"account_id": a, "n_txn_from_community": int(c)} for a, c in shared.items()],
            "max_member_risk": round(max([account_risk.get(a, 0.0) for a in cs] or [0.0]), 3),
            "accounts": cs[:200],
        })
    return out


def trace(idx: TxnIndex, account: str, direction: str = "forward", max_hops: int = 4, min_amount: float = 0.0,
          t0: float | None = None, t1: float | None = None, max_gap_h: float = 168.0, max_paths: int = 100,
          min_flow_fraction: float = 0.0, branch: int = 12, timeout_s: float = 5.0) -> dict:
    """Temporally-consistent multi-hop trace. Forward: each next transfer occurs after the previous one; backward mirrors it.

    NOTE: transaction values alone cannot attribute a specific unit of money to a specific outgoing payment.
    `min_flow_fraction` bounds how much smaller than the preceding hop a payment may be to count as a plausible
    continuation; results are approximate flow tracing, not proof that the same funds moved.
    """
    deadline = time.monotonic() + timeout_s
    fwd = direction == "forward"
    gap = max_gap_h * 3600.0
    lo = -np.inf if t0 is None else t0
    hi = np.inf if t1 is None else t1
    paths: list[list[int]] = []
    timed_out = False

    def candidates(acc: str, last_t: float, last_a: float | None) -> list[int]:
        if fwd:
            rows = idx.out_between(acc, max(last_t, lo), min(last_t + gap, hi))
        else:
            rows = idx.in_between(acc, max(last_t - gap, lo), min(last_t, hi))
        if len(rows) == 0:
            return []
        rows = rows[idx.amt[rows] >= min_amount]
        if last_a is not None and min_flow_fraction > 0:
            if fwd:
                rows = rows[(idx.amt[rows] >= min_flow_fraction * last_a) & (idx.amt[rows] <= 1.25 * last_a)]
            else:
                rows = rows[(idx.amt[rows] <= last_a / max(min_flow_fraction, 1e-9)) & (idx.amt[rows] >= last_a / 1.25)]
        if len(rows) > branch:
            order = np.argsort(np.abs(idx.amt[rows] - last_a)) if last_a is not None else np.argsort(-idx.amt[rows])
            rows = rows[order[:branch]]
        return [int(r) for r in rows]

    def dfs(path: list[int], visited: set[str]):
        nonlocal timed_out
        if time.monotonic() > deadline or len(paths) >= max_paths * 5:
            timed_out = True
            return
        last = path[-1]
        acc = idx.dst[last] if fwd else idx.src[last]
        ext = [] if len(path) >= max_hops else [r for r in candidates(acc, idx.ts[last], idx.amt[last]) if (idx.dst[r] if fwd else idx.src[r]) not in visited]
        if not ext:
            paths.append(list(path))
            return
        for r in ext:
            nxt = idx.dst[r] if fwd else idx.src[r]
            path.append(r)
            visited.add(nxt)
            dfs(path, visited)
            visited.discard(nxt)
            path.pop()

    for r in _first_hop(idx, account, fwd, lo, hi, min_amount, branch * 3):
        dfs([r], {account, idx.dst[r] if fwd else idx.src[r]})
    scored = []
    for p in paths:
        amts = idx.amt[p]
        scored.append((len(p), float(amts.min() / amts.max()), p))
    scored.sort(key=lambda x: (-x[0], -x[1]))
    result = []
    for ln, ret, p in scored[:max_paths]:
        chain = p if fwd else p[::-1]
        accts = [idx.src[chain[0]]] + [idx.dst[r] for r in chain]
        result.append({
            "hops": ln, "accounts": [str(a) for a in accts],
            "transactions": [{"transaction_id": str(idx.txid[r]), "source": str(idx.src[r]), "destination": str(idx.dst[r]),
                              "timestamp": pd.Timestamp(idx.ts[r], unit="s").isoformat(), "amount": float(idx.amt[r])} for r in chain],
            "first_amount": float(idx.amt[chain[0]]), "last_amount": float(idx.amt[chain[-1]]),
            "min_to_max_ratio": round(ret, 4),
            "elapsed_hours": round(float(idx.ts[chain[-1]] - idx.ts[chain[0]]) / 3600, 3),
        })
    return {"account": account, "direction": direction, "paths": result, "n_paths": len(result), "truncated": timed_out or len(scored) > max_paths,
            "approximate": True,
            "note": "Approximate flow tracing: paths are temporally consistent chains of transfers; amounts do not prove the same funds moved."}


def _first_hop(idx: TxnIndex, account: str, fwd: bool, lo: float, hi: float, min_amount: float, limit: int) -> list[int]:
    t0 = lo if np.isfinite(lo) else -1e18
    t1 = hi if np.isfinite(hi) else 1e18
    rows = idx.out_between(account, t0, t1) if fwd else idx.in_between(account, t0, t1)
    if len(rows) == 0:
        return []
    rows = rows[idx.amt[rows] >= min_amount]
    if len(rows) > limit:
        rows = rows[np.argsort(-idx.amt[rows])[:limit]]
    return [int(r) for r in sorted(rows)]
