# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Process-local restart capability, installed by the single-worker CLI."""

import asyncio
from collections.abc import Callable
from uuid import uuid4

from openviking_cli.exceptions import FailedPreconditionError


class RestartController:
    def __init__(self, shutdown: Callable[[], None] | None = None):
        self.instance_id = uuid4().hex
        self.shutdown = shutdown
        self.requested = False
        self.lock = asyncio.Lock()

    def status(self) -> dict:
        return {
            "supported": self.shutdown is not None,
            "instance_id": self.instance_id,
            "restarting": self.requested,
        }

    def request(self) -> dict:
        if self.shutdown is None:
            raise FailedPreconditionError(
                "Remote restart requires the single-worker openviking-server CLI"
            )
        self.requested = True
        return self.status()

    def stop(self) -> None:
        if self.requested and self.shutdown is not None:
            self.shutdown()
