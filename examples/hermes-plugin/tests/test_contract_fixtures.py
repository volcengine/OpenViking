"""The Python wire helpers against the shared cross-language contract fixtures.

The fixtures live in ``examples/memory-plugin-shared/testing/contract`` and pin
the behaviour the JavaScript plugins share (see the README there). Inputs use
neutral snake_case names that map one to one onto ``build_openviking_headers``
keyword arguments; a retry case maps ``result.status``/``result.error`` onto
``is_retryable_failure``.
"""

import json
import re
from pathlib import Path

import pytest

_PLUGIN = Path(__file__).resolve().parents[1]
_CONTRACT = _PLUGIN.parent / "memory-plugin-shared" / "testing" / "contract"

pytestmark = pytest.mark.skipif(
    not _CONTRACT.is_dir(), reason="shared contract fixtures are not next to this plugin checkout"
)


def _fixture(name):
    return json.loads((_CONTRACT / name).read_text(encoding="utf-8"))


def _cases(name, key="cases"):
    if not _CONTRACT.is_dir():
        return []
    return _fixture(name)[key]


@pytest.fixture
def http(external_provider, core_module):
    _, _, module, _ = external_provider("contract")
    return core_module(module, "http")


def _substitute(value, placeholders):
    for name, replacement in placeholders.items():
        value = value.replace("{" + name + "}", replacement)
    return value


@pytest.mark.parametrize("case", _cases("headers.json"), ids=lambda case: case["name"])
def test_headers_match_contract(http, case):
    placeholders = case.get("placeholders", {})
    inputs = {key: _substitute(value, placeholders) if isinstance(value, str) else value
              for key, value in case["input"].items()}
    expected = {key: _substitute(value, placeholders) for key, value in case["expected"].items()}

    headers = http.build_openviking_headers(**inputs)

    assert headers == expected
    for name in _fixture("headers.json")["never_present"]:
        assert name not in headers


@pytest.mark.parametrize("case", _cases("headers.json", "user_agent")["cases"] if _CONTRACT.is_dir() else [],
                         ids=lambda case: case["expected"])
def test_user_agent_matches_contract(http, case):
    assert http.build_user_agent(case["plugin"], case["version"]) == case["expected"]


def test_client_headers_use_bearer_and_plugin_version(http):
    version = re.search(r"^version:\s*(\S+)", (_PLUGIN / "plugin.yaml").read_text(encoding="utf-8"), re.M).group(1)
    client = http._VikingClient("http://127.0.0.1:1", api_key="secret", account="acct-a", user="user-a",
                                agent="peer-1", transport=object())

    assert client._headers() == {
        "Content-Type": "application/json",
        "Authorization": "Bearer secret",
        "X-OpenViking-Actor-Peer": "peer-1",
        "User-Agent": f"openviking-memory-hermes/{version}",
    }
    # The trusted-identity retry adds the account and user headers to a keyed request.
    retry = client._headers(include_tenant=True)
    assert (retry["X-OpenViking-Account"], retry["X-OpenViking-User"]) == ("acct-a", "user-a")
    assert "X-API-Key" not in retry
    # Without a key the server is trusted: identity headers go out, no Authorization.
    keyless = http._VikingClient("http://127.0.0.1:1", account="acct-a", user="user-a",
                                 agent="peer-1", transport=object())._headers()
    assert "Authorization" not in keyless
    assert (keyless["X-OpenViking-Account"], keyless["X-OpenViking-User"]) == ("acct-a", "user-a")


@pytest.mark.parametrize("case", _cases("retryable.json"), ids=lambda case: case["name"])
def test_retry_classification_matches_contract(http, case):
    result = case["result"]
    retryable = False if result.get("ok") else http.is_retryable_failure(result.get("status"), result.get("error"))
    assert retryable is case["retryable"]


def test_batch_fallback_statuses_are_not_retryable(http):
    batch = _fixture("retryable.json")["batch_endpoint"]
    for status in batch["fallback_statuses"]:
        assert http.is_retryable_failure(status) is False


@pytest.mark.parametrize("identity", [
    ("http://127.0.0.1:1933/", "secret", "acct-a", "user-a", "peer-1"),
    ("http://ov.example", "", "default", "default", ""),
    ("http://127.0.0.1:1933", "kéy", "acct", "用户", "peer"),
], ids=["keyed", "keyless", "non-ascii"])
def test_connection_snapshot_reproduces_both_fingerprints(external_provider, core_module, identity):
    import threading
    from types import SimpleNamespace

    _, _, module, _ = external_provider("snapshot")
    http, connection, mirror = (core_module(module, name) for name in ("http", "connection", "mirror"))
    endpoint, api_key, account, user, agent = identity
    client = http._VikingClient(endpoint, api_key=api_key, account=account, user=user, agent=agent,
                                transport=object())
    holder = SimpleNamespace(_session_state_lock=threading.Lock(), _commit_scope=None, _client=client,
                             _conn_snapshot=None, _turn_count=0)

    snapshot = connection.ConnectionSnapshot.from_client(client)

    assert snapshot.as_tuple() == client._conn_snapshot
    assert snapshot.peer == client._agent
    assert snapshot.commit_key() == connection.ConnectionMixin._capture_commit_scope(holder).connection_key
    assert snapshot.mirror_connection() == mirror._connection_fingerprint(client)
    with pytest.raises(AttributeError):
        snapshot.url = "http://other"
