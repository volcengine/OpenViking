import asyncio
import re
from collections import defaultdict
from datetime import datetime
from typing import Any

from loguru import logger

from vikingbot.config.loader import load_config
from vikingbot.openviking_mount.session_state import (
    get_openviking_session_id,
    get_openviking_state,
    get_unsynced_messages,
    get_unsynced_messages_for_sender,
    parse_local_index,
    set_sender_synced_local_index,
)
from vikingbot.utils.helpers import cal_str_tokens

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

    async def _execute_session_context_commit(
        self,
        context: HookContext,
        session: Session,
        client: VikingClient,
        agents_config: Any,
        admin_user_id: str,
        *,
        force_commit: bool,
        keep_recent_count: int,
        commit_message_threshold: int | None,
    ) -> dict[str, Any]:
        state = get_openviking_state(session)
        session_id = get_openviking_session_id(
            session,
            default_session_id=context.session_key.safe_name(),
        )

        commit_token_threshold = int(getattr(agents_config, "commit_token_threshold", 6000) or 6000)
        pending_tokens = int(state.get("last_pending_tokens", 0) or 0)
        messages_to_sync = get_unsynced_messages(session)
        last_commit_local_index = parse_local_index(state.get("last_commit_local_index", -1))
        messages_since_commit = len(session.messages) - last_commit_local_index - 1
        reached_message_threshold = bool(
            commit_message_threshold is not None
            and commit_message_threshold > 0
            and messages_since_commit >= commit_message_threshold
        )

        admin_append_result = None
        admin_commit_result = None
        user_results = []
        pending_tokens_before_sync = pending_tokens

        unsynced_tokens = sum(
            cal_str_tokens(str(msg.get("content") or ""))
            for msg in messages_to_sync
            if msg.get("content") is not None
        )

        all_sender_ids = sorted(
            {
                str(msg.get("sender_id"))
                for msg in session.messages
                if msg.get("sender_id") and msg.get("sender_id") != admin_user_id
            }
        )
        unsynced_messages_by_sender = {}
        sender_latest_indexes: dict[str, int] = {}
        for sender_id in all_sender_ids:
            user_messages = get_unsynced_messages_for_sender(
                session,
                sender_id,
                admin_user_id=admin_user_id,
            )
            if user_messages:
                unsynced_messages_by_sender[sender_id] = user_messages
                sender_latest_indexes[sender_id] = max(
                    index
                    for index, msg in enumerate(session.messages)
                    if msg.get("sender_id") == sender_id
                )

        should_commit = bool(
            force_commit
            or pending_tokens + unsynced_tokens >= commit_token_threshold
            or reached_message_threshold
        )
        sender_ids_to_sync = (
            all_sender_ids if should_commit else sorted(unsynced_messages_by_sender)
        )
        user_results_by_id: dict[str, dict[str, Any]] = {}

        if sender_ids_to_sync:
            semaphore = asyncio.Semaphore(5)

            async def sync_sender(user_id: str):
                user_messages = unsynced_messages_by_sender.get(user_id, [])
                async with semaphore:
                    sender_session_id = f"{session_id}_{user_id}"
                    append_result = None
                    if user_messages:
                        append_result = await client.append_messages(
                            session_id=sender_session_id,
                            messages=user_messages,
                            default_user_role_id=user_id,
                            session_user_id=user_id,
                        )
                    return {
                        "session_id": sender_session_id,
                        "user_id": user_id,
                        "append": append_result,
                    }

            user_results = await asyncio.gather(
                *(sync_sender(user_id) for user_id in sender_ids_to_sync),
                return_exceptions=True,
            )
            for result in user_results:
                if isinstance(result, dict):
                    user_id = result["user_id"]
                    user_results_by_id[user_id] = result
                    if result.get("append") is not None and user_id in sender_latest_indexes:
                        set_sender_synced_local_index(
                            session, user_id, sender_latest_indexes[user_id]
                        )

        fanout_errors = [result for result in user_results if isinstance(result, Exception)]
        if fanout_errors:
            error_message = "; ".join(str(error) for error in fanout_errors)
            state["last_pending_tokens"] = pending_tokens_before_sync
            state["last_commit_performed"] = False
            state["last_sync_status"] = "error"
            state["last_sync_error"] = error_message
            return {
                "success": False,
                "session_id": session_id,
                "admin_result": {
                    "append": admin_append_result,
                    "commit": admin_commit_result,
                    "committed": False,
                },
                "user_results": user_results,
                "users_count": len(sender_ids_to_sync),
                "pending_tokens": pending_tokens_before_sync,
                "error": error_message,
            }

        if messages_to_sync:
            admin_append_result = await client.append_messages(
                session_id=session_id,
                messages=messages_to_sync,
                default_user_role_id=admin_user_id,
                session_user_id=admin_user_id,
            )
            state["last_synced_local_index"] = len(session.messages) - 1
            admin_session_state = await client.get_session(session_id, user_id=admin_user_id)
            pending_tokens = int(admin_session_state.get("pending_tokens", 0) or 0)
        elif force_commit:
            admin_session_state = await client.get_session(session_id, user_id=admin_user_id)
            pending_tokens = int(admin_session_state.get("pending_tokens", 0) or 0)

        should_commit = (
            force_commit or pending_tokens >= commit_token_threshold or reached_message_threshold
        )
        if should_commit:
            sender_ids_to_commit = all_sender_ids
            for user_id in sender_ids_to_commit:
                user_results_by_id.setdefault(
                    user_id,
                    {
                        "session_id": f"{session_id}_{user_id}",
                        "user_id": user_id,
                        "append": None,
                    },
                )

            if sender_ids_to_commit:
                semaphore = asyncio.Semaphore(5)

                async def commit_sender(user_id: str):
                    async with semaphore:
                        sender_session_id = f"{session_id}_{user_id}"
                        logger.info(
                            f"[HOOK] Committed session {sender_session_id} for user {user_id}"
                        )
                        return await client.commit_session(
                            session_id=sender_session_id,
                            keep_recent_count=keep_recent_count,
                            user_id=user_id,
                        )

                commit_results = await asyncio.gather(
                    *(commit_sender(user_id) for user_id in sender_ids_to_commit),
                    return_exceptions=True,
                )
                for user_id, commit_result in zip(
                    sender_ids_to_commit, commit_results, strict=True
                ):
                    user_results_by_id[user_id]["commit"] = commit_result
                fanout_errors = [
                    result for result in commit_results if isinstance(result, Exception)
                ]
                if fanout_errors:
                    error_message = "; ".join(str(error) for error in fanout_errors)
                    state["last_pending_tokens"] = pending_tokens
                    state["last_commit_performed"] = False
                    state["last_sync_status"] = "error"
                    state["last_sync_error"] = error_message
                    return {
                        "success": False,
                        "session_id": session_id,
                        "admin_result": {
                            "append": admin_append_result,
                            "commit": admin_commit_result,
                            "committed": False,
                        },
                        "user_results": list(user_results_by_id.values()),
                        "users_count": len(user_results_by_id),
                        "pending_tokens": pending_tokens,
                        "error": error_message,
                    }

            admin_commit_result = await client.commit_session(
                session_id=session_id,
                keep_recent_count=keep_recent_count,
                user_id=admin_user_id,
            )
            logger.info(f"[HOOK] Committed session {session_id} for user {admin_user_id}")
            admin_session_state = await client.get_session(session_id, user_id=admin_user_id)
            pending_tokens = int(admin_session_state.get("pending_tokens", 0) or 0)

        if should_commit:
            state["last_commit_at"] = datetime.now().isoformat()
            state["last_commit_local_index"] = len(session.messages) - 1
        state["last_pending_tokens"] = pending_tokens
        state["last_commit_performed"] = should_commit
        state["last_sync_status"] = "success"
        state.pop("last_sync_error", None)

        return {
            "success": True,
            "session_id": session_id,
            "admin_result": {
                "append": admin_append_result,
                "commit": admin_commit_result,
                "committed": should_commit,
            },
            "user_results": list(user_results_by_id.values()),
            "users_count": len(user_results_by_id),
            "pending_tokens": pending_tokens,
        }

    async def execute(self, context: HookContext, **kwargs) -> Any:
        vikingbot_session: Session = kwargs.get("session", {})
        session_id = context.session_key.safe_name()
        config = load_config()
        ov_config = config.ov_server
        agents_config = config.agents
        admin_user_id = ov_config.admin_user_id
        force_commit = bool(kwargs.get("force_commit", False))
        keep_recent_count = int(
            kwargs.get(
                "keep_recent_count",
                getattr(agents_config, "commit_keep_recent_count", 10),
            )
            or 0
        )
        commit_message_threshold = kwargs.get("commit_message_threshold")
        if commit_message_threshold is not None:
            commit_message_threshold = int(commit_message_threshold)

        try:
            client = await self._get_client(context.workspace_id)

            if getattr(agents_config, "session_context_enabled", False):
                return await self._execute_session_context_commit(
                    context,
                    vikingbot_session,
                    client,
                    agents_config,
                    admin_user_id,
                    force_commit=force_commit,
                    keep_recent_count=keep_recent_count,
                    commit_message_threshold=commit_message_threshold,
                )

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
            state = None
            if hasattr(vikingbot_session, "metadata"):
                state = get_openviking_state(vikingbot_session)
                state["last_sync_status"] = "error"
                state["last_sync_error"] = str(e)
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
