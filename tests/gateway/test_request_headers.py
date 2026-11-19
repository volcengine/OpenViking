"""Model-route responses carry the request ID and say which errors the gateway produced."""

import json
from types import SimpleNamespace

import aiohttp
import pytest
from aiohttp import web

from openviking_gateway import proxy

CHAT = {"model": "model", "messages": [{"role": "user", "content": "Hello there"}]}
ERROR_HEADERS = (
    "x-openviking-error-reason",
    "x-openviking-error-origin",
    "x-openviking-upstream-attempted",
)
HOST_HEADERS = {
    "X-OpenViking-Request-Id": "host-log.id:42/a_b",
    "X-OpenViking-Resource-Id": "library-7",
    "X-OpenViking-Request-Source": "web_app",
}


@pytest.fixture
def gateway_config():
    return {"request_log_sink": "stdout"}


def only_record(capsys):
    lines = capsys.readouterr().out.splitlines()
    found = [value for line in lines if (value := json.loads(line)).get("event") == "request"]
    assert len(found) == 1, found
    return found[0]


def bearer(issued, **headers):
    return {"Authorization": "Bearer " + issued["key"], **headers}


async def test_request_id_is_returned_on_success_error_and_stream(running_gateway, capsys):
    _, client, _, issued, _, _ = running_gateway
    for headers, body, status in [
        (bearer(issued), CHAT, 200),
        (bearer(issued), {**CHAT, "stream": True}, 200),
        ({"Authorization": "Bearer ovgw_unknown"}, CHAT, 401),
    ]:
        capsys.readouterr()
        response = await client.post("/v1/chat/completions", headers=headers, json=body)
        assert response.status_code == status
        identifier = response.headers["x-openviking-request-id"]
        assert only_record(capsys)["request_id"] == identifier
        if status == 401:
            assert response.json()["request_id"] == identifier
    response = await client.get("/v1/models", headers=bearer(issued))
    assert response.status_code == 200 and response.headers["x-openviking-request-id"]


async def test_valid_inbound_request_id_is_adopted(running_gateway, capsys):
    _, client, _, issued, _, _ = running_gateway
    capsys.readouterr()
    response = await client.post(
        "/v1/chat/completions",
        headers=bearer(
            issued, **{"X-OpenViking-Request-Id": HOST_HEADERS["X-OpenViking-Request-Id"]}
        ),
        json=CHAT,
    )
    assert response.headers["x-openviking-request-id"] == "host-log.id:42/a_b"
    record = only_record(capsys)
    assert record["request_id"] == "host-log.id:42/a_b" and "request_id_rejected" not in record


@pytest.mark.parametrize("value", ["has space", "x" * 129, "semi;colon", ""])
async def test_invalid_inbound_request_id_is_replaced_and_flagged(running_gateway, capsys, value):
    _, client, _, issued, _, _ = running_gateway
    capsys.readouterr()
    response = await client.post(
        "/v1/chat/completions",
        headers=bearer(issued, **{"X-OpenViking-Request-Id": value}),
        json=CHAT,
    )
    identifier = response.headers["x-openviking-request-id"]
    assert identifier != value and len(identifier) == 32
    record = only_record(capsys)
    assert record["request_id"] == identifier and record["request_id_rejected"] is True


@pytest.mark.parametrize("origin", ["gateway", "openviking", "upstream"])
async def test_gateway_errors_carry_reason_origin_and_attempted(
    running_gateway, monkeypatch, origin
):
    app, client, _, issued, _, _ = running_gateway
    headers = bearer(issued)
    if origin == "gateway":
        headers["X-OpenViking-Upstream-URL"] = "https://elsewhere.example/v1"
        expected = (403, "upstream_url_not_allowed", "0")
    elif origin == "openviking":
        app.state.test_backend["health_status"] = {"user-key": 402}
        app.state.viking.identities.clear()
        expected = (402, "openviking_not_ordered", "0")
    else:

        async def refuse(*args, **kwargs):
            raise aiohttp.ClientConnectorError(
                SimpleNamespace(host="upstream", port=443, ssl=True), ConnectionRefusedError()
            )

        monkeypatch.setattr(proxy, "open_upstream", refuse)
        expected = (502, "upstream_unreachable", "1")
    response = await client.post("/v1/chat/completions", headers=headers, json=CHAT)
    status, reason, attempted = expected
    assert response.status_code == status and response.json()["reason"] == reason
    assert response.headers["x-openviking-error-reason"] == reason
    assert response.headers["x-openviking-error-origin"] == origin
    assert response.headers["x-openviking-upstream-attempted"] == attempted
    assert "retry-after" not in response.headers


async def test_relayed_upstream_error_has_no_error_headers(running_gateway):
    _, client, _, issued, _, _ = running_gateway
    response = await client.post(
        "/v1/chat/completions", headers=bearer(issued), json={**CHAT, "model": "error"}
    )
    assert response.status_code == 429 and response.headers["retry-after"] == "4"
    assert response.headers["x-openviking-request-id"]
    assert not any(name in response.headers for name in ERROR_HEADERS)


@pytest.mark.parametrize("status", [200, 503])
async def test_upstream_cannot_forge_gateway_headers(running_gateway, status):
    app, client, _, issued, _, _ = running_gateway
    forged = {
        "X-OpenViking-Request-Id": "forged",
        "X-OpenViking-Error-Reason": "store_unavailable",
        "X-OpenViking-Error-Origin": "gateway",
        "X-OpenViking-Upstream-Attempted": "0",
        "X-OpenViking-Degraded": "forged",
    }

    async def backend(request):
        if request.path == "/v1/chat/completions":
            return web.json_response({"error": {"code": "busy"}}, status=status, headers=forged)

    app.state.test_backend["handler"] = backend
    response = await client.post("/v1/chat/completions", headers=bearer(issued), json=CHAT)
    assert response.status_code == status
    assert response.headers["x-openviking-request-id"] != "forged"
    assert "x-openviking-degraded" not in response.headers
    assert not any(name in response.headers for name in ERROR_HEADERS)


async def test_degraded_header_lists_degradations(running_gateway):
    app, client, _, issued, _, _ = running_gateway
    response = await client.post(
        "/v1/chat/completions", headers=bearer(issued, **{"X-OpenViking-Session": "s"}), json=CHAT
    )
    assert "x-openviking-degraded" not in response.headers
    app.state.viking.unavailable_reason = "openviking_unavailable"
    response = await client.post(
        "/v1/chat/completions", headers=bearer(issued, **{"X-OpenViking-Session": "d"}), json=CHAT
    )
    assert response.status_code == 200
    assert response.headers["x-openviking-degraded"] == "revocation_check_unavailable,recall_failed"


@pytest.mark.parametrize(
    "gateway_config",
    [
        {"request_log_sink": "stdout"},
        {"request_log_sink": "stdout", "trusted_host_headers": True},
    ],
    ids=["untrusted", "trusted"],
)
async def test_host_headers_are_recorded_only_when_trusted(running_gateway, capsys):
    app, client, _, issued, _, _ = running_gateway
    trusted = app.state.config.trusted_host_headers
    for headers, recorded in [
        (HOST_HEADERS, trusted),
        (
            {"X-OpenViking-Resource-Id": "bad id", "X-OpenViking-Request-Source": "Web App"},
            False,
        ),
    ]:
        capsys.readouterr()
        response = await client.post(
            "/v1/chat/completions", headers=bearer(issued, **headers), json=CHAT
        )
        assert response.status_code == 200
        record = only_record(capsys)
        if recorded:
            assert record["resource_id"] == "library-7" and record["request_source"] == "web_app"
        else:
            assert "resource_id" not in record and "request_source" not in record


async def test_openviking_calls_carry_the_request_id(running_gateway):
    app, client, _, issued, _, _ = running_gateway
    seen = []

    async def backend(request):
        if request.path in {"/health", "/api/v1/search/search"}:
            seen.append((request.path, request.headers.get("X-Request-ID")))

    app.state.test_backend["handler"] = backend
    app.state.viking.identities.clear()
    response = await client.post(
        "/v1/chat/completions",
        headers=bearer(issued, **{"X-OpenViking-Session": "traced"}),
        json=CHAT,
    )
    assert response.status_code == 200
    identifier = response.headers["x-openviking-request-id"]
    assert {path for path, _ in seen} == {"/health", "/api/v1/search/search"}
    assert all(value == identifier for _, value in seen)

    seen.clear()
    app.state.viking.identities.clear()
    response = await client.get("/v1/models", headers=bearer(issued))
    assert response.status_code == 200
    identifier = response.headers["x-openviking-request-id"]
    assert [path for path, _ in seen] == ["/health"]
    assert all(value == identifier for _, value in seen)


async def test_new_request_headers_never_reach_the_upstream(running_gateway):
    app, client, _, issued, captured, _ = running_gateway
    app.state.config.trusted_host_headers = True
    response = await client.post(
        "/v1/chat/completions", headers=bearer(issued, **HOST_HEADERS), json=CHAT
    )
    assert response.status_code == 200
    (_, _, headers) = captured[-1]
    assert not [name for name in headers if name.lower().startswith("x-openviking-")]
    assert "X-Request-ID" not in headers and "x-request-id" not in headers
