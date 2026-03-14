# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""Gemini Embedding 2 provider using the official google-genai SDK."""

from typing import Any, Dict, List, Optional

from google import genai
from google.genai import types
from google.genai.errors import APIError

import logging

from openviking.models.embedder.base import (
    DenseEmbedderBase,
    EmbedResult,
    truncate_and_normalize,
)

logger = logging.getLogger("gemini_embedders")

_SUPPORTED_MULTIMODAL_MIMES = frozenset({
    # Images
    "image/jpeg",
    "image/png",
    "image/gif",
    "image/webp",
    # Audio
    "audio/mpeg",
    "audio/mp3",
    "audio/wav",
    "audio/ogg",
    "audio/flac",
    # Video
    "video/mp4",
    "video/mpeg",
    "video/mov",
    "video/avi",
    "video/webm",
    "video/wmv",
    "video/3gpp",
    # Documents
    "application/pdf",
})

_TEXT_BATCH_SIZE = 100

# Maximum input tokens per Gemini embedding request (model hard limit).
_GEMINI_INPUT_TOKEN_LIMIT = 8192


class GeminiDenseEmbedder(DenseEmbedderBase):
    """Dense embedder backed by Google's Gemini Embedding 2 model.

    Input token limit: 8,192 tokens per request.
    Output dimension: 128–3072 (recommended: 768, 1536, 3072; default: 3072).
    """

    KNOWN_DIMENSIONS: Dict[str, int] = {
        "gemini-embedding-2-preview": 3072,
        "gemini-embedding-001": 3072,
        "text-embedding-004": 768,
    }

    def __init__(
        self,
        model_name: str = "gemini-embedding-2-preview",
        api_key: Optional[str] = None,
        dimension: Optional[int] = None,
        task_type: Optional[str] = None,
        config: Optional[Dict[str, Any]] = None,
    ):
        super().__init__(model_name, config)
        if not api_key:
            raise ValueError("Gemini provider requires api_key")
        self.client = genai.Client(api_key=api_key)
        self.task_type = task_type
        self._dimension = dimension or self.KNOWN_DIMENSIONS.get(model_name, 3072)
        config_kwargs: Dict[str, Any] = {"output_dimensionality": self._dimension}
        if self.task_type:
            config_kwargs["task_type"] = self.task_type
        self._embed_config = types.EmbedContentConfig(**config_kwargs)

    @property
    def supports_multimodal(self) -> bool:
        return True

    def embed(self, text: str) -> EmbedResult:
        try:
            result = self.client.models.embed_content(
                model=self.model_name,
                contents=text,
                config=self._embed_config,
            )
            vector = truncate_and_normalize(list(result.embeddings[0].values), self._dimension)
            return EmbedResult(dense_vector=vector)
        except APIError as e:
            raise RuntimeError(f"Gemini embedding failed (code={e.code}): {e}") from e

    def embed_multimodal(self, vectorize: "Vectorize") -> EmbedResult:  # type: ignore[name-defined]
        media = getattr(vectorize, "media", None)
        if (
            media is None
            or media.data is None
            or media.mime_type not in _SUPPORTED_MULTIMODAL_MIMES
        ):
            return self.embed(vectorize.text)

        parts: List[Any] = []
        if vectorize.text:
            parts.append(types.Part.from_text(text=vectorize.text))
        parts.append(types.Part.from_bytes(data=media.data, mime_type=media.mime_type))

        try:
            result = self.client.models.embed_content(
                model=self.model_name,
                contents=[types.Content(parts=parts)],
                config=self._embed_config,
            )
            vector = truncate_and_normalize(list(result.embeddings[0].values), self._dimension)
            return EmbedResult(dense_vector=vector)
        except APIError as e:
            if e.code in (429, 502, 503, 504):
                raise RuntimeError(f"Gemini transient error (code={e.code}), caller should retry") from e
            logger.warning(
                f"Gemini multimodal embed failed (code={e.code}) for {media.uri!r} — "
                f"falling back to text. [multimodal_fallback=True]"
            )
            return self.embed(vectorize.text)

    def embed_batch(self, texts: List[str]) -> List[EmbedResult]:
        if not texts:
            return []
        results: List[EmbedResult] = []
        for i in range(0, len(texts), _TEXT_BATCH_SIZE):
            batch = texts[i : i + _TEXT_BATCH_SIZE]
            try:
                response = self.client.models.embed_content(
                    model=self.model_name,
                    contents=batch,
                    config=self._embed_config,
                )
                for emb in response.embeddings:
                    vector = truncate_and_normalize(list(emb.values), self._dimension)
                    results.append(EmbedResult(dense_vector=vector))
            except APIError as e:
                logger.warning(
                    f"Gemini batch embed failed (code={e.code}) for batch of {len(batch)}, "
                    "falling back to individual calls"
                )
                for text in batch:
                    results.append(self.embed(text))
        return results

    def get_dimension(self) -> int:
        return self._dimension

    def close(self):
        if hasattr(self.client, "_http_client"):
            try:
                self.client._http_client.close()
            except Exception:
                pass
