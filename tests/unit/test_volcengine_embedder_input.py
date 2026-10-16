# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Embedding output configuration and Ark request contracts."""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from openviking.models.embedder.volcengine_embedders import VolcengineEmbedder
from openviking_cli.utils.config.config_loader import load_json_config
from openviking_cli.utils.config.embedding_config import EmbeddingConfig

_MULTIMODAL_INPUT = [
    {"type": "text", "text": "diagram of a heat exchanger"},
    {"type": "image_url", "image_url": {"url": "https://example.test/image.png"}},
    {"type": "video_url", "video_url": {"url": "https://example.test/video.mp4"}},
]


def test_output_configuration_validates_model_and_every_credential(tmp_path, monkeypatch):
    values = dict(
        provider="volcengine",
        model="doubao-embedding-vision-251215",
        api_key="key",
        input="text",
        output="dense_sparse",
    )
    legacy_model = {
        "provider": "volcengine",
        "api_key": "$EMBEDDING_API_KEY",
        "model": "doubao-embedding-vision-251215",
        "api_base": "https://ark.cn-beijing.volces.com/api/v3",
        "dimension": 1024,
        "input": "multimodal",
    }
    path = tmp_path / "ov.conf"
    path.write_text(json.dumps({"embedding": {"dense": legacy_model, "max_retries": 7}}))
    monkeypatch.setenv("EMBEDDING_API_KEY", "resolved-key")
    legacy = EmbeddingConfig(**load_json_config(path)["embedding"])
    flat = EmbeddingConfig(**{**legacy_model, "api_key": "resolved-key", "max_retries": 7})
    assert legacy.model_dump() == flat.model_dump()
    assert legacy.output == "dense" and legacy.input == "multimodal"
    assert "dense" not in legacy.model_dump()
    assert json.loads(path.read_text())["embedding"]["dense"] == legacy_model

    config = EmbeddingConfig(**values)
    assert config.output == "dense_sparse"
    assert EmbeddingConfig().output == "dense"
    assert (
        EmbeddingConfig(**{**values, "model": "doubao-embedding-vision-250615"}).output
        == "dense_sparse"
    )
    assert (
        EmbeddingConfig(**{**values, "credentials": [{"model": "ep-deployment"}]}).output
        == "dense_sparse"
    )
    assert (
        EmbeddingConfig(
            provider="vikingdb",
            model="bge-m3",
            ak="ak",
            sk="sk",
            region="cn-beijing",
            input="text",
            output="dense_sparse",
        ).dimension
        == 1024
    )
    for invalid in [
        {**values, "output": "sparse"},
        {**values, "input": "multimodal"},
        {**values, "model": "doubao-embedding-vision-241215"},
        {**values, "model": "ep-unknown"},
        {**values, "provider": "openai"},
        {
            **values,
            "credentials": [
                {"api_key": "first"},
                {"provider": "openai", "model": "text-embedding-3-small"},
            ],
        },
        {
            **values,
            "credentials": [{"api_key": "first"}, {"model": "doubao-embedding-vision-241215"}],
        },
        {"dense": values},
        {"dense": legacy_model, "model": "another-model"},
        {"dense": legacy_model, "sparse": values},
        {"dense": None},
        {"hybrid": values},
        {"sparse": values},
    ]:
        with pytest.raises(ValueError):
            EmbeddingConfig(**invalid)


@pytest.mark.parametrize("output", ["dense", "dense_sparse"])
@pytest.mark.parametrize("use_async", [False, True])
async def test_ark_output_contract(output, use_async):
    response = SimpleNamespace(
        usage=SimpleNamespace(prompt_tokens=3, total_tokens=3),
        data=SimpleNamespace(
            embedding=[1.0, 0.0], sparse_embedding=[SimpleNamespace(index=0, value=0.5)]
        ),
    )
    with patch("volcenginesdkarkruntime.Ark") as ark:
        embedder = VolcengineEmbedder(
            model_name="doubao-embedding-vision-251215",
            api_key="key",
            input_type="text",
            output=output,
            dimension=2,
        )
    client = MagicMock()
    client.multimodal_embeddings.create = (
        AsyncMock(return_value=response) if use_async else MagicMock(return_value=response)
    )
    if use_async:
        embedder._get_async_client = lambda: client
        result = await embedder.embed_async("hello")
    else:
        ark.return_value.multimodal_embeddings.create = client.multimodal_embeddings.create
        result = embedder.embed("hello")
    assert result.dense_vector == [1.0, 0.0]
    assert result.sparse_vector == ({"0": 0.5} if output == "dense_sparse" else None)
    request = client.multimodal_embeddings.create.call_args.kwargs
    assert request["input"] == [{"type": "text", "text": "hello"}]
    assert request.get("sparse_embedding") == (
        {"type": "enabled"} if output == "dense_sparse" else None
    )
    if output == "dense_sparse":
        with pytest.raises(ValueError, match="only accepts text"):
            embedder.prepare_embedding_input(_MULTIMODAL_INPUT)
        response.data.sparse_embedding = None
        with pytest.raises(ValueError, match="missing requested sparse"):
            if use_async:
                await embedder.embed_async("hello")
            else:
                embedder.embed("hello")


@patch("openviking.models.embedder.volcengine_embedders.volcenginesdkarkruntime.Ark")
@patch("openviking.models.embedder.volcengine_embedders.time.perf_counter")
@patch("openviking.models.embedder.volcengine_embedders.get_current_telemetry")
def test_dense_embedder_passes_local_provider_duration_to_metrics(
    _telemetry, mock_perf_counter, mock_ark
):
    response = SimpleNamespace(
        usage=SimpleNamespace(prompt_tokens=3, total_tokens=3),
        data=[SimpleNamespace(embedding=[1.0, 0.0])],
    )
    client = MagicMock()
    client.embeddings.create.return_value = response
    mock_ark.return_value = client
    mock_perf_counter.side_effect = [100.0, 100.25]
    embedder = VolcengineEmbedder(
        model_name="test", api_key="test-key", input_type="text", dimension=2
    )
    embedder.update_token_usage = MagicMock()
    embedder.embed("hello")
    assert embedder.update_token_usage.call_args.kwargs["duration_seconds"] == 0.25
