import pytest

from app.db import models as m
from app.db.session import session_scope
from app.investigations import service as inv


@pytest.fixture()
def alert_id(seeded):
    with session_scope() as s:
        a = s.query(m.Alert).filter(m.Alert.dataset_id == seeded["dataset_id"], m.Alert.investigation_status == "Not opened").first()
        return a.alert_id


def test_create_assign_note_status_and_audit(seeded, alert_id):
    with session_scope() as s:
        i = inv.create_from_alert(s, alert_id, "alice")
        iid = i.investigation_id
        assert i.status == "New" and i.priority in inv.PRIORITIES
        assert s.get(m.Alert, alert_id).investigation_status == "New"
        inv.update(s, iid, "alice", assigned_analyst="bob", priority="High")
        inv.add_note(s, iid, "bob", "Looks like payroll; checking employer.")
        inv.update(s, iid, "bob", status="In Review")
        assert s.get(m.Alert, alert_id).investigation_status == "In Review"
        with pytest.raises(inv.CaseError):                       # cannot skip to Closed
            inv.update(s, iid, "bob", status="Closed")
        with pytest.raises(inv.CaseError):                       # resolving needs disposition + findings
            inv.update(s, iid, "bob", status="Resolved")
        inv.update(s, iid, "bob", status="Resolved", disposition="Benign activity", findings="Payroll run confirmed with employer.")
        inv.update(s, iid, "bob", status="Closed")
        with pytest.raises(inv.CaseError):                       # closed is read-only
            inv.add_note(s, iid, "bob", "late note")
        with pytest.raises(inv.CaseError):
            inv.update(s, iid, "bob", priority="Low")
        hist = inv.history(s, iid)
        fields = [(h["actor"], h["field"], h["old"], h["new"]) for h in hist]
        assert ("alice", "assigned_analyst", None, "bob") in fields and ("bob", "status", "In Review", "Resolved") in fields
        assert hist[0]["action"] == "created" and all(h["at"] for h in hist)
        d = inv.serialize(s, i, True)
        assert d["disposition"] == "Benign activity" and "not a legal finding" in d["disposition_notice"] and len(d["notes"]) == 1


def test_validation_errors(seeded, alert_id):
    with session_scope() as s:
        with pytest.raises(inv.NotFound):
            inv.create_from_alert(s, "AL-NOPE", "x")
        with pytest.raises(inv.NotFound):
            inv.update(s, "INV-NOPE", "x", priority="Low")
        a2 = s.query(m.Alert).filter(m.Alert.dataset_id == seeded["dataset_id"], m.Alert.investigation_status == "Not opened", m.Alert.alert_id != alert_id).first()
        i = inv.create_from_alert(s, a2.alert_id, "x")
        with pytest.raises(inv.CaseError):
            inv.create_from_alert(s, a2.alert_id, "x")           # duplicate open investigation
        with pytest.raises(inv.CaseError):
            inv.update(s, i.investigation_id, "x", priority="Urgent")
        with pytest.raises(inv.CaseError):
            inv.update(s, i.investigation_id, "x", status="In Review", disposition="Not a disposition")
        with pytest.raises(inv.CaseError):
            inv.add_note(s, i.investigation_id, "x", "   ")
        with pytest.raises(inv.CaseError):
            inv.attach_transactions(s, i.investigation_id, ["TX-DOES-NOT-EXIST"], "x")


def test_rerunning_pipeline_does_not_overwrite_human_review(seeded):
    from app.services.pipeline import run_pipeline
    with session_scope() as s:
        a = s.query(m.Alert).filter(m.Alert.dataset_id == seeded["dataset_id"], m.Alert.investigation_status == "Not opened").first()
        iid = inv.create_from_alert(s, a.alert_id, "carol").investigation_id
        inv.update(s, iid, "carol", status="In Review", findings="x")
        aid = a.alert_id
    with session_scope() as s:
        run_pipeline(s, seeded["dataset_id"], with_explanations=False)
    with session_scope() as s:
        assert s.get(m.Alert, aid) is not None and s.get(m.Alert, aid).investigation_status == "In Review"
        assert s.get(m.Investigation, iid).status == "In Review"
