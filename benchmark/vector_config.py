# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

from dataclasses import dataclass
from types import SimpleNamespace

from openviking_cli.utils.config.vectordb_config import VectorDBBackendConfig


@dataclass(frozen=True)
class BenchmarkVectorConfigResolver:
    """Bind a benchmark account to the run's preconfigured shared collection."""

    account_id: str
    vectordb: VectorDBBackendConfig

    async def resolve(self, account_id: str) -> SimpleNamespace:
        if account_id != self.account_id:
            raise ValueError(f"Unexpected benchmark account: {account_id!r}")
        return SimpleNamespace(vectordb=self.vectordb, dedicated_vectordb=False)
