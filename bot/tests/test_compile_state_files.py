"""New shards avoid command sessions; overwrites retain complete atomic publication."""

import asyncio
import shlex
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from vikingbot.compile.pipeline_io import TaskFiles
from vikingbot.config.schema import Config
from vikingbot.sandbox.backends.direct import DirectBackend
from vikingbot.sandbox.backends.opensandbox import OpenSandboxBackend


def remote_backend(tmp_path):
    from opensandbox.exceptions import SandboxApiException

    values = {}

    async def write(path, value, **kwargs):
        values[path] = value.encode() if isinstance(value, str) else value

    async def move(entries):
        for entry in entries:
            if entry.dest in values:
                raise SandboxApiException("Destination already exists", status_code=500)
            values[entry.dest] = values.pop(entry.src)

    async def execute(command, **kwargs):
        _, _, _, source, destination = shlex.split(command)
        values[destination] = values.pop(source)
        return "(no output)"

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
    backend.execute = AsyncMock(side_effect=execute)
    return backend, values


@pytest.mark.asyncio
async def test_new_remote_shards_publish_without_host_paths_or_exec(tmp_path):
    backend, values = remote_backend(tmp_path)
    files = TaskFiles(backend)
    assert await files.get("runtime") is None
    await files.put("runtime", {"generation": 1})
    await files.put("calls/map-1", {"facts": ["complete", "证据"]})
    assert await files.get("calls/map-1") == {"facts": ["complete", "证据"]}
    assert len(values) == 2
    assert backend.local_file_path("runtime") is None
    assert not backend.workspace.exists()
    backend.execute.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("recovered", [False, True])
async def test_overwrites_use_atomic_fallback_with_no_clobber_file_api(tmp_path, recovered):
    backend, values = remote_backend(tmp_path)
    files = TaskFiles(backend)
    await files.put("runtime", {"generation": 1})
    if recovered:
        backend._published_paths.clear()
    await files.put("runtime", {"generation": 2})
    assert await files.get("runtime") == {"generation": 2}
    assert len(values) == 1
    assert backend.execute.await_count == 1
    assert backend._sandbox.files.move_files.await_count == (2 if recovered else 1)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("failure", "overwrite"), [(OSError, False), (asyncio.CancelledError, True)]
)
async def test_failed_or_cancelled_publication_preserves_previous_value(
    tmp_path, failure, overwrite
):
    backend, values = remote_backend(tmp_path)
    files = TaskFiles(backend)
    await files.put("runtime", {"generation": 1})
    operation = backend.execute if overwrite else backend._sandbox.files.move_files
    operation.side_effect = failure("publication interrupted")
    with pytest.raises(failure):
        await files.put("runtime" if overwrite else "new", {"generation": 2})
    assert await files.get("runtime") == {"generation": 1}
    assert len([path for path in values if path.endswith(".tmp")]) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("source", "destination"),
    [("../temp", "state"), ("a/temp", "b/state")],
)
async def test_remote_replacement_rejects_paths_before_file_api(tmp_path, source, destination):
    backend, _ = remote_backend(tmp_path)
    with pytest.raises(ValueError):
        await backend.replace_file(source, destination)
    backend._sandbox.files.move_files.assert_not_awaited()


@pytest.mark.asyncio
async def test_local_state_uses_native_replacement(tmp_path):
    config = Config(storage_workspace=str(tmp_path))
    backend = DirectBackend(config.sandbox, "state", tmp_path / "space in workspace")
    await backend.start()
    backend.execute = AsyncMock(side_effect=AssertionError("Local rename needs no shell"))
    try:
        files = TaskFiles(backend)
        await files.put("nested/state", {"generation": 1})
        await files.put("nested/state", {"generation": 2})
        assert await files.get("nested/state") == {"generation": 2}
        backend.execute.assert_not_awaited()
    finally:
        await backend.stop()
