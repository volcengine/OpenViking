"""AGFS Python SDK - Client library for AGFS Server API"""

__version__ = "0.1.7"

from .client import AGFSClient, FileHandle
from .exceptions import (
    AGFSClientError,
    AGFSConnectionError,
    AGFSHTTPError,
    AGFSNotSupportedError,
    AGFSTimeoutError,
)
from .helpers import cp, download, upload

# Binding client: try Rust native (ragfs-python via PyO3) first,
# then fall back to Go ctypes binding (libagfsbinding.so/dylib/dll).
try:
    from ragfs_python import RAGFSBindingClient as AGFSBindingClient

    BindingFileHandle = None  # FileHandle not yet implemented in ragfs-python
except ImportError:
    try:
        from .binding_client import AGFSBindingClient
        from .binding_client import FileHandle as BindingFileHandle
    except (ImportError, OSError):
        AGFSBindingClient = None
        BindingFileHandle = None

__all__ = [
    "AGFSClient",
    "AGFSBindingClient",
    "FileHandle",
    "BindingFileHandle",
    "AGFSClientError",
    "AGFSConnectionError",
    "AGFSTimeoutError",
    "AGFSHTTPError",
    "AGFSNotSupportedError",
    "cp",
    "upload",
    "download",
]
