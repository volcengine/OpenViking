"""Session history, lifecycle policies, commands, and active-turn interruption and steering."""

from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from loguru import logger

from vikingbot.agent.context import ContextBuilder
from vikingbot.agent.memory import MemoryStore
from vikingbot.bus.events import InboundMessage, OutboundEventType, OutboundMessage
from vikingbot.config.schema import BotMode, SessionKey
from vikingbot.hooks import HookContext
from vikingbot.hooks.manager import hook_manager
from vikingbot.openviking_mount.session_budget import session_budget
from vikingbot.openviking_mount.session_state import (
    get_openviking_session_id,
    get_openviking_state,
    get_unsynced_messages,
    parse_local_index,
    reset_openviking_state,
)
from vikingbot.session.manager import Session
from vikingbot.utils.helpers import cal_str_tokens, ensure_non_empty_assistant_content

if TYPE_CHECKING:
    from vikingbot.agent.loop.loop import AgentLoop
    from vikingbot.agent.loop.loop_events import _ResponseOutcomeUpdate


@dataclass(frozen=True)
class _MessageOptions:
    openviking_connection: dict[str, Any] | None
    actor_peer_id: str
    profile_user_list: list[str]
    memory_peer_ids: list[str]
    memory_owner_user_ids: list[str]


@dataclass(slots=True)
class _PreparedSteer:
    """Preprocessed same-session input to merge into an active agent loop."""

    msg: InboundMessage
    prompt_messages: list[dict[str, Any]]
    newly_recalled_exp_uris: list[str]
    newly_recalled_exp_content: str
    auto_memory_tool: dict[str, Any] | None = None


@dataclass(slots=True)
class _AcceptedSteer:
    """A steer accepted by the active turn and its in-flight preprocessing."""

    msg: InboundMessage
    task: asyncio.Task[_PreparedSteer]


@dataclass(slots=True)
class _AssistantBoundary:
    """A plain-text reply emitted before later steer input continued the turn."""

    content: str
    reasoning_content: str | None = None


@dataclass(slots=True)
class _ActiveTurn:
    """The single turn currently running in the serial AgentLoop."""

    session_key: SessionKey
    task: asyncio.Task
    interruptible: bool = True
    interrupted: bool = False
    accepting_steer: bool = True
    accepted_steers: list[_AcceptedSteer] = field(default_factory=list)
    history_items: list[_PreparedSteer | _AssistantBoundary] = field(default_factory=list)


class LocalSessionPolicy:
    def __init__(self, loop: AgentLoop):
        self.loop = loop

    async def before_turn(
        self,
        session: Session,
        msg: InboundMessage,
        *,
        outcome_update: _ResponseOutcomeUpdate | None = None,
    ) -> None:
        loop = self.loop
        if len(session.messages) <= loop.memory_window:
            return
        snapshot = session.clone()
        keep_count = min(10, max(2, loop.memory_window // 2))
        consolidated = await loop._consolidate_memory(
            snapshot.clone(),
            archive_all=False,
            openviking_connection=msg.openviking_connection,
        )
        if consolidated:
            await loop._trim_consolidated_history(
                session, snapshot, keep_count=keep_count, outcome_update=outcome_update
            )

    async def build_history(
        self,
        session: Session,
        provider_name: str | None = None,
        openviking_connection: dict[str, Any] | None = None,
        actor_peer_id: str | None = None,
    ) -> list[dict[str, Any]]:
        return session.get_history(
            max_messages=self.loop.memory_window, provider_name=provider_name
        )

    async def after_turn(
        self, session: Session, openviking_connection: dict[str, Any] | None
    ) -> None:
        pass

    async def compact(self, session: Session, openviking_connection: dict[str, Any] | None) -> bool:
        snapshot = session.clone()
        committed = await self.loop._consolidate_memory(
            snapshot.clone(), archive_all=True, openviking_connection=openviking_connection
        )
        if not committed:
            return False
        return await self.loop._trim_consolidated_history(session, snapshot, keep_count=0)

    async def remember(
        self,
        session: Session,
        openviking_connection: dict[str, Any] | None,
        *,
        ov_tools_enable: bool,
    ) -> bool | None:
        if not ov_tools_enable:
            return None
        return await self.loop._consolidate_viking_memory(
            session.clone(), openviking_connection=openviking_connection
        )

    async def archive(self, session: Session, openviking_connection: dict[str, Any] | None) -> bool:
        """Run the backend commit before local memory is consolidated."""
        return True

    def reset_session(self, session: Session) -> None:
        session.clear()
        session.metadata.pop("recalled_exp_uris", None)


class LegacyOpenVikingSessionPolicy(LocalSessionPolicy):
    """Keep local history and summaries, committing remotely before local consolidation."""

    async def archive(self, session: Session, openviking_connection: dict[str, Any] | None) -> bool:
        return await self.loop._consolidate_viking_memory(
            session, openviking_connection=openviking_connection
        )


class WorkingMemorySessionPolicy(LegacyOpenVikingSessionPolicy):
    async def before_turn(
        self,
        session: Session,
        msg: InboundMessage,
        *,
        outcome_update: _ResponseOutcomeUpdate | None = None,
    ) -> None:
        await self.loop._maybe_commit_openviking_before_turn(session, msg)

    async def build_history(
        self,
        session: Session,
        provider_name: str | None = None,
        openviking_connection: dict[str, Any] | None = None,
        actor_peer_id: str | None = None,
    ) -> list[dict[str, Any]]:
        loop = self.loop
        agents_config = getattr(loop.config, "agents", None)
        _, token_budget, _ = session_budget(agents_config)
        session_id = get_openviking_session_id(session)
        request_client = None

        try:
            client = await loop._get_ov_client(
                session.key,
                openviking_connection=openviking_connection,
                actor_peer_id=actor_peer_id,
            )
            if openviking_connection or actor_peer_id:
                request_client = client
            context_payload = await client.get_session_context(
                session_id=session_id,
                token_budget=token_budget,
            )
            ov_history = loop._build_ov_history_messages(
                session,
                context_payload,
                provider_name=provider_name,
            )
            unsynced_messages = get_unsynced_messages(session)
            if not ov_history and len(unsynced_messages) < len(session.messages):
                logger.warning(
                    f"OpenViking returned no session context for {session_id}; "
                    "falling back to complete local session history."
                )
                unsynced_messages = session.messages
            local_tail = loop._format_history_messages(
                session,
                unsynced_messages,
                provider_name=provider_name,
            )
            combined_history = ov_history + local_tail
            summary_messages = (
                ov_history[:1] if context_payload.get("latest_archive_overview") else []
            )
            summary_tokens = sum(
                loop._history_message_tokens(message) for message in summary_messages
            )
            if summary_tokens >= token_budget:
                summary_messages = []
                summary_tokens = 0
            trimmed_history = loop._trim_history_to_token_budget(
                combined_history[len(summary_messages) :],
                token_budget - summary_tokens,
            )
            trimmed_history = summary_messages + trimmed_history
            if len(trimmed_history) != len(combined_history) or any(
                before.get("content") != after.get("content")
                for before, after in zip(
                    combined_history[-len(trimmed_history) :],
                    trimmed_history,
                    strict=False,
                )
            ):
                logger.info(
                    f"Trimmed OpenViking session history for {session_id} to "
                    f"token_budget={token_budget}: messages={len(combined_history)}"
                    f"->{len(trimmed_history)}"
                )
            return trimmed_history
        except Exception as e:
            logger.warning(
                f"Failed to load OpenViking session context for {session_id}: {e}. "
                "Falling back to local session history."
            )
            return session.get_history(max_messages=loop.memory_window, provider_name=provider_name)
        finally:
            if request_client is not None:
                await request_client.close()

    async def after_turn(
        self, session: Session, openviking_connection: dict[str, Any] | None
    ) -> None:
        await self.loop._submit_openviking_session_and_clear_if_committed(
            session,
            commit_message_threshold=self.loop.memory_window,
            openviking_connection=openviking_connection,
        )

    async def compact(self, session: Session, openviking_connection: dict[str, Any] | None) -> bool:
        return await self.loop._commit_openviking_session(
            session,
            keep_recent_turn_count=0,
            clear_local_session=True,
            openviking_connection=openviking_connection,
        )

    async def remember(
        self,
        session: Session,
        openviking_connection: dict[str, Any] | None,
        *,
        ov_tools_enable: bool,
    ) -> bool:
        return await self.loop._commit_openviking_session(
            session,
            keep_recent_turn_count=self.loop.config.agents.commit_keep_recent_turn_count,
            openviking_connection=openviking_connection,
        )

    def reset_session(self, session: Session) -> None:
        super().reset_session(session)
        reset_openviking_state(session, rotate_session_id=True)


class LoopSessionMixin:
    """Session lifecycle and active turns. State is owned by AgentLoop."""

    def _ov_session_context_enabled(self) -> bool:
        agents_config = getattr(self.config, "agents", None)
        return bool(
            self.config.ov_server.is_available()
            and agents_config
            and getattr(agents_config, "session_context_enabled", False)
        )

    def _get_session_policy(self) -> LocalSessionPolicy:
        if self._ov_session_context_enabled():
            return WorkingMemorySessionPolicy(self)
        if self.config.ov_server.is_available():
            return LegacyOpenVikingSessionPolicy(self)
        return LocalSessionPolicy(self)

    def _get_ov_workspace_id(self, session_key: SessionKey) -> str:
        if self.sandbox_manager:
            return self.sandbox_manager.to_workspace_id(session_key)
        return "shared"

    async def _get_ov_client(
        self,
        session_key: SessionKey,
        openviking_connection: dict[str, Any] | None = None,
        actor_peer_id: str | None = None,
    ):
        workspace_id = self._get_ov_workspace_id(session_key)
        if openviking_connection or actor_peer_id:
            from vikingbot.openviking_mount.ov_server import VikingClient

            return await VikingClient.create(
                workspace_id,
                connection=openviking_connection,
                actor_peer_id=actor_peer_id,
                config=self.config,
            )

        client = self._ov_clients.get(workspace_id)
        if client is None:
            from vikingbot.openviking_mount.ov_server import VikingClient

            client = await VikingClient.create(workspace_id, config=self.config)
            self._ov_clients[workspace_id] = client
        return client

    def _format_history_messages(
        self,
        session: Session,
        messages: list[dict[str, Any]],
        provider_name: str | None = None,
    ) -> list[dict[str, Any]]:
        if not messages:
            return []
        temp_session = Session(key=session.key, messages=list(messages), metadata=session.metadata)
        return temp_session.get_history(max_messages=len(messages), provider_name=provider_name)

    @staticmethod
    def _flatten_ov_message_text(message: dict[str, Any]) -> str:
        content = message.get("content")
        if isinstance(content, str) and content.strip():
            return content.strip()

        text_parts: list[str] = []
        for part in message.get("parts") or []:
            if not isinstance(part, dict):
                continue
            for key in ("text", "abstract", "tool_output"):
                value = part.get(key)
                if isinstance(value, str) and value.strip():
                    text_parts.append(value.strip())
        return "\n".join(text_parts).strip()

    def _build_ov_history_messages(
        self,
        session: Session,
        context_payload: dict[str, Any],
        provider_name: str | None = None,
    ) -> list[dict[str, Any]]:
        raw_messages: list[dict[str, Any]] = []
        overview = str(context_payload.get("latest_archive_overview") or "").strip()
        if overview:
            raw_messages.append(
                {
                    "role": "assistant",
                    "content": ensure_non_empty_assistant_content(
                        f"[Earlier conversation summary]\n{overview}"
                    ),
                }
            )

        for message in context_payload.get("messages") or []:
            if not isinstance(message, dict):
                continue
            role = str(message.get("role") or "").strip().lower()
            if role not in {"user", "assistant"}:
                continue
            text = self._flatten_ov_message_text(message)
            if not text:
                continue
            raw_messages.append(
                {
                    "role": role,
                    "content": (
                        ensure_non_empty_assistant_content(text) if role == "assistant" else text
                    ),
                }
            )

        return self._format_history_messages(
            session,
            raw_messages,
            provider_name=provider_name,
        )

    @staticmethod
    def _history_message_tokens(message: dict[str, Any]) -> int:
        """Estimate prompt tokens for one formatted history message."""
        content = message.get("content", "")
        if not isinstance(content, str):
            content = json.dumps(content, ensure_ascii=False, default=str)
        # Reserve a small amount for the role and provider message framing.
        return cal_str_tokens(content, text_type="mixed") + 4

    @classmethod
    def _truncate_history_message(
        cls,
        message: dict[str, Any],
        token_budget: int,
    ) -> dict[str, Any] | None:
        """Return a prompt-only copy of a message clipped to ``token_budget``."""
        if token_budget <= 4:
            return None

        content = message.get("content", "")
        if not isinstance(content, str) or not content:
            return None

        content_budget = token_budget - 4
        marker = "\n[History truncated to fit session context token budget]"

        def fits(value: str) -> bool:
            return cal_str_tokens(value, text_type="mixed") <= content_budget

        low = 0
        high = len(content)
        best = ""
        while low <= high:
            mid = (low + high) // 2
            candidate = content[:mid] + (marker if mid < len(content) else "")
            if fits(candidate):
                best = candidate
                low = mid + 1
            else:
                high = mid - 1

        # Very small budgets may not fit the marker. Preserve as much raw text
        # as possible while still honoring the hard limit.
        if not best:
            # Empty content is not a valid candidate and breaks the monotonic
            # assumption of this binary search: "" is rejected while a
            # one-character prefix may fit.
            low = 1
            high = len(content)
            while low <= high:
                mid = (low + high) // 2
                candidate = content[:mid]
                if candidate and fits(candidate):
                    best = candidate
                    low = mid + 1
                else:
                    high = mid - 1

        if not best:
            return None

        clipped = dict(message)
        clipped["content"] = best
        # Reasoning is not part of OV session messages. Drop any provider-only
        # reasoning field from a clipped local fallback message so it cannot
        # silently exceed the session-history budget.
        clipped.pop("reasoning_content", None)
        return clipped

    @classmethod
    def _trim_history_to_token_budget(
        cls,
        messages: list[dict[str, Any]],
        token_budget: int,
    ) -> list[dict[str, Any]]:
        """Fit history to a hard budget without dropping the latest User anchor."""
        if token_budget <= 0 or not messages:
            return []

        total_tokens = sum(cls._history_message_tokens(message) for message in messages)
        if total_tokens <= token_budget:
            return messages

        latest_anchor_index = next(
            (
                index
                for index in range(len(messages) - 1, -1, -1)
                if messages[index].get("role") == "user"
            ),
            None,
        )
        if latest_anchor_index is None:
            # Legacy assistant-only history has no Turn boundary to preserve.
            remaining = token_budget
            retained_reversed: list[dict[str, Any]] = []
            for message in reversed(messages):
                message_tokens = cls._history_message_tokens(message)
                if message_tokens <= remaining:
                    retained_reversed.append(message)
                    remaining -= message_tokens
                    continue

                clipped = cls._truncate_history_message(message, remaining)
                if clipped is not None:
                    retained_reversed.append(clipped)
                break
            return list(reversed(retained_reversed))

        final_index = next(
            (
                index
                for index in range(len(messages) - 1, latest_anchor_index, -1)
                if messages[index].get("role") == "assistant"
            ),
            None,
        )

        selected: dict[int, dict[str, Any]] = {}
        used_tokens = 0

        def _select_message(
            index: int,
            *,
            allow_truncate: bool,
            max_budget: int | None = None,
        ) -> tuple[bool, bool]:
            nonlocal used_tokens
            remaining = max(0, token_budget - used_tokens)
            if max_budget is not None:
                remaining = min(remaining, max(0, int(max_budget)))

            message = messages[index]
            message_tokens = cls._history_message_tokens(message)
            chosen = message
            truncated = False
            if message_tokens > remaining:
                if not allow_truncate or remaining <= 0:
                    return False, False
                clipped = cls._truncate_history_message(message, remaining)
                if clipped is None:
                    return False, False
                chosen = clipped
                truncated = True

            selected[index] = chosen
            used_tokens += cls._history_message_tokens(chosen)
            return True, truncated

        def _minimum_message_budget(index: int) -> int | None:
            message = messages[index]
            message_tokens = cls._history_message_tokens(message)
            if message_tokens == 0:
                return 0

            low = 1
            high = min(message_tokens, token_budget)
            minimum: int | None = None
            while low <= high:
                candidate = (low + high) // 2
                if cls._truncate_history_message(message, candidate) is not None:
                    minimum = candidate
                    high = candidate - 1
                else:
                    low = candidate + 1
            return minimum

        # OpenViking already returns Turn-aware context, but VikingBot applies a
        # second budget with a different estimator after adding the local tail.
        # Jointly reserve room for the latest User anchor and final Assistant so
        # this final boundary cannot turn the prompt back into a half Turn.
        final_reserve = 0
        if final_index is not None:
            anchor_minimum = _minimum_message_budget(latest_anchor_index)
            final_minimum = _minimum_message_budget(final_index)
            if (
                anchor_minimum is not None
                and final_minimum is not None
                and anchor_minimum + final_minimum <= token_budget
            ):
                final_reserve = min(
                    cls._history_message_tokens(messages[final_index]),
                    max(final_minimum, token_budget // 2),
                    token_budget - anchor_minimum,
                )

        _select_message(
            latest_anchor_index,
            allow_truncate=True,
            max_budget=token_budget - final_reserve,
        )
        if final_index is not None:
            _select_message(final_index, allow_truncate=True)

        # Prefer the newest remaining Steps in the latest Turn. At most one Step
        # is clipped; older material is useful only while this suffix remains
        # lossless.
        latest_turn_end = final_index if final_index is not None else len(messages)
        for index in range(latest_turn_end - 1, latest_anchor_index, -1):
            kept, truncated = _select_message(index, allow_truncate=True)
            if not kept or truncated:
                break

        # Add older Turns only as complete units so the final prompt never starts
        # from the assistant half of an earlier Turn. An assistant-only prefix
        # such as the archive overview is treated as one conservative unit.
        older_turns: list[list[int]] = []
        current_turn: list[int] = []
        for index in range(latest_anchor_index):
            if messages[index].get("role") == "user" and current_turn:
                older_turns.append(current_turn)
                current_turn = []
            current_turn.append(index)
        if current_turn:
            older_turns.append(current_turn)

        remaining = token_budget
        remaining -= used_tokens
        for turn_indexes in reversed(older_turns):
            turn_tokens = sum(
                cls._history_message_tokens(messages[index]) for index in turn_indexes
            )
            if turn_tokens > remaining:
                break
            for index in turn_indexes:
                selected[index] = messages[index]
            used_tokens += turn_tokens
            remaining -= turn_tokens

        return [selected[index] for index in sorted(selected)]

    async def _build_prompt_history(
        self,
        session: Session,
        provider_name: str | None = None,
        openviking_connection: dict[str, Any] | None = None,
        actor_peer_id: str | None = None,
    ) -> list[dict[str, Any]]:
        if get_openviking_state(session).get("working_memory_confirmed") is False:
            # A rejected WM request must not replace local history with an OV tail.
            return session.get_history(
                max_messages=len(session.messages), provider_name=provider_name
            )
        return await self._get_session_policy().build_history(
            session,
            provider_name=provider_name,
            openviking_connection=openviking_connection,
            actor_peer_id=actor_peer_id,
        )

    async def _submit_openviking_session(
        self,
        session: Session,
        *,
        force_commit: bool = False,
        keep_recent_turn_count: int | None = None,
        commit_message_threshold: int | None = None,
        openviking_connection: dict[str, Any] | None = None,
    ) -> bool:
        if not self._ov_session_context_enabled():
            return False

        state = get_openviking_state(session)
        state.pop("last_commit_performed", None)
        kwargs: dict[str, Any] = {
            "session": session,
            "force_commit": force_commit,
        }
        if keep_recent_turn_count is not None:
            kwargs["keep_recent_turn_count"] = keep_recent_turn_count
        if commit_message_threshold is not None:
            kwargs["commit_message_threshold"] = commit_message_threshold
        if openviking_connection:
            kwargs["openviking_connection"] = openviking_connection

        session_id = get_openviking_session_id(session)
        state.pop("last_sync_status", None)
        await hook_manager.execute_hooks(
            context=HookContext(
                event_type="message.compact",
                session_id=session_id,
                workspace_id=self._get_ov_workspace_id(session.key),
                session_key=session.key,
                config=self.config,
                openviking_connection=openviking_connection,
            ),
            **kwargs,
        )
        await self.sessions.save(session)
        return get_openviking_state(session).get("last_sync_status") == "success"

    async def _submit_openviking_session_and_clear_if_committed(
        self,
        session: Session,
        *,
        force_commit: bool = False,
        keep_recent_turn_count: int | None = None,
        commit_message_threshold: int | None = None,
        openviking_connection: dict[str, Any] | None = None,
    ) -> bool:
        success = await self._submit_openviking_session(
            session,
            force_commit=force_commit,
            keep_recent_turn_count=keep_recent_turn_count,
            commit_message_threshold=commit_message_threshold,
            openviking_connection=openviking_connection,
        )
        if not success:
            return False
        if not get_openviking_state(session).get("last_commit_performed"):
            return True

        session.clear()
        reset_openviking_state(session, rotate_session_id=False)
        state = get_openviking_state(session)
        state["last_sync_status"] = "success"
        await self.sessions.save(session)
        return True

    async def _maybe_commit_openviking_before_turn(
        self,
        session: Session,
        msg: InboundMessage,
    ) -> None:
        if not self._ov_session_context_enabled():
            return

        agents_config = getattr(self.config, "agents", None)
        if agents_config is None:
            return

        state = get_openviking_state(session)
        pending_tokens = int(state.get("last_pending_tokens", 0) or 0)
        incoming_tokens = cal_str_tokens(msg.content or "")
        _, _, token_threshold = session_budget(agents_config)
        last_commit_local_index = parse_local_index(state.get("last_commit_local_index", -1))
        messages_since_commit = len(session.messages) - last_commit_local_index - 1
        incoming_messages_count = 1
        should_commit = bool(
            pending_tokens + incoming_tokens >= token_threshold
            or messages_since_commit + incoming_messages_count >= self.memory_window
        )
        if not should_commit:
            return

        await self._submit_openviking_session_and_clear_if_committed(
            session,
            force_commit=True,
            keep_recent_turn_count=int(
                getattr(agents_config, "commit_keep_recent_turn_count", 3) or 0
            ),
            commit_message_threshold=self.memory_window,
            openviking_connection=getattr(msg, "openviking_connection", None),
        )

    async def _commit_openviking_session(
        self,
        session: Session,
        *,
        keep_recent_turn_count: int = 0,
        clear_local_session: bool = False,
        rotate_session_id: bool = False,
        openviking_connection: dict[str, Any] | None = None,
    ) -> bool:
        success = await self._submit_openviking_session(
            session,
            force_commit=True,
            keep_recent_turn_count=keep_recent_turn_count,
            openviking_connection=openviking_connection,
        )
        if not success:
            return False
        if clear_local_session:
            session.clear()
            reset_openviking_state(session, rotate_session_id=rotate_session_id)
            state = get_openviking_state(session)
            state["last_sync_status"] = "success"
            await self.sessions.save(session)
        return True

    async def _consolidate_memory(
        self,
        session,
        archive_all: bool = False,
        openviking_connection: dict[str, Any] | None = None,
    ) -> bool:
        """Archive a cloned session; callers may trim history only after success."""
        try:
            if not session.messages:
                return True

            # use openviking tools to extract memory
            config = self.config
            if config.mode == BotMode.READONLY:
                if not config.channels_config or not config.channels_config.get_all_channels():
                    return False
                allow_from = [config.ov_server.admin_user_id]
                for channel_config in config.channels_config.get_all_channels():
                    if channel_config and channel_config.type.value == session.key.type:
                        if hasattr(channel_config, "allow_from"):
                            allow_from.extend(channel_config.allow_from)
                messages = [msg for msg in session.messages if msg.get("sender_id") in allow_from]
                session.messages = messages
            committed = await self._get_session_policy().archive(session, openviking_connection)
            if not committed:
                return False

            if self.sandbox_manager:
                memory_workspace = self.sandbox_manager.get_workspace_path(session.key)
            else:
                memory_workspace = self.workspace

            memory = MemoryStore(memory_workspace, config=self.config)
            if archive_all:
                old_messages = session.messages
                keep_count = 0
            else:
                keep_count = min(10, max(2, self.memory_window // 2))
                old_messages = session.messages[:-keep_count]
            if not old_messages:
                return True
            logger.info(
                f"Memory consolidation started: {len(session.messages)} messages, archiving {len(old_messages)}, keeping {keep_count}"
            )

            # Format messages for LLM (include tool names when available)
            lines = []
            for m in old_messages:
                if not m.get("content"):
                    continue
                tools_used = m.get("tools_used", [])
                if tools_used and isinstance(tools_used, list):
                    tool_names = [
                        tc.get("tool_name", "unknown") for tc in tools_used if isinstance(tc, dict)
                    ]
                    tools_str = f" [tools: {', '.join(tool_names)}]" if tool_names else ""
                else:
                    tools_str = ""
                lines.append(
                    f"[{m.get('timestamp', '?')[:16]}] {m['role'].upper()}{tools_str}: {m['content']}"
                )
            conversation = "\n".join(lines)
            current_memory = memory.read_long_term()

            prompt = f"""You are a memory consolidation agent. Process this conversation and return a JSON object with exactly two keys:

1. "history_entry": A paragraph (2-5 sentences) summarizing the key events/decisions/topics. Start with a timestamp like [YYYY-MM-DD HH:MM]. Include enough detail to be useful when found by grep search later.

2. "memory_update": The updated long-term memory content. Add any new facts: user location, preferences, personal info, habits, project context, technical decisions, tools/services used. If nothing new, return the existing content unchanged.

## Current Long-term Memory
{current_memory or "(empty)"}

## Conversation to Process
{conversation}

Respond with ONLY valid JSON, no markdown fences."""

            response = await self.provider.chat(
                messages=[
                    {
                        "role": "system",
                        "content": "You are a memory consolidation agent. Respond only with valid JSON.",
                    },
                    {"role": "user", "content": prompt},
                ],
                model=self.model,
                temperature=self.temperature,
                session_id=session.key.safe_name(),
            )
            text = (response.content or "").strip()
            if text.startswith("```"):
                text = text.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
            result = json.loads(text)

            entry = result.get("history_entry")
            if not isinstance(entry, str) or not entry.strip():
                raise ValueError("Memory consolidation returned no history entry")
            memory.append_history(entry)
            if update := result.get("memory_update"):
                if self.config.use_local_memory and update != current_memory:
                    memory.write_long_term(update)

            logger.info("Memory consolidation done")
            return True
        except Exception as e:
            logger.exception(f"Memory consolidation failed: {e}")
            return False

    async def _trim_consolidated_history(
        self,
        session: Session,
        snapshot: Session,
        *,
        keep_count: int,
        outcome_update: _ResponseOutcomeUpdate | None = None,
    ) -> bool:
        """Remove only archived snapshot messages, keeping concurrent session updates."""
        remove_count = max(0, len(snapshot.messages) - keep_count)

        def trim(current: Session) -> bool:
            # Merge the unsaved outcome unless a concurrent writer has updated it.
            if outcome_update is not None:
                outcomes = current.metadata.setdefault("response_outcomes", {})
                response_id = outcome_update.payload["response_id"]
                if outcomes.get(response_id) == outcome_update.previous:
                    outcomes[response_id] = outcome_update.payload
            if current.messages[: len(snapshot.messages)] != snapshot.messages:
                return False
            current.messages = current.messages[remove_count:]
            if not current.messages:
                current.clear()
            return True

        current, trimmed = await self.sessions.update_session(
            session.key, trim, skip_heartbeat=session.key.type == "cli"
        )
        # The running turn still holds the original Session object.
        session.messages = current.messages
        session.metadata = current.metadata
        session.created_at = current.created_at
        session.updated_at = current.updated_at
        return trimmed

    async def _consolidate_viking_memory(
        self,
        session,
        openviking_connection: dict[str, Any] | None = None,
    ) -> bool:
        """Submit a cloned session and report the hook's actual outcome."""
        try:
            if not session.messages:
                logger.info(
                    f"No messages to commit openviking for session {session.key.safe_name()} (allow_from filter applied)"
                )
                return True

            # Async HookManager results are discarded; confirm this call through session state.
            get_openviking_state(session).pop("last_sync_status", None)
            await hook_manager.execute_hooks(
                context=HookContext(
                    event_type="message.compact",
                    session_id=session.key.safe_name(),
                    workspace_id=self._get_ov_workspace_id(session.key),
                    session_key=session.key,
                    config=self.config,
                    openviking_connection=openviking_connection,
                ),
                session=session,
                openviking_connection=openviking_connection,
            )
            return get_openviking_state(session).get("last_sync_status") == "success"
        except Exception as e:
            logger.exception(f"Memory consolidation failed: {e}")
            return False

    def _resolve_message_options(
        self, msg: InboundMessage, *, ov_tools_enable: bool
    ) -> _MessageOptions:
        """Resolve request overrides and channel defaults for initial and steer input."""
        connection = getattr(msg, "openviking_connection", None)
        actor_peer_id = getattr(msg, "actor_peer_id", None) or msg.sender_id
        memory_peer_ids = self._metadata_memory_peer_ids(msg.metadata)
        memory_owner_user_ids = self._metadata_memory_owner_user_ids(msg.metadata)
        channel_config = self._get_channel_config(msg.session_key)
        profile_user_list = []
        if channel_config and ov_tools_enable:
            profile_user_list = getattr(channel_config, "profile_user_list", [])
            if not memory_peer_ids:
                memory_peer_ids = self._channel_memory_peer_ids(channel_config)
            if not memory_owner_user_ids:
                memory_owner_user_ids = self._channel_memory_owner_user_ids(channel_config)
        return _MessageOptions(
            openviking_connection=connection if isinstance(connection, dict) else None,
            actor_peer_id=actor_peer_id,
            profile_user_list=profile_user_list,
            memory_peer_ids=memory_peer_ids,
            memory_owner_user_ids=memory_owner_user_ids,
        )

    def _get_channel_config(self, session_key: SessionKey):
        """Get channel config for a session key.

        Args:
            session_key: Session key to get channel config for

        Returns:
            Channel config object if found, None otherwise
        """
        return self.config.channels_config.get_channel_by_key(session_key.channel_key())

    @staticmethod
    def _normalize_id_list(value: Any) -> list[str]:
        if value is None:
            return []
        if isinstance(value, str):
            value = [value]
        if not isinstance(value, list):
            return []
        normalized: list[str] = []
        for item in value:
            item_str = str(item).strip()
            if item_str and item_str not in normalized:
                normalized.append(item_str)
        return normalized

    def _metadata_memory_peer_ids(self, metadata: dict[str, Any] | None) -> list[str]:
        if not isinstance(metadata, dict):
            return []
        return self._normalize_id_list(metadata.get("memory_peers"))

    def _metadata_memory_owner_user_ids(self, metadata: dict[str, Any] | None) -> list[str]:
        if not isinstance(metadata, dict):
            return []
        return self._normalize_id_list(metadata.get("memory_users"))

    def _channel_memory_peer_ids(self, channel_config: Any) -> list[str]:
        return self._normalize_id_list(getattr(channel_config, "memory_peer", None))

    def _channel_memory_owner_user_ids(self, channel_config: Any) -> list[str]:
        return self._normalize_id_list(getattr(channel_config, "memory_user", None))

    def _get_ov_tools_enable(self, session_key: SessionKey) -> bool:
        """Get ov_tools_enable setting from channel config.

        Args:
            session_key: Session key to get channel config for

        Returns:
            True if ov tools should be enabled, False otherwise
        """
        if not self.config.ov_server.is_available():
            return False
        channel_config = self._get_channel_config(session_key)
        return getattr(channel_config, "ov_tools_enable", True) if channel_config else True

    def _check_cmd_auth(self, msg: InboundMessage) -> bool:
        """Check if the session key is authorized for command execution.

        Returns:
            True if authorized, False otherwise.
        Args:
            session_key: Session key to check.
        """
        if self.config.mode == BotMode.NORMAL:
            return True
        allow_from = []
        if self.config.ov_server and self.config.ov_server.admin_user_id:
            allow_from.append(self.config.ov_server.admin_user_id)
        channel_config = self._get_channel_config(msg.session_key)
        if channel_config:
            allow_cmd = getattr(channel_config, "allow_cmd_from", [])
            if allow_cmd:
                allow_from.extend(allow_cmd)

        # If channel not found or sender not in allow_from list, ignore message
        if msg.sender_id not in allow_from:
            logger.debug(
                f"Sender {msg.sender_id} not allowed in channel {msg.session_key.channel_key()}"
            )
            return False
        return True

    async def _handle_session_command(
        self,
        msg: InboundMessage,
        session: Session,
        ov_tools_enable: bool,
        openviking_connection: dict[str, Any] | None,
        *,
        is_group_chat: bool,
    ) -> OutboundMessage | None:
        """Handle session slash commands at the inbound-message boundary."""
        if is_group_chat:
            cmd = msg.content
            cmd = re.sub(r"^\[[^\]]+\]:\s*", "", cmd)
            cmd = cmd.replace(f"@{msg.sender_id}", "").strip().lower()
        else:
            cmd = msg.content.strip().lower()
        if cmd == "/new":
            if not self._check_cmd_auth(msg):
                return OutboundMessage(
                    session_key=msg.session_key,
                    content="🐈 Sorry, you are not authorized to use this command.",
                    metadata=msg.metadata,
                )

            await self.sessions.update_session(
                msg.session_key,
                self._get_session_policy().reset_session,
                skip_heartbeat=msg.session_key.type == "cli",
            )
            return OutboundMessage(
                session_key=msg.session_key,
                content="🐈 New session started. Session history droped.",
                metadata=msg.metadata,
            )
        elif cmd == "/compact":
            if not self._check_cmd_auth(msg):
                return OutboundMessage(
                    session_key=msg.session_key,
                    content="🐈 Sorry, you are not authorized to use this command.",
                    metadata=msg.metadata,
                )
            committed = await self._get_session_policy().compact(session, openviking_connection)
            if not committed:
                return OutboundMessage(
                    session_key=msg.session_key,
                    content="🐈 Memory consolidation failed. Session history was kept.",
                    metadata=msg.metadata,
                )
            return OutboundMessage(
                session_key=msg.session_key,
                content="🐈 New session started. Memory consolidated.",
                metadata=msg.metadata,
            )
        if cmd == "/remember":
            if not self._check_cmd_auth(msg):
                return OutboundMessage(
                    session_key=msg.session_key,
                    content="🐈 Sorry, you are not authorized to use this command.",
                    metadata=msg.metadata,
                )
            remembered = await self._get_session_policy().remember(
                session, openviking_connection, ov_tools_enable=ov_tools_enable
            )
            if remembered is None:
                return OutboundMessage(
                    session_key=msg.session_key,
                    content="Memory storage is not enabled for this session.",
                    metadata=msg.metadata,
                )
            if not remembered:
                return OutboundMessage(
                    session_key=msg.session_key,
                    content="Failed to submit this conversation to memory storage.",
                    metadata=msg.metadata,
                )
            return OutboundMessage(
                session_key=msg.session_key,
                content="This conversation has been submitted to memory storage.",
                metadata=msg.metadata,
            )
        if cmd == "/help":
            return OutboundMessage(
                session_key=msg.session_key,
                content="🐈 vikingbot commands:\n/new — Start a new conversation\n/remember — Submit current session to memories and start new session\n/help — Show available commands",
                metadata=msg.metadata,
            )

        return None

    async def _process_system_message(self, msg: InboundMessage) -> OutboundMessage | None:
        """
        Process a system message (e.g., subagent announce).

        The chat_id field contains "original_channel:original_chat_id" to route
        the response back to the correct destination.
        """
        logger.info(f"Processing system message from {msg.sender_id}")

        session = self.sessions.get_or_create(msg.session_key)

        # Get channel config
        ov_tools_enable = self._get_ov_tools_enable(msg.session_key)
        profile_user_list = []
        channel_config = self._get_channel_config(msg.session_key)
        if channel_config and ov_tools_enable:
            profile_user_list = getattr(channel_config, "profile_user_list", [])

        # Build messages with the announce content
        provider_name = self.config.get_provider_name(self.model) if self.config else None
        actor_peer_id = getattr(msg, "actor_peer_id", None) or msg.sender_id
        history = await self._build_prompt_history(
            session,
            provider_name=provider_name,
            openviking_connection=msg.openviking_connection,
            actor_peer_id=actor_peer_id,
        )
        from vikingbot.agent.context import ContextBuilder

        message_workspace = (
            self.sandbox_manager.get_workspace_path(msg.session_key)
            if self.sandbox_manager
            else self.workspace
        )
        message_context = ContextBuilder(
            message_workspace,
            sandbox_manager=self.sandbox_manager,
            sender_id=msg.sender_id,
            actor_peer_id=actor_peer_id,
            openviking_connection=msg.openviking_connection,
            enable_subagents=self._subagents_enabled(),
            config=self.config,
        )
        messages = await message_context.build_messages(
            history=history,
            current_message=msg.content,
            session_key=msg.session_key,
            ov_tools_enable=ov_tools_enable,
            profile_user_list=profile_user_list,
        )

        # Run agent loop (no events published)
        agent_turns: list[dict[str, Any]] = []
        (
            final_content,
            final_reasoning_content,
            tools_used,
            token_usage,
            iteration,
        ) = await self._run_agent_loop(
            messages=messages,
            session_key=msg.session_key,
            publish_events=False,
            sender_id=msg.sender_id,
            actor_peer_id=actor_peer_id,
            ov_tools_enable=ov_tools_enable,
            memory_peer_ids=None,
            openviking_connection=msg.openviking_connection,
            channel_metadata=msg.metadata,
            captured_turns=agent_turns,
        )

        if final_content is None or (isinstance(final_content, str) and not final_content.strip()):
            final_content = "Background task completed."

        # Save to session (mark as system message in history)
        session.add_message("user", f"[System: {msg.sender_id}] {msg.content}")
        session.add_message(
            "assistant",
            final_content,
            tools_used=tools_used if tools_used else None,
            reasoning_content=final_reasoning_content,
            agent_turns=agent_turns if agent_turns else None,
        )
        await self.sessions.save(session)

        return OutboundMessage(
            session_key=msg.session_key,
            content=final_content,
            metadata=dict(msg.metadata or {}),
        )

    async def interrupt(self, session_key: SessionKey) -> bool:
        """Interrupt the active or already queued turn for a session."""
        active = self._active_turn
        if (
            active
            and active.session_key == session_key
            and active.interruptible
            and not active.task.done()
        ):
            pending_count = self.bus.count_pending_inbound_session(session_key)
            if pending_count:
                self._queued_interrupt_counts[session_key] = pending_count
            active.interrupted = True
            active.task.cancel()
            return True
        pending_count = self.bus.count_pending_inbound_session(session_key)
        queued_interrupts = self._queued_interrupt_counts.get(session_key, 0)
        if queued_interrupts < pending_count:
            self._queued_interrupt_counts[session_key] = pending_count
            return True
        return False

    def steer(self, msg: InboundMessage) -> bool:
        """Attach same-session input directly to the active turn."""
        active = self._active_turn
        if (
            active is None
            or active.session_key != msg.session_key
            or not active.accepting_steer
            or active.interrupted
            or active.task.done()
            or not msg.need_reply
            or msg.content.strip().startswith("/")
        ):
            return False
        active.accepted_steers.append(
            _AcceptedSteer(
                msg=msg,
                task=asyncio.create_task(self._prepare_steer_message(msg)),
            )
        )
        return True

    async def _process_message_as_active_turn(self, msg: InboundMessage) -> OutboundMessage | None:
        active: _ActiveTurn

        async def run_active_message() -> OutboundMessage | None:
            return await self._process_message(msg, active_turn=active)

        task = asyncio.create_task(run_active_message())
        is_agent_turn = bool(
            msg.need_reply
            and msg.session_key.type != "system"
            and self.config.mode != BotMode.DEBUG
            and not msg.content.strip().startswith("/")
        )
        active = _ActiveTurn(
            msg.session_key,
            task,
            interruptible=is_agent_turn,
            accepting_steer=is_agent_turn,
        )
        self._active_turn = active
        try:
            response = await task
            if active.accepted_steers:
                raise RuntimeError("Active turn completed with unconsumed steer input")
            return response
        except asyncio.CancelledError:
            if active.interrupted:
                logger.info(f"Interrupted message from {msg.session_key}")
                return await self._complete_interrupted_message(msg)
            raise
        except Exception:
            if self._active_turn is active and (active.history_items or active.accepted_steers):
                await self._complete_interrupted_message(msg)
            raise
        finally:
            if self._active_turn is active:
                # Clear the active pointer before cleanup so later interrupt calls
                # cannot cancel this turn while its local teardown is finishing.
                self._active_turn = None

    async def _prepare_steer_message(self, msg: InboundMessage) -> _PreparedSteer:
        try:
            session_key = msg.session_key
            session = self.sessions.get_or_create(
                session_key,
                skip_heartbeat=session_key.type == "cli",
            )
            ov_tools_enable = self._get_ov_tools_enable(session_key)
            message_options = self._resolve_message_options(msg, ov_tools_enable=ov_tools_enable)
            openviking_connection = message_options.openviking_connection
            actor_peer_id = message_options.actor_peer_id
            memory_peer_ids = message_options.memory_peer_ids
            memory_owner_user_ids = message_options.memory_owner_user_ids
            profile_user_list = message_options.profile_user_list

            message_workspace = (
                self.sandbox_manager.get_workspace_path(session_key)
                if self.sandbox_manager
                else self.workspace
            )
            message_context = ContextBuilder(
                message_workspace,
                sandbox_manager=self.sandbox_manager,
                sender_id=msg.sender_id,
                actor_peer_id=actor_peer_id,
                sender_name=msg.sender_name,
                is_group_chat=msg.metadata.get("chat_type") == "group",
                eval=self._eval,
                openviking_connection=openviking_connection,
                enable_subagents=self._subagents_enabled(),
                config=self.config,
            )
            exp_exclude_uris = session.metadata.get("recalled_exp_uris", [])
            if not isinstance(exp_exclude_uris, list):
                exp_exclude_uris = []
            prepared_messages = await message_context.build_messages(
                history=[],
                current_message=msg.content,
                media=msg.media if msg.media else None,
                session_key=msg.session_key,
                ov_tools_enable=ov_tools_enable,
                profile_user_list=profile_user_list,
                memory_peer_ids=memory_peer_ids,
                memory_owner_user_ids=memory_owner_user_ids,
                exp_exclude_uris=exp_exclude_uris,
                include_system=False,
            )
            auto_memory_tool = None
            if ov_tools_enable and message_context.latest_relevant_memories:
                auto_memory_tool = await self._publish_auto_memory_context(
                    session_key=session_key,
                    query=msg.content,
                    result=message_context.latest_relevant_memories,
                    publish_events=False,
                )
            return _PreparedSteer(
                msg=msg,
                prompt_messages=prepared_messages,
                newly_recalled_exp_uris=list(message_context.latest_recalled_exp_uris),
                newly_recalled_exp_content=message_context.latest_recalled_exp_content,
                auto_memory_tool=auto_memory_tool,
            )
        except Exception:
            logger.exception("Failed to prepare steer context; using raw input")
        return _PreparedSteer(
            msg=msg,
            prompt_messages=[
                {
                    "role": "user",
                    "content": self.context._build_user_content(msg.content, msg.media),
                }
            ],
            newly_recalled_exp_uris=[],
            newly_recalled_exp_content="",
        )

    async def _take_accepted_steers(
        self,
        active: _ActiveTurn | None,
    ) -> list[_PreparedSteer]:
        if active is None:
            return []
        prepared: list[_PreparedSteer] = []
        prepared_count = 0
        # Keep ownership on the active turn until every observed batch is ready,
        # so interrupt can still persist all accepted raw inputs at any await.
        while prepared_count < len(active.accepted_steers):
            accepted = active.accepted_steers[prepared_count:]
            batch = await asyncio.gather(*(steer.task for steer in accepted))
            prepared.extend(batch)
            prepared_count += len(accepted)
        del active.accepted_steers[:prepared_count]
        return prepared

    @staticmethod
    def _append_active_turn_history(
        session: Session,
        items: list[_PreparedSteer | _AssistantBoundary],
    ) -> None:
        for item in items:
            if isinstance(item, _AssistantBoundary):
                session.add_message(
                    "assistant",
                    item.content,
                    reasoning_content=item.reasoning_content,
                )
            else:
                if item.newly_recalled_exp_content:
                    session.add_message(
                        "user",
                        "[Experience Reminder]\n"
                        f"## Relevant Agent Experience\n{item.newly_recalled_exp_content}",
                        exp_uris=item.newly_recalled_exp_uris,
                        experience_reminder=True,
                    )
                if item.newly_recalled_exp_uris:
                    recalled_uris = session.metadata.setdefault("recalled_exp_uris", [])
                    for uri in item.newly_recalled_exp_uris:
                        if uri not in recalled_uris:
                            recalled_uris.append(uri)
                session.add_message(
                    "user",
                    item.msg.content,
                    sender_id=item.msg.sender_id,
                )

    async def _complete_interrupted_message(self, msg: InboundMessage) -> OutboundMessage:
        session_key = msg.session_key
        skip_heartbeat = session_key.type == "cli"
        session = self.sessions.get_or_create(session_key, skip_heartbeat=skip_heartbeat)
        session.add_message("user", msg.content, sender_id=msg.sender_id)
        active = self._active_turn
        accepted_steers: list[_AcceptedSteer] = []
        if active is not None and active.session_key == session_key:
            accepted_steers = active.accepted_steers
            consumed = (
                item.msg for item in active.history_items if isinstance(item, _PreparedSteer)
            )
            pending = (steer.msg for steer in accepted_steers)
            for steer_msg in (*consumed, *pending):
                session.add_message(
                    "user",
                    steer_msg.content,
                    sender_id=steer_msg.sender_id,
                )
        await self.sessions.save(session)
        for steer in accepted_steers:
            steer.task.cancel()
        await asyncio.gather(
            *(steer.task for steer in accepted_steers),
            return_exceptions=True,
        )
        accepted_steers.clear()
        return OutboundMessage(
            session_key=msg.session_key,
            content="",
            event_type=OutboundEventType.INTERRUPT,
            metadata={**(msg.metadata or {}), "reason": "interrupted"},
        )
