# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Content detection for file extensions shared by code and media formats."""

from pathlib import Path
from typing import Union

MPEG_TS_EXTENSION = ".ts"
_MPEG_TS_PACKET_SIZES = (188, 192, 204)
_MIN_SYNC_PACKETS = 4
MPEG_TS_SNIFF_BYTES = max(_MPEG_TS_PACKET_SIZES) * (_MIN_SYNC_PACKETS + 1)


def is_mpeg_transport_stream_bytes(data: bytes) -> bool:
    """Return whether *data* starts like an MPEG transport stream.

    MPEG-TS packets use a sync byte (0x47) at a fixed 188-byte cadence. Some
    containers add a 4-byte prefix or parity bytes, yielding 192- or 204-byte
    packets. Requiring four consecutive sync bytes avoids classifying ordinary
    TypeScript source as video based on the ambiguous ``.ts`` suffix alone.
    """

    if len(data) < min(_MPEG_TS_PACKET_SIZES) * _MIN_SYNC_PACKETS:
        return False

    for packet_size in _MPEG_TS_PACKET_SIZES:
        max_offset = min(packet_size, len(data))
        for offset in range(max_offset):
            if all(
                offset + packet_index * packet_size < len(data)
                and data[offset + packet_index * packet_size] == 0x47
                for packet_index in range(_MIN_SYNC_PACKETS)
            ):
                return True
    return False


def is_mpeg_transport_stream_file(source: Union[str, Path]) -> bool:
    """Inspect a bounded prefix of a local file for an MPEG-TS signature."""

    path = Path(source)
    if path.suffix.lower() != MPEG_TS_EXTENSION or not path.is_file():
        return False

    try:
        with path.open("rb") as file_obj:
            return is_mpeg_transport_stream_bytes(file_obj.read(MPEG_TS_SNIFF_BYTES))
    except OSError:
        return False
