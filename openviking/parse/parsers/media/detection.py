# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Content detection for file extensions shared by code and media formats."""

from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Callable, Iterable, Mapping, Optional, Union

MPEG_TS_EXTENSION = ".ts"
_MPEG_TS_PACKET_SIZES = (188, 192, 204)
_MIN_SYNC_PACKETS = 4
MPEG_TS_SNIFF_BYTES = max(_MPEG_TS_PACKET_SIZES) * (_MIN_SYNC_PACKETS + 1)


@dataclass(frozen=True)
class AmbiguousMediaRule:
    """Describe how one ambiguous suffix is routed using bounded content sniffing."""

    extension: str
    media_type: str
    parser_name: str
    fallback_parser_name: str
    sniff_bytes: int
    matcher: Callable[[bytes], bool]

    def __post_init__(self) -> None:
        normalized = self.extension.lower()
        if not normalized.startswith(".") or len(normalized) == 1:
            raise ValueError("ambiguous media extensions must start with '.'")
        if self.sniff_bytes <= 0:
            raise ValueError("ambiguous media sniff_bytes must be positive")
        object.__setattr__(self, "extension", normalized)


class AmbiguousMediaDetectorRegistry:
    """Immutable suffix-to-detector registry shared by all parser entry points."""

    def __init__(self, rules: Iterable[AmbiguousMediaRule]):
        by_extension = {}
        for rule in rules:
            if rule.extension in by_extension:
                raise ValueError(f"duplicate ambiguous media extension: {rule.extension}")
            by_extension[rule.extension] = rule
        self._rules: Mapping[str, AmbiguousMediaRule] = MappingProxyType(by_extension)

    def rule_for(self, source: Union[str, Path]) -> Optional[AmbiguousMediaRule]:
        return self._rules.get(Path(source).suffix.lower())

    def matches_bytes(self, source: Union[str, Path], data: bytes) -> Optional[bool]:
        """Return a media match, or ``None`` when the suffix is not ambiguous."""

        rule = self.rule_for(source)
        return None if rule is None else rule.matcher(data[: rule.sniff_bytes])

    def matches_file(self, source: Union[str, Path]) -> Optional[bool]:
        """Inspect a bounded local prefix, or return ``None`` for ordinary suffixes."""

        path = Path(source)
        rule = self.rule_for(path)
        if rule is None:
            return None
        if not path.is_file():
            return False
        try:
            with path.open("rb") as file_obj:
                return rule.matcher(file_obj.read(rule.sniff_bytes))
        except OSError:
            return False


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


AMBIGUOUS_MEDIA_DETECTORS = AmbiguousMediaDetectorRegistry(
    (
        AmbiguousMediaRule(
            extension=MPEG_TS_EXTENSION,
            media_type="video",
            parser_name="video",
            fallback_parser_name="text",
            sniff_bytes=MPEG_TS_SNIFF_BYTES,
            matcher=is_mpeg_transport_stream_bytes,
        ),
    )
)


def get_ambiguous_media_rule(
    source: Union[str, Path],
) -> Optional[AmbiguousMediaRule]:
    """Return the registered content detector for *source*'s suffix."""

    return AMBIGUOUS_MEDIA_DETECTORS.rule_for(source)


def matches_ambiguous_media_bytes(source: Union[str, Path], data: bytes) -> Optional[bool]:
    """Apply the suffix's detector, or return ``None`` for an ordinary suffix."""

    return AMBIGUOUS_MEDIA_DETECTORS.matches_bytes(source, data)


def matches_ambiguous_media_file(source: Union[str, Path]) -> Optional[bool]:
    """Inspect a bounded local prefix for a registered ambiguous suffix."""

    return AMBIGUOUS_MEDIA_DETECTORS.matches_file(source)


def is_mpeg_transport_stream_file(source: Union[str, Path]) -> bool:
    """Inspect a bounded prefix of a local file for an MPEG-TS signature."""

    path = Path(source)
    return path.suffix.lower() == MPEG_TS_EXTENSION and bool(matches_ambiguous_media_file(path))
