import pandas as pd
import pytest

from app.graph import analytics as GA
from app.graph import detectors as D
from app.graph.index import TxnIndex, build_graph, ego_subgraph

T0 = pd.Timestamp("2026-03-01 08:00")


def mk(rows):
    """rows: (src, dst, hours_offset, amount)"""
    df = pd.DataFrame([(f"T{i}", s, d, T0 + pd.Timedelta(hours=h), a, "P2P_TRANSFER", "UPI") for i, (s, d, h, a) in enumerate(rows)],
                      columns=["transaction_id", "source_account_id", "destination_account_id", "transaction_timestamp", "amount", "transaction_type", "channel"])
    return df


def win(idx):
    return idx.ts[0] - 1, idx.ts[-1] + 1


def test_edge_direction_aggregation_and_parallel_transactions():
    df = mk([("A", "B", 0, 100), ("A", "B", 1, 300), ("B", "A", 2, 50), ("A", "C", 3, 10)])
    G = build_graph(df)
    assert G.has_edge("A", "B") and G.has_edge("B", "A") and not G.has_edge("C", "A")
    e = G["A"]["B"]
    assert e["count"] == 2 and e["total"] == 400 and e["mean"] == 200 and e["min"] == 100 and e["max"] == 300
    assert e["txn_ids"] == ["T0", "T1"] and e["first"] == T0 and e["last"] == T0 + pd.Timedelta(hours=1)
    assert G["B"]["A"]["count"] == 1


def test_time_window_and_min_amount_filtering():
    df = mk([("A", "B", 0, 100), ("A", "B", 10, 300), ("C", "D", 20, 5)])
    G = build_graph(df, t_start=T0 + pd.Timedelta(hours=5), t_end=T0 + pd.Timedelta(hours=15))
    assert list(G.edges) == [("A", "B")] and G["A"]["B"]["count"] == 1
    assert list(build_graph(df, min_amount=50).edges) == [("A", "B")]
    assert build_graph(df.iloc[0:0]).number_of_nodes() == 0


def test_cycle_detected_with_evidence_and_trivial_excluded():
    df = mk([("A", "B", 0, 10000), ("B", "C", 2, 9500), ("C", "A", 4, 9000),   # 3-cycle
             ("X", "Y", 0, 10000), ("Y", "X", 1, 9900)])                       # trivial 2-cycle
    idx = TxnIndex(df)
    cyc = D.detect_cycles(idx, *win(idx))
    assert len(cyc) == 1
    c = cyc[0]
    assert c.txn_ids == ["T0", "T1", "T2"] and c.path == ["A", "B", "C", "A"]
    assert c.evidence["span_hours"] == 4.0 and c.evidence["retention"] == pytest.approx(0.9)
    assert "X" not in c.accounts


def test_no_cycle_in_acyclic_and_disconnected_graphs():
    df = mk([("A", "B", 0, 10000), ("B", "C", 1, 9900), ("C", "D", 2, 9800), ("P", "Q", 0, 7000), ("R", "S", 1, 7000)])
    idx = TxnIndex(df)
    assert D.detect_cycles(idx, *win(idx)) == []


def test_cycle_respects_time_window_and_retention_and_length():
    late = mk([("A", "B", 0, 10000), ("B", "C", 2, 9500), ("C", "A", 100, 9000)])          # closes after 100h > 72h
    assert D.detect_cycles(TxnIndex(late), *win(TxnIndex(late))) == []
    leak = mk([("A", "B", 0, 10000), ("B", "C", 2, 9500), ("C", "A", 4, 2000)])           # 80% value disappears
    assert D.detect_cycles(TxnIndex(leak), *win(TxnIndex(leak))) == []
    long = mk([("A", "B", 0, 10000), ("B", "C", 1, 9900), ("C", "D", 2, 9800), ("D", "E", 3, 9700), ("E", "F", 4, 9600), ("F", "A", 5, 9500)])
    assert D.detect_cycles(TxnIndex(long), *win(TxnIndex(long)), max_len=5) == []        # 6 hops > max_len
    assert len(D.detect_cycles(TxnIndex(long), *win(TxnIndex(long)), max_len=6)) == 1


def test_cycle_search_terminates_on_dense_cyclic_graph():
    rows = [(f"N{i}", f"N{j}", (i * 7 + j) % 40, 10000) for i in range(25) for j in range(25) if i != j]
    idx = TxnIndex(mk(rows))
    res = D.detect_cycles(idx, *win(idx), max_results=50, timeout_s=5)
    assert len(res) <= 50


def test_layered_chain_vs_benign_fanout():
    chain = mk([("A", "B", 0, 90000), ("B", "C", 1, 86000), ("C", "D", 2, 82000), ("D", "E", 3.5, 79000)])
    idx = TxnIndex(chain)
    res = D.detect_chains(idx, *win(idx))
    assert len(res) == 1 and res[0].path == ["A", "B", "C", "D", "E"]
    payroll = mk([("CO", f"E{i}", i * 0.01, 30000) for i in range(30)])
    idx2 = TxnIndex(payroll)
    assert D.detect_chains(idx2, *win(idx2)) == []


def test_shared_beneficiary_detection_and_baseline_awareness():
    burst = mk([(f"S{i}", "BEN", i * 0.5, 12000) for i in range(8)])
    idx = TxnIndex(burst)
    res = [d for d in D.detect_fan(idx, *win(idx), "in") if d.entity_id == "BEN"]
    assert len(res) == 1 and res[0].evidence["unique_counterparties"] == 8 and res[0].evidence["concentration_vs_baseline"] >= 8
    spread = mk([(f"S{i}", "BEN", i * 100, 12000) for i in range(8)])        # same senders over 33 days: no 48h concentration
    idx2 = TxnIndex(spread)
    assert D.detect_fan(idx2, *win(idx2), "in") == []
    # habitual hub: daily 8 distinct customers for 40 days then the same level in the window -> low strength vs. history
    hub = mk([(f"C{d}_{i}", "SHOP", d * 24 + i, 500) for d in range(40) for i in range(8)])
    idx3 = TxnIndex(hub)
    ws = idx3.ts[0] + 35 * 86400
    res3 = D.detect_fan(idx3, ws, idx3.ts[-1] + 1, "in", win_h=48, min_k=5)
    assert res3 and max(r.evidence["concentration_vs_baseline"] for r in res3) < 3


def test_structuring_like_requires_repeated_near_threshold_payments():
    rows = [("A", "B", i * 20, 49000 + i * 100) for i in range(5)] + [("C", "D", 0, 49500)]
    idx = TxnIndex(mk(rows))
    res = D.detect_structuring(idx, *win(idx), threshold=50000)
    assert [r.entity_id for r in res] == ["A"] and res[0].evidence["n_near_threshold"] == 5


def test_forward_and_backward_trace_with_limits():
    df = mk([("A", "B", 0, 1000), ("B", "C", 1, 950), ("C", "D", 2, 900), ("D", "E", 3, 850), ("B", "X", 1.5, 5), ("Z", "A", -5, 1000)])
    idx = TxnIndex(df)
    f = GA.trace(idx, "A", "forward", max_hops=4)
    assert f["approximate"] and f["paths"][0]["accounts"] == ["A", "B", "C", "D", "E"]
    assert {tuple(p["accounts"]) for p in f["paths"]} >= {("A", "B", "X")}
    assert max(p["hops"] for p in GA.trace(idx, "A", "forward", max_hops=2)["paths"]) == 2          # hop limit
    assert all("X" not in p["accounts"] for p in GA.trace(idx, "A", "forward", min_amount=100)["paths"])  # amount filter
    b = GA.trace(idx, "E", "backward", max_hops=5)
    assert b["paths"][0]["accounts"] == ["Z", "A", "B", "C", "D", "E"]
    assert GA.trace(idx, "A", "forward", t1=T0.value / 1e9 + 0.5 * 3600)["paths"][0]["accounts"] == ["A", "B"]  # time window
    assert len(GA.trace(idx, "A", "forward", max_paths=1)["paths"]) == 1
    # temporal consistency: B->C happens BEFORE A->B -> must not be chained
    wrong = mk([("B", "C", 0, 500), ("A", "B", 5, 500)])
    assert max(p["hops"] for p in GA.trace(TxnIndex(wrong), "A", "forward")["paths"]) == 1
    assert GA.trace(idx, "UNKNOWN", "forward")["paths"] == []


def test_communities_separate_disconnected_groups():
    rows = [(a, b, i, 1000) for i, (a, b) in enumerate([(f"G1_{x}", f"G1_{y}") for x in range(5) for y in range(5) if x != y])]
    rows += [(a, b, i, 1000) for i, (a, b) in enumerate([(f"G2_{x}", f"G2_{y}") for x in range(5) for y in range(5) if x != y])]
    df = mk(rows)
    comms = GA.detect_communities(build_graph(df), min_size=3)
    assert len(comms) == 2 and {frozenset(c) for c in comms} == {frozenset(f"G1_{x}" for x in range(5)), frozenset(f"G2_{x}" for x in range(5))}
    summ = GA.community_summaries(df, comms)
    assert summ[0]["internal_share"] == 1.0 and summ[0]["n_accounts"] == 5


def test_ego_subgraph_limits_and_truncation():
    rows = [("HUB", f"N{i}", i * 0.01, 100 + i) for i in range(300)]
    idx = TxnIndex(mk(rows))
    g = ego_subgraph(idx, "HUB", hops=1, max_nodes=50, per_node=30)
    assert len(g["nodes"]) <= 50 and g["truncated"] and len(g["edges"]) <= 1500
    small = ego_subgraph(TxnIndex(mk([("A", "B", 0, 1), ("B", "C", 1, 1), ("C", "D", 2, 1)])), "A", hops=2)
    assert {n["id"] for n in small["nodes"]} == {"A", "B", "C"} and not small["truncated"]


def test_node_features_values():
    df = mk([("A", "B", 0, 100), ("B", "A", 1, 50), ("A", "C", 2, 200), ("D", "A", 3, 10)])
    f = GA.node_features(build_graph(df)).set_index("account_id")
    assert f.loc["A", "out_degree"] == 2 and f.loc["A", "in_degree"] == 2 and f.loc["A", "out_value"] == 300
    assert f.loc["A", "reciprocity"] == 0.5 and f.loc["C", "reciprocity"] == 0
    assert f["pagerank"].sum() == pytest.approx(1.0)


def test_inference_code_does_not_touch_ground_truth():
    import pathlib
    root = pathlib.Path(__file__).parents[1] / "app"
    for sub in ("graph", "risk", "features"):
        for p in (root / sub).glob("*.py"):
            assert "gt_txn_labels" not in p.read_text() and "GtTxnLabel" not in p.read_text() and "load_labels" not in p.read_text(), p
    for p in (root / "api").glob("*.py"):
        src = p.read_text()
        assert "GtTxnLabel" not in src and "GtScenario" not in src and "load_labels" not in src, p   # no label-reading endpoints
