# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

from openviking.storage.collection_schemas import CollectionSchemas
from openviking.storage.vectordb_adapters.factory import create_collection_adapter
from openviking_cli.utils.config.vectordb_config import VectorDBBackendConfig


def test_dynamic_loading_mock_adapter():
    name = "custom_collection"
    config = VectorDBBackendConfig(
        backend="tests.storage.mock_backend.MockCollectionAdapter",
        name=name,
        dimension=4,
        custom_params={"index_name": "custom_index"},
    )
    adapter = create_collection_adapter(config)
    try:
        assert adapter.create_collection(
            name,
            CollectionSchemas.context_collection(name, config.dimension),
            distance="cosine",
            sparse_weight=0,
            index_name=adapter.index_name,
        )
        assert adapter.get_collection().has_index("custom_index")
    finally:
        adapter.close()
