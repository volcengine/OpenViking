# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""Lightweight Session class for OpenViking client.

Session delegates all operations to the underlying Client (LocalClient or AsyncHTTPClient).
"""

from typing import TYPE_CHECKING, Any, Dict

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

    async def add_message(self, role: str, content: str) -> Dict[str, Any]:
        """Add a message to the session.

        Args:
            role: Message role (e.g., "user", "assistant")
            content: Message content

        Returns:
            Result dict with session_id and message_count
        """
        return await self._client.add_message(self.session_id, role, content)

    async def commit(self) -> Dict[str, Any]:
        """Commit the session (archive messages and extract memories).

        Returns:
            Commit result
        """
        return await self._client.commit_session(self.session_id)

    async def delete(self) -> None:
        """Delete the session."""
        await self._client.delete_session(self.session_id)

    async def load(self) -> Dict[str, Any]:
        """Load session data.

        Returns:
            Session details
        """
        return await self._client.get_session(self.session_id)

    def __repr__(self) -> str:
        return f"Session(id={self.session_id}, user={self.user.__str__()})"
