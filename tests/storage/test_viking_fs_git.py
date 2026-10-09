# tests/storage/test_viking_fs_git.py
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from openviking.pyagfs.exceptions import (
    AGFSNotFoundError,
    AGFSPathNotFoundError,
    GitRestoreWritebackPartialError,
)
from openviking.server.identity import RequestContext, Role
from openviking.storage import viking_fs as viking_fs_module
from openviking.storage.viking_fs import VikingFS
from openviking_cli.exceptions import (
    ConflictError,
    NotFoundError,
    PermissionDeniedError,
    ResourceExhaustedError,
)
from openviking_cli.session.user_id import UserIdentifier

pytestmark = pytest.mark.asyncio


async def test_show_without_limit_preserves_existing_binding_call():
    class RecordingAGFS:
        def __init__(self):
            self.calls = []

        async def run(self, operation, **kwargs):
            self.calls.append((operation, kwargs))
            return {"oid": "a" * 40}

    class ShowVikingFS:
        def __init__(self):
            self._async_agfs = RecordingAGFS()

        def _ctx_or_default(self, ctx):
            return ctx

        def _uri_to_tree_path(self, path, *, ctx):
            return path

    vfs = ShowVikingFS()

    await VikingFS.show(vfs, "main", ctx=_request_context())

    assert vfs._async_agfs.calls == [
        (
            "git_show",
            {
                "account": "account",
                "target_ref": "main",
                "path": None,
            },
        )
    ]


@pytest.mark.skip(reason="needs git-enabled VikingFS fixture")
async def test_show_blob_raw_returns_envelope(viking_fs_with_two_commits):
    """show_blob_raw must return the full {oid, size, bytes} dict, not strip it."""
    vfs, _account, commit_oid, sample_path, sample_bytes = viking_fs_with_two_commits

    raw = await vfs.show_blob_raw(commit_oid, path=sample_path)

    assert isinstance(raw, dict)
    assert raw["bytes"] == sample_bytes
    assert raw["size"] == len(sample_bytes)
    assert isinstance(raw["oid"], str) and len(raw["oid"]) == 40


async def test_diff_reads_blobs_from_resolved_commit_oids():
    from_oid = "a" * 40
    to_oid = "b" * 40

    class MovingRefVikingFS:
        def __init__(self):
            self._async_agfs = _RecordingDiffAGFS(
                blobs={
                    from_oid: b"old content\n",
                    to_oid: b"new content\n",
                    "base": b"moved base content\n",
                    "main": b"moved main content\n",
                },
                ref_oids={"base": from_oid, "main": to_oid},
            )

        def _ctx_or_default(self, ctx):
            return ctx

        async def _ensure_access(self, uri, ctx):
            pass

        def _uri_to_tree_path(self, path, *, ctx):
            return path.removeprefix("viking://")

    vfs = MovingRefVikingFS()
    ctx = RequestContext(
        user=UserIdentifier(account_id="account", user_id="user"),
        role=Role.ROOT,
    )

    result = await VikingFS.diff(
        vfs,
        path="viking://user/user/memories/experiences/example.md",
        from_ref="base",
        to_ref="main",
        ctx=ctx,
    )

    assert vfs._async_agfs.blob_refs == [from_oid, to_oid]
    assert result["from_commit"] == from_oid
    assert result["to_commit"] == to_oid
    assert "-old content" in result["diff_text"]
    assert "+new content" in result["diff_text"]


async def test_diff_can_hide_memory_fields():
    before = b'old content\n\n<!-- MEMORY_FIELDS\n{"version": 1}\n-->'
    after = b'new content\n\n<!-- MEMORY_FIELDS\n{"version": 2}\n-->'
    vfs = _DiffVikingFS(before, after)

    result = await VikingFS.diff(
        vfs,
        path="viking://user/user/memories/experiences/example.md",
        from_ref="from",
        to_ref="to",
        raw=False,
        ctx=_request_context(),
    )

    assert "-old content" in result["diff_text"]
    assert "+new content" in result["diff_text"]
    assert "MEMORY_FIELDS" not in result["diff_text"]
    assert "version" not in result["diff_text"]


class _DiffVikingFS:
    def __init__(self, before: bytes, after: bytes):
        self._before = before
        self._after = after
        self._async_agfs = _RecordingDiffAGFS(blobs={"from": before, "to": after})
        self.blob_read_limits = self._async_agfs.blob_read_limits
        self.access_checks = []

    def _ctx_or_default(self, ctx):
        return ctx

    async def _ensure_access(self, uri, ctx):
        self.access_checks.append((uri, ctx))

    def _uri_to_tree_path(self, path, *, ctx):
        return path.removeprefix("viking://")


class _RecordingDiffAGFS:
    def __init__(self, *, blobs=None, ref_oids=None):
        self.calls = []
        self.blobs = blobs or {}
        self.ref_oids = ref_oids or {}
        self.blob_errors = {}
        self.blob_refs = []
        self.blob_read_limits = []

    async def run(self, operation, **kwargs):
        self.calls.append((operation, kwargs))
        if operation == "git_show":
            target_ref = kwargs["target_ref"]
            if kwargs["path"] is None:
                return {"oid": self.ref_oids.get(target_ref, target_ref)}
            error = self.blob_errors.get(target_ref)
            if error is not None:
                raise error
            self.blob_refs.append(target_ref)
            self.blob_read_limits.append(kwargs.get("max_blob_bytes"))
            value = self.blobs[target_ref]
            return {"oid": target_ref, "size": len(value), "bytes": value}
        assert operation == "git_diff_text"
        before = kwargs["before"]
        after = kwargs["after"]
        output = (
            f"--- {kwargs['fromfile']}\n"
            f"+++ {kwargs['tofile']}\n"
            "@@ -1 +1 @@\n"
            f"-{before.rstrip()}\n"
            f"+{after.rstrip()}\n"
        )
        if len(output.encode("utf-8")) > kwargs["max_output_bytes"]:
            from openviking.pyagfs.exceptions import AGFSResourceExhaustedError

            raise AGFSResourceExhaustedError("snapshot diff output size limit exceeded")
        return output


def _request_context() -> RequestContext:
    return RequestContext(
        user=UserIdentifier(account_id="account", user_id="user"),
        role=Role.ROOT,
    )


async def test_diff_rejects_files_over_size_limit(monkeypatch):
    monkeypatch.setattr(viking_fs_module, "SNAPSHOT_DIFF_MAX_FILE_BYTES", 3)
    vfs = _DiffVikingFS(b"old\n", b"new\n")

    with pytest.raises(ResourceExhaustedError, match="file size limit"):
        await VikingFS.diff(
            vfs,
            path="viking://user/user/memories/experiences/example.md",
            from_ref="from",
            to_ref="to",
            ctx=_request_context(),
        )


async def test_diff_passes_file_size_limit_to_blob_reads(monkeypatch):
    monkeypatch.setattr(viking_fs_module, "SNAPSHOT_DIFF_MAX_FILE_BYTES", 123)
    vfs = _DiffVikingFS(b"old\n", b"new\n")

    await VikingFS.diff(
        vfs,
        path="viking://user/user/memories/experiences/example.md",
        from_ref="from",
        to_ref="to",
        ctx=_request_context(),
    )

    assert vfs.blob_read_limits == [123, 123]


async def test_diff_checks_access_before_reading_snapshot_content():
    path = "viking://user/other-user/memories/private.md"
    ctx = RequestContext(
        user=UserIdentifier(account_id="account", user_id="user"),
        role=Role.USER,
    )
    vfs = object.__new__(VikingFS)
    vfs.acl_manager = None
    show_calls = []

    async def show(*args, **kwargs):
        show_calls.append((args, kwargs))
        return {"oid": "a" * 40}

    vfs.show = show

    with pytest.raises(PermissionDeniedError):
        await VikingFS.diff(
            vfs,
            path=path,
            from_ref="from",
            to_ref="to",
            ctx=ctx,
        )

    assert show_calls == []


async def test_diff_rejects_excessive_line_count_before_building_diff(monkeypatch):
    monkeypatch.setattr(viking_fs_module, "SNAPSHOT_DIFF_MAX_LINES", 2, raising=False)
    vfs = _DiffVikingFS(b"a\nb\nc\n", b"a\nb\nd\n")

    with pytest.raises(ResourceExhaustedError, match="line count limit"):
        await VikingFS.diff(
            vfs,
            path="viking://user/user/memories/experiences/example.md",
            from_ref="from",
            to_ref="to",
            ctx=_request_context(),
        )


@pytest.mark.parametrize(
    "text",
    [
        "",
        "one line",
        "one line\n",
        "one\r\ntwo\r\n",
        "one\rtwo",
        "one\u2028two\u2029",
        "\n\n",
    ],
)
async def test_snapshot_line_count_matches_splitlines(text):
    assert viking_fs_module._snapshot_line_count(text) == len(text.splitlines())


async def test_diff_rejects_output_over_size_limit(monkeypatch):
    monkeypatch.setattr(viking_fs_module, "SNAPSHOT_DIFF_MAX_FILE_BYTES", 1024)
    monkeypatch.setattr(viking_fs_module, "SNAPSHOT_DIFF_MAX_OUTPUT_BYTES", 16)
    vfs = _DiffVikingFS(b"old\n", b"new\n")

    with pytest.raises(ResourceExhaustedError, match="output size limit"):
        await VikingFS.diff(
            vfs,
            path="viking://user/user/memories/experiences/example.md",
            from_ref="from",
            to_ref="to",
            ctx=_request_context(),
        )


async def test_diff_uses_bounded_native_diff_builder():
    vfs = _DiffVikingFS(b"old\n", b"new\n")

    result = await VikingFS.diff(
        vfs,
        path="viking://user/user/memories/experiences/example.md",
        from_ref="from",
        to_ref="to",
        ctx=_request_context(),
    )

    assert vfs._async_agfs.calls[-1:] == [
        (
            "git_diff_text",
            {
                "before": "old\n",
                "after": "new\n",
                "fromfile": ("viking://user/user/memories/experiences/example.md@from"),
                "tofile": "viking://user/user/memories/experiences/example.md@to",
                "timeout_ms": viking_fs_module.SNAPSHOT_DIFF_TIMEOUT_MS,
                "max_output_bytes": viking_fs_module.SNAPSHOT_DIFF_MAX_OUTPUT_BYTES,
            },
        )
    ]
    assert "-old" in result["diff_text"]
    assert "+new" in result["diff_text"]


async def test_diff_treats_only_missing_tree_path_as_absent():
    vfs = _DiffVikingFS(b"old\n", b"")
    vfs._async_agfs.blob_errors["to"] = AGFSPathNotFoundError("path not found in tree")

    result = await VikingFS.diff(
        vfs,
        path="viking://user/user/memories/experiences/example.md",
        from_ref="from",
        to_ref="to",
        ctx=_request_context(),
    )

    assert result["change_type"] == "deleted"


async def test_diff_does_not_treat_missing_storage_object_as_absent():
    vfs = _DiffVikingFS(b"old\n", b"")
    vfs._async_agfs.blob_errors["to"] = AGFSNotFoundError("object not found: deadbeef")

    with pytest.raises(AGFSNotFoundError, match="object not found"):
        await VikingFS.diff(
            vfs,
            path="viking://user/user/memories/experiences/example.md",
            from_ref="from",
            to_ref="to",
            ctx=_request_context(),
        )


def _ttl_metadata(expires_at: str = "2030-01-02T00:00:00.000Z") -> bytes:
    return json.dumps({"expires_at": expires_at}).encode()


class _RestoreAGFS:
    def __init__(self, *, plan, blobs, result=None, error=None, current=None):
        self.plan = plan
        self.blobs = blobs
        self.result = result
        self.error = error
        self.calls = []
        self.current = current or {}

    async def stat(self, path, **kwargs):
        self.calls.append(("stat", path))
        if path not in self.current:
            raise FileNotFoundError(path)
        return {"isDir": False}

    async def read(self, path, **kwargs):
        if path not in self.current:
            raise FileNotFoundError(path)
        return self.current[path]

    async def pathlock_acquire_tree(self, path):
        self.calls.append(("lock", path))
        return {"lease_ref": "restore"}

    async def pathlock_release(self, lease):
        self.calls.append(("unlock", lease))

    async def run(self, operation, **kwargs):
        self.calls.append((operation, kwargs))
        if operation == "git_show":
            if kwargs["path"] not in self.blobs:
                raise AGFSPathNotFoundError(kwargs["path"])
            data = self.blobs[kwargs["path"]]
            return {"oid": "b" * 40, "size": len(data), "bytes": data}
        assert operation == "git_restore"
        if kwargs.get("dry_run"):
            return self.plan
        if self.error is not None:
            raise self.error
        return self.result


def _restore_vfs(agfs):
    vfs = object.__new__(VikingFS)
    vfs._async_agfs = agfs
    vfs.ttl_registry = SimpleNamespace(mark_account=AsyncMock())
    vfs.acl_manager = None
    vfs.vector_store = None
    vfs._background_tasks = set()
    vfs._schedule_restore_reindex_for_paths = AsyncMock(return_value=None)
    return vfs


def _restore_plan(*, to_write=(), to_delete=()):
    return {
        "result": "dry_run",
        "source": "a" * 40,
        "head": "c" * 40,
        "diff": {
            "to_write": [
                {"path": path, "oid": str(index) * 40}
                for index, path in enumerate(to_write, start=1)
            ],
            "to_delete": list(to_delete),
            "unchanged": [],
        },
    }


@pytest.mark.parametrize("partial", [False, True])
async def test_restore_marks_account_before_writeback_and_preserves_partial_error(partial):
    event_path = "user/user/memories/events/2026/09/28/.ttl.json"
    session_meta = "user/user/sessions/s1/.meta.json"
    result = {
        "result": "applied",
        "written_paths": [event_path, session_meta],
        "deleted_paths": [],
    }
    error = (
        GitRestoreWritebackPartialError(
            "partial",
            {
                "written_paths": [event_path],
                "deleted_paths": [],
                "failed_writes": [(session_meta, "injected")],
            },
        )
        if partial
        else None
    )
    agfs = _RestoreAGFS(
        plan=_restore_plan(
            to_write=(
                event_path,
                session_meta,
                *(f"user/user/sessions/s1/{i}.txt" for i in range(20)),
            )
        ),
        blobs={event_path: _ttl_metadata(), session_meta: _ttl_metadata()},
        result=result,
        error=error,
    )
    vfs = _restore_vfs(agfs)

    async def mark_account(account_id):
        assert account_id == "account"
        assert not any(
            op == "git_restore" and not args.get("dry_run")
            for op, args in agfs.calls
            if isinstance(args, dict)
        )

    vfs.ttl_registry.mark_account.side_effect = mark_account
    if partial:
        with pytest.raises(GitRestoreWritebackPartialError):
            await vfs.restore(source_commit="source", ctx=_request_context())
    else:
        await vfs.restore(source_commit="source", ctx=_request_context())
    assert vfs.ttl_registry.mark_account.await_count == 2
    assert len([op for op, _ in agfs.calls if op == "stat"]) == 2
    assert (
        next(args for op, args in agfs.calls if op == "git_restore" and not args.get("dry_run"))[
            "source_commit"
        ]
        == "a" * 40
    )


@pytest.mark.parametrize("scope", ["event", "session"])
@pytest.mark.parametrize(
    "entry,delete,expires_at",
    [
        ("metadata", False, "2040-01-01T00:00:00Z"),
        ("metadata", True, "2040-01-01T00:00:00Z"),
        ("body", False, "2000-01-01T00:00:00Z"),
        ("body", False, "2040-01-01T00:00:00Z"),
    ],
)
async def test_restore_cannot_detach_existing_expiry(scope, entry, delete, expires_at):
    owner = (
        "user/user/sessions/s1" if scope == "session" else "user/user/memories/events/2026/09/28"
    )
    # Exercise legacy event metadata as well as the current .meta.json format.
    metadata_path = owner + (
        "/.ttl.json" if scope == "event" and entry == "metadata" else "/.meta.json"
    )
    path = (
        metadata_path
        if entry == "metadata"
        else owner + ("/messages.jsonl" if scope == "session" else "/body.md")
    )
    current = {"/local/account/" + metadata_path: _ttl_metadata(expires_at)}
    agfs = _RestoreAGFS(
        plan=_restore_plan(**{"to_delete" if delete else "to_write": (path,)}),
        blobs={path: _ttl_metadata() if entry == "metadata" else b"old unmanaged body"},
        current=current,
    )
    vfs = _restore_vfs(agfs)
    expected = NotFoundError if expires_at.startswith("2000") else ConflictError
    with pytest.raises(expected):
        await vfs.restore(source_commit="source", ctx=_request_context())
    assert agfs.current == current
    vfs.ttl_registry.mark_account.assert_not_awaited()
    assert not any(
        op == "git_restore" and not args.get("dry_run")
        for op, args in agfs.calls
        if isinstance(args, dict)
    )


async def test_restore_dry_run_does_not_write_account_marker():
    path = "user/user/memories/events/2026/09/28/.ttl.json"
    agfs = _RestoreAGFS(plan=_restore_plan(to_write=(path,)), blobs={path: _ttl_metadata()})
    vfs = _restore_vfs(agfs)
    result = await vfs.restore(source_commit="source", dry_run=True, ctx=_request_context())
    assert result["result"] == "dry_run"
    vfs.ttl_registry.mark_account.assert_not_awaited()
    assert [call[0] for call in agfs.calls] == ["git_restore"]


@pytest.mark.parametrize("operation", ["show", "show_blob_raw", "diff"])
@pytest.mark.parametrize("scope", ["event", "session"])
@pytest.mark.parametrize("live_expiry", [None, "2000-01-01T00:00:00Z", "", "2040-01-01T00:00:00Z"])
async def test_snapshot_reads_enforce_historical_and_current_expiry(operation, scope, live_expiry):
    from openviking.core import ttl
    from openviking.storage.ttl_registry import TTLRegistry
    from tests.unit.storage.ttl_test_storage import MemoryAGFS

    fs = VikingFS(agfs=SimpleNamespace())
    agfs = MemoryAGFS()
    fs._async_agfs = agfs
    fs.ttl_registry = TTLRegistry(agfs)
    ctx = _request_context()
    paths = {
        "event": "viking://user/user/memories/events/2026/09/28/snapshot.md",
        "session": "viking://user/user/sessions/s1/messages.jsonl",
    }
    uri = paths[scope]
    kind = scope
    owner = uri.rsplit("/", 1)[0]
    meta_uri = ttl.ttl_metadata_uri(kind, owner)
    fields = {"expires_at": "2000-01-01T00:00:00Z"}
    metadata = json.dumps(fields).encode()
    blobs = {
        uri.removeprefix("viking://"): b"expired body",
        meta_uri.removeprefix("viking://"): metadata,
    }
    if live_expiry is not None:
        await fs.write_file(meta_uri, json.dumps({"expires_at": live_expiry}), ctx=ctx)
    if live_expiry == "2000-01-01T00:00:00Z":
        # Even a snapshot predating TTL adoption must obey the current fence.
        blobs = {uri.removeprefix("viking://"): b"old unmanaged body"}

    async def run(operation, **kwargs):
        if operation == "git_diff_text":
            return kwargs["after"]
        assert operation == "git_show"
        path = kwargs.get("path")
        if path is None:
            return {"oid": "a" * 40}
        if path not in blobs:
            raise AGFSPathNotFoundError(path)
        value = blobs[path]
        return {"oid": "blob", "bytes": value, "size": len(value)}

    agfs.run = run
    from contextlib import nullcontext

    expired = live_expiry is None or live_expiry == "2000-01-01T00:00:00Z"
    with pytest.raises(NotFoundError) if expired else nullcontext():
        if operation == "diff":
            await fs.diff(path=uri, from_ref=None, to_ref="main", ctx=ctx)
        else:
            await getattr(fs, operation)("main", path=uri, ctx=ctx)
