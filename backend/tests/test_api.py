import json

import pytest
from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.main import app


@pytest.fixture(scope="module")
def client(seeded):
    with TestClient(app) as c:
        yield c


@pytest.fixture(scope="module")
def ds(seeded):
    return seeded["dataset_id"]


def test_health_and_docs(client):
    r = client.get("/api/health")
    assert r.status_code == 200 and r.json()["status"] == "ok" and "prototype" in r.json()["disclaimer"]
    spec = client.get("/openapi.json").json()
    assert "/api/alerts" in spec["paths"] and "/api/graph/trace" in spec["paths"]


def test_datasets_and_stats_do_not_expose_labels(client, ds):
    items = client.get("/api/datasets").json()["items"]
    assert any(d["dataset_id"] == ds for d in items)
    st = client.get(f"/api/datasets/{ds}/stats").json()
    assert st["n_transactions"] > 1000 and "SYNTHETIC" in st["provenance"]
    blob = json.dumps(client.get("/api/overview", params={"dataset_id": ds}).json()) + json.dumps(items)
    for forbidden in ("scenario_id", "is_suspicious_sim", "template_variant"):
        assert forbidden not in blob
    t = client.get("/api/transactions", params={"dataset_id": ds, "page_size": 5}).json()["items"][0]
    assert "scenario_id" not in t and "is_suspicious_sim" not in t


def test_transaction_search_filters_sort_pagination(client, ds):
    p1 = client.get("/api/transactions", params={"dataset_id": ds, "page_size": 20, "page": 1, "sort": "amount", "order": "desc"}).json()
    p2 = client.get("/api/transactions", params={"dataset_id": ds, "page_size": 20, "page": 2, "sort": "amount", "order": "desc"}).json()
    assert p1["total"] == p2["total"] and len(p1["items"]) == 20
    amts = [t["amount"] for t in p1["items"]] + [t["amount"] for t in p2["items"]]
    assert amts == sorted(amts, reverse=True)
    acc = p1["items"][0]["source_account_id"]
    out = client.get("/api/transactions", params={"dataset_id": ds, "account": acc, "direction": "out", "page_size": 200}).json()
    assert out["total"] > 0 and all(t["source_account_id"] == acc for t in out["items"])
    big = client.get("/api/transactions", params={"dataset_id": ds, "min_amount": 100000, "page_size": 50}).json()
    assert all(t["amount"] >= 100000 for t in big["items"])
    tid = p1["items"][0]["transaction_id"]
    assert client.get("/api/transactions", params={"dataset_id": ds, "q": tid}).json()["total"] == 1
    d = client.get(f"/api/transactions/{tid}", params={"dataset_id": ds}).json()
    assert d["transaction_id"] == tid
    assert client.get("/api/transactions/NOPE", params={"dataset_id": ds}).status_code == 404


def test_validation_errors_and_limits(client, ds):
    assert client.get("/api/transactions", params={"dataset_id": ds, "page_size": 100000}).status_code == 422
    assert client.get("/api/transactions", params={"dataset_id": ds, "sort": "; DROP TABLE x"}).status_code == 422
    assert client.get("/api/transactions", params={"dataset_id": ds, "min_amount": -5}).status_code == 422
    assert client.get("/api/transactions", params={"dataset_id": "DS-nope"}).status_code == 404
    assert client.get("/api/graph/trace", params={"account_id": "x", "dataset_id": ds, "max_hops": 50}).status_code == 422
    assert client.get("/api/graph/subgraph", params={"account_id": "AC-NOT-THERE", "dataset_id": ds}).status_code == 404
    inj = "x" + chr(39) + " OR " + chr(39) + "1" + chr(39) + "=" + chr(39) + "1"
    r = client.get("/api/transactions", params={"dataset_id": ds, "q": inj})
    assert r.status_code == 200 and r.json()["total"] == 0          # parameterised


def test_alerts_listing_detail_and_evidence(client, ds):
    r = client.get("/api/alerts", params={"dataset_id": ds, "page_size": 10}).json()
    assert r["total"] > 0 and r["items"][0]["risk_score"] >= r["items"][-1]["risk_score"]
    high = client.get("/api/alerts", params={"dataset_id": ds, "band": "Critical"}).json()
    assert all(a["risk_band"] == "Critical" for a in high["items"])
    a = r["items"][0]
    d = client.get(f"/api/alerts/{a['alert_id']}").json()
    assert d["transactions"] and d["detail"]["recommended_steps"] and d["detail"]["limitations"] and d["model_version"] and d["feature_version"]
    assert d["detail"]["risk_breakdown"]["method"] and d["detail"]["evidence_timestamps"]["first"]
    assert d["detail"]["exposure"]["gross_value"] >= d["detail"]["exposure"]["exposed_value"] - 1e-6
    assert "not a finding of wrongdoing" in d["disclaimer"]
    ex = client.get(f"/api/alerts/{a['alert_id']}/explanation").json()
    assert ex["risk_breakdown"] and ex["caveats"]
    assert client.get("/api/alerts/AL-NOPE").status_code == 404
    assert client.get("/api/alerts", params={"dataset_id": ds, "sort": "bogus"}).status_code == 422


def test_alert_evidence_matches_stored_transactions(client, ds):
    a = client.get("/api/alerts", params={"dataset_id": ds, "page_size": 1}).json()["items"][0]
    d = client.get(f"/api/alerts/{a['alert_id']}").json()
    if d["n_transactions"] <= 500:
        assert sum(t["amount"] for t in d["transactions"]) == pytest.approx(d["gross_value"], rel=1e-6)


def test_network_endpoints(client, ds):
    top = client.get("/api/alerts", params={"dataset_id": ds, "page_size": 1}).json()["items"][0]
    acct = top["entity_id"]
    g = client.get("/api/graph/subgraph", params={"account_id": acct, "dataset_id": ds, "hops": 2, "max_nodes": 60}).json()
    assert 1 <= len(g["nodes"]) <= 60 and g["edges"] and {"source", "target", "total", "txn_ids"} <= set(g["edges"][0])
    ids = {n["id"] for n in g["nodes"]}
    assert all(e["source"] in ids and e["target"] in ids for e in g["edges"])
    e = g["edges"][0]
    et = client.get("/api/graph/edge", params={"source": e["source"], "target": e["target"], "dataset_id": ds}).json()
    assert et["total"] >= 1
    tr = client.get("/api/graph/trace", params={"account_id": acct, "dataset_id": ds, "direction": "forward", "max_hops": 3}).json()
    assert tr["approximate"] is True and "Approximate" in tr["note"]
    bt = client.get("/api/graph/trace", params={"account_id": acct, "dataset_id": ds, "direction": "backward"}).json()
    assert bt["direction"] == "backward"
    cy = client.get("/api/graph/cycles", params={"dataset_id": ds}).json()
    assert cy["total"] >= 1 and cy["items"][0]["accounts"][0] == cy["items"][0]["accounts"][-1]
    cm = client.get("/api/graph/communities", params={"dataset_id": ds, "min_size": 3}).json()
    assert cm["items"] and "not evidence of a criminal organisation" in cm["note"]
    acc = client.get(f"/api/accounts/{acct}", params={"dataset_id": ds}).json()
    assert acc["stats"]["counterparties"] > 0
    assert client.get("/api/accounts", params={"dataset_id": ds, "q": acct[:5]}).json()["items"]


def test_investigation_flow_via_api(client, ds):
    alerts = client.get("/api/alerts", params={"dataset_id": ds, "status": "Not opened", "page_size": 5}).json()["items"]
    aid = alerts[0]["alert_id"]
    h = {"X-Analyst": "dana"}
    r = client.post("/api/investigations", json={"alert_id": aid, "priority": "High"}, headers=h)
    assert r.status_code == 201
    iid = r.json()["investigation_id"]
    assert client.post("/api/investigations", json={"alert_id": aid}, headers=h).status_code == 409
    assert client.patch(f"/api/investigations/{iid}", json={"status": "Closed"}, headers=h).status_code == 409
    assert client.patch(f"/api/investigations/{iid}", json={"status": "In Review", "assigned_analyst": "erin"}, headers=h).status_code == 200
    n = client.post(f"/api/investigations/{iid}/notes", json={"body": "checked counterparties"}, headers=h)
    assert n.status_code == 201 and n.json()["author"] == "dana"
    assert client.post(f"/api/investigations/{iid}/notes", json={"body": ""}, headers=h).status_code == 422
    t = client.get(f"/api/alerts/{aid}").json()["transactions"][0]["transaction_id"]
    assert client.post(f"/api/investigations/{iid}/transactions", json={"transaction_ids": [t]}, headers=h).status_code == 201
    got = client.get(f"/api/investigations/{iid}").json()
    assert got["status"] == "In Review" and got["assigned_analyst"] == "erin" and got["notes"][0]["body"] == "checked counterparties"
    hist = client.get(f"/api/investigations/{iid}/history").json()["items"]
    assert {"created", "updated", "note_added"} <= {x["action"] for x in hist} and all(x["actor"] for x in hist)
    assert client.get("/api/investigations/INV-NOPE").status_code == 404
    lst = client.get("/api/investigations", params={"dataset_id": ds, "status": "In Review"}).json()
    assert any(i["investigation_id"] == iid for i in lst["items"])
    assert client.get(f"/api/alerts/{aid}").json()["investigation_status"] == "In Review"


def test_models_monitoring_overview_insights(client, ds):
    am = client.get("/api/models/active").json()
    assert am["is_active"] and am["evaluation_metrics"]["split"]["train_end"]
    assert client.get(f"/api/models/{am['model_run_id']}/metrics").status_code == 200
    mon = client.get("/api/monitoring/summary", params={"dataset_id": ds}).json()
    assert mon["runs"][0]["metrics"]["n_scored"] > 0 and "does not show accuracy has fallen" in " ".join(mon["notes"])
    ov = client.get("/api/overview", params={"dataset_id": ds}).json()
    assert ov["kpis"]["transactions_analyzed"] > 0 and ov["definitions"]["potentially_exposed_value"]
    ins = client.get("/api/insights", params={"dataset_id": ds}).json()
    assert ins["items"] and "no external language model" in ins["method"]


def test_import_endpoints_and_upload_safety(client):
    csv = "txn_id,sender,receiver,timestamp,amount\nA1,X1,X2,2026-01-01T10:00:00Z,100\nA2,X2,X3,2026-01-01T11:00:00Z,-5\nA3,X3,X1,bad,10\n"
    insp = client.post("/api/imports/inspect", files={"file": ("t.csv", csv, "text/csv")}).json()
    assert insp["suggested_mapping"]["source_account_id"] == "sender" and insp["n_rows"] == 3
    r = client.post("/api/imports", files={"file": ("t.csv", csv, "text/csv")}, data={"name": "tiny"})
    assert r.status_code == 200
    rep = r.json()
    assert rep["report"]["rows_accepted"] == 1 and rep["report"]["rows_rejected"] == 2 and rep["has_error_report"]
    err = client.get(f"/api/imports/{rep['report_id']}/errors")
    assert err.status_code == 200 and "invalid_amount" in err.text and "invalid_timestamp" in err.text
    assert client.post("/api/imports", files={"file": ("t.exe", csv, "application/octet-stream")}).status_code == 415
    assert client.post("/api/imports", files={"file": ("t.csv", csv, "text/csv")}, data={"mapping": "[1,2]"}).status_code == 422
    empty = client.post("/api/imports", files={"file": ("e.csv", "a,b\n", "text/csv")}).json()
    assert empty["status"] == "failed"
    cfg = get_settings()
    old = cfg.max_upload_mb
    cfg.max_upload_mb = 0
    try:
        assert client.post("/api/imports", files={"file": ("t.csv", csv, "text/csv")}).status_code == 413
    finally:
        cfg.max_upload_mb = old
    if rep["dataset_id"]:
        assert client.delete(f"/api/datasets/{rep['dataset_id']}").status_code == 200


def test_api_key_enforced_when_configured(client):
    cfg = get_settings()
    cfg.api_key = "s3cret"
    try:
        assert client.get("/api/alerts").status_code == 401
        assert client.get("/api/alerts", headers={"X-API-Key": "wrong"}).status_code == 401
        assert client.get("/api/alerts", headers={"X-API-Key": "s3cret"}).status_code == 200
        assert client.get("/api/health").status_code == 200
    finally:
        cfg.api_key = None
