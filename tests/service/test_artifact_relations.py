import asyncio
import hashlib
from contextlib import asynccontextmanager

import pytest
from pydantic import ValidationError

from openviking.server.identity import RequestContext, Role
from openviking.server.routers.artifact_relations import GetArtifactRelationsRequest, router
from openviking.service.artifact_relations import ArtifactRelationsService
from openviking.service.compile_targets import CompileVersions
from openviking_cli.session.user_id import UserIdentifier


class FakeStore:
    def __init__(self, to: str, head: str):
        self.to = to
        self.record = {"to": to, "status": "active", "wiki_head": head}
        self.data = {}
        self.locked_paths = []

    def _base(self, account_id: str, to: str) -> str:
        key = hashlib.sha256(to.encode()).hexdigest()
        return f"/local/{account_id}/_system/compile/target_folders/{key}"

    def artifact_relations_path(self, account_id: str, to: str) -> str:
        return f"{self._base(account_id, to)}/artifact_relations.json"

    def relation_sources_path(self, account_id: str, to: str) -> str:
        return f"{self._base(account_id, to)}/relation_sources.json"

    def artifact_relations_state_path(self, account_id: str, to: str) -> str:
        return f"{self._base(account_id, to)}/artifact_relations_state.json"

    async def get(self, account_id: str, to: str):
        return dict(self.record) if to == self.to else None

    async def get_by_to(self, account_id: str, to: str):
        return await self.get(account_id, to)

    async def read_json(self, path: str):
        return self.data.get(path)

    async def write_json(self, path: str, value, lease=None):
        self.data[path] = value

    @asynccontextmanager
    async def edit(self, account_id: str, to: str):
        assert to == self.to
        yield self.record

    async def list_summaries(self, account_id: str):
        return [dict(self.record)]

    @asynccontextmanager
    async def locked(self, path: str):
        self.locked_paths.append(path)
        yield {"lease_ref": "lease"}


class FakeFS:
    def __init__(self, snapshots, diffs):
        self.snapshots = snapshots
        self.diffs = diffs
        self.visibility = {}
        self.commits = []

    async def diff_paths(self, *, dirs, base, head, ctx):
        return list(self.diffs[(base, head)])

    async def show_blob_raw(self, snapshot, *, path, ctx):
        content = self.snapshots[snapshot][path]
        return {
            "oid": hashlib.sha256(content).hexdigest(),
            "bytes": content,
        }

    def authorize_read(self, ctx, uris):
        return ctx

    async def _ensure_access(self, uri, ctx):
        return None

    async def _can_access_many(self, uris, ctx):
        return {uri: self.visibility.get(uri, True) for uri in uris}

    async def commit(self, *, message, paths, ctx, **kwargs):
        self.commits.append({"message": message, "paths": paths})
        return {"commit_oid": "committed-snapshot"}


def _ctx():
    return RequestContext(
        user=UserIdentifier(account_id="acct", user_id="alice"),
        role=Role.USER,
    )


def _edge(graph, relation_type, source, target):
    return next(
        (
            edge
            for edge in graph["Edges"]
            if edge["RelationType"] == relation_type
            and edge["SourceURI"] == source
            and edge["TargetURI"] == target
        ),
        None,
    )


@pytest.mark.asyncio
async def test_initial_build_publishes_complete_graph_and_sources():
    to = "viking://resources/wiki"
    index = f"{to}/index.md"
    page = f"{to}/guide/page.md"
    future = f"{to}/later.md"
    snapshots = {
        "s1": {
            index: b"[Guide](guide/page.md#intro) and [Later](later.md)",
            page: b"[Home](../index.md)",
            f"{to}/asset.txt": b"asset",
        }
    }
    changes = [{"uri": uri, "change_type": "added"} for uri in snapshots["s1"]]
    store = FakeStore(to, "s1")
    fs = FakeFS(snapshots, {(None, "s1"): changes})
    service = ArtifactRelationsService(store, fs)

    graph = await service.build("acct", to)

    assert graph["To"] == to
    assert graph["SnapshotID"] == "s1"
    assert {node["URI"] for node in graph["Nodes"]} == {
        to,
        index,
        f"{to}/guide",
        page,
        f"{to}/asset.txt",
    }
    reference = _edge(graph, "references", index, page)
    assert reference is not None
    assert reference["Anchors"] == ["Guide"]
    assert _edge(graph, "references", index, future) is None
    assert _edge(graph, "contains", to, f"{to}/guide") is not None
    sources = store.data[store.relation_sources_path("acct", to)]
    assert sources["snapshot_id"] == "s1"
    assert sources["sources"][index]["links"][1]["target_uri"] == future
    assert store.data[store.artifact_relations_state_path("acct", to)] == {"snapshot_id": "s1"}


@pytest.mark.asyncio
async def test_incremental_build_reuses_moved_source_and_resolves_new_target():
    to = "viking://resources/wiki"
    index = f"{to}/index.md"
    old_page = f"{to}/guide/page.md"
    new_page = f"{to}/page.md"
    future = f"{to}/later.md"
    page_content = b"[Home](../index.md)"
    snapshots = {
        "s1": {
            index: b"[Later](later.md)",
            old_page: page_content,
        },
        "s2": {
            index: b"[Later](later.md)",
            new_page: page_content,
            future: b"# Later",
        },
    }
    diffs = {
        (None, "s1"): [
            {"uri": index, "change_type": "added"},
            {"uri": old_page, "change_type": "added"},
        ],
        ("s1", "s2"): [
            {"uri": old_page, "change_type": "deleted"},
            {"uri": new_page, "change_type": "added"},
            {"uri": future, "change_type": "added"},
        ],
    }
    store = FakeStore(to, "s1")
    fs = FakeFS(snapshots, diffs)
    service = ArtifactRelationsService(store, fs)
    await service.build("acct", to)
    store.record["wiki_head"] = "s2"

    graph = await service.build("acct", to)

    assert old_page not in {node["URI"] for node in graph["Nodes"]}
    assert new_page in {node["URI"] for node in graph["Nodes"]}
    assert _edge(graph, "references", index, future) is not None
    assert _edge(graph, "references", new_page, index) is None
    sources = store.data[store.relation_sources_path("acct", to)]["sources"]
    assert sources[new_page]["content_id"] == hashlib.sha256(page_content).hexdigest()
    assert sources[new_page]["links"][0]["target_uri"] is None


@pytest.mark.asyncio
async def test_query_filters_nodes_and_requires_both_edge_ends_visible():
    to = "viking://resources/wiki"
    index = f"{to}/index.md"
    page = f"{to}/page.md"
    snapshots = {"s1": {index: b"[Page](page.md)", page: b"# Page"}}
    changes = [
        {"uri": index, "change_type": "added"},
        {"uri": page, "change_type": "added"},
    ]
    store = FakeStore(to, "s1")
    fs = FakeFS(snapshots, {(None, "s1"): changes})
    service = ArtifactRelationsService(store, fs)
    await service.build("acct", to)
    fs.visibility[page] = False

    graph = await service.get(to, _ctx())

    assert {node["URI"] for node in graph["Nodes"]} == {to, index}
    assert not any(page in (edge["SourceURI"], edge["TargetURI"]) for edge in graph["Edges"])


@pytest.mark.asyncio
async def test_stale_query_timeout_returns_old_graph_and_keeps_build_running(monkeypatch):
    to = "viking://resources/wiki"
    store = FakeStore(to, "s2")
    old_graph = {
        "To": to,
        "SnapshotID": "s1",
        "GeneratedAt": "old",
        "Nodes": [{"URI": to, "Name": "wiki", "NodeType": "directory"}],
        "Edges": [],
    }
    store.data[store.artifact_relations_path("acct", to)] = old_graph
    store.data[store.artifact_relations_state_path("acct", to)] = {"snapshot_id": "s1"}
    fs = FakeFS({}, {})
    service = ArtifactRelationsService(store, fs)
    completed = asyncio.Event()

    async def slow_build(account_id, target, *, extracted=None):
        try:
            await asyncio.sleep(0.05)
            completed.set()
            return {**old_graph, "SnapshotID": "s2"}
        except asyncio.CancelledError:
            pytest.fail("timed-out query cancelled the shared build")

    monkeypatch.setattr(service, "build", slow_build)
    monkeypatch.setattr("openviking.service.artifact_relations.QUERY_WAIT_SECONDS", 0.001)

    graph = await service.get(to, _ctx())

    assert graph["SnapshotID"] == "s1"
    await asyncio.wait_for(completed.wait(), timeout=0.2)


@pytest.mark.asyncio
async def test_stale_query_build_failure_returns_old_graph(monkeypatch):
    to = "viking://resources/wiki"
    store = FakeStore(to, "s2")
    old_graph = {
        "To": to,
        "SnapshotID": "s1",
        "GeneratedAt": "old",
        "Nodes": [{"URI": to, "Name": "wiki", "NodeType": "directory"}],
        "Edges": [],
    }
    store.data[store.artifact_relations_path("acct", to)] = old_graph
    store.data[store.artifact_relations_state_path("acct", to)] = {"snapshot_id": "s1"}
    service = ArtifactRelationsService(store, FakeFS({}, {}))

    async def failed_build(account_id, target, *, extracted=None):
        raise RuntimeError("build failed")

    monkeypatch.setattr(service, "build", failed_build)

    graph = await service.get(to, _ctx())

    assert graph["SnapshotID"] == "s1"


@pytest.mark.asyncio
async def test_ordinary_operation_advances_wiki_head_without_building_graph():
    to = "viking://resources/wiki"
    store = FakeStore(to, "s1")
    fs = FakeFS({}, {})
    versions = CompileVersions(store, fs)

    await versions.advance_wiki_heads([f"{to}/page.md"], _ctx(), "rm")

    assert store.record["wiki_head"] == "committed-snapshot"
    assert fs.commits == [{"message": f"rm {to}", "paths": [to]}]
    assert store.artifact_relations_state_path("acct", to) not in store.data


def test_artifact_relations_api_contract_uses_fixed_path_and_pascal_case_to():
    assert any(route.path == "/api/openviking/GetArtifactRelations" for route in router.routes)
    assert GetArtifactRelationsRequest.model_validate({"To": "viking://resources/wiki"}).to == (
        "viking://resources/wiki"
    )
    with pytest.raises(ValidationError):
        GetArtifactRelationsRequest.model_validate({})
