import asyncio
import importlib.util
from pathlib import Path

import pytest


def load_profiler_module():
    path = Path(__file__).parents[2] / "benchmark/custom/ingest_profile.py"
    assert path.exists(), "ingest profiler has not been implemented"
    spec = importlib.util.spec_from_file_location("ingest_profile", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_profiler():
    return load_profiler_module().Profiler()


def test_zip_fixture_preserves_repo_marker_and_bytes(tmp_path):
    import zipfile

    module = load_profiler_module()
    source = tmp_path / "source"
    source.mkdir()
    (source / ".git").mkdir()
    (source / ".git" / "HEAD").write_text("ref: refs/heads/main\n")
    (source / "a.py").write_bytes(b"def add(a, b): return a + b\n")
    archive = module.pack_fixture(source, tmp_path / "source.zip")
    with zipfile.ZipFile(archive) as zipped:
        assert zipped.read("source/a.py") == (source / "a.py").read_bytes()
        assert "source/.git/HEAD" in zipped.namelist()


@pytest.mark.asyncio
async def test_nested_spans_preserve_results_and_label_io():
    profiler = load_profiler()

    class Worker:
        async def io(self, method, *args, **kwargs):
            return b"secret-content"

        async def stage(self):
            return await self.io("read", "/private/path", token="private-token")

    profiler.patch(Worker, "stage", "parse")
    profiler.patch(Worker, "io", "io", io=True)
    profiler.begin("noop")
    assert await Worker().stage() == b"secret-content"
    result = profiler.finish()
    assert result["stages"]["parse"]["calls"] == 1
    assert result["io"]["parse:read"]["calls"] == 1
    assert result["io"]["parse:read"]["bytes"] == 14
    assert "secret-content" not in str(result)
    assert "private-token" not in str(result)
    profiler.restore()


@pytest.mark.asyncio
async def test_staticmethod_binding_is_preserved():
    profiler = load_profiler()

    class Worker:
        @staticmethod
        async def lock(*, timeout):
            return timeout

    profiler.patch(Worker, "lock", "lock")
    profiler.begin("lock")
    assert await Worker().lock(timeout=3) == 3
    profiler.finish()
    profiler.restore()
    assert isinstance(vars(Worker)["lock"], staticmethod)


@pytest.mark.asyncio
async def test_failures_are_recorded_and_propagated():
    profiler = load_profiler()

    class Worker:
        async def stage(self):
            raise ValueError("do not swallow")

    original = Worker.stage
    profiler.patch(Worker, "stage", "parse")
    profiler.begin("failed")
    with pytest.raises(ValueError, match="do not swallow"):
        await Worker().stage()
    result = profiler.finish()
    assert result["stages"]["parse"]["errors"] == 1
    assert result["events"][0]["exception_type"] == "ValueError"
    profiler.restore()
    assert Worker.stage is original


@pytest.mark.asyncio
async def test_parallel_spans_report_union_not_sum():
    profiler = load_profiler()
    assert profiler.union_seconds([(0, 3), (1, 2), (2, 5), (6, 7)]) == 6

    class Worker:
        async def stage(self):
            await asyncio.sleep(0.01)

    profiler.patch(Worker, "stage", "parse")
    profiler.begin("parallel")
    await asyncio.gather(Worker().stage(), Worker().stage())
    result = profiler.finish()
    assert result["stages"]["parse"]["calls"] == 2
    assert result["stages"]["parse"]["sum_s"] > result["stages"]["parse"]["wall_union_s"]
    profiler.restore()
