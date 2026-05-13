from pathlib import Path

from benchmark.tau2.scripts.category_rerank import CategoryReranker


def _reranker() -> CategoryReranker:
    return CategoryReranker.from_payload(
        {
            "enabled": True,
            "catalog_path": "benchmark/tau2/config/category_catalog.json",
            "apply_nodes": ["before_write_tool_call"],
            "retrieve_limit": 6,
            "inject_limit": 4,
            "positive_match_required": True,
            "no_match_policy": "skip_injection",
        },
        repo_root=Path(__file__).resolve().parents[2],
    )


def test_category_rerank_keeps_positive_category_match() -> None:
    rows = [
        {
            "uri": "viking://agent/demo/memories/trajectories/delivered_exchange.md",
            "score": 0.25,
            "_text": "Use exchange_delivered_order_items for a delivered order exchange replacement.",
        },
        {
            "uri": "viking://agent/demo/memories/trajectories/pending_cancel.md",
            "score": 0.99,
            "_text": "Use cancel_pending_order for a pending order cancellation.",
        },
    ]

    selected, trace_rows, diagnostics = _reranker().select(
        domain="retail",
        query="I need to exchange_delivered_order_items for a delivered order replacement.",
        rows=rows,
        decision_node="before_write_tool_call",
        base_limit=4,
    )

    assert diagnostics["applied"] is True
    assert diagnostics["decision"] == "positive_category2_match"
    assert diagnostics["positive_match_level"] == "category2"
    assert diagnostics["query_category"]["primary_category_id"] == (
        "retail_order_post_shipment_service_request:delivered_order_exchange"
    )
    assert [row["uri"] for row in selected] == [
        "viking://agent/demo/memories/trajectories/delivered_exchange.md"
    ]
    assert trace_rows[0]["selected_for_injection"] is True
    assert trace_rows[1]["selected_for_injection"] is False
    assert trace_rows[1]["skipped_reason"] == "category_rerank"


def test_category_rerank_skips_non_target_node() -> None:
    rows = [
        {"uri": "viking://agent/demo/memories/trajectories/one.md", "score": 0.2},
        {"uri": "viking://agent/demo/memories/trajectories/two.md", "score": 0.1},
    ]

    selected, trace_rows, diagnostics = _reranker().select(
        domain="retail",
        query="exchange_delivered_order_items",
        rows=rows,
        decision_node="first_user",
        base_limit=1,
    )

    assert diagnostics["applied"] is False
    assert diagnostics["decision"] == "node_not_enabled"
    assert [row["uri"] for row in selected] == [
        "viking://agent/demo/memories/trajectories/one.md"
    ]
    assert trace_rows[0]["selected_for_injection"] is True
    assert trace_rows[1]["selected_for_injection"] is False
