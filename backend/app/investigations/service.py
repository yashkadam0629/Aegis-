"""Case management with a full audit trail.

Analyst dispositions are workflow outcomes recorded by a human reviewer; they are NOT legal findings.
Model inference (pipeline re-runs) never changes an investigation or its review decision.
"""
from __future__ import annotations

import uuid

from sqlalchemy.orm import Session

from app.db import models as m
from app.db.models import utcnow

STATUSES = ["New", "In Review", "Escalated", "Resolved", "Closed"]
PRIORITIES = ["Low", "Medium", "High", "Critical"]
DISPOSITIONS = ["No issue identified", "Benign activity", "Further review required", "Confirmed internal policy violation", "Other documented outcome"]
TRANSITIONS = {
    "New": {"In Review"},
    "In Review": {"Escalated", "Resolved"},
    "Escalated": {"In Review", "Resolved"},
    "Resolved": {"Closed", "In Review"},
    "Closed": set(),
}
NEEDS_DISPOSITION = {"Resolved", "Closed"}
BAND_TO_PRIORITY = {"Critical": "Critical", "High": "High", "Medium": "Medium", "Low": "Low", "Minimal": "Low"}


class CaseError(ValueError):
    """Invalid case operation (maps to HTTP 409/422)."""


class NotFound(LookupError):
    pass


def _audit(s: Session, inv_id: str, actor: str, action: str, field: str | None = None, old=None, new=None):
    s.add(m.AuditLog(investigation_id=inv_id, actor=actor, action=action, field=field, old_value=None if old is None else str(old), new_value=None if new is None else str(new)))


def _get(s: Session, inv_id: str) -> m.Investigation:
    inv = s.get(m.Investigation, inv_id)
    if inv is None:
        raise NotFound(f"investigation {inv_id} not found")
    return inv


def create_from_alert(s: Session, alert_id: str, actor: str, title: str | None = None, priority: str | None = None, assignee: str | None = None) -> m.Investigation:
    alert = s.get(m.Alert, alert_id)
    if alert is None:
        raise NotFound(f"alert {alert_id} not found")
    existing = (s.query(m.Investigation).join(m.InvestigationAlert, m.InvestigationAlert.investigation_id == m.Investigation.investigation_id)
                .filter(m.InvestigationAlert.alert_id == alert_id, m.Investigation.status != "Closed").first())
    if existing:
        raise CaseError(f"alert {alert_id} is already linked to open investigation {existing.investigation_id}")
    if priority is not None and priority not in PRIORITIES:
        raise CaseError(f"priority must be one of {PRIORITIES}")
    inv_id = "INV-" + uuid.uuid4().hex[:8].upper()
    inv = m.Investigation(investigation_id=inv_id, dataset_id=alert.dataset_id, title=title or f"{alert.alert_type.replace('_', ' ').title()} – {alert.entity_id}",
                          priority=priority or BAND_TO_PRIORITY.get(alert.risk_band, "Medium"), assigned_analyst=assignee, created_by=actor)
    s.add(inv)
    s.flush()
    s.add(m.InvestigationAlert(investigation_id=inv_id, alert_id=alert_id))
    txn_ids = [r[0] for r in s.query(m.AlertTransaction.transaction_id).filter(m.AlertTransaction.alert_id == alert_id).limit(200)]
    for t in txn_ids:
        s.add(m.InvestigationTransaction(investigation_id=inv_id, transaction_id=t))
    alert.investigation_status = inv.status
    _audit(s, inv_id, actor, "created", "alert", None, alert_id)
    s.flush()
    return inv


def update(s: Session, inv_id: str, actor: str, *, status: str | None = None, priority: str | None = None, assigned_analyst: str | None = None,
           disposition: str | None = None, findings: str | None = None, clear_assignee: bool = False) -> m.Investigation:
    inv = _get(s, inv_id)
    if inv.status == "Closed":
        raise CaseError("closed investigations are read-only")
    changes: list[tuple[str, object, object]] = []
    if disposition is not None and disposition not in DISPOSITIONS:
        raise CaseError(f"disposition must be one of {DISPOSITIONS}")
    if priority is not None and priority not in PRIORITIES:
        raise CaseError(f"priority must be one of {PRIORITIES}")
    new_disp = disposition if disposition is not None else inv.disposition
    if status is not None and status != inv.status:
        if status not in STATUSES:
            raise CaseError(f"status must be one of {STATUSES}")
        if status not in TRANSITIONS[inv.status]:
            raise CaseError(f"invalid transition {inv.status} → {status}; allowed: {sorted(TRANSITIONS[inv.status]) or 'none'}")
        if status in NEEDS_DISPOSITION and not new_disp:
            raise CaseError(f"a disposition is required to move to {status}")
        if status == "Resolved" and not (findings or inv.findings):
            raise CaseError("recorded findings are required to resolve an investigation")
        changes.append(("status", inv.status, status))
        inv.status = status
        if status == "Closed":
            inv.closed_at = utcnow()
        if status == "In Review" and inv.closed_at:
            inv.closed_at = None
    for fld, new in (("priority", priority), ("disposition", disposition), ("findings", findings)):
        if new is not None and new != getattr(inv, fld):
            changes.append((fld, getattr(inv, fld), new))
            setattr(inv, fld, new)
    if assigned_analyst is not None and assigned_analyst != inv.assigned_analyst:
        changes.append(("assigned_analyst", inv.assigned_analyst, assigned_analyst))
        inv.assigned_analyst = assigned_analyst
    if clear_assignee and inv.assigned_analyst:
        changes.append(("assigned_analyst", inv.assigned_analyst, None))
        inv.assigned_analyst = None
    for fld, old, new in changes:
        _audit(s, inv_id, actor, "updated", fld, old, new)
    if changes:
        inv.updated_at = utcnow()
        for ia in s.query(m.InvestigationAlert).filter_by(investigation_id=inv_id):
            a = s.get(m.Alert, ia.alert_id)
            if a is not None:
                a.investigation_status = inv.status
    s.flush()
    return inv


def add_note(s: Session, inv_id: str, actor: str, body: str) -> m.InvestigationNote:
    inv = _get(s, inv_id)
    body = (body or "").strip()
    if not body:
        raise CaseError("note body is empty")
    if len(body) > 5000:
        raise CaseError("note too long (max 5000 characters)")
    if inv.status == "Closed":
        raise CaseError("closed investigations are read-only")
    n = m.InvestigationNote(investigation_id=inv_id, author=actor, body=body)
    s.add(n)
    inv.updated_at = utcnow()
    _audit(s, inv_id, actor, "note_added", "note", None, body[:80])
    s.flush()
    return n


def link_alert(s: Session, inv_id: str, alert_id: str, actor: str):
    inv = _get(s, inv_id)
    a = s.get(m.Alert, alert_id)
    if a is None:
        raise NotFound(f"alert {alert_id} not found")
    if a.dataset_id != inv.dataset_id:
        raise CaseError("alert belongs to a different dataset")
    if s.get(m.InvestigationAlert, (inv_id, alert_id)):
        raise CaseError("alert already linked")
    if inv.status == "Closed":
        raise CaseError("closed investigations are read-only")
    s.add(m.InvestigationAlert(investigation_id=inv_id, alert_id=alert_id))
    a.investigation_status = inv.status
    inv.updated_at = utcnow()
    _audit(s, inv_id, actor, "alert_linked", "alert", None, alert_id)
    s.flush()


def attach_transactions(s: Session, inv_id: str, txn_ids: list[str], actor: str) -> int:
    inv = _get(s, inv_id)
    if inv.status == "Closed":
        raise CaseError("closed investigations are read-only")
    if len(txn_ids) > 500:
        raise CaseError("at most 500 transactions per request")
    valid = {r[0] for r in s.query(m.Transaction.transaction_id).filter(m.Transaction.dataset_id == inv.dataset_id, m.Transaction.transaction_id.in_(txn_ids))}
    missing = set(txn_ids) - valid
    if missing:
        raise CaseError(f"unknown transactions: {sorted(missing)[:5]}")
    have = {r[0] for r in s.query(m.InvestigationTransaction.transaction_id).filter_by(investigation_id=inv_id)}
    new = [t for t in valid if t not in have]
    for t in new:
        s.add(m.InvestigationTransaction(investigation_id=inv_id, transaction_id=t))
    if new:
        inv.updated_at = utcnow()
        _audit(s, inv_id, actor, "transactions_attached", "transactions", None, f"{len(new)} added")
    s.flush()
    return len(new)


def history(s: Session, inv_id: str) -> list[dict]:
    _get(s, inv_id)
    rows = s.query(m.AuditLog).filter_by(investigation_id=inv_id).order_by(m.AuditLog.audit_id).all()
    return [{"audit_id": r.audit_id, "actor": r.actor, "at": r.at.isoformat(), "action": r.action, "field": r.field, "old": r.old_value, "new": r.new_value} for r in rows]


def serialize(s: Session, inv: m.Investigation, detail: bool = False) -> dict:
    d = {"investigation_id": inv.investigation_id, "dataset_id": inv.dataset_id, "title": inv.title, "assigned_analyst": inv.assigned_analyst, "status": inv.status,
         "priority": inv.priority, "created_at": inv.created_at.isoformat(), "updated_at": inv.updated_at.isoformat(), "closed_at": inv.closed_at.isoformat() if inv.closed_at else None,
         "disposition": inv.disposition, "findings": inv.findings, "created_by": inv.created_by,
         "disposition_notice": "Analyst workflow outcome; not a legal finding.",
         "age_days": round(((inv.closed_at or utcnow()) - inv.created_at).total_seconds() / 86400, 2)}
    alerts = [r.alert_id for r in s.query(m.InvestigationAlert).filter_by(investigation_id=inv.investigation_id)]
    d["alert_ids"] = alerts
    if detail:
        d["notes"] = [{"note_id": n.note_id, "author": n.author, "created_at": n.created_at.isoformat(), "body": n.body}
                      for n in s.query(m.InvestigationNote).filter_by(investigation_id=inv.investigation_id).order_by(m.InvestigationNote.note_id)]
        d["transaction_ids"] = [r.transaction_id for r in s.query(m.InvestigationTransaction).filter_by(investigation_id=inv.investigation_id)]
        d["history"] = history(s, inv.investigation_id)
        d["allowed_transitions"] = sorted(TRANSITIONS[inv.status])
    return d


def median_age_days(s: Session, dataset_id: str) -> float | None:
    invs = s.query(m.Investigation).filter(m.Investigation.dataset_id == dataset_id, m.Investigation.status != "Closed").all()
    if not invs:
        return None
    ages = sorted((utcnow() - i.created_at).total_seconds() / 86400 for i in invs)
    k = len(ages)
    return round(ages[k // 2] if k % 2 else (ages[k // 2 - 1] + ages[k // 2]) / 2, 3)
