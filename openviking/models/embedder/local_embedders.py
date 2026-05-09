# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Local GGUF embedders powered by llama-cpp-python."""

from __future__ import annotations

import importlib
import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests

from openviking.models.embedder.base import DenseEmbedderBase, EmbedResult
from openviking.storage.errors import EmbeddingConfigurationError

logger = logging.getLogger(__name__)

DEFAULT_LOCAL_MODEL_CACHE_DIR = "~/.cache/openviking/models"
DEFAULT_LOCAL_DENSE_MODEL = "bge-small-zh-v1.5-f16"
DEFAULT_BGE_ZH_QUERY_INSTRUCTION = "为这个句子生成表示以用于检索相关文章："


@dataclass(frozen=True)
class LocalModelSpec:
    model_name: str
    dimension: int
    filename: str
    download_url: str
    query_instruction: Optional[str] = None


LOCAL_DENSE_MODEL_SPECS: Dict[str, LocalModelSpec] = {
    DEFAULT_LOCAL_DENSE_MODEL: LocalModelSpec(
        model_name=DEFAULT_LOCAL_DENSE_MODEL,
        dimension=512,
        filename="bge-small-zh-v1.5-f16.gguf",
        download_url=(
            "https://huggingface.co/CompendiumLabs/bge-small-zh-v1.5-gguf/resolve/main/"
            "bge-small-zh-v1.5-f16.gguf?download=true"
        ),
        query_instruction=DEFAULT_BGE_ZH_QUERY_INSTRUCTION,
    )
}


def get_local_model_spec(model_name: str) -> LocalModelSpec:
    try:
        return LOCAL_DENSE_MODEL_SPECS[model_name]
    except KeyError as exc:
        raise ValueError(
            f"Unknown local embedding model '{model_name}'. "
            f"Supported models: {list(LOCAL_DENSE_MODEL_SPECS.keys())}"
        ) from exc


def get_local_model_default_dimension(model_name: str) -> int:
    return get_local_model_spec(model_name).dimension


def get_local_model_cache_path(model_name: str, cache_dir: Optional[str] = None) -> Path:
    spec = get_local_model_spec(model_name)
    cache_root = Path(cache_dir or DEFAULT_LOCAL_MODEL_CACHE_DIR).expanduser().resolve()
    return cache_root / spec.filename


def get_local_model_identity(model_name: str, model_path: Optional[str] = None) -> str:
    if model_path:
        resolved = Path(model_path).expanduser().resolve()
        return str(resolved)
    return get_local_model_spec(model_name).filename


class LocalDenseEmbedder(DenseEmbedderBase):
    """Dense embedder backed by a local GGUF model via llama-cpp-python."""

    def __init__(
        self,
        model_name: str = DEFAULT_LOCAL_DENSE_MODEL,
        model_path: Optional[str] = None,
        cache_dir: Optional[str] = None,
        dimension: Optional[int] = None,
        query_instruction: Optional[str] = None,
        config: Optional[Dict[str, Any]] = None,
    ):
        runtime_config = dict(config or {})
        runtime_config.setdefault("provider", "local")
        super().__init__(model_name, runtime_config)

        self.model_spec = get_local_model_spec(model_name)
        self.model_path = model_path
        self.cache_dir = cache_dir or DEFAULT_LOCAL_MODEL_CACHE_DIR
        self.query_instruction = (
            query_instruction
            if query_instruction is not None
            else self.model_spec.query_instruction
        )
        self._dimension = dimension or self.model_spec.dimension
        if self._dimension != self.model_spec.dimension:
            raise ValueError(
                f"Local model '{model_name}' has fixed dimension {self.model_spec.dimension}, "
                f"but got dimension={self._dimension}"
            )

        self._resolved_model_path = self._resolve_model_path()
        self._llama = self._load_model()

    def _import_llama(self):
        try:
            module = importlib.import_module("llama_cpp")
        except ImportError as exc:
            raise EmbeddingConfigurationError(
                "Local embedding is enabled but 'llama-cpp-python' is not installed. "
                'Install it with: pip install "openviking[local-embed]". '
                "If you prefer a remote provider, set embedding.dense.provider explicitly in ov.conf."
            ) from exc

        llama_cls = getattr(module, "Llama", None)
        if llama_cls is None:
            raise EmbeddingConfigurationError(
                "llama_cpp.Llama is unavailable in the installed llama-cpp-python package."
            )
        return llama_cls

    def _resolve_model_path(self) -> Path:
        if self.model_path:
            resolved = Path(self.model_path).expanduser().resolve()
            if not resolved.exists():
                raise EmbeddingConfigurationError(
                    f"Local embedding model file not found: {resolved}"
                )
            return resolved

        cache_root = Path(self.cache_dir).expanduser().resolve()
        cache_root.mkdir(parents=True, exist_ok=True)
        target = get_local_model_cache_path(self.model_name, self.cache_dir)
        if target.exists():
            return target

        self._download_model(self.model_spec.download_url, target)
        return target

    def _download_model(self, url: str, target: Path) -> None:
        logger.info("Downloading local embedding model %s to %s", self.model_name, target)
        tmp_target = target.with_suffix(target.suffix + ".part")
        try:
            with requests.get(url, stream=True, timeout=(10, 300)) as response:
                response.raise_for_status()
                with tmp_target.open("wb") as fh:
                    for chunk in response.iter_content(chunk_size=1024 * 1024):
                        if chunk:
                            fh.write(chunk)
            os.replace(tmp_target, target)
        except Exception as exc:
            tmp_target.unlink(missing_ok=True)
            raise EmbeddingConfigurationError(
                f"Failed to download local embedding model '{self.model_name}' from {url} "
                f"to {target}: {exc}"
            ) from exc

    def _load_model(self):
        llama_cls = self._import_llama()
        try:
            return llama_cls(
                model_path=str(self._resolved_model_path),
                embedding=True,
                verbose=False,
            )
        except Exception as exc:
            raise EmbeddingConfigurationError(
                f"Failed to load GGUF embedding model from {self._resolved_model_path}: {exc}"
            ) from exc

    def _format_text(self, text: str, *, is_query: bool) -> str:
        if is_query and self.query_instruction:
            return f"{self.query_instruction}{text}"
        return text

    def _supports_native_batch_embeddings(self) -> bool:
        context_params = getattr(self._llama, "context_params", None)
        n_seq_max = getattr(context_params, "n_seq_max", 1)
        return n_seq_max > 1

    @staticmethod
    def _extract_embedding(payload: Any) -> List[float]:
        if isinstance(payload, dict):
            data = payload.get("data")
            if isinstance(data, list) and data:
                item = data[0]
                if isinstance(item, dict) and "embedding" in item:
                    return list(item["embedding"])
            if "embedding" in payload:
                return list(payload["embedding"])
        raise RuntimeError("Unexpected llama-cpp-python embedding response format")

    @staticmethod
    def _extract_embeddings(payload: Any) -> List[List[float]]:
        if isinstance(payload, dict):
            data = payload.get("data")
            if isinstance(data, list):
                vectors: List[List[float]] = []
                for item in data:
                    if not isinstance(item, dict) or "embedding" not in item:
                        raise RuntimeError(
                            "Unexpected llama-cpp-python batch embedding response format"
                        )
                    vectors.append(list(item["embedding"]))
                return vectors
        raise RuntimeError("Unexpected llama-cpp-python batch embedding response format")

    def _embed_formatted_text(self, formatted: str) -> EmbedResult:
        payload = self._llama.create_embedding(formatted)
        return EmbedResult(dense_vector=self._extract_embedding(payload))

    def _embed_formatted_texts_sequential(self, formatted: List[str]) -> List[EmbedResult]:
        return [
            self._run_with_retry(
                lambda formatted_text=text: self._embed_formatted_text(formatted_text),
                logger=logger,
                operation_name="local sequential batch embedding",
            )
            for text in formatted
        ]

    def embed(self, text: str, is_query: bool = False) -> EmbedResult:
        formatted = self._format_text(text, is_query=is_query)

        try:
            result = self._run_with_retry(
                lambda: self._embed_formatted_text(formatted),
                logger=logger,
                operation_name="local embedding",
            )
        except Exception as exc:
            raise RuntimeError(f"Local embedding failed: {exc}") from exc

        estimated_tokens = self._estimate_tokens(formatted)
        self.update_token_usage(
            model_name=self.model_name,
            provider="local",
            prompt_tokens=estimated_tokens,
            completion_tokens=0,
        )
        return result

    def embed_batch(self, texts: List[str], is_query: bool = False) -> List[EmbedResult]:
        if not texts:
            return []

        formatted = [self._format_text(text, is_query=is_query) for text in texts]
        if len(formatted) > 1 and not self._supports_native_batch_embeddings():
            logger.info(
                "Local model %s does not support native multi-sequence batch embedding "
                "(n_seq_max <= 1); using sequential mode",
                self.model_name,
            )
            results = self._embed_formatted_texts_sequential(formatted)
            estimated_tokens = sum(self._estimate_tokens(text) for text in formatted)
            self.update_token_usage(
                model_name=self.model_name,
                provider="local",
                prompt_tokens=estimated_tokens,
                completion_tokens=0,
            )
            return results

        def _call_batch() -> List[EmbedResult]:
            payload = self._llama.create_embedding(formatted)
            return [
                EmbedResult(dense_vector=vector) for vector in self._extract_embeddings(payload)
            ]

        try:
            results = self._run_with_retry(
                _call_batch,
                logger=logger,
                operation_name="local batch embedding",
            )
        except Exception as batch_exc:
            logger.warning(
                "Local batch embedding failed for model=%s (%s); falling back to sequential embedding",
                self.model_name,
                batch_exc,
            )
            try:
                results = self._embed_formatted_texts_sequential(formatted)
            except Exception as exc:
                raise RuntimeError(f"Local batch embedding failed: {exc}") from exc

        estimated_tokens = sum(self._estimate_tokens(text) for text in formatted)
        self.update_token_usage(
            model_name=self.model_name,
            provider="local",
            prompt_tokens=estimated_tokens,
            completion_tokens=0,
        )
        return results

    def get_dimension(self) -> int:
        return self._dimension

    def close(self):
        close_fn = getattr(self._llama, "close", None)
        if callable(close_fn):
            close_fn()
