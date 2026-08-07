"""Regression tests for issue #3274: bounded, non-aggressive defaults for
SessionCompressorV2 memory path-lock retry parameters.

The previous defaults (interval=0.2s, max_retries=0=unbounded) caused ~140% CPU,
event-loop starvation and INFO log floods under concurrent memory extraction.
These tests pin the safer production defaults validated in the issue report:
3.0s outer retry interval with a 100-attempt bound (~5 minutes ceiling),
matching the field-verified mitigation from the obsolete #3281 proposal.
"""

from __future__ import annotations

import pytest

from openviking_cli.utils.config.memory_config import MemoryConfig


class TestMemoryLockRetryDefaults:
    def test_default_retry_interval_is_three_seconds(self) -> None:
        """The retry interval default must be 3.0s (field-validated outer bound),
        not the aggressive 0.2s inner poll used by the Rust PathLock manager."""
        cfg = MemoryConfig()
        assert cfg.v2_lock_retry_interval_seconds == 3.0

    def test_default_max_retries_is_bounded(self) -> None:
        """max_retries must default to a positive bound (not 0=unlimited)."""
        cfg = MemoryConfig()
        assert cfg.v2_lock_max_retries > 0
        # 100 at 3.0s ~ 5 minutes of outer compressor wait.
        assert cfg.v2_lock_max_retries == 100

    def test_zero_max_retries_still_means_unlimited(self) -> None:
        """0 must still be accepted and retain its documented "unlimited" meaning
        for operators who explicitly opt in."""
        cfg = MemoryConfig(v2_lock_max_retries=0)
        assert cfg.v2_lock_max_retries == 0

    @pytest.mark.parametrize("bad_value", [-1, -0.1])
    def test_negative_interval_rejected(self, bad_value: float) -> None:
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            MemoryConfig(v2_lock_retry_interval_seconds=bad_value)

    def test_override_honours_explicit_values(self) -> None:
        cfg = MemoryConfig(
            v2_lock_retry_interval_seconds=3.0,
            v2_lock_max_retries=100,
        )
        assert cfg.v2_lock_retry_interval_seconds == 3.0
        assert cfg.v2_lock_max_retries == 100
