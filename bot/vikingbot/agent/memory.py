"""Memory system for persistent agent memory."""

import asyncio
import time
from pathlib import Path
from typing import Any

from loguru import logger

from vikingbot.config.loader import load_config
from vikingbot.openviking_mount.ov_server import VikingClient
from vikingbot.utils.helpers import ensure_dir


class MemoryStore:
    """Two-layer memory: MEMORY.md (long-term facts) + HISTORY.md (grep-searchable log)."""

    def __init__(self, workspace: Path):
        self.memory_dir = ensure_dir(workspace / "memory")
        self.memory_file = self.memory_dir / "MEMORY.md"
        self.history_file = self.memory_dir / "HISTORY.md"

    def read_long_term(self) -> str:
        if self.memory_file.exists():
            return self.memory_file.read_text(encoding="utf-8")
        return ""

    async def _parse_viking_memory(
        self, result: Any, client: Any, min_score: float = 0.3, max_chars: int = 4000
    ) -> str:
        """Parse viking memory with score filtering and character limit.
        Automatically reads full content for memories above threshold.

        Args:
            result: Memory search results
            client: VikingClient instance to read content
            min_score: Minimum score threshold (default: 0.4)
            max_chars: Maximum character limit for output (default: 4000)

        Returns:
            Formatted memory string within character limit
        """
        if not result or len(result) == 0:
            return ""

        # Filter by min_score and sort by score descending
        def get_score(m):
            return m.get('score', 0) if isinstance(m, dict) else getattr(m, 'score', 0.0)
        def get_uri(m):
            return m.get('uri', '') if isinstance(m, dict) else getattr(m, 'uri', '')
        def get_abstract(m):
            return m.get('abstract', '') if isinstance(m, dict) else getattr(m, 'abstract', '')

        filtered_memories = [
            memory for memory in result if get_score(memory) >= min_score
        ]
        filtered_memories.sort(key=get_score, reverse=True)

        user_memories = []
        total_chars = 0
        seen_content_hashes = set()

        for idx, memory in enumerate(filtered_memories, start=1):
            uri = get_uri(memory)
            abstract = get_abstract(memory)
            score = get_score(memory)

            # First, try to build full memory with content
            content = ""
            try:
                content = await client.read_content(uri, level="read")
            except Exception as e:
                logger.warning(f"Failed to read content from {uri}: {e}")

            # Deduplicate by content hash (use content or abstract as key)
            content_to_hash = content or abstract
            content_hash = hash(content_to_hash)
            if content_to_hash and content_hash in seen_content_hashes:
                continue
            if content_to_hash:
                seen_content_hashes.add(content_hash)

            if content:
                # Try full version first (no abstract when content is present)
                full_memory_str = (
                    f'<memory index="{idx}" type="full">\n'
                    f"  <uri>{uri}</uri>\n"
                    f"  <score>{score}</score>\n"
                    f"  <content>{content}</content>\n"
                    f"</memory>"
                )
                full_chars = len(full_memory_str)
                if user_memories:
                    full_chars += 1

                if total_chars + full_chars <= max_chars:
                    user_memories.append(full_memory_str)
                    total_chars += full_chars
                else:
                    # Full version too big, use link-only version (always add)
                    link_only_str = (
                        f'<memory index="{idx}" type="link">\n'
                        f"  <uri>{uri}</uri>\n"
                        f"  <score>{score}</score>\n"
                        f"</memory>"
                    )
                    user_memories.append(link_only_str)
                    # Don't count link-only towards max_chars
            else:
                # No content available, use link-only version (always add)
                logger.info(f"Using link-only for {uri} (read failed or empty)")
                memory_str = (
                    f'<memory index="{idx}" type="link">\n'
                    f"  <uri>{uri}</uri>\n"
                    f"  <score>{score}</score>\n"
                    f"</memory>"
                )
                user_memories.append(memory_str)
                # Don't count link-only towards max_chars

        return "\n".join(user_memories)

    def write_long_term(self, content: str) -> None:
        self.memory_file.write_text(content, encoding="utf-8")

    def append_history(self, entry: str) -> None:
        with open(self.history_file, "a", encoding="utf-8") as f:
            f.write(entry.rstrip() + "\n\n")

    def get_memory_context(self) -> str:
        long_term = self.read_long_term()
        return f"## Long-term Memory\n{long_term}" if long_term else ""

    async def get_viking_memory_context(
        self, current_message: str, workspace_id: str, sender_id: str, user_ids: list[str] | None = None
    ) -> str:
        try:
            config = load_config().ov_server
            admin_user_id = config.admin_user_id
            # Use provided user_ids or fall back to sender_id
            search_user_ids = user_ids if user_ids else [sender_id]
            logger.info(f'workspace_id={workspace_id}')
            logger.info(f'user_ids={search_user_ids}')
            logger.info(f'admin_user_id={admin_user_id}')
            client = await VikingClient.create(agent_id=workspace_id)
            result = await client.search_memory(
                query=current_message, user_ids=search_user_ids, agent_user_id=admin_user_id, limit=30
            )
            if not result:
                return ""

            # Log raw search results for debugging
            memory_list = []
            memory_list.append(f"user_memory[{len(result['user_memory'])}]:")

            for i, mem in enumerate(result['user_memory']):
                uri = mem.get('uri', '') if isinstance(mem, dict) else getattr(mem, 'uri', '')
                score = mem.get('score', 0) if isinstance(mem, dict) else getattr(mem, 'score', 0)
                memory_list.append(f"{i},{uri},{score}")
            memory_list.append(f'agent_memory[{len(result['agent_memory'])}]:')
            for i, mem in enumerate(result['agent_memory']):
                uri = mem.get('uri', '') if isinstance(mem, dict) else getattr(mem, 'uri', '')
                score = mem.get('score', 0) if isinstance(mem, dict) else getattr(mem, 'score', 0)
                memory_list.append(f"{i},{uri},{score}")
            raw_memories_log = "\n".join(memory_list)
            logger.info(f"[RAW_MEMORIES]\n{raw_memories_log}")
            user_memory = await self._parse_viking_memory(
                result["user_memory"], client, min_score=0.1, max_chars=4000
            )
            agent_memory = await self._parse_viking_memory(
                result["agent_memory"], client, min_score=0.1, max_chars=2000
            )
            return f"### user memories:\n{user_memory}\n### agent memories:\n{agent_memory}"
        except Exception as e:
            logger.error(f"[READ_USER_MEMORY]: search error. {e}")
            return ""

    async def get_viking_user_profile(self, workspace_id: str, user_id: str) -> str:
        client = await VikingClient.create(agent_id=workspace_id)
        result = await client.read_user_profile(user_id)
        if not result:
            return ""
        return result

    async def get_viking_user_profiles(self, workspace_id: str, user_ids: list[str]) -> str:
        """Get multiple user profiles concurrently.

        Args:
            workspace_id: Workspace ID
            user_ids: List of user IDs to get profiles for

        Returns:
            Formatted string with all user profiles
        """
        if not user_ids:
            return ""

        client = await VikingClient.create(agent_id=workspace_id)

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
