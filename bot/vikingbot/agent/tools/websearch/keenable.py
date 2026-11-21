"""Keenable Search backend - opt-in, works without an API key."""

import os
from typing import Any

import httpx

from .base import WebSearchBackend
from .registry import register_backend


@register_backend
class KeenableBackend(WebSearchBackend):
    """Keenable Search API backend. KEENABLE_API_KEY is optional and lifts rate limits."""

    name = "keenable"

    def __init__(self, api_key: str | None = None):
        self.api_key = api_key or os.environ.get("KEENABLE_API_KEY", "")

    @property
    def is_available(self) -> bool:
        return True

    async def search(self, query: str, count: int, **kwargs: Any) -> str:
        try:
            n = min(max(count, 1), 20)
            url = "https://api.keenable.ai/v1/search"
            headers = {"X-Keenable-Title": "openviking"}
            if self.api_key:
                headers["X-API-Key"] = self.api_key
            else:
                url += "/public"
            async with httpx.AsyncClient() as client:
                r = await client.post(
                    url,
                    headers=headers,
                    json={"query": query, "max_results": n, "snippet_max_length": 500},
                    timeout=15.0,
                )
                r.raise_for_status()

            results = r.json().get("results", [])
            if not results:
                return f"No results for: {query}"

            lines = [f"Results for: {query}\n"]
            for i, item in enumerate(results[:n], 1):
                lines.append(f"{i}. {item.get('title', '')}\n   {item.get('url', '')}")
                if snippet := " ".join((item.get("snippet") or "").split()):
                    lines.append(f"   {snippet[:500]}")
            return "\n".join(lines)
        except Exception as e:
            return f"Error: {e}"
