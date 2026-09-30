"""Prefetch: recall routing, deadlines, context-mode recall and recall status."""

from __future__ import annotations

import threading
from typing import Any, Callable, Dict, List, Optional

from .deps import _rest_client
from .host import get_secret, spawn_context_thread
from .http import _is_timeout_error, _resolve_user_space, _VikingClient
from .log import get_logger
from .settings import _RECALL_SETTING_KEYS, _SETTING_SPECS
from .transcript import _derive_openviking_user_text, openviking_session_id

logger = get_logger()


_RECALL_QUERY_MIN_CHARS = 5
_RECALL_MIN_TIMEOUT_SECONDS = 0.05
# One deadline for a whole prefetch() call. The host joins prefetch for a fixed
# 8 s (hermes:agent/memory_manager.py:32) and drops a late result.
_PREFETCH_BUDGET_SECONDS = 7.5
# A recall request that still has a fallback after it leaves this much of the
# budget for that fallback; a search without LLM steps takes about 0.4-0.8 s.
_RECALL_FALLBACK_RESERVE_SECONDS = 1.0
_SESSION_START_DEFAULT_KEY = "__openviking_default_session__"
# Outcome of the last prefetch per session id, read by recall_status() and last_recall_outcome().
_RECALL_PENDING, _RECALL_INJECTED, _RECALL_EMPTY = "pending", "injected", "empty"
_RECALL_TIMEOUT, _RECALL_UNAVAILABLE, _RECALL_ERROR = "timeout", "unavailable", "error"
_RECALL_OUTCOMES_KEPT = 64  # session ids remembered per provider; oldest dropped first
_RECALL_STATUS_LABEL = "OpenViking"
# Per-type context-mode slots for recall_limit; same weights and rounding as
# memory-plugin-shared lib/recall-core.mjs codingQuotas().
_CONTEXT_QUOTA_WEIGHTS = {"events": 1, "entities": 2, "preferences": 1, "experiences": 1, "resources": 3, "skills": 2}


def _context_quotas(limit: Any) -> Dict[str, int]:
    """Split ``limit`` into per-type slots, each at least 1, closest to the weights."""
    try:
        slots = max(1, int(limit))
    except (TypeError, ValueError):
        slots = 10
    quotas = dict.fromkeys(_CONTEXT_QUOTA_WEIGHTS, 1)
    if slots < len(quotas):
        return quotas
    total = sum(_CONTEXT_QUOTA_WEIGHTS.values())
    ideals = {key: slots * weight / total for key, weight in _CONTEXT_QUOTA_WEIGHTS.items()}
    while sum(quotas.values()) < slots:
        best = next(iter(quotas))
        for key in quotas:
            if ideals[key] - quotas[key] > ideals[best] - quotas[best]:
                best = key
        quotas[best] += 1
    return quotas


class _RecallProbe:
    """What one prefetch saw: the first failure and the number of injected recall entries."""

    __slots__ = ("failure", "count")

    def __init__(self) -> None:
        self.failure = ""
        self.count = 0

    def fail(self, error: Optional[BaseException] = None, *, outcome: str = "") -> None:
        if not self.failure:
            self.failure = outcome or (_RECALL_TIMEOUT if _is_timeout_error(error) else _RECALL_ERROR)

    def outcome(self, text: str) -> str:
        return _RECALL_INJECTED if text else (self.failure or _RECALL_EMPTY)


class RecallMixin:
    """Methods of ``OpenVikingMemoryProvider`` moved here unchanged; mixed into that class."""

    def _prefetch_context(self, query: str, session_id: str, probe: _RecallProbe) -> str:
        """Connection check, then the session-start block and query recall concurrently.

        One deadline covers all three. A part that misses it is dropped from this
        turn's result (outcome ``timeout``) and the other part is still returned.
        """
        started = self._deps.monotonic()
        query_text = _derive_openviking_user_text(query).strip()
        if not self._ensure_client():
            probe.fail(outcome=_RECALL_UNAVAILABLE)
            return ""
        deadline = started + self._prefetch_budget()
        session_key = session_id or _SESSION_START_DEFAULT_KEY
        claim = self._claim_session_start(session_key)
        # (name, probe, part): each part reports into its own probe, merged below.
        parts: List[tuple[str, _RecallProbe, Callable[[_RecallProbe], Optional[str]]]] = []
        if claim is not None:
            parts.append(("session-start", _RecallProbe(), lambda part_probe: self._session_start_memory_context(
                deadline=deadline, probe=part_probe)))
        if len(query_text) >= _RECALL_QUERY_MIN_CHARS:
            parts.append(("recall", _RecallProbe(), lambda part_probe: self._search_prefetch_context(
                query_text, session_id=session_id, probe=part_probe, deadline=deadline)))
        results: List[Optional[tuple]] = [None] * len(parts)
        try:
            results = self._run_prefetch_parts(parts, deadline)
        finally:
            if claim is not None:
                # Latch only a block that reached this turn's result.
                delivered = results[0] is not None and results[0][0] is not None
                self._settle_session_start(session_key, claim, latch=delivered)
        texts = []
        for (name, part_probe, _part), result in zip(parts, results, strict=True):
            if result is None:
                logger.debug("OpenViking %s prefetch missed the recall deadline; dropped from this turn", name)
                probe.fail(outcome=_RECALL_TIMEOUT)
                continue
            if part_probe.failure:
                probe.fail(outcome=part_probe.failure)
            if name == "recall":
                probe.count = part_probe.count
            if result[0]:
                texts.append(result[0])
        return "## OpenViking Context\n" + "\n\n".join(texts) if texts else ""

    def _run_prefetch_parts(self, parts: List[tuple[str, _RecallProbe, Callable[[_RecallProbe], Any]]],
                            deadline: float) -> List[Optional[tuple]]:
        """Run each part on a one-shot context-bound thread and wait for them until ``deadline``.

        Returns ``(value,)`` for a part that finished and None for one that missed
        the deadline. Every request of a part is clamped to the same deadline, so a
        late part ends shortly after it; its value is discarded.
        """
        done = threading.Condition()
        results: List[Optional[tuple]] = [None] * len(parts)
        # Read the budget once; the wait below then runs on the Condition's clock.
        wait = max(0.0, deadline - self._deps.monotonic())

        def run(index: int, part_probe: _RecallProbe, part: Callable[[_RecallProbe], Any]) -> None:
            value = None
            try:
                value = part(part_probe)
            except Exception as e:  # parts handle their own errors; this only keeps the signal
                logger.debug("OpenViking prefetch part failed: %s", e)
                part_probe.fail(e)
            finally:
                with done:
                    results[index] = (value,)
                    done.notify_all()

        for index, (name, part_probe, part) in enumerate(parts):
            spawn_context_thread(run, name=f"openviking-prefetch-{name}", args=(index, part_probe, part)).start()
        with done:
            done.wait_for(lambda: all(result is not None for result in results), timeout=wait)
            return list(results)

    def _prefetch_budget(self) -> float:
        try:
            return self._recall_budget(self._recall_config())
        except Exception as e:
            logger.debug("OpenViking recall config unreadable; using the full prefetch budget: %s", e)
            return _PREFETCH_BUDGET_SECONDS

    @staticmethod
    def _recall_budget(cfg: Dict[str, Any]) -> float:
        """Total recall deadline: ``recall_timeout_seconds``, capped by the prefetch budget."""
        return min(_PREFETCH_BUDGET_SECONDS, cfg["timeout_seconds"])

    def _begin_recall(self, session_id: str) -> int:
        """Mark a prefetch as running, so recall_status() never reports an earlier result."""
        with self._recall_status_lock:
            self._recall_sequence += 1
            self._recall_outcomes[session_id] = (self._recall_sequence, _RECALL_PENDING, 0)
            self._recall_outcomes.move_to_end(session_id)
            while len(self._recall_outcomes) > _RECALL_OUTCOMES_KEPT:
                self._recall_outcomes.popitem(last=False)
            self._last_recall_session = session_id
            return self._recall_sequence

    def _finish_recall(self, session_id: str, sequence: int, outcome: str, count: int) -> None:
        with self._recall_status_lock:
            current = self._recall_outcomes.get(session_id)
            if current is not None and current[0] == sequence:
                self._recall_outcomes[session_id] = (sequence, outcome, count)

    def _recall_record(self, session_id: Optional[str]) -> Optional[tuple[int, str, int]]:
        with self._recall_status_lock:
            key = self._last_recall_session if session_id is None else session_id
            return None if key is None else self._recall_outcomes.get(key)

    def _remaining_recall_timeout(self, deadline: float, per_request_timeout: float) -> float:
        remaining = deadline - self._deps.monotonic()
        if remaining <= _RECALL_MIN_TIMEOUT_SECONDS:
            raise TimeoutError("OpenViking recall budget exhausted")
        return min(per_request_timeout, remaining)

    def _fallback_request_timeout(self, deadline: float, per_request_timeout: float) -> Optional[float]:
        """Timeout for a request that still has a fallback after it, keeping
        ``_RECALL_FALLBACK_RESERVE_SECONDS`` for that fallback; None when too little is left."""
        remaining = deadline - self._deps.monotonic() - _RECALL_FALLBACK_RESERVE_SECONDS
        return None if remaining <= _RECALL_MIN_TIMEOUT_SECONDS else min(per_request_timeout, remaining)

    def _search_prefetch_context(self, query: str, *, session_id: str = "", client: Optional[_VikingClient] = None,
                                 probe: Optional[_RecallProbe] = None, deadline: Optional[float] = None) -> str:
        query_text = (query or "").strip()
        probe = probe or _RecallProbe()
        session_id = openviking_session_id(session_id)
        sender_peer = self._current_sender_peer()
        if len(query_text) < _RECALL_QUERY_MIN_CHARS:
            return ""
        try:
            if client is None:
                if self._env_refresh_enabled:
                    client = self._ensure_client()
                elif self._client is not None:
                    client = self._new_client()  # legacy/hand-wired path: no env baseline yet
        except Exception as e:
            logger.debug("OpenViking prefetch client build failed: %s", e)
            probe.fail(e)
            return ""
        if client is None:
            probe.fail(outcome=_RECALL_UNAVAILABLE)
            return ""

        cfg = None
        try:
            cfg = self._recall_config()
            if deadline is None:
                deadline = self._deps.monotonic() + self._recall_budget(cfg)
            scope = cfg["scope"]
            target_uri = None
            if scope in ("shared", "peer"):
                endpoint, api_key, account, user, _agent = client._conn_snapshot
                client = _rest_client(self._deps, endpoint, api_key, account=account, user=user,
                                      agent=sender_peer if scope == "peer" else "")
            if scope == "peer":
                # Explicit roots also constrain fallback searches when there is
                # no sender. An actor-less user-root search includes all peers.
                user = _resolve_user_space(
                    client, timeout=self._remaining_recall_timeout(deadline, cfg["request_timeout_seconds"]),
                    raise_on_timeout=True,
                )
                if not user:
                    probe.fail(outcome=_RECALL_ERROR)
                    return ""
                user_root = f"viking://user/{user}"
                target_uri = [f"{user_root}/memories"]
                if sender_peer:
                    target_uri.append(f"{user_root}/peers/{sender_peer}/memories")
                if cfg["resources"]:
                    target_uri += [f"{user_root}/resources", "viking://resources"]
                    if sender_peer:
                        target_uri.append(f"{user_root}/peers/{sender_peer}/resources")
            primary = getattr(self, "_agent_context", "primary") == "primary"
            # Context mode is the default route for shared recall and for
            # sender-scoped recall with a sender (recall_context_mode=false
            # reverts both to list search). Otherwise compression alone picks it.
            # Without an actor, context-mode resource defaults include peers,
            # so that case always uses scoped list recall.
            context_route = primary and cfg["context_mode"] and (
                scope == "shared" or (scope == "peer" and bool(sender_peer)))
            compress = cfg["compress"] in ("server", "auto")
            if context_route or (compress and (scope != "peer" or sender_peer)):
                payload = {
                    "query": query_text,
                    "mode": "context",
                    "purpose": "coding",
                    "score_threshold": cfg["score_threshold"],
                    "max_tokens": max(64, min(32000, cfg["max_injected_chars"] // 4)),
                    "context_type": ["memory", "resource"] if cfg["resources"] else "memory",
                }
                if compress:
                    payload["rewrite"] = True if cfg["compress"] == "server" else "auto"
                if context_route:
                    payload["quotas"] = _context_quotas(cfg["limit"])
                if session_id and primary:
                    payload["session_id"] = session_id
                if scope in ("shared", "peer"):
                    payload["peer_scope"] = "actor" if scope == "peer" else "all"
                # Rewrite and query expansion are LLM steps on the server. Leave
                # budget for the search without them, which runs if this one
                # fails or times out.
                timeout = self._fallback_request_timeout(deadline, cfg["request_timeout_seconds"])
                if timeout is None:
                    logger.debug("OpenViking recall budget left no time for context rewrite, using search")
                else:
                    try:
                        assembled = self._unwrap_result(
                            client.post("/api/v1/search/search", payload, timeout=timeout)
                        )
                        if isinstance(assembled, dict) and any(
                            k in assembled for k in ("rendered", "digest", "entries")
                        ):
                            if scope == "peer" and (assembled.get("stats") or {}).get("peer_scope") != "actor":
                                # Older servers may ignore an unknown field. Never
                                # inject a digest whose sender scope is unconfirmed.
                                raise ValueError("OpenViking did not confirm actor-scoped context")
                            if (assembled.get("stats") or {}).get("rewrite") == "no_relevant":
                                return ""
                            entries = assembled.get("entries")
                            probe.count = len(entries) if isinstance(entries, list) else 0
                            # A rewrite request injects the server's digest; plain
                            # context mode injects the rendered block.
                            text = assembled.get("digest") if "rewrite" in payload else None
                            return str(text or assembled.get("rendered") or "").strip()
                    except Exception as e:
                        logger.debug(
                            "OpenViking context rewrite unavailable or timed out, falling back to search: %s", e
                        )
            result = self._unwrap_result(
                self._post_prefetch_search(
                    client,
                    query_text,
                    session_id,
                    limit=max(cfg["limit"] * 4, 20),
                    context_type=["memory", "resource"] if cfg["resources"] else "memory",
                    deadline=deadline,
                    request_timeout=cfg["request_timeout_seconds"],
                    target_uri=target_uri,
                )
            )
            if not isinstance(result, dict):
                return ""
            candidates = [item for ctx_type in ("memories", "resources") for item in (result.get(ctx_type, []) or []) if isinstance(item, dict)]
            selected = self._select_recall_candidates(candidates, query_text, limit=cfg["limit"], score_threshold=cfg["score_threshold"])
            entries = self._build_prefetch_entries(
                client, selected, prefer_abstract=cfg["prefer_abstract"], max_injected_chars=cfg["max_injected_chars"],
                deadline=deadline, request_timeout=cfg["request_timeout_seconds"], full_read_limit=cfg["full_read_limit"],
            )
            probe.count = len(entries)
            return "\n".join(entries)
        except Exception as e:
            probe.fail(e)
            # A timeout leaves query recall empty. Report only local, bounded
            # diagnostics; exception text can contain query or identity data.
            if cfg is not None and _is_timeout_error(e):
                logger.warning(
                    "OpenViking recall timed out (%s; budget_s=%s request_s=%s); no query context injected",
                    type(e).__name__, self._recall_budget(cfg), cfg["request_timeout_seconds"],
                )
            else:
                logger.debug("OpenViking context search failed: %s", e)
            return ""

    # -- typed settings ------------------------------------------------------

    def _recall_config(self) -> Dict[str, Any]:
        cfg, env = self._profile_config_and_env()
        resolved = {
            key.removeprefix("recall_"): self._setting(key, cfg, env=env) for key in _RECALL_SETTING_KEYS
        }
        if resolved["compress"] in ("server", "auto"):
            # Retrieval plus server rewrite needs more than the default 3 s, so
            # without explicit user deadlines it may use the whole prefetch
            # budget. The default off path is unchanged.
            for key in ("recall_timeout_seconds", "recall_request_timeout_seconds"):
                override = get_secret(_SETTING_SPECS[key]["env_var"]) if env is None else env.get(_SETTING_SPECS[key]["env_var"])
                if key not in cfg and not override:
                    resolved[key.removeprefix("recall_")] = _PREFETCH_BUDGET_SECONDS
        return resolved
