"""Context builder for assembling agent prompts."""

import base64
import mimetypes
import platform
import time as _time
from datetime import datetime
from pathlib import Path
from typing import Any

from loguru import logger

from vikingbot.agent.memory import MemoryStore
from vikingbot.agent.skills import SkillsLoader
from vikingbot.config.schema import SessionKey
from vikingbot.sandbox import SandboxManager
from vikingbot.utils.helpers import ensure_non_empty_assistant_content


class ContextBuilder:
    """
    Builds the context (system prompt + messages) for the agent.

    Assembles bootstrap files, memory, skills, and conversation history
    into a coherent prompt for the LLM.
    """

    BOOTSTRAP_FILES = ["AGENTS.md", "SOUL.md", "TOOLS.md", "IDENTITY.md"]
    INIT_DIR = "init"

    def __init__(
        self,
        workspace: Path,
        sandbox_manager: SandboxManager | None = None,
        sender_id: str = None,
        sender_name: str = None,
        is_group_chat: bool = False,
        eval: bool = False,
        openviking_connection: dict[str, Any] | None = None,
        enable_subagents: bool = True,
    ):
        self.workspace = workspace
        self._templates_ensured = False
        self.sandbox_manager = sandbox_manager
        self._memory = None
        self._skills = None
        self._sender_id = sender_id
        self._sender_name = sender_name
        self._is_group_chat = is_group_chat
        self._eval = eval
        self._openviking_connection = openviking_connection
        self._enable_subagents = enable_subagents
        self.latest_relevant_memories: str | None = None

    @property
    def memory(self):
        """Lazy-load MemoryStore when first needed."""
        if self._memory is None:
            self._memory = MemoryStore(self.workspace)
        return self._memory

    @property
    def skills(self):
        """Lazy-load SkillsLoader when first needed."""
        if self._skills is None:
            self._skills = SkillsLoader(self.workspace)
        return self._skills

    def _ensure_templates_once(self):
        """Ensure workspace templates only once, when first needed."""
        if not self._templates_ensured:
            from vikingbot.utils.helpers import ensure_workspace_templates

            ensure_workspace_templates(self.workspace)
            self._templates_ensured = True

    def _get_workspace_id(self, session_key: SessionKey) -> str:
        if self.sandbox_manager:
            return self.sandbox_manager.to_workspace_id(session_key)
        return session_key.safe_name()

    @staticmethod
    def _dedupe_ids(values: list[str] | None, *, exclude: set[str] | None = None) -> list[str]:
        exclude = exclude or set()
        normalized: list[str] = []
        for value in values or []:
            value_str = str(value).strip()
            if value_str and value_str not in exclude and value_str not in normalized:
                normalized.append(value_str)
        return normalized

    async def build_system_prompt(
        self,
        session_key: SessionKey,
        ov_tools_enable: bool = True,
        profile_user_list: list[str] | None = None,
        memory_peer_ids: list[str] | None = None,
        memory_owner_user_ids: list[str] | None = None,
    ) -> str:
        """
        Build the system prompt from bootstrap files, memory, and skills.

        Args:
            session_key: Session key for the context.
            ov_tools_enable: Whether to enable OpenViking tools and memory.
            profile_user_list: Deprecated list of additional peer IDs to fetch profiles for.
            memory_peer_ids: Peer IDs used for memory retrieval; profiles are fetched too.
            memory_owner_user_ids: Deprecated owner-user IDs used for trusted-mode lookup.

        Returns:
            Complete system prompt.
        """
        # Ensure workspace templates exist only when first needed
        self._ensure_templates_once()
        workspace_id = self._get_workspace_id(session_key)

        parts = []

        # Core identity
        parts.append(await self._get_identity(session_key))

        # Sandbox environment info
        if self.sandbox_manager:
            sandbox_cwd = await self.sandbox_manager.get_sandbox_cwd(session_key)
            parts.append(
                f"## Sandbox Environment\n\nYou are running in a sandboxed environment. All file operations and command execution are restricted to the sandbox directory.\nThe sandbox root directory is `{sandbox_cwd}` (use relative paths for all operations)."
            )

        # Bootstrap files
        bootstrap = self._load_bootstrap_files()
        if bootstrap:
            parts.append(bootstrap)

        # Skills - progressive loading
        # 1. Always-loaded skills: include full content
        always_skills = self.skills.get_always_skills()
        if always_skills:
            always_content = self.skills.load_skills_for_context(always_skills)
            if always_content:
                parts.append(f"# Active Skills\n\n{always_content}")

        # 2. Available skills: only show summary (agent uses read_file to load)
        skills_summary = self.skills.build_skills_summary()
        if skills_summary:
            required_skill_note = ""
            required_skill_candidates = [
                "skills/experience_loader/SKILL.md",
                "skills/task_case_experience/SKILL.md",
            ]
            for skill_path in required_skill_candidates:
                if (self.workspace / skill_path).exists():
                    required_skill_note = (
                        "\nRequired skill: before taking any task action, you MUST read "
                        f"`{skill_path}` and apply its instructions.\n"
                    )
                    break
            parts.append(f"""# Skills

The following skills extend your capabilities. To use a skill, read its SKILL.md file using the read_file tool.
Skills with available="false" need dependencies installed first - you can try installing them with apt/brew.
{required_skill_note}
{skills_summary}""")

        # Viking peer profile (only if ov tools are enabled). In the current
        # OpenViking identity model, the bot API key owns the User, and the
        # message sender is represented as a peer under that User.
        if ov_tools_enable:
            # Fetch current sender's peer profile
            start = _time.time()
            profile = await self.memory.get_viking_peer_profile(
                workspace_id=workspace_id,
                peer_id=self._sender_id,
                openviking_connection=self._openviking_connection,
                actor_peer_id=self._sender_id,
            )
            cost = round(_time.time() - start, 2)
            logger.info(
                f"[READ_PEER_PROFILE]: cost {cost}s, profile={profile[:50] if profile else 'None'}"
            )
            if profile:
                parts.append(f"## Current sender's information\n{profile}")

            # Fetch additional peer profiles from profile_user_list and from the
            # peers used for memory retrieval. The profile_user_list config name
            # is retained for compatibility with older deployments.
            additional_peer_ids = self._dedupe_ids(
                [*(profile_user_list or []), *(memory_peer_ids or [])],
                exclude={self._sender_id} if self._sender_id else set(),
            )
            if additional_peer_ids:
                profiles = await self.memory.get_viking_peer_profiles(
                    workspace_id=workspace_id,
                    peer_ids=additional_peer_ids,
                    openviking_connection=self._openviking_connection,
                    use_peer_actor_scope=bool(self._sender_id),
                )
                if profiles:
                    parts.append(profiles)

        return "\n\n---\n\n".join(parts)

    async def _build_user_memory(
        self,
        session_key: SessionKey,
        current_message: str,
        sender_id: str,
        memory_peer_ids: list[str] | None = None,
        memory_owner_user_ids: list[str] | None = None,
        ov_tools_enable: bool = True,
        experience_recall_enable: bool | None = None,
    ) -> str:
        """
        Build the system prompt from bootstrap files, memory, and skills.

        Args:
            skill_names: Optional list of skills to include.

        Returns:
            Complete system prompt.
        """
        parts = []
        now = datetime.now().strftime("%Y-%m-%d %H:%M (%A)")
        tz = _time.strftime("%Z") or "UTC"
        parts.append(f"## Current Time: {now} ({tz})")

        # Add session context
        session_context = "## Current Session"
        if session_key and session_key.type:
            session_context += f"\nChannel: {session_key.type}"
            if self._is_group_chat:
                session_context += (
                    f"\n**Group chat session.** Current user: {self._sender_name if self._sender_name else self._sender_id}\n"
                    f"Multiple users can participate in this conversation. Each user message is prefixed with the user's name in brackets like '[张三]: 你好'. "
                    f"You should pay attention to who is speaking to understand the context. "
                )
        parts.append(session_context)

        workspace_id = self._get_workspace_id(session_key)

        if ov_tools_enable:
            start = _time.time()
            # Default recall runs under the configured/request OpenViking user.
            # sender_id is passed separately as peer identity.
            search_peer_ids = memory_peer_ids if memory_peer_ids else None
            viking_memory = await self.memory.get_viking_memory_context(
                current_message=current_message,
                workspace_id=workspace_id,
                sender_id=sender_id,
                peer_ids=search_peer_ids,
                user_ids=memory_owner_user_ids if memory_owner_user_ids else None,
                openviking_connection=self._openviking_connection,
            )
            logger.info(f"viking_memory={viking_memory}")
            cost = round(_time.time() - start, 2)
            logger.info(
                f"[READ_USER_MEMORY]: cost {cost}s, "
                f"memory={viking_memory[:50] if viking_memory else 'None'}"
            )
            if viking_memory:
                self.latest_relevant_memories = viking_memory
                parts.append(f"## openviking_search(query=[user_query])\n{viking_memory}")

            parts.append(
                "## OpenViking Memory Retrieval\n"
                "- For questions about the user's remembered facts, preferences, profile, or personal context, use openviking_search for the current question before saying there is no relevant record.\n"
                "- A previous empty search result does not prove that a different follow-up question has no memory; search again when the requested fact changes.\n"
                "- Injected memories are grouped by memory_type: events contain atomic time-based facts; entities contain stable topic/entity facts; preferences contain likes, habits, and recurring tendencies.\n"
                "- Injected memory entries use three types: full means the full memory content is already shown; summary means only a summary is shown and the URI has more detail; uri means only the URI is shown and it may still point to key facts.\n"
                "- For relevant summary or uri entries, use openviking_multi_read on their URIs to fetch full details to help you to resolve the query. "
            )



        parts.append(
            "Reply in the same language as the user's query, ignoring the language of the reference materials. User's query:"
        )

        return "\n\n---\n\n".join(parts)

    async def _get_identity(self, session_key: SessionKey) -> str:
        """Get the core identity section."""

        workspace_path = str(self.workspace.expanduser().resolve())
        system = platform.system()
        runtime = f"{'macOS' if system == 'Darwin' else system} {platform.machine()}, Python {platform.python_version()}"

        # Determine workspace display based on sandbox state
        if self.sandbox_manager:
            workspace_display = await self.sandbox_manager.get_sandbox_cwd(session_key)
        else:
            workspace_display = workspace_path

        capabilities = [
            "- Read, search, and grep OpenViking files",
            "- Read, write, and edit local files",
            "- Execute shell commands",
            "- Search the web and fetch web pages",
            "- Send messages to users on chat channels",
        ]
        if self._enable_subagents:
            capabilities.append("- Spawn subagents for complex background tasks")
        capabilities_text = "\n".join(capabilities)

        return f"""# vikingbot 🐈

You are VikingBot, an AI assistant built based on the OpenViking context database.
When acquiring information, data, and knowledge, you **prioritize using openviking tools to read and search OpenViking (a context database) above all other sources**.
You have access to tools that allow you to:
{capabilities_text}

## Runtime
{runtime}

## Workspace
You have two workspaces:
1. Local workspace: {workspace_display}
2. OpenViking workspace: managed via OpenViking tools
- Custom skills: {workspace_display}/skills/{{skill-name}}/SKILL.md

IMPORTANT:
- When responding to direct questions or conversations, reply directly with your text response.
- Only use the 'message' tool when you need to send a message to a specific chat channel (like WhatsApp).For normal conversation, just respond with text - do not call the message tool.
- Always be helpful, accurate, and concise. When using tools, think step by step: what you know, what you need, and why you chose this tool.

## Memory
- Remember important facts: using openviking_memory_commit tool to commit"""

    def _load_bootstrap_files(self) -> str:
        """Load all bootstrap files from workspace."""
        parts = []

        for filename in self.BOOTSTRAP_FILES:
            file_path = self.workspace / filename
            if file_path.exists():
                content = file_path.read_text(encoding="utf-8")
                if content:
                    parts.append(f"## {filename}\n\n{content}")

        return "\n\n".join(parts) if parts else ""

    async def build_messages(
        self,
        history: list[dict[str, Any]],
        current_message: str,
        media: list[str] | None = None,
        session_key: SessionKey | None = None,
        ov_tools_enable: bool = True,
        profile_user_list: list[str] | None = None,
        memory_peer_ids: list[str] | None = None,
        memory_owner_user_ids: list[str] | None = None,
        experience_recall_enable: bool | None = None,
        exp_exclude_uris: list[str] | None = None,
        experience_case_lookup: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        """
        Build the complete message list for an LLM call.

        Args:
            history: Previous conversation messages.
            current_message: The new user message.
            media: Optional list of local file paths for images/media.
            session_key: Optional session key.
            ov_tools_enable: Whether to enable OpenViking tools and memory.
            profile_user_list: Deprecated list of additional peer IDs to fetch profiles for.
            memory_peer_ids: Optional list of peer IDs to fetch memory for.
            memory_owner_user_ids: Deprecated legacy owner-user IDs used for root-key fanout.
            experience_recall_enable: Whether automatic experience recall may run independently
                from exposing OpenViking tools. Defaults to ov_tools_enable.
            exp_exclude_uris: Optional list of experience URIs that have already been recalled
                in this session and should be skipped (deduplication).

        Returns:
            List of messages including system prompt.
        """
        messages = []

        # System prompt
        system_prompt = await self.build_system_prompt(
            session_key,
            ov_tools_enable=ov_tools_enable,
            profile_user_list=profile_user_list,
            memory_peer_ids=memory_peer_ids,
            memory_owner_user_ids=memory_owner_user_ids,
        )
        messages.append({"role": "system", "content": system_prompt})

        # History
        if not self._eval:
            messages.extend(history)

        # Experience recall (reminder message, deduplicated by URI)
        self.latest_recalled_exp_content = ""
        self.latest_recalled_exp_uris: list[str] = []
        if experience_recall_enable is None:
            experience_recall_enable = ov_tools_enable
        if experience_recall_enable and session_key:
            workspace_id = self._get_workspace_id(session_key)
            start = _time.time()
            exp_content, exp_uris = await self.memory.get_viking_experience_reminder(
                query=current_message,
                workspace_id=workspace_id,
                exclude_uris=exp_exclude_uris,
                openviking_connection=self._openviking_connection,
                case_lookup=experience_case_lookup,
            )
            cost = round(_time.time() - start, 2)
            logger.info(
                f"[READ_EXP_AUTO]: cost {cost}s, "
                f"new={len(exp_uris)} uris, "
                f"exp={exp_content[:50] if exp_content else 'None'}"
            )
            if exp_content:
                self.latest_recalled_exp_content = exp_content
                self.latest_recalled_exp_uris = exp_uris
                messages.append(
                    {
                        "role": "user",
                        "content": f"[Experience Reminder]\n## Relevant Agent Experience\n{exp_content}",
                    }
                )

        # User
        user_info = await self._build_user_memory(
            session_key,
            current_message,
            self._sender_id,
            memory_peer_ids,
            memory_owner_user_ids,
            ov_tools_enable=ov_tools_enable,
            experience_recall_enable=experience_recall_enable,
        )
        messages.append({"role": "user", "content": user_info})

        # Current message (with optional image attachments)
        user_content = self._build_user_content(current_message, media)
        messages.append({"role": "user", "content": user_content})

        return messages

    def _build_user_content(self, text: str, media: list[str] | None) -> str | list[dict[str, Any]]:
        """Build user message content with optional base64-encoded images."""
        if not media:
            return text

        images = []
        for path in media:
            p = Path(path)
            mime, _ = mimetypes.guess_type(path)
            if not p.is_file() or not mime or not mime.startswith("image/"):
                continue
            b64 = base64.b64encode(p.read_bytes()).decode()
            images.append({"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}})
            images.append({"type": "text", "text": f"image saved to {path}"})

        if not images:
            return text
        return images + [{"type": "text", "text": text}]

    def add_tool_result(
        self, messages: list[dict[str, Any]], tool_call_id: str, tool_name: str, result: str
    ) -> list[dict[str, Any]]:
        """
        Add a tool result to the message list.

        Args:
            messages: Current message list.
            tool_call_id: ID of the tool call.
            tool_name: Name of the tool.
            result: Tool execution result.

        Returns:
            Updated message list.
        """
        messages.append(
            {"role": "tool", "tool_call_id": tool_call_id, "name": tool_name, "content": result}
        )
        return messages

    def add_assistant_message(
        self,
        messages: list[dict[str, Any]],
        content: str | None,
        tool_calls: list[dict[str, Any]] | None = None,
        reasoning_content: str | None = None,
    ) -> list[dict[str, Any]]:
        """
        Add an assistant message to the message list.

        Args:
            messages: Current message list.
            content: Message content.
            tool_calls: Optional tool calls.
            reasoning_content: Thinking output (Kimi, DeepSeek-R1, etc.).

        Returns:
            Updated message list.
        """
        msg: dict[str, Any] = {"role": "assistant"}

        # Moonshot rejects empty/whitespace assistant content (incl. tool-only turns).
        msg["content"] = ensure_non_empty_assistant_content(content)

        if tool_calls:
            msg["tool_calls"] = tool_calls

        # Thinking models reject history without this
        if reasoning_content:
            msg["reasoning_content"] = reasoning_content

        messages.append(msg)
        return messages
