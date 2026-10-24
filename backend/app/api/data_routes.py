"""Datasets, imports, transactions, accounts, graph."""
from __future__ import annotations

import io
import json
import time
from datetime import datetime

import numpy as np
import pandas as pd
from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy import and_, func, or_
from sqlalchemy.orm import Session

from app.api.common import SessionDep, clean, page_params, require_key, resolve_dataset
from app.core.config import get_settings
from app.db import models as m
from app.graph import analytics as GA
from app.graph.detectors import detect_cycles
from app.graph.index import build_graph, ego_subgraph
from app.ingestion import pipeline as ING
from app.services import jobs
from app.services.pipeline import get_index

router = APIRouter(dependencies=[Depends(require_key)])


# ------------------------------------------------------------------ datasets
def _ds(d: m.Dataset) -> dict:
    return {"dataset_id": d.dataset_id, "name": d.name, "kind": d.kind, "source_dataset": d.source_dataset, "dataset_version": d.dataset_version,
            "created_at": d.created_at.isoformat(), "n_accounts": d.n_accounts, "n_transactions": d.n_transactions,
            "min_ts": d.min_ts.isoformat() if d.min_ts else None, "max_ts": d.max_ts.isoformat() if d.max_ts else None,
            "has_ground_truth": d.has_ground_truth, "status": d.status,
            "provenance": "SYNTHETIC – simulated patterns, not real customers or established financial crime" if d.kind == "synthetic" else "User-supplied upload"}


@router.get("/datasets")
def list_datasets(s: Session = SessionDep):
    return {"items": [_ds(d) for d in s.query(m.Dataset).order_by(m.Dataset.created_at.desc())]}


@router.get("/datasets/{dataset_id}/stats")
def dataset_stats(dataset_id: str, s: Session = SessionDep):
    d = resolve_dataset(s, dataset_id)
    q = s.query(func.count(), func.sum(m.Transaction.amount), func.avg(m.Transaction.amount), func.max(m.Transaction.amount)).filter(m.Transaction.dataset_id == dataset_id).one()
    types = s.query(m.Transaction.transaction_type, func.count(), func.sum(m.Transaction.amount)).filter(m.Transaction.dataset_id == dataset_id).group_by(m.Transaction.transaction_type).all()
    acc_types = s.query(m.Account.account_type, func.count()).filter(m.Account.dataset_id == dataset_id).group_by(m.Account.account_type).all()
    last_run = s.query(m.PipelineRun).filter_by(dataset_id=dataset_id).order_by(m.PipelineRun.started_at.desc()).first()
    return clean({**_ds(d), "total_value": q[1] or 0, "avg_amount": q[2] or 0, "max_amount": q[3] or 0,
                  "by_type": [{"type": t, "count": c, "value": v} for t, c, v in types], "by_account_type": [{"type": t, "count": c} for t, c in acc_types],
                  "last_pipeline_run": None if not last_run else {"run_id": last_run.run_id, "status": last_run.status, "finished_at": last_run.finished_at.isoformat() if last_run.finished_at else None,
                                                                  "window_start": last_run.window_start.isoformat(), "window_end": last_run.window_end.isoformat()}})


@router.delete("/datasets/{dataset_id}")
def delete_dataset(dataset_id: str, s: Session = SessionDep):
    """Data deletion procedure: removes the dataset and every derived row (alerts, scores, labels, investigations)."""
    d = resolve_dataset(s, dataset_id)
    ids = [r[0] for r in s.query(m.Investigation.investigation_id).filter_by(dataset_id=dataset_id)]
    from sqlalchemy import text
    for tbl in ("alert_transactions", "txn_scores", "gt_txn_labels", "gt_scenarios", "alerts", "pipeline_runs", "transactions", "accounts"):
        s.execute(text(f"DELETE FROM {tbl} WHERE dataset_id = :d"), {"d": dataset_id})
    for i in ids:
        for tbl in ("investigation_alerts", "investigation_transactions", "investigation_notes", "audit_log"):
            s.execute(text(f"DELETE FROM {tbl} WHERE investigation_id = :i"), {"i": i})
        s.execute(text("DELETE FROM investigations WHERE investigation_id = :i"), {"i": i})
    s.execute(text("DELETE FROM import_reports WHERE dataset_id = :d"), {"d": dataset_id})
    s.delete(d)
    from app.services.pipeline import clear_cache
    clear_cache(dataset_id)
    return {"deleted": dataset_id}


class _SynthIn:
    pass


@router.post("/datasets/synthetic")
def create_synthetic(quick: bool = True, seed: int = 42):
    """Generate + ingest a synthetic dataset as a background job (quick ≈ 12k txns; standard ≈ 72k)."""
    def work(log):
        from app.db.session import session_scope
        from app.synthetic.generator import GeneratorConfig, generate
        cfg = GeneratorConfig.quick() if quick else GeneratorConfig()
        cfg.seed = seed
        log("generating")
        ds = generate(cfg)
        log("ingesting")
        with session_scope() as s:
            rec = ING.load_synthetic(s, ds, f"AEGIS synthetic ({'quick' if quick else 'standard'}, seed {seed})")
            return {"dataset_id": rec.dataset_id, "report_id": rec.report_id}
    return {"job_id": jobs.submit("synthetic", work)}


# ------------------------------------------------------------------ imports
def _read_upload(f: UploadFile) -> bytes:
    limit = get_settings().max_upload_mb * 1024 * 1024
    buf = io.BytesIO()
    while chunk := f.file.read(1024 * 1024):
        buf.write(chunk)
        if buf.tell() > limit:
            raise HTTPException(413, f"file exceeds {get_settings().max_upload_mb} MB limit")
    if not (f.filename or "").lower().endswith(".csv"):
        raise HTTPException(415, "only .csv files are accepted")
    return buf.getvalue()


@router.post("/imports/inspect")
def inspect(file: UploadFile = File(...)):
    content = _read_upload(file)
    try:
        df = pd.read_csv(io.BytesIO(content), dtype=str, keep_default_na=False, na_values=[""], nrows=50000)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(422, f"unparseable CSV: {type(e).__name__}")
    return ING.inspect_schema(df)


@router.post("/imports")
def import_csv(file: UploadFile = File(...), name: str = Form(""), mapping: str = Form(""), accounts_file: UploadFile | None = File(None), s: Session = SessionDep):
    content = _read_upload(file)
    acc = _read_upload(accounts_file) if accounts_file is not None else None
    try:
        mp = json.loads(mapping) if mapping else None
        if mp is not None and not isinstance(mp, dict):
            raise ValueError
    except ValueError:
        raise HTTPException(422, "mapping must be a JSON object {canonical_field: csv_column}")
    try:
        rec = ING.import_csv_bytes(s, content, file.filename or "upload.csv", (name or file.filename or "Upload")[:200], mp, acc)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(422, f"import failed: {type(e).__name__}: {str(e)[:200]}")
    return _imp(rec)


def _imp(r: m.ImportReport) -> dict:
    return {"report_id": r.report_id, "dataset_id": r.dataset_id, "filename": r.filename, "created_at": r.created_at.isoformat(), "status": r.status, "report": r.report,
            "has_error_report": bool(r.reject_path)}


@router.get("/imports")
def list_imports(s: Session = SessionDep):
    return {"items": [_imp(r) for r in s.query(m.ImportReport).order_by(m.ImportReport.created_at.desc()).limit(50)]}


@router.get("/imports/{report_id}")
def get_import(report_id: str, s: Session = SessionDep):
    r = s.get(m.ImportReport, report_id)
    if r is None:
        raise HTTPException(404, "report not found")
    return _imp(r)


@router.get("/imports/{report_id}/errors")
def download_errors(report_id: str, s: Session = SessionDep):
    r = s.get(m.ImportReport, report_id)
    if r is None or not r.reject_path:
        raise HTTPException(404, "no error report")
    return FileResponse(r.reject_path, media_type="text/csv", filename=f"{report_id}_rejected_rows.csv")


# ------------------------------------------------------------------ transactions / accounts
SORTABLE = {"transaction_timestamp": m.Transaction.transaction_timestamp, "amount": m.Transaction.amount, "transaction_id": m.Transaction.transaction_id,
            "source_account_id": m.Transaction.source_account_id, "destination_account_id": m.Transaction.destination_account_id, "model_risk": m.TxnScore.model_risk}


@router.get("/transactions")
def search_transactions(dataset_id: str | None = None, q: str | None = None, account: str | None = None, direction: str = Query("any", pattern="^(any|in|out)$"),
                        min_amount: float | None = Query(None, ge=0), max_amount: float | None = Query(None, ge=0), transaction_type: str | None = None,
                        date_from: datetime | None = None, date_to: datetime | None = None, sort: str = "transaction_timestamp", order: str = Query("desc", pattern="^(asc|desc)$"),
                        scored_only: bool = False, pp=Depends(page_params), s: Session = SessionDep):
    d = resolve_dataset(s, dataset_id)
    page, size = pp
    T = m.Transaction
    qy = s.query(T, m.TxnScore.model_risk, m.TxnScore.anomaly).outerjoin(m.TxnScore, and_(m.TxnScore.dataset_id == T.dataset_id, m.TxnScore.transaction_id == T.transaction_id)).filter(T.dataset_id == d.dataset_id)
    if q:
        q = q.strip()[:64]
        qy = qy.filter(or_(T.transaction_id == q, T.source_account_id == q, T.destination_account_id == q))
    if account:
        col = {"in": [T.destination_account_id == account], "out": [T.source_account_id == account]}.get(direction, [T.source_account_id == account, T.destination_account_id == account])
        qy = qy.filter(or_(*col))
    if min_amount is not None:
        qy = qy.filter(T.amount >= min_amount)
    if max_amount is not None:
        qy = qy.filter(T.amount <= max_amount)
    if transaction_type:
        qy = qy.filter(T.transaction_type == transaction_type)
    if date_from:
        qy = qy.filter(T.transaction_timestamp >= date_from)
    if date_to:
        qy = qy.filter(T.transaction_timestamp <= date_to)
    if scored_only:
        qy = qy.filter(m.TxnScore.model_risk.isnot(None))
    if sort not in SORTABLE:
        raise HTTPException(422, f"sort must be one of {sorted(SORTABLE)}")
    col = SORTABLE[sort]
    qy = qy.order_by(col.desc() if order == "desc" else col.asc(), T.transaction_id)
    total = qy.order_by(None).count()
    rows = qy.offset((page - 1) * size).limit(size).all()
    return {"items": [{"transaction_id": t.transaction_id, "source_account_id": t.source_account_id, "destination_account_id": t.destination_account_id,
                       "transaction_timestamp": t.transaction_timestamp.isoformat(), "amount": t.amount, "currency": t.currency, "transaction_type": t.transaction_type,
                       "channel": t.channel, "model_risk": risk, "anomaly": an} for t, risk, an in rows], "total": total, "page": page, "page_size": size}


@router.get("/transactions/{transaction_id}")
def transaction_detail(transaction_id: str, dataset_id: str | None = None, s: Session = SessionDep):
    d = resolve_dataset(s, dataset_id)
    t = s.get(m.Transaction, (d.dataset_id, transaction_id))
    if t is None:
        raise HTTPException(404, "transaction not found")
    sc = s.get(m.TxnScore, (d.dataset_id, transaction_id))
    alerts = s.query(m.Alert).join(m.AlertTransaction, m.AlertTransaction.alert_id == m.Alert.alert_id).filter(m.AlertTransaction.transaction_id == transaction_id, m.Alert.dataset_id == d.dataset_id).all()
    return clean({"transaction_id": t.transaction_id, "source_account_id": t.source_account_id, "destination_account_id": t.destination_account_id, "transaction_timestamp": t.transaction_timestamp.isoformat(),
                  "amount": t.amount, "currency": t.currency, "transaction_type": t.transaction_type, "channel": t.channel, "status": t.transaction_status, "source_dataset": t.source_dataset,
                  "ingested_at": t.ingestion_timestamp.isoformat(), "scores": None if sc is None else {"model_risk": sc.model_risk, "anomaly_percentile": sc.anomaly, "rule_score": sc.rule_score},
                  "alerts": [{"alert_id": a.alert_id, "alert_type": a.alert_type, "risk_band": a.risk_band, "risk_score": a.risk_score} for a in alerts]})


@router.get("/accounts")
def search_accounts(dataset_id: str | None = None, q: str = "", limit: int = Query(20, ge=1, le=100), s: Session = SessionDep):
    d = resolve_dataset(s, dataset_id)
    qy = s.query(m.Account).filter(m.Account.dataset_id == d.dataset_id)
    if q:
        qy = qy.filter(m.Account.account_id.like(q.strip()[:40] + "%"))
    return {"items": [{"account_id": a.account_id, "account_type": a.account_type, "created_at": a.created_at.isoformat() if a.created_at else None, "inferred": a.inferred}
                      for a in qy.order_by(m.Account.account_id).limit(limit)]}


@router.get("/accounts/{account_id}")
def account_detail(account_id: str, dataset_id: str | None = None, s: Session = SessionDep):
    d = resolve_dataset(s, dataset_id)
    a = s.get(m.Account, (d.dataset_id, account_id))
    if a is None:
        raise HTTPException(404, "account not found")
    idx = get_index(s, d.dataset_id)
    out_r, in_r = idx.out_rows.get(account_id, np.empty(0, int)), idx.in_rows.get(account_id, np.empty(0, int))
    cps = pd.concat([pd.Series(idx.dst[out_r]), pd.Series(idx.src[in_r])]).value_counts().head(8)
    ids = {x["alert_id"] for x in _alert_map(s, d.dataset_id).get(account_id, [])}
    alerts = s.query(m.Alert).filter(m.Alert.dataset_id == d.dataset_id).filter(or_(m.Alert.entity_id == account_id, m.Alert.alert_id.in_(ids or [""]))).order_by(m.Alert.risk_score.desc()).limit(25).all()
    invs = s.query(m.Investigation).join(m.InvestigationAlert, m.InvestigationAlert.investigation_id == m.Investigation.investigation_id).filter(m.InvestigationAlert.alert_id.in_([x.alert_id for x in alerts] or [""])).all()
    return clean({"account_id": a.account_id, "account_type": a.account_type, "created_at": a.created_at.isoformat() if a.created_at else None, "status": a.account_status,
                  "segment": a.customer_segment, "inferred": a.inferred,
                  "stats": {"outgoing_count": len(out_r), "incoming_count": len(in_r), "outgoing_value": float(idx.amt[out_r].sum()) if len(out_r) else 0.0,
                            "incoming_value": float(idx.amt[in_r].sum()) if len(in_r) else 0.0, "counterparties": len(set(idx.dst[out_r]) | set(idx.src[in_r])),
                            "first_activity": pd.Timestamp(min(idx.ts[out_r].min() if len(out_r) else 1e18, idx.ts[in_r].min() if len(in_r) else 1e18), unit="s").isoformat() if (len(out_r) or len(in_r)) else None,
                            "last_activity": pd.Timestamp(max(idx.ts[out_r].max() if len(out_r) else 0, idx.ts[in_r].max() if len(in_r) else 0), unit="s").isoformat() if (len(out_r) or len(in_r)) else None},
                  "top_counterparties": [{"account_id": k, "n_transactions": int(v)} for k, v in cps.items()],
                  "alerts": [{"alert_id": x.alert_id, "alert_type": x.alert_type, "risk_band": x.risk_band, "risk_score": x.risk_score, "status": x.investigation_status} for x in alerts],
                  "investigations": [{"investigation_id": i.investigation_id, "status": i.status, "priority": i.priority} for i in invs]})


@router.get("/accounts/{account_id}/transactions")
def account_transactions(account_id: str, dataset_id: str | None = None, pp=Depends(page_params), s: Session = SessionDep):
    return search_transactions(dataset_id=dataset_id, account=account_id, direction="any", pp=pp, s=s, q=None, min_amount=None, max_amount=None, transaction_type=None,
                               date_from=None, date_to=None, sort="transaction_timestamp", order="desc", scored_only=False)


# ------------------------------------------------------------------ graph
def _ts(v: datetime | None) -> float | None:
    return None if v is None else pd.Timestamp(v).value / 1e9


_ALERT_MAP: dict = {}


def _alert_map(s: Session, dataset_id: str) -> dict[str, list[dict]]:
    n = s.query(func.count(m.Alert.alert_id), func.max(m.Alert.created_at)).filter(m.Alert.dataset_id == dataset_id).one()
    key = (dataset_id, n[0], str(n[1]))
    if _ALERT_MAP.get("key") != key:
        mp: dict[str, list[dict]] = {}
        for a in s.query(m.Alert).filter(m.Alert.dataset_id == dataset_id):
            for acc in (a.detail or {}).get("accounts", [])[:200]:
                mp.setdefault(acc, []).append({"alert_id": a.alert_id, "alert_type": a.alert_type, "risk_band": a.risk_band, "risk_score": a.risk_score})
        _ALERT_MAP.update(key=key, map=mp)
    return _ALERT_MAP["map"]


@router.get("/graph/subgraph")
def subgraph(account_id: str, dataset_id: str | None = None, hops: int = Query(1, ge=1, le=3), date_from: datetime | None = None, date_to: datetime | None = None,
             min_amount: float = Query(0, ge=0), max_nodes: int = Query(150, ge=10, le=400), s: Session = SessionDep):
    d = resolve_dataset(s, dataset_id)
    st = get_settings()
    idx = get_index(s, d.dataset_id)
    if account_id not in idx.out_rows and account_id not in idx.in_rows:
        raise HTTPException(404, "account has no transactions in this dataset")
    t0 = time.perf_counter()
    g = ego_subgraph(idx, account_id, hops, _ts(date_from), _ts(date_to), min_amount, min(max_nodes, st.graph_max_nodes), st.graph_max_edges)
    amap = _alert_map(s, d.dataset_id)
    types = {a.account_id: a.account_type for a in s.query(m.Account).filter(m.Account.dataset_id == d.dataset_id, m.Account.account_id.in_([n["id"] for n in g["nodes"]]))}
    import networkx as nx
    U = nx.Graph()
    U.add_weighted_edges_from([(e["source"], e["target"], np.log1p(e["total"])) for e in g["edges"]])
    comm = {}
    if U.number_of_edges():
        for i, c in enumerate(nx.community.louvain_communities(U, weight="weight", seed=7)):
            for a in c:
                comm[a] = i
    alert_ids = {}
    for n in g["nodes"]:
        al = amap.get(n["id"], [])
        n.update({"account_type": types.get(n["id"], "unknown"), "alerts": al[:5], "max_risk": max([x["risk_score"] for x in al], default=0.0), "community": comm.get(n["id"])})
        for x in al:
            alert_ids[x["alert_id"]] = x
    g["alerts_in_view"] = list(alert_ids.values())
    g["query_ms"] = round((time.perf_counter() - t0) * 1000, 1)
    g["note"] = "Edges aggregate transactions between a pair of accounts; select an edge to list the underlying transactions."
    return clean(g)


@router.get("/graph/edge")
def edge_transactions(source: str, target: str, dataset_id: str | None = None, s: Session = SessionDep):
    d = resolve_dataset(s, dataset_id)
    T = m.Transaction
    rows = s.query(T).filter(T.dataset_id == d.dataset_id, T.source_account_id == source, T.destination_account_id == target).order_by(T.transaction_timestamp).limit(200).all()
    return {"items": [{"transaction_id": t.transaction_id, "transaction_timestamp": t.transaction_timestamp.isoformat(), "amount": t.amount, "transaction_type": t.transaction_type, "channel": t.channel} for t in rows],
            "total": len(rows)}


@router.get("/graph/trace")
def trace(account_id: str, dataset_id: str | None = None, direction: str = Query("forward", pattern="^(forward|backward)$"), max_hops: int = Query(4, ge=1, le=6),
          min_amount: float = Query(0, ge=0), date_from: datetime | None = None, date_to: datetime | None = None, max_gap_hours: float = Query(168, gt=0, le=720),
          max_paths: int = Query(50, ge=1, le=200), min_flow_fraction: float = Query(0.0, ge=0, le=1), s: Session = SessionDep):
    d = resolve_dataset(s, dataset_id)
    idx = get_index(s, d.dataset_id)
    r = GA.trace(idx, account_id, direction, max_hops, min_amount, _ts(date_from), _ts(date_to), max_gap_hours, max_paths, min_flow_fraction, timeout_s=get_settings().query_timeout_s)
    return clean(r)


@router.get("/graph/cycles")
def cycles(dataset_id: str | None = None, account_id: str | None = None, date_from: datetime | None = None, date_to: datetime | None = None, max_len: int = Query(5, ge=3, le=6),
           window_hours: float = Query(72, gt=0, le=720), min_amount: float = Query(5000, ge=0), s: Session = SessionDep):
    d = resolve_dataset(s, dataset_id)
    idx = get_index(s, d.dataset_id)
    ws = _ts(date_from) if date_from else idx.ts[0]
    we = _ts(date_to) if date_to else idx.ts[-1] + 1
    t0 = time.perf_counter()
    res = detect_cycles(idx, ws, we, max_len, window_hours, min_amount, timeout_s=get_settings().query_timeout_s)
    if account_id:
        res = [c for c in res if account_id in c.accounts]
    return clean({"items": [{"accounts": c.path, "transaction_ids": c.txn_ids, "start": c.start, "end": c.end, "strength": c.strength, "summary": c.summary, **c.evidence} for c in res[:200]],
                  "total": len(res), "query_ms": round((time.perf_counter() - t0) * 1000, 1),
                  "note": "Cycles are structural leads: legitimate circular flows exist (refunds, rotating credit groups)."})


_COMM_CACHE: dict = {}


@router.get("/graph/communities")
def communities(dataset_id: str | None = None, min_size: int = Query(4, ge=2), limit: int = Query(30, ge=1, le=200), min_risk: float = Query(0, ge=0, le=1),
                resolution: float = Query(1.0, gt=0, le=3), s: Session = SessionDep):
    d = resolve_dataset(s, dataset_id)
    run = s.query(m.PipelineRun).filter_by(dataset_id=d.dataset_id, status="completed").order_by(m.PipelineRun.started_at.desc()).first()
    ws, we = (run.window_start, run.window_end) if run else (d.min_ts, d.max_ts + pd.Timedelta(seconds=1))
    key = (d.dataset_id, d.dataset_version, str(ws), str(we), resolution, run.run_id if run else None)
    if key not in _COMM_CACHE:
        idx = get_index(s, d.dataset_id)
        df = idx.df[(idx.df["transaction_timestamp"] >= ws) & (idx.df["transaction_timestamp"] < we)]
        t0 = time.perf_counter()
        G = build_graph(df)
        comms = GA.detect_communities(G, resolution=resolution, min_size=3)
        risk, types = {}, {}
        for acc, als in _alert_map(s, d.dataset_id).items():
            risk[acc] = max(a["risk_score"] for a in als)
            types[acc] = sorted({a["alert_type"] for a in als})
        _COMM_CACHE.clear()
        _COMM_CACHE[key] = {"items": GA.community_summaries(df, comms, risk, types), "n_nodes": G.number_of_nodes(), "n_edges": G.number_of_edges(), "seconds": round(time.perf_counter() - t0, 2),
                            "window": [str(ws), str(we)]}
    c = _COMM_CACHE[key]
    items = [x for x in c["items"] if x["n_accounts"] >= min_size and x["max_member_risk"] >= min_risk]
    items.sort(key=lambda x: (-x["max_member_risk"], -x["n_accounts"]))
    return clean({"items": items[:limit], "total": len(items), "n_communities_all": len(c["items"]), "graph": {"nodes": c["n_nodes"], "edges": c["n_edges"]}, "compute_seconds": c["seconds"], "window": c["window"],
                  "note": "Communities are groups of densely connected accounts (Louvain). They are not evidence of a criminal organisation."})


@router.get("/graph/node-features")
def node_features(account_id: str, dataset_id: str | None = None, hops: int = Query(2, ge=1, le=3), s: Session = SessionDep):
    d = resolve_dataset(s, dataset_id)
    idx = get_index(s, d.dataset_id)
    g = ego_subgraph(idx, account_id, hops, max_nodes=300)
    import networkx as nx
    G = nx.DiGraph()
    for e in g["edges"]:
        G.add_edge(e["source"], e["target"], total=e["total"], count=e["count"])
    f = GA.node_features(G, with_betweenness=G.number_of_nodes() <= 300)
    row = f[f["account_id"] == account_id]
    return clean({"account_id": account_id, "scope": f"{hops}-hop neighbourhood ({G.number_of_nodes()} nodes); values are local to this extract", "features": row.to_dict("records")[0] if len(row) else None,
                  "documentation": "see docs/architecture.md – graph feature table"})
