# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Logical address of one scoped configuration document.

``ConfigScope`` identifies Cluster configuration or one Account's configuration.
It does not express inheritance, fallback, precedence, or storage layout.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Optional

from openviking.core.identifiers import validate_account_id


class ScopeKind(str, Enum):
    CLUSTER = "cluster"
    ACCOUNT = "account"


@dataclass(frozen=True)
class ConfigScope:
    """A validated logical address for one scoped configuration."""

    kind: ScopeKind
    key: Optional[str] = None

    def __post_init__(self) -> None:
        if self.kind is ScopeKind.CLUSTER:
            if self.key is not None:
                raise ValueError("cluster scope has no key")
        elif self.kind is ScopeKind.ACCOUNT:
            if not self.key:
                raise ValueError("account scope requires key")
            error = validate_account_id(self.key)
            if error:
                # Fail closed on path-traversal and other identifier abuse.
                raise ValueError(error)
        else:
            raise ValueError(f"unknown scope kind: {self.kind}")

    @classmethod
    def cluster(cls) -> "ConfigScope":
        return cls(ScopeKind.CLUSTER)

    @classmethod
    def account(cls, account_id: str) -> "ConfigScope":
        return cls(ScopeKind.ACCOUNT, account_id)
