# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Lightweight Session class for OpenViking client.

Session delegates all operations to the underlying Client (LocalClient or AsyncHTTPClient).
"""

from dataclasses import asdict
from typing import TYPE_CHECKING, Any, Dict, List, Optional

from openviking.message.part import Part
from openviking.telemetry import TelemetryRequest
from openviking_cli.session.user_id import UserIdentifier

if TYPE_CHECKING:
    from openviking_cli.client.base import BaseClient


class Session:
    """Lightweight Session wrapper that delegates operations to Client.

    This class provides a convenient OOP interface for session operations.
    All actual work is delegated to the underlying client.
    """

    def __init__(self, client: "BaseClient", session_id: str, user: UserIdentifier):
        """Initialize Session.

        Args:
            client: The underlying client (LocalClient or AsyncHTTPClient)
            session_id: Session ID
            user: User name
        """
        self._client = client
        self.session_id = session_id
        self.user = user

    async def add_message(
        self,
        role: str,
        content: Optional[str] = None,
        parts: Optional[List[Part]] = None,
        created_at: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Add a message to the session.

        Args:
            role: Message role (e.g., "user", "assistant")
            content: Text content (simple mode)
            parts: Parts list (TextPart, ContextPart, ToolPart)
            created_at: Message creation time (ISO format string). If not provided, current time is used.

        If both content and parts are provided, parts takes precedence.

        Returns:
            Result dict with session_id and message_count
        """
        if parts is not None:
            parts_dicts = [asdict(p) for p in parts]
            return await self._client.add_message(
                self.session_id, role, parts=parts_dicts, created_at=created_at
            )
        return await self._client.add_message(
            self.session_id, role, content=content, created_at=created_at
        )

    async def commit(self, telemetry: TelemetryRequest = False) -> Dict[str, Any]:
        """Commit the session (archive messages and extract memories).

        Returns:
            Commit result
        """
        return await self._client.commit_session(self.session_id, telemetry=telemetry)

    async def commit_async(self, telemetry: TelemetryRequest = False) -> Dict[str, Any]:
        """Commit the session asynchronously (archive messages and extract memories).
           Used in viking bot for committing.

        Returns:
            Commit result
        """
        return await self.commit(telemetry)

    async def delete(self) -> None:
        """Delete the session."""
        await self._client.delete_session(self.session_id)

    async def load(self) -> Dict[str, Any]:
        """Load session data.

        Returns:
            Session details
        """
        return await self._client.get_session(self.session_id)

    async def get_session_context(self, token_budget: int = 128_000) -> Dict[str, Any]:
        """Get assembled session context."""
        return await self._client.get_session_context(self.session_id, token_budget=token_budget)

    async def get_archive(self, archive_id: str) -> Dict[str, Any]:
        """Get one completed archive for the session."""
        return await self._client.get_session_archive(self.session_id, archive_id)

    def __repr__(self) -> str:
        return f"Session(id={self.session_id}, user={self.user.__str__()})"
