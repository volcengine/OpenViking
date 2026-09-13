# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Tests for incremental diff assembly aligning artifact paths to the target.

Code artifacts live under ``<root>/repository/...`` but the target resource tree
and its vectors are keyed relative to the resource root (no ``repository``
prefix). build_resource_diff_plan must strip ``doc_rel`` from the new manifest so
the three snapshots line up; otherwise every file would look added+deleted.
"""

import pytest

from openviking.parse.output import ArtifactEntry, ParseArtifactRef
from openviking.storage.resource_diff import build_resource_diff_plan


class _Ctx:
    account_id = "acct"


class _FakeVikingFS:
    def __init__(self, tree_entries):
        self._tree_entries = tree_entries

    async def tree(
        self,
        uri,
        *,
        output="original",
        show_all_hidden=False,
        node_limit=1000,
        level_limit=3,
        ctx=None,
    ):
        return list(self._tree_entries)


class _FakeVikingDB:
    def __init__(self, records):
        self._records = records

    async def get_l2_diff_records_under_uri(self, target_uri, *, ctx):
        prefix = target_uri.rstrip("/") + "/"
        return {uri: record for uri, record in self._records.items() if uri.startswith(prefix)}


class _FakeStore:
    """Store whose artifact holds files under repository/."""

    def __init__(self, rels):
        self._rels = set(rels)

    async def list(self, ref, rel_path=""):
        base = rel_path.strip("/")
        prefix = f"{base}/" if base else ""
        seen = {}
        for rel in self._rels:
            if not rel.startswith(prefix):
                continue
            rest = rel[len(prefix) :]
            head = rest.split("/", 1)
            name = head[0]
            is_dir = len(head) > 1
            child_rel = f"{base}/{name}" if base else name
            seen[name] = ArtifactEntry(name=name, rel_path=child_rel, is_dir=is_dir)
        return list(seen.values())


_REF = ParseArtifactRef(backend="local", root="/tmp/art", root_type="dir")
_TARGET = "viking://resources/acme/demo"


@pytest.mark.asyncio
async def test_manifest_prefix_stripped_aligns_with_target() -> None:
    # New artifact has repository/a.py and repository/b.py; target already has
    # a.py (an existing file). Without prefix stripping these would never match.
    store = _FakeStore({"repository/a.py", "repository/b.py"})
    vfs = _FakeVikingFS(
        [
            {"rel_path": "a.py", "isDir": False, "uri": f"{_TARGET}/a.py"},
        ]
    )
    vikingdb = _FakeVikingDB({f"{_TARGET}/a.py": {"md5": "", "abstract": ""}})

    plan = await build_resource_diff_plan(
        viking_fs=vfs,
        vikingdb=vikingdb,
        store=store,
        artifact_ref=_REF,
        target_uri=_TARGET,
        ctx=_Ctx(),
        doc_rel="repository",
    )

    # a.py is in N and F+V -> intersection (md5 unknown -> body compare);
    # b.py is new -> added. Neither should be a false delete.
    assert "b.py" in plan.added
    assert "a.py" in plan.needs_body_compare
    assert plan.deleted == []


@pytest.mark.asyncio
async def test_vector_only_uri_under_target_is_planned_as_orphan() -> None:
    store = _FakeStore({"repository/a.py"})
    vfs = _FakeVikingFS([{"rel_path": "a.py", "isDir": False, "uri": f"{_TARGET}/a.py"}])
    vikingdb = _FakeVikingDB(
        {
            f"{_TARGET}/a.py": {"md5": "same", "abstract": "A"},
            f"{_TARGET}/ghost.py": {"md5": "stale", "abstract": "G"},
        }
    )

    plan = await build_resource_diff_plan(
        viking_fs=vfs,
        vikingdb=vikingdb,
        store=store,
        artifact_ref=_REF,
        target_uri=_TARGET,
        ctx=_Ctx(),
        doc_rel="repository",
    )

    assert plan.orphan_vectors == ["ghost.py"]
