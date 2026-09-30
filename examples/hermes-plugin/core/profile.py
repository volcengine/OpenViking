"""The session-start memory block: profile, preferences and entities."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from .http import _status_code_from_error
from .log import get_logger
from .recall import _RECALL_UNAVAILABLE, _RecallProbe

logger = get_logger()


# Explicit-uid URIs (viking://user/<uid>/...) work under every auth mode and
# supported OpenViking version. The `~` alias requires OpenViking 0.4.16+ for
# USER/ADMIN roles and 0.4.17+ for ROOT, so internal paths remain explicit.
_SESSION_START_SUFFIXES = ("memories/profile.md", "memories/preferences", "memories/entities")
_SESSION_START_LIST_PARAMS = {"output": "agent", "recursive": True, "abs_limit": 512, "node_limit": 512}


class ProfileMixin:
    """Methods of ``OpenVikingMemoryProvider`` moved here unchanged; mixed into that class."""

    def _claim_session_start(self, session_key: str) -> Optional[object]:
        """Claim the session-start block for one prefetch of this sid; None when it was
        already delivered or another prefetch of the same sid is fetching it."""
        with self._session_start_lock:
            if session_key in self._profile_prefetched_sessions or session_key in self._session_start_claims:
                return None
            claim = self._session_start_claims[session_key] = object()
            return claim

    def _settle_session_start(self, session_key: str, claim: object, *, latch: bool) -> None:
        with self._session_start_lock:
            if self._session_start_claims.get(session_key) is not claim:
                return  # re-armed while this prefetch ran; the next one injects again
            del self._session_start_claims[session_key]
            if latch:
                self._profile_prefetched_sessions.add(session_key)

    def _rearm_session_start(self, *session_keys: Optional[str]) -> None:
        """Inject the session-start block again on the next prefetch of these sids."""
        with self._session_start_lock:
            for session_key in session_keys:
                if session_key:
                    self._profile_prefetched_sessions.discard(session_key)
                    self._session_start_claims.pop(session_key, None)

    def _profile_token_budget(self) -> int:
        cfg, env = self._profile_config_and_env()
        return self._setting("profile_token_budget", cfg, env=env)

    # -- session-start memory block -----------------------------------------

    @classmethod
    def _extract_memory_listing(cls, resp: Any) -> List[Dict[str, str]]:
        result = cls._unwrap_result(resp)
        entries = [{"name": name, "abstract": " ".join(str(raw.get("abstract") or "").split())[:200]}
                   for raw in (result if isinstance(result, list) else []) if isinstance(raw, dict) and not raw.get("isDir")
                   if (name := str(raw.get("rel_path") or raw.get("name") or "").strip()).endswith(".md")]
        return sorted(entries, key=lambda entry: entry["name"])

    @staticmethod
    def _token_units(content: str) -> int:
        """Quarter-token units (shared OpenViking estimator: CJK-range chars weigh 6)."""
        return sum(6 if ord(ch) >= 0x3000 else 1 for ch in content)

    @classmethod
    def _estimate_tokens(cls, content: str) -> int:
        return (cls._token_units(content) + 3) // 4

    @staticmethod
    def _take_tokens(content: str, max_units: int, *, from_end: bool = False) -> str:
        """Longest prefix (or suffix) of ``content`` within ``max_units``."""
        if max_units <= 0:
            return ""
        used = 0
        for idx in (range(len(content) - 1, -1, -1) if from_end else range(len(content))):
            used += 6 if ord(content[idx]) >= 0x3000 else 1
            if used > max_units:
                return content[idx + 1:] if from_end else content[:idx]
        return content

    @classmethod
    def _truncate_profile_content(cls, content: str, max_units: int) -> str:
        """Keep head + tail (first 8 lines, then the end) within max_units; head-only for short profiles."""
        content = content.strip()
        if cls._token_units(content) <= max_units:
            return content

        def _head_only() -> str:
            marker = "\n... [profile truncated]"
            head = cls._take_tokens(content, max_units - cls._token_units(marker)).rstrip()
            return f"{head}{marker}" if head else cls._take_tokens(content, max_units)

        lines = content.split("\n")
        marker = "\n... [profile middle elided] ...\n"
        remaining = max_units - cls._token_units(marker)
        if len(lines) <= 12 or remaining <= 0:  # fewer than 8 head + 4 tail lines: no middle to elide
            return _head_only()
        head = cls._take_tokens("\n".join(lines[:8]), remaining // 2).rstrip()
        tail = cls._take_tokens("\n".join(lines[8:]), remaining - cls._token_units(head), from_end=True).lstrip()
        return f"{head}{marker}{tail}" if tail else _head_only()

    @staticmethod
    def _assemble_session_start_memory_block(profile: str, preference_lines: List[str], entity_lines: List[str],
                                             profile_uri: str = "viking://user/default/memories/profile.md") -> str:
        lines: List[str] = []
        if profile:
            lines += [f'<user-profile uri="{profile_uri}">', profile, "</user-profile>"]
        if preference_lines or entity_lines:
            lines += ["<available-memories>", *preference_lines, *entity_lines, "</available-memories>"]
        return "\n".join(lines)

    @classmethod
    def _format_memory_listing(cls, uri: str, entries: List[Dict[str, str]], max_units: int) -> tuple[List[str], int]:
        """Listing lines within max_units; degrades to a "+N more" tail or a one-line stub."""
        if not entries or max_units <= 0:
            return [], 0
        header = f"  {uri}/"
        used = cls._token_units(header)
        if used > max_units:
            stub = f"  {uri}/  ({len(entries)} entries; use `viking_search`)"
            stub_units = cls._token_units(stub)
            return ([stub], stub_units) if stub_units <= max_units else ([], 0)

        lines = [header]
        newline_units = cls._token_units("\n")
        for index, entry in enumerate(entries):
            abstract = entry.get("abstract", "")
            line = f"    - {entry['name']}{f' — {abstract}' if abstract else ''}"
            line_units = newline_units + cls._token_units(line)
            if used + line_units > max_units:
                tail = f"    ... +{len(entries) - index} more, use `viking_search`"
                tail_units = newline_units + cls._token_units(tail)
                if used + tail_units <= max_units:
                    lines.append(tail)
                    used += tail_units
                break
            lines.append(line)
            used += line_units
        return lines, used

    @classmethod
    def _build_session_start_memory_block(cls, *, profile: str, preferences: List[Dict[str, str]],
                                          entities: List[Dict[str, str]], token_budget: int, uris: Optional[tuple] = None) -> str:
        """Profile (<= half the budget) then preferences/entities listings sharing the rest."""
        profile_uri, preferences_uri, entities_uri = uris or tuple(f"viking://user/default/{suffix}" for suffix in _SESSION_START_SUFFIXES)
        profile = profile.strip()
        if not profile and not preferences and not entities:
            return ""

        placeholder = "\0"
        scaffold = cls._assemble_session_start_memory_block(
            placeholder if profile else "", [placeholder] if preferences else [], [placeholder] if entities else [], profile_uri=profile_uri,
        )
        placeholder_count = int(bool(profile)) + int(bool(preferences)) + int(bool(entities))
        available_units = max(0, (token_budget * 4) - (cls._token_units(scaffold) - placeholder_count))

        profile_text = ""
        if profile and available_units > 0:
            profile_text = cls._truncate_profile_content(profile, min(available_units, token_budget * 2))
            available_units -= cls._token_units(profile_text)

        preference_budget = available_units // 2 if (preferences and entities) else available_units
        preference_lines, preference_units = cls._format_memory_listing(preferences_uri, preferences, preference_budget)
        entity_lines, _ = cls._format_memory_listing(entities_uri, entities, available_units - preference_units)
        return cls._assemble_session_start_memory_block(profile_text, preference_lines, entity_lines, profile_uri=profile_uri)

    def _session_start_memory_context(self, *, deadline: float, probe: _RecallProbe) -> Optional[str]:
        """Profile + preferences/entities listings, injected once per session by ``prefetch``.

        None (the session is not latched) when the profile read fails for any
        reason other than absence (404/410) or no client is set.
        """
        try:
            client = self._client
            if not client:
                probe.fail(outcome=_RECALL_UNAVAILABLE)
                return None
            request_timeout = self._recall_config()["request_timeout_seconds"]

            def budgeted_get(path: str, params: dict) -> Any:
                return client.get(path, params=params, timeout=self._remaining_recall_timeout(deadline, request_timeout))

            try:
                user = self._user_space(client, timeout=self._remaining_recall_timeout(deadline, request_timeout))
            except Exception as e:
                probe.fail(e)
                return None
            uris = tuple(f"viking://user/{user}/{suffix}" for suffix in _SESSION_START_SUFFIXES)
            try:
                profile = self._extract_text_content(budgeted_get("/api/v1/content/read", {"uri": uris[0]}))
            except Exception as e:
                if _status_code_from_error(e) not in {404, 410}:
                    probe.fail(e)
                    return None
                profile = ""
            listings = []
            for uri in uris[1:]:
                try:
                    listings.append(self._extract_memory_listing(budgeted_get("/api/v1/fs/ls", {"uri": uri, **_SESSION_START_LIST_PARAMS})))
                except Exception:
                    listings.append([])
        except Exception as e:
            logger.debug("OpenViking session-start memory prefetch failed: %s", e)
            probe.fail(e)
            return None
        return self._build_session_start_memory_block(
            profile=profile, preferences=listings[0], entities=listings[1], token_budget=self._profile_token_budget(), uris=uris,
        )
