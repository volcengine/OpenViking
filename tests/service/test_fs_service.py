# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Tests for file-system service coordination behavior."""

import asyncio
from contextlib import asynccontextmanager
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from openviking.pyagfs.exceptions import AGFSAlreadyExistsError
from openviking.server.identity import RequestContext, Role
from openviking.service.fs_service import FSService, ListingPage
from openviking.service.task_work_index import bind_task_context
from openviking.storage.abstract_overview import body_for_preview
from openviking.storage.acl import AclAction
from openviking.storage.errors import LockAcquisitionError
from openviking.storage.viking_fs import VikingFS
from openviking_cli.exceptions import InvalidArgumentError
from openviking_cli.session.user_id import UserIdentifier


@pytest.fixture(autouse=True)
def run_fs_operations_inline(monkeypatch):
    async def run_tracked_operation(_task_type, *, operation, **_kwargs):
        with bind_task_context("test-task", "default", "test-user", enqueued_work=[]):
            return await operation(), "test-task"

    monkeypatch.setattr(
        "openviking.service.fs_service.run_tracked_operation",
        run_tracked_operation,
    )


class _FakeVikingFS:
    def __init__(self, *, rm_error=None, events=None, parent_exists=True):
        self._ensure_access = AsyncMock()
        self._ensure_destination_access = AsyncMock()
        self.rm_calls = []
        self.mv_calls = []
        self.cp_calls = []
        self.rm_error = rm_error
        self.events = events
        self.parent_exists = parent_exists

    async def exists(self, uri, ctx=None):
        return self.parent_exists

    async def rm(self, uri, recursive=False, ctx=None):
        self.rm_calls.append({"uri": uri, "recursive": recursive, "ctx": ctx})
        if self.rm_error:
            raise self.rm_error
        return {"estimated_deleted_count": 3}

    async def mv(self, from_uri, to_uri, ctx=None):
        self.mv_calls.append({"from_uri": from_uri, "to_uri": to_uri, "ctx": ctx})
        if self.events is not None:
            self.events.append(("mv", from_uri, to_uri))
        return {"source_acl": {}}

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


@pytest.mark.asyncio
async def test_stat_forwards_optional_fields_without_changing_defaults(request_context):
    viking_fs = SimpleNamespace(stat=AsyncMock(return_value={"isDir": True}))
    service = FSService(viking_fs=viking_fs)

    await service.stat(
        "viking://resources",
        request_context,
        skip_count=True,
        include_lock_status=True,
    )
    await service.stat("viking://resources", request_context)

    assert viking_fs.stat.await_args_list[0].kwargs == {
        "ctx": request_context,
        "skip_count": True,
        "include_lock_status": True,
    }
    assert viking_fs.stat.await_args_list[1].kwargs == {
        "ctx": request_context,
        "skip_count": False,
        "include_lock_status": False,
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


class _MkdirPathlockAGFS:
    def __init__(self):
        self._lock = asyncio.Lock()
        self.held = False
        self.acquire_count = 0

    async def pathlock_acquire_exact(self, _path):
        if self._lock.locked():
            raise LockAcquisitionError("exact path is busy")
        await self._lock.acquire()
        self.held = True
        self.acquire_count += 1
        return {"lease_ref": f"mkdir-{self.acquire_count}"}

    async def pathlock_release(self, _lease):
        self.held = False
        self._lock.release()


class _MkdirVikingFS:
    def __init__(self):
        self.directory_exists = False
        self.abstract_content = None
        self._async_agfs = _MkdirPathlockAGFS()

    def _uri_to_path(self, uri, ctx=None):
        del ctx
        return f"/local/default/{uri.removeprefix('viking://')}"

    async def mkdir(self, _uri, ctx=None):
        del ctx
        if self.directory_exists:
            raise AGFSAlreadyExistsError("directory already exists")
        self.directory_exists = True

    async def write_file(self, _uri, content, ctx=None, lease_ref=None):
        del ctx
        assert self._async_agfs.held
        assert lease_ref is not None
        self.abstract_content = content


@pytest.mark.asyncio
async def test_concurrent_default_mkdir_vectorizes_once(monkeypatch, request_context):
    viking_fs = _MkdirVikingFS()
    vectorized = []

    async def record_vectorization(**kwargs):
        assert viking_fs._async_agfs.held
        vectorized.append(kwargs["abstract"])
        await asyncio.sleep(0)

    monkeypatch.setattr(
        "openviking.service.fs_service.vectorize_directory_meta", record_vectorization
    )
    service = FSService(viking_fs=viking_fs)

    results = await asyncio.gather(
        service.mkdir("viking://resources/shared", ctx=request_context),
        service.mkdir("viking://resources/shared", ctx=request_context),
        return_exceptions=True,
    )

    assert sum(result is None for result in results) == 1
    assert sum(isinstance(result, AGFSAlreadyExistsError) for result in results) == 1

    assert body_for_preview(viking_fs.abstract_content) == "# shared"
    assert vectorized == ["# shared"]


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

    viking_fs.grep.reset_mock()
    await service.grep(
        "viking://user/ryoma/sessions/session-1",
        "secret",
        ctx=request_context,
    )
    assert "content_transform" not in viking_fs.grep.await_args.kwargs


@pytest.mark.asyncio
async def test_grep_forwards_context_to_viking_fs(request_context):
    viking_fs = SimpleNamespace(grep=AsyncMock(return_value={"matches": []}))
    service = FSService(viking_fs=viking_fs)

    await service.grep(
        "viking://resources",
        "needle",
        ctx=request_context,
        before_context=2,
        after_context=3,
    )

    assert viking_fs.grep.await_args.kwargs["before_context"] == 2
    assert viking_fs.grep.await_args.kwargs["after_context"] == 3


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
async def test_plain_listing_reads_only_requested_page_summaries(request_context, monkeypatch):
    selected = {"uri": "viking://resources/docs", "isDir": True}
    entries = [selected, {"uri": "viking://resources/probe", "isDir": True}]
    viking_fs = VikingFS(agfs=SimpleNamespace())
    monkeypatch.setattr(viking_fs, "_ensure_access", AsyncMock())
    monkeypatch.setattr(
        viking_fs, "_ls_original", AsyncMock(return_value=[dict(e) for e in entries])
    )
    monkeypatch.setattr(viking_fs, "_tree_original", AsyncMock(return_value=entries))
    abstract = AsyncMock(return_value="L0 summary")
    overview = AsyncMock(return_value="L1 overview")
    monkeypatch.setattr(viking_fs, "_read_abstract_for_known_dir", abstract)
    monkeypatch.setattr(viking_fs, "overview", overview)

    class FakeVikingDB:
        async def filter(self, **_kwargs):
            raise AssertionError("plain filesystem reads should not load tags")

    service = FSService(viking_fs=viking_fs, vikingdb=FakeVikingDB())

    assert await service.ls(
        "viking://resources",
        ctx=request_context,
        node_limit=1,
        include_abstract=True,
        include_overview=True,
    ) == ListingPage(
        entries=[{**selected, "abstract": "L0 summary", "overview": "L1 overview"}],
        has_more=True,
    )
    assert await service.tree("viking://resources", ctx=request_context) == ListingPage(
        entries=entries, has_more=False
    )
    abstract.assert_awaited_once_with(selected["uri"], ctx=request_context)
    overview.assert_awaited_once_with(selected["uri"], ctx=request_context)


@pytest.mark.asyncio
@pytest.mark.parametrize("method_name", ["ls", "tree"])
@pytest.mark.parametrize(
    ("entry_count", "expected_has_more"),
    [(1, False), (2, False), (3, True)],
)
async def test_ls_and_tree_detect_more_entries_with_n_plus_one(
    request_context, method_name, entry_count, expected_has_more
):
    entries = [
        {"uri": f"viking://resources/{index}.md", "isDir": False} for index in range(entry_count)
    ]

    async def fetch(*_args, offset=0, node_limit=None, **_kwargs):
        return entries[offset:] if node_limit is None else entries[offset : offset + node_limit]

    viking_fs = SimpleNamespace(
        ls=AsyncMock(side_effect=fetch),
        tree=AsyncMock(side_effect=fetch),
    )
    service = FSService(viking_fs=viking_fs)

    page = await getattr(service, method_name)(
        "viking://resources",
        ctx=request_context,
        offset=0,
        node_limit=2,
    )

    assert page.entries == entries[:2]
    assert page.has_more is expected_has_more
    fetch_mock = getattr(viking_fs, method_name)
    assert fetch_mock.await_args.kwargs["node_limit"] == 3


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
    assert viking_fs.glob.await_args.kwargs["node_limit"] == 1
    assert viking_fs.glob.await_args.kwargs["tag_filter"] == {
        "op": "and",
        "conds": [
            {"op": "must", "field": "search_tags", "conds": ["team=search"]},
            {"op": "must", "field": "search_tags", "conds": ["env=prod"]},
        ],
    }


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
async def test_ls_filters_and_paginates_before_reading_summaries(request_context, monkeypatch):
    entries = [
        {
            "uri": f"viking://resources/unmatched-{index:03d}.md",
            "isDir": False,
        }
        for index in range(256)
    ] + [
        {"uri": "viking://resources/b.md", "isDir": False},
        {"uri": "viking://resources/docs", "isDir": True},
        {"uri": "viking://resources/d.md", "isDir": False},
    ]

    async def fake_ls(*_args, offset=0, node_limit=None, **_kwargs):
        return entries[offset:] if node_limit is None else entries[offset : offset + node_limit]

    viking_fs = VikingFS(agfs=SimpleNamespace())
    monkeypatch.setattr(viking_fs, "_ensure_access", AsyncMock())
    monkeypatch.setattr(viking_fs, "_ls_original", AsyncMock(side_effect=fake_ls))
    abstract = AsyncMock(return_value="L0 summary")
    overview = AsyncMock(return_value="L1 overview")
    monkeypatch.setattr(viking_fs, "_read_abstract_for_known_dir", abstract)
    monkeypatch.setattr(viking_fs, "overview", overview)

    class FakeVikingDB:
        async def filter(self, **_kwargs):
            return [
                {
                    "uri": "viking://resources/b.md",
                    "level": 2,
                    "search_tags": ["team=search", "env=prod"],
                },
                {
                    "uri": "viking://resources/docs",
                    "level": 0,
                    "search_tags": ["team=search"],
                },
                {
                    "uri": "viking://resources/docs",
                    "level": 1,
                    "search_tags": ["env=prod", "team=search"],
                },
                {
                    "uri": "viking://resources/d.md",
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
        offset=1,
        include_abstract=True,
        include_overview=True,
    )

    assert result == ListingPage(
        entries=[
            {
                "uri": "viking://resources/docs",
                "isDir": True,
                "tags": ["team=search", "env=prod"],
                "abstract": "L0 summary",
                "overview": "L1 overview",
            }
        ],
        has_more=True,
    )
    abstract.assert_awaited_once_with("viking://resources/docs", ctx=request_context)
    overview.assert_awaited_once_with("viking://resources/docs", ctx=request_context)


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

    assert [entry["uri"] for entry in entry_result.entries] == [
        "viking://resources/a.md",
        "viking://resources/b.md",
    ]
    assert simple_result == ListingPage(
        entries=["viking://resources/a.md", "viking://resources/b.md"], has_more=False
    )


@pytest.mark.asyncio
async def test_tree_projects_directory_tags_before_pagination_and_summaries(
    request_context, monkeypatch
):
    directory = {"uri": "viking://resources/docs", "isDir": True}
    entries = [
        {"uri": "viking://resources/unmatched", "isDir": True},
        {"uri": "viking://resources/skipped", "isDir": True},
        directory,
    ]
    viking_fs = VikingFS(agfs=SimpleNamespace())
    monkeypatch.setattr(viking_fs, "tree", AsyncMock(return_value=entries))
    abstract = AsyncMock(return_value="L0 summary")
    overview = AsyncMock(return_value="L1 overview")
    monkeypatch.setattr(viking_fs, "_read_abstract_for_known_dir", abstract)
    monkeypatch.setattr(viking_fs, "overview", overview)

    class FakeVikingDB:
        async def filter(self, **_kwargs):
            return [
                {
                    "uri": "viking://resources/skipped",
                    "level": 0,
                    "search_tags": ["team=search", "env=prod"],
                },
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
        node_limit=1,
        offset=1,
        include_abstract=True,
        include_overview=True,
    )

    assert result == ListingPage(
        entries=[
            {
                **directory,
                "tags": ["team=search", "env=prod"],
                "abstract": "L0 summary",
                "overview": "L1 overview",
            }
        ],
        has_more=False,
    )
    # Intermediate pages must not load summaries before tag filtering selects the final page.
    fetch_options = viking_fs.tree.await_args.kwargs
    assert fetch_options["output"] == "original"
    assert fetch_options["include_abstract"] is None
    assert fetch_options["include_overview"] is False
    abstract.assert_awaited_once_with(directory["uri"], ctx=request_context)
    overview.assert_awaited_once_with(directory["uri"], ctx=request_context)


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

    assert [entry["uri"] for entry in result.entries] == [
        "viking://resources/a.md",
        "viking://resources/b.md",
    ]
    assert result.has_more is False
    assert viking_fs.tree.await_args.kwargs["node_limit"] is None


@pytest.mark.asyncio
async def test_resource_rm_enqueues_parent_delete_refresh_and_waits(request_context, monkeypatch):
    viking_fs = _FakeVikingFS()
    service = FSService(viking_fs=viking_fs)
    queue_manager = _FakeQueueManager()
    monkeypatch.setattr("openviking.service.fs_service.get_queue_manager", lambda: queue_manager)
    service._wait_for_refresh = AsyncMock(return_value={"Semantic": {"pending_count": 0}})

    uri = "viking://resources/images/2026/06/10/不二周助_jpeg"
    result = await service.rm(
        uri,
        ctx=request_context,
        recursive=True,
        wait=True,
    )

    viking_fs._ensure_access.assert_awaited_once_with(uri, request_context, action=AclAction.MANAGE)
    assert viking_fs.rm_calls == [
        {"uri": uri, "recursive": True, "ctx": replace(request_context, bypass_acl=True)}
    ]
    assert len(queue_manager.messages) == 1
    msg = queue_manager.messages[0]
    assert msg.uri == "viking://resources/images/2026/06/10"
    assert msg.context_type == "resource"
    assert msg.recursive is False
    assert msg.changes == {"deleted": [uri]}
    assert result["semantic_root_uri"] == "viking://resources/images/2026/06/10"
    assert result["semantic_status"] == "complete"
    assert result["queue_status"] == {"Semantic": {"pending_count": 0}}


@pytest.mark.asyncio
async def test_resource_rm_reports_failed_semantic_status_when_wait_queue_has_errors(
    request_context, monkeypatch
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
async def test_resource_rm_skips_parent_refresh_when_parent_is_gone(request_context):
    """Refreshing a deleted parent would lock its sidecars and recreate it."""
    viking_fs = _FakeVikingFS(parent_exists=False)
    service = FSService(viking_fs=viking_fs)
    service._enqueue_delete_refresh = AsyncMock()

    uri = "viking://resources/deleted/sub/a.md"
    result = await service.rm(uri, ctx=request_context, wait=True)

    assert viking_fs.rm_calls == [
        {"uri": uri, "recursive": False, "ctx": replace(request_context, bypass_acl=True)}
    ]
    service._enqueue_delete_refresh.assert_not_awaited()
    assert "semantic_root_uri" not in result


@pytest.mark.asyncio
async def test_resource_rm_without_wait_only_queues_refresh(request_context):
    viking_fs = _FakeVikingFS()
    service = FSService(viking_fs=viking_fs)
    service._enqueue_delete_refresh = AsyncMock()

    uri = "viking://resources/images/2026/06/10/不二周助_jpeg"
    result = await service.rm(uri, ctx=request_context, recursive=True)

    service._enqueue_delete_refresh.assert_awaited_once()
    assert result["semantic_status"] == "queued"


@pytest.mark.asyncio
async def test_resource_scope_rm_does_not_refresh_global_root(request_context):
    service = FSService(viking_fs=_FakeVikingFS())
    service._enqueue_delete_refresh = AsyncMock()

    result = await service.rm(
        "viking://resources",
        ctx=request_context,
        recursive=True,
        wait=True,
    )

    service._enqueue_delete_refresh.assert_not_awaited()
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
@pytest.mark.parametrize("separator", ["/", "//"])
async def test_resource_mv_validates_then_moves_then_rewrites_watch_tasks(
    request_context, separator
):
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
        f"viking://resources{separator}codeask/wiki",
        f"viking://resources{separator}codeask/wiki-renamed",
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
            "ctx": replace(request_context, bypass_acl=True),
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
@pytest.mark.parametrize(
    "name",
    [
        ".path.ovlock",
        ".exact.ovlock.",
        ".exact.ovlock.probe.md",
        ".exact.ovlock.notes.md.0123abcd",
        ".redirect.json",
        ".sync_log.json",
    ],
)
@pytest.mark.parametrize("suffix", ["", "/", "/child", "/child/notes.md"])
async def test_mkdir_cp_mv_reject_storage_internal_names(request_context, name, suffix):
    viking_fs = _FakeVikingFS(events=[])
    service = FSService(viking_fs=viking_fs)
    target = f"viking://resources/project/{name}{suffix}"
    viking_fs.exists = AsyncMock()
    viking_fs.mkdir = AsyncMock()

    with pytest.raises(InvalidArgumentError, match="storage internal name"):
        await service.mkdir(target, ctx=request_context)
    with pytest.raises(InvalidArgumentError, match="storage internal name"):
        await service.cp("viking://resources/project/a.md", target, False, ctx=request_context)
    with pytest.raises(InvalidArgumentError, match="storage internal name"):
        await service.mv("viking://resources/project/a.md", target, ctx=request_context)
    assert viking_fs.mv_calls == []
    assert viking_fs.cp_calls == []
    viking_fs.exists.assert_not_called()
    viking_fs.mkdir.assert_not_called()


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
            "ctx": replace(request_context, bypass_acl=True),
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
@pytest.mark.parametrize("separator", ["/", "//"])
async def test_resource_cp_coordinates_mutation_without_copying_watch_tasks(
    request_context, separator
):
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

    result = await service.cp(
        source.replace("resources/", f"resources{separator}"),
        target.replace("resources/", f"resources{separator}"),
        recursive=True,
        ctx=request_context,
    )

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
@pytest.mark.parametrize("recursive", [False, True])
async def test_file_only_transfer_rebuild_uses_fresh_semantic_processing(
    request_context,
    monkeypatch,
    recursive,
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
    target = (
        "viking://resources/archive/copied"
        if recursive
        else "viking://resources/archive/copied.md"
    )
    root = target if recursive else "viking://resources/archive"

    status = await service._enqueue_transfer_rebuild(
        root_uri=root,
        target_uri=target,
        recursive=recursive,
        context_type="resource",
        ctx=request_context,
    )

    assert status == "queued"
    msg = queue_manager.messages[0]
    assert msg.uri == root
    assert msg.recursive is recursive
    assert msg.changes is None if recursive else msg.changes == {"added": [target]}
    assert msg.generation_trigger == "content_transfer_rebuild"
    if recursive:
        mark_pending.assert_not_awaited()
    else:
        mark_pending.assert_awaited_once()


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


@pytest.mark.asyncio
async def test_system_mkdir_skips_semantic_sidecar_and_vectorization(request_context):
    uri = "viking://system/memory/memory_templates/custom/demo"
    viking_fs = SimpleNamespace(_ensure_access=AsyncMock(), mkdir=AsyncMock())
    service = FSService(viking_fs=viking_fs)

    await service.mkdir(uri, ctx=request_context)

    viking_fs._ensure_access.assert_awaited_once()
    viking_fs.mkdir.assert_awaited_once_with(uri, ctx=request_context)


@pytest.mark.asyncio
async def test_system_rm_skips_vector_and_semantic_work(request_context):
    uri = "viking://system/memory/memory_templates/custom/demo.md"
    viking_fs = SimpleNamespace(
        _ensure_access=AsyncMock(),
        rm=AsyncMock(return_value={"estimated_deleted_count": 0}),
    )
    service = FSService(viking_fs=viking_fs)

    result = await service._rm(uri, ctx=request_context)

    viking_fs.rm.assert_awaited_once_with(
        uri,
        recursive=False,
        ctx=replace(request_context, bypass_acl=True),
        no_vector=True,
    )
    assert result["semantic_status"] == "skipped"
    assert result["vector_status"] == "skipped"


@pytest.mark.asyncio
async def test_system_stat_suppresses_vector_fields(request_context):
    uri = "viking://system/memory/memory_templates/custom/demo.md"
    viking_fs = SimpleNamespace(stat=AsyncMock(return_value={"uri": uri}))
    service = FSService(viking_fs=viking_fs)

    await service.stat(uri, ctx=request_context)

    viking_fs.stat.assert_awaited_once_with(
        uri,
        ctx=request_context,
        skip_count=True,
        include_lock_status=False,
        include_vector_fields=False,
    )


@pytest.mark.asyncio
async def test_system_ls_allows_agent_output_without_semantic_enrichment(request_context):
    uri = "viking://system/memory/memory_templates/custom"
    entries = [{"uri": f"{uri}/demo.md", "isDir": False}]
    formatted = [{**entries[0], "size": 4}]
    viking_fs = SimpleNamespace(
        _ensure_access=AsyncMock(),
        ls=AsyncMock(return_value=entries),
        _finalize_listing_entries=AsyncMock(return_value=formatted),
    )
    service = FSService(viking_fs=viking_fs)

    result = await service.ls(uri, ctx=request_context, output="agent")

    assert result == ListingPage(entries=formatted, has_more=False)
    finalize_call = viking_fs._finalize_listing_entries.await_args
    assert finalize_call.args[1] == "agent"
    assert finalize_call.kwargs["include_abstract"] is False
    assert finalize_call.kwargs["include_overview"] is False


@pytest.mark.asyncio
async def test_system_tree_allows_agent_output_without_semantic_enrichment(request_context):
    uri = "viking://system/memory/memory_templates/custom"
    entries = [{"uri": f"{uri}/demo.md", "isDir": False, "rel_path": "demo.md"}]
    viking_fs = SimpleNamespace(
        _ensure_access=AsyncMock(),
        tree=AsyncMock(return_value=entries),
    )
    service = FSService(viking_fs=viking_fs)

    result = await service.tree(uri, ctx=request_context, output="agent")

    assert result == ListingPage(entries=entries, has_more=False)
    tree_options = viking_fs.tree.await_args.kwargs
    assert tree_options["output"] == "agent"
    assert tree_options["include_abstract"] is False
    assert tree_options["include_overview"] is False


@pytest.mark.asyncio
async def test_copy_to_system_uses_physical_file_strategy(request_context):
    source = "viking://resources/project/demo.md"
    target = "viking://system/memory/memory_templates/custom/demo.md"
    viking_fs = SimpleNamespace(
        cp=AsyncMock(return_value={"from": source, "to": target, "recursive": False}),
    )
    service = FSService(viking_fs=viking_fs)

    result = await service.cp(source, target, recursive=False, ctx=request_context)

    viking_fs.cp.assert_awaited_once_with(
        source,
        target,
        recursive=False,
        ctx=request_context,
        maintain_vectors=False,
        copy_semantic_sidecars=False,
    )
    assert result["semantic_status"] == "skipped"
    assert result["vector_status"] == "skipped"


@pytest.mark.asyncio
async def test_indexed_copy_preserves_native_vectors_and_content_copy_refresh(request_context):
    source = "viking://resources/source/demo.md"
    target = "viking://resources/target/demo.md"
    viking_fs = SimpleNamespace(
        cp=AsyncMock(return_value={"from": source, "to": target, "recursive": False}),
    )
    service = FSService(viking_fs=viking_fs)
    service._enqueue_copy_refresh = AsyncMock(return_value="queued")

    await service.cp(source, target, recursive=False, ctx=request_context)

    viking_fs.cp.assert_awaited_once_with(
        source,
        target,
        recursive=False,
        ctx=request_context,
    )
    service._enqueue_copy_refresh.assert_awaited_once()


@pytest.mark.asyncio
async def test_indexed_move_preserves_native_vector_transfer(request_context):
    source = "viking://resources/source/demo.md"
    target = "viking://resources/target/demo.md"
    viking_fs = SimpleNamespace(
        _ensure_access=AsyncMock(), _ensure_destination_access=AsyncMock(), mv=AsyncMock()
    )
    service = FSService(viking_fs=viking_fs)
    service._refresh_move_parents = AsyncMock()

    await service.mv(source, target, ctx=request_context)

    viking_fs._ensure_access.assert_awaited_once_with(
        source, request_context, action=AclAction.MANAGE
    )
    viking_fs._ensure_destination_access.assert_awaited_once_with(target, request_context)
    viking_fs.mv.assert_awaited_once_with(
        source, target, ctx=replace(request_context, bypass_acl=True)
    )
    service._refresh_move_parents.assert_awaited_once_with(
        from_uri=source,
        to_uri=target,
        ctx=request_context,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["cp", "mv"])
async def test_transfer_to_registered_memory_template_is_rejected(request_context, operation):
    source = "viking://resources/project/demo.yaml"
    target = "viking://system/memory/memory_templates/custom/profile.yaml"
    viking_fs = SimpleNamespace(cp=AsyncMock(), mv=AsyncMock())
    service = FSService(viking_fs=viking_fs)

    with pytest.raises(InvalidArgumentError, match="must be updated through write"):
        if operation == "cp":
            await service.cp(source, target, recursive=False, ctx=request_context)
        else:
            await service.mv(source, target, ctx=request_context)

    getattr(viking_fs, operation).assert_not_awaited()


@pytest.mark.asyncio
async def test_move_to_system_deletes_source_vectors_without_target_index(
    monkeypatch,
    request_context,
):
    source = "viking://resources/project/demo.md"
    target = "viking://system/memory/memory_templates/custom/demo.md"
    viking_fs = SimpleNamespace(
        _ensure_access=AsyncMock(), _ensure_destination_access=AsyncMock(), mv=AsyncMock()
    )
    service = FSService(viking_fs=viking_fs)
    refresh = AsyncMock()
    monkeypatch.setattr(service, "_refresh_move_parents", refresh)

    await service.mv(source, target, ctx=request_context)

    viking_fs.mv.assert_awaited_once_with(
        source,
        target,
        ctx=replace(request_context, bypass_acl=True),
        vector_mode="delete_source",
        copy_semantic_sidecars=False,
    )
    refresh.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("sidecar_name", [".abstract.md", ".overview.md"])
async def test_copy_to_system_rejects_semantic_sidecar_target(
    request_context,
    sidecar_name,
):
    source = "viking://resources/project/demo.md"
    target = f"viking://system/memory/memory_templates/custom/{sidecar_name}"
    viking_fs = SimpleNamespace(cp=AsyncMock())
    service = FSService(viking_fs=viking_fs)

    with pytest.raises(InvalidArgumentError, match="semantic sidecars are not supported"):
        await service.cp(source, target, recursive=False, ctx=request_context)

    viking_fs.cp.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("sidecar_name", [".abstract.md", ".overview.md"])
async def test_move_to_system_rejects_semantic_sidecar_target(
    request_context,
    sidecar_name,
):
    source = "viking://resources/project/demo.md"
    target = f"viking://system/memory/memory_templates/custom/{sidecar_name}"
    viking_fs = SimpleNamespace(mv=AsyncMock())
    service = FSService(viking_fs=viking_fs)

    with pytest.raises(InvalidArgumentError, match="semantic sidecars are not supported"):
        await service.mv(source, target, ctx=request_context)

    viking_fs.mv.assert_not_awaited()


@pytest.mark.asyncio
async def test_copy_from_system_rebuilds_target_index(request_context):
    source = "viking://system/memory/memory_templates/custom/demo.md"
    target = "viking://resources/project/demo.md"
    viking_fs = SimpleNamespace(
        cp=AsyncMock(return_value={"from": source, "to": target, "recursive": False}),
        stat=AsyncMock(return_value={"isDir": False}),
    )
    service = FSService(viking_fs=viking_fs)
    service._enqueue_transfer_rebuild = AsyncMock(return_value="queued")

    result = await service.cp(source, target, recursive=False, ctx=request_context)

    viking_fs.cp.assert_awaited_once_with(
        source,
        target,
        recursive=False,
        ctx=request_context,
        maintain_vectors=False,
        copy_semantic_sidecars=False,
    )
    service._enqueue_transfer_rebuild.assert_awaited_once_with(
        root_uri="viking://resources/project",
        target_uri=target,
        recursive=False,
        context_type="resource",
        ctx=request_context,
    )
    assert result["semantic_status"] == "queued"


@pytest.mark.asyncio
async def test_recursive_copy_from_system_rebuilds_target_subtree(request_context):
    source = "viking://system/compile/example-skills/demo"
    target = "viking://resources/project/demo"
    viking_fs = SimpleNamespace(
        cp=AsyncMock(return_value={"from": source, "to": target, "recursive": True}),
        stat=AsyncMock(return_value={"isDir": True}),
    )
    service = FSService(viking_fs=viking_fs)
    service._enqueue_transfer_rebuild = AsyncMock(return_value="queued")

    await service.cp(source, target, recursive=True, ctx=request_context)

    service._enqueue_transfer_rebuild.assert_awaited_once_with(
        root_uri=target,
        target_uri=target,
        recursive=True,
        context_type="resource",
        ctx=request_context,
    )


@pytest.mark.asyncio
async def test_move_from_system_rebuilds_target_index(request_context):
    source = "viking://system/memory/memory_templates/custom/demo.md"
    target = "viking://resources/project/demo.md"
    viking_fs = SimpleNamespace(
        _ensure_access=AsyncMock(),
        _ensure_destination_access=AsyncMock(),
        mv=AsyncMock(),
        stat=AsyncMock(return_value={"isDir": False}),
    )
    service = FSService(viking_fs=viking_fs)
    service._enqueue_transfer_rebuild = AsyncMock(return_value="queued")
    service._refresh_move_parents = AsyncMock()

    await service.mv(source, target, ctx=request_context)

    viking_fs.mv.assert_awaited_once_with(
        source,
        target,
        ctx=replace(request_context, bypass_acl=True),
        vector_mode="none",
        copy_semantic_sidecars=False,
    )
    service._refresh_move_parents.assert_awaited_once_with(
        from_uri=source,
        to_uri=target,
        ctx=request_context,
        refresh_target=False,
    )
    service._enqueue_transfer_rebuild.assert_awaited_once_with(
        root_uri="viking://resources/project",
        target_uri=target,
        recursive=False,
        context_type="resource",
        ctx=request_context,
    )


@pytest.mark.asyncio
async def test_move_target_refresh_failure_does_not_roll_back_physical_move(request_context):
    source = "viking://system/memory/memory_templates/custom/demo.md"
    target = "viking://resources/project/demo.md"
    viking_fs = SimpleNamespace(
        _ensure_access=AsyncMock(),
        _ensure_destination_access=AsyncMock(),
        mv=AsyncMock(),
        stat=AsyncMock(return_value={"isDir": False}),
    )
    service = FSService(viking_fs=viking_fs)
    service._enqueue_transfer_rebuild = AsyncMock(side_effect=RuntimeError("queue unavailable"))

    await service.mv(source, target, ctx=request_context)

    viking_fs.mv.assert_awaited_once()
    service._enqueue_transfer_rebuild.assert_awaited_once()


@pytest.mark.asyncio
async def test_recursive_move_from_system_rebuilds_target_subtree(request_context):
    source = "viking://system/compile/example-skills/demo"
    target = "viking://resources/project/demo"
    viking_fs = SimpleNamespace(
        _ensure_access=AsyncMock(),
        _ensure_destination_access=AsyncMock(),
        mv=AsyncMock(),
        stat=AsyncMock(return_value={"isDir": True}),
    )
    service = FSService(viking_fs=viking_fs)
    service._enqueue_transfer_rebuild = AsyncMock(return_value="queued")
    service._refresh_move_parents = AsyncMock()

    await service.mv(source, target, ctx=request_context)

    service._enqueue_transfer_rebuild.assert_awaited_once_with(
        root_uri=target,
        target_uri=target,
        recursive=True,
        context_type="resource",
        ctx=request_context,
    )


@pytest.mark.asyncio
async def test_system_grep_and_glob_force_filesystem_implementation(request_context):
    uri = "viking://system/memory/memory_templates/custom"
    viking_fs = SimpleNamespace(
        _ensure_access=AsyncMock(),
        grep=AsyncMock(return_value={"matches": []}),
        glob=AsyncMock(return_value={"matches": []}),
    )
    service = FSService(viking_fs=viking_fs)

    await service.grep(uri, "demo", ctx=request_context)
    await service.glob("*.md", uri=uri, ctx=request_context)

    assert viking_fs.grep.await_args.kwargs["force_fs"] is True
    assert viking_fs.glob.await_args.kwargs["force_fs"] is True


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["grep_tags", "glob_tags", "glob_vector_fields"])
async def test_system_file_search_rejects_index_features(request_context, operation):
    uri = "viking://system/memory/memory_templates/custom"
    viking_fs = SimpleNamespace(
        _ensure_access=AsyncMock(),
        grep=AsyncMock(return_value={"matches": []}),
        glob=AsyncMock(return_value={"matches": []}),
    )
    service = FSService(viking_fs=viking_fs)

    with pytest.raises(InvalidArgumentError, match="not supported"):
        if operation == "grep_tags":
            await service.grep(uri, "demo", ctx=request_context, include_tags=True)
        elif operation == "glob_tags":
            await service.glob("*.md", uri=uri, ctx=request_context, tags=["team=search"])
        else:
            await service.glob("*.md", uri=uri, ctx=request_context, extra_fields=["id"])

    viking_fs.grep.assert_not_awaited()
    viking_fs.glob.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["abstract", "overview"])
async def test_system_rejects_semantic_sidecar_reads(request_context, operation):
    uri = "viking://system/memory/memory_templates/custom/demo.md"
    viking_fs = SimpleNamespace(
        _ensure_access=AsyncMock(),
        abstract=AsyncMock(),
        overview=AsyncMock(),
    )
    service = FSService(viking_fs=viking_fs)

    with pytest.raises(InvalidArgumentError, match=f"{operation} is not supported"):
        await getattr(service, operation)(uri, ctx=request_context)

    getattr(viking_fs, operation).assert_not_awaited()
