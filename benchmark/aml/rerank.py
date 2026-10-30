"""VikingDB reranking with document-ID alignment and shared throttle cooldown."""

from __future__ import annotations

import asyncio
import json
import math
import re
import time
from datetime import timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any

import httpx

from benchmark.aml.audit import audit_event


class RerankError(RuntimeError):
    pass


class RerankRetriesExhausted(RerankError):
    """Transient provider failures exhausted the configured attempt budget."""

    def __init__(self, *, attempts: int, http_status: int | None) -> None:
        super().__init__("Rerank technical retries exhausted")
        self.attempts = attempts
        self.http_status = http_status


def retry_after_seconds(value: str | None) -> float | None:
    """Parse RFC 9110 delay-seconds or HTTP-date without trusting invalid values."""
    if not value:
        return None
    value = value.strip()
    try:
        if re.fullmatch(r"[0-9]+", value):
            delay = float(value)
        else:
            date = parsedate_to_datetime(value)
            if date.tzinfo is None:
                date = date.replace(tzinfo=timezone.utc)
            delay = date.timestamp() - time.time()
    except (TypeError, ValueError, OverflowError):
        return None
    return max(0.0, delay) if math.isfinite(delay) else None


def align_scores(body: dict[str, Any], count: int) -> list[float]:
    """Return scores in input order; the provider returns relevance order."""
    try:
        items = body["result"]["data"]
        if not isinstance(items, list) or len(items) != count:
            raise ValueError("Unexpected rerank response length")
        scores: list[float | None] = [None] * count
        for item in items:
            raw_id = item["id"]
            if isinstance(raw_id, bool) or not isinstance(raw_id, (int, str)):
                raise ValueError("Invalid rerank document ID")
            index = int(raw_id)
            if not 0 <= index < count or scores[index] is not None:
                raise ValueError("Duplicate or out-of-range rerank document ID")
            score = float(item["score"])
            if not math.isfinite(score):
                raise ValueError("Nonfinite rerank score")
            scores[index] = score
        if any(score is None for score in scores):
            raise ValueError("Missing rerank document ID")
        return [float(score) for score in scores if score is not None]
    except (KeyError, TypeError, ValueError, OverflowError) as exc:
        raise RerankError("Invalid rerank response; refusing to use unaligned scores") from exc


class VikingDBReranker:
    def __init__(self, config_path: str) -> None:
        config = json.loads(Path(config_path).read_text())
        self.ak = config["ak"]
        self.sk = config["sk"]
        self.host = config.get("host", "api-vikingdb.vikingdb.cn-beijing.volces.com")
        self.model = config.get("model_name", "doubao-seed-rerank")
        self.version = config.get("model_version", "251028")
        self.threshold = float(config.get("threshold", 0))
        self.batch_size = int(config.get("batch_size", 100))
        self.attempts = int(config.get("retry_attempts", 8))
        concurrency = int(config.get("max_concurrent", 4))
        if not 1 <= self.batch_size <= 100 or self.attempts < 1 or concurrency < 1:
            raise ValueError("Invalid rerank batch size, retries or concurrency")
        if not math.isfinite(self.threshold):
            raise ValueError("Rerank threshold must be finite")
        self._slots = asyncio.Semaphore(concurrency)
        self._cooldown_until = 0.0

    def _signed_headers(self, payload: dict[str, Any]) -> tuple[dict[str, str], str]:
        from volcengine.auth.SignerV4 import SignerV4
        from volcengine.base.Request import Request
        from volcengine.Credentials import Credentials

        request = Request()
        request.set_shema("https")
        request.set_method("POST")
        request.set_host(self.host)
        request.set_path("/api/vikingdb/rerank")
        request.set_headers(
            {"Content-Type": "application/json", "Accept": "application/json", "Host": self.host}
        )
        request.set_body(json.dumps(payload))
        SignerV4.sign(request, Credentials(self.ak, self.sk, "vikingdb", "cn-beijing"))
        return request.headers, request.body

    async def _batch(self, query: str, documents: list[str]) -> list[float]:
        payload = {
            "model_name": self.model,
            "model_version": self.version,
            "query": [{"text": query}],
            "data": [[{"text": text}] for text in documents],
            "instruction": (
                "Whether the Document answers the Query or matches the content retrieval intent"
            ),
        }
        async with self._slots, httpx.AsyncClient(timeout=60, trust_env=False) as client:
            for attempt in range(self.attempts):
                while (remaining := self._cooldown_until - time.monotonic()) > 0:
                    await asyncio.sleep(min(remaining, 2))
                headers, body = self._signed_headers(payload)
                started = time.monotonic()
                try:
                    response = await client.post(
                        f"https://{self.host}/api/vikingdb/rerank", headers=headers, content=body
                    )
                    if response.status_code in {429, 503}:
                        requested_delay = retry_after_seconds(response.headers.get("retry-after"))
                        delay = max(
                            65.0 if response.status_code == 429 else 0.0, requested_delay or 0.0
                        )
                        if delay:
                            self._cooldown_until = max(
                                self._cooldown_until, time.monotonic() + delay
                            )
                            audit_event(
                                "rerank_cooldown",
                                http_status=response.status_code,
                                retry_after_seconds=requested_delay,
                                cooldown_seconds=delay,
                            )
                    response.raise_for_status()
                    scores = align_scores(response.json(), len(documents))
                    audit_event(
                        "rerank_batch_completed",
                        document_count=len(documents),
                        attempt=attempt + 1,
                        elapsed_ms=round((time.monotonic() - started) * 1000, 3),
                    )
                    return scores
                except (httpx.RequestError, httpx.HTTPStatusError) as exc:
                    code = (
                        exc.response.status_code if isinstance(exc, httpx.HTTPStatusError) else None
                    )
                    audit_event(
                        "rerank_attempt_failed",
                        http_status=code,
                        error_type=type(exc).__name__,
                        attempt=attempt + 1,
                        document_count=len(documents),
                    )
                    if code is not None and code not in {429, 500, 502, 503, 504}:
                        raise RerankError(f"Rerank rejected the request (HTTP {code})") from exc
                    if attempt + 1 == self.attempts:
                        raise RerankRetriesExhausted(
                            attempts=self.attempts, http_status=code
                        ) from exc
                    await asyncio.sleep(min(2**attempt, 20))
                except ValueError as exc:
                    raise RerankError("Rerank returned invalid JSON") from exc
        raise AssertionError("Rerank retry loop exited unexpectedly")

    async def rank(self, query: str, hits: list[dict[str, Any]]) -> list[dict[str, Any]]:
        scored: list[tuple[float, int, dict[str, Any]]] = []
        for start in range(0, len(hits), self.batch_size):
            batch = hits[start : start + self.batch_size]
            documents = [hit.get("abstract") or hit.get("content") for hit in batch]
            if any(not isinstance(text, str) or not text.strip() for text in documents):
                raise RerankError("A rerank candidate has no usable text")
            scores = await self._batch(query, documents)
            for offset, (hit, score) in enumerate(zip(batch, scores, strict=True)):
                if score > self.threshold:
                    scored.append((score, start + offset, hit))
        scored.sort(key=lambda item: (-item[0], item[1]))
        return [hit for _, _, hit in scored]
