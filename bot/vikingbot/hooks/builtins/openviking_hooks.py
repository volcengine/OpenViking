import asyncio
import re
import time
from datetime import datetime
from typing import Any

from loguru import logger

from vikingbot.config.loader import load_config
from vikingbot.openviking_mount.session_state import (
    get_openviking_session_id,
    get_openviking_state,
    get_unsynced_messages,
    parse_local_index,
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

_global_clients: dict[tuple[str, int, int], Any] = {}


async def get_global_client(workspace_id: str | None, config: Any = None) -> VikingClient:
    """Get or create the shared VikingClient."""
    # VikingClient (and its underlying AsyncHTTPClient / streaming updater / connection
    # pool) creates asyncio.Event/Lock/Semaphore bound to the running loop at creation
    # time. Reusing one client across event loops (which happens with the native
    # multi-threaded rollout workers in tau2 training) raises
    # "<asyncio.locks.Event ... [unset]> is bound to a different event loop". Key the
    # cache by (workspace_id, running_loop) so each loop gets its own client.
    cache_key = (
        str(workspace_id or "__default__"),
        id(asyncio.get_running_loop()),
        id(config),
    )
    client = _global_clients.get(cache_key)
    if client is None:
        if config is None:
            client = await VikingClient.create(workspace_id)
        else:
            client = await VikingClient.create(workspace_id, config=config)
        _global_clients[cache_key] = client
    return client


class OpenVikingCompactHook(Hook):
    name = "openviking_compact"

    async def _get_client(
        self,
        workspace_id: str,
        openviking_connection: dict[str, Any] | None = None,
        config: Any = None,
    ) -> tuple[VikingClient, bool]:
        if openviking_connection:
            return (
                await VikingClient.create(
                    workspace_id,
                    connection=openviking_connection,
                    config=config,
                ),
                True,
            )
        return await get_global_client(workspace_id, config=config), False

    async def _execute_session_context_commit(
        self,
        context: HookContext,
        session: Session,
        client: VikingClient,
        agents_config: Any,
        admin_user_id: str,
        *,
        force_commit: bool,
        keep_recent_turn_count: int,
        retained_message_token_budget: int,
        min_raw_tail_steps: int,
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

        unsynced_tokens = sum(
            cal_str_tokens(str(msg.get("content") or ""))
            for msg in messages_to_sync
            if msg.get("content") is not None
        )

        should_commit = bool(
            force_commit
            or pending_tokens + unsynced_tokens >= commit_token_threshold
            or reached_message_threshold
        )
        session_user_id = client.session_owner_user_id()

        if messages_to_sync:
            admin_append_result = await client.append_messages(
                session_id=session_id,
                messages=messages_to_sync,
                default_user_peer_id=None,
                session_user_id=session_user_id,
            )
            state["last_synced_local_index"] = len(session.messages) - 1
            admin_session_state = await client.get_session(session_id, user_id=session_user_id)
            pending_tokens = int(admin_session_state.get("pending_tokens", 0) or 0)
        elif force_commit:
            admin_session_state = await client.get_session(session_id, user_id=session_user_id)
            pending_tokens = int(admin_session_state.get("pending_tokens", 0) or 0)

        should_commit = (
            force_commit or pending_tokens >= commit_token_threshold or reached_message_threshold
        )
        if should_commit:
            admin_commit_result = await client.commit_session(
                session_id=session_id,
                keep_recent_count=0,
                retention_mode="turn_budget",
                keep_recent_turn_count=keep_recent_turn_count,
                retained_message_token_budget=retained_message_token_budget,
                min_raw_tail_steps=min_raw_tail_steps,
                user_id=session_user_id,
            )
            logger.info(
                f"[HOOK] Committed session {session_id} for user {session_user_id or 'current'}"
            )
            admin_session_state = await client.get_session(session_id, user_id=session_user_id)
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
            "user_results": [],
            "users_count": 0,
            "pending_tokens": pending_tokens,
        }

    async def execute(self, context: HookContext, **kwargs) -> Any:
        vikingbot_session: Session = kwargs.get("session", {})
        session_id = context.session_key.safe_name()
        config = context.config or load_config()
        ov_config = config.ov_server
        if not str(getattr(ov_config, "server_url", "") or "").strip():
            return {"success": False, "skipped": "standalone"}
        agents_config = config.agents
        admin_user_id = ov_config.admin_user_id
        openviking_connection = context.openviking_connection or kwargs.get("openviking_connection")
        if not isinstance(openviking_connection, dict):
            openviking_connection = None
        force_commit = bool(kwargs.get("force_commit", False))
        keep_recent_turn_count = int(
            kwargs.get(
                "keep_recent_turn_count",
                getattr(agents_config, "commit_keep_recent_turn_count", 3),
            )
            or 0
        )
        retained_message_token_budget = int(
            kwargs.get(
                "retained_message_token_budget",
                getattr(agents_config, "commit_retained_message_token_budget", 6_000),
            )
            or 6_000
        )
        min_raw_tail_steps = int(
            kwargs.get(
                "min_raw_tail_steps",
                getattr(agents_config, "commit_min_raw_tail_steps", 1),
            )
            or 0
        )
        commit_message_threshold = kwargs.get("commit_message_threshold")
        if commit_message_threshold is not None:
            commit_message_threshold = int(commit_message_threshold)

        try:
            if openviking_connection:
                if context.config is None:
                    client_result = await self._get_client(
                        context.workspace_id,
                        openviking_connection=openviking_connection,
                    )
                else:
                    client_result = await self._get_client(
                        context.workspace_id,
                        openviking_connection=openviking_connection,
                        config=config,
                    )
            else:
                if context.config is None:
                    client_result = await self._get_client(context.workspace_id)
                else:
                    client_result = await self._get_client(context.workspace_id, config=config)
            if isinstance(client_result, tuple):
                client, should_close_client = client_result
            else:
                client = client_result
                should_close_client = False
            admin_user_id = getattr(client, "admin_user_id", None) or admin_user_id

            if getattr(agents_config, "session_context_enabled", False):
                return await self._execute_session_context_commit(
                    context,
                    vikingbot_session,
                    client,
                    agents_config,
                    admin_user_id,
                    force_commit=force_commit,
                    keep_recent_turn_count=keep_recent_turn_count,
                    retained_message_token_budget=retained_message_token_budget,
                    min_raw_tail_steps=min_raw_tail_steps,
                    commit_message_threshold=commit_message_threshold,
                )

            admin_result = await client.commit(
                session_id,
                vikingbot_session.messages,
                user_id=client.session_owner_user_id(),
            )
            return {
                "success": True,
                "admin_result": admin_result,
                "user_results": [],
                "users_count": 0,
            }
        except Exception as e:
            state = None
            if hasattr(vikingbot_session, "metadata"):
                state = get_openviking_state(vikingbot_session)
                state["last_sync_status"] = "error"
                state["last_sync_error"] = str(e)
            logger.exception(f"Failed to add message to OpenViking: {e}")
            return {"success": False, "error": str(e)}
        finally:
            if "should_close_client" in locals() and should_close_client:
                await client.close()


class OpenVikingPostCallHook(Hook):
    name = "openviking_post_call"
    # Hook execute() is genuinely async (it awaits ov_client search/read). Mark it
    # async so the hook manager routes it through asyncio.gather with other async
    # hooks instead of the sequential sync_hooks path.
    is_sync = False

    async def _get_client(self, workspace_id: str, config: Any = None) -> VikingClient:
        return await get_global_client(workspace_id, config=config)

    async def _search_skill_experiences(
        self,
        workspace_id: str,
        query: str,
        config: Any = None,
        openviking_connection: dict[str, Any] | None = None,
    ) -> str:
        """用 skill 描述检索 experience 记忆，只检索 experiences 目录。"""
        if not query:
            return ""
        started_at = time.perf_counter()
        query_preview = query.replace("\n", "\\n")[:120]
        ov_client = None
        should_close = False
        try:
            if (
                config is not None
                and not str(getattr(config.ov_server, "server_url", "") or "").strip()
            ):
                return ""
            if openviking_connection:
                ov_client = await VikingClient.create(
                    workspace_id,
                    connection=openviking_connection,
                    config=config,
                )
                should_close = True
            elif config is None:
                ov_client = await self._get_client(workspace_id)
            else:
                ov_client = await self._get_client(workspace_id, config=config)
            logger.debug(
                "[SKILL_EXP]: start workspace_id=%s query_len=%d query=%r",
                workspace_id,
                len(query),
                query_preview,
            )
            experiences = await ov_client.search_experiences(query, limit=3)
            logger.info(
                "[SKILL_EXP]: found %d experiences workspace_id=%s elapsed_ms=%.1f query=%r",
                len(experiences),
                workspace_id,
                (time.perf_counter() - started_at) * 1000.0,
                query_preview,
            )
            if not experiences:
                return ""
            parts = []
            for index, exp in enumerate(experiences):
                uri = exp.get("uri", "") if isinstance(exp, dict) else getattr(exp, "uri", "")
                score = exp.get("score", 0) if isinstance(exp, dict) else getattr(exp, "score", 0)
                if score < 0.3:
                    logger.debug(
                        "[SKILL_EXP]: skip low score workspace_id=%s index=%d uri=%s score=%s",
                        workspace_id,
                        index,
                        uri,
                        score,
                    )
                    continue
                read_started_at = time.perf_counter()
                try:
                    content = await ov_client.read_content(uri, level="read")
                except Exception as read_exc:
                    logger.warning(
                        "[SKILL_EXP]: failed to read experience workspace_id=%s "
                        "index=%d uri=%s score=%s elapsed_ms=%.1f error_type=%s error=%r",
                        workspace_id,
                        index,
                        uri,
                        score,
                        (time.perf_counter() - read_started_at) * 1000.0,
                        type(read_exc).__name__,
                        read_exc,
                    )
                    continue
                if content:
                    parts.append(content)
                    logger.debug(
                        "[SKILL_EXP]: read experience workspace_id=%s index=%d uri=%s "
                        "score=%s chars=%d elapsed_ms=%.1f",
                        workspace_id,
                        index,
                        uri,
                        score,
                        len(content),
                        (time.perf_counter() - read_started_at) * 1000.0,
                    )
            logger.info(
                "[SKILL_EXP]: finished workspace_id=%s kept=%d/%d elapsed_ms=%.1f query=%r",
                workspace_id,
                len(parts),
                len(experiences),
                (time.perf_counter() - started_at) * 1000.0,
                query_preview,
            )
            return "\n\n---\n".join(parts) if parts else ""
        except Exception as e:
            # Skill experience injection is best-effort. Under high-parallel evals,
            # OpenViking search may time out; log a compact line instead of a full
            # traceback so rollout logs are not dominated by optional retrieval.
            logger.warning(
                "[SKILL_EXP]: skipped experience search workspace_id={} "
                "elapsed_ms={:.1f} error_type={} error={!r} query_len={} query={!r}",
                workspace_id,
                (time.perf_counter() - started_at) * 1000.0,
                type(e).__name__,
                e,
                len(query),
                query_preview,
            )
            return ""
        finally:
            if should_close and ov_client is not None:
                await ov_client.close()

    async def execute(self, context: HookContext, tool_name, params, result) -> Any:
        if tool_name == "read_file":
            if result and not isinstance(result, Exception):
                match = re.search(r"^---\s*\nname:\s*(.+?)\s*\n", result, re.MULTILINE)
                if match:
                    skill_name = match.group(1).strip()
                    if skill_name == "experience_loader":
                        return {"tool_name": tool_name, "params": params, "result": result}
                    desc_match = re.search(r"^description:\s*(.+)$", result, re.MULTILINE)
                    skill_query = desc_match.group(1).strip() if desc_match else skill_name

                    exp_memory = await self._search_skill_experiences(
                        context.workspace_id,
                        skill_query,
                        config=context.config,
                        openviking_connection=context.openviking_connection,
                    )
                    if exp_memory:
                        result = f"{result}\n\n---\n## Related Experiences\n{exp_memory}"

        return {"tool_name": tool_name, "params": params, "result": result}


hooks = {"message.compact": [OpenVikingCompactHook()], "tool.post_call": [OpenVikingPostCallHook()]}
