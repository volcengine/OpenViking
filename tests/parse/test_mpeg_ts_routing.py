# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from openviking.parse import registry as registry_module
from openviking.parse.parsers.media import constants as media_constants
from openviking.parse.parsers.media import utils as media_utils
from openviking.parse.parsers.media.video import VideoParser
from openviking.storage.queuefs import semantic_processor as semantic_processor_module
from openviking.storage.queuefs.semantic_processor import SemanticProcessor


def _transport_packet(counter: int, payload_byte: int) -> bytes:
    return b"\x47\x40\x00" + bytes([0x10 | counter]) + bytes([payload_byte]) * 184


def _mpeg_ts_bytes(
    packet_size: int,
    *,
    counters: tuple[int, int, int, int] = (0, 1, 2, 3),
    payload_bytes: tuple[int, int, int, int] = (1, 2, 3, 4),
) -> bytes:
    packets = []
    for counter, payload_byte in zip(counters, payload_bytes):
        transport_packet = _transport_packet(counter, payload_byte)
        if packet_size == 188:
            packet = transport_packet
        elif packet_size == 192:
            packet = b"\x00\x00\x00\x00" + transport_packet
        elif packet_size == 204:
            packet = transport_packet + b"\x00" * 16
        else:  # pragma: no cover - test helper misuse
            raise ValueError(packet_size)
        packets.append(packet)
    return b"".join(packets)


def _typescript_with_bare_sync_cadence(packet_size: int) -> bytes:
    source = bytearray(b"//" + b" " * (packet_size * 4 + 8))
    for index in range(4):
        source[2 + packet_size * index] = 0x47
    return bytes(source)


@pytest.mark.parametrize("packet_size", [188, 192, 204])
def test_mpeg_ts_accepts_four_valid_packets(packet_size):
    assert media_constants.is_mpeg_ts(_mpeg_ts_bytes(packet_size))


@pytest.mark.parametrize("packet_size", [188, 192, 204])
def test_mpeg_ts_rejects_typescript_with_bare_sync_cadence(packet_size):
    assert not media_constants.is_mpeg_ts(
        _typescript_with_bare_sync_cadence(packet_size)
    )


def test_mpeg_ts_rejects_invalid_adaptation_control():
    content = bytearray(_mpeg_ts_bytes(188))
    for offset in range(0, len(content), 188):
        content[offset + 3] &= 0x0F

    assert not media_constants.is_mpeg_ts(bytes(content))


def test_mpeg_ts_rejects_invalid_continuity_progression():
    assert not media_constants.is_mpeg_ts(
        _mpeg_ts_bytes(188, counters=(0, 7, 8, 9))
    )


def test_mpeg_ts_allows_only_identical_duplicate_packets():
    duplicate = _transport_packet(0, 1)
    valid = duplicate + duplicate + _transport_packet(1, 2) + _transport_packet(2, 3)
    invalid = (
        duplicate
        + _transport_packet(0, 9)
        + _transport_packet(1, 2)
        + _transport_packet(2, 3)
    )

    assert media_constants.is_mpeg_ts(valid)
    assert not media_constants.is_mpeg_ts(invalid)


def test_mpeg_ts_scans_only_bounded_prefix():
    packets = _mpeg_ts_bytes(188)
    inside_prefix = (
        b"x" * (media_constants.MPEG_TS_SNIFF_BYTES - len(packets)) + packets
    )
    outside_prefix = b"x" * media_constants.MPEG_TS_SNIFF_BYTES + packets

    assert media_constants.is_mpeg_ts(inside_prefix)
    assert not media_constants.is_mpeg_ts(outside_prefix)


def test_get_media_type_requires_content_evidence_for_ts():
    source = b"export const greeting: string = 'hello';"

    assert (
        media_utils.get_media_type(
            "viking://resources/video/2026/07/24/broadcast.ts", None
        )
        is None
    )
    assert media_utils.get_media_type("download.ts", "video", content=source) is None
    assert (
        media_utils.get_media_type(
            "download.ts", None, content=_mpeg_ts_bytes(188)
        )
        == "video"
    )
    assert media_utils.get_media_type("validated.ts", "video") == "video"


@pytest.mark.parametrize("relative_path", ["source.ts", "video/source.ts"])
def test_parser_registry_routes_ordinary_typescript_to_text_fallback(
    tmp_path, relative_path
):
    source_path = tmp_path / relative_path
    source_path.parent.mkdir(parents=True, exist_ok=True)
    source_path.write_text("export const answer: number = 42;", encoding="utf-8")
    registry = registry_module.ParserRegistry(register_optional=False)

    assert registry.get_parser_for_file(source_path) is None


def test_parser_registry_routes_actual_mpeg_ts_to_video(tmp_path):
    source_path = tmp_path / "broadcast.ts"
    source_path.write_bytes(_mpeg_ts_bytes(188))
    registry = registry_module.ParserRegistry(register_optional=False)

    assert isinstance(registry.get_parser_for_file(source_path), VideoParser)


def test_parser_registry_does_not_read_past_sniff_limit(tmp_path):
    source_path = tmp_path / "late-broadcast.ts"
    source_path.write_bytes(
        b"x" * media_constants.MPEG_TS_SNIFF_BYTES + _mpeg_ts_bytes(188)
    )
    registry = registry_module.ParserRegistry(register_optional=False)

    assert registry.get_parser_for_file(source_path) is None


@pytest.mark.asyncio
async def test_video_parser_validates_and_accepts_mpeg_ts(tmp_path, monkeypatch):
    source_path = tmp_path / "broadcast.ts"
    source_path.write_bytes(_mpeg_ts_bytes(192))
    fs = SimpleNamespace(
        create_temp_uri=lambda: "viking://temp/test",
        mkdir=AsyncMock(),
        write_file_bytes=AsyncMock(),
    )
    monkeypatch.setattr(
        "openviking.storage.viking_fs.get_viking_fs",
        lambda: fs,
    )

    result = await VideoParser().parse(source_path)

    assert result.source_format == "video"
    assert result.meta["format"] == "ts"


@pytest.mark.asyncio
async def test_video_parser_rejects_typescript(tmp_path, monkeypatch):
    source_path = tmp_path / "source.ts"
    source_path.write_text("export const answer = 42;", encoding="utf-8")
    fs = SimpleNamespace(
        create_temp_uri=lambda: "viking://temp/test",
        mkdir=AsyncMock(),
        write_file_bytes=AsyncMock(),
    )
    monkeypatch.setattr(
        "openviking.storage.viking_fs.get_viking_fs",
        lambda: fs,
    )

    with pytest.raises(ValueError, match="Invalid video file"):
        await VideoParser().parse(source_path)


@pytest.mark.asyncio
async def test_semantic_routing_keeps_typescript_under_video_directory_as_text(
    monkeypatch,
):
    processor = SemanticProcessor()
    text_summary = AsyncMock(
        return_value={"name": "source.ts", "summary": "TypeScript source"}
    )
    video_summary = AsyncMock()
    fs = SimpleNamespace(
        read=AsyncMock(return_value=b"export const answer = 42;")
    )
    monkeypatch.setattr(processor, "_generate_text_summary", text_summary)
    monkeypatch.setattr(
        semantic_processor_module, "generate_video_summary", video_summary
    )
    monkeypatch.setattr(semantic_processor_module, "get_viking_fs", lambda: fs)

    result = await processor._generate_single_file_summary(
        "viking://resources/projects/video/source.ts"
    )

    assert result == {"name": "source.ts", "summary": "TypeScript source"}
    fs.read.assert_awaited_once_with(
        "viking://resources/projects/video/source.ts",
        offset=0,
        size=media_constants.MPEG_TS_SNIFF_BYTES,
        ctx=None,
    )
    text_summary.assert_awaited_once()
    video_summary.assert_not_awaited()


@pytest.mark.asyncio
async def test_semantic_routing_sniffs_actual_mpeg_ts(monkeypatch):
    processor = SemanticProcessor()
    text_summary = AsyncMock()
    video_summary = AsyncMock(
        return_value={"name": "broadcast.ts", "summary": ""}
    )
    fs = SimpleNamespace(read=AsyncMock(return_value=_mpeg_ts_bytes(204)))
    monkeypatch.setattr(processor, "_generate_text_summary", text_summary)
    monkeypatch.setattr(
        semantic_processor_module, "generate_video_summary", video_summary
    )
    monkeypatch.setattr(semantic_processor_module, "get_viking_fs", lambda: fs)

    result = await processor._generate_single_file_summary(
        "viking://resources/imported/broadcast.ts"
    )

    assert result == {"name": "broadcast.ts", "summary": ""}
    fs.read.assert_awaited_once_with(
        "viking://resources/imported/broadcast.ts",
        offset=0,
        size=media_constants.MPEG_TS_SNIFF_BYTES,
        ctx=None,
    )
    video_summary.assert_awaited_once()
    text_summary.assert_not_awaited()
