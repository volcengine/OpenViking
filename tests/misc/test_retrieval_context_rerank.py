# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

from types import SimpleNamespace

import pytest

from openviking.models.rerank import CohereRerankClient
from openviking.service.search_service import SearchService
from openviking.utils.token_estimation import estimate_text_tokens
from openviking_cli.utils.config.open_viking_config import OpenVikingConfig

COHERE = {"provider": "cohere", "api_key": "key", "threshold": 0.3, "max_input_tokens": 128}


@pytest.mark.parametrize(
    "options",
    [
        pytest.param({"rerank": COHERE}, id="opt-in-required"),
        pytest.param(
            {"rerank": {}, "retrieval": {"apply_rerank_to_context": True}},
            id="provider-required",
        ),
    ],
)
def test_context_rerank_stays_off_without_both_opt_in_and_provider(options):
    config = OpenVikingConfig.from_dict(options)

    assert (
        SearchService(
            SimpleNamespace(retrieval_config=config.retrieval, rerank_config=config.rerank)
        ).context_reranker()
        is None
    )


async def test_context_rerank_uses_the_configured_provider_threshold_and_budget(monkeypatch):
    config = OpenVikingConfig.from_dict(
        {"rerank": COHERE, "retrieval": {"apply_rerank_to_context": True}}
    )
    received = []

    def rerank_batch(self, query, documents):
        received.append((query, documents))
        return [0.85]

    monkeypatch.setattr(CohereRerankClient, "rerank_batch", rerank_batch)
    service = SearchService(
        SimpleNamespace(retrieval_config=config.retrieval, rerank_config=config.rerank)
    )
    reranker = service.context_reranker()
    assert reranker is not None
    scored = await reranker.score("roll back", ["Context " + "details " * 200], [0.2])

    assert scored.model_used
    assert scored.scores == [0.85]
    assert reranker.threshold == 0.3
    ((query, (document,)),) = received
    assert estimate_text_tokens(query) + estimate_text_tokens(document) <= 128
