"""Brave Search backend."""

import os
from typing import Any

import httpx

from .base import WebSearchBackend
from .registry import register_backend
from .url_safety import safe_public_result_url


@register_backend
class BraveBackend(WebSearchBackend):
    """Brave Search API backend."""

    name = "brave"

    def __init__(self, api_key: str | None = None):
        self.api_key = api_key or os.environ.get("BRAVE_API_KEY", "")

    @property
    def is_available(self) -> bool:
        return bool(self.api_key)

    async def search(self, query: str, count: int, **kwargs: Any) -> str:
        if not self.api_key:
            return "Error: BRAVE_API_KEY not configured"

        try:
            n = min(max(count, 1), 10)
            async with httpx.AsyncClient() as client:
                r = await client.get(
                    "https://api.search.brave.com/res/v1/web/search",
                    params={"q": query, "count": n},
                    headers={"Accept": "application/json", "X-Subscription-Token": self.api_key},
                    timeout=10.0,
                )
                r.raise_for_status()

            results = r.json().get("web", {}).get("results", [])
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
                if desc := item.get("description"):
                    lines.append(f"   {desc}")
            if result_number == 0:
                return f"No results for: {query}"
            return "\n".join(lines)
        except Exception as e:
            return f"Error: {e}"
