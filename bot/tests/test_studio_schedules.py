"""Scheduler inventory shares live state without exposing execution credentials."""

from vikingbot.config.schema import SessionKey
from vikingbot.cron.service import CronService
from vikingbot.cron.types import CronSchedule
from vikingbot.studio.schedules import snapshot


def test_inventory_includes_disabled_and_excludes_private_execution_context(tmp_path):
    cron = CronService(tmp_path / "jobs.json")
    job = cron.add_job(
        "Reminder",
        CronSchedule(kind="every", every_ms=60000),
        "Check updates",
        SessionKey(type="cli", channel_id="default", chat_id="test"),
        channel_metadata={"api_key": "secret"},
    )
    cron.enable_job(job.id, False)
    job.state.last_error = "private upstream error"
    job.state.last_status = "error"
    result = snapshot(cron)
    assert result["available"] is True
    assert result["running"] is False
    assert len(result["jobs"]) == 1
    assert result["jobs"][0]["enabled"] is False
    assert result["jobs"][0]["state"]["last_status"] == "error"
    assert "secret" not in str(result)
    assert "private upstream error" not in str(result)
    assert "session_key" not in str(result)


def test_missing_scheduler_is_not_an_empty_running_scheduler():
    assert snapshot(None) == {"available": False, "running": False, "jobs": []}
