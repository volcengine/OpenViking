import json
from pathlib import Path
from types import SimpleNamespace

from benchmark.tau2.scripts.category_rerank import CategoryReranker
from benchmark.tau2.scripts.run_eval import (
    _cell_artifacts,
    _cell_metrics,
    _summarize,
    _tau2_command,
)
from benchmark.tau2.scripts.tau2_common import load_config
from benchmark.tau2.scripts.run_memory_v2_eval import (
    _load_scope_prompt,
    _probe_corpus,
    _runtime_evidence_status,
    _trace_category_summary,
)


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


def _has_key_fragment(value: object, fragment: str) -> bool:
    if isinstance(value, dict):
        return any(
            fragment in str(key).lower() or _has_key_fragment(item, fragment)
            for key, item in value.items()
        )
    if isinstance(value, list):
        return any(_has_key_fragment(item, fragment) for item in value)
    return False


def test_category_rerank_config_matches_s89_alignment_shape() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    config = load_config(repo_root / "benchmark/tau2/config/category_rerank.yaml")
    strategies = {row["id"]: row for row in config["strategies"]}
    category_strategy = strategies["memory_v2_trajectory_category_prewrite"]

    assert config["benchmark"]["reasoning_effort"] == "high"
    assert config["openviking"]["retrieval_top_k"] == 4
    assert category_strategy["memory_backend"] == "openviking"
    assert category_strategy["train_memory_mode"] == "experience_only"
    assert category_strategy["search_memory_type"] == "trajectories"
    assert category_strategy["retrieval_mode"] == "first_user_prewrite"
    assert category_strategy["corpus_id"] == "memory_v2_trajectory_view"

    category_rerank = category_strategy["category_rerank"]
    assert category_rerank["enabled"] is True
    assert category_rerank["apply_nodes"] == ["before_write_tool_call"]
    assert category_rerank["retrieve_limit"] == 6
    assert category_rerank["inject_limit"] == 2
    assert category_rerank["mismatch_policy"] == "keep_positive_match_drop_mismatch"
    assert category_rerank["positive_match_required"] is True
    assert category_rerank["no_match_policy"] == "skip_injection"
    assert category_rerank["search_score_weight"] == 0.0

    scope_prompt = category_strategy["scope_prompt"]
    assert scope_prompt["enabled"] is True
    assert scope_prompt["injection_point"] == "system_prompt"
    assert scope_prompt["domain_files"] == {
        "retail": "benchmark/tau2/config/scope_prompts/retail_same_order_variant_guard.md"
    }

    assert "memory_v2_trajectory_prewrite" in strategies
    assert not _has_key_fragment(category_strategy, "annotation")
    assert not _has_key_fragment(category_strategy, "sidecar")


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


def test_trace_category_summary_counts_runtime_sources(tmp_path: Path) -> None:
    trace = tmp_path / "retrieval_trace.jsonl"
    rows = [
        {
            "decision_node": "static_scope_prompt",
            "retrieval_action_taken": "scope_prompt_static_injection",
            "injected": True,
        },
        {
            "decision_node": "before_write_tool_call",
            "retrieval_action_taken": "retrieve_and_inject",
            "tool_calls": [{"name": "exchange_delivered_order_items"}],
            "category_rerank": {
                "enabled": True,
                "applied": True,
                "decision": "soft_reranked_keep_category2_matches",
                "query_category": {
                    "matched": True,
                    "category_source": "tau2_category_catalog_keyword_match",
                },
            },
            "matches": [
                {
                    "uri": "viking://agent/example/memories/trajectories/.overview.md",
                    "selected_for_injection": True,
                    "memory_category_source_prompt": "tau2_category_catalog_keyword_match",
                    "memory_category1_prompt": ["retail_order_post_shipment_service_request"],
                    "memory_category2_prompt": ["delivered_order_exchange"],
                    "category2_match": True,
                },
                {
                    "uri": "viking://agent/example/memories/trajectories/concrete.md",
                    "selected_for_injection": False,
                    "category_rerank_reasons": ["missing_memory_category"],
                },
                {
                    "uri": "viking://agent/example/memories/trajectories/pending_cancel.md",
                    "selected_for_injection": False,
                    "memory_category_source_prompt": "tau2_category_catalog_keyword_match",
                    "memory_category1_prompt": ["retail_order_cancellation"],
                    "memory_category2_prompt": ["pending_order_cancel"],
                    "category1_match": False,
                    "category2_match": False,
                },
            ],
        },
    ]
    trace.write_text(
        "\n".join(json.dumps(row, sort_keys=True) for row in rows) + "\n"
    )

    summary = _trace_category_summary(trace)

    assert summary["trace_present"] is True
    assert summary["decision_nodes"]["before_write_tool_call"] == 1
    assert summary["category_decisions"]["soft_reranked_keep_category2_matches"] == 1
    assert summary["query_category_sources"]["tau2_category_catalog_keyword_match"] == 1
    assert summary["selected_memory_category_sources"]["tau2_category_catalog_keyword_match"] == 1
    assert summary["tool_calls"]["exchange_delivered_order_items"] == 1
    assert summary["rates"]["memory_category_candidate_coverage"] == 2 / 3
    assert summary["rates"]["selected_memory_category_coverage"] == 1.0
    assert summary["rates"]["memory_category_match_coverage"] == 1 / 3
    assert summary["rates"]["selected_memory_category_match_coverage"] == 1.0
    assert summary["counts"]["aggregate_memory_candidate_count"] == 1
    assert summary["counts"]["concrete_memory_candidate_count"] == 2
    assert summary["counts"]["memory_category_present_count"] == 2
    assert summary["counts"]["memory_category_matched_count"] == 1
    assert summary["rates"]["concrete_memory_candidate_rate"] == 2 / 3
    assert summary["rates"]["selected_concrete_memory_rate"] == 0.0


def test_runtime_evidence_marks_aggregate_only_category_diagnostic() -> None:
    evidence = _runtime_evidence_status(
        category_rerank={"enabled": True},
        corpus_probe={
            "match_count": 1,
            "aggregate_match_count": 1,
            "concrete_match_count": 0,
        },
        retrieval_trace_summary={
            "trace_present": True,
            "counts": {
                "category_applied_event_count": 1,
                "query_category_matched_event_count": 1,
                "memory_category_present_count": 1,
                "memory_category_matched_count": 0,
            },
            "rates": {
                "concrete_memory_candidate_rate": 0.0,
                "selected_positive_category_match_rate": 0.0,
            },
        },
    )

    assert evidence["status"] == "diagnostic"
    assert "aggregate_only_corpus_probe" in evidence["reasons"]
    assert "no_concrete_corpus_probe_matches" in evidence["reasons"]
    assert "no_concrete_memory_candidates" in evidence["reasons"]
    assert "no_matched_memory_categories" in evidence["reasons"]
    assert "no_selected_positive_category_match" in evidence["reasons"]


def test_runtime_evidence_requires_applied_category_events() -> None:
    evidence = _runtime_evidence_status(
        category_rerank={"enabled": True},
        corpus_probe={
            "match_count": 1,
            "aggregate_match_count": 0,
            "concrete_match_count": 1,
        },
        retrieval_trace_summary={
            "trace_present": True,
            "category_event_count": 1,
            "counts": {
                "category_enabled_event_count": 1,
                "category_applied_event_count": 0,
            },
            "rates": {
                "concrete_memory_candidate_rate": 1.0,
                "selected_positive_category_match_rate": 1.0,
            },
        },
    )

    assert evidence["status"] == "diagnostic"
    assert "no_category_rerank_applied_events" in evidence["reasons"]


def test_runtime_evidence_requires_query_category_coverage() -> None:
    evidence = _runtime_evidence_status(
        category_rerank={"enabled": True},
        corpus_probe={
            "match_count": 1,
            "aggregate_match_count": 0,
            "concrete_match_count": 1,
        },
        retrieval_trace_summary={
            "trace_present": True,
            "category_event_count": 1,
            "counts": {
                "category_applied_event_count": 1,
                "query_category_matched_event_count": 0,
                "memory_category_present_count": 1,
                "memory_category_matched_count": 1,
            },
            "rates": {
                "concrete_memory_candidate_rate": 1.0,
                "selected_positive_category_match_rate": 1.0,
            },
        },
    )

    assert evidence["status"] == "diagnostic"
    assert "no_query_category_coverage" in evidence["reasons"]


def test_runtime_evidence_accepts_valid_category_runtime_coverage() -> None:
    evidence = _runtime_evidence_status(
        category_rerank={"enabled": True},
        corpus_probe={
            "match_count": 2,
            "aggregate_match_count": 0,
            "concrete_match_count": 2,
        },
        retrieval_trace_summary={
            "trace_present": True,
            "category_event_count": 1,
            "counts": {
                "category_applied_event_count": 1,
                "query_category_matched_event_count": 1,
                "memory_category_present_count": 2,
                "memory_category_matched_count": 1,
            },
            "rates": {
                "concrete_memory_candidate_rate": 1.0,
                "selected_positive_category_match_rate": 1.0,
            },
        },
    )

    assert evidence == {"status": "valid", "reasons": []}


def test_scoreboard_excludes_diagnostic_runtime_evidence() -> None:
    scoreboard = _summarize(
        [
            {
                "domain": "airline",
                "strategy_id": "memory_v2_trajectory_category_prewrite",
                "metrics": {
                    "simulation_count": 1,
                    "avg_reward": 1.0,
                    "db_match_rate": 1.0,
                },
                "runtime_evidence": {
                    "status": "diagnostic",
                    "reasons": [
                        "no_concrete_memory_candidates",
                        "no_query_category_coverage",
                    ],
                },
            },
            {
                "domain": "airline",
                "strategy_id": "memory_v2_trajectory_category_prewrite",
                "metrics": {
                    "simulation_count": 1,
                    "avg_reward": 0.5,
                    "db_match_rate": 0.0,
                },
                "runtime_evidence": {"status": "valid", "reasons": []},
            },
        ]
    )

    domain = scoreboard["strategies"]["memory_v2_trajectory_category_prewrite"][
        "domains"
    ]["airline"]
    assert domain["completed_cell_count"] == 2
    assert domain["valid_completed_cell_count"] == 1
    assert domain["diagnostic_cell_count"] == 1
    assert domain["diagnostic_reason_counts"] == {
        "no_concrete_memory_candidates": 1,
        "no_query_category_coverage": 1,
    }
    assert domain["diagnostic_simulation_count"] == 1
    assert domain["simulation_count"] == 1
    assert domain["avg_reward"] == 0.5
    assert domain["db_match_rate"] == 0.0


def test_no_memory_strategy_uses_wrapper_command(tmp_path: Path) -> None:
    config = {
        "benchmark": {
            "eval_split_name": "test",
            "max_steps": 7,
            "task_max_concurrency": 2,
            "agent": "llm_agent",
            "user": "user_simulator",
            "reasoning_effort": "high",
        },
        "model": {
            "agent_llm": "agent-model",
            "user_llm": "user-model",
        },
        "paths": {
            "tau2_repo": str(tmp_path / "tau2-bench"),
            "output_dir": str(tmp_path / "result"),
        },
    }

    command = _tau2_command(
        config,
        domain="airline",
        strategy={"id": "no_memory", "memory_backend": "none"},
        configured_run_id="baseline_run",
        run_label="baseline_run_airline_no_memory_r1",
        task_ids=["18"],
        num_tasks=None,
        train_num_tasks=None,
        seed=300,
    )

    assert command is not None
    assert command[1].endswith("run_memory_v2_eval.py")
    assert "--no-memory" in command
    assert "--openviking-url" not in command
    assert command[command.index("--strategy-id") + 1] == "no_memory"
    assert command[command.index("--base-agent") + 1] == "llm_agent"
    assert command[command.index("--task-id") + 1] == "18"
    assert command[command.index("--run-dir") + 1].endswith(
        "result/baseline_run/memory_cells/baseline_run_airline_no_memory_r1"
    )


def test_no_memory_artifacts_read_wrapper_summary_metrics(tmp_path: Path) -> None:
    out = tmp_path / "out"
    cell = {
        "memory_backend": "none",
        "domain": "airline",
        "strategy_id": "no_memory",
        "run_label": "baseline_run_airline_no_memory_r1",
    }

    artifacts = _cell_artifacts(cell, repo=tmp_path / "tau2-bench", out=out)
    assert set(artifacts) == {"summary", "results"}

    summary_path = Path(artifacts["summary"])
    summary_path.parent.mkdir(parents=True)
    summary_path.write_text(
        json.dumps(
            {
                "metrics": {
                    "simulation_count": 1,
                    "avg_reward": 0.0,
                    "db_match_rate": 0.0,
                }
            }
        )
    )

    assert _cell_metrics(cell, artifacts) == {
        "simulation_count": 1,
        "avg_reward": 0.0,
        "db_match_rate": 0.0,
    }


def test_runtime_evidence_marks_empty_corpus_probe_diagnostic() -> None:
    evidence = _runtime_evidence_status(
        category_rerank={"enabled": True},
        corpus_probe={"match_count": 0},
        retrieval_trace_summary={"trace_present": True, "counts": {}, "rates": {}},
    )

    assert evidence["status"] == "diagnostic"
    assert "empty_corpus_probe" in evidence["reasons"]


def test_probe_corpus_counts_aggregate_and_concrete_matches() -> None:
    class FakeClient:
        def __init__(self) -> None:
            self.limit: int | None = None

        def search(self, **kwargs: object) -> SimpleNamespace:
            self.limit = int(kwargs["limit"])
            return SimpleNamespace(
                memories=[
                    SimpleNamespace(
                        uri="viking://agent/a/memories/trajectories/.overview.md",
                        score=0.2,
                    ),
                    SimpleNamespace(
                        uri="viking://agent/a/memories/trajectories/concrete.md#chunk_0001",
                        score=0.1,
                    ),
                ]
            )

        def read(self, uri: str) -> str:
            return f"body for {uri}"

    client = FakeClient()
    probe = _probe_corpus(
        SimpleNamespace(
            category_reranker=_reranker(),
            domain="airline",
            search_uri="viking://agent/a/memories/trajectories",
            retrieval_top_k=4,
        ),
        client,
    )

    assert client.limit == 6
    assert probe["probe_limit"] == 6
    assert probe["match_count"] == 2
    assert probe["aggregate_match_count"] == 1
    assert probe["concrete_match_count"] == 1
    assert probe["aggregate_read_non_empty_count"] == 1
    assert probe["concrete_read_non_empty_count"] == 1
    assert probe["matches"][0]["is_aggregate_memory"] is True
    assert probe["matches"][1]["is_concrete_memory"] is True
