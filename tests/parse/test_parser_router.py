from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from openviking.parse.accessors.base import LocalResource, SourceType
from openviking.parse.parser_router import ParserRouter
from openviking.parse.understanding_api import PREPARED_RESPONSE_ID_ARG
from openviking.utils.media_processor import UnifiedResourceProcessor


def test_should_use_understanding_api_for_signed_video_url(monkeypatch):
    config = SimpleNamespace(
        parser_api=SimpleNamespace(
            enable=True,
            enable_feishu_url=False,
            extensions=["mp4"],
        ),
    )
    monkeypatch.setattr(
        "openviking_cli.utils.config.open_viking_config.get_openviking_config",
        lambda: config,
    )

    router = ParserRouter(parser_registry=object())

    assert router.should_use_understanding_api(
        "https://example.com/media/video.mp4?X-Tos-Signature=abc&X-Tos-Expires=60"
    )


def test_resolved_extension_routes_extensionless_download(monkeypatch, tmp_path):
    config = SimpleNamespace(
        parser_api=SimpleNamespace(
            enable=True,
            enable_feishu_url=False,
            extensions=["pdf"],
        ),
    )
    monkeypatch.setattr(
        "openviking_cli.utils.config.open_viking_config.get_openviking_config",
        lambda: config,
    )
    downloaded = tmp_path / "download"
    downloaded.write_bytes(b"%PDF-1.7")
    resource = LocalResource(
        path=downloaded,
        source_type=SourceType.HTTP,
        original_source="https://example.com/download?id=123",
        meta={"extension": ".pdf"},
        is_temporary=False,
    )
    processor = UnifiedResourceProcessor()

    processor._set_resolved_identity(resource, source_name=None)

    assert resource.meta["resolved_extension"] == ".pdf"
    assert processor.should_use_understanding_api(resource)


def test_should_use_understanding_api_for_feishu_url(monkeypatch):
    config = SimpleNamespace(
        parser_api=SimpleNamespace(
            enable=True,
            enable_feishu_url=True,
            extensions=[],
        ),
    )
    monkeypatch.setattr(
        "openviking_cli.utils.config.open_viking_config.get_openviking_config",
        lambda: config,
    )

    router = ParserRouter(parser_registry=object())

    assert router.should_use_understanding_api("https://example.larkoffice.com/wiki/wikicnToken")
    assert not router.should_use_understanding_api(
        "https://larkoffice.com.evil.example/wiki/wikicnToken"
    )


def test_feishu_url_flag_is_required_even_when_extension_matches(monkeypatch):
    config = SimpleNamespace(
        parser_api=SimpleNamespace(
            enable=True,
            enable_feishu_url=False,
            extensions=["pdf"],
        ),
    )
    monkeypatch.setattr(
        "openviking_cli.utils.config.open_viking_config.get_openviking_config",
        lambda: config,
    )

    router = ParserRouter(parser_registry=object())

    assert not router.should_use_understanding_api(
        "https://example.larkoffice.com/docx/doxcnToken.pdf"
    )


def test_directories_never_route_to_understanding(monkeypatch, tmp_path):
    config = SimpleNamespace(
        parser_api=SimpleNamespace(
            enable=True,
            enable_feishu_url=False,
            extensions=["pdf"],
        ),
    )
    monkeypatch.setattr(
        "openviking_cli.utils.config.open_viking_config.get_openviking_config",
        lambda: config,
    )
    resource = LocalResource(
        path=tmp_path,
        source_type=SourceType.HTTP,
        original_source="https://example.com/site",
        meta={"resolved_extension": ".pdf"},
        is_temporary=False,
    )

    assert not UnifiedResourceProcessor().should_use_understanding_api(resource)


@pytest.mark.asyncio
async def test_normalized_feishu_markdown_stays_internal(monkeypatch, tmp_path):
    config = SimpleNamespace(
        parser_api=SimpleNamespace(
            enable=True,
            enable_feishu_url=True,
            extensions=["md"],
        ),
    )
    monkeypatch.setattr(
        "openviking_cli.utils.config.open_viking_config.get_openviking_config",
        lambda: config,
    )
    resource = LocalResource(
        path=tmp_path / "document.md",
        source_type=SourceType.FEISHU,
        original_source="https://example.feishu.cn/docx/doc-token",
        meta={"resolved_extension": ".md"},
        is_temporary=False,
    )
    registry = SimpleNamespace(parse=AsyncMock(return_value=object()))
    understanding = SimpleNamespace(parse=AsyncMock(return_value=object()))
    router = ParserRouter(registry)
    router._understanding_api = understanding
    processor = UnifiedResourceProcessor()
    processor._parser_router = router

    assert not processor.should_use_understanding_api(resource)
    await router.parse(
        resource,
        resolved_extension=".md",
        parser_backend="understanding",
    )

    registry.parse.assert_awaited_once()
    understanding.parse.assert_not_awaited()


@pytest.mark.asyncio
async def test_forced_resolved_extension_survives_worker_redownload(tmp_path):
    downloaded = tmp_path / "redetected.docx"
    downloaded.write_bytes(b"content")
    resource = LocalResource(
        path=downloaded,
        source_type=SourceType.HTTP,
        original_source="https://example.com/download",
        meta={"resolved_extension": ".docx"},
        is_temporary=False,
    )
    parser_router = SimpleNamespace(parse=AsyncMock(return_value=object()))
    processor = UnifiedResourceProcessor(vlm_processor=object())
    processor._parser_router = parser_router

    await processor.process(
        "https://example.com/download",
        prepared_resource=resource,
        resolved_extension=".pdf",
    )

    assert parser_router.parse.await_args.kwargs["resolved_extension"] == ".pdf"


@pytest.mark.asyncio
async def test_prepared_understanding_response_bypasses_remote_redownload():
    result = object()
    parser_router = SimpleNamespace(parse=AsyncMock(return_value=result))
    accessor = SimpleNamespace(
        access=AsyncMock(side_effect=AssertionError("prepared response must not redownload source"))
    )
    processor = UnifiedResourceProcessor(vlm_processor=object())
    processor._parser_router = parser_router
    processor._accessor_registry = accessor

    actual = await processor.process(
        "https://example.com/expiring-download",
        resolved_extension=".pdf",
        **{PREPARED_RESPONSE_ID_ARG: "response-1"},
    )

    assert actual is result
    accessor.access.assert_not_awaited()
    parser_router.parse.assert_awaited_once()
    assert parser_router.parse.await_args.args == ("https://example.com/expiring-download",)
    assert parser_router.parse.await_args.kwargs["parser_backend"] == "understanding"
    assert parser_router.parse.await_args.kwargs[PREPARED_RESPONSE_ID_ARG] == "response-1"
