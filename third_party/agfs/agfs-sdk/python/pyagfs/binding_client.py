"""AGFS Python Binding Client - Direct binding to AGFS Server implementation"""

import ctypes
import json
import os
import platform
from pathlib import Path
from typing import List, Dict, Any, Optional, Union, Iterator, BinaryIO

from .exceptions import AGFSClientError, AGFSNotSupportedError


def _find_library() -> str:
    """Find the AGFS binding shared library."""
    system = platform.system()

    if system == "Darwin":
        lib_name = "libagfsbinding.dylib"
    elif system == "Linux":
        lib_name = "libagfsbinding.so"
    elif system == "Windows":
        lib_name = "libagfsbinding.dll"
    else:
        raise AGFSClientError(f"Unsupported platform: {system}")

    search_paths = [
        Path(__file__).parent / "lib" / lib_name,
        Path(__file__).parent.parent / "lib" / lib_name,
        Path("/usr/local/lib") / lib_name,
        Path("/usr/lib") / lib_name,
        Path(os.environ.get("AGFS_LIB_PATH", "")) / lib_name
        if os.environ.get("AGFS_LIB_PATH")
        else None,
        Path("/tmp") / lib_name,
    ]

    for path in search_paths:
        if path and path.exists():
            return str(path)

    raise AGFSClientError(
        f"Could not find {lib_name}. Please set AGFS_LIB_PATH environment variable "
        f"or install the library to /usr/local/lib"
    )


class BindingLib:
    """Wrapper for the AGFS binding shared library."""

    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._load_library()
        return cls._instance

    def _load_library(self):
        lib_path = _find_library()
        self.lib = ctypes.CDLL(lib_path)
        self._setup_functions()

    def _setup_functions(self):
        self.lib.AGFS_NewClient.argtypes = []
        self.lib.AGFS_NewClient.restype = ctypes.c_int64

        self.lib.AGFS_FreeClient.argtypes = [ctypes.c_int64]
        self.lib.AGFS_FreeClient.restype = None

        self.lib.AGFS_GetLastError.argtypes = [ctypes.c_int64]
        self.lib.AGFS_GetLastError.restype = ctypes.c_char_p

        self.lib.AGFS_FreeString.argtypes = [ctypes.c_char_p]
        self.lib.AGFS_FreeString.restype = None

        self.lib.AGFS_Health.argtypes = [ctypes.c_int64]
        self.lib.AGFS_Health.restype = ctypes.c_int

        self.lib.AGFS_GetCapabilities.argtypes = [ctypes.c_int64]
        self.lib.AGFS_GetCapabilities.restype = ctypes.c_char_p

        self.lib.AGFS_Ls.argtypes = [ctypes.c_int64, ctypes.c_char_p]
        self.lib.AGFS_Ls.restype = ctypes.c_char_p

        self.lib.AGFS_Read.argtypes = [
            ctypes.c_int64,
            ctypes.c_char_p,
            ctypes.c_int64,
            ctypes.c_int64,
            ctypes.POINTER(ctypes.c_char_p),
            ctypes.POINTER(ctypes.c_int64),
        ]
        self.lib.AGFS_Read.restype = ctypes.c_int64

        self.lib.AGFS_Write.argtypes = [
            ctypes.c_int64,
            ctypes.c_char_p,
            ctypes.c_void_p,
            ctypes.c_int64,
        ]
        self.lib.AGFS_Write.restype = ctypes.c_char_p

        self.lib.AGFS_Create.argtypes = [ctypes.c_int64, ctypes.c_char_p]
        self.lib.AGFS_Create.restype = ctypes.c_char_p

        self.lib.AGFS_Mkdir.argtypes = [ctypes.c_int64, ctypes.c_char_p, ctypes.c_uint]
        self.lib.AGFS_Mkdir.restype = ctypes.c_char_p

        self.lib.AGFS_Rm.argtypes = [ctypes.c_int64, ctypes.c_char_p, ctypes.c_int]
        self.lib.AGFS_Rm.restype = ctypes.c_char_p

        self.lib.AGFS_Stat.argtypes = [ctypes.c_int64, ctypes.c_char_p]
        self.lib.AGFS_Stat.restype = ctypes.c_char_p

        self.lib.AGFS_Mv.argtypes = [ctypes.c_int64, ctypes.c_char_p, ctypes.c_char_p]
        self.lib.AGFS_Mv.restype = ctypes.c_char_p

        self.lib.AGFS_Chmod.argtypes = [ctypes.c_int64, ctypes.c_char_p, ctypes.c_uint]
        self.lib.AGFS_Chmod.restype = ctypes.c_char_p

        self.lib.AGFS_Touch.argtypes = [ctypes.c_int64, ctypes.c_char_p]
        self.lib.AGFS_Touch.restype = ctypes.c_char_p

        self.lib.AGFS_Mounts.argtypes = [ctypes.c_int64]
        self.lib.AGFS_Mounts.restype = ctypes.c_char_p

        self.lib.AGFS_Mount.argtypes = [
            ctypes.c_int64,
            ctypes.c_char_p,
            ctypes.c_char_p,
            ctypes.c_char_p,
        ]
        self.lib.AGFS_Mount.restype = ctypes.c_char_p

        self.lib.AGFS_Unmount.argtypes = [ctypes.c_int64, ctypes.c_char_p]
        self.lib.AGFS_Unmount.restype = ctypes.c_char_p

        self.lib.AGFS_LoadPlugin.argtypes = [ctypes.c_int64, ctypes.c_char_p]
        self.lib.AGFS_LoadPlugin.restype = ctypes.c_char_p

        self.lib.AGFS_UnloadPlugin.argtypes = [ctypes.c_int64, ctypes.c_char_p]
        self.lib.AGFS_UnloadPlugin.restype = ctypes.c_char_p

        self.lib.AGFS_ListPlugins.argtypes = [ctypes.c_int64]
        self.lib.AGFS_ListPlugins.restype = ctypes.c_char_p

        self.lib.AGFS_OpenHandle.argtypes = [
            ctypes.c_int64,
            ctypes.c_char_p,
            ctypes.c_int,
            ctypes.c_uint,
            ctypes.c_int,
        ]
        self.lib.AGFS_OpenHandle.restype = ctypes.c_int64

        self.lib.AGFS_CloseHandle.argtypes = [ctypes.c_int64]
        self.lib.AGFS_CloseHandle.restype = ctypes.c_char_p

        self.lib.AGFS_HandleRead.argtypes = [
            ctypes.c_int64,
            ctypes.c_int64,
            ctypes.c_int64,
            ctypes.c_int,
        ]
        self.lib.AGFS_HandleRead.restype = ctypes.c_char_p

        self.lib.AGFS_HandleWrite.argtypes = [
            ctypes.c_int64,
            ctypes.c_void_p,
            ctypes.c_int64,
            ctypes.c_int64,
            ctypes.c_int,
        ]
        self.lib.AGFS_HandleWrite.restype = ctypes.c_char_p

        self.lib.AGFS_HandleSeek.argtypes = [ctypes.c_int64, ctypes.c_int64, ctypes.c_int]
        self.lib.AGFS_HandleSeek.restype = ctypes.c_char_p

        self.lib.AGFS_HandleSync.argtypes = [ctypes.c_int64]
        self.lib.AGFS_HandleSync.restype = ctypes.c_char_p

        self.lib.AGFS_HandleStat.argtypes = [ctypes.c_int64]
        self.lib.AGFS_HandleStat.restype = ctypes.c_char_p

        self.lib.AGFS_ListHandles.argtypes = [ctypes.c_int64]
        self.lib.AGFS_ListHandles.restype = ctypes.c_char_p

        self.lib.AGFS_GetHandleInfo.argtypes = [ctypes.c_int64]
        self.lib.AGFS_GetHandleInfo.restype = ctypes.c_char_p


class AGFSBindingClient:
    """Client for interacting with AGFS using Python binding (no HTTP server required).

    This client directly uses the AGFS server implementation through a shared library,
    providing better performance than the HTTP client by avoiding network overhead.

    The interface is compatible with the HTTP client (AGFSClient), allowing easy
    switching between implementations.
    """

    def __init__(self, config_path: Optional[str] = None):
        """
        Initialize AGFS binding client.

        Args:
            config_path: Optional path to configuration file (not used in binding mode).
        """
        self._lib = BindingLib()
        self._client_id = self._lib.lib.AGFS_NewClient()
        if self._client_id <= 0:
            raise AGFSClientError("Failed to create AGFS client")

    def __del__(self):
        if hasattr(self, "_client_id") and self._client_id > 0:
            try:
                self._lib.lib.AGFS_FreeClient(self._client_id)
            except Exception:
                pass

    def _parse_response(self, result: bytes) -> Dict[str, Any]:
        """Parse JSON response from the library."""
        if isinstance(result, bytes):
            result = result.decode("utf-8")
        data = json.loads(result)

        if "error_id" in data and data["error_id"] != 0:
            error_msg = self._lib.lib.AGFS_GetLastError(data["error_id"])
            if isinstance(error_msg, bytes):
                error_msg = error_msg.decode("utf-8")
            raise AGFSClientError(error_msg if error_msg else "Unknown error")

        return data

    def health(self) -> Dict[str, Any]:
        """Check client health."""
        result = self._lib.lib.AGFS_Health(self._client_id)
        return {"status": "healthy" if result == 1 else "unhealthy"}

    def get_capabilities(self) -> Dict[str, Any]:
        """Get client capabilities."""
        result = self._lib.lib.AGFS_GetCapabilities(self._client_id)
        return self._parse_response(result)

    def ls(self, path: str = "/") -> List[Dict[str, Any]]:
        """List directory contents."""
        result = self._lib.lib.AGFS_Ls(self._client_id, path.encode("utf-8"))
        data = self._parse_response(result)
        return data.get("files", [])

    def read(self, path: str, offset: int = 0, size: int = -1, stream: bool = False):
        return self.cat(path, offset, size, stream)

    def cat(self, path: str, offset: int = 0, size: int = -1, stream: bool = False):
        """Read file content with optional offset and size."""
        if stream:
            raise AGFSNotSupportedError("Streaming not supported in binding mode")

        result_ptr = ctypes.c_char_p()
        size_ptr = ctypes.c_int64()

        error_id = self._lib.lib.AGFS_Read(
            self._client_id,
            path.encode("utf-8"),
            ctypes.c_int64(offset),
            ctypes.c_int64(size),
            ctypes.byref(result_ptr),
            ctypes.byref(size_ptr),
        )

        if error_id < 0:
            error_msg = self._lib.lib.AGFS_GetLastError(error_id)
            if isinstance(error_msg, bytes):
                error_msg = error_msg.decode("utf-8")
            raise AGFSClientError(error_msg if error_msg else "Unknown error")

        if result_ptr:
            data = ctypes.string_at(result_ptr, size_ptr.value)
            return data

        return b""

    def write(
        self, path: str, data: Union[bytes, Iterator[bytes], BinaryIO], max_retries: int = 3
    ) -> str:
        """Write data to file."""
        if not isinstance(data, bytes):
            if hasattr(data, "read"):
                data = data.read()
            else:
                data = b"".join(data)

        result = self._lib.lib.AGFS_Write(
            self._client_id, path.encode("utf-8"), data, ctypes.c_int64(len(data))
        )
        resp = self._parse_response(result)
        return resp.get("message", "OK")

    def create(self, path: str) -> Dict[str, Any]:
        """Create a new file."""
        result = self._lib.lib.AGFS_Create(self._client_id, path.encode("utf-8"))
        return self._parse_response(result)

    def mkdir(self, path: str, mode: str = "755") -> Dict[str, Any]:
        """Create a directory."""
        mode_int = int(mode, 8)
        result = self._lib.lib.AGFS_Mkdir(
            self._client_id, path.encode("utf-8"), ctypes.c_uint(mode_int)
        )
        return self._parse_response(result)

    def rm(self, path: str, recursive: bool = False) -> Dict[str, Any]:
        """Remove a file or directory."""
        result = self._lib.lib.AGFS_Rm(self._client_id, path.encode("utf-8"), 1 if recursive else 0)
        return self._parse_response(result)

    def stat(self, path: str) -> Dict[str, Any]:
        """Get file/directory information."""
        result = self._lib.lib.AGFS_Stat(self._client_id, path.encode("utf-8"))
        return self._parse_response(result)

    def mv(self, old_path: str, new_path: str) -> Dict[str, Any]:
        """Rename/move a file or directory."""
        result = self._lib.lib.AGFS_Mv(
            self._client_id, old_path.encode("utf-8"), new_path.encode("utf-8")
        )
        return self._parse_response(result)

    def chmod(self, path: str, mode: int) -> Dict[str, Any]:
        """Change file permissions."""
        result = self._lib.lib.AGFS_Chmod(
            self._client_id, path.encode("utf-8"), ctypes.c_uint(mode)
        )
        return self._parse_response(result)

    def touch(self, path: str) -> Dict[str, Any]:
        """Touch a file."""
        result = self._lib.lib.AGFS_Touch(self._client_id, path.encode("utf-8"))
        return self._parse_response(result)

    def mounts(self) -> List[Dict[str, Any]]:
        """List all mounted plugins."""
        result = self._lib.lib.AGFS_Mounts(self._client_id)
        data = self._parse_response(result)
        return data.get("mounts", [])

    def mount(self, fstype: str, path: str, config: Dict[str, Any]) -> Dict[str, Any]:
        """Mount a plugin dynamically."""
        config_json = json.dumps(config)
        result = self._lib.lib.AGFS_Mount(
            self._client_id,
            fstype.encode("utf-8"),
            path.encode("utf-8"),
            config_json.encode("utf-8"),
        )
        return self._parse_response(result)

    def unmount(self, path: str) -> Dict[str, Any]:
        """Unmount a plugin."""
        result = self._lib.lib.AGFS_Unmount(self._client_id, path.encode("utf-8"))
        return self._parse_response(result)

    def load_plugin(self, library_path: str) -> Dict[str, Any]:
        """Load an external plugin."""
        result = self._lib.lib.AGFS_LoadPlugin(self._client_id, library_path.encode("utf-8"))
        return self._parse_response(result)

    def unload_plugin(self, library_path: str) -> Dict[str, Any]:
        """Unload an external plugin."""
        result = self._lib.lib.AGFS_UnloadPlugin(self._client_id, library_path.encode("utf-8"))
        return self._parse_response(result)

    def list_plugins(self) -> List[str]:
        """List all loaded external plugins."""
        result = self._lib.lib.AGFS_ListPlugins(self._client_id)
        data = self._parse_response(result)
        return data.get("loaded_plugins", [])

    def get_plugins_info(self) -> List[dict]:
        """Get detailed information about all loaded plugins."""
        return self.list_plugins()

    def grep(
        self,
        path: str,
        pattern: str,
        recursive: bool = False,
        case_insensitive: bool = False,
        stream: bool = False,
    ):
        """Search for a pattern in files."""
        raise AGFSNotSupportedError("Grep not supported in binding mode")

    def digest(self, path: str, algorithm: str = "xxh3") -> Dict[str, Any]:
        """Calculate the digest of a file."""
        raise AGFSNotSupportedError("Digest not supported in binding mode")

    def open_handle(
        self, path: str, flags: int = 0, mode: int = 0o644, lease: int = 60
    ) -> "FileHandle":
        """Open a file handle for stateful operations."""
        handle_id = self._lib.lib.AGFS_OpenHandle(
            self._client_id, path.encode("utf-8"), flags, ctypes.c_uint(mode), lease
        )

        if handle_id < 0:
            raise AGFSClientError("Failed to open handle")

        return FileHandle(self, handle_id, path, flags)

    def list_handles(self) -> List[Dict[str, Any]]:
        """List all active file handles."""
        result = self._lib.lib.AGFS_ListHandles(self._client_id)
        data = self._parse_response(result)
        return data.get("handles", [])

    def get_handle_info(self, handle_id: int) -> Dict[str, Any]:
        """Get information about a specific handle."""
        result = self._lib.lib.AGFS_GetHandleInfo(ctypes.c_int64(handle_id))
        return self._parse_response(result)

    def close_handle(self, handle_id: int) -> Dict[str, Any]:
        """Close a file handle."""
        result = self._lib.lib.AGFS_CloseHandle(ctypes.c_int64(handle_id))
        return self._parse_response(result)

    def handle_read(self, handle_id: int, size: int = -1, offset: Optional[int] = None) -> bytes:
        """Read from a file handle."""
        has_offset = 1 if offset is not None else 0
        offset_val = offset if offset is not None else 0

        result = self._lib.lib.AGFS_HandleRead(
            ctypes.c_int64(handle_id), ctypes.c_int64(size), ctypes.c_int64(offset_val), has_offset
        )

        if isinstance(result, bytes):
            return result

        data = json.loads(result.decode("utf-8") if isinstance(result, bytes) else result)
        if "error_id" in data and data["error_id"] != 0:
            error_msg = self._lib.lib.AGFS_GetLastError(data["error_id"])
            if isinstance(error_msg, bytes):
                error_msg = error_msg.decode("utf-8")
            raise AGFSClientError(error_msg if error_msg else "Unknown error")

        return result if isinstance(result, bytes) else result.encode("utf-8")

    def handle_write(self, handle_id: int, data: bytes, offset: Optional[int] = None) -> int:
        """Write to a file handle."""
        has_offset = 1 if offset is not None else 0
        offset_val = offset if offset is not None else 0

        result = self._lib.lib.AGFS_HandleWrite(
            ctypes.c_int64(handle_id),
            data,
            ctypes.c_int64(len(data)),
            ctypes.c_int64(offset_val),
            has_offset,
        )
        resp = self._parse_response(result)
        return resp.get("bytes_written", 0)

    def handle_seek(self, handle_id: int, offset: int, whence: int = 0) -> int:
        """Seek within a file handle."""
        result = self._lib.lib.AGFS_HandleSeek(
            ctypes.c_int64(handle_id), ctypes.c_int64(offset), whence
        )
        data = self._parse_response(result)
        return data.get("position", 0)

    def handle_sync(self, handle_id: int) -> Dict[str, Any]:
        """Sync a file handle."""
        result = self._lib.lib.AGFS_HandleSync(ctypes.c_int64(handle_id))
        return self._parse_response(result)

    def handle_stat(self, handle_id: int) -> Dict[str, Any]:
        """Get file info via handle."""
        result = self._lib.lib.AGFS_HandleStat(ctypes.c_int64(handle_id))
        return self._parse_response(result)

    def renew_handle(self, handle_id: int, lease: int = 60) -> Dict[str, Any]:
        """Renew the lease on a file handle."""
        return {"message": "lease renewed", "lease": lease}


class FileHandle:
    """A file handle for stateful file operations.

    Supports context manager protocol for automatic cleanup.
    """

    O_RDONLY = 0
    O_WRONLY = 1
    O_RDWR = 2
    O_APPEND = 8
    O_CREATE = 16
    O_EXCL = 32
    O_TRUNC = 64

    SEEK_SET = 0
    SEEK_CUR = 1
    SEEK_END = 2

    def __init__(self, client: AGFSBindingClient, handle_id: int, path: str, flags: int):
        self._client = client
        self._handle_id = handle_id
        self._path = path
        self._flags = flags
        self._closed = False

    @property
    def handle_id(self) -> int:
        """The handle ID."""
        return self._handle_id

    @property
    def path(self) -> str:
        """The file path."""
        return self._path

    @property
    def flags(self) -> int:
        """The open flags (numeric)."""
        return self._flags

    @property
    def closed(self) -> bool:
        """Whether the handle is closed."""
        return self._closed

    def read(self, size: int = -1) -> bytes:
        """Read from current position."""
        if self._closed:
            raise AGFSClientError("Handle is closed")
        return self._client.handle_read(self._handle_id, size)

    def read_at(self, size: int, offset: int) -> bytes:
        """Read at specific offset (pread)."""
        if self._closed:
            raise AGFSClientError("Handle is closed")
        return self._client.handle_read(self._handle_id, size, offset)

    def write(self, data: bytes) -> int:
        """Write at current position."""
        if self._closed:
            raise AGFSClientError("Handle is closed")
        return self._client.handle_write(self._handle_id, data)

    def write_at(self, data: bytes, offset: int) -> int:
        """Write at specific offset (pwrite)."""
        if self._closed:
            raise AGFSClientError("Handle is closed")
        return self._client.handle_write(self._handle_id, data, offset)

    def seek(self, offset: int, whence: int = 0) -> int:
        """Seek to position."""
        if self._closed:
            raise AGFSClientError("Handle is closed")
        return self._client.handle_seek(self._handle_id, offset, whence)

    def tell(self) -> int:
        """Get current position."""
        return self.seek(0, self.SEEK_CUR)

    def sync(self) -> None:
        """Flush data to storage."""
        if self._closed:
            raise AGFSClientError("Handle is closed")
        self._client.handle_sync(self._handle_id)

    def stat(self) -> Dict[str, Any]:
        """Get file info."""
        if self._closed:
            raise AGFSClientError("Handle is closed")
        return self._client.handle_stat(self._handle_id)

    def info(self) -> Dict[str, Any]:
        """Get handle info."""
        if self._closed:
            raise AGFSClientError("Handle is closed")
        return self._client.get_handle_info(self._handle_id)

    def renew(self, lease: int = 60) -> Dict[str, Any]:
        """Renew the handle lease."""
        if self._closed:
            raise AGFSClientError("Handle is closed")
        return self._client.renew_handle(self._handle_id, lease)

    def close(self) -> None:
        """Close the handle."""
        if not self._closed:
            self._client.close_handle(self._handle_id)
            self._closed = True

    def __enter__(self) -> "FileHandle":
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.close()

    def __repr__(self) -> str:
        status = "closed" if self._closed else "open"
        return f"FileHandle(id={self._handle_id}, path={self._path}, flags={self._flags}, {status})"
