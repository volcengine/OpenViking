"""Webz.io News Search backend."""

import os
from typing import Any

import httpx

from .base import WebSearchBackend
from .registry import register_backend


@register_backend
class WebzBackend(WebSearchBackend):
    """Search the Webz.io news index using a natural-language query."""

    name = "webz"
    filters_schema = {
        "type": "object",
        "description": "Optional filters for recent news. Use language names and ISO alpha-2 country codes.",
        "properties": {
            **{
                field: {"type": "array", "items": {"type": "string"}}
                for field in ("language", "country", "sentiment", "domain", "ticker")
            },
            "published_from": {"type": "string", "description": "Start date (YYYY-MM-DD)"},
            "published_to": {"type": "string", "description": "End date (YYYY-MM-DD)"},
        },
    }

    def __init__(self):
        self.api_token = os.environ.get("WEBZ_API_TOKEN", "")

    @property
    def is_available(self) -> bool:
        return bool(self.api_token)

    async def search(
        self, query: str, count: int, *, filters: dict[str, Any] | None = None, **kwargs: Any
    ) -> str:
        if not self.is_available:
            return "Error: WEBZ_API_TOKEN not configured"

        n = min(max(count, 1), 20)
        payload: dict[str, Any] = {"query": query, "k": n}
        if filters:
            payload["filters"] = filters

        try:
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    "https://api.webz.io/api/news/context",
                    headers={"Authorization": f"Bearer {self.api_token}"},
                    json=payload,
                    timeout=25.0,
                )
                response.raise_for_status()
            results = response.json().get("results", [])
        except httpx.HTTPStatusError as exc:
            return f"Error: Webz.io returned HTTP {exc.response.status_code}"
        except httpx.RequestError as exc:
            return f"Error: Webz.io request failed ({type(exc).__name__})"
        except ValueError:
            return "Error: Webz.io returned invalid JSON"

        if not results:
            return f"No results for: {query}"

        lines = [f"Results for: {query}\n"]
        for i, item in enumerate(results[:n], 1):
            article = item["article"]
            lines.append(f"{i}. {article['title']}\n   {article['url']}")
            if published_at := article.get("published_at"):
                lines.append(f"   Published: {published_at}")
            if snippet := item.get("chunk", {}).get("text"):
                suffix = "..." if len(snippet) > 500 else ""
                lines.append(f"   {snippet[:500]}{suffix}")
        return "\n".join(lines)
