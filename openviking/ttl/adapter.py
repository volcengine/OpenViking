# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Directory TTL contract shared by the runtime, hooks and internal transport."""

from __future__ import annotations

from dataclasses import dataclass
from typing import (
    TYPE_CHECKING,
    Any,
    Mapping,
    Optional,
    Protocol,
    Sequence,
    runtime_checkable,
)

if TYPE_CHECKING:
    from openviking.scheduling.expiry.api import ExpiryScheduler
    from openviking.server.identity import RequestContext


@dataclass(frozen=True)
class TTLBackfillRequest:
    """Fixed initial scan: reset=False resumes the saved scope/checkpoint.

    reset=True stops the scan, clears done, resets the checkpoint and registers
    pending; indexes survive. Omitted roots are discovered at the next hourly
    window, including after restart. Untagged resets require state/log
    confirmation after a lost response. Tagged resets are adapter-deduplicated.
    """

    resource_id: str
    account_id: str
    roots: Optional[tuple[str, ...]] = None
    #: True for explicit internal repair, (re)opening or widening scope; the adapter
    #: must clear ``done`` / reset the checkpoint before re-running initial.
    reset: bool = False
    #: Optional explicit reset intents (scope -> token). The adapter consumes
    #: matching tokens atomically with reset; replay must not reset twice.
    reset_intents: Optional[dict[str, str]] = None


@dataclass(frozen=True)
class TTLRecycleRequest:
    """Writer's hourly request to recycle one scope's due directories.

    Normal triggers supply identity only. The writer resolves the conservative
    candidate cutoff, initial state and per-directory policy. An explicit cutoff
    remains available for isolated validation.
    """

    resource_id: str
    account_id: str
    #: Upper bound on the base-time score to scan, in epoch milliseconds.
    max_base_time_ms: Optional[int] = None
    limit: int = 256


@dataclass(frozen=True)
class TTLRecycleResult:
    """Outcome of a single recycle sweep, for writer-side logging/metrics."""

    scanned: int = 0
    deleted: int = 0
    skipped: int = 0
    failed: int = 0


@runtime_checkable
class TTLAdapter(Protocol):
    """Directory-TTL integration contract (default absent)."""

    def bind_runtime(self, service: Any, account_manager: Any) -> None:
        """Reuse the service's storage and published configuration lifecycle."""

    def install_expiry(self, scheduler: "ExpiryScheduler") -> None:
        """Register maintenance on the existing Redis scheduler before roles start."""

    def supports_config(self) -> bool:
        """Return True when explicit TTL configuration writes are allowed.

        The admin configuration routes keep returning 501 until a loaded
        adapter reports support; support depends on configuration, not edition.
        """

    async def patch_configuration(self, settings: dict, account_id: Optional[str] = None) -> None:
        """Persist reset intent before publishing a TTL configuration change."""

    async def prepare_user_configuration(self, ctx: Any, before: Any, after: Any) -> None:
        """Confirm durable reset intent before a UserConfig TTL save."""

    async def publish_user_configuration(self, ctx: Any, config: Any) -> None:
        """Refresh the user's in-process policy after a confirmed save."""

    async def read_initial(self, account_id: str) -> dict:
        """Read the fixed initial state for internal diagnostics."""

    async def delete_account(self, account_id: str) -> None:
        """Remove Account indexes, backfill state and maintenance registration."""

    async def on_post_write(
        self,
        uri: str,
        ctx: Optional["RequestContext"],
        written_at_ms: int,
    ) -> None:
        """Buffer a committed write for background directory registration.

        Runs after the storage write is committed and must never turn a
        successful write into a failure; the kernel already swallows and logs
        exceptions raised here. The implementation only merges into a
        bounded local buffer here; Redis registration belongs to its worker.
        """

    async def register_initial_backfill(self, request: TTLBackfillRequest) -> None:
        """Register (or reset-and-resume) the fixed initial scan for a scope."""

    async def recycle_due(self, request: TTLRecycleRequest) -> TTLRecycleResult:
        """Recycle the due directories for one scope under the directory lock."""

    async def resolve_expires_at(
        self,
        uris: Sequence[str],
        ctx: Optional["RequestContext"],
    ) -> Mapping[str, Optional[str]]:
        """Batch-compute UTC expiry or None; never persist it or stat for display.

        Missing scores/Redis failure yield None without failing the read.
        None alone does not imply TTL disabled or permanent retention.
        """

    async def start(self) -> None:
        """Start background consumers/connections after the service is ready."""

    async def aclose(self) -> None:
        """Stop in-flight work and release Redis connections on shutdown."""


__all__ = [
    "TTLAdapter",
    "TTLBackfillRequest",
    "TTLRecycleRequest",
    "TTLRecycleResult",
]
