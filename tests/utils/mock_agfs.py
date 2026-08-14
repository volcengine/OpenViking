import json
import re
import shutil
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock

_ISO_TIMESTAMP = "%Y-%m-%dT%H:%M:%S.%fZ"


class MockLocalAGFS:
    """
    A mock implementation of the AGFS binding client that operates on a local
    directory. Useful for tests where running the real RAGFS binding is not
    feasible or desired.

    The mock mirrors the synchronous AGFS interface the production code
    dispatches through AsyncAGFSClient (see openviking/pyagfs/async_client.py).
    Keep the method surface in sync with AGFSSyncClientProtocol and the
    pathlock_* family; the contract tests in tests/utils/test_mock_agfs_contract.py
    enforce that.
    """

    def __init__(self, config=None, root_path=None):
        self.config = config
        self.root = Path(root_path) if root_path else Path("/tmp/viking_data")
        self.root.mkdir(parents=True, exist_ok=True)
        self._pathlocks_guard = threading.Lock()
        self._pathlocks = {}
        self._pathlock_leases = {}
        self._pathlock_handoffs = {}
        self._queue_guard = threading.Lock()
        self._queues = {}
        self._queue_processing = {}

    @staticmethod
    def _queue_operation(path):
        parts = str(path).strip("/").split("/")
        if len(parts) >= 3 and parts[-3] == "queue":
            return parts[-2], parts[-1]
        return None

    def _resolve(self, path):
        if str(path).startswith("viking://"):
            path = str(path).replace("viking://", "")
        if str(path).startswith("/"):
            path = str(path)[1:]
        return self.root / path

    def exists(self, path, ctx=None):
        return self._resolve(path).exists()

    def mkdir(self, path, mode="755", ctx=None, parents=True, exist_ok=True):
        self._resolve(path).mkdir(parents=parents, exist_ok=exist_ok)
        return {"path": path}

    def ensure_parent_dirs(self, path, mode="755", ctx=None):
        parent = self._resolve(path).parent
        parent.mkdir(parents=True, exist_ok=True)
        return {"path": str(parent)}

    def ls(self, path, ctx=None, **kwargs):
        p = self._resolve(path)
        if not p.exists():
            return []
        res = []
        for item in p.iterdir():
            res.append(
                {
                    "name": item.name,
                    "isDir": item.is_dir(),  # Note: JS style camelCase for some APIs
                    "type": "directory" if item.is_dir() else "file",
                    "size": item.stat().st_size if item.is_file() else 0,
                    "mtime": item.stat().st_mtime,
                    "uri": f"viking://{path}/{item.name}".replace("//", "/"),
                }
            )
        return res

    def glob_directory(
        self,
        path,
        pattern,
        show_hidden=False,
        page_size=None,
        level_limit=None,
        continuation_token=None,
        ctx=None,
    ):
        del ctx
        root = self._resolve(path)
        matches = []
        for item in sorted(root.glob(pattern)):
            relative = item.relative_to(root)
            if not show_hidden and any(part.startswith(".") for part in relative.parts):
                continue
            if level_limit is not None and len(relative.parts) > level_limit:
                continue
            matches.append(
                {
                    "path": f"{str(path).rstrip('/')}/{relative.as_posix()}",
                    "rel_path": relative.as_posix(),
                    "name": item.name,
                    "is_dir": item.is_dir(),
                }
            )

        start = int(continuation_token or 0)
        end = len(matches) if not page_size else start + page_size
        return {
            "entries": matches[start:end],
            "next_token": str(end) if end < len(matches) else None,
        }

    def tree_directory(
        self,
        path,
        show_hidden=False,
        node_limit=None,
        level_limit=None,
        ctx=None,
    ):
        del ctx
        root = self._resolve(path)
        if not root.exists():
            return []
        entries = []

        def _walk(current: Path, depth: int) -> None:
            if node_limit is not None and len(entries) >= node_limit:
                return
            if level_limit is not None and depth > level_limit:
                return
            for item in sorted(current.iterdir()):
                if node_limit is not None and len(entries) >= node_limit:
                    return
                if not show_hidden and item.name.startswith("."):
                    continue
                rel = item.relative_to(root)
                entries.append(
                    {
                        "path": f"{str(path).rstrip('/')}/{rel.as_posix()}",
                        "rel_path": rel.as_posix(),
                        "info": {
                            "name": item.name,
                            "size": item.stat().st_size if item.is_file() else 0,
                            "mode": item.stat().st_mode & 0o777,
                            "modTime": datetime.fromtimestamp(
                                item.stat().st_mtime, tz=timezone.utc
                            ).strftime(_ISO_TIMESTAMP),
                            "isDir": item.is_dir(),
                        },
                    }
                )
                if item.is_dir():
                    _walk(item, depth + 1)

        _walk(root, 0)
        return entries

    def writeto(self, path, content, ctx=None, **kwargs):
        queue_operation = self._queue_operation(path)
        if queue_operation:
            queue_name, operation = queue_operation
            raw = content.decode("utf-8") if isinstance(content, bytes) else str(content)
            with self._queue_guard:
                if operation == "enqueue":
                    try:
                        parsed = json.loads(raw)
                    except (ValueError, TypeError):
                        parsed = None
                    if isinstance(parsed, dict):
                        message = dict(parsed)
                        message.setdefault("id", str(uuid.uuid4()))
                        message_id = message["id"]
                    else:
                        message_id = str(uuid.uuid4())
                        message = {"id": message_id, "data": raw}
                    self._queues.setdefault(queue_name, []).append(message)
                    return message_id
                if operation == "ack":
                    self._queue_processing.setdefault(queue_name, {}).pop(raw, None)
                    return ""
                if operation == "clear":
                    self._queues[queue_name] = []
                    self._queue_processing[queue_name] = {}
                    return ""

        p = self._resolve(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(content, str):
            p.write_text(content, encoding="utf-8")
        else:
            p.write_bytes(content)
        return str(p)

    def write(self, path, content, ctx=None, **kwargs):
        return self.writeto(path, content, ctx, **kwargs)

    def write_file(self, path, content, ctx=None, **kwargs):
        return self.writeto(path, content, ctx, **kwargs)

    def read_file(self, path, ctx=None, **kwargs):
        queue_operation = self._queue_operation(path)
        if queue_operation:
            queue_name, operation = queue_operation
            with self._queue_guard:
                queue = self._queues.setdefault(queue_name, [])
                processing = self._queue_processing.setdefault(queue_name, {})
                if operation == "dequeue":
                    if not queue:
                        return b"{}"
                    message = queue.pop(0)
                    processing[message["id"]] = message
                    return json.dumps(message).encode("utf-8")
                if operation == "peek":
                    return json.dumps(queue[0] if queue else {}).encode("utf-8")
                if operation == "size":
                    return str(len(queue)).encode("utf-8")
                if operation == "messages":
                    return json.dumps(queue + list(processing.values())).encode("utf-8")

        p = self._resolve(path)
        if not p.exists():
            raise FileNotFoundError(path)
        return p.read_bytes()

    def read(self, path, ctx=None, **kwargs):
        return self.read_file(path, ctx, **kwargs)

    def cat(self, path, ctx=None, **kwargs):
        return self.read_file(path, ctx, **kwargs)

    def rm(self, path, recursive=False, force=True, ctx=None):
        p = self._resolve(path)
        if not p.exists():
            if force:
                return {"removed": path}
            raise FileNotFoundError(path)
        if p.is_dir():
            if recursive:
                shutil.rmtree(p)
            else:
                p.rmdir()
        else:
            p.unlink()
        return {"removed": path}

    def delete_temp(self, path, ctx=None):
        self.rm(path, recursive=True, ctx=ctx)

    def mv(self, src, dst, ctx=None):
        s = self._resolve(src)
        d = self._resolve(dst)
        d.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(s), str(d))
        return {"moved": dst}

    def copy_within_mount(self, src_path, dst_path, ctx=None):
        del ctx
        src = self._resolve(src_path)
        dst = self._resolve(dst_path)
        if not src.exists():
            raise FileNotFoundError(src_path)
        dst.parent.mkdir(parents=True, exist_ok=True)
        if src.is_dir():
            shutil.copytree(str(src), str(dst), dirs_exist_ok=True)
        else:
            shutil.copy2(str(src), str(dst))
        return {"performed": True}

    def grep(self, **kwargs):
        path = kwargs.get("path", "")
        pattern = kwargs.get("pattern", "")
        exclude_path = kwargs.get("exclude_path")
        node_limit = kwargs.get("node_limit")
        case_insensitive = kwargs.get("case_insensitive", False)
        root = self._resolve(path)
        if not root.exists():
            return {"matches": [], "count": 0, "match_count": 0, "files_scanned": 0}

        flags = re.IGNORECASE if case_insensitive else 0
        try:
            regex = re.compile(pattern, flags)
        except re.error:
            return {"matches": [], "count": 0, "match_count": 0, "files_scanned": 0}

        matches = []
        files_scanned = 0
        exclude_prefix = str(exclude_path) if exclude_path else None
        for item in sorted(root.rglob("*")):
            if not item.is_file():
                continue
            rel = item.relative_to(root)
            if exclude_prefix is not None and str(rel).startswith(exclude_prefix):
                continue
            files_scanned += 1
            try:
                text = item.read_text(encoding="utf-8", errors="replace")
            except Exception:
                continue
            for line_number, line in enumerate(text.splitlines(), start=1):
                if regex.search(line):
                    matches.append(
                        {
                            "file": str(rel),
                            "line": line_number,
                            "line_number": line_number,
                            "content": line,
                        }
                    )
                    if node_limit is not None and len(matches) >= node_limit:
                        break
            if node_limit is not None and len(matches) >= node_limit:
                break

        return {
            "matches": matches,
            "count": len(matches),
            "match_count": len(matches),
            "files_scanned": files_scanned,
        }

    def system_sync_status(self, path, ctx=None):
        return {"status": "synced", "path": path}

    def system_sync_retry(self, path, ctx=None):
        return {"status": "ok", "path": path}

    def stat(self, path, ctx=None):
        p = self._resolve(path)
        if not p.exists():
            raise FileNotFoundError(path)
        s = p.stat()
        return {
            "size": s.st_size,
            "mtime": s.st_mtime,
            "isDir": p.is_dir(),
            "is_dir": p.is_dir(),
        }

    def bind_request_context(self, ctx):
        return MagicMock(__enter__=lambda x: None, __exit__=lambda x, y, z: None)

    # ========== Pathlock family ==========

    def _lease_locks(self, path):
        with self._pathlocks_guard:
            return self._pathlocks.setdefault(path, threading.Lock())

    def _acquire_lock(self, path, timeout_secs):
        lock = self._lease_locks(path)
        acquired = lock.acquire(timeout=timeout_secs)
        if not acquired:
            raise TimeoutError(f"timed out acquiring test path lock: {path}")
        return lock

    def _acquire_paths(self, paths, timeout_secs):
        ordered = sorted(set(paths))
        locks = []
        try:
            for path in ordered:
                locks.append((path, self._acquire_lock(path, timeout_secs)))
        except BaseException:
            for _, lock in reversed(locks):
                lock.release()
            raise
        return locks

    def _make_owned_lease(self, locks, paths):
        lease_ref = str(uuid.uuid4())
        lease = {
            "lease_ref": lease_ref,
            "ownership_ref": str(uuid.uuid4()),
            "owner_id": "mock-local-agfs",
            "owned": True,
            "lock_paths": list(paths),
        }
        with self._pathlocks_guard:
            self._pathlock_leases[lease_ref] = dict(locks)
        return lease

    def pathlock_acquire_exact(self, ctx, path, timeout_secs=0.0, owner_lease_ref=None):
        del ctx, owner_lease_ref
        locks = self._acquire_paths([path], timeout_secs)
        return self._make_owned_lease(locks, [path])

    pathlock_acquire_tree = pathlock_acquire_exact

    def pathlock_acquire_exact_batch(self, ctx, paths, timeout_secs=0.0, owner_lease_ref=None):
        del ctx, owner_lease_ref
        ordered = sorted(set(paths))
        locks = self._acquire_paths(ordered, timeout_secs)
        return self._make_owned_lease(locks, ordered)

    pathlock_acquire_tree_batch = pathlock_acquire_exact_batch

    def pathlock_acquire_exact_tree_batch(
        self, ctx, exact_paths, tree_paths, timeout_secs=0.0, owner_lease_ref=None
    ):
        del ctx, owner_lease_ref
        ordered = sorted(set(exact_paths) | set(tree_paths))
        locks = self._acquire_paths(ordered, timeout_secs)
        return self._make_owned_lease(locks, ordered)

    def pathlock_acquire_batch(self, ctx, requests, timeout_secs=0.0, owner_lease_ref=None):
        del owner_lease_ref
        if not requests:
            raise ValueError("pathlock request batch must not be empty")
        paths = []
        for request in requests:
            path = request.get("path")
            if not path or not path.startswith("/"):
                raise ValueError("pathlock request.path must be an absolute path")
            if request.get("kind") not in {"exact", "tree"}:
                raise ValueError("pathlock request.kind must be 'exact' or 'tree'")
            paths.append(path)
        return self.pathlock_acquire_exact_batch(ctx, paths, timeout_secs)

    def pathlock_as_borrowed(self, ctx, owned_lease_ref):
        del ctx
        lease_ref = owned_lease_ref["lease_ref"]
        with self._pathlocks_guard:
            if lease_ref not in self._pathlock_leases:
                raise ValueError("cannot borrow an unknown lease")
        return {
            "lease_ref": lease_ref,
            "ownership_ref": owned_lease_ref.get("ownership_ref"),
            "owner_id": owned_lease_ref.get("owner_id"),
            "owned": False,
        }

    def pathlock_refresh(self, ctx, owned_lease_ref):
        del ctx
        lease_ref = owned_lease_ref["lease_ref"]
        with self._pathlocks_guard:
            if lease_ref not in self._pathlock_leases:
                return "lost"
        return "refreshed"

    def pathlock_release(self, ctx, owned_lease_ref):
        del ctx
        if isinstance(owned_lease_ref, str):
            raise TypeError("pathlock_release requires a lease ref dict, not a raw string")
        lease_ref = owned_lease_ref["lease_ref"]
        if not owned_lease_ref.get("owned", True):
            raise ValueError("cannot release a borrowed lease")
        with self._pathlocks_guard:
            locks = self._pathlock_leases.pop(lease_ref, None)
        if locks is None:
            raise ValueError("cannot release an unknown lease")
        if not isinstance(locks, dict):
            locks = {owned_lease_ref.get("lease_ref", ""): locks}
        for lock in reversed(list(locks.values())):
            if lock.locked():
                lock.release()

    def pathlock_release_selected(self, ctx, owned_lease_ref, lock_paths):
        del ctx
        lease_ref = owned_lease_ref["lease_ref"]
        if not owned_lease_ref.get("owned", True):
            raise ValueError("cannot release a borrowed lease")
        with self._pathlocks_guard:
            locks = self._pathlock_leases.get(lease_ref)
        if locks is None:
            raise ValueError("cannot release an unknown lease")
        if not isinstance(locks, dict):
            locks = {lease_ref: locks}
        selected = set(lock_paths)
        released = []
        for path in selected:
            lock = locks.pop(path, None)
            if lock is not None and lock.locked():
                lock.release()
                released.append(path)
        if locks:
            self._pathlock_leases[lease_ref] = locks
        else:
            with self._pathlocks_guard:
                self._pathlock_leases.pop(lease_ref, None)
        return released

    def pathlock_to_handoff(self, ctx, owned_lease_ref):
        del ctx
        lease_ref = owned_lease_ref["lease_ref"]
        with self._pathlocks_guard:
            if lease_ref not in self._pathlock_leases:
                raise ValueError("cannot hand off an unknown lease")
        return {
            "lease_ref": lease_ref,
            "owner_id": owned_lease_ref.get("owner_id") or "mock-local-agfs",
            "lock_paths": owned_lease_ref.get("lock_paths", []),
            "covered_paths": [
                {"path": path, "kind": "exact"} for path in owned_lease_ref.get("lock_paths", [])
            ],
        }

    def pathlock_handoff(self, ctx, owned_lease_ref):
        del ctx
        lease_ref = owned_lease_ref["lease_ref"]
        with self._pathlocks_guard:
            locks = self._pathlock_leases.pop(lease_ref, None)
            if locks is None:
                raise ValueError("cannot hand off an unknown lease")
            self._pathlock_handoffs[lease_ref] = {
                "locks": locks,
                "owner_id": owned_lease_ref.get("owner_id"),
                "lock_paths": list(owned_lease_ref.get("lock_paths", [])),
                "covered_paths": [
                    {"path": path, "kind": "exact"}
                    for path in owned_lease_ref.get("lock_paths", [])
                ],
            }

    def pathlock_adopt(self, ctx, handoff_ref):
        del ctx
        lease_ref = handoff_ref.get("lease_ref")
        lock_paths = handoff_ref.get("lock_paths", [])
        owner_id = handoff_ref.get("owner_id") or handoff_ref.get("handle_id")
        if not lease_ref or not owner_id or not lock_paths:
            raise ValueError("handoff requires lease_ref, owner_id, and lock_paths")
        paths = [lp["path"] if isinstance(lp, dict) else lp for lp in lock_paths]
        covered_paths = handoff_ref.get("covered_paths", [])
        with self._pathlocks_guard:
            pending = self._pathlock_handoffs.pop(lease_ref, None)
        if pending is None:
            raise ValueError("cannot adopt a lease that is not pending handoff")
        if (
            owner_id != pending["owner_id"]
            or paths != pending["lock_paths"]
            or covered_paths != pending["covered_paths"]
        ):
            with self._pathlocks_guard:
                self._pathlock_handoffs[lease_ref] = pending
            raise ValueError("handoff metadata does not match the pending lease")
        locks = pending["locks"]
        return self._make_owned_lease(list(locks.items()), paths)

    def pathlock_is_locked(self, ctx, path, ignore_stale=True):
        del ctx, ignore_stale
        with self._pathlocks_guard:
            lock = self._pathlocks.get(path)
            if lock is None:
                return False
            return lock.locked()

    def pathlock_observe(self, ctx):
        del ctx
        with self._pathlocks_guard:
            active = sum(1 for lock in self._pathlocks.values() if lock.locked())
        return {
            "active_locks": active,
            "waiting_locks": 0,
            "stale_locks_removed": 0,
            "conflicts": [],
        }
