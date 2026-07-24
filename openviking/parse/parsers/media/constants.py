# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Constants for media parsers."""

# Image extensions supported by ImageParser
IMAGE_EXTENSIONS = [".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp", ".svg", ".tiff", ".tif", ".ico", ".dib", ".icns", ".sgi", ".jp2"]

# Audio extensions supported by AudioParser
AUDIO_EXTENSIONS = [".mp3", ".wav", ".ogg", ".flac", ".aac", ".m4a", ".opus", ".ac3"]

# Video extensions supported by VideoParser
VIDEO_EXTENSIONS = [".mp4", ".avi", ".mov", ".mkv", ".webm", ".flv", ".wmv", ".ts"]

# All media extensions combined
MEDIA_EXTENSIONS = set(IMAGE_EXTENSIONS + AUDIO_EXTENSIONS + VIDEO_EXTENSIONS)

MPEG_TS_SNIFF_BYTES = 64 * 1024
_MPEG_TS_PACKET_SIZES = (188, 192, 204)
_MPEG_TS_MIN_PACKETS = 4


def is_mpeg_ts(content: object) -> bool:
    """Return whether a bounded content sample contains valid MPEG-TS packets."""
    if not isinstance(content, bytes):
        return False
    return _has_mpeg_ts_packets(content[:MPEG_TS_SNIFF_BYTES])


def _has_mpeg_ts_packets(content: bytes) -> bool:
    for offset, byte in enumerate(content):
        if byte != 0x47:
            continue
        for packet_size in _MPEG_TS_PACKET_SIZES:
            headers = [
                _parse_mpeg_ts_header(content, offset + packet_size * index)
                for index in range(_MPEG_TS_MIN_PACKETS)
            ]
            if all(header is not None for header in headers) and _has_valid_continuity(
                headers
            ):
                return True
    return False


def _parse_mpeg_ts_header(
    content: bytes, offset: int
) -> tuple[int, int, bool, bool, bytes] | None:
    packet_end = offset + 188
    if packet_end > len(content) or content[offset] != 0x47:
        return None

    second, third, fourth = content[offset + 1 : offset + 4]
    if second & 0x80:
        return None

    adaptation_field_control = (fourth >> 4) & 0x03
    if adaptation_field_control == 0:
        return None

    discontinuity = False
    if adaptation_field_control in {2, 3}:
        adaptation_length = content[offset + 4]
        if adaptation_field_control == 2 and adaptation_length != 183:
            return None
        if adaptation_field_control == 3 and adaptation_length > 182:
            return None
        if adaptation_length:
            discontinuity = bool(content[offset + 5] & 0x80)

    pid = ((second & 0x1F) << 8) | third
    continuity_counter = fourth & 0x0F
    has_payload = adaptation_field_control in {1, 3}
    return (
        pid,
        continuity_counter,
        has_payload,
        discontinuity,
        content[offset:packet_end],
    )


def _has_valid_continuity(
    headers: list[tuple[int, int, bool, bool, bytes] | None],
) -> bool:
    last_by_pid: dict[int, tuple[int, bytes]] = {}
    for header in headers:
        if header is None:
            return False
        pid, counter, has_payload, discontinuity, packet = header
        previous = last_by_pid.get(pid)
        if previous is not None and not discontinuity:
            previous_counter, previous_packet = previous
            if has_payload:
                expected = (previous_counter + 1) & 0x0F
                if counter == previous_counter:
                    if packet != previous_packet:
                        return False
                elif counter != expected:
                    return False
            elif counter != previous_counter:
                return False
        last_by_pid[pid] = (counter, packet)
    return True
