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

# Binding client depends on a native shared library (libagfsbinding.so/dylib/dll).
# Make it optional so the pure-HTTP AGFSClient remains usable when the native
# library is not installed (e.g. Docker images without CGO build).
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
