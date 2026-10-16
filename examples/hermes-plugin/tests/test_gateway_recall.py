"""Mock gateway turns through the external loader, Hermes manager and HTTP client."""

import json
import threading
from contextlib import contextmanager
from contextvars import ContextVar
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
            result = {"pending_tokens": len(self.pending)}
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


def pause_first_worker(module, monkeypatch):
    entered, release = threading.Event(), threading.Event()
    spawn = module.spawn_context_thread
    count = 0

    def delayed(body, *, name):
        nonlocal count
        if name == "openviking-sync":
            count += 1
            if count == 1:
                def paused():
                    entered.set()
                    assert release.wait(10)
                    body()

                return spawn(paused, name=name)
        return spawn(body, name=name)

    monkeypatch.setattr(module, "spawn_context_thread", delayed)
    return entered, release


def user_texts(messages):
    return [m["parts"][0]["text"] for m in messages if m["role"] == "user"]


@pytest.mark.parametrize("boundary", ["end", "switch", "threshold"])
def test_delayed_worker_preserves_order_context_and_commit(
    external_provider, monkeypatch, boundary
):
    home, provider, module, manager, backend = initialize(external_provider, monkeypatch)
    entered, release = pause_first_worker(module, monkeypatch)
    uploaded = threading.Event()
    context = ContextVar("upload-author", default="missing")
    seen = []
    run = module._TurnUpload.run

    def record(upload):
        seen.append(context.get())
        result = run(upload)
        uploaded.set()
        return result

    monkeypatch.setattr(module._TurnUpload, "run", record)
    if boundary == "threshold":
        monkeypatch.setattr(provider, "_setting", lambda *_args, **_kwargs: 0)
    try:
        with profile_scope(home):
            for number, author in enumerate(("alice", "bob", "alice"), 1):
                token = context.set(f"{author}-{number}")
                try:
                    manager.sync_all(
                        f"Turn {number}", "Reply", session_id="shared-group",
                        turn_author={"id": author},
                    )
                    assert manager.flush_pending(timeout=5)
                finally:
                    context.reset(token)
                if number == 1:
                    assert entered.wait(5)
            # No later upload may overtake a worker paused before its write lock.
            assert not uploaded.wait(0.5)
            assert not provider._drain_writers("shared-group", timeout=0.01)
            if boundary == "switch":
                provider.on_session_switch("next-session")
                assert provider._session_id == "next-session"
            elif boundary == "end":
                monkeypatch.setattr(module, "_SESSION_DRAIN_TIMEOUT", 0.01)
                provider.on_session_end([])
            assert backend.archived == []
            release.set()
            assert provider._drain_writers("shared-group", timeout=10)
            if boundary == "end":
                provider.on_session_end([])
            assert provider._drain_finalizers(timeout=10)
        assert user_texts(backend.archived) == ["Turn 1", "Turn 2", "Turn 3"]
        assert seen == ["alice-1", "bob-2", "alice-3"]
        assert [m["peer_id"] for m in backend.archived if m["role"] == "user"] == [
            "telegram.alice", "telegram.bob", "telegram.alice",
        ]
        assert backend.pending == []
        assert provider._inflight_writers == {}
        assert provider._upload_tails == {}
        assert not provider._state_path("pending", "shared-group").exists()
    finally:
        release.set()
        manager.shutdown_all()


def test_upload_order_does_not_wait_for_previous_metadata(external_provider, monkeypatch):
    home, provider, _, manager, backend = initialize(external_provider, monkeypatch)
    checking, release, second_written = (threading.Event() for _ in range(3))
    check = provider._maybe_commit_live_session
    calls = 0

    def delay_metadata(*args):
        nonlocal calls
        calls += 1
        if calls == 1:
            checking.set()
            assert release.wait(10)
        else:
            second_written.set()
        check(*args)

    monkeypatch.setattr(provider, "_maybe_commit_live_session", delay_metadata)
    try:
        with profile_scope(home):
            provider.sync_turn("First", "Reply")
            assert checking.wait(5)
            provider.sync_turn("Second", "Reply")
            assert second_written.wait(5)
            assert user_texts(backend.pending) == ["First", "Second"]
            assert not provider._drain_writers("shared-group", timeout=0.01)
            release.set()
            assert provider._drain_writers("shared-group", timeout=5)
    finally:
        release.set()
        manager.shutdown_all()


def test_failed_worker_start_does_not_break_upload_order(external_provider, monkeypatch):
    home, provider, module, manager, backend = initialize(external_provider, monkeypatch)
    entered, release = pause_first_worker(module, monkeypatch)
    spawn = module.spawn_context_thread
    calls = 0

    def fail_second_start(body, *, name):
        nonlocal calls
        thread = spawn(body, name=name)
        if name == "openviking-sync":
            calls += 1
            if calls == 2:
                def fail():
                    raise RuntimeError("No thread available")

                thread.start = fail
        return thread

    monkeypatch.setattr(module, "spawn_context_thread", fail_second_start)
    try:
        with profile_scope(home):
            provider.sync_turn("First", "Reply")
            assert entered.wait(5)
            provider.sync_turn("Cannot start", "Reply")
            provider.sync_turn("Third", "Reply")
            assert not provider._drain_writers("shared-group", timeout=0.1)
            assert backend.pending == []
            release.set()
            assert provider._drain_writers("shared-group", timeout=5)
            assert user_texts(backend.pending) == ["First", "Third"]
            provider.sync_turn("Fourth", "Reply")
            assert provider._drain_writers("shared-group", timeout=5)
            assert user_texts(backend.pending) == ["First", "Third", "Fourth"]
            assert provider._upload_tails == {}
            assert provider._inflight_writers == {}
    finally:
        release.set()
        manager.shutdown_all()


@pytest.mark.parametrize("fail_all", [False, True])
def test_failed_upload_releases_next_turn(external_provider, monkeypatch, fail_all):
    home, provider, module, manager, backend = initialize(external_provider, monkeypatch)
    entered, release = pause_first_worker(module, monkeypatch)
    run = module._TurnUpload.run

    def fail_first(upload):
        if upload.user_content == "First":
            backend.batch_failures = 4
            if fail_all:
                # Exhaust the individual-message fallback too, then allow the next turn.
                def offline(*_args):
                    raise OSError("offline")

                monkeypatch.setattr(upload.client, "post", offline)
        result = run(upload)
        backend.batch_failures = 0
        return result

    monkeypatch.setattr(module._TurnUpload, "run", fail_first)
    try:
        with profile_scope(home):
            for text in ("First", "Second"):
                provider.sync_turn(text, "Reply", messages=[{"role": "user", "content": text}])
                if text == "First":
                    assert entered.wait(5)
            release.set()
            assert provider._drain_writers("shared-group", timeout=5)
            assert user_texts(backend.pending) == (["Second"] if fail_all else ["First", "Second"])
            assert provider._upload_tails == {}
    finally:
        release.set()
        manager.shutdown_all()


def test_other_session_upload_is_not_blocked_by_paused_worker(external_provider, monkeypatch):
    home, provider, module, manager, backend = initialize(external_provider, monkeypatch)
    entered, release = pause_first_worker(module, monkeypatch)
    try:
        with profile_scope(home):
            provider.sync_turn("First session", "Reply", session_id="first")
            assert entered.wait(5)
            provider.sync_turn("Second session", "Reply", session_id="second")
            assert provider._drain_writers("second", timeout=5)
            assert user_texts(backend.pending) == ["Second session"]
            release.set()
            manager.shutdown_all()
            assert user_texts(backend.pending) == ["Second session", "First session"]
            assert provider._inflight_writers == {}
            assert provider._upload_tails == {}
    finally:
        release.set()
        manager.shutdown_all()
