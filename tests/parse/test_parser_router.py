from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from openviking.parse.accessors.base import LocalResource, SourceType
from openviking.parse.parser_router import ParserRouter


def test_should_use_understanding_api_for_signed_video_url(monkeypatch):
    config = SimpleNamespace(
        parser_api=SimpleNamespace(enable=True, extensions=["mp4"]),
    )
    monkeypatch.setattr(
        "openviking_cli.utils.config.open_viking_config.get_openviking_config",
        lambda: config,
    )

    router = ParserRouter(parser_registry=object())

    assert router.should_use_understanding_api(
        "https://example.com/media/video.mp4?X-Tos-Signature=abc&X-Tos-Expires=60"
    )


@pytest.mark.asyncio
async def test_local_typescript_bypasses_media_understanding_api(monkeypatch, tmp_path):
    source_path = tmp_path / "example.ts"
    source_path.write_text("const answer = 42", encoding="utf-8")
    source = LocalResource(
        path=source_path,
        source_type=SourceType.LOCAL,
        original_source=str(source_path),
        is_temporary=False,
    )
    config = SimpleNamespace(parser_api=SimpleNamespace(enable=True, extensions=["ts"]))
    monkeypatch.setattr(
        "openviking_cli.utils.config.open_viking_config.get_openviking_config",
        lambda: config,
    )
    registry = SimpleNamespace(parse=AsyncMock(return_value="text-result"))
    router = ParserRouter(parser_registry=registry)
    router._get_understanding_api = lambda: (_ for _ in ()).throw(
        AssertionError("local TypeScript must not use UnderstandingAPI")
    )

    assert await router.parse(source) == "text-result"
    registry.parse.assert_awaited_once_with(source_path)


@pytest.mark.asyncio
async def test_remote_mpeg_ts_uses_accessor_confirmed_video_parser(monkeypatch, tmp_path):
    source_path = tmp_path / "download.ts"
    source_path.write_bytes(b"mpeg-ts")
    source = LocalResource(
        path=source_path,
        source_type=SourceType.HTTP,
        original_source="https://example.com/sample.ts",
        meta={"url_type": "download_video"},
    )
    config = SimpleNamespace(parser_api=SimpleNamespace(enable=False, extensions=[]))
    monkeypatch.setattr(
        "openviking_cli.utils.config.open_viking_config.get_openviking_config",
        lambda: config,
    )
    video_parser = SimpleNamespace(parse=AsyncMock(return_value="video-result"))
    registry = SimpleNamespace(
        get_parser=lambda name: video_parser if name == "video" else None,
        parse=AsyncMock(),
    )
    router = ParserRouter(parser_registry=registry)

    assert await router.parse(source) == "video-result"
    video_parser.parse.assert_awaited_once_with(source_path)
    registry.parse.assert_not_awaited()
