"""Recall routing table: which request each recall_scope / sender / agent context sends."""

import importlib.util
import json
from pathlib import Path

import httpx
import pytest

TESTS_DIR = Path(__file__).resolve().parent
CONTRACT = json.loads(
    (TESTS_DIR.parents[1] / "memory-plugin-shared/testing/contract/recall-request.json").read_text(
        encoding="utf-8"
    )
)
_spec = importlib.util.spec_from_file_location(
    "_openviking_routing_harness", TESTS_DIR / "test_gateway_recall.py"
)
_gateway = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_gateway)
initialize, profile_scope = _gateway.initialize, _gateway.profile_scope

QUERY = "Recall preferences"
# recall_limit defaults to 6: one slot per type.
DEFAULT_QUOTAS = {
    "events": 1, "entities": 1, "preferences": 1, "experiences": 1, "resources": 1, "skills": 1,
}


def run_prefetch(external_provider, inject_deps, *, scope, author="alice", compress="off",
                 settings=None, agent_context=None, backend_setup=None):
    identity = {"agent_context": agent_context} if agent_context else {}
    home, provider, _, manager, backend = initialize(
        external_provider, inject_deps, scope=scope, compress=compress, **identity
    )
    try:
        with profile_scope(home):
            if settings:
                provider.save_config(settings, str(home))
            if backend_setup:
                backend_setup(backend)
            manager.on_turn_start(1, QUERY, author_id=author)
            result = provider.prefetch(QUERY, session_id="shared-group")
        searches = [(r, p) for r, p in backend.searches if p.get("query") == QUERY]
        return result, searches
    finally:
        manager.shutdown_all()


def context_requests(searches):
    return [(r, p) for r, p in searches if p.get("mode") == "context"]


def test_shared_uses_context_mode_for_all_peers(external_provider, inject_deps):
    result, searches = run_prefetch(external_provider, inject_deps, scope="shared")
    assert len(searches) == 1
    request, payload = searches[0]
    assert request.url.path == "/api/v1/search/search"
    assert payload == {
        "query": QUERY,
        "mode": "context",
        "purpose": "coding",
        "score_threshold": 0.15,
        "max_tokens": 1000,
        "context_type": ["memory", "resource"],
        "quotas": DEFAULT_QUOTAS,
        "session_id": payload["session_id"],
        "peer_scope": "all",
    }
    assert payload["session_id"]
    assert "target_uri" not in payload
    # The injected block is the server's rendered text.
    assert result == "## OpenViking Context\ncommon alice bob assistant resource"


def test_peer_with_sender_uses_actor_context_mode(external_provider, inject_deps):
    result, searches = run_prefetch(external_provider, inject_deps, scope="peer", author="bob")
    assert len(searches) == 1
    request, payload = searches[0]
    assert request.headers["X-OpenViking-Actor-Peer"] == "telegram.bob"
    assert payload["mode"] == "context" and payload["peer_scope"] == "actor"
    assert payload["quotas"] == DEFAULT_QUOTAS
    assert "rewrite" not in payload and "target_uri" not in payload
    assert result == "## OpenViking Context\ncommon bob resource"


def test_peer_with_sender_falls_back_to_rooted_list_when_scope_unconfirmed(
    external_provider, inject_deps
):
    def unconfirmed(backend):
        backend.unconfirmed_context = True

    result, searches = run_prefetch(
        external_provider, inject_deps, scope="peer", author="bob", backend_setup=unconfirmed
    )
    assert "WRONG" not in result and "bob" in result and "alice" not in result
    assert [p.get("mode") for _, p in searches] == ["context", None]
    assert searches[1][1]["target_uri"] == [
        "viking://user/tenant/memories",
        "viking://user/tenant/peers/telegram.bob/memories",
        "viking://user/tenant/resources",
        "viking://resources",
        "viking://user/tenant/peers/telegram.bob/resources",
    ]


@pytest.mark.parametrize("compress", ["off", "server"])
def test_peer_without_sender_uses_common_roots_only(external_provider, inject_deps, compress):
    result, searches = run_prefetch(
        external_provider, inject_deps, scope="peer", author=None, compress=compress
    )
    assert not context_requests(searches)
    assert searches[-1][1]["target_uri"] == [
        "viking://user/tenant/memories", "viking://user/tenant/resources", "viking://resources",
    ]
    assert "common" in result and "alice" not in result and "bob" not in result


def test_unset_scope_keeps_list_request_when_compress_off(external_provider, inject_deps):
    _result, searches = run_prefetch(external_provider, inject_deps, scope=None)
    assert not context_requests(searches)
    payload = searches[0][1]
    assert set(payload) == {"query", "limit", "score_threshold", "context_type", "session_id"}


def test_unset_scope_keeps_compressed_context_request(external_provider, inject_deps):
    _result, searches = run_prefetch(external_provider, inject_deps, scope=None, compress="server")
    payload = context_requests(searches)[0][1]
    assert payload["rewrite"] is True and payload["session_id"]
    assert "quotas" not in payload and "peer_scope" not in payload


@pytest.mark.parametrize("compress", ["off", "server"])
def test_non_primary_keeps_todays_shape_without_session(external_provider, inject_deps, compress):
    _result, searches = run_prefetch(
        external_provider, inject_deps, scope="shared", compress=compress, agent_context="subagent"
    )
    contexts = context_requests(searches)
    if compress == "off":
        assert not contexts
    else:
        payload = contexts[0][1]
        assert payload["rewrite"] is True and payload["peer_scope"] == "all"
        assert "session_id" not in payload and "quotas" not in payload


@pytest.mark.parametrize("response", [
    httpx.Response(400, json={"error": {"message": "unknown field"}}),
    httpx.Response(422, json={"detail": "mode"}),
    httpx.Response(200, json={"status": "ok", "result": {"memories": []}}),
])
@pytest.mark.parametrize("scope,author", [("shared", "alice"), ("peer", "bob")])
def test_context_unavailable_falls_back_to_list(
    external_provider, inject_deps, response, scope, author
):
    def unavailable(backend):
        backend.context_response = response

    result, searches = run_prefetch(
        external_provider, inject_deps, scope=scope, author=author, backend_setup=unavailable
    )
    assert searches[0][1]["mode"] == "context"
    assert all(p.get("mode") != "context" for _, p in searches[1:]) and len(searches) > 1
    assert "common" in result
    if scope == "peer":
        assert "bob" in result and "alice" not in result
        assert searches[-1][1]["target_uri"]


@pytest.mark.parametrize("scope,author", [("shared", "alice"), ("peer", "bob")])
def test_context_mode_setting_reverts_to_list(external_provider, inject_deps, scope, author):
    _result, searches = run_prefetch(
        external_provider, inject_deps, scope=scope, author=author,
        settings={"recall_context_mode": False},
    )
    assert searches and not context_requests(searches)


_CONFIGURABLE = {"recall_limit", "score_threshold", "peer_scope"}
_FIXTURE_CASES = [
    case for case in CONTRACT["cases"]
    if case["input"] and set(case["input"]) <= _CONFIGURABLE
    and case["input"].get("peer_scope", "actor") == "actor"
]


@pytest.mark.parametrize("case", _FIXTURE_CASES, ids=[c["name"] for c in _FIXTURE_CASES])
def test_context_request_matches_shared_contract(external_provider, inject_deps, case):
    """Common fields the case configures; Hermes owns max_tokens and its score default."""
    settings = {}
    if "recall_limit" in case["input"]:
        settings["recall_limit"] = case["input"]["recall_limit"]
    if "score_threshold" in case["input"]:
        settings["recall_score_threshold"] = case["input"]["score_threshold"]
    _result, searches = run_prefetch(
        external_provider, inject_deps, scope="peer", author="bob", settings=settings
    )
    payload = context_requests(searches)[0][1]
    expected = case["expected"]
    compared = {"mode", "purpose"} | set(case["input"]) - {"recall_limit"}
    if "recall_limit" in case["input"]:
        compared.add("quotas")
    for field in compared:
        assert field in CONTRACT["common_fields"]
        assert payload[field] == expected[field], field
