"""Idempotent append through the HTTP owner and real RagFS test storage."""

import asyncio

import pytest


async def test_append_replays_the_original_result_after_a_lost_reply(client):
    path = "/api/v1/sessions/append-replay/messages/append"
    body = {"messages": [{"role": "user", "content": "synthetic"}], "idempotency_key": "batch"}
    first = await client.post(path, json=body)
    assert first.status_code == 200, first.text
    replay = await client.post(path, json=body)
    assert replay.status_code == 200, replay.text
    assert replay.json()["result"] == first.json()["result"]
    changed = await client.post(
        path, json={**body, "messages": [{"role": "user", "content": "changed"}]}
    )
    assert changed.status_code == 409, changed.text
    legacy = await client.post(
        "/api/v1/sessions/append-replay/messages/batch",
        json={"messages": [{"role": "assistant", "content": "legacy"}]},
    )
    assert legacy.status_code == 200, legacy.text
    assert legacy.json()["result"]["message_count"] == 2


async def test_concurrent_duplicate_batches_return_one_receipt(client):
    body = {"messages": [{"role": "user", "content": "once"}], "idempotency_key": "same"}
    responses = await asyncio.gather(
        *[
            client.post("/api/v1/sessions/append-concurrent/messages/append", json=body)
            for _ in range(8)
        ]
    )
    assert all(r.status_code == 200 for r in responses), [r.text for r in responses]
    assert all(r.json()["result"]["message_count"] == 1 for r in responses)


@pytest.mark.parametrize("key", ["", " ", "x" * 129, 123, True, None])
async def test_invalid_operation_keys_fail_before_session_creation(client, key):
    response = await client.post(
        "/api/v1/sessions/append-invalid/messages/append",
        json={"messages": [{"role": "user", "content": "synthetic"}], "idempotency_key": key},
    )
    assert response.status_code in {400, 422}
    state = await client.get("/api/v1/sessions/append-invalid")
    assert state.status_code == 404


async def test_append_receipt_is_private_in_direct_and_parent_file_operations(client):
    root = "/api/v1/sessions/append-private"
    response = await client.post(
        root + "/messages/append",
        json={"messages": [{"role": "user", "content": "synthetic"}], "idempotency_key": "private"},
    )
    assert response.status_code == 200, response.text
    state = await client.get(root)
    uri = state.json()["result"]["uri"]
    for leaf in [".append_receipts/pending.json", "%2eappend_receipts/pending.json"]:
        result = await client.get("/api/v1/content/read", params={"uri": uri + "/" + leaf})
        assert result.status_code == 403, result.text
    listing = await client.get("/api/v1/fs/ls", params={"uri": uri, "show_all_hidden": True})
    assert listing.status_code == 200, listing.text
    assert ".append_receipts" not in listing.text
    copied = await client.post(
        "/api/v1/fs/cp",
        json={
            "from_uri": uri,
            "to_uri": "viking://resources/copied-session",
            "recursive": True,
        },
    )
    assert copied.status_code == 403, copied.text


async def test_append_replay_preserves_registered_privacy_and_externalization(client, service):
    from openviking.server.config import ToolOutputExternalizationConfig, UserSettingsPatch
    from openviking.server.identity import RequestContext, Role
    from openviking.server.user_config import patch_user_config
    from openviking_cli.session.user_id import UserIdentifier

    ctx = RequestContext(user=UserIdentifier.the_default_user(), role=Role.ROOT)
    await patch_user_config(
        service.viking_fs,
        ctx,
        UserSettingsPatch.model_validate({"privacy": {"mode": "registered"}}),
    )
    await service.privacy_configs.upsert(
        ctx, "credential", "synthetic", {"value": "registered-synthetic-value"}
    )
    service.sessions.set_tool_output_externalization_config(
        ToolOutputExternalizationConfig(enabled=True, threshold_chars=1024, preview_chars=200)
    )
    body = {
        "messages": [
            {
                "role": "assistant",
                "parts": [
                    {
                        "type": "tool",
                        "tool_id": "read",
                        "tool_output": "padding\n" * 1500 + "registered-synthetic-value",
                    }
                ],
            }
        ],
        "idempotency_key": "private-tool",
    }
    path = "/api/v1/sessions/append-tool/messages/append"
    first = await client.post(path, json=body)
    replay = await client.post(path, json=body)
    assert first.status_code == replay.status_code == 200, [first.text, replay.text]
    assert first.json()["result"] == replay.json()["result"]
    session = await service.sessions.open("append-tool", ctx)
    stored = await service.viking_fs.read_file(session.uri + "/messages.jsonl", ctx=ctx)
    assert "registered-synthetic-value" not in stored
    import json

    part = json.loads(stored)["parts"][0]
    assert part["tool_output_ref"]
    content = await service.viking_fs.read_file(part["tool_output_ref"] + "/output.txt", ctx=ctx)
    assert "registered-synthetic-value" not in content
