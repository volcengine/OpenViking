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
    AGFSAlreadyExistsError,
    AGFSClientError,
    AGFSConfigError,
    AGFSConnectionError,
    AGFSDirectoryNotEmptyError,
    AGFSFileExistsError,
    AGFSHTTPError,
    AGFSInternalError,
    AGFSInvalidOperationError,
    AGFSInvalidPathError,
    AGFSIoError,
    AGFSIsADirectoryError,
    AGFSMountPointExistsError,
    AGFSMountPointNotFoundError,
    AGFSNetworkError,
    AGFSNotADirectoryError,
    AGFSNotFoundError,
    AGFSNotSupportedError,
    AGFSPermissionDeniedError,
    AGFSPluginError,
    AGFSSerializationError,
    AGFSTimeoutError,
)
from .helpers import cp, download, upload

_logger = logging.getLogger(__name__)

# Directory that ships pre-built native libraries (Rust .so/.dylib).
_LIB_DIR = Path(__file__).resolve().parent.parent / "lib"


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


def get_binding_client():
    """Get the RAGFS binding client class.

    Returns:
        ``(RAGFSBindingClient_class, BindingFileHandle_class)``
    """
    try:
        client, fh = _load_rust_binding()
        _logger.info("Loaded RAGFS Rust binding")
        return client, fh
    except ImportError as exc:
        raise ImportError("ragfs_python native library is not available: " + str(exc)) from exc


# Module-level defaults
# Ensure module import never fails, even if bindings are unavailable
try:
    RAGFSBindingClient, BindingFileHandle = get_binding_client()
    # Backward compatibility alias
    AGFSBindingClient = RAGFSBindingClient
except Exception:
    _logger.warning(
        "Failed to initialize RAGFSBindingClient during module import; "
        "RAGFSBindingClient will be None. Use get_binding_client() for explicit handling."
    )
    RAGFSBindingClient = None
    AGFSBindingClient = None
    BindingFileHandle = None

__all__ = [
    "AGFSClient",
    "AGFSBindingClient",
    "RAGFSBindingClient",
    "FileHandle",
    "BindingFileHandle",
    "get_binding_client",
    "AGFSClientError",
    "AGFSConnectionError",
    "AGFSTimeoutError",
    "AGFSHTTPError",
    "AGFSNotSupportedError",
    "AGFSNotFoundError",
    "AGFSAlreadyExistsError",
    "AGFSFileExistsError",
    "AGFSPermissionDeniedError",
    "AGFSInvalidPathError",
    "AGFSNotADirectoryError",
    "AGFSIsADirectoryError",
    "AGFSDirectoryNotEmptyError",
    "AGFSInvalidOperationError",
    "AGFSIoError",
    "AGFSConfigError",
    "AGFSMountPointNotFoundError",
    "AGFSMountPointExistsError",
    "AGFSSerializationError",
    "AGFSNetworkError",
    "AGFSInternalError",
    "AGFSPluginError",
    "cp",
    "upload",
    "download",
]
