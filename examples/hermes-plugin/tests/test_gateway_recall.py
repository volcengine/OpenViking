"""Mock gateway turns through the external loader, Hermes manager and HTTP client."""

import json
import threading
from contextlib import contextmanager
from types import SimpleNamespace

import httpx
import pytest
import yaml


@contextmanager
def profile_scope(home):
    from agent.secret_scope import build_profile_secret_scope, reset_secret_scope, set_secret_scope
    from hermes_constants import reset_hermes_home_override, set_hermes_home_override

    token = set_hermes_home_override(home)
    secrets = set_secret_scope(build_profile_secret_scope(home))
    try:
        yield
    finally:
        reset_secret_scope(secrets)
        reset_hermes_home_override(token)


class GatewayBackend:
    """In-memory HTTP service: capture/archive plus deterministic indexed fixtures."""

    def __init__(self):
        self.pending = []
        self.archived = []
        self.searches = []
        self.requests = []
        self.batch_failures = 0
        self.reject_context = False
        self.reject_session_search = False
        self.unconfirmed_context = False
        self.reject_identity = False
        self.upload_started = None
        self.release_upload = None

    def __call__(self, request):
        path = request.url.path
        payload = json.loads(request.content) if request.content else {}
        self.requests.append((request, payload))
        result = {}
        if path == "/health":
            return httpx.Response(200, json={"status": "ok", "healthy": True, "version": "0.4.21"})
        if path == "/api/v1/system/status":
            if self.reject_identity:
                return httpx.Response(503)
            result = {"user": "tenant"}
        elif path.endswith("/messages/batch") or path.endswith("/messages"):
            if self.upload_started:
                self.upload_started.set()
                assert self.release_upload.wait(10)
            if path.endswith("/batch") and self.batch_failures:
                self.batch_failures -= 1
                return httpx.Response(500, json={"error": {"message": "temporary capture failure"}})
            self.pending.extend(payload.get("messages", [payload]))
        elif path.endswith("/commit"):
            assert payload == {"keep_recent_count": 0}
            self.archived.extend(self.pending)
            self.pending.clear()
            result = {"status": "accepted"}
        elif path.startswith("/api/v1/sessions/"):
            result = {"pending_tokens": 0}
        elif path.startswith("/api/v1/search/"):
            self.searches.append((request, payload))
            if self.reject_session_search and payload.get("session_id"):
                return httpx.Response(503)
            if payload.get("mode") == "context" and self.reject_context:
                return httpx.Response(422)
            actor = request.headers.get("X-OpenViking-Actor-Peer", "")
            roots = payload.get("target_uri")
            if roots is None:
                all_peers = not actor or (
                    payload.get("mode") == "context" and payload.get("peer_scope", "all") == "all"
                )
                roots = (
                    ["viking://user/tenant", "viking://resources"]
                    if all_peers
                    else [
                        "viking://user/tenant/memories",
                        f"viking://user/tenant/peers/{actor}",
                        "viking://resources",
                    ]
                )
            docs = [("common", "viking://user/tenant/memories/preferences/common.md")]
            docs += [
                (name, f"viking://user/tenant/peers/telegram.{name}/memories/preferences/fact.md")
                for name in ("alice", "bob", "assistant")
            ]
            docs += [("resource", "viking://resources/manual/readme.md")]
            allowed = [
                (name, uri)
                for name, uri in docs
                if any(uri.startswith(root.rstrip("/") + "/") for root in roots)
            ]
            if "resource" not in payload.get("context_type", []):
                allowed = [(name, uri) for name, uri in allowed if name != "resource"]
            if payload.get("mode") == "context":
                result = {
                    "rendered": " ".join(name for name, _ in allowed),
                    "entries": [],
                    "stats": {"peer_scope": payload.get("peer_scope", "all")},
                }
                if self.unconfirmed_context:
                    result = {"rendered": "WRONG_BOB_CONTEXT", "entries": [], "stats": {}}
            else:
                result = {
                    "memories": [
                        {"uri": uri, "score": 0.9, "abstract": name, "level": 2}
                        for name, uri in allowed
                    ]
                }
        elif path == "/api/v1/fs/ls":
            result = []
        elif path.startswith("/api/v1/content/"):
            return httpx.Response(404)
        return httpx.Response(200, json={"status": "ok", "result": result})


def initialize(
    external_provider, monkeypatch, *, name="gateway", scope="peer", compress="off", **identity
):
    from agent.memory_manager import MemoryManager

    home, provider, module, _ = external_provider(name)
    config = {
        "endpoint": f"http://{name}.test",
        "use_ovcli_config": False,
        "agent": "telegram.assistant",
        "api_key": "mock-key",
        "recall_scope": scope,
        "recall_compress": compress,
        "recall_prefer_abstract": True,
        "recall_resources": True,
        "recall_timeout_seconds": 10,
        "recall_request_timeout_seconds": 5,
    }
    (home / "config.yaml").write_text(
        yaml.safe_dump({"memory": {"provider": "openviking", "openviking": config}})
    )
    backend = GatewayBackend()
    client = httpx.Client(transport=httpx.MockTransport(backend))
    monkeypatch.setattr(module, "_get_httpx", lambda: client)
    manager = MemoryManager(external_prefetch_timeout=10)
    manager.add_provider(provider)
    with profile_scope(home):
        manager.initialize_all(
            session_id="shared-group",
            hermes_home=str(home),
            platform="telegram",
            user_id="alice",
            **identity,
        )
    return home, provider, module, manager, backend


@pytest.mark.parametrize("compress", ["off", "server"])
@pytest.mark.parametrize("scope", [None, "shared", "peer"])
def test_gateway_capture_commit_and_sender_scoped_recall(
    external_provider, monkeypatch, scope, compress
):
    from agent.turn_context import _memory_turn_start_and_prefetch

    home, provider, _, manager, backend = initialize(
        external_provider, monkeypatch, scope=scope, compress=compress
    )
    agent = SimpleNamespace(
        _memory_manager=manager,
        _user_turn_count=0,
        session_id="shared-group",
        _emit_status=lambda _: None,
    )
    try:
        with profile_scope(home):
            for index, sender in enumerate(("alice", "bob", "alice"), start=1):
                author = {"id": sender, "name": sender, "is_bot": False}
                query = f"Remember my {sender} drink preference"
                agent._user_turn_count = index
                recalled = _memory_turn_start_and_prefetch(agent, query, author)
                assert "common" in recalled
                if scope == "peer":
                    assert sender in recalled
                    assert ("bob" if sender == "alice" else "alice") not in recalled
                    assert "assistant" not in recalled
                elif scope == "shared":
                    assert "alice" in recalled and "bob" in recalled
                elif compress == "off":
                    assert (
                        "assistant" in recalled
                        and "alice" not in recalled
                        and "bob" not in recalled
                    )
                manager.sync_all(
                    query,
                    "Acknowledged",
                    session_id="shared-group",
                    turn_author=author,
                    messages=[
                        {"role": "user", "content": query},
                        {"role": "assistant", "content": "Acknowledged"},
                    ],
                )
            assert manager.flush_pending(timeout=10)
            assert provider._drain_writers("shared-group", timeout=10)
            provider.on_session_end([])
            assert provider._drain_finalizers(timeout=10)
        assert [m["peer_id"] for m in backend.archived if m["role"] == "user"] == [
            "telegram.alice",
            "telegram.bob",
            "telegram.alice",
        ]
        assert all(
            m["peer_id"] == "telegram.assistant"
            for m in backend.archived
            if m["role"] == "assistant"
        )
        assert backend.pending == []
        assert not provider._state_path("pending", "shared-group").exists()
        # Sender recall never changes the configured identity used for capture/tools.
        with profile_scope(home):
            provider.handle_tool_call("viking_search", {"query": "preference"})
        assert backend.searches[-1][0].headers["X-OpenViking-Actor-Peer"] == "telegram.assistant"
    finally:
        manager.shutdown_all()


@pytest.mark.parametrize("batch_failures,structured", [(1, True), (4, True), (0, False)])
def test_delayed_capture_keeps_author_through_fallback(
    external_provider, monkeypatch, batch_failures, structured
):
    home, provider, _, manager, backend = initialize(external_provider, monkeypatch)
    backend.batch_failures = batch_failures
    backend.upload_started, backend.release_upload = threading.Event(), threading.Event()
    try:
        with profile_scope(home):
            manager.on_turn_start(1, "Alice fact", author_id="alice")
            provider.sync_turn(
                "Alice fact",
                "Reply",
                session_id="shared-group",
                turn_author={"id": "alice"},
                messages=[{"role": "user", "content": "Alice fact"}] if structured else None,
            )
            assert backend.upload_started.wait(5)
            manager.on_turn_start(2, "Bob fact", author_id="bob")
            backend.release_upload.set()
            assert provider._drain_writers("shared-group", timeout=10)
        assert backend.pending
        assert {m["peer_id"] for m in backend.pending if m["role"] == "user"} == {"telegram.alice"}
    finally:
        backend.release_upload.set()
        manager.shutdown_all()


@pytest.mark.parametrize("author_id", [None, "", "bob"])
def test_missing_author_and_context_fallback_keep_scope(external_provider, monkeypatch, author_id):
    home, provider, _, manager, backend = initialize(
        external_provider, monkeypatch, compress="server"
    )
    try:
        with profile_scope(home):
            manager.on_turn_start(1, "Alice fact", author_id="alice")
            manager.on_turn_start(2, "Recall preferences", author_id=author_id)
            for fault in ("reject_context", "unconfirmed_context"):
                setattr(backend, fault, True)
                result = provider.prefetch("Recall preferences", session_id="shared-group")
                assert "common" in result and "resource" in result
                assert "alice" not in result and "assistant" not in result and "WRONG" not in result
                assert ("bob" in result) == bool(author_id)
                assert backend.searches[-1][1]["target_uri"]
                setattr(backend, fault, False)
            backend.reject_identity = True
            backend.searches.clear()
            assert provider.prefetch("Recall preferences", session_id="shared-group") == ""
            assert not backend.searches
    finally:
        manager.shutdown_all()


def test_peer_identity_alt_ids_and_profile_settings(external_provider, monkeypatch):
    home_a, provider_a, module_a, manager_a, _ = initialize(
        external_provider, monkeypatch, name="profile-a", user_id_alt="stable-alice"
    )
    home_b, provider_b, _, manager_b, _ = initialize(
        external_provider, monkeypatch, name="profile-b", scope="shared"
    )
    try:
        for home, provider, expected in (
            (home_a, provider_a, "peer"),
            (home_b, provider_b, "shared"),
            (home_a, provider_a, "peer"),
        ):
            with profile_scope(home):
                assert provider._recall_config()["scope"] == expected
        with profile_scope(home_a):
            provider_a.on_turn_start(1, "hello", author_id="alice")
            assert provider_a._current_sender_peer() == "telegram.stable-alice"
            provider_a.on_turn_start(2, "hello", author_id="bob")
            assert provider_a._current_sender_peer() == "telegram.bob"
        assert module_a._gateway_peer_id("telegram", "alice") != module_a._gateway_peer_id(
            "discord", "alice"
        )
        assert module_a._gateway_peer_id("telegram", "a/b") != module_a._gateway_peer_id(
            "telegram", "a?b"
        )
        peer = module_a._gateway_peer_id("telegram", "a/b" * 100)
        assert len(peer) <= 128 and "/" not in peer
        provider_a.save_config({"recall_scope": "shared"}, str(home_a))
        assert (
            yaml.safe_load((home_a / "config.yaml").read_text())["memory"]["openviking"][
                "recall_scope"
            ]
            == "shared"
        )
    finally:
        manager_a.shutdown_all()
        manager_b.shutdown_all()


def test_find_fallback_retains_sender_roots(external_provider, monkeypatch):
    home, provider, _, manager, backend = initialize(external_provider, monkeypatch)
    backend.reject_session_search = True
    try:
        with profile_scope(home):
            manager.on_turn_start(1, "Recall preferences", author_id="bob")
            result = provider.prefetch("Recall preferences", session_id="shared-group")
        assert "bob" in result and "common" in result and "resource" in result
        assert "alice" not in result and "assistant" not in result
        assert backend.searches[-1][0].url.path == "/api/v1/search/find"
        assert backend.searches[-1][1]["target_uri"] == backend.searches[-2][1]["target_uri"]
    finally:
        manager.shutdown_all()


@pytest.mark.parametrize("platform,expected", [("telegram", "telegram.alice"), ("", None)])
def test_older_hooks_and_no_gateway_sender(external_provider, monkeypatch, platform, expected):
    home, provider, _, manager, backend = initialize(external_provider, monkeypatch)
    try:
        with profile_scope(home):
            provider._gateway_platform = platform
            provider._user_id = provider._sender_peer("alice")
            # Older Hermes does not pass per-turn author metadata.
            provider.on_turn_start(1, "Remember this")
            provider.sync_turn("Remember this", "OK", session_id="shared-group")
            assert provider._drain_writers("shared-group", timeout=10)
            assert backend.pending[0].get("peer_id") == expected
            backend.pending.clear()
            # An explicit missing author must clear the initialized sender.
            provider.on_turn_start(2, "Remember another fact", author_id=None)
            provider.sync_turn(
                "Remember another fact", "OK", session_id="shared-group", turn_author={"id": None}
            )
            assert provider._drain_writers("shared-group", timeout=10)
            assert "peer_id" not in backend.pending[0]
    finally:
        manager.shutdown_all()


def test_recall_scope_default_and_environment_override(external_provider, monkeypatch):
    _, provider, module, _ = external_provider("config")
    schema = {field["key"]: field for field in provider.get_config_schema()}
    assert schema["recall_scope"]["choices"] == ["shared", "peer"]
    assert module.OpenVikingMemoryProvider._setting("recall_scope", {}) is None
    monkeypatch.setenv("OPENVIKING_RECALL_SCOPE", "peer")
    assert provider._setting("recall_scope", {"recall_scope": "shared"}) == "peer"
    monkeypatch.delenv("OPENVIKING_RECALL_SCOPE")
    assert provider._setting("recall_scope", {"recall_scope": "invalid"}) is None
