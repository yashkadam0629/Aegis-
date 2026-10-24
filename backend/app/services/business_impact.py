"""Business-impact evaluation on SYNTHETIC ground truth: compares triage approaches on a common unit — the investigation *case*.

Case definition (identical for every approach):
  * txn-level approaches: the top-scoring transactions are grouped by source account with a 24h gap -> cases ranked by max score.
  * alert-level approach (AEGIS combined risk prioritisation): each alert is a case, ranked by risk score.
A case is a TRUE POSITIVE if >=50% of its transactions belong to a suspicious simulated scenario.
A suspicious scenario is DETECTED at capacity N if one of the top-N TP cases contains at least one of its transactions.
All costs are assumptions (see ASSUMPTIONS) – nothing here is a measured saving.
"""
from __future__ import annotations

import time

import numpy as np
import pandas as pd
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db import models as m
from app.features.engine import FEATURE_GROUPS, compute_features
from app.ingestion.pipeline import validate_and_normalize
from app.ml import evaluation as ev
from app.ml import training as T
from app.synthetic.generator import GeneratorConfig, generate

ASSUMPTIONS = {"minutes_per_case": 25, "analyst_cost_per_hour_inr": 600, "cost_per_case_inr": 250.0,
               "note": "Assumed review effort and loaded analyst cost; no saving or loss prevented is claimed."}
CAPACITIES = (25, 50, 100, 200)


def txn_cases(df: pd.DataFrame, score: np.ndarray, top_frac: float = 0.03, gap_h: float = 24.0) -> list[dict]:
    """df has transaction_id, source_account_id, transaction_timestamp; returns ranked cases from top-scoring txns."""
    k = max(1, int(len(df) * top_frac))
    order = np.argsort(-score, kind="stable")[:k]
    sel = df.iloc[order].assign(_s=score[order]).sort_values(["source_account_id", "transaction_timestamp"])
    cases = []
    for acc, g in sel.groupby("source_account_id"):
        cur, last = [], None
        for _, r in g.iterrows():
            if last is not None and (r["transaction_timestamp"] - last) > pd.Timedelta(hours=gap_h):
                cases.append(cur)
                cur = []
            cur.append(r)
            last = r["transaction_timestamp"]
        if cur:
            cases.append(cur)
    out = [{"txns": [r["transaction_id"] for r in c], "score": max(r["_s"] for r in c)} for c in cases]
    out.sort(key=lambda c: -c["score"])
    return out


def score_cases(cases: list[dict], suspicious: set[str], txn_scenario: dict[str, str], universe: set[str], amount: dict[str, float], caps=CAPACITIES) -> dict:
    res = {}
    for n in caps:
        top = cases[:n]
        tp = 0
        detected: set[str] = set()
        det_val = 0.0
        n_txn = 0
        for c in top:
            ids = c["txns"]
            n_txn += len(ids)
            frac = sum(t in suspicious for t in ids) / len(ids)
            if frac >= 0.5:
                tp += 1
                detected |= {txn_scenario[t] for t in ids if t in suspicious}
                det_val += sum(amount.get(t, 0.0) for t in ids if t in suspicious)
        det = detected & universe
        res[str(n)] = {"cases": len(top), "true_positive_cases": tp, "precision": tp / len(top) if top else 0.0,
                       "scenarios_detected": len(det), "scenario_recall": len(det) / len(universe) if universe else float("nan"),
                       "false_positive_cases": len(top) - tp, "transactions_to_inspect": n_txn,
                       "suspicious_value_in_detected_cases": round(det_val, 2),
                       "assumed_review_cost_inr": round(len(top) * ASSUMPTIONS["cost_per_case_inr"], 2),
                       "assumed_cost_per_detected_scenario_inr": round(len(top) * ASSUMPTIONS["cost_per_case_inr"] / max(1, len(det)), 2)}
    return res


def run_business_impact(session: Session, dataset_id: str, log=print, robustness: bool = True) -> dict:
    t0 = time.perf_counter()
    bundle, run = T.load_bundle(session)
    if bundle is None:
        raise ValueError("train a model first")
    feats = T.feature_table(session, dataset_id)
    lab, sc = T.load_labels(session, dataset_id)
    d = feats.merge(lab, on="transaction_id", how="left")
    d["y"] = d["is_suspicious_sim"].fillna(False).astype(int)
    t_tr, t_va, m_tr, m_va, m_te = T.chrono_split(d["transaction_timestamp"])
    d2 = d.merge(sc[["scenario_id", "end"]], on="scenario_id", how="left")
    strad = (d2["scenario_id"].notna() & (d2["scenario_id"] != "") & (d2["end"] > t_tr)).to_numpy()
    m_trp = m_tr & ~strad
    strad_v = (d2["scenario_id"].notna() & (d2["scenario_id"] != "") & (d2["end"] > t_va)).to_numpy()
    m_vap = m_va & ~strad_v
    te = d[m_te].reset_index(drop=True)
    suspicious = set(te.loc[te["y"] == 1, "transaction_id"])
    txn_scn = dict(zip(te["transaction_id"], te["scenario_id"]))
    amount = dict(zip(te["transaction_id"], te["amount"]))
    susp_sc = sc[sc["is_suspicious"]]
    # universe: suspicious scenarios with a majority of transactions inside the test window
    counts_test = te[te["y"] == 1].groupby("scenario_id").size()
    totals = d[d["y"] == 1].groupby("scenario_id").size()
    universe = {s for s in counts_test.index if counts_test[s] / totals[s] >= 0.5 and s in set(susp_sc["scenario_id"])}
    approaches: dict = {}
    y = d["y"].to_numpy()

    approaches["1_simple_rules"] = {"cases": txn_cases(te, T.rule_score(te)), "desc": "Transparent threshold rules on each transaction"}
    for key, g in (("2_transaction_only_ml", "A_transaction"), ("3_behavioral_ml", "B_behavioral"), ("4_graph_enhanced_ml", "C_graph"), ("4b_combined_features_ml", "D_combined")):
        cols = FEATURE_GROUPS[g]
        X = d[cols].astype(float).replace([np.inf, -np.inf], np.nan)
        mdl = T._fit("lightgbm", X[m_trp], y[m_trp], X[m_vap], y[m_vap])
        s = T._score(mdl, X[m_te])
        approaches[key] = {"cases": txn_cases(te, s), "desc": f"LightGBM on feature group {g}", "txn_pr_auc": float(ev.average_precision_score(y[m_te], s))}
    # AEGIS combined prioritisation = stored alerts
    arows = session.execute(text("SELECT alert_id, risk_score, model_score, rule_score, detail FROM alerts WHERE dataset_id = :d"), {"d": dataset_id}).fetchall()
    at = pd.read_sql(text("SELECT alert_id, transaction_id FROM alert_transactions WHERE dataset_id = :d"), session.connection(), params={"d": dataset_id})
    members = at.groupby("alert_id")["transaction_id"].apply(list).to_dict()
    import json

    def pat(detail):
        det = detail if isinstance(detail, dict) else json.loads(detail)
        return max([x["strength"] for x in det["detections"]] or [0.0])

    alert_cases = [{"txns": [t for t in members.get(r[0], [])], "score": r[1], "model": r[2] or 0.0, "rules": r[3] or 0.0, "pattern": pat(r[4])} for r in arows]
    alert_cases = [c for c in alert_cases if c["txns"]]
    for k, label in (("score", "5_aegis_combined_risk"), ("pattern", "5b_pattern_only"), ("model", "5c_model_signal_only"), ("rules", "5d_rules_pattern_plus_behavior")):
        approaches[label] = {"cases": sorted(alert_cases, key=lambda c: -c[k]), "desc": {"score": "Noisy-OR over pattern, model and behaviour families", "pattern": "Detector strength only",
                                                                                          "model": "Model/anomaly family only", "rules": "Pattern + rule behaviour, no ML"}[k]}
    out_rows = {}
    for name, a in approaches.items():
        out_rows[name] = {"description": a["desc"], "n_cases_available": len(a["cases"]), "by_capacity": score_cases(a["cases"], suspicious, txn_scn, universe, amount)}
        if "txn_pr_auc" in a:
            out_rows[name]["txn_pr_auc"] = a["txn_pr_auc"]
        log(f"{name:34s} " + "  ".join(f"@{n}: P={v['precision']:.2f} R={v['scenario_recall']:.2f}" for n, v in out_rows[name]["by_capacity"].items()))
    # sensitivity: assumed cost per case x0.5 / x1 / x2 on the combined approach at capacity 100
    base = out_rows["5_aegis_combined_risk"]["by_capacity"]["100"]
    sens = []
    for mult in (0.5, 1.0, 2.0):
        c = ASSUMPTIONS["cost_per_case_inr"] * mult * base["cases"]
        sens.append({"cost_per_case_inr": ASSUMPTIONS["cost_per_case_inr"] * mult, "review_cost_inr": c, "cost_per_detected_scenario_inr": c / max(1, base["scenarios_detected"])})
    report = {
        "dataset_id": dataset_id, "model_version": run.model_version, "kind": "SYNTHETIC BENCHMARK – not real-world results",
        "evaluation_window": {"start": t_va.isoformat(), "end": d["transaction_timestamp"].max().isoformat(), "n_transactions": len(te),
                               "n_suspicious_transactions": int(te["y"].sum()), "n_suspicious_scenarios_in_window": len(universe),
                               "n_benign_lookalike_scenarios": int((~sc["is_suspicious"]).sum())},
        "case_definition": __doc__, "assumptions": ASSUMPTIONS, "approaches": out_rows, "sensitivity_cost_per_case": sens,
        "sensitivity_capacity": {k: {n: v["by_capacity"][n]["scenario_recall"] for n in v["by_capacity"]} for k, v in out_rows.items()},
        "limitations": ["Synthetic scenarios; templates are parametric so absolute numbers overstate real-world performance.",
                        "Txn-level approaches are converted to cases by a simple grouping heuristic; a production queue would differ.",
                        "Cost figures are assumed review costs, not measured savings; no fraud loss prevented is claimed.",
                        f"Evaluation window contains {len(universe)} suspicious scenarios; per-scenario recall has wide sampling uncertainty (each scenario ≈ {100 / max(1, len(universe)):.1f} pp)."],
        "seconds": round(time.perf_counter() - t0, 1)}
    if robustness:
        report["robustness"] = evasion_stress_test(bundle, session, dataset_id, log)
    return report


def evasion_stress_test(bundle, session: Session, dataset_id: str, log=print, levels=(0.0, 0.5, 1.0)) -> dict:
    """Regenerate the same scenarios with increasing evasion (slower hops, value leakage, spread-out timing) and score with the
    DEPLOYED model. Shows how much detection power depends on the patterns being fast and value-preserving."""
    ds = session.get(m.Dataset, dataset_id)
    gc = ds.meta.get("generator_config") if ds.meta else None
    rows = []
    for lvl in levels:
        base = GeneratorConfig(**{k: v for k, v in (gc or {}).items() if k in GeneratorConfig.__dataclass_fields__}) if gc else GeneratorConfig()
        base.evasion_level = lvl
        base.seed = base.seed + 1000
        syn = generate(base)
        clean, _, _ = validate_and_normalize(syn.transactions, None, syn.accounts)
        created = {a: pd.Timestamp(c) for a, c in zip(syn.accounts["account_id"], syn.accounts["created_at"])}
        f = compute_features(clean, created)
        lab = syn.txn_labels.drop_duplicates("transaction_id").set_index("transaction_id")["is_suspicious_sim"]
        f["y"] = f["transaction_id"].map(lab).fillna(False).astype(int)
        te = f[f["transaction_id"].notna()].iloc[int(len(f) * 0.75):]
        s = bundle.raw(te)
        rules = T.rule_score(te)
        rows.append({"evasion_level": lvl, "n": len(te), "n_pos": int(te["y"].sum()),
                     "model_pr_auc": float(ev.average_precision_score(te["y"], s)), "rules_pr_auc": float(ev.average_precision_score(te["y"], rules)),
                     "model_recall_at_1pct": ev.capacity_metrics(te["y"].to_numpy(), s)["0.010"]["recall"],
                     "rules_recall_at_1pct": ev.capacity_metrics(te["y"].to_numpy(), rules)["0.010"]["recall"]})
        log("evasion", lvl, {k: round(v, 3) for k, v in rows[-1].items() if isinstance(v, float)})
    return {"description": "Deployed model scored on regenerated data (different seed) with increasing evasion; labels are synthetic.", "rows": rows}
