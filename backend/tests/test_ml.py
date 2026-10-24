import joblib
import numpy as np
import pandas as pd
import pytest

from app.db.session import session_scope
from app.features.engine import ALL_FEATURES, FEATURE_GROUPS
from app.ml import training as T


def test_chronological_split_properties():
    ts = pd.Series(pd.date_range("2026-01-01", periods=1000, freq="h"))
    t_tr, t_va, a, b, c = T.chrono_split(ts)
    assert (a & b).sum() == 0 and (b & c).sum() == 0 and (a | b | c).all()
    assert ts[a].max() < ts[b].min() and ts[b].max() < ts[c].min()
    assert a.mean() == pytest.approx(0.6, abs=0.01) and c.mean() == pytest.approx(0.25, abs=0.01)


def test_training_report_is_chronological_and_leak_free(seeded):
    r = seeded["train"]
    sp = r["split"]
    assert sp["train_end"] <= sp["validation_end"] == sp["test_start"] <= sp["test_end"]
    assert sp["rows"]["train_purged"] >= 0 and sp["positives"]["test"] > 0
    assert set(r["results"]["models"]) == {f"{g}/{k}" for g in FEATURE_GROUPS for k in ("logreg", "random_forest", "lightgbm")}
    # no label-derived column may be a model input
    for g in FEATURE_GROUPS.values():
        assert not {"y", "scenario_id", "is_suspicious_sim", "variant"} & set(g)
    for g, cols in FEATURE_GROUPS.items():
        assert set(cols) <= set(ALL_FEATURES)
    # selection used validation, and train/validation/test metrics are reported separately
    best = r["selected_model"]
    assert best == max(r["results"]["models"], key=lambda k: np.nan_to_num(r["results"]["models"][k]["validation"]["pr_auc"]))
    for split in ("train", "validation", "test"):
        assert "pr_auc" in r["results"]["models"][best][split]
    assert "holdout_templates" in r["results"] and r["results"]["holdout_templates"]
    assert r["calibration"]["conclusion"] and r["cost_assumptions"]["note"].startswith("ASSUMPTIONS")


def test_model_beats_chance_and_rules_on_test(seeded):
    r = seeded["train"]["results"]
    best = r["models"][seeded["train"]["selected_model"]]["test"]
    assert best["pr_auc"] > 3 * best["prevalence"]
    assert best["pr_auc"] > r["rules_baseline"]["test"]["pr_auc"]


def test_bundle_roundtrip_schema_and_missing_feature_handling(seeded):
    with session_scope() as s:
        bundle, run = T.load_bundle(s)
        assert run.is_active and run.feature_version
        feats = T.feature_table(s, seeded["dataset_id"]).tail(300).reset_index(drop=True)
    raw = bundle.raw(feats)
    assert raw.shape == (300,) and ((raw >= 0) & (raw <= 1)).all()
    risk = bundle.risk(feats)
    assert ((risk >= 0) & (risk <= 1)).all()
    # monotone calibration: ordering of raw scores is preserved (weakly)
    order = np.argsort(raw)
    assert (np.diff(risk[order]) >= -1e-12).all()
    # serialization: reload from disk gives identical predictions
    again = joblib.load(run.artifact_reference)
    np.testing.assert_allclose(again.raw(feats), raw)
    # NaN features are tolerated; a missing column is an explicit error
    nan_feats = feats.copy()
    nan_feats[bundle.features[3]] = np.nan
    assert np.isfinite(bundle.raw(nan_feats)).all()
    with pytest.raises(ValueError, match="missing features"):
        bundle.raw(feats.drop(columns=[bundle.features[0]]))


def test_explanations_have_signed_contributions(seeded):
    with session_scope() as s:
        bundle, _ = T.load_bundle(s)
        feats = T.feature_table(s, seeded["dataset_id"]).tail(5).reset_index(drop=True)
    ex = bundle.explain(feats)
    assert len(ex) == 5 and ex[0]["factors"] and {"feature", "contribution", "direction"} <= set(ex[0]["factors"][0])


def test_feature_table_cache_is_deterministic(seeded):
    with session_scope() as s:
        a = T.feature_table(s, seeded["dataset_id"], use_cache=False)
        b = T.feature_table(s, seeded["dataset_id"], use_cache=False)
    pd.testing.assert_frame_equal(a, b)


def test_rule_baseline_scores_in_unit_interval(seeded):
    with session_scope() as s:
        feats = T.feature_table(s, seeded["dataset_id"])
    r = T.rule_score(feats)
    assert r.min() >= 0 and r.max() <= 1 and r.max() > 0
