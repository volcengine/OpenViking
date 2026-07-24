# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0

from pathlib import Path
from types import SimpleNamespace

from openviking.parse.parser_router import ParserRouter
from openviking.parse.parsers.media import detection as detection_module
from openviking.parse.parsers.media.detection import (
    AmbiguousMediaDetectorRegistry,
    AmbiguousMediaRule,
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


def test_detector_registry_dispatches_multiple_ambiguous_suffix_groups():
    detectors = AmbiguousMediaDetectorRegistry(
        (
            AmbiguousMediaRule(".alpha", "video", "video", "text", 4, lambda data: data == b"VID0"),
            AmbiguousMediaRule(".beta", "audio", "audio", "text", 4, lambda data: data == b"AUD0"),
        )
    )

    assert detectors.matches_bytes("sample.alpha", b"VID0 trailing bytes")
    assert not detectors.matches_bytes("sample.alpha", b"TEXT")
    assert detectors.matches_bytes("sample.beta", b"AUD0 trailing bytes")
    assert detectors.matches_bytes("sample.other", b"VID0") is None


def test_parser_registry_uses_registered_detector_for_future_suffix(tmp_path: Path, monkeypatch):
    detectors = AmbiguousMediaDetectorRegistry(
        (AmbiguousMediaRule(".ambvid", "video", "video", "text", 4, lambda data: data == b"VID0"),)
    )
    monkeypatch.setattr(detection_module, "AMBIGUOUS_MEDIA_DETECTORS", detectors)
    registry = ParserRegistry()
    registry._extension_map[".ambvid"] = "video"
    media_source = tmp_path / "clip.ambvid"
    media_source.write_bytes(b"VID0 payload")
    text_source = tmp_path / "notes.ambvid"
    text_source.write_text("not media", encoding="utf-8")

    assert isinstance(registry.get_parser_for_file(media_source), VideoParser)
    assert isinstance(registry.get_parser_for_file(text_source), TextParser)


def test_typescript_source_is_not_a_transport_stream(tmp_path: Path):
    source = tmp_path / "component.ts"
    source.write_text("export const answer: number = 42;\n", encoding="utf-8")

    assert not is_mpeg_transport_stream_file(source)
    assert isinstance(ParserRegistry().get_parser_for_file(source), TextParser)
    assert get_media_type(str(source), "markdown") is None


def test_printable_typescript_with_sync_byte_cadence_stays_text(tmp_path: Path):
    source = tmp_path / "generated.ts"
    content = bytearray(b" " * 800)
    content[:2] = b"/*"
    content[-2:] = b"*/"
    for packet_index in range(4):
        content[2 + packet_index * 188] = 0x47
    source.write_bytes(content)

    assert not is_mpeg_transport_stream_bytes(bytes(content))
    assert not is_mpeg_transport_stream_file(source)
    assert isinstance(ParserRegistry().get_parser_for_file(source), TextParser)


def test_real_transport_stream_keeps_video_parser(tmp_path: Path):
    source = tmp_path / "clip.ts"
    source.write_bytes(_transport_stream())

    assert is_mpeg_transport_stream_file(source)
    assert isinstance(ParserRegistry().get_parser_for_file(source), VideoParser)


def test_understanding_api_skips_typescript_but_accepts_transport_stream(
    tmp_path: Path, monkeypatch
):
    config = SimpleNamespace(
        parser_api=SimpleNamespace(
            enable=True,
            enable_feishu_url=False,
            extensions=["ts"],
        )
    )
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
