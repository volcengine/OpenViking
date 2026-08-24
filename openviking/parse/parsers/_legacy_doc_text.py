# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Best-effort text extraction for legacy OLE2 Word documents."""

from __future__ import annotations

import struct
from pathlib import Path

_MAX_STREAM_SIZE = 50 * 1024 * 1024
_MAX_CCP_TEXT = 10_000_000
OLE2_SIGNATURE = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
ZIP_SIGNATURES = (b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08")


def has_ole2_signature(path: Path) -> bool:
    with Path(path).open("rb") as file_obj:
        return file_obj.read(8) == OLE2_SIGNATURE


def has_zip_signature(path: Path) -> bool:
    with Path(path).open("rb") as file_obj:
        return file_obj.read(4) in ZIP_SIGNATURES


def extract_legacy_doc_text(path: Path) -> str:
    """Extract text from a legacy .doc file, falling back to raw text runs."""
    import olefile

    path = Path(path)
    try:
        ole = olefile.OleFileIO(str(path))
    except Exception:
        return _fallback_extract(path)

    try:
        return _extract_from_ole(ole)
    except Exception:
        return _fallback_extract(path)
    finally:
        ole.close()


def _read_ole_stream(ole, stream_name: str) -> bytes:
    stream = ole.openstream(stream_name)
    data = stream.read(_MAX_STREAM_SIZE + 1)
    if len(data) > _MAX_STREAM_SIZE:
        raise ValueError(f"OLE stream '{stream_name}' exceeds {_MAX_STREAM_SIZE} bytes")
    return data


def _extract_from_ole(ole) -> str:
    if not ole.exists("WordDocument"):
        raise ValueError("No WordDocument stream found")

    word_doc = _read_ole_stream(ole, "WordDocument")
    if len(word_doc) < 0x01A8:
        raise ValueError(f"WordDocument stream too small ({len(word_doc)} bytes)")

    nfib = struct.unpack_from("<H", word_doc, 0x0002)[0]
    if nfib < 0x00C1:
        raise ValueError(f"Unsupported Word version (nFib=0x{nfib:04X}), need Word 97+")

    flags = struct.unpack_from("<H", word_doc, 0x000A)[0]
    table_stream_name = "1Table" if flags & 0x0200 else "0Table"
    ccp_text = struct.unpack_from("<i", word_doc, 0x004C)[0]
    if ccp_text <= 0:
        raise ValueError("ccpText is zero or negative")
    ccp_text = min(ccp_text, _MAX_CCP_TEXT)

    if not ole.exists(table_stream_name):
        raise ValueError(f"Table stream '{table_stream_name}' not found")
    table_data = _read_ole_stream(ole, table_stream_name)

    fc_clx = struct.unpack_from("<i", word_doc, 0x01A2)[0]
    lcb_clx = struct.unpack_from("<i", word_doc, 0x01A6)[0]
    if fc_clx <= 0 or lcb_clx <= 0 or fc_clx + lcb_clx > len(table_data):
        return _simple_text_extract(word_doc, ccp_text)

    return _extract_via_clx(word_doc, table_data, fc_clx, lcb_clx, ccp_text)


def _simple_text_extract(word_doc: bytes, ccp_text: int) -> str:
    text_start = 0x0800
    if text_start >= len(word_doc):
        raise ValueError("WordDocument stream too small for text extraction")

    if ccp_text * 2 + text_start <= len(word_doc):
        end = text_start + ccp_text * 2
        text = word_doc[text_start:end].decode("utf-16-le", errors="replace")
        if sum(1 for c in text[:200] if c.isprintable() or c in "\n\r\t") > len(text[:200]) * 0.5:
            return _clean_word_text(text)

    end = min(text_start + ccp_text, len(word_doc))
    return _clean_word_text(_decode_cp1252(word_doc[text_start:end]))


def _extract_via_clx(
    word_doc: bytes,
    table_data: bytes,
    fc_clx: int,
    lcb_clx: int,
    ccp_text: int,
) -> str:
    clx = table_data[fc_clx : fc_clx + lcb_clx]
    pos = 0
    text_parts: list[str] = []
    chars_extracted = 0

    while pos < len(clx) and clx[pos] == 0x01:
        if pos + 3 > len(clx):
            break
        cb = struct.unpack_from("<H", clx, pos + 1)[0]
        advance = 3 + cb
        if advance <= 0:
            break
        pos += advance

    if pos >= len(clx) or clx[pos] != 0x02:
        return _simple_text_extract(word_doc, ccp_text)

    pos += 1
    if pos + 4 > len(clx):
        return _simple_text_extract(word_doc, ccp_text)

    lcb_pcd = struct.unpack_from("<I", clx, pos)[0]
    pos += 4
    pcd_start = pos
    pcd_end = pos + lcb_pcd
    if pcd_end > len(clx):
        return _simple_text_extract(word_doc, ccp_text)

    n_pieces = (lcb_pcd - 4) // 12
    if n_pieces <= 0:
        return _simple_text_extract(word_doc, ccp_text)

    cps = []
    for i in range(n_pieces + 1):
        offset = pcd_start + i * 4
        if offset + 4 > len(clx):
            break
        cps.append(struct.unpack_from("<I", clx, offset)[0])

    pcd_array_start = pcd_start + (n_pieces + 1) * 4
    for i in range(min(n_pieces, len(cps) - 1)):
        if chars_extracted >= ccp_text:
            break
        pcd_offset = pcd_array_start + i * 8
        if pcd_offset + 8 > len(clx):
            break

        fc_value = struct.unpack_from("<I", clx, pcd_offset + 2)[0]
        piece_char_count = cps[i + 1] - cps[i]
        is_compressed = bool(fc_value & 0x40000000)
        fc_real = fc_value & 0x3FFFFFFF

        if is_compressed:
            byte_offset = fc_real // 2
            byte_end = byte_offset + piece_char_count
            if byte_end <= len(word_doc):
                text_parts.append(_decode_cp1252(word_doc[byte_offset:byte_end]))
        else:
            byte_offset = fc_real
            byte_end = byte_offset + piece_char_count * 2
            if byte_end <= len(word_doc):
                text_parts.append(word_doc[byte_offset:byte_end].decode("utf-16-le", errors="replace"))

        chars_extracted += piece_char_count

    result = _clean_word_text("".join(text_parts))
    if not result.strip():
        return _simple_text_extract(word_doc, ccp_text)
    return result


def _decode_cp1252(data: bytes) -> str:
    return data.decode("cp1252", errors="replace")


def _clean_word_text(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = text.replace("\x07", "\t").replace("\x0b", "\n").replace("\x0c", "\n\n")
    return text


def _fallback_extract(path: Path) -> str:
    raw = Path(path).read_bytes()[:_MAX_STREAM_SIZE]

    try:
        decoded = raw.decode("utf-16-le", errors="ignore")
        lines = _printable_runs(decoded)
        text = "\n".join(lines)
        if len(text) > 50:
            return text
    except Exception:
        pass

    return "\n".join(_printable_runs(raw.decode("cp1252", errors="replace")))


def _printable_runs(text: str) -> list[str]:
    lines: list[str] = []
    current: list[str] = []
    for ch in text:
        if ch.isprintable() or ch in "\n\t":
            current.append(ch)
        else:
            if len(current) > 3:
                lines.append("".join(current))
            current = []
    if current and len(current) > 3:
        lines.append("".join(current))
    return lines
