# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""
Legacy Word document (.doc) parser for OpenViking.

Extracts text from OLE2 compound binary .doc files using olefile,
then delegates to MarkdownParser for tree structure creation.
"""

import struct
from pathlib import Path
from typing import List, Optional, Union

from openviking.parse.base import ParseResult
from openviking.parse.parsers.base_parser import BaseParser
from openviking_cli.utils.config.parser_config import ParserConfig
from openviking_cli.utils.logger import get_logger

logger = get_logger(__name__)


# Max stream size to read (50MB) — prevents DoS from crafted files
_MAX_STREAM_SIZE = 50 * 1024 * 1024
# Max character count sanity cap for ccpText
_MAX_CCP_TEXT = 10_000_000


class LegacyDocParser(BaseParser):
    """
    Legacy .doc (OLE2 binary) parser.

    Extracts text content from Word 97-2003 (.doc) files using olefile
    to read the WordDocument and table streams, then delegates to
    MarkdownParser for tree structure.
    """

    def __init__(self, config: Optional[ParserConfig] = None):
        from openviking.parse.parsers.markdown import MarkdownParser

        self._md_parser = MarkdownParser(config=config)
        self.config = config or ParserConfig()

    @property
    def supported_extensions(self) -> List[str]:
        return [".doc"]

    async def parse(self, source: Union[str, Path], instruction: str = "", **kwargs) -> ParseResult:
        """Parse legacy .doc file."""
        path = Path(source)

        if path.exists():
            text = self._extract_text(path)
            result = await self._md_parser.parse_content(
                text, source_path=str(path), instruction=instruction, **kwargs
            )
        else:
            result = await self._md_parser.parse_content(
                str(source), instruction=instruction, **kwargs
            )
        result.source_format = "doc"
        result.parser_name = "LegacyDocParser"
        return result

    async def parse_content(
        self, content: str, source_path: Optional[str] = None, instruction: str = "", **kwargs
    ) -> ParseResult:
        """Parse content string — delegates to MarkdownParser."""
        result = await self._md_parser.parse_content(
            content, source_path, instruction=instruction, **kwargs
        )
        result.source_format = "doc"
        result.parser_name = "LegacyDocParser"
        return result

    def _extract_text(self, path: Path) -> str:
        """
        Extract text from a legacy .doc OLE2 file.

        Reads the WordDocument stream and uses the FIB (File Information Block)
        to locate text in the document body. Falls back to raw byte scanning
        if structured extraction fails.
        """
        import olefile

        try:
            ole = olefile.OleFileIO(str(path))
        except Exception as e:
            logger.warning(f"Failed to open .doc as OLE file: {e}")
            return self._fallback_extract(path)

        try:
            return self._extract_from_ole(ole)
        except Exception as e:
            logger.warning(f"Structured OLE extraction failed, using fallback: {e}")
            return self._fallback_extract(path)
        finally:
            ole.close()

    @staticmethod
    def _read_ole_stream(ole, stream_name: str) -> bytes:
        """Read an OLE stream with size cap to prevent DoS."""
        stream = ole.openstream(stream_name)
        data = stream.read(_MAX_STREAM_SIZE + 1)
        if len(data) > _MAX_STREAM_SIZE:
            raise ValueError(f"OLE stream '{stream_name}' exceeds {_MAX_STREAM_SIZE} bytes")
        return data

    def _extract_from_ole(self, ole) -> str:
        """
        Extract text from OLE streams using the Word Binary File Format.

        Reads the FIB to determine if text is stored as UTF-16 or compressed
        (CP1252), then extracts the document body text from the appropriate
        stream (WordDocument or table stream).
        """
        if not ole.exists("WordDocument"):
            raise ValueError("No WordDocument stream found")

        word_doc = self._read_ole_stream(ole, "WordDocument")

        # Minimum FIB size: need at least 0x01A8 bytes for Word 97+ FIB fields
        if len(word_doc) < 0x01A8:
            raise ValueError(f"WordDocument stream too small ({len(word_doc)} bytes)")

        # Check FIB version (nFib at offset 0x0002) — require Word 97+ (0x00C1+)
        nfib = struct.unpack_from("<H", word_doc, 0x0002)[0]
        if nfib < 0x00C1:
            raise ValueError(f"Unsupported Word version (nFib=0x{nfib:04X}), need Word 97+")

        # Read FIB flags at offset 0x000A
        # Bit 9: table stream selector (0Table vs 1Table)
        # Bit 8: fComplex (complex fast-saved format — does not affect encoding)
        flags = struct.unpack_from("<H", word_doc, 0x000A)[0]
        is_1table = bool(flags & 0x0200)
        table_stream_name = "1Table" if is_1table else "0Table"

        # Read ccpText (character count of main document text) at FIB offset 0x004C
        ccp_text = struct.unpack_from("<i", word_doc, 0x004C)[0]

        if ccp_text <= 0:
            raise ValueError("ccpText is zero or negative")
        # Cap ccpText to prevent memory exhaustion from crafted files
        ccp_text = min(ccp_text, _MAX_CCP_TEXT)

        # Read the Clx from the table stream to find text positions
        if not ole.exists(table_stream_name):
            raise ValueError(f"Table stream '{table_stream_name}' not found")
        table_data = self._read_ole_stream(ole, table_stream_name)

        # fcClx offset in FIB (Word 97+ standard location)
        fc_clx = struct.unpack_from("<i", word_doc, 0x01A2)[0]
        lcb_clx = struct.unpack_from("<i", word_doc, 0x01A6)[0]

        if fc_clx <= 0 or lcb_clx <= 0 or fc_clx + lcb_clx > len(table_data):
            return self._simple_text_extract(word_doc, ccp_text)

        return self._extract_via_clx(word_doc, table_data, fc_clx, lcb_clx, ccp_text)

    def _simple_text_extract(self, word_doc: bytes, ccp_text: int) -> str:
        """
        Simple text extraction using FIB text offset.

        The main document text starts at offset 0x0800 in the WordDocument stream
        for most Word 97+ files. Tries UTF-16LE first; falls back to CP1252 if
        the stream is too small for UTF-16.
        """
        text_start = 0x0800  # Standard text start offset

        if text_start >= len(word_doc):
            raise ValueError("WordDocument stream too small for text extraction")

        # Try UTF-16LE first (2 bytes per char)
        if ccp_text * 2 + text_start <= len(word_doc):
            end = text_start + ccp_text * 2
            raw = word_doc[text_start:end]
            text = raw.decode("utf-16-le", errors="replace")
            # Sanity: if mostly printable, it's likely correct
            if (
                sum(1 for c in text[:200] if c.isprintable() or c in "\n\r\t")
                > len(text[:200]) * 0.5
            ):
                return self._clean_word_text(text)

        # Fall back to CP1252 single-byte
        end = min(text_start + ccp_text, len(word_doc))
        raw = word_doc[text_start:end]
        return self._clean_word_text(self._decode_cp1252(raw))

    def _extract_via_clx(
        self,
        word_doc: bytes,
        table_data: bytes,
        fc_clx: int,
        lcb_clx: int,
        ccp_text: int,
    ) -> str:
        """
        Extract text using the Clx (piece table) structure.

        The Clx contains a PiecePLC that maps character positions to file offsets,
        allowing reconstruction of the document text even when pieces are scattered.
        """
        clx = table_data[fc_clx : fc_clx + lcb_clx]
        pos = 0
        text_parts = []
        chars_extracted = 0

        # Skip any Grpprl (type 0x01) entries in the Clx
        while pos < len(clx) and clx[pos] == 0x01:
            if pos + 3 > len(clx):
                break
            cb = struct.unpack_from("<H", clx, pos + 1)[0]
            advance = 3 + cb
            if advance <= 0:
                break  # Prevent infinite loop on zero-length Grpprl
            pos += advance

        # Now we should be at the Pcdt (type 0x02)
        if pos >= len(clx) or clx[pos] != 0x02:
            return self._simple_text_extract(word_doc, ccp_text)

        pos += 1  # skip type byte
        if pos + 4 > len(clx):
            return self._simple_text_extract(word_doc, ccp_text)

        lcb_pcd = struct.unpack_from("<I", clx, pos)[0]
        pos += 4

        # PLC structure: (n+1) CPs followed by n PCDs (each 8 bytes)
        pcd_start = pos
        pcd_end = pos + lcb_pcd

        if pcd_end > len(clx):
            return self._simple_text_extract(word_doc, ccp_text)

        # Calculate number of pieces: (lcb_pcd - 4) / (4 + 8) per piece,
        # but CPs are (n+1)*4 bytes + n*8 bytes = lcb_pcd
        # So: 4*(n+1) + 8*n = lcb_pcd → 12n + 4 = lcb_pcd → n = (lcb_pcd - 4) / 12
        n_pieces = (lcb_pcd - 4) // 12
        if n_pieces <= 0:
            return self._simple_text_extract(word_doc, ccp_text)

        # Read character positions (n+1 values)
        cps = []
        for i in range(n_pieces + 1):
            offset = pcd_start + i * 4
            if offset + 4 > len(clx):
                break
            cps.append(struct.unpack_from("<I", clx, offset)[0])

        # Read piece descriptors (start after the CPs)
        pcd_array_start = pcd_start + (n_pieces + 1) * 4

        for i in range(min(n_pieces, len(cps) - 1)):
            if chars_extracted >= ccp_text:
                break

            pcd_offset = pcd_array_start + i * 8
            if pcd_offset + 8 > len(clx):
                break

            # PCD: 2 bytes flags, 4 bytes fc, 2 bytes prm
            fc_value = struct.unpack_from("<I", clx, pcd_offset + 2)[0]

            piece_cp_start = cps[i]
            piece_cp_end = cps[i + 1]
            piece_char_count = piece_cp_end - piece_cp_start

            # Bit 30 of fc indicates compressed (CP1252) text
            is_compressed = bool(fc_value & 0x40000000)
            fc_real = fc_value & 0x3FFFFFFF

            if is_compressed:
                # CP1252: fc_real / 2 is the byte offset
                byte_offset = fc_real // 2
                byte_end = byte_offset + piece_char_count
                if byte_end <= len(word_doc):
                    raw = word_doc[byte_offset:byte_end]
                    text_parts.append(self._decode_cp1252(raw))
                else:
                    logger.warning(
                        f"Piece {i} extends beyond stream ({byte_end} > {len(word_doc)})"
                    )
            else:
                # UTF-16LE
                byte_offset = fc_real
                byte_end = byte_offset + piece_char_count * 2
                if byte_end <= len(word_doc):
                    raw = word_doc[byte_offset:byte_end]
                    text_parts.append(raw.decode("utf-16-le", errors="replace"))
                else:
                    logger.warning(
                        f"Piece {i} extends beyond stream ({byte_end} > {len(word_doc)})"
                    )

            chars_extracted += piece_char_count

        result = self._clean_word_text("".join(text_parts))
        if not result.strip():
            return self._simple_text_extract(word_doc, ccp_text)
        return result

    @staticmethod
    def _decode_cp1252(data: bytes) -> str:
        """Decode CP1252 bytes to string."""
        return data.decode("cp1252", errors="replace")

    @staticmethod
    def _clean_word_text(text: str) -> str:
        """Normalize Word control characters to readable equivalents."""
        text = text.replace("\r\n", "\n").replace("\r", "\n")
        # \x07 = cell/row end, \x0B = soft line break, \x0C = section break
        text = text.replace("\x07", "\t").replace("\x0b", "\n").replace("\x0c", "\n\n")
        return text

    def _fallback_extract(self, path: Path) -> str:
        """
        Last-resort text extraction by scanning raw bytes for readable text runs.

        Tries UTF-16LE decoding first (common in .doc), then falls back to CP1252.
        """
        # Cap read size to prevent DoS from large files
        with open(path, "rb") as f:
            raw = f.read(_MAX_STREAM_SIZE)

        # Try to find UTF-16LE text (every other byte is often 0x00 for ASCII)
        try:
            decoded = raw.decode("utf-16-le", errors="ignore")
            # Filter to printable text runs
            lines = []
            current = []
            for ch in decoded:
                if ch.isprintable() or ch in "\n\t":
                    current.append(ch)
                else:
                    if len(current) > 3:
                        lines.append("".join(current))
                    current = []
            if current and len(current) > 3:
                lines.append("".join(current))
            text = "\n".join(lines)
            if len(text) > 50:
                return text
        except Exception:
            pass

        # Fall back to CP1252
        text = raw.decode("cp1252", errors="replace")
        lines = []
        current = []
        for ch in text:
            if ch.isprintable() or ch in "\n\t":
                current.append(ch)
            else:
                if len(current) > 3:
                    lines.append("".join(current))
                current = []
        if current and len(current) > 3:
            lines.append("".join(current))
        return "\n".join(lines)
