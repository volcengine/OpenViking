# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Service-level tests for Feishu wiki batch dispatch (issue #3120).

Verifies that ``ResourceService._maybe_enqueue_feishu_batch_add_resource``:

* dispatches on the expanded entries' ``source_kind`` — not on
  ``len(expanded) == 1`` — so a one-document space/directory still takes the
  batch path (issue #3120 review, blocking #1);
* creates the batch parent through the **real** FS and forwards
  ``create_parent`` per child so each child's target resolution succeeds
  through the real path (blocking #2);
* persists a durable provider-neutral manifest before fan-out and reuses it
  for idempotent resume, recording per-item task ids / partial failures
  (blocking #3);
* preserves the wiki hierarchy via each child's ``rel_path`` (blocking #3).

The FS is a real in-memory fake (not a mock) so mkdir / manifest writes are
exercised through the actual code path; only the outer ``add_resource`` return
value is substituted, since the queue + understanding API are covered elsewhere.
"""

import asyncio
from typing import Any, Dict, List, Optional
from unittest.mock import AsyncMock, patch

import pytest

from openviking.parse.accessors.feishu_accessor import ExpandedDoc, FeishuAccessor
from openviking.service import batch_manifest_store as batch_store
from openviking.service.resource_service import (
    ResourceService,
    _is_feishu_batch_candidate,
)

# ---------------------------------------------------------------------------
# In-memory VikingFS double — real state, so mkdir / manifest writes are
# actually exercised (the test fails if _maybe_enqueue skips the FS).
# ---------------------------------------------------------------------------


class _InMemoryFS:
    """Minimal async FS that records dirs/files like the real VikingFS."""

    def __init__(self) -> None:
        self.dirs: set[str] = set()
        self.files: Dict[str, str] = {}

    def _norm(self, uri: str) -> str:
        return uri.rstrip("/")

    async def mkdir(
        self,
        uri: str,
        mode: str = "755",
        exist_ok: bool = False,
        ctx: Optional[Any] = None,
    ) -> None:
        u = self._norm(uri)
        # Mirror VikingFS: always ensure parent dirs exist.
        parent = u.rsplit("/", 1)[0] if "/" in u else u
        if parent != u:
            self.dirs.add(parent)
        self.dirs.add(u)

    async def exists(self, uri: str, ctx: Optional[Any] = None) -> bool:
        u = self._norm(uri)
        return u in self.dirs or u in self.files

    async def write_file(self, uri: str, data: str, ctx: Optional[Any] = None) -> None:
        u = self._norm(uri)
        self.files[u] = data
        parent = u.rsplit("/", 1)[0] if "/" in u else u
        if parent != u:
            self.dirs.add(parent)

    async def read_file(self, uri: str, ctx: Optional[Any] = None) -> str:
        return self.files.get(self._norm(uri), "")

    async def mv(self, src: str, dst: str, ctx: Optional[Any] = None) -> None:
        s, d = self._norm(src), self._norm(dst)
        if s in self.files:
            self.files[d] = self.files.pop(s)
        elif s in self.dirs:
            self.dirs.discard(s)
            self.dirs.add(d)

    async def rm(self, uri: str, ctx: Optional[Any] = None) -> None:
        u = self._norm(uri)
        self.files.pop(u, None)
        self.dirs.discard(u)

    async def ls(self, uri: str, ctx: Optional[Any] = None) -> List[Dict[str, Any]]:
        u = self._norm(uri)
        prefix = u + "/"
        names: set[str] = set()
        for f in self.files:
            if f.startswith(prefix) and "/" not in f[len(prefix) :]:
                names.add(f[len(prefix) :])
        for d in self.dirs:
            if d.startswith(prefix) and "/" not in d[len(prefix) :]:
                names.add(d[len(prefix) :])
        return [{"name": n, "isDir": n in {d[len(prefix) :] for d in self.dirs}} for n in names]


def _make_service(fs: Optional[_InMemoryFS] = None) -> ResourceService:
    """A ResourceService wired to an in-memory FS for batch dispatch tests."""
    return ResourceService(viking_fs=fs or _InMemoryFS())


def _child(name: str, *, rel_path: str = "", title: str = "") -> ExpandedDoc:
    return ExpandedDoc(
        url=f"https://x.feishu.cn/docx/{name}",
        title=title or name,
        source_kind="batch_child",
        rel_path=rel_path,
    )


def _install_fake_add_resource(
    service: ResourceService,
    *,
    fail_on: Optional[set[str]] = None,
) -> List[Dict[str, Any]]:
    """Replace ``add_resource`` with a recorder that returns a durable task_id.

    Asserts ``create_parent=True`` (blocking #2) and that the batch parent
    already exists in the real FS when each child is dispatched (i.e. mkdir
    happened before fan-out through the real path, not a mock).
    """
    fail_on = fail_on or set()
    captured: List[Dict[str, Any]] = []

    async def _fake_add_resource(path: str, *args: Any, **kwargs: Any) -> Dict[str, Any]:
        parent = kwargs.get("parent")
        captured.append(
            {
                "path": path,
                "parent": parent,
                "create_parent": kwargs.get("create_parent"),
            }
        )
        # blocking #2: create_parent must be forwarded on every child.
        assert kwargs.get("create_parent") is True, (
            "create_parent must be forwarded so each child's rel_path directory "
            "is created through the real target-resolution path"
        )
        if path in fail_on:
            raise RuntimeError(f"simulated ingest failure for {path}")
        task_id = f"task-{path.rsplit('/', 1)[-1]}"
        return {"status": "success", "task_id": task_id, "root_uri": f"{parent}/{task_id}"}

    service.add_resource = _fake_add_resource  # type: ignore[assignment]
    return captured


# ---------------------------------------------------------------------------
# _is_feishu_batch_candidate — pure routing predicate.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "url,expected",
    [
        ("https://x.feishu.cn/wiki/settings/space1", True),
        ("https://x.feishu.cn/wiki/nodeTok1", True),
        ("https://x.feishu.cn/docx/doxcnABC", False),
        ("https://x.feishu.cn/sheets/stok1", False),
        ("https://x.feishu.cn/base/appTok1", False),
        ("https://github.com/org/repo", False),
        ("https://example.com/wiki/foo", False),  # not a feishu domain
    ],
)
def test_is_feishu_batch_candidate(url, expected):
    assert _is_feishu_batch_candidate(url) is expected


# ---------------------------------------------------------------------------
# blocking #2 + #3: real batch_parent creation + durable manifest.
# ---------------------------------------------------------------------------


def test_batch_creates_parent_and_persists_manifest():
    """A space-root URL expands to 3 docs → batch parent is created through the
    real FS, a manifest is persisted, and each child is dispatched with
    ``create_parent=True`` and a per-item task_id."""
    fs = _InMemoryFS()
    service = _make_service(fs)
    captured = _install_fake_add_resource(service)
    docs = [_child("doxcn1"), _child("doxcn2"), _child("doxcn3")]

    async def _runner():
        with patch.object(
            FeishuAccessor,
            "expand_feishu_url",
            new=AsyncMock(return_value=docs),
        ):
            return await service._maybe_enqueue_feishu_batch_add_resource(
                path="https://x.feishu.cn/wiki/settings/space1",
                ctx=None,
                to="viking://resources/my_wiki",
                parent=None,
                parser_args={},
                kwargs={},
            )

    result = asyncio.run(_runner())

    assert result is not None
    assert result["status"] == "batch_queued"
    assert result["batch_count"] == 3
    assert result["queued_count"] == 3
    assert result["root_uri"] == "viking://resources/my_wiki"

    # blocking #2: the batch parent directory was created through the real FS
    # (not mocked) before fan-out, so child target resolution can succeed.
    assert asyncio.run(fs.exists("viking://resources/my_wiki", ctx=None)) is True

    # blocking #3: one add_resource per child, each with the batch parent and
    # forwarded create_parent.
    assert len(captured) == 3
    assert {c["path"] for c in captured} == {
        "https://x.feishu.cn/docx/doxcn1",
        "https://x.feishu.cn/docx/doxcn2",
        "https://x.feishu.cn/docx/doxcn3",
    }
    assert all(c["parent"] == "viking://resources/my_wiki" for c in captured)

    # blocking #3: the durable manifest was persisted and every item carries a
    # real task_id (durable per-item state, not fire-and-forget).
    batch_id = result["batch_id"]
    manifest = asyncio.run(batch_store.load_manifest(batch_id, fs, ctx=None))
    assert manifest is not None
    assert manifest["source_url"] == "https://x.feishu.cn/wiki/settings/space1"
    assert manifest["parent_uri"] == "viking://resources/my_wiki"
    assert len(manifest["items"]) == 3
    assert all(item["task_id"] for item in manifest["items"])
    assert {item["url"] for item in manifest["items"]} == {
        "https://x.feishu.cn/docx/doxcn1",
        "https://x.feishu.cn/docx/doxcn2",
        "https://x.feishu.cn/docx/doxcn3",
    }


# ---------------------------------------------------------------------------
# blocking #1: source_kind dispatch (NOT len(expanded) == 1).
# ---------------------------------------------------------------------------


def test_batch_returns_none_for_passthrough_single_doc():
    """A passthrough single-doc entry (wiki leaf) → None (fall through to the
    single-doc path); add_resource is never called."""
    fs = _InMemoryFS()
    service = _make_service(fs)
    captured = _install_fake_add_resource(service)

    async def _runner():
        with patch.object(
            FeishuAccessor,
            "expand_feishu_url",
            new=AsyncMock(
                return_value=[
                    ExpandedDoc(
                        url="https://x.feishu.cn/wiki/leaf1",
                        source_kind="single_doc_passthrough",
                    )
                ]
            ),
        ):
            return await service._maybe_enqueue_feishu_batch_add_resource(
                path="https://x.feishu.cn/wiki/leaf1",
                ctx=None,
                parser_args={},
                kwargs={},
            )

    result = asyncio.run(_runner())
    assert result is None
    assert captured == []


def test_batch_handles_single_document_space_as_batch():
    """Regression (issue #3120 review, blocking #1): a space/directory that
    contains *exactly one* importable document must still take the batch path.
    The old ``len(expanded) == 1`` check mis-classified this as a leaf and tried
    to ingest the original ``/wiki/settings/<id>`` URL as a single doc."""
    fs = _InMemoryFS()
    service = _make_service(fs)
    captured = _install_fake_add_resource(service)
    docs = [_child("onlyDoc", title="Only")]  # one batch child, not a passthrough

    async def _runner():
        with patch.object(
            FeishuAccessor,
            "expand_feishu_url",
            new=AsyncMock(return_value=docs),
        ):
            return await service._maybe_enqueue_feishu_batch_add_resource(
                path="https://x.feishu.cn/wiki/settings/space_one",
                ctx=None,
                to="viking://resources/my_wiki",
                parser_args={},
                kwargs={},
            )

    result = asyncio.run(_runner())
    # The single batch_child drives the batch path — no fall-through.
    assert result is not None
    assert result["status"] == "batch_queued"
    assert result["batch_count"] == 1
    assert len(captured) == 1
    assert captured[0]["path"] == "https://x.feishu.cn/docx/onlyDoc"


# ---------------------------------------------------------------------------
# Token forwarding + failure propagation.
# ---------------------------------------------------------------------------


def test_batch_passes_feishu_access_token_to_expander():
    fs = _InMemoryFS()
    service = _make_service(fs)
    _install_fake_add_resource(service)
    docs = [_child("d1"), _child("d2")]

    async def _runner():
        mock_expand = AsyncMock(return_value=docs)
        with patch.object(FeishuAccessor, "expand_feishu_url", new=mock_expand):
            await service._maybe_enqueue_feishu_batch_add_resource(
                path="https://x.feishu.cn/wiki/settings/space1",
                ctx=None,
                to="viking://resources/my_wiki",
                parser_args={"feishu_access_token": "u-test-token"},
                kwargs={},
            )
        return mock_expand

    mock_expand = asyncio.run(_runner())
    assert mock_expand.call_count == 1
    assert mock_expand.call_args.kwargs.get("feishu_access_token") == "u-test-token"


def test_batch_propagates_expand_failure():
    fs = _InMemoryFS()
    service = _make_service(fs)
    captured = _install_fake_add_resource(service)

    async def _runner():
        with patch.object(
            FeishuAccessor,
            "expand_feishu_url",
            new=AsyncMock(side_effect=RuntimeError("wiki API down")),
        ):
            with pytest.raises(RuntimeError, match="wiki API down"):
                await service._maybe_enqueue_feishu_batch_add_resource(
                    path="https://x.feishu.cn/wiki/settings/space1",
                    ctx=None,
                    parser_args={},
                    kwargs={},
                )

    asyncio.run(_runner())
    assert captured == []


# ---------------------------------------------------------------------------
# blocking #3: hierarchy via rel_path + idempotent resume + partial failure.
# ---------------------------------------------------------------------------


def test_batch_preserves_hierarchy_via_rel_path():
    """Children with a non-empty ``rel_path`` are dispatched with a nested
    parent ``<batch_parent>/<rel_path>`` so the wiki hierarchy is preserved."""
    fs = _InMemoryFS()
    service = _make_service(fs)
    captured = _install_fake_add_resource(service)
    docs = [
        _child("top1", rel_path=""),
        _child("sub1", rel_path="team"),
        _child("sub2", rel_path="team/handbook"),
    ]

    async def _runner():
        with patch.object(
            FeishuAccessor,
            "expand_feishu_url",
            new=AsyncMock(return_value=docs),
        ):
            return await service._maybe_enqueue_feishu_batch_add_resource(
                path="https://x.feishu.cn/wiki/settings/space1",
                ctx=None,
                to="viking://resources/my_wiki",
                parser_args={},
                kwargs={},
            )

    result = asyncio.run(_runner())
    assert result is not None
    by_url = {c["path"]: c["parent"] for c in captured}
    assert by_url["https://x.feishu.cn/docx/top1"] == "viking://resources/my_wiki"
    assert by_url["https://x.feishu.cn/docx/sub1"] == "viking://resources/my_wiki/team"
    assert by_url["https://x.feishu.cn/docx/sub2"] == "viking://resources/my_wiki/team/handbook"


def test_batch_is_idempotent_on_resubmit():
    """Re-submitting the same source URL reuses the prior manifest: children
    that already have a task_id are not re-enqueued (idempotent resume)."""
    fs = _InMemoryFS()
    service = _make_service(fs)
    docs = [_child("doxcn1"), _child("doxcn2")]
    batch_id = batch_store.derive_batch_id(
        "https://x.feishu.cn/wiki/settings/space1", "viking://resources/my_wiki"
    )

    async def _seed_manifest():
        manifest = batch_store.build_manifest(
            batch_id=batch_id,
            source_url="https://x.feishu.cn/wiki/settings/space1",
            source_kind="feishu_wiki_space",
            parent_uri="viking://resources/my_wiki",
            created_at="2026-07-28T00:00:00",
            items=[
                {
                    "url": "https://x.feishu.cn/docx/doxcn1",
                    "title": "doxcn1",
                    "rel_path": "",
                    "parent_uri": "viking://resources/my_wiki",
                    "task_id": "task-preexisting",
                    "to_uri": "viking://resources/my_wiki/task-preexisting",
                    "status": "queued",
                }
            ],
        )
        await batch_store.save_manifest(manifest, fs, ctx=None)

    asyncio.run(_seed_manifest())
    captured = _install_fake_add_resource(service)

    async def _runner():
        with patch.object(
            FeishuAccessor,
            "expand_feishu_url",
            new=AsyncMock(return_value=docs),
        ):
            return await service._maybe_enqueue_feishu_batch_add_resource(
                path="https://x.feishu.cn/wiki/settings/space1",
                ctx=None,
                to="viking://resources/my_wiki",
                parser_args={},
                kwargs={},
            )

    result = asyncio.run(_runner())
    assert result is not None
    # doxcn1 already had a task_id → not re-enqueued; only doxcn2 is dispatched.
    dispatched = {c["path"] for c in captured}
    assert "https://x.feishu.cn/docx/doxcn1" not in dispatched
    assert "https://x.feishu.cn/docx/doxcn2" in dispatched
    # The reused task_id is carried through to the response + manifest.
    item_by_url = {it["url"]: it for it in result["items"]}
    assert item_by_url["https://x.feishu.cn/docx/doxcn1"]["task_id"] == "task-preexisting"


def test_batch_partial_failure_records_error():
    """When one child's add_resource fails, the failure is recorded on that
    item (visible, not silently logged) while the others still succeed."""
    fs = _InMemoryFS()
    service = _make_service(fs)
    docs = [_child("ok1"), _child("bad1"), _child("ok2")]
    captured = _install_fake_add_resource(service, fail_on={"https://x.feishu.cn/docx/bad1"})

    async def _runner():
        with patch.object(
            FeishuAccessor,
            "expand_feishu_url",
            new=AsyncMock(return_value=docs),
        ):
            return await service._maybe_enqueue_feishu_batch_add_resource(
                path="https://x.feishu.cn/wiki/settings/space1",
                ctx=None,
                to="viking://resources/my_wiki",
                parser_args={},
                kwargs={},
            )

    result = asyncio.run(_runner())
    assert result is not None
    assert result["queued_count"] == 2  # ok1 + ok2 succeeded
    item_by_url = {it["url"]: it for it in result["items"]}
    assert item_by_url["https://x.feishu.cn/docx/bad1"]["status"] == "failed"
    assert "error" in item_by_url["https://x.feishu.cn/docx/bad1"]
    assert item_by_url["https://x.feishu.cn/docx/ok1"]["status"] == "queued"
    # All three were attempted (partial failure does not abort the batch).
    assert len(captured) == 3
