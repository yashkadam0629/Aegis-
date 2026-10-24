import io

import numpy as np
import pandas as pd

from app.db.session import init_db, session_scope
from app.ingestion.pipeline import (
    import_csv_bytes,
    inspect_schema,
    load_synthetic,
    suggest_mapping,
    validate_and_normalize,
)
from app.synthetic.generator import GeneratorConfig, generate


def _base(n=5):
    return pd.DataFrame({
        "transaction_id": [f"T{i}" for i in range(n)], "source_account_id": [f"A{i}" for i in range(n)],
        "destination_account_id": [f"A{i+1}" for i in range(n)], "transaction_timestamp": ["2026-01-01T10:00:00Z"] * n,
        "amount": ["100.5"] * n, "currency": ["INR"] * n, "transaction_type": ["P2P_TRANSFER"] * n})


def test_generator_is_deterministic():
    a, b = generate(GeneratorConfig.quick()), generate(GeneratorConfig.quick())
    assert a.transactions.fillna("x").equals(b.transactions.fillna("x"))
    c = generate(GeneratorConfig(**{**GeneratorConfig.quick().__dict__, "seed": 7}))
    assert not a.transactions["amount"].astype(str).equals(c.transactions["amount"].astype(str))


def test_ground_truth_separated_and_scenarios_complete(small_ds):
    assert not {"scenario_id", "is_suspicious_sim"} & set(small_ds.transactions.columns)
    codes = {s["scenario_code"] for s in small_ds.scenarios}
    assert codes == set("ABCDEFGHIJ")
    assert any(not s["is_suspicious"] for s in small_ds.scenarios)  # benign lookalikes exist
    for s in small_ds.scenarios:
        assert s["transaction_ids"] and s["accounts"] and s["start_time"] <= s["end_time"]
    pos = small_ds.txn_labels["is_suspicious_sim"].mean()
    assert 0.005 < pos < 0.15


def test_generator_data_quality_issues_present(small_ds):
    t = small_ds.transactions
    assert t["transaction_id"].duplicated().any()
    assert t.isna().any().any()


def test_valid_import():
    clean, rej, rep = validate_and_normalize(_base())
    assert rep["rows_accepted"] == 5 and rep["rows_rejected"] == 0 and rej.empty
    assert clean["amount"].iloc[0] == 100.5 and str(clean["transaction_timestamp"].dtype).startswith("datetime64")


def test_missing_fields_invalid_values_and_reasons():
    df = _base(8)
    df.loc[0, "amount"] = None
    df.loc[1, "transaction_timestamp"] = "not-a-date"
    df.loc[2, "amount"] = "-5"
    df.loc[3, "currency"] = "ZZZ"
    df.loc[4, "destination_account_id"] = df.loc[4, "source_account_id"]
    df.loc[5, "source_account_id"] = None
    clean, rej, rep = validate_and_normalize(df)
    reasons = dict(zip(rej["source_row"], rej["reject_reason"]))
    assert reasons[2] == "missing_required" and reasons[3] == "invalid_timestamp" and reasons[4] == "invalid_amount"
    assert reasons[5] == "invalid_currency" and reasons[6] == "self_transfer" and reasons[7] == "missing_required"
    assert rep["rows_accepted"] == 2 and rep["rows_received"] == 8 and rep["rows_rejected"] == 6  # nothing silently dropped
    assert rep["rows_accepted"] + rep["rows_rejected"] + rep["duplicate_records"]["exact_duplicates_dropped"] * 0 == 8


def test_duplicates_exact_vs_conflicting():
    df = pd.concat([_base(3), _base(3).iloc[[0]], _base(3).iloc[[1]].assign(amount="999")], ignore_index=True)
    clean, rej, rep = validate_and_normalize(df)
    assert rep["duplicate_records"] == {"exact_duplicates_dropped": 1, "conflicting_duplicates_rejected": 1}
    assert clean["transaction_id"].is_unique and len(clean) == 3


def test_unknown_account_references_strict_with_accounts_file():
    accounts = pd.DataFrame({"account_id": ["A0", "A1", "A2"], "created_at": ["2025-01-01"] * 3})
    clean, rej, rep = validate_and_normalize(_base(4), accounts=accounts)
    assert rep["unresolved_account_references"] == 2 and len(clean) == 2
    assert (rej["reject_reason"] == "unresolved_account").all()


def test_inconsistent_record_flagged_not_rejected():
    accounts = pd.DataFrame({"account_id": [f"A{i}" for i in range(6)], "created_at": ["2027-01-01"] * 6})
    clean, rej, rep = validate_and_normalize(_base(3), accounts=accounts)
    assert rep["potentially_inconsistent_records"] == 3 and len(clean) == 3


def test_empty_and_missing_columns():
    clean, rej, rep = validate_and_normalize(pd.DataFrame(columns=["a", "b"]))
    assert rep["rows_accepted"] == 0 and "fatal_error" in rep
    clean, rej, rep = validate_and_normalize(_base(0))
    assert rep["rows_accepted"] == 0 and rep["rows_received"] == 0


def test_mapping_inference_and_schema_inspection():
    df = pd.DataFrame({"nameOrig": ["a"], "nameDest": ["b"], "amount": ["1"], "timestamp": ["2026-01-01"], "txn_id": ["x"]})
    mp = suggest_mapping(list(df.columns))
    assert mp["source_account_id"] == "nameOrig" and mp["transaction_id"] == "txn_id"
    info = inspect_schema(df)
    assert {c["name"] for c in info["columns"]} == set(df.columns)


def test_large_csv_import_and_error_report(tmp_path):
    init_db()
    n = 60000
    rng = np.random.default_rng(0)
    df = pd.DataFrame({"transaction_id": [f"L{i}" for i in range(n)], "source_account_id": [f"A{i % 500}" for i in range(n)],
                       "destination_account_id": [f"A{(i * 7 + 3) % 500 + 500}" for i in range(n)],
                       "transaction_timestamp": pd.date_range("2026-01-01", periods=n, freq="min").strftime("%Y-%m-%dT%H:%M:%SZ"),
                       "amount": rng.uniform(10, 1000, n).round(2).astype(str)})
    df.loc[5, "amount"] = "=HYPERLINK(1)"  # untrusted content must not become an executable formula in the error report
    buf = io.StringIO()
    df.to_csv(buf, index=False)
    with session_scope() as s:
        rec = import_csv_bytes(s, buf.getvalue().encode(), "big.csv", "big")
        assert rec.report["rows_accepted"] == n - 1
        text = open(rec.reject_path).read()
        assert "'=HYPERLINK" in text or "invalid_amount" in text
        assert "\n=" not in text


def test_synthetic_load_roundtrip(small_ds):
    init_db()
    with session_scope() as s:
        rec = load_synthetic(s, small_ds, "test")
        rep = rec.report
        assert rep["rows_accepted"] > 0 and rep["rows_rejected"] + rep["rows_accepted"] == rep["rows_received"]
        assert rep["duplicate_records"]["exact_duplicates_dropped"] >= 1
