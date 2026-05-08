"""Tests for reindex admin endpoint and executor behavior."""

import httpx
import pytest

from openviking.server.identity import RequestContext, Role
from openviking_cli.exceptions import OpenVikingError
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


async def test_reindex_requires_admin_role(admin_client: httpx.AsyncClient):
    resp = await admin_client.post(
        "/api/v1/content/reindex",
        json={"uri": "viking://resources/demo", "mode": "vectors_only"},
    )
    assert resp.status_code == 401


async def test_reindex_rejects_unsupported_uri(admin_client: httpx.AsyncClient):
    resp = await admin_client.post(
        "/api/v1/content/reindex",
        json={"uri": "viking://unknown/demo", "mode": "vectors_only"},
        headers=ROOT_ACCOUNT_HEADERS,
    )
    assert resp.status_code == 400
    body = resp.json()
    assert body["status"] == "error"
    assert body["error"]["code"] == "UNSUPPORTED_URI"


async def test_reindex_rejects_session_uri(admin_client: httpx.AsyncClient):
    resp = await admin_client.post(
        "/api/v1/content/reindex",
        json={"uri": "viking://session/test/demo", "mode": "vectors_only"},
        headers=ROOT_ACCOUNT_HEADERS,
    )
    assert resp.status_code == 400
    body = resp.json()
    assert body["status"] == "error"
    assert body["error"]["code"] == "UNSUPPORTED_URI"


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
    assert resp.status_code == 400


async def test_reindex_root_requires_explicit_account(admin_client: httpx.AsyncClient):
    resp = await admin_client.post(
        "/api/v1/content/reindex",
        json={"uri": "viking://resources/demo", "mode": "vectors_only"},
        headers={"X-API-Key": ROOT_KEY},
    )
    assert resp.status_code == 400
    body = resp.json()
    assert body["status"] == "error"
    assert body["error"]["code"] == "INVALID_ARGUMENT"


@pytest.mark.asyncio
async def test_reindex_resource_vectors_only_wait_true(monkeypatch):
    from openviking.server.routers.content import ReindexRequest, reindex

    seen = {}

    class FakeService:
        async def reindex(self, *, uri, mode, wait, ctx):
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
        user=UserIdentifier(account_id="test", user_id="alice", agent_id="default"),
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
        async def reindex(self, *, uri, mode, wait, ctx):
            return {
                "task_id": "rbld_123",
                "status": "accepted",
                "uri": uri,
                "object_type": "resource",
                "mode": mode,
            }

    ctx = RequestContext(
        user=UserIdentifier(account_id="test", user_id="alice", agent_id="default"),
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
async def test_reindex_memory_supports_semantic_and_vectors(monkeypatch):
    from openviking.service.reindex_executor import ReindexExecutor

    service = ReindexExecutor()
    service._validate_mode("memory", "semantic_and_vectors")


@pytest.mark.asyncio
async def test_reindex_memory_semantic_and_vectors_rebuilds_full_subtree(monkeypatch):
    from openviking.service.reindex_executor import ReindexExecutor, _ReindexCounters

    seen = {"semantic": [], "vectors": []}

    async def fake_run_semantic_processor(self, *, uri, context_type, ctx):
        seen["semantic"].append((uri, context_type))

    async def fake_reindex_memory_vectors(self, *, uri, counters, ctx):
        seen["vectors"].append(uri)

    monkeypatch.setattr(ReindexExecutor, "_run_semantic_processor", fake_run_semantic_processor)
    monkeypatch.setattr(ReindexExecutor, "_reindex_memory_vectors", fake_reindex_memory_vectors)

    service = ReindexExecutor()
    counters = _ReindexCounters()
    ctx = RequestContext(
        user=UserIdentifier(account_id="test", user_id="alice", agent_id="default"),
        role=Role.ROOT,
    )

    await service._reindex_memory(
        uri="viking://user/default/memories",
        mode="semantic_and_vectors",
        counters=counters,
        ctx=ctx,
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
    assert service._infer_target_type("viking://agent/default/skills") == "skill_namespace"
    assert service._infer_target_type("viking://agent/default/skills/demo") == "skill"
    with pytest.raises(OpenVikingError, match="Unsupported reindex URI"):
        service._infer_target_type("viking://agent/default/skills/demo/SKILL.md")


@pytest.mark.asyncio
async def test_reindex_executor_infers_user_namespace_root():
    from openviking.service.reindex_executor import ReindexExecutor

    service = ReindexExecutor()

    assert service._infer_target_type("viking://user/") == "user_namespace"
    assert service._infer_target_type("viking://user/default") == "user_namespace"


@pytest.mark.asyncio
async def test_reindex_executor_infers_agent_namespace_root():
    from openviking.service.reindex_executor import ReindexExecutor

    service = ReindexExecutor()

    assert service._infer_target_type("viking://agent/") == "agent_namespace"
    assert service._infer_target_type("viking://agent/default") == "agent_namespace"


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
    assert not classify_uri("viking://agent/default/resources/memories-report.md").is_memory


@pytest.mark.asyncio
async def test_reindex_executor_does_not_treat_skill_subdirectories_as_skill_roots():
    from openviking.core.namespace import classify_uri

    assert classify_uri("viking://agent/default/skills/my_skill").is_skill_root
    assert not classify_uri("viking://agent/default/skills/my_skill/assets").is_skill_root
    assert not classify_uri("viking://agent/default/resources/skills-report.md").is_skill


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

    async def fake_reindex_memory(self, *, uri, mode, counters, ctx):
        seen["memory_modes"].append((uri, mode))

    async def fake_run_semantic_processor(self, *, uri, context_type, ctx):
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
        user=UserIdentifier(account_id="test", user_id="alice", agent_id="default"),
        role=Role.ROOT,
    )

    await service._reindex_user_namespace(
        uri="viking://user/default",
        mode="semantic_and_vectors",
        counters=counters,
        ctx=ctx,
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

    async def fake_reindex_memory(self, *, uri, mode, counters, ctx):
        seen["memory_modes"].append((uri, mode))

    async def fake_run_semantic_processor(self, *, uri, context_type, ctx):
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
        user=UserIdentifier(account_id="test", user_id="alice", agent_id="default"),
        role=Role.ROOT,
    )

    await service._reindex_user_namespace(
        uri="viking://user/default",
        mode="semantic_and_vectors",
        counters=counters,
        ctx=ctx,
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

    async def fake_run_semantic_processor(self, *, uri, context_type, ctx):
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
        user=UserIdentifier(account_id="test", user_id="alice", agent_id="default"),
        role=Role.ROOT,
    )

    await service._reindex_user_namespace(
        uri="viking://user/default",
        mode="semantic_and_vectors",
        counters=counters,
        ctx=ctx,
    )

    assert seen["semantic_calls"] == [("viking://user/default/resources", "resource")]
    assert seen["resource_files"] == ["viking://user/default/resources/doc.md"]
    assert counters.unsupported_records == 1
    assert "viking://user/default/profile.md" in counters.warnings[0]


@pytest.mark.asyncio
async def test_reindex_agent_namespace_semantic_and_vectors_promotes_memory_and_skill_modes(
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
                {"uri": "viking://agent/default/memories", "isDir": True},
                {"uri": "viking://agent/default/skills/demo", "isDir": True},
                {"uri": "viking://agent/default/resources", "isDir": True},
            ]

    seen = {"memory_modes": [], "skill_modes": [], "semantic_calls": [], "resource_calls": []}

    async def fake_reindex_memory(self, *, uri, mode, counters, ctx):
        seen["memory_modes"].append((uri, mode))

    async def fake_reindex_skill(self, *, uri, mode, counters, ctx):
        seen["skill_modes"].append((uri, mode))

    async def fake_run_semantic_processor(self, *, uri, context_type, ctx):
        seen["semantic_calls"].append((uri, context_type))

    async def fake_reindex_resource_vectors_from_entries(
        self, *, root_uri, directories, files, counters, ctx
    ):
        seen["resource_calls"].append((root_uri, list(directories), list(files)))

    monkeypatch.setattr("openviking.service.reindex_executor.get_viking_fs", lambda: FakeVikingFS())
    monkeypatch.setattr(ReindexExecutor, "_reindex_memory", fake_reindex_memory)
    monkeypatch.setattr(ReindexExecutor, "_reindex_skill", fake_reindex_skill)
    monkeypatch.setattr(ReindexExecutor, "_run_semantic_processor", fake_run_semantic_processor)
    monkeypatch.setattr(
        ReindexExecutor,
        "_reindex_resource_vectors_from_entries",
        fake_reindex_resource_vectors_from_entries,
    )

    service = ReindexExecutor()
    counters = _ReindexCounters()
    ctx = RequestContext(
        user=UserIdentifier(account_id="test", user_id="alice", agent_id="default"),
        role=Role.ROOT,
    )

    await service._reindex_agent_namespace(
        uri="viking://agent/default",
        mode="semantic_and_vectors",
        counters=counters,
        ctx=ctx,
    )

    assert seen["memory_modes"] == [("viking://agent/default/memories", "semantic_and_vectors")]
    assert seen["skill_modes"] == [("viking://agent/default/skills/demo", "semantic_and_vectors")]
    assert seen["semantic_calls"] == [("viking://agent/default/resources", "resource")]
    assert seen["resource_calls"]


@pytest.mark.asyncio
async def test_reindex_agent_namespace_semantic_and_vectors_does_not_reprocess_memory_as_resource(
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
                {"uri": "viking://agent/default/memories", "isDir": True},
                {"uri": "viking://agent/default/memories/cases", "isDir": True},
                {"uri": "viking://agent/default/skills/demo", "isDir": True},
                {"uri": "viking://agent/default/resources", "isDir": True},
            ]

    seen = {"memory_modes": [], "skill_modes": [], "semantic_calls": []}

    async def fake_reindex_memory(self, *, uri, mode, counters, ctx):
        seen["memory_modes"].append((uri, mode))

    async def fake_reindex_skill(self, *, uri, mode, counters, ctx):
        seen["skill_modes"].append((uri, mode))

    async def fake_run_semantic_processor(self, *, uri, context_type, ctx):
        seen["semantic_calls"].append((uri, context_type))

    async def fake_reindex_resource_vectors_from_entries(
        self, *, root_uri, directories, files, counters, ctx
    ):
        return None

    monkeypatch.setattr("openviking.service.reindex_executor.get_viking_fs", lambda: FakeVikingFS())
    monkeypatch.setattr(ReindexExecutor, "_reindex_memory", fake_reindex_memory)
    monkeypatch.setattr(ReindexExecutor, "_reindex_skill", fake_reindex_skill)
    monkeypatch.setattr(ReindexExecutor, "_run_semantic_processor", fake_run_semantic_processor)
    monkeypatch.setattr(
        ReindexExecutor,
        "_reindex_resource_vectors_from_entries",
        fake_reindex_resource_vectors_from_entries,
    )

    service = ReindexExecutor()
    counters = _ReindexCounters()
    ctx = RequestContext(
        user=UserIdentifier(account_id="test", user_id="alice", agent_id="default"),
        role=Role.ROOT,
    )

    await service._reindex_agent_namespace(
        uri="viking://agent/default",
        mode="semantic_and_vectors",
        counters=counters,
        ctx=ctx,
    )

    assert seen["memory_modes"] == [("viking://agent/default/memories", "semantic_and_vectors")]
    assert seen["skill_modes"] == [("viking://agent/default/skills/demo", "semantic_and_vectors")]
    assert seen["semantic_calls"] == [("viking://agent/default/resources", "resource")]


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
                {"uri": "viking://agent/default/skills/my_skill", "isDir": True},
                {"uri": "viking://agent/default/skills/my_skill/assets", "isDir": True},
                {"uri": "viking://agent/default/skills/my_skill/SKILL.md", "isDir": False},
            ]

    seen = []

    async def fake_reindex_skill(self, *, uri, mode, counters, ctx):
        seen.append((uri, mode))

    monkeypatch.setattr("openviking.service.reindex_executor.get_viking_fs", lambda: FakeVikingFS())
    monkeypatch.setattr(ReindexExecutor, "_reindex_skill", fake_reindex_skill)

    service = ReindexExecutor()
    counters = _ReindexCounters()
    ctx = RequestContext(
        user=UserIdentifier(account_id="test", user_id="alice", agent_id="default"),
        role=Role.ROOT,
    )

    await service._reindex_skill_namespace(
        uri="viking://agent/default/skills",
        mode="semantic_and_vectors",
        counters=counters,
        ctx=ctx,
    )

    assert seen == [("viking://agent/default/skills/my_skill", "semantic_and_vectors")]


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
                {"uri": "viking://agent/default", "isDir": True},
                {"uri": "viking://resources", "isDir": True},
            ]

    seen = {"user_modes": [], "agent_modes": [], "semantic_calls": [], "resource_calls": []}

    async def fake_reindex_user_namespace(self, *, uri, mode, counters, ctx):
        seen["user_modes"].append((uri, mode))

    async def fake_reindex_agent_namespace(self, *, uri, mode, counters, ctx):
        seen["agent_modes"].append((uri, mode))

    async def fake_run_semantic_processor(self, *, uri, context_type, ctx):
        seen["semantic_calls"].append((uri, context_type))

    async def fake_reindex_resource_vectors_from_entries(
        self, *, root_uri, directories, files, counters, ctx
    ):
        seen["resource_calls"].append((root_uri, list(directories), list(files)))

    monkeypatch.setattr("openviking.service.reindex_executor.get_viking_fs", lambda: FakeVikingFS())
    monkeypatch.setattr(ReindexExecutor, "_reindex_user_namespace", fake_reindex_user_namespace)
    monkeypatch.setattr(ReindexExecutor, "_reindex_agent_namespace", fake_reindex_agent_namespace)
    monkeypatch.setattr(ReindexExecutor, "_run_semantic_processor", fake_run_semantic_processor)
    monkeypatch.setattr(
        ReindexExecutor,
        "_reindex_resource_vectors_from_entries",
        fake_reindex_resource_vectors_from_entries,
    )

    service = ReindexExecutor()
    counters = _ReindexCounters()
    ctx = RequestContext(
        user=UserIdentifier(account_id="test", user_id="alice", agent_id="default"),
        role=Role.ROOT,
    )

    await service._reindex_global_namespace(
        uri="viking://",
        mode="semantic_and_vectors",
        counters=counters,
        ctx=ctx,
    )

    assert seen["user_modes"] == [("viking://user/default", "semantic_and_vectors")]
    assert seen["agent_modes"] == [("viking://agent/default", "semantic_and_vectors")]
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
        user=UserIdentifier(account_id="test", user_id="alice", agent_id="default"),
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
        user=UserIdentifier(account_id="test", user_id="alice", agent_id="default"),
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
        async def on_dequeue(self, payload):
            seen["payload"] = payload

    monkeypatch.setattr(
        "openviking.service.reindex_executor.SemanticProcessor",
        FakeSemanticProcessor,
    )

    service = ReindexExecutor()
    ctx = RequestContext(
        user=UserIdentifier(account_id="test", user_id="alice", agent_id="default"),
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
        user=UserIdentifier(account_id="test", user_id="alice", agent_id="default"),
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
        user=UserIdentifier(account_id="test", user_id="alice", agent_id="default"),
        role=Role.ROOT,
    )

    vector_text = await service._best_resource_file_vector_text(
        "viking://resources/demo/image.png",
        "",
        ctx=ctx,
    )

    assert vector_text == "existing image summary"


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
        user=UserIdentifier(account_id="test", user_id="alice", agent_id="default"),
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
        user=UserIdentifier(account_id="test", user_id="alice", agent_id="default"),
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
    from openviking.service.reindex_executor import ReindexExecutor, _ReindexCounters

    class FakeVikingFS:
        async def exists(self, uri, ctx=None):
            return True

        async def stat(self, uri, ctx=None):
            return {"isDir": False}

    seen = {}

    async def fake_safe_read_text(self, uri, *, ctx):
        return "memory body text"

    async def fake_fetch_existing_record(self, *, uri, level, ctx):
        return None

    async def fake_best_file_summary(self, uri, *, ctx):
        return ""

    async def fake_upsert_context(self, **kwargs):
        seen[kwargs["uri"]] = kwargs

    monkeypatch.setattr("openviking.service.reindex_executor.get_viking_fs", lambda: FakeVikingFS())
    monkeypatch.setattr(ReindexExecutor, "_safe_read_text", fake_safe_read_text)
    monkeypatch.setattr(ReindexExecutor, "_fetch_existing_record", fake_fetch_existing_record)
    monkeypatch.setattr(ReindexExecutor, "_best_file_summary", fake_best_file_summary)
    monkeypatch.setattr(ReindexExecutor, "_chunk_memory_body", lambda self, uri, body: [])
    monkeypatch.setattr(ReindexExecutor, "_upsert_context", fake_upsert_context)

    service = ReindexExecutor()
    counters = _ReindexCounters()
    ctx = RequestContext(
        user=UserIdentifier(account_id="test", user_id="alice", agent_id="default"),
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
    from openviking.service.reindex_executor import ReindexExecutor, _ReindexCounters

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

    async def fake_safe_read_text(self, uri, *, ctx):
        return raw_body

    async def fake_fetch_existing_record(self, *, uri, level, ctx):
        return None

    async def fake_best_file_summary(self, uri, *, ctx):
        return ""

    async def fake_upsert_context(self, **kwargs):
        seen[kwargs["uri"]] = kwargs

    monkeypatch.setattr("openviking.service.reindex_executor.get_viking_fs", lambda: FakeVikingFS())
    monkeypatch.setattr(ReindexExecutor, "_safe_read_text", fake_safe_read_text)
    monkeypatch.setattr(ReindexExecutor, "_fetch_existing_record", fake_fetch_existing_record)
    monkeypatch.setattr(ReindexExecutor, "_best_file_summary", fake_best_file_summary)
    monkeypatch.setattr(ReindexExecutor, "_chunk_memory_body", lambda self, uri, body: [])
    monkeypatch.setattr(ReindexExecutor, "_upsert_context", fake_upsert_context)

    service = ReindexExecutor()
    counters = _ReindexCounters()
    ctx = RequestContext(
        user=UserIdentifier(account_id="test", user_id="alice", agent_id="default"),
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

    async def fake_safe_read_text(self, uri, *, ctx):
        return "likes spicy food"

    async def fake_fetch_existing_record(self, *, uri, level, ctx):
        return None

    async def fake_best_file_summary(self, uri, *, ctx):
        return ""

    async def fake_upsert_context(self, **kwargs):
        seen[kwargs["uri"]] = kwargs

    monkeypatch.setattr("openviking.service.reindex_executor.get_viking_fs", lambda: FakeVikingFS())
    monkeypatch.setattr(ReindexExecutor, "_safe_read_text", fake_safe_read_text)
    monkeypatch.setattr(ReindexExecutor, "_fetch_existing_record", fake_fetch_existing_record)
    monkeypatch.setattr(ReindexExecutor, "_best_file_summary", fake_best_file_summary)
    monkeypatch.setattr(ReindexExecutor, "_chunk_memory_body", lambda self, uri, body: [])
    monkeypatch.setattr(ReindexExecutor, "_upsert_context", fake_upsert_context)

    service = ReindexExecutor()
    counters = _ReindexCounters()
    ctx = RequestContext(
        user=UserIdentifier(account_id="test", user_id="alice", agent_id="default"),
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

    async def fake_safe_read_text(self, uri, *, ctx):
        return "likes spicy food"

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
    monkeypatch.setattr(ReindexExecutor, "_safe_read_text", fake_safe_read_text)
    monkeypatch.setattr(ReindexExecutor, "_fetch_existing_record", fake_fetch_existing_record)
    monkeypatch.setattr(ReindexExecutor, "_best_file_summary", fake_best_file_summary)
    monkeypatch.setattr(ReindexExecutor, "_chunk_memory_body", lambda self, uri, body: [])
    monkeypatch.setattr(ReindexExecutor, "_upsert_context", fake_upsert_context)

    service = ReindexExecutor()
    counters = _ReindexCounters()
    ctx = RequestContext(
        user=UserIdentifier(account_id="test", user_id="alice", agent_id="default"),
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
async def test_reindex_user_namespace_partitions_memory_and_resource(monkeypatch):
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
                {"uri": "viking://user/default/resources/doc.md", "isDir": False},
                {"uri": "viking://user/default/profile.md", "isDir": False},
                {"uri": "viking://user/default/memories/preferences/theme.md", "isDir": False},
            ]

    seen = {"memory": [], "resource_dirs": [], "resource_files": []}

    async def fake_reindex_memory(self, *, uri, mode, counters, ctx):
        seen["memory"].append((uri, mode))

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
    monkeypatch.setattr(
        ReindexExecutor,
        "_reindex_resource_vectors_from_entries",
        fake_reindex_resource_vectors_from_entries,
    )

    service = ReindexExecutor()
    counters = _ReindexCounters()
    ctx = RequestContext(
        user=UserIdentifier(account_id="test", user_id="alice", agent_id="default"),
        role=Role.ROOT,
    )

    await service._reindex_user_namespace(
        uri="viking://user/",
        mode="vectors_only",
        counters=counters,
        ctx=ctx,
    )

    assert seen["memory"] == [("viking://user/default/memories", "vectors_only")]
    assert "viking://user/default/resources" in seen["resource_dirs"]
    assert "viking://user/default/memories" not in seen["resource_dirs"]
    assert seen["resource_files"] == [
        "viking://user/default/resources/doc.md",
        "viking://user/default/profile.md",
    ]


@pytest.mark.asyncio
async def test_reindex_agent_namespace_partitions_memory_skill_and_resource(monkeypatch):
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
                {"uri": "viking://agent/default/memories", "isDir": True},
                {"uri": "viking://agent/default/memories/cases", "isDir": True},
                {"uri": "viking://agent/default/skills", "isDir": True},
                {"uri": "viking://agent/default/skills/my_skill", "isDir": True},
                {"uri": "viking://agent/default/resources", "isDir": True},
                {"uri": "viking://agent/default/resources/doc.md", "isDir": False},
                {"uri": "viking://agent/default/notes.md", "isDir": False},
                {"uri": "viking://agent/default/memories/cases/a.md", "isDir": False},
                {"uri": "viking://agent/default/skills/my_skill/SKILL.md", "isDir": False},
            ]

    seen = {"memory": [], "skill": [], "resource_dirs": [], "resource_files": []}

    async def fake_reindex_memory(self, *, uri, mode, counters, ctx):
        seen["memory"].append((uri, mode))

    async def fake_reindex_skill(self, *, uri, mode, counters, ctx):
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
        user=UserIdentifier(account_id="test", user_id="alice", agent_id="default"),
        role=Role.ROOT,
    )

    await service._reindex_agent_namespace(
        uri="viking://agent/",
        mode="vectors_only",
        counters=counters,
        ctx=ctx,
    )

    assert seen["memory"] == [("viking://agent/default/memories", "vectors_only")]
    assert seen["skill"] == [("viking://agent/default/skills/my_skill", "vectors_only")]
    assert "viking://agent/default/resources" in seen["resource_dirs"]
    assert "viking://agent/default/memories" not in seen["resource_dirs"]
    assert "viking://agent/default/skills" not in seen["resource_dirs"]
    assert seen["resource_files"] == [
        "viking://agent/default/resources/doc.md",
        "viking://agent/default/notes.md",
    ]


@pytest.mark.asyncio
async def test_reindex_global_namespace_partitions_user_agent_and_resources(monkeypatch):
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
                {"uri": "viking://agent", "isDir": True},
                {"uri": "viking://agent/default", "isDir": True},
                {"uri": "viking://agent/default/skills", "isDir": True},
                {"uri": "viking://session", "isDir": True},
                {"uri": "viking://session/default", "isDir": True},
                {"uri": "viking://resources", "isDir": True},
                {"uri": "viking://session/default/archive.txt", "isDir": False},
                {"uri": "viking://resources/demo.txt", "isDir": False},
                {"uri": "viking://README.md", "isDir": False},
            ]

    seen = {"user": [], "agent": [], "resource_dirs": [], "resource_files": []}

    async def fake_reindex_user_namespace(self, *, uri, mode, counters, ctx):
        seen["user"].append((uri, mode))

    async def fake_reindex_agent_namespace(self, *, uri, mode, counters, ctx):
        seen["agent"].append((uri, mode))

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
    monkeypatch.setattr(ReindexExecutor, "_reindex_agent_namespace", fake_reindex_agent_namespace)
    monkeypatch.setattr(
        ReindexExecutor,
        "_reindex_resource_vectors_from_entries",
        fake_reindex_resource_vectors_from_entries,
    )

    service = ReindexExecutor()
    counters = _ReindexCounters()
    ctx = RequestContext(
        user=UserIdentifier(account_id="test", user_id="alice", agent_id="default"),
        role=Role.ROOT,
    )

    await service._reindex_global_namespace(
        uri="viking://",
        mode="vectors_only",
        counters=counters,
        ctx=ctx,
    )

    assert seen["user"] == [("viking://user/default", "vectors_only")]
    assert seen["agent"] == [("viking://agent/default", "vectors_only")]
    assert "viking://resources" in seen["resource_dirs"]
    assert "viking://" not in seen["resource_dirs"]
    assert "viking://user" not in seen["resource_dirs"]
    assert "viking://agent" not in seen["resource_dirs"]
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
        user=UserIdentifier(account_id="test", user_id="alice", agent_id="default"),
        role=Role.ROOT,
    )

    await service._reindex_skill_vectors(
        uri="viking://agent/skills/my_skill",
        counters=counters,
        ctx=ctx,
    )

    assert seen["viking://agent/skills/my_skill/SKILL.md"]["abstract"] == "skill abstract"


@pytest.mark.asyncio
async def test_openviking_service_reindex_uses_default_root_context(monkeypatch):
    from openviking.service.core import OpenVikingService

    seen = {}

    class FakeExecutor:
        async def execute(self, *, uri, mode, wait, ctx):
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
    service._user = UserIdentifier(account_id="acct", user_id="alice", agent_id="assistant")

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
    assert seen["ctx"].user.agent_id == "assistant"
