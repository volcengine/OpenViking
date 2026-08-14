import json
import shutil
import threading
import uuid
from pathlib import Path
from unittest.mock import MagicMock


class MockLocalAGFS:
    """
    A mock implementation of the AGFS binding client that operates on a local
    directory. Useful for tests where running the real RAGFS binding is not
    feasible or desired.
    """

    def __init__(self, config=None, root_path=None):
        self.config = config
        self.root = Path(root_path) if root_path else Path("/tmp/viking_data")
        self.root.mkdir(parents=True, exist_ok=True)
        self._pathlocks_guard = threading.Lock()
        self._pathlocks = {}
        self._pathlock_leases = {}
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

    def mkdir(self, path, ctx=None, parents=True, exist_ok=True):
        self._resolve(path).mkdir(parents=parents, exist_ok=exist_ok)

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

    def writeto(self, path, content, ctx=None, **kwargs):
        queue_operation = self._queue_operation(path)
        if queue_operation:
            queue_name, operation = queue_operation
            raw = content.decode("utf-8") if isinstance(content, bytes) else str(content)
            with self._queue_guard:
                if operation == "enqueue":
                    message_id = str(uuid.uuid4())
                    self._queues.setdefault(queue_name, []).append({"id": message_id, "data": raw})
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

    def rm(self, path, recursive=False, ctx=None):
        p = self._resolve(path)
        if p.exists():
            if p.is_dir():
                if recursive:
                    shutil.rmtree(p)
                else:
                    p.rmdir()
            else:
                p.unlink()

    def delete_temp(self, path, ctx=None):
        self.rm(path, recursive=True, ctx=ctx)

    def mv(self, src, dst, ctx=None):
        s = self._resolve(src)
        d = self._resolve(dst)
        d.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(s), str(d))

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

    def pathlock_acquire_tree(
        self,
        ctx,
        path,
        timeout_secs=0.0,
        owner_lease_ref=None,
    ):
        del ctx, owner_lease_ref
        with self._pathlocks_guard:
            lock = self._pathlocks.setdefault(path, threading.Lock())

        acquired = lock.acquire(timeout=timeout_secs)
        if not acquired:
            raise TimeoutError(f"timed out acquiring test path lock: {path}")

        lease_ref = str(uuid.uuid4())
        lease = {
            "lease_ref": lease_ref,
            "ownership_ref": str(uuid.uuid4()),
            "owner_id": "mock-local-agfs",
            "owned": True,
        }
        with self._pathlocks_guard:
            self._pathlock_leases[lease_ref] = lock
        return lease

    pathlock_acquire_exact = pathlock_acquire_tree

    def pathlock_acquire_exact_batch(
        self,
        ctx,
        paths,
        timeout_secs=0.0,
        owner_lease_ref=None,
    ):
        del ctx, owner_lease_ref
        with self._pathlocks_guard:
            locks = [
                self._pathlocks.setdefault(path, threading.Lock()) for path in sorted(set(paths))
            ]

        acquired = []
        try:
            for lock in locks:
                if not lock.acquire(timeout=timeout_secs):
                    raise TimeoutError("timed out acquiring test path lock batch")
                acquired.append(lock)
        except BaseException:
            for lock in reversed(acquired):
                lock.release()
            raise

        lease_ref = str(uuid.uuid4())
        lease = {
            "lease_ref": lease_ref,
            "ownership_ref": str(uuid.uuid4()),
            "owner_id": "mock-local-agfs",
            "owned": True,
        }
        with self._pathlocks_guard:
            self._pathlock_leases[lease_ref] = locks
        return lease

    def pathlock_release(self, ctx, owned_lease_ref):
        del ctx
        lease_ref = owned_lease_ref["lease_ref"]
        with self._pathlocks_guard:
            locks = self._pathlock_leases.pop(lease_ref)
        if not isinstance(locks, list):
            locks = [locks]
        for lock in reversed(locks):
            lock.release()
