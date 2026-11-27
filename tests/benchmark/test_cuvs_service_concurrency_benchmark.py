import asyncio

import numpy as np
import pytest

from benchmark.cuvs import run_service_concurrency_benchmark as benchmark


def test_auto_background_backend_enables_coalescing_rebuild(tmp_path):
    parser = benchmark.build_parser()
    args = parser.parse_args(
        [
            "--backends",
            "auto_cuvs_background",
            "--auto-rebuild-debounce-ms",
            "25",
        ]
    )
    [backend] = benchmark.validate_args(parser, args)
    config = benchmark.make_config(
        backend,
        project_path=tmp_path,
        collection_name="background",
        dimension=128,
        filter_cache_size=16,
        auto_rebuild_debounce_ms=args.auto_rebuild_debounce_ms,
    )

    assert config.backend == "local"
    assert config.cuvs is not None
    assert config.cuvs.auto_enable is True
    assert config.cuvs.auto_background_rebuild is True
    assert config.cuvs.auto_rebuild_debounce_ms == 25


@pytest.mark.asyncio
async def test_run_request_set_reports_successes_and_failures_under_concurrency(
    tmp_path, monkeypatch
):
    active = peak = 0
    requests = []
    native_query = benchmark.VikingVectorIndexBackend.query

    async def query(manager, *, filter, ctx, limit, **kwargs):
        nonlocal active, peak
        if not filter or filter["field"] != "row_number":
            return await native_query(manager, filter=filter, ctx=ctx, limit=limit, **kwargs)
        requests.append((filter, ctx.account_id, limit))
        request_no = len(requests)
        active += 1
        peak = max(peak, active)
        try:
            await asyncio.sleep(0)
            if request_no == 1:
                return []
            if request_no == 2:
                raise RuntimeError("query failed")
            return await native_query(manager, filter=filter, ctx=ctx, limit=limit, **kwargs)
        finally:
            active -= 1

    monkeypatch.setattr(benchmark.VikingVectorIndexBackend, "query", query)
    queries = np.asarray([[1.0, 0.0, 0.0, 0.0], [0.0, 1.0, 0.0, 0.0]], dtype=np.float32)
    dataset = np.tile(queries, (512, 1))
    report = await asyncio.wait_for(
        benchmark.run_backend(
            "native",
            dataset,
            queries,
            run_root=tmp_path,
            ingest_batch_size=256,
            filter_cache_size=16,
            auto_rebuild_debounce_ms=25,
            background_timeout=5,
            concurrency_levels=[4],
            cached_request_count=4,
            unique_request_count=12,
            k=10,
        ),
        timeout=10,
    )
    assert report["record_count"] == len(dataset)
    scenarios = {scenario["name"]: scenario["results"] for scenario in report["scenarios"]}
    for name in ("tenant_cached", "tenant_uniform_10pct_cached", "post_mutation_burst"):
        [other] = scenarios[name]
        assert other["success_count"] == 4
        assert other["error_count"] == 0

    [result] = scenarios["unique_filter"]
    assert len(requests) == 12
    assert {(account, limit) for _, account, limit in requests} == {("benchmark", 10)}
    filters = [filter_spec for filter_spec, _, _ in requests]
    assert len({item["gte"] for item in filters}) == 12
    assert all(
        0 <= item["gte"] and item["lt"] == item["gte"] + 100 <= len(dataset) for item in filters
    )
    assert peak == 4
    assert result["success_count"] == 10
    assert result["error_count"] == 2
    assert result["errors"] == ["expected 10 results, received 0", "RuntimeError: query failed"]
    assert result["qps"] * result["wall_seconds"] == pytest.approx(10)
    assert len(result["raw_latency_ms"]) == 12
