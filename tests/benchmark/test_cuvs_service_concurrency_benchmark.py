import importlib.util
import sys
from pathlib import Path

import numpy as np
import pytest

MODULE_PATH = (
    Path(__file__).resolve().parents[2]
    / "benchmark"
    / "cuvs"
    / "run_service_concurrency_benchmark.py"
)
SPEC = importlib.util.spec_from_file_location("run_cuvs_service_concurrency_benchmark", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
benchmark = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = benchmark
SPEC.loader.exec_module(benchmark)


def test_unique_filters_are_distinct_and_keep_enough_candidates():
    filters = [benchmark.unique_filter(index, 100_000, 10_000) for index in range(32)]

    assert len({(item["gte"], item["lt"]) for item in filters}) == 32
    assert all(item["lt"] - item["gte"] == 100 for item in filters)
    assert all(0 <= item["gte"] < item["lt"] <= 100_000 for item in filters)


def test_service_record_contains_tenant_and_filter_fields():
    record = benchmark.make_record(1001, [1.0, 0.0])

    assert record == {
        "id": "record-1001",
        "vector": [1.0, 0.0],
        "account_id": "benchmark",
        "uniform_bucket": 1,
        "row_number": 1001,
    }


def test_auto_background_backend_enables_coalescing_rebuild(tmp_path):
    config = benchmark.make_config(
        "auto_cuvs_background",
        project_path=tmp_path,
        collection_name="background",
        dimension=128,
        filter_cache_size=16,
        auto_rebuild_debounce_ms=25,
    )

    assert config.backend == "local"
    assert config.cuvs.auto_enable is True
    assert config.cuvs.auto_background_rebuild is True
    assert config.cuvs.auto_rebuild_debounce_ms == 25


def test_validate_args_accepts_auto_background_backend():
    parser = benchmark.build_parser()
    args = parser.parse_args(["--backends", "auto_cuvs_background"])

    assert benchmark.validate_args(parser, args) == ["auto_cuvs_background"]


@pytest.mark.asyncio
async def test_run_request_set_reports_concurrent_successes():
    class FakeManager:
        async def query(self, **_kwargs):
            return [{"id": str(index)} for index in range(10)]

    ctx = benchmark.RequestContext(
        user=benchmark.UserIdentifier("benchmark", "user"),
        role=benchmark.Role.USER,
    )
    result = await benchmark.run_request_set(
        FakeManager(),
        ctx,
        np.asarray([[1.0, 0.0], [0.0, 1.0]], dtype=np.float32),
        concurrency=4,
        request_count=12,
        k=10,
        filter_factory=lambda _index: None,
    )

    assert result["request_count"] == 12
    assert result["success_count"] == 12
    assert result["error_count"] == 0
    assert result["qps"] > 0
    assert len(result["raw_latency_ms"]) == 12
