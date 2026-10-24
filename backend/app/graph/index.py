"""Transaction index + graph construction.

`TxnIndex` is the storage-independent substrate the graph engine works on: contiguous numpy arrays
sorted by time plus per-account row lookups. `GraphSource` is the abstraction point where a graph
database (e.g. Neo4j) could be plugged in; the shipped implementation is NetworkX over this index.
"""
from __future__ import annotations

from typing import Protocol

import networkx as nx
import numpy as np
import pandas as pd

MAX_EDGE_TXN_IDS = 200


class TxnIndex:
    def __init__(self, df: pd.DataFrame):
        df = df.sort_values("transaction_timestamp", kind="stable").reset_index(drop=True)
        self.df = df
        self.n = len(df)
        self.ts = df["transaction_timestamp"].astype("datetime64[s]").astype("int64").to_numpy().astype(float)
        self.src = df["source_account_id"].to_numpy(dtype=object)
        self.dst = df["destination_account_id"].to_numpy(dtype=object)
        self.amt = df["amount"].to_numpy(float)
        self.txid = df["transaction_id"].to_numpy(dtype=object)
        self.ttype = df["transaction_type"].to_numpy(dtype=object) if "transaction_type" in df else np.array([""] * self.n, dtype=object)
        self.out_rows: dict[str, np.ndarray] = {k: np.asarray(v) for k, v in df.groupby("source_account_id").indices.items()}
        self.in_rows: dict[str, np.ndarray] = {k: np.asarray(v) for k, v in df.groupby("destination_account_id").indices.items()}
        self._out_ts = {k: self.ts[v] for k, v in self.out_rows.items()}
        self._in_ts = {k: self.ts[v] for k, v in self.in_rows.items()}
        self._pos = {t: i for i, t in enumerate(self.txid)}

    def window(self, t0: float | None, t1: float | None) -> tuple[int, int]:
        lo = 0 if t0 is None else int(np.searchsorted(self.ts, t0, "left"))
        hi = self.n if t1 is None else int(np.searchsorted(self.ts, t1, "left"))
        return lo, hi

    def out_between(self, acc: str, t0: float, t1: float) -> np.ndarray:
        ts = self._out_ts.get(acc)
        if ts is None:
            return np.empty(0, int)
        return self.out_rows[acc][np.searchsorted(ts, t0, "left"):np.searchsorted(ts, t1, "right")]

    def in_between(self, acc: str, t0: float, t1: float) -> np.ndarray:
        ts = self._in_ts.get(acc)
        if ts is None:
            return np.empty(0, int)
        return self.in_rows[acc][np.searchsorted(ts, t0, "left"):np.searchsorted(ts, t1, "right")]

    def pos(self, txid: str) -> int | None:
        return self._pos.get(txid)

    def rows(self, positions) -> pd.DataFrame:
        return self.df.iloc[list(positions)]


class GraphSource(Protocol):
    """Backend-independent contract for the analysis engine."""

    def build(self, t_start=None, t_end=None, min_amount: float = 0.0) -> nx.DiGraph: ...


def build_graph(df: pd.DataFrame, t_start=None, t_end=None, min_amount: float = 0.0, recent_hours: float = 24.0) -> nx.DiGraph:
    """Directed, weighted, time-aware graph. Each edge keeps the ids of its underlying transactions."""
    d = df
    if t_start is not None:
        d = d[d["transaction_timestamp"] >= pd.Timestamp(t_start)]
    if t_end is not None:
        d = d[d["transaction_timestamp"] < pd.Timestamp(t_end)]
    if min_amount > 0:
        d = d[d["amount"] >= min_amount]
    G = nx.DiGraph()
    if d.empty:
        return G
    cutoff = d["transaction_timestamp"].max() - pd.Timedelta(hours=recent_hours)
    d = d.assign(_recent=(d["transaction_timestamp"] > cutoff).astype(int))
    g = d.groupby(["source_account_id", "destination_account_id"], sort=False)
    agg = g.agg(count=("amount", "size"), total=("amount", "sum"), mean=("amount", "mean"), min=("amount", "min"), max=("amount", "max"),
                first=("transaction_timestamp", "min"), last=("transaction_timestamp", "max"), recent=("_recent", "sum"))
    ids = g["transaction_id"].apply(lambda s: list(s)[:MAX_EDGE_TXN_IDS])
    types = g["transaction_type"].apply(lambda s: sorted(set(s.dropna()))) if "transaction_type" in d else None
    for (u, v), r in agg.iterrows():
        G.add_edge(u, v, count=int(r["count"]), total=float(r["total"]), mean=float(r["mean"]), min=float(r["min"]), max=float(r["max"]),
                   first=r["first"], last=r["last"], recent_count=int(r["recent"]), txn_ids=ids[(u, v)],
                   types=types[(u, v)] if types is not None else [])
    return G


def ego_subgraph(idx: TxnIndex, center: str, hops: int = 2, t0: float | None = None, t1: float | None = None, min_amount: float = 0.0,
                 max_nodes: int = 400, max_edges: int = 1500, per_node: int = 30) -> dict:
    """Bounded neighbourhood extraction (BFS). Returns JSON-ready nodes/edges and a `truncated` flag."""
    lo, hi = idx.window(t0, t1)
    tmin = idx.ts[lo] if hi > lo else 0.0
    tmax = idx.ts[hi - 1] + 1 if hi > lo else 0.0
    nodes: dict[str, int] = {center: 0}
    edges: dict[tuple, dict] = {}
    frontier = [center]
    truncated = False
    for h in range(1, hops + 1):
        nxt = []
        for a in frontier:
            rows = np.concatenate([idx.out_between(a, tmin, tmax), idx.in_between(a, tmin, tmax)])
            if len(rows) == 0:
                continue
            rows = rows[idx.amt[rows] >= min_amount]
            if len(rows) > 4000:  # keep the largest transactions of very busy accounts
                rows = rows[np.argsort(-idx.amt[rows])[:4000]]
            # aggregate to neighbour level, keep the heaviest `per_node` neighbours
            agg: dict[str, float] = {}
            for r in rows:
                other = idx.dst[r] if idx.src[r] == a else idx.src[r]
                agg[other] = agg.get(other, 0.0) + idx.amt[r]
            keep = sorted(agg, key=agg.get, reverse=True)[:per_node]
            if len(agg) > per_node:
                truncated = True
            keepset = set(keep)
            for r in rows:
                u, v = idx.src[r], idx.dst[r]
                other = v if u == a else u
                if other not in keepset:
                    continue
                if other not in nodes:
                    if len(nodes) >= max_nodes:
                        truncated = True
                        continue
                    nodes[other] = h
                    nxt.append(other)
                e = edges.setdefault((u, v), {"source": u, "target": v, "count": 0, "total": 0.0, "txn_ids": [], "first": idx.ts[r], "last": idx.ts[r], "max": 0.0})
                e["count"] += 1
                e["total"] += float(idx.amt[r])
                e["max"] = max(e["max"], float(idx.amt[r]))
                e["first"], e["last"] = min(e["first"], idx.ts[r]), max(e["last"], idx.ts[r])
                if len(e["txn_ids"]) < 50:
                    e["txn_ids"].append(str(idx.txid[r]))
        frontier = nxt
    edge_list = [e for e in edges.values() if e["source"] in nodes and e["target"] in nodes]
    if len(edge_list) > max_edges:
        truncated = True
        edge_list = sorted(edge_list, key=lambda e: -e["total"])[:max_edges]
    for e in edge_list:
        e["first"] = pd.Timestamp(e["first"], unit="s").isoformat()
        e["last"] = pd.Timestamp(e["last"], unit="s").isoformat()
        e["total"] = round(e["total"], 2)
    return {"center": center, "hops": hops, "nodes": [{"id": n, "hop": h} for n, h in nodes.items()], "edges": edge_list, "truncated": truncated,
            "limits": {"max_nodes": max_nodes, "max_edges": max_edges, "per_node": per_node}}
