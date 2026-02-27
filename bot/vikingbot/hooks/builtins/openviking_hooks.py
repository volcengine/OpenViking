from typing import Any

from loguru import logger
import re

from vikingbot.config.loader import get_data_dir
from ..base import Hook, HookContext
from ...session import Session

from vikingbot.config.loader import load_config

try:
    from vikingbot.openviking_mount.ov_server import VikingClient
    import openviking as ov

    HAS_OPENVIKING = True
except Exception:
    HAS_OPENVIKING = False
    VikingClient = None
    ov = None


class OpenVikingCompactHook(Hook):
    name = "openviking_compact"

    def __init__(self):
        self._client = None

    async def _get_client(self, sandbox_key: str) -> VikingClient:
        if not self._client:
            client = await VikingClient.create(sandbox_key)
            self._client = client
        return self._client

    async def execute(self, context: HookContext, **kwargs) -> Any:
        vikingbot_session: Session = kwargs.get("session", {})
        session_id = context.session_id
        try:
            client = await self._get_client(context.sandbox_key)
            result = await client.commit(session_id, vikingbot_session.messages)
            return result
        except Exception as e:
            logger.exception(f"Failed to add message to OpenViking: {e}")
            return {"success": False, "error": str(e)}


class OpenVikingPostCallHook(Hook):
    name = "openviking_post_call"
    is_sync = True

    def __init__(self):
        self._client = None

    async def _get_client(self, sandbox_key: str) -> VikingClient:
        if not self._client:
            client = await VikingClient.create(sandbox_key)
            self._client = client
        return self._client

    async def _read_skill_memory(self, sandbox_key: str, skill_name: str) -> str:
        ov_client = await self._get_client(sandbox_key)
        config = load_config()
        openviking_config = config.openviking
        if not skill_name or (not sandbox_key and openviking_config.mode != "local"):
            return ""
        try:
            if openviking_config.mode == "local":
                skill_memory_uri = f"viking://agent/ffb1327b18bf/memories/skills/{skill_name}.md"
            else:
                skill_memory_uri = (
                    f"viking://agent/{ov_client.agent_space_name}/memories/skills/{skill_name}.md"
                )
            # logger.warning(f"skill_memory_uri={skill_memory_uri}")
            content = await ov_client.read_content(skill_memory_uri, level="read")
            # logger.warning(f"content={content}")
            return f"\n\n---\n## Skill Memory\n{content}" if content else ""
        except Exception as e:
            logger.warning(f"Failed to read skill memory for {skill_name}: {e}")
            return ""

    async def execute(self, context: HookContext, tool_name, params, result) -> Any:
        if tool_name == "read_file":
            if result and not isinstance(result, Exception):
                match = re.search(r"^---\s*\nname:\s*(.+?)\s*\n", result, re.MULTILINE)
                if match:
                    skill_name = match.group(1).strip()
                    # logger.debug(f"skill_name={skill_name}")

                    agent_space_name = context.sandbox_key
                    # logger.debug(f"agent_space_name={agent_space_name}")

                    skill_memory = await self._read_skill_memory(agent_space_name, skill_name)
                    # logger.debug(f"skill_memory={skill_memory}")
                    if skill_memory:
                        result = f"{result}{skill_memory}"

        return {"tool_name": tool_name, "params": params, "result": result}


hooks = {"message.compact": [OpenVikingCompactHook()], "tool.post_call": [OpenVikingPostCallHook()]}
