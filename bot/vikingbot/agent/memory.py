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

    def _parse_viking_memory(self, result: Any) -> str:
        if result and len(result) > 0:
            user_memories = []
            for idx, memory in enumerate(result, start=1):
                user_memories.append(
                    f"<memory index=\"{idx}\">\n"
                    f"  <abstract>{getattr(memory, 'abstract', '')}</abstract>\n"
                    f"  <uri>{getattr(memory, 'uri', '')}</uri>\n"
                    f"  <is_leaf>{getattr(memory, 'is_leaf', False)}</is_leaf>\n"
                    f"  <score>{getattr(memory, 'score', 0.0)}</score>\n"
                    f"</memory>"
                )
            return "\n".join(user_memories)
        return ""

    def write_long_term(self, content: str) -> None:
        self.memory_file.write_text(content, encoding="utf-8")

    def append_history(self, entry: str) -> None:
        with open(self.history_file, "a", encoding="utf-8") as f:
            f.write(entry.rstrip() + "\n\n")

    def get_memory_context(self) -> str:
        long_term = self.read_long_term()
        return f"## Long-term Memory\n{long_term}" if long_term else ""

    async def get_viking_memory_context(self, current_message: str, workspace_id: str, sender_id: str) -> str:
        try:
            config = load_config().ov_server
            admin_user_id = config.admin_user_id
            user_id = sender_id if config.mode == "remote" else admin_user_id
            client = await VikingClient.create(agent_id=workspace_id)
            result = await client.search_memory(query=current_message, user_id=user_id, agent_user_id=admin_user_id, limit=5)
            if not result:
                return ""
            user_memory = self._parse_viking_memory(result["user_memory"])
            agent_memory = self._parse_viking_memory(result["agent_memory"])
            return (
                f"### user memories:\n{user_memory}\n"
                f"### agent memories:\n{agent_memory}"
            )
        except Exception as e:
            logger.error(f"[READ_USER_MEMORY]: search error. {e}")
            return ""

    async def get_viking_user_profile(self, workspace_id: str, user_id: str) -> str:
        client = await VikingClient.create(agent_id=workspace_id)
        result = await client.read_user_profile(user_id)
        if not result:
            return ""
        return result