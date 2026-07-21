# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""
Registry for Data Accessors.

Manages DataAccessor registration and provides automatic source routing.
"""

import inspect
from pathlib import Path
from typing import Callable, List, Optional, Union

from openviking_cli.utils import get_logger

from .base import DataAccessor, LocalResource

logger = get_logger(__name__)


def _accepts_var_keyword(func: Callable) -> bool:
    """Whether ``func`` accepts arbitrary keyword arguments (**kwargs).

    Lets the registry forward accessor-selection hints to modern accessors
    (e.g. WebFeedAccessor's ``site=True`` override) while staying compatible
    with accessors whose ``can_handle`` only takes ``source``.
    """
    try:
        sig = inspect.signature(func)
    except (TypeError, ValueError):
        return True
    return any(p.kind == p.VAR_KEYWORD for p in sig.parameters.values())


class AccessorRegistry:
    """
    Registry for data accessors.

    Provides automatic accessor selection based on source type and priority.
    """

    def __init__(self, register_default: bool = True):
        """
        Initialize the accessor registry.

        Args:
            register_default: Whether to register default accessors
        """
        self._accessors: List[DataAccessor] = []

        if register_default:
            self._register_defaults()

    def _register_defaults(self) -> None:
        """Register default accessors."""
        # GitAccessor - handles git repositories
        try:
            from .git_accessor import GitAccessor

            self.register(GitAccessor())
        except Exception as e:
            logger.debug(f"[AccessorRegistry] Failed to register GitAccessor: {e}")

        # WebFeedAccessor - handles sitemap / RSS / Atom URLs (whole-site ingest)
        try:
            from .web_feed_accessor import WebFeedAccessor

            self.register(WebFeedAccessor())
        except Exception as e:
            logger.debug(f"[AccessorRegistry] Failed to register WebFeedAccessor: {e}")

        # HTTPAccessor - handles HTTP/HTTPS URLs
        try:
            from .http_accessor import HTTPAccessor

            self.register(HTTPAccessor())
        except Exception as e:
            logger.debug(f"[AccessorRegistry] Failed to register HTTPAccessor: {e}")

        # FeishuAccessor - handles Feishu/Lark documents
        try:
            from .feishu_accessor import FeishuAccessor

            self.register(FeishuAccessor())
        except Exception as e:
            logger.debug(f"[AccessorRegistry] Failed to register FeishuAccessor: {e}")

        # LocalAccessor - handles local files (lowest priority)
        try:
            from .local_accessor import LocalAccessor

            self.register(LocalAccessor())
        except Exception as e:
            logger.debug(f"[AccessorRegistry] Failed to register LocalAccessor: {e}")

    def register(self, accessor: DataAccessor) -> None:
        """
        Register an accessor.

        Accessors are stored in descending order of priority.
        When multiple accessors can handle the same source,
        the one with the highest priority is selected.

        Args:
            accessor: DataAccessor instance to register
        """
        # Insert in priority order (descending)
        insert_idx = len(self._accessors)
        for i, existing in enumerate(self._accessors):
            if accessor.priority > existing.priority:
                insert_idx = i
                break

        self._accessors.insert(insert_idx, accessor)
        logger.debug(
            f"[AccessorRegistry] Registered accessor {accessor.__class__.__name__} with priority {accessor.priority}"
        )

    def unregister(self, accessor_name: str) -> bool:
        """
        Unregister an accessor by class name.

        Args:
            accessor_name: Name of the accessor class to unregister

        Returns:
            True if an accessor was unregistered, False otherwise
        """
        for i, accessor in enumerate(self._accessors):
            if accessor.__class__.__name__ == accessor_name:
                del self._accessors[i]
                logger.debug(f"[AccessorRegistry] Unregistered accessor {accessor_name}")
                return True
        return False

    def get_accessor(self, source: Union[str, Path], **kwargs) -> Optional[DataAccessor]:
        """
        Get the highest priority accessor that can handle the source.

        Args:
            source: Source string or Path to check
            **kwargs: Optional accessor-selection hints forwarded to
                ``can_handle`` (e.g. an explicit ``site=True`` override).

        Returns:
            DataAccessor instance or None if no accessor can handle the source
        """
        for accessor in self._accessors:
            if kwargs and _accepts_var_keyword(accessor.can_handle):
                handled = accessor.can_handle(source, **kwargs)
            else:
                handled = accessor.can_handle(source)
            if handled:
                return accessor
        return None

    async def access(self, source: Union[str, Path], **kwargs) -> LocalResource:
        """
        Access a source by routing to the appropriate accessor.

        Args:
            source: Source string (URL, path, etc.) or Path object
            **kwargs: Additional arguments passed to the accessor

        Returns:
            LocalResource pointing to the locally available data
        """
        source_str = str(source)

        # Find an accessor - LocalAccessor should always be registered as fallback.
        # Forward kwargs so accessors can honor explicit selection hints (e.g.
        # args={"site": True}) that aren't inferable from the URL alone.
        accessor = self.get_accessor(source, **kwargs)
        if accessor:
            logger.debug(
                f"[AccessorRegistry] Using accessor {accessor.__class__.__name__} for source: {source_str}"
            )
            return await accessor.access(source, **kwargs)

        # This should not happen if LocalAccessor is registered
        raise RuntimeError(
            f"No accessor found for source: {source_str}. "
            "LocalAccessor should be registered as a fallback."
        )

    def clear(self) -> None:
        """Remove all registered accessors."""
        self._accessors.clear()
        logger.debug("[AccessorRegistry] Cleared all accessors from registry")


# Global registry instance
_default_registry: Optional[AccessorRegistry] = None


def get_accessor_registry(register_default: bool = True) -> AccessorRegistry:
    """
    Get the default accessor registry.

    Args:
        register_default: Whether to register default accessors if creating
            a new registry instance (only used on first call)

    Returns:
        The global AccessorRegistry instance
    """
    global _default_registry
    if _default_registry is None:
        _default_registry = AccessorRegistry(register_default=register_default)
    return _default_registry


async def access(source: Union[str, Path], **kwargs) -> LocalResource:
    """
    Access a source using the default registry.

    Args:
        source: Source string or Path
        **kwargs: Additional arguments

    Returns:
        LocalResource
    """
    return await get_accessor_registry().access(source, **kwargs)
