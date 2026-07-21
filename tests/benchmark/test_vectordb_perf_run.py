# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

import json
import struct

import pytest

from benchmark.vectordb_perf.run import (
    RunResult,
    build_dir_vector_workload,
    directory_queries,
    extract_path,
    iter_json_array_records,
    parse_args,
    recall_scope,
    record_id,
    upsert_records,
    wait_for_auto_derived_index,
)


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
    _write_fvecs(tmp_path / "dbpedia_dir_2m_query_vectors.fvecs", vectors)


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


@pytest.fixture
def run_to_thread_inline(monkeypatch):
    async def run_inline(fn):
        return fn()

    monkeypatch.setattr("benchmark.vectordb_perf.run.asyncio.to_thread", run_inline)


@pytest.mark.asyncio
async def test_wait_for_auto_derived_index_uses_configured_index_and_timeout(
    run_to_thread_inline,
):
    del run_to_thread_inline
    calls = []

    class _Index:
        def wait_for_background_rebuild(self, *, timeout):
            calls.append(("wait_for_background_rebuild", timeout))
            return True

    index = _Index()

    class _Collection:
        def get_index(self, index_name):
            calls.append(("get_index", index_name))
            return index

    class _Adapter:
        index_name = "configured-index"

        def get_collection(self):
            calls.append(("get_collection",))
            return _Collection()

    class _Backend:
        _shared_adapter = _Adapter()

    assert await wait_for_auto_derived_index(_Backend(), timeout=12.5) is True
    assert calls == [
        ("get_collection",),
        ("get_index", "configured-index"),
        ("wait_for_background_rebuild", 12.5),
    ]


@pytest.mark.asyncio
async def test_wait_for_auto_derived_index_converts_false_to_timeout_error(
    run_to_thread_inline,
):
    del run_to_thread_inline

    class _Index:
        def wait_for_background_rebuild(self, *, timeout):
            assert timeout == 7
            return False

    class _Collection:
        def get_index(self, index_name):
            assert index_name == "default"
            return _Index()

    class _Adapter:
        index_name = "default"

        def get_collection(self):
            return _Collection()

    class _Backend:
        _shared_adapter = _Adapter()

    with pytest.raises(TimeoutError, match="not ready within 7s"):
        await wait_for_auto_derived_index(_Backend(), timeout=7)


@pytest.mark.asyncio
async def test_wait_for_auto_derived_index_rejects_missing_index(run_to_thread_inline):
    del run_to_thread_inline

    class _Collection:
        def get_index(self, index_name):
            assert index_name == "missing-index"
            return None

    class _Adapter:
        index_name = "missing-index"

        def get_collection(self):
            return _Collection()

    class _Backend:
        _shared_adapter = _Adapter()

    with pytest.raises(RuntimeError, match="benchmark index not found: missing-index"):
        await wait_for_auto_derived_index(_Backend())


@pytest.mark.asyncio
async def test_wait_for_auto_derived_index_rejects_unsupported_index(run_to_thread_inline):
    del run_to_thread_inline

    class _Collection:
        def get_index(self, index_name):
            assert index_name == "native-only"
            return object()

    class _Adapter:
        index_name = "native-only"

        def get_collection(self):
            return _Collection()

    class _Backend:
        _shared_adapter = _Adapter()

    with pytest.raises(RuntimeError, match="does not expose background rebuild readiness"):
        await wait_for_auto_derived_index(_Backend())


@pytest.mark.asyncio
async def test_upsert_records_uses_one_bulk_backend_call():
    calls = []

    class _Backend:
        async def upsert_many(self, records, *, ctx):
            calls.append((records, ctx))
            return [record["id"] for record in records]

        async def upsert(self, record, *, ctx):  # pragma: no cover - should never run
            raise AssertionError(f"unexpected serial upsert: {record}, {ctx}")

    backend = _Backend()
    ctx = object()
    records = [{"id": "rec-1"}, {"id": "rec-2"}]

    ids = await upsert_records(backend, ctx, records)

    assert ids == ["rec-1", "rec-2"]
    assert calls == [(records, ctx)]


@pytest.mark.asyncio
async def test_upsert_records_rejects_incomplete_bulk_result():
    class _Backend:
        async def upsert_many(self, records, *, ctx):
            del records, ctx
            return ["rec-1"]

    with pytest.raises(RuntimeError, match="returned 1 ids for 2 records"):
        await upsert_records(
            _Backend(),
            object(),
            [{"id": "rec-1"}, {"id": "rec-2"}],
        )


def test_iter_json_array_records_streams_across_small_chunks(tmp_path):
    path = tmp_path / "records.json"
    expected = [{"id": 0, "path": "/α/β"}, {"id": 1, "path": '/quoted/"x"'}]
    path.write_text(json.dumps(expected, ensure_ascii=False), encoding="utf-8")

    assert list(iter_json_array_records(path, chunk_size=3)) == expected
    assert list(iter_json_array_records(path, limit=1, chunk_size=2)) == expected[:1]
    assert list(iter_json_array_records(path, limit=0, chunk_size=2)) == []


@pytest.mark.parametrize(
    "payload, message",
    [
        ('[{"id": 0},]', "trailing comma"),
        ('[{"id": 0}] garbage', "unexpected content"),
        ('[{"id": 0}', "truncated JSON array"),
    ],
)
def test_iter_json_array_records_rejects_invalid_full_input(tmp_path, payload, message):
    path = tmp_path / "invalid.json"
    path.write_text(payload, encoding="utf-8")

    with pytest.raises(ValueError, match=message):
        list(iter_json_array_records(path, chunk_size=2))


def test_path_extraction_does_not_treat_prose_slashes_as_a_directory():
    assert extract_path({"title": "pronounced /foo/", "text": "a/b"}) == "/"
    assert record_id({"_id": 17}, 0) == "17"


def test_wiki_mapping_and_derived_scopes_use_real_hierarchy(tmp_path):
    _write_wiki_fixture(tmp_path)
    workload = build_dir_vector_workload(
        _wiki_options(tmp_path, "--dir-vector-query-scope", "derived_gt_lca_v1")
    )

    records = list(workload.records())
    assert [record["uri"] for record in records] == [
        "viking://resources/bench/wiki/science/physics/quantum/0",
        "viking://resources/bench/wiki/science/physics/classical/1",
        "viking://resources/bench/wiki/history/ancient/2",
    ]
    assert len(workload.queries) == 3
    assert [case.query_id for case in workload.directory_queries] == ["0", "2"]
    assert workload.directory_queries[0].filter_path.endswith("/science/physics")
    assert workload.directory_queries[0].ground_truth_ids == ["0", "1"]
    assert workload.path_diagnostics == {
        "corpus_path_source": "dbpedia_dir_2m_corpus_paths.json",
        "query_scope_source": "derived_gt_lca_v1",
        "base_query_count": 3,
        "directory_query_count": 2,
        "unique_directory_scopes": 2,
        "root_derived_scopes_omitted": 1,
        "directory_scope_depth_min": 2,
        "directory_scope_depth_median": 2.0,
        "directory_scope_depth_max": 2,
    }


def test_wiki_dataset_scope_refuses_missing_query_constraints(tmp_path):
    _write_wiki_fixture(tmp_path)

    with pytest.raises(ValueError, match="refusing a root-only Directory benchmark"):
        build_dir_vector_workload(_wiki_options(tmp_path))


def test_wiki_write_only_preserves_an_intentionally_empty_directory_set(tmp_path):
    _write_wiki_fixture(tmp_path)

    workload = build_dir_vector_workload(_wiki_options(tmp_path, "--mode", "write-only"))

    assert workload.directory_queries == []
    assert directory_queries(workload) == []


def test_wiki_dataset_scope_accepts_explicit_query_paths(tmp_path):
    _write_wiki_fixture(tmp_path, query_paths=True)

    workload = build_dir_vector_workload(_wiki_options(tmp_path))

    assert len(workload.directory_queries) == 3
    assert workload.path_diagnostics["query_scope_source"] == "dataset"
    assert workload.path_diagnostics["unique_directory_scopes"] == 3


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


def test_derived_scope_only_labels_filtered_phase(tmp_path):
    _write_wiki_fixture(tmp_path)
    workload = build_dir_vector_workload(
        _wiki_options(tmp_path, "--full", "--dir-vector-query-scope", "derived_gt_lca_v1")
    )
    result = RunResult(
        run_id="test",
        collection_name="test",
        output_dir=str(tmp_path),
        events=[],
        validation_errors=[],
        quality={},
        workload={
            "workload": "dir-vector",
            "full": True,
            "path_diagnostics": workload.path_diagnostics,
        },
        environment={},
        kept_collection=True,
    )

    assert recall_scope(result, "vector_search") == "official_full"
    assert recall_scope(result, "filtered_vector_search") == "derived_gt_lca_v1"

    result.workload["path_diagnostics"] = {}
    assert recall_scope(result, "filtered_vector_search") == "official_full"


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
    assert len(workload.directory_queries) == 2
