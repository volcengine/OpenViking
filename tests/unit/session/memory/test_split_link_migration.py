# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""
Regression tests for issue #5904: memory consolidation N-way split deletes the
source file without migrating its links/backlinks, silently leaving dangling
references behind (errors=[]).

A batch of ``add x N + delete(source)`` with an empty ``delete_replacements``
map must not leave any in-batch link pointing at the deleted URI: the source's
relations are migrated onto the derived files (declared via ``derived_from``
links), and if a deleted URI still ends up referenced, the result must say so
instead of returning silently.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from openviking.session.memory.dataclass import (
    MemoryFile,
    ResolvedOperation,
    ResolvedOperations,
    StoredLink,
)
from openviking.session.memory.memory_updater import MemoryUpdater
from openviking.session.memory.utils import MemoryFileUtils


def _link(from_uri: str, to_uri: str, link_type: str = "related_to") -> dict:
    return {
        "from_uri": from_uri,
        "to_uri": to_uri,
        "link_type": link_type,
        "weight": 0.8,
        "match_text": "hobby",
        "description": "old link",
    }


def _build_split_operations(delete_replacements=None):
    """add x3 + delete(source): one memory file split into three derived files.

    The source carries one forward link (source -> profile) and one backlink
    (event -> source); both neighbors mirror those records in their own
    frontmatter, exactly like the reporter's snapshot evidence.
    """
    source_uri = "viking://user/u/memories/preferences/Evan/hobby_preferences.md"
    new_uris = [
        "viking://user/u/memories/preferences/Evan/hobby_part_1.md",
        "viking://user/u/memories/preferences/Evan/hobby_part_2.md",
        "viking://user/u/memories/preferences/Evan/hobby_part_3.md",
    ]
    profile_uri = "viking://user/u/memories/profile.md"
    event_uri = "viking://user/u/memories/events/2026/01/02/audit.md"

    source_file = MemoryFile(
        uri=source_uri,
        content="old hobby preferences",
        memory_type="preferences",
        links=[_link(source_uri, profile_uri)],
        backlinks=[_link(event_uri, source_uri)],
    )
    profile_file = MemoryFile(
        uri=profile_uri,
        content="profile",
        memory_type="profile",
        backlinks=[_link(source_uri, profile_uri)],
    )
    event_file = MemoryFile(
        uri=event_uri,
        content="event",
        memory_type="events",
        links=[_link(event_uri, source_uri)],
    )
    new_files = [
        MemoryFile(uri=uri, content=f"hobby part {index}", memory_type="preferences")
        for index, uri in enumerate(new_uris, start=1)
    ]

    files = {
        source_uri: MemoryFileUtils.write(source_file),
        profile_uri: MemoryFileUtils.write(profile_file),
        event_uri: MemoryFileUtils.write(event_file),
    }
    for uri, file_content in zip(new_uris, new_files, strict=False):
        files[uri] = MemoryFileUtils.write(file_content)

    mock_viking_fs = MagicMock()
    mock_viking_fs.read_file = AsyncMock(side_effect=lambda uri, ctx=None: files[uri])

    async def write_file(uri, content, ctx=None, **kwargs):
        files[uri] = content

    mock_viking_fs.write_file = AsyncMock(side_effect=write_file)

    updater = MemoryUpdater(registry=MagicMock())
    updater._get_viking_fs = MagicMock(return_value=mock_viking_fs)
    updater._apply_upsert = AsyncMock(return_value=None)
    updater._apply_delete = AsyncMock()
    updater._vectorize_memories = AsyncMock()
    updater.generate_overview = AsyncMock()

    operations = ResolvedOperations(
        upsert_operations=[
            ResolvedOperation(
                memory_fields={"name": f"hobby_part_{index}"},
                memory_type="preferences",
                uris=[uri],
            )
            for index, uri in enumerate(new_uris, start=1)
        ],
        delete_file_contents=[source_file],
        errors=[],
        resolved_links=[],
        delete_replacements=delete_replacements or {},
    )
    return updater, operations, files, source_uri, new_uris, profile_uri, event_uri


@pytest.mark.asyncio
async def test_n_way_split_migrates_source_links_to_derived_files():
    """add xN + delete(source) with derived_from links: neighbors are re-pointed
    at the derived files and no dangling reference survives."""
    (
        updater,
        operations,
        files,
        source_uri,
        new_uris,
        profile_uri,
        event_uri,
    ) = _build_split_operations()
    operations.resolved_links = [
        StoredLink(
            from_uri=uri,
            to_uri=source_uri,
            link_type="derived_from",
            weight=0.9,
            description="split lineage",
        )
        for uri in new_uris
    ]

    result = await updater.apply_operations(operations, MagicMock())

    assert result.deleted_uris == [source_uri]
    assert result.errors == []

    # In-batch links no longer reference the deleted source.
    endpoints = {(link.from_uri, link.to_uri) for link in operations.resolved_links}
    for from_uri, to_uri in endpoints:
        assert source_uri not in (from_uri, to_uri)

    # The profile's backlink to the deleted source is re-pointed at every
    # derived file (frontmatter form).
    profile = MemoryFileUtils.read(files[profile_uri], uri=profile_uri)
    assert {(link["from_uri"], link["to_uri"]) for link in profile.backlinks} == {
        (uri, profile_uri) for uri in new_uris
    }

    # The event's forward link to the deleted source is re-pointed too.
    event = MemoryFileUtils.read(files[event_uri], uri=event_uri)
    assert {(link["from_uri"], link["to_uri"]) for link in event.links} == {
        (event_uri, uri) for uri in new_uris
    }


def _build_replacement_operations(delete_replacements, deleted_uris_with_links=None):
    """1:1 replacement batch: delete S (and M when chained), upsert F.

    S carries one forward link (S -> profile) and one backlink (event -> S),
    mirrored in the neighbors' frontmatter, exactly like the 1:1 case that
    worked on pristine base. ``deleted_uris_with_links`` restricts which
    deleted files carry those relations (the chained case keeps them only on
    the chain head, since the intermediate file holds nothing new).
    """
    source_uri = "viking://user/u/memories/preferences/Evan/hobby_preferences.md"
    final_uri = "viking://user/u/memories/preferences/Evan/hobby_final.md"
    profile_uri = "viking://user/u/memories/profile.md"
    event_uri = "viking://user/u/memories/events/2026/01/02/audit.md"

    deleted_uris = [uri for uri in (source_uri, *delete_replacements.keys())]
    deleted_uris = [uri for uri in dict.fromkeys(deleted_uris) if uri != final_uri]
    if deleted_uris_with_links is None:
        deleted_uris_with_links = set(deleted_uris)

    source_file = MemoryFile(
        uri=source_uri,
        content="old hobby preferences",
        memory_type="preferences",
        links=[_link(source_uri, profile_uri)],
        backlinks=[_link(event_uri, source_uri)],
    )
    profile_file = MemoryFile(
        uri=profile_uri,
        content="profile",
        memory_type="profile",
        backlinks=[_link(source_uri, profile_uri)],
    )
    event_file = MemoryFile(
        uri=event_uri,
        content="event",
        memory_type="events",
        links=[_link(event_uri, source_uri)],
    )
    final_file = MemoryFile(
        uri=final_uri,
        content="consolidated hobby preferences",
        memory_type="preferences",
    )

    deleted_files = []
    files = {
        profile_uri: MemoryFileUtils.write(profile_file),
        event_uri: MemoryFileUtils.write(event_file),
        final_uri: MemoryFileUtils.write(final_file),
    }
    for uri in deleted_uris:
        carries_links = uri in deleted_uris_with_links
        deleted_file = MemoryFile(
            uri=uri,
            content=f"stale {uri}",
            memory_type="preferences",
            links=[_link(uri, profile_uri)] if carries_links else [],
            backlinks=[_link(event_uri, uri)] if carries_links else [],
        )
        deleted_files.append(deleted_file)
        files[uri] = MemoryFileUtils.write(deleted_file)

    mock_viking_fs = MagicMock()
    mock_viking_fs.read_file = AsyncMock(side_effect=lambda uri, ctx=None: files[uri])

    async def write_file(uri, content, ctx=None, **kwargs):
        files[uri] = content

    mock_viking_fs.write_file = AsyncMock(side_effect=write_file)

    updater = MemoryUpdater(registry=MagicMock())
    updater._get_viking_fs = MagicMock(return_value=mock_viking_fs)
    updater._apply_upsert = AsyncMock(return_value=None)
    updater._apply_delete = AsyncMock()
    updater._vectorize_memories = AsyncMock()
    updater.generate_overview = AsyncMock()

    operations = ResolvedOperations(
        upsert_operations=[
            ResolvedOperation(
                memory_fields={"name": "hobby_final"},
                memory_type="preferences",
                uris=[final_uri],
            )
        ],
        delete_file_contents=deleted_files,
        errors=[],
        resolved_links=[],
        delete_replacements=dict(delete_replacements),
    )
    return updater, operations, files, final_uri, profile_uri, event_uri


@pytest.mark.asyncio
async def test_direct_replacement_inherits_links_on_terminal_target():
    """A direct 1:1 delete replacement keeps inheriting on the replacement
    target (paired with base behavior)."""
    (
        updater,
        operations,
        files,
        final_uri,
        profile_uri,
        event_uri,
    ) = _build_replacement_operations(
        {
            "viking://user/u/memories/preferences/Evan/hobby_preferences.md":
                "viking://user/u/memories/preferences/Evan/hobby_final.md"
        }
    )

    result = await updater.apply_operations(operations, MagicMock())

    assert result.errors == []
    profile = MemoryFileUtils.read(files[profile_uri], uri=profile_uri)
    assert (final_uri, profile_uri) in {
        (link["from_uri"], link["to_uri"]) for link in profile.backlinks
    }
    final_file = MemoryFileUtils.read(files[final_uri], uri=final_uri)
    assert (final_uri, profile_uri) in {
        (link["from_uri"], link["to_uri"]) for link in final_file.links
    }
    assert (event_uri, final_uri) in {
        (link["from_uri"], link["to_uri"]) for link in final_file.backlinks
    }
    event = MemoryFileUtils.read(files[event_uri], uri=event_uri)
    assert (event_uri, final_uri) in {
        (link["from_uri"], link["to_uri"]) for link in event.links
    }


@pytest.mark.asyncio
async def test_chained_replacement_inherits_links_on_terminal_target():
    """With a chained remap (S -> M -> F) the inheritance target must be the
    terminal resolution F, not the immediate target M: M is itself deleted, so
    grouping under it silently drops S's forward link and backlink (review
    regression on 2026-10-12)."""
    source_uri = "viking://user/u/memories/preferences/Evan/hobby_preferences.md"
    intermediate_uri = "viking://user/u/memories/preferences/Evan/hobby_intermediate.md"
    (
        updater,
        operations,
        files,
        final_uri,
        profile_uri,
        event_uri,
    ) = _build_replacement_operations(
        {
            source_uri: intermediate_uri,
            intermediate_uri: "viking://user/u/memories/preferences/Evan/hobby_final.md",
        },
        deleted_uris_with_links={source_uri},
    )

    result = await updater.apply_operations(operations, MagicMock())

    assert result.errors == []
    # The terminal record F receives the source's relations, not M.
    final_file = MemoryFileUtils.read(files[final_uri], uri=final_uri)
    assert (final_uri, profile_uri) in {
        (link["from_uri"], link["to_uri"]) for link in final_file.links
    }
    assert (event_uri, final_uri) in {
        (link["from_uri"], link["to_uri"]) for link in final_file.backlinks
    }
    profile = MemoryFileUtils.read(files[profile_uri], uri=profile_uri)
    assert (final_uri, profile_uri) in {
        (link["from_uri"], link["to_uri"]) for link in profile.backlinks
    }
    event = MemoryFileUtils.read(files[event_uri], uri=event_uri)
    assert (event_uri, final_uri) in {
        (link["from_uri"], link["to_uri"]) for link in event.links
    }


@pytest.mark.asyncio
async def test_split_without_migration_reports_still_referenced_links():
    """Without a replacement or a derived_from signal there is no migration
    channel: the deletion may proceed, but the result must surface the dangling
    reference instead of returning errors=[] (issue #5904's minimum ask)."""
    source_uri = "viking://user/u/memories/preferences/Evan/hobby_preferences.md"
    other_uri = "viking://user/u/memories/preferences/Evan/summary.md"
    (
        updater,
        operations,
        _files,
        source_uri,
        _new_uris,
        _profile_uri,
        _event_uri,
    ) = _build_split_operations()
    # No derived_from signal: the upsert is unrelated content, not a split.
    operations.resolved_links = [
        StoredLink(
            from_uri=other_uri,
            to_uri=source_uri,
            link_type="related_to",
            weight=0.9,
            description="still points at the deleted file",
        )
    ]

    result = await updater.apply_operations(operations, MagicMock())

    assert result.deleted_uris == [source_uri]
    dangling_errors = [
        error for uri, error in result.errors if uri == source_uri and "referenced" in str(error)
    ]
    assert dangling_errors, (
        "deleting a URI that in-batch links still reference must be reported, "
        f"got errors={result.errors}"
    )
