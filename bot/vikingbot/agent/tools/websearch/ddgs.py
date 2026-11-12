"""DDGS (DuckDuckGo) backend - free, no API key required."""

import asyncio
from typing import Any

from .base import WebSearchBackend
from .registry import register_backend
from .url_safety import safe_public_result_url


@register_backend
class DDGSBackend(WebSearchBackend):
    """DDGS (DuckDuckGo) backend - free, no API key required."""

    name = "ddgs"

    def __init__(self):
        self._ddgs = None

    def _get_ddgs(self):
        if self._ddgs is None:
            from ddgs import DDGS

            self._ddgs = DDGS()
        return self._ddgs

    @property
    def is_available(self) -> bool:
        try:
            from ddgs import DDGS

            return True
        except ImportError:
            return False

    async def search(self, query: str, count: int, **kwargs: Any) -> str:
        try:
            n = min(max(count, 1), 20)
            ddgs = self._get_ddgs()

            results = await asyncio.to_thread(ddgs.text, query=query, max_results=n)

            if not results:
                return f"No results for: {query}"

            lines = [f"Results for: {query}\n"]
            result_number = 0
            for item in results[:n]:
                url = safe_public_result_url(item.get("href"))
                if not url:
                    continue
                result_number += 1
                lines.append(f"{result_number}. {item.get('title', '')}\n   {url}")
                if body := item.get("body"):
                    lines.append(f"   {body}")
            if result_number == 0:
                return f"No results for: {query}"
            return "\n".join(lines)
        except Exception as e:
            return f"Error: {e}"
