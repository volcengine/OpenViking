"""The server feature table the gateway shares with the plugins."""

import json
from pathlib import Path

import pytest

from openviking_gateway import client as client_module
from openviking_gateway.client import FEATURES, VikingClient, VikingError, supports
from openviking_gateway.models import Policy

ROOT = Path(__file__).parents[2]
SOURCE = ROOT / "examples/memory-plugin-shared/lib/server-features.json"
COPY = ROOT / "openviking_gateway/server-features.json"
# The plugin reader's test suite reads the same cases, so both readers are proven to
# answer alike for every string in it.
CASES = json.loads((ROOT / "tests/fixtures/server_feature_versions.json").read_text())


def test_the_gateway_reads_a_byte_identical_copy_of_the_shared_table():
    assert COPY.read_bytes() == SOURCE.read_bytes(), (
        "run node examples/memory-plugin-shared/sync.mjs"
    )
    assert FEATURES == json.loads(SOURCE.read_text())
    assert FEATURES["working_memory_opt_in"] == "0.5.0"


@pytest.mark.parametrize("case", CASES, ids=lambda case: repr(case["version"]))
def test_supports_agrees_with_the_shared_version_cases(case):
    assert supports(case["feature"], case["version"]) is case["supported"]


def test_an_unknown_feature_is_a_programming_error():
    with pytest.raises(KeyError):
        supports("time_travel", "9.9.9")


def fake_health(client, versions, role="user"):
    """Answer /health for each key with its version; a key absent from ``versions`` is down."""

    async def request(method, path, key, body=None, timeout=30, peer=None):
        assert path == "/health"
        if key not in versions:
            raise VikingError("openviking_timeout")
        health = {"role": role, "account_id": "tenant", "user_id": "a"}
        if versions[key] is not None:
            health["version"] = versions[key]
        return health

    client.request = request


async def test_the_client_asks_the_table_with_the_version_health_reports():
    client = VikingClient(None, "http://unused", "0.4.16")
    fake_health(client, {"new": "0.5.0.dev3", "old": "0.4.23", "bad": "0.5.0abc", "none": None})
    assert await client.supports("new", "working_memory_opt_in")
    assert not await client.supports("old", "working_memory_opt_in")
    with pytest.raises(KeyError, match="no_such_feature"):
        await client.supports("new", "no_such_feature")
    # A server the gateway cannot use is an error, not a server without the feature:
    # health() refuses it exactly as it does for every other call.
    for key in ("bad", "none"):
        with pytest.raises(VikingError, match="openviking_version_mismatch"):
            await client.supports(key, "working_memory_opt_in")
    with pytest.raises(VikingError, match="openviking_timeout"):
        await client.supports("down", "working_memory_opt_in")
    root = VikingClient(None, "http://unused", "0.4.16")
    fake_health(root, {"root": "0.5.4"}, role="root")
    with pytest.raises(VikingError, match="root_key_not_allowed"):
        await root.supports("root", "working_memory_opt_in")


async def test_working_memory_opt_in_follows_the_table(monkeypatch):
    """The 0.5.0 check is the table's row, not a constant of its own."""

    async def bodies_for(version):
        client = VikingClient(None, "http://unused", "0.4.16")
        bodies = []

        async def request(method, path, key, body=None, timeout=30, peer=None):
            if path == "/health":
                return {"version": version, "role": "user", "account_id": "t", "user_id": "a"}
            bodies.append(body)
            return {}

        client.request = request
        await client.create_session("key", "session", Policy())
        await client.commit("key", "session", keep=2)
        return bodies

    new = await bodies_for("0.5.4")
    assert "memory_policy" not in new[0]
    assert new[1] == {"keep_recent_count": 2, "enable_working_memory": False}
    monkeypatch.setitem(client_module.FEATURES, "working_memory_opt_in", "0.6.0")
    old = await bodies_for("0.5.4")
    assert old[0]["memory_policy"] == {"working_memory": {"enabled": False}}
    assert old[1] == {"keep_recent_count": 2}
    # A version that cannot be read stops both calls before they send a body: the gateway
    # never guesses which body a server it cannot identify expects.
    monkeypatch.undo()
    client = VikingClient(None, "http://unused", "0.4.16")
    fake_health(client, {"key": "unknown"})
    for call in (
        client.create_session("key", "session", Policy()),
        client.commit("key", "session", keep=2),
    ):
        with pytest.raises(VikingError, match="openviking_version_mismatch"):
            await call
