"""State publication must preserve complete values without remote shell round trips."""

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from vikingbot.compile.pipeline_io import TaskFiles
from vikingbot.config.schema import Config
from vikingbot.sandbox.backends.direct import DirectBackend
from vikingbot.sandbox.backends.opensandbox import OpenSandboxBackend


def remote_backend(tmp_path):
    values = {}

    async def write(path, value, **kwargs):
        values[path] = value.encode() if isinstance(value, str) else value

    async def move(entries):
        for entry in entries:
            values[entry.dest] = values.pop(entry.src)

    async def read(path, **kwargs):
        if path not in values:
            raise FileNotFoundError(path)

        async def chunks():
            yield values[path][:3]
            yield values[path][3:]

        return chunks()

    config = Config(storage_workspace=str(tmp_path), sandbox={"backend": "opensandbox"})
    backend = OpenSandboxBackend(config.sandbox, "state", tmp_path / "not-mounted")
    backend._sandbox = SimpleNamespace(
        files=SimpleNamespace(
            write_file=AsyncMock(side_effect=write),
            move_files=AsyncMock(side_effect=move),
            read_bytes_stream=AsyncMock(side_effect=read),
        )
    )
    backend.execute = AsyncMock(side_effect=AssertionError("State writes must not execute shell"))
    return backend, values


@pytest.mark.asyncio
async def test_remote_state_replaces_existing_value_without_host_paths_or_exec(tmp_path):
    backend, values = remote_backend(tmp_path)
    files = TaskFiles(backend)
    assert await files.get("runtime") is None
    await files.put("runtime", {"generation": 1})
    await files.put("runtime", {"generation": 2, "facts": ["complete", "证据"]})
    assert await files.get("runtime") == {"generation": 2, "facts": ["complete", "证据"]}
    assert list(values) == ["/workspace/__compile_staging__/pipeline/runtime.json"]
    assert backend.local_file_path("runtime") is None
    assert not backend.workspace.exists()
    backend.execute.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [OSError, asyncio.CancelledError])
async def test_failed_or_cancelled_publication_preserves_previous_value(tmp_path, failure):
    backend, values = remote_backend(tmp_path)
    files = TaskFiles(backend)
    await files.put("runtime", {"generation": 1})
    backend._sandbox.files.move_files.side_effect = failure("publication interrupted")
    with pytest.raises(failure):
        await files.put("runtime", {"generation": 2})
    assert await files.get("runtime") == {"generation": 1}
    assert len([path for path in values if path.endswith(".tmp")]) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("source", "destination"),
    [("../temp", "state"), ("/temp", "state"), ("a/temp", "b/state"), ("temp", "")],
)
async def test_remote_replacement_rejects_paths_before_file_api(tmp_path, source, destination):
    backend, _ = remote_backend(tmp_path)
    with pytest.raises(ValueError):
        await backend.replace_file(source, destination)
    backend._sandbox.files.move_files.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("legacy_remote", [False, True])
async def test_local_and_legacy_remote_state_keep_complete_values(tmp_path, legacy_remote):
    config = Config(storage_workspace=str(tmp_path))
    backend = DirectBackend(config.sandbox, "state", tmp_path / "space in workspace")
    await backend.start()
    if legacy_remote:
        backend.local_file_path = lambda _path: None
        backend.replace_file = None
        backend.execute = AsyncMock(wraps=backend.execute)
    else:
        backend.execute = AsyncMock(side_effect=AssertionError("Local rename needs no shell"))
    try:
        files = TaskFiles(backend)
        await files.put("nested/state", {"generation": 1})
        await files.put("nested/state", {"generation": 2})
        if legacy_remote:
            saved = backend.workspace / "__compile_staging__/pipeline/nested/state.json"
            assert json.loads(saved.read_text()) == {"generation": 2}
            assert backend.execute.await_count == 2
        else:
            assert await files.get("nested/state") == {"generation": 2}
            backend.execute.assert_not_awaited()
    finally:
        await backend.stop()
