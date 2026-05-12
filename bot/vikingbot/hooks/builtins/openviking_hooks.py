import asyncio
import re
from collections import defaultdict
from typing import Any

from loguru import logger

from vikingbot.config.loader import load_config
from ..base import Hook, HookContext
from ...session import Session

try:
    import openviking as ov
    from vikingbot.openviking_mount.ov_server import VikingClient

    HAS_OPENVIKING = True
except Exception:
    HAS_OPENVIKING = False
    VikingClient = None
    ov = None

# Clients are cached per workspace so canonical agent_id routing stays aligned
# with OpenViking account namespace policy.
_global_clients: dict[str | None, VikingClient] = {}


async def get_global_client(workspace_id: str | None) -> VikingClient:
    """Get or create a workspace-scoped VikingClient."""
    client = _global_clients.get(workspace_id)
    if client is None:
        client = await VikingClient.create(workspace_id)
        _global_clients[workspace_id] = client
    return client


class OpenVikingCompactHook(Hook):
    name = "openviking_compact"

    async def _get_client(self, workspace_id: str) -> VikingClient:
        return await get_global_client(workspace_id)


    async def execute(self, context: HookContext, **kwargs) -> Any:
        vikingbot_session: Session = kwargs.get("session", {})
        session_id = context.session_key.safe_name()
        config = load_config()
        admin_user_id = config.ov_server.admin_user_id

        try:
            client = await self._get_client(context.workspace_id)

            if not client.should_sender_fanout():
                single_result = await client.commit(session_id, vikingbot_session.messages, None)
                return {
                    "success": True,
                    "admin_result": single_result,
                    "user_results": [],
                    "users_count": 0,
                }

            admin_result = await client.commit(session_id, vikingbot_session.messages, admin_user_id)

            messages_by_sender = defaultdict(list)
            for msg in vikingbot_session.messages:
                sender_id = msg.get("sender_id")
                if sender_id and sender_id != admin_user_id:
                    messages_by_sender[sender_id].append(msg)

            user_results = []
            if messages_by_sender:
                semaphore = asyncio.Semaphore(5)

                async def commit_with_semaphore(user_id: str, user_messages: list):
                    async with semaphore:
                        return await client.commit(f"{session_id}_{user_id}", user_messages, user_id)

                user_tasks = []
                for user_id, user_messages in messages_by_sender.items():
                    task = commit_with_semaphore(user_id, user_messages)
                    user_tasks.append(task)

                user_results = await asyncio.gather(*user_tasks, return_exceptions=True)

            return {
                "success": True,
                "admin_result": admin_result,
                "user_results": user_results,
                "users_count": len(messages_by_sender),
            }
        except Exception as e:
            logger.exception(f"Failed to add message to OpenViking: {e}")
            return {"success": False, "error": str(e)}


class OpenVikingPostCallHook(Hook):
    name = "openviking_post_call"
    is_sync = True

    async def _get_client(self, workspace_id: str) -> VikingClient:
        return await get_global_client(workspace_id)

    async def _read_skill_memory(self, workspace_id: str, skill_name: str) -> str:
        ov_client = await self._get_client(workspace_id)
        config = load_config()
        openviking_config = config.ov_server
        if not skill_name:
            return ""
        try:
            skill_owner_user_id = None if openviking_config.mode == "local" else openviking_config.admin_user_id
            skill_memory_uri = ov_client._skill_memory_uri(skill_name, skill_owner_user_id)
            content = await ov_client.read_content(skill_memory_uri, level="read")
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
