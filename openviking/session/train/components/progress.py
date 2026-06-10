# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Small terminal progress helper for train components."""

from __future__ import annotations

import sys
from dataclasses import dataclass


@dataclass(slots=True)
class ProgressPrinter:
    """Render a single-line percentage progress indicator to stdout."""

    total: int
    label: str
    enabled: bool
    completed: int = 0
    _finished: bool = False

    def render(self) -> None:
        if not self.enabled or self.total <= 0:
            return
        self._write()

    def advance(self) -> None:
        if not self.enabled or self.total <= 0 or self._finished:
            return
        self.completed = min(self.total, self.completed + 1)
        self._write()

    def finish(self) -> None:
        if not self.enabled or self.total <= 0 or self._finished:
            return
        self._finished = True
        self._write(newline=True)

    def _write(self, *, newline: bool = False) -> None:
        percent = (self.completed / self.total) * 100.0
        width = 24
        filled = int(width * self.completed / self.total) if self.total else 0
        bar = "#" * filled + "." * (width - filled)
        suffix = "\n" if newline else ""
        sys.stdout.write(
            f"\r[{self.label}] [{bar}] {percent:6.2f}% "
            f"{self.completed}/{self.total}{suffix}"
        )
        sys.stdout.flush()
