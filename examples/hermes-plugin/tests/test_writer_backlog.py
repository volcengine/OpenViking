"""Failed turn uploads are kept and resent in order, through the public hooks only."""

import logging
import time

import pytest


class Clock:
    """Monotonic clock that tests move forward past the upload cooldown."""

    def __init__(self):
        self.offset = 0.0

    def __call__(self):
        return time.monotonic() + self.offset

    def advance(self, seconds):
        self.offset += seconds


class FakeServer:
    """One OpenViking server as seen by one identity. ``fail`` is None (up), "down" or an HTTP status."""

    def __init__(self, http_error):
        self.http_error = http_error
        self.fail = None
        self.pending = {}
        self.commits = []
        self.requests = []
        self.gets = []
        # pending_tokens the write responses report; None leaves the field out.
        self.reported_pending_tokens = None

    def _check(self):
        if self.fail == "down":
            raise ConnectionError("connection refused")
        if self.fail is not None:
            raise self.http_error(f"HTTP {self.fail}", self.fail)

    def post(self, path, payload=None, **_kwargs):
        self.requests.append(path)
        self._check()
        sid = path.split("/")[4]
        if path.endswith("/commit"):
            self.commits.append((sid, self.pending.pop(sid, [])))
            return {"result": {}}
        self.pending.setdefault(sid, []).extend(payload["messages"] if path.endswith("/batch") else [payload])
        if self.reported_pending_tokens is None:
            return {"result": {}}
        return {"result": {"pending_tokens": self.reported_pending_tokens}}

    def get(self, path, **_kwargs):
        self.gets.append(path)
        self._check()
        return {"result": {"pending_tokens": 10 if self.pending.get(path.split("/")[4]) else 0}}

    def texts(self, sid):
        return [m["parts"][0]["text"] for m in self.pending.get(sid, [])]

    def committed(self, sid):
        return [[m["parts"][0]["text"] for m in messages] for s, messages in self.commits if s == sid]


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def writer(external_provider, monkeypatch, inject_deps, core_module, clock):
    home, provider, module, _ = external_provider("writer-backlog")
    monkeypatch.setenv("HERMES_HOME", str(home))
    http_error = core_module(module, "http")._OpenVikingHTTPError
    servers = {name: FakeServer(http_error) for name in ("alice", "bob")}

    class Client:
        def __init__(self, endpoint, api_key="", *, account="", user="", agent=""):
            self._conn_snapshot = (endpoint, api_key, account, user, agent)
            self.get = servers[user].get
            self.post = servers[user].post

    inject_deps(module, provider, client=Client, health=lambda *_: ("healthy", ""), monotonic=clock)

    def connect(user):
        monkeypatch.setenv("OPENVIKING_USER", user)
        monkeypatch.setenv("OPENVIKING_ENDPOINT", "http://127.0.0.1:19531")
        monkeypatch.setenv("OPENVIKING_API_KEY", "private-test-key")
        if not provider._env_refresh_enabled:
            provider.initialize("sid-1", hermes_home=str(home))
        else:
            assert provider._ensure_client() is not None

    connect("alice")
    yield provider, servers, connect, core_module(module, "session_writer")
    provider.shutdown()


def turn(provider, text, sid="sid-1"):
    provider.sync_turn(f"u{text}", f"a{text}", session_id=sid)
    assert provider._drain_writers(f"hermes-{sid}", timeout=5)
    assert provider._drain_finalizers(timeout=5)


def recover(server, clock):
    """The server is back and the upload cooldown after its failure has passed."""
    server.fail = None
    clock.advance(60)


def pair(*turns):
    return [t for n in turns for t in (f"u{n}", f"a{n}")]


def test_server_down_then_back_keeps_every_turn_in_order(writer, clock):
    provider, servers, _, _ = writer
    alice = servers["alice"]
    alice.fail = "down"
    for n in (1, 2, 3):
        turn(provider, n)
    assert alice.texts("hermes-sid-1") == []
    recover(alice, clock)
    turn(provider, 4)
    assert alice.texts("hermes-sid-1") == pair(1, 2, 3, 4)
    provider.on_session_end([])
    assert alice.committed("hermes-sid-1") == [pair(1, 2, 3, 4)]


def test_retryable_status_is_backlogged_and_resent(writer, clock):
    provider, servers, _, _ = writer
    alice = servers["alice"]
    alice.fail = 503
    turn(provider, 1)
    recover(alice, clock)
    turn(provider, 2)
    assert alice.texts("hermes-sid-1") == pair(1, 2)


def test_commit_waits_for_backlog(writer):
    provider, servers, _, _ = writer
    alice = servers["alice"]
    turn(provider, 1)
    alice.fail = "down"
    turn(provider, 2)
    marker = provider._state_path("pending", "hermes-sid-1")
    provider.on_session_end([])
    assert alice.commits == []
    assert marker.exists()
    alice.fail = None
    alice.requests.clear()
    provider.on_session_end([])
    # The backlog goes out before the commit, and the commit covers it.
    assert alice.requests == ["/api/v1/sessions/hermes-sid-1/messages/batch", "/api/v1/sessions/hermes-sid-1/commit"]
    assert alice.committed("hermes-sid-1") == [pair(1, 2)]
    assert not marker.exists()


def test_session_switch_with_backlog_commits_old_session_after_resend(writer, clock):
    provider, servers, _, _ = writer
    alice = servers["alice"]
    alice.fail = "down"
    turn(provider, 1, sid="sid-1")
    provider.on_session_switch("sid-2")
    assert provider._drain_finalizers(timeout=5)
    assert alice.commits == []
    assert provider._state_path("pending", "hermes-sid-1").exists()
    recover(alice, clock)
    turn(provider, 2, sid="sid-2")
    assert alice.committed("hermes-sid-1") == [pair(1)]
    assert alice.texts("hermes-sid-2") == pair(2)
    assert not provider._state_path("pending", "hermes-sid-1").exists()


def test_backlog_keeps_its_identity_across_reload(writer):
    provider, servers, connect, _ = writer
    alice, bob = servers["alice"], servers["bob"]
    alice.fail = "down"
    turn(provider, 1)
    connect("bob")
    alice.fail = None
    turn(provider, 2)
    # Alice's retryable backlog is sent with Alice's client, never with Bob's.
    assert alice.texts("hermes-sid-1") == pair(1)
    assert bob.texts("hermes-sid-1") == pair(2)


def test_auth_failure_is_not_retried_and_resent_within_the_same_connection(writer):
    provider, servers, _, _ = writer
    alice = servers["alice"]
    alice.fail = 401
    turn(provider, 1)
    # No immediate retry and no per-message fallback.
    assert alice.requests == ["/api/v1/sessions/hermes-sid-1/messages/batch"]
    alice.fail = None
    turn(provider, 2)
    assert alice.texts("hermes-sid-1") == pair(1, 2)


def test_auth_failure_backlog_is_dropped_when_credentials_change(writer, caplog):
    provider, servers, connect, _ = writer
    alice, bob = servers["alice"], servers["bob"]
    alice.fail = 403
    turn(provider, 1)
    connect("bob")
    alice.fail = None
    with caplog.at_level(logging.WARNING):
        turn(provider, 2)
        provider.on_session_end([])
    assert alice.texts("hermes-sid-1") == []
    assert bob.committed("hermes-sid-1") == [pair(2)]
    assert any("rejected with 401/403" in r.getMessage() for r in caplog.records)


def test_other_client_errors_are_dropped_with_a_warning(writer, caplog):
    provider, servers, _, _ = writer
    alice = servers["alice"]
    alice.fail = 400
    with caplog.at_level(logging.WARNING):
        turn(provider, 1)
    alice.fail = None
    turn(provider, 2)
    assert alice.texts("hermes-sid-1") == pair(2)


def test_backlog_bound_drops_oldest_with_warning(writer, monkeypatch, caplog, clock):
    provider, servers, _, session_writer = writer
    alice = servers["alice"]
    monkeypatch.setattr(session_writer, "_BACKLOG_MAX_MESSAGES", 4)
    alice.fail = "down"
    with caplog.at_level(logging.WARNING):
        for n in (1, 2, 3):
            turn(provider, n)
    assert any("dropped the 2 oldest" in r.getMessage() for r in caplog.records)
    recover(alice, clock)
    turn(provider, 4)
    assert alice.texts("hermes-sid-1") == pair(2, 3, 4)


def test_backlog_is_resent_in_batches_of_at_most_100(writer, clock):
    provider, servers, _, _ = writer
    alice = servers["alice"]
    alice.fail = "down"
    for n in range(60):
        turn(provider, n)
    recover(alice, clock)
    alice.requests.clear()
    turn(provider, "last")
    assert alice.texts("hermes-sid-1") == pair(*range(60), "last")
    assert alice.requests == ["/api/v1/sessions/hermes-sid-1/messages/batch"] * 3


def test_turns_within_the_cooldown_are_queued_without_a_request(writer, clock):
    provider, servers, _, session_writer = writer
    alice = servers["alice"]
    alice.fail = "down"
    turn(provider, 1)
    alice.fail = None
    alice.requests.clear()
    clock.advance(session_writer._UPLOAD_COOLDOWN_SECONDS / 2)
    turn(provider, 2)
    # Still inside the cooldown: turn 2 waits behind turn 1 without touching the server.
    assert alice.requests == []
    clock.advance(session_writer._UPLOAD_COOLDOWN_SECONDS)
    turn(provider, 3)
    assert alice.requests == ["/api/v1/sessions/hermes-sid-1/messages/batch"] * 2
    assert alice.texts("hermes-sid-1") == pair(1, 2, 3)


def test_cooldown_is_per_connection_generation(writer, clock):
    provider, servers, connect, _ = writer
    alice, bob = servers["alice"], servers["bob"]
    alice.fail = "down"
    turn(provider, 1)
    connect("bob")
    turn(provider, 2)
    # Bob's generation has no failure behind it, so its turn is sent at once.
    assert bob.texts("hermes-sid-1") == pair(2)


@pytest.mark.parametrize("reported, committed", [(10**9, True), (0, False)])
def test_threshold_commit_uses_pending_tokens_from_the_write_response(writer, reported, committed):
    provider, servers, _, _ = writer
    alice = servers["alice"]
    alice.reported_pending_tokens = reported
    turn(provider, 1)
    assert alice.gets == []
    assert alice.committed("hermes-sid-1") == ([pair(1)] if committed else [])


def test_threshold_commit_looks_up_the_session_when_the_response_has_no_pending_tokens(writer):
    provider, servers, _, _ = writer
    alice = servers["alice"]
    turn(provider, 1)
    assert alice.gets == ["/api/v1/sessions/hermes-sid-1"]


def test_auth_failure_backlog_expires_within_the_same_connection(writer, clock, caplog):
    provider, servers, _, session_writer = writer
    alice = servers["alice"]
    alice.fail = 401
    turn(provider, 1)
    turn(provider, 2)
    alice.fail = None
    clock.advance(session_writer._AUTH_BACKLOG_TTL_SECONDS)
    with caplog.at_level(logging.WARNING):
        turn(provider, 3)
    # Both rejected turns expired together: the TTL counts from the first rejection.
    assert alice.texts("hermes-sid-1") == pair(3)
    assert any("still not accepted" in r.getMessage() for r in caplog.records)


def test_auth_failure_backlog_is_kept_before_its_ttl(writer, clock):
    provider, servers, _, session_writer = writer
    alice = servers["alice"]
    alice.fail = 403
    turn(provider, 1)
    alice.fail = None
    clock.advance(session_writer._AUTH_BACKLOG_TTL_SECONDS - 60)
    turn(provider, 2)
    assert alice.texts("hermes-sid-1") == pair(1, 2)
