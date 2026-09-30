"""List-mode recall: search/find, candidate ranking and entry building."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from .http import _VikingClient
from .log import get_logger

logger = get_logger()


_RECALL_SUMMARY_KEYS = ("abstract", "overview", "text", "content")


class RecallListMixin:
    """Methods of ``OpenVikingMemoryProvider`` moved here unchanged; mixed into that class."""

    def _post_prefetch_search(self, client: _VikingClient, query: str, session_id: str, *, limit: int,
                              context_type: str | List[str], deadline: float, request_timeout: float,
                              target_uri: Optional[List[str]] = None) -> dict:
        """Session-aware search first, falling back to search/find (budget errors propagate).

        The session-aware search runs server-side intent analysis, so it leaves
        part of the budget for search/find and is skipped when that part is all
        that is left.
        """
        base_payload = {"query": query, "limit": limit, "score_threshold": 0, "context_type": context_type}
        if target_uri:
            base_payload["target_uri"] = target_uri
        timeout = self._fallback_request_timeout(deadline, request_timeout) if session_id else None
        if session_id and timeout is None:
            logger.debug("OpenViking recall budget left no time for session-aware search, using search/find")
        elif session_id:
            try:
                return client.post("/api/v1/search/search", {**base_payload, "session_id": session_id}, timeout=timeout)
            except TimeoutError:
                raise
            except Exception as e:
                logger.debug("OpenViking session-aware prefetch failed, falling back to search/find: %s", e)
        return client.post("/api/v1/search/find", base_payload, timeout=self._remaining_recall_timeout(deadline, request_timeout))

    @staticmethod
    def _clamp_score(value: Any) -> float:
        try:
            return max(0.0, min(1.0, float(value)))
        except (TypeError, ValueError):
            return 0.0

    @staticmethod
    def _recall_abstract(item: Dict[str, Any]) -> str:
        for key in _RECALL_SUMMARY_KEYS:
            value = item.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        return str(item.get("uri") or "").strip()

    @classmethod
    def _select_recall_candidates(cls, items: List[Dict[str, Any]], query: str, *, limit: int, score_threshold: float) -> List[Dict[str, Any]]:
        """Threshold + dedupe (uri, then abstract+category — events/cases stay URI-distinct),
        ranked by score + L2 leaf boost + query-token overlap."""
        tokens = ["".join(ch for ch in raw if ch.isalnum()) for raw in query.lower().replace("_", " ").split()]
        tokens = [token for token in tokens if len(token) >= 2][:8]

        def rank(item: Dict[str, Any]) -> float:
            text = f"{item.get('uri', '')} {cls._recall_abstract(item)}".lower()
            overlap_boost = min(0.2, sum(1 for token in tokens if token in text) * 0.05)
            return cls._clamp_score(item.get("score")) + (0.12 if item.get("level") == 2 else 0.0) + overlap_boost

        seen_uri, seen_key = set(), set()
        filtered: List[Dict[str, Any]] = []
        for item in items:
            uri = str(item.get("uri") or "").strip()
            if not uri or uri in seen_uri or cls._clamp_score(item.get("score")) < score_threshold:
                continue
            abstract = " ".join(cls._recall_abstract(item).lower().split())
            if abstract and "/events/" not in uri.lower() and "/cases/" not in uri.lower():
                key = f"abstract:{str(item.get('category') or '').strip().lower() or 'unknown'}:{abstract}"
            else:
                key = f"uri:{uri}"
            if key in seen_key:
                continue
            seen_uri.add(uri)
            seen_key.add(key)
            filtered.append(item)
        filtered.sort(key=rank, reverse=True)
        return filtered[:limit]

    def _build_prefetch_entries(self, client: _VikingClient, items: List[Dict[str, Any]], *, prefer_abstract: bool,
                                max_injected_chars: int, deadline: float, request_timeout: float, full_read_limit: int) -> List[str]:
        """One entry per item: abstract, or a full L2 read (budgeted by ``full_read_limit``) for
        leaf hits / items without an explicit summary; total size capped by ``max_injected_chars``."""
        entries: List[str] = []
        total_chars = 0
        full_reads = 0
        for item in items:
            content = self._recall_abstract(item)
            has_explicit_summary = any(isinstance(item.get(key), str) and item.get(key).strip() for key in _RECALL_SUMMARY_KEYS)
            uri = str(item.get("uri") or "")
            if not (prefer_abstract and has_explicit_summary) and uri and (item.get("level") == 2 or not has_explicit_summary) and full_reads < full_read_limit:
                try:
                    timeout = self._remaining_recall_timeout(deadline, request_timeout)
                    full_reads += 1
                    content = self._extract_text_content(client.get("/api/v1/content/read", params={"uri": uri}, timeout=timeout), strict=True) or content
                except Exception as e:
                    logger.debug("OpenViking prefetch full read failed for %s: %s", uri, e)
            if not content:
                continue
            category = str(item.get("category") or "").strip() or "memory"
            entry = "\n".join([f"- [{category}]", f"  <uri>{item.get('uri', '')}</uri>", *[f"  {line}" for line in content.splitlines()]])
            projected_chars = total_chars + (1 if entries else 0) + len(entry)
            if projected_chars <= max_injected_chars:
                entries.append(entry)
                total_chars = projected_chars
        return entries
