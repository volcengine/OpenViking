# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Process-local allowance for extra model attempts owned by one offline task."""

import threading


class RetryBudget:
    def __init__(self, max_retries: int = 3) -> None:
        if isinstance(max_retries, bool) or not isinstance(max_retries, int) or max_retries < 0:
            raise ValueError("max_retries must be a non-negative integer")
        self.max_retries = max_retries
        self._retry_count = 0
        self._lock = threading.Lock()

    @property
    def retry_count(self) -> int:
        with self._lock:
            return self._retry_count

    def try_consume(self) -> bool:
        """Reserve one extra attempt across threads/loops; never refund sent work."""
        with self._lock:
            if self._retry_count >= self.max_retries:
                return False
            self._retry_count += 1
            return True


def configured_model_retries() -> int:
    from openviking_cli.utils.config import get_openviking_config

    try:
        return get_openviking_config().model_retry.max_retries
    except FileNotFoundError:
        # Standalone model utilities may be used without a running OV service.
        return 3
