"""Memory system for persistent agent memory."""

from vikingbot.config.loader import load_config
from pathlib import Path
from typing import Any

from vikingbot.openviking_mount.ov_server import VikingClient
from vikingbot.utils.helpers import ensure_dir


class MemoryStore:
    """Two-layer memory: MEMORY.md (long-term facts) + HISTORY.md (grep-searchable log)."""

    def __init__(self, workspace: Path):
        self.memory_dir = ensure_dir(workspace / "memory")
        self.memory_file = self.memory_dir / "MEMORY.md"
        self.history_file = self.memory_dir / "HISTORY.md"
        config = load_config()
        ov_config = config.openviking
        self.user_id = ov_config.user_id if ov_config.mode == "remote" else "default"

    def read_long_term(self) -> str:
        if self.memory_file.exists():
            return self.memory_file.read_text(encoding="utf-8")
        return ""

    def _parse_viking_memory(self, result: Any) -> str:
        if result and len(result) > 0:
            user_memories = []
            for idx, memory in enumerate(result, start=1):
                user_memories.append(
                    f"{idx}. {getattr(memory, 'abstract', '')}; "
                    f"uri: {getattr(memory, 'uri', '')}; "
                    f"isDir: {getattr(memory, 'is_leaf', False)}; "
                    f"related score: {getattr(memory, 'score', 0.0)}"
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

    async def get_viking_memory_context(self, current_message: str, sandbox_key: str) -> str:
        client = await VikingClient.create(agent_id=sandbox_key)
        result = await client.search_memory(current_message, limit=5)
        if not result:
            return ""
        user_memory = self._parse_viking_memory(result["user_memory"])
        agent_memory = self._parse_viking_memory(result["agent_memory"])
        return (
            f"## Related memories.Using tools to read more details.\n"
            f"### user memories:\n{user_memory}\n"
            f"### agent memories:\n{agent_memory}"
        )

    async def get_viking_user_profile(self, sandbox_key: str) -> str:
        client = await VikingClient.create(agent_id=sandbox_key)
        result = await client.read_content(
            uri=f"viking://user/{self.user_id}/memories/profile.md", level="read"
        )
        if not result:
            return ""
        return f"## User Information\n{result}"
