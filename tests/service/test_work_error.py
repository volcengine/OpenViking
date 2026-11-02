# Copyright (c) 2026 Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

from openviking.service.task_domain import (
    QueueWorkSummary,
    make_work_error,
    project_work_error,
    queue_result,
    serialize_work_errors,
)


def test_make_work_error_omits_empty_optional_fields():
    assert make_work_error("m") == {"message": "m"}
    assert make_work_error("m", uri="v://r") == {"message": "m", "uri": "v://r"}
    assert make_work_error("m", err_brief="b") == {"message": "m", "err_brief": "b"}
    assert make_work_error("m", "v://r", "b") == {
        "message": "m",
        "uri": "v://r",
        "err_brief": "b",
    }


def test_project_legacy_string_is_message_only():
    assert project_work_error("old failure") == {"message": "old failure"}


def test_project_shaped_dict_keeps_known_fields_only():
    out = project_work_error(
        {"message": "m", "uri": "v://r", "err_brief": "b", "internal": "x"}
    )
    assert out == {"message": "m", "uri": "v://r", "err_brief": "b"}


def test_project_unknown_type_yields_empty_message_not_fake_text():
    assert project_work_error(object()) == {"message": ""}


def test_serialize_masks_message_and_preserves_uri_brief():
    out = serialize_work_errors([make_work_error("token sk-" + "a" * 40, "v://r", "keep")])
    assert len(out) == 1
    assert "[REDACTED]" in out[0]["message"]
    assert out[0]["uri"] == "v://r"
    assert out[0]["err_brief"] == "keep"


def test_serialize_truncates_long_message():
    out = serialize_work_errors([make_work_error("x" * 700)])
    assert len(out[0]["message"]) == 514
    assert out[0]["message"].endswith("...[truncated]")


def test_serialize_drops_empty_or_unknown():
    assert serialize_work_errors([make_work_error(""), object()]) == []


def test_queue_result_projects_mixed_entries():
    summary = QueueWorkSummary(
        error_count=2,
        errors=["legacy string", make_work_error("m", uri="v://r", err_brief="b")],
    )
    result = queue_result(summary)
    assert result["error_count"] == 2
    assert result["errors"] == [
        {"message": "legacy string"},
        {"message": "m", "uri": "v://r", "err_brief": "b"},
    ]
