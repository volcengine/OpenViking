from __future__ import annotations

import pytest

from openviking_cli.utils.config.memory_config import MemoryConfig


@pytest.mark.parametrize("values", [{}, {"minimal_mode": False}, {"minimal_mode": True}])
def test_minimal_mode_is_opt_in_and_round_trips(values):
    config = MemoryConfig.model_validate(values)
    assert config.minimal_mode is values.get("minimal_mode", False)
    assert "event_range_padding" not in config.model_dump()
    assert MemoryConfig.model_validate_json(config.model_dump_json()) == config
