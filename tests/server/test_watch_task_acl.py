# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

"""Regression tests for watch-task control file access boundaries."""

import contextvars

import pytest

from openviking.resource.watch_storage import (
    WATCH_TASK_STORAGE_BAK_URI,
    WATCH_TASK_STORAGE_TMP_URI,
    WATCH_TASK_STORAGE_URI,
)
from openviking.server.identity import RequestContext, Role
from openviking.storage.content_write import ContentWriteCoordinator
from openviking.storage.viking_fs import VikingFS
from openviking_cli.exceptions import InvalidArgumentError
from openviking_cli.session.user_id import UserIdentifier


@pytest.fixture
def root_ctx() -> RequestContext:
    return RequestContext(user=UserIdentifier.the_default_user(), role=Role.ROOT)


@pytest.fixture
def user_ctx() -> RequestContext:
    return RequestContext(user=UserIdentifier("default", "alice", "default"), role=Role.USER)


@pytest.fixture
def bare_viking_fs() -> VikingFS:
    fs = object.__new__(VikingFS)
    fs._bound_ctx = contextvars.ContextVar("vikingfs_bound_ctx", default=None)
    return fs


@pytest.mark.parametrize(
    "uri",
    [
        WATCH_TASK_STORAGE_URI,
        WATCH_TASK_STORAGE_BAK_URI,
        WATCH_TASK_STORAGE_TMP_URI,
    ],
)
def test_watch_task_control_files_are_root_only(bare_viking_fs, root_ctx, user_ctx, uri):
    assert bare_viking_fs._is_accessible(uri, root_ctx) is True
    assert bare_viking_fs._is_accessible(uri, user_ctx) is False

    with pytest.raises(PermissionError):
        bare_viking_fs._ensure_access(uri, user_ctx)


@pytest.mark.asyncio
async def test_hidden_listing_filters_watch_task_control_files_for_non_root(
    bare_viking_fs, root_ctx, user_ctx
):
    bare_viking_fs._uri_to_path = lambda uri, ctx=None: "/fake/resources"
    bare_viking_fs._ctx_or_default = lambda ctx=None: ctx
    bare_viking_fs._ls_entries = lambda path: [
        {"name": ".watch_tasks.json", "isDir": False, "size": 10, "modTime": "2026-01-01T00:00:00+00:00"},
        {"name": ".watch_tasks.json.bak", "isDir": False, "size": 10, "modTime": "2026-01-01T00:00:00+00:00"},
        {"name": ".watch_tasks.json.tmp", "isDir": False, "size": 10, "modTime": "2026-01-01T00:00:00+00:00"},
        {"name": "public.txt", "isDir": False, "size": 5, "modTime": "2026-01-01T00:00:00+00:00"},
    ]
    bare_viking_fs._path_to_uri = lambda path, ctx=None: f"viking://resources/{path.split('/')[-1]}"

    root_entries = await bare_viking_fs._ls_original(
        "viking://resources",
        show_all_hidden=True,
        ctx=root_ctx,
    )
    root_uris = {entry["uri"] for entry in root_entries}
    assert root_uris >= {
        WATCH_TASK_STORAGE_URI,
        WATCH_TASK_STORAGE_BAK_URI,
        WATCH_TASK_STORAGE_TMP_URI,
        "viking://resources/public.txt",
    }

    user_entries = await bare_viking_fs._ls_original(
        "viking://resources",
        show_all_hidden=True,
        ctx=user_ctx,
    )
    user_uris = {entry["uri"] for entry in user_entries}
    assert "viking://resources/public.txt" in user_uris
    assert WATCH_TASK_STORAGE_URI not in user_uris
    assert WATCH_TASK_STORAGE_BAK_URI not in user_uris
    assert WATCH_TASK_STORAGE_TMP_URI not in user_uris


@pytest.mark.parametrize(
    "uri",
    [
        WATCH_TASK_STORAGE_URI,
        WATCH_TASK_STORAGE_BAK_URI,
        WATCH_TASK_STORAGE_TMP_URI,
    ],
)
def test_content_write_rejects_watch_task_control_files(uri):
    coordinator = object.__new__(ContentWriteCoordinator)

    with pytest.raises(InvalidArgumentError, match="watch task control file"):
        coordinator._validate_target_uri(uri)
