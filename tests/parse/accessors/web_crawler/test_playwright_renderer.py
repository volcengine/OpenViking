from openviking.parse.accessors.web_crawler.playwright_renderer import PlaywrightRenderer


class _FakePage:
    """Minimal page stub returning a scripted sequence of body texts."""

    def __init__(self, body_sequence):
        self._bodies = list(body_sequence)
        self.inner_text_calls = 0
        self.slept_ms = 0

    async def inner_text(self, _selector):
        self.inner_text_calls += 1
        idx = min(self.inner_text_calls - 1, len(self._bodies) - 1)
        return self._bodies[idx]

    async def wait_for_timeout(self, ms):
        self.slept_ms += ms


class TestRenderer:
    async def test_close_is_idempotent_when_browser_never_started(self):
        renderer = PlaywrightRenderer()
        await renderer.close()
        await renderer.close()

    def test_request_validator_stored(self):
        called = []
        renderer = PlaywrightRenderer(request_validator=lambda u: called.append(u))
        assert renderer._request_validator is not None
        renderer._request_validator("http://x.com/")
        assert called == ["http://x.com/"]


class _FakeRoute:
    def __init__(self, url):
        self.request = type("Req", (), {"url": url})()
        self.aborted = False
        self.continued = False

    async def abort(self):
        self.aborted = True

    async def continue_(self):
        self.continued = True


class TestGuardRoute:
    async def test_blocks_disallowed_subresource_without_raising(self):
        def validator(url):
            if "private" in url:
                raise ValueError("private network blocked")

        route = _FakeRoute("http://private.internal/probe")
        # Must not raise: a blocked sub-resource cannot fail the whole render.
        await PlaywrightRenderer._guard_route(route, validator)
        assert route.aborted is True
        assert route.continued is False

    async def test_allows_valid_subresource(self):
        route = _FakeRoute("https://cdn.example.com/app.js")
        await PlaywrightRenderer._guard_route(route, lambda _u: None)
        assert route.continued is True
        assert route.aborted is False


class TestWaitPastChallenge:
    async def test_waits_until_challenge_redirects_to_content(self):
        real = "Real documentation body with plenty of visible content here." * 3
        page = _FakePage(["Please wait...", "Please wait...", real])
        await PlaywrightRenderer._wait_past_challenge(page, timeout_ms=10_000, poll_ms=10)
        assert page.inner_text_calls == 3
        assert page.slept_ms == 20

    async def test_returns_immediately_when_content_present(self):
        real = "Real documentation body with plenty of visible content here." * 3
        page = _FakePage([real])
        await PlaywrightRenderer._wait_past_challenge(page, timeout_ms=10_000, poll_ms=10)
        assert page.inner_text_calls == 1
        assert page.slept_ms == 0

    async def test_gives_up_after_timeout_on_persistent_challenge(self):
        page = _FakePage(["Please wait..."])
        await PlaywrightRenderer._wait_past_challenge(page, timeout_ms=0, poll_ms=10)
        assert page.inner_text_calls == 1
        assert page.slept_ms == 0

