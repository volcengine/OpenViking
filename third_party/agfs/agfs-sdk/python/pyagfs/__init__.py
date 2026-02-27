"""AGFS Python SDK - Client library for AGFS Server API"""

__version__ = "0.1.7"

from .client import AGFSClient, FileHandle
from .binding_client import AGFSBindingClient, FileHandle as BindingFileHandle
from .exceptions import (
    AGFSClientError,
    AGFSConnectionError,
    AGFSTimeoutError,
    AGFSHTTPError,
    AGFSNotSupportedError,
)
from .helpers import cp, upload, download

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
