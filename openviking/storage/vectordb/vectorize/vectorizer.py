# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
from typing import Any, Dict, List, Optional, Tuple, TypedDict


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
                data["text"] = raw_data[self.text_field]
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
            data["text"] = text
        if image:
            data["image"] = image
        if video:
            data["video"] = video
        result = self.vectorizer.vectorize_document([data], self.dense_model, self.sparse_model)
        return result.dense_vectors[0] if result.dense_vectors else None, (
            result.sparse_vectors[0] if result.sparse_vectors else None
        )
