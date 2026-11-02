"""Explicit OpenViking refusals stop dispatch even when the key is already cached."""

import pytest

from openviking_gateway.storage import digest


@pytest.fixture
def gateway_config():
    return {"openviking_keys": True}


@pytest.mark.parametrize("kind", ["openviking", "issued"])
@pytest.mark.parametrize("status", [402, 403, 404, 429, 503])
async def test_existing_keys_respect_refusals_and_degrade_only_when_unconfirmed(
    running_gateway, kind, status
):
    app, client, _, issued, captured, _ = running_gateway
    token = "bob-key" if kind == "openviking" else issued["key"]
    ov_key = "bob-key" if kind == "openviking" else "user-key"
    identifier = digest("openviking\0" + token) if kind == "openviking" else issued["id"]
    headers = {"Authorization": "Bearer " + token}
    assert (await client.get("/v1/models", headers=headers)).status_code == 200
    assert await app.state.management.credential(identifier)
    app.state.test_backend["health_status"] = {ov_key: status}
    app.state.viking.identities.clear()
    before = len(captured)
    response = await client.post(
        "/v1/chat/completions",
        headers=headers,
        json={"model": "model", "messages": [{"role": "user", "content": "Is this allowed?"}]},
    )
    if status in {402, 403}:
        assert response.status_code == status, response.text
        assert response.json()["reason"] == f"openviking_http_{status}"
        assert len(captured) == before
        if status == 402:
            assert "not ordered" in response.json()["detail"]
    else:
        assert response.status_code == 200, response.text
        assert len(captured) == before + 1
        logs = await app.state.management.logs("tenant")
        assert any(log.get("degradation") == "revocation_check_unavailable" for log in logs)
    assert await app.state.management.credential(identifier)
