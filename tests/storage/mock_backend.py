from openviking.storage.vectordb_adapters.local_adapter import LocalCollectionAdapter


class MockCollectionAdapter(LocalCollectionAdapter):
    @classmethod
    def from_config(cls, config):
        return super().from_config(
            config.model_copy(update={"index_name": config.custom_params["index_name"]})
        )
