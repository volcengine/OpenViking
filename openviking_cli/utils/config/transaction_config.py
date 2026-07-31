# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
from pydantic import BaseModel, Field


class TransactionConfig(BaseModel):
    """Deprecated compatibility settings for legacy transaction fields.

    Prefer ``storage.agfs.pathlock`` for active PathLock configuration.
    """

    lock_timeout: float = Field(
        default=0.0,
        description=(
            "Deprecated compatibility field. "
            "Use storage.agfs.pathlock.lock_timeout_secs instead. "
            "When the new field is unset, this value is mapped to it."
        ),
    )

    lock_expire: float = Field(
        default=1800.0,
        description=(
            "Deprecated compatibility field. "
            "Use storage.agfs.pathlock.lock_expire_secs instead. "
            "When the new field is unset, this value is mapped to it."
        ),
    )

    redo_recovery_enabled: bool = Field(
        default=True,
        description=(
            "Deprecated compatibility flag for the legacy session commit phase-2 redo recovery. "
            "Ignored in the current implementation, which resumes phase-2 work from the "
            "persistent session_commit queue."
        ),
    )

    model_config = {"extra": "forbid"}
