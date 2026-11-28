# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

import asyncio
import json
import struct
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from benchmark.vectordb_perf.run import (
    QueryCase,
    benchmark_context,
    build_dir_vector_workload,
    directory_queries,
    iter_json_array_records,
    parse_args,
    run_benchmark,
    run_search_phase,
    upsert_records,
    wait_for_auto_derived_index,
    write_outputs,
)
from openviking_cli.utils.config import OpenVikingConfigSingleton


def _write_fvecs(path, vectors):
    with path.open("wb") as handle:
        for vector in vectors:
            handle.write(struct.pack("<i", len(vector)))
            handle.write(struct.pack(f"<{len(vector)}f", *vector))


def _write_wiki_fixture(tmp_path, *, query_paths=False, mapping_ids=(0, 1, 2)):
    corpus = [
        {"_id": 0, "title": "pronounced /not-a-directory/", "text": "zero"},
        {"_id": 1, "title": "one", "text": "one"},
        {"_id": 2, "title": "two", "text": "two"},
    ]
    paths = [
        {"id": mapping_ids[0], "path": "/science/physics/quantum/"},
        {"id": mapping_ids[1], "path": "/science/physics/classical/"},
        {"id": mapping_ids[2], "path": "/history/ancient/"},
    ]
    queries = [
        {"_id": 0, "text": "physics", "metadata": {}},
        {"_id": 1, "text": "mixed", "metadata": {}},
        {"_id": 2, "text": "history", "metadata": {}},
    ]
    if query_paths:
        queries[0]["metadata"] = {"path": "/science/physics"}
        queries[1]["metadata"] = {"path": "/science"}
        queries[2]["metadata"] = {"path": "/history"}

    (tmp_path / "dbpedia_dir_2m_corpus.jsonl").write_text(
        "".join(json.dumps(item) + "\n" for item in corpus), encoding="utf-8"
    )
    (tmp_path / "dbpedia_dir_2m_corpus_paths.json").write_text(json.dumps(paths), encoding="utf-8")
    (tmp_path / "dbpedia_dir_2m_query.jsonl").write_text(
        "".join(json.dumps(item) + "\n" for item in queries), encoding="utf-8"
    )
    (tmp_path / "dbpedia_dir_2m_groundtruth.tsv").write_text(
        "query-id\tcorpus-id\tscore\n0\t0\t2\n0\t1\t2\n1\t0\t2\n1\t2\t2\n2\t2\t1\n",
        encoding="utf-8",
    )
    vectors = [[float(index), 0.0, 0.0, 1.0] for index in range(3)]
    _write_fvecs(tmp_path / "dbpedia_dir_2m_corpus_vectors.fvecs", vectors)
    # The two leading queries miss at K=1 before filtering. The mixed-scope
    # query has no non-root common directory and only runs unfiltered.
    _write_fvecs(tmp_path / "dbpedia_dir_2m_query_vectors.fvecs", [vectors[2], *vectors[1:]])


def _wiki_options(tmp_path, *extra):
    return parse_args(
        [
            "--workload",
            "dir-vector",
            "--dataset",
            "wiki",
            "--dataset-root",
            str(tmp_path),
            "--rows",
            "3",
            "--queries",
            "3",
            *extra,
        ]
    )


def _write_arxiv_public_shape_fixture(tmp_path):
    corpus = [
        {"id": "0", "categories": ["cs.AI", "cs.LG"], "time": "2024/01"},
        {"id": "1", "categories": ["math.OC"], "time": "2024/02"},
    ]
    queries = [
        {"query_id": "0", "constraints": {"time": "2024/01"}},
        {"query_id": "1", "constraints": {"category": "math.OC"}},
    ]
    (tmp_path / "arxiv_corpus_metadata.json").write_text(json.dumps(corpus), encoding="utf-8")
    (tmp_path / "arxiv_query_constraint.json").write_text(json.dumps(queries), encoding="utf-8")
    (tmp_path / "arxiv_ground_truth.txt").write_text("0 1\n1 0\n", encoding="utf-8")
    vectors = [[0.0, 1.0, 0.0, 0.0], [1.0, 0.0, 0.0, 0.0]]
    _write_fvecs(tmp_path / "arxiv_corpus_vectors.fvecs", vectors)
    _write_fvecs(tmp_path / "arxiv_query_vectors.fvecs", vectors)


@pytest.mark.asyncio
async def test_wait_for_auto_derived_index_uses_configured_index_and_timeout():
    ready = Mock(side_effect=[True, False])
    index = SimpleNamespace(wait_for_background_rebuild=ready)
    get_index = Mock(return_value=index)
    adapter = SimpleNamespace(
        index_name="configured-index", get_collection=lambda: SimpleNamespace(get_index=get_index)
    )
    backend = Mock(spec=["_shared_adapter"])
    backend._shared_adapter = adapter

    assert await wait_for_auto_derived_index(backend, timeout=12)
    get_index.assert_called_with("configured-index")
    ready.assert_called_once_with(timeout=12)

    with pytest.raises(TimeoutError, match="not ready within 7s"):
        await wait_for_auto_derived_index(backend, timeout=7)


@pytest.mark.asyncio
async def test_wait_for_auto_derived_index_rejects_missing_index():
    get_index = Mock(side_effect=[None, object()])
    backend = Mock(spec=["_shared_adapter"])
    backend._shared_adapter = SimpleNamespace(
        index_name="wrong-index",
        get_collection=lambda: SimpleNamespace(get_index=get_index),
    )

    with pytest.raises(RuntimeError, match="benchmark index not found: wrong-index"):
        await wait_for_auto_derived_index(backend)
    with pytest.raises(RuntimeError, match="does not expose background rebuild readiness"):
        await wait_for_auto_derived_index(backend)


@pytest.mark.asyncio
async def test_upsert_records_uses_one_bulk_backend_call():
    backend = Mock(upsert_many=AsyncMock(side_effect=[["rec-1", "rec-2"], ["rec-1"]]))
    ctx = benchmark_context()
    records = [{"id": "rec-1"}, {"id": "rec-2"}]

    assert await upsert_records(backend, ctx, records) == ["rec-1", "rec-2"]
    backend.upsert_many.assert_awaited_once_with(records, ctx=ctx)

    with pytest.raises(RuntimeError, match="returned 1 ids for 2 records"):
        await upsert_records(backend, ctx, records)


def test_iter_json_array_records_streams_across_small_chunks(tmp_path):
    path = tmp_path / "records.json"
    expected = [{"id": 0, "path": "/α/β"}, {"id": 1, "path": '/quoted/"x"'}]
    path.write_text(json.dumps(expected, ensure_ascii=False), encoding="utf-8")

    assert list(iter_json_array_records(path, chunk_size=3)) == expected
    assert list(iter_json_array_records(path, limit=1, chunk_size=2)) == expected[:1]
    assert list(iter_json_array_records(path, limit=0, chunk_size=2)) == []


def test_iter_json_array_records_rejects_invalid_full_input(tmp_path):
    path = tmp_path / "invalid.json"
    for payload, message in [
        ('[{"id": 0},]', "trailing comma"),
        ('[{"id": 0}] garbage', "unexpected content"),
        ('[{"id": 0}', "truncated JSON array"),
    ]:
        path.write_text(payload, encoding="utf-8")
        with pytest.raises(ValueError, match=message):
            list(iter_json_array_records(path, chunk_size=2))


def test_wiki_mapping_and_derived_scopes_use_real_hierarchy(tmp_path, monkeypatch):
    _write_wiki_fixture(tmp_path)
    config = tmp_path / "ov.conf"
    config.write_text(
        json.dumps({"storage": {"workspace": str(tmp_path / "storage")}}), encoding="utf-8"
    )
    for name in ("_instance", "_config_file", "_config_file_revision", "_config_file_content"):
        monkeypatch.setattr(OpenVikingConfigSingleton, name, None)
    output = tmp_path / "output"
    options = _wiki_options(
        tmp_path,
        "--config",
        str(config),
        "--full",
        "--dir-vector-query-scope",
        "derived_gt_lca_v1",
        "--output-dir",
        str(output),
        "--top-k",
        "1",
        "--distance",
        "l2",
    )
    workload = build_dir_vector_workload(options)

    assert [record["uri"] for record in workload.records()] == [
        "viking://resources/bench/wiki/science/physics/quantum/0",
        "viking://resources/bench/wiki/science/physics/classical/1",
        "viking://resources/bench/wiki/history/ancient/2",
    ]
    assert directory_queries(workload)[0].ground_truth_ids == ["0", "1"]

    write_outputs(run_benchmark(options), options)
    summary = json.loads((output / "run_summary.json").read_bytes())
    assert summary["validation_errors"] == []
    assert summary["workload"]["path_diagnostics"]["root_derived_scopes_omitted"] == 1
    assert summary["workload"]["path_diagnostics"]["unique_directory_scopes"] == 2
    assert summary["quality"] == {
        "vector_search": {"queries_with_ground_truth": 3, "hit_rate_at_k": 1 / 3, "hits": 1},
        "filtered_vector_search": {"queries_with_ground_truth": 2, "hit_rate_at_k": 1.0, "hits": 2},
    }
    events = [
        json.loads(line)
        for line in (output / "events.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert {
        event["extra"]["query_id"] for event in events if event["phase"] == "vector_search"
    } == {
        "0",
        "1",
        "2",
    }
    assert {
        event["extra"]["query_id"]: event["extra"]["filter_path"]
        for event in events
        if event["phase"] == "filtered_vector_search"
    } == {
        "0": "viking://resources/bench/wiki/science/physics",
        "2": "viking://resources/bench/wiki/history/ancient",
    }

    report = (output / "summary_zh.md").read_text(encoding="utf-8")
    rows = [line.strip("|").split("|") for line in report.splitlines() if line.startswith("|")]
    recall_rows = {
        row[0].strip(): tuple(cell.strip() for cell in row[-2:]) for row in rows if len(row) == 6
    }
    assert recall_rows["vector_search"] == ("official_full", "33.33%")
    assert recall_rows["filtered_vector_search"] == ("derived_gt_lca_v1", "100.00%")


def test_wiki_dataset_scope_requires_explicit_query_constraints(tmp_path):
    _write_wiki_fixture(tmp_path)

    with pytest.raises(ValueError, match="refusing a root-only Directory benchmark"):
        build_dir_vector_workload(_wiki_options(tmp_path))

    workload = build_dir_vector_workload(_wiki_options(tmp_path, "--mode", "write-only"))
    assert directory_queries(workload) == []
    assert len(list(workload.records())) == 3

    _write_wiki_fixture(tmp_path, query_paths=True)

    workload = build_dir_vector_workload(_wiki_options(tmp_path))

    assert {query.query_id: query.filter_path for query in directory_queries(workload)} == {
        "0": "viking://resources/bench/wiki/science/physics",
        "1": "viking://resources/bench/wiki/science",
        "2": "viking://resources/bench/wiki/history",
    }
    assert workload.path_diagnostics["query_scope_source"] == "dataset"


def test_wiki_mapping_requires_row_aligned_ids(tmp_path):
    _write_wiki_fixture(tmp_path, mapping_ids=(0, 9, 2))

    with pytest.raises(ValueError, match="not row-aligned at row 1"):
        build_dir_vector_workload(
            _wiki_options(tmp_path, "--dir-vector-query-scope", "derived_gt_lca_v1")
        )


def test_wiki_mapping_requires_all_rows_in_full_mode(tmp_path):
    _write_wiki_fixture(tmp_path)
    path = tmp_path / "dbpedia_dir_2m_corpus_paths.json"
    mapping = json.loads(path.read_text(encoding="utf-8"))
    mapping.append({"id": 3, "path": "/extra"})
    path.write_text(json.dumps(mapping), encoding="utf-8")
    workload = build_dir_vector_workload(
        _wiki_options(tmp_path, "--full", "--dir-vector-query-scope", "derived_gt_lca_v1")
    )

    with pytest.raises(ValueError, match=r"zip\(\) argument 3 is longer"):
        list(workload.records())


def test_wiki_mapping_rejects_missing_ground_truth_rows(tmp_path):
    _write_wiki_fixture(tmp_path)
    path = tmp_path / "dbpedia_dir_2m_corpus_paths.json"
    mapping = json.loads(path.read_text(encoding="utf-8"))
    path.write_text(json.dumps(mapping[:2]), encoding="utf-8")

    with pytest.raises(ValueError, match="missing 1 ground-truth ids"):
        build_dir_vector_workload(
            _wiki_options(tmp_path, "--dir-vector-query-scope", "derived_gt_lca_v1")
        )


def test_wiki_mapping_rejects_root_path(tmp_path):
    _write_wiki_fixture(tmp_path)
    path = tmp_path / "dbpedia_dir_2m_corpus_paths.json"
    mapping = json.loads(path.read_text(encoding="utf-8"))
    mapping[0]["path"] = None
    path.write_text(json.dumps(mapping), encoding="utf-8")

    with pytest.raises(ValueError, match="empty root path at row 0"):
        build_dir_vector_workload(
            _wiki_options(tmp_path, "--dir-vector-query-scope", "derived_gt_lca_v1")
        )


def test_full_wiki_requires_query_metadata_for_every_vector(tmp_path):
    _write_wiki_fixture(tmp_path)
    path = tmp_path / "dbpedia_dir_2m_query.jsonl"
    lines = path.read_text(encoding="utf-8").splitlines()
    path.write_text("\n".join(lines[:2]) + "\n", encoding="utf-8")

    with pytest.raises(ValueError, match="query metadata/vector count mismatch: 2 vs 3"):
        build_dir_vector_workload(
            _wiki_options(tmp_path, "--full", "--dir-vector-query-scope", "derived_gt_lca_v1")
        )


@pytest.mark.asyncio
async def test_filtered_search_keeps_query_results_aligned_under_concurrency(monkeypatch):
    release_first = asyncio.Event()
    active = peak = 0
    base = "viking://resources/bench/wiki"
    queries = [
        QueryCase(
            str(index), [float(index), 1.0], f"{base}/{index}", [str(index)] if index < 2 else []
        )
        for index in range(3)
    ]

    async def search(*, query_vector, target_directories, **kwargs):
        nonlocal active, peak
        index = int(query_vector[0])
        assert target_directories == [queries[index].filter_path]
        active += 1
        peak = max(peak, active)
        try:
            if index == 0:
                await release_first.wait()
                return [{"id": "0", "uri": f"{base}/0/document"}]
            await asyncio.sleep(0)
            return [{"id": "unrelated", "uri": f"{base}/other/document"}] if index == 1 else []
        finally:
            active -= 1

    def on_progress(*args, **kwargs):
        release_first.set()
        return 0.0

    monkeypatch.setattr("benchmark.vectordb_perf.run.progress_count", on_progress)
    events, errors, quality = await asyncio.wait_for(
        run_search_phase(
            backend=Mock(search_in_tenant=search),
            ctx=benchmark_context(),
            phase="filtered_vector_search",
            queries=queries,
            top_k=1,
            concurrency=2,
            filtered=True,
        ),
        timeout=5,
    )

    ids = [event.extra["query_id"] for event in events]
    assert set(ids) == {"0", "1", "2"}
    assert ids[0] != "0"
    assert peak == 2
    assert errors == [
        f"filtered_vector_search: query 1 returned rows outside {base}/1: ['unrelated']",
        "filtered_vector_search: query 2 returned no results",
    ]
    assert quality == {"queries_with_ground_truth": 2, "hit_rate_at_k": 0.5, "hits": 1}


def test_derived_wiki_option_keeps_non_wiki_loader_behavior(tmp_path):
    _write_arxiv_public_shape_fixture(tmp_path)
    options = parse_args(
        [
            "--workload",
            "dir-vector",
            "--dataset",
            "arxiv",
            "--dataset-root",
            str(tmp_path),
            "--rows",
            "2",
            "--queries",
            "2",
            "--dir-vector-query-scope",
            "derived_gt_lca_v1",
        ]
    )

    workload = build_dir_vector_workload(options)

    assert workload.path_diagnostics["query_scope_source"] == "dataset"
    assert workload.directory_queries is not None
    assert len(workload.directory_queries) == 2
