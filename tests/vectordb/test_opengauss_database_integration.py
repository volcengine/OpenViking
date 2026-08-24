import os
import uuid
from urllib.parse import unquote, urlparse

import psycopg2
import pytest

from openviking.storage.vectordb_adapters.opengauss_adapter import OpenGaussCollectionAdapter

pytestmark = pytest.mark.integration


def _adapter_from_dsn(index_type: str, mode: str) -> OpenGaussCollectionAdapter:
    environment_name = (
        "OPENVIKING_OPENGAUSS_DISTRIBUTED_DSN"
        if mode == "distributed"
        else "OPENVIKING_OPENGAUSS_STANDALONE_DSN"
    )
    raw_dsn = os.getenv(environment_name)
    if not raw_dsn:
        pytest.skip(f"{environment_name} is not configured")
    parsed = urlparse(raw_dsn)
    build_params = {
        "hnsw": {"m": 16, "ef_construction": 64},
        "hnsw-pq": {"m": 16, "ef_construction": 64, "pq_m": 8},
        "hnsw-rabitq": {"m": 16, "ef_construction": 64, "rabitq_refine_type": "FP32"},
        "ivfflat": {"lists": 1},
        "ivf-pq": {"lists": 1, "pq_m": 8},
        "ivf-rabitq": {"lists": 1},
        "diskann": {"index_size": 50},
    }[index_type]
    return OpenGaussCollectionAdapter(
        collection_name=f"ov_validation_{uuid.uuid4().hex[:8]}",
        host=parsed.hostname or "127.0.0.1",
        port=parsed.port or 5432,
        user=unquote(parsed.username or "gaussdb"),
        password=unquote(parsed.password or ""),
        db_name=parsed.path.lstrip("/") or "omm",
        distributed=mode == "distributed",
        shard_count=4,
        index_type=index_type,
        build_params=build_params,
        search_params={"ef_search": 40} if index_type.startswith("hnsw") else {"probes": 8},
    )


@pytest.mark.parametrize(
    "index_type",
    ["hnsw", "hnsw-pq", "hnsw-rabitq", "ivfflat", "ivf-pq", "ivf-rabitq", "diskann"],
)
def test_standalone_real_index_and_ann_plan(index_type):
    adapter = _adapter_from_dsn(index_type, "standalone")
    collection_name = adapter._collection_name
    try:
        schema = {
            "CollectionName": collection_name,
            "Fields": [
                {"FieldName": "id", "FieldType": "string", "IsPrimaryKey": True},
                {"FieldName": "uri", "FieldType": "path"},
                {"FieldName": "vector", "FieldType": "vector", "Dim": 8},
            ],
            "ScalarIndex": ["uri"],
        }
        assert adapter.create_collection(
            collection_name,
            schema,
            distance="cosine",
            sparse_weight=0,
            index_name="default",
        )
        adapter.begin_bulk_ingest()
        adapter.upsert(
            [
                {"id": f"item-{number}", "uri": f"viking://resources/{number}", "vector": [float(number == position) for position in range(8)]}
                for number in range(8)
            ]
        )
        adapter.end_bulk_ingest()
        collection = adapter.get_collection()
        assert collection.has_index("default")
        fetched = adapter.get(["item-0"])
        assert isinstance(fetched[0]["vector"], list)
        assert len(fetched[0]["vector"]) == 8

        cursor = adapter._conn.cursor()
        cursor.execute(
            "SELECT indexdef FROM pg_indexes WHERE schemaname=current_schema() AND tablename=%s AND indexname=%s",
            (collection_name, f"idx_{collection_name}_default_vec"),
        )
        index_definition = cursor.fetchone()[0]
        assert "USING " in index_definition
        cursor.execute("SET enable_seqscan=off")
        cursor.execute(
            f'EXPLAIN SELECT id FROM "{collection_name}" ORDER BY vector <=> %s::vector LIMIT 3',
            ("[1,0,0,0,0,0,0,0]",),
        )
        plan = "\n".join(row[0] for row in cursor.fetchall())
        adapter._conn.commit()
        cursor.close()
        assert f"Ann Index Scan using idx_{collection_name}_default_vec" in plan
    finally:
        adapter.drop_collection()
        adapter.close()


def test_distributed_hnsw_real_catalog_and_plan():
    adapter = _adapter_from_dsn("hnsw", "distributed")
    collection_name = adapter._collection_name
    try:
        schema = {
            "CollectionName": collection_name,
            "Fields": [
                {"FieldName": "id", "FieldType": "string", "IsPrimaryKey": True},
                {"FieldName": "vector", "FieldType": "vector", "Dim": 8},
            ],
        }
        assert adapter.create_collection(
            collection_name,
            schema,
            distance="cosine",
            sparse_weight=0,
            index_name="default",
        )
        adapter.upsert(
            [{"id": f"item-{number}", "vector": [float(number == position) for position in range(8)]} for number in range(8)]
        )
        cursor = adapter._conn.cursor()
        cursor.execute(
            "SELECT partmethod FROM pg_dist_partition WHERE logicalrelid=%s::regclass",
            (collection_name,),
        )
        assert cursor.fetchone() is not None
        cursor.execute("SET enable_seqscan=off")
        cursor.execute(
            f'EXPLAIN SELECT id FROM "{collection_name}" ORDER BY vector <=> %s::vector LIMIT 3',
            ("[1,0,0,0,0,0,0,0]",),
        )
        plan = "\n".join(row[0] for row in cursor.fetchall())
        adapter._conn.commit()
        cursor.close()
        assert "Ann Index Scan" in plan
    finally:
        adapter.drop_collection()
        adapter.close()


@pytest.mark.parametrize(
    ("index_type", "build_params"),
    [
        ("hnsw-pq", {"pq_m": 8}),
        ("hnsw-rabitq", {"rabitq_refine_type": "FP32"}),
        ("ivfflat", {"lists": 1}),
        ("ivf-pq", {"lists": 1, "pq_m": 8}),
        ("ivf-rabitq", {"lists": 1}),
        ("diskann", {"index_size": 50}),
    ],
)
def test_distributed_non_plain_hnsw_is_rejected_without_orphan_table(
    index_type,
    build_params,
):
    raw_dsn = os.getenv("OPENVIKING_OPENGAUSS_DISTRIBUTED_DSN")
    if not raw_dsn:
        pytest.skip("OPENVIKING_OPENGAUSS_DISTRIBUTED_DSN is not configured")
    parsed = urlparse(raw_dsn)
    collection_name = f"ov_validation_{uuid.uuid4().hex[:8]}"

    with pytest.raises(
        ValueError,
        match="supports only plain HNSW",
    ):
        OpenGaussCollectionAdapter(
            collection_name=collection_name,
            host=parsed.hostname or "127.0.0.1",
            port=parsed.port or 5432,
            user=unquote(parsed.username or "gaussdb"),
            password=unquote(parsed.password or ""),
            db_name=parsed.path.lstrip("/") or "postgres",
            distributed=True,
            shard_count=4,
            index_type=index_type,
            build_params=build_params,
        )

    connection = psycopg2.connect(raw_dsn)
    cursor = connection.cursor()
    cursor.execute(
        "SELECT COUNT(*) FROM information_schema.tables "
        "WHERE table_schema=current_schema() AND table_name=%s",
        (collection_name,),
    )
    assert cursor.fetchone()[0] == 0
    connection.commit()
    cursor.close()
    connection.close()
