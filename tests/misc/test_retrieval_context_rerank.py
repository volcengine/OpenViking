# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

from types import SimpleNamespace

from openviking.models.rerank import CohereRerankClient
from openviking.retrieve.rerank_scoring import Reranker
from openviking.service.search_service import SearchService
from openviking_cli.utils.config.open_viking_config import OpenVikingConfig
from openviking_cli.utils.config.rerank_config import RerankConfig
from openviking_cli.utils.config.retrieval_config import RetrievalConfig

COHERE = RerankConfig(provider="cohere", api_key="key", threshold=0.3, max_input_tokens=512)


def _service(*, apply_rerank_to_context, rerank_config=COHERE):
    fs = SimpleNamespace(
        retrieval_config=RetrievalConfig(apply_rerank_to_context=apply_rerank_to_context),
        rerank_config=rerank_config,
    )
    return SearchService(fs)


def test_apply_rerank_to_context_defaults_off():
    assert RetrievalConfig().apply_rerank_to_context is False
    assert OpenVikingConfig.from_dict({}).retrieval.apply_rerank_to_context is False


def test_apply_rerank_to_context_loads_from_config():
    config = OpenVikingConfig.from_dict({"retrieval": {"apply_rerank_to_context": True}})

    assert config.retrieval.apply_rerank_to_context is True


def test_context_reranker_is_off_without_the_flag():
    assert _service(apply_rerank_to_context=False).context_reranker() is None
    assert SearchService().context_reranker() is None
    assert SearchService(SimpleNamespace(retrieval_config=None)).context_reranker() is None


def test_context_reranker_needs_a_usable_rerank_config():
    assert _service(apply_rerank_to_context=True, rerank_config=None).context_reranker() is None
    assert (
        _service(apply_rerank_to_context=True, rerank_config=RerankConfig()).context_reranker()
        is None
    )


def test_context_reranker_carries_the_rerank_config():
    reranker = _service(apply_rerank_to_context=True).context_reranker()

    assert isinstance(reranker, Reranker)
    assert isinstance(reranker.client, CohereRerankClient)
    assert reranker.threshold == 0.3
    assert reranker.max_input_tokens == 512
