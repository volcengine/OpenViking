# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Vector store integration mixin for VikingFS."""

from functools import partial
from typing import TYPE_CHECKING, Any, List, Optional

from openviking.server.identity import RequestContext
from openviking.storage.expr import And, Eq, In, Or, PathScope
from openviking.storage.viking_fs._base import logger

if TYPE_CHECKING:
    from openviking.storage.viking_vector_index_backend import VikingVectorIndexBackend


class _VectorMixin:
    """Vector store integration: delete/update URIs, get store/embedder."""

    async def _delete_from_vector_store(
        self,
        uris: List[str],
        ctx: Optional[RequestContext] = None,
        *,
        recursive_uri: Optional[str] = None,
        level: Optional[int] = None,
    ) -> None:
        """Delete records with specified URIs from vector store.

        Uses tenant-safe URI deletion semantics from vector store.
        """
        vector_store = self._get_vector_store()
        if not vector_store:
            return
        real_ctx = self._ctx_or_default(ctx)

        try:
            options = {"level": level} if level is not None else {}
            if recursive_uri is not None:
                await vector_store.delete_uri_scope(real_ctx, recursive_uri, **options)
            else:
                await vector_store.delete_uris(real_ctx, uris, **options)
            for uri in uris:
                logger.debug(f"[VikingFS] Deleted from vector store: {uri}")
        except Exception as e:
            logger.warning(f"[VikingFS] Failed to delete from vector store: {e}")
            raise

    async def _confirm_vector_scope_cleared(
        self, target_uri: str, ctx: Optional[RequestContext] = None, *, level: Optional[int] = None
    ) -> None:
        """Strict-mode check: raise unless the vector scope is fully cleared.

        Mirrors the delete-then-confirm pattern used for account files: after
        the FS + vector deletes, re-count the recursive URI scope and refuse to
        report success while any record remains. Backend count errors propagate
        (they are not swallowed here), so a strict caller never observes a false
        success. A lingering residue means either a partial vector delete or an
        eventual-consistency lag; the caller (e.g. the cleanup queue) retries.
        """
        vector_store = self._get_vector_store()
        if not vector_store:
            return
        scope = Or(
            [
                Eq("uri", target_uri),
                PathScope("uri", target_uri, depth=-1),
            ]
        )
        residue = await vector_store.count(
            filter=And([scope, Eq("level", level)]) if level is not None else scope,
            ctx=self._ctx_or_default(ctx),
        )
        if residue:
            raise RuntimeError(
                f"Vector records still present after delete: {target_uri} (residue={residue})"
            )

    async def _confirm_vector_uris_cleared(
        self, uris: List[str], ctx: Optional[RequestContext] = None
    ) -> None:
        """Strictly confirm exact URI rows are gone without touching children."""
        vector_store = self._get_vector_store()
        targets = list(dict.fromkeys(uri.rstrip("/") for uri in uris if uri))
        if not vector_store or not targets:
            return
        residue = await vector_store.count(
            filter=In("uri", targets),
            ctx=self._ctx_or_default(ctx),
        )
        if residue:
            raise RuntimeError(
                "Vector records still present after delete: "
                f"{', '.join(targets)} (residue={residue})"
            )

    async def _copy_vector_store_uris(
        self,
        old_base: str,
        new_base: str,
        *,
        recursive: bool,
        ctx: Optional[RequestContext] = None,
        source_uris: List[str] | None = None,
    ) -> Any:
        """Copy a complete vector URI scope while preserving the source."""
        vector_store = self._get_vector_store()
        if not vector_store:
            return None
        return await vector_store.copy_uri_mapping(
            ctx=self._ctx_or_default(ctx),
            source_uri=old_base,
            target_uri=new_base,
            recursive=recursive,
            target_entry_exists=partial(self.exists, ctx=ctx),  # type: ignore[attr-defined]
            **({"source_uris": source_uris} if source_uris is not None else {}),
        )

    async def _update_vector_store_uris(
        self,
        old_base: str,
        new_base: str,
        *,
        recursive: bool,
        ctx: Optional[RequestContext] = None,
        source_uris: List[str] | None = None,
    ) -> Any:
        """Move a vector URI scope, overwriting matching target records.

        Preserves vector data and updates URI-derived identifiers without regenerating embeddings.
        """
        vector_store = self._get_vector_store()
        if not vector_store:
            return None
        return await vector_store.update_uri_mapping(
            ctx=self._ctx_or_default(ctx),
            source_uri=old_base,
            target_uri=new_base,
            recursive=recursive,
            target_entry_exists=partial(self.exists, ctx=ctx),  # type: ignore[attr-defined]
            **({"source_uris": source_uris} if source_uris is not None else {}),
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
