import base64
from types import SimpleNamespace

import pytest

import openviking.storage.content_write as content_write_module
from openviking.server.identity import RequestContext, Role
from openviking.session.memory.dataclass import MemoryFile
from openviking.session.memory.utils import MemoryFileUtils
from openviking.storage.content_write import ContentWriteCoordinator
from openviking.storage.context_update_plan import (
    ContentTreeAction,
    ContextUpdatePlan,
    FileRefreshIntent,
)
from openviking.storage.queuefs.semantic_ops.freshness_policy import FreshnessAction
from openviking.utils.content_hash import content_md5
from openviking_cli.exceptions import (
    InvalidArgumentError,
    NotFoundError,
    OpenVikingError,
)
from openviking_cli.session.user_id import UserIdentifier


class _PathLockClient:
    def __init__(self):
        self.held = False
        self.releases = 0

    async def pathlock_acquire_exact_batch(self, paths):
        del paths
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
async def test_batch_resource_files_compile_independent_rnfv_plans(monkeypatch):
    root = "viking://resources/wiki"
    first = f"{root}/docs/a.md"
    second = f"{root}/docs/b.md"
    vfs = _VFS(root)

    class _VikingDB:
        def __init__(self):
            self.calls = []

        async def get_incremental_inventory_by_uris(self, uris, **kwargs):
            self.calls.append((list(uris), kwargs))
            return {uri: {} for uri in uris}

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
        uri = kwargs["snapshot"].request.target_uri
        return None, ContextUpdatePlan(
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
        assert vfs._async_agfs.held is False
        refresh_calls.append(kwargs["changes"])
        return {
            "status": "success",
            "enqueued_count": 1,
            "parent_actions": {f"{root}/docs": "refresh_now"},
        }

    monkeypatch.setattr(content_write_module, "build_rnfv_snapshot", build_snapshot)
    monkeypatch.setattr(content_write_module, "build_context_update_plan_from_snapshot", build_plan)
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

    assert result["created"] == [first, second]
    assert vikingdb.calls[0][0] == [first, second]
    assert len(snapshots) == 2
    assert all(snapshot["root_is_file"] is True for snapshot in snapshots)
    assert all(snapshot["vector_scope"] == "self" for snapshot in snapshots)
    assert all(snapshot["vector_inventory"] == {} for snapshot in snapshots)
    assert len(commits) == 2
    assert direct_actions == []
    assert len(refresh_calls) == 1
    assert {change.file_uri for change in refresh_calls[0]} == {first, second}


@pytest.mark.asyncio
async def test_batch_releases_file_locks_before_one_aggregated_refresh(monkeypatch):
    root = "viking://resources/wiki"
    a = f"{root}/a.md"
    b = f"{root}/b.md"
    vfs = _VFS(root)
    locks = vfs._async_agfs
    coordinator = ContentWriteCoordinator(vfs)
    calls = []

    async def refresh(**kwargs):
        assert locks.held is False
        calls.append(kwargs["refresh_kinds"])
        return {"Semantic": {"processed": 1, "error_count": 0}}

    monkeypatch.setattr(coordinator, "_refresh_batch", refresh)
    ctx = RequestContext(user=UserIdentifier.the_default_user(), role=Role.USER)
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
    assert calls == [{a: "added", b: "added"}]
    assert locks.releases == 1


@pytest.mark.asyncio
async def test_batch_passes_final_md5s_and_old_abstracts_to_refresh(monkeypatch):
    root = "viking://resources/wiki"
    existing = f"{root}/existing.md"
    created = f"{root}/new.md"
    vfs = _VFS(root, {existing: "old"})

    class _VikingDB:
        async def get_l2_diff_records_by_uris(self, uris, *, ctx):
            assert uris == [existing]
            return {existing: {"abstract": "old abstract", "md5": "old-md5"}}

    coordinator = ContentWriteCoordinator(vfs, vikingdb=_VikingDB())
    captured = {}

    async def refresh(**kwargs):
        captured.update(kwargs)
        return None

    monkeypatch.setattr(coordinator, "_refresh_batch", refresh)
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

    assert captured["file_md5s"] == {
        existing: content_md5(b"new"),
        created: content_md5(b"created"),
    }
    assert captured["file_abstracts"] == {existing: "old abstract"}


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

    assert result["semantic_status"] == "skipped"
    assert result["vector_status"] == "queued"
    assert result["queue_status"] is None


@pytest.mark.asyncio
async def test_batch_upserts_binary_content(monkeypatch):
    root = "viking://resources/wiki"
    image = f"{root}/figure.png"
    original = b"\x89PNG\r\n\x1a\nold"
    replacement = b"\x89PNG\r\n\x1a\nnew"
    vfs = _VFS(root, {image: original})
    coordinator = ContentWriteCoordinator(vfs)

    refresh_calls = []

    async def refresh(**kwargs):
        refresh_calls.append(kwargs)
        return None

    monkeypatch.setattr(coordinator, "_refresh_batch", refresh)
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
    assert refresh_calls[0]["file_md5s"] == {image: content_md5(replacement)}

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
async def test_batch_partial_failure_refreshes_successful_files_and_retry_is_safe(monkeypatch):
    root = "viking://resources/wiki"
    a = f"{root}/a.md"
    b = f"{root}/b.md"
    vfs = _VFS(root, fail_uri=b)
    locks = vfs._async_agfs
    coordinator = ContentWriteCoordinator(vfs)
    calls = []

    async def refresh(**kwargs):
        assert locks.held is False
        calls.append(dict(kwargs["refresh_kinds"]))

    monkeypatch.setattr(coordinator, "_refresh_batch", refresh)
    ctx = RequestContext(user=UserIdentifier.the_default_user(), role=Role.USER)
    operations = [
        {"uri": a, "content": "A", "mode": "upsert"},
        {"uri": b, "content": "B", "mode": "upsert"},
    ]
    with pytest.raises(OSError, match="injected"):
        await coordinator.batch_write(root_uri=root, operations=operations, ctx=ctx, wait=False)
    assert calls == [{a: "added"}]

    vfs.fail_uri = None
    result = await coordinator.batch_write(
        root_uri=root, operations=operations, ctx=ctx, wait=False
    )
    assert result["updated"] == [a]
    assert result["created"] == [b]
    assert calls[-1] == {a: "modified", b: "added"}


@pytest.mark.asyncio
async def test_batch_refresh_failure_retry_rewrites_and_refreshes(monkeypatch):
    root = "viking://resources/wiki"
    page = f"{root}/page.md"
    vfs = _VFS(root)
    coordinator = ContentWriteCoordinator(vfs)
    calls = []

    async def refresh(**kwargs):
        calls.append(dict(kwargs["refresh_kinds"]))
        if len(calls) == 1:
            raise RuntimeError("injected refresh failure")

    monkeypatch.setattr(coordinator, "_refresh_batch", refresh)
    ctx = RequestContext(user=UserIdentifier.the_default_user(), role=Role.USER)
    operations = [
        {
            "uri": page,
            "content": "landed",
            "mode": "upsert",
        }
    ]

    with pytest.raises(OpenVikingError) as error:
        await coordinator.batch_write(root_uri=root, operations=operations, ctx=ctx, wait=False)
    assert error.value.code == "REFRESH_FAILED"
    assert "injected refresh failure" in str(error.value)
    assert "do not blindly retry an already-successful append" in str(error.value)
    assert error.value.details["created"] == [page]
    assert vfs.files[page] == "landed"
    assert vfs.writes == [page]

    result = await coordinator.batch_write(
        root_uri=root, operations=operations, ctx=ctx, wait=False
    )
    assert result["updated"] == [page]
    assert vfs.writes == [page, page]
    assert calls == [{page: "added"}, {page: "modified"}]


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
        timeout=None,
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
