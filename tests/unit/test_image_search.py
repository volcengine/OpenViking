from openviking.models.embedder.base import DenseEmbedderBase, EmbedResult
from openviking.utils.image_search import (
    build_multimodal_embedding_input,
    image_bytes_to_data_uri,
    normalize_client_image_input,
)


class TextOnlyEmbedder(DenseEmbedderBase):
    def embed(self, content, is_query=False):
        return EmbedResult(dense_vector=[1.0])

    def get_dimension(self) -> int:
        return 1


class MultimodalEmbedder(TextOnlyEmbedder):
    @property
    def supports_multimodal(self) -> bool:
        return True


def test_text_only_embedder_drops_image_parts():
    parts = build_multimodal_embedding_input(
        "cat photo",
        "data:image/png;base64,abc",
    )

    assert TextOnlyEmbedder("test").prepare_embedding_input(parts) == "cat photo"
    assert MultimodalEmbedder("test").prepare_embedding_input(parts) == parts


def test_image_helpers_accept_base64_bytes_and_image_uris(tmp_path):
    image_path = tmp_path / "sample.jpg"
    image_path.write_bytes(b"jpg")

    assert image_bytes_to_data_uri(b"png").startswith("data:image/png;base64,")
    assert normalize_client_image_input(image_path).startswith("data:image/jpeg;base64,")
