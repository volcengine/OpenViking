import json

import pytest
from vikingbot.agent.tools.web import WebFetchTool


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1/admin",
        "http://169.254.169.254/latest/meta-data/",
        "http://[::1]/private",
    ],
)
async def test_web_fetch_rejects_non_public_destinations_before_connecting(url):
    result = json.loads(await WebFetchTool().execute(None, url))

    assert "URL validation failed" in result["error"]
