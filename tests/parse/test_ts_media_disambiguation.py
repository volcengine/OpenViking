# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0

from pathlib import Path
from types import SimpleNamespace

from openviking.parse.parser_router import ParserRouter
from openviking.parse.parsers.media.detection import (
    is_mpeg_transport_stream_bytes,
    is_mpeg_transport_stream_file,
)
from openviking.parse.parsers.media.utils import get_media_type
from openviking.parse.parsers.media.video import VideoParser
from openviking.parse.parsers.text import TextParser
from openviking.parse.registry import ParserRegistry


def _transport_stream(packet_size: int = 188, prefix_size: int = 0) -> bytes:
    packet = b"\x47" + bytes(packet_size - 1)
    return bytes(prefix_size) + packet * 5


def test_detects_common_mpeg_transport_stream_packet_sizes():
    assert is_mpeg_transport_stream_bytes(_transport_stream(188))
    assert is_mpeg_transport_stream_bytes(_transport_stream(192, prefix_size=4))
    assert is_mpeg_transport_stream_bytes(_transport_stream(204))


def test_typescript_source_is_not_a_transport_stream(tmp_path: Path):
    source = tmp_path / "component.ts"
    source.write_text("export const answer: number = 42;\n", encoding="utf-8")

    assert not is_mpeg_transport_stream_file(source)
    assert isinstance(ParserRegistry().get_parser_for_file(source), TextParser)
    assert get_media_type(str(source), "markdown") is None


def test_real_transport_stream_keeps_video_parser(tmp_path: Path):
    source = tmp_path / "clip.ts"
    source.write_bytes(_transport_stream())

    assert is_mpeg_transport_stream_file(source)
    assert isinstance(ParserRegistry().get_parser_for_file(source), VideoParser)


def test_understanding_api_skips_typescript_but_accepts_transport_stream(
    tmp_path: Path, monkeypatch
):
    config = SimpleNamespace(parser_api=SimpleNamespace(enable=True, extensions=["ts"]))
    monkeypatch.setattr(
        "openviking_cli.utils.config.open_viking_config.get_openviking_config",
        lambda: config,
    )
    code_source = tmp_path / "component.ts"
    code_source.write_text("export default function main() {}\n", encoding="utf-8")
    video_source = tmp_path / "clip.ts"
    video_source.write_bytes(_transport_stream())
    router = ParserRouter(parser_registry=object())

    assert not router.should_use_understanding_api(code_source)
    assert router.should_use_understanding_api(video_source)
    assert router.should_use_understanding_api("https://example.com/media/clip.ts")
