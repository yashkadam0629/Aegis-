"""Practical drift signals.

Population Stability Index (PSI) with reference-quantile bins. Rule of thumb (a convention, not a test):
<0.10 stable, 0.10-0.25 moderate shift, >0.25 large shift.

Limitations: drift is a DATA signal. It does not show that model accuracy has fallen (that needs labels), and a
shift can be benign (seasonality, growth). Drift never promotes or demotes a model automatically.
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def psi(ref: np.ndarray, cur: np.ndarray, bins: int = 10) -> float:
    ref = np.asarray(ref, float)
    cur = np.asarray(cur, float)
    ref, cur = ref[~np.isnan(ref)], cur[~np.isnan(cur)]
    if len(ref) < 20 or len(cur) < 20:
        return float("nan")
    edges = np.unique(np.quantile(ref, np.linspace(0, 1, bins + 1)))
    if len(edges) < 3:
        return 0.0
    edges[0], edges[-1] = -np.inf, np.inf
    r = np.histogram(ref, edges)[0] / len(ref)
    c = np.histogram(cur, edges)[0] / len(cur)
    r, c = np.clip(r, 1e-4, None), np.clip(c, 1e-4, None)
    return float(np.sum((c - r) * np.log(c / r)))


def level(v: float) -> str:
    if np.isnan(v):
        return "insufficient data"
    return "stable" if v < 0.10 else "moderate" if v < 0.25 else "large"


def drift_report(ref: pd.DataFrame, cur: pd.DataFrame, features: list[str], scores_ref: np.ndarray | None = None, scores_cur: np.ndarray | None = None, top: int = 10) -> dict:
    rows = []
    for f in features:
        if f in ref and f in cur:
            v = psi(ref[f].to_numpy(), cur[f].to_numpy())
            rows.append({"feature": f, "psi": None if np.isnan(v) else round(v, 4), "level": level(v),
                         "missing_rate_ref": round(float(ref[f].isna().mean()), 4), "missing_rate_cur": round(float(cur[f].isna().mean()), 4)})
    rows.sort(key=lambda r: -(r["psi"] or 0))
    out = {"features": rows[:top], "n_features_large": sum(1 for r in rows if r["level"] == "large"), "n_features": len(rows),
           "caveat": "PSI measures distribution change in inputs/scores. It is not evidence of degraded accuracy; labels are required to measure that."}
    if scores_ref is not None and scores_cur is not None:
        v = psi(scores_ref, scores_cur)
        out["score_psi"] = None if np.isnan(v) else round(v, 4)
        out["score_level"] = level(v)
    return out
