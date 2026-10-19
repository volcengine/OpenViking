"""Extra HTTP headers (e.g. Cloudflare Access service tokens) reach the wire.

An OpenViking server published behind an authenticating gateway (Cloudflare Access,
oauth2-proxy, an mTLS-terminating proxy) requires a client header on every request.
The JS side has carried this for a while (``OPENVIKING_EXTRA_HEADERS`` in
``memory-plugin-shared/lib/mcp-proxy-config.mjs``, plus ``extra_headers`` in the
linked ``ovcli.conf``); these tests pin the same behaviour for this plugin.
"""

import json
import threading
from contextlib import suppress
from http.server import BaseHTTPRequestHandler, HTTPServer


def _start_server(records, *, require_header=None, redirect_to=None, plain_redirect_to=None,
                  gateway_value=None, require_key=False, key_retry_redirect_to=None):
    """Local stand-in for an OpenViking server.

    ``redirect_to`` models an authenticating gateway that answers header-less requests
    by redirecting to its own login page (Cloudflare Access, oauth2-proxy, ...) instead
    of returning 401/403.

    ``plain_redirect_to`` models an endpoint that simply redirects every request and
    says nothing about being OpenViking — the ordinary 3xx that must never receive
    credentials.

    ``gateway_value`` makes ``require_header`` value-checked: a request that does not
    present exactly this value is answered like a header-less one, modelling a gateway
    token that expired or was rotated.

    ``require_key`` makes the server behind the gateway ask for credentials itself: a
    request that passes the gateway but carries no ``X-API-Key`` gets ``401``.

    ``key_retry_redirect_to`` makes the answer to that key-bearing request a redirect
    whose body is a perfectly healthy-looking payload, modelling a gateway that gates
    the retry too. Nothing may read it as an answer.
    """

    class Handler(BaseHTTPRequestHandler):
        def _redirect(self, target):
            body = b"<html><head><title>302 Found</title></head><body></body></html>"
            self.send_response(302)
            self.send_header("Location", target)
            self.send_header("Content-Type", "text/html; charset=UTF-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _redirect_with_json(self, target):
            """A redirect that also carries a healthy payload: only the status is honest."""
            raw = json.dumps({"status": "ok", "healthy": True, "version": "0.4.18",
                              "auth_mode": "api_key"}).encode()
            self.send_response(302)
            self.send_header("Location", target)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def _json(self, status, payload):
            raw = json.dumps(payload).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def do_GET(self):
            records.append(dict(self.headers))
            if plain_redirect_to:
                self._redirect(plain_redirect_to)
                return
            if require_header:
                presented = self.headers.get(require_header)
                authorised = bool(presented) and (
                    gateway_value is None or presented == gateway_value
                )
                if not authorised:
                    if redirect_to:
                        self._redirect(redirect_to)
                        return
                    self._json(403, {"error": "blocked at edge"})
                    return
            if require_key and not self.headers.get("X-API-Key"):
                self._json(401, {"error": {"code": "AuthenticationError",
                                           "message": "The API key in the request is missing or invalid."}})
                return
            if key_retry_redirect_to:
                self._redirect_with_json(key_retry_redirect_to)
                return
            self._json(200, {"status": "ok", "healthy": True,
                             "version": "0.4.18", "auth_mode": "api_key"})

        def log_message(self, *_args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    return server, worker


def test_extra_headers_from_env_reach_every_request(external_provider):
    _, _, module, _ = external_provider("extra-headers")
    records = []
    server, worker = _start_server(records, require_header="CF-Access-Client-Id")
    endpoint = f"http://127.0.0.1:{server.server_port}"
    try:
        settings = module._resolve_connection_settings({}, env={
            "OPENVIKING_API_KEY": "test-key",
            "OPENVIKING_EXTRA_HEADERS": json.dumps({
                "CF-Access-Client-Id": "id.access",
                "CF-Access-Client-Secret": "gateway-secret",
            }),
        })
        assert settings["extra_headers"] == {
            "CF-Access-Client-Id": "id.access",
            "CF-Access-Client-Secret": "gateway-secret",
        }
        client = module._VikingClient(endpoint, settings["api_key"],
                                      extra_headers=settings["extra_headers"])
        assert client.get("/health")["status"] == "ok"
        assert records[-1]["CF-Access-Client-Id"] == "id.access"
        assert records[-1]["CF-Access-Client-Secret"] == "gateway-secret"
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=5)


def test_extra_headers_are_read_from_linked_ovcli_conf():
    """The documented client file already carries extra_headers; do not drop it."""
    import importlib.util
    import sys
    from pathlib import Path

    here = Path(__file__).resolve().parents[1]
    spec = importlib.util.spec_from_file_location("openviking_under_test", here / "__init__.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)

    values = module._connection_values_from_ovcli({
        "url": "https://ov.example.com",
        "api_key": "user-key",
        "extra_headers": {"CF-Access-Client-Id": "id.access"},
    })
    assert values["extra_headers"] == {"CF-Access-Client-Id": "id.access"}


def test_extra_headers_cannot_forge_identity(external_provider):
    _, _, module, _ = external_provider("extra-headers-identity")
    parsed = module._parse_extra_headers(json.dumps({
        "X-OpenViking-Account": "someone-else",
        "X-OpenViking-User": "root",
        "CF-Access-Client-Id": "id.access",
    }))
    assert parsed == {"CF-Access-Client-Id": "id.access"}

    client = module._VikingClient("http://127.0.0.1:1", "real-key", extra_headers={
        "X-API-Key": "not-mine",
        "Authorization": "Bearer not-mine",
        "X-OpenViking-Actor-Peer": "not-mine",
        "CF-Access-Client-Id": "id.access",
    })
    headers = client._headers()
    assert headers["X-API-Key"] == "real-key"
    assert headers["Authorization"] == "Bearer real-key"
    assert "X-OpenViking-Account" not in headers
    assert headers["CF-Access-Client-Id"] == "id.access"


def test_extra_headers_invalid_input_is_ignored(external_provider):
    _, _, module, _ = external_provider("extra-headers-invalid")
    assert module._parse_extra_headers("not json") == {}
    assert module._parse_extra_headers("") == {}
    assert module._parse_extra_headers(None) == {}
    assert module._parse_extra_headers("[1, 2]") == {}
    assert module._parse_extra_headers({"A": {"nested": 1}, "B": 2, "C": True}) == {"B": "2"}
    settings = module._resolve_connection_settings({}, env={"OPENVIKING_EXTRA_HEADERS": "not json"})
    assert settings["extra_headers"] == {}


def test_startup_health_probe_carries_extra_headers(external_provider):
    """The startup health check must use the resolved extra headers.

    A gateway that protects every route (Cloudflare Access, oauth2-proxy) rejects a
    header-less probe. ``_VikingClient`` snapshots the map at construction, so the
    provider has to resolve it *before* the first client is built — otherwise the
    probe is refused, the client is dropped, and the provider stays unusable until
    some unrelated config change forces a rebuild.
    """
    records = []
    server, worker = _start_server(records, require_header="CF-Access-Client-Id")
    endpoint = "http://127.0.0.1:%d" % server.server_port
    try:
        home, provider, _module, _settings = external_provider("extra-headers-startup")
        (home / ".env").write_text(
            "OPENVIKING_ENDPOINT=%s\nOPENVIKING_API_KEY=test-key\nOPENVIKING_EXTRA_HEADERS=%s\n"
            % (endpoint, json.dumps({"CF-Access-Client-Id": "id.access"})),
            encoding="utf-8",
        )
        provider.initialize(session_id="startup-health", hermes_home=str(home), platform="cli")
        assert records, "startup should have probed /health"
        assert any(r.get("CF-Access-Client-Id") == "id.access" for r in records), (
            "every probe went out without the resolved headers: a gateway would refuse them all"
        )
        assert provider._client is not None, "provider dropped the client after a refused probe"
        assert provider._client._extra_headers == {"CF-Access-Client-Id": "id.access"}
        # A 401/403 is the server itself asking for credentials: that retry keeps sending the
        # key, exactly as it did before gateway support existed.
        assert records[-1].get("X-API-Key") == "test-key", (
            "the retry after 403 stopped carrying the key"
        )
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=5)


def test_startup_probe_retries_when_gateway_redirects_to_login(external_provider):
    """A redirect to a login page means "credentials wanted", not "not OpenViking".

    Cloudflare Access answers a header-less request with ``302`` and
    ``Location: https://<team>.cloudflareaccess.com/cdn-cgi/access/login/<host>``. The
    anonymous identity probe deliberately sends no credentials, so it cannot read that
    HTML page; without a retry the provider reports a reachable server as "not valid
    OpenViking" and drops the client, even though every authenticated request would work.
    """
    records = []
    server, worker = _start_server(
        records,
        require_header="CF-Access-Client-Id",
        redirect_to="https://team.cloudflareaccess.com/cdn-cgi/access/login/openviking.example.com",
    )
    endpoint = "http://127.0.0.1:%d" % server.server_port
    try:
        home, provider, _module, _settings = external_provider("extra-headers-redirect")
        (home / ".env").write_text(
            "OPENVIKING_ENDPOINT=%s\nOPENVIKING_API_KEY=test-key\nOPENVIKING_EXTRA_HEADERS=%s\n"
            % (endpoint, json.dumps({"CF-Access-Client-Id": "id.access"})),
            encoding="utf-8",
        )
        provider.initialize(session_id="gateway-redirect", hermes_home=str(home), platform="cli")
        assert provider._client is not None, (
            "provider dropped a reachable server because the anonymous probe was redirected"
        )
        assert records[-1].get("CF-Access-Client-Id") == "id.access", (
            "the retry after the redirect went out without the resolved headers"
        )
        leaked = [
            r for r in records[-1:]
            if r.get("X-API-Key") or r.get("Authorization") or r.get("X-OpenViking-Actor-Peer")
        ]
        assert not leaked, (
            "the retry after a redirect carried more than the gateway headers: %s"
            % json.dumps(leaked[-1])
        )
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=5)


def test_ordinary_redirect_receives_no_secrets(external_provider):
    """An ordinary 3xx is not authorization to resend credentials.

    Without gateway headers configured there is nothing identifying the endpoint as
    OpenViking, so a redirect must not turn the probe into an authenticated request:
    the API key (``X-API-Key``, ``Authorization``) has to stay unsent, and the endpoint
    stays unusable exactly as it was before this change.
    """
    records = []
    server, worker = _start_server(records, plain_redirect_to="https://example.com/moved")
    endpoint = "http://127.0.0.1:%d" % server.server_port
    try:
        home, provider, _module, _settings = external_provider("plain-redirect")
        (home / ".env").write_text(
            "OPENVIKING_ENDPOINT=%s\nOPENVIKING_API_KEY=test-key\n" % endpoint,
            encoding="utf-8",
        )
        provider.initialize(session_id="plain-redirect", hermes_home=str(home), platform="cli")

        assert records, "startup should have probed /health"
        leaked = [
            r for r in records
            if r.get("X-API-Key") or r.get("Authorization") or r.get("X-OpenViking-Account")
        ]
        assert not leaked, (
            "an ordinary redirect was answered with credentials: %s" % json.dumps(leaked[-1])
        )
        assert len(records) == 1, (
            "the probe retried after an ordinary redirect instead of failing once: %d requests"
            % len(records)
        )
        assert provider._client is None, (
            "a redirecting non-OpenViking endpoint was accepted as a client"
        )
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=5)


def test_gateway_only_deployment_needs_no_api_key(external_provider):
    """A gateway-only deployment: extra_headers set, no OpenViking API key at all.

    The gateway retry carries the operator's headers and nothing else, so it must not be
    gated on a key that request never sends. A loopback OpenViking published through
    Cloudflare Access has exactly this shape.
    """
    records = []
    server, worker = _start_server(
        records,
        require_header="CF-Access-Client-Id",
        redirect_to="https://team.cloudflareaccess.com/cdn-cgi/access/login/openviking.example.com",
    )
    endpoint = "http://127.0.0.1:%d" % server.server_port
    try:
        _, _, module, _ = external_provider("gateway-only-no-key")
        client = module._VikingClient(
            endpoint, "", extra_headers={"CF-Access-Client-Id": "id.access"}
        )
        payload = client.health_payload()

        assert payload.get("status") == "ok", (
            "a gateway-only deployment could not probe /health without an API key"
        )
        assert len(records) == 2, (
            "expected one anonymous probe and one gateway retry, got %d requests" % len(records)
        )
        assert records[1].get("CF-Access-Client-Id") == "id.access"
        assert not (records[1].get("X-API-Key") or records[1].get("Authorization")), (
            "the gateway retry carried credentials it never needed"
        )
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=5)


def test_gateway_redirect_then_key_challenge_is_answered(external_provider):
    """A gateway in front of an API-key-protected /health still starts.

    anonymous -> 302 (gateway), gateway-only retry -> 401 (the server itself asks for
    credentials), key retry -> 200. Three requests at most, and the key stays out of the
    redirect attempt.
    """
    records = []
    server, worker = _start_server(
        records,
        require_header="CF-Access-Client-Id",
        redirect_to="https://team.cloudflareaccess.com/cdn-cgi/access/login/openviking.example.com",
        require_key=True,
    )
    endpoint = "http://127.0.0.1:%d" % server.server_port
    try:
        _, _, module, _ = external_provider("gateway-then-key")
        client = module._VikingClient(
            endpoint, "test-key", extra_headers={"CF-Access-Client-Id": "id.access"}
        )
        payload = client.health_payload()

        assert payload.get("status") == "ok", (
            "the 401 challenge behind the gateway was never answered"
        )
        assert len(records) == 3, (
            "expected probe + gateway retry + key retry, got %d requests" % len(records)
        )
        assert not (records[1].get("X-API-Key") or records[1].get("Authorization")), (
            "the redirect retry carried the key"
        )
        assert records[2].get("X-API-Key") == "test-key", (
            "the explicit 401 challenge went unanswered"
        )
        assert records[2].get("CF-Access-Client-Id") == "id.access"
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=5)


def test_stale_gateway_token_reports_the_gateway_not_openviking(external_provider):
    """An expired gateway token must say so, not "not valid OpenViking".

    The retry stays bounded and the login URL is never followed.
    """
    records = []
    server, worker = _start_server(
        records,
        require_header="CF-Access-Client-Id",
        redirect_to="https://team.cloudflareaccess.com/cdn-cgi/access/login/openviking.example.com",
        gateway_value="rotated-token",
    )
    endpoint = "http://127.0.0.1:%d" % server.server_port
    try:
        _, _, module, _ = external_provider("stale-gateway-token")
        client = module._VikingClient(
            endpoint, "test-key", extra_headers={"CF-Access-Client-Id": "stale-token"}
        )
        message = ""
        try:
            client.health_payload()
        except Exception as exc:  # noqa: BLE001 — the message is the point here
            message = str(exc)

        assert "still redirecting" in message or "extra_headers" in message, (
            "a repeated gateway redirect was reported as %r" % message
        )
        assert len(records) == 2, "the retry was not bounded: %d requests" % len(records)
        assert not (records[1].get("X-API-Key") or records[1].get("Authorization")), (
            "the bounded retry carried credentials"
        )
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=5)


def test_gateway_only_deployment_needs_no_api_key_when_gateway_answers_403(external_provider):
    """A gateway-only deployment whose gateway answers ``403`` instead of redirecting.

    Cloudflare Access redirects, but oauth2-proxy and many WAFs answer ``401``/``403``
    to a request that carries no service token. The retry that would satisfy them sends
    the operator's headers and no OpenViking key, so it must not be gated on a key this
    deployment does not have. ``require_header`` with no ``redirect_to`` is exactly that
    shape: the header-less probe is refused, the header-bearing retry is accepted.
    """
    records = []
    server, worker = _start_server(records, require_header="CF-Access-Client-Id")
    endpoint = "http://127.0.0.1:%d" % server.server_port
    try:
        _, _, module, _ = external_provider("gateway-only-403")
        client = module._VikingClient(
            endpoint, "", extra_headers={"CF-Access-Client-Id": "id.access"}
        )
        payload = client.health_payload()

        assert payload.get("status") == "ok", (
            "a gateway-only deployment could not probe a gateway that answers 403"
        )
        assert len(records) == 2, (
            "expected one anonymous probe and one gateway retry, got %d requests" % len(records)
        )
        assert records[1].get("CF-Access-Client-Id") == "id.access"
        assert not (records[1].get("X-API-Key") or records[1].get("Authorization")), (
            "the gateway retry carried credentials it never needed"
        )
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=5)


def test_gateway_only_deployment_starts_when_gateway_answers_403(external_provider):
    """The same shape through provider.initialize(): the client must survive startup."""
    records = []
    server, worker = _start_server(records, require_header="CF-Access-Client-Id")
    endpoint = "http://127.0.0.1:%d" % server.server_port
    try:
        home, provider, _module, _settings = external_provider("gateway-only-403-startup")
        (home / ".env").write_text(
            "OPENVIKING_ENDPOINT=%s\nOPENVIKING_EXTRA_HEADERS=%s\n"
            % (endpoint, json.dumps({"CF-Access-Client-Id": "id.access"})),
            encoding="utf-8",
        )
        provider.initialize(session_id="gateway-only-403", hermes_home=str(home), platform="cli")

        assert provider._client is not None, (
            "provider dropped a reachable, gateway-protected server: the 403 challenge was "
            "never retried with the gateway headers (%d request(s))" % len(records)
        )
        assert any(r.get("CF-Access-Client-Id") == "id.access" for r in records), (
            "no request ever carried the gateway headers"
        )
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=5)


def test_key_retry_that_lands_on_a_redirect_is_not_read_as_a_payload(external_provider):
    """A redirect answered to the key retry must not become a "healthy" payload.

    ``_parse_response`` raises only at ``>= 400``, so a 3xx carrying a healthy-looking
    JSON body would otherwise be returned as the probe result and the endpoint accepted
    — even though the gateway is still gating the request.
    """
    records = []
    server, worker = _start_server(
        records,
        require_key=True,
        key_retry_redirect_to="https://team.cloudflareaccess.com/cdn-cgi/access/login/openviking.example.com",
    )
    endpoint = "http://127.0.0.1:%d" % server.server_port
    try:
        _, _, module, _ = external_provider("key-retry-redirect")
        client = module._VikingClient(endpoint, "test-key")
        message = ""
        payload = None
        try:
            payload = client.health_payload()
        except Exception as exc:  # noqa: BLE001 — the message is the point here
            message = str(exc)

        assert not (isinstance(payload, dict) and payload.get("status") == "ok"), (
            "a redirect answered to the key retry was accepted as a healthy payload: %r" % (payload,)
        )
        assert "302" in message and "redirecting" in message, (
            "a redirect answered to the key retry was reported as %r" % message
        )
        assert len(records) == 2, "the retry was not bounded: %d requests" % len(records)
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=5)


def test_redirect_destination_is_never_requested(external_provider):
    """The login URL must not be fetched — proven by a second listener as the target.

    A single listener cannot tell a followed redirect from an unfollowed one: if the
    ``Location`` names another host, nothing in the test process would observe the
    request. So the target is a second stub on loopback, and it must stay empty.
    """
    target_records = []
    target, target_worker = _start_server(target_records)
    destination = "http://127.0.0.1:%d/cdn-cgi/access/login" % target.server_port
    records = []
    server, worker = _start_server(records, plain_redirect_to=destination)
    endpoint = "http://127.0.0.1:%d" % server.server_port
    try:
        _, _, module, _ = external_provider("redirect-destination")
        client = module._VikingClient(endpoint, "test-key")
        with suppress(Exception):
            client.health_payload()

        assert not target_records, (
            "the redirect destination was requested %d time(s): %s"
            % (len(target_records), json.dumps(target_records))
        )
        assert len(records) == 1, (
            "the probe did not fail once on an ordinary redirect: %d requests" % len(records)
        )
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=5)
        target.shutdown()
        target.server_close()
        target_worker.join(timeout=5)


def test_gateway_redirect_destination_is_never_requested(external_provider):
    """Same for the gateway path: the retry must not follow the login URL either."""
    target_records = []
    target, target_worker = _start_server(target_records)
    destination = "http://127.0.0.1:%d/cdn-cgi/access/login" % target.server_port
    records = []
    server, worker = _start_server(
        records,
        require_header="CF-Access-Client-Id",
        redirect_to=destination,
        gateway_value="rotated-token",
    )
    endpoint = "http://127.0.0.1:%d" % server.server_port
    try:
        _, _, module, _ = external_provider("gateway-redirect-destination")
        client = module._VikingClient(
            endpoint, "test-key", extra_headers={"CF-Access-Client-Id": "stale-token"}
        )
        with suppress(Exception):
            client.health_payload()

        assert not target_records, (
            "the gateway retry followed the login URL (%d request(s) at the destination)"
            % len(target_records)
        )
        assert len(records) == 2, "the retry was not bounded: %d requests" % len(records)
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=5)
        target.shutdown()
        target.server_close()
        target_worker.join(timeout=5)
