# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

from types import SimpleNamespace

import pytest

from openviking.retrieve.sag2 import expansion_terms, interleave, retrieve_sag2
from openviking.storage.collection_schemas import CollectionSchemas
from openviking.storage.expr import Eq
from openviking.storage.vectordb_adapters.sag2_adapter import Sag2CollectionAdapter
from openviking.storage.vectordb_adapters.sag2_lexical import Sag2TenantScope, Term
from openviking.storage.viking_vector_index_backend import _AsyncVectorAdapter
from openviking_cli.utils.config.vectordb_config import VectorDBBackendConfig


class FakeScorer:
    def __init__(self):
        self.calls = []

    async def score(self, question, candidates, evidence):
        self.calls.append(([item["id"] for item in candidates], [item["id"] for item in evidence]))
        return [
            {
                **item,
                "_score": 0.99 if "final answer" in str(item.get("abstract", "")) else 0.8,
            }
            for item in candidates
        ]


def _candidate(record_id, uri, text, level=0):
    return {
        "id": record_id,
        "account_id": "acct",
        "uri": uri,
        "context_type": "resource",
        "level": level,
        "name": record_id,
        "abstract": text,
    }


async def _scope(tmp_path, records, **sag2):
    config = VectorDBBackendConfig(backend="sag2", path=str(tmp_path), sag2=sag2)
    adapter = Sag2CollectionAdapter.from_config(config)
    adapter.create_collection(
        "context",
        CollectionSchemas.context_collection("context", 0, requires_embedding=False),
        distance="cosine",
        sparse_weight=0,
        index_name="default",
    )
    adapter.upsert(records)
    scope = Sag2TenantScope(
        "acct",
        Eq("account_id", "acct"),
        adapter.current_epoch("acct"),
        _AsyncVectorAdapter(adapter),
    )
    return scope, config.sag2, adapter


@pytest.mark.asyncio
async def test_multihop_abc_and_duplicate_uri_identity(tmp_path):
    records = [
        _candidate("a-l0", "viking://resources/same", "alpha bridge"),
        _candidate("a-l1", "viking://resources/same", "alpha context", level=1),
        _candidate("b", "viking://resources/b", "bridge gamma"),
        _candidate("c", "viking://resources/c", "gamma final answer"),
        _candidate("noise", "viking://resources/noise", "ordinary unrelated"),
    ]
    scope, config, adapter = await _scope(tmp_path, records, candidate_k=10, max_hops=2)
    scorer = FakeScorer()
    results, traces = await retrieve_sag2(scope, "alpha", config, scorer, max_hops=2)
    assert len(scorer.calls) == 3  # No extra final re-scoring pass.
    assert {"a-l0", "a-l1"}.issubset(set(scorer.calls[0][0]))
    assert "c" not in scorer.calls[0][0]
    assert "c" not in scorer.calls[1][0]
    assert "c" in scorer.calls[2][0]
    assert [trace.hop for trace in traces] == [0, 1, 2]
    assert results[0]["id"] == "c"
    adapter.close()


@pytest.mark.asyncio
async def test_h0_scores_once_and_budget_stops_complete_next_round(tmp_path):
    records = [
        _candidate("a", "viking://resources/a", "alpha bridge"),
        _candidate("a2", "viking://resources/a2", "alpha context"),
        _candidate("b", "viking://resources/b", "bridge gamma"),
        _candidate("c", "viking://resources/c", "gamma final answer"),
    ]
    scope, config, adapter = await _scope(
        tmp_path, records, candidate_k=3, protected_k=1, seed_k=1, max_hops=1, max_judgments=3
    )
    scorer = FakeScorer()
    results, traces = await retrieve_sag2(scope, "alpha", config, scorer, max_hops=1)
    assert len(scorer.calls) == 1
    assert len(traces) == 1
    assert {item["id"] for item in results} == {"a", "a2"}
    adapter.close()


@pytest.mark.asyncio
async def test_h2_full_pool_respects_configured_budget_without_final_rescore(monkeypatch):
    records = {
        key: _candidate(key, f"viking://resources/{key}", key)
        for key in ("a", "b", "c", "d", "e", "f")
    }

    class FullPoolScope:
        def __init__(self):
            self.expansion = 0

        async def query(self, expression, **kwargs):
            if kwargs.get("required_term") is None:
                return [dict(records["a"]), dict(records["b"])]
            self.expansion += 1
            keys = ("c", "d") if self.expansion == 1 else ("e", "f")
            return [dict(records[key]) for key in keys]

        async def has_match(self, expression, excluded_ids=()):
            return True

        async def titles(self):
            return []

        async def check_epoch(self):
            return None

    async def terms(scope, query, seeds, limit):
        return [Term("bridge", 1.0)]

    async def resolve(scope, terms, seen, max_query_terms):
        return terms

    monkeypatch.setattr("openviking.retrieve.sag2.expansion_terms", terms)
    monkeypatch.setattr("openviking.retrieve.sag2._resolve_terms", resolve)

    class PoolScorer:
        def __init__(self):
            self.calls = []

        async def score(self, question, candidates, evidence):
            self.calls.append([item["id"] for item in candidates])
            # H0 chooses b (outside protected a); H1 chooses new c, producing
            # two distinct carried seeds and the maximum H2 pool.
            preferred = "b" if len(self.calls) == 1 else "c"
            return [
                {**item, "_score": 0.99 if item["id"] == preferred else 0.5} for item in candidates
            ]

    config = SimpleNamespace(
        candidate_k=2,
        seed_k=1,
        protected_k=1,
        expansion_term_limit=1,
        max_query_terms=80,
        max_hops=2,
        max_judgments=10,
        total_timeout_s=30,
    )
    scope = FullPoolScope()
    scorer = PoolScorer()
    await retrieve_sag2(scope, "alpha", config, scorer, max_hops=2)
    assert scorer.calls == [["a", "b"], ["a", "b", "c", "d"]]

    config.max_judgments = 11
    scope = FullPoolScope()
    scorer = PoolScorer()
    await retrieve_sag2(scope, "alpha", config, scorer, max_hops=2)
    assert scorer.calls == [["a", "b"], ["a", "b", "c", "d"], ["a", "b", "c", "e", "f"]]


@pytest.mark.asyncio
async def test_h2_truncates_only_incoming_to_twenty_and_marks_previous_scores(monkeypatch):
    initial = [_candidate(key, f"viking://resources/{key}", key) for key in ("a", "b", "c")]
    incoming = [
        _candidate(f"n{index}", f"viking://resources/n{index}", "new") for index in range(25)
    ]

    class Scope:
        def __init__(self):
            self.expansion = 0

        async def query(self, expression, **kwargs):
            if kwargs.get("required_term") is None:
                return [dict(item) for item in initial[:2]]
            self.expansion += 1
            values = initial[2:] if self.expansion == 1 else incoming
            return [dict(item) for item in values]

        async def has_match(self, expression, excluded_ids=()):
            return True

        async def titles(self):
            return []

        async def check_epoch(self):
            return None

    async def terms(scope, query, seeds, limit):
        return [Term("bridge", 1.0)]

    async def resolve(scope, terms, seen, max_query_terms):
        return terms

    monkeypatch.setattr("openviking.retrieve.sag2.expansion_terms", terms)
    monkeypatch.setattr("openviking.retrieve.sag2._resolve_terms", resolve)

    class Scorer:
        def __init__(self):
            self.calls = []
            self.evidence = []

        async def score(self, question, candidates, evidence):
            self.calls.append([item["id"] for item in candidates])
            self.evidence.append(evidence)
            preferred = "b" if len(self.calls) == 1 else "c"
            return [
                {**item, "_score": 0.99 if item["id"] == preferred else 0.5} for item in candidates
            ]

    config = SimpleNamespace(
        candidate_k=30,
        seed_k=1,
        protected_k=1,
        expansion_term_limit=1,
        max_query_terms=80,
        max_hops=2,
        max_judgments=100,
        total_timeout_s=30,
    )
    scorer = Scorer()
    await retrieve_sag2(Scope(), "alpha", config, scorer, max_hops=2)
    third = scorer.calls[2]
    assert len(third) == 23
    assert len([key for key in third if key.startswith("n")]) == 20
    assert "n19" in third and "n20" not in third
    assert len(scorer.evidence[2]) == 3
    assert all("previous_jev_score" in item for item in scorer.evidence[2])


@pytest.mark.asyncio
async def test_zero_seed_weights_and_interleave_are_deterministic(tmp_path):
    scope, config, adapter = await _scope(
        tmp_path,
        [
            _candidate("a", "viking://resources/a", "alpha bridge"),
            _candidate("b", "viking://resources/b", "bridge"),
        ],
    )
    seed = (await scope.query('"alpha"', limit=1))[0]
    seed["_score"] = 0.0
    terms = await expansion_terms(scope, "alpha", [seed], 20)
    assert terms
    assert all(term.score >= 0 for term in terms)
    assert [
        x["id"] for x in interleave([[{"id": "a"}, {"id": "c"}], [{"id": "b"}, {"id": "c"}]], 4)
    ] == ["a", "b", "c"]
    adapter.close()
