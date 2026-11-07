"""Outcrawl Search backend."""

import os
from typing import Any

import httpx

from .base import WebSearchBackend
from .registry import register_backend


@register_backend
class OutcrawlBackend(WebSearchBackend):
    """Outcrawl Search API backend."""

    name = "outcrawl"

    def __init__(self, api_key: str | None = None):
        self.api_key = api_key or os.environ.get("OUTCRAWL_API_KEY", "")

    @property
    def is_available(self) -> bool:
        return bool(self.api_key)

    async def search(self, query: str, count: int, **kwargs: Any) -> str:
        if not self.api_key:
            return "Error: OUTCRAWL_API_KEY not configured"

        try:
            n = min(max(count, 1), 20)
            async with httpx.AsyncClient() as client:
                r = await client.post(
                    "https://api.outcrawl.ai/v1/search",
                    headers={
                        "Authorization": f"Bearer {self.api_key}",
                        "Content-Type": "application/json",
                    },
                    json={"query": query, "limit": n},
                    timeout=25.0,
                )
                r.raise_for_status()

            results = r.json().get("hits", [])
            if not results:
                return f"No results for: {query}"

            lines = [f"Results for: {query}\n"]
            for i, item in enumerate(results[:n], 1):
                lines.append(f"{i}. {item.get('title', '')}\n   {item.get('url', '')}")
                if snippet := item.get("snippet"):
                    lines.append(f"   {snippet}")
            return "\n".join(lines)
        except Exception as e:
            return f"Error: {e}"
