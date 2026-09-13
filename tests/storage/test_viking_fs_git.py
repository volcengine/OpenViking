# tests/storage/test_viking_fs_git.py
import pytest

from openviking.pyagfs.exceptions import AGFSNotFoundError, AGFSPathNotFoundError
from openviking.server.identity import RequestContext, Role
from openviking.storage import viking_fs as viking_fs_module
from openviking.storage.viking_fs import VikingFS
from openviking_cli.exceptions import PermissionDeniedError, ResourceExhaustedError
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
                "fromfile": (
                    "viking://user/user/memories/experiences/example.md@from"
                ),
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


async def test_commit_locks_nearest_existing_ancestor_for_deleted_paths():
    """A deletion snapshot must not tree-lock the deleted path itself.

    Tree locks live at ``{path}/.path.ovlock``, so locking a path that was just
    deleted recreates it as an empty directory. Lock the closest surviving
    ancestor instead; the commit still records the original path.
    """

    class RecordingAGFS:
        def __init__(self, existing):
            self.existing = existing
            self.locked = None
            self.commits = []
            self.released = []

        async def stat(self, path):
            if path not in self.existing:
                raise AGFSPathNotFoundError(path)
            return {"path": path, "is_dir": True}

        async def pathlock_acquire_tree_batch(self, paths):
            self.locked = list(paths)
            return "lease-1"

        async def pathlock_release(self, lease):
            self.released.append(lease)

        async def run(self, operation, **kwargs):
            self.commits.append((operation, kwargs))
            return {"result": "created", "commit_oid": "b" * 40}

    class CommitVikingFS:
        _DEFAULT_GIT_AUTHOR_NAME = "bot"
        _DEFAULT_GIT_AUTHOR_EMAIL = "bot@example.com"

        def __init__(self, existing):
            self._async_agfs = RecordingAGFS(existing)

        def _ctx_or_default(self, ctx):
            return ctx

        def _uri_to_tree_path(self, uri, *, ctx):
            return uri.removeprefix("viking://")

        def _uri_to_path(self, uri, *, ctx):
            return "/acct/" + uri.removeprefix("viking://")

        async def _snapshot_scope_uris(self, paths, ctx):
            return list(paths)

        async def _ensure_access_many(self, uris, ctx, *, action):
            return None

    deleted = "viking://user/alice/memories/experiences/example.md"
    vfs = CommitVikingFS({"/acct/user/alice/memories/experiences"})
    ctx = RequestContext(
        user=UserIdentifier(account_id="account", user_id="alice"),
        role=Role.USER,
    )

    result = await VikingFS.commit(vfs, message="record deletion", paths=[deleted], ctx=ctx)

    assert result["result"] == "created"
    # Locked the surviving parent, never the deleted file path.
    assert vfs._async_agfs.locked == ["/acct/user/alice/memories/experiences"]
    # The deletion itself is still part of the commit.
    assert vfs._async_agfs.commits[0][1]["paths"] == [
        "user/alice/memories/experiences/example.md"
    ]
    assert vfs._async_agfs.released == ["lease-1"]


async def test_commit_locks_the_path_itself_when_it_still_exists():
    class RecordingAGFS:
        def __init__(self, existing):
            self.existing = existing
            self.locked = None

        async def stat(self, path):
            if path not in self.existing:
                raise AGFSPathNotFoundError(path)
            return {"path": path, "is_dir": False}

        async def pathlock_acquire_tree_batch(self, paths):
            self.locked = list(paths)
            return "lease-1"

        async def pathlock_release(self, lease):
            return None

        async def run(self, operation, **kwargs):
            return {"result": "created", "commit_oid": "c" * 40}

    class CommitVikingFS:
        _DEFAULT_GIT_AUTHOR_NAME = "bot"
        _DEFAULT_GIT_AUTHOR_EMAIL = "bot@example.com"

        def __init__(self, existing):
            self._async_agfs = RecordingAGFS(existing)

        def _ctx_or_default(self, ctx):
            return ctx

        def _uri_to_tree_path(self, uri, *, ctx):
            return uri.removeprefix("viking://")

        def _uri_to_path(self, uri, *, ctx):
            return "/acct/" + uri.removeprefix("viking://")

        async def _snapshot_scope_uris(self, paths, ctx):
            return list(paths)

        async def _ensure_access_many(self, uris, ctx, *, action):
            return None

    uri = "viking://user/alice/memories/experiences/example.md"
    path = "/acct/user/alice/memories/experiences/example.md"
    vfs = CommitVikingFS({path})
    ctx = RequestContext(
        user=UserIdentifier(account_id="account", user_id="alice"),
        role=Role.USER,
    )

    await VikingFS.commit(vfs, message="create", paths=[uri], ctx=ctx)

    assert vfs._async_agfs.locked == [path]
