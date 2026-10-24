import numpy as np
import pandas as pd
import pytest

from app.features.engine import ALL_FEATURES, FEATURE_GROUPS, compute_features, reference_features
from app.ingestion.pipeline import validate_and_normalize


@pytest.fixture(scope="module")
def clean(small_ds):
    c, _, _ = validate_and_normalize(small_ds.transactions, None, small_ds.accounts)
    return c.sort_values("transaction_timestamp", kind="stable").reset_index(drop=True)


def test_feature_groups_reference_known_columns():
    assert set(ALL_FEATURES) >= {f for g in FEATURE_GROUPS.values() for f in g}
    assert len(ALL_FEATURES) == len(set(ALL_FEATURES))


def test_incremental_equals_batch_reference(clean, small_ds):
    sub = clean.iloc[:6000]
    feats = compute_features(sub)
    rng = np.random.default_rng(0)
    for i in rng.choice(len(sub), 120, replace=False):
        ref = reference_features(sub, int(i))
        compared = 0
        for k, v in ref.items():
            if k not in feats.columns:
                continue
            compared += 1
            assert feats.iloc[i][k] == pytest.approx(v, rel=1e-9, abs=1e-6), (i, k)
        assert compared >= 15


def test_no_future_leakage(clean):
    """Features of the first N rows are identical whether or not later transactions exist."""
    full = compute_features(clean.iloc[:4000])
    part = compute_features(clean.iloc[:2500])
    a, b = full.iloc[:2500].drop(columns="transaction_id"), part.drop(columns="transaction_id")
    pd.testing.assert_frame_equal(a, b, check_exact=False, rtol=1e-12)


def test_cycle_and_chain_features_fire_on_planted_pattern():
    t0 = pd.Timestamp("2026-03-01")
    rows = [("T1", "A", "B", t0, 1000.0), ("T2", "B", "C", t0 + pd.Timedelta(hours=1), 960.0),
            ("T3", "C", "A", t0 + pd.Timedelta(hours=2), 920.0)]
    df = pd.DataFrame(rows, columns=["transaction_id", "source_account_id", "destination_account_id", "transaction_timestamp", "amount"])
    df["transaction_type"] = "P2P_TRANSFER"
    df["channel"] = "UPI"
    f = compute_features(df).set_index("transaction_id")
    assert list(f["chain_len"]) == [1, 2, 3]
    assert f.loc["T3", "cycle_close3"] == 1.0 and f.loc["T1", "cycle_close3"] == 0.0


def test_zero_history_values_are_defined():
    df = pd.DataFrame({"transaction_id": ["X"], "source_account_id": ["A"], "destination_account_id": ["B"],
                       "transaction_timestamp": [pd.Timestamp("2026-01-01")], "amount": [10.0], "transaction_type": ["P2P_TRANSFER"], "channel": ["UPI"]})
    f = compute_features(df).iloc[0]
    assert f["s_out_cnt_24h"] == 0 and f["s_inout_ratio_24h"] == 1.0 and f["pass_through_ratio"] == pytest.approx(1 / 11)
    assert np.isnan(f["s_amt_z"]) and not np.isinf(f.drop("transaction_id").astype(float)).any()
