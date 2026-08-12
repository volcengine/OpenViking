# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Local keyword sidecar integration mixin for VikingFS."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any, List, Optional

from openviking.server.identity import RequestContext
from openviking.storage.viking_fs._base import logger

if TYPE_CHECKING:
    from openviking.storage.keywordfs.keyword_fs import KeywordFS
    from openviking_cli.utils.config.keyword_config import KeywordConfig


class _KeywordMixin:
    """Keyword sidecar wiring shared by grep, semantic search, and storage ops."""

    def _get_keyword_fs(self) -> Optional["KeywordFS"]:
        """Get the keyword sidecar instance (may be None when disabled)."""
        return self._keyword_fs

    def set_keyword_fs(self, keyword_fs: Optional["KeywordFS"]) -> None:
        """Attach the keyword sidecar (for deferred initialization)."""
        self._keyword_fs = keyword_fs

    def _keyword_indexing_wired(self) -> bool:
        """True when the keyword sidecar should receive mutations."""
        if not self.keyword_config or not self.keyword_config.enabled:
            return False
        if self.keyword_config.respect_encryption and self._encryptor is not None:
            return False
        return self._get_keyword_fs() is not None

    def _keyword_available(self, ctx: Optional[RequestContext] = None) -> bool:
        """True when the local keyword sidecar is enabled, safe and ready."""
        if not self.keyword_config or not self.keyword_config.enabled:
            return False
        if self.keyword_config.respect_encryption and self._encryptor is not None:
            return False
        kfs = self._get_keyword_fs()
        if kfs is None:
            return False
        real_ctx = self._ctx_or_default(ctx)
        try:
            return kfs.is_ready(real_ctx.account_id)
        except Exception:
            return False

    async def _enqueue_keyword_delete(self, uris: List[str], ctx) -> None:
        """Best-effort enqueue of keyword delete messages (fire-and-forget)."""
        if not self._keyword_indexing_wired() or not uris:
            return
        try:
            from openviking.storage.keywordfs.keyword_msg import Delete, KeywordMsg
            from openviking.storage.queuefs.queue_manager import get_queue_manager

            qm = get_queue_manager()
            queue = qm.get_queue(qm.KEYWORD)
            real_ctx = self._ctx_or_default(ctx)
            for u in uris:
                await queue.enqueue(
                    KeywordMsg(kind=Delete, uri=u, account_id=real_ctx.account_id)
                )
        except Exception as e:
            logger.warning(f"[VikingFS] Failed to enqueue keyword deletes: {e}")

    async def _enqueue_keyword_move(
        self, uris: List[str], old_base: str, new_base: str, ctx
    ) -> None:
        """Best-effort enqueue of keyword move messages (fire-and-forget)."""
        if not self._keyword_indexing_wired() or not uris:
            return
        try:
            from openviking.storage.keywordfs.keyword_msg import KeywordMsg, Move
            from openviking.storage.queuefs.queue_manager import get_queue_manager

            qm = get_queue_manager()
            queue = qm.get_queue(qm.KEYWORD)
            real_ctx = self._ctx_or_default(ctx)
            for u in uris:
                new_uri = new_base + u[len(old_base) :]
                await queue.enqueue(
                    KeywordMsg(
                        kind=Move,
                        old_uri=u,
                        new_uri=new_uri,
                        account_id=real_ctx.account_id,
                    )
                )
        except Exception as e:
            logger.warning(f"[VikingFS] Failed to enqueue keyword moves: {e}")

    async def _maybe_hybrid_keyword(
        self,
        query: str,
        matched: List[Any],
        target_directories: List[str],
        ctx: RequestContext,
        limit: int,
        override: Optional[bool] = None,
    ) -> List[Any]:
        """Fuse keyword-sidecar recall into dense results when hybrid is enabled."""
        hybrid_cfg = (
            getattr(self.retrieval_config, "hybrid", None) if self.retrieval_config else None
        )
        enabled = (
            override
            if override is not None
            else bool(hybrid_cfg and getattr(hybrid_cfg, "enabled", False))
        )
        if not enabled:
            return matched
        kfs = self._get_keyword_fs()
        if kfs is None or not self._keyword_indexing_wired():
            return matched
        try:
            from openviking.retrieve.hybrid_keyword import HybridKeywordRecaller

            recaller = HybridKeywordRecaller(kfs, hybrid_cfg, self.keyword_config)
            if not recaller.enabled(ctx):
                return matched
            return await recaller.enhance(
                query=query,
                dense=matched,
                scope_uris=target_directories or [""],
                ctx=ctx,
                limit=limit,
                read_abstract=lambda uri: self.abstract(uri, ctx=ctx),
            )
        except Exception:
            logger.exception("[VikingFS] hybrid keyword fusion failed; using dense results")
            return matched

    def _schedule_keyword_rebuild(
        self,
        *,
        written: List[str],
        deleted: List[str],
        ctx: RequestContext,
    ) -> None:
        """Fire-and-forget keyword-sidecar cleanup for a git restore."""
        if not self._keyword_indexing_wired():
            return
        affected = list(dict.fromkeys([*written, *deleted]))
        if not affected:
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            logger.warning(
                "[VikingFS] git restore keyword rebuild skipped: no running event loop"
            )
            return

        loop.create_task(
            self._run_keyword_restore_cleanup(affected, ctx),
            name=f"vikingfs-git-keyword:{ctx.account_id}",
        )

    async def _run_keyword_restore_cleanup(self, uris: List[str], ctx: RequestContext) -> None:
        """Enqueue keyword delete_prefix messages for the restored paths."""
        if not self._keyword_indexing_wired() or not uris:
            return
        try:
            from openviking.storage.keywordfs.keyword_msg import DeletePrefix, KeywordMsg
            from openviking.storage.queuefs.queue_manager import get_queue_manager

            qm = get_queue_manager()
            queue = qm.get_queue(qm.KEYWORD)
            real_ctx = self._ctx_or_default(ctx)
            for u in uris:
                await queue.enqueue(
                    KeywordMsg(kind=DeletePrefix, uri=u, account_id=real_ctx.account_id)
                )
        except Exception as e:
            logger.warning(f"[VikingFS] Failed to enqueue keyword restore cleanup: {e}")
