"""Run the API test server with mock models when CI has no model credentials."""

import hashlib
import math
from unittest.mock import patch

from openviking.models.embedder.base import DenseEmbedderBase, EmbedResult
from openviking.models.vlm.base import VLMBase, VLMResponse
from openviking.server.bootstrap import main
from openviking_cli.utils.config.embedding_config import EmbeddingConfig
from openviking_cli.utils.config.vlm_config import VLMConfig


class MockEmbedder(DenseEmbedderBase):
    def __init__(self, dimension: int):
        super().__init__(model_name="api-test-mock-embedding")
        self.dimension = dimension

    def embed(self, content, is_query: bool = False) -> EmbedResult:
        digest = hashlib.shake_256(str(content).encode("utf-8")).digest(self.dimension)
        vector = [value / 255.0 - 0.5 for value in digest]
        norm = math.sqrt(sum(value * value for value in vector))
        return EmbedResult(dense_vector=[value / norm for value in vector])

    def get_dimension(self) -> int:
        return self.dimension


class MockVLM(VLMBase):
    def __init__(self):
        super().__init__({"provider": "mock", "model": "api-test-mock-vlm"})

    def get_completion(self, prompt: str = "", **kwargs) -> str | VLMResponse:
        content = "API test mock summary."
        if prompt.lstrip().startswith("Analyze the following skill content"):
            # The API/CLI skill fixture contains no private configuration values.
            content = '{"values": {}}'
        elif any(
            "Output Format: restricted Python memory SDK" in str(message.get("content", ""))
            for message in kwargs.get("messages") or []
        ):
            content = "sdk.commit()"
        if kwargs.get("tools") is not None:
            return VLMResponse(content=content)
        return content

    async def get_completion_async(self, prompt: str = "", **kwargs) -> str | VLMResponse:
        return self.get_completion(prompt, **kwargs)

    def get_vision_completion(self, prompt: str = "", **kwargs) -> str | VLMResponse:
        return self.get_completion(prompt, **kwargs)

    async def get_vision_completion_async(self, prompt: str = "", **kwargs) -> str | VLMResponse:
        return self.get_completion(prompt, **kwargs)


def get_mock_embedder(config: EmbeddingConfig) -> MockEmbedder:
    if not config.dense or not config.dense.dimension or config.sparse or config.hybrid:
        raise ValueError("API mock server requires a dense embedding dimension")
    return MockEmbedder(config.dense.dimension)


if __name__ == "__main__":
    mock_vlm = MockVLM()
    with (
        patch.object(EmbeddingConfig, "get_embedder", get_mock_embedder),
        patch.object(VLMConfig, "get_vlm_instance", lambda self: mock_vlm),
    ):
        main()
