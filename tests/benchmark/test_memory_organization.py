import json
from pathlib import Path

import pytest

from benchmark.memory_organization.autonomous_cases import build_cases
from benchmark.memory_organization.autonomous_grader import (
    autonomous_metrics,
    grade_autonomous_result,
)
from benchmark.memory_organization.grader import (
    grade_result,
    mcnemar_exact_p_value,
    paired_counts,
    summarize,
)
from benchmark.memory_organization.models import (
    OrganizationCase,
    fact_lines,
    fact_markers,
    load_cases,
)
from benchmark.memory_organization.report_autonomous import regrade_rows, summarize_autonomous
from benchmark.memory_organization.run_ab import _call_count
from benchmark.memory_organization.run_autonomous_ab import (
    AutonomousProvider,
    _close_vlm_async_clients,
    build_jobs,
    parse_args,
    run_one,
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


def test_all_fixtures_have_unique_markers_and_expected_topics():
    cases = load_cases(
        Path(__file__).parents[2]
        / "benchmark"
        / "memory_organization"
        / "cases"
        / "organization_cases.json"
    )

    assert len(cases) == 12
    assert {case.category for case in cases} == {"merge", "split", "mixed"}
    assert all(set(case.expected_files) == set(case.canonical_topics) for case in cases)
    for case in cases:
        source_markers = [
            marker for content in case.initial_files.values() for marker in fact_markers(content)
        ]
        assert len(source_markers) == len(set(source_markers))
        assert set(source_markers) == case.expected_markers


def test_stress_fixtures_are_large_and_well_formed():
    cases = load_cases(
        Path(__file__).parents[2]
        / "benchmark"
        / "memory_organization"
        / "cases"
        / "organization_stress_cases.json"
    )

    assert len(cases) == 3
    assert all(len(case.expected_markers) >= 24 for case in cases)
    for case in cases:
        source_markers = [
            marker for content in case.initial_files.values() for marker in fact_markers(content)
        ]
        assert len(source_markers) == len(set(source_markers))
        assert set(source_markers) == case.expected_markers


def test_autonomous_suite_contains_the_three_core_cases():
    cases = build_cases()

    assert [case.case_id for case in cases] == [
        "case_insensitive_directory_collision",
        "oversized_profile_routes_preferences",
        "oversized_preference_splits",
    ]
    assert [case.category for case in cases] == [
        "directory_merge",
        "cross_type_split",
        "same_type_split",
    ]
    assert cases[0].messages[0].parts[0].text == (
        "你还记得 Atlas 项目的发布方式、回滚流程和日常运维信息吗？"
    )
    assert cases[1].messages[0].parts[0].text == (
        "你还记得我的基本情况，以及我在工作、饮食、旅行和游戏方面的偏好吗？"
    )
    assert all(case.messages[0].parts[0].text != "请继续。" for case in cases)


def test_parallel_jobs_are_unique_and_counterbalanced():
    jobs = build_jobs(build_cases(), repeat=2)

    assert len(jobs) == 12
    assert len({(case_id, protocol, repeat) for _, case_id, protocol, repeat in jobs}) == 12
    assert jobs[:2] == [
        (0, "case_insensitive_directory_collision", "json", 0),
        (1, "case_insensitive_directory_collision", "python", 0),
    ]
    assert jobs[6:8] == [
        (6, "case_insensitive_directory_collision", "python", 1),
        (7, "case_insensitive_directory_collision", "json", 1),
    ]


def test_parallel_argument_requires_positive_value():
    assert parse_args(["--parallel", "6"]).parallel == 6
    with pytest.raises(SystemExit):
        parse_args(["--parallel", "0"])


@pytest.mark.asyncio
async def test_parallel_worker_closes_loop_bound_vlm_clients():
    closed: list[str] = []

    class Client:
        async def close(self):
            closed.append("closed")

    class Cache:
        def pop_all(self):
            return [Client()]

    class VLM:
        _async_client_cache = Cache()

    await _close_vlm_async_clients(VLM())

    assert closed == ["closed"]


def test_exact_two_split_case_combines_existing_and_conversation_facts():
    case = {case.case_id: case for case in build_cases()}["oversized_preference_splits"]

    assert len(case.initial_files) == 1
    assert len(next(iter(case.initial_files.values())).content.splitlines()) == 32
    assert len(case.expected_facts) == 40
    assert set(case.expected_facts) == {f"F{index:02d}" for index in range(25, 65)}
    assert len(case.expected_groups["preferences"]) == 2
    assert len(case.additional_facts or {}) == 8
    assert len(case.messages) == 1


def test_preference_split_action_accepts_two_or_more_new_files():
    case = {case.case_id: case for case in build_cases()}["oversized_preference_splits"]
    source_uri = next(iter(case.initial_files))
    files = {}
    for index, group in enumerate(case.expected_groups["preferences"], start=1):
        content = "\n".join(
            f"- [{marker}] {case.expected_facts[marker]}" for marker in sorted(group)
        )
        files[f"viking://user/default/memories/preferences/Andrew/topic_{index}.md"] = (
            "preferences",
            content,
        )

    grade = grade_autonomous_result(case, files, {})
    metrics = autonomous_metrics(case, files, {}, grade)

    assert metrics["organization_action_success"]
    assert metrics["information_integrity"]

    first_child_uri = next(iter(files))
    files[source_uri] = files.pop(first_child_uri)
    grade = grade_autonomous_result(case, files, {})
    assert not autonomous_metrics(case, files, {}, grade)["organization_action_success"]

    files = {
        source_uri: (
            "preferences",
            "\n".join(
                f"- [{marker}] {case.expected_facts[marker]}"
                for marker in sorted(case.expected_facts)
            ),
        )
    }
    grade = grade_autonomous_result(case, files, {})
    assert not autonomous_metrics(case, files, {}, grade)["organization_action_success"]


def test_preference_split_action_accepts_alternative_partition_and_more_children():
    case = {case.case_id: case for case in build_cases()}["oversized_preference_splits"]
    markers = sorted(case.expected_facts)
    files = {}
    for index, group in enumerate((markers[:36], markers[36:]), start=1):
        files[f"viking://user/default/memories/preferences/Andrew/alternative_{index}.md"] = (
            "preferences",
            "\n".join(f"- [{marker}] {case.expected_facts[marker]}" for marker in group),
        )

    grade = grade_autonomous_result(case, files, {})
    metrics = autonomous_metrics(case, files, {}, grade)

    assert metrics["organization_action_success"]
    assert metrics["information_integrity"]

    marker = markers.pop()
    files[next(reversed(files))] = (
        "preferences",
        "\n".join(f"- [{item}] {case.expected_facts[item]}" for item in markers[36:]),
    )
    files["viking://user/default/memories/preferences/Andrew/third.md"] = (
        "preferences",
        f"- [{marker}] {case.expected_facts[marker]}",
    )
    grade = grade_autonomous_result(case, files, {})
    metrics = autonomous_metrics(case, files, {}, grade)
    assert metrics["organization_action_success"]
    assert metrics["information_integrity"]


@pytest.mark.asyncio
async def test_autonomous_prompts_do_not_leak_expected_partition():
    cases = {case.case_id: case for case in build_cases()}
    profile_case = cases["oversized_profile_routes_preferences"]
    provider = AutonomousProvider(profile_case)

    messages = await provider.prefetch()
    visible = (
        provider.instruction()
        + "\n"
        + "\n".join(str(message.get("content", "")) for message in messages)
    )

    assert profile_case.case_id not in visible
    assert "exactly five" not in visible
    assert "food, travel" not in visible
    assert "expected_groups" not in visible
    assert "Decide autonomously" not in visible
    assert "preserved, normalized, merged, split, or moved" in visible
    assert "You are a memory extraction and maintenance agent" in provider.instruction()
    assert {schema.memory_type for schema in provider.get_memory_schemas(None)} == {
        schema.memory_type
        for schema in provider._get_registry().list_all(include_disabled=False)
        if schema.stage == "user"
    }


def test_autonomous_provider_accepts_explicit_output_language():
    case = build_cases()[0]

    provider = AutonomousProvider(case, output_language="zh-CN")

    assert provider.get_output_language() == "zh-CN"


@pytest.mark.asyncio
async def test_run_one_restores_vlm_recorder(monkeypatch):
    class FakeUsage:
        def get_token_usage(self):
            return {"total_usage": {}}

        def reset_token_usage(self):
            pass

        async def get_completion_async(self, *args, **kwargs):
            del args, kwargs
            return ""

        provider = "fake"
        model = "fake"
        temperature = 0.0
        thinking = False
        max_tokens = None

    class FakeMemoryConfig:
        extraction_output_format = "python"
        link_enabled = False

    class FakeVLMConfig:
        def __init__(self, vlm):
            self._vlm = vlm

        def get_vlm_instance(self):
            return self._vlm

    class FakeConfig:
        def __init__(self, vlm):
            self.memory = FakeMemoryConfig()
            self.vlm = FakeVLMConfig(vlm)

    async def fake_run(self):
        await self.vlm.get_completion_async(messages=[])
        raise RuntimeError("stop after recording")

    from openviking.session.memory.extract_loop import ExtractLoop

    monkeypatch.setattr(ExtractLoop, "run", fake_run)
    vlm = FakeUsage()
    original = vlm.get_completion_async

    row = await run_one(build_cases()[0], "python", 0, FakeConfig(vlm))

    assert len(row["raw_responses"]) == 1
    assert vlm.get_completion_async == original


def test_autonomous_directory_grader_accepts_case_normalization():
    case = build_cases()[0]
    target = "viking://user/default/memories/entities/projects/atlas.md"
    source = "viking://user/default/memories/entities/Projects/atlas.md"
    grade = grade_autonomous_result(
        case,
        {
            target: (
                "entities",
                "# Atlas\nAtlas 是一个软件服务。\n\n## 事实\n"
                "- [F01] 使用分阶段发布。\n"
                "- [F02] 有明确记录的回滚流程。\n"
                "- [F03] 由平台团队负责。\n"
                "- [F04] 提供健康检查接口。",
            )
        },
        {source: target},
    )

    assert grade.organization_success
    metrics = autonomous_metrics(
        case,
        {
            target: (
                "entities",
                "# Atlas\nAtlas 是一个软件服务。\n\n## 事实\n"
                "- [F01] 使用分阶段发布。\n"
                "- [F02] 有明确记录的回滚流程。\n"
                "- [F03] 由平台团队负责。\n"
                "- [F04] 提供健康检查接口。",
            )
        },
        {source: target},
        grade,
    )
    assert metrics["organization_action_success"]
    assert metrics["information_integrity"]


def test_autonomous_profile_grader_requires_cross_type_routing():
    case = build_cases()[1]
    profile_uri = "viking://user/default/memories/profile.md"
    profile = case.initial_files[profile_uri].content

    grade = grade_autonomous_result(case, {profile_uri: ("profile", profile)}, {})

    assert not grade.content_organization_success
    assert grade.misplaced_fact_count > 0
    metrics = autonomous_metrics(case, {profile_uri: ("profile", profile)}, {}, grade)
    assert not metrics["organization_action_success"]
    assert metrics["information_integrity"]


def test_profile_move_action_is_independent_of_information_integrity():
    case = build_cases()[1]
    files = {
        "viking://user/default/memories/profile.md": (
            "profile",
            "# 安德鲁\n- 是一名后端工程师 (as of 2026-08-01)",
        ),
        "viking://user/default/memories/preferences/安德鲁/food.md": (
            "preferences",
            "- 喜欢吃辣味面条 (as of 2026-08-01)",
        ),
    }

    grade = grade_autonomous_result(case, files, {})
    metrics = autonomous_metrics(case, files, {}, grade)

    assert metrics["organization_action_success"]
    assert not metrics["information_integrity"]


def test_autonomous_routing_allows_schema_formatting_changes():
    case = build_cases()[1]
    profile_uri = "viking://user/default/memories/profile.md"
    source_lines = case.initial_files[profile_uri].content.splitlines()
    profile = "\n".join(
        line.replace(f"[F{index:02d}] ", "")
        for index, line in enumerate(source_lines[1:7], start=1)
    )
    preference = "\n".join(
        line.replace(f"[F{index:02d}] ", "").replace(" (as of 2026-08-01)", "")
        for index, line in enumerate(source_lines[7:], start=7)
    )
    files = {
        profile_uri: ("profile", profile),
        "viking://user/default/memories/preferences/andrew/all.md": (
            "preferences",
            preference,
        ),
    }
    grade = grade_autonomous_result(case, files, {})
    metrics = autonomous_metrics(case, files, {}, grade)

    assert metrics["organization_action_success"]
    assert metrics["information_integrity"]
    assert set(metrics) == {"organization_action_success", "information_integrity"}


def test_autonomous_report_regrades_stored_rows():
    case = build_cases()[1]
    profile_uri = "viking://user/default/memories/profile.md"
    source_lines = case.initial_files[profile_uri].content.splitlines()
    profile = "\n".join(line.split("] ", 1)[1] for line in source_lines[1:7])
    preference = "\n".join(
        line.split("] ", 1)[1].replace(" (as of 2026-08-01)", "") for line in source_lines[7:]
    )
    rows = [
        {
            "case_id": case.case_id,
            "grade": {},
            "autonomous_metrics": {},
            "actual_files": {
                profile_uri: {"memory_type": "profile", "content": profile},
                "viking://user/default/memories/preferences/Andrew/all.md": {
                    "memory_type": "preferences",
                    "content": preference,
                },
            },
            "actual_replacements": {},
        }
    ]

    metrics = regrade_rows(rows)[0]["autonomous_metrics"]
    assert metrics["organization_action_success"]
    assert metrics["information_integrity"]


def test_autonomous_report_exposes_only_two_primary_quality_metrics():
    case = build_cases()[0]
    target = "viking://user/default/memories/entities/projects/atlas.md"
    source = "viking://user/default/memories/entities/Projects/atlas.md"
    row = {
        "case_id": case.case_id,
        "protocol": "python",
        "repeat_index": 0,
        "actual_files": {
            target: {
                "memory_type": "entities",
                "content": "- [F01] 使用分阶段发布。\n- [F02] 有明确记录的回滚流程。\n"
                "- [F03] 由平台团队负责。\n- [F04] 提供健康检查接口。",
            }
        },
        "actual_replacements": {source: target},
        "calls": 1,
        "retries": 0,
        "total_tokens": 100,
        "duration_seconds": 1.0,
        "terminal_error": "",
    }

    report = summarize_autonomous(regrade_rows([row]))[case.case_id]
    assert set(report["summary"]["python"]) == {
        "runs",
        "organization_action_success_rate",
        "information_integrity_rate",
    }
    assert set(report["paired"]) == {
        "organization_action_success",
        "information_integrity",
    }


def test_grader_accepts_exact_target_state():
    case = _case()
    grade = grade_result(case, {"travel": "[F01] A\n[F02] B"}, {"trip": "travel"})

    assert grade.organization_success
    assert grade.content_organization_success
    assert grade.file_tree_accuracy == 1.0
    assert grade.fact_recall == 1.0
    assert grade.placement_precision == 1.0
    assert grade.replacement_accuracy == 1.0


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


def test_grader_accepts_any_merge_survivor_with_correct_replacements():
    case = _case()

    grade = grade_result(case, {"trip": "[F01] A\n[F02] B"}, {"travel": "trip"})

    assert grade.organization_success
    assert grade.replacement_accuracy == 1.0


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


def test_fact_lines_accepts_markdown_bullets_and_normalizes_whitespace():
    content = "- [F01]  A   stable fact.  \n* [F02] Another."

    assert fact_lines(content) == [
        ("F01", "A stable fact."),
        ("F02", "Another."),
    ]


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

    assert paired_counts(rows) == {
        "both_success": 0,
        "python_only": 1,
        "json_only": 0,
        "both_fail": 0,
    }
    result = summarize(rows)
    assert result["python"]["organization_success_rate"] == 1.0
    assert result["json"]["organization_success_rate"] == 0.0
    assert result["python"]["content_organization_success_rate"] == 1.0
    assert result["python"]["mean_retries"] == 0
    assert result["json"]["mean_prompt_tokens"] == 120
    assert result["json"]["mean_completion_tokens"] == 30


def test_exact_mcnemar_p_value():
    assert mcnemar_exact_p_value({"python_only": 0, "json_only": 0}) == 1.0
    assert mcnemar_exact_p_value({"python_only": 1, "json_only": 4}) == 0.375


def test_grade_is_json_serializable():
    grade = grade_result(_case(), {"travel": "[F01] A\n[F02] B"}, {"trip": "travel"})
    json.dumps(grade.to_dict())


def test_call_count_sums_model_usage():
    assert (
        _call_count(
            {
                "total_usage": {"call_count": 0},
                "usage_by_model": {
                    "model-a": {"total_usage": {"call_count": 2}},
                    "model-b": {"total_usage": {"call_count": 3}},
                },
            }
        )
        == 5
    )
