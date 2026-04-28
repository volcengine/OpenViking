# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Gemini Embedding 2 provider using the official google-genai SDK."""

import asyncio
from typing import Any, Dict, List, Optional, Union

from google import genai
from google.genai import types
from google.genai.errors import APIError, ClientError

try:
    from google.genai.types import HttpOptions, HttpRetryOptions

    _HTTP_RETRY_AVAILABLE = True
except ImportError:
    _HTTP_RETRY_AVAILABLE = False

import ipaddress
import logging
import socket
from urllib.parse import urlparse

from openviking.models.embedder.base import (
    DenseEmbedderBase,
    EmbedResult,
    truncate_and_normalize,
)

logger = logging.getLogger("gemini_embedders")

_TEXT_BATCH_SIZE = 100

# Keep for backward-compat with existing unit tests that import it
_GEMINI_INPUT_TOKEN_LIMIT = 8192  # gemini-embedding-2-preview hard limit

# Per-model token limits (Google API hard limits, from official docs)
_MODEL_TOKEN_LIMITS: Dict[str, int] = {
    "gemini-embedding-2": 8192,
    "gemini-embedding-2-preview": 8192,
    "gemini-embedding-001": 2048,
}
_DEFAULT_TOKEN_LIMIT = 2048  # conservative fallback for unknown future models

# Multimodal mime-type whitelist for gemini-embedding-2. Google's
# documentation is inconsistent across pages — the embed_content multimodal
# page lists a narrower set (PNG/JPEG, MP4/MOV) while the broader Gemini
# multimodal docs list more (incl. WebP/BMP/HEIC/HEIF/AVIF for images,
# video/mpeg for video). We take the **union** so we don't false-reject
# formats Google might actually accept; the API will surface a clear error
# if it doesn't accept a given format. Update when Google reconciles their
# docs or adds formats.
_MULTIMODAL_MIME_WHITELIST: Dict[str, str] = {
    # Images — union of both Google docs pages
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".bmp": "image/bmp",
    ".heic": "image/heic",
    ".heif": "image/heif",
    ".avif": "image/avif",
    # Audio — both docs agree
    ".mp3": "audio/mpeg",
    ".wav": "audio/wav",
    # Video — union of both Google docs pages (mp4, mov, mpeg)
    ".mp4": "video/mp4",
    ".mov": "video/quicktime",
    ".mpeg": "video/mpeg",
    ".mpg": "video/mpeg",
    # Documents
    ".pdf": "application/pdf",
}

# Per-modality input caps from the Gemini API docs. Server is authoritative;
# we surface API errors rather than pre-validating, but these are documented
# in the class docstring so users know what to expect.
_MULTIMODAL_LIMITS: Dict[str, str] = {
    "images": "6 max per call (PNG, JPEG)",
    "audio": "180 seconds max (MP3, WAV)",
    "video": "120 seconds max (MP4, MOV; H264/H265/AV1/VP9 codecs); 32 frames sampled",
    "pdf": "6 pages max",
    "text": "8192 tokens aggregate across all parts",
}

# Multimodal /metrics telemetry uses Google's `count_tokens` API for an
# exact server-side count (one extra round-trip per embed_content call;
# count_tokens is documented as free). Local per-modality estimation
# (PIL tile counting, pdfplumber, audio/video duration math) was off by
# 2–6× against count_tokens for typical inputs and was dropped.

_VALID_TASK_TYPES: frozenset = frozenset(
    {
        "RETRIEVAL_QUERY",
        "RETRIEVAL_DOCUMENT",
        "SEMANTIC_SIMILARITY",
        "CLASSIFICATION",
        "CLUSTERING",
        "QUESTION_ANSWERING",
        "FACT_VERIFICATION",
        "CODE_RETRIEVAL_QUERY",
    }
)

_ERROR_HINTS: Dict[int, str] = {
    400: "Invalid request — check model name and task_type value.",
    401: "Invalid API key. Verify your GOOGLE_API_KEY or api_key in config.",
    403: "Permission denied. API key may lack access to this model.",
    404: "Model not found: '{model}'. Check spelling (e.g. 'gemini-embedding-2-preview').",
    429: "Quota exceeded. Wait and retry, or increase your Google API quota.",
    500: "Gemini service error (Google-side). Retry after a delay.",
    503: "Gemini service unavailable. Retry after a delay.",
}


def _raise_api_error(e: APIError, model: str) -> None:
    hint = _ERROR_HINTS.get(e.code, "")
    # Gemini returns HTTP 400 (not 401) when the API key is invalid
    if e.code == 400 and "api key" in str(e).lower():
        hint = "Invalid API key. Verify your GOOGLE_API_KEY or api_key in config."
    api_msg = getattr(e, "message", None) or str(e)
    msg = f"Gemini embedding failed (HTTP {e.code}): {api_msg}"
    if hint:
        msg += f" Hint: {hint.format(model=model)}"
    raise RuntimeError(msg) from e


class GeminiDenseEmbedder(DenseEmbedderBase):
    """Dense embedder backed by Google's Gemini Embedding models. Dual-mode.

    +----------+--------------------------+--------------------------+--------------------------------+
    | Mode     | Models                   | Methods                  | Inputs                         |
    +==========+==========================+==========================+================================+
    | text     | gemini-embedding-2,      | embed(), embed_async(),  | str (query / doc)              |
    | (default)| gemini-embedding-001,    | embed_batch(),           |                                |
    |          | text-embedding-004       | embed_batch_async()      |                                |
    +----------+--------------------------+--------------------------+--------------------------------+
    | multi-   | gemini-embedding-2 only  | embed_content(),         | List[Dict] of text/image/      |
    | modal    | (or gemini-embedding-2-  | embed_content_async()    | audio/video/pdf parts          |
    |          | preview)                 |                          | (mime-type whitelist enforced) |
    +----------+--------------------------+--------------------------+--------------------------------+

    Mode is selected via `input_type` constructor param ("text" | "multimodal"). Default
    "text" preserves existing behavior for callers that don't pass the param. The
    `supports_multimodal` property derives from `input_type` — single source of truth.

    Multimodal mime whitelist: PNG, JPEG, WebP, BMP, HEIC/HEIF, AVIF, MP3, WAV,
    MP4, MOV, MPEG, PDF. Unsupported types raise ValueError with a hint to
    pre-convert. Update whitelist when Google adds new formats.

    Multimodal /metrics telemetry uses Google's `count_tokens` API for an exact
    server-side count (one extra round-trip per embed_content call; count_tokens
    is documented as free). On count_tokens failure, the embed result is still
    returned and a warning is logged — /metrics simply skips that call rather
    than fabricating a number.

    Multimodal output is ONE aggregated embedding per `embed_content()` call (matches
    DashScope multimodal convention). For per-chunk text embedding, callers use the
    text branch (`embed()`) or loop `embed_content()` per chunk.

    Multimodal task asymmetry (query vs document): the underlying vectorizer pipeline
    (`vectorize_one` → `vectorize_document`) does NOT thread `is_query` through, so
    `embed_content()` cannot tell which role a content list is playing. Single
    `task_instruction` (no query/document split) is supported on the multimodal
    branch — same shape as DashScope. The text branch keeps `task_type` /
    `query_param` / `document_param` for legacy callers.

    Output dimension: 1–3072 (MRL; recommended 768, 1536, 3072; default 3072). Set at
    init time only; per-call override is intentionally NOT supported because the
    vectordb index is sized at collection-creation time and a per-call dim mismatch
    would silently produce vectors that don't fit the index.

    Server returns L2-normalized vectors for all dimensions; class does not renormalize.

    Regional access: gemini-embedding-2 requires reachable Google API endpoints. Users
    in restricted regions should configure `DashScopeDenseEmbedder` (multimodal mode)
    or `VolcengineEmbedder` instead.

    Non-symmetric text mode: use query_param/document_param in EmbeddingModelConfig.
    """

    # Default output dimensions per model (used when user does not specify `dimension`).
    # gemini-embedding-2:         3072 MRL model — supports 1–3072 via output_dimensionality
    # gemini-embedding-2-preview: 3072 MRL model (preview ID for the same v2 family)
    # gemini-embedding-001:       3072 (native 768-dim vectors; 3072 shown as default for MRL compat)
    # text-embedding-004:         768  fixed-dim legacy model, does not support MRL truncation
    # Future gemini-embedding-*:  default 3072 via _default_dimension() fallback
    # Future text-embedding-*:    default 768  via _default_dimension() prefix rule

    KNOWN_DIMENSIONS: Dict[str, int] = {
        "gemini-embedding-2": 3072,
        "gemini-embedding-2-preview": 3072,
        "gemini-embedding-001": 3072,
        "text-embedding-004": 768,
    }

    @classmethod
    def _default_dimension(cls, model: str) -> int:
        """Return default output dimension for a Gemini model.

        Lookup order:
        1. Exact match in KNOWN_DIMENSIONS
        2. Prefix rule: text-embedding-* → 768 (legacy fixed-dim series)
        3. Fallback: 3072 (gemini-embedding-* MRL models)

        Examples:
            gemini-embedding-2-preview → 3072 (exact match)
            gemini-embedding-2         → 3072 (fallback — future model)
            text-embedding-004         → 768  (exact match)
            text-embedding-005         → 768  (prefix rule — future model)
        """
        if model in cls.KNOWN_DIMENSIONS:
            return cls.KNOWN_DIMENSIONS[model]
        if model.startswith("text-embedding-"):
            return 768
        return 3072

    def __init__(
        self,
        model_name: str = "gemini-embedding-2-preview",
        api_key: Optional[str] = None,
        dimension: Optional[int] = None,
        task_type: Optional[str] = None,
        query_param: Optional[str] = None,
        document_param: Optional[str] = None,
        max_concurrent_batches: int = 10,
        input_type: str = "text",
        task_instruction: Optional[str] = None,
        config: Optional[Dict[str, Any]] = None,
    ):
        super().__init__(model_name, config)
        self.provider = "gemini"
        if not api_key:
            raise ValueError("Gemini provider requires api_key")
        if input_type not in ("text", "multimodal"):
            raise ValueError(f"Invalid input_type '{input_type}'. Must be 'text' or 'multimodal'.")
        if input_type == "multimodal" and not model_name.startswith("gemini-embedding-2"):
            raise ValueError(
                f"Multimodal mode requires a gemini-embedding-2 family model "
                f"(e.g. 'gemini-embedding-2' or 'gemini-embedding-2-preview'); "
                f"got '{model_name}'. Use input_type='text' for older Gemini "
                f"embedding models."
            )
        if task_type and task_type not in _VALID_TASK_TYPES:
            raise ValueError(
                f"Invalid task_type '{task_type}'. "
                f"Valid values: {', '.join(sorted(_VALID_TASK_TYPES))}"
            )
        if dimension is not None and not (1 <= dimension <= 3072):
            raise ValueError(f"dimension must be between 1 and 3072, got {dimension}")
        if _HTTP_RETRY_AVAILABLE:
            self.client = genai.Client(
                api_key=api_key,
                http_options=HttpOptions(
                    retry_options=HttpRetryOptions(
                        attempts=max(self.max_retries + 1, 1),
                        initial_delay=0.5,
                        max_delay=8.0,
                        exp_base=2.0,
                    )
                ),
            )
        else:
            self.client = genai.Client(api_key=api_key)
        self._input_type = input_type
        self.task_type = task_type
        self.task_instruction = task_instruction
        self.query_param = query_param
        self.document_param = document_param
        self._dimension = dimension or self._default_dimension(model_name)
        self._token_limit = _MODEL_TOKEN_LIMITS.get(model_name, _DEFAULT_TOKEN_LIMIT)
        self._max_concurrent_batches = max_concurrent_batches

    @property
    def supports_multimodal(self) -> bool:
        """True iff this instance is configured for multimodal mode.

        Single source of truth derived from `input_type`. Settable directly is
        intentionally NOT supported — change `input_type` at construction time.
        """
        return self._input_type == "multimodal"

    def _build_config(
        self,
        *,
        task_type: Optional[str] = None,
        title: Optional[str] = None,
    ) -> types.EmbedContentConfig:
        """Build EmbedContentConfig, merging per-call overrides with instance defaults."""
        effective_task_type = task_type or self.task_type
        kwargs: Dict[str, Any] = {"output_dimensionality": self._dimension}
        if effective_task_type:
            kwargs["task_type"] = effective_task_type.upper()
        if title:
            kwargs["title"] = title
        return types.EmbedContentConfig(**kwargs)

    def _resolve_task_type(
        self,
        *,
        is_query: bool = False,
        task_type: Optional[str] = None,
    ) -> Optional[str]:
        if task_type is None:
            if is_query and self.query_param:
                task_type = self.query_param
            elif not is_query and self.document_param:
                task_type = self.document_param
        return task_type

    def __repr__(self) -> str:
        return (
            f"GeminiDenseEmbedder("
            f"model={self.model_name!r}, "
            f"dim={self._dimension}, "
            f"task_type={self.task_type!r})"
        )

    def embed(
        self,
        text: str,
        is_query: bool = False,
        *,
        task_type: Optional[str] = None,
        title: Optional[str] = None,
    ) -> EmbedResult:
        if not text or not text.strip():
            logger.warning("Empty text passed to embed(), returning zero vector")
            return EmbedResult(dense_vector=[0.0] * self._dimension)
        task_type = self._resolve_task_type(is_query=is_query, task_type=task_type)

        # SDK accepts plain str; converts to REST Parts format internally.
        def _call() -> EmbedResult:
            result = self.client.models.embed_content(
                model=self.model_name,
                contents=text,
                config=self._build_config(task_type=task_type, title=title),
            )
            vector = truncate_and_normalize(list(result.embeddings[0].values), self._dimension)
            return EmbedResult(dense_vector=vector)

        try:
            result = (
                _call()
                if _HTTP_RETRY_AVAILABLE
                else self._run_with_retry(
                    _call,
                    logger=logger,
                    operation_name="Gemini embedding",
                )
            )
            # Estimate token usage
            estimated_tokens = self._estimate_tokens(text)
            self.update_token_usage(
                model_name=self.model_name,
                provider="gemini",
                prompt_tokens=estimated_tokens,
                completion_tokens=0,
            )
            return result
        except (APIError, ClientError) as e:
            _raise_api_error(e, self.model_name)

    async def embed_async(
        self,
        text: str,
        is_query: bool = False,
        *,
        task_type: Optional[str] = None,
        title: Optional[str] = None,
    ) -> EmbedResult:
        if not text or not text.strip():
            logger.warning("Empty text passed to embed_async(), returning zero vector")
            return EmbedResult(dense_vector=[0.0] * self._dimension)

        task_type = self._resolve_task_type(is_query=is_query, task_type=task_type)

        async def _call() -> EmbedResult:
            result = await self.client.aio.models.embed_content(
                model=self.model_name,
                contents=text,
                config=self._build_config(task_type=task_type, title=title),
            )
            vector = truncate_and_normalize(list(result.embeddings[0].values), self._dimension)
            return EmbedResult(dense_vector=vector)

        try:
            result = await self._run_with_async_retry(
                _call,
                logger=logger,
                operation_name="Gemini async embedding",
            )
            estimated_tokens = self._estimate_tokens(text)
            self.update_token_usage(
                model_name=self.model_name,
                provider="gemini",
                prompt_tokens=estimated_tokens,
                completion_tokens=0,
            )
            return result
        except (APIError, ClientError) as e:
            _raise_api_error(e, self.model_name)

    def embed_batch(
        self,
        texts: List[str],
        is_query: bool = False,
        *,
        task_type: Optional[str] = None,
        titles: Optional[List[str]] = None,
    ) -> List[EmbedResult]:
        if not texts:
            return []
        # When titles are provided, delegate per-item (titles are per-document metadata).
        if titles is not None:
            return [
                self.embed(text, is_query=is_query, task_type=task_type, title=title)
                for text, title in zip(texts, titles, strict=True)
            ]
        task_type = self._resolve_task_type(is_query=is_query, task_type=task_type)
        results: List[EmbedResult] = []
        config = self._build_config(task_type=task_type)
        for i in range(0, len(texts), _TEXT_BATCH_SIZE):
            batch = texts[i : i + _TEXT_BATCH_SIZE]
            non_empty_indices = [j for j, t in enumerate(batch) if t and t.strip()]
            empty_indices = [j for j, t in enumerate(batch) if not (t and t.strip())]

            if not non_empty_indices:
                results.extend(EmbedResult(dense_vector=[0.0] * self._dimension) for _ in batch)
                continue

            non_empty_texts = [batch[j] for j in non_empty_indices]

            def _call_batch(
                non_empty_texts: List[str] = non_empty_texts,
                config: types.EmbedContentConfig = config,
            ) -> Any:
                response = self.client.models.embed_content(
                    model=self.model_name,
                    contents=non_empty_texts,
                    config=config,
                )
                return response

            try:
                if _HTTP_RETRY_AVAILABLE:
                    response = _call_batch()
                else:
                    response = self._run_with_retry(
                        _call_batch,
                        logger=logger,
                        operation_name="Gemini batch embedding",
                    )
                batch_results = [None] * len(batch)
                for j, emb in zip(non_empty_indices, response.embeddings, strict=True):
                    batch_results[j] = EmbedResult(
                        dense_vector=truncate_and_normalize(list(emb.values), self._dimension)
                    )
                for j in empty_indices:
                    batch_results[j] = EmbedResult(dense_vector=[0.0] * self._dimension)
                results.extend(batch_results)
            except (APIError, ClientError) as e:
                logger.warning(
                    "Gemini batch embed failed (HTTP %d) for batch of %d, falling back to individual",
                    e.code,
                    len(batch),
                )
                for text in batch:
                    results.append(self.embed(text, is_query=is_query))
        # Token usage is already tracked via individual embed() calls
        # No need to track here to avoid double counting
        return results

    async def embed_batch_async(
        self,
        texts: List[str],
        is_query: bool = False,
        *,
        task_type: Optional[str] = None,
        titles: Optional[List[str]] = None,
    ) -> List[EmbedResult]:
        if not texts:
            return []
        if titles is not None:
            return [
                await self.embed_async(
                    text,
                    is_query=is_query,
                    task_type=task_type,
                    title=title,
                )
                for text, title in zip(texts, titles, strict=True)
            ]

        task_type = self._resolve_task_type(is_query=is_query, task_type=task_type)
        batches = [texts[i : i + _TEXT_BATCH_SIZE] for i in range(0, len(texts), _TEXT_BATCH_SIZE)]
        results: List[Optional[List[EmbedResult]]] = [None] * len(batches)
        sem = asyncio.Semaphore(self._max_concurrent_batches)

        async def _embed_one(idx: int, batch: List[str]) -> None:
            async with sem:
                non_empty_indices = [j for j, t in enumerate(batch) if t and t.strip()]
                empty_indices = [j for j, t in enumerate(batch) if not (t and t.strip())]
                batch_results: List[Optional[EmbedResult]] = [None] * len(batch)
                for j in empty_indices:
                    batch_results[j] = EmbedResult(dense_vector=[0.0] * self._dimension)

                if not non_empty_indices:
                    results[idx] = [r for r in batch_results if r is not None]
                    return

                non_empty_texts = [batch[j] for j in non_empty_indices]

                async def _call_batch() -> Any:
                    return await self.client.aio.models.embed_content(
                        model=self.model_name,
                        contents=non_empty_texts,
                        config=self._build_config(task_type=task_type),
                    )

                try:
                    response = await self._run_with_async_retry(
                        _call_batch,
                        logger=logger,
                        operation_name="Gemini async batch embedding",
                    )
                    for j, emb in zip(non_empty_indices, response.embeddings, strict=True):
                        batch_results[j] = EmbedResult(
                            dense_vector=truncate_and_normalize(list(emb.values), self._dimension)
                        )
                    total_tokens = sum(self._estimate_tokens(text) for text in non_empty_texts)
                    self.update_token_usage(
                        model_name=self.model_name,
                        provider="gemini",
                        prompt_tokens=total_tokens,
                        completion_tokens=0,
                    )
                except (APIError, ClientError) as e:
                    logger.warning(
                        "Gemini async batch embed failed (HTTP %d) for batch of %d, falling back to per-item async calls",
                        e.code,
                        len(batch),
                    )
                    for j in non_empty_indices:
                        batch_results[j] = await self.embed_async(
                            batch[j],
                            is_query=is_query,
                            task_type=task_type,
                        )

                results[idx] = [r for r in batch_results if r is not None]

        await asyncio.gather(*(_embed_one(idx, batch) for idx, batch in enumerate(batches)))
        return [r for batch_results in results for r in (batch_results or [])]

    async def async_embed_batch(self, texts: List[str]) -> List[EmbedResult]:
        """Backward-compatible alias for the standardized async batch API."""
        return await self.embed_batch_async(texts)

    def get_dimension(self) -> int:
        return self._dimension

    # ------------------------------------------------------------------
    # Multimodal branch — embed_content / embed_content_async
    # ------------------------------------------------------------------

    @staticmethod
    def _detect_mime_type(value: Union[str, bytes], explicit_mime: Optional[str]) -> str:
        """Resolve a Part's mime type from explicit hint or filename/URL extension.

        Order of preference:
        1. Explicit `mime_type` key on the input dict (caller-provided).
        2. Lowercase extension of the URL/filename (whitelisted only).

        Raises:
            ValueError if no mime can be resolved or the resolved mime is outside
            the gemini-embedding-2 whitelist.
        """
        if explicit_mime:
            if explicit_mime not in _MULTIMODAL_MIME_WHITELIST.values():
                supported = ", ".join(sorted(set(_MULTIMODAL_MIME_WHITELIST.values())))
                raise ValueError(
                    f"Mime type '{explicit_mime}' is not in the gemini-embedding-2 "
                    f"whitelist. Supported mimes: {supported}. Pre-convert your "
                    f"asset (e.g. .docx → text, .webp → .png) or use a different "
                    f"embedder."
                )
            return explicit_mime
        if not isinstance(value, str):
            raise ValueError(
                "Cannot infer mime_type from raw bytes; pass an explicit "
                "'mime_type' key on the content dict (e.g. {'image': b'...', "
                "'mime_type': 'image/png'})."
            )
        # Strip query string + fragment so 'foo.png?token=...' still resolves.
        path = urlparse(value).path or value
        last_dot = path.rfind(".")
        ext = path[last_dot:].lower() if last_dot != -1 else ""
        if ext not in _MULTIMODAL_MIME_WHITELIST:
            supported = ", ".join(sorted(_MULTIMODAL_MIME_WHITELIST.keys()))
            raise ValueError(
                f"Unsupported file extension '{ext or '(none)'}' for "
                f"gemini-embedding-2. Supported extensions: {supported}. "
                f"Pre-convert .docx/.txt/.md to text (then use the text branch), "
                f"or .webp/.heic to .png, or .m4a/.ogg to .mp3."
            )
        return _MULTIMODAL_MIME_WHITELIST[ext]

    @staticmethod
    def _validate_url(url: str) -> None:
        """SSRF guard for user-supplied URLs.

        Whitelists http and https schemes. Rejects file://, gs://, data:, and any
        URL whose host resolves to a loopback / link-local / private (RFC1918) /
        multicast / reserved address. This stops a malicious config or upstream
        caller from getting the google-genai SDK to fetch internal metadata
        services (AWS IMDS at 169.254.169.254, GCP metadata, internal admin
        APIs on localhost, etc.).

        Raises:
            ValueError on disallowed scheme or host.
        """
        parsed = urlparse(url)
        scheme = parsed.scheme.lower()
        if scheme not in ("http", "https"):
            raise ValueError(
                f"URL scheme '{scheme or '(none)'}' is not allowed. Only http and "
                f"https are accepted to prevent SSRF (file://, gs://, data:, etc. "
                f"are rejected). For Google Cloud Storage, fetch the bytes "
                f"yourself and pass them with an explicit 'mime_type'."
            )
        host = parsed.hostname
        if not host:
            raise ValueError(f"URL '{url}' has no host component.")
        # Resolve once and check every returned address. DNS rebinding is not
        # fully mitigated here (the SDK will resolve again at fetch time), but
        # rejecting an obviously-internal hostname is the cheap layer of defense.
        try:
            addr_info = socket.getaddrinfo(host, None)
        except socket.gaierror:
            # Hostname doesn't resolve; let the SDK surface the error rather
            # than failing here on a transient DNS issue.
            return
        for _family, _, _, _, sockaddr in addr_info:
            ip_str = sockaddr[0]
            try:
                ip = ipaddress.ip_address(ip_str)
            except ValueError:
                continue
            if (
                ip.is_loopback
                or ip.is_link_local
                or ip.is_private
                or ip.is_multicast
                or ip.is_reserved
                or ip.is_unspecified
            ):
                raise ValueError(
                    f"URL host '{host}' resolves to {ip_str}, which is in a "
                    f"loopback / link-local / private / multicast / reserved "
                    f"range. SSRF guard refuses this URL. If this was "
                    f"intentional, fetch the bytes yourself and pass them "
                    f"directly with an explicit 'mime_type'."
                )

    def _dict_to_part(self, content: Dict[str, Any]) -> "types.Part":
        """Convert a single multimodal content dict into a google.genai.types.Part.

        Accepted dict shapes:
            {"text": "..."}                                  — text part
            {"image": "https://..."}                         — image URL
            {"image": b"...", "mime_type": "image/png"}      — image bytes
            {"audio": "..." | bytes, "mime_type": ...}       — audio
            {"video": "..." | bytes, "mime_type": ...}       — video
            {"pdf":   "..." | bytes, "mime_type": ...}       — pdf

        Mime type is inferred from the URL/filename extension or pulled from an
        explicit 'mime_type' key. Whitelist enforced (PNG, JPEG, WebP, BMP,
        HEIC/HEIF, AVIF, MP3, WAV, MP4, MOV/MPEG, PDF). URLs are SSRF-validated
        before being passed to the SDK.

        Raises:
            ValueError on unknown dict shape, unsupported mime, or disallowed URL.
        """
        if "text" in content:
            text = content["text"]
            if not isinstance(text, str):
                raise ValueError(f"'text' content must be a string, got {type(text).__name__}.")
            return types.Part(text=text)
        explicit_mime = content.get("mime_type")
        for key in ("image", "audio", "video", "pdf", "document"):
            if key not in content:
                continue
            value = content[key]
            mime = self._detect_mime_type(value, explicit_mime)
            if isinstance(value, str):
                self._validate_url(value)
                # Part.from_uri carries inline data via the SDK fetch path.
                return types.Part.from_uri(file_uri=value, mime_type=mime)
            if isinstance(value, (bytes, bytearray)):
                return types.Part.from_bytes(data=bytes(value), mime_type=mime)
            raise ValueError(
                f"'{key}' content must be a URL string or raw bytes; got {type(value).__name__}."
            )
        raise ValueError(
            f"Unknown content dict shape: {list(content.keys())}. "
            f"Expected one of: 'text', 'image', 'audio', 'video', 'pdf'."
        )

    def _build_multimodal_contents(self, contents: List[Dict[str, Any]]) -> List["types.Part"]:
        """Convert the user's content list into Parts, prepending task_instruction.

        If `task_instruction` is set on the embedder, it's prepended to the FIRST
        text part in the contents list (or inserted as the first part if there
        is no text part). Mirrors how Gemini's docs describe v2 task routing.
        """
        if not contents:
            raise ValueError("contents must be a non-empty list of dicts.")
        parts = [self._dict_to_part(c) for c in contents]
        if self.task_instruction:
            for i, p in enumerate(parts):
                # Heuristic: types.Part exposes the text via the .text attribute
                # when constructed from text. We modify the first text part in
                # place by replacing it with a new Part carrying the prefix.
                p_text = getattr(p, "text", None)
                if p_text is not None:
                    parts[i] = types.Part(text=f"{self.task_instruction} {p_text}")
                    break
            else:
                # No text part found — insert the instruction as the first text part.
                parts.insert(0, types.Part(text=self.task_instruction))
        return parts

    def _track_multimodal_usage(self, parts: List["types.Part"]) -> None:
        """Forward exact token-usage telemetry to /metrics via Gemini's
        `count_tokens` API. Adds one round-trip per embed_content call;
        count_tokens is documented as free.

        Called only after a successful embed — count_tokens failure (rate
        limits, transient errors) logs a warning and skips the telemetry
        update. Choosing zero-recorded over wrong-recorded keeps /metrics
        honest about what was actually measured.

        On the Vertex AI auth path, `result.metadata.billable_character_count`
        and per-embedding `statistics.token_count` are populated and would be a
        cheaper source than count_tokens. Public Gemini API (api_key) returns
        `None` for both — verified via the SDK type hints, which mark both
        fields "Vertex API only".
        """
        try:
            resp = self.client.models.count_tokens(model=self.model_name, contents=parts)
            prompt_tokens = int(resp.total_tokens)
        except Exception as exc:
            logger.warning(
                "count_tokens failed for %s; skipping telemetry update: %s",
                self.model_name,
                exc,
            )
            return
        self.update_token_usage(
            model_name=self.model_name,
            provider="gemini",
            prompt_tokens=prompt_tokens,
            completion_tokens=0,
        )

    async def _track_multimodal_usage_async(self, parts: List["types.Part"]) -> None:
        """Async counterpart to `_track_multimodal_usage`. See its docstring."""
        try:
            resp = await self.client.aio.models.count_tokens(
                model=self.model_name, contents=parts
            )
            prompt_tokens = int(resp.total_tokens)
        except Exception as exc:
            logger.warning(
                "count_tokens (async) failed for %s; skipping telemetry update: %s",
                self.model_name,
                exc,
            )
            return
        self.update_token_usage(
            model_name=self.model_name,
            provider="gemini",
            prompt_tokens=prompt_tokens,
            completion_tokens=0,
        )

    def embed_content(self, contents: List[Dict[str, Any]]) -> EmbedResult:
        """Embed a list of multimodal content parts into a single fused vector.

        Available only when `input_type='multimodal'`. Mirrors
        `DashScopeDenseEmbedder.embed_content()` in shape so
        `Collection.search_by_multimodal()` can route to either provider
        without special-casing.

        Args:
            contents: List of content dicts. See `_dict_to_part` for accepted
                shapes. Aggregated server-side into one embedding (NOT one per
                input — for per-chunk text use the text branch).

        Returns:
            EmbedResult with a single `dense_vector` of length `self._dimension`.

        Raises:
            RuntimeError if multimodal mode is not active or the API call fails.
            ValueError on whitelist miss, SSRF rejection, or malformed contents.
        """
        if not self.supports_multimodal:
            raise RuntimeError(
                "embed_content() is only available in multimodal mode. "
                "Construct GeminiDenseEmbedder with input_type='multimodal' "
                "(and a gemini-embedding-2 family model)."
            )
        parts = self._build_multimodal_contents(contents)

        def _call() -> EmbedResult:
            result = self.client.models.embed_content(
                model=self.model_name,
                contents=parts,
                config=types.EmbedContentConfig(output_dimensionality=self._dimension),
            )
            # Track inside the closure so retries don't double-count this call.
            self._track_multimodal_usage(parts)
            vector = list(result.embeddings[0].values)
            return EmbedResult(dense_vector=truncate_and_normalize(vector, self._dimension))

        try:
            result = (
                _call()
                if _HTTP_RETRY_AVAILABLE
                else self._run_with_retry(
                    _call,
                    logger=logger,
                    operation_name="Gemini multimodal embedding",
                )
            )
            return result
        except (APIError, ClientError) as e:
            _raise_api_error(e, self.model_name)

    async def embed_content_async(self, contents: List[Dict[str, Any]]) -> EmbedResult:
        """Async version of `embed_content`. Same semantics; uses `client.aio`."""
        if not self.supports_multimodal:
            raise RuntimeError(
                "embed_content_async() is only available in multimodal mode. "
                "Construct GeminiDenseEmbedder with input_type='multimodal' "
                "(and a gemini-embedding-2 family model)."
            )
        parts = self._build_multimodal_contents(contents)

        async def _call() -> EmbedResult:
            result = await self.client.aio.models.embed_content(
                model=self.model_name,
                contents=parts,
                config=types.EmbedContentConfig(output_dimensionality=self._dimension),
            )
            # Track inside the closure so retries don't double-count this call.
            await self._track_multimodal_usage_async(parts)
            vector = list(result.embeddings[0].values)
            return EmbedResult(dense_vector=truncate_and_normalize(vector, self._dimension))

        try:
            result = await self._run_with_async_retry(
                _call,
                logger=logger,
                operation_name="Gemini multimodal async embedding",
            )
            return result
        except (APIError, ClientError) as e:
            _raise_api_error(e, self.model_name)

    def close(self):
        if hasattr(self.client, "_http_client"):
            try:
                self.client._http_client.close()
            except Exception:
                pass
