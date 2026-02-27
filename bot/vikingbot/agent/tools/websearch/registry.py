"""Web search backend registry."""

from typing import Dict, List, Type, Optional

from .base import WebSearchBackend


class WebSearchBackendRegistry:
    """Registry for web search backends.

    Open/Closed Principle: Add new backends without modifying this class.
    """

    def __init__(self):
        self._backends: Dict[str, Type[WebSearchBackend]] = {}

    def register(self, backend_class: Type[WebSearchBackend]) -> Type[WebSearchBackend]:
        """
        Register a backend class.

        Usage:
            @registry.register
            class MyBackend(WebSearchBackend):
                name = "mybackend"
                ...
        """
        name = backend_class.name
        self._backends[name] = backend_class
        return backend_class

    def get(self, name: str) -> Optional[Type[WebSearchBackend]]:
        """Get backend class by name."""
        return self._backends.get(name)

    def list_names(self) -> List[str]:
        """List all registered backend names."""
        return list(self._backends.keys())

    def create(
        self, name: str, brave_api_key: Optional[str] = None, exa_api_key: Optional[str] = None
    ) -> Optional[WebSearchBackend]:
        """
        Create a backend instance.

        Args:
            name: Backend name
            brave_api_key: Brave API key (for brave backend)
            exa_api_key: Exa API key (for exa backend)

        Returns:
            Backend instance or None
        """
        backend_class = self.get(name)
        if not backend_class:
            return None

        # Pass appropriate parameters based on backend type
        if name == "brave":
            return backend_class(api_key=brave_api_key)
        elif name == "exa":
            return backend_class(api_key=exa_api_key)
        else:
            return backend_class()

    def select_auto(
        self, brave_api_key: Optional[str] = None, exa_api_key: Optional[str] = None
    ) -> WebSearchBackend:
        """
        Auto-select the best available backend.

        Priority: exa → brave → ddgs
        """
        priority = ["exa", "brave", "ddgs"]

        for name in priority:
            backend = self.create(name, brave_api_key, exa_api_key)
            if backend and backend.is_available:
                return backend

        # Fallback to ddgs (should always be available if installed)
        ddgs = self.create("ddgs")
        if ddgs:
            return ddgs

        raise RuntimeError("No web search backend available")


# Global registry instance
registry = WebSearchBackendRegistry()


# Decorator for easy registration
def register_backend(cls: Type[WebSearchBackend]) -> Type[WebSearchBackend]:
    """
    Decorator to register a backend class.

    Usage:
        @register_backend
        class MyBackend(WebSearchBackend):
            name = "mybackend"
            ...
    """
    return registry.register(cls)
