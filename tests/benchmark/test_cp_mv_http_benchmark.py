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


class _IndexedClient(_FakeClient):
    def __init__(self):
        super().__init__()
        self.records: list[dict] = []

    def batch_write(self, root_uri, operations, *, indexed=False):
        assert indexed
        self.calls.append(("index", root_uri))
        super().batch_write(root_uri, operations)
        self.records.extend(
            {"uri": operation["uri"], "level": 2, "md5": f"{index:032x}"}
            for index, operation in enumerate(operations)
        )

    def cp(self, source, target):
        result = super().cp(source, target)
        copied = [
            {**record, "uri": target + record["uri"][len(source) :]}
            for record in self.records
            if record["uri"] == source or record["uri"].startswith(source + "/")
        ]
        self.records.extend(copied)
        result["vectors"] = {"written": len(copied) + 2}
        return result

    def mv(self, source, target):
        result = super().mv(source, target)
        for record in self.records:
            if record["uri"] == source or record["uri"].startswith(source + "/"):
                record["uri"] = target + record["uri"][len(source) :]
        return result

    def vector_records(self, uri):
        self.calls.append(("vectors", uri))
        return [record for record in self.records if record["uri"] == uri]

    def vector_l2_count(self, uri):
        self.calls.append(("l2_count", uri))
        return sum(
            record["level"] == 2 and (record["uri"] == uri or record["uri"].startswith(uri + "/"))
            for record in self.records
        )

    def rm(self, uri, recursive=False, missing_ok=False):
        super().rm(uri, recursive=recursive, missing_ok=missing_ok)
        self.records = [
            record
            for record in self.records
            if record["uri"] != uri and not record["uri"].startswith(uri + "/")
        ]


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
    client = _IndexedClient()
    ticks = iter(range(4))

    def timer():
        client.calls.append(("timer",))
        return next(ticks)

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
        timer=timer,
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
    assert result["mv"]["samples_seconds"] == [1]
    assert any(call[0] == "vectors" for call in client.calls[:first_copy])
    timer_positions = [i for i, call in enumerate(client.calls) if call[0] == "timer"]
    for start, end in zip(timer_positions[::2], timer_positions[1::2], strict=True):
        assert [call[0] for call in client.calls[start + 1 : end]] in (["cp"], ["mv"])
    assert any(call[0] == "l2_count" for call in client.calls[timer_positions[-1] + 1 :])
    assert not client.records

    class MissingVectors(_IndexedClient):
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


@pytest.mark.parametrize(
    "fault", ["directory_only", "missing", "duplicate", "missing_md5", "extra"]
)
def test_indexed_fixture_requires_one_l2_per_file_before_timing(fault):
    benchmark = _load_benchmark()

    class InvalidFixture(_IndexedClient):
        def wait(self):
            super().wait()
            if sum(call[0] == "wait" for call in self.calls) == 1:
                if fault == "directory_only":
                    self.records = [
                        {"uri": self.records[0]["uri"].rsplit("/", 1)[0], "level": level}
                        for level in (0, 1)
                    ]
                elif fault == "missing":
                    self.records.pop()
                elif fault == "duplicate":
                    self.records.append(dict(self.records[-1]))
                elif fault == "extra":
                    self.records.append(
                        {
                            "uri": self.records[0]["uri"].rsplit("/", 1)[0] + "/unexpected.txt",
                            "level": 2,
                            "md5": "extra",
                        }
                    )
                else:
                    self.records[-1].pop("md5")

    client = InvalidFixture()
    with pytest.raises(benchmark.BenchmarkError, match="L2|md5"):
        benchmark.benchmark_endpoint(
            client,
            label="indexed",
            file_count=3,
            file_size=4,
            setup_batch_size=3,
            warmups=0,
            runs=1,
            name_suffix="invalid-fixture",
            indexed=True,
            timer=lambda: pytest.fail("invalid fixture reached timing"),
        )
    assert not any(call[0] == "cp" for call in client.calls)
    assert not client.paths


@pytest.mark.parametrize("operation", ["cp", "mv setup cp", "mv"])
@pytest.mark.parametrize("fault", ["missing", "duplicate", "wrong_md5", "stale_source", "extra"])
def test_indexed_transfers_validate_l2_target_and_source(operation, fault):
    benchmark = _load_benchmark()

    class InvalidTransfer(_IndexedClient):
        def damage(self, source, target):
            records = [record for record in self.records if record["uri"].startswith(target + "/")]
            if fault == "missing":
                self.records.remove(records[-1])
            elif fault == "duplicate":
                self.records.append(dict(records[-1]))
            elif fault == "wrong_md5":
                records[-1]["md5"] = "wrong"
            elif fault == "extra":
                self.records.append({"uri": target + "/unexpected.txt", "level": 2, "md5": "extra"})
            elif operation == "mv":
                self.records.append({"uri": source + "/orphan.txt", "level": 2, "md5": "stale"})
            else:
                source_record = next(
                    record for record in self.records if record["uri"].startswith(source + "/")
                )
                self.records.remove(source_record)

        def cp(self, source, target):
            result = super().cp(source, target)
            if (operation == "cp" and "/cp-run-" in target) or (
                operation == "mv setup cp" and "/mv-source-" in target
            ):
                self.damage(source, target)
            return result

        def mv(self, source, target):
            result = super().mv(source, target)
            if operation == "mv":
                self.damage(source, target)
            return result

    with pytest.raises(benchmark.BenchmarkError, match="L2|md5"):
        benchmark.benchmark_endpoint(
            InvalidTransfer(),
            label="indexed",
            file_count=3,
            file_size=4,
            setup_batch_size=3,
            warmups=0,
            runs=1,
            name_suffix="invalid-transfer",
            indexed=True,
        )


def test_vector_validation_serializes_authenticated_http_requests(monkeypatch):
    benchmark = _load_benchmark()
    client = benchmark.OpenVikingHttpClient(
        "http://unused.invalid", api_key="ordinary-key", account="account", user="user"
    )
    requests = []
    uri = "viking://resources/source/000000.txt"

    class Response:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def read(self):
            if requests[-1].full_url.startswith("http://unused.invalid/api/v1/debug/vector/scroll"):
                return benchmark.json.dumps(
                    {
                        "result": {
                            "records": [{"uri": uri, "level": 2, "md5": "hash"}],
                            "next_cursor": None,
                        }
                    }
                ).encode()
            return b'{"result": {"count": 0}}'

    def urlopen(request, timeout):
        requests.append(request)
        return Response()

    monkeypatch.setattr(benchmark, "urlopen", urlopen)
    assert client.vector_records(uri) == [{"uri": uri, "level": 2, "md5": "hash"}]
    assert client.vector_l2_count("viking://resources/source") == 0
    from urllib.parse import parse_qs, urlsplit

    assert parse_qs(urlsplit(requests[0].full_url).query) == {"uri": [uri], "limit": ["1000"]}
    count_query = parse_qs(urlsplit(requests[1].full_url).query)
    assert benchmark.json.loads(count_query["filter"][0]) == {
        "op": "and",
        "conds": [
            {"op": "must", "field": "uri", "conds": ["viking://resources/source"], "para": "-d=-1"},
            {"op": "must", "field": "level", "conds": [2]},
        ],
    }
    assert all(request.get_method() == "GET" and request.data is None for request in requests)
    assert all(request.get_header("Authorization") == "Bearer ordinary-key" for request in requests)
    assert all(request.get_header("X-openviking-account") == "account" for request in requests)
    assert all(request.get_header("X-openviking-user") == "user" for request in requests)


def test_per_file_vector_read_rejects_truncated_results(monkeypatch):
    benchmark = _load_benchmark()
    client = benchmark.OpenVikingHttpClient("http://unused.invalid")
    monkeypatch.setattr(
        client,
        "_request",
        lambda *args, **kwargs: (200, {"result": {"records": [], "next_cursor": "1000"}}),
    )
    with pytest.raises(benchmark.BenchmarkError, match="vector"):
        client.vector_records("viking://resources/source/000000.txt")


def test_indexed_batch_setup_requests_synchronous_processing(monkeypatch):
    benchmark = _load_benchmark()
    client = benchmark.OpenVikingHttpClient("http://unused.invalid")
    requests = []
    monkeypatch.setattr(client, "_request", lambda *args, **kwargs: requests.append(kwargs))
    client.batch_write("viking://resources/source", [], indexed=True)
    assert requests[0]["payload"]["wait"] is True
    assert requests[0]["payload"]["defer_processing"] is False
