"""Memory system for persistent agent memory."""

import asyncio
import re
import time
from pathlib import Path
from typing import Any

from loguru import logger

from vikingbot.config.loader import load_config
from vikingbot.openviking_mount.ov_server import VikingClient
from vikingbot.utils.helpers import ensure_dir

_LEGACY_MEMORY_RECALL_LIMIT = 30
_TYPE_QUOTA_MEMORY_TYPES = ("events", "entities", "preferences")
_TYPE_QUOTA_EVENT_CHAR_RATIO = 0.75
_TYPE_QUOTA_PREFERENCE_FULL_LIMIT = 1
_MEMORY_TYPE_DESCRIPTIONS = {
    "events": (
        "Event memories. The URI path includes the event date."
    ),
    "entities": (
        "Entity and topic memories. Use them for stable facts, attributes, "
        "relationships, and background about people, hobbies, places, or concepts."
    ),
    "preferences": (
        "Preference memories. Use them for likes, dislikes, habits, recurring choices, "
        "and long-term personal tendencies."
    ),
}


class MemoryStore:
    """Two-layer memory: MEMORY.md (long-term facts) + HISTORY.md (grep-searchable log)."""

    def __init__(self, workspace: Path):
        self.memory_dir = ensure_dir(workspace / "memory")
        self.memory_file = self.memory_dir / "MEMORY.md"
        self.history_file = self.memory_dir / "HISTORY.md"

    @staticmethod
    def _get_score(memory: Any) -> float:
        raw_score = (
            memory.get("score", 0) if isinstance(memory, dict) else getattr(memory, "score", 0.0)
        )
        try:
            return float(raw_score)
        except (TypeError, ValueError):
            return 0.0

    @staticmethod
    def _get_uri(memory: Any) -> str:
        return memory.get("uri", "") if isinstance(memory, dict) else getattr(memory, "uri", "")

    @staticmethod
    def _get_abstract(memory: Any) -> str:
        return (
            memory.get("abstract", "")
            if isinstance(memory, dict)
            else getattr(memory, "abstract", "")
        )

    @staticmethod
    def _get_recall_type(memory: Any) -> str:
        return (
            memory.get("_recall_type", "")
            if isinstance(memory, dict)
            else getattr(memory, "_recall_type", "")
        )

    @staticmethod
    def _get_recall_rank(memory: Any) -> int:
        raw_rank = (
            memory.get("_recall_rank", 0)
            if isinstance(memory, dict)
            else getattr(memory, "_recall_rank", 0)
        )
        try:
            return int(raw_rank)
        except (TypeError, ValueError):
            return 0

    @classmethod
    def _infer_memory_type(cls, memory: Any) -> str:
        recall_type = cls._get_recall_type(memory)
        if recall_type:
            return recall_type

        uri = cls._get_uri(memory).strip("/")
        parts = [part for part in uri.split("/") if part]
        for idx, part in enumerate(parts):
            if part == "memories" and idx + 1 < len(parts):
                return parts[idx + 1]
        return ""

    @classmethod
    def _with_recall_metadata(cls, memory: Any, memory_type: str, rank: int) -> dict[str, Any]:
        if isinstance(memory, dict):
            item = dict(memory)
        else:
            item = {
                "uri": cls._get_uri(memory),
                "score": cls._get_score(memory),
                "abstract": cls._get_abstract(memory),
            }
        item["_recall_type"] = memory_type
        item["_recall_rank"] = rank
        return item

    @classmethod
    def _limit_memories(cls, result: list[Any], limit: int) -> list[Any]:
        return sorted(result, key=cls._get_score, reverse=True)[:limit]

    @staticmethod
    def _extract_memories(result: Any) -> list[Any]:
        if not result:
            return []
        if isinstance(result, list):
            return result
        if isinstance(result, dict):
            memories = result.get("memories")
            return memories if isinstance(memories, list) else []
        memories = getattr(result, "memories", None)
        return memories if isinstance(memories, list) else []

    @classmethod
    def _dedupe_memories(cls, memories: list[Any]) -> list[Any]:
        deduped: list[Any] = []
        seen_uris: set[str] = set()
        for memory in memories:
            uri = cls._get_uri(memory)
            if not uri or uri in seen_uris:
                continue
            seen_uris.add(uri)
            deduped.append(memory)
        return deduped

    @staticmethod
    def _memory_type_target(base_uri: str, memory_type: str) -> str:
        return f"{base_uri.rstrip('/')}/{memory_type.strip('/')}/"

    @staticmethod
    def _peer_id_from_memory_uri(uri: str) -> str | None:
        parts = [part for part in uri.strip("/").split("/") if part]
        for idx, part in enumerate(parts):
            if part == "peers" and idx + 1 < len(parts):
                return parts[idx + 1]
        return None

    @classmethod
    def _order_type_quota_memories(
        cls,
        memories: list[Any],
    ) -> list[Any]:
        groups: dict[str, list[Any]] = {}
        others: list[Any] = []
        for memory in memories:
            memory_type = cls._infer_memory_type(memory)
            if memory_type:
                groups.setdefault(memory_type, []).append(memory)
            else:
                others.append(memory)

        for group in groups.values():
            group.sort(key=cls._get_score, reverse=True)
        others.sort(key=cls._get_score, reverse=True)

        ordered: list[Any] = []
        for memory_type in _TYPE_QUOTA_MEMORY_TYPES:
            ordered.extend(groups.get(memory_type, []))
        ordered.extend(others)
        return cls._dedupe_memories(ordered)

    @classmethod
    def _select_type_quota_memories(
        cls,
        memories: list[Any],
        quotas: dict[str, int],
    ) -> list[Any]:
        memories = sorted(cls._dedupe_memories(memories), key=cls._get_score, reverse=True)
        selected: list[Any] = []
        for memory_type in _TYPE_QUOTA_MEMORY_TYPES:
            quota = max(0, int(quotas.get(memory_type, 0) or 0))
            if quota <= 0:
                continue
            type_memories = [
                memory
                for memory in memories
                if cls._infer_memory_type(memory) == memory_type
            ][:quota]
            selected.extend(
                cls._with_recall_metadata(memory, memory_type, rank)
                for rank, memory in enumerate(type_memories, start=1)
            )
        return cls._dedupe_memories(selected)

    @staticmethod
    def _type_quota_char_budgets(max_chars: int) -> dict[str, int]:
        max_chars = max(0, int(max_chars))
        event_budget = int(max_chars * _TYPE_QUOTA_EVENT_CHAR_RATIO)
        return {
            "events": event_budget,
            "entities": max_chars - event_budget,
        }

    @staticmethod
    def _format_memory_group(memory_type: str, memories: list[str]) -> str:
        description = _MEMORY_TYPE_DESCRIPTIONS.get(
            memory_type,
            "Other retrieved memories. Use them when relevant and inspect URI entries if needed.",
        )
        body = "\n".join(memories)
        return (
            f'<memory_group type="{memory_type}">\n'
            f"  <group_hint>{description}</group_hint>\n"
            f"{body}\n"
            f"</memory_group>"
        )

    @staticmethod
    def _format_full_memory(idx: int, uri: str, score: float, content: str) -> str:
        return (
            f'<memory index="{idx}" type="full">\n'
            f"  <uri>{uri}</uri>\n"
            f"  <score>{score}</score>\n"
            f"  <content>{content}</content>\n"
            f"</memory>"
        )

    @staticmethod
    def _format_summary_memory(idx: int, uri: str, score: float, summary: str) -> str:
        return (
            f'<memory index="{idx}" type="summary">\n'
            f"  <uri>{uri}</uri>\n"
            f"  <score>{score}</score>\n"
            f"  <summary>{summary}</summary>\n"
            f"</memory>"
        )

    @staticmethod
    def _format_uri_memory(idx: int, uri: str, score: float) -> str:
        return (
            f'<memory index="{idx}" type="uri">\n'
            f"  <uri>{uri}</uri>\n"
            f"  <score>{score}</score>\n"
            f"</memory>"
        )

    @staticmethod
    def _extract_event_summary(content: str, fallback: str = "") -> str:
        if content:
            match = re.search(
                r"(?is)^\s*Summary:\s*(.*?)(?:\n\s*\d{4}-\d{2}-\d{2}"
                r"(?:\s*\([^)]+\))?\s*ChatLog:|\n\s*ChatLog:|\n\s*<!--\s*MEMORY_FIELDS|$)",
                content,
            )
            if match:
                return re.sub(r"\s+", " ", match.group(1)).strip()
        return fallback.strip()

    def read_long_term(self) -> str:
        if self.memory_file.exists():
            return self.memory_file.read_text(encoding="utf-8")
        return ""

    async def _parse_viking_memory(
        self,
        result: Any,
        client: Any,
        min_score: float = 0.3,
        max_chars: int = 4000,
        full_limit: int | None = None,
        type_char_budgets: dict[str, int] | None = None,
        preference_full_limit: int = 0,
        include_uri_entries: bool = True,
        read_content: Any | None = None,
    ) -> str:
        """Parse viking memory with score filtering and character limit.
        Automatically reads full content for memories that fit the relevant budget;
        memories beyond budget are kept as URI-only entries when include_uri_entries is true.

        Args:
            result: Memory search results
            client: VikingClient instance to read content
            min_score: Minimum score threshold (default: 0.4)
            max_chars: Maximum character limit for full memories in global mode
            full_limit: Number of top memories allowed to use full content in global mode
            type_char_budgets: Per-memory-type character budgets for type_quota recall
            preference_full_limit: Number of preference memories forced full in type_quota mode
            include_uri_entries: Whether to keep URI-only candidates after content budgets are exhausted

        Returns:
            Formatted memory string within character limit
        """
        if not result or len(result) == 0:
            return ""

        filtered_memories = [memory for memory in result if self._get_score(memory) >= min_score]
        use_type_budgets = bool(type_char_budgets) and any(
            self._infer_memory_type(memory) for memory in filtered_memories
        )
        if use_type_budgets:
            filtered_memories = self._order_type_quota_memories(filtered_memories)
        else:
            filtered_memories.sort(key=self._get_score, reverse=True)

        grouped_memories: dict[str, list[str]] = {}
        total_chars = 0
        type_chars = dict.fromkeys(_TYPE_QUOTA_MEMORY_TYPES, 0)
        preference_full_count = 0
        seen_content_hashes = set()
        full_limit = len(filtered_memories) if full_limit is None else max(0, full_limit)
        type_char_budgets = type_char_budgets or {}

        for idx, memory in enumerate(filtered_memories, start=1):
            uri = self._get_uri(memory)
            abstract = self._get_abstract(memory)
            score = self._get_score(memory)
            memory_type = self._infer_memory_type(memory) or "other"
            should_try_full = idx <= full_limit
            if use_type_budgets:
                should_try_full = (
                    memory_type in type_char_budgets
                ) or (
                    memory_type == "preferences"
                    and preference_full_count < max(0, preference_full_limit)
                )

            content = ""
            try:
                if read_content:
                    content = await read_content(uri, level="read")
                else:
                    content = await client.read_content(uri, level="read")
            except Exception as e:
                logger.warning(f"Failed to read content from {uri}: {e}")

            # Deduplicate by content hash (use content or abstract as key)
            content_to_hash = content or abstract or uri
            content_hash = hash(content_to_hash)
            if content_to_hash and content_hash in seen_content_hashes:
                continue
            if content_to_hash:
                seen_content_hashes.add(content_hash)

            if should_try_full and content:
                full_memory_str = self._format_full_memory(idx, uri, score, content)
                full_chars = len(full_memory_str)
                if any(grouped_memories.values()):
                    full_chars += 1

                if use_type_budgets and memory_type in type_char_budgets:
                    budget = max(0, int(type_char_budgets[memory_type]))
                    if (
                        type_chars[memory_type] + full_chars <= budget
                        and total_chars + full_chars <= max_chars
                    ):
                        grouped_memories.setdefault(memory_type, []).append(full_memory_str)
                        type_chars[memory_type] += full_chars
                        total_chars += full_chars
                        continue
                elif use_type_budgets and memory_type == "preferences":
                    preference_full_count += 1
                    if total_chars + full_chars <= max_chars:
                        grouped_memories.setdefault(memory_type, []).append(full_memory_str)
                        total_chars += full_chars
                        continue
                elif total_chars + full_chars <= max_chars:
                    grouped_memories.setdefault(memory_type, []).append(full_memory_str)
                    total_chars += full_chars
                    continue

            if use_type_budgets and memory_type == "events" and content:
                summary = self._extract_event_summary(content, fallback=abstract)
                if summary:
                    grouped_memories.setdefault(memory_type, []).append(
                        self._format_summary_memory(idx, uri, score, summary)
                    )
                    continue

            if include_uri_entries:
                grouped_memories.setdefault(memory_type, []).append(
                    self._format_uri_memory(idx, uri, score)
                )

        ordered_groups: list[str] = []
        for memory_type in (*_TYPE_QUOTA_MEMORY_TYPES, "other"):
            memories = grouped_memories.get(memory_type)
            if memories:
                ordered_groups.append(self._format_memory_group(memory_type, memories))
        for memory_type, memories in grouped_memories.items():
            if memory_type not in (*_TYPE_QUOTA_MEMORY_TYPES, "other") and memories:
                ordered_groups.append(self._format_memory_group(memory_type, memories))

        return "\n".join(ordered_groups)

    def write_long_term(self, content: str) -> None:
        self.memory_file.write_text(content, encoding="utf-8")

    def append_history(self, entry: str) -> None:
        with open(self.history_file, "a", encoding="utf-8") as f:
            f.write(entry.rstrip() + "\n\n")

    def get_memory_context(self) -> str:
        long_term = self.read_long_term()
        return f"## Long-term Memory\n{long_term}" if long_term else ""

    async def _search_viking_memory_by_type_quota(
        self,
        client: VikingClient,
        query: str,
        peer_ids: list[str] | None,
        quotas: dict[str, int],
    ) -> list[Any]:
        if getattr(client, "actor_peer_id", None):
            try:
                base_targets = [client._current_peer_memory_target_uri(client.actor_peer_id)]
            except ValueError:
                base_targets = []
        else:
            base_targets = client.build_current_memory_target_uris(
                peer_ids=peer_ids,
                include_self=not bool(peer_ids),
            )
        if not base_targets:
            return []

        all_memories: list[Any] = []
        for memory_type, quota in quotas.items():
            if quota <= 0:
                continue
            type_memories: list[Any] = []
            for base_target in base_targets:
                target_uri = self._memory_type_target(base_target, memory_type)
                try:
                    find_kwargs = {
                        "query": query,
                        "target_uri": target_uri,
                        "limit": quota,
                    }
                    if getattr(client, "actor_peer_id", None):
                        find_kwargs["context_type"] = "memory"
                    result = await client.find(**find_kwargs)
                except Exception as e:
                    logger.warning(f"Failed to search {target_uri}: {e}")
                    continue
                type_memories.extend(self._extract_memories(result))
            type_memories = self._limit_memories(self._dedupe_memories(type_memories), quota)
            all_memories.extend(
                self._with_recall_metadata(memory, memory_type, rank)
                for rank, memory in enumerate(type_memories, start=1)
            )

        return self._dedupe_memories(all_memories)

    async def _search_actor_peer_memories_by_type_quota(
        self,
        query: str,
        workspace_id: str,
        openviking_connection: dict[str, Any] | None,
        base_client: VikingClient,
        peer_ids: list[str],
        quotas: dict[str, int],
    ) -> list[Any]:
        current_actor_peer_id = getattr(base_client, "actor_peer_id", None)
        normalized_peer_ids = VikingClient._dedupe_strings(
            [
                normalized_peer_id
                for normalized_peer_id in (VikingClient._peer_id(peer_id) for peer_id in peer_ids)
                if normalized_peer_id
            ]
        )

        async def search_peer(normalized_peer_id: str) -> list[Any]:
            peer_client = base_client
            should_close = False
            if normalized_peer_id != current_actor_peer_id:
                peer_client = await VikingClient.create(
                    agent_id=workspace_id,
                    connection=openviking_connection,
                    actor_peer_id=normalized_peer_id,
                )
                should_close = True

            try:
                return await self._search_viking_memory_by_type_quota(
                    client=peer_client,
                    query=query,
                    peer_ids=[normalized_peer_id],
                    quotas=quotas,
                )
            finally:
                if should_close:
                    try:
                        await peer_client.close()
                    except Exception as e:
                        logger.warning(f"Error closing VikingClient: {e}")

        results = await asyncio.gather(
            *(search_peer(peer_id) for peer_id in normalized_peer_ids),
            return_exceptions=True,
        )
        all_memories: list[Any] = []
        for result in results:
            if isinstance(result, Exception):
                logger.warning(f"Failed to search actor peer memories: {result}")
                continue
            all_memories.extend(result)

        return self._select_type_quota_memories(all_memories, quotas)

    async def get_viking_memory_context(
        self,
        current_message: str,
        workspace_id: str,
        sender_id: str,
        peer_ids: list[str] | None = None,
        user_ids: list[str] | None = None,
        openviking_connection: dict[str, Any] | None = None,
    ) -> str:
        client = None
        read_clients: dict[str, VikingClient] = {}
        try:
            ov_cfg = load_config().ov_server
            admin_user_id = (
                str(openviking_connection.get("user_id"))
                if isinstance(openviking_connection, dict) and openviking_connection.get("user_id")
                else ov_cfg.admin_user_id
            )
            logger.info(f"workspace_id={workspace_id}")
            logger.info(f"sender_id={sender_id}")
            logger.info(f"peer_ids={peer_ids}")
            logger.info(f"user_ids={user_ids}")
            logger.info(f"admin_user_id={admin_user_id}")

            client = await VikingClient.create(
                agent_id=workspace_id,
                connection=openviking_connection,
                actor_peer_id=sender_id,
            )
            if sender_id:
                search_peer_ids = [sender_id, *(peer_ids or [])]
            else:
                search_peer_ids = peer_ids or None
            type_quotas = {
                "events": max(0, int(getattr(ov_cfg, "memory_recall_events_limit", 10))),
                "entities": max(0, int(getattr(ov_cfg, "memory_recall_entities_limit", 10))),
                "preferences": max(0, int(getattr(ov_cfg, "memory_recall_preferences_limit", 3))),
            }
            recall_max_chars = max(1, int(getattr(ov_cfg, "memory_recall_max_chars", 6500)))
            use_type_quota = not user_ids
            if use_type_quota:
                if getattr(client, "actor_peer_id", None):
                    result = await self._search_actor_peer_memories_by_type_quota(
                        query=current_message,
                        workspace_id=workspace_id,
                        openviking_connection=openviking_connection,
                        base_client=client,
                        peer_ids=search_peer_ids or [],
                        quotas=type_quotas,
                    )
                else:
                    result = await self._search_viking_memory_by_type_quota(
                        client=client,
                        query=current_message,
                        peer_ids=search_peer_ids,
                        quotas=type_quotas,
                    )
            else:
                result = await client.search_memory(
                    query=current_message,
                    user_ids=user_ids,
                    peer_ids=search_peer_ids,
                    limit=_LEGACY_MEMORY_RECALL_LIMIT + 5,
                )
            if not result:
                return ""
            result = [
                memory
                for memory in result
                if not self._get_uri(memory).rstrip("/").endswith("/profile.md")
            ]
            if not result:
                return ""
            if not use_type_quota:
                result = self._limit_memories(result, limit=_LEGACY_MEMORY_RECALL_LIMIT)

            async def read_memory_content(uri: str, level: str = "read") -> str:
                actor_peer_id = getattr(client, "actor_peer_id", None)
                memory_peer_id = self._peer_id_from_memory_uri(uri)
                if actor_peer_id and memory_peer_id and memory_peer_id != actor_peer_id:
                    peer_client = read_clients.get(memory_peer_id)
                    if not peer_client:
                        peer_client = await VikingClient.create(
                            agent_id=workspace_id,
                            connection=openviking_connection,
                            actor_peer_id=memory_peer_id,
                        )
                        read_clients[memory_peer_id] = peer_client
                    return await peer_client.read_content(uri, level=level)
                return await client.read_content(uri, level=level)

            # Log raw search results for debugging
            recall_strategy = "type_quota" if use_type_quota else "global"
            memory_list = []
            memory_list.append(f"user_memory[{len(result)}],strategy={recall_strategy}:")

            for i, mem in enumerate(result):
                uri = self._get_uri(mem)
                score = self._get_score(mem)
                memory_list.append(f"{i},{uri},{score}")
            raw_memories_log = "\n".join(memory_list)
            logger.info(f"[RAW_MEMORIES]\n{raw_memories_log}")
            user_memory = await self._parse_viking_memory(
                result,
                client,
                min_score=0.1,
                max_chars=recall_max_chars,
                full_limit=0 if use_type_quota else None,
                type_char_budgets=(
                    self._type_quota_char_budgets(recall_max_chars) if use_type_quota else None
                ),
                preference_full_limit=(
                    _TYPE_QUOTA_PREFERENCE_FULL_LIMIT if use_type_quota else 0
                ),
                include_uri_entries=True,
                read_content=read_memory_content,
            )
            return f"### user memories:\n{user_memory}"
        except Exception as e:
            logger.error(f"[READ_USER_MEMORY]: search error. {e}")
            return ""
        finally:
            for read_client in read_clients.values():
                try:
                    await read_client.close()
                except Exception as e:
                    logger.warning(f"Error closing VikingClient: {e}")
            if client:
                try:
                    await client.close()
                except Exception as e:
                    logger.warning(f"Error closing VikingClient: {e}")

    async def get_viking_experience_context(
        self,
        query: str,
        workspace_id: str,
        openviking_connection: dict[str, Any] | None = None,
        actor_peer_id: str | None = None,
    ) -> str:
        """用当前任务 query 检索 experience 记忆，注入到 system prompt。"""
        client = None
        try:
            ov_cfg = load_config().ov_server
            client = await VikingClient.create(
                agent_id=workspace_id,
                connection=openviking_connection,
                actor_peer_id=actor_peer_id,
            )
            experiences = await client.search_experiences(query, limit=ov_cfg.exp_recall_limit)
            logger.info(
                f"[READ_EXPERIENCE_MEMORY]: found {len(experiences)} experiences, query={query[:50]}"
            )
            for i, exp in enumerate(experiences):
                uri = exp.get("uri", "") if isinstance(exp, dict) else getattr(exp, "uri", "")
                score = exp.get("score", 0) if isinstance(exp, dict) else getattr(exp, "score", 0)
                logger.info(f"  {i},{uri},{score}")
            if not experiences:
                return ""
            return await self._parse_viking_memory(
                experiences, client, min_score=0.3, max_chars=ov_cfg.exp_recall_max_chars
            )
        except Exception as e:
            logger.error(f"[READ_EXPERIENCE_MEMORY]: error. {e}")
            return ""
        finally:
            if client:
                try:
                    await client.close()
                except Exception:
                    pass

    async def get_viking_user_profile(
        self,
        workspace_id: str,
        user_id: str | None,
        openviking_connection: dict[str, Any] | None = None,
        actor_peer_id: str | None = None,
    ) -> str:
        client = None
        try:
            client = await VikingClient.create(
                agent_id=workspace_id,
                connection=openviking_connection,
                actor_peer_id=actor_peer_id,
            )
            result = await client.read_user_profile(user_id)
            return result or ""
        except Exception as e:
            logger.error(f"[READ_USER_PROFILE]: user_id={user_id}, error. {e}")
            return ""
        finally:
            if client:
                try:
                    await client.close()
                except Exception as e:
                    logger.warning(f"Error closing VikingClient: {e}")

    async def get_viking_peer_profile(
        self,
        workspace_id: str,
        peer_id: str | None,
        openviking_connection: dict[str, Any] | None = None,
        actor_peer_id: str | None = None,
    ) -> str:
        if not peer_id:
            return ""

        client = None
        try:
            client = await VikingClient.create(
                agent_id=workspace_id,
                connection=openviking_connection,
                actor_peer_id=actor_peer_id or peer_id,
            )
            result = await client.read_peer_profile(peer_id)
            return result or ""
        except Exception as e:
            logger.error(f"[READ_PEER_PROFILE]: peer_id={peer_id}, error. {e}")
            return ""
        finally:
            if client:
                try:
                    await client.close()
                except Exception as e:
                    logger.warning(f"Error closing VikingClient: {e}")

    async def get_viking_peer_profiles(
        self,
        workspace_id: str,
        peer_ids: list[str],
        openviking_connection: dict[str, Any] | None = None,
        use_peer_actor_scope: bool = False,
    ) -> str:
        if not peer_ids:
            return ""

        client = None
        try:
            if not use_peer_actor_scope:
                client = await VikingClient.create(
                    agent_id=workspace_id,
                    connection=openviking_connection,
                )

            async def fetch_profile(peer_id: str) -> tuple[str, str]:
                peer_client = client
                should_close = False
                try:
                    if use_peer_actor_scope:
                        peer_client = await VikingClient.create(
                            agent_id=workspace_id,
                            connection=openviking_connection,
                            actor_peer_id=peer_id,
                        )
                        should_close = True
                    start_time = time.time()
                    profile = await peer_client.read_peer_profile(peer_id)
                    cost = round(time.time() - start_time, 2)
                    logger.info(
                        f"[READ_PEER_PROFILE]: peer_id={peer_id}, cost {cost}s, "
                        f"profile={profile[:50] if profile else 'None'}"
                    )
                    return (peer_id, profile or "")
                except Exception as e:
                    logger.error(f"[READ_PEER_PROFILE]: peer_id={peer_id}, error. {e}")
                    return (peer_id, "")
                finally:
                    if should_close and peer_client:
                        try:
                            await peer_client.close()
                        except Exception as e:
                            logger.warning(f"Error closing VikingClient: {e}")

            tasks = [fetch_profile(peer_id) for peer_id in peer_ids]
            results = await asyncio.gather(*tasks, return_exceptions=True)

            parts = []
            for result in results:
                if isinstance(result, Exception):
                    continue
                peer_id, profile = result
                if profile:
                    parts.append(f"## Peer profile for {peer_id}: \n{profile}")

            return "\n\n".join(parts) if parts else ""
        except Exception as e:
            logger.error(f"[READ_PEER_PROFILES]: error. {e}")
            return ""
        finally:
            if client:
                try:
                    await client.close()
                except Exception as e:
                    logger.warning(f"Error closing VikingClient: {e}")

    async def get_viking_user_profiles(
        self,
        workspace_id: str,
        user_ids: list[str],
        openviking_connection: dict[str, Any] | None = None,
        actor_peer_id: str | None = None,
    ) -> str:
        """Get multiple user profiles concurrently.

        Args:
            workspace_id: Workspace ID
            user_ids: List of user IDs to get profiles for

        Returns:
            Formatted string with all user profiles
        """
        if not user_ids:
            return ""

        client = None
        try:
            client = await VikingClient.create(
                agent_id=workspace_id,
                connection=openviking_connection,
                actor_peer_id=actor_peer_id,
            )

            async def fetch_profile(user_id: str) -> tuple[str, str]:
                """Fetch a single user profile."""
                try:
                    start_time = time.time()
                    profile = await client.read_user_profile(user_id)
                    cost = round(time.time() - start_time, 2)
                    logger.info(
                        f"[READ_USER_PROFILE]: user_id={user_id}, cost {cost}s, "
                        f"profile={profile[:50] if profile else 'None'}"
                    )
                    return (user_id, profile or "")
                except Exception as e:
                    logger.error(f"[READ_USER_PROFILE]: user_id={user_id}, error. {e}")
                    return (user_id, "")

            # Fetch all profiles concurrently
            tasks = [fetch_profile(user_id) for user_id in user_ids]
            results = await asyncio.gather(*tasks, return_exceptions=True)

            # Build the result string
            parts = []
            for result in results:
                if isinstance(result, Exception):
                    continue
                user_id, profile = result
                if profile:
                    parts.append(f"## User profile for {user_id}: \n{profile}")

            return "\n\n".join(parts) if parts else ""
        except Exception as e:
            logger.error(f"[READ_USER_PROFILES]: error. {e}")
            return ""
        finally:
            if client:
                try:
                    await client.close()
                except Exception as e:
                    logger.warning(f"Error closing VikingClient: {e}")
