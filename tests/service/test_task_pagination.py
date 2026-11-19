# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Task cursors must stay valid across worker processes and restarts."""

import base64
import importlib
import json

import pytest

from openviking.service import task_pagination
from openviking_cli.exceptions import InvalidArgumentError


def test_cursor_remains_valid_when_another_worker_serves_the_next_page():
    scope = task_pagination.page_scope(account="a", user="u", status="running")
    cursor = task_pagination.encode_cursor({"created_at": 123.4, "task_id": "cmp_a"}, scope)

    # A second uvicorn worker (or a restart) imports the module independently.
    # Pagination must not depend on node-local state.
    worker_module = importlib.reload(importlib.import_module("openviking.service.task_pagination"))

    assert worker_module.decode_cursor(cursor, scope) == (123.4, "cmp_a")


def test_cursor_is_still_bound_to_scope_and_rejects_garbage():
    scope = task_pagination.page_scope(account="a", user="u")
    cursor = task_pagination.encode_cursor({"created_at": 1.0, "task_id": "t1"}, scope)

    with pytest.raises(InvalidArgumentError):
        task_pagination.decode_cursor(cursor, task_pagination.page_scope(account="a", user="x"))
    with pytest.raises(InvalidArgumentError):
        task_pagination.decode_cursor("not-a-cursor", scope)
    with pytest.raises(InvalidArgumentError):
        task_pagination.decode_cursor("x" * 3000, scope)


def test_cursor_with_out_of_range_timestamp_is_rejected_as_invalid():
    scope = task_pagination.page_scope(account="a", user="u")
    cursor = base64.urlsafe_b64encode(
        json.dumps({"scope": scope, "time": 10**400, "id": "t1"}).encode()
    ).decode()

    with pytest.raises(InvalidArgumentError):
        task_pagination.decode_cursor(cursor, scope)
