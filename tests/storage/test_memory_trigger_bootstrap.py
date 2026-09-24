from unittest.mock import AsyncMock

import pytest

from openviking.storage.collection_schemas import init_context_collection
from tests.storage.test_collection_schemas import _DummyConfig, _DummyEmbedder


@pytest.mark.asyncio
async def test_auxiliary_collection_bootstrap_keeps_shared_embedding_schema(monkeypatch):
    config = _DummyConfig(_DummyEmbedder())
    monkeypatch.setattr("openviking_cli.utils.config.get_openviking_config", lambda: config)
    storage = type("Storage", (), {})()
    storage.create_collection = AsyncMock(return_value=True)
    assert await init_context_collection(storage, name="context_memory_triggers")
    name, schema = storage.create_collection.call_args.args
    assert name == "context_memory_triggers"
    assert "[openviking.embedding]" in schema["Description"]
    assert config.storage.vectordb.name == "context"
