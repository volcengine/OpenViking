import asyncio
import json

import pytest

from openviking.service.memory_consolidation import (
    ConsolidationSource,
    build_exact_duplicate_dry_run_plan,
    build_exact_duplicate_dry_run_plan_from_fs,
)

SCOPE = "viking://user/alice/memories/events"


def _source(
    name: str,
    content: str,
    *,
    version: int = 1,
    memory_type: str = "events",
    metadata: dict | None = None,
):
    fields = {"memory_type": memory_type, "version": version, **(metadata or {})}
    return ConsolidationSource(
        uri=f"{SCOPE}/{name}.md",
        raw_content=(
            f"{content}\n\n<!-- MEMORY_FIELDS\n{json.dumps(fields, ensure_ascii=False)}\n-->"
        ),
    )


def test_plan_is_stable_and_selects_lexicographic_canonical():
    sources = [_source("zeta", "same body"), _source("alpha", "same body")]

    forward = build_exact_duplicate_dry_run_plan(
        scope_uri=SCOPE, memory_type="events", sources=sources
    )
    reverse = build_exact_duplicate_dry_run_plan(
        scope_uri=f"{SCOPE}/", memory_type="events", sources=list(reversed(sources))
    )

    assert forward == reverse
    assert forward.schema_version == "memory_consolidation_dry_run_plan_v1"
    assert forward.consolidator_version == "exact-normalized-v1"
    assert forward.scanned_files == 2
    assert forward.groups[0].candidate_id.startswith("exact:")
    assert forward.groups[0].canonical.uri == f"{SCOPE}/alpha.md"
    assert forward.groups[0].canonical.version == 1
    assert forward.groups[0].canonical.content_sha256
    assert [item.uri for item in forward.groups[0].duplicates] == [f"{SCOPE}/zeta.md"]


def test_plan_normalizes_unicode_newlines_and_trailing_whitespace_only():
    plan = build_exact_duplicate_dry_run_plan(
        scope_uri=SCOPE,
        memory_type="events",
        sources=[
            _source("one", "Cafe\u0301  \r\nsecond line\t"),
            _source("two", "Café\nsecond line"),
            _source("three", "Café second line"),
        ],
    )

    assert len(plan.groups) == 1
    assert plan.groups[0].canonical.uri == f"{SCOPE}/one.md"
    assert [item.uri for item in plan.groups[0].duplicates] == [f"{SCOPE}/two.md"]


def test_links_remain_part_of_conservative_fingerprint():
    plan = build_exact_duplicate_dry_run_plan(
        scope_uri=SCOPE,
        memory_type="events",
        sources=[
            _source("one", "See [source](viking://resources/a.md)."),
            _source("two", "See [source](viking://resources/b.md)."),
        ],
    )

    assert plan.groups == []


def test_persisted_link_metadata_remains_part_of_conservative_fingerprint():
    first_link = {
        "from_uri": f"{SCOPE}/one.md",
        "to_uri": "viking://resources/a.md",
        "link_type": "related_to",
    }
    second_link = {**first_link, "to_uri": "viking://resources/b.md"}

    plan = build_exact_duplicate_dry_run_plan(
        scope_uri=SCOPE,
        memory_type="events",
        sources=[
            _source("one", "same body", metadata={"links": [first_link]}),
            _source("two", "same body", metadata={"links": [second_link]}),
        ],
    )

    assert plan.groups == []


def test_revision_changes_when_source_version_changes():
    first = build_exact_duplicate_dry_run_plan(
        scope_uri=SCOPE,
        memory_type="events",
        sources=[_source("one", "same", version=1), _source("two", "same", version=1)],
    )
    second = build_exact_duplicate_dry_run_plan(
        scope_uri=SCOPE,
        memory_type="events",
        sources=[_source("one", "same", version=2), _source("two", "same", version=1)],
    )

    assert first.revision != second.revision


@pytest.mark.parametrize(
    ("scope_uri", "memory_type", "source"),
    [
        ("viking://user/alice/memories", "events", _source("one", "body")),
        (f"{SCOPE}/nested", "events", _source("one", "body")),
        (SCOPE, "profiles", _source("one", "body")),
        (SCOPE, "events", _source("one", "body", memory_type="profiles")),
        (
            SCOPE,
            "events",
            ConsolidationSource(
                uri="viking://user/bob/memories/events/one.md",
                raw_content="body",
            ),
        ),
    ],
)
def test_plan_rejects_cross_scope_or_cross_type_sources(scope_uri, memory_type, source):
    with pytest.raises(ValueError):
        build_exact_duplicate_dry_run_plan(
            scope_uri=scope_uri, memory_type=memory_type, sources=[source]
        )


def test_plan_rejects_duplicate_source_uri():
    source = _source("one", "body")

    with pytest.raises(ValueError, match="duplicate source URI"):
        build_exact_duplicate_dry_run_plan(
            scope_uri=SCOPE, memory_type="events", sources=[source, source]
        )


class _FakeFSService:
    def __init__(self, entries, contents):
        self.entries = entries
        self.contents = contents
        self.calls = []

    async def ls(self, uri, **kwargs):
        self.calls.append(("ls", uri, kwargs))
        return self.entries

    async def read(self, uri, **kwargs):
        self.calls.append(("read", uri, kwargs))
        return self.contents[uri]


def test_fs_dry_run_reads_only_memory_files_and_skips_sidecars():
    one = _source("one", "same")
    two = _source("two", "same")
    sidecar = f"{SCOPE}/.overview.md"
    fs = _FakeFSService(
        entries=[
            {"uri": f"{SCOPE}/nested", "isDir": True},
            {"uri": sidecar, "isDir": False},
            {"uri": two.uri, "isDir": False},
            {"uri": one.uri, "isDir": False},
        ],
        contents={one.uri: one.raw_content, two.uri: two.raw_content},
    )

    plan = asyncio.run(
        build_exact_duplicate_dry_run_plan_from_fs(
            fs_service=fs,
            ctx=object(),
            scope_uri=SCOPE,
            memory_type="events",
        )
    )

    assert plan.scanned_files == 2
    assert plan.groups[0].canonical.uri == one.uri
    assert [call[1] for call in fs.calls if call[0] == "read"] == [one.uri, two.uri]
    assert all(call[0] in {"ls", "read"} for call in fs.calls)


def test_fs_dry_run_fails_closed_when_node_limit_is_reached():
    fs = _FakeFSService(
        entries=[{"uri": f"{SCOPE}/{index}.md", "isDir": False} for index in range(2)],
        contents={},
    )

    with pytest.raises(ValueError, match="node_limit"):
        asyncio.run(
            build_exact_duplicate_dry_run_plan_from_fs(
                fs_service=fs,
                ctx=object(),
                scope_uri=SCOPE,
                memory_type="events",
                node_limit=2,
            )
        )
