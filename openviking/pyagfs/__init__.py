"""AGFS Python SDK - Client library for AGFS Server API"""

__version__ = "0.1.7"

import logging
import os

from .client import AGFSClient, FileHandle
from .exceptions import (
    AGFSClientError,
    AGFSConnectionError,
    AGFSHTTPError,
    AGFSNotSupportedError,
    AGFSTimeoutError,
)
from .helpers import cp, download, upload

_logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Binding implementation selection via RAGFS_IMPL environment variable.
#
#   RAGFS_IMPL=auto  (default) — Rust first, Go fallback
#   RAGFS_IMPL=rust             — Rust only, error if unavailable
#   RAGFS_IMPL=go               — Go only, error if unavailable
# ---------------------------------------------------------------------------

_RAGFS_IMPL_ENV = os.environ.get("RAGFS_IMPL", "").lower() or None


def _load_rust_binding():
    """Attempt to load the Rust (PyO3) binding client."""
    from ragfs_python import RAGFSBindingClient as _Rust

    return _Rust, None  # FileHandle not yet implemented in ragfs-python


def _load_go_binding():
    """Attempt to load the Go (ctypes) binding client."""
    from .binding_client import AGFSBindingClient as _Go
    from .binding_client import FileHandle as _GoFH

    return _Go, _GoFH


def _resolve_binding(impl: str):
    """Return (AGFSBindingClient, BindingFileHandle) based on *impl*.

    *impl* should be one of ``"auto"``, ``"rust"``, or ``"go"``.
    """

    if impl == "rust":
        try:
            client, fh = _load_rust_binding()
            _logger.info("RAGFS_IMPL=rust: loaded Rust binding")
            return client, fh
        except ImportError as exc:
            raise ImportError(
                "RAGFS_IMPL=rust but ragfs_python is not installed: " + str(exc)
            ) from exc

    if impl == "go":
        try:
            client, fh = _load_go_binding()
            _logger.info("RAGFS_IMPL=go: loaded Go binding")
            return client, fh
        except (ImportError, OSError) as exc:
            raise ImportError(
                "RAGFS_IMPL=go but Go binding (libagfsbinding) is not available: " + str(exc)
            ) from exc

    if impl == "auto":
        # Rust first, Go fallback, silent None if neither available
        try:
            client, fh = _load_rust_binding()
            _logger.info("RAGFS_IMPL=auto: loaded Rust binding")
            return client, fh
        except ImportError:
            pass

        try:
            client, fh = _load_go_binding()
            _logger.info("RAGFS_IMPL=auto: Rust unavailable, loaded Go binding")
            return client, fh
        except (ImportError, OSError):
            pass

        _logger.warning(
            "RAGFS_IMPL=auto: neither Rust nor Go binding available; AGFSBindingClient will be None"
        )
        return None, None

    raise ValueError(f"Invalid RAGFS_IMPL value: '{impl}'. Must be one of: auto, rust, go")


def get_binding_client(config_impl: str = "auto"):
    """Resolve binding classes with env-var override.

    Priority: ``RAGFS_IMPL`` env var  >  *config_impl*  >  ``"auto"``

    Returns:
        ``(AGFSBindingClient_class, BindingFileHandle_class)``
    """
    effective = _RAGFS_IMPL_ENV or config_impl or "auto"
    return _resolve_binding(effective)


# Module-level defaults (used when importing ``from openviking.pyagfs import AGFSBindingClient``)
AGFSBindingClient, BindingFileHandle = _resolve_binding(_RAGFS_IMPL_ENV or "auto")

__all__ = [
    "AGFSClient",
    "AGFSBindingClient",
    "FileHandle",
    "BindingFileHandle",
    "get_binding_client",
    "AGFSClientError",
    "AGFSConnectionError",
    "AGFSTimeoutError",
    "AGFSHTTPError",
    "AGFSNotSupportedError",
    "cp",
    "upload",
    "download",
]
