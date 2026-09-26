import hashlib
import json

import pytest

from openviking.resource.dingtalk_import import DINGTALK_SYNC_SIDECAR
from openviking.resource.dingtalk_incremental import (
    mark_complete,
    previous_state,
    processing_key,
)


class _MemoryFS:
    def __init__(self, target, state, files=None):
        self.target = target
        self.files = {
            f"{target}/{DINGTALK_SYNC_SIDECAR}": json.dumps(state).encode(),
            **{f"{target}/{path}": value for path, value in (files or {}).items()},
        }
        self._async_agfs = self
        self.writes = []

    async def exists(self, uri, ctx=None):
        return uri in self.files

    async def read_file(self, uri, ctx=None):
        return self.files[uri].decode()

    async def read_file_bytes(self, uri, ctx=None):
        return self.files[uri]

    async def write_file(self, uri, content, ctx=None, lease_ref=None):
        self.files[uri] = content.encode() if isinstance(content, str) else content
        self.writes.append((uri, lease_ref))

    async def tree(self, target, **kwargs):
        prefix = target.rstrip("/") + "/"
        return [
            {"uri": uri, "rel_path": uri[len(prefix) :], "isDir": False}
            for uri in sorted(self.files)
            if uri.startswith(prefix)
        ]

    def _uri_to_path(self, uri, ctx=None):
        return uri.replace("viking://", "/")

    async def pathlock_acquire_tree(self, path):
        return {"path": path}

    async def pathlock_release(self, lock):
        return None

    def state(self):
        return json.loads(self.files[f"{self.target}/{DINGTALK_SYNC_SIDECAR}"])


def _state(*, complete=True, key="key-a", run_id="run-a", content=b"body"):
    return {
        "manifest": [],
        "run_id": run_id,
        "processing_key": key,
        "complete": complete,
        "artifacts": {"doc.md": hashlib.sha256(content).hexdigest()},
    }


@pytest.mark.asyncio
async def test_previous_state_reuses_matching_complete_artifacts():
    target = "viking://resources/dingtalk"
    state = _state()
    fs = _MemoryFS(target, state, {"doc.md": b"body"})

    assert await previous_state(fs, target, "key-a", None) == state


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("complete", "stored_key", "requested_key"),
    [(False, "key-a", "key-a"), (True, "key-a", "key-b")],
)
async def test_previous_state_rejects_incomplete_or_different_processing_key(
    complete, stored_key, requested_key
):
    target = "viking://resources/dingtalk"
    fs = _MemoryFS(target, _state(complete=complete, key=stored_key), {"doc.md": b"body"})

    assert await previous_state(fs, target, requested_key, None) == {}


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["missing", "modified"])
async def test_previous_state_rejects_missing_or_modified_local_artifact(change):
    target = "viking://resources/dingtalk"
    fs = _MemoryFS(target, _state(), {"doc.md": b"body"})
    if change == "missing":
        del fs.files[f"{target}/doc.md"]
    else:
        fs.files[f"{target}/doc.md"] = b"changed"

    assert await previous_state(fs, target, "key-a", None) == {}


@pytest.mark.asyncio
async def test_completion_updates_only_the_matching_run():
    target = "viking://resources/dingtalk"
    fs = _MemoryFS(target, _state(complete=False, run_id="run-current"), {"doc.md": b"body"})

    await mark_complete(fs, target, "run-old", None)
    assert fs.state()["complete"] is False
    assert fs.writes == []

    await mark_complete(fs, target, "run-current", None)
    assert fs.state()["complete"] is True
    assert fs.state()["run_id"] == "run-current"
    assert await previous_state(fs, target, "key-a", None) == fs.state()


def test_processing_key_covers_source_options_and_processing_settings(monkeypatch):
    class _Config:
        def __init__(self, embedding):
            self.embedding = embedding

        def model_dump(self, *, mode, include):
            assert mode == "json"
            assert {"embedding", "semantic", "dingtalk"} <= include
            return {"embedding": self.embedding}

    config = _Config("embed-a")
    monkeypatch.setattr(
        "openviking.resource.dingtalk_incremental.get_openviking_config", lambda: config
    )
    baseline = processing_key("https://alidocs.dingtalk.com/i/nodes/A", {"summarize": True})

    assert baseline == processing_key(
        "https://alidocs.dingtalk.com/i/nodes/A",
        {
            "summarize": True,
            "request_validator": object(),
            "_dingtalk_internal": "transient",
        },
    )
    assert baseline == processing_key(
        "https://alidocs.dingtalk.com/i/nodes/A",
        {
            "summarize": True,
            "strict": False,
            "source_name": None,
            "ignore_dirs": None,
            "include": None,
            "exclude": None,
            "directly_upload_media": True,
        },
    )

    assert baseline != processing_key("https://alidocs.dingtalk.com/i/nodes/B", {"summarize": True})
    assert baseline != processing_key(
        "https://alidocs.dingtalk.com/i/nodes/A", {"summarize": False}
    )
    config.embedding = "embed-b"
    assert baseline != processing_key("https://alidocs.dingtalk.com/i/nodes/A", {"summarize": True})
