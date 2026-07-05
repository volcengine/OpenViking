# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
import logging
from typing import Any, Dict, List, Optional, Tuple, TypedDict

logger = logging.getLogger(__name__)


class DenseMeta(TypedDict, total=False):
    ModelName: str
    Version: str
    Dim: int
    TextField: str
    ImageField: str
    VideoField: str


class SparseMeta(TypedDict, total=False):
    ModelName: str
    Version: str


class VectorizeMeta(TypedDict, total=False):
    Dense: DenseMeta
    Sparse: SparseMeta


def _safe_vectorizer_config(vectorizer: Any) -> Optional[Dict[str, Any]]:
    try:
        config = getattr(vectorizer, "config", None)
    except AttributeError:
        return None
    except Exception as exc:
        raise RuntimeError("failed to read vectorizer config") from exc
    if config is None:
        return None
    return config if isinstance(config, dict) else None


def _resolve_runtime_max_input_tokens(config: Optional[Dict[str, Any]]) -> Optional[int]:
    if config is None:
        return None
    raw_value = config.get("max_input_tokens")
    if raw_value is None or isinstance(raw_value, bool):
        if isinstance(raw_value, bool):
            logger.warning("Invalid vectorizer max_input_tokens=%r; limit disabled", raw_value)
        return None
    if isinstance(raw_value, int):
        max_input_tokens = raw_value
    elif isinstance(raw_value, str) and raw_value.strip().isdigit():
        max_input_tokens = int(raw_value)
    else:
        logger.warning("Invalid vectorizer max_input_tokens=%r; limit disabled", raw_value)
        return None
    return max_input_tokens if max_input_tokens > 0 else None


def _truncate_provider_text(text: str, max_input_tokens: int) -> str:
    try:
        from openviking.utils.embedding_input import truncate_embedding_input
    except ImportError as exc:
        raise RuntimeError("embedding input truncation is unavailable") from exc

    return truncate_embedding_input(text, max_input_tokens)


class VectorizerAdapter:
    """Adapter for vectorizer to handle data vectorization.

    Adapts the base vectorizer to work with specific collection configuration,
    managing field mapping and model parameters.
    """

    def __init__(self, vectorizer: Any, vectorize_meta: VectorizeMeta):
        """Initialize the VectorizerAdapter.

        Args:
            vectorizer: The underlying vectorizer instance.
            vectorize_meta (VectorizeMeta): Configuration for vectorization,
                including model names, versions, and field mappings.
        """
        dense_meta = vectorize_meta.get("Dense", {})
        self.text_field = dense_meta.get("TextField", "")
        self.image_field = dense_meta.get("ImageField", "")
        self.video_field = dense_meta.get("VideoField", "")
        self.vectorizer = vectorizer
        self.max_input_tokens = _resolve_runtime_max_input_tokens(
            _safe_vectorizer_config(vectorizer)
        )
        sparse_meta = vectorize_meta.get("Sparse", {})
        self.dense_model = {
            "name": dense_meta.get("ModelName", ""),
            "version": dense_meta.get("Version", "default"),
        }
        if "Dim" in dense_meta:
            self.dense_model["dim"] = int(dense_meta["Dim"])
        self.sparse_model = (
            {
                "name": sparse_meta.get("ModelName", ""),
                "version": sparse_meta.get("Version", "default"),
            }
            if sparse_meta
            else {}
        )
        self.dim = self.vectorizer.get_dense_vector_dim(self.dense_model, self.sparse_model)

    def _prepare_text(self, text: Any) -> Any:
        if self.max_input_tokens is None or not isinstance(text, str):
            return text
        # The vectorizer provider request has one text slot shared by dense and
        # sparse generation. Keep stored raw fields untouched, but bound the
        # provider-facing text so one oversized file cannot fail the whole write.
        return _truncate_provider_text(text, self.max_input_tokens)

    def get_dim(self) -> int:
        """Get the dimension of the dense vector.

        Returns:
            int: The dimension of the dense vector.
        """
        return self.dim

    def vectorize_raw_data(
        self, raw_data_list: List[Dict[str, Any]]
    ) -> Tuple[List[List[float]], List[Dict[str, float]]]:
        """Vectorize a list of raw data items.

        Args:
            raw_data_list (List[Dict[str, Any]]): List of data dictionaries to vectorize.

        Returns:
            Tuple[List[List[float]], List[Dict[str, float]]]: A tuple containing:
                - List of dense vectors.
                - List of sparse vectors (dictionaries of term-weight pairs).
        """
        data_list = []
        for raw_data in raw_data_list:
            data = {}
            if self.text_field in raw_data:
                data["text"] = self._prepare_text(raw_data[self.text_field])
            if self.image_field in raw_data:
                data["image"] = raw_data[self.image_field]
            if self.video_field in raw_data:
                data["video"] = raw_data[self.video_field]
            data_list.append(data)
        result = self.vectorizer.vectorize_document(data_list, self.dense_model, self.sparse_model)
        return result.dense_vectors, result.sparse_vectors

    def vectorize_one(
        self, text: Optional[str] = None, image: Optional[Any] = None, video: Optional[Any] = None
    ) -> Tuple[Optional[List[float]], Optional[Dict[str, float]]]:
        """Vectorize a single item.

        Args:
            text (Optional[str]): Text content to vectorize.
            image (Optional[Any]): Image content to vectorize.
            video (Optional[Any]): Video content to vectorize.

        Returns:
            Tuple[Optional[List[float]], Optional[Dict[str, float]]]: A tuple containing:
                - Dense vector (or None if not generated).
                - Sparse vector (or None if not generated).
        """
        data = {}
        if text:
            data["text"] = self._prepare_text(text)
        if image:
            data["image"] = image
        if video:
            data["video"] = video
        result = self.vectorizer.vectorize_document([data], self.dense_model, self.sparse_model)
        return result.dense_vectors[0] if result.dense_vectors else None, (
            result.sparse_vectors[0] if result.sparse_vectors else None
        )
