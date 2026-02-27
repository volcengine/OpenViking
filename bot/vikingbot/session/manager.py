"""Session management for conversation history."""

import asyncio
import json
from pathlib import Path
from dataclasses import dataclass, field

from datetime import datetime
from typing import TYPE_CHECKING, Any

from loguru import logger

from vikingbot.config.schema import SessionKey
from vikingbot.utils.helpers import ensure_dir


from vikingbot.sandbox.manager import SandboxManager


@dataclass
class Session:
    """
    A conversation session.

    Stores messages in JSONL format for easy reading and persistence.
    """

    key: SessionKey  # channel:chat_id
    messages: list[dict[str, Any]] = field(default_factory=list)
    created_at: datetime = field(default_factory=datetime.now)
    updated_at: datetime = field(default_factory=datetime.now)
    metadata: dict[str, Any] = field(default_factory=dict)

    def add_message(self, role: str, content: str, **kwargs: Any) -> None:
        """Add a message to the session."""
        msg = {"role": role, "content": content, "timestamp": datetime.now().isoformat(), **kwargs}
        self.messages.append(msg)
        self.updated_at = datetime.now()

    def get_history(self, max_messages: int = 50) -> list[dict[str, Any]]:
        """
        Get message history for LLM context.

        Args:
            max_messages: Maximum messages to return.

        Returns:
            List of messages in LLM format.
        """
        # Get recent messages
        recent = (
            self.messages[-max_messages:] if len(self.messages) > max_messages else self.messages
        )

        # Convert to LLM format (just role and content)
        return [{"role": m["role"], "content": m["content"]} for m in recent]

    def clear(self) -> None:
        """Clear all messages in the session."""
        self.messages = []
        self.updated_at = datetime.now()


class SessionManager:
    """
    Manages conversation sessions.

    Sessions are stored as JSONL files in sessions directory.
    """

    def __init__(
        self,
        workspace: Path,
        sandbox_manager: "SandboxManager | None" = None,
    ):
        self.workspace = workspace
        self.sessions_dir = ensure_dir(Path.home() / ".vikingbot" / "sessions")
        self._cache: dict[SessionKey, Session] = {}
        self.sandbox_manager = sandbox_manager

    def _get_session_path(self, session_key: SessionKey) -> Path:
        return self.sessions_dir / f"{session_key.safe_name()}.jsonl"

    def get_or_create(self, key: SessionKey, skip_heartbeat: bool = False) -> Session:
        """
        Get an existing session or create a new one.

        Args:
            key: Session key (usually channel:chat_id).
            skip_heartbeat: Whether to skip heartbeat for this session.

        Returns:
            The session.
        """
        # Check cache
        if key in self._cache:
            return self._cache[key]

        # Try to load from disk
        session = self._load(key)
        if session is None:
            session = Session(key=key)
            if skip_heartbeat:
                session.metadata["skip_heartbeat"] = True

        self._cache[key] = session

        if self.sandbox_manager:
            from vikingbot.utils.helpers import ensure_session_workspace

            if self.sandbox_manager.config.mode == "shared":
                workspace_path = self.sandbox_manager.workspace / "shared"
            else:
                workspace_path = self.sandbox_manager.workspace / key.replace(":", "_")
            ensure_session_workspace(workspace_path)

        # Initialize sandbox
        if self.sandbox_manager:
            asyncio.create_task(self._init_sandbox(key))

        return session

    async def _init_sandbox(self, key: SessionKey) -> None:
        """Initialize sandbox for a session."""
        if self.sandbox_manager is None:
            return
        try:
            await self.sandbox_manager.get_sandbox(key)
        except Exception as e:
            logger.warning(f"Failed to initialize sandbox for {key}: {e}")

    def _load(self, session_key: SessionKey) -> Session | None:
        """Load a session from disk."""
        path = self._get_session_path(session_key)

        if not path.exists():
            return None

        try:
            messages = []
            metadata = {}
            created_at = None
            session_key_from_metadata = None

            with open(path) as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue

                    data = json.loads(line)

                    if data.get("_type") == "metadata":
                        metadata = data.get("metadata", {})
                        created_at = (
                            datetime.fromisoformat(data["created_at"])
                            if data.get("created_at")
                            else None
                        )
                        session_key_from_metadata = SessionKey.from_safe_name(
                            data.get("session_key")
                        )
                    else:
                        messages.append(data)

            effective_key = session_key_from_metadata if session_key_from_metadata else session_key

            return Session(
                key=effective_key,
                messages=messages,
                created_at=created_at or datetime.now(),
                metadata=metadata,
            )
        except Exception as e:
            logger.warning(f"Failed to load session {session_key}: {e}")
            return None

    def save(self, session: Session) -> None:
        """Save a session to disk."""
        path = self._get_session_path(session.key)

        with open(path, "w") as f:
            # Write metadata first
            metadata_line = {
                "_type": "metadata",
                "session_key": session.key.safe_name(),
                "created_at": session.created_at.isoformat(),
                "updated_at": session.updated_at.isoformat(),
                "metadata": session.metadata,
            }
            f.write(json.dumps(metadata_line, ensure_ascii=False) + "\n")

            # Write messages
            for msg in session.messages:
                f.write(json.dumps(msg, ensure_ascii=False) + "\n")

        self._cache[session.key] = session

    def delete(self, key: SessionKey) -> bool:
        """
        Delete a session.

        Args:
            key: Session key.

        Returns:
            True if deleted, False if not found.
        """
        # Clean up sandbox if enabled
        if self.sandbox_manager is not None:
            asyncio.create_task(self.sandbox_manager.cleanup_session(key))

        # Remove from cache
        self._cache.pop(key, None)

        # Remove file
        path = self._get_session_path(key)
        if path.exists():
            path.unlink()
            return True
        return False

    def list_sessions(self) -> list[dict[str, Any]]:
        """
        List all sessions.

        Returns:
            List of session info dicts.
        """
        sessions = []

        for path in self.sessions_dir.glob("*.jsonl"):
            try:
                with open(path) as f:
                    first_line = f.readline().strip()
                    if first_line:
                        data = json.loads(first_line)
                        if data.get("_type") == "metadata":
                            session_key = SessionKey.from_safe_name(data.get("session_key"))
                            metadata = data.get("metadata", {})
                            sessions.append(
                                {
                                    "key": session_key,
                                    "created_at": data.get("created_at"),
                                    "updated_at": data.get("updated_at"),
                                    "metadata": metadata,
                                    "path": str(path),
                                }
                            )
            except Exception:
                continue

        return sorted(sessions, key=lambda x: x.get("updated_at", ""), reverse=True)
