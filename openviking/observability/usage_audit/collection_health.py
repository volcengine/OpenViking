"""Bounded, principal-scoped counters for the existing best-effort writer.

Counters describe deliveries, not distinct provider RPCs or a complete bill.
A producer that crashes before its first flush may never be observed.
"""

from __future__ import annotations

import threading
from datetime import datetime, timezone
from uuid import uuid4

FIELDS = ("accepted", "persisted", "dropped", "failed", "uncertain")


class CollectionHealth:
    def __init__(self, maximum_scopes=1024):
        self.producer_id = uuid4().hex
        self.started_at = datetime.now(timezone.utc).isoformat()
        self._scopes = {}
        self._maximum_scopes = maximum_scopes
        self._lock = threading.Lock()

    def add(self, events, field):
        with self._lock:
            for event in events:
                key = (str(event.account_id or "__unknown__"), str(event.user_id or ""))
                row = self._scopes.get(key)
                if row is None:
                    if len(self._scopes) >= self._maximum_scopes:
                        continue
                    row = {
                        "producer_id": self.producer_id,
                        "account_id": key[0],
                        "user_id": key[1],
                        "started_at": self.started_at,
                        "revision": 0,
                        **dict.fromkeys(FIELDS, 0),
                    }
                    self._scopes[key] = row
                row[field] += 1
                row["revision"] += 1

    def snapshot(self, *, clean=False):
        with self._lock:
            now = datetime.now(timezone.utc).isoformat()
            return [
                {
                    **row,
                    "updated_at": now,
                    "clean": int(clean),
                    "pending": max(
                        0, row["accepted"] - row["persisted"] - row["failed"] - row["uncertain"]
                    ),
                }
                for row in self._scopes.values()
            ]
