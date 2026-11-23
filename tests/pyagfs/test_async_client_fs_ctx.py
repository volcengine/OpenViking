from __future__ import annotations

from typing import Any

import pytest

from openviking.pyagfs.async_client import AsyncAGFSClient
from openviking.pyagfs.helpers import cp
from openviking.storage.errors import InvalidPathLockRequestError


class _RecordingClient:
    """Record ctx values received by the sync AGFS surface."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str, dict[str, str] | None]] = []
        self.list_options: dict[str, Any] = {}

    def write(self, path: str, data: bytes, *, ctx: dict[str, str] | None = None) -> str:
        """Record write ctx and return a stable fake backend id."""
        self.calls.append(("write", path, ctx))
        return "written"

    def read(self, path: str, *, ctx: dict[str, str] | None = None, **_: Any) -> bytes:
        """Record read ctx and return a stable fake payload."""
        self.calls.append(("read", path, ctx))
        return b"payload"

    def ls(
        self,
        path: str,
        *,
        ctx: dict[str, str] | None = None,
        **options: Any,
    ) -> list[dict[str, Any]]:
        """Record listing options and return an empty result."""
        self.calls.append(("ls", path, ctx))
        self.list_options = options
        return []


@pytest.mark.asyncio
async def test_async_client_derives_account_ctx_from_local_agfs_path() -> None:
    client = _RecordingClient()
    agfs = AsyncAGFSClient(client)

    await agfs.write("/local/acct-1/data/file.txt", b"x")
    await agfs.ls(
        "/local/acct-1/data",
        offset=2,
        limit=3,
        sort_by="mtime",
        sort_order="desc",
    )

    assert client.calls == [
        ("write", "/local/acct-1/data/file.txt", {"account_id": "acct-1"}),
        ("ls", "/local/acct-1/data", {"account_id": "acct-1"}),
    ]
    assert client.list_options == {
        "offset": 2,
        "limit": 3,
        "sort_by": "mtime",
        "sort_order": "desc",
    }


@pytest.mark.asyncio
async def test_async_client_uses_system_ctx_for_non_local_agfs_path() -> None:
    client = _RecordingClient()
    agfs = AsyncAGFSClient(client)

    await agfs.read("/queue/semantic/dequeue")

    assert client.calls == [("read", "/queue/semantic/dequeue", {"account_id": "_system"})]


@pytest.mark.asyncio
async def test_async_client_preserves_explicit_fs_ctx() -> None:
    client = _RecordingClient()
    agfs = AsyncAGFSClient(client)

    await agfs.write(
        "/local/path-account/data/file.txt", b"x", fs_ctx={"account_id": "ctx-account"}
    )

    assert client.calls == [
        ("write", "/local/path-account/data/file.txt", {"account_id": "ctx-account"})
    ]


@pytest.mark.asyncio
async def test_async_client_rejects_cross_account_transfer() -> None:
    client = _RecordingClient()
    agfs = AsyncAGFSClient(client)

    source, target = "/local/a/data/file.txt", "/local/b/data/file.txt"
    with pytest.raises(ValueError, match="cross-account"):
        await agfs.mv(source, target)
    with pytest.raises(ValueError, match="cross-account"):
        await agfs._copy_file(source, target, fs_ctx={"account_id": "a", "lease_ref": "lease"})


@pytest.mark.asyncio
async def test_async_client_types_native_invalid_pathlock_handoff() -> None:
    class _InvalidHandoffClient:
        def pathlock_adopt(self, ctx, handoff_ref):
            del ctx, handoff_ref
            raise ValueError(
                "invalid lock request: handoff coverage '/local/default/resources/"
                "信贷风险管理_1.md' does not map to lock path "
                "'/local/default/resources/.exact.ovlock.1.md.deadbeef'"
            )

    agfs = AsyncAGFSClient(_InvalidHandoffClient())

    with pytest.raises(InvalidPathLockRequestError) as exc_info:
        await agfs.pathlock_adopt({"owner_id": "producer"})

    assert "handoff coverage" in str(exc_info.value)
    assert isinstance(exc_info.value.__cause__, ValueError)


def test_cp_rejects_cross_account_raw_copy() -> None:
    class _CpClient:
        """Minimal cp client; methods must not be called after account guard fails."""

        def stat(self, path: str) -> dict[str, Any]:
            """Fail if cp checks storage before validating account boundaries."""
            raise AssertionError(f"unexpected stat call: {path}")

    with pytest.raises(ValueError, match="cross-account"):
        cp(_CpClient(), "/local/a/data/file.txt", "/local/b/data/file.txt")


def test_cp_rejects_cross_encryption_domain_raw_copy() -> None:
    class _CpClient:
        """Minimal cp client; methods must not be called after account guard fails."""

        def stat(self, path: str) -> dict[str, Any]:
            """Fail if cp checks storage before validating account boundaries."""
            raise AssertionError(f"unexpected stat call: {path}")

    with pytest.raises(ValueError, match="cross-account"):
        cp(_CpClient(), "/local/a/data/file.txt", "/mem/data/file.txt")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("options", "supports_fast_copy", "supports_streaming", "expected_io"),
    [
        pytest.param({}, True, True, ["read chunks", "write chunks"], id="default-streaming"),
        pytest.param(
            {"allow_same_mount_fast_path": True},
            True,
            True,
            ["raw copy"],
            id="raw-copy-opt-in",
        ),
        pytest.param(
            {"allow_same_mount_fast_path": True},
            False,
            True,
            ["raw copy", "read chunks", "write chunks"],
            id="raw-copy-declined",
        ),
        pytest.param(
            {},
            True,
            False,
            ["read chunks", "read buffer", "write buffer"],
            id="binding-without-streaming",
        ),
    ],
)
async def test_async_cp_copies_bytes_using_the_supported_io(
    options, supports_fast_copy, supports_streaming, expected_io
) -> None:
    source = "/local/acct/data/source.bin"
    target = "/local/acct/data/target.bin"
    fs_ctx = {"account_id": "acct", "lease_ref": "lease"}
    payload = b"firstsecond"
    files = {source: payload}
    operations = []
    metadata_reads = []

    class Client:
        def stat(self, path, *, ctx=None):
            assert ctx == fs_ctx
            metadata_reads.append(path)
            return {"isDir": path == "/local/acct/data"}

        def copy_within_mount(self, src, dst, *, ctx=None):
            assert ctx == fs_ctx
            operations.append("raw copy")
            if supports_fast_copy:
                files[dst] = files[src]
            return {"performed": supports_fast_copy}

        def cat(self, path, *, stream=False, ctx=None):
            assert ctx == fs_ctx
            if stream:
                operations.append("read chunks")
                if not supports_streaming:
                    raise RuntimeError("Streaming not supported in binding mode")
                return iter((files[path][:5], files[path][5:]))
            operations.append("read buffer")
            return files[path]

        def write(self, path, data, *, ctx=None):
            assert ctx == fs_ctx
            if isinstance(data, bytes):
                operations.append("write buffer")
                files[path] = data
            else:
                operations.append("write chunks")
                files[path] = b"".join(data)
            return path

    agfs = AsyncAGFSClient(Client())
    await agfs.cp(source, target, fs_ctx=fs_ctx, **options)

    assert files[target] == payload
    assert operations == expected_io

    if not options:
        del files[target]
        operations.clear()
        metadata_reads.clear()
        await agfs._copy_file(source, target, fs_ctx=fs_ctx)
        assert files[target] == payload
        assert operations == expected_io
        assert not metadata_reads
