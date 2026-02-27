"""
Web search tool with multiple backends (brave, ddgs, exa).

To add a new backend:
    1. Create new file: websearch/mybackend.py
    2. Inherit WebSearchBackend
    3. Add @register_backend decorator
    4. Import here: from . import mybackend

NO NEED TO MODIFY THIS CLASS!
"""

from typing import Any, Optional, Union

from vikingbot.agent.tools.base import Tool

from .base import WebSearchBackend
from .registry import registry

# Import backends to register them
from . import brave, ddgs, exa


class WebSearchTool(Tool):
    """
    Search the web with configurable backend.

    To add a new backend:
        1. Create new file: websearch/mybackend.py
        2. Inherit WebSearchBackend
        3. Add @register_backend decorator
        4. Import in websearch/__init__.py

    NO NEED TO MODIFY THIS CLASS!
    """

    name = "web_search"
    description = "Search the web. Returns titles, URLs, and snippets."
    parameters = {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Search query"},
            "count": {
                "type": "integer",
                "description": "Results (1-20)",
                "minimum": 1,
                "maximum": 20,
            },
            # Backend-specific optional parameters (forwarded to backend)
            "type": {
                "type": "string",
                "enum": ["auto", "fast", "deep"],
                "description": "Exa: Search type",
            },
            "livecrawl": {
                "type": "string",
                "enum": ["fallback", "preferred"],
                "description": "Exa: Live crawl mode",
            },
        },
        "required": ["query"],
    }

    def __init__(
        self,
        backend: Union[str, WebSearchBackend] = "auto",
        brave_api_key: Optional[str] = None,
        exa_api_key: Optional[str] = None,
        max_results: int = 5,
    ):
        """
        Initialize WebSearchTool.

        Args:
            backend: Backend name ("auto", "brave", "ddgs", "exa") or WebSearchBackend instance
            brave_api_key: Brave Search API key
            exa_api_key: Exa AI API key
            max_results: Default max results
        """
        self.max_results = max_results
        self._brave_api_key = brave_api_key
        self._exa_api_key = exa_api_key

        # Select backend
        if isinstance(backend, WebSearchBackend):
            self._backend = backend
        elif backend == "auto":
            self._backend = registry.select_auto(brave_api_key, exa_api_key)
        else:
            self._backend = registry.create(backend, brave_api_key, exa_api_key)
            if not self._backend:
                raise ValueError(f"Unknown backend: {backend}")

    @property
    def backend(self) -> WebSearchBackend:
        """Get the active backend."""
        return self._backend

    async def execute(
        self, tool_context: "ToolContext", query: str, count: Optional[int] = None, **kwargs: Any
    ) -> str:
        n = min(max(count or self.max_results, 1), 20)
        return await self._backend.search(query, n, **kwargs)
