import json
import sys
from types import SimpleNamespace

import pytest

from benchmark.memory_organization import report_autonomous
from benchmark.memory_organization.autonomous_cases import build_cases
from benchmark.memory_organization.autonomous_grader import (
    autonomous_metrics,
    grade_autonomous_result,
)
from benchmark.memory_organization.grader import (
    grade_result,
    summarize,
)
from benchmark.memory_organization.models import (
    OrganizationCase,
)
from benchmark.memory_organization.report import paired_report
from benchmark.memory_organization.run_autonomous_ab import (
    _run_job_in_process,
    build_jobs,
    parse_args,
)


def _case() -> OrganizationCase:
    return OrganizationCase.from_dict(
        {
            "case_id": "merge",
            "category": "merge",
            "canonical_topics": ["travel"],
            "initial_files": {"travel": "[F01] A", "trip": "[F02] B"},
            "expected_files": {"travel": ["F01", "F02"]},
            "expected_replacements": {"travel": "travel", "trip": "travel"},
        }
    )


def test_parallel_jobs_are_unique_and_counterbalanced():
    first, second = build_cases()[:2]
    args = parse_args(["--parallel", "2", "--repeat", "2"])
    jobs = build_jobs([first, second], repeat=args.repeat)
    with pytest.raises(SystemExit):
        parse_args(["--parallel", "0"])

    assert [job[0] for job in jobs] == list(range(8))
    assert [job[1:] for job in jobs] == [
        (first.case_id, "json", 0),
        (first.case_id, "python", 0),
        (second.case_id, "python", 0),
        (second.case_id, "json", 0),
        (first.case_id, "python", 1),
        (first.case_id, "json", 1),
        (second.case_id, "json", 1),
        (second.case_id, "python", 1),
    ]


def test_preference_split_action_accepts_two_or_more_new_files():
    case = next(case for case in build_cases() if case.category == "same_type_split")
    source_uri = next(iter(case.initial_files))
    facts = list(case.expected_facts)
    root = source_uri.rsplit("/", 1)[0]

    def files_for(*groups):
        return {
            f"{root}/topic_{index}.md": (
                "preferences",
                "\n".join(f"- [{key}] {case.expected_facts[key]}" for key in group),
            )
            for index, group in enumerate(groups)
        }

    def score(files):
        return autonomous_metrics(case, files, {}, grade_autonomous_result(case, files, {}))

    two = files_for(facts[:36], facts[36:])
    assert score(two) == {"organization_action_success": True, "information_integrity": True}
    three = files_for(facts[:36], facts[36:-1], facts[-1:])
    assert score(three) == {"organization_action_success": True, "information_integrity": True}
    two[source_uri] = two.pop(f"{root}/topic_0.md")
    assert score(two) == {"organization_action_success": False, "information_integrity": True}


def test_run_one_restores_vlm_recorder(monkeypatch, tmp_path):
    from unittest.mock import AsyncMock

    from openviking.models.vlm.base import VLMResponse
    from openviking_cli.utils.config import OpenVikingConfigSingleton
    from openviking_cli.utils.config.vlm_config import VLMConfig

    attempts: list[str] = []
    close = AsyncMock()
    partial_response = "This is not an extraction program."

    class Model:
        provider = "openai"
        model = "offline"
        temperature = 0.0
        thinking = False
        max_tokens = None
        _async_client_cache = SimpleNamespace(pop_all=lambda: [SimpleNamespace(close=close)])

        def reset_token_usage(self):
            attempts.clear()

        async def get_completion_async(self, *, messages, **kwargs):
            attempts.append(json.dumps(messages, ensure_ascii=False))
            if len(attempts) == 1:
                return VLMResponse(content=partial_response)
            raise TimeoutError("offline model timeout")

        def get_token_usage(self):
            return {
                "total_usage": {"call_count": 0},
                "usage_by_model": {"offline": {"total_usage": {"call_count": 1}}},
            }

    vlm = Model()
    original = vlm.get_completion_async

    def get_vlm(config):
        config._vlm_instance = vlm
        return vlm

    monkeypatch.setattr(VLMConfig, "get_vlm_instance", get_vlm)
    for field in ("_instance", "_config_file", "_config_file_revision", "_config_file_content"):
        monkeypatch.setattr(OpenVikingConfigSingleton, field, None)
    config_file = tmp_path / "ov.conf"
    config_file.write_text(
        json.dumps(
            {
                "storage": {"workspace": str(tmp_path / "storage")},
                "vlm": {
                    "provider": "openai",
                    "model": "offline",
                    "api_key": "offline-test",
                    "api_base": "http://127.0.0.1:1/v1",
                },
                "embedding": {
                    "dense": {
                        "provider": "openai",
                        "model": "offline",
                        "api_key": "offline-test",
                        "api_base": "http://127.0.0.1:1/v1",
                        "dimension": 8,
                    }
                },
            }
        )
    )
    case_id = "oversized_profile_routes_preferences"

    row = _run_job_in_process(str(config_file), case_id, "python", 0, "zh")

    assert len(attempts) == 2
    prompt = attempts[0]
    assert "你还记得我的基本情况" in prompt
    assert case_id not in prompt
    assert all(label not in prompt for label in ("expected_groups", "exactly five", "food, travel"))
    assert row["terminal_error"] == "TimeoutError: offline model timeout"
    assert row["raw_responses"] == [{"content": partial_response, "tool_calls": []}]
    assert row["calls"] == 1
    assert json.loads(json.dumps(row))["grade"] == row["grade"]
    assert vlm.get_completion_async == original
    close.assert_awaited_once()


def test_autonomous_directory_grader_accepts_case_normalization():
    case = next(case for case in build_cases() if case.category == "directory_merge")
    source, target = case.initial_files
    files = {target: ("entities", "\n".join(item.content for item in case.initial_files.values()))}
    moved = {source: target}

    grade = grade_autonomous_result(case, files, moved)
    assert grade.organization_success
    assert autonomous_metrics(case, files, moved, grade) == {
        "organization_action_success": True,
        "information_integrity": True,
    }
    grade = grade_autonomous_result(case, files, {})
    assert not autonomous_metrics(case, files, {}, grade)["organization_action_success"]


def test_autonomous_report_regrades_stored_rows(tmp_path, monkeypatch):
    case = next(case for case in build_cases() if case.category == "cross_type_split")
    (profile_uri,) = case.initial_files
    preference_uri = profile_uri.rsplit("/", 1)[0] + "/preferences/Andrew/all.md"
    original = case.initial_files[profile_uri].content
    source_lines = original.splitlines()
    profile = "\n".join(line.split("] ", 1)[1] for line in source_lines[1:7])
    preference = "\n".join(
        line.split("] ", 1)[1].replace(" (as of 2026-08-01)", "") for line in source_lines[7:]
    )
    unchanged = {profile_uri: {"memory_type": "profile", "content": original}}
    moved = {
        profile_uri: {"memory_type": "profile", "content": profile},
        preference_uri: {"memory_type": "preferences", "content": preference},
    }
    truncated = {
        **moved,
        preference_uri: {**moved[preference_uri], "content": preference.splitlines()[0]},
    }
    rows = [
        {
            "case_id": case.case_id,
            "protocol": protocol,
            "repeat_index": repeat,
            "actual_files": files,
            "actual_replacements": {},
            "grade": {},
            "autonomous_metrics": {},
        }
        for protocol, repeat, files in [
            ("json", 0, unchanged),
            ("python", 0, moved),
            ("json", 1, moved),
            ("python", 1, truncated),
        ]
    ]
    input_path = tmp_path / "runs.jsonl"
    input_path.write_text("".join(json.dumps(row) + "\n" for row in rows))
    output = tmp_path / "report.json"
    monkeypatch.setattr(
        sys, "argv", ["report_autonomous.py", str(input_path), "--output", str(output)]
    )

    report_autonomous.main()

    report = json.loads(output.read_text())[case.case_id]
    assert report["summary"]["json"] == {
        "runs": 2,
        "organization_action_success_rate": 0.5,
        "information_integrity_rate": 1.0,
    }
    assert report["summary"]["python"] == {
        "runs": 2,
        "organization_action_success_rate": 1.0,
        "information_integrity_rate": 0.5,
    }
    assert set(report["paired"]) == {"organization_action_success", "information_integrity"}
    assert report["paired"]["organization_action_success"]["python_only"] == 1
    assert report["paired"]["information_integrity"]["json_only"] == 1


def test_grader_accepts_exact_target_state():
    case = _case()
    for survivor, removed in (("travel", "trip"), ("trip", "travel")):
        grade = grade_result(case, {survivor: "- [F01]  A\n* [F02] B"}, {removed: survivor})
        assert grade.organization_success
        assert grade.file_tree_accuracy == grade.fact_recall == grade.replacement_accuracy == 1.0


def test_grader_accepts_equivalent_topic_names():
    case = OrganizationCase.from_dict(
        {
            "case_id": "split",
            "category": "split",
            "canonical_topics": ["food", "travel"],
            "initial_files": {"mixed": "[F01] A\n[F02] B"},
            "expected_files": {"food": ["F01"], "travel": ["F02"]},
            "expected_replacements": {},
        }
    )

    grade = grade_result(
        case,
        {"food_preferences": "[F01] A", "travel_preferences": "[F02] B"},
    )

    assert grade.organization_success


def test_grader_rejects_duplicate_misplaced_and_missing_facts():
    case = _case()
    grade = grade_result(
        case,
        {"travel": "[F01] A\n[F01] A", "trip": "[F02] B"},
        {},
    )

    assert not grade.organization_success
    assert not grade.content_organization_success
    assert grade.duplicate_fact_count == 1
    assert grade.misplaced_fact_count == 1
    assert grade.replacement_accuracy == 0.0


def test_grader_rejects_changed_fact_text_and_empty_leftover_file():
    case = _case()

    grade = grade_result(
        case,
        {"travel": "[F01] changed\n[F02] B", "trip": ""},
        {"trip": "travel"},
    )

    assert not grade.organization_success
    assert grade.fact_recall == 0.5
    assert grade.altered_fact_count == 1
    assert grade.unexpected_file_count == 1


def test_summary_and_paired_counts():
    rows = [
        {
            "case_id": "c1",
            "repeat_index": 0,
            "protocol": "python",
            "grade": {
                "content_organization_success": True,
                "organization_success": True,
                "file_tree_accuracy": 1.0,
                "fact_recall": 1.0,
                "placement_precision": 1.0,
            },
            "calls": 1,
            "retries": 0,
            "prompt_tokens": 80,
            "completion_tokens": 20,
            "total_tokens": 100,
            "duration_seconds": 1.0,
            "terminal_error": "",
        },
        {
            "case_id": "c1",
            "repeat_index": 0,
            "protocol": "json",
            "grade": {
                "content_organization_success": False,
                "organization_success": False,
                "file_tree_accuracy": 0.5,
                "fact_recall": 1.0,
                "placement_precision": 0.5,
            },
            "calls": 2,
            "retries": 1,
            "prompt_tokens": 120,
            "completion_tokens": 30,
            "total_tokens": 150,
            "duration_seconds": 2.0,
            "terminal_error": "failed",
        },
    ]

    assert paired_report(rows, metric="organization_success") == {
        "both_success": 0,
        "python_only": 1,
        "json_only": 0,
        "both_fail": 0,
        "mcnemar_exact_p_value": 1.0,
    }
    result = summarize(rows)
    assert result["python"]["organization_success_rate"] == 1.0
    assert result["json"]["organization_success_rate"] == 0.0
    assert result["python"]["content_organization_success_rate"] == 1.0
    assert result["python"]["mean_retries"] == 0
    assert result["json"]["mean_prompt_tokens"] == 120
    assert result["json"]["mean_completion_tokens"] == 30

    paired = paired_report(
        [
            *rows,
            *(
                {
                    **row,
                    "repeat_index": repeat,
                    "grade": {"organization_success": row["protocol"] == "json"},
                }
                for repeat in range(1, 5)
                for row in rows
            ),
        ],
        metric="organization_success",
    )
    assert paired["mcnemar_exact_p_value"] == 0.375
