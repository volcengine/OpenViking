# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Vector store integration mixin for VikingFS."""

from typing import TYPE_CHECKING, Any, List, Optional, Sequence

from openviking.server.identity import RequestContext
from openviking.storage.viking_fs._base import logger

if TYPE_CHECKING:
    from openviking.storage.viking_vector_index_backend import VikingVectorIndexBackend


class _VectorMixin:
    """Vector store integration: delete/update URIs, get store/embedder."""

    async def _delete_from_vector_store(
        self, uris: List[str], ctx: Optional[RequestContext] = None
    ) -> None:
        """Delete records with specified URIs from vector store.

        Uses tenant-safe URI deletion semantics from vector store.
        """
        vector_store = self._get_vector_store()
        if not vector_store:
            return
        real_ctx = self._ctx_or_default(ctx)

        try:
            await vector_store.delete_uris(real_ctx, uris)
            for uri in uris:
                logger.debug(f"[VikingFS] Deleted from vector store: {uri}")
        except Exception as e:
            logger.warning(f"[VikingFS] Failed to delete from vector store: {e}")
            raise

    async def _update_vector_store_uris(
        self,
        uris: List[str],
        old_base: str,
        new_base: str,
        ctx: Optional[RequestContext] = None,
    ) -> List[tuple[str, str]]:
        """Update URIs in vector store (when moving files).

        Preserves vector data and updates URI-derived identifiers without regenerating embeddings.
        """
        vector_store = self._get_vector_store()
        if not vector_store:
            return []

        real_ctx = self._ctx_or_default(ctx)
        mappings: List[tuple[str, str]] = []

        try:
            for uri in uris:
                new_uri = new_base + uri[len(old_base) :]
                updated = await vector_store.update_uri_mapping(
                    ctx=real_ctx,
                    uri=uri,
                    new_uri=new_uri,
                )
                if updated:
                    mappings.append((uri, new_uri))
                    logger.debug(f"[VikingFS] Updated URI: {uri} -> {new_uri}")
        except Exception:
            await self._restore_vector_store_uris(mappings, ctx=real_ctx)
            raise
        return mappings

    async def _restore_vector_store_uris(
        self,
        mappings: Sequence[tuple[str, str]],
        ctx: Optional[RequestContext] = None,
    ) -> None:
        vector_store = self._get_vector_store()
        if not vector_store:
            return
        real_ctx = self._ctx_or_default(ctx)
        for old_uri, new_uri in sorted(mappings, key=lambda item: item[0].count("/")):
            try:
                restored = await vector_store.update_uri_mapping(
                    ctx=real_ctx,
                    uri=new_uri,
                    new_uri=old_uri,
                )
                if not restored:
                    logger.warning(
                        "[VikingFS] Vector URI rollback found no records: %s -> %s",
                        new_uri,
                        old_uri,
                    )
            except Exception:
                logger.exception(
                    "[VikingFS] Failed to roll back vector URI: %s -> %s",
                    new_uri,
                    old_uri,
                )

    def _get_vector_store(self) -> Optional["VikingVectorIndexBackend"]:
        """Get vector store instance."""
        return self.vector_store

    def _get_embedder(self) -> Any:
        """Get embedder instance."""
        return self.query_embedder
