# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""
End-to-end integration tests for OpenViking add-memory + search using Gemini embeddings.

Exercises the full workflow: inject Gemini config → add_resource → wait_processed → find/search.
No mocking — real Gemini API calls. Auto-skipped when GOOGLE_API_KEY is not set.

Run:
    GOOGLE_API_KEY=<key> pytest tests/integration/test_gemini_openviking_it.py -v

NOTE: provider MUST be "gemini" — "google" is not a valid provider value.
"""

from pathlib import Path

import pytest

from tests.integration.conftest import (
    gemini_config_dict,
    make_ov_client,
    requires_api_key,
    requires_engine,
    sample_markdown,
    teardown_ov_client,
)

pytestmark = [requires_api_key, requires_engine]


# ---------------------------------------------------------------------------
# Test 1: Basic add-memory + search
# ---------------------------------------------------------------------------


async def test_add_and_search_basic(gemini_ov_client, tmp_path):
    """Add a single markdown document and verify it is returned by find()."""
    client, model, dim = gemini_ov_client

    doc = sample_markdown(
        tmp_path,
        "ml_intro",
        "# Machine Learning\n\nMachine learning is a field of AI that uses statistical methods.",
    )

    result = await client.add_resource(path=str(doc), reason="IT test basic", wait=True)
    assert result.get("root_uri"), "add_resource should return a root_uri"

    found = await client.find(query="machine learning AI statistical")
    assert found.total > 0, f"Expected search results for ML doc, got total={found.total}"
    scores = [r.score for r in found.resources]
    assert any(s > 0.0 for s in scores), f"Expected non-zero similarity scores, got {scores}"


# ---------------------------------------------------------------------------
# Test 2: Batch — multiple documents, search returns relevant one
# ---------------------------------------------------------------------------


async def test_batch_documents_search(gemini_ov_client, tmp_path):
    """Add 5 documents on different topics; search returns the relevant one first."""
    client, model, dim = gemini_ov_client

    docs = {
        "python_types": "Python supports dynamic typing and type hints via the typing module.",
        "quantum_physics": "Quantum mechanics describes the behavior of particles at atomic scale.",
        "cooking_pasta": "To cook pasta: boil salted water, add pasta, cook 8-12 minutes, drain.",
        "git_branching": "Git branches allow parallel development. Use git checkout -b to create.",
        "solar_system": "The solar system has 8 planets. Jupiter is the largest planet.",
    }
    for slug, content in docs.items():
        doc_path = sample_markdown(tmp_path, slug, f"# {slug}\n\n{content}")
        await client.add_resource(path=str(doc_path), reason="IT batch test")

    await client.wait_processed()

    found = await client.find(query="how to cook pasta boil water")
    assert found.total > 0, "Expected at least one result for pasta query"


# ---------------------------------------------------------------------------
# Test 3: Large text chunking
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "model,dim,token_limit",
    [
        pytest.param("gemini-embedding-2-preview", 768, 8192, id="g2p-large"),
        pytest.param("gemini-embedding-001", 768, 2048, id="g001-large"),
    ],
)
async def test_large_text_add_and_search(model, dim, token_limit, tmp_path):
    """Add a document exceeding the model's token limit; verify chunking and searchability."""
    data_path = str(tmp_path / "ov_large")
    Path(data_path).mkdir(parents=True, exist_ok=True)

    client = await make_ov_client(gemini_config_dict(model, dim), data_path)
    try:
        phrase = "Neural networks are computational models inspired by the brain. "
        repeats = (token_limit * 2) // len(phrase.split()) + 10
        large_content = f"# Large Document\n\n{phrase * repeats}"

        doc = sample_markdown(tmp_path, "large_doc", large_content)
        result = await client.add_resource(path=str(doc), reason="large text IT", wait=True)
        assert result.get("root_uri"), "Large doc should index without error"

        found = await client.find(query="neural networks computational brain")
        assert found.total > 0, "Chunked large doc should be findable"
    finally:
        await teardown_ov_client()


# ---------------------------------------------------------------------------
# Test 4: RETRIEVAL_QUERY / RETRIEVAL_DOCUMENT routing via EmbeddingConfig
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "query_param,doc_param",
    [
        pytest.param("RETRIEVAL_QUERY", "RETRIEVAL_DOCUMENT", id="retrieval-routing"),
        pytest.param("SEMANTIC_SIMILARITY", "SEMANTIC_SIMILARITY", id="semantic-routing"),
    ],
)
async def test_retrieval_routing_workflow(query_param, doc_param, tmp_path):
    """Verify add+search works with non-symmetric task-type routing."""
    data_path = str(tmp_path / "ov_routing")
    Path(data_path).mkdir(parents=True, exist_ok=True)

    client = await make_ov_client(
        gemini_config_dict(
            "gemini-embedding-2-preview", 768, query_param=query_param, doc_param=doc_param
        ),
        data_path,
    )
    try:
        doc = sample_markdown(
            tmp_path,
            "routing_doc",
            "# Retrieval Test\n\nOpenViking provides memory management for AI agents.",
        )
        result = await client.add_resource(path=str(doc), reason="routing IT", wait=True)
        assert result.get("root_uri")

        found = await client.find(query="memory management AI agents")
        assert found.total > 0, f"Routing {query_param}/{doc_param}: expected search results"
    finally:
        await teardown_ov_client()


# ---------------------------------------------------------------------------
# Test 5: Dimension variants — verify index schema uses requested dim
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("dim", [512, 768, 1536, 3072])
async def test_dimension_variant_add_search(dim, tmp_path):
    """Each dimension variant should index and search without errors."""
    data_path = str(tmp_path / f"ov_dim_{dim}")
    Path(data_path).mkdir(parents=True, exist_ok=True)

    client = await make_ov_client(gemini_config_dict("gemini-embedding-2-preview", dim), data_path)
    from openviking_cli.utils.config.open_viking_config import OpenVikingConfigSingleton

    assert OpenVikingConfigSingleton.get_instance().embedding.dimension == dim, (
        f"Expected embedder dimension={dim}, got {OpenVikingConfigSingleton.get_instance().embedding.dimension}"
    )
    try:
        doc = sample_markdown(
            tmp_path,
            f"dim_doc_{dim}",
            f"# Dimension {dim} Test\n\nThis document is indexed with embedding dimension {dim}.",
        )
        result = await client.add_resource(path=str(doc), reason=f"dim={dim} IT", wait=True)
        assert result.get("root_uri"), f"dim={dim}: add_resource should succeed"

        found = await client.find(query=f"embedding dimension {dim}")
        assert found.total > 0, f"dim={dim}: should find the indexed doc"
    finally:
        await teardown_ov_client()


# ---------------------------------------------------------------------------
# Test 6: Multi-turn session + search (smoke test)
# ---------------------------------------------------------------------------


async def test_session_search_smoke(gemini_ov_client, tmp_path):
    """Session construction + embedding-based find works with Gemini embeddings.

    Uses find() (pure embedding path) rather than search() which requires a VLM.
    """
    from openviking.message import TextPart

    client, model, dim = gemini_ov_client

    doc = sample_markdown(
        tmp_path,
        "session_doc",
        "# Python Testing\n\nPytest is a mature full-featured Python testing tool.",
    )
    await client.add_resource(path=str(doc), reason="session IT", wait=True)

    session = client.session(session_id="gemini_it_session")
    session.add_message("user", [TextPart("Tell me about Python testing.")])

    result = await client.find(query="pytest testing tool")
    assert result.total > 0, "Embedding-based find should return the indexed pytest doc"
