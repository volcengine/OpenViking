"""Tavily Search backend."""

import os
from typing import Any

from .base import WebSearchBackend
from .registry import register_backend


@register_backend
class TavilyBackend(WebSearchBackend):
    """Tavily Search API backend."""

    name = "tavily"

    def __init__(self, api_key: str | None = None):
        self.api_key = api_key or os.environ.get("TAVILY_API_KEY", "")
        if self.api_key:
            from tavily import AsyncTavilyClient

            self._client = AsyncTavilyClient(api_key=self.api_key)
        else:
            self._client = None

    @property
    def is_available(self) -> bool:
        return bool(self.api_key)

    async def search(self, query: str, count: int, **kwargs: Any) -> str:
        if not self._client:
            return "Error: TAVILY_API_KEY not configured"

        try:
            n = min(max(count, 1), 20)
            response = await self._client.search(
                query=query,
                max_results=n,
                search_depth="basic",
            )

            results = response.get("results", [])
            if not results:
                return f"No results for: {query}"

            lines = [f"Results for: {query}\n"]
            for i, item in enumerate(results[:n], 1):
                lines.append(f"{i}. {item.get('title', '')}\n   {item.get('url', '')}")
                if content := item.get("content"):
                    snippet = content[:500]
                    suffix = "..." if len(content) > 500 else ""
                    lines.append(f"   {snippet}{suffix}")
            return "\n".join(lines)
        except Exception as e:
            return f"Error: {e}"
