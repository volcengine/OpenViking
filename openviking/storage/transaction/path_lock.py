import asyncio
import hashlib
import re
import time
from dataclasses import dataclass, field
from typing import Optional, Tuple

from openviking.pyagfs import AGFSClient
from openviking.storage.transaction.lock_handle import LockOwner
from openviking_cli.utils.logger import get_logger

logger = get_logger(__name__)

# Lock file name
LOCK_FILE_NAME = ".path.ovlock"
EXACT_LOCK_FILE_PREFIX = ".exact.ovlock."

# Lock type constants
LOCK_TYPE_EXACT = "E"
LOCK_TYPE_TREE = "T"
# Upgrade compatibility: old POINT/SUBTREE tokens are still treated as tree locks.
_READ_ONLY_TREE_LOCK_TYPES = {"P", "S"}

# Default poll interval when waiting for a lock (seconds)
_POLL_INTERVAL = 0.2


@dataclass
class LockRefreshResult:
    refreshed_paths: list[str] = field(default_factory=list)
    lost_paths: list[str] = field(default_factory=list)
    failed_paths: list[str] = field(default_factory=list)


def _make_fencing_token(owner_id: str, lock_type: str = LOCK_TYPE_EXACT) -> str:
    return f"{owner_id}:{time.time_ns()}:{lock_type}"


def _parse_fencing_token(token: str) -> Tuple[str, int, str]:
    known_types = {LOCK_TYPE_EXACT, LOCK_TYPE_TREE, *_READ_ONLY_TREE_LOCK_TYPES}
    if len(token) >= 2 and token[-2] == ":" and token[-1] in known_types:
        lock_type = LOCK_TYPE_TREE if token[-1] in _READ_ONLY_TREE_LOCK_TYPES else token[-1]
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

    return token, 0, LOCK_TYPE_EXACT


class PathLockEngine:
    def __init__(self, agfs_client: AGFSClient, lock_expire: float = 300.0):
        self._agfs = agfs_client
        self._lock_expire = lock_expire

    def _get_lock_path(self, path: str) -> str:
        path = path.rstrip("/") or "/"
        if path == "/":
            return f"/{LOCK_FILE_NAME}"
        return f"{path}/{LOCK_FILE_NAME}"

    def _is_existing_directory(self, path: str) -> bool:
        try:
            stat = self._agfs.stat(path.rstrip("/") or "/")
        except Exception:
            return False
        if isinstance(stat, dict):
            return stat.get("isDir") is True
        return getattr(stat, "isDir", None) is True

    def _get_prefixed_exact_lock_path(self, path: str) -> str:
        path = path.rstrip("/") or "/"
        parent = self._get_parent_path(path)
        if not parent:
            return self._get_lock_path(path)
        name = path.rsplit("/", 1)[-1]
        safe_name = re.sub(r"[^A-Za-z0-9._-]+", "_", name).strip("._") or "path"
        safe_name = safe_name[:80]
        digest = hashlib.sha1(path.encode("utf-8")).hexdigest()[:16]
        return f"{parent}/{EXACT_LOCK_FILE_PREFIX}{safe_name}.{digest}"

    def _get_exact_lock_path(self, path: str) -> str:
        """Return the primary lock-file path for an exact path lock."""
        if self._is_existing_directory(path):
            return self._get_lock_path(path)
        return self._get_prefixed_exact_lock_path(path)

    def _get_exact_lock_paths(self, path: str) -> list[str]:
        """Return all lock-file paths that can protect this exact path."""
        primary = self._get_exact_lock_path(path)
        prefixed = self._get_prefixed_exact_lock_path(path)
        if primary == prefixed:
            return [primary]
        return [primary, prefixed]

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

    def is_locked(self, path: str, ignore_stale: bool = True) -> bool:
        """Check whether *path* is currently locked.

        Detection rules (aligned with conflict checks in the acquire flow):
        - The path itself has a valid .path.ovlock; or
        - The path has a valid exact-path lock in the parent directory; or
        - Any ancestor directory holds a TREE lock.

        Args:
            path: Path to check (already converted to AGFS internal path).
            ignore_stale: Whether to ignore expired (stale) locks. Defaults
                to True to stay consistent with the acquire flow: stale
                locks will be cleaned up by the next acquirer, so they are
                not considered as held here.
        """
        # 1. Lock on the path itself
        own_lock_path = self._get_lock_path(path)
        token = self._read_token(own_lock_path)
        if token is not None:
            if not (ignore_stale and self.is_lock_stale(own_lock_path, self._lock_expire)):
                return True

        # 2. ExactPathLock for file, directory name, or not-yet-created paths
        for exact_lock_path in self._get_exact_lock_paths(path):
            if exact_lock_path == own_lock_path:
                continue
            exact_token = self._read_token(exact_lock_path)
            if exact_token is not None:
                if not (ignore_stale and self.is_lock_stale(exact_lock_path, self._lock_expire)):
                    return True

        # 3. Ancestor TREE locks
        parent = self._get_parent_path(path)
        while parent:
            ancestor_lock = self._get_lock_path(parent)
            ancestor_token = self._read_token(ancestor_lock)
            if ancestor_token is not None:
                _, _, lock_type = _parse_fencing_token(ancestor_token)
                if lock_type == LOCK_TYPE_TREE and not (
                    ignore_stale and self.is_lock_stale(ancestor_lock, self._lock_expire)
                ):
                    return True
            parent = self._get_parent_path(parent)

        return False

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
        self, lock_path: str, owner_id: str, lock_type: str = LOCK_TYPE_EXACT
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

    async def _has_owned_ancestor_tree(self, path: str, owner: LockOwner) -> bool:
        current = path.rstrip("/")
        while current:
            if await self._owned_lock_type(current, owner) == LOCK_TYPE_TREE:
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

    async def _check_ancestors_for_tree(self, path: str, exclude_owner_id: str) -> Optional[str]:
        parent = self._get_parent_path(path)
        while parent:
            lock_path = self._get_lock_path(parent)
            token = self._read_token(lock_path)
            if token is not None:
                owner_id, _, lock_type = _parse_fencing_token(token)
                if owner_id != exclude_owner_id and lock_type == LOCK_TYPE_TREE:
                    return lock_path
            parent = self._get_parent_path(parent)
        return None

    async def _check_path_lock(self, path: str, exclude_owner_id: str) -> Optional[str]:
        lock_path = self._get_lock_path(path)
        token = self._read_token(lock_path)
        if token is None:
            return None
        owner_id, _, _ = _parse_fencing_token(token)
        if owner_id != exclude_owner_id:
            return lock_path
        return None

    async def _check_exact_path_lock(self, path: str, exclude_owner_id: str) -> Optional[str]:
        for lock_path in self._get_exact_lock_paths(path):
            token = self._read_token(lock_path)
            if token is None:
                continue
            owner_id, _, _ = _parse_fencing_token(token)
            if owner_id != exclude_owner_id:
                return lock_path
        return None

    async def _scan_descendants_for_locks(self, path: str, exclude_owner_id: str) -> Optional[str]:
        try:
            try:
                self._agfs.stat(path)
            except Exception:
                return None
            entries = self._agfs.ls(path)
            if not isinstance(entries, list):
                return None
            for entry in entries:
                if not isinstance(entry, dict):
                    continue
                name = entry.get("name", "")
                if not name or name in (".", ".."):
                    continue
                entry_path = f"{path.rstrip('/')}/{name}"
                if name.startswith(EXACT_LOCK_FILE_PREFIX):
                    token = self._read_token(entry_path)
                    if token is not None:
                        owner_id, _, _ = _parse_fencing_token(token)
                        if owner_id != exclude_owner_id:
                            return entry_path
                    continue
                if not entry.get("isDir", False):
                    continue
                subdir = entry_path
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

    async def acquire_exact_path(
        self, path: str, owner: LockOwner, timeout: Optional[float] = 0.0
    ) -> bool:
        """Acquire a short lock for one exact path.

        It conflicts with:
        - another ExactPathLock on the same path;
        - any ancestor TREE lock;
        - a TREE lock on the exact same path.

        It does not conflict with sibling exact paths.
        """
        owner_id = owner.id
        lock_path = self._get_exact_lock_path(path)
        if lock_path in owner.locks and self.is_lock_owned_by(lock_path, owner_id):
            owner.add_lock(lock_path)
            logger.debug(f"[EXACT] Reusing owned exact lock on: {path}")
            return True
        if await self._has_owned_ancestor_tree(path, owner):
            logger.debug(f"[EXACT] Reusing owned ancestor TREE lock on: {path}")
            return True
        if timeout is None:
            deadline = float("inf")
        else:
            deadline = asyncio.get_running_loop().time() + timeout

        while True:
            existing_exact_lock = await self._check_exact_path_lock(path, owner_id)
            if existing_exact_lock:
                if self.is_lock_stale(existing_exact_lock, self._lock_expire):
                    logger.warning(f"[EXACT] Removing stale exact lock: {existing_exact_lock}")
                    await self._remove_lock_file(existing_exact_lock)
                    continue
                if asyncio.get_running_loop().time() >= deadline:
                    logger.warning(f"[EXACT] Timeout waiting for exact lock on: {path}")
                    return False
                await asyncio.sleep(_POLL_INTERVAL)
                continue

            same_path_lock = self._get_lock_path(path)
            if same_path_lock != lock_path:
                token = self._read_token(same_path_lock)
                if token is not None:
                    lock_owner, _, _ = _parse_fencing_token(token)
                    if lock_owner != owner_id:
                        if self.is_lock_stale(same_path_lock, self._lock_expire):
                            logger.warning(f"[EXACT] Removing stale lock: {same_path_lock}")
                            await self._remove_lock_file(same_path_lock)
                            continue
                        if asyncio.get_running_loop().time() >= deadline:
                            logger.warning(f"[EXACT] Timeout waiting for lock: {path}")
                            return False
                        await asyncio.sleep(_POLL_INTERVAL)
                        continue

            ancestor_conflict = await self._check_ancestors_for_tree(path, owner_id)
            if ancestor_conflict:
                if self.is_lock_stale(ancestor_conflict, self._lock_expire):
                    logger.warning(
                        f"[EXACT] Removing stale ancestor TREE lock: {ancestor_conflict}"
                    )
                    await self._remove_lock_file(ancestor_conflict)
                    continue
                if asyncio.get_running_loop().time() >= deadline:
                    logger.warning(
                        f"[EXACT] Timeout waiting for ancestor TREE lock: {ancestor_conflict}"
                    )
                    return False
                await asyncio.sleep(_POLL_INTERVAL)
                continue

            parent = self._get_parent_path(path)
            if (
                lock_path != self._get_lock_path(path)
                and parent
                and not self._ensure_directory_exists(parent)
            ):
                logger.warning(f"[EXACT] Failed to ensure parent directory exists: {parent}")
                return False

            try:
                await self._create_lock_file(lock_path, owner_id, LOCK_TYPE_EXACT)
            except Exception as e:
                logger.error(f"[EXACT] Failed to create lock file: {e}")
                return False

            if not self.is_lock_owned_by(lock_path, owner_id):
                logger.debug(f"[EXACT] Lost lock write race on: {path}")
                if asyncio.get_running_loop().time() >= deadline:
                    return False
                await asyncio.sleep(_POLL_INTERVAL)
                continue

            conflict_after = await self._check_path_lock(path, owner_id)
            if conflict_after == lock_path:
                conflict_after = None
            if not conflict_after:
                conflict_after = await self._check_ancestors_for_tree(path, owner_id)
            if conflict_after:
                their_token = self._read_token(conflict_after)
                if their_token:
                    their_owner_id, their_ts, _ = _parse_fencing_token(their_token)
                    my_token = self._read_token(lock_path)
                    _, my_ts, _ = (
                        _parse_fencing_token(my_token) if my_token else ("", 0, LOCK_TYPE_EXACT)
                    )
                    if (my_ts, owner_id) > (their_ts, their_owner_id):
                        logger.debug(f"[EXACT] Backing off (livelock guard) on {path}")
                        if self.is_lock_owned_by(lock_path, owner_id):
                            await self._remove_lock_file(lock_path)
                if asyncio.get_running_loop().time() >= deadline:
                    if self.is_lock_owned_by(lock_path, owner_id):
                        await self._remove_lock_file(lock_path)
                    return False
                await asyncio.sleep(_POLL_INTERVAL)
                continue

            if not self.is_lock_owned_by(lock_path, owner_id):
                logger.debug(f"[EXACT] Lock ownership verification failed: {path}")
                if asyncio.get_running_loop().time() >= deadline:
                    return False
                await asyncio.sleep(_POLL_INTERVAL)
                continue

            owner.add_lock(lock_path)
            logger.debug(f"[EXACT] Lock acquired: {lock_path}")
            return True

    async def acquire_tree(
        self, path: str, owner: LockOwner, timeout: Optional[float] = 0.0
    ) -> bool:
        owner_id = owner.id
        lock_path = self._get_lock_path(path)
        owned_lock_type = await self._owned_lock_type(path, owner)
        if owned_lock_type == LOCK_TYPE_TREE:
            owner.add_lock(lock_path)
            logger.debug(f"[TREE] Reusing owned TREE lock on: {path}")
            return True
        if await self._has_owned_ancestor_tree(path, owner):
            logger.debug(f"[TREE] Reusing owned ancestor TREE lock on: {path}")
            return True
        if timeout is None:
            # 无限等待
            deadline = float("inf")
        else:
            # 有限超时
            deadline = asyncio.get_running_loop().time() + timeout

        while True:
            if await self._is_locked_by_other(lock_path, owner_id):
                if self.is_lock_stale(lock_path, self._lock_expire):
                    logger.warning(f"[TREE] Removing stale lock: {lock_path}")
                    await self._remove_lock_file(lock_path)
                    continue
                if asyncio.get_running_loop().time() >= deadline:
                    logger.warning(f"[TREE] Timeout waiting for lock on: {path}")
                    return False
                await asyncio.sleep(_POLL_INTERVAL)
                continue

            # Check ancestor paths for TREE locks held by other owners
            ancestor_conflict = await self._check_ancestors_for_tree(path, owner_id)
            if ancestor_conflict:
                if self.is_lock_stale(ancestor_conflict, self._lock_expire):
                    logger.warning(f"[TREE] Removing stale ancestor TREE lock: {ancestor_conflict}")
                    await self._remove_lock_file(ancestor_conflict)
                    continue
                if asyncio.get_running_loop().time() >= deadline:
                    logger.warning(
                        f"[TREE] Timeout waiting for ancestor TREE lock: {ancestor_conflict}"
                    )
                    return False
                await asyncio.sleep(_POLL_INTERVAL)
                continue

            exact_conflict = await self._check_exact_path_lock(path, owner_id)
            if exact_conflict:
                if self.is_lock_stale(exact_conflict, self._lock_expire):
                    logger.warning(f"[TREE] Removing stale exact lock: {exact_conflict}")
                    await self._remove_lock_file(exact_conflict)
                    continue
                if asyncio.get_running_loop().time() >= deadline:
                    logger.warning(f"[TREE] Timeout waiting for exact lock: {exact_conflict}")
                    return False
                await asyncio.sleep(_POLL_INTERVAL)
                continue

            desc_conflict = await self._scan_descendants_for_locks(path, owner_id)
            if desc_conflict:
                if self.is_lock_stale(desc_conflict, self._lock_expire):
                    logger.warning(f"[TREE] Removing stale descendant lock: {desc_conflict}")
                    await self._remove_lock_file(desc_conflict)
                    continue
                if asyncio.get_running_loop().time() >= deadline:
                    logger.warning(f"[TREE] Timeout waiting for descendant lock: {desc_conflict}")
                    return False
                await asyncio.sleep(_POLL_INTERVAL)
                continue

            if not self._ensure_directory_exists(path):
                logger.warning(f"[TREE] Failed to ensure directory exists: {path}")
                return False

            try:
                await self._create_lock_file(lock_path, owner_id, LOCK_TYPE_TREE)
            except Exception as e:
                logger.error(f"[TREE] Failed to create lock file: {e}")
                return False

            backed_off = False
            conflict_after = await self._scan_descendants_for_locks(path, owner_id)
            if not conflict_after:
                conflict_after = await self._check_exact_path_lock(path, owner_id)
            if not conflict_after:
                conflict_after = await self._check_ancestors_for_tree(path, owner_id)
            if conflict_after:
                their_token = self._read_token(conflict_after)
                if their_token:
                    their_owner_id, their_ts, _ = _parse_fencing_token(their_token)
                    my_token = self._read_token(lock_path)
                    _, my_ts, _ = (
                        _parse_fencing_token(my_token) if my_token else ("", 0, LOCK_TYPE_TREE)
                    )
                    if (my_ts, owner_id) > (their_ts, their_owner_id):
                        logger.debug(f"[TREE] Backing off (livelock guard) on {path}")
                        await self._remove_lock_file(lock_path)
                        backed_off = True
                if asyncio.get_running_loop().time() >= deadline:
                    if not backed_off:
                        await self._remove_lock_file(lock_path)
                    return False
                await asyncio.sleep(_POLL_INTERVAL)
                continue

            if not self.is_lock_owned_by(lock_path, owner_id):
                logger.debug(f"[TREE] Lock ownership verification failed: {path}")
                if asyncio.get_running_loop().time() >= deadline:
                    return False
                await asyncio.sleep(_POLL_INTERVAL)
                continue

            owner.add_lock(lock_path)
            logger.debug(f"[TREE] Lock acquired: {lock_path}")
            return True

    async def acquire_mv(
        self,
        src_path: str,
        dst_path: str,
        owner: LockOwner,
        timeout: Optional[float] = 0.0,
        src_is_dir: bool = True,
    ) -> bool:
        """Acquire locks for a move operation.

        Args:
            src_path: Source path to lock.
            dst_path: Destination path to lock.
            owner: Lock owner handle.
            timeout: Maximum seconds to wait for each lock.
            src_is_dir: Whether the source is a directory (TREE lock)
                or a file (ExactPathLock on source and destination path).
        """
        if src_is_dir:
            if not await self.acquire_tree(src_path, owner, timeout=timeout):
                logger.warning(f"[MV] Failed to acquire TREE lock on source: {src_path}")
                return False
            if not await self.acquire_exact_path(dst_path, owner, timeout=timeout):
                logger.warning(f"[MV] Failed to acquire exact lock on destination: {dst_path}")
                await self.release(owner)
                return False
        else:
            if not await self.acquire_exact_path(src_path, owner, timeout=timeout):
                logger.warning(f"[MV] Failed to acquire exact lock on source: {src_path}")
                return False
            if not await self.acquire_exact_path(dst_path, owner, timeout=timeout):
                logger.warning(f"[MV] Failed to acquire exact lock on destination: {dst_path}")
                await self.release(owner)
                return False

        logger.debug(f"[MV] Locks acquired: {src_path} -> {dst_path}")
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
