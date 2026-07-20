"""Memory system for persistent agent memory."""

import asyncio
import json
import re
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

from loguru import logger

from vikingbot.config.loader import load_config
from vikingbot.openviking_mount.ov_server import VikingClient
from vikingbot.utils.helpers import ensure_dir

if TYPE_CHECKING:
    from vikingbot.config.schema import Config

_LEGACY_MEMORY_RECALL_LIMIT = 30
_TYPE_QUOTA_MEMORY_TYPES = ("events", "entities", "preferences")
_TYPE_QUOTA_EVENT_CHAR_RATIO = 0.75
_TYPE_QUOTA_PREFERENCE_FULL_LIMIT = 1
_MEMORY_TYPE_DESCRIPTIONS = {
    "events": ("Event memories. The URI path includes the event date."),
    "entities": (
        "Entity and topic memories. Use them for stable facts, attributes, "
        "relationships, and background about people, hobbies, places, or concepts."
    ),
    "preferences": (
        "Preference memories. Use them for likes, dislikes, habits, recurring choices, "
        "and long-term personal tendencies."
    ),
    "cases": (
        "Structured training case memories. Use them as scenario/rubric context and "
        "follow direct deterministic links to experiences when available."
    ),
    "experiences": (
        "Reusable agent experiences distilled from prior tasks. Apply them only when their "
        "Situation and policy gates match the current task."
    ),
    "trajectories": (
        "Diagnostic trajectory memories from evaluated rollouts. Use them as read-only "
        "failure/success deltas for similar tasks."
    ),
}


class MemoryStore:
    """Two-layer memory: MEMORY.md (long-term facts) + HISTORY.md (grep-searchable log)."""

    def __init__(self, workspace: Path, config: "Config | None" = None):
        self.memory_dir = ensure_dir(workspace / "memory")
        self.memory_file = self.memory_dir / "MEMORY.md"
        self.history_file = self.memory_dir / "HISTORY.md"
        self._config = config

    def _get_config(self) -> "Config":
        if self._config is None:
            self._config = load_config()
        return self._config

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
    def _filename_from_uri(uri: str) -> str:
        """Extract the filename (basename) from a memory URI."""
        stripped = uri.rstrip("/")
        if not stripped:
            return ""
        return stripped.rsplit("/", 1)[-1]

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
                memory for memory in memories if cls._infer_memory_type(memory) == memory_type
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
        filename = MemoryStore._filename_from_uri(uri)
        return (
            f'<memory index="{idx}" type="full">\n'
            f"  <uri>{uri}</uri>\n"
            f"  <filename>{filename}</filename>\n"
            f"  <score>{score}</score>\n"
            f"  <content>{content}</content>\n"
            f"</memory>"
        )

    @staticmethod
    def _format_summary_memory(idx: int, uri: str, score: float, summary: str) -> str:
        filename = MemoryStore._filename_from_uri(uri)
        return (
            f'<memory index="{idx}" type="summary">\n'
            f"  <uri>{uri}</uri>\n"
            f"  <filename>{filename}</filename>\n"
            f"  <score>{score}</score>\n"
            f"  <summary>{summary}</summary>\n"
            f"</memory>"
        )

    @staticmethod
    def _format_uri_memory(idx: int, uri: str, score: float) -> str:
        filename = MemoryStore._filename_from_uri(uri)
        return (
            f'<memory index="{idx}" type="uri">\n'
            f"  <uri>{uri}</uri>\n"
            f"  <filename>{filename}</filename>\n"
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
                should_try_full = (memory_type in type_char_budgets) or (
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
                    config=self._get_config(),
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
            config = self._get_config()
            ov_cfg = config.ov_server
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
                config=config,
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
                            config=config,
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
                preference_full_limit=(_TYPE_QUOTA_PREFERENCE_FULL_LIMIT if use_type_quota else 0),
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
        case_lookup: dict[str, Any] | None = None,
    ) -> str:
        """用当前任务 query 检索 experience 记忆，注入到 system prompt。"""
        content, _ = await self.get_viking_experience_reminder(
            query=query,
            workspace_id=workspace_id,
            exclude_uris=None,
            openviking_connection=openviking_connection,
            case_lookup=case_lookup,
        )
        return content

    async def get_viking_experience_reminder(
        self,
        query: str,
        workspace_id: str,
        exclude_uris: list[str] | None = None,
        openviking_connection: dict[str, Any] | None = None,
        case_lookup: dict[str, Any] | None = None,
    ) -> tuple[str, list[str]]:
        """检索 experience 记忆并排除已召回过的 URI。

        Returns:
            (formatted_content, recalled_uris) — 格式化后的记忆块和实际命中的 URI 列表。
            无命中时返回 ("", [])。
        """
        if case_lookup:
            return await self._get_linked_case_experience_content(
                query=query,
                workspace_id=workspace_id,
                case_lookup=case_lookup,
                openviking_connection=openviking_connection,
                exclude_uris=exclude_uris,
            )
        client = None
        try:
            config = self._get_config()
            ov_cfg = config.ov_server
            client = await VikingClient.create(
                agent_id=workspace_id,
                connection=openviking_connection,
                config=config,
            )
            case_limit = max(0, int(getattr(ov_cfg, "case_recall_limit", 0) or 0))
            if case_lookup:
                cases = await self._find_cases_by_lookup(
                    client,
                    case_lookup,
                    limit=max(case_limit, 1),
                    fallback_query=query,
                )
                logger.info(
                    f"[READ_CASE_MEMORY]: exact lookup found {len(cases)} cases, "
                    f"lookup={case_lookup}, query={query[:50]}"
                )
            else:
                cases = await self._search_memory_type(
                    client,
                    query,
                    memory_type="cases",
                    limit=case_limit,
                )
                logger.info(f"[READ_CASE_MEMORY]: found {len(cases)} cases, query={query[:50]}")
            top_case = cases[:1]
            linked_experiences = await self._linked_experiences_from_cases(
                top_case,
                client,
                limit=0,
            )
            experiences = self._dedupe_memories(linked_experiences)
            logger.info(
                f"[READ_EXPERIENCE_MEMORY]: found {len(linked_experiences)} linked experiences "
                f"from top1 case, query={query[:50]}"
            )
            for i, case in enumerate(cases):
                uri = case.get("uri", "") if isinstance(case, dict) else getattr(case, "uri", "")
                score = (
                    case.get("score", 0) if isinstance(case, dict) else getattr(case, "score", 0)
                )
                logger.info(f"  case {i},{uri},{score}")
            for i, exp in enumerate(experiences):
                uri = exp.get("uri", "") if isinstance(exp, dict) else getattr(exp, "uri", "")
                score = exp.get("score", 0) if isinstance(exp, dict) else getattr(exp, "score", 0)
                logger.info(f"  {i},{uri},{score}")
            if not experiences:
                return "", []

            # 过滤掉已召回过的 URI。case 只作为路由入口，不注入上下文。
            if exclude_uris:
                exclude_set = set(exclude_uris)
                experiences = [exp for exp in experiences if self._get_uri(exp) not in exclude_set]
                logger.info(
                    f"[READ_EXPERIENCE_MEMORY]: after exclude {len(exclude_set)} uris, "
                    f"{len(experiences)} experiences remaining"
                )
                if not experiences:
                    return "", []

            recall_max_chars = max(1, int(ov_cfg.exp_recall_max_chars))
            content = await self._parse_viking_memory(
                experiences,
                client,
                min_score=0.0,
                max_chars=recall_max_chars,
                full_limit=len(experiences),
                include_uri_entries=False,
            )

            recalled_uris = [
                self._get_uri(exp) for exp in experiences if self._get_score(exp) >= 0.0
            ]

            return content, recalled_uris
        except Exception as e:
            logger.error(f"[READ_EXPERIENCE_MEMORY]: error. {e}")
            return "", []
        finally:
            if client:
                try:
                    await client.close()
                except Exception:
                    pass

    async def _get_linked_case_experience_content(
        self,
        *,
        query: str,
        workspace_id: str,
        case_lookup: dict[str, Any],
        openviking_connection: dict[str, Any] | None = None,
        exclude_uris: list[str] | None = None,
    ) -> tuple[str, list[str]]:
        client = None
        try:
            config = self._get_config()
            ov_cfg = config.ov_server
            client = await VikingClient.create(
                agent_id=workspace_id,
                connection=openviking_connection,
                config=config,
            )
            case_limit = max(1, int(getattr(ov_cfg, "case_recall_limit", 0) or 1))
            cases = await self._find_cases_by_lookup(
                client,
                case_lookup,
                limit=case_limit,
                fallback_query=query,
            )
            logger.info(
                f"[READ_TASK_CASE_EXP]: found {len(cases)} exact cases, "
                f"lookup={case_lookup}, query={query[:50]}"
            )
            linked_experiences = await self._linked_experiences_from_cases(
                cases[:1],
                client,
                limit=0,
            )
            experiences = self._dedupe_memories(linked_experiences)
            if exclude_uris:
                exclude_set = set(exclude_uris)
                experiences = [exp for exp in experiences if self._get_uri(exp) not in exclude_set]
            if not experiences:
                return "", []
            recall_max_chars = max(1, int(getattr(ov_cfg, "exp_recall_max_chars", 10000)))
            content = await self._parse_viking_memory(
                experiences,
                client,
                min_score=0.0,
                max_chars=recall_max_chars,
                full_limit=len(experiences),
                include_uri_entries=False,
            )
            recalled_uris = [
                self._get_uri(exp) for exp in experiences if self._get_score(exp) >= 0.0
            ]
            return content, recalled_uris
        except Exception as e:
            logger.error(f"[READ_TASK_CASE_EXP]: error. {e}")
            return "", []
        finally:
            if client:
                try:
                    await client.close()
                except Exception:
                    pass

    async def _find_cases_by_lookup(
        self,
        client: Any,
        case_lookup: dict[str, Any],
        *,
        limit: int,
        fallback_query: str,
    ) -> list[Any]:
        """Find the current task's case by exact structured identity.

        Tau2 passes task identity (domain/split/task_id/task_no) from the runner.
        We may use search only to enumerate candidates, but every returned case
        must pass an exact MEMORY_FIELDS/input match before its links are used.
        """
        lookup = self._normalize_case_lookup(case_lookup)
        if not lookup:
            return []

        target_uri = f"viking://user/{client.admin_user_id}/memories/cases/"
        matched = await self._read_exact_case_uri_candidates(
            client,
            lookup,
            base_uri=target_uri,
            limit=limit,
        )
        if matched:
            return matched
        if lookup.get("strict"):
            # Strict exact lookup is for benchmark/eval or other controlled
            # tasks where injecting a semantically similar but different case
            # is worse than recalling nothing.
            return []

        candidates: list[Any] = []
        seen: set[str] = set()
        for query_value in self._case_lookup_queries(lookup, fallback_query=fallback_query):
            remaining = max(1, int(limit or 1) * 5)
            try:
                result = await client.search(
                    query=query_value,
                    target_uri=target_uri,
                    limit=remaining,
                )
            except Exception as exc:
                logger.warning(f"[READ_CASE_MEMORY]: exact lookup candidate search error. {exc}")
                continue
            for memory in self._extract_memories(result):
                uri = self._get_uri(memory)
                if not uri or uri in seen:
                    continue
                seen.add(uri)
                candidates.append(memory)

        matched = []
        for case in candidates:
            uri = self._get_uri(case)
            if not uri:
                continue
            try:
                content = await client.read_content(uri, level="read")
            except Exception as exc:
                logger.warning(f"Failed to read case content from {uri}: {exc}")
                continue
            if not self._case_matches_lookup(content, lookup, uri=uri):
                continue
            matched.append(self._with_recall_metadata(case, "cases", len(matched) + 1))
            if len(matched) >= max(1, int(limit or 1)):
                break
        return matched

    async def _read_exact_case_uri_candidates(
        self,
        client: Any,
        lookup: dict[str, str],
        *,
        base_uri: str,
        limit: int,
    ) -> list[Any]:
        matched: list[Any] = []
        for uri in self._case_uri_candidates(base_uri, lookup):
            try:
                content = await client.read_content(uri, level="read")
            except Exception as exc:
                logger.warning(f"Failed to read exact case content from {uri}: {exc}")
                continue
            if not content or not self._case_matches_lookup(content, lookup, uri=uri):
                continue
            matched.append(
                {
                    "uri": uri,
                    "score": 1.0,
                    "abstract": "",
                    "_recall_type": "cases",
                    "_recall_rank": len(matched) + 1,
                    "_matched_by": "exact_case_uri",
                }
            )
            if len(matched) >= max(1, int(limit or 1)):
                break
        return matched

    @classmethod
    def _case_uri_candidates(cls, base_uri: str, lookup: dict[str, Any]) -> list[str]:
        names: list[str] = []
        for value in lookup.get("case_names") or []:
            if value:
                names.append(str(value))
        for key in ("case_name", "original_case_name"):
            value = lookup.get(key)
            if value:
                names.append(str(value))
        data_split = lookup.get("data_split")
        task_no = lookup.get("task_no")
        if data_split and task_no:
            names.append(f"tau2_{data_split}_{task_no}")
        uris: list[str] = []
        for name in names:
            filename = cls._safe_case_filename(name)
            if not filename:
                continue
            uri = f"{base_uri.rstrip('/')}/{filename}"
            if uri not in uris:
                uris.append(uri)
        return uris

    @staticmethod
    def _safe_case_filename(name: str) -> str:
        value = str(name or "").strip().strip("/")
        if not value or "/" in value or "\\" in value:
            return ""
        return value if value.endswith(".md") else f"{value}.md"

    @staticmethod
    def _normalize_case_lookup(case_lookup: dict[str, Any] | None) -> dict[str, Any]:
        if not isinstance(case_lookup, dict):
            return {}
        normalized: dict[str, Any] = {}
        for key in (
            "benchmark",
            "domain",
            "split",
            "data_split",
            "task_id",
            "task_no",
            "case_name",
            "task_signature",
            "original_case_name",
        ):
            value = case_lookup.get(key)
            if value is None:
                continue
            value_str = str(value).strip()
            if value_str:
                normalized[key] = value_str

        case_names = case_lookup.get("case_names")
        if isinstance(case_names, (list, tuple)):
            normalized["case_names"] = [
                str(value).strip()
                for value in case_names
                if value is not None and str(value).strip()
            ]

        expected_fields = case_lookup.get("expected_fields")
        if isinstance(expected_fields, dict):
            normalized["expected_fields"] = {
                str(key).strip(): str(value).strip()
                for key, value in expected_fields.items()
                if str(key).strip() and value is not None
            }

        if "strict" in case_lookup:
            normalized["strict"] = bool(case_lookup.get("strict"))
        elif "allow_query_fallback" in case_lookup:
            normalized["strict"] = not bool(case_lookup.get("allow_query_fallback"))
        else:
            normalized["strict"] = False
        return normalized

    @classmethod
    def _case_lookup_queries(cls, lookup: dict[str, Any], *, fallback_query: str) -> list[str]:
        queries: list[str] = []
        for value in lookup.get("case_names") or []:
            if value:
                queries.append(str(value))
        for key in ("case_name", "original_case_name", "task_signature"):
            value = lookup.get(key)
            if value:
                queries.append(str(value))
        domain = lookup.get("domain")
        split = lookup.get("split")
        task_id = lookup.get("task_id")
        task_no = lookup.get("task_no")
        data_split = lookup.get("data_split") or (f"{domain}_{split}" if domain and split else "")
        if domain and split and task_id:
            queries.append(f"{domain}:{split}:{task_id}")
        if data_split and task_no:
            queries.append(f"{data_split}_{task_no}")
        if data_split and task_id:
            queries.append(f"{data_split} task_id {task_id}")
        if fallback_query:
            queries.append(fallback_query)

        deduped: list[str] = []
        for query in queries:
            query = str(query or "").strip()
            if query and query not in deduped:
                deduped.append(query)
        return deduped

    @classmethod
    def _case_matches_lookup(
        cls, raw_content: str, lookup: dict[str, Any], *, uri: str = ""
    ) -> bool:
        try:
            from openviking.session.memory.utils.memory_file_utils import MemoryFileUtils

            memory_file = MemoryFileUtils.read(raw_content or "", uri=uri or None)
        except Exception:
            return False

        fields = dict(memory_file.extra_fields or {})
        if not fields:
            fields = cls._case_fields_from_markdown(memory_file.content or raw_content, uri=uri)
        case_input = cls._parse_json_object(fields.get("input"))
        case_name = str(fields.get("case_name") or cls._filename_from_uri(uri).removesuffix(".md"))
        task_signature = str(fields.get("task_signature") or "")

        accepted_names = [str(value) for value in lookup.get("case_names") or [] if value]
        if lookup.get("case_name"):
            accepted_names.append(str(lookup["case_name"]))
        if lookup.get("original_case_name"):
            accepted_names.append(str(lookup["original_case_name"]))
        case_names = {case_name, cls._filename_from_uri(uri).removesuffix(".md")}
        if accepted_names and not case_names.intersection(accepted_names):
            return False
        if lookup.get("task_signature") and str(lookup["task_signature"]) != task_signature:
            return False

        expected_fields = dict(lookup.get("expected_fields") or {})
        if not expected_fields:
            for key in ("domain", "task_id", "split", "data_split", "task_no"):
                expected = lookup.get(key)
                if expected:
                    expected_fields[f"input.{key}"] = expected

        document = {"fields": fields, "input": case_input}
        for path, expected in expected_fields.items():
            actual = cls._get_dotted_value(document, str(path))
            if str(actual if actual is not None else "").strip() != str(expected).strip():
                return False
        return True

    @staticmethod
    def _get_dotted_value(document: dict[str, Any], path: str) -> Any:
        current: Any = document
        for part in str(path or "").split("."):
            if not part:
                continue
            if isinstance(current, dict) and part in current:
                current = current[part]
            else:
                return None
        return current

    @staticmethod
    def _parse_json_object(value: Any) -> dict[str, Any]:
        if isinstance(value, dict):
            return value
        if not isinstance(value, str) or not value.strip():
            return {}
        try:
            parsed = json.loads(value)
        except Exception:
            return {}
        return parsed if isinstance(parsed, dict) else {}

    async def _linked_experiences_from_cases(
        self,
        cases: list[Any],
        client: Any,
        *,
        limit: int,
    ) -> list[Any]:
        """Read direct case -> experience links from the visible case markdown."""
        if not cases:
            return []
        max_links = int(limit or 0)

        linked: list[Any] = []
        seen: set[str] = set()
        for case in cases:
            case_uri = self._get_uri(case)
            if not case_uri:
                continue
            try:
                content = await client.read_content(case_uri, level="read")
            except Exception as exc:
                logger.warning(f"Failed to read case content from {case_uri}: {exc}")
                continue
            for exp_uri in self._extract_linked_experience_uris(content, source_uri=case_uri):
                if exp_uri in seen:
                    continue
                seen.add(exp_uri)
                linked.append(
                    {
                        "uri": exp_uri,
                        "score": self._get_score(case),
                        "abstract": "",
                        "_recall_type": "experiences",
                        "_recall_rank": len(linked) + 1,
                        "_linked_from_case_uri": case_uri,
                    }
                )
                if max_links > 0 and len(linked) >= max_links:
                    return linked
        return linked

    @classmethod
    def _case_fields_from_markdown(cls, content: str, *, uri: str = "") -> dict[str, str]:
        fields = {"case_name": cls._filename_from_uri(uri).removesuffix(".md")}
        title = re.search(r"(?m)^#\s+(.+?)\s*$", content or "")
        if title:
            fields["case_name"] = title.group(1).strip()
        for field, heading in (
            ("task_signature", "Task Signature"),
            ("input", "Input"),
            ("rubric", "Rubric"),
            ("evidence", "Evidence"),
        ):
            value = cls._markdown_section(content, heading)
            if value:
                fields[field] = value
        return fields

    @staticmethod
    def _markdown_section(content: str, heading: str) -> str:
        match = re.search(
            rf"(?ims)^##\s+{re.escape(heading)}\s*\n(.*?)(?=^##\s+|\Z)",
            content or "",
        )
        return match.group(1).strip() if match else ""

    @classmethod
    def _extract_linked_experience_uris(cls, content: str, *, source_uri: str = "") -> list[str]:
        section = cls._markdown_section(content, "Linked Experiences")
        if not section:
            return []

        targets = re.findall(r"\[[^\]]+\]\(([^)\s]+)\)", section)
        if not targets:
            targets = [
                line.lstrip("- ").strip()
                for line in section.splitlines()
                if line.strip().startswith("- ")
            ]

        uris: list[str] = []
        for target in targets:
            uri = cls._resolve_case_link_uri(target, source_uri=source_uri)
            if "/memories/experiences/" in uri and uri not in uris:
                uris.append(uri)
        return uris

    @staticmethod
    def _resolve_case_link_uri(target: str, *, source_uri: str) -> str:
        import posixpath

        target = str(target or "").strip()
        if not target:
            return ""
        if "://" in target:
            return target
        if "/" not in target:
            target = f"../experiences/{target.removesuffix('.md')}.md"
        if not source_uri.startswith("viking://"):
            return target
        source_dir = source_uri.removeprefix("viking://").rsplit("/", 1)[0]
        return "viking://" + posixpath.normpath(f"{source_dir}/{target}")

    async def _search_memory_type(
        self,
        client: Any,
        query: str,
        *,
        memory_type: str,
        limit: int,
    ) -> list[Any]:
        if limit <= 0:
            return []
        try:
            target_uri = f"viking://user/{client.admin_user_id}/memories/{memory_type}/"
            result = await client.search(query=query, target_uri=target_uri, limit=limit)
        except Exception as e:
            logger.warning(f"[READ_{memory_type.upper()}_MEMORY]: error. {e}")
            return []
        memories = result.get("memories", []) if isinstance(result, dict) else []
        return [
            self._with_recall_metadata(memory, memory_type, rank)
            for rank, memory in enumerate(memories, start=1)
        ]

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
                config=self._get_config(),
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
                    config=self._get_config(),
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
                            config=self._get_config(),
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
