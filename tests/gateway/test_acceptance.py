"""Prevent opt-in live checks from reporting unsupported evidence as success."""

import importlib.util
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location(
    "gateway_acceptance", Path(__file__).parents[2] / "scripts/context_gateway_acceptance.py"
)
acceptance = importlib.util.module_from_spec(spec)
spec.loader.exec_module(acceptance)


def test_cache_check_accepts_partial_last_block():
    acceptance.check_cache(
        [
            {"turn": 1, "usage": {"input_tokens": 4570, "cached_tokens": 0}},
            {"turn": 2, "usage": {"input_tokens": 4690, "cached_tokens": 4480}},
            {"turn": 3, "usage": {"input_tokens": 4728, "cached_tokens": 4608}},
        ],
        128,
    )


@pytest.mark.parametrize("cached", [0, 128])
def test_cache_check_rejects_missing_or_short_prefix(cached):
    with pytest.raises(RuntimeError, match="cached prefix"):
        acceptance.check_cache(
            [
                {"turn": 1, "usage": {"input_tokens": 4096, "cached_tokens": 0}},
                {"turn": 2, "usage": {"input_tokens": 4200, "cached_tokens": cached}},
            ],
            128,
        )
