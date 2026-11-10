# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Stable ``ConfigSource`` contract.

External source implementations depend only on this module and
:mod:`openviking.config.source.registry`; they must not import other private
kernel modules. These two modules are maintained as a stable API.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Optional, Protocol, runtime_checkable

from openviking.config.scope import ConfigScope, ScopeKind

__all__ = ["ConfigScope", "ScopeKind", "ConfigSource", "ConfigSourceContext", "Mutate"]

# A pure, repeatable function producing the complete new settings document from
# the current one. It must have no side effects or send consumer notifications;
# a conflict retry may run it again.
Mutate = Callable[[Optional[dict]], dict]


@dataclass
class ConfigSourceContext:
    """Configuration passed to an external source factory at construction time.

    ``params`` is opaque to the manager; its meaning and connection lifecycle
    belong to the provider. The context intentionally contains no kernel
    dependencies, so plugins cannot receive AGFS or other service objects.
    """

    params: dict[str, Any] = field(default_factory=dict)


@runtime_checkable
class ConfigSource(Protocol):
    """Storage and change awareness for scoped configuration documents.

    A source instance may be called concurrently from different threads and
    event loops. Implementations must synchronize mutable state accordingly and
    must not rely on loop-affine clients or asyncio primitives unless they
    dispatch those operations to their owning loop internally.
    """

    async def load(self, scope: ConfigScope) -> Optional[dict]:
        """Read the explicitly stored settings for one scope.

        Omitted model fields are not materialized in storage. This serialization
        detail does not make Account configuration an override of Cluster
        configuration. Returns ``None`` when the scope has no stored document.
        """
        ...

    async def update(self, scope: ConfigScope, mutate: "Mutate") -> dict:
        """Perform a read-modify-write within the source's own concurrency guard.

        Reads the current settings, calls ``mutate`` to get the complete new
        document, and writes it back. ``mutate`` is guaranteed to run against the
        latest value and the write must not drop updates; lock vs. CAS is an
        implementation choice.
        """
        ...

    async def delete(self, scope: ConfigScope) -> None:
        """Delete the persisted settings for one scope.

        Missing state is treated as success. Implementations must provide the
        same concurrency guarantees as :meth:`update`.
        """
        ...
