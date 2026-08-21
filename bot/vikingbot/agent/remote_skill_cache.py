"""Versioned, principal-scoped host cache for remote Skill snapshots."""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import time
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from loguru import logger

if TYPE_CHECKING:
    from vikingbot.config.schema import Config


_CACHE_FORMAT_VERSION = 1
_CACHE_METADATA = "metadata.json"
_CACHE_SNAPSHOT = "snapshot"


@dataclass(frozen=True)
class RemoteSkillCacheKey:
    """Opaque filesystem-safe identity for one authorized Skill revision."""

    scope_digest: str
    root_digest: str
    revision_digest: str


@dataclass(frozen=True)
class SnapshotBuildResult:
    """Validated size information returned by a cache snapshot builder."""

    total_bytes: int
    file_count: int


@dataclass(frozen=True)
class CachedSkillSnapshot:
    """One leased immutable host-side Skill snapshot."""

    key: RemoteSkillCacheKey
    path: Path
    hit: bool
    total_bytes: int
    file_count: int


@dataclass
class _KeyLockState:
    """One keyed lock plus the holders and waiters that still reference it."""

    lock: asyncio.Lock
    users: int = 0


SnapshotBuilder = Callable[[Path], Awaitable[SnapshotBuildResult]]


class RemoteSkillCacheError(RuntimeError):
    """The host cache is unavailable; callers may fall back to uncached materialization."""


class RemoteSkillSnapshotCache:
    """Retain validated Skill bytes across Agent Turns without exposing them to tools."""

    def __init__(self, config: Config):
        settings = config.remote_skills
        self.root = config.bot_data_path / "remote_skill_cache"
        self.idle_ttl_seconds = float(settings.cache_idle_ttl_seconds)
        self.max_entries = int(settings.cache_max_entries)
        self.max_bytes = int(settings.cache_max_bytes)
        self._key_locks: dict[RemoteSkillCacheKey, _KeyLockState] = {}
        self._state_lock = asyncio.Lock()
        self._prune_lock = asyncio.Lock()
        self._active_entries: dict[Path, int] = {}

    def entry_path(self, key: RemoteSkillCacheKey) -> Path:
        return self.root / key.scope_digest / key.root_digest / key.revision_digest

    @asynccontextmanager
    async def acquire(
        self,
        *,
        key: RemoteSkillCacheKey,
        root_uri: str,
        revision: str,
        manifest_signature: str,
        builder: SnapshotBuilder,
    ) -> AsyncIterator[CachedSkillSnapshot]:
        """Lease a validated cached snapshot, building it once on a cache miss."""
        async with self._key_lock(key):
            entry = self.entry_path(key)
            await self._lease(entry)
            used_successfully = False
            try:
                metadata = await asyncio.to_thread(
                    self._load_entry,
                    entry,
                    root_uri=root_uri,
                    revision=revision,
                    manifest_signature=manifest_signature,
                )
                hit = metadata is not None
                if metadata is None:
                    if entry.exists():
                        await asyncio.to_thread(shutil.rmtree, entry, True)
                    metadata = await self._build_entry(
                        entry=entry,
                        root_uri=root_uri,
                        revision=revision,
                        manifest_signature=manifest_signature,
                        builder=builder,
                    )

                yield CachedSkillSnapshot(
                    key=key,
                    path=entry / _CACHE_SNAPSHOT,
                    hit=hit,
                    total_bytes=int(metadata["total_bytes"]),
                    file_count=int(metadata["file_count"]),
                )
                used_successfully = True
            finally:
                await self._release(entry, touch=used_successfully)
                try:
                    await self.prune()
                except Exception as exc:
                    logger.warning("Failed to prune remote Skill cache {}: {}", self.root, exc)

    async def invalidate(self, key: RemoteSkillCacheKey) -> None:
        """Remove a corrupt or otherwise unusable cache entry."""
        async with self._key_lock(key):
            entry = self.entry_path(key)
            async with self._state_lock:
                active = self._active_entries.get(entry, 0)
            if active:
                return
            await asyncio.to_thread(shutil.rmtree, entry, True)

    @asynccontextmanager
    async def _key_lock(self, key: RemoteSkillCacheKey) -> AsyncIterator[None]:
        """Serialize one cache key and discard the lock after its last user exits."""
        async with self._state_lock:
            state = self._key_locks.get(key)
            if state is None:
                state = _KeyLockState(lock=asyncio.Lock())
                self._key_locks[key] = state
            state.users += 1
        try:
            async with state.lock:
                yield
        finally:
            async with self._state_lock:
                state.users -= 1
                if state.users == 0 and self._key_locks.get(key) is state:
                    self._key_locks.pop(key, None)

    async def prune(self) -> None:
        """Apply idle-TTL, entry-count, and byte-size eviction to inactive entries."""
        async with self._prune_lock:
            if not self.root.exists():
                return
            async with self._state_lock:
                active = {path for path, count in self._active_entries.items() if count > 0}
            now = time.time()
            entries = await asyncio.to_thread(self._scan_entries)

            retained: list[tuple[Path, int, float]] = []
            for entry, total_bytes, last_access in entries:
                if entry not in active and now - last_access >= self.idle_ttl_seconds:
                    await asyncio.to_thread(shutil.rmtree, entry, True)
                    continue
                retained.append((entry, total_bytes, last_access))

            total = sum(item[1] for item in retained)
            count = len(retained)
            for entry, total_bytes, _last_access in sorted(retained, key=lambda item: item[2]):
                if count <= self.max_entries and total <= self.max_bytes:
                    break
                if entry in active:
                    continue
                await asyncio.to_thread(shutil.rmtree, entry, True)
                count -= 1
                total -= total_bytes

            await asyncio.to_thread(self._remove_empty_parents)

    async def _build_entry(
        self,
        *,
        entry: Path,
        root_uri: str,
        revision: str,
        manifest_signature: str,
        builder: SnapshotBuilder,
    ) -> dict[str, Any]:
        staging = self.root / f".partial-{uuid.uuid4().hex}"
        snapshot = staging / _CACHE_SNAPSHOT
        try:
            await asyncio.to_thread(self._prepare_root)
            await asyncio.to_thread(snapshot.mkdir, parents=True, exist_ok=False, mode=0o700)
            result = await builder(snapshot)
            metadata = {
                "format_version": _CACHE_FORMAT_VERSION,
                "root_uri": root_uri,
                "revision": revision,
                "manifest_signature": manifest_signature,
                "total_bytes": int(result.total_bytes),
                "file_count": int(result.file_count),
                "created_at": time.time(),
            }
            await asyncio.to_thread(self._write_metadata, staging, metadata)
            await asyncio.to_thread(self._make_private, staging)
            await asyncio.to_thread(entry.parent.mkdir, parents=True, exist_ok=True, mode=0o700)
            if entry.exists():
                await asyncio.to_thread(shutil.rmtree, entry, True)
            await asyncio.to_thread(os.replace, staging, entry)
            return metadata
        except OSError as exc:
            await asyncio.to_thread(shutil.rmtree, staging, True)
            raise RemoteSkillCacheError(f"Remote Skill cache is unavailable: {exc}") from exc
        except Exception:
            await asyncio.to_thread(shutil.rmtree, staging, True)
            raise

    async def _lease(self, entry: Path) -> None:
        async with self._state_lock:
            self._active_entries[entry] = self._active_entries.get(entry, 0) + 1

    async def _release(self, entry: Path, *, touch: bool) -> None:
        metadata = entry / _CACHE_METADATA
        if touch and metadata.exists():
            try:
                await asyncio.to_thread(os.utime, metadata, None)
            except OSError:
                pass
        async with self._state_lock:
            remaining = self._active_entries.get(entry, 0) - 1
            if remaining > 0:
                self._active_entries[entry] = remaining
            else:
                self._active_entries.pop(entry, None)

    def _prepare_root(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        try:
            self.root.chmod(0o700)
        except OSError:
            pass

    @staticmethod
    def _write_metadata(staging: Path, metadata: dict[str, Any]) -> None:
        path = staging / _CACHE_METADATA
        path.write_text(
            json.dumps(metadata, sort_keys=True, separators=(",", ":")),
            encoding="utf-8",
        )
        path.chmod(0o600)

    @staticmethod
    def _make_private(root: Path) -> None:
        for current, directories, files in os.walk(root):
            Path(current).chmod(0o700)
            for directory in directories:
                (Path(current) / directory).chmod(0o700)
            for filename in files:
                (Path(current) / filename).chmod(0o600)

    def _load_entry(
        self,
        entry: Path,
        root_uri: str,
        revision: str,
        manifest_signature: str,
    ) -> dict[str, Any] | None:
        metadata_path = entry / _CACHE_METADATA
        snapshot_path = entry / _CACHE_SNAPSHOT
        try:
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            return None
        if not isinstance(metadata, dict) or not snapshot_path.is_dir():
            return None
        expected = {
            "format_version": _CACHE_FORMAT_VERSION,
            "root_uri": root_uri,
            "revision": revision,
            "manifest_signature": manifest_signature,
        }
        if any(metadata.get(key) != value for key, value in expected.items()):
            return None
        try:
            if int(metadata.get("total_bytes", -1)) < 0 or int(metadata.get("file_count", -1)) < 1:
                return None
            if time.time() - metadata_path.stat().st_mtime >= self.idle_ttl_seconds:
                return None
        except (OSError, TypeError, ValueError):
            return None
        return metadata

    def _scan_entries(self) -> list[tuple[Path, int, float]]:
        entries: list[tuple[Path, int, float]] = []
        for metadata_path in self.root.glob(f"*/*/*/{_CACHE_METADATA}"):
            entry = metadata_path.parent
            try:
                metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
                total_bytes = int(metadata.get("total_bytes", 0))
                last_access = metadata_path.stat().st_mtime
            except (OSError, ValueError, TypeError):
                shutil.rmtree(entry, ignore_errors=True)
                continue
            entries.append((entry, max(total_bytes, 0), last_access))
        for partial in self.root.glob(".partial-*"):
            try:
                if time.time() - partial.stat().st_mtime >= self.idle_ttl_seconds:
                    shutil.rmtree(partial, ignore_errors=True)
            except OSError:
                pass
        return entries

    def _remove_empty_parents(self) -> None:
        if not self.root.exists():
            return
        for depth in (3, 2, 1):
            pattern = "/".join("*" for _ in range(depth))
            for path in self.root.glob(pattern):
                if not path.is_dir() or path.name.startswith(".partial-"):
                    continue
                try:
                    path.rmdir()
                except OSError:
                    pass
        try:
            next(self.root.iterdir())
        except StopIteration:
            try:
                self.root.rmdir()
            except OSError:
                pass
        except OSError:
            logger.debug("Unable to inspect remote Skill cache root {}", self.root)
