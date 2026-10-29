# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""The outcome of one delivery, not the outcome of its owning business task."""

from dataclasses import dataclass
from enum import Enum
from typing import Any, Optional, Tuple


class ProcessOutcome(str, Enum):
    SUCCESS = "success"
    FAILED = "failed"
    REQUEUED = "requeued"
    CANCELLED = "cancelled"


@dataclass(frozen=True)
class ProcessResult:
    """A settled delivery that may be ACKed after middleware completes.

    REQUEUED means the handler already enqueued a replacement. Exceptions,
    including unhandled cancellation, leave the current delivery unacknowledged.
    """

    outcome: ProcessOutcome
    value: Any = None
    error: Optional[str] = None
    errors: Optional[Tuple[str, ...]] = None

    def __post_init__(self) -> None:
        if not isinstance(self.outcome, ProcessOutcome):
            raise ValueError("outcome must be a ProcessOutcome")
        if self.outcome is ProcessOutcome.FAILED:
            if not isinstance(self.error, str):
                raise ValueError("failed results require an error string")
            if self.errors is not None and (
                not self.errors or any(not isinstance(value, str) for value in self.errors)
            ):
                raise ValueError("failed result errors must be a non-empty tuple of strings")
        elif self.error is not None or self.errors is not None:
            raise ValueError("only failed results may contain errors")

    @classmethod
    def success(cls, value: Any = None) -> "ProcessResult":
        return cls(ProcessOutcome.SUCCESS, value=value)

    @classmethod
    def failed(
        cls,
        error: str,
        *,
        errors: Optional[Tuple[str, ...]] = None,
    ) -> "ProcessResult":
        return cls(ProcessOutcome.FAILED, error=error, errors=errors)

    @classmethod
    def requeued(cls) -> "ProcessResult":
        return cls(ProcessOutcome.REQUEUED)

    @classmethod
    def cancelled(cls) -> "ProcessResult":
        return cls(ProcessOutcome.CANCELLED)
