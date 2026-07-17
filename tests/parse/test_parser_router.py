from types import SimpleNamespace

from openviking.parse.parser_router import ParserRouter


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


def test_feishu_url_flag_defaults_to_extension_routing(monkeypatch):
    config = SimpleNamespace(
        parser_api=SimpleNamespace(
            enable=True,
            enable_feishu_url=False,
            extensions=[],
        ),
    )
    monkeypatch.setattr(
        "openviking_cli.utils.config.open_viking_config.get_openviking_config",
        lambda: config,
    )

    router = ParserRouter(parser_registry=object())

    assert not router.should_use_understanding_api("https://example.larkoffice.com/docx/doxcnToken")
