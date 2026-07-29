# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Unit tests for DeadlineExceededError carrying task_id / root_uri details (issue #3306)."""

from __future__ import annotations

import pytest

from openviking_cli.exceptions import DeadlineExceededError


class TestDeadlineExceededErrorExtraDetails:
    """Ensure DeadlineExceededError transparently forwards custom details to the error payload."""

    def test_operation_and_timeout_keep_original_behavior(self):
        err = DeadlineExceededError("queue processing", 10.0)
        assert err.code == "DEADLINE_EXCEEDED"
        assert "Queue processing timed out after 10.0s" in err.message
        assert err.details["operation"] == "queue processing"
        assert err.details["timeout"] == 10.0
        # Extra details should not be present unless explicitly supplied.
        assert "task_id" not in err.details
        assert "root_uri" not in err.details

    def test_task_id_and_root_uri_are_exposed_in_details(self):
        """Requirement from issue #3306: add_resource --wait timeout returns no task_id.

        When the service raises DeadlineExceededError because the post-ingest
        processing queue did not complete within the user-provided timeout, we
        must still communicate the task identifier and the target resource URI
        so the caller can poll /tasks/{task_id} or retry explicitly instead of
        losing track of the enqueued work.
        """
        tid = "ov-task-1234"
        uri = "viking://resources/texts/my-note"
        err = DeadlineExceededError(
            "queue processing",
            0.25,
            task_id=tid,
            root_uri=uri,
        )
        assert err.details.get("task_id") == tid
        assert err.details.get("root_uri") == uri
        assert err.details["operation"] == "queue processing"
        assert err.details["timeout"] == 0.25
        # code/message remain unchanged.
        assert err.code == "DEADLINE_EXCEEDED"
        assert "timed out after 0.25s" in err.message

    def test_unknown_extra_keys_are_forwarded_as_details(self):
        err = DeadlineExceededError("commit", None, account_id="acct-42", user_id="u-1")
        assert err.details["account_id"] == "acct-42"
        assert err.details["user_id"] == "u-1"
        assert err.details["operation"] == "commit"
        assert err.details.get("timeout") is None

    def test_extra_details_task_id_still_preserves_constructor_reserved_fields(self):
        # Reserved positional/keyword fields (operation, timeout) should never be
        # overwritten by extra_details: the constructor-level assignment happens
        # first, so we simply confirm extra custom keys never blow away the base
        # contract of DeadlineExceededError.details.
        err = DeadlineExceededError("op", 5.0, task_id="t", custom_flag=True)
        assert err.details["timeout"] == 5.0
        assert err.details["operation"] == "op"
        assert err.details["task_id"] == "t"
        assert err.details["custom_flag"] is True
        assert err.code == "DEADLINE_EXCEEDED"
