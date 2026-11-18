import importlib.util
import sys
from pathlib import Path

import pytest

MODULE_PATH = Path(__file__).resolve().parents[2] / "scripts" / "benchmark_cp_mv_http.py"


def _load_benchmark():
    assert MODULE_PATH.exists(), "HTTP cp/mv benchmark script is missing"
    spec = importlib.util.spec_from_file_location("benchmark_cp_mv_http", MODULE_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_latency_summary_and_comparison_report_speedup():
    benchmark = _load_benchmark()

    baseline = {
        "cp": benchmark.summarize_samples([4.0, 5.0, 6.0]),
        "mv": benchmark.summarize_samples([8.0, 10.0, 12.0]),
    }
    candidate = {
        "cp": benchmark.summarize_samples([2.0, 2.5, 3.0]),
        "mv": benchmark.summarize_samples([4.0, 5.0, 6.0]),
    }

    comparison = benchmark.compare_results(baseline, candidate)

    assert baseline["cp"]["median_seconds"] == 5.0
    assert comparison["cp"]["speedup"] == 2.0
    assert comparison["cp"]["improvement_percent"] == 50.0
    assert comparison["mv"]["speedup"] == 2.0


class _FakeClient:
    def __init__(self):
        self.paths: set[str] = set()
        self.batch_payloads: list[tuple[str, list[dict]]] = []
        self.calls: list[tuple] = []

    def health(self):
        self.calls.append(("health",))

    def mkdir(self, uri):
        self.calls.append(("mkdir", uri))
        self.paths.add(uri)

    def batch_write(self, root_uri, operations):
        self.calls.append(("batch_write", root_uri, len(operations)))
        self.batch_payloads.append((root_uri, operations))
        self.paths.update(operation["uri"] for operation in operations)

    def cp(self, source, target):
        self.calls.append(("cp", source, target))
        copied = {target + path[len(source) :] for path in self.paths if path.startswith(source)}
        self.paths.update(copied)
        return {"files_created": len(copied) + 1}

    def mv(self, source, target):
        self.calls.append(("mv", source, target))
        moved = {target + path[len(source) :] for path in self.paths if path.startswith(source)}
        self.paths = {path for path in self.paths if not path.startswith(source)}
        self.paths.update(moved)
        return {"from": source, "to": target}

    def wait(self):
        self.calls.append(("wait",))

    def stat_status(self, uri):
        self.calls.append(("stat", uri))
        return 200 if uri in self.paths else 404

    def rm(self, uri, recursive=False, missing_ok=False):
        self.calls.append(("rm", uri, recursive, missing_ok))
        self.paths = {path for path in self.paths if path != uri and not path.startswith(uri + "/")}


def test_endpoint_benchmark_prepares_checks_and_cleans_both_operations():
    benchmark = _load_benchmark()
    client = _FakeClient()

    result = benchmark.benchmark_endpoint(
        client,
        label="candidate",
        file_count=3,
        file_size=16,
        setup_batch_size=2,
        warmups=1,
        runs=2,
        name_suffix="fixed",
    )

    assert len(result["cp"]["samples_seconds"]) == 2
    assert len(result["mv"]["samples_seconds"]) == 2
    assert all(
        operation["content_base64"] and operation["mode"] == "create"
        for _, operations in client.batch_payloads
        for operation in operations
    )
    assert any(call[0] == "cp" for call in client.calls)
    assert any(call[0] == "mv" for call in client.calls)
    assert sum(call[0] == "wait" for call in client.calls) == 9
    assert not any("cp-mv-perf-candidate-fixed" in path for path in client.paths)


def test_cleanup_failure_does_not_hide_original_benchmark_error():
    benchmark = _load_benchmark()

    class FailingClient(_FakeClient):
        def mkdir(self, uri):
            raise benchmark.BenchmarkError("setup failed")

        def rm(self, uri, recursive=False, missing_ok=False):
            raise OSError("cleanup failed")

    with pytest.raises(benchmark.BenchmarkError, match="setup failed"):
        benchmark.benchmark_endpoint(
            FailingClient(),
            label="candidate",
            file_count=1,
            file_size=1,
            setup_batch_size=1,
            warmups=0,
            runs=1,
            name_suffix="failure",
        )


def test_benchmark_both_orders_combines_abba_samples():
    benchmark = _load_benchmark()
    order: list[str] = []

    class OrderedClient(_FakeClient):
        def __init__(self, name):
            super().__init__()
            self.name = name

        def health(self):
            order.append(self.name)
            super().health()

    result = benchmark.benchmark_both_orders(
        OrderedClient("baseline"),
        OrderedClient("candidate"),
        file_count=1,
        file_size=1,
        setup_batch_size=1,
        warmups=0,
        runs=1,
        name_suffix="abba",
    )

    assert order == ["baseline", "candidate", "candidate", "baseline"]
    assert len(result["baseline"]["cp"]["samples_seconds"]) == 2
    assert len(result["candidate"]["mv"]["samples_seconds"]) == 2


def test_indexed_benchmark_builds_vectors_before_timing_and_checks_copy():
    benchmark = _load_benchmark()

    class IndexedClient(_FakeClient):
        def batch_write(self, root_uri, operations, *, indexed=False):
            assert indexed
            self.calls.append(("index", root_uri))
            super().batch_write(root_uri, operations)

        def cp(self, source, target):
            result = super().cp(source, target)
            result["vectors"] = {"written": 3}
            return result

    client = IndexedClient()
    ticks = iter(range(4))
    result = benchmark.benchmark_endpoint(
        client,
        label="indexed",
        file_count=3,
        file_size=4,
        setup_batch_size=3,
        warmups=0,
        runs=1,
        name_suffix="test",
        indexed=True,
        timer=lambda: next(ticks),
    )
    reindex_position = next(i for i, call in enumerate(client.calls) if call[0] == "index")
    first_copy = next(i for i, call in enumerate(client.calls) if call[0] == "cp")
    assert reindex_position < first_copy
    assert all(
        operation["uri"].endswith(".txt")
        for _, batch in client.batch_payloads
        for operation in batch
    )
    assert all(
        operation.get("content") == "xxxx"
        for _, batch in client.batch_payloads
        for operation in batch
    )
    assert result["cp"]["samples_seconds"] == [1]

    class MissingVectors(IndexedClient):
        def cp(self, source, target):
            return _FakeClient.cp(self, source, target)

    with pytest.raises(benchmark.BenchmarkError, match="vectors"):
        benchmark.benchmark_endpoint(
            MissingVectors(),
            label="indexed",
            file_count=3,
            file_size=4,
            setup_batch_size=3,
            warmups=0,
            runs=1,
            name_suffix="bad",
            indexed=True,
        )


def test_indexed_batch_setup_requests_synchronous_processing(monkeypatch):
    benchmark = _load_benchmark()
    client = benchmark.OpenVikingHttpClient("http://unused.invalid")
    requests = []
    monkeypatch.setattr(client, "_request", lambda *args, **kwargs: requests.append(kwargs))
    client.batch_write("viking://resources/source", [], indexed=True)
    assert requests[0]["payload"]["wait"] is True
    assert requests[0]["payload"]["defer_processing"] is False
