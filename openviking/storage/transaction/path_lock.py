import asyncio
import time
from dataclasses import dataclass, field
from typing import Optional, Tuple

from openviking.pyagfs import AGFSClient
from openviking.storage.transaction.lock_handle import LockOwner
from openviking_cli.utils.logger import get_logger

logger = get_logger(__name__)

# Lock file name
LOCK_FILE_NAME = ".path.ovlock"

# Lock type constants
LOCK_TYPE_POINT = "P"
LOCK_TYPE_SUBTREE = "S"

# Default poll interval when waiting for a lock (seconds)
_POLL_INTERVAL = 0.2


@dataclass
class LockRefreshResult:
    refreshed_paths: list[str] = field(default_factory=list)
    lost_paths: list[str] = field(default_factory=list)
    failed_paths: list[str] = field(default_factory=list)


def _make_fencing_token(owner_id: str, lock_type: str = LOCK_TYPE_POINT) -> str:
    return f"{owner_id}:{time.time_ns()}:{lock_type}"


def _parse_fencing_token(token: str) -> Tuple[str, int, str]:
    if token.endswith(f":{LOCK_TYPE_POINT}") or token.endswith(f":{LOCK_TYPE_SUBTREE}"):
        lock_type = token[-1]
        rest = token[:-2]
        idx = rest.rfind(":")
        if idx >= 0:
            owner_id_part = rest[:idx]
            ts_part = rest[idx + 1 :]
            try:
                return owner_id_part, int(ts_part), lock_type
            except ValueError:
                pass
        return rest, 0, lock_type

    if ":" in token:
        idx = token.rfind(":")
        owner_id_part = token[:idx]
        ts_part = token[idx + 1 :]
        try:
            return owner_id_part, int(ts_part), LOCK_TYPE_POINT
        except ValueError:
            pass

    return token, 0, LOCK_TYPE_POINT


class PathLock:
    def __init__(self, agfs_client: AGFSClient, lock_expire: float = 300.0):
        self._agfs = agfs_client
        self._lock_expire = lock_expire

    def _get_lock_path(self, path: str) -> str:
        path = path.rstrip("/")
        return f"{path}/{LOCK_FILE_NAME}"

    def _ensure_directory_exists(self, path: str):
        """确保目录存在，不存在则创建"""
        try:
            # 检查路径是否存在
            self._agfs.stat(path)
        except Exception:
            # 路径不存在，尝试创建目录
            try:
                parent = self._get_parent_path(path)
                if parent:
                    # 递归创建父目录
                    self._ensure_directory_exists(parent)
                # 创建当前目录
                self._agfs.mkdir(path)
                logger.debug(f"Directory created: {path}")
            except Exception as e:
                logger.warning(f"Failed to create directory {path}: {e}")
                return False
        return True

    def _get_parent_path(self, path: str) -> Optional[str]:
        path = path.rstrip("/")
        if "/" not in path:
            return None
        parent = path.rsplit("/", 1)[0]
        return parent if parent else None

    def _read_token(self, lock_path: str) -> Optional[str]:
        try:
            content = self._agfs.read(lock_path)
            if isinstance(content, bytes):
                token = content.decode("utf-8").strip()
            else:
                token = str(content).strip()
            return token if token else None
        except Exception:
            return None

    def _read_owner_and_type(self, lock_path: str) -> Tuple[Optional[str], Optional[str]]:
        token = self._read_token(lock_path)
        if token is None:
            return None, None
        owner_id, _, lock_type = _parse_fencing_token(token)
        return owner_id, lock_type

    def is_lock_owned_by(self, lock_path: str, owner_id: str) -> bool:
        current_owner_id, _ = self._read_owner_and_type(lock_path)
        return current_owner_id == owner_id

    def collect_lost_owner_locks(self, owner: LockOwner) -> list[str]:
        lost_paths: list[str] = []
        for lock_path in list(owner.locks):
            if not self.is_lock_owned_by(lock_path, owner.id):
                lost_paths.append(lock_path)
        return lost_paths

    async def _is_locked_by_other(self, lock_path: str, owner_id: str) -> bool:
        token = self._read_token(lock_path)
        if token is None:
            return False
        lock_owner, _, _ = _parse_fencing_token(token)
        return lock_owner != owner_id

    async def _create_lock_file(
        self, lock_path: str, owner_id: str, lock_type: str = LOCK_TYPE_POINT
    ) -> None:
        token = _make_fencing_token(owner_id, lock_type)
        self._agfs.write(lock_path, token.encode("utf-8"))

    async def _owned_lock_type(self, path: str, owner: LockOwner) -> Optional[str]:
        lock_path = self._get_lock_path(path)
        if lock_path not in owner.locks:
            return None
        token = self._read_token(lock_path)
        if token is None:
            return None
        lock_owner, _, lock_type = _parse_fencing_token(token)
        if lock_owner != owner.id:
            return None
        return lock_type

    async def _has_owned_ancestor_subtree(self, path: str, owner: LockOwner) -> bool:
        current = path.rstrip("/")
        while current:
            if await self._owned_lock_type(current, owner) == LOCK_TYPE_SUBTREE:
                return True
            current = self._get_parent_path(current) or ""
        return False

    async def _remove_lock_file(self, lock_path: str) -> bool:
        try:
            self._agfs.rm(lock_path)
            return True
        except Exception as e:
            if "not found" in str(e).lower():
                return True
            return False

    def is_lock_stale(self, lock_path: str, expire_seconds: float = 300.0) -> bool:
        token = self._read_token(lock_path)
        if token is None:
            return True
        _, ts, _ = _parse_fencing_token(token)
        if ts == 0:
            return True
        age = (time.time_ns() - ts) / 1e9
        return age > expire_seconds

    async def _check_ancestors_for_subtree(self, path: str, exclude_owner_id: str) -> Optional[str]:
        parent = self._get_parent_path(path)
        while parent:
            lock_path = self._get_lock_path(parent)
            token = self._read_token(lock_path)
            if token is not None:
                owner_id, _, lock_type = _parse_fencing_token(token)
                if owner_id != exclude_owner_id and lock_type == LOCK_TYPE_SUBTREE:
                    return lock_path
            parent = self._get_parent_path(parent)
        return None

    async def _scan_descendants_for_locks(self, path: str, exclude_owner_id: str) -> Optional[str]:
        try:
            entries = self._agfs.ls(path)
            if not isinstance(entries, list):
                return None
            for entry in entries:
                if not isinstance(entry, dict):
                    continue
                name = entry.get("name", "")
                if not name or name in (".", ".."):
                    continue
                if not entry.get("isDir", False):
                    continue
                subdir = f"{path.rstrip('/')}/{name}"
                subdir_lock = self._get_lock_path(subdir)
                token = self._read_token(subdir_lock)
                if token is not None:
                    owner_id, _, _ = _parse_fencing_token(token)
                    if owner_id != exclude_owner_id:
                        return subdir_lock
                result = await self._scan_descendants_for_locks(subdir, exclude_owner_id)
                if result:
                    return result
        except Exception as e:
            logger.warning(f"Failed to scan descendants of {path}: {e}")
        return None

    async def acquire_point(
        self, path: str, owner: LockOwner, timeout: Optional[float] = 0.0
    ) -> bool:
        owner_id = owner.id
        lock_path = self._get_lock_path(path)
        owned_lock_type = await self._owned_lock_type(path, owner)
        if owned_lock_type in {LOCK_TYPE_POINT, LOCK_TYPE_SUBTREE}:
            owner.add_lock(lock_path)
            logger.debug(f"[POINT] Reusing owned lock on: {path}")
            return True
        if await self._has_owned_ancestor_subtree(path, owner):
            logger.debug(f"[POINT] Reusing owned ancestor SUBTREE lock on: {path}")
            return True
        if timeout is None:
            # 无限等待
            deadline = float("inf")
        else:
            # 有限超时
            deadline = asyncio.get_running_loop().time() + timeout

        # 确保目录存在
        if not self._ensure_directory_exists(path):
            logger.warning(f"[POINT] Failed to ensure directory exists: {path}")
            return False

        while True:
            if await self._is_locked_by_other(lock_path, owner_id):
                if self.is_lock_stale(lock_path, self._lock_expire):
                    logger.warning(f"[POINT] Removing stale lock: {lock_path}")
                    await self._remove_lock_file(lock_path)
                    continue
                if asyncio.get_running_loop().time() >= deadline:
                    logger.warning(f"[POINT] Timeout waiting for lock on: {path}")
                    return False
                await asyncio.sleep(_POLL_INTERVAL)
                continue

            ancestor_conflict = await self._check_ancestors_for_subtree(path, owner_id)
            if ancestor_conflict:
                if self.is_lock_stale(ancestor_conflict, self._lock_expire):
                    logger.warning(
                        f"[POINT] Removing stale ancestor SUBTREE lock: {ancestor_conflict}"
                    )
                    await self._remove_lock_file(ancestor_conflict)
                    continue
                if asyncio.get_running_loop().time() >= deadline:
                    logger.warning(
                        f"[POINT] Timeout waiting for ancestor SUBTREE lock: {ancestor_conflict}"
                    )
                    return False
                await asyncio.sleep(_POLL_INTERVAL)
                continue

            try:
                await self._create_lock_file(lock_path, owner_id, LOCK_TYPE_POINT)
            except Exception as e:
                logger.error(f"[POINT] Failed to create lock file: {e}")
                return False

            backed_off = False
            conflict_after = await self._check_ancestors_for_subtree(path, owner_id)
            if conflict_after:
                their_token = self._read_token(conflict_after)
                if their_token:
                    their_owner_id, their_ts, _ = _parse_fencing_token(their_token)
                    my_token = self._read_token(lock_path)
                    _, my_ts, _ = (
                        _parse_fencing_token(my_token) if my_token else ("", 0, LOCK_TYPE_POINT)
                    )
                    if (my_ts, owner_id) > (their_ts, their_owner_id):
                        logger.debug(f"[POINT] Backing off (livelock guard) on {path}")
                        await self._remove_lock_file(lock_path)
                        backed_off = True
                if asyncio.get_running_loop().time() >= deadline:
                    if not backed_off:
                        await self._remove_lock_file(lock_path)
                    return False
                await asyncio.sleep(_POLL_INTERVAL)
                continue

            if not self.is_lock_owned_by(lock_path, owner_id):
                logger.debug(f"[POINT] Lock ownership verification failed: {path}")
                if asyncio.get_running_loop().time() >= deadline:
                    return False
                await asyncio.sleep(_POLL_INTERVAL)
                continue

            owner.add_lock(lock_path)
            logger.debug(f"[POINT] Lock acquired: {lock_path}")
            return True

    async def acquire_subtree(
        self, path: str, owner: LockOwner, timeout: Optional[float] = 0.0
    ) -> bool:
        owner_id = owner.id
        lock_path = self._get_lock_path(path)
        owned_lock_type = await self._owned_lock_type(path, owner)
        if owned_lock_type == LOCK_TYPE_SUBTREE:
            owner.add_lock(lock_path)
            logger.debug(f"[SUBTREE] Reusing owned SUBTREE lock on: {path}")
            return True
        if await self._has_owned_ancestor_subtree(path, owner):
            logger.debug(f"[SUBTREE] Reusing owned ancestor SUBTREE lock on: {path}")
            return True
        if timeout is None:
            # 无限等待
            deadline = float("inf")
        else:
            # 有限超时
            deadline = asyncio.get_running_loop().time() + timeout

        # 确保目录存在
        if not self._ensure_directory_exists(path):
            logger.warning(f"[SUBTREE] Failed to ensure directory exists: {path}")
            return False

        while True:
            if await self._is_locked_by_other(lock_path, owner_id):
                if self.is_lock_stale(lock_path, self._lock_expire):
                    logger.warning(f"[SUBTREE] Removing stale lock: {lock_path}")
                    await self._remove_lock_file(lock_path)
                    continue
                if asyncio.get_running_loop().time() >= deadline:
                    logger.warning(f"[SUBTREE] Timeout waiting for lock on: {path}")
                    return False
                await asyncio.sleep(_POLL_INTERVAL)
                continue

            # Check ancestor paths for SUBTREE locks held by other owners
            ancestor_conflict = await self._check_ancestors_for_subtree(path, owner_id)
            if ancestor_conflict:
                if self.is_lock_stale(ancestor_conflict, self._lock_expire):
                    logger.warning(
                        f"[SUBTREE] Removing stale ancestor SUBTREE lock: {ancestor_conflict}"
                    )
                    await self._remove_lock_file(ancestor_conflict)
                    continue
                if asyncio.get_running_loop().time() >= deadline:
                    logger.warning(
                        f"[SUBTREE] Timeout waiting for ancestor SUBTREE lock: {ancestor_conflict}"
                    )
                    return False
                await asyncio.sleep(_POLL_INTERVAL)
                continue

            desc_conflict = await self._scan_descendants_for_locks(path, owner_id)
            if desc_conflict:
                if self.is_lock_stale(desc_conflict, self._lock_expire):
                    logger.warning(f"[SUBTREE] Removing stale descendant lock: {desc_conflict}")
                    await self._remove_lock_file(desc_conflict)
                    continue
                if asyncio.get_running_loop().time() >= deadline:
                    logger.warning(
                        f"[SUBTREE] Timeout waiting for descendant lock: {desc_conflict}"
                    )
                    return False
                await asyncio.sleep(_POLL_INTERVAL)
                continue

            try:
                await self._create_lock_file(lock_path, owner_id, LOCK_TYPE_SUBTREE)
            except Exception as e:
                logger.error(f"[SUBTREE] Failed to create lock file: {e}")
                return False

            backed_off = False
            conflict_after = await self._scan_descendants_for_locks(path, owner_id)
            if not conflict_after:
                conflict_after = await self._check_ancestors_for_subtree(path, owner_id)
            if conflict_after:
                their_token = self._read_token(conflict_after)
                if their_token:
                    their_owner_id, their_ts, _ = _parse_fencing_token(their_token)
                    my_token = self._read_token(lock_path)
                    _, my_ts, _ = (
                        _parse_fencing_token(my_token) if my_token else ("", 0, LOCK_TYPE_SUBTREE)
                    )
                    if (my_ts, owner_id) > (their_ts, their_owner_id):
                        logger.debug(f"[SUBTREE] Backing off (livelock guard) on {path}")
                        await self._remove_lock_file(lock_path)
                        backed_off = True
                if asyncio.get_running_loop().time() >= deadline:
                    if not backed_off:
                        await self._remove_lock_file(lock_path)
                    return False
                await asyncio.sleep(_POLL_INTERVAL)
                continue

            if not self.is_lock_owned_by(lock_path, owner_id):
                logger.debug(f"[SUBTREE] Lock ownership verification failed: {path}")
                if asyncio.get_running_loop().time() >= deadline:
                    return False
                await asyncio.sleep(_POLL_INTERVAL)
                continue

            owner.add_lock(lock_path)
            logger.debug(f"[SUBTREE] Lock acquired: {lock_path}")
            return True

    async def acquire_mv(
        self,
        src_path: str,
        dst_parent_path: str,
        owner: LockOwner,
        timeout: Optional[float] = 0.0,
        src_is_dir: bool = True,
    ) -> bool:
        """Acquire locks for a move operation.

        Args:
            src_path: Source path to lock.
            dst_parent_path: Parent directory of the destination to lock.
                Callers typically pass the destination's parent so that the
                lock covers sibling-level conflicts without requiring the
                target to exist yet.
            owner: Lock owner handle.
            timeout: Maximum seconds to wait for each lock.
            src_is_dir: Whether the source is a directory (SUBTREE lock)
                or a file (POINT lock on parent).
        """
        if src_is_dir:
            if not await self.acquire_subtree(src_path, owner, timeout=timeout):
                logger.warning(f"[MV] Failed to acquire SUBTREE lock on source: {src_path}")
                return False
            if not await self.acquire_subtree(dst_parent_path, owner, timeout=timeout):
                logger.warning(
                    f"[MV] Failed to acquire SUBTREE lock on destination parent: {dst_parent_path}"
                )
                await self.release(owner)
                return False
        else:
            src_parent = src_path.rsplit("/", 1)[0] if "/" in src_path else src_path
            if not await self.acquire_point(src_parent, owner, timeout=timeout):
                logger.warning(f"[MV] Failed to acquire POINT lock on source parent: {src_parent}")
                return False
            if not await self.acquire_point(dst_parent_path, owner, timeout=timeout):
                logger.warning(
                    f"[MV] Failed to acquire POINT lock on destination parent: {dst_parent_path}"
                )
                await self.release(owner)
                return False

        logger.debug(f"[MV] Locks acquired: {src_path} -> {dst_parent_path}")
        return True

    async def refresh(self, owner: LockOwner) -> LockRefreshResult:
        """Rewrite all lock file timestamps to prevent stale cleanup."""
        result = LockRefreshResult()
        for lock_path in list(owner.locks):
            parsed_owner_id, lock_type = self._read_owner_and_type(lock_path)
            if parsed_owner_id != owner.id or lock_type is None:
                result.lost_paths.append(lock_path)
                continue
            new_token = _make_fencing_token(owner.id, lock_type)
            try:
                self._agfs.write(lock_path, new_token.encode("utf-8"))
                result.refreshed_paths.append(lock_path)
            except Exception as e:
                logger.warning(f"Failed to refresh lock {lock_path}: {e}")
                result.failed_paths.append(lock_path)
        return result

    async def release(self, owner: LockOwner) -> None:
        lock_count = len(owner.locks)
        released_count = 0
        for lock_path in reversed(list(owner.locks)):
            if self.is_lock_owned_by(lock_path, owner.id):
                await self._remove_lock_file(lock_path)
                released_count += 1
            owner.remove_lock(lock_path)

        logger.debug(f"Released {released_count}/{lock_count} locks for owner {owner.id}")

    async def release_selected(self, owner: LockOwner, lock_paths: list[str]) -> None:
        for lock_path in reversed(lock_paths):
            if lock_path not in owner.locks:
                continue
            if self.is_lock_owned_by(lock_path, owner.id):
                await self._remove_lock_file(lock_path)
            owner.remove_lock(lock_path)
