from openviking.parse.accessors.web_crawler.playwright_renderer import PlaywrightRenderer


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
