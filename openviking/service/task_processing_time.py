# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Union of worker execution intervals; queue waits do not advance the clock.

Only tasks created in this process have complete coverage. After a restart we
leave active tasks unmeasured rather than inventing time across the downtime.
"""

import time
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Iterator

if TYPE_CHECKING:
    from openviking.service.task_work_index import TaskWorkIndex

# Inherited by shielded child coroutines; DAG nodes install their own owner.
processing_owner: ContextVar[tuple["TaskWorkIndex", str, Any] | None] = ContextVar(
    "processing_owner", default=None
)


@contextmanager
def pause_task_processing() -> Iterator[None]:
    owner = processing_owner.get()
    if owner is None:
        yield
    else:
        index, task_id, worker = owner
        with index.pause_processing(task_id, worker=worker):
            yield


@dataclass
class ProcessingClock:
    elapsed: float = 0.0
    started: float | None = None
    workers: set[Any] = field(default_factory=set)
    observed: bool = False

    def enter(self, worker: Any) -> None:
        if not self.workers:
            self.started = time.monotonic()
        self.workers.add(worker)
        self.observed = True

    def leave(self, worker: Any) -> None:
        self.workers.discard(worker)
        if not self.workers and self.started is not None:
            self.elapsed += time.monotonic() - self.started
            self.started = None

    def seconds(self) -> float | None:
        if not self.observed:
            return None
        return self.elapsed + (time.monotonic() - self.started if self.started is not None else 0.0)
