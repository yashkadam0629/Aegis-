"""SQLAlchemy schema. Works on SQLite (default) and PostgreSQL (AEGIS_DATABASE_URL)."""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class Base(DeclarativeBase):
    pass


class Dataset(Base):
    __tablename__ = "datasets"
    dataset_id: Mapped[str] = mapped_column(String(40), primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    kind: Mapped[str] = mapped_column(String(20), default="synthetic")  # synthetic | upload
    source_dataset: Mapped[str] = mapped_column(String(100), default="aegis-synthetic-v1")
    dataset_version: Mapped[str] = mapped_column(String(40), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    n_accounts: Mapped[int] = mapped_column(Integer, default=0)
    n_transactions: Mapped[int] = mapped_column(Integer, default=0)
    min_ts: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    max_ts: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    has_ground_truth: Mapped[bool] = mapped_column(Boolean, default=False)
    status: Mapped[str] = mapped_column(String(20), default="ready")
    meta: Mapped[dict] = mapped_column(JSON, default=dict)


class Account(Base):
    __tablename__ = "accounts"
    dataset_id: Mapped[str] = mapped_column(ForeignKey("datasets.dataset_id", ondelete="CASCADE"), primary_key=True)
    account_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    account_type: Mapped[str] = mapped_column(String(32), default="unknown")
    created_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    account_status: Mapped[str] = mapped_column(String(20), default="active")
    customer_segment: Mapped[str | None] = mapped_column(String(40), nullable=True)
    inferred: Mapped[bool] = mapped_column(Boolean, default=False)  # created from a transaction reference, not an accounts file


class Transaction(Base):
    __tablename__ = "transactions"
    dataset_id: Mapped[str] = mapped_column(ForeignKey("datasets.dataset_id", ondelete="CASCADE"), primary_key=True)
    transaction_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    source_account_id: Mapped[str] = mapped_column(String(64))
    destination_account_id: Mapped[str] = mapped_column(String(64))
    transaction_timestamp: Mapped[datetime] = mapped_column(DateTime)
    amount: Mapped[float] = mapped_column(Float)
    currency: Mapped[str] = mapped_column(String(3), default="INR")
    transaction_type: Mapped[str] = mapped_column(String(32), default="P2P_TRANSFER")
    channel: Mapped[str | None] = mapped_column(String(20), nullable=True)
    transaction_status: Mapped[str] = mapped_column(String(20), default="completed")
    source_dataset: Mapped[str] = mapped_column(String(100), default="")
    ingestion_timestamp: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    __table_args__ = (
        Index("ix_txn_src", "dataset_id", "source_account_id", "transaction_timestamp"),
        Index("ix_txn_dst", "dataset_id", "destination_account_id", "transaction_timestamp"),
        Index("ix_txn_ts", "dataset_id", "transaction_timestamp"),
    )


class ImportReport(Base):
    __tablename__ = "import_reports"
    report_id: Mapped[str] = mapped_column(String(40), primary_key=True)
    dataset_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    filename: Mapped[str] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    status: Mapped[str] = mapped_column(String(20), default="completed")
    report: Mapped[dict] = mapped_column(JSON, default=dict)
    reject_path: Mapped[str | None] = mapped_column(String(500), nullable=True)


# ---- hidden ground truth (evaluation only; never read by inference code paths) -----------------
class GtScenario(Base):
    __tablename__ = "gt_scenarios"
    dataset_id: Mapped[str] = mapped_column(String(40), primary_key=True)
    scenario_id: Mapped[str] = mapped_column(String(20), primary_key=True)
    payload: Mapped[dict] = mapped_column(JSON)


class GtTxnLabel(Base):
    __tablename__ = "gt_txn_labels"
    dataset_id: Mapped[str] = mapped_column(String(40), primary_key=True)
    transaction_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    scenario_id: Mapped[str] = mapped_column(String(20), default="")
    is_suspicious_sim: Mapped[bool] = mapped_column(Boolean, default=False)


class ModelRun(Base):
    __tablename__ = "model_runs"
    model_run_id: Mapped[str] = mapped_column(String(40), primary_key=True)
    model_name: Mapped[str] = mapped_column(String(100))
    model_version: Mapped[str] = mapped_column(String(40))
    dataset_id: Mapped[str] = mapped_column(String(40))
    dataset_version: Mapped[str] = mapped_column(String(40))
    feature_version: Mapped[str] = mapped_column(String(40))
    training_timestamp: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    evaluation_metrics: Mapped[dict] = mapped_column(JSON, default=dict)
    configuration: Mapped[dict] = mapped_column(JSON, default=dict)
    artifact_reference: Mapped[str] = mapped_column(String(500))
    is_active: Mapped[bool] = mapped_column(Boolean, default=False)


class PipelineRun(Base):
    __tablename__ = "pipeline_runs"
    run_id: Mapped[str] = mapped_column(String(40), primary_key=True)
    dataset_id: Mapped[str] = mapped_column(String(40))
    model_run_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="running")
    window_start: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    window_end: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    metrics: Mapped[dict] = mapped_column(JSON, default=dict)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)


class Alert(Base):
    __tablename__ = "alerts"
    alert_id: Mapped[str] = mapped_column(String(40), primary_key=True)
    dataset_id: Mapped[str] = mapped_column(String(40), index=True)
    run_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    alert_timestamp: Mapped[datetime] = mapped_column(DateTime)       # end of the evidence window (event time)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    entity_type: Mapped[str] = mapped_column(String(20))
    entity_id: Mapped[str] = mapped_column(String(64), index=True)
    alert_type: Mapped[str] = mapped_column(String(40), index=True)
    risk_score: Mapped[float] = mapped_column(Float)
    risk_band: Mapped[str] = mapped_column(String(12), index=True)
    model_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    rule_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    n_transactions: Mapped[int] = mapped_column(Integer, default=0)
    n_accounts: Mapped[int] = mapped_column(Integer, default=0)
    gross_value: Mapped[float] = mapped_column(Float, default=0.0)
    unique_value: Mapped[float] = mapped_column(Float, default=0.0)
    exposed_value: Mapped[float] = mapped_column(Float, default=0.0)
    evidence_start: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    evidence_end: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    investigation_status: Mapped[str] = mapped_column(String(20), default="Not opened")
    duplicate_of: Mapped[str | None] = mapped_column(String(40), nullable=True)
    model_version: Mapped[str] = mapped_column(String(40), default="")
    feature_version: Mapped[str] = mapped_column(String(40), default="")
    detail: Mapped[dict] = mapped_column(JSON, default=dict)          # evidence summary, paths, explanation, limitations


class AlertTransaction(Base):
    __tablename__ = "alert_transactions"
    alert_id: Mapped[str] = mapped_column(ForeignKey("alerts.alert_id", ondelete="CASCADE"), primary_key=True)
    transaction_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    dataset_id: Mapped[str] = mapped_column(String(40), index=True)


class Investigation(Base):
    __tablename__ = "investigations"
    investigation_id: Mapped[str] = mapped_column(String(40), primary_key=True)
    dataset_id: Mapped[str] = mapped_column(String(40), index=True)
    title: Mapped[str] = mapped_column(String(300))
    assigned_analyst: Mapped[str | None] = mapped_column(String(100), nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="New", index=True)
    priority: Mapped[str] = mapped_column(String(12), default="Medium")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    disposition: Mapped[str | None] = mapped_column(String(60), nullable=True)
    findings: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by: Mapped[str] = mapped_column(String(100), default="analyst")


class InvestigationAlert(Base):
    __tablename__ = "investigation_alerts"
    investigation_id: Mapped[str] = mapped_column(ForeignKey("investigations.investigation_id", ondelete="CASCADE"), primary_key=True)
    alert_id: Mapped[str] = mapped_column(String(40), primary_key=True)


class InvestigationTransaction(Base):
    __tablename__ = "investigation_transactions"
    investigation_id: Mapped[str] = mapped_column(ForeignKey("investigations.investigation_id", ondelete="CASCADE"), primary_key=True)
    transaction_id: Mapped[str] = mapped_column(String(64), primary_key=True)


class InvestigationNote(Base):
    __tablename__ = "investigation_notes"
    note_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    investigation_id: Mapped[str] = mapped_column(ForeignKey("investigations.investigation_id", ondelete="CASCADE"), index=True)
    author: Mapped[str] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    body: Mapped[str] = mapped_column(Text)


class AuditLog(Base):
    __tablename__ = "audit_log"
    audit_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    investigation_id: Mapped[str] = mapped_column(String(40), index=True)
    actor: Mapped[str] = mapped_column(String(100))
    at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    action: Mapped[str] = mapped_column(String(40))
    field: Mapped[str | None] = mapped_column(String(60), nullable=True)
    old_value: Mapped[str | None] = mapped_column(Text, nullable=True)
    new_value: Mapped[str | None] = mapped_column(Text, nullable=True)


class TxnScore(Base):
    """Model outputs for scored transactions (written by the pipeline; no ground truth here)."""
    __tablename__ = "txn_scores"
    dataset_id: Mapped[str] = mapped_column(String(40), primary_key=True)
    transaction_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    run_id: Mapped[str] = mapped_column(String(40))
    model_risk: Mapped[float] = mapped_column(Float)
    anomaly: Mapped[float] = mapped_column(Float)
    rule_score: Mapped[float] = mapped_column(Float)
