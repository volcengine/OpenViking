# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Small terminal progress helper for train components."""

from __future__ import annotations

import sys
from dataclasses import dataclass


@dataclass(slots=True)
class ProgressPrinter:
    """Render a single-line P/R/C progress indicator to stdout."""

    total: int
    label: str
    enabled: bool
    pending: int = 0
    running: int = 0
    completed: int = 0
    _finished: bool = False

    def __post_init__(self) -> None:
        if self.pending == 0 and self.running == 0 and self.completed == 0:
            self.pending = max(0, self.total)

    def render(self) -> None:
        if not self.enabled or self.total <= 0:
            return
        self._write()

    def start_one(self) -> None:
        if not self.enabled or self.total <= 0 or self._finished:
            return
        if self.pending > 0:
            self.pending -= 1
        self.running += 1
        self._write()

    def complete_one(self) -> None:
        if not self.enabled or self.total <= 0 or self._finished:
            return
        if self.running > 0:
            self.running -= 1
        elif self.pending > 0:
            self.pending -= 1
        self.completed = min(self.total, self.completed + 1)
        self._write()

    def advance(self) -> None:
        """Compatibility helper for callers that only track completion."""
        self.complete_one()

    def finish(self) -> None:
        if not self.enabled or self.total <= 0 or self._finished:
            return
        self._finished = True
        self._write(newline=True)

    def _write(self, *, newline: bool = False) -> None:
        width = 24
        pending_width, running_width, completed_width = _state_widths(
            pending=self.pending,
            running=self.running,
            completed=self.completed,
            total=self.total,
            width=width,
        )
        bar = "P" * pending_width + "R" * running_width + "C" * completed_width
        percent = (self.completed / self.total) * 100.0
        suffix = "\n" if newline else ""
        sys.stdout.write(
            f"\r[{self.label}] [{bar}] {percent:6.2f}% "
            f"({self.completed}/{self.total}){suffix}"
        )
        sys.stdout.flush()


def _state_widths(
    *,
    pending: int,
    running: int,
    completed: int,
    total: int,
    width: int,
) -> tuple[int, int, int]:
    values = [pending, running, completed]
    if total <= 0 or width <= 0:
        return 0, 0, 0

    exact = [(value / total) * width if value > 0 else 0.0 for value in values]
    widths = [int(value) for value in exact]

    for idx, value in enumerate(values):
        if value > 0 and widths[idx] == 0:
            widths[idx] = 1

    while sum(widths) > width:
        candidates = [
            idx
            for idx, value in enumerate(values)
            if widths[idx] > (1 if value > 0 else 0)
        ]
        if not candidates:
            break
        idx = max(candidates, key=lambda item: widths[item])
        widths[idx] -= 1

    while sum(widths) < width:
        idx = max(range(len(values)), key=lambda item: exact[item] - int(exact[item]))
        widths[idx] += 1

    return widths[0], widths[1], widths[2]
