import asyncio
import json

import pytest

from openviking.server.identity import RequestContext, Role
from openviking.service.memory_consolidation import (
    ConsolidationSource,
    build_exact_duplicate_dry_run_plan,
    build_exact_duplicate_dry_run_plan_from_fs,
)
from openviking_cli.exceptions import PermissionDeniedError
from openviking_cli.session.user_id import UserIdentifier

SCOPE = "viking://user/alice/memories/events"
CTX = RequestContext(user=UserIdentifier("test", "alice"), role=Role.USER)


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
    assert forward.consolidator_version == "exact-normalized-v3"
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
    second_link = {
        **first_link,
        "from_uri": f"{SCOPE}/two.md",
        "to_uri": "viking://resources/b.md",
    }

    plan = build_exact_duplicate_dry_run_plan(
        scope_uri=SCOPE,
        memory_type="events",
        sources=[
            _source("one", "same body", metadata={"links": [first_link]}),
            _source("two", "same body", metadata={"links": [second_link]}),
        ],
    )

    assert plan.groups == []


def test_system_provenance_does_not_block_structural_duplicate_grouping():
    plan = build_exact_duplicate_dry_run_plan(
        scope_uri=SCOPE,
        memory_type="events",
        sources=[
            _source(
                "one",
                "same body",
                metadata={
                    "source_extraction_id": "extract-one",
                    "source_extraction_ids": ["extract-one"],
                    "last_update_trace_id": "trace-one",
                },
            ),
            _source(
                "two",
                "same body",
                metadata={
                    "source_extraction_id": "extract-two",
                    "source_extraction_ids": ["extract-two"],
                    "last_update_trace_id": "trace-two",
                },
            ),
        ],
    )

    assert len(plan.groups) == 1
    assert plan.groups[0].canonical.content_sha256 != plan.groups[0].duplicates[0].content_sha256


def test_domain_metadata_remains_part_of_structural_identity():
    plan = build_exact_duplicate_dry_run_plan(
        scope_uri=SCOPE,
        memory_type="events",
        sources=[
            _source("one", "same body", metadata={"event_name": "launch"}),
            _source("two", "same body", metadata={"event_name": "review"}),
        ],
    )

    assert plan.groups == []


@pytest.mark.parametrize(
    ("field", "self_endpoint", "other_endpoint"),
    [
        ("links", "from_uri", "to_uri"),
        ("backlinks", "to_uri", "from_uri"),
    ],
)
def test_persisted_links_compare_structure_without_source_uri(field, self_endpoint, other_endpoint):
    first_link = {
        self_endpoint: f"{SCOPE}/one.md",
        other_endpoint: "viking://resources/a.md",
        "link_type": "related_to",
    }
    second_link = {
        **first_link,
        self_endpoint: f"{SCOPE}/two.md",
    }

    plan = build_exact_duplicate_dry_run_plan(
        scope_uri=SCOPE,
        memory_type="events",
        sources=[
            _source("one", "same body", metadata={field: [first_link]}),
            _source("two", "same body", metadata={field: [second_link]}),
        ],
    )

    assert len(plan.groups) == 1
    assert plan.groups[0].canonical.uri == f"{SCOPE}/one.md"


@pytest.mark.parametrize(
    ("field", "self_endpoint", "other_endpoint"),
    [
        ("links", "from_uri", "to_uri"),
        ("backlinks", "to_uri", "from_uri"),
    ],
)
def test_relationship_order_and_creation_time_are_provenance(field, self_endpoint, other_endpoint):
    def relationship(source_name: str, target: str, created_at: str) -> dict:
        return {
            self_endpoint: f"{SCOPE}/{source_name}.md",
            other_endpoint: target,
            "link_type": "related_to",
            "created_at": created_at,
        }

    first = [
        relationship("one", "viking://resources/a.md", "2026-01-01T00:00:00Z"),
        relationship("one", "viking://resources/b.md", "2026-01-02T00:00:00Z"),
    ]
    second = [
        relationship("two", "viking://resources/b.md", "2026-02-02T00:00:00Z"),
        relationship("two", "viking://resources/a.md", "2026-02-01T00:00:00Z"),
    ]

    plan = build_exact_duplicate_dry_run_plan(
        scope_uri=SCOPE,
        memory_type="events",
        sources=[
            _source("one", "same body", metadata={field: first}),
            _source("two", "same body", metadata={field: second}),
        ],
    )

    assert len(plan.groups) == 1
    assert plan.groups[0].canonical.content_sha256 != plan.groups[0].duplicates[0].content_sha256


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
        (
            "viking://resources/archive/memories/events",
            "events",
            _source("one", "body"),
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
            ctx=CTX,
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
                ctx=CTX,
                scope_uri=SCOPE,
                memory_type="events",
                node_limit=2,
            )
        )


def test_fs_dry_run_canonicalizes_short_scope_before_listing_and_revision():
    one = _source("one", "same")
    two = _source("two", "same")
    fs = _FakeFSService(
        entries=[
            {"uri": two.uri, "isDir": False},
            {"uri": one.uri, "isDir": False},
        ],
        contents={one.uri: one.raw_content, two.uri: two.raw_content},
    )

    plan = asyncio.run(
        build_exact_duplicate_dry_run_plan_from_fs(
            fs_service=fs,
            ctx=CTX,
            scope_uri="viking://user/memories/events",
            memory_type="events",
        )
    )

    assert plan.scope_uri == SCOPE
    assert fs.calls[0][0:2] == ("ls", SCOPE)


def test_fs_dry_run_rejects_inaccessible_scope_before_storage_calls():
    fs = _FakeFSService(entries=[], contents={})

    with pytest.raises(PermissionDeniedError, match="Access denied"):
        asyncio.run(
            build_exact_duplicate_dry_run_plan_from_fs(
                fs_service=fs,
                ctx=CTX,
                scope_uri="viking://user/bob/memories/events",
                memory_type="events",
            )
        )

    assert fs.calls == []


def test_fs_dry_run_rejects_outside_entries_before_reading():
    outside_uri = "viking://user/alice/resources/archive.md"
    fs = _FakeFSService(
        entries=[{"uri": outside_uri, "isDir": False}],
        contents={outside_uri: "must not be read"},
    )

    with pytest.raises(ValueError, match="outside consolidation scope"):
        asyncio.run(
            build_exact_duplicate_dry_run_plan_from_fs(
                fs_service=fs,
                ctx=CTX,
                scope_uri=SCOPE,
                memory_type="events",
            )
        )

    assert [call[0] for call in fs.calls] == ["ls"]
