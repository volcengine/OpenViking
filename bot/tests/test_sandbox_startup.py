# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Regression tests for sandbox construction and startup failures."""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from vikingbot.config.schema import Config, SessionKey  # noqa: E402
from vikingbot.sandbox.backends.srt import SrtBackend  # noqa: E402
from vikingbot.sandbox.manager import SandboxManager  # noqa: E402


def test_srt_backend_uses_workspace_id_and_nested_config(tmp_path):
    config = Config().sandbox
    config.backends.srt.network.allowed_domains = ["example.com"]

    backend = SrtBackend(config, "shared", tmp_path / "shared")

    assert backend._settings_path.name == "shared-srt-settings.json"
    settings = json.loads(backend._settings_path.read_text())
    assert settings["network"]["allowedDomains"] == ["example.com"]
    assert settings["filesystem"]["allowWrite"][0] == str((tmp_path / "shared").resolve())


@pytest.mark.asyncio
async def test_startup_failure_is_not_cached_and_can_retry(tmp_path):
    class FailingBackend:
        instances = []

        def __init__(self, config, workspace_id, workspace):
            self.stopped = False
            self.instances.append(self)

        async def start(self):
            raise RuntimeError("startup failed")

        async def stop(self):
            self.stopped = True

    manager = SandboxManager(Config(), tmp_path / "sandboxes", tmp_path / "source")
    manager._backend_cls = FailingBackend
    session_key = SessionKey(type="cli", channel_id="default", chat_id="test")

    for expected_attempts in (1, 2):
        with pytest.raises(RuntimeError, match="startup failed"):
            await manager.get_sandbox(session_key)
        assert manager._sandboxes == {}
        assert len(FailingBackend.instances) == expected_attempts
        assert FailingBackend.instances[-1].stopped
