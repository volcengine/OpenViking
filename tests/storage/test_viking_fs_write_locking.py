import pytest

from openviking.storage.viking_fs import VikingFS


class _FakeAGFS:
    """Minimal synchronous AGFS stub for VikingFS write-path tests."""

    def __init__(self):
        self.storage = {}

    def ensure_parent_dirs(self, path, ctx=None):
        """Pretend parent directories already exist."""
        return None

    def write(self, path, data, ctx=None):
        """Record writes and simulate a successful backend write."""
        self.storage[path] = data
        return path

    def read(self, path, ctx=None):
        """Return stored data or raise FileNotFoundError when missing."""
        if path not in self.storage:
            raise FileNotFoundError(path)
        return self.storage[path]


class _RecordingLockContext:
    """Record LockContext arguments so tests can assert dual-path locking."""

    calls = []

    def __init__(self, lock_manager, paths, lock_mode="exact", **kwargs):
        """Store lock paths and mode while ignoring unrelated details."""
        self.lock_manager = lock_manager
        self.paths = list(paths)
        self.lock_mode = lock_mode
        self.kwargs = kwargs

    async def __aenter__(self):
        """Record the call on context entry."""
        type(self).calls.append((self.paths, self.lock_mode, self.kwargs))
        return self

    async def __aexit__(self, exc_type, exc, tb):
        """Do not swallow exceptions on exit."""
        return False


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("method_name", "args", "expected_bytes"),
    [
        ("write_file", ("hello",), b"hello"),
        ("append_file", (" world",), b"hello world"),
    ],
)
async def test_encrypted_writes_lock_final_and_temp_paths(
    monkeypatch, method_name, args, expected_bytes
):
    """Encrypted writes must lock both the final file path and the hidden temp path."""
    import openviking.storage.transaction as transaction_module

    _RecordingLockContext.calls.clear()
    monkeypatch.setattr(transaction_module, "LockContext", _RecordingLockContext)
    monkeypatch.setattr(transaction_module, "get_lock_manager", lambda: object())

    agfs = _FakeAGFS()
    agfs.storage["/local/default/resources/note.md"] = b"hello"
    fs = VikingFS(agfs=agfs, encryptor=object())

    await getattr(fs, method_name)("viking://resources/note.md", *args)

    assert _RecordingLockContext.calls == [
        (
            [
                "/local/default/resources/note.md",
                "/local/default/temp/.encrypt_stage/571a25aab6e6bca05a60a6e4aec646389a9ac38237daf55ceda4f72f3d1b4afe.encrypt",
            ],
            "exact",
            {"handle": None},
        )
    ]
    assert agfs.storage["/local/default/resources/note.md"] == expected_bytes


@pytest.mark.parametrize(
    ("final_path", "temp_path"),
    [
        (
            "/local/default/resources/.abstract.md",
            "/local/default/temp/.encrypt_stage/1ea2c4fea9d85474a57fd03cac44d3bbe7c85fd6eb3c678c54044c4f49ecdbf7.encrypt",
        ),
        (
            "/local/default/resources/note.md",
            "/local/default/temp/.encrypt_stage/571a25aab6e6bca05a60a6e4aec646389a9ac38237daf55ceda4f72f3d1b4afe.encrypt",
        ),
        (
            "/s3/bucket/docs/a.md",
            "/s3/bucket/temp/.encrypt_stage/2a95445c0cdc88c4efdf32724651d61f72990a95d445fe0ce146150e39e5630d.encrypt",
        ),
        (
            "/note.md",
            "/temp/.encrypt_stage/71f3eca3f3ad54df082026dd6b40f2f3b2c2ba67b51bb5d24fda5632044c3228.encrypt",
        ),
        (
            ".abstract.md",
            "/temp/.encrypt_stage/2f3429bdbec8a484aec67cd1584e5dd64d8cf139d2fc4b4cca97d9cdc9b66ad3.encrypt",
        ),
    ],
)
def test_encrypted_temp_path_mapping_matches_rust(final_path, temp_path):
    """Keep Python temp-path mapping aligned with the Rust encryption wrapper."""
    fs = VikingFS(agfs=_FakeAGFS(), encryptor=object())
    assert fs._encrypted_temp_path(final_path) == temp_path
