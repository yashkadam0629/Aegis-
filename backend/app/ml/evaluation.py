"""Evaluation helpers for imbalanced, capacity-constrained alert ranking."""
from __future__ import annotations

import numpy as np
from sklearn.metrics import average_precision_score, brier_score_loss, roc_auc_score


def capacity_metrics(y: np.ndarray, score: np.ndarray, fractions=(0.005, 0.01, 0.02, 0.05)) -> dict:
    """Precision/recall when only the top-k fraction of transactions can be reviewed."""
    n, pos = len(y), int(y.sum())
    order = np.argsort(-score, kind="stable")
    out = {}
    for f in fractions:
        k = max(1, int(round(n * f)))
        tp = int(y[order[:k]].sum())
        out[f"{f:.3f}"] = {"k": k, "precision": tp / k, "recall": tp / pos if pos else float("nan"), "tp": tp, "fp": k - tp}
    return out


def threshold_metrics(y: np.ndarray, score: np.ndarray, thr: float) -> dict:
    pred = score >= thr
    tp = int((pred & (y == 1)).sum())
    fp = int((pred & (y == 0)).sum())
    fn = int((~pred & (y == 1)).sum())
    tn = int((~pred & (y == 0)).sum())
    prec = tp / (tp + fp) if tp + fp else 0.0
    rec = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * prec * rec / (prec + rec) if prec + rec else 0.0
    return {"threshold": float(thr), "precision": prec, "recall": rec, "f1": f1, "fpr": fp / (fp + tn) if fp + tn else 0.0,
            "fnr": fn / (fn + tp) if fn + tp else 0.0, "tp": tp, "fp": fp, "fn": fn, "tn": tn, "n_alerts": tp + fp}


def best_f1_threshold(y: np.ndarray, score: np.ndarray) -> float:
    cands = np.unique(np.quantile(score, np.linspace(0.5, 0.999, 200)))
    best, bt = -1.0, float(cands[-1])
    for t in cands:
        f = threshold_metrics(y, score, t)["f1"]
        if f > best:
            best, bt = f, float(t)
    return bt


def cost_curve(y: np.ndarray, score: np.ndarray, amount: np.ndarray, cost_fp: float, loss_rate: float, verify_cost: float = 0.0, n_points: int = 60) -> list[dict]:
    """Expected cost = cost_fp * FP + verify_cost * alerts + loss_rate * (value of missed suspicious transactions).
    ALL parameters are assumptions supplied by the caller; none are measured."""
    out = []
    for t in np.unique(np.quantile(score, np.linspace(0.5, 0.9995, n_points))):
        pred = score >= t
        fp = int((pred & (y == 0)).sum())
        missed_val = float(amount[(~pred) & (y == 1)].sum())
        n_alerts = int(pred.sum())
        out.append({"threshold": float(t), "n_alerts": n_alerts, "fp": fp, "missed_value": missed_val,
                    "expected_cost": cost_fp * fp + verify_cost * n_alerts + loss_rate * missed_val})
    return out


def calibration(y: np.ndarray, p: np.ndarray, bins: int = 10) -> dict:
    edges = np.quantile(p, np.linspace(0, 1, bins + 1))
    edges = np.unique(edges)
    idx = np.clip(np.searchsorted(edges, p, side="right") - 1, 0, len(edges) - 2)
    rows, ece = [], 0.0
    for b in range(len(edges) - 1):
        m = idx == b
        if m.sum() == 0:
            continue
        mp, my = float(p[m].mean()), float(y[m].mean())
        rows.append({"bin": b, "n": int(m.sum()), "mean_predicted": mp, "observed_rate": my})
        ece += m.sum() / len(p) * abs(mp - my)
    return {"brier": float(brier_score_loss(y, np.clip(p, 0, 1))), "ece": float(ece), "bins": rows,
            "base_rate": float(y.mean())}


def summarize(y: np.ndarray, score: np.ndarray, thr: float | None = None, amount: np.ndarray | None = None) -> dict:
    m = {"n": len(y), "n_pos": int(y.sum()), "prevalence": float(y.mean())}
    if y.sum() == 0 or y.sum() == len(y):
        return {**m, "pr_auc": float("nan"), "roc_auc": float("nan")}
    m["pr_auc"] = float(average_precision_score(y, score))
    m["roc_auc"] = float(roc_auc_score(y, score))
    m["capacity"] = capacity_metrics(y, score)
    if thr is not None:
        m["at_threshold"] = threshold_metrics(y, score, thr)
        if amount is not None:
            pred = score >= thr
            m["detected_value"] = float(amount[pred & (y == 1)].sum())
            m["suspicious_value_total"] = float(amount[y == 1].sum())
    return m


def bootstrap_ci(y: np.ndarray, score: np.ndarray, fn, n: int = 200, seed: int = 0) -> tuple[float, float]:
    rng = np.random.default_rng(seed)
    vals = []
    for _ in range(n):
        i = rng.integers(0, len(y), len(y))
        if y[i].sum() == 0:
            continue
        vals.append(fn(y[i], score[i]))
    return (float(np.percentile(vals, 2.5)), float(np.percentile(vals, 97.5))) if vals else (float("nan"), float("nan"))
