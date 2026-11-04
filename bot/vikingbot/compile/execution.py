"""Shared deadlines and continuation limits for one logical Reduce job."""

from __future__ import annotations

import asyncio
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any


class ReduceExecutionError(ValueError):
    """A terminal execution limit that must not restart the job's retry allowance."""


@dataclass
class ReduceBudget:
    """Bound all windows and transport retries of a single group.

    window_seconds includes request queueing. sessions retain private scratch and
    checkpoints by assignment fingerprint; a changed assignment cannot reuse them.
    """

    max_windows: int
    window_seconds: float
    windows: int = 0
    sessions: dict[str, Any] = field(default_factory=dict)
    deadline: float = field(init=False)

    def __post_init__(self):
        self.deadline = asyncio.get_running_loop().time() + self.max_windows * self.window_seconds

    def remaining(self) -> float:
        """Return remaining job seconds; an expired budget is a terminal failure."""
        seconds = self.deadline - asyncio.get_running_loop().time()
        if seconds <= 0:
            raise self.error("total deadline exhausted")
        return seconds

    def start_window(self) -> None:
        """Charge a window before any request; failed windows also consume the allowance."""
        self.remaining()
        if self.windows >= self.max_windows:
            raise self.error("continuation window limit exhausted")
        self.windows += 1

    def error(self, reason: str) -> ReduceExecutionError:
        """Describe the shared execution budget without exposing source content."""
        for session in reversed(list(self.sessions.values())):
            checkpoint = session.get("checkpoint")
            if checkpoint is not None and checkpoint.latest is not None:
                reason += (
                    f"; checkpoint retains {len(checkpoint.latest['pending_inputs'])} inputs "
                    f"and {len(checkpoint.latest['pending_tasks'])} unfinished tasks"
                )
                break
        return ReduceExecutionError(
            f"Reduce incomplete ({self.windows}/{self.max_windows} windows): {reason}"
        )


# Context-local budgets isolate concurrent groups while propagating through model calls.
reduce_budget: ContextVar[ReduceBudget | None] = ContextVar("compile_reduce_budget", default=None)
