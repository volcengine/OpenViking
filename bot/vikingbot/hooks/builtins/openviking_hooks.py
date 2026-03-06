from typing import Any

from loguru import logger
import re

from vikingbot.config.loader import get_data_dir
from ..base import Hook, HookContext
from ...session import Session

from vikingbot.config.loader import load_config
from vikingbot.config.schema import SessionKey

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

    async def _get_client(self, workspace_id: str) -> VikingClient:
        if not self._client:
            client = await VikingClient.create(workspace_id)
            self._client = client
        return self._client

    def _filter_messages_by_sender(self, messages: list[dict], allow_from: list[str]) -> list[dict]:
        """筛选出 sender_id 在 allow_from 列表中的消息"""
        if not allow_from:
            return []
        return [msg for msg in messages if msg.get("sender_id") in allow_from]

    def _get_channel_allow_from(self, session_key: SessionKey) -> list[str]:
        """根据 session_id 获取对应频道的 allow_from 配置"""
        config = load_config()
        if not session_key or not config.channels:
            return []

        # 查找对应类型的 channel config
        for channel_config in config.channels:
            if hasattr(channel_config, "type") and channel_config.type == session_key.channel_id:
                if hasattr(channel_config, "allow_from"):
                    return channel_config.allow_from
        return []

    async def execute(self, context: HookContext, **kwargs) -> Any:
        vikingbot_session: Session = kwargs.get("session", {})
        session_id = context.session_key.safe_name()

        try:
            allow_from = self._get_channel_allow_from(session_id)
            filtered_messages = self._filter_messages_by_sender(
                vikingbot_session.messages, allow_from
            )

            if not filtered_messages:
                logger.info(
                    f"No messages to commit openviking for session {session_id} (allow_from filter applied)"
                )
                return {"success": True, "message": "No messages matched allow_from filter"}

            client = await self._get_client(context.workspace_id)
            result = await client.commit(
                session_id, filtered_messages, load_config().ov_server.admin_user_id
            )
            return result
        except Exception as e:
            logger.exception(f"Failed to add message to OpenViking: {e}")
            return {"success": False, "error": str(e)}


class OpenVikingPostCallHook(Hook):
    name = "openviking_post_call"
    is_sync = True

    def __init__(self):
        self._client = None

    async def _get_client(self, workspace_id: str) -> VikingClient:
        if not self._client:
            client = await VikingClient.create(workspace_id)
            self._client = client
        return self._client

    async def _read_skill_memory(self, workspace_id: str, skill_name: str) -> str:
        ov_client = await self._get_client(workspace_id)
        config = load_config()
        openviking_config = config.ov_server
        if not skill_name:
            return ""
        try:
            if openviking_config.mode == "local":
                skill_memory_uri = f"viking://agent/ffb1327b18bf/memories/skills/{skill_name}.md"
            else:
                agent_space_name = ov_client.get_agent_space_name(openviking_config.admin_user_id)
                skill_memory_uri = (
                    f"viking://agent/{agent_space_name}/memories/skills/{skill_name}.md"
                )
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

                    agent_space_name = context.workspace_id
                    # logger.debug(f"agent_space_name={agent_space_name}")

                    skill_memory = await self._read_skill_memory(agent_space_name, skill_name)
                    # logger.debug(f"skill_memory={skill_memory}")
                    if skill_memory:
                        result = f"{result}{skill_memory}"

        return {"tool_name": tool_name, "params": params, "result": result}


hooks = {"message.compact": [OpenVikingCompactHook()], "tool.post_call": [OpenVikingPostCallHook()]}
