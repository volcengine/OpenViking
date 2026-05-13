import asyncio
import re
from collections import defaultdict
from typing import Any

from loguru import logger

from vikingbot.config.loader import load_config

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

            admin_result = await client.commit(
                session_id, vikingbot_session.messages, admin_user_id
            )

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
                        return await client.commit(
                            f"{session_id}_{user_id}", user_messages, user_id
                        )

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

    async def _search_skill_experiences(self, workspace_id: str, query: str) -> str:
        """用 skill 描述检索 experience 记忆，只检索 experiences 目录。"""
        if not query:
            return ""
        try:
            ov_client = await self._get_client(workspace_id)
            experiences = await ov_client.search_experiences(query, limit=3)
            logger.info(f"[SKILL_EXP]: found {len(experiences)} experiences, query={query[:50]}")
            if not experiences:
                return ""
            parts = []
            for exp in experiences:
                uri = exp.get("uri", "") if isinstance(exp, dict) else getattr(exp, "uri", "")
                score = exp.get("score", 0) if isinstance(exp, dict) else getattr(exp, "score", 0)
                if score < 0.3:
                    continue
                content = await ov_client.read_content(uri, level="read")
                if content:
                    parts.append(content)
            return "\n\n---\n".join(parts) if parts else ""
        except Exception as e:
            logger.warning(f"Failed to search experiences for skill: {e}")
            return ""

    async def execute(self, context: HookContext, tool_name, params, result) -> Any:
        if tool_name == "read_file":
            if result and not isinstance(result, Exception):
                match = re.search(r"^---\s*\nname:\s*(.+?)\s*\n", result, re.MULTILINE)
                if match:
                    skill_name = match.group(1).strip()
                    desc_match = re.search(r"^description:\s*(.+)$", result, re.MULTILINE)
                    skill_query = desc_match.group(1).strip() if desc_match else skill_name

                    exp_memory = await self._search_skill_experiences(
                        context.workspace_id, skill_query
                    )
                    if exp_memory:
                        result = f"{result}\n\n---\n## Related Experiences\n{exp_memory}"

        return {"tool_name": tool_name, "params": params, "result": result}


hooks = {"message.compact": [OpenVikingCompactHook()], "tool.post_call": [OpenVikingPostCallHook()]}
