"""Tests for reindex admin endpoint and executor behavior."""

import httpx
import pytest

from openviking.core.context import ContextLevel
from openviking.server.identity import RequestContext, Role
from openviking_cli.exceptions import OpenVikingError, PermissionDeniedError
from openviking_cli.session.user_id import UserIdentifier
from tests.server.test_admin_api import ROOT_KEY
from tests.server.test_admin_api import admin_app as _admin_app_fixture
from tests.server.test_admin_api import admin_client as _admin_client_fixture
from tests.server.test_admin_api import admin_service as _admin_service_fixture

admin_service = _admin_service_fixture
admin_app = _admin_app_fixture
admin_client = _admin_client_fixture

ROOT_ACCOUNT_HEADERS = {
    "X-API-Key": ROOT_KEY,
    "X-OpenViking-Account": "default",
}


def _make_reindex_run(ctx, counters):
    from openviking.service.reindex_executor import _ReindexRunContext

    return _ReindexRunContext(ctx=ctx, counters=counters)


async def test_reindex_requires_admin_role(admin_client: httpx.AsyncClient):
    resp = await admin_client.post(
        "/api/v1/content/reindex",
        json={"uri": "viking://resources/demo", "mode": "vectors_only"},
    )
    assert resp.status_code == 401


async def test_reindex_user_can_only_target_own_user_scope(monkeypatch):
    from inspect import signature

    from openviking.server.routers.content import ReindexRequest, reindex

    ctx = RequestContext(
        user=UserIdentifier(account_id="reindex_user_scope", user_id="bob"),
        role=Role.USER,
    )
    role_dependency = signature(reindex).parameters["ctx"].default.dependency
    assert await role_dependency(ctx=ctx) == ctx
    seen = {}

    class FakeService:
        async def reindex(self, *, uri, mode, wait, dry_run=False, ctx):
            seen.update(uri=uri, mode=mode, wait=wait, dry_run=dry_run, ctx=ctx)
            return {"status": "completed", "uri": uri, "mode": mode}

    monkeypatch.setattr("openviking.server.routers.content.get_service", lambda: FakeService())

    own_scope = await reindex(
        body=ReindexRequest(uri="viking://user/resources", mode="vectors_only"),
        ctx=ctx,
    )
    assert own_scope.status == "ok"
    assert seen["uri"] == "viking://user/bob/resources"
    assert seen["ctx"].role == Role.USER
    assert seen["ctx"].account_id == "reindex_user_scope"

    with pytest.raises(PermissionDeniedError):
        await reindex(
            body=ReindexRequest(uri="viking://resources/shared", mode="vectors_only"),
            ctx=ctx,
        )

    with pytest.raises(PermissionDeniedError):
        await reindex(
            body=ReindexRequest(uri="viking://user/alice/resources", mode="vectors_only"),
            ctx=ctx,
        )

    peer_ctx = RequestContext(
        user=ctx.user,
        role=Role.USER,
        actor_peer_id="peer-a",
    )
    peer_scope = await reindex(
        body=ReindexRequest(
            uri="viking://user/bob/peers/peer-a/resources",
            mode="vectors_only",
        ),
        ctx=peer_ctx,
    )
    assert peer_scope.status == "ok"
    assert seen["uri"] == "viking://user/bob/peers/peer-a/resources"

    for hidden_uri in (
        "viking://user/bob",
        "viking://user/bob/peers",
        "viking://user/bob/peers/peer-b/resources",
    ):
        with pytest.raises(PermissionDeniedError):
            await reindex(
                body=ReindexRequest(uri=hidden_uri, mode="vectors_only"),
                ctx=peer_ctx,
            )


async def test_reindex_rejects_unsupported_uri(admin_client: httpx.AsyncClient):
    resp = await admin_client.post(
        "/api/v1/content/reindex",
        json={"uri": "viking://unknown/demo", "mode": "vectors_only"},
        headers=ROOT_ACCOUNT_HEADERS,
    )
    assert resp.status_code == 403
    body = resp.json()
    assert body["status"] == "error"


@pytest.mark.parametrize(
    "uri",
    [
        "viking://session/test/demo",
        "viking://user/default/sessions/test/demo",
    ],
)
async def test_reindex_rejects_session_uri(admin_client: httpx.AsyncClient, uri: str):
    resp = await admin_client.post(
        "/api/v1/content/reindex",
        json={"uri": uri, "mode": "vectors_only"},
        headers=ROOT_ACCOUNT_HEADERS,
    )
    assert resp.status_code == 403
    body = resp.json()
    assert body["status"] == "error"


async def test_reindex_rejects_reason_field(admin_client: httpx.AsyncClient):
    resp = await admin_client.post(
        "/api/v1/content/reindex",
        json={
            "uri": "viking://resources/demo",
            "mode": "vectors_only",
            "reason": "unused",
        },
        headers=ROOT_ACCOUNT_HEADERS,
    )
    assert resp.status_code == 403


async def test_reindex_root_requires_explicit_account(admin_client: httpx.AsyncClient):
    resp = await admin_client.post(
        "/api/v1/content/reindex",
        json={"uri": "viking://resources/demo", "mode": "vectors_only"},
        headers={"X-API-Key": ROOT_KEY},
    )
    assert resp.status_code == 403
    body = resp.json()
    assert body["status"] == "error"


@pytest.mark.asyncio
async def test_reindex_resource_vectors_only_wait_true(monkeypatch):
    from openviking.server.routers.content import ReindexRequest, reindex

    seen = {}

    class FakeService:
        async def reindex(self, *, uri, mode, wait, ctx, dry_run=False):
            seen["uri"] = uri
            seen["mode"] = mode
            seen["wait"] = wait
            seen["ctx"] = ctx
            return {
                "status": "completed",
                "uri": uri,
                "object_type": "resource",
                "mode": mode,
                "rebuilt_records": 1,
                "scanned_records": 1,
                "unsupported_records": 0,
                "failed_records": 0,
                "duration_ms": 12,
                "warnings": [],
            }

    ctx = RequestContext(
        user=UserIdentifier(account_id="test", user_id="alice"),
        role=Role.ROOT,
    )
    request = ReindexRequest(uri="viking://resources/demo", mode="vectors_only", wait=True)

    monkeypatch.setattr("openviking.server.routers.content.get_service", lambda: FakeService())
    response = await reindex(body=request, ctx=ctx)

    assert response.status == "ok"
    assert response.result["status"] == "completed"
    assert response.result["object_type"] == "resource"
    assert response.result["rebuilt_records"] == 1
    assert seen["uri"] == "viking://resources/demo"
    assert seen["mode"] == "vectors_only"
    assert seen["wait"] is True
    assert seen["ctx"] == ctx
    assert "reason" not in response.result


@pytest.mark.asyncio
async def test_reindex_resource_vectors_only_wait_false(monkeypatch):
    from openviking.server.routers.content import ReindexRequest, reindex

    class FakeService:
        async def reindex(self, *, uri, mode, wait, ctx, dry_run=False):
            return {
                "task_id": "rbld_123",
                "status": "accepted",
                "uri": uri,
                "object_type": "resource",
                "mode": mode,
            }

    ctx = RequestContext(
        user=UserIdentifier(account_id="test", user_id="alice"),
        role=Role.ROOT,
    )
    request = ReindexRequest(uri="viking://resources/demo", mode="vectors_only", wait=False)

    monkeypatch.setattr("openviking.server.routers.content.get_service", lambda: FakeService())
    response = await reindex(body=request, ctx=ctx)

    assert response.status == "ok"
    assert response.result["status"] == "accepted"
    assert response.result["task_id"] == "rbld_123"
    assert response.result["object_type"] == "resource"
    assert "reason" not in response.result


@pytest.mark.asyncio
async def test_reindex_prune_orphans_passes_dry_run_to_service(monkeypatch):
    from openviking.server.routers.content import ReindexRequest, reindex

    seen = {}

    class FakeService:
        async def reindex(self, *, uri, mode, wait, ctx, dry_run=False):
            seen["uri"] = uri
            seen["mode"] = mode
            seen["wait"] = wait
            seen["dry_run"] = dry_run
            return {
                "status": "completed",
                "uri": uri,
                "object_type": "resource",
                "mode": mode,
                "scanned_records": 1,
                "deleted_records": 0,
                "would_delete_records": 1,
                "unsupported_records": 0,
                "failed_records": 0,
                "duration_ms": 1,
                "warnings": [],
            }

    ctx = RequestContext(
        user=UserIdentifier(account_id="test", user_id="alice"),
        role=Role.ROOT,
    )
    request = ReindexRequest(
        uri="viking://resources/demo",
        mode="prune_orphans",
        wait=True,
        dry_run=True,
    )

    monkeypatch.setattr("openviking.server.routers.content.get_service", lambda: FakeService())
    response = await reindex(body=request, ctx=ctx)

    assert response.status == "ok"
    assert response.result["would_delete_records"] == 1
    assert seen == {
        "uri": "viking://resources/demo",
        "mode": "prune_orphans",
        "wait": True,
        "dry_run": True,
    }


@pytest.mark.asyncio
async def test_reindex_rejects_dry_run_for_non_prune_mode(monkeypatch):
    from openviking.server.routers.content import ReindexRequest, reindex

    class FakeService:
        async def reindex(self, **kwargs):
            raise AssertionError("service should not be called")

    ctx = RequestContext(
        user=UserIdentifier(account_id="test", user_id="alice"),
        role=Role.ROOT,
    )
    request = ReindexRequest(
        uri="viking://resources/demo",
        mode="vectors_only",
        wait=True,
        dry_run=True,
    )

    monkeypatch.setattr("openviking.server.routers.content.get_service", lambda: FakeService())
    with pytest.raises(OpenVikingError, match="dry_run"):
        await reindex(body=request, ctx=ctx)


@pytest.mark.asyncio
async def test_reindex_executor_rejects_dry_run_for_non_prune_mode_direct():
    from openviking.service.reindex_executor import ReindexExecutor

    ctx = RequestContext(
        user=UserIdentifier(account_id="test", user_id="alice"),
        role=Role.ROOT,
    )

    with pytest.raises(OpenVikingError, match="dry_run"):
        await ReindexExecutor().execute(
            uri="viking://resources/demo",
            mode="vectors_only",
            wait=True,
            dry_run=True,
            ctx=ctx,
        )


@pytest.mark.asyncio
async def test_reindex_upsert_uses_uri_owner_for_user_scoped_records(monkeypatch):
    from openviking.service.reindex_executor import ReindexExecutor

    captured = {}

    class FakeVikingDB:
        async def get_context_by_uri(self, uri, owner_space=None, level=None, limit=1, *, ctx=None):
            captured["lookup_ctx"] = ctx
            return []

        async def enqueue_embedding_msg(self, msg):
            captured["msg"] = msg
            return True

    fake_service = type("Svc", (), {"vikingdb_manager": FakeVikingDB()})()
    monkeypatch.setattr("openviking.service.reindex_executor.get_service", lambda: fake_service)

    service = ReindexExecutor()
    ctx = RequestContext(
        user=UserIdentifier(account_id="acct", user_id="admin"),
        role=Role.ROOT,
    )

    await service._upsert_context(
        uri="viking://user/bob/memories/preferences/theme.md",
        parent_uri="viking://user/bob/memories/preferences",
        abstract="theme",
        vector_text="theme",
        is_leaf=True,
        context_type="memory",
        level=ContextLevel.DETAIL,
        ctx=ctx,
    )

    data = captured["msg"].context_data
    assert data["user"]["user_id"] == "bob"
    assert data["owner_user_id"] == "bob"
    assert data["owner_space"] == "bob"
    assert captured["lookup_ctx"].user.user_id == "bob"


@pytest.mark.asyncio
async def test_reindex_semantic_processor_uses_uri_owner_for_user_scoped_records(monkeypatch):
    from openviking.service.reindex_executor import ReindexExecutor

    captured = {}

    class FakeSemanticProcessor:
        def __init__(self, **_kwargs):
            pass

        async def on_dequeue(self, payload, lock=None):
            captured["payload"] = payload
            captured["lock"] = lock

    monkeypatch.setattr(
        "openviking.service.reindex_executor.SemanticProcessor",
        FakeSemanticProcessor,
    )

    service = ReindexExecutor()
    ctx = RequestContext(
        user=UserIdentifier(account_id="acct", user_id="admin"),
        role=Role.ROOT,
    )

    await service._run_semantic_processor(
        uri="viking://user/bob/memories/preferences",
        context_type="memory",
        ctx=ctx,
    )

    data = captured["payload"]["data"]
    assert '"user_id": "bob"' in data
    assert '"peer_id": "bob"' in data
    assert '"account_id": "acct"' in data


@pytest.mark.asyncio
async def test_prune_orphans_candidate_filter_includes_target_uri(monkeypatch):
    from openviking.service.reindex_executor import ReindexExecutor, _ReindexCounters
    from openviking.storage.expr import And, Eq, Or, PathScope

    target_uri = "viking://resources/demo/missing.txt"

    def matches_target_self(filter_expr):
        if isinstance(filter_expr, And):
            return all(matches_target_self(cond) for cond in filter_expr.conds)
        if isinstance(filter_expr, Or):
            return any(matches_target_self(cond) for cond in filter_expr.conds)
        if isinstance(filter_expr, Eq):
            return True
        if isinstance(filter_expr, PathScope):
            return filter_expr.path == target_uri and filter_expr.depth == 0
        return False

    class FakeVikingFS:
        async def exists(self, uri, ctx=None):
            return False

    class FakeVikingDB:
        async def filter(self, *, filter, limit, output_fields, ctx):
            if not matches_target_self(filter):
                return []
            return [
                {
                    "id": "target",
                    "uri": target_uri,
                    "level": 2,
                    "context_type": "resource",
                    "account_id": "test",
                }
            ]

        async def delete(self, ids, *, ctx):
            raise AssertionError("no records should be deleted")

    fake_service = type("Svc", (), {"vikingdb_manager": FakeVikingDB()})()
    monkeypatch.setattr("openviking.service.reindex_executor.get_service", lambda: fake_service)
    monkeypatch.setattr("openviking.service.reindex_executor.get_viking_fs", lambda: FakeVikingFS())

    counters = _ReindexCounters()
    ctx = RequestContext(
        user=UserIdentifier(account_id="test", user_id="admin"),
        role=Role.ROOT,
    )

    await ReindexExecutor()._prune_orphan_vectors(
        uri=target_uri,
        object_type="resource",
        dry_run=True,
        counters=counters,
        ctx=ctx,
    )

    assert counters.scanned_records == 1
    assert counters.would_delete_records == 1


@pytest.mark.asyncio
async def test_prune_orphans_dry_run_preserves_resource_l1_fallback_and_skips_unknown(
    monkeypatch,
):
    from openviking.service.reindex_executor import ReindexExecutor, _ReindexCounters

    class FakeVikingFS:
        async def exists(self, uri, ctx=None):
            if uri == "viking://resources/demo/.abstract.md":
                return True
            if uri == "viking://resources/demo/.overview.md":
                return False
            return False

        async def read_file(self, uri, ctx=None):
            if uri == "viking://resources/demo/.abstract.md":
                return "resource abstract"
            raise FileNotFoundError(uri)

    class FakeVikingDB:
        async def filter(self, *, filter, limit, output_fields, ctx):
            assert limit == 100000
            assert ctx.account_id == "test"
            return [
                {
                    "id": "keep_l1",
                    "uri": "viking://resources/demo",
                    "level": 1,
                    "context_type": "resource",
                    "account_id": "test",
                },
                {
                    "id": "delete_l2",
                    "uri": "viking://resources/demo/missing.txt",
                    "level": 2,
                    "context_type": "resource",
                    "account_id": "test",
                },
                {
                    "id": "skip_unknown",
                    "uri": "viking://resources/demo/unknown",
                    "level": 99,
                    "context_type": "resource",
                    "account_id": "test",
                },
            ]

        async def delete(self, ids, *, ctx):
            raise AssertionError("dry_run should not delete")

    fake_service = type("Svc", (), {"vikingdb_manager": FakeVikingDB()})()
    monkeypatch.setattr("openviking.service.reindex_executor.get_service", lambda: fake_service)
    monkeypatch.setattr("openviking.service.reindex_executor.get_viking_fs", lambda: FakeVikingFS())

    service = ReindexExecutor()
    counters = _ReindexCounters()
    ctx = RequestContext(
        user=UserIdentifier(account_id="test", user_id="admin"),
        role=Role.ROOT,
    )

    await service._prune_orphan_vectors(
        uri="viking://resources/demo",
        object_type="resource",
        dry_run=True,
        counters=counters,
        ctx=ctx,
    )

    assert counters.scanned_records == 3
    assert counters.deleted_records == 0
    assert counters.would_delete_records == 1
    assert counters.unsupported_records == 1
    assert any("unknown" in warning for warning in counters.warnings)


@pytest.mark.asyncio
async def test_prune_orphans_deletes_stale_memory_chunk_with_uri_owner_ctx(monkeypatch):
    from openviking.service.reindex_executor import ReindexExecutor, _ReindexCounters

    base_uri = "viking://user/bob/memories/preferences/theme.md"
    deleted = {}

    class FakeVikingFS:
        async def exists(self, uri, ctx=None):
            return uri == base_uri

        async def read_file(self, uri, ctx=None):
            assert uri == base_uri
            return "memory body"

    class FakeVikingDB:
        async def filter(self, *, filter, limit, output_fields, ctx):
            assert limit == 100000
            assert ctx.account_id == "acct"
            return [
                {
                    "id": "keep_base",
                    "uri": base_uri,
                    "level": 2,
                    "context_type": "memory",
                    "account_id": "acct",
                    "owner_user_id": "bob",
                },
                {
                    "id": "keep_chunk",
                    "uri": f"{base_uri}#chunk_0000",
                    "level": 2,
                    "context_type": "memory",
                    "account_id": "acct",
                    "owner_user_id": "bob",
                },
                {
                    "id": "delete_chunk",
                    "uri": f"{base_uri}#chunk_0001",
                    "level": 2,
                    "context_type": "memory",
                    "account_id": "acct",
                    "owner_user_id": "bob",
                },
            ]

        async def delete(self, ids, *, ctx):
            deleted["ids"] = ids
            deleted["ctx"] = ctx
            return len(ids)

    fake_service = type("Svc", (), {"vikingdb_manager": FakeVikingDB()})()
    monkeypatch.setattr("openviking.service.reindex_executor.get_service", lambda: fake_service)
    monkeypatch.setattr("openviking.service.reindex_executor.get_viking_fs", lambda: FakeVikingFS())
    monkeypatch.setattr(
        ReindexExecutor,
        "_chunk_memory_body",
        lambda self, uri, body: [(f"{uri}#chunk_0000", body)],
    )

    service = ReindexExecutor()
    counters = _ReindexCounters()
    ctx = RequestContext(
        user=UserIdentifier(account_id="acct", user_id="admin"),
        role=Role.ROOT,
    )

    await service._prune_orphan_vectors(
        uri="viking://user/bob/memories",
        object_type="memory",
        dry_run=False,
        counters=counters,
        ctx=ctx,
    )

    assert deleted["ids"] == ["delete_chunk"]
    assert deleted["ctx"].user.user_id == "bob"
    assert counters.deleted_records == 1
    assert counters.would_delete_records == 0


@pytest.mark.asyncio
async def test_prune_orphans_skips_resource_l1_when_sidecar_read_fails(monkeypatch):
    from openviking.service.reindex_executor import ReindexExecutor, _ReindexCounters

    class FakeVikingFS:
        async def exists(self, uri, ctx=None):
            return uri in {
                "viking://resources/demo/.overview.md",
                "viking://resources/demo/.abstract.md",
            }

        async def read_file(self, uri, ctx=None):
            raise OSError("temporary storage failure")

    class FakeVikingDB:
        async def filter(self, *, filter, limit, output_fields, ctx):
            return [
                {
                    "id": "keep_l1",
                    "uri": "viking://resources/demo",
                    "level": 1,
                    "context_type": "resource",
                    "account_id": "test",
                }
            ]

        async def delete(self, ids, *, ctx):
            raise AssertionError("read failures must not delete vectors")

    fake_service = type("Svc", (), {"vikingdb_manager": FakeVikingDB()})()
    monkeypatch.setattr("openviking.service.reindex_executor.get_service", lambda: fake_service)
    monkeypatch.setattr("openviking.service.reindex_executor.get_viking_fs", lambda: FakeVikingFS())

    counters = _ReindexCounters()
    ctx = RequestContext(
        user=UserIdentifier(account_id="test", user_id="admin"),
        role=Role.ROOT,
    )

    await ReindexExecutor()._prune_orphan_vectors(
        uri="viking://resources/demo",
        object_type="resource",
        dry_run=False,
        counters=counters,
        ctx=ctx,
    )

    assert counters.deleted_records == 0
    assert counters.failed_records == 1
    assert any("failed to read" in warning for warning in counters.warnings)


@pytest.mark.asyncio
async def test_prune_orphans_skips_memory_chunks_when_base_read_fails(monkeypatch):
    from openviking.service.reindex_executor import ReindexExecutor, _ReindexCounters

    base_uri = "viking://user/bob/memories/preferences/theme.md"

    class FakeVikingFS:
        async def exists(self, uri, ctx=None):
            return uri == base_uri

        async def read_file(self, uri, ctx=None):
            raise OSError("temporary storage failure")

    class FakeVikingDB:
        async def filter(self, *, filter, limit, output_fields, ctx):
            return [
                {
                    "id": "keep_chunk",
                    "uri": f"{base_uri}#chunk_0001",
                    "level": 2,
                    "context_type": "memory",
                    "account_id": "acct",
                    "owner_user_id": "bob",
                }
            ]

        async def delete(self, ids, *, ctx):
            raise AssertionError("base read failures must not delete chunks")

    fake_service = type("Svc", (), {"vikingdb_manager": FakeVikingDB()})()
    monkeypatch.setattr("openviking.service.reindex_executor.get_service", lambda: fake_service)
    monkeypatch.setattr("openviking.service.reindex_executor.get_viking_fs", lambda: FakeVikingFS())

    counters = _ReindexCounters()
    ctx = RequestContext(
        user=UserIdentifier(account_id="acct", user_id="admin"),
        role=Role.ROOT,
    )

    await ReindexExecutor()._prune_orphan_vectors(
        uri="viking://user/bob/memories",
        object_type="memory",
        dry_run=False,
        counters=counters,
        ctx=ctx,
    )

    assert counters.deleted_records == 0
    assert counters.failed_records == 1
    assert any("failed to read" in warning for warning in counters.warnings)


@pytest.mark.asyncio
async def test_prune_orphans_deletes_missing_file_with_string_level(monkeypatch):
    from openviking.service.reindex_executor import ReindexExecutor, _ReindexCounters

    deleted = {}

    class FakeVikingFS:
        async def exists(self, uri, ctx=None):
            return False

    class FakeVikingDB:
        async def filter(self, *, filter, limit, output_fields, ctx):
            return [
                {
                    "id": "delete_l2",
                    "uri": "viking://resources/demo/missing.txt",
                    "level": "2",
                    "context_type": "resource",
                    "account_id": "test",
                }
            ]

        async def delete(self, ids, *, ctx):
            deleted["ids"] = ids
            return len(ids)

    fake_service = type("Svc", (), {"vikingdb_manager": FakeVikingDB()})()
    monkeypatch.setattr("openviking.service.reindex_executor.get_service", lambda: fake_service)
    monkeypatch.setattr("openviking.service.reindex_executor.get_viking_fs", lambda: FakeVikingFS())

    counters = _ReindexCounters()
    ctx = RequestContext(
        user=UserIdentifier(account_id="test", user_id="admin"),
        role=Role.ROOT,
    )

    await ReindexExecutor()._prune_orphan_vectors(
        uri="viking://resources/demo",
        object_type="resource",
        dry_run=False,
        counters=counters,
        ctx=ctx,
    )

    assert deleted["ids"] == ["delete_l2"]
    assert counters.deleted_records == 1
    assert counters.unsupported_records == 0


@pytest.mark.asyncio
async def test_prune_orphans_continues_after_delete_group_failure(monkeypatch):
    from openviking.service.reindex_executor import ReindexExecutor, _ReindexCounters

    deleted = {}
    filter_calls = 0

    class FakeVikingFS:
        async def exists(self, uri, ctx=None):
            return False

    class FakeVikingDB:
        async def filter(self, *, filter, limit, output_fields, ctx):
            nonlocal filter_calls
            filter_calls += 1
            if filter_calls > 1:
                return []
            return [
                {
                    "id": "fail_delete",
                    "uri": "viking://user/alice/docs/missing.txt",
                    "level": 2,
                    "context_type": "resource",
                    "account_id": "acct",
                    "owner_user_id": "alice",
                },
                {
                    "id": "ok_delete",
                    "uri": "viking://user/bob/docs/missing.txt",
                    "level": 2,
                    "context_type": "resource",
                    "account_id": "acct",
                    "owner_user_id": "bob",
                },
            ]

        async def delete(self, ids, *, ctx):
            if ctx.user.user_id == "alice":
                raise RuntimeError("delete backend unavailable")
            deleted["ids"] = ids
            deleted["ctx"] = ctx
            return len(ids)

    fake_service = type("Svc", (), {"vikingdb_manager": FakeVikingDB()})()
    monkeypatch.setattr("openviking.service.reindex_executor.get_service", lambda: fake_service)
    monkeypatch.setattr("openviking.service.reindex_executor.get_viking_fs", lambda: FakeVikingFS())

    counters = _ReindexCounters()
    ctx = RequestContext(
        user=UserIdentifier(account_id="acct", user_id="admin"),
        role=Role.ROOT,
    )

    await ReindexExecutor()._prune_orphan_vectors(
        uri="viking://user",
        object_type="user_namespace",
        dry_run=False,
        counters=counters,
        ctx=ctx,
    )

    assert deleted["ids"] == ["ok_delete"]
    assert deleted["ctx"].user.user_id == "bob"
    assert counters.deleted_records == 1
    assert counters.failed_records == 1
    assert any("Failed to delete" in warning for warning in counters.warnings)


@pytest.mark.asyncio
async def test_prune_orphans_pages_past_candidate_limit(monkeypatch):
    from openviking.service import reindex_executor
    from openviking.service.reindex_executor import ReindexExecutor, _ReindexCounters

    filter_offsets = []
    deleted = {}

    class FakeVikingFS:
        async def exists(self, uri, ctx=None):
            return not uri.endswith("missing.txt")

    class FakeVikingDB:
        async def filter(self, *, filter, limit, output_fields, ctx, offset=0):
            filter_offsets.append(offset)
            records = [
                {
                    "id": "keep_1",
                    "uri": "viking://resources/demo/keep-1.txt",
                    "level": 2,
                    "context_type": "resource",
                    "account_id": "acct",
                },
                {
                    "id": "keep_2",
                    "uri": "viking://resources/demo/keep-2.txt",
                    "level": 2,
                    "context_type": "resource",
                    "account_id": "acct",
                },
                {
                    "id": "delete_3",
                    "uri": "viking://resources/demo/missing.txt",
                    "level": 2,
                    "context_type": "resource",
                    "account_id": "acct",
                },
            ]
            return records[offset : offset + limit]

        async def delete(self, ids, *, ctx):
            deleted["ids"] = ids
            return len(ids)

    fake_service = type("Svc", (), {"vikingdb_manager": FakeVikingDB()})()
    monkeypatch.setattr("openviking.service.reindex_executor.get_service", lambda: fake_service)
    monkeypatch.setattr("openviking.service.reindex_executor.get_viking_fs", lambda: FakeVikingFS())
    monkeypatch.setattr(reindex_executor, "PRUNE_ORPHAN_CANDIDATE_LIMIT", 2)

    counters = _ReindexCounters()
    ctx = RequestContext(
        user=UserIdentifier(account_id="acct", user_id="admin"),
        role=Role.ROOT,
    )

    await ReindexExecutor()._prune_orphan_vectors(
        uri="viking://resources/demo",
        object_type="resource",
        dry_run=False,
        counters=counters,
        ctx=ctx,
    )

    assert filter_offsets == [0, 2]
    assert counters.scanned_records == 3
    assert deleted["ids"] == ["delete_3"]
    assert counters.deleted_records == 1


@pytest.mark.asyncio
async def test_prune_orphans_reports_zero_delete_count_as_failure(monkeypatch):
    from openviking.service.reindex_executor import ReindexExecutor, _ReindexCounters

    class FakeVikingFS:
        async def exists(self, uri, ctx=None):
            return False

    class FakeVikingDB:
        async def filter(self, *, filter, limit, output_fields, ctx, offset=0):
            if offset:
                return []
            return [
                {
                    "id": "stale",
                    "uri": "viking://resources/demo/missing.txt",
                    "level": 2,
                    "context_type": "resource",
                    "account_id": "acct",
                }
            ]

        async def delete(self, ids, *, ctx):
            return 0

    fake_service = type("Svc", (), {"vikingdb_manager": FakeVikingDB()})()
    monkeypatch.setattr("openviking.service.reindex_executor.get_service", lambda: fake_service)
    monkeypatch.setattr("openviking.service.reindex_executor.get_viking_fs", lambda: FakeVikingFS())

    counters = _ReindexCounters()
    ctx = RequestContext(
        user=UserIdentifier(account_id="acct", user_id="admin"),
        role=Role.ROOT,
    )

    await ReindexExecutor()._prune_orphan_vectors(
        uri="viking://resources/demo",
        object_type="resource",
        dry_run=False,
        counters=counters,
        ctx=ctx,
    )

    assert counters.deleted_records == 0
    assert counters.failed_records == 1
    assert any("Only deleted 0 of 1" in warning for warning in counters.warnings)


@pytest.mark.asyncio
async def test_reindex_memory_supports_semantic_and_vectors(monkeypatch):
    from openviking.service.reindex_executor import ReindexExecutor

    service = ReindexExecutor()
    service._validate_mode("memory", "semantic_and_vectors")


@pytest.mark.asyncio
async def test_reindex_semantic_processor_uses_configured_vlm_concurrency(monkeypatch):
    from types import SimpleNamespace

    import openviking.service.reindex_executor as reindex_mod

    seen = {}

    class FakeSemanticProcessor:
        def __init__(self, max_concurrent_llm=64):
            seen["max_concurrent_llm"] = max_concurrent_llm

        async def on_dequeue(self, data, lock=None):
            seen["data"] = data

    monkeypatch.setattr(reindex_mod, "SemanticProcessor", FakeSemanticProcessor)
    monkeypatch.setattr(
        reindex_mod,
        "get_openviking_config",
        lambda: SimpleNamespace(vlm=SimpleNamespace(max_concurrent=2)),
    )

    ctx = RequestContext(
        user=UserIdentifier(account_id="test", user_id="alice"),
        role=Role.ROOT,
    )
    await reindex_mod.ReindexExecutor()._run_semantic_processor(
        uri="viking://resources/demo",
        context_type="resource",
        ctx=ctx,
    )

    assert seen["max_concurrent_llm"] == 2


@pytest.mark.asyncio
async def test_reindex_memory_semantic_and_vectors_rebuilds_full_subtree(monkeypatch):
    from openviking.service.reindex_executor import ReindexExecutor, _ReindexCounters

    seen = {"semantic": [], "vectors": []}

    async def fake_run_semantic_processor(self, *, uri, context_type, ctx, lock=None):
        seen["semantic"].append((uri, context_type))

    async def fake_reindex_memory_vectors(self, *, uri, counters, ctx):
        seen["vectors"].append(uri)

    class FakeVikingFS:
        async def stat(self, uri, ctx=None):
            return {"isDir": True}

    monkeypatch.setattr(ReindexExecutor, "_run_semantic_processor", fake_run_semantic_processor)
    monkeypatch.setattr(ReindexExecutor, "_reindex_memory_vectors", fake_reindex_memory_vectors)
    monkeypatch.setattr("openviking.service.reindex_executor.get_viking_fs", lambda: FakeVikingFS())

    service = ReindexExecutor()
    counters = _ReindexCounters()
    ctx = RequestContext(
        user=UserIdentifier(account_id="test", user_id="alice"),
        role=Role.ROOT,
    )

    await service._reindex_memory(
        uri="viking://user/default/memories",
        mode="semantic_and_vectors",
        run=_make_reindex_run(ctx, counters),
    )

    assert seen["semantic"] == [("viking://user/default/memories", "memory")]
    assert seen["vectors"] == ["viking://user/default/memories"]


@pytest.mark.asyncio
async def test_reindex_executor_infers_skill_supports_semantic_and_vectors():
    from openviking.service.reindex_executor import ReindexExecutor

    service = ReindexExecutor()
    service._validate_mode("skill", "semantic_and_vectors")
    service._validate_mode("skill_namespace", "semantic_and_vectors")


@pytest.mark.asyncio
async def test_reindex_executor_infers_resource_and_skill_container_scopes():
    from openviking.service.reindex_executor import ReindexExecutor

    service = ReindexExecutor()

    assert service._infer_target_type("viking://resources") == "resource"
    assert service._infer_target_type("viking://resources/demo.md") == "resource"
    assert service._infer_target_type("viking://user/default/skills") == "skill_namespace"
    assert service._infer_target_type("viking://user/default/skills/demo") == "skill"
    with pytest.raises(OpenVikingError, match="Unsupported reindex URI"):
        service._infer_target_type("viking://user/default/skills/demo/SKILL.md")


@pytest.mark.asyncio
async def test_reindex_executor_infers_user_namespace_root():
    from openviking.service.reindex_executor import ReindexExecutor

    service = ReindexExecutor()

    assert service._infer_target_type("viking://user/") == "user_namespace"
    assert service._infer_target_type("viking://user/default") == "user_namespace"


@pytest.mark.asyncio
async def test_reindex_executor_rejects_deprecated_agent_namespace_root():
    from openviking.service.reindex_executor import ReindexExecutor

    service = ReindexExecutor()

    with pytest.raises(OpenVikingError, match="no longer supported"):
        service._infer_target_type("viking://agent/")


@pytest.mark.asyncio
async def test_reindex_executor_infers_global_namespace_root():
    from openviking.service.reindex_executor import ReindexExecutor

    service = ReindexExecutor()

    assert service._infer_target_type("viking://") == "global_namespace"


@pytest.mark.asyncio
async def test_reindex_executor_does_not_treat_resource_named_memories_as_memory():
    from openviking.core.namespace import classify_uri
    from openviking.service.reindex_executor import ReindexExecutor

    service = ReindexExecutor()

    assert (
        service._infer_target_type("viking://user/default/resources/memories/report.md")
        == "resource"
    )
    assert not classify_uri("viking://user/default/resources/memories-report.md").is_memory
    assert not classify_uri("viking://user/default/resources/memories-report.md").is_memory


@pytest.mark.asyncio
async def test_reindex_executor_does_not_treat_skill_subdirectories_as_skill_roots():
    from openviking.core.namespace import classify_uri

    assert classify_uri("viking://user/default/skills/my_skill").is_skill_root
    assert not classify_uri("viking://user/default/skills/my_skill/assets").is_skill_root
    assert not classify_uri("viking://user/default/resources/skills-report.md").is_skill


@pytest.mark.asyncio
async def test_reindex_user_namespace_semantic_and_vectors_promotes_memory_mode(monkeypatch):
    from openviking.service.reindex_executor import ReindexExecutor, _ReindexCounters

    class FakeVikingFS:
        async def tree(
            self,
            uri,
            output="original",
            show_all_hidden=True,
            node_limit=1000,
            level_limit=None,
            ctx=None,
        ):
            return [
                {"uri": "viking://user/default/memories", "isDir": True},
                {"uri": "viking://user/default/resources", "isDir": True},
            ]

    seen = {"memory_modes": [], "semantic_calls": [], "resource_calls": []}

    async def fake_reindex_memory(self, *, uri, mode, run):
        seen["memory_modes"].append((uri, mode))

    async def fake_run_semantic_processor(self, *, uri, context_type, ctx, lock=None):
        seen["semantic_calls"].append((uri, context_type))

    async def fake_reindex_resource_vectors_from_entries(
        self, *, root_uri, directories, files, counters, ctx
    ):
        seen["resource_calls"].append((root_uri, list(directories), list(files)))

    monkeypatch.setattr("openviking.service.reindex_executor.get_viking_fs", lambda: FakeVikingFS())
    monkeypatch.setattr(ReindexExecutor, "_reindex_memory", fake_reindex_memory)
    monkeypatch.setattr(ReindexExecutor, "_run_semantic_processor", fake_run_semantic_processor)
    monkeypatch.setattr(
        ReindexExecutor,
        "_reindex_resource_vectors_from_entries",
        fake_reindex_resource_vectors_from_entries,
    )

    service = ReindexExecutor()
    counters = _ReindexCounters()
    ctx = RequestContext(
        user=UserIdentifier(account_id="test", user_id="alice"),
        role=Role.ROOT,
    )

    await service._reindex_user_namespace(
        uri="viking://user/default",
        mode="semantic_and_vectors",
        run=_make_reindex_run(ctx, counters),
    )

    assert seen["memory_modes"] == [("viking://user/default/memories", "semantic_and_vectors")]
    assert seen["semantic_calls"] == [("viking://user/default/resources", "resource")]
    assert seen["resource_calls"]


@pytest.mark.asyncio
async def test_reindex_user_namespace_semantic_and_vectors_does_not_reprocess_memory_as_resource(
    monkeypatch,
):
    from openviking.service.reindex_executor import ReindexExecutor, _ReindexCounters

    class FakeVikingFS:
        async def tree(
            self,
            uri,
            output="original",
            show_all_hidden=True,
            node_limit=1000,
            level_limit=None,
            ctx=None,
        ):
            return [
                {"uri": "viking://user/default/memories", "isDir": True},
                {"uri": "viking://user/default/memories/preferences", "isDir": True},
                {"uri": "viking://user/default/resources", "isDir": True},
            ]

    seen = {"memory_modes": [], "semantic_calls": []}

    async def fake_reindex_memory(self, *, uri, mode, run):
        seen["memory_modes"].append((uri, mode))

    async def fake_run_semantic_processor(self, *, uri, context_type, ctx, lock=None):
        seen["semantic_calls"].append((uri, context_type))

    async def fake_reindex_resource_vectors_from_entries(
        self, *, root_uri, directories, files, counters, ctx
    ):
        return None

    monkeypatch.setattr("openviking.service.reindex_executor.get_viking_fs", lambda: FakeVikingFS())
    monkeypatch.setattr(ReindexExecutor, "_reindex_memory", fake_reindex_memory)
    monkeypatch.setattr(ReindexExecutor, "_run_semantic_processor", fake_run_semantic_processor)
    monkeypatch.setattr(
        ReindexExecutor,
        "_reindex_resource_vectors_from_entries",
        fake_reindex_resource_vectors_from_entries,
    )

    service = ReindexExecutor()
    counters = _ReindexCounters()
    ctx = RequestContext(
        user=UserIdentifier(account_id="test", user_id="alice"),
        role=Role.ROOT,
    )

    await service._reindex_user_namespace(
        uri="viking://user/default",
        mode="semantic_and_vectors",
        run=_make_reindex_run(ctx, counters),
    )

    assert seen["memory_modes"] == [("viking://user/default/memories", "semantic_and_vectors")]
    assert seen["semantic_calls"] == [("viking://user/default/resources", "resource")]


@pytest.mark.asyncio
async def test_reindex_user_namespace_semantic_and_vectors_skips_uncovered_root_files(
    monkeypatch,
):
    from openviking.service.reindex_executor import ReindexExecutor, _ReindexCounters

    class FakeVikingFS:
        async def tree(
            self,
            uri,
            output="original",
            show_all_hidden=True,
            node_limit=1000,
            level_limit=None,
            ctx=None,
        ):
            return [
                {"uri": "viking://user/default/resources", "isDir": True},
                {"uri": "viking://user/default/resources/doc.md", "isDir": False},
                {"uri": "viking://user/default/profile.md", "isDir": False},
            ]

    seen = {"semantic_calls": [], "resource_files": []}

    async def fake_run_semantic_processor(self, *, uri, context_type, ctx, lock=None):
        seen["semantic_calls"].append((uri, context_type))

    async def fake_reindex_resource_vectors_from_entries(
        self, *, root_uri, directories, files, counters, ctx
    ):
        seen["resource_files"] = list(files)

    monkeypatch.setattr("openviking.service.reindex_executor.get_viking_fs", lambda: FakeVikingFS())
    monkeypatch.setattr(ReindexExecutor, "_run_semantic_processor", fake_run_semantic_processor)
    monkeypatch.setattr(
        ReindexExecutor,
        "_reindex_resource_vectors_from_entries",
        fake_reindex_resource_vectors_from_entries,
    )

    service = ReindexExecutor()
    counters = _ReindexCounters()
    ctx = RequestContext(
        user=UserIdentifier(account_id="test", user_id="alice"),
        role=Role.ROOT,
    )

    await service._reindex_user_namespace(
        uri="viking://user/default",
        mode="semantic_and_vectors",
        run=_make_reindex_run(ctx, counters),
    )

    assert seen["semantic_calls"] == [("viking://user/default/resources", "resource")]
    assert seen["resource_files"] == ["viking://user/default/resources/doc.md"]
    assert counters.unsupported_records == 1
    assert "viking://user/default/profile.md" in counters.warnings[0]


@pytest.mark.asyncio
async def test_reindex_skill_namespace_reindexes_only_skill_roots(monkeypatch):
    from openviking.service.reindex_executor import ReindexExecutor, _ReindexCounters

    class FakeVikingFS:
        async def tree(
            self,
            uri,
            output="original",
            show_all_hidden=True,
            node_limit=1000,
            level_limit=3,
            ctx=None,
        ):
            assert node_limit is None
            assert level_limit is None
            return [
                {"uri": "viking://user/default/skills/my_skill", "isDir": True},
                {"uri": "viking://user/default/skills/my_skill/assets", "isDir": True},
                {"uri": "viking://user/default/skills/my_skill/SKILL.md", "isDir": False},
            ]

    seen = []

    async def fake_reindex_skill(self, *, uri, mode, run):
        seen.append((uri, mode))

    monkeypatch.setattr("openviking.service.reindex_executor.get_viking_fs", lambda: FakeVikingFS())
    monkeypatch.setattr(ReindexExecutor, "_reindex_skill", fake_reindex_skill)

    service = ReindexExecutor()
    counters = _ReindexCounters()
    ctx = RequestContext(
        user=UserIdentifier(account_id="test", user_id="alice"),
        role=Role.ROOT,
    )

    await service._reindex_skill_namespace(
        uri="viking://user/default/skills",
        mode="semantic_and_vectors",
        run=_make_reindex_run(ctx, counters),
    )

    assert seen == [("viking://user/default/skills/my_skill", "semantic_and_vectors")]


@pytest.mark.asyncio
async def test_reindex_global_namespace_semantic_and_vectors_propagates_to_child_namespaces(
    monkeypatch,
):
    from openviking.service.reindex_executor import ReindexExecutor, _ReindexCounters

    class FakeVikingFS:
        async def tree(
            self,
            uri,
            output="original",
            show_all_hidden=True,
            node_limit=1000,
            level_limit=None,
            ctx=None,
        ):
            return [
                {"uri": "viking://user/default", "isDir": True},
                {"uri": "viking://user/default", "isDir": True},
                {"uri": "viking://resources", "isDir": True},
            ]

    seen = {"user_modes": [], "semantic_calls": [], "resource_calls": []}

    async def fake_reindex_user_namespace(self, *, uri, mode, run):
        seen["user_modes"].append((uri, mode))

    async def fake_run_semantic_processor(self, *, uri, context_type, ctx, lock=None):
        seen["semantic_calls"].append((uri, context_type))

    async def fake_reindex_resource_vectors_from_entries(
        self, *, root_uri, directories, files, counters, ctx
    ):
        seen["resource_calls"].append((root_uri, list(directories), list(files)))

    monkeypatch.setattr("openviking.service.reindex_executor.get_viking_fs", lambda: FakeVikingFS())
    monkeypatch.setattr(ReindexExecutor, "_reindex_user_namespace", fake_reindex_user_namespace)
    monkeypatch.setattr(ReindexExecutor, "_run_semantic_processor", fake_run_semantic_processor)
    monkeypatch.setattr(
        ReindexExecutor,
        "_reindex_resource_vectors_from_entries",
        fake_reindex_resource_vectors_from_entries,
    )

    service = ReindexExecutor()
    counters = _ReindexCounters()
    ctx = RequestContext(
        user=UserIdentifier(account_id="test", user_id="alice"),
        role=Role.ROOT,
    )

    await service._reindex_global_namespace(
        uri="viking://",
        mode="semantic_and_vectors",
        run=_make_reindex_run(ctx, counters),
    )

    assert seen["user_modes"] == [("viking://user/default", "semantic_and_vectors")]
    assert seen["semantic_calls"] == [("viking://resources", "resource")]
    assert seen["resource_calls"]


@pytest.mark.asyncio
async def test_reindex_fetch_existing_record_uses_get_context_by_uri(monkeypatch):
    from openviking.service.reindex_executor import ReindexExecutor

    class FakeVikingDB:
        def __init__(self):
            self.fetch_calls = []
            self.lookup_calls = []

        async def fetch_by_uri(self, uri, *, ctx):
            self.fetch_calls.append((uri, ctx))
            return {"uri": uri, "level": 2, "abstract": "from-fetch"}

        async def get_context_by_uri(self, uri, owner_space=None, level=None, limit=1, *, ctx=None):
            self.lookup_calls.append((uri, owner_space, level, limit, ctx))
            return [{"uri": uri, "level": level, "abstract": "from-lookup"}]

    fake_service = type("Svc", (), {"vikingdb_manager": FakeVikingDB()})()
    monkeypatch.setattr("openviking.service.reindex_executor.get_service", lambda: fake_service)

    service = ReindexExecutor()
    ctx = RequestContext(
        user=UserIdentifier(account_id="test", user_id="alice"),
        role=Role.ROOT,
    )

    record = await service._fetch_existing_record(
        uri="viking://resources/demo.txt",
        level=2,
        ctx=ctx,
    )

    assert record["abstract"] == "from-lookup"
    assert not fake_service.vikingdb_manager.fetch_calls
    assert fake_service.vikingdb_manager.lookup_calls


@pytest.mark.asyncio
async def test_reindex_upsert_context_preserves_existing_search_tags(monkeypatch):
    from openviking.service.reindex_executor import ReindexExecutor

    captured = {}

    class FakeVikingDB:
        async def get_context_by_uri(self, uri, owner_space=None, level=None, limit=1, *, ctx=None):
            del owner_space, limit, ctx
            return [
                {
                    "uri": uri,
                    "level": level,
                    "abstract": "existing",
                    "search_tags": ["team-a", "project-x"],
                }
            ]

        async def enqueue_embedding_msg(self, msg):
            captured["msg"] = msg
            return True

    fake_service = type("Svc", (), {"vikingdb_manager": FakeVikingDB()})()
    monkeypatch.setattr("openviking.service.reindex_executor.get_service", lambda: fake_service)

    class _FakeMsg:
        def __init__(self):
            self.telemetry_id = ""
            self.id = "msg-1"

    def fake_from_context(context):
        captured["meta"] = dict(context.meta or {})
        return _FakeMsg()

    monkeypatch.setattr(
        "openviking.service.reindex_executor.EmbeddingMsgConverter.from_context",
        fake_from_context,
    )

    service = ReindexExecutor()
    ctx = RequestContext(
        user=UserIdentifier(account_id="test", user_id="alice"),
        role=Role.ROOT,
    )

    await service._upsert_context(
        uri="viking://resources/demo.txt",
        parent_uri="viking://resources",
        abstract="new abstract",
        vector_text="new vector text",
        is_leaf=True,
        context_type="resource",
        level=ContextLevel.DETAIL,
        ctx=ctx,
    )

    assert captured["meta"]["search_tags"] == ["team-a", "project-x"]


@pytest.mark.asyncio
async def test_reindex_resource_vectors_only_continues_after_single_record_failure(monkeypatch):
    from openviking.service.reindex_executor import ReindexExecutor, _ReindexCounters
    from openviking_cli.exceptions import OpenVikingError

    class FakeVikingFS:
        async def exists(self, uri, ctx=None):
            return True

        async def stat(self, uri, ctx=None):
            return {"isDir": True}

        async def tree(
            self,
            uri,
            output="original",
            show_all_hidden=True,
            node_limit=1000,
            level_limit=3,
            ctx=None,
        ):
            assert node_limit is None
            assert level_limit is None
            return [
                {"uri": "viking://resources/demo/bad.txt", "isDir": False},
                {"uri": "viking://resources/demo/good.txt", "isDir": False},
            ]

    async def fake_read_directory_abstract(self, uri, *, ctx):
        return ""

    async def fake_read_directory_overview(self, uri, *, ctx):
        return ""

    async def fake_best_file_summary(self, uri, *, ctx):
        return f"summary:{uri.rsplit('/', 1)[-1]}"

    async def fake_best_resource_file_vector_text(self, uri, summary, ctx):
        return summary

    seen = []

    async def fake_upsert_context(self, **kwargs):
        seen.append(kwargs["uri"])
        if kwargs["uri"].endswith("bad.txt"):
            raise OpenVikingError("boom", code="PROCESSING_ERROR")

    monkeypatch.setattr("openviking.service.reindex_executor.get_viking_fs", lambda: FakeVikingFS())
    monkeypatch.setattr(ReindexExecutor, "_read_directory_abstract", fake_read_directory_abstract)
    monkeypatch.setattr(ReindexExecutor, "_read_directory_overview", fake_read_directory_overview)
    monkeypatch.setattr(ReindexExecutor, "_best_file_summary", fake_best_file_summary)
    monkeypatch.setattr(
        ReindexExecutor,
        "_best_resource_file_vector_text",
        fake_best_resource_file_vector_text,
    )
    monkeypatch.setattr(ReindexExecutor, "_upsert_context", fake_upsert_context)

    service = ReindexExecutor()
    counters = _ReindexCounters()
    ctx = RequestContext(
        user=UserIdentifier(account_id="test", user_id="alice"),
        role=Role.ROOT,
    )

    await service._reindex_resource_vectors(
        uri="viking://resources/demo",
        counters=counters,
        ctx=ctx,
    )

    assert seen == [
        "viking://resources/demo/bad.txt",
        "viking://resources/demo/good.txt",
    ]
    assert counters.failed_records == 1
    assert counters.rebuilt_records == 1


@pytest.mark.asyncio
async def test_reindex_semantic_processor_runs_with_skip_vectorization(monkeypatch):
    from openviking.service.reindex_executor import ReindexExecutor

    seen = {}

    class FakeSemanticProcessor:
        def __init__(self, **_kwargs):
            pass

        async def on_dequeue(self, payload, lock=None):
            seen["payload"] = payload

    monkeypatch.setattr(
        "openviking.service.reindex_executor.SemanticProcessor",
        FakeSemanticProcessor,
    )

    service = ReindexExecutor()
    ctx = RequestContext(
        user=UserIdentifier(account_id="test", user_id="alice"),
        role=Role.ROOT,
    )

    await service._run_semantic_processor(
        uri="viking://resources/demo",
        context_type="resource",
        ctx=ctx,
    )

    import json

    msg = json.loads(seen["payload"]["data"])
    assert msg["skip_vectorization"] is True


@pytest.mark.asyncio
async def test_reindex_resource_l2_falls_back_to_vector_text_when_summary_missing(monkeypatch):
    from openviking.service.reindex_executor import ReindexExecutor, _ReindexCounters

    class FakeVikingFS:
        async def exists(self, uri, ctx=None):
            return True

        async def stat(self, uri, ctx=None):
            return {"isDir": True}

        async def tree(
            self,
            uri,
            output="original",
            show_all_hidden=True,
            node_limit=1000,
            level_limit=3,
            ctx=None,
        ):
            assert node_limit is None
            assert level_limit is None
            return [{"uri": "viking://resources/demo/file.txt", "isDir": False}]

    seen = {}

    async def fake_read_directory_abstract(self, uri, *, ctx):
        return "skill abstract"

    async def fake_read_directory_overview(self, uri, *, ctx):
        return ""

    async def fake_best_file_summary(self, uri, *, ctx):
        return ""

    async def fake_best_resource_file_vector_text(self, uri, summary, ctx):
        return "raw file body"

    async def fake_upsert_context(self, **kwargs):
        seen[kwargs["uri"]] = kwargs

    monkeypatch.setattr("openviking.service.reindex_executor.get_viking_fs", lambda: FakeVikingFS())
    monkeypatch.setattr(ReindexExecutor, "_read_directory_abstract", fake_read_directory_abstract)
    monkeypatch.setattr(ReindexExecutor, "_read_directory_overview", fake_read_directory_overview)
    monkeypatch.setattr(ReindexExecutor, "_best_file_summary", fake_best_file_summary)
    monkeypatch.setattr(
        ReindexExecutor,
        "_best_resource_file_vector_text",
        fake_best_resource_file_vector_text,
    )
    monkeypatch.setattr(ReindexExecutor, "_upsert_context", fake_upsert_context)

    service = ReindexExecutor()
    counters = _ReindexCounters()
    ctx = RequestContext(
        user=UserIdentifier(account_id="test", user_id="alice"),
        role=Role.ROOT,
    )

    await service._reindex_resource_vectors(
        uri="viking://resources/demo",
        counters=counters,
        ctx=ctx,
    )

    assert seen["viking://resources/demo/file.txt"]["abstract"] == "raw file body"


@pytest.mark.asyncio
async def test_reindex_resource_vector_text_uses_existing_record_for_non_text(monkeypatch):
    from openviking.service.reindex_executor import ReindexExecutor

    async def fake_safe_read_text(self, uri, *, ctx):
        return "decoded binary payload"

    async def fake_fetch_existing_record(self, *, uri, level, ctx):
        return {"abstract": "existing image summary"}

    monkeypatch.setattr(ReindexExecutor, "_safe_read_text", fake_safe_read_text)
    monkeypatch.setattr(ReindexExecutor, "_fetch_existing_record", fake_fetch_existing_record)

    service = ReindexExecutor()
    ctx = RequestContext(
        user=UserIdentifier(account_id="test", user_id="alice"),
        role=Role.ROOT,
    )

    vector_text = await service._best_resource_file_vector_text(
        "viking://resources/demo/image.png",
        "",
        ctx=ctx,
    )

    assert vector_text == "existing image summary"


@pytest.mark.asyncio
async def test_reindex_file_summary_reads_existing_record_as_uri_owner(monkeypatch):
    from openviking.service.reindex_executor import ReindexExecutor

    captured = {}

    async def fake_safe_read_text(self, uri, *, ctx):
        return ""

    async def fake_fetch_existing_record(self, *, uri, level, ctx):
        captured["ctx"] = ctx
        return {"abstract": "owner summary"}

    monkeypatch.setattr(ReindexExecutor, "_safe_read_text", fake_safe_read_text)
    monkeypatch.setattr(ReindexExecutor, "_fetch_existing_record", fake_fetch_existing_record)

    service = ReindexExecutor()
    ctx = RequestContext(
        user=UserIdentifier(account_id="test", user_id="admin"),
        role=Role.ROOT,
    )

    summary = await service._best_file_summary(
        "viking://user/bob/resources/demo/image.png",
        ctx=ctx,
    )

    assert summary == "owner summary"
    assert captured["ctx"].user.user_id == "bob"


@pytest.mark.asyncio
async def test_reindex_memory_fallback_reads_existing_record_as_uri_owner(monkeypatch):
    from openviking.service.reindex_executor import (
        ReindexExecutor,
        _PruneSourceRead,
        _ReindexCounters,
    )

    captured = {}
    upserts = []

    class FakeVikingFS:
        async def exists(self, uri, ctx=None):
            return True

        async def stat(self, uri, ctx=None):
            return {"isDir": False}

    async def fake_read_memory_body(self, uri, *, ctx):
        return _PruneSourceRead(exists=True, text="")

    async def fake_fetch_existing_record(self, *, uri, level, ctx):
        captured["ctx"] = ctx
        return {"abstract": "owner memory summary"}

    async def fake_best_file_summary(self, uri, *, ctx):
        return ""

    async def fake_upsert_context(self, **kwargs):
        upserts.append(kwargs)

    monkeypatch.setattr("openviking.service.reindex_executor.get_viking_fs", lambda: FakeVikingFS())
    monkeypatch.setattr(ReindexExecutor, "_read_memory_body", fake_read_memory_body)
    monkeypatch.setattr(ReindexExecutor, "_fetch_existing_record", fake_fetch_existing_record)
    monkeypatch.setattr(ReindexExecutor, "_best_file_summary", fake_best_file_summary)
    monkeypatch.setattr(ReindexExecutor, "_upsert_context", fake_upsert_context)

    service = ReindexExecutor()
    ctx = RequestContext(
        user=UserIdentifier(account_id="test", user_id="admin"),
        role=Role.ROOT,
    )

    await service._reindex_memory_vectors(
        uri="viking://user/bob/memories/preferences/theme.md",
        counters=_ReindexCounters(),
        ctx=ctx,
    )

    assert captured["ctx"].user.user_id == "bob"
    assert upserts[0]["abstract"] == "owner memory summary"


@pytest.mark.asyncio
async def test_reindex_memory_skips_fallback_when_body_read_fails(monkeypatch):
    from openviking.service.reindex_executor import ReindexExecutor, _ReindexCounters

    upserts = []

    class FakeVikingFS:
        async def exists(self, uri, ctx=None):
            return True

        async def stat(self, uri, ctx=None):
            return {"isDir": False}

        async def read_file(self, uri, ctx=None):
            raise OSError("backend read failed")

    async def fake_fetch_existing_record(self, *, uri, level, ctx):
        return {"abstract": "existing summary"}

    async def fake_best_file_summary(self, uri, *, ctx):
        return ""

    async def fake_upsert_context(self, **kwargs):
        upserts.append(kwargs)

    monkeypatch.setattr("openviking.service.reindex_executor.get_viking_fs", lambda: FakeVikingFS())
    monkeypatch.setattr(ReindexExecutor, "_fetch_existing_record", fake_fetch_existing_record)
    monkeypatch.setattr(ReindexExecutor, "_best_file_summary", fake_best_file_summary)
    monkeypatch.setattr(ReindexExecutor, "_upsert_context", fake_upsert_context)

    service = ReindexExecutor()
    counters = _ReindexCounters()
    ctx = RequestContext(
        user=UserIdentifier(account_id="test", user_id="alice"),
        role=Role.ROOT,
    )

    await service._reindex_memory_vectors(
        uri="viking://user/default/memories/events/item.md",
        counters=counters,
        ctx=ctx,
    )

    assert upserts == []
    assert counters.failed_records == 1
    assert any("failed to read memory body" in warning for warning in counters.warnings)


@pytest.mark.asyncio
async def test_reindex_resource_vector_text_skips_non_text_body_without_summary(monkeypatch):
    from openviking.service.reindex_executor import ReindexExecutor

    async def fail_if_content_read(self, uri, *, ctx):
        raise AssertionError("non-text resource content should not be read for vector text")

    async def fake_fetch_existing_record(self, *, uri, level, ctx):
        return None

    monkeypatch.setattr(ReindexExecutor, "_safe_read_text", fail_if_content_read)
    monkeypatch.setattr(ReindexExecutor, "_fetch_existing_record", fake_fetch_existing_record)

    service = ReindexExecutor()
    ctx = RequestContext(
        user=UserIdentifier(account_id="test", user_id="alice"),
        role=Role.ROOT,
    )

    vector_text = await service._best_resource_file_vector_text(
        "viking://resources/demo/image.png",
        "",
        ctx=ctx,
    )

    assert vector_text == ""


@pytest.mark.asyncio
async def test_reindex_resource_vectors_accepts_single_file_uri(monkeypatch):
    from openviking.service.reindex_executor import ReindexExecutor, _ReindexCounters

    class FakeVikingFS:
        async def exists(self, uri, ctx=None):
            return True

        async def stat(self, uri, ctx=None):
            return {"isDir": False}

        async def tree(self, *args, **kwargs):
            raise AssertionError("single-file reindex should not call tree")

    seen = {}

    async def fake_best_file_summary(self, uri, *, ctx):
        return "file summary"

    async def fake_best_resource_file_vector_text(self, uri, summary, ctx):
        return summary

    async def fake_upsert_context(self, **kwargs):
        seen[kwargs["uri"]] = kwargs

    monkeypatch.setattr("openviking.service.reindex_executor.get_viking_fs", lambda: FakeVikingFS())
    monkeypatch.setattr(ReindexExecutor, "_best_file_summary", fake_best_file_summary)
    monkeypatch.setattr(
        ReindexExecutor,
        "_best_resource_file_vector_text",
        fake_best_resource_file_vector_text,
    )
    monkeypatch.setattr(ReindexExecutor, "_upsert_context", fake_upsert_context)

    service = ReindexExecutor()
    counters = _ReindexCounters()
    ctx = RequestContext(
        user=UserIdentifier(account_id="test", user_id="alice"),
        role=Role.ROOT,
    )

    await service._reindex_resource_vectors(
        uri="viking://resources/demo/file.txt",
        counters=counters,
        ctx=ctx,
    )

    assert list(seen) == ["viking://resources/demo/file.txt"]
    assert counters.scanned_records == 1
    assert counters.rebuilt_records == 1


@pytest.mark.asyncio
async def test_reindex_memory_l2_falls_back_to_body_when_abstract_missing(monkeypatch):
    from openviking.service.reindex_executor import (
        ReindexExecutor,
        _PruneSourceRead,
        _ReindexCounters,
    )

    class FakeVikingFS:
        async def exists(self, uri, ctx=None):
            return True

        async def stat(self, uri, ctx=None):
            return {"isDir": False}

    seen = {}

    async def fake_read_memory_body(self, uri, *, ctx):
        return _PruneSourceRead(exists=True, text="memory body text")

    async def fake_fetch_existing_record(self, *, uri, level, ctx):
        return None

    async def fake_best_file_summary(self, uri, *, ctx):
        return ""

    async def fake_upsert_context(self, **kwargs):
        seen[kwargs["uri"]] = kwargs

    monkeypatch.setattr("openviking.service.reindex_executor.get_viking_fs", lambda: FakeVikingFS())
    monkeypatch.setattr(ReindexExecutor, "_read_memory_body", fake_read_memory_body)
    monkeypatch.setattr(ReindexExecutor, "_fetch_existing_record", fake_fetch_existing_record)
    monkeypatch.setattr(ReindexExecutor, "_best_file_summary", fake_best_file_summary)
    monkeypatch.setattr(ReindexExecutor, "_upsert_context", fake_upsert_context)

    service = ReindexExecutor()
    counters = _ReindexCounters()
    ctx = RequestContext(
        user=UserIdentifier(account_id="test", user_id="alice"),
        role=Role.ROOT,
    )

    await service._reindex_memory_vectors(
        uri="viking://user/default/memories/events/item.md",
        counters=counters,
        ctx=ctx,
    )

    assert seen["viking://user/default/memories/events/item.md"]["abstract"] == "memory body text"


@pytest.mark.asyncio
async def test_reindex_memory_l2_strips_memory_fields_from_abstract(monkeypatch):
    from openviking.service.reindex_executor import (
        ReindexExecutor,
        _PruneSourceRead,
        _ReindexCounters,
    )

    class FakeVikingFS:
        async def exists(self, uri, ctx=None):
            return True

        async def stat(self, uri, ctx=None):
            return {"isDir": False}

    seen = {}
    raw_body = (
        "User has a preference for watermelon, as mentioned in the conversation: "
        '\'我爱吃西瓜\'. <!-- MEMORY_FIELDS { "user": "user", "topic": "food_preference" } -->'
    )

    async def fake_read_memory_body(self, uri, *, ctx):
        return _PruneSourceRead(exists=True, text=raw_body)

    async def fake_fetch_existing_record(self, *, uri, level, ctx):
        return None

    async def fake_best_file_summary(self, uri, *, ctx):
        return ""

    async def fake_upsert_context(self, **kwargs):
        seen[kwargs["uri"]] = kwargs

    monkeypatch.setattr("openviking.service.reindex_executor.get_viking_fs", lambda: FakeVikingFS())
    monkeypatch.setattr(ReindexExecutor, "_read_memory_body", fake_read_memory_body)
    monkeypatch.setattr(ReindexExecutor, "_fetch_existing_record", fake_fetch_existing_record)
    monkeypatch.setattr(ReindexExecutor, "_best_file_summary", fake_best_file_summary)
    monkeypatch.setattr(ReindexExecutor, "_upsert_context", fake_upsert_context)

    service = ReindexExecutor()
    counters = _ReindexCounters()
    ctx = RequestContext(
        user=UserIdentifier(account_id="test", user_id="alice"),
        role=Role.ROOT,
    )

    await service._reindex_memory_vectors(
        uri="viking://user/default/memories/preferences/food_preference.md",
        counters=counters,
        ctx=ctx,
    )

    assert (
        seen["viking://user/default/memories/preferences/food_preference.md"]["abstract"]
        == "User has a preference for watermelon, as mentioned in the conversation: '我爱吃西瓜'."
    )
    assert (
        seen["viking://user/default/memories/preferences/food_preference.md"]["vector_text"]
        == raw_body
    )


@pytest.mark.asyncio
async def test_reindex_memory_vectors_walks_deep_subtree(monkeypatch):
    from openviking.service.reindex_executor import (
        ReindexExecutor,
        _PruneSourceRead,
        _ReindexCounters,
    )

    class FakeVikingFS:
        async def exists(self, uri, ctx=None):
            return True

        async def stat(self, uri, ctx=None):
            return {"isDir": True}

        async def tree(
            self,
            uri,
            output="original",
            show_all_hidden=False,
            node_limit=1000,
            level_limit=3,
            ctx=None,
        ):
            if level_limit is not None:
                return [
                    {"uri": "viking://user/default/memories/preferences/user", "isDir": True},
                ]
            return [
                {"uri": "viking://user/default/memories/preferences/user", "isDir": True},
                {
                    "uri": "viking://user/default/memories/preferences/user/food_preference.md",
                    "isDir": False,
                },
            ]

    seen = {}

    async def fake_read_memory_body(self, uri, *, ctx):
        return _PruneSourceRead(exists=True, text="likes spicy food")

    async def fake_fetch_existing_record(self, *, uri, level, ctx):
        return None

    async def fake_best_file_summary(self, uri, *, ctx):
        return ""

    async def fake_upsert_context(self, **kwargs):
        seen[kwargs["uri"]] = kwargs

    monkeypatch.setattr("openviking.service.reindex_executor.get_viking_fs", lambda: FakeVikingFS())
    monkeypatch.setattr(ReindexExecutor, "_read_memory_body", fake_read_memory_body)
    monkeypatch.setattr(ReindexExecutor, "_fetch_existing_record", fake_fetch_existing_record)
    monkeypatch.setattr(ReindexExecutor, "_best_file_summary", fake_best_file_summary)
    monkeypatch.setattr(ReindexExecutor, "_upsert_context", fake_upsert_context)

    service = ReindexExecutor()
    counters = _ReindexCounters()
    ctx = RequestContext(
        user=UserIdentifier(account_id="test", user_id="alice"),
        role=Role.ROOT,
    )

    await service._reindex_memory_vectors(
        uri="viking://user/default/memories/preferences",
        counters=counters,
        ctx=ctx,
    )

    assert "viking://user/default/memories/preferences/user/food_preference.md" in seen


@pytest.mark.asyncio
async def test_reindex_memory_vectors_rebuilds_directory_levels_without_regenerating_semantics(
    monkeypatch,
):
    from openviking.core.context import ContextLevel
    from openviking.service.reindex_executor import (
        ReindexExecutor,
        _PruneSourceRead,
        _ReindexCounters,
    )

    class FakeVikingFS:
        async def exists(self, uri, ctx=None):
            return True

        async def stat(self, uri, ctx=None):
            return {"isDir": True}

        async def tree(
            self,
            uri,
            output="original",
            show_all_hidden=False,
            node_limit=1000,
            level_limit=3,
            ctx=None,
        ):
            return [
                {"uri": "viking://user/default/memories/preferences/user", "isDir": True},
                {
                    "uri": "viking://user/default/memories/preferences/user/food_preference.md",
                    "isDir": False,
                },
            ]

    seen = []

    async def fake_read_directory_abstract(self, uri, *, ctx):
        if uri == "viking://user/default/memories/preferences/user":
            return "user preferences abstract"
        return ""

    async def fake_read_directory_overview(self, uri, *, ctx):
        if uri == "viking://user/default/memories/preferences":
            return "preferences overview"
        if uri == "viking://user/default/memories/preferences/user":
            return "user preferences overview"
        return ""

    async def fake_read_memory_body(self, uri, *, ctx):
        return _PruneSourceRead(exists=True, text="likes spicy food")

    async def fake_fetch_existing_record(self, *, uri, level, ctx):
        return None

    async def fake_best_file_summary(self, uri, *, ctx):
        return ""

    async def fake_upsert_context(self, **kwargs):
        seen.append(
            {
                "uri": kwargs["uri"],
                "level": int(kwargs["level"]),
                "vector_text": kwargs["vector_text"],
            }
        )

    monkeypatch.setattr("openviking.service.reindex_executor.get_viking_fs", lambda: FakeVikingFS())
    monkeypatch.setattr(ReindexExecutor, "_read_directory_abstract", fake_read_directory_abstract)
    monkeypatch.setattr(ReindexExecutor, "_read_directory_overview", fake_read_directory_overview)
    monkeypatch.setattr(ReindexExecutor, "_read_memory_body", fake_read_memory_body)
    monkeypatch.setattr(ReindexExecutor, "_fetch_existing_record", fake_fetch_existing_record)
    monkeypatch.setattr(ReindexExecutor, "_best_file_summary", fake_best_file_summary)
    monkeypatch.setattr(ReindexExecutor, "_upsert_context", fake_upsert_context)

    service = ReindexExecutor()
    counters = _ReindexCounters()
    ctx = RequestContext(
        user=UserIdentifier(account_id="test", user_id="alice"),
        role=Role.ROOT,
    )

    await service._reindex_memory_vectors(
        uri="viking://user/default/memories/preferences",
        counters=counters,
        ctx=ctx,
    )

    assert {
        ("viking://user/default/memories/preferences", int(ContextLevel.OVERVIEW)),
        ("viking://user/default/memories/preferences/user", int(ContextLevel.ABSTRACT)),
        ("viking://user/default/memories/preferences/user", int(ContextLevel.OVERVIEW)),
        (
            "viking://user/default/memories/preferences/user/food_preference.md",
            int(ContextLevel.DETAIL),
        ),
    } <= {(item["uri"], item["level"]) for item in seen}


@pytest.mark.asyncio
async def test_reindex_user_namespace_partitions_memory_skill_and_resource(monkeypatch):
    from openviking.service.reindex_executor import ReindexExecutor, _ReindexCounters

    class FakeVikingFS:
        async def tree(
            self,
            uri,
            output="original",
            show_all_hidden=True,
            node_limit=1000,
            level_limit=None,
            ctx=None,
        ):
            return [
                {"uri": "viking://user/default/memories", "isDir": True},
                {"uri": "viking://user/default/memories/preferences", "isDir": True},
                {"uri": "viking://user/default/skills", "isDir": True},
                {"uri": "viking://user/default/skills/my_skill", "isDir": True},
                {"uri": "viking://user/default/sessions", "isDir": True},
                {"uri": "viking://user/default/sessions/s1", "isDir": True},
                {"uri": "viking://user/default/resources", "isDir": True},
                {"uri": "viking://user/default/resources/doc.md", "isDir": False},
                {"uri": "viking://user/default/sessions/s1/messages.jsonl", "isDir": False},
                {"uri": "viking://user/default/profile.md", "isDir": False},
                {"uri": "viking://user/default/memories/preferences/theme.md", "isDir": False},
                {"uri": "viking://user/default/skills/my_skill/SKILL.md", "isDir": False},
            ]

    seen = {"memory": [], "skill": [], "resource_dirs": [], "resource_files": []}

    async def fake_reindex_memory(self, *, uri, mode, run):
        seen["memory"].append((uri, mode))

    async def fake_reindex_skill(self, *, uri, mode, run):
        seen["skill"].append((uri, mode))

    async def fake_reindex_resource_vectors_from_entries(
        self,
        *,
        root_uri,
        directories,
        files,
        counters,
        ctx,
    ):
        seen["resource_dirs"] = list(directories)
        seen["resource_files"] = list(files)

    monkeypatch.setattr("openviking.service.reindex_executor.get_viking_fs", lambda: FakeVikingFS())
    monkeypatch.setattr(ReindexExecutor, "_reindex_memory", fake_reindex_memory)
    monkeypatch.setattr(ReindexExecutor, "_reindex_skill", fake_reindex_skill)
    monkeypatch.setattr(
        ReindexExecutor,
        "_reindex_resource_vectors_from_entries",
        fake_reindex_resource_vectors_from_entries,
    )

    service = ReindexExecutor()
    counters = _ReindexCounters()
    ctx = RequestContext(
        user=UserIdentifier(account_id="test", user_id="alice"),
        role=Role.ROOT,
    )

    await service._reindex_user_namespace(
        uri="viking://user/",
        mode="vectors_only",
        run=_make_reindex_run(ctx, counters),
    )

    assert seen["memory"] == [("viking://user/default/memories", "vectors_only")]
    assert seen["skill"] == [("viking://user/default/skills/my_skill", "vectors_only")]
    assert "viking://user/default/resources" in seen["resource_dirs"]
    assert "viking://user/default/memories" not in seen["resource_dirs"]
    assert "viking://user/default/skills" not in seen["resource_dirs"]
    assert "viking://user/default/sessions" not in seen["resource_dirs"]
    assert seen["resource_files"] == [
        "viking://user/default/resources/doc.md",
        "viking://user/default/profile.md",
    ]


@pytest.mark.asyncio
async def test_reindex_global_namespace_partitions_user_and_resources(monkeypatch):
    from openviking.service.reindex_executor import ReindexExecutor, _ReindexCounters

    class FakeVikingFS:
        async def tree(
            self,
            uri,
            output="original",
            show_all_hidden=True,
            node_limit=1000,
            level_limit=None,
            ctx=None,
        ):
            return [
                {"uri": "viking://user", "isDir": True},
                {"uri": "viking://user/default", "isDir": True},
                {"uri": "viking://user/default/memories", "isDir": True},
                {"uri": "viking://user/default", "isDir": True},
                {"uri": "viking://user/default/skills", "isDir": True},
                {"uri": "viking://session", "isDir": True},
                {"uri": "viking://session/default", "isDir": True},
                {"uri": "viking://resources", "isDir": True},
                {"uri": "viking://session/default/archive.txt", "isDir": False},
                {"uri": "viking://resources/demo.txt", "isDir": False},
                {"uri": "viking://README.md", "isDir": False},
            ]

    seen = {"user": [], "resource_dirs": [], "resource_files": []}

    async def fake_reindex_user_namespace(self, *, uri, mode, run):
        seen["user"].append((uri, mode))

    async def fake_reindex_resource_vectors_from_entries(
        self,
        *,
        root_uri,
        directories,
        files,
        counters,
        ctx,
    ):
        seen["resource_dirs"] = list(directories)
        seen["resource_files"] = list(files)

    monkeypatch.setattr("openviking.service.reindex_executor.get_viking_fs", lambda: FakeVikingFS())
    monkeypatch.setattr(ReindexExecutor, "_reindex_user_namespace", fake_reindex_user_namespace)
    monkeypatch.setattr(
        ReindexExecutor,
        "_reindex_resource_vectors_from_entries",
        fake_reindex_resource_vectors_from_entries,
    )

    service = ReindexExecutor()
    counters = _ReindexCounters()
    ctx = RequestContext(
        user=UserIdentifier(account_id="test", user_id="alice"),
        role=Role.ROOT,
    )

    await service._reindex_global_namespace(
        uri="viking://",
        mode="vectors_only",
        run=_make_reindex_run(ctx, counters),
    )

    assert seen["user"] == [("viking://user/default", "vectors_only")]
    assert "viking://resources" in seen["resource_dirs"]
    assert "viking://" not in seen["resource_dirs"]
    assert "viking://user" not in seen["resource_dirs"]
    assert "viking://session" not in seen["resource_dirs"]
    assert "viking://session/default" not in seen["resource_dirs"]
    assert seen["resource_files"] == ["viking://resources/demo.txt"]


@pytest.mark.asyncio
async def test_reindex_skill_l2_falls_back_to_skill_content_when_abstract_missing(monkeypatch):
    from openviking.service.reindex_executor import ReindexExecutor, _ReindexCounters

    class FakeVikingFS:
        async def exists(self, uri, ctx=None):
            return True

    seen = {}

    async def fake_read_directory_abstract(self, uri, *, ctx):
        return "skill abstract"

    async def fake_read_directory_overview(self, uri, *, ctx):
        return ""

    async def fake_fetch_existing_record(self, *, uri, level, ctx):
        return None

    async def fake_safe_read_text(self, uri, *, ctx):
        return "# Skill Title\n\nDo things well."

    async def fake_upsert_context(self, **kwargs):
        seen[kwargs["uri"]] = kwargs

    async def fake_skill_meta(self, *, uri, abstract, ctx):
        return {"name": "my_skill", "description": abstract}

    monkeypatch.setattr("openviking.service.reindex_executor.get_viking_fs", lambda: FakeVikingFS())
    monkeypatch.setattr(ReindexExecutor, "_read_directory_abstract", fake_read_directory_abstract)
    monkeypatch.setattr(ReindexExecutor, "_read_directory_overview", fake_read_directory_overview)
    monkeypatch.setattr(ReindexExecutor, "_fetch_existing_record", fake_fetch_existing_record)
    monkeypatch.setattr(ReindexExecutor, "_safe_read_text", fake_safe_read_text)
    monkeypatch.setattr(ReindexExecutor, "_skill_meta", fake_skill_meta)
    monkeypatch.setattr(ReindexExecutor, "_upsert_context", fake_upsert_context)

    service = ReindexExecutor()
    counters = _ReindexCounters()
    ctx = RequestContext(
        user=UserIdentifier(account_id="test", user_id="alice"),
        role=Role.ROOT,
    )

    await service._reindex_skill_vectors(
        uri="viking://user/skills/my_skill",
        counters=counters,
        ctx=ctx,
    )

    assert seen["viking://user/skills/my_skill/SKILL.md"]["abstract"] == "skill abstract"


@pytest.mark.asyncio
async def test_openviking_service_reindex_uses_default_root_context(monkeypatch):
    from openviking.service.core import OpenVikingService

    seen = {}

    class FakeExecutor:
        async def execute(self, *, uri, mode, wait, ctx, dry_run=False):
            seen["uri"] = uri
            seen["mode"] = mode
            seen["wait"] = wait
            seen["ctx"] = ctx
            return {"status": "completed", "uri": uri}

    import sys

    monkeypatch.setattr(
        sys.modules["openviking.service.reindex_executor"],
        "get_reindex_executor",
        lambda: FakeExecutor(),
    )

    service = OpenVikingService.__new__(OpenVikingService)
    service._initialized = True
    service._user = UserIdentifier(account_id="acct", user_id="alice")

    result = await OpenVikingService.reindex(
        service,
        uri="viking://resources/demo",
        mode="vectors_only",
        wait=True,
    )

    assert result == {"status": "completed", "uri": "viking://resources/demo"}
    assert seen["ctx"].role == Role.ROOT
    assert seen["ctx"].user.account_id == "acct"
    assert seen["ctx"].user.user_id == "alice"


@pytest.mark.asyncio
async def test_openviking_service_reindex_canonicalizes_user_shorthand(monkeypatch):
    from openviking.service.core import OpenVikingService

    seen = {}

    class FakeExecutor:
        async def execute(self, *, uri, mode, wait, ctx, dry_run=False):
            seen["uri"] = uri
            seen["ctx"] = ctx
            return {"status": "completed", "uri": uri}

    import openviking.service.reindex_executor as reindex_executor

    monkeypatch.setattr(
        reindex_executor,
        "get_reindex_executor",
        lambda: FakeExecutor(),
    )

    service = OpenVikingService.__new__(OpenVikingService)
    service._initialized = True
    service._user = UserIdentifier(account_id="acct", user_id="alice")
    ctx = RequestContext(
        user=UserIdentifier(account_id="acct", user_id="alice"),
        role=Role.ADMIN,
    )

    result = await OpenVikingService.reindex(
        service,
        uri="viking://user/memories",
        mode="prune_orphans",
        wait=True,
        dry_run=True,
        ctx=ctx,
    )

    assert result == {"status": "completed", "uri": "viking://user/alice/memories"}
    assert seen["uri"] == "viking://user/alice/memories"
    assert seen["ctx"] is ctx
