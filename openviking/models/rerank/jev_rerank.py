# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""
Jev (TypeSafe System One) Rerank API Client.

TypeSafe has no native rerank endpoint; each document is evaluated by an
independent Noul question against the query. The returned yes probability is
used as the relevance score.

Speaks only the TypeSafe System One protocol. Vercel AI Gateway exposes a
TypeSafe-compatible endpoint (https://ai-gateway.vercel.sh/typesafe), so routing
through Vercel is just a different api_base and model id.

Same interface as the other rerank clients:
rerank_batch(query, documents) -> List[float]
"""

import json
import time
from typing import Dict, List, Optional

import httpx

from openviking.models.rerank.base import RerankBase
from openviking_cli.utils import get_logger

logger = get_logger(__name__)


class JevRerankClient(RerankBase):
    """Jev rerank client — same interface as VikingDB RerankClient."""

    def __init__(
        self,
        api_key: str,
        model_name: str = "jev-latest",
        api_base: str = "https://api.typesafe.ai",
        timeout: float = 30.0,
        log_payloads: bool = False,
    ):
        super().__init__()
        self.api_key = api_key
        self.model_name = model_name
        self.api_base = api_base.rstrip("/")
        self.api_url = (
            self.api_base
            if self.api_base.endswith("/v1/systemone")
            else f"{self.api_base}/v1/systemone"
        )
        self.timeout = timeout
        self.log_payloads = log_payloads
        self.provider = "jev"
        self._client = httpx.Client(
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            timeout=timeout,
        )

    def _build_questions(self, documents: List[str]) -> Dict[str, dict]:
        """Build one independent relevance question per document."""
        return {
            f"relevance_{index}": {
                "type": "noul",
                "instructions": {
                    "candidate_index": index,
                    "question": (
                        "Does candidate_documents[candidate_index] answer or match "
                        "the retrieval intent of query?"
                    ),
                },
                "criteria": {
                    "true": "The candidate directly answers or is relevant to the query",
                    "false": "The candidate does not answer and is not relevant to the query",
                },
            }
            for index in range(len(documents))
        }

    def rerank_batch(self, query: str, documents: List[str]) -> Optional[List[float]]:
        """Rerank documents against a query using the Jev System One API.

        Returns relevance scores (0-1) in the same order as input documents,
        or None on failure so the caller falls back to vector scores.
        """
        if not documents:
            return []

        questions = self._build_questions(documents)
        body = {
            "model": self.model_name,
            "state": {"query": query, "candidate_documents": documents},
            "questions": questions,
        }

        try:
            if self.log_payloads:
                logger.warning(
                    "[JevRerank] Request items=%s payload=%s",
                    len(documents),
                    json.dumps(body, ensure_ascii=False),
                )
            started = time.monotonic()
            resp = self._client.post(self.api_url, json=body)
            duration_seconds = time.monotonic() - started
            resp.raise_for_status()
            data = resp.json()
            if self.log_payloads:
                logger.warning(
                    "[JevRerank] Response status=%s duration_ms=%.1f payload=%s",
                    resp.status_code,
                    duration_seconds * 1000,
                    json.dumps(data, ensure_ascii=False),
                )

            answers = data.get("answers")
            if not isinstance(answers, dict):
                logger.warning("[JevRerank] Response has no answers object")
                return None

            scores = []
            for index in range(len(documents)):
                answer = answers.get(f"relevance_{index}")
                if not isinstance(answer, dict) or answer.get("type") != "noul":
                    logger.warning("[JevRerank] Missing or malformed answer for item %s", index)
                    return None
                score = answer.get("noul")
                if not isinstance(score, (int, float)) or not 0 <= score <= 1:
                    logger.warning("[JevRerank] Invalid relevance score for item %s", index)
                    return None
                scores.append(float(score))

            usage = data.get("usage") if isinstance(data.get("usage"), dict) else {}
            input_tokens = usage.get("input_tokens", 0)
            output_tokens = usage.get("output_tokens", 0)
            self.update_token_usage(
                model_name=data.get("model") or self.model_name,
                provider=self.provider,
                prompt_tokens=int(input_tokens or 0)
                or self._estimate_tokens(query)
                + sum(self._estimate_tokens(doc) for doc in documents),
                completion_tokens=int(output_tokens or 0),
                duration_seconds=duration_seconds,
            )

            logger.debug(f"[JevRerank] Reranked {len(documents)} documents")
            return scores

        except httpx.HTTPStatusError as e:
            logger.error(f"[JevRerank] API error: {e.response.status_code} {e.response.text}")
            return None
        except Exception as e:
            logger.error(f"[JevRerank] Rerank failed: {e}")
            return None

    def close(self):
        self._client.close()

    @classmethod
    def from_config(cls, config) -> Optional["JevRerankClient"]:
        """Create JevRerankClient from RerankConfig."""
        if not config or not config.is_available():
            return None
        api_base = config.api_base or "https://api.typesafe.ai"
        default_model = "typesafe-ai/jev" if "ai-gateway.vercel.sh" in api_base else "jev-latest"
        return cls(
            api_key=config.api_key,
            model_name=config.model or default_model,
            api_base=api_base,
            timeout=config.timeout,
            log_payloads=config.log_payloads,
        )
