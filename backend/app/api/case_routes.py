"""Alerts, investigations, models, monitoring, overview, insights, jobs, evaluation."""
from __future__ import annotations

import json
from typing import Literal

import numpy as np
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, text
from sqlalchemy.orm import Session

from app.api.common import SessionDep, actor, clean, page_params, require_key, resolve_dataset
from app.core.config import get_settings
from app.db import models as m
from app.investigations import service as inv
from app.ml import training as T
from app.services import jobs
from app.services.pipeline import run_pipeline

router = APIRouter(dependencies=[Depends(require_key)])


def _alert_row(a: m.Alert) -> dict:
    return {"alert_id": a.alert_id, "dataset_id": a.dataset_id, "entity_type": a.entity_type, "entity_id": a.entity_id, "alert_type": a.alert_type,
            "risk_score": round(a.risk_score, 4), "risk_band": a.risk_band, "n_transactions": a.n_transactions, "n_accounts": a.n_accounts, "gross_value": a.gross_value,
            "exposed_value": a.exposed_value, "alert_timestamp": a.alert_timestamp.isoformat(), "investigation_status": a.investigation_status, "model_version": a.model_version,
            "feature_version": a.feature_version, "evidence_summary": (a.detail or {}).get("evidence_summary", ""), "methods": (a.detail or {}).get("detection_methods", [])}


# ------------------------------------------------------------------ alerts
@router.get("/alerts")
def list_alerts(dataset_id: str | None = None, band: str | None = None, alert_type: str | None = None, status: str | None = None, entity: str | None = None,
                min_score: float = Query(0, ge=0, le=1), sort: str = Query("risk_score", pattern="^(risk_score|alert_timestamp|exposed_value|n_transactions)$"),
                order: str = Query("desc", pattern="^(asc|desc)$"), pp=Depends(page_params), s: Session = SessionDep):
    d = resolve_dataset(s, dataset_id)
    page, size = pp
    q = s.query(m.Alert).filter(m.Alert.dataset_id == d.dataset_id, m.Alert.risk_score >= min_score)
    if band:
        q = q.filter(m.Alert.risk_band == band)
    if alert_type:
        q = q.filter(m.Alert.alert_type == alert_type)
    if status:
        q = q.filter(m.Alert.investigation_status == status)
    if entity:
        q = q.filter(m.Alert.entity_id == entity.strip()[:64])
    col = getattr(m.Alert, sort)
    total = q.count()
    rows = q.order_by(col.desc() if order == "desc" else col.asc(), m.Alert.alert_id).offset((page - 1) * size).limit(size).all()
    assignees = {r.alert_id: r.assigned_analyst for r in s.query(m.InvestigationAlert.alert_id, m.Investigation.assigned_analyst).join(m.Investigation, m.Investigation.investigation_id == m.InvestigationAlert.investigation_id)}
    items = [{**_alert_row(a), "assigned_analyst": assignees.get(a.alert_id)} for a in rows]
    facets = {"band": dict(s.query(m.Alert.risk_band, func.count()).filter(m.Alert.dataset_id == d.dataset_id).group_by(m.Alert.risk_band).all()),
              "alert_type": dict(s.query(m.Alert.alert_type, func.count()).filter(m.Alert.dataset_id == d.dataset_id).group_by(m.Alert.alert_type).all())}
    return clean({"items": items, "total": total, "page": page, "page_size": size, "facets": facets})


@router.get("/alerts/{alert_id}")
def alert_detail(alert_id: str, s: Session = SessionDep):
    a = s.get(m.Alert, alert_id)
    if a is None:
        raise HTTPException(404, "alert not found")
    ids = [r[0] for r in s.query(m.AlertTransaction.transaction_id).filter_by(alert_id=alert_id)]
    T_ = m.Transaction
    txns = (s.query(T_, m.TxnScore.model_risk).outerjoin(m.TxnScore, (m.TxnScore.dataset_id == T_.dataset_id) & (m.TxnScore.transaction_id == T_.transaction_id))
            .filter(T_.dataset_id == a.dataset_id, T_.transaction_id.in_(ids)).order_by(T_.transaction_timestamp).limit(500).all())
    invs = s.query(m.Investigation).join(m.InvestigationAlert, m.InvestigationAlert.investigation_id == m.Investigation.investigation_id).filter(m.InvestigationAlert.alert_id == alert_id).all()
    return clean({**_alert_row(a), "detail": a.detail, "unique_value": a.unique_value, "model_score": a.model_score, "rule_score": a.rule_score,
                  "evidence_start": a.evidence_start.isoformat() if a.evidence_start else None, "evidence_end": a.evidence_end.isoformat() if a.evidence_end else None,
                  "transactions": [{"transaction_id": t.transaction_id, "source_account_id": t.source_account_id, "destination_account_id": t.destination_account_id,
                                    "transaction_timestamp": t.transaction_timestamp.isoformat(), "amount": t.amount, "transaction_type": t.transaction_type, "model_risk": r} for t, r in txns],
                  "investigations": [{"investigation_id": i.investigation_id, "status": i.status, "priority": i.priority, "assigned_analyst": i.assigned_analyst} for i in invs],
                  "disclaimer": "Potentially suspicious activity flagged for review. This is a model-generated risk estimate, not a finding of wrongdoing."})


@router.get("/alerts/{alert_id}/explanation")
def alert_explanation(alert_id: str, s: Session = SessionDep):
    a = s.get(m.Alert, alert_id)
    if a is None:
        raise HTTPException(404, "alert not found")
    d = a.detail or {}
    return clean({"alert_id": alert_id, "risk_breakdown": d.get("risk_breakdown"), "feature_explanation": d.get("explanation"), "explained_transaction": d.get("explained_transaction"),
                  "detections": d.get("detections"), "alternative_scores": d.get("alternative_scores"),
                  "caveats": ["Feature attributions describe how the model arrived at its score; they are not evidence of causality or intent.", *d.get("limitations", [])]})


# ------------------------------------------------------------------ investigations
class InvestigationCreate(BaseModel):
    alert_id: str
    title: str | None = Field(None, max_length=300)
    priority: Literal["Low", "Medium", "High", "Critical"] | None = None
    assigned_analyst: str | None = Field(None, max_length=100)


class InvestigationUpdate(BaseModel):
    status: str | None = None
    priority: str | None = None
    assigned_analyst: str | None = Field(None, max_length=100)
    disposition: str | None = None
    findings: str | None = Field(None, max_length=10000)


class NoteIn(BaseModel):
    body: str = Field(..., min_length=1, max_length=5000)


class LinkAlertIn(BaseModel):
    alert_id: str


class AttachIn(BaseModel):
    transaction_ids: list[str] = Field(..., min_length=1, max_length=500)


def _guard(fn):
    try:
        return fn()
    except inv.NotFound as e:
        raise HTTPException(404, str(e))
    except inv.CaseError as e:
        raise HTTPException(409, str(e))


@router.get("/investigations/meta")
def investigation_meta():
    return {"statuses": inv.STATUSES, "priorities": inv.PRIORITIES, "dispositions": inv.DISPOSITIONS, "transitions": {k: sorted(v) for k, v in inv.TRANSITIONS.items()},
            "disposition_notice": "Dispositions are analyst workflow outcomes, not legal findings."}


@router.post("/investigations", status_code=201)
def create_investigation(body: InvestigationCreate, who: str = Depends(actor), s: Session = SessionDep):
    i = _guard(lambda: inv.create_from_alert(s, body.alert_id, who, body.title, body.priority, body.assigned_analyst))
    return inv.serialize(s, i, True)


@router.get("/investigations")
def list_investigations(dataset_id: str | None = None, status: str | None = None, priority: str | None = None, assigned: str | None = None, pp=Depends(page_params), s: Session = SessionDep):
    d = resolve_dataset(s, dataset_id)
    page, size = pp
    q = s.query(m.Investigation).filter(m.Investigation.dataset_id == d.dataset_id)
    if status:
        q = q.filter(m.Investigation.status == status)
    if priority:
        q = q.filter(m.Investigation.priority == priority)
    if assigned:
        q = q.filter(m.Investigation.assigned_analyst == assigned)
    total = q.count()
    rows = q.order_by(m.Investigation.updated_at.desc()).offset((page - 1) * size).limit(size).all()
    return {"items": [inv.serialize(s, i) for i in rows], "total": total, "page": page, "page_size": size}


@router.get("/investigations/{inv_id}")
def get_investigation(inv_id: str, s: Session = SessionDep):
    i = _guard(lambda: inv._get(s, inv_id))
    return inv.serialize(s, i, True)


@router.patch("/investigations/{inv_id}")
def update_investigation(inv_id: str, body: InvestigationUpdate, who: str = Depends(actor), s: Session = SessionDep):
    i = _guard(lambda: inv.update(s, inv_id, who, **body.model_dump(exclude_unset=True)))
    return inv.serialize(s, i, True)


@router.post("/investigations/{inv_id}/notes", status_code=201)
def add_note(inv_id: str, body: NoteIn, who: str = Depends(actor), s: Session = SessionDep):
    n = _guard(lambda: inv.add_note(s, inv_id, who, body.body))
    return {"note_id": n.note_id, "author": n.author, "created_at": n.created_at.isoformat(), "body": n.body}


@router.post("/investigations/{inv_id}/alerts", status_code=201)
def link_alert(inv_id: str, body: LinkAlertIn, who: str = Depends(actor), s: Session = SessionDep):
    _guard(lambda: inv.link_alert(s, inv_id, body.alert_id, who))
    return inv.serialize(s, inv._get(s, inv_id), True)


@router.post("/investigations/{inv_id}/transactions", status_code=201)
def attach(inv_id: str, body: AttachIn, who: str = Depends(actor), s: Session = SessionDep):
    n = _guard(lambda: inv.attach_transactions(s, inv_id, body.transaction_ids, who))
    return {"attached": n}


@router.get("/investigations/{inv_id}/history")
def inv_history(inv_id: str, s: Session = SessionDep):
    return {"items": _guard(lambda: inv.history(s, inv_id))}


# ------------------------------------------------------------------ models / monitoring
def _run(r: m.ModelRun, full: bool = False) -> dict:
    d = {"model_run_id": r.model_run_id, "model_name": r.model_name, "model_version": r.model_version, "dataset_id": r.dataset_id, "dataset_version": r.dataset_version,
         "feature_version": r.feature_version, "training_timestamp": r.training_timestamp.isoformat(), "is_active": r.is_active, "configuration": r.configuration}
    if full:
        d["evaluation_metrics"] = r.evaluation_metrics
    else:
        em = r.evaluation_metrics or {}
        sel = (em.get("results", {}).get("models", {}) or {}).get(em.get("selected_model", ""), {})
        d["summary"] = {"selected": em.get("selected_model"), "test_pr_auc": (sel.get("test") or {}).get("pr_auc"), "validation_pr_auc": (sel.get("validation") or {}).get("pr_auc")}
    return d


@router.get("/models")
def list_models(s: Session = SessionDep):
    return clean({"items": [_run(r) for r in s.query(m.ModelRun).order_by(m.ModelRun.training_timestamp.desc())]})


@router.get("/models/active")
def active_model(s: Session = SessionDep):
    r = s.query(m.ModelRun).filter(m.ModelRun.is_active.is_(True)).first()
    if r is None:
        raise HTTPException(404, "no active model")
    return clean(_run(r, True))


@router.get("/models/{run_id}/metrics")
def model_metrics(run_id: str, s: Session = SessionDep):
    r = s.get(m.ModelRun, run_id)
    if r is None:
        raise HTTPException(404, "model run not found")
    return clean(_run(r, True))


@router.post("/models/train")
def train_model(dataset_id: str | None = None, s: Session = SessionDep):
    d = resolve_dataset(s, dataset_id)
    if not d.has_ground_truth:
        raise HTTPException(422, "this dataset has no labels; supervised training needs labelled data")

    def work(log):
        from app.db.session import session_scope
        with session_scope() as ss:
            r = T.train_and_evaluate(ss, d.dataset_id, log=log)
            return {"model_run_id": r["model_run_id"], "selected": r["selected_model"]}
    return {"job_id": jobs.submit("train", work)}


@router.post("/models/{run_id}/activate")
def activate_model(run_id: str, who: str = Depends(actor), s: Session = SessionDep):
    """Promotion is an explicit human action; drift or metrics never trigger it automatically."""
    r = s.get(m.ModelRun, run_id)
    if r is None:
        raise HTTPException(404, "model run not found")
    for x in s.query(m.ModelRun).filter(m.ModelRun.is_active.is_(True)):
        x.is_active = False
    r.is_active = True
    return {"activated": run_id, "by": who}


@router.post("/pipeline/run")
def start_pipeline(dataset_id: str | None = None, s: Session = SessionDep):
    d = resolve_dataset(s, dataset_id)

    def work(log):
        from app.db.session import session_scope
        with session_scope() as ss:
            r = run_pipeline(ss, d.dataset_id, log=log)
            return {k: v for k, v in r.items()}
    return {"job_id": jobs.submit("pipeline", work)}


@router.get("/jobs")
def list_jobs():
    return {"items": [{k: v for k, v in j.items() if k != "result"} | {"result": j["result"]} for j in jobs.list_jobs()]}


@router.get("/jobs/{job_id}")
def get_job(job_id: str):
    j = jobs.get(job_id)
    if j is None:
        raise HTTPException(404, "job not found")
    return clean(j)


@router.get("/monitoring/summary")
def monitoring(dataset_id: str | None = None, s: Session = SessionDep):
    d = resolve_dataset(s, dataset_id)
    runs = s.query(m.PipelineRun).filter_by(dataset_id=d.dataset_id).order_by(m.PipelineRun.started_at.desc()).limit(10).all()
    open_alerts = s.query(func.count()).filter(m.Alert.dataset_id == d.dataset_id, m.Alert.investigation_status == "Not opened").scalar()
    closed = s.query(m.Investigation).filter(m.Investigation.dataset_id == d.dataset_id, m.Investigation.closed_at.isnot(None)).all()
    turn = [(i.closed_at - i.created_at).total_seconds() / 86400 for i in closed]
    latest = runs[0].metrics if runs else {}
    return clean({"runs": [{"run_id": r.run_id, "status": r.status, "started_at": r.started_at.isoformat(), "window_start": r.window_start.isoformat(), "window_end": r.window_end.isoformat(),
                            "metrics": r.metrics, "error": r.error} for r in runs],
                  "alert_backlog": open_alerts, "investigation_turnaround_days_median": float(np.median(turn)) if turn else None, "n_closed_investigations": len(closed),
                  "drift": (latest or {}).get("drift"),
                  "labels_available_for_performance": bool(d.has_ground_truth),
                  "notes": ["Drift indicates input/score distribution change only; it does not show accuracy has fallen.",
                            "Model promotion is manual: use POST /models/{id}/activate after reviewing the benchmark comparison.",
                            "Performance with delayed labels can only be computed once reviewer outcomes or ground truth exist."]})


# ------------------------------------------------------------------ overview / insights
@router.get("/overview")
def overview(dataset_id: str | None = None, s: Session = SessionDep):
    d = resolve_dataset(s, dataset_id)
    ds_id = d.dataset_id
    tx_sum = s.query(func.sum(m.Transaction.amount)).filter(m.Transaction.dataset_id == ds_id).scalar() or 0.0
    alerts = s.query(func.count(), func.sum(m.Alert.exposed_value)).filter(m.Alert.dataset_id == ds_id).one()
    active_alerts = s.query(func.count()).filter(m.Alert.dataset_id == ds_id, m.Alert.investigation_status.in_(["Not opened", "New", "In Review", "Escalated"])).scalar()
    uniq_exposed = s.execute(text("SELECT COALESCE(SUM(t.amount),0) FROM transactions t JOIN (SELECT DISTINCT transaction_id FROM alert_transactions WHERE dataset_id = :d) a "
                                  "ON a.transaction_id = t.transaction_id WHERE t.dataset_id = :d"), {"d": ds_id}).scalar()
    hp = s.query(func.count()).filter(m.Investigation.dataset_id == ds_id, m.Investigation.priority.in_(["High", "Critical"]), m.Investigation.status != "Closed").scalar()
    by_band = dict(s.query(m.Alert.risk_band, func.count()).filter(m.Alert.dataset_id == ds_id).group_by(m.Alert.risk_band).all())
    pat = s.query(m.Alert.alert_type, func.count(), func.sum(m.Alert.exposed_value)).filter(m.Alert.dataset_id == ds_id).group_by(m.Alert.alert_type).all()
    run = s.query(m.ModelRun).filter(m.ModelRun.is_active.is_(True)).first()
    last = s.query(m.PipelineRun).filter_by(dataset_id=ds_id, status="completed").order_by(m.PipelineRun.started_at.desc()).first()
    imp = s.query(m.ImportReport).filter_by(dataset_id=ds_id).order_by(m.ImportReport.created_at).first()
    n_networks = s.query(func.count()).filter(m.Alert.dataset_id == ds_id, m.Alert.alert_type.in_(["coordinated_group", "shared_beneficiary", "layered_chain", "cycle", "fan_out"])).scalar()
    perf = None
    if run:
        em = run.evaluation_metrics or {}
        sel = em.get("results", {}).get("models", {}).get(em.get("selected_model", ""), {})
        perf = {"model": em.get("selected_model"), "pr_auc_test": (sel.get("test") or {}).get("pr_auc"), "recall_at_1pct": ((sel.get("test") or {}).get("capacity", {}).get("0.010") or {}).get("recall"),
                "evaluation": "chronological hold-out test period on synthetic labels", "rules_baseline_pr_auc": ((em.get("results", {}).get("rules_baseline") or {}).get("test") or {}).get("pr_auc")}
    rep = (imp.report if imp else {}) or {}
    return clean({
        "dataset": {"dataset_id": ds_id, "name": d.name, "kind": d.kind, "min_ts": d.min_ts, "max_ts": d.max_ts, "dataset_version": d.dataset_version},
        "kpis": {"transactions_analyzed": d.n_transactions, "accounts_analyzed": d.n_accounts, "active_alerts": active_alerts, "high_priority_investigations": hp,
                 "total_value_processed": tx_sum, "potentially_exposed_value": alerts[1] or 0.0, "unique_alert_value": uniq_exposed, "detected_networks": n_networks,
                 "median_investigation_age_days": inv.median_age_days(s, ds_id), "alerts_total": alerts[0]},
        "definitions": {"potentially_exposed_value": "Sum of distinct transactions referenced by any alert (each transaction counted once across alerts). Potentially affected value – NOT a loss or fraud-prevented figure.",
                        "detected_networks": "Alerts describing multi-account structures (chains, cycles, shared beneficiaries, fan-out, coordinated groups) – leads, not confirmed networks.",
                        "active_alerts": "Alerts not yet resolved/closed through an investigation.",
                        "model_performance": "Measured on a held-out chronological test period using SYNTHETIC scenario labels."},
        "alerts_by_band": by_band, "alerts_by_type": [{"alert_type": t, "count": c, "exposed_value": v} for t, c, v in pat],
        "model": None if run is None else {"model_run_id": run.model_run_id, "version": run.model_version, "feature_version": run.feature_version}, "model_performance": perf,
        "freshness": {"last_pipeline_run": last.finished_at if last else None, "window_end": last.window_end if last else None, "data_max_ts": d.max_ts},
        "data_quality": {"rows_received": rep.get("rows_received"), "rows_accepted": rep.get("rows_accepted"), "rows_rejected": rep.get("rows_rejected"), "duplicates": rep.get("duplicate_records"),
                         "rejected_by_reason": rep.get("rejected_by_reason"), "report_id": imp.report_id if imp else None}})


@router.get("/insights")
def insights(dataset_id: str | None = None, s: Session = SessionDep):
    """Deterministic, evidence-backed summaries (no LLM). Every sentence is generated from the figures returned beside it."""
    d = resolve_dataset(s, dataset_id)
    ds_id = d.dataset_id
    rows = s.query(m.Alert.alert_type, func.count(), func.sum(m.Alert.exposed_value), func.avg(m.Alert.risk_score)).filter(m.Alert.dataset_id == ds_id).group_by(m.Alert.alert_type).all()
    total = sum(r[1] for r in rows) or 1
    out = []
    if rows:
        top = sorted(rows, key=lambda r: -r[1])[:3]
        out.append({"title": "Most common detected patterns", "evidence": [{"alert_type": r[0], "alerts": r[1], "share": r[1] / total} for r in top],
                    "text": "; ".join(f"{r[0].replace('_', ' ')} ({r[1]} alerts, {r[1] / total:.0%})" for r in top) + "."})
        topv = sorted(rows, key=lambda r: -(r[2] or 0))[:3]
        out.append({"title": "Potential exposure by pattern type", "evidence": [{"alert_type": r[0], "exposed_value": r[2]} for r in topv],
                    "text": "Highest potentially exposed value: " + "; ".join(f"{r[0].replace('_', ' ')} ₹{(r[2] or 0):,.0f}" for r in topv) + ". Exposure is potentially affected transaction value, not loss."})
    band = dict(s.query(m.Alert.risk_band, func.count()).filter(m.Alert.dataset_id == ds_id).group_by(m.Alert.risk_band).all())
    conc = s.query(m.Alert.entity_id, func.count()).filter(m.Alert.dataset_id == ds_id).group_by(m.Alert.entity_id).having(func.count() > 1).order_by(func.count().desc()).limit(5).all()
    out.append({"title": "Alert concentration", "evidence": {"by_band": band, "repeat_entities": [{"entity": e, "alerts": c} for e, c in conc]},
                "text": f"{sum(band.values())} alerts: " + ", ".join(f"{v} {k}" for k, v in band.items()) + (f". {len(conc)} entities carry more than one alert." if conc else ".")})
    invs = s.query(m.Investigation.status, func.count()).filter(m.Investigation.dataset_id == ds_id).group_by(m.Investigation.status).all()
    unopened = s.query(func.count()).filter(m.Alert.dataset_id == ds_id, m.Alert.investigation_status == "Not opened").scalar()
    out.append({"title": "Investigation backlog", "evidence": {"by_status": dict(invs), "alerts_without_investigation": unopened, "median_open_age_days": inv.median_age_days(s, ds_id)},
                "text": f"{unopened} alerts have no investigation; " + (", ".join(f"{c} {st}" for st, c in invs) if invs else "no investigations opened yet") + "."})
    runs = s.query(m.PipelineRun).filter_by(dataset_id=ds_id, status="completed").order_by(m.PipelineRun.started_at.desc()).limit(2).all()
    if runs and (runs[0].metrics or {}).get("drift"):
        dr = runs[0].metrics["drift"]
        out.append({"title": "Changes in transaction behaviour", "evidence": dr.get("features", [])[:3],
                    "text": f"Score-distribution PSI {dr.get('score_psi')} ({dr.get('score_level')}); {dr.get('n_features_large')} of {dr.get('n_features')} monitored features show large shift versus the pre-window baseline. "
                            "This is a data-distribution signal, not evidence of model deterioration."})
    models = s.query(m.ModelRun).order_by(m.ModelRun.training_timestamp.desc()).limit(2).all()
    if len(models) == 2:
        a, b = [(x.evaluation_metrics.get("results", {}).get("models", {}).get(x.evaluation_metrics.get("selected_model", ""), {}).get("test") or {}).get("pr_auc") for x in models]
        if a is not None and b is not None:
            out.append({"title": "Model performance change", "evidence": {"latest": a, "previous": b}, "text": f"Latest model test PR-AUC {a:.3f} vs previous {b:.3f} (synthetic labels, different training runs)."})
    return clean({"items": out, "method": "deterministic templates over database aggregates; no external language model used"})


@router.get("/evaluation/business-impact")
def business_impact_cached():
    p = get_settings().artifact_dir / "reports" / "business_impact.json"
    if not p.exists():
        raise HTTPException(404, "business impact report not generated yet (run scripts/run_evaluation or POST /evaluation/business-impact)")
    return json.loads(p.read_text())


@router.post("/evaluation/business-impact")
def business_impact_run(dataset_id: str | None = None, s: Session = SessionDep):
    d = resolve_dataset(s, dataset_id)

    def work(log):
        from app.db.session import session_scope
        from app.services.business_impact import run_business_impact
        with session_scope() as ss:
            rep = run_business_impact(ss, d.dataset_id, log=log)
        p = get_settings().artifact_dir / "reports"
        p.mkdir(parents=True, exist_ok=True)
        (p / "business_impact.json").write_text(json.dumps(clean(rep), indent=1))
        return {"saved": str(p / "business_impact.json")}
    return {"job_id": jobs.submit("business_impact", work)}


@router.post("/streaming/simulate")
def streaming_simulate(dataset_id: str | None = None, n_events: int = Query(2000, ge=100, le=20000), batch_size: int = Query(1, ge=1, le=500),
                       failure_rate: float = Query(0.0, ge=0, le=0.5), s: Session = SessionDep):
    """Replay the latest transactions as a simulated event stream through the incremental feature + scoring path (measured, in-process)."""
    from app.services.pipeline import get_index
    from app.streaming import simulator as S
    d = resolve_dataset(s, dataset_id)
    bundle, _ = T.load_bundle(s)
    if bundle is None:
        raise HTTPException(422, "no trained model")
    idx = get_index(s, d.dataset_id)
    df = idx.df
    cut = max(0, len(df) - n_events)
    warm = df.iloc[max(0, cut - 20000):cut]
    created = {a.account_id: a.created_at for a in s.query(m.Account).filter(m.Account.dataset_id == d.dataset_id)}
    warm_ev = [{"src": r.source_account_id, "dst": r.destination_account_id, "ts": r.transaction_timestamp, "amount": float(r.amount), "type": r.transaction_type, "channel": r.channel or ""} for r in warm.itertuples()]
    events = S.make_events(df.iloc[cut:], n_events)
    res = S.simulate(events, bundle, created, warm_ev, batch_size=batch_size, transient_failure_rate=failure_rate)
    res.pop("features")
    return clean(res)
