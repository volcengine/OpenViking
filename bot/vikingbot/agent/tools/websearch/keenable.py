"""Keenable Search backend - works without an API key."""

import os
from typing import Any

import httpx

from .base import WebSearchBackend
from .registry import register_backend


@register_backend
class KeenableBackend(WebSearchBackend):
    """Keenable Search API backend.

    Uses the public endpoint when no key is configured (10 requests/s and
    1000 requests/hour per client IP). Setting KEENABLE_API_KEY lifts those
    limits; it is never required.
    """

    name = "keenable"

    def __init__(self, api_key: str | None = None):
        self.api_key = api_key or os.environ.get("KEENABLE_API_KEY", "")

    @property
    def is_available(self) -> bool:
        return True

    async def search(self, query: str, count: int, **kwargs: Any) -> str:
        try:
            n = min(max(count, 1), 20)
            headers = {"Content-Type": "application/json", "X-Keenable-Title": "openviking"}
            if self.api_key:
                url = "https://api.keenable.ai/v1/search"
                headers["X-API-Key"] = self.api_key
            else:
                url = "https://api.keenable.ai/v1/search/public"

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
                if text := (item.get("snippet") or item.get("description")):
                    text = " ".join(text.split())
                    snippet = text[:500]
                    suffix = "..." if len(text) > 500 else ""
                    lines.append(f"   {snippet}{suffix}")
            return "\n".join(lines)
        except Exception as e:
            return f"Error: {e}"
