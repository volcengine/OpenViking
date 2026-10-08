"""Serply Search backend."""

import os
from typing import Any
from urllib.parse import urlencode

import httpx

from .base import WebSearchBackend
from .registry import register_backend


@register_backend
class SerplyBackend(WebSearchBackend):
    """Serply Search API backend."""

    name = "serply"

    def __init__(self, api_key: str | None = None):
        self.api_key = api_key or os.environ.get("SERPLY_API_KEY", "")

    @property
    def is_available(self) -> bool:
        return bool(self.api_key)

    async def search(self, query: str, count: int, **kwargs: Any) -> str:
        if not self.api_key:
            return "Error: SERPLY_API_KEY not configured"

        try:
            n = min(max(count, 1), 20)
            # Serply embeds the encoded query string in the URL path
            async with httpx.AsyncClient() as client:
                r = await client.get(
                    f"https://api.serply.io/v1/search/{urlencode({'q': query, 'num': n})}",
                    headers={"Accept": "application/json", "X-Api-Key": self.api_key},
                    timeout=10.0,
                )
                r.raise_for_status()

            results = r.json().get("results", [])
            if not results:
                return f"No results for: {query}"

            lines = [f"Results for: {query}\n"]
            for i, item in enumerate(results[:n], 1):
                lines.append(f"{i}. {item.get('title', '')}\n   {item.get('link', '')}")
                if desc := item.get("description"):
                    lines.append(f"   {desc}")
            return "\n".join(lines)
        except Exception as e:
            return f"Error: {e}"
