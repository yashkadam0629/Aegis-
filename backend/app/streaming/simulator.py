"""Simulated real-time processing (in-process; no Kafka/Redpanda required).

event -> validation -> dedup -> incremental feature update -> risk scoring -> alert candidate -> result sink

It reuses the same `FeatureState` as the batch pipeline, so streaming features equal batch features for the same
event order (tested). Latency/throughput are MEASURED on the machine that runs the simulation and reported with
that machine's description; they are not production claims. A message broker was deliberately not added: the
processing logic, not transport, is what this prototype evaluates (see docs/architecture.md, decision D7).
"""
from __future__ import annotations

import platform
import time
import uuid

import numpy as np
import pandas as pd

from app.features.engine import ALL_FEATURES, FeatureState


def hardware() -> dict:
    return {"platform": platform.platform(), "processor": platform.processor() or "unknown", "python": platform.python_version(), "cpu_count": __import__("os").cpu_count()}


def make_events(df: pd.DataFrame, n: int, dup_rate: float = 0.01, invalid_rate: float = 0.005, seed: int = 0) -> list[dict]:
    """Reproducible event stream from historical transactions, with injected duplicates and malformed events."""
    rng = np.random.default_rng(seed)
    ev = []
    for r in df.head(n).itertuples():
        ev.append({"event_id": f"EV-{r.transaction_id}", "txn": r.transaction_id, "src": r.source_account_id, "dst": r.destination_account_id,
                   "ts": r.transaction_timestamp, "amount": float(r.amount), "type": r.transaction_type, "channel": r.channel or ""})
    out = []
    for e in ev:
        out.append(e)
        if rng.random() < dup_rate:
            out.append(dict(e))                                  # redelivery of the same event
        if rng.random() < invalid_rate:
            bad = dict(e)
            bad["event_id"] = "EV-BAD-" + uuid.uuid4().hex[:6]
            bad["amount"] = -1.0                                 # malformed
            out.append(bad)
    return out


def simulate(events: list[dict], bundle, created: dict | None = None, warmup: list[dict] | None = None, batch_size: int = 1,
             transient_failure_rate: float = 0.0, max_retries: int = 3, alert_threshold: float | None = None, seed: int = 1) -> dict:
    rng = np.random.default_rng(seed)
    st = FeatureState({k: pd.Timestamp(v).value / 1e9 for k, v in (created or {}).items() if pd.notna(v)})
    for w in warmup or []:
        t = pd.Timestamp(w["ts"])
        st.process(w["src"], w["dst"], t.value / 1e9, w["amount"], w["type"], w["channel"], t.hour, t.dayofweek)
    thr = alert_threshold if alert_threshold is not None else (bundle.meta["thresholds"]["cost"] if bundle is not None else 1.0)
    seen: set[str] = set()
    stats = {"received": 0, "duplicates_dropped": 0, "invalid_dropped": 0, "processed": 0, "retries": 0, "dead_letter": 0, "alerts": 0}
    failures: list[dict] = []
    alerts: list[dict] = []
    lat_ms: list[float] = []
    feats_out: list[dict] = []
    batch: list[tuple[dict, dict, float]] = []
    t_start = time.perf_counter()

    def flush():
        nonlocal batch
        if not batch:
            return
        t0 = time.perf_counter()
        X = pd.DataFrame([b[1] for b in batch])
        raw = bundle.raw(X) if bundle is not None else np.zeros(len(batch))
        score_ms = (time.perf_counter() - t0) * 1000
        for (e, f, feat_ms), s in zip(batch, raw):
            lat_ms.append(feat_ms + score_ms)       # processing time attributable to this event (batch scoring cost shared)
            stats["processed"] += 1
            if s >= thr:
                stats["alerts"] += 1
                alerts.append({"event_id": e["event_id"], "transaction_id": e["txn"], "score": float(s), "processed_at": pd.Timestamp.utcnow().isoformat()})
        batch = []

    for e in events:
        stats["received"] += 1
        if e["event_id"] in seen:
            stats["duplicates_dropped"] += 1
            continue
        if not (np.isfinite(e["amount"]) and e["amount"] > 0 and e["src"] and e["dst"] and e["src"] != e["dst"]):
            stats["invalid_dropped"] += 1
            failures.append({"event_id": e["event_id"], "reason": "validation"})
            continue
        ok = False
        for attempt in range(max_retries + 1):
            try:
                if transient_failure_rate and rng.random() < transient_failure_rate:
                    raise RuntimeError("transient failure (simulated)")
                t0 = time.perf_counter()
                t = pd.Timestamp(e["ts"])
                f = st.process(e["src"], e["dst"], t.value / 1e9, e["amount"], e["type"], e["channel"], t.hour, t.dayofweek)
                feat_ms = (time.perf_counter() - t0) * 1000
                ok = True
                break
            except RuntimeError:
                stats["retries"] += 1
        if not ok:
            stats["dead_letter"] += 1
            failures.append({"event_id": e["event_id"], "reason": "retries exhausted"})
            continue
        seen.add(e["event_id"])
        batch.append((e, f, feat_ms))
        feats_out.append({"transaction_id": e["txn"], **f})
        if len(batch) >= batch_size:
            flush()
    flush()
    elapsed = time.perf_counter() - t_start
    lat = np.array(lat_ms) if lat_ms else np.array([0.0])
    return {"stats": stats, "elapsed_seconds": round(elapsed, 3), "throughput_events_per_s": round(stats["processed"] / elapsed, 1) if elapsed else None,
            "latency_ms": {"p50": round(float(np.percentile(lat, 50)), 3), "p95": round(float(np.percentile(lat, 95)), 3), "p99": round(float(np.percentile(lat, 99)), 3), "max": round(float(lat.max()), 3)},
            "batch_size": batch_size, "hardware": hardware(), "alerts_sample": alerts[:20], "failures_sample": failures[:20],
            "features": pd.DataFrame(feats_out, columns=["transaction_id"] + ALL_FEATURES) if feats_out else pd.DataFrame(),
            "caveat": "Single-process simulation on one machine; latency excludes network/broker time. Not a production throughput claim."}
