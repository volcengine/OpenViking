"""Protocol types for synchronous AGFS-like clients."""

from __future__ import annotations

from collections.abc import Iterator
from typing import (
    Any,
    Awaitable,
    BinaryIO,
    Dict,
    Literal,
    Protocol,
    TypeAlias,
    overload,
    runtime_checkable,
)

AGFSByteStream = Iterator[bytes]
CacheScriptValue: TypeAlias = None | bool | int | bytes | list["CacheScriptValue"]


class CacheRuntimeHandleProtocol(Protocol):
    """Asynchronous cache Runtime handle returned by the native AGFS binding."""

    @property
    def instance(self) -> str:
        """Return the configured cache instance name."""

    @property
    def default_ttl_seconds(self) -> float | None:
        """Return the default TTL in seconds, or None when disabled."""

    def get(self, key: str) -> Awaitable[bytes | None]:
        """Read key and return its bytes value or None."""

    def set(
        self,
        key: str,
        value: bytes,
        *,
        ttl_seconds: float | None = None,
        if_not_exists: bool = False,
        if_exists: bool = False,
        keep_ttl: bool = False,
    ) -> Awaitable[bool]:
        """Store bytes at key and return whether the condition allowed the write."""

    def delete(self, keys: list[str]) -> Awaitable[int]:
        """Delete keys and return the number removed."""

    def unlink(self, keys: list[str]) -> Awaitable[int]:
        """Unlink keys and return the number removed."""

    def mget(self, keys: list[str]) -> Awaitable[list[bytes | None]]:
        """Read keys and preserve the input order."""

    def mset(self, entries: dict[str, bytes]) -> Awaitable[None]:
        """Store multiple byte values."""

    def incr(self, key: str) -> Awaitable[int]:
        """Increment key by one and return the resulting value."""

    def decr(self, key: str) -> Awaitable[int]:
        """Decrement key by one and return the resulting value."""

    def incr_by(self, key: str, delta: int = 1) -> Awaitable[int]:
        """Increment key by delta and return the resulting value."""

    def decr_by(self, key: str, delta: int = 1) -> Awaitable[int]:
        """Decrement key by delta and return the resulting value."""

    def expire(self, key: str, ttl_seconds: float) -> Awaitable[bool]:
        """Set key expiration and return whether the key exists."""

    def hget(self, key: str, field: str) -> Awaitable[bytes | None]:
        """Read a hash field and return bytes or None."""

    def hset(self, key: str, field: str, value: bytes) -> Awaitable[int]:
        """Store a hash field and return the number of newly added fields."""

    def sismember(self, key: str, member: bytes) -> Awaitable[bool]:
        """Return whether member belongs to the set at key."""

    def smembers(self, key: str) -> Awaitable[list[bytes]]:
        """Return all members in the set at key."""

    def scard(self, key: str) -> Awaitable[int]:
        """Return the cardinality of the set at key."""

    def zadd(self, key: str, score: float, member: str) -> Awaitable[int]:
        """Add or update a sorted-set member and return whether it was newly added."""

    def zscan_match(self, key: str, pattern: str, count: int) -> Awaitable[list[str]]:
        """Return sorted-set members matching pattern."""

    def zrangebyscore(
        self, key: str, min_score: float, max_score: float, limit: int
    ) -> Awaitable[list[str]]:
        """Return sorted-set members in the inclusive score range."""

    def zrem(self, key: str, members: list[str]) -> Awaitable[int]:
        """Remove members from a sorted set and return the number removed."""

    def lpush(self, key: str, values: list[bytes]) -> Awaitable[int]:
        """Push byte values onto the head of a list and return its length."""

    def rpush(self, key: str, values: list[bytes]) -> Awaitable[int]:
        """Push byte values onto the tail of a list and return its length."""

    def lpop(self, key: str, count: int | None = None) -> Awaitable[list[bytes]]:
        """Pop up to count values from the head of a list."""

    def rpop(self, key: str, count: int | None = None) -> Awaitable[list[bytes]]:
        """Pop up to count values from the tail of a list."""

    def llen(self, key: str) -> Awaitable[int]:
        """Return the length of the list at key."""

    def lrange(self, key: str, start: int, stop: int) -> Awaitable[list[bytes]]:
        """Return the inclusive list range from start through stop."""

    def lindex(self, key: str, index: int) -> Awaitable[bytes | None]:
        """Return one list element by index."""

    def lset(self, key: str, index: int, value: bytes) -> Awaitable[None]:
        """Replace one list element by index."""

    def ltrim(self, key: str, start: int, stop: int) -> Awaitable[None]:
        """Trim a list to the inclusive range from start through stop."""

    def lrem(self, key: str, count: int, value: bytes) -> Awaitable[int]:
        """Remove matching list elements and return the number removed."""

    def linsert(
        self, key: str, position: str, pivot: bytes, value: bytes
    ) -> Awaitable[int]:
        """Insert value before or after pivot and return the new list length."""

    def lmove(
        self,
        source: str,
        destination: str,
        source_direction: str,
        destination_direction: str,
    ) -> Awaitable[bytes | None]:
        """Move one list element and return the moved bytes or None."""

    def register_script(self, script_id: str, redis_lua: str) -> None:
        """Register a named Redis Lua script without performing I/O."""

    def execute_script(
        self, script_id: str, keys: list[str], args: list[bytes]
    ) -> Awaitable[CacheScriptValue]:
        """Execute one registered script and return its recursive Python result."""

    def ping(self) -> Awaitable[None]:
        """Check cache provider health."""

@runtime_checkable
class AGFSSyncClientProtocol(Protocol):
    """AGFS binding contract used by OpenViking."""

    def cache_runtime(self, instance: str = "default") -> CacheRuntimeHandleProtocol:
        """Return the native cache Runtime handle selected by instance."""

    def ls(
        self,
        path: str = "/",
        ctx: Dict[str, str] | None = None,
        *,
        offset: int = 0,
        limit: int | None = None,
        sort_by: str | None = None,
        sort_order: str = "asc",
    ) -> list[Dict[str, Any]]:
        """List a sorted directory range."""

    @overload
    def read(
        self,
        path: str,
        offset: int = 0,
        size: int = -1,
        stream: Literal[False] = False,
    ) -> bytes:
        """Read file content from AGFS."""

    @overload
    def read(
        self,
        path: str,
        offset: int = 0,
        size: int = -1,
        stream: Literal[True] = True,
    ) -> AGFSByteStream:
        """Read file content from AGFS."""

    @overload
    def cat(
        self,
        path: str,
        offset: int = 0,
        size: int = -1,
        stream: Literal[False] = False,
    ) -> bytes:
        """Read file content from AGFS."""

    @overload
    def cat(
        self,
        path: str,
        offset: int = 0,
        size: int = -1,
        stream: Literal[True] = True,
    ) -> AGFSByteStream:
        """Read file content or a byte stream from AGFS."""

    def write(
        self,
        path: str,
        data: bytes | Iterator[bytes] | BinaryIO,
        max_retries: int = 3,
    ) -> str:
        """Write file content to AGFS and return the backend result."""

    def mkdir(self, path: str, mode: str = "755") -> Dict[str, Any]:
        """Create a directory in AGFS."""

    def ensure_parent_dirs(self, path: str, mode: str = "755") -> Dict[str, Any]:
        """Ensure parent directories exist for the given AGFS path."""

    def rm(self, path: str, recursive: bool = False, force: bool = True) -> Dict[str, Any]:
        """Remove a file or directory from AGFS."""

    def stat(self, path: str) -> Dict[str, Any]:
        """Return AGFS metadata for the given path."""

    def mv(self, old_path: str, new_path: str) -> Dict[str, Any]:
        """Move or rename a path inside AGFS."""

    def copy_within_mount(self, src_path: str, dst_path: str) -> Dict[str, Any]:
        """Attempt a same-mount verbatim copy and report whether it was used."""

    def grep(self, **kwargs: Any) -> Dict[str, Any]:
        """Run a grep-like search through the AGFS backend."""

    def tree_directory(
        self,
        path: str,
        show_hidden: bool = False,
        node_limit: int | None = None,
        level_limit: int | None = None,
        ctx: Dict[str, str] | None = None,
        *,
        offset: int = 0,
        sort_by: str | None = None,
        sort_order: str = "asc",
        directories_only: bool = False,
    ) -> list[Dict[str, Any]]:
        """Return a sorted range from a directory tree."""

    def glob_directory(
        self,
        path: str,
        pattern: str,
        show_hidden: bool = False,
        page_size: int | None = None,
        level_limit: int | None = None,
        continuation_token: str | None = None,
    ) -> Dict[str, Any]:
        """Return one page of flat glob matches for the given AGFS directory."""

    def pathlock_handle_mode(self) -> Literal["lock_path", "logical_path"]:
        """Return the actual runtime provider's lease handle representation."""

    def pathlock_acquire_batch_async(
        self,
        ctx: Dict[str, str],
        requests: list[Dict[str, str]],
        timeout_secs: float = 0.0,
        owner_lease_ref: Dict[str, Any] | None = None,
    ) -> Awaitable[Dict[str, Any]]:
        """Acquire a PathLock batch without blocking a Python worker thread."""

    def leader_try_acquire_async(
        self, key: str, holder_id: str, ttl_secs: float
    ) -> Awaitable[str | None]:
        """Try to acquire leadership and return an opaque lease reference."""

    def leader_validate_async(self) -> Awaitable[None]:
        """Initialize and validate coordination leadership asynchronously."""

    def leader_renew_async(self, lease_ref: str, ttl_secs: float) -> Awaitable[bool]:
        """Renew leadership while its hidden ownership token still matches."""

    def leader_release_async(self, lease_ref: str) -> Awaitable[None]:
        """Release leadership while its hidden ownership token still matches."""

    def revision_allocate_async(self, key: str) -> Awaitable[int]:
        """Reserve a monotonically increasing revision."""

    def revision_publish_async(self, key: str, revision: int) -> Awaitable[int]:
        """Publish persisted work and return the latest revision."""

    def revision_latest_async(self, key: str) -> Awaitable[int]:
        """Return the latest published revision, or zero."""
