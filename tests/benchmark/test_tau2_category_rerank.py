import json
from pathlib import Path

from benchmark.tau2.scripts.category_rerank import CategoryReranker
from benchmark.tau2.scripts.run_memory_v2_eval import _load_scope_prompt


def _reranker() -> CategoryReranker:
    return CategoryReranker.from_payload(
        {
            "enabled": True,
            "catalog_path": "benchmark/tau2/config/category_catalog.json",
            "apply_nodes": ["before_write_tool_call"],
            "retrieve_limit": 6,
            "inject_limit": 2,
            "mismatch_policy": "keep_positive_match_drop_mismatch",
            "positive_match_required": True,
            "no_match_policy": "skip_injection",
            "search_score_weight": 0.0,
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
    assert diagnostics["decision"] == "soft_reranked_keep_category2_matches"
    assert diagnostics["mismatch_policy"] == "keep_positive_match_drop_mismatch"
    assert diagnostics["positive_match_level"] == "category2"
    assert diagnostics["inject_limit"] == 2
    assert diagnostics["query_category"]["primary_category_id"] == (
        "retail_order_post_shipment_service_request:delivered_order_exchange"
    )
    assert [row["uri"] for row in selected] == [
        "viking://agent/demo/memories/trajectories/delivered_exchange.md"
    ]
    assert trace_rows[0]["selected_for_injection"] is True
    assert trace_rows[0]["query_category1_prompt"] == [
        "retail_order_post_shipment_service_request"
    ]
    assert trace_rows[0]["memory_category1_prompt"] == [
        "retail_order_post_shipment_service_request"
    ]
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


def test_category_rerank_prefers_annotation_sidecar(tmp_path: Path) -> None:
    sidecar = tmp_path / "annotations.jsonl"
    memory_uri = "viking://agent/demo/memories/trajectories/sidecar_exchange.md"
    query_subject = (
        "tau2_query_signature_tau2_retail_pre_write_action_tools_exchange_delivered_order_items"
    )
    rows = [
        {
            "schema_version": "memory_category_annotation.v0",
            "annotation_id": f"query:{query_subject}:abc123",
            "request_id": f"query:{query_subject}:abc123",
            "subject": {
                "subject_type": "query",
                "subject_id": query_subject,
                "domain": "retail",
            },
            "category": {
                "category1": "sidecar_query_family",
                "category2": "sidecar_exact",
                "category_source": "existing_catalog",
                "confidence": 1.0,
            },
        },
        {
            "schema_version": "memory_category_annotation.v0",
            "annotation_id": "memory:sidecar_exchange:abc123",
            "request_id": "memory:sidecar_exchange:abc123",
            "subject": {
                "subject_type": "memory",
                "subject_id": "sidecar_exchange",
                "subject_ref": memory_uri,
                "domain": "retail",
            },
            "category": {
                "category1": "sidecar_query_family",
                "category2": "sidecar_exact",
                "category_source": "llm_prompt",
                "confidence": 1.0,
            },
        },
    ]
    sidecar.write_text("\n".join(json.dumps(row) for row in rows) + "\n")
    reranker = CategoryReranker.from_payload(
        {
            "enabled": True,
            "catalog_path": "benchmark/tau2/config/category_catalog.json",
            "annotation_files": [str(sidecar)],
            "apply_nodes": ["before_write_tool_call"],
            "retrieve_limit": 6,
            "inject_limit": 1,
            "mismatch_policy": "keep_positive_match_drop_mismatch",
            "no_match_policy": "skip_injection",
        },
        repo_root=Path(__file__).resolve().parents[2],
    )

    selected, trace_rows, diagnostics = reranker.select(
        domain="retail",
        query="Before executing write-like tool call(s): exchange_delivered_order_items({})",
        rows=[
            {
                "uri": memory_uri,
                "score": 0.1,
                "_text": "This text would otherwise look like cancel_pending_order.",
            },
            {
                "uri": "viking://agent/demo/memories/trajectories/catalog_exchange.md",
                "score": 0.9,
                "_text": "Use exchange_delivered_order_items for a delivered order exchange.",
            },
        ],
        decision_node="before_write_tool_call",
        base_limit=2,
    )

    assert diagnostics["annotation_sidecar"]["row_count"] == 2
    assert diagnostics["query_category"]["annotation_id"] == f"query:{query_subject}:abc123"
    assert [row["uri"] for row in selected] == [memory_uri]
    assert trace_rows[0]["memory_category1_prompt"] == "sidecar_query_family"
    assert trace_rows[0]["query_category2_prompt"] == "sidecar_exact"
    assert trace_rows[1]["selected_for_injection"] is False


def test_scope_prompt_loads_domain_file(tmp_path: Path) -> None:
    prompt = tmp_path / "retail_scope.md"
    prompt.write_text("<custom_memory_applicability_guard>same order</custom_memory_applicability_guard>")

    text, summary = _load_scope_prompt(
        {"enabled": True, "domain_files": {"retail": str(prompt)}},
        domain="retail",
        repo_root=Path(__file__).resolve().parents[2],
    )

    assert "same order" in text
    assert summary["enabled"] is True
    assert summary["loaded"] is True
    assert summary["loaded_files"] == [str(prompt)]


def test_scope_prompt_skips_unconfigured_domain(tmp_path: Path) -> None:
    prompt = tmp_path / "retail_scope.md"
    prompt.write_text("retail only")

    text, summary = _load_scope_prompt(
        {"enabled": True, "domain_files": {"retail": str(prompt)}},
        domain="airline",
        repo_root=Path(__file__).resolve().parents[2],
    )

    assert text == ""
    assert summary["loaded"] is False
    assert summary["skipped_reason"] == "no_domain_scope_prompt"
