# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

EncodedValue = dict[str, Any]
ReplayOutcome = Literal["returned", "raised"]


class ReplayError(RuntimeError):
    """Base error for trace-backed replay."""


class ReplayCodecError(ReplayError):
    """Raised when a value cannot be encoded or decoded safely."""


class ReplayDataMissingError(ReplayError):
    """Raised when replay needs a mock result that was not recorded."""


class ReplayRecordedException(ReplayError):
    """Represents an exception returned by a historical mock call."""

    def __init__(self, exception_type: str, message: str) -> None:
        self.exception_type = exception_type
        self.recorded_message = message
        super().__init__(f"Recorded {exception_type}: {message}")


@dataclass(slots=True)
class EntryRecord:
    name: str
    module: str
    arguments: EncodedValue
    outcome: ReplayOutcome
    result: EncodedValue | None = None
    exception: EncodedValue | None = None
    invocation_id: str | None = None


@dataclass(slots=True)
class MockRecord:
    name: str
    match_key: EncodedValue
    outcome: ReplayOutcome
    result: EncodedValue | None = None
    exception: EncodedValue | None = None
    invocation_id: str | None = None


@dataclass(slots=True)
class ReplayResult:
    outcome: ReplayOutcome
    result: Any = None
    exception: BaseException | None = None
    unconsumed_records: list[MockRecord] = field(default_factory=list)
