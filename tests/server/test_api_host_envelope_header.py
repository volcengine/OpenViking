# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

"""Client-declared host envelopes via X-OpenViking-Host-Envelope."""

from types import SimpleNamespace

import httpx
import pytest

from openviking.server.config import HostEnvelopeConfig
from openviking.utils.host_envelope import BUILTIN_ENVELOPES
from openviking_cli.retrieve import FindResult

HEADER = "X-OpenViking-Host-Envelope"
WRAPPED = "<system-reminder>\nTodo list is empty\n</system-reminder>\nwhy is recall slow?"
BRIDGE_TURN = "<bridge_reminder>r</bridge_reminder>\n<user_message>\nhi there\n</user_message>"


@pytest.fixture
def queries(app, service, monkeypatch):
    app.state.config.host_envelope = HostEnvelopeConfig(
        custom=[{"name": "bridge", "detect_any": ["bridge_reminder"], "extract": "user_message"}]
    )
    app.state.search_host_envelopes = ()
    calls = []

    async def fake_find(**kwargs):
        calls.append(kwargs["query"])
        return FindResult(memories=[], resources=[], skills=[])

    monkeypatch.setattr(service.search, "find", fake_find)
    return calls


async def test_declared_envelope_applies_when_config_is_off(client: httpx.AsyncClient, queries):
    response = await client.post(
        "/api/v1/search/find", json={"query": WRAPPED}, headers={HEADER: "claude-code"}
    )

    assert response.status_code == 200
    assert queries == ["why is recall slow?"]


async def test_declared_custom_envelope_and_order(client: httpx.AsyncClient, queries):
    response = await client.post(
        "/api/v1/search/find", json={"query": BRIDGE_TURN}, headers={HEADER: "bridge, claude-code"}
    )

    assert response.status_code == 200
    assert queries == ["hi there"]


async def test_none_turns_configured_envelopes_off(client: httpx.AsyncClient, app, queries):
    app.state.search_host_envelopes = (BUILTIN_ENVELOPES["claude-code"],)
    response = await client.post(
        "/api/v1/search/find", json={"query": WRAPPED}, headers={HEADER: "none"}
    )

    assert response.status_code == 200
    assert queries == [WRAPPED]


async def test_unknown_declared_envelope_is_rejected(client: httpx.AsyncClient, queries):
    response = await client.post(
        "/api/v1/search/find", json={"query": WRAPPED}, headers={HEADER: "nope"}
    )

    assert response.status_code == 400
    assert queries == []


@pytest.mark.parametrize("batch", [False, True])
async def test_session_writes_receive_declared_envelopes(
    client: httpx.AsyncClient, app, service, monkeypatch, batch
):
    app.state.config.host_envelope = HostEnvelopeConfig()
    received = []

    class FakeSession:
        messages = []
        meta = SimpleNamespace(pending_tokens=0)

        async def add_messages_async(self, specs, host_envelopes=None):
            received.append(host_envelopes)
            return []

    async def fake_open(*args, **kwargs):
        return FakeSession()

    async def no_auto_commit(*args, **kwargs):
        return None

    monkeypatch.setattr(service.sessions, "open", fake_open)
    monkeypatch.setattr(service.sessions, "maybe_schedule_auto_commit", no_auto_commit)
    message = {"role": "user", "content": WRAPPED}
    path = "/api/v1/sessions/s1/messages" + ("/batch" if batch else "")
    payload = {"messages": [message]} if batch else message

    assert (await client.post(path, json=payload)).status_code == 200
    assert (
        await client.post(path, json=payload, headers={HEADER: "claude-code"})
    ).status_code == 200
    assert received == [None, [BUILTIN_ENVELOPES["claude-code"]]]
