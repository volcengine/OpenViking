"""Pending-session markers, run locks and recovery of dead runs' sessions."""

from __future__ import annotations

import errno
import json
from contextlib import suppress
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import quote, unquote

from .connection import _CommitScope
from .host import atomic_json_write
from .log import get_logger

logger = get_logger()


try:
    import fcntl
except ImportError:  # pragma: no cover - Windows
    fcntl = None


_PENDING_SESSIONS_RELATIVE_DIR = Path("openviking") / "pending_sessions"
_RUN_LOCKS_RELATIVE_DIR = Path("openviking") / "runs"
_LEGACY_RECOVERY_LOCK_FILENAME = "legacy-recovery.lock"
_LOCK_BUSY_ERRNOS = {errno.EWOULDBLOCK, errno.EACCES, errno.EAGAIN}


class StateStoreMixin:
    """Methods of ``OpenVikingMemoryProvider`` moved here unchanged; mixed into that class."""

    def _state_path(self, kind: str, name: str, *, scope: Optional[_CommitScope] = None) -> Optional[Path]:
        """Marker/lock file under HERMES_HOME: ``pending`` -> pending_sessions/<sid>.<generation>.json,
        ``lock`` -> runs/<run_id>.lock; an empty run id maps to the legacy recovery lock."""
        name = str(name or "").strip()
        if not self._hermes_home or (not name and kind != "lock"):
            return None
        if kind == "pending":
            scope = scope or self._capture_commit_scope()
            return Path(self._hermes_home) / _PENDING_SESSIONS_RELATIVE_DIR / f"{quote(name, safe='')}.{scope.marker_id}.json"
        return Path(self._hermes_home) / _RUN_LOCKS_RELATIVE_DIR / (f"{quote(name, safe='')}.lock" if name else _LEGACY_RECOVERY_LOCK_FILENAME)

    @staticmethod
    def _flock_open(path: Path):
        """Open ``path`` and take a non-blocking exclusive flock; returns the file (closed again on failure)."""
        path.parent.mkdir(parents=True, exist_ok=True)
        lock_file = path.open("a+", encoding="utf-8")
        try:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BaseException:
            lock_file.close()
            raise
        return lock_file

    @staticmethod
    def _flock_close(lock_file, path: Optional[Path], label: str) -> None:
        steps = []
        if lock_file is not None:
            if fcntl is not None:
                steps.append(("unlock", lambda: fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)))
            steps.append(("close", lock_file.close))
        if path is not None:
            steps.append(("remove", lambda: path.unlink(missing_ok=True)))
        for verb, step in steps:
            try:
                step()
            except Exception as e:
                logger.debug("Could not %s OpenViking %s %s: %s", verb, label, path, e)

    def _acquire_run_lock(self) -> None:
        path = None if self._run_lock_path is not None else self._state_path("lock", self._run_id)
        if path is None:
            return
        if fcntl is None:
            logger.debug("OpenViking run locks are not supported on this platform")
            return
        try:
            self._run_lock_file = self._flock_open(path)
            self._run_lock_path = path
        except Exception as e:
            with suppress(Exception):
                path.unlink(missing_ok=True)
            logger.debug("Could not acquire OpenViking run lock %s: %s", path, e)

    def _release_run_lock(self) -> None:
        lock_file, path = self._run_lock_file, self._run_lock_path
        self._run_lock_file = self._run_lock_path = None
        self._flock_close(lock_file, path, "run lock")

    def _claim_owner_run_for_recovery(self, owner_run_id: str) -> tuple[bool, Optional[Any]]:
        """Try to take the dead owner's run lock; (True, lock_file) means we may recover its sessions."""
        owner_run_id = str(owner_run_id or "").strip()
        if owner_run_id == self._run_id:
            return False, None
        path = self._state_path("lock", owner_run_id)
        if path is None:
            return False, None
        if fcntl is None:
            if not owner_run_id:
                # Legacy markers predate run ownership; keep that upgrade path on
                # platforms without POSIX locks (concurrent recovery is guarded on POSIX only).
                return True, None
            logger.debug("Skipping OpenViking pending-session recovery for owner %s; advisory locks are not supported", owner_run_id)
            return False, None
        try:
            return True, self._flock_open(path)
        except Exception as e:
            if not (isinstance(e, OSError) and e.errno in _LOCK_BUSY_ERRNOS):
                logger.debug("Skipping OpenViking pending-session recovery for owner %s; could not check run lock %s: %s", owner_run_id, path, e)
            return False, None

    def _mark_session_pending(self, sid: str, *, scope: Optional[_CommitScope] = None) -> None:
        scope = scope or self._capture_commit_scope()
        if not sid or self._has_committed_session(sid, scope=scope) or sid in scope.pending:
            return
        path = self._state_path("pending", sid, scope=scope)
        if path is None:
            return
        if self._run_lock_path is None:
            logger.debug("Could not safely mark OpenViking session %s pending without a run lock", sid)
            return
        try:
            from .host import mkdir_under_hermes_home
            mkdir_under_hermes_home(path.parent)
            atomic_json_write(path, {"session_id": sid, "owner_run_id": self._run_id,
                                    "connection_key": scope.connection_key}, mode=0o600)
            scope.pending.add(sid)
        except Exception as e:
            logger.debug("Could not mark OpenViking session %s pending: %s", sid, e)

    def _clear_pending_session(self, sid: str, *, scope: Optional[_CommitScope] = None,
                               pending_path: Optional[Path] = None) -> None:
        scope = scope or self._capture_commit_scope()
        scope.pending.discard(sid)
        path = pending_path or self._state_path("pending", sid, scope=scope)
        try:
            if path is not None:
                path.unlink(missing_ok=True)
        except Exception as e:
            logger.debug("Could not clear OpenViking pending session %s: %s", sid, e)

    def _pending_sessions(self) -> List[tuple[str, str, Path, str]]:
        """Read both scoped markers and legacy <sid>.json recovery markers."""
        directory = Path(self._hermes_home) / _PENDING_SESSIONS_RELATIVE_DIR if self._hermes_home else None
        if directory is None or not directory.is_dir():
            return []
        sessions: List[tuple[str, str, Path, str]] = []
        for path in sorted(directory.glob("*.json")):
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                raw = None
            raw = raw if isinstance(raw, dict) else {}
            sid = str(raw.get("session_id") or "").strip() or unquote(path.stem).strip()
            if sid:
                sessions.append((sid, str(raw.get("owner_run_id") or "").strip(), path,
                                 str(raw.get("connection_key") or "")))
        return sessions

    def _recover_pending_sessions(self) -> None:
        """Commit sessions left pending by dead runs, one thread per former owner."""
        scope = self._capture_commit_scope()
        if not scope.client:
            return
        pending_by_owner: Dict[str, List[tuple[str, Path]]] = {}
        for sid, owner_run_id, path, connection_key in self._pending_sessions():
            if connection_key and connection_key != scope.connection_key:
                continue
            pending_by_owner.setdefault(owner_run_id, []).append((sid, path))

        for owner_run_id, sids in pending_by_owner.items():
            recoverable, owner_lock_file = self._claim_owner_run_for_recovery(owner_run_id)
            if not recoverable:
                continue

            def _recover_owner(pending_sids=tuple(sids), owner=owner_run_id, lock_file=owner_lock_file) -> None:
                try:
                    for pending_sid, pending_path in pending_sids:
                        if not self._claim_deferred_sid(pending_sid, scope=scope):
                            continue
                        try:
                            with self._writer_commit_lock:
                                if self._has_committed_session(pending_sid, scope=scope):
                                    self._clear_pending_session(pending_sid, scope=scope, pending_path=pending_path)
                                elif not self._shutting_down:
                                    self._commit_session(pending_sid, 0, context="during startup recovery", clear_missing=True,
                                                         scope=scope, pending_path=pending_path)
                        finally:
                            self._claim_deferred_sid(pending_sid, release=True, scope=scope)
                finally:
                    self._flock_close(lock_file, None if owner == self._run_id else self._state_path("lock", owner), "owner run lock")

            self._spawn_tracked(f"openviking-recover-owner-{owner_run_id or 'legacy'}", _recover_owner, self._deferred_commit_lock, lambda: self._deferred_commit_threads)
