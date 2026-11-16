# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Compare posting-first and adaptive scoped retrieval on one dedicated corpus.

Set OV_PGVECTOR_TEST_DSN, then run --stage load, compare, cleanup with the
same --rows and --output arguments. Reports never include the connection DSN.
"""

import argparse
import concurrent.futures
import io
import json
import os
import platform
import statistics
import sys
import threading
import time
import uuid
from pathlib import Path
from types import MethodType

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import psycopg2  # noqa: E402

from openviking.storage.expr import And, Eq, Or, PathScope  # noqa: E402
from openviking.storage.vectordb_adapters.factory import create_collection_adapter  # noqa: E402
from openviking.storage.vectordb_adapters.opengauss.retrieval import (  # noqa: E402
    OpenGaussRetrievalMixin,
)
from openviking_cli.utils.config.vectordb_config import VectorDBBackendConfig  # noqa: E402


def summarize(samples, elapsed=None):
    ordered = sorted(samples)

    def percentile(percent):
        position = (len(ordered) - 1) * percent / 100
        lower = int(position)
        upper = min(lower + 1, len(ordered) - 1)
        return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)

    return {
        "requests": len(samples),
        "p50_ms": statistics.median(samples),
        "p95_ms": percentile(95),
        "p99_ms": percentile(99),
        "max_ms": max(samples),
        **({"elapsed_s": elapsed, "qps": len(samples) / elapsed} if elapsed else {}),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rows", type=int, required=True)
    parser.add_argument("--stage", choices=["load", "compare", "cleanup"], default="compare")
    parser.add_argument("--rounds", type=int, default=30)
    parser.add_argument("--concurrent-seconds", type=float, default=10)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.rows < 2000 or args.rounds < 1 or args.concurrent_seconds < 0:
        parser.error("Require rows >= 2000, rounds >= 1 and concurrent-seconds >= 0")
    if not os.environ.get("OV_PGVECTOR_TEST_DSN"):
        parser.error("Set OV_PGVECTOR_TEST_DSN to a dedicated benchmark database")
    path = args.output
    if args.stage == "load":
        if path.exists():
            parser.error(f"Preserve the existing benchmark report: {path}")
        result = {
            "rows": args.rows,
            "seed": 20261116,
            "collection": f"ov_sparsebench_{args.rows}_{uuid.uuid4().hex[:8]}",
        }
    else:
        result = json.loads(path.read_text())
        if result["rows"] != args.rows or not result["collection"].startswith(
            f"ov_sparsebench_{args.rows}_"
        ):
            parser.error("Report does not identify the requested dedicated benchmark corpus")
    config = VectorDBBackendConfig(
        backend="pgvector",
        name=result["collection"],
        dimension=4,
        sparse_weight=0,
        pgvector={
            "dsn_env": "OV_PGVECTOR_TEST_DSN",
            "index_type": "flat",
            "connection_pool_min_size": 16,
            "connection_pool_max_size": 16,
        },
    )
    adapter = create_collection_adapter(config)

    def save():
        path.write_text(json.dumps(result, ensure_ascii=False, indent=2))

    def emit(event, value):
        save()
        print(json.dumps({"event": event, "value": value}), flush=True)

    try:
        if args.stage == "load":
            adapter.create_collection(
                result["collection"],
                {
                    "Fields": [
                        {"FieldName": "id", "FieldType": "string", "IsPrimaryKey": True},
                        {"FieldName": "vector", "FieldType": "vector", "Dim": 4},
                        {"FieldName": "sparse_vector", "FieldType": "sparse_vector"},
                        {"FieldName": "account_id", "FieldType": "string"},
                        {"FieldName": "uri", "FieldType": "path"},
                        {"FieldName": "level", "FieldType": "int64"},
                    ],
                    "ScalarIndex": ["account_id", "uri", "level"],
                },
                distance="cosine",
                sparse_weight=0,
                index_name="default",
            )
            save()
            raw = adapter.get_collection()._Collection__collection
            started = time.perf_counter()
            with psycopg2.connect(os.environ["OV_PGVECTOR_TEST_DSN"]) as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT version()")
                    result["postgres_version"] = cur.fetchone()[0]
                    cur.execute("SELECT extversion FROM pg_extension WHERE extname='vector'")
                    result["pgvector_version"] = cur.fetchone()[0]
                    result["host"] = {
                        "platform": platform.platform(),
                        "logical_cpus": os.cpu_count(),
                    }
                    for begin in range(0, args.rows, 5000):
                        buf = io.StringIO()
                        for i in range(begin, min(begin + 5000, args.rows)):
                            terms = {"common": 1.0}
                            if i % 5 == 0:
                                terms["hot"] = 1.0
                            if i % 100 == 0:
                                terms["medium"] = 1.0
                            j = 0
                            while len(terms) < 16:
                                terms[f"t{(i * 7919 + j * 104729) % 100000:05d}"] = (
                                    1.0 + ((i + j) % 7) / 8
                                )
                                j += 1
                            uri = adapter._encode_uri_field_value(
                                f"viking://resources/g{(i // 500) % 10}/f{i}.md"
                            )
                            buf.write(
                                "\t".join(
                                    [
                                        f"r{i:08d}",
                                        "[1,0,0,0]",
                                        json.dumps(terms),
                                        f"a{(i // 5) % 100:03d}",
                                        uri,
                                        "2",
                                    ]
                                )
                                + "\n"
                            )
                        buf.seek(0)
                        cur.copy_expert(
                            f'COPY "{result["collection"]}" '
                            "(id,vector,sparse_vector,account_id,uri,level) FROM STDIN",
                            buf,
                        )
                        conn.commit()
            result["load"] = {"wall_s": time.perf_counter() - started}
            started = time.perf_counter()
            raw.create_sparse_index(candidate_limit=1000, sparse_weight=0.5)
            result["sparse_build"] = {
                "s": time.perf_counter() - started,
                "postings": 16 * args.rows,
            }
            for table in (result["collection"], raw._sparse_posting_table_name()):
                raw._execute(f'ANALYZE "{table}"')
            save()
            return
        raw = adapter.get_collection()._Collection__collection
        if args.stage == "cleanup":
            tables = [raw._name, raw._sparse_posting_table_name(), raw._index_catalog_table()]
            result["cleanup"] = {
                "dropped": adapter.drop_collection(),
                "absent": not adapter.collection_exists(),
            }
            with psycopg2.connect(os.environ["OV_PGVECTOR_TEST_DSN"]) as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT tablename FROM pg_tables WHERE schemaname='public' "
                        "AND tablename=ANY(%s)",
                        [tables],
                    )
                    result["cleanup"]["remaining_tables"] = [r[0] for r in cur.fetchall()]
                    cur.execute(
                        'SELECT count(*) FROM "_ov_collection_meta" WHERE table_name=%s',
                        [result["collection"]],
                    )
                    result["cleanup"]["remaining_metadata_rows"] = cur.fetchone()[0]
            assert not result["cleanup"]["remaining_tables"]
            assert not result["cleanup"]["remaining_metadata_rows"]
            emit("cleanup", result["cleanup"])
            return
        adaptive = raw._sparse_candidate_ctes
        variants = {
            "before": MethodType(OpenGaussRetrievalMixin._sparse_candidate_ctes, raw),
            "after": adaptive,
        }
        scenarios = [
            ("tenant_hot", {"hot": 1.0}, Eq("account_id", "a007")),
            ("tenant_common", {"common": 1.0}, Eq("account_id", "a007")),
            ("directory_hot", {"hot": 1.0}, PathScope("uri", "viking://resources/g3", -1)),
            ("directory_common", {"common": 1.0}, PathScope("uri", "viking://resources/g3", -1)),
            # t55665 matches document 1535 in both tenant a007 and directory g3.
            ("tenant_rare", {"t55665": 1.0}, Eq("account_id", "a007")),
            ("directory_rare", {"t55665": 1.0}, PathScope("uri", "viking://resources/g3", -1)),
            ("tenant_mixed", {"hot": 0.5, "t55665": 1.0}, Eq("account_id", "a007")),
            (
                "tenant_directory_hot",
                {"hot": 1.0},
                And(
                    [
                        Eq("account_id", "a007"),
                        PathScope("uri", "viking://resources/g3", -1),
                    ]
                ),
            ),
            (
                "large_scope_common",
                {"common": 1.0},
                Or([Eq("account_id", f"a{i:03d}") for i in range(30)]),
            ),
            ("unscoped_hot", {"hot": 1.0}, None),
            (
                "unscoped_rare8",
                {
                    f"t{x:05d}": 1.0
                    for x in (
                        1,
                        12345,
                        33333,
                        54321,
                        77777,
                        88888,
                        99999,
                        42424,
                    )
                },
                None,
            ),
            ("tenant_medium", {"medium": 1.0}, Eq("account_id", "a000")),
            ("directory_medium", {"medium": 1.0}, PathScope("uri", "viking://resources/g3", -1)),
        ]
        comparison = result["comparison"] = {
            "serial": [],
            "concurrent": [],
            "correctness": [],
            "plans": {},
            "rounds": args.rounds,
            "concurrent_seconds": args.concurrent_seconds,
            "method": "same collection; alternating before/after calls; warm cache",
        }
        expected_outputs = {}

        def query(scenario):
            _, terms, scope = scenario
            return adapter.query(
                sparse_query_vector=terms,
                filter=scope,
                limit=10,
                output_fields=["account_id", "uri"],
            )

        for conn in adapter._pool._pool:
            with conn.cursor() as cur:
                cur.execute("SET statement_timeout='120s'")
            conn.commit()
        try:
            for scenario in scenarios:
                label = scenario[0]
                outputs, plans = {}, {}
                for variant, hook in variants.items():
                    raw._sparse_candidate_ctes = hook
                    captured = []
                    original_cursor = raw._cursor

                    class Capture:
                        def __init__(self, cursor, captures):
                            self.cursor, self.captures = cursor, captures

                        def __getattr__(self, name):
                            return getattr(self.cursor, name)

                        def execute(self, sql, params=None):
                            self.captures.append((sql, params))
                            return self.cursor.execute(sql, params)

                    raw._cursor = lambda factory=original_cursor, captures=captured: Capture(
                        factory(),
                        captures,
                    )
                    try:
                        outputs[variant] = query(scenario)
                    finally:
                        raw._cursor = original_cursor
                    sql, params = captured[-1]
                    plan = raw._execute(
                        "EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) " + sql,
                        params,
                        fetch=True,
                    )[0][0][0]
                    plans[variant] = {"sql": sql, "params": params, "plan": plan}
                assert outputs["before"] == outputs["after"], f"Mismatch: {label}"
                if label in {"tenant_rare", "directory_rare"}:
                    assert outputs["before"], f"Rare-term fixture has no scoped hit: {label}"
                expected_outputs[label] = outputs["before"]
                comparison["correctness"].append(
                    {
                        "scenario": label,
                        "passed": True,
                        "outputs": outputs,
                    }
                )
                comparison["plans"][label] = plans
                samples = {variant: [] for variant in variants}
                for i in range(args.rounds):
                    order = ("before", "after") if i % 2 == 0 else ("after", "before")
                    for variant in order:
                        raw._sparse_candidate_ctes = variants[variant]
                        start = time.perf_counter()
                        got = query(scenario)
                        elapsed = (time.perf_counter() - start) * 1000
                        assert got == expected_outputs[label], f"Mismatch: {label}/{variant}"
                        samples[variant].append(elapsed)
                record = {"scenario": label, **{v: summarize(s) for v, s in samples.items()}}
                record["p50_speedup"] = record["before"]["p50_ms"] / record["after"]["p50_ms"]
                comparison["serial"].append(record)
                emit("serial", record)
            if args.concurrent_seconds:
                for scenario in [scenarios[i] for i in (0, 2, 5, 9, 10)]:
                    label = scenario[0]
                    record = {"scenario": label, "concurrency": 16}
                    for variant, hook in variants.items():
                        raw._sparse_candidate_ctes = hook
                        barrier = threading.Barrier(17)

                        def worker(barrier=barrier, scenario=scenario, label=label):
                            barrier.wait()
                            samples = []
                            deadline = time.perf_counter() + args.concurrent_seconds
                            while len(samples) < 8 or time.perf_counter() < deadline:
                                start = time.perf_counter()
                                got = query(scenario)
                                elapsed = (time.perf_counter() - start) * 1000
                                assert got == expected_outputs[label], f"Mismatch: {label}"
                                samples.append(elapsed)
                            return samples

                        with concurrent.futures.ThreadPoolExecutor(max_workers=16) as pool:
                            futures = [pool.submit(worker) for _ in range(16)]
                            start = time.perf_counter()
                            barrier.wait()
                            samples = [s for f in futures for s in f.result()]
                            elapsed = time.perf_counter() - start
                        record[variant] = summarize(samples, elapsed)
                    record["qps_speedup"] = record["after"]["qps"] / record["before"]["qps"]
                    comparison["concurrent"].append(record)
                    emit("concurrent", record)
            emit("complete", {"scenarios": len(comparison["correctness"])})
        finally:
            raw._sparse_candidate_ctes = adaptive
            save()
    finally:
        adapter.close()


if __name__ == "__main__":
    main()
