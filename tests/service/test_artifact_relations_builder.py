import copy
from types import SimpleNamespace

import pytest

from openviking.service.artifact_relation_links import extract_relation_source
from openviking.service.artifact_relations_builder import (
    ArtifactRelationsBuilder,
    SnapshotEntry,
    assemble_relations,
)
from openviking.service.artifact_relations_models import ArtifactRelationsState, RelationSources

ROOT = "viking://resources/wiki"


class Snapshots:
    def __init__(self):
        self.snapshots = {}
        self.reads = []
        self._async_agfs = self

    def _uri_to_tree_path(self, uri, **kwargs):
        return uri.removeprefix("viking://")

    def _tree_path_to_uri(self, path):
        return "viking://" + path

    def save(self, snapshot, files, dirs=()):
        self.snapshots[snapshot] = (copy.deepcopy(files), set(dirs))

    async def run(self, method, **kwargs):
        if method == "git_snapshot_entries":
            files, dirs = self.snapshots[kwargs["snapshot"]]
            entries = [(ROOT.removeprefix("viking://"), "tree", True)]
            entries.extend((f"resources/wiki/{d}", "tree", True) for d in sorted(dirs))
            entries.extend(
                (f"resources/wiki/{path}", extract_relation_source(body).blob_oid, False)
                for path, body in sorted(files.items())
            )
            return entries
        assert method == "git_snapshot_blobs"
        blobs = {
            extract_relation_source(body).blob_oid: body
            for files, _ in self.snapshots.values()
            for body in files.values()
        }
        self.reads.extend(kwargs["oids"])
        return [blobs[oid] for oid in kwargs["oids"]]

    async def diff_paths(self, *, base, head, **kwargs):
        before = self.snapshots[base][0]
        after = self.snapshots[head][0]
        return [
            {"uri": f"{ROOT}/{path}", "change_type": "modified"}
            for path in before.keys() | after.keys()
            if before.get(path) != after.get(path)
        ]


def claim(snapshot, *, compile=False):
    return ArtifactRelationsState(
        status="building",
        phase="computing",
        target_snapshot_id=snapshot,
        build_id=f"build-{snapshot}",
        compile_operation_id="compile" if compile else None,
    )


def structure(graph):
    return graph.nodes, [edge.model_dump(by_alias=True) for edge in graph.edges]


@pytest.mark.asyncio
async def test_incremental_equals_full_after_broken_target_restore_and_source_move():
    fs = Snapshots()
    builder = ArtifactRelationsBuilder(fs)
    ctx = SimpleNamespace(account_id="account")
    fs.save("a", {"a.md": b"[b](b.md) [future](future.md)", "b.md": b"b"})
    sources, graph = await builder.build(ROOT, claim("a"), None, None, ctx)
    for snapshot, files, dirs in [
        ("b", {"a.md": b"[b](b.md) [future](future.md)"}, ()),
        ("c", {"a.md": b"[b](b.md) [future](future.md)", "future.md": b"new"}, ()),
        ("d", {"sub/a.md": b"[b](b.md) [future](../future.md)", "future.md": b"new"}, ("sub",)),
        ("e", {"a.md": b"[b](b.md) [future](future.md)", "b.md": b"b"}, ("empty",)),
    ]:
        fs.save(snapshot, files, dirs)
        sources, graph = await builder.build(ROOT, claim(snapshot), sources, graph, ctx)
        full_sources, full_graph = await builder.build(ROOT, claim(snapshot), None, None, ctx)
        assert sources.pages == full_sources.pages
        assert structure(graph) == structure(full_graph)
    assert f"{ROOT}/empty/" in graph.nodes


@pytest.mark.asyncio
async def test_unchanged_source_resolves_new_target_without_rereading_its_body():
    fs = Snapshots()
    fs.save("a", {"a.md": b"[future](future.md)"})
    builder = ArtifactRelationsBuilder(fs)
    ctx = SimpleNamespace(account_id="account")
    sources, graph = await builder.build(ROOT, claim("a"), None, None, ctx)
    fs.reads.clear()
    fs.save("b", {"a.md": b"[future](future.md)", "future.md": b"target"})
    _, graph = await builder.build(ROOT, claim("b"), sources, graph, ctx)
    assert fs.reads == [extract_relation_source(b"target").blob_oid]
    assert any(edge.type == "references" and edge.to == ROOT + "/future.md" for edge in graph.edges)


@pytest.mark.asyncio
async def test_compile_reuses_sources_and_ordinary_build_rejects_polluted_baseline():
    fs = Snapshots()
    fs.save("a", {"a.md": b"[missing](missing.md)"})
    builder = ArtifactRelationsBuilder(fs)
    ctx = SimpleNamespace(account_id="account")
    cached = RelationSources(
        parser_version="markdown-v1",
        pages={ROOT + "/a.md": extract_relation_source(b"[missing](missing.md)")},
    )
    sources, graph = await builder.build(ROOT, claim("a", compile=True), cached, None, ctx)
    assert not fs.reads
    sources.build_id = "polluted"
    await builder.build(ROOT, claim("a"), sources, graph, ctx)
    assert len(fs.reads) == 1


def test_builder_rejects_missing_parent_and_incomplete_page_coverage():
    sources = RelationSources(parser_version="markdown-v1")
    with pytest.raises(ValueError, match="cover"):
        assemble_relations(
            ROOT,
            [SnapshotEntry(ROOT, "tree", True), SnapshotEntry(ROOT + "/a.md", "blob", False)],
            sources,
            snapshot_id="a",
            build_id="b",
        )
    with pytest.raises(ValueError, match="parent"):
        assemble_relations(
            ROOT,
            [
                SnapshotEntry(ROOT, "tree", True),
                SnapshotEntry(ROOT + "/missing/file.txt", "blob", False),
            ],
            sources,
            snapshot_id="a",
            build_id="b",
        )
