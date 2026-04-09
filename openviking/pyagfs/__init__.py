"""AGFS Python SDK - Client library for AGFS Server API"""

__version__ = "0.1.7"

import glob
import importlib.util
import logging
import os
import sysconfig
from pathlib import Path

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

# Directory that ships pre-built native libraries (Go .so/.dylib and Rust .so/.dylib).
_LIB_DIR = Path(__file__).resolve().parent.parent / "lib"

# ---------------------------------------------------------------------------
# Binding implementation selection via RAGFS_IMPL environment variable.
#
#   RAGFS_IMPL=auto  (default) — Rust first, Go fallback
#   RAGFS_IMPL=rust             — Rust only, error if unavailable
#   RAGFS_IMPL=go               — Go only, error if unavailable
# ---------------------------------------------------------------------------

_RAGFS_IMPL_ENV = os.environ.get("RAGFS_IMPL", "").lower() or None


def _find_ragfs_so():
    """Locate the ragfs_python native extension inside openviking/lib/.

    Returns the path to the ``.so`` / ``.dylib`` / ``.pyd`` file, or *None*.
    """
    try:
        ext_suffix = sysconfig.get_config_var("EXT_SUFFIX") or ".so"
        # Exact match first: ragfs_python.cpython-312-darwin.so
        exact = _LIB_DIR / f"ragfs_python{ext_suffix}"
        if exact.exists():
            return str(exact)
        # Glob fallback: ragfs_python.cpython-*.so / ragfs_python.*.pyd
        for pattern in ("ragfs_python.cpython-*", "ragfs_python.*"):
            matches = glob.glob(str(_LIB_DIR / pattern))
            if matches:
                return matches[0]
    except Exception:
        pass
    return None


def _load_rust_binding():
    """Attempt to load the Rust (PyO3) binding client.

    Searches openviking/lib/ for the pre-built native extension first,
    then falls back to a pip-installed ``ragfs_python`` package.
    """
    try:
        so_path = _find_ragfs_so()
        if so_path:
            spec = importlib.util.spec_from_file_location("ragfs_python", so_path)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            return mod.RAGFSBindingClient, None

        # Fallback: maybe ragfs_python was pip-installed (dev environment)
        from ragfs_python import RAGFSBindingClient as _Rust

        return _Rust, None
    except Exception:
        raise ImportError("Rust binding not available")


def _load_go_binding():
    """Attempt to load the Go (ctypes) binding client."""
    try:
        from .binding_client import AGFSBindingClient as _Go
        from .binding_client import FileHandle as _GoFH

        return _Go, _GoFH
    except Exception:
        raise ImportError("Go binding not available")


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
                "RAGFS_IMPL=rust but ragfs_python native library is not available: " + str(exc)
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
            _logger.info("RAGFS_IMPL=auto: loaded Rust binding (ragfs-python)")
            return client, fh
        except Exception:
            pass

        try:
            client, fh = _load_go_binding()
            _logger.info("RAGFS_IMPL=auto: Rust unavailable, loaded Go binding (libagfsbinding)")
            return client, fh
        except Exception:
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
# Ensure module import never fails, even if bindings are unavailable
try:
    AGFSBindingClient, BindingFileHandle = _resolve_binding(_RAGFS_IMPL_ENV or "auto")
except Exception:
    _logger.warning(
        "Failed to initialize AGFSBindingClient during module import; "
        "AGFSBindingClient will be None. Use get_binding_client() for explicit handling."
    )
    AGFSBindingClient = None
    BindingFileHandle = None

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
