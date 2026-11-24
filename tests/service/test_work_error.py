# Copyright (c) 2026 Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

from openviking.service.task_domain import (
    QueueWorkSummary,
    make_work_error,
    queue_result,
    serialize_work_errors,
)


def test_make_work_error_omits_empty_optional_fields():
    assert make_work_error("m") == {"message": "m"}
    assert make_work_error("m", uri="", err_brief="") == {"message": "m"}


def test_serialize_masks_and_truncates_message_and_preserves_uri_brief():
    uri = "viking://resources/project/page.md"
    detail = "x" * 700
    error = make_work_error("Provider rejected sk-example: " + detail, uri, "embedding_failed")
    assert serialize_work_errors([error]) == [
        {
            "message": ("Provider rejected [REDACTED]: " + detail)[:500] + "...[truncated]",
            "uri": uri,
            "err_brief": "embedding_failed",
        },
    ]


def test_serialize_drops_empty_or_unknown():
    assert serialize_work_errors([make_work_error(""), 42]) == []


def test_queue_result_projects_mixed_entries():
    summary = QueueWorkSummary(
        error_count=2,
        errors=[
            "legacy string",
            {"message": "m", "uri": "viking://resources/r", "err_brief": "b", "internal": "x"},
        ],
    )
    result = queue_result(summary)
    assert result["error_count"] == 2
    assert result["errors"] == [
        {"message": "legacy string"},
        {"message": "m", "uri": "viking://resources/r", "err_brief": "b"},
    ]
