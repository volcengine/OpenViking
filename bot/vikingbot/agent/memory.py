"""Memory system for persistent agent memory."""

from pathlib import Path
from typing import Any
from loguru import logger
import time

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

    def _parse_viking_memory(
        self, result: Any, min_score: float = 0.4, max_chars: int = 4000
    ) -> str:
        """Parse viking memory with score filtering and character limit.

        Args:
            result: Memory search results
            min_score: Minimum score threshold (default: 0.4)
            max_chars: Maximum character limit for output (default: 4000)

        Returns:
            Formatted memory string within character limit
        """
        if not result or len(result) == 0:
            return ""

        # Filter by min_score and sort by score descending
        filtered_memories = [
            memory for memory in result if getattr(memory, "score", 0.0) >= min_score
        ]
        filtered_memories.sort(key=lambda m: getattr(m, "score", 0.0), reverse=True)

        user_memories = []
        total_chars = 0

        for idx, memory in enumerate(filtered_memories, start=1):
            memory_str = (
                f'<memory index="{idx}">\n'
                f"  <abstract>{getattr(memory, 'abstract', '')}</abstract>\n"
                f"  <uri>{getattr(memory, 'uri', '')}</uri>\n"
                f"  <score>{getattr(memory, 'score', 0.0)}</score>\n"
                f"</memory>"
            )

            # Check if adding this memory would exceed the limit
            memory_chars = len(memory_str)
            if user_memories:
                # Account for newline separator between memories
                memory_chars += 1

            if total_chars + memory_chars > max_chars:
                break

            user_memories.append(memory_str)
            total_chars += memory_chars

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
        self, current_message: str, workspace_id: str, sender_id: str
    ) -> str:
        try:
            config = load_config().ov_server
            admin_user_id = config.admin_user_id
            user_id = sender_id
            client = await VikingClient.create(agent_id=workspace_id)
            result = await client.search_memory(
                query=current_message, user_id=user_id, agent_user_id=admin_user_id, limit=20
            )
            if not result:
                return ""
            user_memory = self._parse_viking_memory(result["user_memory"])
            agent_memory = self._parse_viking_memory(result["agent_memory"])
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
