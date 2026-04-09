import re
import asyncio
from typing import Any
from collections import defaultdict

from loguru import logger

from vikingbot.config.loader import load_config
from vikingbot.config.schema import SessionKey, AgentMemoryMode

from ...session import Session
from ..base import Hook, HookContext

try:
    import openviking as ov
    from vikingbot.openviking_mount.ov_server import VikingClient

    HAS_OPENVIKING = True
except Exception:
    HAS_OPENVIKING = False
    VikingClient = None
    ov = None

# Global singleton client
_global_client: VikingClient | None = None


async def get_global_client() -> VikingClient:
    """Get or create the global singleton VikingClient."""
    global _global_client
    if _global_client is None:
        _global_client = await VikingClient.create(None)
    return _global_client


class OpenVikingCompactHook(Hook):
    name = "openviking_compact"

    async def _get_client(self, workspace_id: str) -> VikingClient:
        # Use global singleton client
        return await get_global_client()


    async def execute(self, context: HookContext, **kwargs) -> Any:
        vikingbot_session: Session = kwargs.get("session", {})
        session_id = context.session_key.safe_name()
        config = load_config()
        admin_user_id = config.ov_server.admin_user_id

        try:
            client = await self._get_client(context.workspace_id)

            # 1. 提交全部的 message 到 admin
            admin_result = await client.commit(session_id, vikingbot_session.messages, admin_user_id)

            # 2. 根据 message 里的 sender_id 进行分组
            messages_by_sender = defaultdict(list)
            for msg in vikingbot_session.messages:
                sender_id = msg.get("sender_id")
                if sender_id and sender_id != admin_user_id:
                    messages_by_sender[sender_id].append(msg)

            # 3. 带并发限制地提交到各个 user
            user_results = []
            if messages_by_sender:
                # 限制最大并发数为 5
                semaphore = asyncio.Semaphore(5)

                async def commit_with_semaphore(user_id: str, user_messages: list):
                    async with semaphore:
                        return await client.commit(f"{session_id}_{user_id}", user_messages, user_id)

                user_tasks = []
                for user_id, user_messages in messages_by_sender.items():
                    task = commit_with_semaphore(user_id, user_messages)
                    user_tasks.append(task)

                # 等待所有用户任务完成
                user_results = await asyncio.gather(*user_tasks, return_exceptions=True)

            return {
                "success": True,
                "admin_result": admin_result,
                "user_results": user_results,
                "users_count": len(messages_by_sender)
            }
        except Exception as e:
            logger.exception(f"Failed to add message to OpenViking: {e}")
            return {"success": False, "error": str(e)}


class OpenVikingPostCallHook(Hook):
    name = "openviking_post_call"
    is_sync = True

    async def _get_client(self, workspace_id: str) -> VikingClient:
        # Use global singleton client
        return await get_global_client()

    async def _read_skill_memory(self, workspace_id: str, skill_name: str) -> str:
        ov_client = await self._get_client(workspace_id)
        config = load_config()
        openviking_config = config.ov_server
        # (f'openviking_config.mode={openviking_config.mode}')
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
            # print(f'content={content}')
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
