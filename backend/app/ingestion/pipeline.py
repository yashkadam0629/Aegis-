"""CSV ingestion: schema inspection, mapping, validation, normalisation, dedup, persistence.

Nothing is silently dropped: every rejected row is returned with a reason and written to a
downloadable error report. Uploaded files are treated as untrusted data (parsed, never executed;
error-report cells that could be interpreted as spreadsheet formulas are neutralised).
"""
from __future__ import annotations

import io
import uuid

import numpy as np
import pandas as pd
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db import models as m

CANONICAL = ["transaction_id", "source_account_id", "destination_account_id", "transaction_timestamp", "amount",
             "currency", "transaction_type", "channel", "transaction_status"]
REQUIRED = ["transaction_id", "source_account_id", "destination_account_id", "transaction_timestamp", "amount"]
ALIASES = {
    "transaction_id": ["transaction_id", "txn_id", "id", "tx_id", "transactionid"],
    "source_account_id": ["source_account_id", "source", "nameorig", "sender", "from_account", "src", "origin_account"],
    "destination_account_id": ["destination_account_id", "destination", "namedest", "receiver", "to_account", "dst", "beneficiary_account"],
    "transaction_timestamp": ["transaction_timestamp", "timestamp", "datetime", "date", "time", "txn_time", "booking_date"],
    "amount": ["amount", "amt", "value", "transaction_amount"],
    "currency": ["currency", "ccy", "curr"],
    "transaction_type": ["transaction_type", "type", "txn_type"],
    "channel": ["channel", "mode"],
    "transaction_status": ["transaction_status", "status"],
}
VALID_CURRENCIES = {"INR", "USD", "EUR", "GBP", "AED", "SGD", "JPY", "AUD", "CAD", "CHF", "CNY", "HKD"}
HANDLING = {
    "missing_required": "Row rejected (reported in error file). Required: " + ", ".join(REQUIRED) + ".",
    "invalid_timestamp": "Row rejected; timestamps are parsed as ISO-8601/UTC and normalised to naive UTC.",
    "invalid_amount": "Row rejected: amount must be a finite number > 0.",
    "invalid_currency": "Row rejected: currency must be a 3-letter ISO 4217 code from the supported list; blank currency defaults to INR.",
    "self_transfer": "Row rejected: source and destination are the same account.",
    "duplicate_exact": "Later copies of an identical transaction_id+content are dropped (first occurrence kept) and counted.",
    "duplicate_conflicting": "Same transaction_id with different content: first occurrence kept, later rows rejected and reported.",
    "unresolved_account": "Rows referencing accounts missing from the supplied accounts file are rejected (strict policy). Without an accounts file, accounts are inferred from transactions and flagged inferred=true.",
    "inconsistent_record": "Accepted but flagged: transaction timestamp precedes the account's recorded creation time.",
}


def suggest_mapping(columns: list[str]) -> dict[str, str | None]:
    low = {c.lower().strip(): c for c in columns}
    out: dict[str, str | None] = {}
    for canon, aliases in ALIASES.items():
        out[canon] = next((low[a] for a in aliases if a in low), None)
    return out


def inspect_schema(df: pd.DataFrame, sample: int = 5) -> dict:
    cols = []
    for c in df.columns:
        s = df[c]
        num = pd.to_numeric(s, errors="coerce").notna().mean() if len(s) else 0
        ts = pd.to_datetime(s.astype(str).head(200), errors="coerce", utc=True).notna().mean() if len(s) else 0
        inferred = "numeric" if num > 0.95 else "timestamp" if ts > 0.95 else "text"
        cols.append({"name": str(c), "inferred_type": inferred, "null_fraction": round(float(s.isna().mean()), 4) if len(s) else 0,
                     "sample": [None if pd.isna(v) else str(v) for v in s.head(sample)]})
    return {"n_rows": len(df), "columns": cols, "suggested_mapping": suggest_mapping([str(c) for c in df.columns]),
            "required": REQUIRED}


def _sanitize_cell(v):
    if isinstance(v, str) and v[:1] in ("=", "+", "-", "@", "\t", "\r"):
        return "'" + v
    return v


def validate_and_normalize(df_raw: pd.DataFrame, mapping: dict[str, str | None] | None = None,
                           accounts: pd.DataFrame | None = None, source_dataset: str = "upload") -> tuple[pd.DataFrame, pd.DataFrame, dict]:
    mapping = mapping or suggest_mapping([str(c) for c in df_raw.columns])
    missing_map = [c for c in REQUIRED if not mapping.get(c) or mapping[c] not in df_raw.columns]
    n = len(df_raw)
    report: dict = {"rows_received": n, "mapping": mapping, "handling": HANDLING, "ingested_source_dataset": source_dataset}
    if missing_map:
        report.update({"rows_accepted": 0, "rows_rejected": n, "fatal_error": f"required columns not mapped: {missing_map}"})
        rej = df_raw.copy()
        rej["reject_reason"] = "schema_error"
        return pd.DataFrame(columns=CANONICAL), rej, report
    df = pd.DataFrame({c: df_raw[mapping[c]] if mapping.get(c) in df_raw.columns else None for c in CANONICAL})
    df["_row"] = np.arange(n) + 2  # 1-based incl. header, as seen in a spreadsheet
    for c in ("transaction_id", "source_account_id", "destination_account_id", "transaction_type", "channel", "transaction_status", "currency"):
        df[c] = df[c].astype("object").where(df[c].notna(), None)
        df[c] = df[c].map(lambda v: v.strip() if isinstance(v, str) else (None if v is None else str(v)))
        df[c] = df[c].replace("", None)
    reason = pd.Series("", index=df.index, dtype=object)

    def mark(mask, why):
        nonlocal reason
        mask = mask & (reason == "")
        reason = reason.where(~mask, why)
        return int(mask.sum())

    missing_counts = {c: int(df[c].isna().sum()) for c in CANONICAL}
    ts = pd.to_datetime(df["transaction_timestamp"], errors="coerce", utc=True).dt.tz_localize(None)
    amt = pd.to_numeric(df["amount"], errors="coerce")
    req_missing = df[["transaction_id", "source_account_id", "destination_account_id"]].isna().any(axis=1) | df["transaction_timestamp"].isna() | df["amount"].isna()
    n_missing = mark(req_missing, "missing_required")
    n_ts = mark(ts.isna(), "invalid_timestamp")
    n_amt = mark(~np.isfinite(amt) | (amt <= 0), "invalid_amount")
    cur = df["currency"].fillna("INR").str.upper()
    n_cur = mark(~cur.isin(VALID_CURRENCIES), "invalid_currency")
    n_self = mark(df["source_account_id"] == df["destination_account_id"], "self_transfer")
    df["transaction_timestamp"], df["amount"], df["currency"] = ts, amt.round(2), cur

    # duplicates among otherwise-valid rows
    ok = reason == ""
    sub = df.loc[ok]
    key = sub["source_account_id"].astype(str) + "|" + sub["destination_account_id"].astype(str) + "|" + sub["transaction_timestamp"].astype(str) + "|" + sub["amount"].astype(str)
    first_seen = df.loc[ok].groupby("transaction_id").cumcount() == 0
    dup_later = ~first_seen
    sig_first = key.groupby(df.loc[ok, "transaction_id"]).transform("first")
    exact = dup_later & (key == sig_first)
    conflicting = dup_later & (key != sig_first)
    reason.loc[exact[exact].index] = "duplicate_exact"
    reason.loc[conflicting[conflicting].index] = "duplicate_conflicting"
    n_dup_exact, n_dup_conf = int(exact.sum()), int(conflicting.sum())

    # account reference resolution
    n_unres = 0
    inferred = False
    if accounts is not None and len(accounts):
        known = set(accounts["account_id"].astype(str))
        unres = (~df["source_account_id"].isin(known) | ~df["destination_account_id"].isin(known))
        n_unres = mark(unres, "unresolved_account")
    else:
        inferred = True
    n_incons = 0
    if accounts is not None and "created_at" in accounts and len(accounts):
        created = pd.to_datetime(accounts.set_index("account_id")["created_at"], errors="coerce", utc=True).dt.tz_localize(None)
        c_src = df["source_account_id"].map(created)
        n_incons = int(((df["transaction_timestamp"] < c_src) & (reason == "")).sum())

    accepted_mask = reason == ""
    clean = df.loc[accepted_mask, CANONICAL].copy()
    clean["transaction_type"] = clean["transaction_type"].fillna("UNKNOWN")
    clean["transaction_status"] = clean["transaction_status"].fillna("completed")
    clean["source_dataset"] = source_dataset
    rej = df_raw.loc[~accepted_mask.values].copy()
    rej.insert(0, "source_row", df.loc[~accepted_mask, "_row"].values)
    rej.insert(1, "reject_reason", reason[~accepted_mask].values)
    report.update({
        "rows_accepted": int(accepted_mask.sum()), "rows_rejected": int((~accepted_mask).sum()),
        "duplicate_records": {"exact_duplicates_dropped": n_dup_exact, "conflicting_duplicates_rejected": n_dup_conf},
        "missing_values": missing_counts, "rejected_by_reason": {k: int(v) for k, v in reason[reason != ""].value_counts().items()},
        "invalid_timestamps": n_ts, "invalid_amounts": n_amt, "invalid_currency": n_cur, "self_transfers": n_self,
        "missing_required_rows": n_missing, "unresolved_account_references": n_unres,
        "potentially_inconsistent_records": n_incons, "accounts_inferred_from_transactions": inferred,
        "out_of_order_rows": int((ts.dropna().diff().dt.total_seconds() < 0).sum()),
    })
    return clean, rej, report


def dataset_fingerprint(tx: pd.DataFrame) -> str:
    h = pd.util.hash_pandas_object(tx[["transaction_id", "source_account_id", "destination_account_id", "amount"]].astype(str), index=False)
    return f"{int(h.sum()) & 0xFFFFFFFFFFFF:012x}"


def persist(session: Session, dataset_id: str, name: str, kind: str, clean: pd.DataFrame, accounts: pd.DataFrame | None,
            report: dict, filename: str, rej: pd.DataFrame, source_dataset: str, has_ground_truth: bool = False) -> m.ImportReport:
    settings = get_settings()
    clean = clean.sort_values("transaction_timestamp", kind="stable").reset_index(drop=True)
    now = pd.Timestamp.utcnow().tz_localize(None)
    clean["ingestion_timestamp"] = now
    clean["dataset_id"] = dataset_id
    if accounts is not None and len(accounts):
        acc = accounts.copy()
        acc["created_at"] = pd.to_datetime(acc["created_at"], errors="coerce", utc=True).dt.tz_localize(None)
        acc["inferred"] = False
    else:
        ids = pd.unique(pd.concat([clean["source_account_id"], clean["destination_account_id"]]))
        first_seen = pd.concat([clean[["source_account_id", "transaction_timestamp"]].set_axis(["account_id", "t"], axis=1),
                                clean[["destination_account_id", "transaction_timestamp"]].set_axis(["account_id", "t"], axis=1)]).groupby("account_id")["t"].min()
        acc = pd.DataFrame({"account_id": ids, "account_type": "unknown", "account_status": "active", "customer_segment": None, "inferred": True})
        acc["created_at"] = acc["account_id"].map(first_seen)
    for col in ("account_type", "account_status", "customer_segment"):
        if col not in acc:
            acc[col] = None
    acc = acc[["account_id", "account_type", "created_at", "account_status", "customer_segment", "inferred"]].drop_duplicates("account_id")
    acc["dataset_id"] = dataset_id
    version = dataset_fingerprint(clean) if len(clean) else "empty"
    ds = m.Dataset(dataset_id=dataset_id, name=name, kind=kind, source_dataset=source_dataset, dataset_version=version,
                   n_accounts=len(acc), n_transactions=len(clean), min_ts=clean["transaction_timestamp"].min() if len(clean) else None,
                   max_ts=clean["transaction_timestamp"].max() if len(clean) else None, has_ground_truth=has_ground_truth)
    session.add(ds)
    session.flush()
    bind = session.connection()
    acc.to_sql("accounts", bind, if_exists="append", index=False, chunksize=1000, method="multi") if len(acc) else None
    clean.to_sql("transactions", bind, if_exists="append", index=False, chunksize=1500, method="multi") if len(clean) else None
    rp = None
    rid = "IMP-" + uuid.uuid4().hex[:10]
    if len(rej):
        rdir = settings.data_dir / "reports"
        rdir.mkdir(parents=True, exist_ok=True)
        rp = rdir / f"{rid}_rejects.csv"
        rej.map(_sanitize_cell).to_csv(rp, index=False)
    report["dataset_version"] = version
    rec = m.ImportReport(report_id=rid, dataset_id=dataset_id, filename=filename, report=report, reject_path=str(rp) if rp else None)
    session.add(rec)
    session.flush()
    return rec


def import_csv_bytes(session: Session, content: bytes, filename: str, name: str, mapping: dict | None = None,
                     accounts_content: bytes | None = None, source_dataset: str = "upload", kind: str = "upload") -> m.ImportReport:
    df_raw = pd.read_csv(io.BytesIO(content), dtype=str, keep_default_na=False, na_values=[""])
    accounts = pd.read_csv(io.BytesIO(accounts_content), dtype=str, keep_default_na=False, na_values=[""]) if accounts_content else None
    if accounts is not None and "account_id" not in accounts:
        raise ValueError("accounts file must contain an account_id column")
    clean, rej, report = validate_and_normalize(df_raw, mapping, accounts, source_dataset)
    if report.get("fatal_error") or len(clean) == 0:
        rid = "IMP-" + uuid.uuid4().hex[:10]
        rec = m.ImportReport(report_id=rid, dataset_id=None, filename=filename, status="failed", report=report)
        session.add(rec)
        session.flush()
        return rec
    dataset_id = "DS-" + uuid.uuid4().hex[:8]
    return persist(session, dataset_id, name, kind, clean, accounts, report, filename, rej, source_dataset)


def load_synthetic(session: Session, ds, name: str = "AEGIS synthetic (seed 42)") -> m.ImportReport:
    """Run a generated dataset through the SAME ingestion path as uploads, then store hidden labels separately."""
    clean, rej, report = validate_and_normalize(ds.transactions.astype({"amount": "object"}), None, ds.accounts, "aegis-synthetic-v1")
    dataset_id = "DS-" + uuid.uuid4().hex[:8]
    rec = persist(session, dataset_id, name, "synthetic", clean, ds.accounts, report, "synthetic_generator", rej, "aegis-synthetic-v1", has_ground_truth=True)
    lab = ds.txn_labels[ds.txn_labels["transaction_id"].isin(set(clean["transaction_id"]))].copy()
    lab["dataset_id"] = dataset_id
    lab.to_sql("gt_txn_labels", session.connection(), if_exists="append", index=False, chunksize=5000, method="multi")
    for s in ds.scenarios:
        session.add(m.GtScenario(dataset_id=dataset_id, scenario_id=s["scenario_id"], payload=_jsonable(s)))
    session.get(m.Dataset, dataset_id).meta = {"generator_config": _jsonable(ds.config.__dict__)}
    session.flush()
    return rec


def _jsonable(o):
    if isinstance(o, dict):
        return {k: _jsonable(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_jsonable(v) for v in o]
    if isinstance(o, (pd.Timestamp,)):
        return o.isoformat()
    if isinstance(o, (np.integer,)):
        return int(o)
    if isinstance(o, (np.floating,)):
        return float(o)
    return o
