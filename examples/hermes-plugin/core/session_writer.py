"""Turn upload, tracked workers, session commit and the exit commit."""

from __future__ import annotations

import atexit
import json
import threading
import weakref
from contextlib import suppress
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

from .connection import _CommitScope
from .deps import default_deps
from .host import env_var_enabled, spawn_context_thread
from .http import _TIMEOUT, _status_code_from_error, _VikingClient
from .log import get_logger
from .transcript import _message_text, openviking_session_id

logger = get_logger()


_SESSION_DRAIN_TIMEOUT = 10.0
_DEFERRED_COMMIT_TIMEOUT = (_TIMEOUT * 2) + 5.0
_SESSION_MESSAGE_BATCH_LIMIT = 100
# Bound of one (sid, generation) backlog of unsent messages; the oldest are dropped first.
_BACKLOG_MAX_MESSAGES = 2000
_BACKLOG_MAX_BYTES = 8 * 1024 * 1024
# After a retryable upload failure, turns of the same connection generation go straight
# into the backlog for this long instead of each waiting on a server that is known to be down.
_UPLOAD_COOLDOWN_SECONDS = 30.0
# Messages rejected with 401/403 wait this long for the same connection to be accepted again.
_AUTH_BACKLOG_TTL_SECONDS = 10 * 60.0
_SYNC_TRACE_ENV = "HERMES_OPENVIKING_SYNC_TRACE"
# Host contexts that must not write into OpenViking. Fixed-prompt output from scheduled
# jobs, delegated subagents, and flush forks has no memory value and would spend server-side
# extraction budget. Hermes delivers the context to initialize(); recall/read paths are unchanged.
_NON_PRIMARY_AGENT_CONTEXTS = frozenset({"cron", "subagent", "flush"})


# atexit safety net: commit the current session even if shutdown_memory_provider
# never runs (gateway crash, exception in the session expiry watcher, ...).
# Only primary providers are held, and only weakly: a multiplexed gateway keeps one per
# profile, and a provider dropped by soft eviction must stay collectable. The hook is
# registered on the first primary initialize(), never at import.
_exit_registry: "weakref.WeakSet[OpenVikingMemoryProvider]" = weakref.WeakSet()  # noqa: F821
_exit_registry_lock = threading.Lock()
_exit_hook_registered = False
# The CLI's exit watchdog os._exit()s 30 s after cleanup starts
# (hermes:hermes_cli/cli_shutdown.py:51-99), and memory shutdown runs inside that window.
_EXIT_COMMIT_BUDGET = 20.0


def _register_for_exit(provider: "OpenVikingMemoryProvider") -> None:  # noqa: F821
    global _exit_hook_registered
    with _exit_registry_lock:
        if provider._shutting_down:
            return
        if not _exit_hook_registered:
            atexit.register(_atexit_commit_sessions)
            _exit_hook_registered = True
        _exit_registry.add(provider)


def _deregister_for_exit(provider: "OpenVikingMemoryProvider") -> None:  # noqa: F821
    with _exit_registry_lock:
        _exit_registry.discard(provider)


def _atexit_commit_sessions(budget: float = _EXIT_COMMIT_BUDGET):
    monotonic = default_deps().monotonic
    deadline = monotonic() + budget
    with _exit_registry_lock:
        providers = list(_exit_registry)
        _exit_registry.clear()
    for provider in providers:
        try:
            remaining = deadline - monotonic()
            if remaining <= 0:
                # No new commit once the budget is spent; the marker stays for the next run's recovery.
                logger.warning("OpenViking exit budget used up; leaving session %s pending", provider._session_id)
                continue
            with suppress(Exception):  # best-effort at shutdown time
                provider._end_session(min(_SESSION_DRAIN_TIMEOUT, remaining), deadline=deadline)
        finally:
            # ``finally`` (as on main): the run lock is released even when the commit
            # dies of a BaseException (KeyboardInterrupt during atexit).
            with suppress(Exception):
                provider._release_run_lock()


def _upload_failure_kind(error: Exception) -> str:
    """``retry`` (network, 408, 429, 5xx, retryable 409), ``auth`` (401/403, never retried
    at once), ``fallback`` (404/405: batch endpoint missing) or ``drop`` (other 4xx)."""
    status = _status_code_from_error(error)
    if status is None or status in (408, 429) or status >= 500:
        return "retry"
    if status == 409 and getattr(error, "retryable", False) is True:
        return "retry"
    if status in (401, 403):
        return "auth"
    if status in (404, 405):
        return "fallback"
    return "drop"


def _response_pending_tokens(response: Any) -> Optional[int]:
    """``pending_tokens`` of a message write response, or None when the server did not send it."""
    result = response.get("result") if isinstance(response, dict) and "result" in response else response
    value = result.get("pending_tokens") if isinstance(result, dict) else None
    return value if isinstance(value, int) and not isinstance(value, bool) else None


@dataclass
class _Backlog:
    """Unsent messages of one (sid, connection generation), oldest first."""

    client: _VikingClient
    messages: List[Dict[str, Any]] = field(default_factory=list)
    sizes: List[int] = field(default_factory=list)
    auth_failed: bool = False
    # Monotonic time of the first 401/403 rejection.
    auth_failed_at: Optional[float] = None

    def mark_auth_failed(self, now: float) -> None:
        self.auth_failed = True
        if self.auth_failed_at is None:
            self.auth_failed_at = now


@dataclass
class _TurnUpload:
    """One turn's OpenViking upload: structured batches, one retry for a retryable failure,
    then individual messages. What is still unsent afterwards is ``unsent()``."""

    client: _VikingClient
    sid: str
    batch_messages: List[Dict[str, Any]]
    user_content: str
    assistant_content: str
    assistant_peer_id: str
    user_peer_id: str = ""
    next_index: int = 0
    failure_kind: str = ""
    # Post-write pending_tokens from the last write response, when the server returns it.
    pending_tokens: Optional[int] = None

    def _trace(self, fmt: str, *args) -> None:
        if env_var_enabled(_SYNC_TRACE_ENV):
            logger.info("OpenViking sync_turn trace: " + fmt, *args)

    def materialize(self) -> None:
        """A turn without a structured batch is sent as one user + one assistant text message."""
        if self.batch_messages:
            return
        user_message: Dict[str, Any] = {"role": "user", "parts": [{"type": "text", "text": self.user_content[:4000]}]}
        if self.user_peer_id:
            user_message["peer_id"] = self.user_peer_id
        assistant_message: Dict[str, Any] = {"role": "assistant", "parts": [{"type": "text", "text": _message_text(self.assistant_content)[:4000]}]}
        if self.assistant_peer_id:
            assistant_message["peer_id"] = self.assistant_peer_id
        self.batch_messages = [user_message, assistant_message]

    def unsent(self) -> List[Dict[str, Any]]:
        return self.batch_messages[self.next_index:]

    def post(self, client: _VikingClient) -> None:
        while self.next_index < len(self.batch_messages):
            batch_end = min(self.next_index + _SESSION_MESSAGE_BATCH_LIMIT, len(self.batch_messages))
            payload = {"messages": self.batch_messages[self.next_index:batch_end]}
            self._trace("POST /api/v1/sessions/%s/messages/batch range=%d:%d payload=%s",
                        self.sid, self.next_index, batch_end, json.dumps(payload, ensure_ascii=False))
            response = client.post(f"/api/v1/sessions/{self.sid}/messages/batch", payload)
            self.pending_tokens = _response_pending_tokens(response)
            self.next_index = batch_end

    def run(self) -> Optional[_VikingClient]:
        """Return the client on success; otherwise ``failure_kind`` says why ``unsent()`` is left.
        The HTTP wrapper opens a new connection for every request, and every attempt keeps the
        identity captured with the turn even if /reload happens meanwhile."""
        self.materialize()
        client = self.client
        for attempt in (0, 1):
            try:
                self.post(client)
                return client
            except Exception as e:
                kind = _upload_failure_kind(e)
                if kind == "retry" and attempt == 0:
                    logger.debug("OpenViking sync_turn failed, retrying: %s", e)
                    continue
                self.failure_kind = kind
                if kind == "auth":
                    logger.warning("OpenViking sync_turn rejected (not retried): %s", e)
                    return None
                logger.warning("OpenViking structured sync failed; writing %d remaining messages individually: %s",
                               len(self.batch_messages) - self.next_index, e)
                break
        try:
            path = f"/api/v1/sessions/{self.sid}/messages"
            for payload in self.batch_messages[self.next_index:]:
                self._trace("POST %s message_index=%d payload=%s", path, self.next_index, json.dumps(payload, ensure_ascii=False))
                response = client.post(path, payload)
                self.pending_tokens = _response_pending_tokens(response)
                self.next_index += 1
            self.failure_kind = ""
            return client
        except Exception as fallback_error:
            self.failure_kind = _upload_failure_kind(fallback_error)
            logger.warning("OpenViking sync_turn failed during individual-message fallback: %s", fallback_error)
        return None


class SessionWriterMixin:
    """Methods of ``OpenVikingMemoryProvider`` moved here unchanged; mixed into that class."""

    def _spawn_tracked(self, name: str, body: Callable[[], None], lock: threading.Lock, workers: Callable[[], Set[threading.Thread]],
                       *, after_discard: Callable[[], None] = None, skip_if: Callable[[], bool] = None) -> None:
        """Daemon thread registered in ``workers()`` (evaluated under ``lock``) for the
        duration of ``body`` so shutdown / drains can join it."""

        def _run() -> None:
            try:
                body()
            finally:
                with lock:
                    workers().discard(thread)
                    if after_discard is not None:
                        after_discard()

        thread = spawn_context_thread(_run, name=name)
        with lock:
            if skip_if is not None and skip_if():
                return
            workers().add(thread)
            try:
                thread.start()
            except Exception as e:
                workers().discard(thread)
                logger.debug("OpenViking %s worker failed to start: %s", name, e)

    def _join_all(self, alive: Callable[[], List[threading.Thread]], timeout: float, *, slice_cap: Optional[float] = None) -> bool:
        """Join threads from ``alive()`` until none remain or the shared budget runs out."""
        monotonic = self._deps.monotonic
        deadline = monotonic() + timeout
        while True:
            workers = alive()
            if not workers:
                return True
            if deadline - monotonic() <= 0:
                return False
            for t in workers:
                slice_left = deadline - monotonic()
                if slice_left <= 0:
                    break
                t.join(timeout=min(slice_left, slice_cap) if slice_cap else slice_left)

    def _drain_finalizers(self, timeout: float) -> bool:
        """Join in-flight async session finalizers (shutdown/tests wait deterministically)."""
        def alive():
            with self._deferred_commit_lock:
                return [t for t in self._deferred_commit_threads if t.is_alive()]
        # Floor each join so a thread whose join() returns instantly while still alive can't hot-spin.
        return self._join_all(alive, timeout, slice_cap=0.05)

    def _drain_writers(self, sid: str, timeout: float) -> bool:
        """Join every in-flight writer for sid; False (budget exhausted) tells callers to skip the commit."""
        if not sid:
            return True

        def alive():
            with self._inflight_lock:
                return [t for t in self._inflight_writers.get(sid, ()) if t.is_alive()]
        return self._join_all(alive, timeout)

    # -- backlog of unsent messages (always under _writer_commit_lock) -------

    def _backlog_table(self) -> Dict[Tuple[str, str], _Backlog]:
        """Unsent messages keyed by (sid, connection generation), in insertion order."""
        table = self.__dict__.get("_upload_backlog")
        if table is None:
            table = self.__dict__.setdefault("_upload_backlog", {})
        return table

    def _upload_cooldown_table(self) -> Dict[str, float]:
        """Connection generation -> monotonic time before which turns are not sent."""
        table = self.__dict__.get("_upload_cooldown")
        if table is None:
            table = self.__dict__.setdefault("_upload_cooldown", {})
        return table

    def _start_upload_cooldown(self, generation: str) -> None:
        self._upload_cooldown_table()[generation] = self._deps.monotonic() + _UPLOAD_COOLDOWN_SECONDS

    def _upload_cooling_down(self, generation: str) -> bool:
        table = self._upload_cooldown_table()
        until = table.get(generation)
        if until is None:
            return False
        if self._deps.monotonic() < until:
            return True
        del table[generation]
        return False

    def _prune_backlog(self) -> None:
        """Messages rejected with 401/403 are never sent under other credentials."""
        table = self._backlog_table()
        with self._session_state_lock:
            current = self._commit_scope.marker_id if self._commit_scope is not None else None
        now = self._deps.monotonic()
        for key, entry in list(table.items()):
            if not entry.auth_failed:
                continue
            if key[1] != current:
                del table[key]
                logger.warning("OpenViking dropped %d unsent messages of session %s: they were rejected with 401/403 "
                               "and the connection changed since", len(entry.messages), key[0])
            elif entry.auth_failed_at is not None and now - entry.auth_failed_at >= _AUTH_BACKLOG_TTL_SECONDS:
                del table[key]
                logger.warning("OpenViking dropped %d unsent messages of session %s: they were rejected with 401/403 "
                               "and still not accepted after %d s", len(entry.messages), key[0], _AUTH_BACKLOG_TTL_SECONDS)

    def _backlog_add(self, sid: str, scope: _CommitScope, client: _VikingClient, messages: List[Dict[str, Any]],
                     *, auth_failed: bool) -> None:
        if not messages:
            return
        entry = self._backlog_table().setdefault((sid, scope.marker_id), _Backlog(client))
        if auth_failed:
            entry.mark_auth_failed(self._deps.monotonic())
        for message in messages:
            entry.messages.append(message)
            entry.sizes.append(len(json.dumps(message, ensure_ascii=False, default=str).encode()))
        dropped = 0
        while len(entry.messages) > _BACKLOG_MAX_MESSAGES or sum(entry.sizes) > _BACKLOG_MAX_BYTES:
            entry.messages.pop(0)
            entry.sizes.pop(0)
            dropped += 1
        if dropped:
            logger.warning("OpenViking backlog for session %s is full; dropped the %d oldest unsent messages", sid, dropped)
        logger.warning("OpenViking keeps %d unsent messages for session %s; they are sent before its next upload or commit",
                       len(entry.messages), sid)

    def _flush_backlog(self, sid: str, *, request_timeout: Optional[float] = None) -> bool:
        """Send every backlog of ``sid`` in order, each with the client it was captured with.
        True when nothing of ``sid`` is left unsent."""
        self._prune_backlog()
        table = self._backlog_table()
        timeout = {} if request_timeout is None else {"timeout": request_timeout}
        for key in [k for k in table if k[0] == sid]:
            entry = table[key]
            while entry.messages:
                batch = entry.messages[:_SESSION_MESSAGE_BATCH_LIMIT]
                try:
                    try:
                        entry.client.post(f"/api/v1/sessions/{sid}/messages/batch", {"messages": batch}, **timeout)
                    except Exception as e:
                        if _upload_failure_kind(e) != "fallback":
                            raise
                        for message in batch:
                            entry.client.post(f"/api/v1/sessions/{sid}/messages", message, **timeout)
                except Exception as e:
                    kind = _upload_failure_kind(e)
                    if kind in ("retry", "auth"):
                        if kind == "auth":
                            entry.mark_auth_failed(self._deps.monotonic())
                        if kind == "retry":
                            self._start_upload_cooldown(key[1])
                        logger.warning("OpenViking could not send %d unsent messages of session %s: %s",
                                       len(entry.messages), sid, e)
                        return False
                    logger.warning("OpenViking dropped %d unsent messages of session %s rejected by the server: %s",
                                   len(batch), sid, e)
                del entry.messages[:len(batch)]
                del entry.sizes[:len(batch)]
            del table[key]
        return True

    def _upload_turn(self, upload: _TurnUpload, sid: str, scope: _CommitScope) -> Optional[_VikingClient]:
        """Send the backlog first, then the turn; keep whatever could not be sent. Under the writer lock."""
        if self._upload_cooling_down(scope.marker_id):
            # A recent retryable failure of this connection: queue without a request.
            upload.materialize()
            self._backlog_add(sid, scope, upload.client, upload.unsent(), auth_failed=False)
            return None
        with self._session_state_lock:
            current_sid = openviking_session_id(self._session_id)
        # Sessions left behind by a switch: once their backlog is sent, commit them off this thread.
        for other in dict.fromkeys(k[0] for k in self._backlog_table() if k[0] != sid and k[1] == scope.marker_id):
            if self._flush_backlog(other) and other != current_sid:
                self._finalize_session_async(other, 0, context="after sending its backlog", scope=scope)
        self._flush_backlog(sid)
        pending = self._backlog_table().get((sid, scope.marker_id))
        if pending is not None:
            # Earlier messages of this connection are still unsent: queue behind them to keep the order.
            upload.materialize()
            self._backlog_add(sid, scope, upload.client, upload.unsent(), auth_failed=False)
            return None
        client = upload.run()
        if client is None and upload.failure_kind in ("retry", "auth"):
            if upload.failure_kind == "retry":
                self._start_upload_cooldown(scope.marker_id)
            self._backlog_add(sid, scope, upload.client, upload.unsent(), auth_failed=upload.failure_kind == "auth")
        return client

    # -- session commit / pending-session recovery --------------------------

    def _has_committed_session(self, sid: str, *, scope: Optional[_CommitScope] = None) -> bool:
        scope = scope or self._capture_commit_scope()
        with self._committed_session_lock:
            return sid in scope.committed

    def _mark_session_committed(self, sid: str, committed: bool = True, *, scope: Optional[_CommitScope] = None) -> None:
        """Latch (or, with ``committed=False``, re-arm) the per-sid commit guard. Re-arming is
        for in-place compression: it keeps the same live id, which would otherwise reject every later commit."""
        scope = scope or self._capture_commit_scope()
        with self._committed_session_lock:
            (scope.committed.add if committed else scope.committed.discard)(sid)

    def _claim_deferred_sid(self, sid: str, *, release: bool = False, scope: Optional[_CommitScope] = None) -> bool:
        """One finalizer per sid and connection generation; none after shutdown."""
        scope = scope or self._capture_commit_scope()
        with self._deferred_commit_lock:
            if release:
                scope.finalizing.discard(sid)
                return True
            if self._shutting_down or sid in scope.finalizing:
                return False
            scope.finalizing.add(sid)
            return True

    def _maybe_commit_live_session(self, sid: str, turn_count: int, threshold: int, client: _VikingClient,
                                   scope: _CommitScope, *, pending_tokens: Optional[int] = None) -> None:
        """Check after a successful upload; metadata failures must not replay the turn.
        ``pending_tokens`` from the write response saves the session lookup."""
        if self._shutting_down:
            return
        try:
            if pending_tokens is None:
                session = self._unwrap_result(client.get(f"/api/v1/sessions/{sid}"))
                pending_tokens = int(session.get("pending_tokens") or 0)
            if pending_tokens >= threshold:
                self._finalize_session_async(sid, turn_count, context="after live token threshold", client=client, scope=scope)
        except Exception as e:
            logger.warning("OpenViking live commit check failed for %s: %s", sid, e)

    def _session_needs_commit(self, sid: str, turn_count: int, *, scope: Optional[_CommitScope] = None,
                              request_timeout: Optional[float] = None) -> bool:
        # The committed-guard wins over turn_count: a racing sync_turn can re-increment
        # _turn_count after a commit+reset.
        scope = scope or self._capture_commit_scope()
        if self._has_committed_session(sid, scope=scope):
            return False
        if turn_count > 0:
            return True
        try:
            timeout = {} if request_timeout is None else {"timeout": request_timeout}
            session = self._unwrap_result(scope.client.get(f"/api/v1/sessions/{sid}", **timeout))
            return isinstance(session, dict) and int(session.get("pending_tokens") or 0) > 0
        except Exception:
            return False

    def _commit_session(self, sid: str, turn_count: int, *, context: str, clear_missing: bool = False,
                        client: Optional[_VikingClient] = None, scope: Optional[_CommitScope] = None,
                        pending_path: Optional[Path] = None, request_timeout: Optional[float] = None) -> bool:
        scope = scope or self._capture_commit_scope()
        # A commit never overtakes messages of its sid that are still unsent; the marker stays.
        if not self._flush_backlog(sid, request_timeout=request_timeout):
            logger.warning("OpenViking session %s has unsent messages; skipping commit %s", sid, context)
            return False
        try:
            timeout = {} if request_timeout is None else {"timeout": request_timeout}
            (client or scope.client).post(f"/api/v1/sessions/{sid}/commit", {"keep_recent_count": 0}, **timeout)
            self._mark_session_committed(sid, scope=scope)
            self._clear_pending_session(sid, scope=scope, pending_path=pending_path)
            with self._session_state_lock:
                if openviking_session_id(self._session_id) == sid and self._commit_scope is scope and self._client is scope.client:
                    self._turn_count = 0
            logger.info("OpenViking session %s committed %s (%d turns)", sid, context, turn_count)
            return True
        except Exception as e:
            if clear_missing and _status_code_from_error(e) == 404:
                self._clear_pending_session(sid, scope=scope, pending_path=pending_path)
                logger.debug("OpenViking pending session %s no longer exists; dropped marker", sid)
            else:
                logger.warning("OpenViking session commit failed for %s: %s", sid, e)
            return False

    def _finalize_session_async(self, sid: str, turn_count: int, *, context: str,
                                client: Optional[_VikingClient] = None, scope: Optional[_CommitScope] = None) -> None:
        """Drain the old session's writers and commit it on a daemon thread, so the
        multi-second drain + pending-token GET + commit POST never runs on the
        caller's command thread (on_session_switch). Deduped per sid and connection; no-op after shutdown."""
        scope = scope or self._capture_commit_scope()
        if not sid or not self._claim_deferred_sid(sid, scope=scope):
            return

        def _finalize() -> None:
            try:
                if self._shutting_down:
                    return
                # Drain before taking the write lock: queued uploads need that
                # lock to finish. A later writer re-arms the guard after this commit.
                if not self._drain_writers(sid, timeout=_DEFERRED_COMMIT_TIMEOUT):
                    logger.warning("OpenViking writer for %s still alive after drain — leaving session uncommitted", sid)
                    return
                with self._writer_commit_lock:
                    if not self._shutting_down and self._session_needs_commit(sid, turn_count, scope=scope):
                        self._commit_session(sid, turn_count, context=context, client=client, scope=scope)
            finally:
                self._claim_deferred_sid(sid, release=True, scope=scope)

        self._spawn_tracked(f"openviking-finalize-{sid}", _finalize, self._deferred_commit_lock, lambda: self._deferred_commit_threads)

    def _end_session(self, drain_timeout: float, *, deadline: Optional[float] = None) -> None:
        """Commit the current session; ``deadline`` (monotonic) caps every request at exit.
        Drops the atexit entry when nothing uncommitted is left behind."""
        if not self._writes_enabled:
            return
        if not self._ensure_client():
            return
        with self._session_state_lock:
            scope = self._capture_commit_scope()
            sid = openviking_session_id(self._session_id)
        if not self._drain_writers(sid, timeout=drain_timeout):
            logger.warning("OpenViking writer for %s still alive after drain — skipping commit", sid)
            return

        def remaining() -> Optional[float]:
            return None if deadline is None else deadline - self._deps.monotonic()

        def expired() -> bool:
            return deadline is not None and remaining() <= 0

        # At exit the lock wait counts against the budget too: an older sid's finalizer or
        # recovery thread may hold it for a whole commit POST at the 30 s client default.
        if deadline is None:
            self._writer_commit_lock.acquire()
        elif not self._writer_commit_lock.acquire(timeout=max(0.0, remaining())):
            logger.warning("OpenViking exit budget used up waiting to commit; leaving session %s pending", sid)
            return
        try:
            with self._session_state_lock:
                turn_count = self._turn_count if openviking_session_id(self._session_id) == sid and self._commit_scope is scope else 0
            if expired():
                return
            if self._session_needs_commit(sid, turn_count, scope=scope, request_timeout=remaining()):
                if expired() or not self._commit_session(
                        sid, turn_count, context="on session end", scope=scope, request_timeout=remaining()):
                    return
            # Under the write lock: a later writer re-registers before it marks its sid pending.
            if not self._has_uncommitted_data(scope):
                _deregister_for_exit(self)
        finally:
            self._writer_commit_lock.release()

    def _has_uncommitted_data(self, scope: _CommitScope) -> bool:
        """A writer still running, or a turn or pending marker of this connection generation not committed."""
        with self._inflight_lock:
            if any(t.is_alive() for group in self._inflight_writers.values() for t in group):
                return True
        if self._backlog_table():
            return True
        with self._session_state_lock:
            return bool(scope.pending) or self._turn_count > 0
