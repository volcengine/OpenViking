"""Exa AI backend."""

import os
from typing import Any

import httpx

from .base import WebSearchBackend
from .registry import register_backend
from .url_safety import safe_public_result_url


@register_backend
class ExaBackend(WebSearchBackend):
    """Exa AI API backend."""

    name = "exa"

    def __init__(self, api_key: str | None = None):
        self.api_key = api_key or os.environ.get("EXA_API_KEY", "")

    @property
    def is_available(self) -> bool:
        return bool(self.api_key)

    async def search(
        self, query: str, count: int, type: str = "auto", livecrawl: str = "fallback", **kwargs: Any
    ) -> str:
        if not self.api_key:
            return "Error: EXA_API_KEY not configured"

        try:
            n = min(max(count, 1), 20)
            async with httpx.AsyncClient() as client:
                r = await client.post(
                    "https://api.exa.ai/search",
                    headers={
                        "accept": "application/json",
                        "content-type": "application/json",
                        "x-api-key": self.api_key,
                    },
                    json={
                        "query": query,
                        "type": type,
                        "numResults": n,
                        "contents": {"text": True, "livecrawl": livecrawl},
                    },
                    timeout=25.0,
                )
                r.raise_for_status()

            data = r.json()
            results = data.get("results", [])

            if not results:
                return f"No results for: {query}"

            lines = [f"Results for: {query}\n"]
            result_number = 0
            for item in results[:n]:
                url = safe_public_result_url(item.get("url"))
                if not url:
                    continue
                result_number += 1
                lines.append(f"{result_number}. {item.get('title', '')}\n   {url}")
                if text := item.get("text"):
                    lines.append(f"   {text[:500]}...")
            if result_number == 0:
                return f"No results for: {query}"
            return "\n".join(lines)
        except Exception as e:
            return f"Error: {e}"
