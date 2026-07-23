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
            self._async_agfs = _RecordingDiffAGFS()
            self.blob_refs = []

        def _ctx_or_default(self, ctx):
            return ctx

        def _ensure_access(self, uri, ctx):
            return None

        async def show(self, target_ref, *, path=None, ctx=None, max_blob_bytes=None):
            if path is None:
                return {"oid": from_oid if target_ref == "base" else to_oid}

            self.blob_refs.append(target_ref)
            contents = {
                from_oid: b"old content\n",
                to_oid: b"new content\n",
                "base": b"moved base content\n",
                "main": b"moved main content\n",
            }
            return contents[target_ref]

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

    assert vfs.blob_refs == [from_oid, to_oid]
    assert result["from_commit"] == from_oid
    assert result["to_commit"] == to_oid
    assert "-old content" in result["diff_text"]
    assert "+new content" in result["diff_text"]


class _DiffVikingFS:
    def __init__(self, before: bytes, after: bytes):
        self._before = before
        self._after = after
        self._async_agfs = _RecordingDiffAGFS()
        self.blob_read_limits = []
        self.access_checks = []

    def _ctx_or_default(self, ctx):
        return ctx

    def _ensure_access(self, uri, ctx):
        self.access_checks.append((uri, ctx))

    async def show(self, target_ref, *, path=None, ctx=None, max_blob_bytes=None):
        if path is None:
            return {"oid": target_ref}
        self.blob_read_limits.append(max_blob_bytes)
        return self._before if target_ref == "from" else self._after


class _RecordingDiffAGFS:
    def __init__(self):
        self.calls = []

    async def run(self, operation, **kwargs):
        self.calls.append((operation, kwargs))
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

    assert vfs._async_agfs.calls == [
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
    class MissingPathVikingFS(_DiffVikingFS):
        async def show(self, target_ref, *, path=None, ctx=None, max_blob_bytes=None):
            if path is None:
                return {"oid": target_ref}
            if target_ref == "to":
                raise AGFSPathNotFoundError("path not found in tree")
            return self._before

    result = await VikingFS.diff(
        MissingPathVikingFS(b"old\n", b""),
        path="viking://user/user/memories/experiences/example.md",
        from_ref="from",
        to_ref="to",
        ctx=_request_context(),
    )

    assert result["change_type"] == "deleted"


async def test_diff_does_not_treat_missing_storage_object_as_absent():
    class MissingObjectVikingFS(_DiffVikingFS):
        async def show(self, target_ref, *, path=None, ctx=None, max_blob_bytes=None):
            if path is None:
                return {"oid": target_ref}
            if target_ref == "to":
                raise AGFSNotFoundError("object not found: deadbeef")
            return self._before

    with pytest.raises(AGFSNotFoundError, match="object not found"):
        await VikingFS.diff(
            MissingObjectVikingFS(b"old\n", b""),
            path="viking://user/user/memories/experiences/example.md",
            from_ref="from",
            to_ref="to",
            ctx=_request_context(),
        )
