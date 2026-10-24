"""End-to-end batch pipeline: features -> scoring -> detectors -> dedup -> risk -> exposure -> alerts -> monitoring."""
from __future__ import annotations

import hashlib
import time
import uuid
from collections import defaultdict

import numpy as np
import pandas as pd
from sqlalchemy import delete, text
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db import models as m
from app.features.engine import ALL_FEATURES, FEATURE_VERSION
from app.graph import detectors as D
from app.graph.index import TxnIndex
from app.ml import training as T
from app.monitoring.drift import drift_report
from app.risk import engine as R

# in-process cache of the (expensive) index per dataset
_INDEX_CACHE: dict[str, tuple[str, TxnIndex]] = {}


def get_index(session: Session, dataset_id: str) -> TxnIndex:
    ds = session.get(m.Dataset, dataset_id)
    hit = _INDEX_CACHE.get(dataset_id)
    if hit and hit[0] == ds.dataset_version:
        return hit[1]
    df, _ = T.load_txns(session, dataset_id)
    idx = TxnIndex(df)
    _INDEX_CACHE[dataset_id] = (ds.dataset_version, idx)
    return idx


def clear_cache(dataset_id: str | None = None):
    if dataset_id:
        _INDEX_CACHE.pop(dataset_id, None)
    else:
        _INDEX_CACHE.clear()


def default_window(session: Session, dataset_id: str, run: m.ModelRun | None) -> tuple[pd.Timestamp, pd.Timestamp]:
    ds = session.get(m.Dataset, dataset_id)
    if run is not None and run.dataset_id == dataset_id:
        return pd.Timestamp(run.evaluation_metrics["split"]["test_start"]), ds.max_ts + pd.Timedelta(seconds=1)
    span = ds.max_ts - ds.min_ts
    return ds.min_ts + span * 0.75, ds.max_ts + pd.Timedelta(seconds=1)


def _alert_id(dataset_id: str, det: D.Detection) -> str:
    key = f"{dataset_id}|{det.detector}|{det.entity_id}|{det.start.floor('h').isoformat()}"
    return "AL-" + hashlib.sha1(key.encode()).hexdigest()[:10].upper()


def _cluster(dets: list[D.Detection]) -> list[list[D.Detection]]:
    """Alert de-duplication: detections that describe overlapping evidence are merged into one alert."""
    clusters: list[tuple[set, list[D.Detection]]] = []
    for d in sorted(dets, key=lambda x: -x.strength):
        s = set(d.txn_ids)
        for cs, members in clusters:
            inter = len(s & cs)
            if inter and (inter / len(s | cs) >= 0.3 or inter / min(len(s), len(cs)) >= 0.6):
                members.append(d)
                cs |= s
                break
        else:
            clusters.append((set(s), [d]))
    return [c[1] for c in clusters]


def run_pipeline(session: Session, dataset_id: str, window: tuple | None = None, log=lambda *_: None, with_explanations: bool = True,
                 model_run_id: str | None = None, min_alert_score: float | None = None) -> dict:
    settings = get_settings()
    t_all = time.perf_counter()
    timings: dict[str, float] = {}
    ds = session.get(m.Dataset, dataset_id)
    if ds is None:
        raise ValueError("unknown dataset")
    bundle, run = T.load_bundle(session, model_run_id)
    ws_ts, we_ts = window or default_window(session, dataset_id, run)
    ws, we = pd.Timestamp(ws_ts).value / 1e9, pd.Timestamp(we_ts).value / 1e9
    prun = m.PipelineRun(run_id="PR-" + uuid.uuid4().hex[:8], dataset_id=dataset_id, model_run_id=run.model_run_id if run else None,
                         window_start=ws_ts, window_end=we_ts)
    session.add(prun)
    session.flush()
    cfg = R.DEFAULT
    min_score = cfg.min_alert_score if min_alert_score is None else min_alert_score
    try:
        t = time.perf_counter()
        feats = T.feature_table(session, dataset_id)
        timings["features"] = round(time.perf_counter() - t, 2)
        idx = get_index(session, dataset_id)
        in_win = ((feats["transaction_timestamp"] >= ws_ts) & (feats["transaction_timestamp"] < we_ts)).to_numpy()
        fw = feats[in_win].reset_index(drop=True)
        # --- scoring
        t = time.perf_counter()
        failed = 0
        missing_rates = fw[ALL_FEATURES].isna().mean().sort_values(ascending=False)
        rules = T.rule_score(fw)
        if bundle is not None:
            try:
                raw = bundle.raw(fw)
                risk = bundle.risk(fw)
                anom = bundle.anomaly(fw)
            except Exception as e:  # noqa: BLE001
                failed = len(fw)
                log("scoring failed:", e)
                raw = risk = anom = np.zeros(len(fw))
        else:
            raw = risk = anom = np.zeros(len(fw))
        timings["scoring"] = round(time.perf_counter() - t, 2)
        sc = pd.DataFrame({"dataset_id": dataset_id, "transaction_id": fw["transaction_id"], "run_id": prun.run_id, "model_risk": risk, "anomaly": anom, "rule_score": rules})
        session.execute(delete(m.TxnScore).where(m.TxnScore.dataset_id == dataset_id))
        sc.to_sql("txn_scores", session.connection(), if_exists="append", index=False, chunksize=2000, method="multi")
        score_of = dict(zip(sc["transaction_id"], zip(sc["model_risk"], sc["anomaly"], sc["rule_score"])))
        # --- detectors
        dets = D.run_all(idx, ws, we, settings.structuring_threshold, timings)
        # --- model-only candidates: high raw score not already covered
        covered = {t_ for d in dets for t_ in d.txn_ids}
        thr = bundle.meta["thresholds"]["cost"] if bundle is not None else 1.0
        extra: list[D.Detection] = []
        if bundle is not None:
            hi = fw[(raw >= thr) & ~fw["transaction_id"].isin(covered)]
            for acc, g in hi.groupby("source_account_id"):
                g = g.sort_values("transaction_timestamp")
                grp, last = [], None
                for _, r in g.iterrows():
                    if last is not None and (r["transaction_timestamp"] - last) > pd.Timedelta(hours=24):
                        extra.append(_model_det(acc, grp))
                        grp = []
                    grp.append(r)
                    last = r["transaction_timestamp"]
                if grp:
                    extra.append(_model_det(acc, grp))
        # --- cluster, score, persist
        t = time.perf_counter()
        amt_of = dict(zip(idx.txid, idx.amt))
        ts_of = dict(zip(idx.txid, idx.ts))
        clusters = _cluster(dets) + [[e] for e in extra]
        alerts = []
        claimed: set[str] = set()
        for members in clusters:
            primary = max(members, key=lambda d: d.strength)
            txn_ids = sorted({t_ for d in members for t_ in d.txn_ids}, key=lambda x: ts_of.get(x, 0))
            scores = [score_of[t_] for t_ in txn_ids if t_ in score_of]
            top = sorted(scores, key=lambda s: -s[0])[:3]
            model_sig = float(np.mean([s[0] for s in top])) if (bundle is not None and top) else None
            anom_sig = float(np.mean(sorted([s[1] for s in scores], reverse=True)[:3])) if (bundle is not None and scores) else None
            behav = float(np.mean(sorted([s[2] for s in scores], reverse=True)[:3])) if scores else None
            distinct = len({d.detector for d in members if d.detector != "model_anomaly"})
            pattern = primary.strength if primary.detector != "model_anomaly" else None
            sig = {"pattern": pattern, "model": R.model_family(model_sig, anom_sig, cfg.anomaly_support_cap), "behavior": behav}
            comb = R.combine(sig, cfg, max(1, distinct))
            if comb["score"] < min_score:
                continue
            ex = R.exposure([(d.detector, {t_: amt_of[t_] for t_ in d.txn_ids if t_ in amt_of}) for d in members])
            new_txns = [t_ for t_ in txn_ids if t_ not in claimed]
            claimed |= set(txn_ids)
            unique_val = float(sum(amt_of.get(t_, 0.0) for t_ in new_txns))
            accounts = sorted({a for d in members for a in d.accounts})
            starts = min(d.start for d in members)
            ends = max(d.end for d in members)
            alerts.append({
                "primary": primary, "members": members, "txn_ids": txn_ids, "accounts": accounts, "comb": comb, "sig": sig, "exposure": ex,
                "unique_value": unique_val, "start": starts, "end": ends,
                "rules": R.rules_score(sig, cfg, max(1, distinct)), "model_only": R.model_score({"model": sig["model"]}) if sig["model"] is not None else None})
        alerts.sort(key=lambda a: -a["comb"]["score"])
        # --- explanations (SHAP) for the highest-risk transaction of each alert
        t_ex = time.perf_counter()
        expl: dict[str, dict] = {}
        if bundle is not None and with_explanations and alerts:
            top_ids = []
            for a in alerts:
                cand = [t_ for t_ in a["txn_ids"] if t_ in score_of]
                if cand:
                    top_ids.append(max(cand, key=lambda t_: score_of[t_][0]))
            rows = fw[fw["transaction_id"].isin(set(top_ids))].drop_duplicates("transaction_id").reset_index(drop=True)
            try:
                for tid, e in zip(rows["transaction_id"], bundle.explain(rows)):
                    expl[tid] = e
            except Exception as e:  # noqa: BLE001
                log("explanations unavailable:", e)
        timings["explanations"] = round(time.perf_counter() - t_ex, 2)
        # --- persist alerts (human review state is never overwritten)
        protected = {r[0] for r in session.execute(text("SELECT alert_id FROM alerts WHERE dataset_id = :d AND investigation_status != 'Not opened'"), {"d": dataset_id})}
        session.execute(text("DELETE FROM alert_transactions WHERE dataset_id = :d AND alert_id NOT IN (SELECT alert_id FROM alerts WHERE dataset_id = :d AND investigation_status != 'Not opened')"), {"d": dataset_id})
        session.execute(text("DELETE FROM alerts WHERE dataset_id = :d AND investigation_status = 'Not opened'"), {"d": dataset_id})
        seen_ids: set[str] = set()
        at_rows = []
        for a in alerts:
            p = a["primary"]
            aid = _alert_id(dataset_id, p)
            if aid in seen_ids:
                aid = aid[:-2] + hashlib.sha1(str(sorted(a["txn_ids"])[:3]).encode()).hexdigest()[:2].upper()
            seen_ids.add(aid)
            score = a["comb"]["score"]
            top_tx = max((t_ for t_ in a["txn_ids"] if t_ in score_of), key=lambda t_: score_of[t_][0], default=None)
            detail = {
                "evidence_summary": p.summary, "detection_methods": sorted({d.detector for d in a["members"]}),
                "detections": [{"method": d.detector, "strength": round(d.strength, 4), "summary": d.summary, "evidence": d.evidence, "path": d.path,
                                "start": d.start.isoformat(), "end": d.end.isoformat()} for d in a["members"]],
                "paths": [d.path for d in a["members"] if d.path], "accounts": a["accounts"][:200],
                "evidence_timestamps": {"first": a["start"].isoformat(), "last": a["end"].isoformat()},
                "risk_breakdown": a["comb"], "alternative_scores": {"rules_only": a["rules"], "model_only": a["model_only"]},
                "exposure": {"gross_value": a["exposure"].gross_value, "exposed_value": a["exposure"].exposed_value, "method": a["exposure"].method,
                             "incremental_unique_value": a["unique_value"], "definition": "Potentially affected transaction value; not a confirmed loss."},
                "explanation": expl.get(top_tx), "explained_transaction": top_tx,
                "recommended_steps": R.RECOMMENDED_STEPS.get(p.detector, R.RECOMMENDED_STEPS["model_anomaly"]),
                "limitations": ["Model-generated risk score and pattern evidence are investigative leads, not findings of wrongdoing.",
                                "Scores are not probabilities; bands are operational categories, not regulatory classifications.",
                                "Counterparty/ownership information outside the transaction data was not available.",
                                "Synthetic data: patterns reflect simulated scenarios." if ds.kind == "synthetic" else "Data quality caveats are in the import report."],
                "risk_config": {"weights": cfg.family_weights, "bands": dict(cfg.bands)},
            }
            fields = dict(
                dataset_id=dataset_id, run_id=prun.run_id, alert_timestamp=a["end"].to_pydatetime(), entity_type=p.entity_type, entity_id=p.entity_id,
                alert_type=p.detector, risk_score=score, risk_band=R.band(score, cfg), model_score=a["sig"]["model"], rule_score=a["rules"],
                n_transactions=len(a["txn_ids"]), n_accounts=len(a["accounts"]), gross_value=a["exposure"].gross_value, unique_value=a["unique_value"],
                exposed_value=a["exposure"].exposed_value, evidence_start=a["start"].to_pydatetime(), evidence_end=a["end"].to_pydatetime(),
                model_version=run.model_version if run else "none", feature_version=FEATURE_VERSION, detail=_clean(detail))
            if aid in protected:   # under human review: refresh evidence, never touch investigation_status
                ex_alert = session.get(m.Alert, aid)
                for k, v in fields.items():
                    setattr(ex_alert, k, v)
                session.execute(delete(m.AlertTransaction).where(m.AlertTransaction.alert_id == aid))
            else:
                session.add(m.Alert(alert_id=aid, **fields))
            at_rows += [{"alert_id": aid, "transaction_id": t_, "dataset_id": dataset_id} for t_ in a["txn_ids"]]
        session.flush()
        if at_rows:
            pd.DataFrame(at_rows).drop_duplicates(["alert_id", "transaction_id"]).to_sql("alert_transactions", session.connection(), if_exists="append", index=False, chunksize=3000, method="multi")
        timings["alerts"] = round(time.perf_counter() - t, 2)
        # --- monitoring
        ref_mask = (feats["transaction_timestamp"] < ws_ts).to_numpy()
        key_feats = ["amount", "s_out_cnt_24h", "d_uniq_senders_24h", "s_burst_ratio", "s_dormancy_h", "chain_len", "d_burst_ratio", "s_new_cp"]
        ref_scores = bundle.ref_scores if bundle is not None else None
        drift = drift_report(feats[ref_mask].tail(40000), fw, key_feats, ref_scores, raw if bundle is not None else None)
        hist = np.histogram(risk, bins=10, range=(0, 1))[0].tolist()
        by_band: dict[str, int] = defaultdict(int)
        by_type: dict[str, int] = defaultdict(int)
        for a in alerts:
            by_band[R.band(a["comb"]["score"], cfg)] += 1
            by_type[a["primary"].detector] += 1
        elapsed = time.perf_counter() - t_all
        prun.metrics = _clean({
            "n_scored": len(fw), "n_failed": failed, "n_alerts": len(alerts), "alerts_by_band": dict(by_band), "alerts_by_type": dict(by_type),
            "latency_seconds_total": round(elapsed, 2), "stage_seconds": timings, "scoring_latency_ms_per_txn": round(timings["scoring"] / max(1, len(fw)) * 1000, 4),
            "top_missing_features": {k: round(float(v), 4) for k, v in missing_rates.head(5).items()}, "score_histogram": hist, "drift": drift,
            "model_version": run.model_version if run else None, "feature_version": FEATURE_VERSION, "risk_config_version": cfg.version(),
            "model_used": bundle is not None})
        prun.status, prun.finished_at = "completed", pd.Timestamp.utcnow().tz_localize(None).to_pydatetime()
        session.flush()
        log(f"pipeline {prun.run_id}: scored {len(fw)} txns, {len(alerts)} alerts in {elapsed:.1f}s {timings}")
        return {"run_id": prun.run_id, "n_scored": len(fw), "n_alerts": len(alerts), "seconds": round(elapsed, 2), "timings": timings}
    except Exception as e:
        prun.status, prun.error = "failed", str(e)[:1000]
        session.flush()
        raise


def _model_det(acc: str, rows: list) -> D.Detection:
    ids = [r["transaction_id"] for r in rows]
    return D.Detection("model_anomaly", str(acc), [str(acc)] + sorted({str(r["destination_account_id"]) for r in rows})[:20], ids, rows[0]["transaction_timestamp"], rows[-1]["transaction_timestamp"], 0.0,
                       {"n_transactions": len(ids)}, f"{len(ids)} transactions from {acc} scored above the model operating threshold without a matching structural pattern")


def _clean(o):
    if isinstance(o, dict):
        return {str(k): _clean(v) for k, v in o.items()}
    if isinstance(o, (list, tuple, set)):
        return [_clean(v) for v in o]
    if isinstance(o, (np.floating, float)):
        return None if (np.isnan(o) or np.isinf(o)) else float(o)
    if isinstance(o, np.integer):
        return int(o)
    if isinstance(o, np.bool_):
        return bool(o)
    if isinstance(o, pd.Timestamp):
        return o.isoformat()
    return o
