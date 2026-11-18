#!/usr/bin/env python3
# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Compare OpenViking directory cp/mv performance through the HTTP API.

Run the pre-change and changed servers with separate storage workspaces, then:

    python scripts/benchmark_cp_mv_http.py \
      --baseline-url http://127.0.0.1:1933 \
      --candidate-url http://127.0.0.1:1934 \
      --api-key test-root-api-key \
      --output /tmp/openviking-cp-mv-benchmark.json

Setup uses batch-write with deferred processing, and setup, validation, and
cleanup time is excluded from the reported cp/mv latency. Pass --indexed to
prepare text files and complete indexing before measuring existing-vector transfers.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import statistics
import sys
import time
import uuid
from pathlib import Path
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


class BenchmarkError(RuntimeError):
    pass


class OpenVikingHttpClient:
    def __init__(
        self,
        base_url: str,
        *,
        api_key: str | None = None,
        account: str = "default",
        user: str = "default",
        timeout: float = 300.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "X-OpenViking-Account": account,
            "X-OpenViking-User": user,
        }
        if api_key:
            self.headers["Authorization"] = f"Bearer {api_key}"

    def _request(
        self,
        method: str,
        path: str,
        *,
        query: dict[str, Any] | None = None,
        payload: dict[str, Any] | None = None,
        expected: tuple[int, ...] = (200,),
    ) -> tuple[int, dict[str, Any]]:
        url = self.base_url + path
        if query:
            url += "?" + urlencode(query)
        data = json.dumps(payload).encode("utf-8") if payload is not None else None
        request = Request(url, data=data, headers=self.headers, method=method)
        try:
            with urlopen(request, timeout=self.timeout) as response:  # noqa: S310
                status = response.status
                body = response.read()
        except HTTPError as exc:
            status = exc.code
            body = exc.read()
        except URLError as exc:
            raise BenchmarkError(f"{method} {url} failed: {exc}") from exc
        parsed: dict[str, Any] = {}
        if body:
            try:
                loaded = json.loads(body)
                parsed = loaded if isinstance(loaded, dict) else {"body": loaded}
            except json.JSONDecodeError:
                parsed = {"body": body.decode("utf-8", errors="replace")}
        if status not in expected:
            raise BenchmarkError(
                f"{method} {url} returned HTTP {status}: "
                f"{json.dumps(parsed, ensure_ascii=False)[:1000]}"
            )
        return status, parsed

    @staticmethod
    def _result(payload: dict[str, Any]) -> dict[str, Any]:
        result = payload.get("result", {})
        if not isinstance(result, dict):
            raise BenchmarkError(f"unexpected result payload: {payload!r}")
        return result

    def health(self) -> None:
        self._request("GET", "/health")

    def mkdir(self, uri: str) -> None:
        self._request("POST", "/api/v1/fs/mkdir", payload={"uri": uri})

    def batch_write(
        self, root_uri: str, operations: list[dict[str, Any]], *, indexed: bool = False
    ) -> None:
        self._request(
            "POST",
            "/api/v1/content/batch-write",
            payload={
                "root_uri": root_uri,
                "operations": operations,
                "wait": indexed,
                "defer_processing": not indexed,
            },
        )

    def cp(self, source: str, target: str) -> dict[str, Any]:
        _, payload = self._request(
            "POST",
            "/api/v1/fs/cp",
            payload={"from_uri": source, "to_uri": target, "recursive": True},
        )
        return self._result(payload)

    def mv(self, source: str, target: str) -> dict[str, Any]:
        _, payload = self._request(
            "POST",
            "/api/v1/fs/mv",
            payload={"from_uri": source, "to_uri": target},
        )
        return self._result(payload)

    def wait(self) -> None:
        self._request("POST", "/api/v1/system/wait", payload={})

    def vector_records(self, uri: str) -> list[dict[str, Any]]:
        _, payload = self._request(
            "GET", "/api/v1/debug/vector/scroll", query={"uri": uri, "limit": 1000}
        )
        result = self._result(payload)
        records = result.get("records")
        # Per-file reads avoid unstable root pagination for tied updated_at values.
        if not isinstance(records, list) or result.get("next_cursor"):
            raise BenchmarkError(f"incomplete per-file vector records: {uri}")
        return records

    def vector_l2_count(self, uri: str) -> int:
        _, payload = self._request(
            "GET",
            "/api/v1/debug/vector/count",
            query={
                "filter": json.dumps(
                    {
                        "op": "and",
                        "conds": [
                            {"op": "must", "field": "uri", "conds": [uri], "para": "-d=-1"},
                            {"op": "must", "field": "level", "conds": [2]},
                        ],
                    }
                )
            },
        )
        count = self._result(payload).get("count")
        if not isinstance(count, int) or count < 0:
            raise BenchmarkError(f"unexpected L2 vector count for {uri}: {count!r}")
        return count

    def stat_status(self, uri: str) -> int:
        status, _ = self._request(
            "GET",
            "/api/v1/fs/stat",
            query={"uri": uri},
            expected=(200, 404),
        )
        return status

    def rm(self, uri: str, recursive: bool = False, missing_ok: bool = False) -> None:
        expected = (200, 404) if missing_ok else (200,)
        self._request(
            "DELETE",
            "/api/v1/fs",
            query={"uri": uri, "recursive": recursive},
            expected=expected,
        )


def _percentile(samples: list[float], percentile: float) -> float:
    ordered = sorted(samples)
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * percentile
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    fraction = position - lower
    return ordered[lower] + (ordered[upper] - ordered[lower]) * fraction


def summarize_samples(samples: list[float]) -> dict[str, Any]:
    if not samples:
        raise ValueError("at least one latency sample is required")
    return {
        "samples_seconds": [round(sample, 6) for sample in samples],
        "mean_seconds": round(statistics.fmean(samples), 6),
        "median_seconds": round(statistics.median(samples), 6),
        "p95_seconds": round(_percentile(samples, 0.95), 6),
        "min_seconds": round(min(samples), 6),
        "max_seconds": round(max(samples), 6),
    }


def compare_results(
    baseline: dict[str, dict[str, Any]],
    candidate: dict[str, dict[str, Any]],
) -> dict[str, dict[str, float]]:
    comparison: dict[str, dict[str, float]] = {}
    for operation in ("cp", "mv"):
        before = float(baseline[operation]["median_seconds"])
        after = float(candidate[operation]["median_seconds"])
        if before <= 0 or after <= 0:
            raise ValueError("median latency must be positive")
        comparison[operation] = {
            "baseline_median_seconds": before,
            "candidate_median_seconds": after,
            "speedup": round(before / after, 3),
            "improvement_percent": round((before - after) / before * 100.0, 2),
        }
    return comparison


def _assert_transfer_count(result: dict[str, Any], expected: int, operation: str) -> None:
    created = result.get("files_created")
    if not isinstance(created, int) or created < expected:
        raise BenchmarkError(
            f"{operation} reported files_created={created!r}, expected at least {expected}: "
            f"{result!r}"
        )


def _assert_vector_count(result: dict[str, Any], expected: int) -> None:
    written = result.get("vectors", {}).get("written", 0)
    if not isinstance(written, int) or written < expected:
        raise BenchmarkError(f"indexed cp wrote {written!r} vectors, expected at least {expected}")


def _validate_l2_vectors(
    client: Any,
    root_uri: str,
    file_count: int,
    expected_md5s: dict[str, str] | None = None,
) -> dict[str, str]:
    md5s: dict[str, str] = {}
    for index in range(file_count):
        name = f"{index:06d}.txt"
        uri = f"{root_uri}/{name}"
        records = [
            record
            for record in client.vector_records(uri)
            if record.get("uri") == uri and record.get("level") == 2
        ]
        if len(records) != 1:
            raise BenchmarkError(f"expected exactly one L2 vector for {uri}, found {len(records)}")
        md5 = records[0].get("md5")
        if not isinstance(md5, str) or not md5:
            raise BenchmarkError(f"L2 vector md5 is missing: {uri}")
        if expected_md5s is not None and md5 != expected_md5s[name]:
            raise BenchmarkError(f"L2 vector md5 differs from source: {uri}")
        md5s[name] = md5
    count = client.vector_l2_count(root_uri)
    if count != file_count:
        raise BenchmarkError(
            f"expected exactly {file_count} L2 vectors under {root_uri}, found {count}"
        )
    return md5s


def benchmark_endpoint(
    client: Any,
    *,
    label: str,
    file_count: int,
    file_size: int,
    setup_batch_size: int,
    warmups: int,
    runs: int,
    name_suffix: str,
    indexed: bool = False,
    timer: Callable[[], float] = time.perf_counter,
) -> dict[str, dict[str, Any]]:
    benchmark_root = f"viking://resources/cp-mv-perf-{label}-{name_suffix}"
    template = f"{benchmark_root}/template"
    extension = "txt" if indexed else "bin"
    sentinel_name = f"000000.{extension}"
    expected_entries = file_count + 1  # template directory plus its files
    payload = base64.b64encode(b"x" * file_size).decode("ascii")
    operations = [
        {
            "uri": f"{template}/{index:06d}.{extension}",
            **({"content": "x" * file_size} if indexed else {"content_base64": payload}),
            "mode": "create",
        }
        for index in range(file_count)
    ]

    client.health()
    try:
        client.mkdir(benchmark_root)
        client.mkdir(template)
        for start in range(0, len(operations), setup_batch_size):
            client.batch_write(
                template,
                operations[start : start + setup_batch_size],
                **({"indexed": True} if indexed else {}),
            )
        if indexed:
            client.wait()
            source_md5s = _validate_l2_vectors(client, template, file_count)

        samples: dict[str, list[float]] = {"cp": [], "mv": []}
        total_iterations = warmups + runs
        for index in range(total_iterations):
            measured = index >= warmups
            phase = "run" if measured else "warmup"

            cp_target = f"{benchmark_root}/cp-{phase}-{index}"
            started = timer()
            cp_result = client.cp(template, cp_target)
            elapsed = timer() - started
            client.wait()
            _assert_transfer_count(cp_result, expected_entries, "cp")
            if indexed:
                _assert_vector_count(cp_result, file_count)
                _validate_l2_vectors(client, cp_target, file_count, source_md5s)
                _validate_l2_vectors(client, template, file_count, source_md5s)
            if client.stat_status(f"{cp_target}/{sentinel_name}") != 200:
                raise BenchmarkError(f"cp target sentinel is missing: {cp_target}/{sentinel_name}")
            if measured:
                samples["cp"].append(elapsed)
            client.rm(cp_target, recursive=True)

            mv_source = f"{benchmark_root}/mv-source-{phase}-{index}"
            mv_target = f"{benchmark_root}/mv-target-{phase}-{index}"
            mv_setup_result = client.cp(template, mv_source)
            _assert_transfer_count(mv_setup_result, expected_entries, "mv setup cp")
            if indexed:
                _assert_vector_count(mv_setup_result, file_count)
            client.wait()
            if indexed:
                _validate_l2_vectors(client, mv_source, file_count, source_md5s)
                _validate_l2_vectors(client, template, file_count, source_md5s)
            started = timer()
            client.mv(mv_source, mv_target)
            elapsed = timer() - started
            client.wait()
            if indexed:
                _validate_l2_vectors(client, mv_target, file_count, source_md5s)
                source_l2_count = client.vector_l2_count(mv_source)
                if source_l2_count:
                    raise BenchmarkError(
                        f"mv source still has {source_l2_count} L2 vectors: {mv_source}"
                    )
            if client.stat_status(f"{mv_source}/{sentinel_name}") != 404:
                raise BenchmarkError(f"mv source still exists: {mv_source}")
            if client.stat_status(f"{mv_target}/{sentinel_name}") != 200:
                raise BenchmarkError(f"mv target sentinel is missing: {mv_target}/{sentinel_name}")
            if measured:
                samples["mv"].append(elapsed)
            client.rm(mv_target, recursive=True)

        return {operation: summarize_samples(values) for operation, values in samples.items()}
    finally:
        active_error = sys.exc_info()[0] is not None
        try:
            client.rm(benchmark_root, recursive=True, missing_ok=True)
        except Exception as cleanup_error:
            if not active_error:
                raise
            print(f"warning: benchmark cleanup failed: {cleanup_error}", file=sys.stderr)


def _combine_results(*results: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {
        operation: summarize_samples(
            [sample for result in results for sample in result[operation]["samples_seconds"]]
        )
        for operation in ("cp", "mv")
    }


def benchmark_both_orders(
    baseline_client: Any,
    candidate_client: Any,
    **benchmark_args: Any,
) -> dict[str, dict[str, dict[str, Any]]]:
    suffix = str(benchmark_args.pop("name_suffix"))
    baseline_first = benchmark_endpoint(
        baseline_client,
        label="baseline-a",
        name_suffix=suffix,
        **benchmark_args,
    )
    candidate_second = benchmark_endpoint(
        candidate_client,
        label="candidate-a",
        name_suffix=suffix,
        **benchmark_args,
    )
    candidate_first = benchmark_endpoint(
        candidate_client,
        label="candidate-b",
        name_suffix=suffix,
        **benchmark_args,
    )
    baseline_second = benchmark_endpoint(
        baseline_client,
        label="baseline-b",
        name_suffix=suffix,
        **benchmark_args,
    )
    return {
        "baseline": _combine_results(baseline_first, baseline_second),
        "candidate": _combine_results(candidate_second, candidate_first),
    }


def _positive_int(value: str) -> int:
    parsed = int(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("must be greater than zero")
    return parsed


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Compare directory cp/mv latency between two OpenViking HTTP servers."
    )
    parser.add_argument("--baseline-url", required=True, help="HTTP URL for the pre-change server")
    parser.add_argument("--candidate-url", required=True, help="HTTP URL for the changed server")
    parser.add_argument("--api-key", default=os.getenv("OPENVIKING_API_KEY"))
    parser.add_argument("--baseline-api-key")
    parser.add_argument("--candidate-api-key")
    parser.add_argument("--account", default="default")
    parser.add_argument("--user", default="default")
    parser.add_argument("--files", type=_positive_int, default=128)
    parser.add_argument("--file-size", type=_positive_int, default=4096, help="Bytes per file")
    parser.add_argument("--setup-batch-size", type=_positive_int, default=32)
    parser.add_argument("--warmups", type=int, default=1)
    parser.add_argument("--runs", type=_positive_int, default=5)
    parser.add_argument("--indexed", action="store_true", help="Build source vectors before timing")
    parser.add_argument("--timeout", type=float, default=300.0)
    parser.add_argument("--output", type=Path, help="Optional JSON result path")
    return parser


def _print_results(result: dict[str, Any]) -> None:
    print("\noperation  baseline median  candidate median  speedup  improvement")
    print("---------  ---------------  ----------------  -------  -----------")
    for operation in ("cp", "mv"):
        item = result["comparison"][operation]
        print(
            f"{operation:<9}  {item['baseline_median_seconds']:>13.6f}s  "
            f"{item['candidate_median_seconds']:>14.6f}s  "
            f"{item['speedup']:>6.3f}x  {item['improvement_percent']:>9.2f}%"
        )


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    if args.warmups < 0:
        parser.error("--warmups must be zero or greater")
    if args.timeout <= 0:
        parser.error("--timeout must be greater than zero")

    suffix = uuid.uuid4().hex[:10]
    common = {
        "account": args.account,
        "user": args.user,
        "timeout": args.timeout,
    }
    baseline_client = OpenVikingHttpClient(
        args.baseline_url,
        api_key=args.baseline_api_key or args.api_key,
        **common,
    )
    candidate_client = OpenVikingHttpClient(
        args.candidate_url,
        api_key=args.candidate_api_key or args.api_key,
        **common,
    )
    benchmark_args = {
        "file_count": args.files,
        "file_size": args.file_size,
        "setup_batch_size": args.setup_batch_size,
        "warmups": args.warmups,
        "runs": args.runs,
        "name_suffix": suffix,
        "indexed": args.indexed,
    }

    print(f"Benchmarking ABBA order: {args.baseline_url} <-> {args.candidate_url}")
    benchmarks = benchmark_both_orders(
        baseline_client,
        candidate_client,
        **benchmark_args,
    )
    baseline = benchmarks["baseline"]
    candidate = benchmarks["candidate"]
    result = {
        "config": {
            "files": args.files,
            "indexed": args.indexed,
            "file_size": args.file_size,
            "warmups": args.warmups,
            "runs": args.runs,
            "samples_per_endpoint": args.runs * 2,
            "setup_batch_size": args.setup_batch_size,
            "execution_order": "baseline,candidate,candidate,baseline",
        },
        "baseline": {"url": args.baseline_url, **baseline},
        "candidate": {"url": args.candidate_url, **candidate},
        "comparison": compare_results(baseline, candidate),
    }
    _print_results(result)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n")
        print(f"\nJSON result: {args.output.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
