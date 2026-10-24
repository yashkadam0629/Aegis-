"""AEGIS risk & prioritisation engine.

Design
------
Evidence is grouped into *families*; signals inside a family are correlated, so they are collapsed (max) rather
than multiplied. Families are then combined with a noisy-OR, which is monotone, bounded in [0,1] and has
diminishing returns, so stacking weak signals cannot exceed what independent evidence would justify:

    risk = 1 - Π_f (1 - w_f · s_f)           s_f ∈ [0,1], w_f ∈ [0,1]

Families: `pattern` (graph/sequence detector strength), `model` (calibrated supervised score, with the
unsupervised anomaly percentile as a weaker supporting signal), `behavior` (transparent rule score).
Missing families are skipped (never imputed as zero evidence) and reported in the breakdown.

The weights and bands are OPERATIONAL settings, not statistically estimated parameters or regulatory
classifications. Two alternative scorers are exposed for comparison in the evaluation report:
`rules_score` (pattern+behavior, no ML) and `model_score` (ML only).
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field


@dataclass(frozen=True)
class RiskConfig:
    family_weights: dict = field(default_factory=lambda: {"pattern": 0.65, "model": 0.75, "behavior": 0.35})
    bands: tuple = (("Critical", 0.80), ("High", 0.60), ("Medium", 0.40), ("Low", 0.20))
    min_alert_score: float = 0.20
    corroboration_bonus: float = 0.06   # per additional DISTINCT detector type on the same evidence
    anomaly_support_cap: float = 0.6

    def version(self) -> str:
        return "risk-v1"


DEFAULT = RiskConfig()
FAMILIES = ("pattern", "model", "behavior")


def _check(name: str, v):
    if v is None:
        return None
    if isinstance(v, bool) or not isinstance(v, (int, float)) or math.isnan(v) or math.isinf(v):
        raise ValueError(f"signal {name!r} must be a finite number or None, got {v!r}")
    if not 0.0 <= v <= 1.0:
        raise ValueError(f"signal {name!r} must be within [0,1], got {v}")
    return float(v)


def model_family(model_risk: float | None, anomaly_pct: float | None, cap: float = 0.6) -> float | None:
    """Collapse correlated model signals: supervised score, supported weakly by unsupervised anomaly percentile (only the top 5%)."""
    m, a = _check("model_risk", model_risk), _check("anomaly_pct", anomaly_pct)
    if m is None and a is None:
        return None
    support = cap * max(0.0, ((a if a is not None else 0.0) - 0.95) / 0.05)
    return max(m or 0.0, support)


def combine(signals: dict, cfg: RiskConfig = DEFAULT, n_distinct_detectors: int = 1) -> dict:
    """Noisy-OR combination of family signals. Returns score, per-family contribution and missing families."""
    prod, used, missing, contrib = 1.0, {}, [], {}
    for f in FAMILIES:
        s = _check(f, signals.get(f))
        if s is None:
            missing.append(f)
            continue
        w = cfg.family_weights[f]
        term = w * s
        prod *= 1.0 - term
        used[f] = s
        contrib[f] = round(term, 4)
    score = 1.0 - prod
    if "pattern" in used and n_distinct_detectors > 1:
        score += cfg.corroboration_bonus * (n_distinct_detectors - 1) * used["pattern"]
    score = min(1.0, max(0.0, score))
    return {"score": round(score, 6), "signals": used, "contributions": contrib, "missing_families": missing,
            "n_distinct_detectors": n_distinct_detectors, "config_version": cfg.version(), "method": "noisy-OR over signal families"}


def rules_score(signals: dict, cfg: RiskConfig = DEFAULT, n_distinct_detectors: int = 1) -> float:
    return combine({"pattern": signals.get("pattern"), "behavior": signals.get("behavior")}, cfg, n_distinct_detectors)["score"]


def model_score(signals: dict) -> float | None:
    v = _check("model", signals.get("model"))
    return v


def band(score: float, cfg: RiskConfig = DEFAULT) -> str:
    if not 0.0 <= score <= 1.0 or math.isnan(score):
        raise ValueError("score must be within [0,1]")
    for name, thr in cfg.bands:
        if score >= thr:
            return name
    return "Minimal"


RECOMMENDED_STEPS = {
    "cycle": ["Review the full transaction cycle and confirm each hop's purpose and counterparties.", "Check whether the accounts share ownership, devices or addresses (outside this dataset).",
              "Compare with each account's usual transfer sizes and counterparties."],
    "layered_chain": ["Trace the chain backwards to its origin and forwards to its final recipient.", "Check whether intermediate accounts retained funds or merely forwarded them.",
                      "Look for a commercial explanation (supply chain, settlement) before escalating."],
    "shared_beneficiary": ["Confirm whether the beneficiary is an expected collection account (e.g. merchant, collection agent).", "Review the senders' relationship to the beneficiary and whether they are new counterparties.",
                           "Compare concentration with the beneficiary's own history."],
    "fan_out": ["Check whether the account is a known distributor (payroll, settlements).", "Compare the number of recipients and amounts with prior periods."],
    "burst": ["Compare the activity with the account's normal pattern and any known events.", "Review the counterparties involved in the burst."],
    "dormant_reactivation": ["Check recent account-maintenance events (contact, credential or device changes).", "Review the new counterparties and amounts against the account's pre-dormancy behaviour."],
    "structuring_like": ["Check whether the reference amount is meaningful for this customer's products or limits.", "Review whether payments could have been combined legitimately."],
    "coordinated_group": ["Review whether the accounts are related (employer, family, business).", "Examine shared counterparties and timing relative to ordinary activity."],
    "model_anomaly": ["Inspect the highest-scoring transactions and the feature contributions.", "Check for a benign explanation before opening an investigation."],
}


@dataclass
class Exposure:
    gross_value: float
    exposed_value: float
    n_transactions: int
    method: str


PATH_LIKE = {"cycle", "layered_chain"}


def exposure(detector_txns: list[tuple[str, dict[str, float]]]) -> Exposure:
    """Financial significance of ONE alert (union of one or more detections).

    gross_value   – sum over distinct transactions (each transaction counted once).
    exposed_value – path-like patterns (cycle, chain) re-use the same funds across hops, so only the largest hop is counted
                    for them; other patterns count each distinct transaction once. Transactions already counted are not repeated.
    This is an ESTIMATE of potentially affected value, not a loss figure.
    """
    seen: dict[str, float] = {}
    for _, txns in detector_txns:
        seen.update(txns)
    gross = float(sum(seen.values()))
    counted: set[str] = set()
    exposed = 0.0
    for det, txns in detector_txns:
        if det not in PATH_LIKE:
            for t, a in txns.items():
                if t not in counted:
                    exposed += a
                    counted.add(t)
    for det, txns in detector_txns:
        if det in PATH_LIKE:
            rest = {t: a for t, a in txns.items() if t not in counted}
            if rest:
                exposed += max(rest.values())
                counted |= set(rest)
    return Exposure(round(gross, 2), round(exposed, 2), len(seen),
                    "distinct transactions once; path-like patterns count their largest hop (funds re-used across hops)")
