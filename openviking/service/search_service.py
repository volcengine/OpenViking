# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""
Search Service for OpenViking.

Provides semantic search operations: search, find.
"""

from typing import TYPE_CHECKING, Any, Dict, List, Optional, Union

from openviking.core.namespace import canonical_user_root
from openviking.core.peer_id import normalize_peer_id
from openviking.core.uri_validation import validate_optional_viking_uris
from openviking.server.identity import RequestContext
from openviking.storage.viking_fs import VikingFS
from openviking_cli.exceptions import InvalidArgumentError, NotInitializedError
from openviking_cli.utils import get_logger

if TYPE_CHECKING:
    from openviking.session import Session

logger = get_logger(__name__)


def _ensure_non_empty_query(query: str) -> None:
    if not query.strip():
        raise InvalidArgumentError("Search query must not be empty.")


def _is_empty_target_uri(target_uri: Union[str, List[str]]) -> bool:
    if isinstance(target_uri, list):
        return not any(target_uri)
    return not target_uri


def _target_uri_for_peer(
    target_uri: Union[str, List[str]],
    ctx: RequestContext,
    peer_id: Optional[str],
) -> Union[str, List[str]]:
    try:
        normalized_peer_id = normalize_peer_id(peer_id)
    except ValueError as exc:
        raise InvalidArgumentError(str(exc)) from exc

    if not normalized_peer_id or not _is_empty_target_uri(target_uri):
        return target_uri

    user_root = canonical_user_root(ctx)
    return [
        f"{user_root}/memories",
        f"{user_root}/peers/{normalized_peer_id}/memories",
    ]


class SearchService:
    """Semantic search service."""

    def __init__(self, viking_fs: Optional[VikingFS] = None):
        self._viking_fs = viking_fs

    def set_viking_fs(self, viking_fs: VikingFS) -> None:
        """Set VikingFS instance (for deferred initialization)."""
        self._viking_fs = viking_fs

    def _ensure_initialized(self) -> VikingFS:
        """Ensure VikingFS is initialized."""
        if not self._viking_fs:
            raise NotInitializedError("VikingFS")
        return self._viking_fs

    async def search(
        self,
        query: str,
        ctx: RequestContext,
        target_uri: Union[str, List[str]] = "",
        peer_id: Optional[str] = None,
        session: Optional["Session"] = None,
        limit: int = 10,
        score_threshold: Optional[float] = None,
        filter: Optional[Dict] = None,
        level: Optional[List[int]] = None,
    ) -> Any:
        """Complex search with session context.

        Args:
            query: Query string
            target_uri: Target directory URI(s), supports str or List[str]
            session: Session object for context
            limit: Max results
            score_threshold: Score threshold
            filter: Metadata filters
            level: Filter by level (0=abstract, 1=overview, 2=file)

        Returns:
            FindResult
        """
        _ensure_non_empty_query(query)
        target_uri = validate_optional_viking_uris(target_uri, field_name="target_uri")
        target_uri = _target_uri_for_peer(target_uri, ctx, peer_id)
        viking_fs = self._ensure_initialized()

        session_info = None
        if session:
            session_info = await session.get_context_for_search(query)

        result = await viking_fs.search(
            query=query,
            ctx=ctx,
            target_uri=target_uri,
            session_info=session_info,
            limit=limit,
            score_threshold=score_threshold,
            filter=filter,
            level=level,
        )
        return result

    async def find(
        self,
        query: str,
        ctx: RequestContext,
        target_uri: Union[str, List[str]] = "",
        peer_id: Optional[str] = None,
        limit: int = 10,
        score_threshold: Optional[float] = None,
        filter: Optional[Dict] = None,
        level: Optional[List[int]] = None,
    ) -> Any:
        """Semantic search without session context.

        Args:
            query: Query string
            target_uri: Target directory URI(s), supports str or List[str]
            limit: Max results
            score_threshold: Score threshold
            filter: Metadata filters
            level: Filter by level (0=abstract, 1=overview, 2=file)

        Returns:
            FindResult
        """
        _ensure_non_empty_query(query)
        target_uri = validate_optional_viking_uris(target_uri, field_name="target_uri")
        target_uri = _target_uri_for_peer(target_uri, ctx, peer_id)
        viking_fs = self._ensure_initialized()
        result = await viking_fs.find(
            query=query,
            ctx=ctx,
            target_uri=target_uri,
            limit=limit,
            score_threshold=score_threshold,
            filter=filter,
            level=level,
        )
        return result
