# Copyright (c) 2026 Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

import pytest

from openviking.models.model_error import ModelErrorKind
from openviking.service.task_domain import (
    MAX_QUEUE_ERROR_DETAILS,
    QueueWorkSummary,
    TaskRecord,
    WorkRecord,
    WorkState,
    finalize_task,
    make_work_error,
    queue_result,
    raise_for_queue_model_limit,
    raise_for_task_model_limit,
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


def test_embedding_throttling_survives_task_finalization():
    from openviking.storage.collection_schemas import TextEmbeddingHandler

    failure = TextEmbeddingHandler._embedding_failed(
        None, "Slow down", kind=ModelErrorKind.RATE_LIMIT_ERROR
    )
    task = TaskRecord("job", "add_resource", result={"uri": "viking://resources/doc"})
    work = WorkRecord(
        "work",
        "job",
        "Embedding",
        state=WorkState.FAILED,
        error=failure.error,
        errors=serialize_work_errors(failure.errors),
    )
    assert finalize_task(task, [work])
    assert task.result["code"] == "RESOURCE_EXHAUSTED"
    assert "model" in task.error.lower()

    # A second, unrelated failure must keep its original classification.
    mixed = TaskRecord("job", "add_resource", result={})
    other = WorkRecord("other", "job", "Semantic", state=WorkState.FAILED, error="write failed")
    assert finalize_task(mixed, [work, other])
    assert "code" not in mixed.result


ARK_LIMIT = "Error code: 429 - {'error': {'code': 'AccountRateLimitExceeded'}}"


@pytest.mark.parametrize(
    ("queue_name", "raw"),
    [
        ("Semantic", "viking://agent/skills/demo/429.md: file read failed"),
        ("Embedding", "Failed to generate embedding: request_id: req-429"),
        ("Embedding", "Error code: 429 - {'error': {'code': 'UnknownProviderCode'}}"),
        ("Semantic", "Error code: 500 - {'error': {'code': 'AccountRateLimitExceeded'}}"),
        ("Semantic", "Error code: 429 - {'error': {'message': 'AccountRateLimitExceeded'}}"),
        ("Semantic", "Error code: 429 - {'error': {'code': 'AccountRateLim...[truncated]"),
        ("Semantic", {"message": ARK_LIMIT, "model_error_kind": "authentication_error"}),
        ("Embedding", {"message": ARK_LIMIT, "upstream_status_code": 403}),
        ("Semantic", ARK_LIMIT + ": file not found"),
        (
            "Semantic",
            """Error code: 429 - {'error': {'message': "'code': 'AccountRateLimitExceeded'...[truncated]""",
        ),
        ("Storage", ARK_LIMIT),
    ],
)
def test_queue_error_requires_model_rate_limit_evidence(queue_name, raw):
    summary = QueueWorkSummary(error_count=1, errors=[raw])
    result = queue_result(summary, queue_name=queue_name)
    assert "model_error_kind" not in result
    raise_for_queue_model_limit({queue_name: {"error_count": 1, "errors": [raw]}})


@pytest.mark.parametrize(
    "message",
    [
        "viking://agent/skills/demo/README.md: " + ARK_LIMIT,
        "Error code: 429 - {'error': {'message': 'Slow down', 'code': 'AccountRateLimitExceeded', 'param': '...[truncated]",
        'Error code: 429 - {"error": {"param": null, "code": "AccountRateLimitExceeded"}}',
        "Error code: 429 - {'error': {'code': 'AccountRateLimitExceeded', 'message': 'Req...[truncated]",
    ],
)
def test_legacy_semantic_failure_survives_task_finalization(message):
    task = TaskRecord("job", "add_skill", result={})
    work = WorkRecord("work", "job", "Semantic", state=WorkState.FAILED, error=message)
    assert finalize_task(task, [work])
    assert task.result["code"] == "RESOURCE_EXHAUSTED"
    assert task.result["details"]["upstream_error_code"] == "AccountRateLimitExceeded"
    entry = task.result["queue_status"]["Semantic"]["errors"][0]
    assert entry["message"] == message
    assert entry["model_error_kind"] == "rate_limit_error"
    assert entry["upstream_status_code"] == 429


def test_mixed_model_limits_survive_queue_and_task_projection():
    from openviking.utils.exceptions import ModelLimitError

    messages = [ARK_LIMIT, ARK_LIMIT.replace("RateLimit", "Quota")]
    work = WorkRecord(
        "w", "job", "Semantic", state=WorkState.FAILED, error=messages[0], errors=messages
    )
    task = TaskRecord("job", "add_skill", result={})
    assert finalize_task(task, [work])
    assert task.result["code"] == "RESOURCE_EXHAUSTED"
    for raise_limit, argument in (
        (raise_for_task_model_limit, task.result),
        (raise_for_queue_model_limit, task.result["queue_status"]),
    ):
        with pytest.raises(ModelLimitError) as exc:
            raise_limit(argument)
        assert exc.value.details["model_error_kinds"] == ["quota_exhausted", "rate_limit_error"]
        assert "model_error_kind" not in exc.value.details
        assert "upstream_error_code" not in exc.value.details


def test_visible_rate_limits_do_not_hide_a_missing_or_unrelated_failure():
    errors = [ARK_LIMIT] * MAX_QUEUE_ERROR_DETAILS + ["storage write failed"]
    summary = QueueWorkSummary(error_count=len(errors), errors=errors)
    stage = queue_result(summary, queue_name="Embedding")
    assert len(stage["errors"]) < stage["error_count"]
    assert all(e["model_error_kind"] == "rate_limit_error" for e in stage["errors"])
    assert "model_error_kind" not in stage
    raise_for_queue_model_limit({"Embedding": stage})
    raise_for_queue_model_limit(
        {
            "Semantic": {"error_count": 1, "errors": [ARK_LIMIT]},
            "Embedding": {"error_count": 1, "errors": ["storage write failed"]},
        }
    )
