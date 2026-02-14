"""Sandbox backend registry."""

from typing import TYPE_CHECKING, Type, Callable, Dict
from vikingbot.sandbox.base import SandboxBackend

_BACKENDS: Dict[str, Type[SandboxBackend]] = {}


def register_backend(name: str) -> Callable[[Type[SandboxBackend]], Type[SandboxBackend]]:
    """Decorator to register a sandbox backend."""

    def decorator(cls: Type[SandboxBackend]) -> Type[SandboxBackend]:
        _BACKENDS[name] = cls
        return cls
    return decorator


def get_backend(name: str) -> Type[SandboxBackend] | None:
    """Get backend class by name."""
    return _BACKENDS.get(name)


def list_backends() -> list[str]:
    """List all registered backends."""
    return list(_BACKENDS.keys())


# Import backends to register them (avoid circular import)
if not TYPE_CHECKING:
    from vikingbot.sandbox.backends import srt


def register_backend(name: str) -> Callable[[Type[SandboxBackend]], Type[SandboxBackend]]:
    """Decorator to register a sandbox backend."""

    def decorator(cls: Type[SandboxBackend]) -> Type[SandboxBackend]:
        _BACKENDS[name] = cls
        return cls
    return decorator


def get_backend(name: str) -> Type[SandboxBackend] | None:
    """Get backend class by name."""
    return _BACKENDS.get(name)


def list_backends() -> list[str]:
    """List all registered backends."""
    return list(_BACKENDS.keys())
