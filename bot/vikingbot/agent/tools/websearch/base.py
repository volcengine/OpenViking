"""Web search backend base class."""

from abc import ABC, abstractmethod
from typing import Any


class WebSearchBackend(ABC):
    """Abstract base class for web search backends."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Backend name: brave, ddgs, exa."""
        pass

    @property
    @abstractmethod
    def is_available(self) -> bool:
        """Check if this backend is available (API key configured, dependencies installed)."""
        pass

    @abstractmethod
    async def search(self, query: str, count: int, **kwargs: Any) -> str:
        """
        Execute search.

        Args:
            query: Search query
            count: Number of results
            **kwargs: Backend-specific parameters

        Returns:
            Formatted search results string
        """
        pass
