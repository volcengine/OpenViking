# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar

from .codecs import decode_value
from .models import MockRecord, ReplayDataMissingError, ReplayRecordedException


class ReplaySession:
    def __init__(self, records: list[MockRecord]) -> None:
        self._records = records.copy()
        self._consumed: set[int] = set()

    @classmethod
    def from_records(cls, records: list[MockRecord]) -> ReplaySession:
        return cls(records)

    def consume(self, name: str, match_key: dict) -> object:
        for index, record in enumerate(self._records):
            if index in self._consumed:
                continue
            if record.name != name or record.match_key != match_key:
                continue
            self._consumed.add(index)
            if record.outcome == "returned":
                if record.result is None:
                    raise ReplayDataMissingError(f"Replay mock {name!r} has no recorded result")
                return decode_value(record.result)
            if record.exception is None:
                raise ReplayDataMissingError(f"Replay mock {name!r} has no recorded exception")
            exception = decode_value(record.exception)
            if not isinstance(exception, dict):
                raise ReplayDataMissingError(f"Replay mock {name!r} has an invalid exception")
            raise ReplayRecordedException(
                str(exception.get("type", "Exception")), str(exception.get("message", ""))
            )
        raise ReplayDataMissingError(
            f"No unconsumed replay data for mock {name!r} and match key {match_key!r}"
        )

    @property
    def unconsumed_records(self) -> list[MockRecord]:
        return [record for index, record in enumerate(self._records) if index not in self._consumed]


_CURRENT_REPLAY_SESSION: ContextVar[ReplaySession | None] = ContextVar(
    "openviking_replay_session", default=None
)


def current_replay_session() -> ReplaySession | None:
    return _CURRENT_REPLAY_SESSION.get()


@contextmanager
def bind_replay_session(session: ReplaySession) -> Iterator[ReplaySession]:
    token = _CURRENT_REPLAY_SESSION.set(session)
    try:
        yield session
    finally:
        _CURRENT_REPLAY_SESSION.reset(token)
