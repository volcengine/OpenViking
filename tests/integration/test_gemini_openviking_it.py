# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""End-to-end Service tests for local VectorDB with Gemini embeddings."""

from pathlib import Path

import pytest

from tests.integration.conftest import (
    gemini_config_dict,
    make_ov_service,
    requires_api_key,
    requires_engine,
    sample_markdown,
    teardown_ov_service,
)

pytestmark = [requires_api_key, requires_engine]


async def test_add_and_search_basic(gemini_ov_service, tmp_path):
    service, ctx, _model, _dim = gemini_ov_service
    doc = sample_markdown(
        tmp_path,
        "ml_intro",
        "# Machine Learning\n\nMachine learning is a field of AI that uses statistical methods.",
    )

    result = await service.resources.add_resource(
        path=str(doc),
        ctx=ctx,
        reason="IT test basic",
        wait=True,
    )
    assert result.get("root_uri")

    found = await service.search.find(query="machine learning AI statistical", ctx=ctx)
    assert found.total > 0
    scores = [resource.score for resource in found.resources]
    assert any(score > 0.0 for score in scores)


async def test_batch_documents_search(gemini_ov_service, tmp_path):
    service, ctx, _model, _dim = gemini_ov_service
    docs = {
        "python_types": "Python supports dynamic typing and type hints via the typing module.",
        "quantum_physics": "Quantum mechanics describes particles at atomic scale.",
        "cooking_pasta": "To cook pasta: boil salted water, add pasta, cook, and drain.",
        "git_branching": "Git branches allow parallel development.",
        "solar_system": "The solar system has 8 planets. Jupiter is the largest.",
    }
    for slug, content in docs.items():
        doc = sample_markdown(tmp_path, slug, f"# {slug}\n\n{content}")
        await service.resources.add_resource(path=str(doc), ctx=ctx, reason="IT batch test")

    await service.resources.wait_processed()
    found = await service.search.find(query="how to cook pasta boil water", ctx=ctx)
    assert found.total > 0


@pytest.mark.parametrize(
    "model,dim,token_limit",
    [
        pytest.param("gemini-embedding-2-preview", 768, 8192, id="g2p-large"),
        pytest.param("gemini-embedding-001", 768, 2048, id="g001-large"),
    ],
)
async def test_large_text_add_and_search(model, dim, token_limit, tmp_path):
    data_path = str(tmp_path / "ov_large")
    Path(data_path).mkdir(parents=True, exist_ok=True)
    service, ctx = await make_ov_service(gemini_config_dict(model, dim), data_path)
    try:
        phrase = "Neural networks are computational models inspired by the brain. "
        repeats = (token_limit * 2) // len(phrase.split()) + 10
        doc = sample_markdown(tmp_path, "large_doc", f"# Large Document\n\n{phrase * repeats}")
        result = await service.resources.add_resource(
            path=str(doc),
            ctx=ctx,
            reason="large text IT",
            wait=True,
        )
        assert result.get("root_uri")
        found = await service.search.find(
            query="neural networks computational brain",
            ctx=ctx,
        )
        assert found.total > 0
    finally:
        await teardown_ov_service(service)


@pytest.mark.parametrize(
    "query_param,doc_param",
    [
        pytest.param("RETRIEVAL_QUERY", "RETRIEVAL_DOCUMENT", id="retrieval-routing"),
        pytest.param("SEMANTIC_SIMILARITY", "SEMANTIC_SIMILARITY", id="semantic-routing"),
    ],
)
async def test_retrieval_routing_workflow(query_param, doc_param, tmp_path):
    service, ctx = await make_ov_service(
        gemini_config_dict(
            "gemini-embedding-2-preview",
            768,
            query_param=query_param,
            doc_param=doc_param,
        ),
        str(tmp_path / "ov_routing"),
    )
    try:
        doc = sample_markdown(
            tmp_path,
            "routing_doc",
            "# Retrieval Test\n\nOpenViking provides memory management for AI agents.",
        )
        result = await service.resources.add_resource(
            path=str(doc),
            ctx=ctx,
            reason="routing IT",
            wait=True,
        )
        assert result.get("root_uri")
        found = await service.search.find(query="memory management AI agents", ctx=ctx)
        assert found.total > 0
    finally:
        await teardown_ov_service(service)


@pytest.mark.parametrize("dim", [512, 768, 1536, 3072])
async def test_dimension_variant_add_search(dim, tmp_path):
    service, ctx = await make_ov_service(
        gemini_config_dict("gemini-embedding-2-preview", dim),
        str(tmp_path / f"ov_dim_{dim}"),
    )
    from openviking_cli.utils.config.open_viking_config import OpenVikingConfigSingleton

    assert OpenVikingConfigSingleton.get_instance().embedding.dimension == dim
    try:
        doc = sample_markdown(
            tmp_path,
            f"dim_doc_{dim}",
            f"# Dimension {dim} Test\n\nThis document uses embedding dimension {dim}.",
        )
        result = await service.resources.add_resource(
            path=str(doc),
            ctx=ctx,
            reason=f"dim={dim} IT",
            wait=True,
        )
        assert result.get("root_uri")
        found = await service.search.find(query=f"embedding dimension {dim}", ctx=ctx)
        assert found.total > 0
    finally:
        await teardown_ov_service(service)


async def test_session_search_smoke(gemini_ov_service, tmp_path):
    from openviking.message import TextPart

    service, ctx, _model, _dim = gemini_ov_service
    doc = sample_markdown(
        tmp_path,
        "session_doc",
        "# Python Testing\n\nPytest is a mature full-featured Python testing tool.",
    )
    await service.resources.add_resource(
        path=str(doc),
        ctx=ctx,
        reason="session IT",
        wait=True,
    )
    session = await service.sessions.create(ctx, session_id="gemini_it_session")
    session.add_message("user", [TextPart("Tell me about Python testing.")])

    result = await service.search.find(query="pytest testing tool", ctx=ctx)
    assert result.total > 0
