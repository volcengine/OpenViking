"""Regression: internal directory enumeration during ingest must not be capped
at ``viking_fs.ls``'s default ``node_limit=1000``.

When a resource namespace already exists, re-ingesting a directory takes the
incremental sync path (``VikingFS.sync_tree`` -> ``sync_dir`` -> ``list_children``),
which lists the temp root's children via ``viking_fs.ls`` with ``node_limit=LS_ALL_NODES``.
``ls`` defaults ``node_limit=1000``, so an import of >1000 docs would silently
materialize only the first 1000 subdirectories into the target if sync_tree
forgot to pass the unlimited constant.

Observed in the wild: a 6,221-doc WixQA re-ingest (the namespace already held an
earlier 209-doc import, so the atomic-move fast path was skipped) produced
exactly 1000 directories under ``resources/wixqa``.
"""

from __future__ import annotations

import pytest

from openviking.storage.queuefs.semantic_processor import SemanticProcessor
from openviking.storage.viking_fs import LS_ALL_NODES


class _TruncatingVikingFS:
    """Fake whose ``ls`` truncates at ``node_limit``, exactly like the real one.

    The temp root holds ``n_children`` doc subdirectories; the target namespace
    pre-exists but is empty (forcing the incremental ``sync_dir`` path instead of
    the atomic whole-directory move).
    """

    TEMP = "viking://temp/import"
    TARGET = "viking://resources/wixqa"

    def __init__(self, n_children: int):
        self._children = [{"name": f"doc{i:05d}", "isDir": True} for i in range(n_children)]
        self.moved: list[tuple[str, str]] = []
        self.ls_node_limits: list[int] = []

    async def exists(self, uri, ctx=None):
        return uri in (self.TEMP, self.TARGET)

    async def ls(self, uri, show_all_hidden=False, node_limit=1000, ctx=None):
        self.ls_node_limits.append(node_limit)
        entries = self._children if uri == self.TEMP else []
        return entries[:node_limit] if node_limit is not None else entries

    async def read_file(self, uri, ctx=None):
        return ""

    async def stat(self, uri, ctx=None):
        return {"size": 0}

    async def mv(self, src, dst, ctx=None, lease_ref=None):
        self.moved.append((src, dst))

    async def rm(self, uri, recursive=False, ctx=None, lease_ref=None):
        pass

    async def mkdir(self, uri, exist_ok=False, ctx=None, lease_ref=None):
        pass

    async def delete_temp(self, uri, ctx=None, lease_ref=None):
        pass

    async def sync_tree(
        self, root_uri, target_uri, *, ctx=None, file_change_status=None,
        lease_ref=None, delete_temp_after=False, is_changed=None,
    ):
        from openviking.storage.viking_fs import SyncDiff
        from openviking_cli.utils.uri import VikingURI

        diff = SyncDiff()
        # Mirror _SyncMixin.list_children: must pass node_limit=LS_ALL_NODES.
        entries = await self.ls(
            root_uri, show_all_hidden=True, node_limit=LS_ALL_NODES, ctx=ctx
        )
        for entry in entries:
            name = entry.get("name", "")
            if not name or name in (".", "..") or name.startswith("."):
                continue
            src = VikingURI(root_uri).join(name).uri
            dst = VikingURI(target_uri).join(name).uri
            if entry.get("isDir", False):
                diff.added_dirs.append(dst)
            else:
                diff.added_files.append(dst)
            await self.mv(src, dst, ctx=ctx, lease_ref=lease_ref)
        return diff


@pytest.mark.asyncio
async def test_sync_materializes_all_children_above_default_node_limit(monkeypatch):
    n_children = 1500  # > the default node_limit of 1000
    fake = _TruncatingVikingFS(n_children)
    monkeypatch.setattr(
        "openviking.storage.queuefs.semantic_processor.get_viking_fs",
        lambda: fake,
    )
    monkeypatch.setattr(
        "openviking.storage.queuefs.semantic_processor.rewrite_image_uris",
        lambda *a, **kw: None,
    )

    diff = await SemanticProcessor()._sync_topdown_recursive(
        _TruncatingVikingFS.TEMP,
        _TruncatingVikingFS.TARGET,
        lock=None,
    )

    assert len(fake.moved) == n_children, (
        f"only {len(fake.moved)}/{n_children} doc dirs materialized — internal "
        "sync was truncated at ls node_limit"
    )
    assert len(diff.added_dirs) == n_children
    assert all(
        lim == LS_ALL_NODES for lim in fake.ls_node_limits
    ), f"sync_tree must pass LS_ALL_NODES to ls, got node_limits={fake.ls_node_limits}"


class _TruncatingLsFS:
    """Minimal fake exposing only a node_limit-truncating ``ls`` over a fixed
    set of child directories."""

    def __init__(self, dir_uri: str, n_children: int):
        self._dir_uri = dir_uri
        self._children = [{"name": f"doc{i:05d}", "isDir": True} for i in range(n_children)]

    async def ls(self, uri, node_limit=1000, ctx=None):
        entries = self._children if uri == self._dir_uri else []
        return entries[:node_limit]  # the real ls truncates here


@pytest.mark.asyncio
async def test_semantic_dag_list_dir_enumerates_all_children(monkeypatch):
    """The summary DAG dispatches one summary/recursion task per child it lists.
    If ``_list_dir`` is capped at node_limit, children beyond 1000 never get an
    ``.abstract.md``/``.overview.md`` (and are never recursed into), so a large
    namespace silently loses its L0/L1 layers."""
    from openviking.storage.queuefs.semantic_dag import SemanticDagExecutor

    dir_uri = "viking://resources/wixqa"
    n_children = 1500  # > the default node_limit of 1000
    fake = _TruncatingLsFS(dir_uri, n_children)
    monkeypatch.setattr(
        "openviking.storage.queuefs.semantic_dag.get_viking_fs",
        lambda: fake,
    )

    executor = SemanticDagExecutor(
        processor=None,
        context_type="resource",
        max_concurrent_llm=1,
        ctx=None,
    )
    children_dirs, file_paths = await executor._list_dir(dir_uri, from_hint="test")

    assert len(children_dirs) == n_children, (
        f"only {len(children_dirs)}/{n_children} children listed — summary DAG "
        "was truncated at ls node_limit"
    )
