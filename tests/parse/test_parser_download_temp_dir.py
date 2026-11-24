import httpx

from openviking.parse.accessors.http_accessor import HTTPAccessor
from openviking_cli.utils.config.open_viking_config import OpenVikingConfig


async def test_http_download_uses_configured_temporary_directory(tmp_path, monkeypatch):
    configured = tmp_path / "downloads"
    config = OpenVikingConfig.from_dict({"parsers": {"download_temp_dir": str(configured)}})
    monkeypatch.setattr("openviking_cli.utils.config.get_openviking_config", lambda: config)
    client_type = httpx.AsyncClient
    content = b"downloaded report"
    transport = httpx.MockTransport(
        lambda request: httpx.Response(200, headers={"Content-Type": "text/plain"}, content=content)
    )
    monkeypatch.setattr(
        httpx, "AsyncClient", lambda **kwargs: client_type(transport=transport, **kwargs)
    )

    resource = await HTTPAccessor().access("https://example.com/report.txt")
    try:
        assert resource.path.parent == configured
        assert resource.path.read_bytes() == content
    finally:
        resource.cleanup()
    assert list(configured.iterdir()) == []
