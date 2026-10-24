"""Measured performance benchmark. Usage:  python benchmarks/scripts/run_benchmarks.py [--sizes 10000 100000 500000]

Every number written to benchmarks/results/ was measured by this script on the machine that ran it; the hardware and software
configuration is recorded alongside. Stages that fail or time out are recorded as such (never silently skipped).
Memory = process RSS sampled at stage boundaries (psutil); the maximum of those samples is reported as `peak_rss_mb`.
"""
from __future__ import annotations

import argparse
import json
import os
import platform
import sys
import tempfile
import time
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))
tmp = tempfile.mkdtemp(prefix="aegis_bench_")
os.environ["AEGIS_DATABASE_URL"] = f"sqlite:///{Path(tmp, 'bench.db').as_posix()}"
os.environ["AEGIS_DATA_DIR"] = str(Path(tmp, "data"))
os.environ["AEGIS_ARTIFACT_DIR"] = str(Path(tmp, "artifacts"))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
import psutil  # noqa: E402

from app.db import models as m  # noqa: E402
from app.db.session import init_db, session_scope  # noqa: E402
from app.features.engine import ALL_FEATURES, compute_features  # noqa: E402
from app.graph import analytics as GA  # noqa: E402
from app.graph import detectors as D  # noqa: E402
from app.graph.index import TxnIndex, build_graph, ego_subgraph  # noqa: E402
from app.ingestion.pipeline import load_synthetic  # noqa: E402
from app.ml import training as T  # noqa: E402
from app.synthetic.generator import GeneratorConfig, generate  # noqa: E402

PROC = psutil.Process()
peak = [0.0]


def rss():
    v = PROC.memory_info().rss / 2**20
    peak[0] = max(peak[0], v)
    return v


def timed(res: dict, key: str, fn, *a, **kw):
    t = time.perf_counter()
    try:
        out = fn(*a, **kw)
        res[key] = round(time.perf_counter() - t, 2)
        print(f"  [{key}] {res[key]}s  (rss {rss():.0f} MB)", flush=True)
        return out
    except Exception as e:  # noqa: BLE001
        res[key] = f"FAILED: {type(e).__name__}: {e}"
        res.setdefault("errors", []).append(traceback.format_exc()[-600:])
        return None


def run_size(n: int) -> dict:
    res: dict = {"n_transactions_requested": n}
    peak[0] = rss()
    ds = timed(res, "generate_s", generate, GeneratorConfig.sized(n))
    if ds is None:
        return res
    res["n_transactions_generated"] = len(ds.transactions)
    init_db()
    with session_scope() as s:
        rec = timed(res, "import_s (validate+dedupe+DB write)", load_synthetic, s, ds, f"bench-{n}")
        ds_id = rec.dataset_id
        res["rows_accepted"] = rec.report["rows_accepted"]
        df, created = T.load_txns(s, ds_id)
    res["db_read_s"] = None
    t = time.perf_counter()
    with session_scope() as s:
        df, created = T.load_txns(s, ds_id)
    res["db_read_s"] = round(time.perf_counter() - t, 2)
    feats = timed(res, "feature_generation_s", compute_features, df, created)
    G = timed(res, "graph_construction_s", build_graph, df)
    if G is not None:
        res["graph_nodes"], res["graph_edges"] = G.number_of_nodes(), G.number_of_edges()
        nf = timed(res, "graph_features_s (degree,reciprocity,pagerank,clustering)", GA.node_features, G)
        comms = timed(res, "community_detection_s (Louvain)", GA.detect_communities, G)
        res["n_communities"] = len(comms or [])
    idx = timed(res, "index_build_s", TxnIndex, df)
    if idx is not None:
        tm: dict = {}
        t = time.perf_counter()
        dets = D.run_all(idx, idx.ts[0], idx.ts[-1] + 1, timings=tm)
        res["detectors_total_s"] = round(time.perf_counter() - t, 2)
        res["detectors_by_stage_s"] = tm
        res["n_detections"] = len(dets)
        rng = np.random.default_rng(0)
        accts = list(idx.out_rows)
        pick = [accts[i] for i in rng.choice(len(accts), min(30, len(accts)), replace=False)]
        lat = []
        for a in pick:
            t = time.perf_counter()
            ego_subgraph(idx, a, hops=2)
            lat.append((time.perf_counter() - t) * 1000)
        res["subgraph_2hop_ms"] = {"p50": round(float(np.percentile(lat, 50)), 1), "p95": round(float(np.percentile(lat, 95)), 1)}
        lat = []
        for a in pick:
            t = time.perf_counter()
            GA.trace(idx, a, "forward", max_hops=4, timeout_s=5)
            lat.append((time.perf_counter() - t) * 1000)
        res["trace_4hop_ms"] = {"p50": round(float(np.percentile(lat, 50)), 1), "p95": round(float(np.percentile(lat, 95)), 1)}
        rss()
    # model training on the labelled data (LightGBM only: the benchmark measures cost, not model selection)
    if feats is not None:
        try:
            import lightgbm as lgb
            from app.ml.training import load_labels
            with session_scope() as s:
                lab, _ = load_labels(s, ds_id)
            d = feats.merge(lab, on="transaction_id", how="left")
            y = d["is_suspicious_sim"].fillna(False).astype(int).to_numpy()
            ts = pd.Series(pd.to_datetime(df["transaction_timestamp"].to_numpy()))
            cut = ts.quantile(0.75)
            tr = (ts < cut).to_numpy()
            X = d[ALL_FEATURES].astype(float)
            t = time.perf_counter()
            mdl = lgb.LGBMClassifier(n_estimators=200, learning_rate=0.05, num_leaves=31, verbose=-1, n_jobs=4).fit(X[tr], y[tr])
            res["model_training_s (LightGBM, 200 trees)"] = round(time.perf_counter() - t, 2)
            Xt = X[~tr]
            t = time.perf_counter()
            mdl.predict_proba(Xt.iloc[:1000])
            res["prediction_1000_rows_ms"] = round((time.perf_counter() - t) * 1000, 1)
            one = []
            for i in range(100):
                t = time.perf_counter()
                mdl.predict_proba(Xt.iloc[i:i + 1])
                one.append((time.perf_counter() - t) * 1000)
            res["prediction_single_row_ms"] = {"p50": round(float(np.percentile(one, 50)), 2), "p95": round(float(np.percentile(one, 95)), 2)}
            rss()
        except Exception as e:  # noqa: BLE001
            res["model_training_s"] = f"FAILED: {type(e).__name__}: {e}"
    res["peak_rss_mb"] = round(peak[0], 0)
    return res


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sizes", type=int, nargs="+", default=[10000, 100000, 500000])
    ap.add_argument("--out", default=str(ROOT / "benchmarks" / "results" / "benchmark_results.json"))
    a = ap.parse_args()
    import lightgbm, networkx, sklearn, sqlalchemy
    env = {"platform": platform.platform(), "processor": platform.processor(), "cpu_logical_cores": os.cpu_count(), "ram_gb": round(psutil.virtual_memory().total / 2**30, 1),
           "python": platform.python_version(), "pandas": pd.__version__, "numpy": np.__version__, "networkx": networkx.__version__, "lightgbm": lightgbm.__version__,
           "sklearn": sklearn.__version__, "sqlalchemy": sqlalchemy.__version__, "database": "SQLite (WAL)"}
    out = {"environment": env, "runs": [], "started": pd.Timestamp.utcnow().isoformat()}
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    if Path(a.out).exists():   # re-running a subset of sizes updates those sizes and keeps the others
        try:
            prev = json.loads(Path(a.out).read_text(encoding="utf-8"))
            out["runs"] = [r for r in prev.get("runs", []) if r["n_transactions_requested"] not in a.sizes]
        except Exception:  # noqa: BLE001
            pass
    for n in a.sizes:
        print(f"== benchmark {n:,} transactions", flush=True)
        t = time.perf_counter()
        r = run_size(n)
        r["wall_clock_total_s"] = round(time.perf_counter() - t, 1)
        out["runs"].append(r)
        out["runs"].sort(key=lambda x: x["n_transactions_requested"])
        print(json.dumps(r, indent=1, default=str)[:1800], flush=True)
        Path(a.out).write_text(json.dumps(out, indent=1, default=str), encoding="utf-8")
    write_md(out, Path(a.out).with_suffix(".md"))


def write_md(out: dict, p: Path):
    env = out["environment"]
    lines = ["# AEGIS benchmark results (measured)", "", "Generated by `benchmarks/scripts/run_benchmarks.py`. Numbers are from a single run on the machine below; they are not guarantees.", "",
             "| Item | Value |", "|---|---|"] + [f"| {k} | {v} |" for k, v in env.items()] + ["", "| Stage | " + " | ".join(f"{r['n_transactions_requested']:,}" for r in out["runs"]) + " |",
                                                                                                 "|---|" + "---|" * len(out["runs"])]
    keys = []
    for r in out["runs"]:
        for k in r:
            if k not in keys and k not in ("errors", "detectors_by_stage_s"):
                keys.append(k)
    for k in keys:
        lines.append(f"| {k} | " + " | ".join(str(r.get(k, "—")) for r in out["runs"]) + " |")
    lines += ["", "Detector time by stage (s):", ""]
    for r in out["runs"]:
        lines.append(f"- {r['n_transactions_requested']:,}: {r.get('detectors_by_stage_s')}")
    p.write_text("\n".join(lines), encoding="utf-8")


if __name__ == "__main__":
    main()
