# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
from enum import Enum
from typing import Any, Dict

from openviking.storage.vectordb.vectorize.base import BaseVectorizer
from openviking.storage.vectordb.vectorize.volcengine_vectorizer import VolcengineVectorizer


class ModelType(Enum):
    """Model type enumeration."""

    LOCAL = "local"  # Local model
    HTTP = "http"  # HTTP remote model
    VOLCENGINE = "Volcengine"  # Volcengine remote model


class VectorizerFactory:
    """Vectorizer factory."""

    _registry: Dict[str, type] = {}

    @classmethod
    def register(cls, model_type: ModelType, vectorizer_class: type):
        """Register vectorizer class."""
        cls._registry[model_type.value.lower()] = vectorizer_class
        # print(f"Register vectorizer {vectorizer_class.__name__} for model type {model_type.value.lower()}")

    @classmethod
    def create(
        cls, config: Dict[str, Any], model_type: ModelType = ModelType.VOLCENGINE
    ) -> BaseVectorizer:
        """
        Create vectorizer instance.

        Args:
            model_type: Model type (local/http/grpc)
            config: Configuration dictionary

        Returns:
            BaseVectorizer instance
        """
        vectorizer_class = cls._registry.get(model_type.value.lower())
        if not vectorizer_class:
            print(
                f"Unknown model type: {model_type.value.lower()}. Available: {list(cls._registry.keys())}"
            )
            raise ValueError(
                f"Unknown model type: {model_type}. Available: {list(cls._registry.keys())}"
            )

        return vectorizer_class(config)


VectorizerFactory.register(ModelType.VOLCENGINE, VolcengineVectorizer)
