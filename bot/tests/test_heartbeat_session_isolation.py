# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

"""Heartbeat delivery across sessions that share one workspace."""

from vikingbot.config.schema import SessionKey
from vikingbot.heartbeat.service import HEARTBEAT_OK_TOKEN, HeartbeatService
from vikingbot.session.manager import SessionManager


async def test_failing_session_does_not_skip_later_sessions(tmp_path):
    workspace = tmp_path / "workspace"
    (workspace / "shared").mkdir(parents=True)
    (workspace / "shared" / "HEARTBEAT.md").write_text("- [ ] Send the daily digest\n")

    sessions = SessionManager(tmp_path / "bot")
    keys = [SessionKey(type="telegram", channel_id="bot", chat_id=f"chat{i}") for i in range(3)]
    for key in keys:
        session = sessions.get_or_create(key)
        session.add_message("user", "hello")
        await sessions.save(session)

    ordered = [info["key"] for info in sessions.list_sessions()]
    failing = ordered[0]
    woken: list[SessionKey] = []

    async def on_heartbeat(prompt, session_key, metadata):
        woken.append(session_key)
        if session_key == failing:
            raise RuntimeError("chat is gone")
        return HEARTBEAT_OK_TOKEN

    heartbeat = HeartbeatService(
        workspace=workspace,
        on_heartbeat=on_heartbeat,
        sandbox_mode="shared",
        session_manager=sessions,
    )

    await heartbeat._tick()

    assert woken == ordered
