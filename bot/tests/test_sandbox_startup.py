# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Regression tests for sandbox construction and startup failures."""

import json

import pytest
from vikingbot.config.schema import Config, SessionKey
from vikingbot.sandbox.backends.srt import SrtBackend
from vikingbot.sandbox.manager import SandboxManager
from vikingbot.utils.session_paths import portable_path_component


def test_srt_backend_uses_workspace_id_and_nested_config(tmp_path):
    config = Config().sandbox
    config.backends.srt.network.allowed_domains = ["example.com"]

    backend = SrtBackend(config, "shared", tmp_path / "shared")

    assert backend._settings_path.name == (f"{portable_path_component('shared')}-srt-settings.json")
    settings = json.loads(backend._settings_path.read_text())
    assert settings["network"]["allowedDomains"] == ["example.com"]
    assert settings["filesystem"]["allowWrite"][0] == str((tmp_path / "shared").resolve())


@pytest.mark.asyncio
async def test_startup_failure_is_not_cached_and_can_retry(tmp_path):
    class RetryBackend:
        instances = []

        def __init__(self, config, workspace_id, workspace):
            self.stopped = False
            self.started = False
            self.workspace = workspace
            self.instances.append(self)

        async def start(self):
            if len(self.instances) == 1:
                raise RuntimeError("startup failed")
            self.workspace.mkdir(parents=True, exist_ok=True)
            self.started = True

        async def stop(self):
            self.stopped = True

    manager = SandboxManager(Config(), tmp_path / "sandboxes", tmp_path / "source")
    manager._backend_cls = RetryBackend
    session_key = SessionKey(type="cli", channel_id="default", chat_id="test")

    with pytest.raises(RuntimeError, match="startup failed"):
        await manager.get_sandbox(session_key)
    assert RetryBackend.instances[0].stopped

    try:
        recovered = await manager.get_sandbox(session_key)
        assert recovered.started
        assert len(RetryBackend.instances) == 2
        assert await manager.get_sandbox(session_key) is recovered
    finally:
        await manager.cleanup_all()
    assert recovered.stopped
