from datetime import datetime, timezone

import pytest

from openviking.service import compile_scheduler
from openviking_cli.utils.config.open_viking_config import CompileApiConfig


@pytest.fixture(autouse=True)
def _restore_timezone():
    yield
    compile_scheduler.configure_timezone(compile_scheduler.DEFAULT_TIMEZONE)


def test_schedule_defaults_to_asia_shanghai():
    now = datetime(2026, 11, 9, 2, 0, tzinfo=timezone.utc)  # 10:00 in Shanghai
    slot = compile_scheduler.next_slot({"frequency": "daily", "hour": 9, "minute": 0}, now)
    assert slot.isoformat() == "2026-11-10T09:00:00+08:00"


def test_schedule_follows_configured_timezone():
    compile_scheduler.configure_timezone("UTC")
    now = datetime(2026, 11, 9, 2, 0, tzinfo=timezone.utc)
    slot = compile_scheduler.next_slot({"frequency": "daily", "hour": 9, "minute": 0}, now)
    assert slot.isoformat() == "2026-11-09T09:00:00+00:00"


def test_config_rejects_unknown_timezone():
    assert CompileApiConfig(scheduler_timezone="UTC").scheduler_timezone == "UTC"
    with pytest.raises(ValueError):
        CompileApiConfig(scheduler_timezone="Mars/Olympus")
