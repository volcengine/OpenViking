# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Tests for file-system service coordination behavior."""

import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from openviking.server.identity import RequestContext, Role
from openviking.service.fs_service import FSService
from openviking_cli.session.user_id import UserIdentifier


class _FakeVikingFS:
    def __init__(self, *, rm_error=None, events=None):
        self.rm_calls = []
        self.mv_calls = []
        self.cp_calls = []
        self.rm_error = rm_error
        self.events = events

    async def rm(self, uri, recursive=False, ctx=None):
        self.rm_calls.append({"uri": uri, "recursive": recursive, "ctx": ctx})
        if self.rm_error:
            raise self.rm_error
        return {"estimated_deleted_count": 3}

    async def mv(self, from_uri, to_uri, ctx=None):
        self.mv_calls.append({"from_uri": from_uri, "to_uri": to_uri, "ctx": ctx})
        if self.events is not None:
            self.events.append(("mv", from_uri, to_uri))

    async def cp(self, from_uri, to_uri, recursive=False, ctx=None):
        self.cp_calls.append(
            {
                "from_uri": from_uri,
                "to_uri": to_uri,
                "recursive": recursive,
                "ctx": ctx,
            }
        )
        if self.events is not None:
            self.events.append(("cp", from_uri, to_uri))
        return {
            "operation_id": "copy-1",
            "operation": "copy",
            "from": from_uri,
            "to": to_uri,
            "recursive": recursive,
        }


class _FakeMutationCoordinator:
    def __init__(self, events=None):
        self.calls = []
        self.events = events

    @asynccontextmanager
    async def mutation(self, account_id, uris):
        self.calls.append({"account_id": account_id, "uris": list(uris)})
        if self.events is not None:
            self.events.append(("mutation-enter", *uris))
        try:
            yield
        finally:
            if self.events is not None:
                self.events.append(("mutation-exit", *uris))


@pytest.mark.asyncio
async def test_write_forwards_tags_and_tag_mode_to_content_coordinator(
    monkeypatch, request_context
):
    seen = {}

    class FakeCoordinator:
        def __init__(self, **_kwargs):
            pass

        async def write(self, **kwargs):
            seen.update(kwargs)
            return {"uri": kwargs["uri"]}

    monkeypatch.setattr("openviking.service.fs_service.ContentWriteCoordinator", FakeCoordinator)
    service = FSService(viking_fs=object())

    await service.write(
        "viking://resources/demo.md",
        "updated",
        request_context,
        tags=["env=prod"],
        tag_mode="append",
    )

    assert seen["tags"] == ["env=prod"]
    assert seen["tag_mode"] == "append"


class _FakeWatchManager:
    def __init__(self, *, events=None):
        self.validate_calls = []
        self.rewrite_calls = []
        self.deactivate_calls = []
        self.validate_error = None
        self.events = events

    async def validate_target_prefix_rewrite_internal(self, from_uri, to_uri, account_id):
        self.validate_calls.append(
            {"from_uri": from_uri, "to_uri": to_uri, "account_id": account_id}
        )
        if self.events is not None:
            self.events.append(("validate", from_uri, to_uri))
        if self.validate_error:
            raise self.validate_error

    async def rewrite_target_prefix_internal(self, from_uri, to_uri, account_id):
        self.rewrite_calls.append(
            {"from_uri": from_uri, "to_uri": to_uri, "account_id": account_id}
        )
        if self.events is not None:
            self.events.append(("rewrite", from_uri, to_uri))
        return [SimpleNamespace(task_id="watch-1")]

    async def deactivate_tasks_under_uri_internal(self, uri, account_id):
        self.deactivate_calls.append({"uri": uri, "account_id": account_id})
        return [SimpleNamespace(task_id="watch-1")]


class _FakeResourceMemoryLinkService:
    def __init__(self, result):
        self.result = result
        self.calls = []

    async def before_resource_delete(self, *, ctx, resource_uri, recursive=False):
        self.calls.append({"ctx": ctx, "resource_uri": resource_uri, "recursive": recursive})
        return self.result


class _FakeWatchScheduler:
    def __init__(self, watch_manager):
        self.watch_manager = watch_manager


class _FakeWaitTracker:
    def __init__(self):
        self.registered_requests = []
        self.registered_roots = []
        self.wait_calls = []
        self.cleaned = []

    def register_request(self, telemetry_id):
        self.registered_requests.append(telemetry_id)

    def register_semantic_root(self, telemetry_id, root_id):
        self.registered_roots.append(
            {
                "telemetry_id": telemetry_id,
                "root_id": root_id,
                "request_was_registered": telemetry_id in self.registered_requests,
            }
        )

    async def wait_for_request(self, telemetry_id, timeout=None):
        self.wait_calls.append((telemetry_id, timeout))

    def build_queue_status(self, telemetry_id):
        return {
            "Semantic": {"processed": 1, "error_count": 0, "errors": []},
            "Embedding": {"processed": 0, "error_count": 0, "errors": []},
        }

    def mark_semantic_failed(self, telemetry_id, root_id, message):
        pass

    def cleanup(self, telemetry_id):
        self.cleaned.append(telemetry_id)


class _FakeQueueManager:
    SEMANTIC = "semantic"

    def __init__(self):
        self.messages = []

    def get_queue(self, name, allow_create=False):
        assert name == self.SEMANTIC
        assert allow_create is True
        return self

    async def enqueue(self, msg):
        self.messages.append(msg)


@pytest.fixture
def request_context():
    return RequestContext(
        user=UserIdentifier("default", "ryoma"),
        role=Role.USER,
    )


@pytest.mark.asyncio
async def test_read_visible_strips_memory_metadata_before_slicing(request_context):
    raw = 'line one\nline two\n\n<!-- MEMORY_FIELDS\n{"secret":"hidden"}\n-->'
    viking_fs = SimpleNamespace(read_file=AsyncMock(return_value=raw))
    service = FSService(viking_fs=viking_fs)
    uri = "viking://user/ryoma/memories/notes/private.md"

    assert await service.read_visible(uri, ctx=request_context, offset=3, limit=1) == ""
    viking_fs.read_file.assert_awaited_once_with(uri, ctx=request_context)


@pytest.mark.parametrize(
    "raw",
    [
        'visible\n<!-- MEMORY_FIELDS\n{"secret":"hidden"}\n-->',
        'visible\n\n<!-- MEMORY_FIELDS {"secret":"hidden"} -->',
        'visible <!-- MEMORY_FIELDS {"secret":"hidden"} -->',
    ],
)
@pytest.mark.asyncio
async def test_read_visible_strips_supported_memory_metadata_trailers(
    request_context,
    raw,
):
    uri = "viking://user/ryoma/memories/notes/private.md"
    service = FSService(
        viking_fs=SimpleNamespace(read_file=AsyncMock(return_value=raw)),
    )

    assert await service.read_visible(uri, ctx=request_context) == "visible"


@pytest.mark.asyncio
async def test_read_visible_preserves_non_memory_content(request_context):
    raw = 'visible\n<!-- MEMORY_FIELDS {"example":true} -->'
    viking_fs = SimpleNamespace(read_file=AsyncMock(return_value=raw))
    service = FSService(viking_fs=viking_fs)

    assert (
        await service.read_visible(
            "viking://resources/example.md",
            ctx=request_context,
            offset=1,
            limit=1,
        )
        == '<!-- MEMORY_FIELDS {"example":true} -->'
    )


@pytest.mark.asyncio
async def test_grep_projects_memory_content_but_keeps_resource_fast_path(request_context):
    viking_fs = SimpleNamespace(grep=AsyncMock(return_value={"matches": []}))
    service = FSService(viking_fs=viking_fs)

    await service.grep(
        "viking://user/ryoma/memories",
        "secret",
        ctx=request_context,
    )
    memory_kwargs = viking_fs.grep.await_args.kwargs
    transform = memory_kwargs["content_transform"]
    assert (
        transform(
            'visible\n<!-- MEMORY_FIELDS {"secret":"hidden"} -->',
            "viking://user/ryoma/memories/private.md",
        )
        == "visible"
    )

    viking_fs.grep.reset_mock()
    await service.grep("viking://resources", "secret", ctx=request_context)
    assert "content_transform" not in viking_fs.grep.await_args.kwargs


@pytest.mark.asyncio
async def test_grep_projects_tags_for_each_match(request_context):
    matches = [
        {"uri": "viking://resources/a.md", "line": 1, "content": "needle"},
        {"uri": "viking://resources/b.md", "line": 2, "content": "needle"},
    ]
    viking_fs = SimpleNamespace(grep=AsyncMock(return_value={"matches": matches, "count": 2}))

    class FakeVikingDB:
        async def filter(self, **_kwargs):
            return [
                {
                    "uri": "viking://resources/a.md",
                    "level": 2,
                    "search_tags": ["team=search", "env=prod"],
                },
                {
                    "uri": "viking://resources/b.md",
                    "level": 2,
                    "search_tags": [],
                },
            ]

    service = FSService(viking_fs=viking_fs, vikingdb=FakeVikingDB())
    result = await service.grep(
        "viking://resources", "needle", ctx=request_context, include_tags=True
    )

    assert result["matches"] == [
        {
            "uri": "viking://resources/a.md",
            "line": 1,
            "content": "needle",
            "tags": ["team=search", "env=prod"],
        },
        {
            "uri": "viking://resources/b.md",
            "line": 2,
            "content": "needle",
            "tags": [],
        },
    ]


@pytest.mark.asyncio
async def test_grep_skips_tag_projection_without_tags_or_include_tags(request_context):
    matches = [{"uri": "viking://resources/a.md", "line": 1, "content": "needle"}]
    viking_fs = SimpleNamespace(grep=AsyncMock(return_value={"matches": matches, "count": 1}))

    class FakeVikingDB:
        async def filter(self, **_kwargs):
            raise AssertionError("plain grep should not load tags")

    service = FSService(viking_fs=viking_fs, vikingdb=FakeVikingDB())

    result = await service.grep("viking://resources", "needle", ctx=request_context)

    assert result["matches"] == matches


@pytest.mark.asyncio
async def test_grep_projects_tags_when_include_tags_is_requested(request_context):
    matches = [{"uri": "viking://resources/a.md", "line": 1, "content": "needle"}]
    viking_fs = SimpleNamespace(grep=AsyncMock(return_value={"matches": matches, "count": 1}))

    class FakeVikingDB:
        async def filter(self, **_kwargs):
            return [{"uri": "viking://resources/a.md", "level": 2, "search_tags": ["env=prod"]}]

    service = FSService(viking_fs=viking_fs, vikingdb=FakeVikingDB())

    result = await service.grep(
        "viking://resources", "needle", ctx=request_context, include_tags=True
    )

    assert result["matches"] == [{**matches[0], "tags": ["env=prod"]}]


@pytest.mark.asyncio
async def test_ls_and_tree_skip_tag_projection_without_tags_or_include_tags(request_context):
    entries = [{"uri": "viking://resources/a.md", "isDir": False}]
    viking_fs = SimpleNamespace(
        ls=AsyncMock(return_value=entries),
        tree=AsyncMock(return_value=entries),
    )

    class FakeVikingDB:
        async def filter(self, **_kwargs):
            raise AssertionError("plain filesystem reads should not load tags")

    service = FSService(viking_fs=viking_fs, vikingdb=FakeVikingDB())

    assert await service.ls("viking://resources", ctx=request_context) == entries
    assert await service.tree("viking://resources", ctx=request_context) == entries


@pytest.mark.asyncio
async def test_glob_skips_tag_projection_without_tags_or_include_tags(request_context):
    expected = {"matches": ["viking://resources/a.md"], "count": 1}
    viking_fs = SimpleNamespace(glob=AsyncMock(return_value=expected))

    class FakeVikingDB:
        async def filter(self, **_kwargs):
            raise AssertionError("plain glob should not load tags")

    service = FSService(viking_fs=viking_fs, vikingdb=FakeVikingDB())

    assert await service.glob("**/*.md", ctx=request_context) == expected
    assert viking_fs.glob.await_args.kwargs["extra_fields"] is None


@pytest.mark.asyncio
async def test_glob_filters_and_projects_tags_before_applying_node_limit(request_context):
    entries = [
        {"uri": "viking://resources/a.md", "isDir": False},
        {"uri": "viking://resources/b.md", "isDir": False},
        {"uri": "viking://resources/c.md", "isDir": False},
    ]
    viking_fs = SimpleNamespace(glob=AsyncMock(return_value={"matches": entries, "count": 3}))

    class FakeVikingDB:
        async def filter(self, **_kwargs):
            return [
                {"uri": "viking://resources/a.md", "level": 2, "search_tags": ["team=search"]},
                {
                    "uri": "viking://resources/b.md",
                    "level": 2,
                    "search_tags": ["team=search", "env=prod"],
                },
                {
                    "uri": "viking://resources/c.md",
                    "level": 2,
                    "search_tags": ["team=search", "env=prod"],
                },
            ]

    service = FSService(viking_fs=viking_fs, vikingdb=FakeVikingDB())
    result = await service.glob(
        "**/*.md",
        ctx=request_context,
        tags=["team=search", "env=prod"],
        node_limit=1,
    )

    assert result == {
        "matches": [
            {
                "uri": "viking://resources/b.md",
                "isDir": False,
                "tags": ["team=search", "env=prod"],
            }
        ],
        "count": 1,
    }
    assert viking_fs.glob.await_args.kwargs["extra_fields"] == []
    assert viking_fs.glob.await_args.kwargs["node_limit"] is None


@pytest.mark.asyncio
async def test_glob_tag_filter_keeps_zero_node_limit_unbounded(request_context):
    entries = [
        {"uri": "viking://resources/a.md", "isDir": False},
        {"uri": "viking://resources/b.md", "isDir": False},
    ]
    viking_fs = SimpleNamespace(glob=AsyncMock(return_value={"matches": entries, "count": 2}))

    class FakeVikingDB:
        async def filter(self, **_kwargs):
            return [
                {"uri": "viking://resources/a.md", "level": 2, "search_tags": ["env=prod"]},
                {"uri": "viking://resources/b.md", "level": 2, "search_tags": ["env=prod"]},
            ]

    service = FSService(viking_fs=viking_fs, vikingdb=FakeVikingDB())
    result = await service.glob("**/*.md", ctx=request_context, tags=["env=prod"], node_limit=0)

    assert result["count"] == 2
    assert [entry["uri"] for entry in result["matches"]] == [
        "viking://resources/a.md",
        "viking://resources/b.md",
    ]


@pytest.mark.asyncio
async def test_grep_passes_tags_to_vikingfs_without_prequerying_vikingdb(request_context):
    viking_fs = SimpleNamespace(grep=AsyncMock(return_value={"matches": [], "count": 0}))

    class FakeVikingDB:
        async def filter(self, **_kwargs):
            raise AssertionError("tagged grep should not pre-query VikingDB")

    service = FSService(viking_fs=viking_fs, vikingdb=FakeVikingDB())
    await service.grep(
        "viking://resources",
        "needle",
        ctx=request_context,
        tags=["team=search", "env=prod"],
    )

    assert viking_fs.grep.await_args.kwargs["tag_filter"] == {
        "op": "and",
        "conds": [
            {"op": "must", "field": "search_tags", "conds": ["team=search"]},
            {"op": "must", "field": "search_tags", "conds": ["env=prod"]},
        ],
    }


@pytest.mark.asyncio
async def test_tagged_grep_reuses_tags_returned_by_viking_fs(request_context):
    matches = [
        {
            "uri": "viking://resources/a.md",
            "line": 1,
            "content": "needle",
            "tags": ["team=search", "env=prod"],
        }
    ]
    viking_fs = SimpleNamespace(grep=AsyncMock(return_value={"matches": matches, "count": 1}))

    class FakeVikingDB:
        async def filter(self, **_kwargs):
            raise AssertionError("tagged grep should reuse VikingFS tag projection")

    service = FSService(viking_fs=viking_fs, vikingdb=FakeVikingDB())
    result = await service.grep(
        "viking://resources",
        "needle",
        ctx=request_context,
        tags=["team=search", "env=prod"],
    )

    assert result["matches"] == matches


@pytest.mark.asyncio
async def test_ls_projects_tags_filters_with_and_before_applying_node_limit(request_context):
    entries = [
        {"uri": "viking://resources/a.md", "isDir": False},
        {"uri": "viking://resources/b.md", "isDir": False},
        {"uri": "viking://resources/c.md", "isDir": False},
    ]
    viking_fs = SimpleNamespace(ls=AsyncMock(return_value=entries))

    class FakeVikingDB:
        async def filter(self, **_kwargs):
            return [
                {"uri": "viking://resources/a.md", "level": 2, "search_tags": ["team=search"]},
                {
                    "uri": "viking://resources/b.md",
                    "level": 2,
                    "search_tags": ["team=search", "env=prod"],
                },
                {
                    "uri": "viking://resources/c.md",
                    "level": 2,
                    "search_tags": ["team=search", "env=prod"],
                },
            ]

    service = FSService(viking_fs=viking_fs, vikingdb=FakeVikingDB())
    result = await service.ls(
        "viking://resources",
        ctx=request_context,
        tags=["team=search", "env=prod"],
        node_limit=1,
    )

    assert result == [
        {"uri": "viking://resources/b.md", "isDir": False, "tags": ["team=search", "env=prod"]}
    ]
    assert viking_fs.ls.await_args.kwargs["node_limit"] is None


@pytest.mark.asyncio
async def test_ls_tag_filter_keeps_zero_node_limit_unbounded_for_entry_and_simple_output(
    request_context,
):
    entries = [
        {"uri": "viking://resources/a.md", "isDir": False},
        {"uri": "viking://resources/b.md", "isDir": False},
    ]

    class FakeVikingDB:
        async def filter(self, **_kwargs):
            return [
                {"uri": entry["uri"], "level": 2, "search_tags": ["env=prod"]} for entry in entries
            ]

    entry_service = FSService(
        viking_fs=SimpleNamespace(ls=AsyncMock(return_value=entries)), vikingdb=FakeVikingDB()
    )
    entry_result = await entry_service.ls(
        "viking://resources", ctx=request_context, tags=["env=prod"], node_limit=0
    )

    simple_service = FSService(
        viking_fs=SimpleNamespace(ls=AsyncMock(return_value=entries)), vikingdb=FakeVikingDB()
    )
    simple_result = await simple_service.ls(
        "viking://resources",
        ctx=request_context,
        simple=True,
        tags=["env=prod"],
        node_limit=0,
    )

    assert [entry["uri"] for entry in entry_result] == [
        "viking://resources/a.md",
        "viking://resources/b.md",
    ]
    assert simple_result == ["viking://resources/a.md", "viking://resources/b.md"]


@pytest.mark.asyncio
async def test_tree_projects_directory_tags_from_abstract_and_overview_records(request_context):
    directory = {"uri": "viking://resources/docs", "isDir": True}
    viking_fs = SimpleNamespace(tree=AsyncMock(return_value=[directory]))

    class FakeVikingDB:
        async def filter(self, **_kwargs):
            return [
                {"uri": "viking://resources/docs", "level": 0, "search_tags": ["team=search"]},
                {
                    "uri": "viking://resources/docs",
                    "level": 1,
                    "search_tags": ["env=prod", "team=search"],
                },
                {"uri": "viking://resources/docs", "level": 2, "search_tags": ["ignored=true"]},
            ]

    service = FSService(viking_fs=viking_fs, vikingdb=FakeVikingDB())
    result = await service.tree(
        "viking://resources",
        ctx=request_context,
        tags=["team=search", "env=prod"],
    )

    assert result == [
        {"uri": "viking://resources/docs", "isDir": True, "tags": ["team=search", "env=prod"]}
    ]
    assert viking_fs.tree.await_args.kwargs["node_limit"] is None


@pytest.mark.asyncio
async def test_tree_tag_filter_keeps_zero_node_limit_unbounded(request_context):
    entries = [
        {"uri": "viking://resources/a.md", "isDir": False},
        {"uri": "viking://resources/b.md", "isDir": False},
    ]
    viking_fs = SimpleNamespace(tree=AsyncMock(return_value=entries))

    class FakeVikingDB:
        async def filter(self, **_kwargs):
            return [
                {"uri": entry["uri"], "level": 2, "search_tags": ["env=prod"]} for entry in entries
            ]

    service = FSService(viking_fs=viking_fs, vikingdb=FakeVikingDB())
    result = await service.tree(
        "viking://resources", ctx=request_context, tags=["env=prod"], node_limit=0
    )

    assert [entry["uri"] for entry in result] == [
        "viking://resources/a.md",
        "viking://resources/b.md",
    ]
    assert viking_fs.tree.await_args.kwargs["node_limit"] is None


@pytest.mark.asyncio
async def test_resource_rm_enqueues_parent_delete_refresh_and_waits(request_context):
    viking_fs = _FakeVikingFS()
    service = FSService(viking_fs=viking_fs)
    service._enqueue_delete_refresh = AsyncMock()
    service._wait_for_refresh = AsyncMock(return_value={"Semantic": {"pending_count": 0}})

    uri = "viking://resources/images/2026/06/10/不二周助_jpeg"
    result = await service.rm(
        uri,
        ctx=request_context,
        recursive=True,
        wait=True,
        timeout=12.0,
    )

    assert viking_fs.rm_calls == [{"uri": uri, "recursive": True, "ctx": request_context}]
    service._enqueue_delete_refresh.assert_awaited_once_with(
        root_uri="viking://resources/images/2026/06/10",
        deleted_uri=uri,
        context_type="resource",
        ctx=request_context,
        force_refresh=True,
    )
    service._wait_for_refresh.assert_awaited_once_with(timeout=12.0)
    assert result["semantic_root_uri"] == "viking://resources/images/2026/06/10"
    assert result["semantic_status"] == "complete"
    assert result["queue_status"] == {"Semantic": {"pending_count": 0}}


@pytest.mark.asyncio
async def test_resource_rm_reports_failed_semantic_status_when_wait_queue_has_errors(
    request_context,
):
    viking_fs = _FakeVikingFS()
    service = FSService(viking_fs=viking_fs)
    service._enqueue_delete_refresh = AsyncMock()
    service._wait_for_refresh = AsyncMock(
        return_value={
            "Semantic": {
                "processed": 1,
                "error_count": 1,
                "errors": [{"message": "refresh failed"}],
            }
        }
    )

    result = await service.rm(
        "viking://resources/images/2026/06/10/不二周助_jpeg",
        ctx=request_context,
        recursive=True,
        wait=True,
    )

    assert result["semantic_status"] == "failed"


@pytest.mark.asyncio
async def test_resource_rm_without_wait_only_queues_refresh(request_context):
    viking_fs = _FakeVikingFS()
    service = FSService(viking_fs=viking_fs)
    service._enqueue_delete_refresh = AsyncMock()
    service._wait_for_refresh = AsyncMock()

    uri = "viking://resources/images/2026/06/10/不二周助_jpeg"
    result = await service.rm(uri, ctx=request_context, recursive=True)

    service._enqueue_delete_refresh.assert_awaited_once()
    service._wait_for_refresh.assert_not_awaited()
    assert result["semantic_status"] == "queued"


@pytest.mark.asyncio
async def test_resource_scope_rm_does_not_refresh_global_root(request_context):
    service = FSService(viking_fs=_FakeVikingFS())
    service._enqueue_delete_refresh = AsyncMock()
    service._wait_for_refresh = AsyncMock()

    result = await service.rm(
        "viking://resources",
        ctx=request_context,
        recursive=True,
        wait=True,
    )

    service._enqueue_delete_refresh.assert_not_awaited()
    service._wait_for_refresh.assert_not_awaited()
    assert "semantic_root_uri" not in result


@pytest.mark.asyncio
async def test_resource_rm_deactivates_watch_tasks(request_context):
    viking_fs = _FakeVikingFS()
    watch_manager = _FakeWatchManager()
    service = FSService(
        viking_fs=viking_fs,
        watch_scheduler=_FakeWatchScheduler(watch_manager),
    )
    service._enqueue_delete_refresh = AsyncMock()

    await service.rm("viking://resources/codeask/wiki", ctx=request_context, recursive=True)

    assert watch_manager.deactivate_calls == [
        {"uri": "viking://resources/codeask/wiki", "account_id": "default"}
    ]


@pytest.mark.asyncio
async def test_resource_rm_does_not_deactivate_watch_task_control_uri(request_context):
    viking_fs = _FakeVikingFS()
    watch_manager = _FakeWatchManager()
    service = FSService(
        viking_fs=viking_fs,
        watch_scheduler=_FakeWatchScheduler(watch_manager),
    )

    await service.rm("viking://resources/.watch_tasks.json", ctx=request_context)

    assert watch_manager.deactivate_calls == []


@pytest.mark.asyncio
async def test_resource_mv_validates_then_moves_then_rewrites_watch_tasks(request_context):
    events = []
    viking_fs = _FakeVikingFS(events=events)
    watch_manager = _FakeWatchManager(events=events)
    service = FSService(
        viking_fs=viking_fs,
        watch_scheduler=_FakeWatchScheduler(watch_manager),
    )

    async def enqueue_refresh(**kwargs):
        events.append(("refresh", kwargs["root_uri"]))
        assert kwargs == {
            "root_uri": "viking://resources/codeask",
            "source_uri": "viking://resources/codeask/wiki",
            "copied_uri": "viking://resources/codeask/wiki-renamed",
            "change_kind": "added",
            "context_type": "resource",
            "ctx": request_context,
        }
        return "queued"

    service._enqueue_copy_refresh = enqueue_refresh

    await service.mv(
        "viking://resources/codeask/wiki",
        "viking://resources/codeask/wiki-renamed",
        ctx=request_context,
    )

    expected_watch_call = [
        {
            "from_uri": "viking://resources/codeask/wiki",
            "to_uri": "viking://resources/codeask/wiki-renamed",
            "account_id": "default",
        }
    ]
    assert watch_manager.validate_calls == expected_watch_call
    assert watch_manager.rewrite_calls == expected_watch_call
    assert viking_fs.mv_calls == [
        {
            "from_uri": "viking://resources/codeask/wiki",
            "to_uri": "viking://resources/codeask/wiki-renamed",
            "ctx": request_context,
        }
    ]
    assert [event[0] for event in events] == ["validate", "mv", "rewrite", "refresh"]


@pytest.mark.asyncio
async def test_resource_mv_conflict_fails_before_resource_move(request_context):
    viking_fs = _FakeVikingFS()
    watch_manager = _FakeWatchManager()
    watch_manager.validate_error = RuntimeError("watch conflict")
    service = FSService(
        viking_fs=viking_fs,
        watch_scheduler=_FakeWatchScheduler(watch_manager),
    )

    with pytest.raises(RuntimeError, match="watch conflict"):
        await service.mv(
            "viking://resources/codeask/wiki",
            "viking://resources/codeask/wiki-renamed",
            ctx=request_context,
        )

    assert viking_fs.mv_calls == []
    assert watch_manager.rewrite_calls == []


@pytest.mark.asyncio
async def test_resource_mv_without_watch_scheduler_moves_resource_directly(request_context):
    events = []
    viking_fs = _FakeVikingFS(events=events)
    service = FSService(viking_fs=viking_fs)
    refresh_calls = []

    async def enqueue_refresh(**kwargs):
        events.append(("refresh", kwargs["root_uri"]))
        refresh_calls.append(kwargs)
        return "queued"

    service._enqueue_copy_refresh = enqueue_refresh

    await service.mv(
        "viking://resources/codeask/wiki",
        "viking://resources/archive/wiki",
        ctx=request_context,
    )

    assert viking_fs.mv_calls == [
        {
            "from_uri": "viking://resources/codeask/wiki",
            "to_uri": "viking://resources/archive/wiki",
            "ctx": request_context,
        }
    ]
    assert refresh_calls == [
        {
            "root_uri": "viking://resources/codeask",
            "source_uri": "viking://resources/codeask/wiki",
            "copied_uri": "viking://resources/codeask/wiki",
            "change_kind": "deleted",
            "context_type": "resource",
            "ctx": request_context,
        },
        {
            "root_uri": "viking://resources/archive",
            "source_uri": "viking://resources/codeask/wiki",
            "copied_uri": "viking://resources/archive/wiki",
            "change_kind": "added",
            "context_type": "resource",
            "ctx": request_context,
        },
    ]
    assert [event[0] for event in events] == ["mv", "refresh", "refresh"]


@pytest.mark.asyncio
async def test_resource_mv_watch_control_file_skips_parent_refresh(request_context):
    viking_fs = _FakeVikingFS()
    service = FSService(
        viking_fs=viking_fs,
        watch_scheduler=_FakeWatchScheduler(_FakeWatchManager()),
    )
    service._enqueue_copy_refresh = AsyncMock()

    await service.mv(
        "viking://resources/.watch_tasks.json",
        "viking://resources/watch-tasks-backup.json",
        ctx=request_context,
    )

    service._enqueue_copy_refresh.assert_not_awaited()


@pytest.mark.asyncio
async def test_resource_cp_coordinates_mutation_without_copying_watch_tasks(request_context):
    source = "viking://resources/codeask/wiki"
    target = "viking://resources/archive/wiki"
    events = []
    viking_fs = _FakeVikingFS(events=events)
    coordinator = _FakeMutationCoordinator(events=events)
    watch_manager = _FakeWatchManager(events=events)
    service = FSService(
        viking_fs=viking_fs,
        watch_scheduler=_FakeWatchScheduler(watch_manager),
        uri_mutation_coordinator=coordinator,
    )

    async def enqueue_refresh(**kwargs):
        events.append(("refresh", kwargs["root_uri"]))
        assert kwargs == {
            "root_uri": "viking://resources/archive",
            "source_uri": source,
            "copied_uri": target,
            "context_type": "resource",
            "ctx": request_context,
        }
        return "queued"

    service._enqueue_copy_refresh = enqueue_refresh

    result = await service.cp(source, target, recursive=True, ctx=request_context)

    assert coordinator.calls == [{"account_id": "default", "uris": [source, target]}]
    assert viking_fs.cp_calls == [
        {
            "from_uri": source,
            "to_uri": target,
            "recursive": True,
            "ctx": request_context,
        }
    ]
    assert [event[0] for event in events] == [
        "mutation-enter",
        "cp",
        "mutation-exit",
        "refresh",
    ]
    assert watch_manager.validate_calls == []
    assert watch_manager.rewrite_calls == []
    assert result["from"] == source
    assert result["to"] == target
    assert result["semantic_root_uri"] == "viking://resources/archive"
    assert result["semantic_status"] == "queued"


@pytest.mark.asyncio
async def test_resource_cp_refresh_failure_does_not_roll_back_copy(request_context):
    source = "viking://resources/source.md"
    target = "viking://resources/archive/copied.md"
    viking_fs = _FakeVikingFS()
    service = FSService(viking_fs=viking_fs)
    service._enqueue_copy_refresh = AsyncMock(side_effect=RuntimeError("queue unavailable"))

    result = await service.cp(source, target, recursive=False, ctx=request_context)

    assert len(viking_fs.cp_calls) == 1
    assert viking_fs.mv_calls == []
    assert result["semantic_status"] == "failed"
    assert result["semantic_error"] == "queue unavailable"


@pytest.mark.asyncio
async def test_resource_cp_finishes_transfer_and_refresh_after_caller_cancel(request_context):
    source = "viking://resources/source.md"
    target = "viking://resources/archive/copied.md"
    started = asyncio.Event()
    release = asyncio.Event()
    completed = asyncio.Event()
    viking_fs = _FakeVikingFS()

    async def blocked_cp(from_uri, to_uri, recursive=False, ctx=None):
        del from_uri, to_uri, recursive, ctx
        started.set()
        await release.wait()
        completed.set()
        return {"operation_id": "copy-cancel"}

    viking_fs.cp = blocked_cp
    service = FSService(viking_fs=viking_fs)
    service._enqueue_copy_refresh = AsyncMock(return_value="queued")

    task = asyncio.create_task(service.cp(source, target, recursive=False, ctx=request_context))
    await started.wait()
    task.cancel()
    await asyncio.sleep(0)
    release.set()

    with pytest.raises(asyncio.CancelledError):
        await task

    assert completed.is_set()
    service._enqueue_copy_refresh.assert_awaited_once()


@pytest.mark.asyncio
async def test_resource_cp_finishes_inflight_refresh_after_caller_cancel(request_context):
    source = "viking://resources/source.md"
    target = "viking://resources/archive/copied.md"
    refresh_started = asyncio.Event()
    release_refresh = asyncio.Event()
    refresh_completed = asyncio.Event()
    service = FSService(viking_fs=_FakeVikingFS())

    async def blocked_refresh(**kwargs):
        del kwargs
        refresh_started.set()
        await release_refresh.wait()
        refresh_completed.set()
        return "queued"

    service._enqueue_copy_refresh = blocked_refresh
    task = asyncio.create_task(service.cp(source, target, recursive=False, ctx=request_context))
    await refresh_started.wait()
    task.cancel()
    await asyncio.sleep(0)
    release_refresh.set()

    with pytest.raises(asyncio.CancelledError):
        await task

    assert refresh_completed.is_set()


@pytest.mark.asyncio
async def test_resource_mv_finishes_transfer_and_refresh_after_caller_cancel(request_context):
    source = "viking://resources/source.md"
    target = "viking://resources/archive/moved.md"
    started = asyncio.Event()
    release = asyncio.Event()
    completed = asyncio.Event()
    viking_fs = _FakeVikingFS()

    async def blocked_mv(from_uri, to_uri, ctx=None):
        del from_uri, to_uri, ctx
        started.set()
        await release.wait()
        completed.set()

    viking_fs.mv = blocked_mv
    service = FSService(viking_fs=viking_fs)
    service._enqueue_copy_refresh = AsyncMock(return_value="queued")

    task = asyncio.create_task(service.mv(source, target, ctx=request_context))
    await started.wait()
    task.cancel()
    await asyncio.sleep(0)
    release.set()

    with pytest.raises(asyncio.CancelledError):
        await task

    assert completed.is_set()
    assert service._enqueue_copy_refresh.await_count == 2


@pytest.mark.asyncio
async def test_non_resource_cp_skips_parent_semantic_refresh(request_context):
    viking_fs = _FakeVikingFS()
    service = FSService(viking_fs=viking_fs)
    service._enqueue_copy_refresh = AsyncMock()

    result = await service.cp(
        "viking://user/ryoma/memories/source.md",
        "viking://user/ryoma/memories/copied.md",
        recursive=False,
        ctx=request_context,
    )

    service._enqueue_copy_refresh.assert_not_awaited()
    assert "semantic_root_uri" not in result


@pytest.mark.asyncio
async def test_copy_refresh_message_only_rebuilds_parent_semantics(
    request_context,
    monkeypatch,
):
    service = FSService(viking_fs=_FakeVikingFS())
    queue_manager = _FakeQueueManager()
    mark_pending = AsyncMock()
    monkeypatch.setattr(
        "openviking.service.fs_service.get_queue_manager",
        lambda: queue_manager,
    )
    monkeypatch.setattr(
        "openviking.service.fs_service.mark_abstract_overview_pending",
        mark_pending,
    )

    status = await service._enqueue_copy_refresh(
        root_uri="viking://resources/archive",
        source_uri="viking://resources/source.md",
        copied_uri="viking://resources/archive/copied.md",
        context_type="resource",
        ctx=request_context,
    )

    assert status == "queued"
    assert len(queue_manager.messages) == 1
    msg = queue_manager.messages[0]
    assert msg.uri == "viking://resources/archive"
    assert msg.recursive is False
    assert msg.skip_vectorization is False
    assert msg.changes == {"added": ["viking://resources/archive/copied.md"]}
    assert msg.generation_trigger == "content_copy"
    assert msg.copy_source_uri == "viking://resources/source.md"


@pytest.mark.asyncio
async def test_transfer_refresh_message_records_deleted_source_entry(
    request_context,
    monkeypatch,
):
    service = FSService(viking_fs=_FakeVikingFS())
    queue_manager = _FakeQueueManager()
    monkeypatch.setattr(
        "openviking.service.fs_service.get_queue_manager",
        lambda: queue_manager,
    )
    monkeypatch.setattr(
        "openviking.service.fs_service.mark_abstract_overview_pending",
        AsyncMock(),
    )

    status = await service._enqueue_copy_refresh(
        root_uri="viking://resources/source",
        source_uri="viking://resources/source/moved.md",
        copied_uri="viking://resources/source/moved.md",
        change_kind="deleted",
        context_type="resource",
        ctx=request_context,
    )

    assert status == "queued"
    assert queue_manager.messages[0].changes == {"deleted": ["viking://resources/source/moved.md"]}


@pytest.mark.asyncio
async def test_resource_rm_wait_registers_request_before_semantic_root(
    request_context,
    monkeypatch,
):
    viking_fs = _FakeVikingFS()
    service = FSService(viking_fs=viking_fs)
    tracker = _FakeWaitTracker()
    queue_manager = _FakeQueueManager()

    monkeypatch.setattr(
        "openviking.service.fs_service.get_current_telemetry",
        lambda: SimpleNamespace(telemetry_id="tm-fs-rm"),
    )
    monkeypatch.setattr(
        "openviking.service.fs_service.get_request_wait_tracker",
        lambda: tracker,
    )
    monkeypatch.setattr(
        "openviking.service.fs_service.get_queue_manager",
        lambda: queue_manager,
    )

    result = await service.rm(
        "viking://resources/images/2026/06/10/不二周助_jpeg",
        ctx=request_context,
        recursive=True,
        wait=True,
        timeout=3,
    )

    assert tracker.registered_requests == ["tm-fs-rm"]
    assert tracker.registered_roots
    assert tracker.registered_roots[0]["request_was_registered"] is True
    assert queue_manager.messages[0].recursive is False
    assert tracker.wait_calls == [("tm-fs-rm", 3)]
    assert tracker.cleaned == ["tm-fs-rm"]
    assert result["semantic_status"] == "complete"


@pytest.mark.asyncio
async def test_resource_rm_does_not_cleanup_memory_if_resource_delete_fails(request_context):
    delete_error = RuntimeError("delete failed")
    viking_fs = _FakeVikingFS(rm_error=delete_error)
    cleanup = {
        "status": "success",
        "memory_uris": ["viking://user/ryoma/memories/entities/动漫角色/越前龙马.md"],
    }
    link_service = _FakeResourceMemoryLinkService(cleanup)
    service = FSService(
        viking_fs=viking_fs,
        resource_memory_link_service=link_service,
    )

    with pytest.raises(RuntimeError, match="delete failed"):
        await service.rm(
            "viking://resources/images/2026/06/10/yueqian_jpeg",
            ctx=request_context,
            recursive=True,
        )

    assert link_service.calls == []


@pytest.mark.asyncio
async def test_resource_rm_refreshes_memory_overview_for_cleaned_memories(
    request_context,
    monkeypatch,
):
    cleanup = {
        "status": "success",
        "memory_uris": ["viking://user/ryoma/memories/entities/动漫角色/不二周助-write-test.md"],
        "deleted_memory_uris": [
            "viking://user/ryoma/memories/entities/动漫角色/不二周助-link-test2.md"
        ],
    }
    viking_fs = _FakeVikingFS()
    link_service = _FakeResourceMemoryLinkService(cleanup)
    service = FSService(
        viking_fs=viking_fs,
        resource_memory_link_service=link_service,
    )
    service._enqueue_delete_refresh = AsyncMock()

    refreshed = []

    async def fake_refresh_schema_overview(*, viking_fs, directory_uri, ctx):
        refreshed.append({"viking_fs": viking_fs, "directory_uri": directory_uri, "ctx": ctx})

    monkeypatch.setattr(
        "openviking.service.fs_service.MemoryUpdater.refresh_schema_overview",
        fake_refresh_schema_overview,
    )

    uri = "viking://resources/images/2026/06/11/不二周助_jpeg"
    result = await service.rm(uri, ctx=request_context, recursive=True)

    assert link_service.calls == [{"ctx": request_context, "resource_uri": uri, "recursive": True}]
    assert refreshed == [
        {
            "viking_fs": viking_fs,
            "directory_uri": "viking://user/ryoma/memories/entities/动漫角色",
            "ctx": request_context,
        }
    ]
    assert result["memory_cleanup"] == cleanup
