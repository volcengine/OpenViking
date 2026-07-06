# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Repo-local retrieval-quality eval for the pgvector backend (B7 confidence gate).

The external LoCoMo dataset the Qdrant PR (#2350) used is not vendored in this
repo, so this is a small, self-contained, deterministic stand-in: a labeled
mini-corpus embedded with a hashing bag-of-words vector (no network / no LLM),
upserted into a live pgvector collection, then queried to measure recall@1 /
recall@3 / F1. It proves the pgvector adapter retrieves the right nearest
neighbours end-to-end under a real HNSW index — it is NOT the LoCoMo benchmark.

Gated on ``OPENVIKING_PGVECTOR_HOST`` so CI skips it without a live container.
"""

from __future__ import annotations

import hashlib
import math
import os
import uuid

import pytest

from openviking.storage.vectordb_adapters.factory import create_collection_adapter
from openviking.storage.vectordb_adapters.pgvector_adapter import PgVectorCollectionAdapter
from openviking_cli.utils.config.vectordb_config import VectorDBBackendConfig

_DIM = 128


def _embed(text: str, dim: int = _DIM) -> list[float]:
    """Deterministic hashing bag-of-words embedding (unit-normalized)."""
    vec = [0.0] * dim
    for token in text.lower().replace(",", " ").replace(".", " ").split():
        bucket = int(hashlib.sha1(token.encode()).hexdigest(), 16) % dim
        vec[bucket] += 1.0
    norm = math.sqrt(sum(x * x for x in vec)) or 1.0
    return [x / norm for x in vec]


# Labeled mini-corpus: doc id -> text, spanning four topics.
_CORPUS = {
    "auth-login": "Users authenticate by logging in with an email and password to receive a session token.",
    "auth-oauth": "OAuth lets a third-party client obtain an access token without sharing the user password.",
    "auth-mfa": "Multi-factor authentication adds a second verification step such as a one-time passcode.",
    "bill-invoice": "Invoices are generated monthly and list the charges for the billing period.",
    "bill-refund": "A refund returns money to the customer for a cancelled or disputed charge.",
    "bill-plan": "Upgrading the subscription plan changes the monthly price and available quota.",
    "deploy-docker": "Deploy the service with docker compose which starts the containers and healthchecks.",
    "deploy-k8s": "Kubernetes runs the workload as pods and scales replicas based on load.",
    "deploy-env": "Configuration is provided through environment variables loaded at container startup.",
    "search-vector": "Vector search finds the nearest embeddings to a query using an approximate index.",
    "search-filter": "A scalar filter narrows vector search results to rows matching metadata conditions.",
    "search-hybrid": "Hybrid search fuses dense vector similarity with sparse lexical matching scores.",
}

# Query -> the single relevant doc id (paraphrases sharing the key content words).
_QUERIES = {
    "how do I log in with my email and password to get a session token": "auth-login",
    "third-party client access token without sharing the password": "auth-oauth",
    "second verification step with a one-time passcode": "auth-mfa",
    "monthly invoice listing the charges for the billing period": "bill-invoice",
    "return money to the customer for a cancelled charge": "bill-refund",
    "upgrade the subscription plan to change monthly price and quota": "bill-plan",
    "deploy with docker compose starting containers and healthchecks": "deploy-docker",
    "kubernetes pods scaling replicas based on load": "deploy-k8s",
    "configuration through environment variables at container startup": "deploy-env",
    "nearest embeddings to a query using an approximate index": "search-vector",
    "narrow vector results to rows matching metadata conditions": "search-filter",
    "fuse dense vector similarity with sparse lexical matching": "search-hybrid",
}


def _eval_config(project: str) -> VectorDBBackendConfig:
    return VectorDBBackendConfig.model_validate(
        {
            "backend": "pgvector",
            "project": project,
            "name": "context",
            "index_name": "default",
            "distance_metric": "cosine",
            "dimension": _DIM,
            "pgvector": {
                "host": os.getenv("OPENVIKING_PGVECTOR_HOST", "127.0.0.1"),
                "port": int(os.getenv("OPENVIKING_PGVECTOR_PORT", "15432")),
                "user": os.getenv("OPENVIKING_PGVECTOR_USER", "postgres"),
                "password": os.getenv("OPENVIKING_PGVECTOR_PASSWORD", "postgres"),
                "db_name": os.getenv("OPENVIKING_PGVECTOR_DB", "postgres"),
                "schema": os.getenv("OPENVIKING_PGVECTOR_SCHEMA", "public"),
            },
        }
    )


@pytest.mark.skipif(
    not os.getenv("OPENVIKING_PGVECTOR_HOST"),
    reason="set OPENVIKING_PGVECTOR_HOST to run the pgvector retrieval eval",
)
def test_pgvector_retrieval_quality():
    adapter = create_collection_adapter(_eval_config(f"eval_{uuid.uuid4().hex[:8]}"))
    assert isinstance(adapter, PgVectorCollectionAdapter)
    meta = {
        "CollectionName": "context",
        "Fields": [
            {"FieldName": "id", "FieldType": "string", "IsPrimaryKey": True},
            {"FieldName": "uri", "FieldType": "path"},
            {"FieldName": "vector", "FieldType": "vector", "Dim": _DIM},
            {"FieldName": "abstract", "FieldType": "string"},
        ],
    }
    collection = adapter._new_collection(meta)
    try:
        collection.create_remote_collection(meta)
        collection.create_index(
            "default",
            {
                "IndexName": "default",
                "VectorIndex": {"IndexType": "hnsw", "Distance": "cosine"},
                "ScalarIndex": ["uri"],
            },
        )
        collection.upsert_data(
            [
                adapter._normalize_record_for_write(
                    {
                        "id": doc_id,
                        "uri": f"viking://resources/kb/{doc_id}.md",
                        "vector": _embed(text),
                        "abstract": text,
                    }
                )
                for doc_id, text in _CORPUS.items()
            ]
        )

        hits_at_1 = 0
        hits_at_3 = 0
        for query, expected in _QUERIES.items():
            result = collection.search_by_vector("default", dense_vector=_embed(query), limit=3)
            ranked = [item.id for item in result.data]
            if ranked and ranked[0] == expected:
                hits_at_1 += 1
            if expected in ranked:
                hits_at_3 += 1

        total = len(_QUERIES)
        recall_at_1 = hits_at_1 / total
        recall_at_3 = hits_at_3 / total
        # single relevant doc, single top-1 retrieved -> precision@1 == recall@1 == F1.
        f1 = recall_at_1
        print(
            f"\n[pgvector retrieval eval] N={total} recall@1={recall_at_1:.3f} "
            f"recall@3={recall_at_3:.3f} F1={f1:.3f}"
        )

        assert recall_at_1 >= 0.8, f"recall@1 {recall_at_1:.3f} below 0.8 bar"
        assert recall_at_3 >= 0.9, f"recall@3 {recall_at_3:.3f} below 0.9 bar"
    finally:
        collection.drop()
        adapter.close()
