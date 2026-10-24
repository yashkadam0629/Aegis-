import pandas as pd
import pytest

from app.db.session import session_scope
from app.features.engine import compute_features
from app.ml import training as T
from app.services.pipeline import get_index
from app.streaming import simulator as S


@pytest.fixture(scope="module")
def stream_inputs(seeded):
    with session_scope() as s:
        bundle, _ = T.load_bundle(s)
        df = get_index(s, seeded["dataset_id"]).df.iloc[:3000].reset_index(drop=True)
    return bundle, df


def test_streaming_features_equal_batch_features(stream_inputs):
    bundle, df = stream_inputs
    events = S.make_events(df, 1500, dup_rate=0.0, invalid_rate=0.0)
    res = S.simulate(events, bundle, None)
    batch = compute_features(df.head(1500))
    pd.testing.assert_frame_equal(res["features"].drop(columns="transaction_id").reset_index(drop=True), batch.drop(columns="transaction_id"), check_exact=False, rtol=1e-12)


def test_duplicates_invalid_events_and_retries(stream_inputs):
    bundle, df = stream_inputs
    ev = S.make_events(df, 800, dup_rate=0.05, invalid_rate=0.02, seed=3)
    res = S.simulate(ev, bundle, None, transient_failure_rate=0.1, max_retries=3, seed=5)
    st = res["stats"]
    assert st["received"] == len(ev) and st["duplicates_dropped"] > 0 and st["invalid_dropped"] > 0 and st["retries"] > 0
    assert st["processed"] + st["duplicates_dropped"] + st["invalid_dropped"] + st["dead_letter"] == st["received"]
    assert len(res["features"]) == st["processed"]
    assert res["throughput_events_per_s"] > 0 and res["latency_ms"]["p50"] <= res["latency_ms"]["p99"] and res["hardware"]["cpu_count"]


def test_event_stream_is_reproducible(stream_inputs):
    _, df = stream_inputs
    a = S.make_events(df, 300, seed=9)
    b = S.make_events(df, 300, seed=9)
    assert [e["event_id"] for e in a] == [e["event_id"] for e in b]


def test_micro_batching_scores_same_events(stream_inputs):
    bundle, df = stream_inputs
    ev = S.make_events(df, 400, dup_rate=0.0, invalid_rate=0.0)
    a = S.simulate(ev, bundle, None, batch_size=1)
    b = S.simulate(ev, bundle, None, batch_size=50)
    assert a["stats"]["processed"] == b["stats"]["processed"] == 400
    assert a["stats"]["alerts"] == b["stats"]["alerts"]
