import copy
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from openviking.service.artifact_relation_links import extract_relation_source
from openviking.service.artifact_relations_builder import (
    ArtifactRelationsBuilder,
    SnapshotEntry,
    assemble_relations,
)
from openviking.service.artifact_relations_models import (
    ArtifactRelations,
    ArtifactRelationsState,
    RelationEdge,
    RelationSource,
    RelationSources,
    can_recover_publication,
    can_use_incremental_sources,
)

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
@pytest.mark.parametrize(
    ("first_files", "first_dirs", "second_files", "second_dirs", "expected_target"),
    [
        (
            {"a.md": b"[b](b.md)", "b.md": b"target"},
            (),
            {"a.md": b"[b](b.md)"},
            ("b.md",),
            ROOT + "/b.md/",
        ),
        (
            {"a.md": b"[b](b.md)"},
            ("b.md",),
            {"a.md": b"[b](b.md)", "b.md": b"target"},
            (),
            ROOT + "/b.md",
        ),
    ],
    ids=["file-to-directory", "directory-to-file"],
)
async def test_incremental_equals_full_after_node_type_change(
    first_files, first_dirs, second_files, second_dirs, expected_target
):
    fs = Snapshots()
    builder = ArtifactRelationsBuilder(fs)
    ctx = SimpleNamespace(account_id="account")
    fs.save("a", first_files, first_dirs)
    sources, graph = await builder.build(ROOT, claim("a"), None, None, ctx)
    fs.save("b", second_files, second_dirs)

    sources, graph = await builder.build(ROOT, claim("b"), sources, graph, ctx)
    full_sources, full_graph = await builder.build(ROOT, claim("b"), None, None, ctx)

    assert sources.pages == full_sources.pages
    assert structure(graph) == structure(full_graph)
    assert any(
        edge.type == "references" and edge.from_uri == ROOT + "/a.md" and edge.to == expected_target
        for edge in graph.edges
    )


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


def model_graph(build_id="published", snapshot="snapshot-a"):
    return ArtifactRelations(
        build_id=build_id,
        target_snapshot_id=snapshot,
        generated_at=0,
        nodes=[ROOT + "/"],
        edges=[],
    )


@pytest.mark.parametrize("source_build", ["candidate", None])
def test_unpublished_sources_cannot_be_used_after_content_is_restored(source_build):
    sources = RelationSources(
        parser_version="markdown-v1",
        build_id=source_build,
        pages={ROOT + "/a.md": RelationSource(blob_oid="blob-b", links=["b.md"])},
    )
    assert not can_use_incremental_sources(sources, model_graph(), parser_version="markdown-v1")


def test_published_sources_require_the_same_parser_version():
    sources = RelationSources(parser_version="markdown-v1", build_id="published")
    assert can_use_incremental_sources(sources, model_graph(), parser_version="markdown-v1")
    assert not can_use_incremental_sources(sources, model_graph(), parser_version="markdown-v2")
    assert not can_use_incremental_sources(None, model_graph(), parser_version="markdown-v1")
    assert not can_use_incremental_sources(sources, None, parser_version="markdown-v1")


@pytest.mark.parametrize(
    "build_id,snapshot,phase,status,expected",
    [
        ("candidate", "snapshot-b", "publishing", "building", True),
        ("published", "snapshot-b", "publishing", "building", False),
        ("candidate", "snapshot-a", "publishing", "building", False),
        ("candidate", "snapshot-b", "computing", "building", False),
        ("candidate", "snapshot-b", "publishing", "failed", False),
        (None, "snapshot-b", "publishing", "building", False),
    ],
)
def test_only_the_current_publication_can_repair_ready(build_id, snapshot, phase, status, expected):
    state = ArtifactRelationsState(
        status=status,
        target_snapshot_id=snapshot,
        build_id=build_id,
        phase=phase,
    )
    assert can_recover_publication(state, model_graph("candidate", "snapshot-b")) is expected
    assert not can_recover_publication(state, None)


def test_missing_page_and_parsed_page_without_links_are_distinct():
    sources = RelationSources(
        parser_version="markdown-v1",
        pages={"a.md": RelationSource(blob_oid="blob-a", links=[])},
    )
    assert sources.pages["a.md"].links == []
    assert "b.md" not in sources.pages


def test_edges_preserve_the_persisted_from_field():
    edge = RelationEdge.model_validate(
        {
            "from": ROOT + "/a.md",
            "to": ROOT + "/b.md",
            "type": "references",
        }
    )
    assert edge.model_dump(by_alias=True)["from"] == edge.from_uri
    with pytest.raises(ValidationError):
        RelationEdge.model_validate({"from": "a.md", "to": "b.md", "type": "similarity"})
