# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Vector store integration mixin for VikingFS."""

from functools import partial
from typing import TYPE_CHECKING, Any, List, Mapping, Optional

from openviking.observability.log_safety import safe_uri_for_log
from openviking.server.identity import RequestContext
from openviking.storage.viking_fs._base import logger

if TYPE_CHECKING:
    from openviking.storage.viking_vector_index_backend import (
        VikingVectorIndexBackend,
        _PreparedVectorTransfer,
    )


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
                logger.debug("Deleted from vector store: %s", safe_uri_for_log(str(uri)))
        except Exception as e:
            logger.debug("Failed to delete from vector store: %s", type(e).__name__)
            raise

    async def _prepare_vector_store_uris(
        self,
        old_base: str,
        new_base: str,
        *,
        recursive: bool,
        ctx: Optional[RequestContext] = None,
        source_uris: List[str] | None = None,
        source_expected_levels: Mapping[str, set[int]] | None = None,
    ) -> Optional["_PreparedVectorTransfer"]:
        """Read all vector transfer dependencies before filesystem writes."""
        vector_store = self._get_vector_store()
        if not vector_store:
            return None
        return await vector_store._prepare_uri_transfer(
            self._ctx_or_default(ctx),
            old_base,
            new_base,
            recursive=recursive,
            target_entry_exists=partial(self.exists, ctx=ctx),  # type: ignore[attr-defined]
            source_uris=source_uris,
            **(
                {"source_expected_levels": source_expected_levels}
                if source_expected_levels is not None
                else {}
            ),
        )

    async def _copy_vector_store_uris(
        self, prepared: "_PreparedVectorTransfer", ctx: Optional[RequestContext] = None
    ) -> Any:
        """Apply prepared vector copy data without repeating inventory reads."""
        vector_store = self._get_vector_store()
        if not vector_store:
            return None
        return await vector_store._copy_prepared_uri_mapping(self._ctx_or_default(ctx), prepared)

    async def _update_vector_store_uris(
        self,
        prepared: "_PreparedVectorTransfer",
        *,
        ctx: Optional[RequestContext] = None,
        restore_acl: dict[str, dict[str, Any]] | None = None,
    ) -> Any:
        """Move a vector URI scope, overwriting matching target records.

        Preserves vector data and updates URI-derived identifiers without regenerating embeddings.
        """
        vector_store = self._get_vector_store()
        if not vector_store:
            return None
        return await vector_store._move_prepared_uri_mapping(
            self._ctx_or_default(ctx),
            prepared,
            **({"restore_acl": restore_acl} if restore_acl is not None else {}),
        )

    def _get_vector_store(self) -> Optional["VikingVectorIndexBackend"]:
        """Get vector store instance."""
        return self.vector_store

    def _get_embedder(self, ctx=None) -> Any:
        """Bind query calls to the account, resolving settings at each call.

        Fails closed: a request that already carries an account_id must never
        fall back to a process-wide startup embedder. When the account embedding
        provider is absent the caller gets an explicit initialization error
        instead of a Cluster-scoped embedder.
        """
        ctx = self._require_request_context(ctx)
        provider = self._embedding_provider
        if provider is None:
            raise RuntimeError("Account embedding provider is not initialized")
        return provider.bind(ctx.account_id)
