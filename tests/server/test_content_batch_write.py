import asyncio
import base64
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import openviking.storage.content_write as content_write_module
from openviking.server.identity import RequestContext, Role
from openviking.service.task_store import PersistentTaskStore
from openviking.service.task_tracker import TaskTracker
from openviking.session.memory.dataclass import MemoryFile
from openviking.session.memory.utils import MemoryFileUtils
from openviking.storage.acl import AclSpec, AclUpdate
from openviking.storage.content_write import ContentWriteCoordinator
from openviking.storage.context_update_plan import (
    ContentTreeAction,
    ContextUpdatePlan,
    FileRefreshIntent,
)
from openviking.storage.queuefs.queue_middleware import EnqueueReceipt
from openviking.storage.queuefs.semantic_ops.freshness_policy import FreshnessAction
from openviking.storage.viking_fs import VikingFS
from openviking.telemetry.context import bind_telemetry
from openviking.telemetry.operation import OperationTelemetry
from openviking.utils.content_hash import content_md5
from openviking_cli.exceptions import (
    InvalidArgumentError,
    NotFoundError,
    PermissionDeniedError,
)
from openviking_cli.session.user_id import UserIdentifier
from tests.test_task_tracker import _FakeAgfs


class _PathLockClient:
    def __init__(self):
        self.held = False
        self.releases = 0
        self.handoffs = 0
        self.paths = []

    async def pathlock_acquire_exact_batch(self, paths):
        self.paths = list(paths)
        self.held = True
        return {"lease_ref": "lock-1"}

    async def pathlock_acquire_exact(self, path):
        self.paths = [path]
        self.held = True
        return {"lease_ref": "lock-1"}

    async def pathlock_to_handoff(self, lease):
        assert lease == {"lease_ref": "lock-1"}
        assert self.held is True
        return {
            "owner_id": "lock-1",
            "covered_paths": [{"path": path, "kind": "exact"} for path in self.paths],
        }

    async def pathlock_handoff(self, lease):
        assert lease == {"lease_ref": "lock-1"}
        assert self.held is True
        self.held = False
        self.handoffs += 1

    async def pathlock_adopt(self, handoff):
        assert handoff["owner_id"] == "lock-1"
        self.held = True
        return {"lease_ref": "lock-1"}

    async def pathlock_release(self, lease):
        assert lease == {"lease_ref": "lock-1"}
        self.held = False
        self.releases += 1


class _VFS:
    def __init__(self, root, files=None, fail_uri=None):
        self.root = root
        self.files = dict(files or {})
        self.fail_uri = fail_uri
        self.writes = []
        self._async_agfs = _PathLockClient()

    async def _ensure_access(self, uri, ctx, *, action):
        del uri, ctx, action

    def _uri_to_path(self, uri, ctx=None):
        del ctx
        return "/virtual/" + uri.removeprefix("viking://")

    async def stat(self, uri, ctx=None, skip_count=False):
        del ctx
        assert skip_count is True
        if uri == self.root:
            return {"uri": uri, "isDir": True}
        if uri in self.files:
            return {"uri": uri, "isDir": False}
        raise NotFoundError(uri, "file")

    async def read_file(self, uri, ctx=None):
        del ctx
        if uri not in self.files:
            raise NotFoundError(uri, "file")
        return self.files[uri]

    async def read_file_bytes(self, uri, ctx=None):
        value = await self.read_file(uri, ctx=ctx)
        return value.encode() if isinstance(value, str) else value

    async def write_file(self, uri, content, ctx=None, lease_ref=None):
        del ctx
        assert lease_ref is not None
        if uri == self.fail_uri:
            raise OSError("injected write failure")
        self.files[uri] = content
        self.writes.append(uri)

    async def write_file_bytes(self, uri, content, ctx=None, lease_ref=None):
        del ctx
        assert lease_ref is not None
        if uri == self.fail_uri:
            raise OSError("injected write failure")
        try:
            self.files[uri] = content.decode("utf-8")
        except UnicodeDecodeError:
            self.files[uri] = content
        self.writes.append(uri)


@pytest.mark.asyncio
async def test_system_write_is_file_only_without_queue_work(
    _coordinator_queue_for_unit_tests,
):
    root = "viking://system/memory/memory_templates/custom/demo"
    uri = f"{root}/template.md"
    vfs = _VFS(root)
    coordinator = ContentWriteCoordinator(vfs)
    ctx = RequestContext(user=UserIdentifier.the_default_user(), role=Role.ADMIN)

    result = await coordinator.write(
        uri=uri,
        content="template",
        ctx=ctx,
        wait=False,
    )

    assert vfs.files[uri] == "template"
    assert vfs._async_agfs.releases == 1
    assert vfs._async_agfs.handoffs == 0
    assert result["root_uri"] == root
    assert result["semantic_status"] == "skipped"
    assert result["vector_status"] == "skipped"
    assert result["queue_status"] is None
    assert _last_content_write_refresh(_coordinator_queue_for_unit_tests) == {"kind": "none"}
    _coordinator_queue_for_unit_tests.content_write.enqueue.assert_awaited_once()
    _coordinator_queue_for_unit_tests.semantic.enqueue.assert_not_awaited()


@pytest.mark.asyncio
async def test_system_batch_write_is_file_only_without_queue_work(
    _coordinator_queue_for_unit_tests,
):
    root = "viking://system/memory/memory_templates/custom/demo"
    first = f"{root}/first.md"
    second = f"{root}/second.md"
    vfs = _VFS(root, {first: "old"})
    coordinator = ContentWriteCoordinator(vfs)
    ctx = RequestContext(user=UserIdentifier.the_default_user(), role=Role.ADMIN)

    result = await coordinator.batch_write(
        root_uri=root,
        operations=[
            {"uri": first, "content": "new"},
            {"uri": second, "content": "created"},
        ],
        ctx=ctx,
        wait=False,
    )

    assert result["updated"] == [first]
    assert result["created"] == [second]
    assert result["semantic_status"] == "skipped"
    assert result["vector_status"] == "skipped"
    assert result["queue_status"] is None
    assert vfs._async_agfs.releases == 1
    assert vfs._async_agfs.handoffs == 0
    assert _last_content_write_refresh(_coordinator_queue_for_unit_tests) == {"kind": "none"}
    _coordinator_queue_for_unit_tests.content_write.enqueue.assert_awaited_once()
    _coordinator_queue_for_unit_tests.semantic.enqueue.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "root",
    [
        "viking://system/compile/example-skills",
        "viking://system/memory/memory_templates/builtin",
    ],
)
async def test_system_builtin_batch_write_rejects_external_root_writes(
    _coordinator_queue_for_unit_tests,
    root,
):
    uri = f"{root}/demo/assets/icon.bin"
    content = b"\x89PNG\r\n\x1a\nasset"
    vfs = _VFS(root)
    vfs._ensure_access = VikingFS(agfs=SimpleNamespace())._ensure_access
    coordinator = ContentWriteCoordinator(vfs)
    ctx = RequestContext(user=UserIdentifier.the_default_user(), role=Role.ROOT)

    with pytest.raises(PermissionDeniedError):
        await coordinator.batch_write(
            root_uri=root,
            operations=[
                {
                    "uri": uri,
                    "content_base64": base64.b64encode(content).decode("ascii"),
                    "mode": "replace",
                }
            ],
            ctx=ctx,
            wait=False,
        )

    assert vfs.files == {}
    assert vfs._async_agfs.paths == []
    _coordinator_queue_for_unit_tests.content_write.enqueue.assert_not_awaited()
    _coordinator_queue_for_unit_tests.semantic.enqueue.assert_not_awaited()


@pytest.mark.asyncio
async def test_user_skill_namespace_batch_write_remains_rejected():
    root = "viking://user/default/skills"
    coordinator = ContentWriteCoordinator(_VFS(root))
    ctx = RequestContext(user=UserIdentifier.the_default_user(), role=Role.USER)

    with pytest.raises(
        InvalidArgumentError,
        match="batch-write root must be a resource or memory directory",
    ):
        await coordinator.batch_write(
            root_uri=root,
            operations=[
                {
                    "uri": f"{root}/demo/SKILL.md",
                    "content": "skill",
                    "mode": "replace",
                }
            ],
            ctx=ctx,
        )


@pytest.mark.asyncio
async def test_system_write_rejects_indexed_metadata():
    root = "viking://system/memory/memory_templates/custom/demo"
    uri = f"{root}/template.md"
    coordinator = ContentWriteCoordinator(_VFS(root))
    ctx = RequestContext(user=UserIdentifier.the_default_user(), role=Role.ADMIN)

    with pytest.raises(InvalidArgumentError, match="tags are not supported"):
        await coordinator.write(
            uri=uri,
            content="template",
            tags=["kind=template"],
            ctx=ctx,
        )


@pytest.mark.asyncio
async def test_system_set_tags_rejects_capability_before_write_access():
    uri = "viking://system/memory/memory_templates/custom/demo/template.md"
    ensure_access = AsyncMock(side_effect=AssertionError("write access must not be checked"))
    coordinator = ContentWriteCoordinator(SimpleNamespace(_ensure_access=ensure_access))
    ctx = RequestContext(user=UserIdentifier.the_default_user(), role=Role.USER)

    with pytest.raises(InvalidArgumentError, match="tags are not supported"):
        await coordinator.set_tags(
            uri=uri,
            tags=["kind=template"],
            ctx=ctx,
        )

    ensure_access.assert_not_awaited()


@pytest.fixture(autouse=True)
def _coordinator_queue_for_unit_tests(monkeypatch, request):
    if "service" in request.fixturenames:
        yield None
        return

    semantic_queue = SimpleNamespace(
        enqueue=AsyncMock(return_value=EnqueueReceipt("queued", "semantic-work-1"))
    )
    content_write_queue = SimpleNamespace(
        enqueue=AsyncMock(return_value=EnqueueReceipt("queued", "content-write-work-1"))
    )
    queues = {
        "semantic": semantic_queue,
        "ContentWrite": content_write_queue,
    }
    queue_manager = SimpleNamespace(
        SEMANTIC="semantic",
        CONTENT_WRITE="ContentWrite",
        get_queue=lambda name, *args, **kwargs: queues[name],
    )
    monkeypatch.setattr(content_write_module, "get_queue_manager", lambda: queue_manager)
    tracker = TaskTracker(PersistentTaskStore(_FakeAgfs()))
    monkeypatch.setattr("openviking.service.task_tracker._instance", tracker)
    yield SimpleNamespace(semantic=semantic_queue, content_write=content_write_queue)


def _last_content_write_payload(queue_fixture):
    return queue_fixture.content_write.enqueue.await_args.args[0]


def _last_content_write_refresh(queue_fixture):
    return _last_content_write_payload(queue_fixture)["refresh"]


@pytest.mark.asyncio
async def test_batch_coalesces_compile_target_updates(service, monkeypatch):
    """A batch saves its managed target's content-update time once."""
    ctx = RequestContext(user=service.user, role=Role.USER)
    root = "viking://resources/batch-metadata"
    store = service.compile.lifecycle.store
    await service.viking_fs.write_file(f"{root}/seed.md", "seed", ctx=ctx)
    async with store.edit(ctx.account_id, root, create=True):
        pass
    update_time = AsyncMock(wraps=store.mark_content_updated)
    monkeypatch.setattr(store, "mark_content_updated", update_time)
    ops = [{"uri": f"{root}/{i}.md", "content": "changed"} for i in range(100)]
    await service.fs.batch_write(root_uri=root, operations=ops, ctx=ctx, defer_processing=True)
    update_time.assert_awaited_once()


@pytest.mark.asyncio
async def test_deferred_batch_versions_content_before_dispatch_without_refresh(
    _coordinator_queue_for_unit_tests,
):
    """Compile publication records bytes under its lease without scheduling index work."""
    root = "viking://resources/wiki"
    uri = f"{root}/page.md"
    vfs = _VFS(root)

    async def commit(uris, *, lease_ref, write):
        assert uris == [uri] and lease_ref == {"lease_ref": "lock-1"}
        assert vfs._async_agfs.held
        return await write()

    commit_content = AsyncMock(side_effect=commit)
    commit_batch = AsyncMock()
    coordinator = ContentWriteCoordinator(
        vfs, commit_content=commit_content, commit_batch=commit_batch
    )
    ctx = RequestContext(user=UserIdentifier.the_default_user(), role=Role.USER)
    result = await coordinator.batch_write(
        root_uri=root,
        operations=[{"uri": uri, "content": "x", "mode": "upsert"}],
        ctx=ctx,
        wait=False,
        defer_processing=True,
    )

    commit_content.assert_awaited_once()
    commit_batch.assert_awaited_once_with(lease_ref={"lease_ref": "lock-1"})
    assert vfs.writes == [uri]
    assert result["semantic_status"] == result["vector_status"] == "deferred"
    assert _last_content_write_refresh(_coordinator_queue_for_unit_tests) == {"kind": "none"}
    assert vfs._async_agfs.releases == 1 and vfs._async_agfs.handoffs == 0


@pytest.mark.asyncio
async def test_batch_accepts_more_than_256_operations(monkeypatch):
    root = "viking://resources/wiki"
    vfs = _VFS(root)
    coordinator = ContentWriteCoordinator(vfs)

    async def refresh(**kwargs):
        del kwargs
        return None

    monkeypatch.setattr(coordinator, "_refresh_batch", refresh)
    ctx = RequestContext(user=UserIdentifier.the_default_user(), role=Role.USER)

    def operations(prefix, count):
        return [
            {
                "uri": f"{root}/{prefix}-{index}.txt",
                "content": "x",
                "mode": "upsert",
            }
            for index in range(count)
        ]

    result = await coordinator.batch_write(
        root_uri=root,
        operations=operations("allowed", 256),
        ctx=ctx,
        wait=False,
    )
    assert len(result["created"]) == 256

    overflow = await coordinator.batch_write(
        root_uri=root,
        operations=operations("overflow", 257),
        ctx=ctx,
        wait=False,
    )
    assert len(overflow["created"]) == 257


@pytest.mark.asyncio
async def test_batch_create_replace_and_upsert_overwrite_or_create(monkeypatch):
    root = "viking://resources/wiki"
    existing = f"{root}/existing.md"
    created_by_replace = f"{root}/replace.md"
    created_by_upsert = f"{root}/upsert.md"
    vfs = _VFS(root, {existing: "newer"})
    coordinator = ContentWriteCoordinator(vfs)

    async def refresh(**kwargs):
        del kwargs

    monkeypatch.setattr(coordinator, "_refresh_batch", refresh)
    ctx = RequestContext(user=UserIdentifier.the_default_user(), role=Role.USER)
    result = await coordinator.batch_write(
        root_uri=root,
        operations=[
            {"uri": existing, "content": "replacement", "mode": "create"},
            {"uri": created_by_replace, "content": "created", "mode": "replace"},
            {"uri": created_by_upsert, "content": "upsert", "mode": "upsert"},
        ],
        ctx=ctx,
        wait=False,
    )

    assert result["updated"] == [existing]
    assert result["created"] == [created_by_replace, created_by_upsert]
    assert vfs.files == {
        existing: "replacement",
        created_by_replace: "created",
        created_by_upsert: "upsert",
    }


@pytest.mark.asyncio
async def test_batch_missing_append_creates_its_initial_body(monkeypatch):
    root = "viking://resources/wiki"
    page = f"{root}/page.md"
    vfs = _VFS(root)
    coordinator = ContentWriteCoordinator(vfs)

    async def refresh(**kwargs):
        del kwargs

    monkeypatch.setattr(coordinator, "_refresh_batch", refresh)
    ctx = RequestContext(user=UserIdentifier.the_default_user(), role=Role.USER)

    result = await coordinator.batch_write(
        root_uri=root,
        operations=[{"uri": page, "content": "initial body", "mode": "append"}],
        ctx=ctx,
        wait=False,
    )

    assert result["created"] == [page]
    assert result["updated"] == []
    assert vfs.files[page] == "initial body"


@pytest.mark.asyncio
async def test_batch_resource_files_compile_independent_rnfv_plans(
    monkeypatch,
    _coordinator_queue_for_unit_tests,
):
    root = "viking://resources/wiki"
    first = f"{root}/docs/a.md"
    second = f"{root}/docs/b.md"
    vfs = _VFS(root, {first: "old A"})

    class _VikingDB:
        def __init__(self):
            self.calls = []

        async def get_incremental_inventory_by_uris(self, uris, **kwargs):
            self.calls.append((list(uris), kwargs))
            return {
                uri: {
                    "a-l2": {
                        "id": "a-l2",
                        "uri": uri,
                        "level": 2,
                        "md5": "old-md5",
                        "abstract": "old abstract",
                    }
                }
                for uri in uris
            }

    vikingdb = _VikingDB()
    coordinator = ContentWriteCoordinator(vfs, vikingdb=vikingdb)
    snapshots = []
    commits = []
    direct_actions = []
    refresh_calls = []

    async def build_snapshot(**kwargs):
        snapshots.append(kwargs)
        return SimpleNamespace(
            request=kwargs["request_intent"],
            vectors=SimpleNamespace(records_by_id={}),
        )

    async def build_plan(**kwargs):
        uri = kwargs["request_intent"].target_uri
        return ContextUpdatePlan(
            root_uri=uri,
            context_type="resource",
            content_tree_actions=(
                ContentTreeAction(
                    "upsert",
                    "",
                    old_kind="file",
                    new_kind="file",
                    artifact_path="",
                    md5="planned-md5",
                ),
            ),
            file_refresh=FileRefreshIntent(file_uri=uri, md5="planned-md5"),
        )

    async def commit(actions, **kwargs):
        assert vfs._async_agfs.held is True
        commits.append((actions, kwargs))

    async def enqueue(actions, **kwargs):
        direct_actions.extend(actions)
        return False

    async def grouped_refresh(_self, **kwargs):
        assert vfs._async_agfs.held is True
        refresh_calls.append(kwargs["changes"])
        return {
            "status": "success",
            "enqueued_count": 1,
            "parent_actions": {f"{root}/docs": "refresh_now"},
        }

    monkeypatch.setattr(content_write_module, "build_rnfv_snapshot", build_snapshot)
    monkeypatch.setattr(coordinator, "_build_deferred_batch_file_plan", build_plan)
    monkeypatch.setattr(content_write_module, "execute_content_tree_actions", commit)
    monkeypatch.setattr(content_write_module, "enqueue_direct_index_actions", enqueue)
    monkeypatch.setattr(content_write_module.Summarizer, "refresh_file_parents", grouped_refresh)
    ctx = RequestContext(user=UserIdentifier.the_default_user(), role=Role.USER)

    result = await coordinator.batch_write(
        root_uri=root,
        operations=[
            {"uri": first, "content": "A", "mode": "replace"},
            {"uri": second, "content": "B", "mode": "upsert"},
        ],
        ctx=ctx,
        wait=False,
    )

    assert result["updated"] == [first]
    assert result["created"] == [second]
    assert vikingdb.calls[0][0] == [first]
    assert len(snapshots) == 2
    assert all(snapshot["root_is_file"] is True for snapshot in snapshots)
    assert all(snapshot["vector_scope"] == "self" for snapshot in snapshots)
    snapshots_by_uri = {snapshot["target_uri"]: snapshot for snapshot in snapshots}
    assert snapshots_by_uri[first]["vector_inventory"]["a-l2"]["uri"] == first
    assert snapshots_by_uri[second]["vector_inventory"] == {}
    assert len(commits) == 2
    assert direct_actions == []
    assert refresh_calls == []
    refresh = _last_content_write_refresh(_coordinator_queue_for_unit_tests)
    plans = [ContextUpdatePlan.from_dict(item["plan"]) for item in refresh["resource_plans"]]
    assert {plan.file_refresh.file_uri for plan in plans if plan.file_refresh is not None} == {
        first,
        second,
    }
    assert {
        item["plan"]["file_refresh"]["file_uri"]: item["existed"]
        for item in refresh["resource_plans"]
    } == {
        first: True,
        second: False,
    }


@pytest.mark.asyncio
async def test_batch_existing_files_defer_full_vector_reads_to_embedding_worker(monkeypatch):
    root = "viking://resources/wiki"
    operations = [
        {"uri": f"{root}/docs/{index}.md", "content": f"new {index}", "mode": "replace"}
        for index in range(64)
    ]
    vfs = _VFS(
        root, {operation["uri"]: f"old {index}" for index, operation in enumerate(operations)}
    )

    class _VikingDB:
        def __init__(self):
            self.inventory_calls = []
            self.hydrate_incremental_records = AsyncMock(
                side_effect=AssertionError("batch preparation must not hydrate full vector records")
            )

        async def get_incremental_inventory_by_uris(self, uris, **kwargs):
            self.inventory_calls.append((list(uris), kwargs))
            return {
                uri: {
                    f"{index}-l2": {
                        "id": f"{index}-l2",
                        "uri": uri,
                        "level": 2,
                        "md5": content_md5(f"old {index}".encode()),
                        "abstract": f"old abstract {index}",
                    }
                }
                for index, uri in enumerate(uris)
            }

    vikingdb = _VikingDB()
    coordinator = ContentWriteCoordinator(vfs, vikingdb=vikingdb)
    monkeypatch.setattr(
        content_write_module.Summarizer,
        "refresh_file_parents",
        AsyncMock(
            return_value={"enqueued_count": 1, "parent_actions": {f"{root}/docs": "refresh_now"}}
        ),
    )
    ctx = RequestContext(user=UserIdentifier.the_default_user(), role=Role.USER)

    result = await coordinator.batch_write(
        root_uri=root,
        operations=operations,
        ctx=ctx,
        wait=False,
        processing_mode="semantic_and_vectors",
    )

    assert result["updated"] == sorted(operation["uri"] for operation in operations)
    assert len(vikingdb.inventory_calls) == 1
    assert vikingdb.inventory_calls[0][0] == sorted(operation["uri"] for operation in operations)
    vikingdb.hydrate_incremental_records.assert_not_awaited()


@pytest.mark.asyncio
async def test_batch_same_content_with_missing_abstract_enqueues_file_refresh(
    monkeypatch,
    _coordinator_queue_for_unit_tests,
):
    root = "viking://resources/wiki"
    uri = f"{root}/docs/a.md"
    vfs = _VFS(root, {uri: "unchanged"})

    class _VikingDB:
        async def get_incremental_inventory_by_uris(self, uris, **kwargs):
            del kwargs
            return {
                item: {
                    "a-l2": {
                        "id": "a-l2",
                        "uri": item,
                        "level": 2,
                        "md5": content_md5(b"unchanged"),
                        "abstract": "",
                    }
                }
                for item in uris
            }

    refresh_file_parents = AsyncMock(
        return_value={"enqueued_count": 1, "parent_actions": {f"{root}/docs": "refresh_now"}}
    )
    monkeypatch.setattr(
        content_write_module.Summarizer,
        "refresh_file_parents",
        refresh_file_parents,
    )
    coordinator = ContentWriteCoordinator(vfs, vikingdb=_VikingDB())
    ctx = RequestContext(user=UserIdentifier.the_default_user(), role=Role.USER)

    await coordinator.batch_write(
        root_uri=root,
        operations=[{"uri": uri, "content": "unchanged", "mode": "replace"}],
        ctx=ctx,
        wait=False,
        processing_mode="semantic_and_vectors",
    )

    refresh_file_parents.assert_not_awaited()
    refresh = _last_content_write_refresh(_coordinator_queue_for_unit_tests)
    plan = ContextUpdatePlan.from_dict(refresh["resource_plans"][0]["plan"])
    assert (plan.file_refresh.file_uri, plan.file_refresh.md5) == (
        uri,
        content_md5(b"unchanged"),
    )


@pytest.mark.asyncio
async def test_batch_vectors_only_existing_file_enqueues_merge_patch_without_hydration(
    monkeypatch,
    _coordinator_queue_for_unit_tests,
):
    root = "viking://resources/wiki"
    uri = f"{root}/a.md"
    vfs = _VFS(root, {uri: "old"})

    class _VikingDB:
        hydrate_incremental_records = AsyncMock(
            side_effect=AssertionError("batch preparation must not hydrate full vector records")
        )

        async def get_incremental_inventory_by_uris(self, uris, **kwargs):
            del kwargs
            return {
                uri: {
                    "existing-l2": {
                        "id": "existing-l2",
                        "uri": uri,
                        "level": 2,
                        "md5": content_md5(b"old"),
                        "abstract": "old abstract",
                    }
                }
                for uri in uris
            }

    actions = []

    async def enqueue(direct_actions, **kwargs):
        del kwargs
        actions.extend(direct_actions)
        return True

    monkeypatch.setattr(content_write_module, "enqueue_direct_index_actions", enqueue)
    coordinator = ContentWriteCoordinator(vfs, vikingdb=_VikingDB())
    ctx = RequestContext(user=UserIdentifier.the_default_user(), role=Role.USER)

    await coordinator.batch_write(
        root_uri=root,
        operations=[
            {
                "uri": uri,
                "content": "new",
                "tags": ["team=search"],
                "tag_mode": "append",
            }
        ],
        ctx=ctx,
        wait=False,
        processing_mode="vectors_only",
    )

    assert actions == []
    refresh = _last_content_write_refresh(_coordinator_queue_for_unit_tests)
    plan = ContextUpdatePlan.from_dict(refresh["resource_plans"][0]["plan"])
    assert len(plan.direct_index_actions) == 1
    action = plan.direct_index_actions[0]
    assert action.action.value == "merge"
    assert action.record_id == "existing-l2"
    assert action.field_patch is not None
    assert action.field_patch.values == {"search_tags": ["team=search"]}
    assert action.field_patch.modes == {"search_tags": "append"}


@pytest.mark.asyncio
async def test_batch_resource_plan_uses_operation_processing_mode_and_tags(monkeypatch):
    root = "viking://resources/wiki"
    uri = f"{root}/a.md"
    vfs = _VFS(root, {uri: "old"})

    class _VikingDB:
        async def get_incremental_inventory_by_uris(self, uris, **kwargs):
            del kwargs
            return {item: {} for item in uris}

    coordinator = ContentWriteCoordinator(vfs, vikingdb=_VikingDB())
    seen = {}

    async def build_snapshot(**kwargs):
        seen["request"] = kwargs["request_intent"]
        return SimpleNamespace(
            request=kwargs["request_intent"], vectors=SimpleNamespace(records_by_id={})
        )

    async def build_plan(**kwargs):
        del kwargs
        return ContextUpdatePlan(uri, "resource")

    monkeypatch.setattr(content_write_module, "build_rnfv_snapshot", build_snapshot)
    monkeypatch.setattr(coordinator, "_build_deferred_batch_file_plan", build_plan)
    monkeypatch.setattr(coordinator, "_refresh_batch", AsyncMock(return_value=None))
    ctx = RequestContext(user=UserIdentifier.the_default_user(), role=Role.USER)

    await coordinator.batch_write(
        root_uri=root,
        operations=[
            {
                "uri": uri,
                "content": "new",
                "mode": "replace",
                "tags": ["team=search"],
                "tag_mode": "append",
            }
        ],
        ctx=ctx,
        wait=False,
        processing_mode="vectors_only",
    )

    request = seen["request"]
    assert request.processing_mode == "vectors_only"
    assert request.scalar_intents[0].value == ("team=search",)
    assert request.scalar_intents[0].mode == "append"


@pytest.mark.asyncio
async def test_batch_resource_plan_allows_clear_tags_without_tags(monkeypatch):
    root = "viking://resources/wiki"
    uri = f"{root}/a.md"
    vfs = _VFS(root, {uri: "old"})

    class _VikingDB:
        async def get_incremental_inventory_by_uris(self, uris, **kwargs):
            del kwargs
            return {item: {} for item in uris}

    coordinator = ContentWriteCoordinator(vfs, vikingdb=_VikingDB())
    seen = {}

    async def build_snapshot(**kwargs):
        seen["request"] = kwargs["request_intent"]
        return SimpleNamespace(
            request=kwargs["request_intent"], vectors=SimpleNamespace(records_by_id={})
        )

    async def build_plan(**kwargs):
        del kwargs
        return ContextUpdatePlan(uri, "resource")

    monkeypatch.setattr(content_write_module, "build_rnfv_snapshot", build_snapshot)
    monkeypatch.setattr(coordinator, "_build_deferred_batch_file_plan", build_plan)
    monkeypatch.setattr(coordinator, "_refresh_batch", AsyncMock(return_value=None))
    ctx = RequestContext(user=UserIdentifier.the_default_user(), role=Role.USER)

    await coordinator.batch_write(
        root_uri=root,
        operations=[{"uri": uri, "content": "new", "tag_mode": "clear"}],
        ctx=ctx,
        wait=False,
    )

    assert seen["request"].scalar_intents[0].value == ()
    # The public ``clear`` spelling reaches RNFV as its backend-compatible
    # equivalent: replace the scalar field with an empty tag list.
    assert seen["request"].scalar_intents[0].mode == "replace"


@pytest.mark.asyncio
async def test_batch_applies_operation_acl_and_forwards_file_options(
    monkeypatch,
    _coordinator_queue_for_unit_tests,
):
    root = "viking://resources/wiki"
    uri = f"{root}/a.md"
    vfs = _VFS(root)
    prepared_update = AclUpdate(
        uri=uri,
        acl=AclSpec(acl_mode="restricted", entries=()),
    )
    vfs.prepare_acl_update = AsyncMock(return_value=prepared_update)
    vfs.acl_manager = SimpleNamespace(apply_indexed_update=AsyncMock())
    coordinator = ContentWriteCoordinator(vfs)
    ctx = RequestContext(user=UserIdentifier.the_default_user(), role=Role.USER)

    await coordinator.batch_write(
        root_uri=root,
        operations=[
            {
                "uri": uri,
                "content": "new",
                "tags": ["team=search"],
                "tag_mode": "append",
                "acl": {"acl_mode": "restricted", "entries": []},
            }
        ],
        ctx=ctx,
        wait=False,
    )

    vfs.prepare_acl_update.assert_awaited_once()
    vfs.acl_manager.apply_indexed_update.assert_awaited_once_with(prepared_update, ctx)
    file_options = _last_content_write_refresh(_coordinator_queue_for_unit_tests)[
        "file_ingest_options"
    ]
    assert file_options[uri]["search_tags"] == ["team=search"]
    assert file_options[uri]["search_tag_mode"] == "append"
    assert file_options[uri]["acl_update"] == prepared_update.model_dump(mode="json")


@pytest.mark.asyncio
async def test_batch_same_content_applies_tags(
    monkeypatch,
    _coordinator_queue_for_unit_tests,
):
    root = "viking://resources/wiki"
    uri = f"{root}/a.md"
    vfs = _VFS(root, {uri: "same"})
    coordinator = ContentWriteCoordinator(vfs)
    ctx = RequestContext(user=UserIdentifier.the_default_user(), role=Role.USER)

    await coordinator.batch_write(
        root_uri=root,
        operations=[
            {
                "uri": uri,
                "content": "same",
                "tags": ["team=search"],
            }
        ],
        ctx=ctx,
        wait=False,
        skip_conflicts=True,
    )

    assert vfs.writes == [uri]
    refresh = _last_content_write_refresh(_coordinator_queue_for_unit_tests)
    assert refresh["file_ingest_options"][uri]["search_tags"] == ["team=search"]


@pytest.mark.asyncio
async def test_batch_hands_off_file_locks_after_one_aggregated_refresh(
    _coordinator_queue_for_unit_tests,
):
    root = "viking://resources/wiki"
    a = f"{root}/a.md"
    b = f"{root}/b.md"
    vfs = _VFS(root)
    locks = vfs._async_agfs
    coordinator = ContentWriteCoordinator(vfs)
    telemetry = OperationTelemetry(operation="content.batch_write", enabled=False)
    ctx = RequestContext(user=UserIdentifier.the_default_user(), role=Role.USER)

    with bind_telemetry(telemetry):
        result = await coordinator.batch_write(
            root_uri=root,
            operations=[
                {"uri": b, "content": "B", "mode": "upsert"},
                {"uri": a, "content": "A", "mode": "upsert"},
            ],
            ctx=ctx,
            wait=False,
        )

    assert vfs.writes == [a, b]
    assert result["created"] == [a, b]
    assert result["queue_status"] is None
    assert result["semantic_status"] == "queued"
    assert result["vector_status"] == "queued"
    assert locks.releases == 0
    assert locks.handoffs == 1
    payload = _coordinator_queue_for_unit_tests.content_write.enqueue.await_args.args[0]
    assert payload["operation"] == "content_batch_write"
    assert payload["resource_id"] == root
    assert payload["result"]["created"] == [a, b]
    assert payload["refresh"]["kind"] == "batch"
    assert payload["refresh"]["refresh_kinds"] == {a: "added", b: "added"}
    assert payload["lock_handoff"]["owner_id"] == "lock-1"


@pytest.mark.asyncio
async def test_batch_retries_after_root_enqueue_failure(
    _coordinator_queue_for_unit_tests,
):
    root = "viking://resources/wiki"
    target = f"{root}/a.md"
    vfs = _VFS(root)
    coordinator = ContentWriteCoordinator(vfs)
    ctx = RequestContext(user=UserIdentifier.the_default_user(), role=Role.USER)
    _coordinator_queue_for_unit_tests.content_write.enqueue.side_effect = RuntimeError(
        "queue unavailable"
    )

    with pytest.raises(RuntimeError, match="queue unavailable"):
        await coordinator.batch_write(
            root_uri=root,
            operations=[{"uri": target, "content": "A", "mode": "upsert"}],
            ctx=ctx,
            wait=False,
        )

    _coordinator_queue_for_unit_tests.content_write.enqueue.side_effect = None
    result = await coordinator.batch_write(
        root_uri=root,
        operations=[{"uri": target, "content": "B", "mode": "upsert"}],
        ctx=ctx,
        wait=False,
    )

    assert result["updated"] == [target]
    assert vfs.files[target] == "B"


@pytest.mark.asyncio
async def test_batch_cancellation_waits_for_root_enqueue_settlement(
    _coordinator_queue_for_unit_tests,
):
    root = "viking://resources/wiki"
    target = f"{root}/a.md"
    vfs = _VFS(root)
    coordinator = ContentWriteCoordinator(vfs)
    ctx = RequestContext(user=UserIdentifier.the_default_user(), role=Role.USER)
    enqueue_started = asyncio.Event()
    finish_enqueue = asyncio.Event()

    async def enqueue(_payload):
        enqueue_started.set()
        await finish_enqueue.wait()
        return EnqueueReceipt("queued", "content-write-work-1")

    _coordinator_queue_for_unit_tests.content_write.enqueue.side_effect = enqueue
    request = asyncio.create_task(
        coordinator.batch_write(
            root_uri=root,
            operations=[{"uri": target, "content": "A", "mode": "upsert"}],
            ctx=ctx,
            wait=False,
        )
    )
    await enqueue_started.wait()
    request.cancel()
    await asyncio.sleep(0)
    assert not request.done()

    finish_enqueue.set()
    with pytest.raises(asyncio.CancelledError):
        await request

    assert vfs._async_agfs.handoffs == 1
    assert vfs._async_agfs.releases == 0
    assert vfs._async_agfs.held is False


@pytest.mark.asyncio
async def test_batch_passes_final_md5s_and_old_abstracts_to_refresh(
    _coordinator_queue_for_unit_tests,
):
    root = "viking://resources/wiki"
    existing = f"{root}/existing.md"
    created = f"{root}/new.md"
    vfs = _VFS(root, {existing: "old"})

    class _VikingDB:
        async def get_l2_diff_records_by_uris(self, uris, *, ctx):
            assert uris == [existing]
            return {existing: {"abstract": "old abstract", "md5": "old-md5"}}

    coordinator = ContentWriteCoordinator(vfs, vikingdb=_VikingDB())
    ctx = RequestContext(user=UserIdentifier.the_default_user(), role=Role.USER)

    await coordinator.batch_write(
        root_uri=root,
        operations=[
            {"uri": existing, "content": "new", "mode": "replace"},
            {"uri": created, "content": "created", "mode": "create"},
        ],
        ctx=ctx,
        wait=False,
    )

    refresh = _last_content_write_refresh(_coordinator_queue_for_unit_tests)
    assert refresh["file_md5s"] == {
        existing: content_md5(b"new"),
        created: content_md5(b"created"),
    }
    assert refresh["file_abstracts"] == {existing: "old abstract"}


@pytest.mark.asyncio
async def test_batch_replace_memory_preserves_metadata(monkeypatch):
    root = "viking://user/default/memories/preferences"
    memory_uri = f"{root}/theme.md"
    metadata = {
        "tags": ["ui", "preference"],
        "fields": {"topic": "theme"},
        "version": 7,
    }
    original = MemoryFileUtils.write(
        MemoryFile(content="Original preference", extra_fields=metadata)
    )
    expected = MemoryFileUtils.read(original, uri=memory_uri)
    vfs = _VFS(root, {memory_uri: original})
    coordinator = ContentWriteCoordinator(vfs)

    async def refresh(**kwargs):
        del kwargs
        return None

    monkeypatch.setattr(coordinator, "_refresh_batch", refresh)
    ctx = RequestContext(user=UserIdentifier.the_default_user(), role=Role.USER)

    result = await coordinator.batch_write(
        root_uri=root,
        operations=[
            {
                "uri": memory_uri,
                "content": "Updated preference",
                "mode": "replace",
            }
        ],
        ctx=ctx,
        wait=False,
    )

    stored = MemoryFileUtils.read(vfs.files[memory_uri], uri=memory_uri)
    assert result["updated"] == [memory_uri]
    assert stored.content == "Updated preference"
    assert stored.extra_fields == expected.extra_fields


@pytest.mark.asyncio
async def test_batch_reports_skipped_directory_and_queued_file_vectors(monkeypatch):
    root = "viking://resources/wide"
    page = f"{root}/page.md"
    coordinator = ContentWriteCoordinator(_VFS(root))

    async def enqueue(**kwargs):
        del kwargs
        return FreshnessAction.NOOP

    monkeypatch.setattr(coordinator, "_enqueue_semantic_refresh_changes", enqueue)
    ctx = RequestContext(user=UserIdentifier.the_default_user(), role=Role.USER)

    result = await coordinator.batch_write(
        root_uri=root,
        operations=[
            {
                "uri": page,
                "content": "content",
                "mode": "create",
            }
        ],
        ctx=ctx,
        wait=False,
    )

    assert result["semantic_status"] == "queued"
    assert result["vector_status"] == "queued"
    assert result["queue_status"] is None


@pytest.mark.asyncio
async def test_batch_upserts_binary_content(_coordinator_queue_for_unit_tests):
    root = "viking://resources/wiki"
    image = f"{root}/figure.png"
    original = b"\x89PNG\r\n\x1a\nold"
    replacement = b"\x89PNG\r\n\x1a\nnew"
    vfs = _VFS(root, {image: original})
    coordinator = ContentWriteCoordinator(vfs)
    ctx = RequestContext(user=UserIdentifier.the_default_user(), role=Role.USER)
    operation = {
        "uri": image,
        "content_base64": base64.b64encode(replacement).decode(),
        "mode": "upsert",
    }
    result = await coordinator.batch_write(
        root_uri=root, operations=[operation], ctx=ctx, wait=False
    )
    assert result["updated"] == [image]
    assert vfs.files[image] == replacement
    refresh = _last_content_write_refresh(_coordinator_queue_for_unit_tests)
    assert refresh["file_md5s"] == {image: content_md5(replacement)}

    retry = await coordinator.batch_write(
        root_uri=root, operations=[operation], ctx=ctx, wait=False
    )
    assert retry["updated"] == [image]


@pytest.mark.asyncio
async def test_batch_rejects_invalid_binary_payload_and_memory_binary():
    ctx = RequestContext(user=UserIdentifier.the_default_user(), role=Role.USER)
    resource_root = "viking://resources/wiki"
    coordinator = ContentWriteCoordinator(_VFS(resource_root))
    with pytest.raises(InvalidArgumentError, match="content_base64 is invalid"):
        await coordinator.batch_write(
            root_uri=resource_root,
            operations=[
                {
                    "uri": f"{resource_root}/figure.png",
                    "content_base64": "not base64!",
                    "mode": "upsert",
                }
            ],
            ctx=ctx,
            wait=False,
        )

    memory_root = "viking://user/default/memories/preferences/wiki"
    coordinator = ContentWriteCoordinator(_VFS(memory_root))
    with pytest.raises(InvalidArgumentError, match="not supported for memories"):
        await coordinator.batch_write(
            root_uri=memory_root,
            operations=[
                {
                    "uri": f"{memory_root}/figure.png",
                    "content_base64": base64.b64encode(b"png").decode(),
                    "mode": "upsert",
                }
            ],
            ctx=ctx,
            wait=False,
        )


@pytest.mark.asyncio
async def test_batch_partial_failure_refreshes_successful_files_and_retry_is_safe(
    _coordinator_queue_for_unit_tests,
):
    root = "viking://resources/wiki"
    a = f"{root}/a.md"
    b = f"{root}/b.md"
    vfs = _VFS(root, fail_uri=b)
    coordinator = ContentWriteCoordinator(vfs)
    ctx = RequestContext(user=UserIdentifier.the_default_user(), role=Role.USER)
    operations = [
        {"uri": a, "content": "A", "mode": "upsert"},
        {"uri": b, "content": "B", "mode": "upsert"},
    ]
    with pytest.raises(OSError, match="injected"):
        await coordinator.batch_write(root_uri=root, operations=operations, ctx=ctx, wait=False)
    payload = _last_content_write_payload(_coordinator_queue_for_unit_tests)
    assert payload["failure"]["message"] == "injected write failure"
    assert payload["refresh"]["refresh_kinds"] == {a: "added"}

    vfs.fail_uri = None
    result = await coordinator.batch_write(
        root_uri=root, operations=operations, ctx=ctx, wait=False
    )
    assert result["updated"] == [a]
    assert result["created"] == [b]
    refresh = _last_content_write_refresh(_coordinator_queue_for_unit_tests)
    assert refresh["refresh_kinds"] == {a: "modified", b: "added"}


@pytest.mark.asyncio
async def test_batch_refresh_groups_resource_and_memory_work(monkeypatch):
    coordinator = ContentWriteCoordinator(_VFS("viking://resources/wiki"), vikingdb=object())
    semantic_calls = []
    overview_calls = []
    embedding_calls = []

    async def resolve_root(uri, **kwargs):
        del uri, kwargs
        return "viking://resources/wiki"

    async def enqueue(**kwargs):
        semantic_calls.append(kwargs)
        return FreshnessAction.MARK_PENDING

    async def overview(**kwargs):
        overview_calls.append(kwargs)

    async def embedding(**kwargs):
        embedding_calls.append(kwargs)
        return False

    monkeypatch.setattr(coordinator, "_resolve_root_uri", resolve_root)
    monkeypatch.setattr(coordinator, "_enqueue_semantic_refresh_changes", enqueue)
    monkeypatch.setattr(content_write_module.MemoryUpdater, "refresh_schema_overview", overview)
    monkeypatch.setattr(content_write_module.MemoryUpdater, "refresh_file_embedding", embedding)
    ctx = RequestContext(user=UserIdentifier.the_default_user(), role=Role.USER)
    outcome = await coordinator._refresh_batch(
        refresh_kinds={
            "viking://resources/wiki/a.md": "added",
            "viking://resources/wiki/b.md": "modified",
            "viking://user/default/memories/preferences/wiki/a.md": "added",
            "viking://user/default/memories/preferences/wiki/b.md": "modified",
        },
        ctx=ctx,
        wait=False,
        telemetry_id="",
    )
    assert len(semantic_calls) == 1
    assert semantic_calls[0]["changes"] == {
        "added": ["viking://resources/wiki/a.md"],
        "modified": ["viking://resources/wiki/b.md"],
    }
    assert len(overview_calls) == 1
    assert overview_calls[0]["strict"] is True
    assert len(embedding_calls) == 2
    assert outcome.statuses(wait=False) == ("deferred", "queued")
    assert all(call["strict"] is True for call in embedding_calls)


@pytest.mark.asyncio
async def test_batch_write_api_upserts_files(client_with_resource):
    client, root = client_with_resource
    listing = await client.get(
        "/api/v1/fs/ls",
        params={"uri": root, "simple": True, "recursive": True},
    )
    existing = listing.json()["result"][0]
    created = f"{root}/compile-batch-created.md"
    operations = [
        {
            "uri": existing,
            "content": "# Batch updated",
            "mode": "upsert",
        },
        {
            "uri": created,
            "content": "# Batch created",
            "mode": "upsert",
        },
    ]
    first = await client.post(
        "/api/v1/content/batch-write",
        json={"root_uri": root, "operations": operations, "wait": False},
    )
    assert first.status_code == 200
    assert first.json()["result"]["updated"] == [existing]
    assert first.json()["result"]["created"] == [created]

    retry = await client.post(
        "/api/v1/content/batch-write",
        json={"root_uri": root, "operations": operations, "wait": False},
    )
    assert retry.status_code == 409

    waited = await client.post(
        "/api/v1/system/wait",
        json={},
    )
    assert waited.status_code == 200

    retry = await client.post(
        "/api/v1/content/batch-write",
        json={"root_uri": root, "operations": operations, "wait": False},
    )
    assert retry.status_code == 200
    assert retry.json()["result"]["updated"] == sorted([existing, created])


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("filename", "content"),
    [
        ("compile-figure.png", b"\x89PNG\r\n\x1a\ncompile"),
        ("compile-report.pdf", b"%PDF-1.7\ncompile"),
    ],
)
async def test_batch_write_api_creates_binary_file(client_with_resource, filename, content):
    client, root = client_with_resource
    artifact = f"{root}/{filename}"
    response = await client.post(
        "/api/v1/content/batch-write",
        json={
            "root_uri": root,
            "wait": False,
            "operations": [
                {
                    "uri": artifact,
                    "content_base64": base64.b64encode(content).decode(),
                    "mode": "upsert",
                }
            ],
        },
    )
    assert response.status_code == 200
    assert response.json()["result"]["created"] == [artifact]
    downloaded = await client.get("/api/v1/content/download", params={"uri": artifact})
    assert downloaded.status_code == 200
    assert downloaded.content == content


@pytest.mark.asyncio
async def test_batch_write_api_create_overwrites_and_batch_continues(client_with_resource):
    client, root = client_with_resource
    listing = await client.get(
        "/api/v1/fs/ls",
        params={"uri": root, "simple": True, "recursive": True},
    )
    existing = listing.json()["result"][0]
    created = f"{root}/compile-created-by-upsert.md"
    response = await client.post(
        "/api/v1/content/batch-write",
        json={
            "root_uri": root,
            "wait": False,
            "operations": [
                {
                    "uri": existing,
                    "content": "create aliases replace",
                    "mode": "create",
                },
                {
                    "uri": created,
                    "content": "created by upsert",
                    "mode": "upsert",
                },
            ],
        },
    )
    assert response.status_code == 200
    result = response.json()["result"]
    assert result["updated"] == [existing]
    assert result["created"] == [created]
    existing_body = await client.get("/api/v1/content/read", params={"uri": existing, "raw": True})
    assert existing_body.status_code == 200
    assert existing_body.json()["result"] == "create aliases replace"
    created_body = await client.get("/api/v1/content/read", params={"uri": created, "raw": True})
    assert created_body.status_code == 200
    assert created_body.json()["result"] == "created by upsert"


@pytest.mark.asyncio
async def test_batch_write_api_missing_append_creates_file(client_with_resource):
    client, root = client_with_resource
    target = f"{root}/compile-created-by-append.md"

    response = await client.post(
        "/api/v1/content/batch-write",
        json={
            "root_uri": root,
            "wait": False,
            "operations": [
                {
                    "uri": target,
                    "content": "initial append body",
                    "mode": "append",
                }
            ],
        },
    )

    assert response.status_code == 200
    assert response.json()["result"]["created"] == [target]
    body = await client.get("/api/v1/content/read", params={"uri": target, "raw": True})
    assert body.status_code == 200
    assert body.json()["result"] == "initial append body"


@pytest.mark.asyncio
async def test_batch_write_rejects_path_traversal(client_with_resource):
    client, root = client_with_resource
    response = await client.post(
        "/api/v1/content/batch-write",
        json={
            "root_uri": root,
            "wait": False,
            "operations": [
                {
                    "uri": f"{root}/../escaped.md",
                    "content": "escape",
                    "mode": "upsert",
                }
            ],
        },
    )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "INVALID_ARGUMENT"
