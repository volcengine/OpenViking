"""Firecrawl Search backend."""

import os
from typing import Any

import httpx

from .base import WebSearchBackend
from .registry import register_backend


@register_backend
class FirecrawlBackend(WebSearchBackend):
    """Firecrawl Search API backend."""

    name = "firecrawl"

    def __init__(self, api_key: str | None = None):
        self.api_key = api_key or os.environ.get("FIRECRAWL_API_KEY", "")

    @property
    def is_available(self) -> bool:
        return bool(self.api_key)

    async def search(self, query: str, count: int, **kwargs: Any) -> str:
        if not self.api_key:
            return "Error: FIRECRAWL_API_KEY not configured"

        try:
            n = min(max(count, 1), 20)
            async with httpx.AsyncClient() as client:
                r = await client.post(
                    "https://api.firecrawl.dev/v2/search",
                    headers={
                        "Authorization": f"Bearer {self.api_key}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "query": query,
                        "limit": n,
                        "sources": ["web"],
                        # Return the one-line search snippet instead of page excerpts
                        "highlights": False,
                        "origin": "openviking",
                    },
                    timeout=25.0,
                )
            if r.is_error:
                try:
                    detail = r.json().get("error") or r.reason_phrase
                except ValueError:
                    detail = r.reason_phrase
                return f"Error: Firecrawl search failed ({r.status_code}): {detail}"

            results = (r.json().get("data") or {}).get("web") or []
            if not results:
                return f"No results for: {query}"

            lines = [f"Results for: {query}\n"]
            for i, item in enumerate(results[:n], 1):
                lines.append(f"{i}. {item.get('title', '')}\n   {item.get('url', '')}")
                if description := item.get("description"):
                    snippet = description[:500]
                    suffix = "..." if len(description) > 500 else ""
                    lines.append(f"   {snippet}{suffix}")
            return "\n".join(lines)
        except Exception as e:
            return f"Error: {str(e) or type(e).__name__}"
