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
        is_group_chat: bool = False,
        eval: bool = False,
    ):
        self.workspace = workspace
        self._templates_ensured = False
        self.sandbox_manager = sandbox_manager
        self._memory = None
        self._skills = None
        self._sender_id = sender_id
        self._is_group_chat = is_group_chat
        self._eval = eval

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

    async def build_system_prompt(
        self, session_key: SessionKey, current_message: str, history: list[dict[str, Any]], ov_tools_enable: bool = True
    ) -> str:
        """
        Build the system prompt from bootstrap files, memory, and skills.

        Args:
            skill_names: Optional list of skills to include.

        Returns:
            Complete system prompt.
        """
        # Ensure workspace templates exist only when first needed
        self._ensure_templates_once()
        workspace_id = self.sandbox_manager.to_workspace_id(session_key)

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

        # Memory context
        # memory = self.memory.get_memory_context()
        # if memory:
        #     parts.append(f"# Memory\n\n{memory}")

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
            parts.append(f"""# Skills

The following skills extend your capabilities. To use a skill, read its SKILL.md file using the read_file tool.
Skills with available="false" need dependencies installed first - you can try installing them with apt/brew.

{skills_summary}""")

        # Viking user profile (only if ov tools are enabled)
        if ov_tools_enable:
            start = _time.time()
            profile = await self.memory.get_viking_user_profile(
                workspace_id=workspace_id, user_id=self._sender_id
            )
            cost = round(_time.time() - start, 2)
            logger.info(
                f"[READ_USER_PROFILE]: cost {cost}s, profile={profile[:50] if profile else 'None'}"
            )
            if profile:
                parts.append(f"## Current user's information\n{profile}")

        return "\n\n---\n\n".join(parts)

    async def _build_user_memory(
        self, session_key: SessionKey, current_message: str, sender_id: str, ov_tools_enable: bool = True
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
                    f"\n**Group chat session.** Current user ID: {self._sender_id}\n"
                    f"Multiple users can participate in this conversation. Each user message is prefixed with the user ID in brackets like @<user_id>. "
                    f"You should pay attention to who is speaking to understand the context. "
                )
        parts.append(session_context)

        workspace_id = self.sandbox_manager.to_workspace_id(session_key)

        # Viking agent memory (only if ov tools are enabled)
        if ov_tools_enable:
            start = _time.time()
            viking_memory = await self.memory.get_viking_memory_context(
                current_message=current_message, workspace_id=workspace_id, sender_id=sender_id
            )
            logger.info(f'viking_memory={viking_memory}')
            cost = round(_time.time() - start, 2)
            logger.info(
                f"[READ_USER_MEMORY]: cost {cost}s, memory={viking_memory[:50] if viking_memory else 'None'}"
            )
            if viking_memory:
                parts.append(
                    f"## openviking_search(query=[user_query])\n"
                    f"{viking_memory}"
                )

        parts.append("Reply in the same language as the user's query, ignoring the language of the reference materials. User's query:")

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

        return f"""# vikingbot 🐈

You are VikingBot, an AI assistant built based on the OpenViking context database.
When acquiring information, data, and knowledge, you **prioritize using openviking tools to read and search OpenViking (a context database) above all other sources**.
You have access to tools that allow you to:
- Read, search, and grep OpenViking files
- Read, write, and edit local files
- Execute shell commands
- Search the web and fetch web pages
- Send messages to users on chat channels
- Spawn subagents for complex background tasks

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
    ) -> list[dict[str, Any]]:
        """
        Build the complete message list for an LLM call.

        Args:
            history: Previous conversation messages.
            current_message: The new user message.
            media: Optional list of local file paths for images/media.
            session_key: Optional session key.
            ov_tools_enable: Whether to enable OpenViking tools and memory.

        Returns:
            List of messages including system prompt.
        """
        messages = []

        # System prompt
        system_prompt = await self.build_system_prompt(session_key, current_message, history, ov_tools_enable=ov_tools_enable)
        messages.append({"role": "system", "content": system_prompt})
        # logger.debug(f"system_prompt: {system_prompt}")

        # History
        if not self._eval:
            messages.extend(history)

        # User
        user_info = await self._build_user_memory(session_key, current_message, self._sender_id, ov_tools_enable=ov_tools_enable)
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
