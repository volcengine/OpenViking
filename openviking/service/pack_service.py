# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""
Pack Service for OpenViking.

Provides ovpack export/import and backup/restore operations.
"""

from typing import Optional

from openviking.core.uri_validation import validate_viking_uri
from openviking.server.identity import RequestContext
from openviking.storage.ovpack.operations import backup_ovpack as local_backup_ovpack
from openviking.storage.ovpack.operations import export_ovpack as local_export_ovpack
from openviking.storage.ovpack.operations import import_ovpack as local_import_ovpack
from openviking.storage.ovpack.operations import restore_ovpack as local_restore_ovpack
from openviking.storage.viking_fs import VikingFS
from openviking_cli.exceptions import NotInitializedError
from openviking_cli.utils import get_logger

logger = get_logger(__name__)


class PackService:
    """OVPack export/import and backup/restore service."""

    def __init__(self, viking_fs: Optional[VikingFS] = None, vector_store=None):
        self._viking_fs = viking_fs
        self._vector_store = vector_store

    def set_viking_fs(self, viking_fs: VikingFS) -> None:
        """Set VikingFS instance (for deferred initialization)."""
        self._viking_fs = viking_fs

    def set_dependencies(self, viking_fs: VikingFS, vector_store=None) -> None:
        """Set pack service dependencies."""
        self._viking_fs = viking_fs
        self._vector_store = vector_store

    def _ensure_initialized(self) -> VikingFS:
        """Ensure VikingFS is initialized."""
        if not self._viking_fs:
            raise NotInitializedError("VikingFS")
        return self._viking_fs

    async def export_ovpack(
        self,
        uri: str,
        to: str,
        ctx: RequestContext,
        include_vectors: bool = False,
    ) -> str:
        """Export specified context path as .ovpack file.

        Args:
            uri: Viking URI
            to: Target file path

        Returns:
            Exported file path
        """
        viking_fs = self._ensure_initialized()
        uri = validate_viking_uri(uri)
        return await local_export_ovpack(
            viking_fs,
            uri,
            to,
            ctx=ctx,
            vector_store=self._vector_store,
            include_vectors=include_vectors,
        )

    async def backup_ovpack(
        self,
        to: str,
        ctx: RequestContext,
        include_vectors: bool = False,
    ) -> str:
        """Back up all public OpenViking scopes as a restore-only .ovpack file."""
        viking_fs = self._ensure_initialized()
        return await local_backup_ovpack(
            viking_fs,
            to,
            ctx=ctx,
            vector_store=self._vector_store,
            include_vectors=include_vectors,
        )

    async def import_ovpack(
        self,
        file_path: str,
        parent: str,
        ctx: RequestContext,
        on_conflict: Optional[str] = None,
        vector_mode: Optional[str] = None,
    ) -> str:
        """Import local .ovpack file to specified parent path.

        Args:
            file_path: Local .ovpack file path
            parent: Target parent URI (e.g., viking://user/alice/resources/references/)
            on_conflict: One of "fail", "overwrite", or "skip"

        Returns:
            Imported root resource URI
        """
        viking_fs = self._ensure_initialized()
        parent = validate_viking_uri(parent, field_name="parent")
        return await local_import_ovpack(
            viking_fs,
            file_path,
            parent,
            on_conflict=on_conflict,
            vector_mode=vector_mode,
            vector_store=self._vector_store,
            ctx=ctx,
        )

    async def restore_ovpack(
        self,
        file_path: str,
        ctx: RequestContext,
        on_conflict: Optional[str] = None,
        vector_mode: Optional[str] = None,
    ) -> str:
        """Restore a backup .ovpack file to its original public scope roots."""
        viking_fs = self._ensure_initialized()
        return await local_restore_ovpack(
            viking_fs,
            file_path,
            ctx=ctx,
            on_conflict=on_conflict,
            vector_mode=vector_mode,
            vector_store=self._vector_store,
        )
