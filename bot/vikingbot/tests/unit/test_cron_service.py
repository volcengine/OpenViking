import json
from pathlib import Path

import pytest

from vikingbot.cron.service import CronService
from vikingbot.cron.types import CronJob, CronStore


def _service(path: Path) -> CronService:
    service = CronService(path)
    service._store = CronStore(jobs=[CronJob(id="new", name="new")])
    return service


def test_save_store_keeps_existing_file_when_write_is_interrupted(tmp_path, monkeypatch):
    path = tmp_path / "cron.json"
    original_data = {"version": 1, "jobs": []}
    path.write_text(json.dumps(original_data))
    original_write_text = Path.write_text

    def interrupted_write(self, data, *args, **kwargs):
        original_write_text(self, data[:10], *args, **kwargs)
        raise OSError("simulated interruption")

    monkeypatch.setattr(Path, "write_text", interrupted_write)

    with pytest.raises(OSError, match="simulated interruption"):
        _service(path)._save_store()

    assert json.loads(path.read_text()) == original_data
    assert not list(tmp_path.glob(".cron.json.*.tmp"))
