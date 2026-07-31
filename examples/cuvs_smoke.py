"""Minimal GPU smoke test for OpenViking's cuVS dense-search backend."""

import argparse
from concurrent.futures import ThreadPoolExecutor

from openviking.storage.vectordb.collection.local_collection import (
    get_or_create_local_collection,
)


def main(algorithm: str, dtype: str) -> None:
    collection = get_or_create_local_collection(
        meta_data={
            "CollectionName": "cuvs_smoke",
            "Fields": [
                {"FieldName": "id", "FieldType": "string", "IsPrimaryKey": True},
                {"FieldName": "vector", "FieldType": "vector", "Dim": 4},
                {"FieldName": "account_id", "FieldType": "string"},
                {"FieldName": "uri", "FieldType": "path"},
            ],
        },
        config={
            "dense_search": {
                "backend": "cuvs",
                "algorithm": algorithm,
                "dtype": dtype,
                "fallback_to_native": True,
                "build_params": (
                    {"graph_degree": 16, "intermediate_graph_degree": 32}
                    if algorithm == "cagra"
                    else {}
                ),
                "search_params": {"itopk_size": 64} if algorithm == "cagra" else {},
            }
        },
    )
    try:
        collection.create_index(
            "default",
            {
                "IndexName": "default",
                "VectorIndex": {"IndexType": "flat", "Distance": "cosine"},
                "ScalarIndex": ["account_id", "uri"],
            },
        )
        records = [
            {
                "id": "a",
                "vector": [1, 0, 0, 0],
                "account_id": "demo",
                "uri": "/docs/a",
            },
            {
                "id": "b",
                "vector": [0, 1, 0, 0],
                "account_id": "demo",
                "uri": "/docs/b",
            },
        ]
        if algorithm == "cagra":
            # CAGRA is a graph index and needs more than a two-point toy dataset.
            # Filler records are excluded by the scalar prefilter below.
            records.extend(
                {
                    "id": f"filler-{index}",
                    "vector": [0, 0, 1, index / 128],
                    "account_id": "filler",
                    "uri": f"/fillers/{index}",
                }
                for index in range(128)
            )
        collection.upsert_data(records)
        demo_filter = {
            "op": "and",
            "conds": [
                {"op": "must", "field": "account_id", "conds": ["demo"]},
                {
                    "op": "must",
                    "field": "uri",
                    "conds": ["/docs"],
                    "para": "-d=-1",
                },
            ],
        }
        result = collection.search_by_vector(
            "default",
            dense_vector=[1, 0, 0, 0],
            limit=2,
            filters=demo_filter,
        )
        ids = [item.id for item in result.data]
        assert ids == ["a", "b"], ids

        with ThreadPoolExecutor(max_workers=4) as executor:
            concurrent_results = list(
                executor.map(
                    lambda _index: collection.search_by_vector(
                        "default",
                        dense_vector=[1, 0, 0, 0],
                        limit=2,
                        filters=demo_filter,
                    ),
                    range(4),
                )
            )
        assert all(
            [item.id for item in concurrent_result.data] == ["a", "b"]
            for concurrent_result in concurrent_results
        )

        collection.update_data(
            [
                {
                    "id": "b",
                    "vector": [2, 0, 0, 0],
                    "account_id": "demo",
                    "uri": "/docs/b",
                }
            ]
        )
        collection.delete_data(["a"])
        result = collection.search_by_vector(
            "default",
            dense_vector=[1, 0, 0, 0],
            limit=2,
            filters=demo_filter,
        )
        ids_after_mutation = [item.id for item in result.data]
        assert ids_after_mutation == ["b"], ids_after_mutation
        print(
            f"cuVS {algorithm}/{dtype} smoke test passed:",
            ids,
            "->",
            ids_after_mutation,
        )
    finally:
        collection.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--algorithm",
        choices=("brute_force", "cagra"),
        default="brute_force",
    )
    parser.add_argument(
        "--dtype",
        choices=("float32", "float16"),
        default="float32",
    )
    args = parser.parse_args()
    main(args.algorithm, args.dtype)
