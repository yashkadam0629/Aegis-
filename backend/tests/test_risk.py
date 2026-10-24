
import pytest

from app.risk import engine as R


def test_noisy_or_score_and_monotonicity():
    a = R.combine({"pattern": 0.5, "model": 0.5, "behavior": 0.5})
    expect = 1 - (1 - 0.65 * 0.5) * (1 - 0.75 * 0.5) * (1 - 0.35 * 0.5)
    assert a["score"] == pytest.approx(expect, abs=1e-6)
    assert R.combine({"pattern": 0.6, "model": 0.5, "behavior": 0.5})["score"] > a["score"]
    assert R.combine({"pattern": 1, "model": 1, "behavior": 1})["score"] <= 1.0
    assert R.combine({"pattern": 0, "model": 0, "behavior": 0})["score"] == 0.0


def test_missing_signals_are_skipped_not_zero_imputed():
    r = R.combine({"pattern": 0.8, "model": None})
    assert r["missing_families"] == ["model", "behavior"] and r["score"] == pytest.approx(0.65 * 0.8)
    assert R.combine({})["score"] == 0.0


def test_invalid_inputs_rejected():
    for bad in (1.5, -0.1, float("nan"), float("inf"), "0.5", True):
        with pytest.raises(ValueError):
            R.combine({"pattern": bad})
    with pytest.raises(ValueError):
        R.band(1.2)


def test_band_assignment_boundaries():
    assert [R.band(x) for x in (0.0, 0.19, 0.2, 0.4, 0.6, 0.8, 1.0)] == ["Minimal", "Minimal", "Low", "Medium", "High", "Critical", "Critical"]


def test_reproducible_and_corroboration_bonus():
    s = {"pattern": 0.7, "model": 0.4, "behavior": 0.2}
    assert R.combine(s) == R.combine(s)
    assert R.combine(s, n_distinct_detectors=3)["score"] > R.combine(s, n_distinct_detectors=1)["score"]
    assert R.combine(s, n_distinct_detectors=3)["score"] <= 1.0


def test_model_family_collapses_correlated_signals():
    assert R.model_family(0.3, 0.5) == 0.3                      # anomaly below the 95th pct adds nothing
    assert R.model_family(0.3, 1.0) == pytest.approx(0.6)       # top-5% anomaly supports at most cap
    assert R.model_family(None, None) is None
    assert R.model_family(0.9, 1.0) == 0.9                      # max, not sum


def test_exposure_does_not_double_count_path_hops():
    chain = {"T1": 100.0, "T2": 95.0, "T3": 90.0}
    e = R.exposure([("layered_chain", chain)])
    assert e.gross_value == 285.0 and e.exposed_value == 100.0
    fan = {"F1": 10.0, "F2": 20.0}
    e2 = R.exposure([("shared_beneficiary", fan)])
    assert e2.gross_value == e2.exposed_value == 30.0
    e3 = R.exposure([("layered_chain", {**chain, "F1": 10.0}), ("shared_beneficiary", {"F1": 10.0, "F2": 20.0})])   # overlapping evidence
    assert e3.gross_value == 315.0 and e3.exposed_value == 130.0
    assert R.exposure([]).gross_value == 0.0
