"""Model training + evaluation. Chronological split, scenario purging, feature-group ablation.

Label provenance: supervision comes from SYNTHETIC scenario labels (`gt_txn_labels`). They describe simulated
patterns, not legally established financial crime. This is the only module (besides evaluation/reporting)
that reads ground truth; inference, graph and risk code paths never import it.
"""
from __future__ import annotations

import time
import uuid
import warnings
from datetime import datetime

import joblib
import lightgbm as lgb
import numpy as np
import pandas as pd
from sklearn.ensemble import IsolationForest, RandomForestClassifier
from sklearn.impute import SimpleImputer
from sklearn.isotonic import IsotonicRegression
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import QuantileTransformer
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db import models as m
from app.features.engine import ALL_FEATURES, FEATURE_GROUPS, FEATURE_VERSION, compute_features

from . import evaluation as ev

warnings.filterwarnings("ignore")
SPLIT_FRACTIONS = (0.60, 0.15, 0.25)
COSTS = {"cost_per_false_positive_inr": 250.0, "loss_rate_on_missed_value": 0.10, "verification_cost_inr": 0.0,
         "note": "ASSUMPTIONS, not measurements: ~25 analyst-minutes at ₹600/h per reviewed alert; 10% of missed suspicious value assumed lost."}
SEED = 42


# ------------------------------------------------------------------ data
def load_txns(session: Session, dataset_id: str) -> tuple[pd.DataFrame, dict]:
    bind = session.connection()
    df = pd.read_sql(text("SELECT transaction_id, source_account_id, destination_account_id, transaction_timestamp, amount, currency, transaction_type, channel "
                          "FROM transactions WHERE dataset_id = :d ORDER BY transaction_timestamp, transaction_id"), bind, params={"d": dataset_id}, parse_dates=["transaction_timestamp"])
    acc = pd.read_sql(text("SELECT account_id, created_at FROM accounts WHERE dataset_id = :d"), bind, params={"d": dataset_id}, parse_dates=["created_at"])
    return df, dict(zip(acc["account_id"], acc["created_at"]))


def load_labels(session: Session, dataset_id: str) -> tuple[pd.DataFrame, pd.DataFrame]:
    """EVALUATION/TRAINING ONLY. Returns (txn labels, scenarios)."""
    bind = session.connection()
    lab = pd.read_sql(text("SELECT transaction_id, scenario_id, is_suspicious_sim FROM gt_txn_labels WHERE dataset_id = :d"), bind, params={"d": dataset_id})
    sc = pd.read_sql(text("SELECT scenario_id, payload FROM gt_scenarios WHERE dataset_id = :d"), bind, params={"d": dataset_id})
    import json
    rows = []
    for _, r in sc.iterrows():
        p = r["payload"] if isinstance(r["payload"], dict) else json.loads(r["payload"])
        rows.append({"scenario_id": r["scenario_id"], "scenario_type": p["scenario_type"], "subtype": p["subtype"], "variant": p["template_variant"],
                     "is_suspicious": p["is_suspicious"], "start": pd.Timestamp(p["start_time"]), "end": pd.Timestamp(p["end_time"]), "code": p["scenario_code"]})
    return lab, pd.DataFrame(rows)


def feature_table(session: Session, dataset_id: str, use_cache: bool = True) -> pd.DataFrame:
    ds = session.get(m.Dataset, dataset_id)
    cache = get_settings().artifact_dir / "features" / f"{dataset_id}_{ds.dataset_version}_{FEATURE_VERSION}.joblib"
    if use_cache and cache.exists():
        return joblib.load(cache)
    df, created = load_txns(session, dataset_id)
    feats = compute_features(df, created, get_settings().structuring_threshold)
    feats["transaction_timestamp"] = df["transaction_timestamp"].to_numpy()
    feats["source_account_id"] = df["source_account_id"].to_numpy()
    feats["destination_account_id"] = df["destination_account_id"].to_numpy()
    cache.parent.mkdir(parents=True, exist_ok=True)
    joblib.dump(feats, cache)
    return feats


def chrono_split(ts: pd.Series, fractions=SPLIT_FRACTIONS) -> tuple[pd.Timestamp, pd.Timestamp, np.ndarray, np.ndarray, np.ndarray]:
    lo, hi = ts.min(), ts.max()
    span = hi - lo
    t_train = lo + span * fractions[0]
    t_val = lo + span * (fractions[0] + fractions[1])
    return t_train, t_val, (ts < t_train).to_numpy(), ((ts >= t_train) & (ts < t_val)).to_numpy(), (ts >= t_val).to_numpy()


# ------------------------------------------------------------------ models
RULE_WEIGHTS = {"large_new_cp": 0.2, "near_threshold_repeat": 0.3, "velocity_24h": 0.25, "fan_in_24h": 0.25, "chain2": 0.3, "cycle": 0.35, "dormant_wakeup": 0.25, "amount_outlier": 0.15}


def rule_score(X: pd.DataFrame, weights: dict | None = None) -> np.ndarray:
    """Transparent analyst-style rules (also the interpretable `behavior` family in the risk engine). Thresholds are conventional
    round numbers chosen before looking at test results; they were not tuned."""
    w = weights or RULE_WEIGHTS
    s = np.zeros(len(X))
    s += w["large_new_cp"] * ((X["amount"] >= 100000) & (X["s_new_cp"] == 1))
    s += w["near_threshold_repeat"] * ((X["near_threshold"] == 1) & (X["s_near_thr_cnt_7d"] >= 2))
    s += w["velocity_24h"] * (X["s_out_cnt_24h"] >= 10)
    s += w["fan_in_24h"] * ((X["d_uniq_senders_24h"] >= 5) & (X["d_burst_ratio"] >= 3))
    s += w["chain2"] * (X["chain_len"] >= 2)
    s += w["cycle"] * ((X["cycle_close2"] == 1) | (X["cycle_close3"] == 1))
    s += w["dormant_wakeup"] * ((X["s_dormancy_h"] > 24 * 30) & (X["s_out_cnt_24h"] >= 3))
    s += w["amount_outlier"] * (X["s_amt_z"].fillna(0) > 4)
    return np.clip(s, 0, 1)


def make_model(kind: str, pos_weight: float = 1.0, seed: int = SEED):
    if kind == "logreg":
        return Pipeline([("imp", SimpleImputer(strategy="median")), ("qt", QuantileTransformer(n_quantiles=200, output_distribution="normal", random_state=seed)),
                         ("clf", LogisticRegression(C=0.5, class_weight="balanced", max_iter=400))])
    if kind == "random_forest":
        return Pipeline([("imp", SimpleImputer(strategy="median")),
                         ("clf", RandomForestClassifier(n_estimators=150, min_samples_leaf=3, max_depth=16, class_weight="balanced_subsample", n_jobs=-1, random_state=seed))])
    if kind == "lightgbm":
        return lgb.LGBMClassifier(n_estimators=300, learning_rate=0.05, num_leaves=31, subsample=0.8, subsample_freq=1, colsample_bytree=0.8,
                                  min_child_samples=20, reg_lambda=1.0, random_state=seed, verbose=-1, n_jobs=4)
    raise ValueError(kind)


def _score(model, X: pd.DataFrame) -> np.ndarray:
    return model.predict_proba(X)[:, 1]


class ModelBundle:
    """Serialisable scorer: model + feature list + calibrator + reference distribution."""

    def __init__(self, model, features: list[str], kind: str, group: str, calibrator: IsotonicRegression | None, ref_scores: np.ndarray,
                 iforest: IsolationForest | None, iforest_ref: np.ndarray | None, meta: dict):
        self.model, self.features, self.kind, self.group = model, features, kind, group
        self.calibrator, self.ref_scores, self.iforest, self.iforest_ref, self.meta = calibrator, np.sort(ref_scores), iforest, iforest_ref, meta

    def _frame(self, X: pd.DataFrame) -> pd.DataFrame:
        missing = [f for f in self.features if f not in X.columns]
        if missing:
            raise ValueError(f"missing features: {missing}")
        return X[self.features].astype(float).replace([np.inf, -np.inf], np.nan)

    def raw(self, X: pd.DataFrame) -> np.ndarray:
        return _score(self.model, self._frame(X))

    def risk(self, X: pd.DataFrame) -> np.ndarray:
        """Monotone-calibrated model risk score in [0,1] (isotonic fit on the validation period). NOT a real-world probability."""
        r = self.raw(X)
        return self.calibrator.predict(r) if self.calibrator is not None else r

    def percentile(self, raw: np.ndarray) -> np.ndarray:
        return np.searchsorted(self.ref_scores, raw, side="right") / len(self.ref_scores)

    def anomaly(self, X: pd.DataFrame) -> np.ndarray:
        """Unsupervised anomaly signal: percentile of -IsolationForest score vs the training period."""
        if self.iforest is None:
            return np.zeros(len(X))
        Z = self._frame(X).fillna(0.0)
        s = -self.iforest.score_samples(Z)
        return np.searchsorted(self.iforest_ref, s, side="right") / len(self.iforest_ref)

    def explain(self, X: pd.DataFrame, top: int = 6) -> list[dict]:
        import shap
        F = self._frame(X)
        est = self.model
        if isinstance(est, Pipeline):
            Ft = pd.DataFrame(est[:-1].transform(F), columns=self.features, index=F.index)
            est = est[-1]
        else:
            Ft = F
        out = []
        if self.kind == "logreg":
            coefs = est.coef_[0]
            vals = Ft.to_numpy() * coefs
        else:
            ex = shap.TreeExplainer(est)
            sv = ex.shap_values(Ft)
            vals = sv[1] if isinstance(sv, list) else (sv[..., 1] if getattr(sv, "ndim", 2) == 3 else sv)
        for i in range(len(F)):
            contrib = sorted(zip(self.features, vals[i], F.iloc[i].to_numpy()), key=lambda x: -abs(x[1]))[:top]
            out.append({"method": "linear contribution" if self.kind == "logreg" else "SHAP TreeExplainer (log-odds)",
                        "factors": [{"feature": f, "contribution": float(c), "value": None if pd.isna(v) else float(v), "direction": "increases" if c > 0 else "decreases"} for f, c, v in contrib]})
        return out


def _fit(kind: str, X_tr, y_tr, X_va, y_va):
    pos_w = float((len(y_tr) - y_tr.sum()) / max(1, y_tr.sum()))
    mdl = make_model(kind, pos_w)
    if kind == "lightgbm":
        mdl.set_params(scale_pos_weight=min(pos_w, 20.0))
        mdl.fit(X_tr, y_tr, eval_set=[(X_va, y_va)], eval_metric="average_precision", callbacks=[lgb.early_stopping(40, verbose=False)])
    else:
        mdl.fit(X_tr, y_tr)
    return mdl


def _clean(X: pd.DataFrame) -> pd.DataFrame:
    return X.astype(float).replace([np.inf, -np.inf], np.nan)


def train_and_evaluate(session: Session, dataset_id: str, kinds=("logreg", "random_forest", "lightgbm"), holdout_variants=(2, 3), log=print) -> dict:
    t_start = time.perf_counter()
    ds = session.get(m.Dataset, dataset_id)
    if not ds.has_ground_truth:
        raise ValueError("dataset has no labels; supervised training requires labels")
    feats = feature_table(session, dataset_id)
    lab, sc = load_labels(session, dataset_id)
    d = feats.merge(lab, on="transaction_id", how="left").merge(sc[["scenario_id", "variant", "end", "is_suspicious"]].rename(columns={"is_suspicious": "sc_susp"}), on="scenario_id", how="left")
    d["y"] = d["is_suspicious_sim"].fillna(False).astype(int)
    t_tr, t_va, m_tr, m_va, m_te = chrono_split(d["transaction_timestamp"])
    # purge: training rows of scenarios that extend past the training cut-off are dropped (no scenario straddles a boundary)
    straddle = d["scenario_id"].notna() & (d["scenario_id"] != "") & (d["end"] > t_tr)
    m_tr_p = m_tr & ~straddle.to_numpy()
    straddle_va = d["scenario_id"].notna() & (d["scenario_id"] != "") & (d["end"] > t_va)
    m_va_p = m_va & ~straddle_va.to_numpy()
    log(f"split: train<{t_tr:%Y-%m-%d} ({m_tr_p.sum()} rows, {m_tr.sum() - m_tr_p.sum()} purged)  val<{t_va:%Y-%m-%d} ({m_va_p.sum()})  test ({m_te.sum()})")
    y = d["y"].to_numpy()
    amount = d["amount"].to_numpy()
    results: dict = {"models": {}, "holdout_templates": {}, "rules_baseline": {}, "unsupervised": {}}
    bundles: dict = {}

    def evaluate(name, score_tr, score_va, score_te, thr):
        return {"train": ev.summarize(y[m_tr_p], score_tr, thr, amount[m_tr_p]), "validation": ev.summarize(y[m_va_p], score_va, thr, amount[m_va_p]),
                "test": ev.summarize(y[m_te], score_te, thr, amount[m_te])}

    # rules baseline
    r_all = rule_score(d)
    thr_r = 0.2
    results["rules_baseline"] = evaluate("rules", r_all[m_tr_p], r_all[m_va_p], r_all[m_te], thr_r)
    results["rules_baseline"]["description"] = "Transparent weighted rules (see RULE_WEIGHTS). Threshold 0.2 = at least one rule fired."
    # unsupervised (trained WITHOUT labels on the training period)
    Xall = _clean(d[ALL_FEATURES]).fillna(0.0)
    iso = IsolationForest(n_estimators=200, contamination="auto", random_state=SEED, n_jobs=-1).fit(Xall[m_tr_p])
    s_iso = -iso.score_samples(Xall)
    iso_ref = np.sort(s_iso[m_tr_p])
    pct = np.searchsorted(iso_ref, s_iso, side="right") / len(iso_ref)
    results["unsupervised"] = {**evaluate("iforest", pct[m_tr_p], pct[m_va_p], pct[m_te], 0.99), "description": "IsolationForest (all features, no labels), percentile vs training period"}

    for gname, feats_g in FEATURE_GROUPS.items():
        Xg = _clean(d[feats_g])
        for kind in kinds:
            t0 = time.perf_counter()
            mdl = _fit(kind, Xg[m_tr_p], y[m_tr_p], Xg[m_va_p], y[m_va_p])
            s_tr, s_va, s_te = _score(mdl, Xg[m_tr_p]), _score(mdl, Xg[m_va_p]), _score(mdl, Xg[m_te])
            thr = ev.best_f1_threshold(y[m_va_p], s_va)
            res = evaluate(f"{gname}/{kind}", s_tr, s_va, s_te, thr)
            lo, hi = ev.bootstrap_ci(y[m_te], s_te, lambda a, b: ev.average_precision_score(a, b), n=100)
            res["test"]["pr_auc_ci95"] = [lo, hi]
            res["fit_seconds"] = round(time.perf_counter() - t0, 2)
            key = f"{gname}/{kind}"
            results["models"][key] = res
            bundles[key] = (mdl, feats_g, s_va)
            log(f"  {key:28s} val PR-AUC {res['validation']['pr_auc']:.3f}  test PR-AUC {res['test']['pr_auc']:.3f}  [{lo:.3f},{hi:.3f}]  ({res['fit_seconds']}s)")

    # template hold-out: train on variants not in `holdout_variants`, test on held-out variants (stronger generalisation check)
    var = d["variant"]
    held = var.isin(holdout_variants)
    in_sc = d["scenario_id"].notna() & (d["scenario_id"] != "")
    m_tr_h = m_tr_p & ~(in_sc & held).to_numpy()
    m_va_h = m_va_p & ~(in_sc & held).to_numpy()
    m_te_h = m_te & ~(in_sc & ~held).to_numpy()
    for gname, feats_g in FEATURE_GROUPS.items():
        Xg = _clean(d[feats_g])
        mdl = _fit("lightgbm", Xg[m_tr_h], y[m_tr_h], Xg[m_va_h], y[m_va_h])
        s_te = _score(mdl, Xg[m_te_h])
        results["holdout_templates"][f"{gname}/lightgbm"] = ev.summarize(y[m_te_h], s_te)
    results["holdout_templates"]["rules_baseline"] = ev.summarize(y[m_te_h], r_all[m_te_h])
    results["holdout_description"] = f"Trained without scenario template variants {list(holdout_variants)}; tested only on those variants (+ all background rows) in the test period."

    # model selection on VALIDATION PR-AUC only
    best = max(results["models"], key=lambda k: np.nan_to_num(results["models"][k]["validation"]["pr_auc"]))
    mdl, feats_g, s_va = bundles[best]
    gname, kind = best.split("/")
    Xg = _clean(d[feats_g])
    s_va_full = _score(mdl, Xg[m_va_p])
    cal = IsotonicRegression(out_of_bounds="clip", y_min=0, y_max=1).fit(s_va_full, y[m_va_p])
    s_te_raw = _score(mdl, Xg[m_te])
    s_te_cal = cal.predict(s_te_raw)
    calib_raw = ev.calibration(y[m_te], s_te_raw)
    calib_cal = ev.calibration(y[m_te], s_te_cal)
    thr_f1 = ev.best_f1_threshold(y[m_va_p], s_va_full)
    cc = ev.cost_curve(y[m_va_p], s_va_full, amount[m_va_p], COSTS["cost_per_false_positive_inr"], COSTS["loss_rate_on_missed_value"], COSTS["verification_cost_inr"])
    thr_cost = min(cc, key=lambda r: r["expected_cost"])["threshold"]
    cost_test = ev.cost_curve(y[m_te], s_te_raw, amount[m_te], COSTS["cost_per_false_positive_inr"], COSTS["loss_rate_on_missed_value"], COSTS["verification_cost_inr"])
    thr_table = {"f1_optimal_validation": ev.threshold_metrics(y[m_te], s_te_raw, thr_f1), "cost_optimal_validation": ev.threshold_metrics(y[m_te], s_te_raw, thr_cost),
                 "top_1pct_capacity": ev.threshold_metrics(y[m_te], s_te_raw, float(np.quantile(s_te_raw, 0.99)))}
    # performance over time (selected model, test period, weekly)
    te = d[m_te].assign(score=s_te_raw)
    te["week"] = te["transaction_timestamp"].dt.to_period("W").dt.start_time
    over_time = []
    for wk, g in te.groupby("week"):
        if g["y"].sum() >= 3:
            over_time.append({"week": wk.isoformat(), "n": len(g), "n_pos": int(g["y"].sum()), "pr_auc": float(ev.average_precision_score(g["y"], g["score"]))})
    # permutation-free importance (gain) for tree models
    fi = {}
    inner = mdl[-1] if isinstance(mdl, Pipeline) else mdl
    if hasattr(inner, "feature_importances_"):
        imp = np.asarray(inner.feature_importances_, float)
        fi = {f: float(v) for f, v in sorted(zip(feats_g, imp / (imp.sum() or 1)), key=lambda x: -x[1])[:25]}

    run_id = "MR-" + uuid.uuid4().hex[:8]
    version = f"{gname.split('_')[0]}-{kind}-{datetime.utcnow():%Y%m%d%H%M%S}"
    mdir = get_settings().artifact_dir / "models"
    mdir.mkdir(parents=True, exist_ok=True)
    meta = {"dataset_id": dataset_id, "dataset_version": ds.dataset_version, "feature_version": FEATURE_VERSION, "model_version": version,
            "selected": best, "thresholds": {"f1": thr_f1, "cost": float(thr_cost)}, "costs": COSTS}
    bundle = ModelBundle(mdl, feats_g, kind, gname, cal, s_va_full, iso, iso_ref, meta)
    art = mdir / f"{run_id}.joblib"
    joblib.dump(bundle, art)

    report = {
        "split": {"train_end": t_tr.isoformat(), "validation_end": t_va.isoformat(), "test_start": t_va.isoformat(), "test_end": d["transaction_timestamp"].max().isoformat(),
                  "train_start": d["transaction_timestamp"].min().isoformat(), "fractions": SPLIT_FRACTIONS,
                  "rows": {"train": int(m_tr_p.sum()), "train_purged": int(m_tr.sum() - m_tr_p.sum()), "validation": int(m_va_p.sum()), "test": int(m_te.sum())},
                  "positives": {"train": int(y[m_tr_p].sum()), "validation": int(y[m_va_p].sum()), "test": int(y[m_te].sum())},
                  "method": "Chronological by timestamp. Training rows of scenarios extending past the training cut-off are purged. Features are strictly as-of each transaction."},
        "selected_model": best, "selection_rule": "highest validation PR-AUC across all feature-group × algorithm combinations",
        "thresholds": thr_table, "cost_assumptions": COSTS, "cost_curve_test": cost_test[::3],
        "calibration": {"raw": calib_raw, "isotonic_on_validation": calib_cal,
                        "conclusion": "Scores are model risk scores. Even after isotonic calibration they are conditioned on the synthetic prevalence and must not be read as real-world fraud probabilities."},
        "feature_importance": fi, "performance_over_time": over_time,
        "results": results, "feature_groups": {k: len(v) for k, v in FEATURE_GROUPS.items()},
        "limitations": ["Labels are synthetic scenario labels; metrics measure recovery of simulated patterns, not real-world fraud detection.",
                        "Synthetic prevalence (~2-3% of transactions) is far higher than real-world prevalence; precision is not transferable.",
                        "Scenario templates are parametric; hold-out by template variant reduces but does not remove similarity between train and test patterns.",
                        "Point-in-time graph features are local (bounded neighbourhood); global metrics such as PageRank/communities are not used in the supervised model to avoid look-ahead."],
        "training_seconds": round(time.perf_counter() - t_start, 1),
    }
    for r in session.query(m.ModelRun).filter(m.ModelRun.is_active.is_(True)).all():
        r.is_active = False
    run = m.ModelRun(model_run_id=run_id, model_name=f"{gname}/{kind}", model_version=version, dataset_id=dataset_id, dataset_version=ds.dataset_version,
                     feature_version=FEATURE_VERSION, evaluation_metrics=_jsonable(report), configuration=_jsonable(meta), artifact_reference=str(art), is_active=True)
    session.add(run)
    session.flush()
    log(f"selected {best}; saved {art.name}; total {report['training_seconds']}s")
    return {"model_run_id": run_id, **report}


def _jsonable(o):
    if isinstance(o, dict):
        return {str(k): _jsonable(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_jsonable(v) for v in o]
    if isinstance(o, (np.floating, float)):
        return None if np.isnan(o) or np.isinf(o) else float(o)
    if isinstance(o, np.integer):
        return int(o)
    if isinstance(o, (np.bool_,)):
        return bool(o)
    return o


def load_bundle(session: Session, model_run_id: str | None = None) -> tuple[ModelBundle, m.ModelRun] | tuple[None, None]:
    q = session.query(m.ModelRun)
    run = session.get(m.ModelRun, model_run_id) if model_run_id else q.filter(m.ModelRun.is_active.is_(True)).order_by(m.ModelRun.training_timestamp.desc()).first()
    if run is None:
        return None, None
    return joblib.load(run.artifact_reference), run
