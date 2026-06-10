# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Shared resource-name resolution for the media parsers (image / audio / video).

A staged upload reaches a parser as a temp file named ``upload_<uuid>.<ext>``.
The caller's real name arrives via the ``resource_name`` / ``source_name``
kwargs (the markdown parser already honors these, and ``media_processor`` passes
``resource_name`` through). Centralizing the resolution keeps the three media
parsers in lockstep so the resource's filename, URI and title reflect the real
upload instead of the internal temp id.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Tuple

from openviking.parse.parsers.media.constants import MEDIA_EXTENSIONS


def resolve_media_names(file_path: Path, ext: str, **kwargs: Any) -> Tuple[str, str, str]:
    """Resolve ``(display_stem, path_stem, original_filename)`` for a media resource.

    Honors a caller-supplied ``resource_name`` / ``source_name``, reducing it to
    its stem only when the trailing suffix is a known media extension (so a
    filename-like value such as ``"photo.png"`` does not double its extension,
    while a name that merely contains a dot like ``"meeting.v1"`` is preserved).
    When neither is supplied, falls back to the temp file name with behavior
    byte-identical to the historical inline logic.

    Args:
        file_path: the temp (or source) file path the parser is reading.
        ext: the resource extension to use (the parser passes ``file_path.suffix``).
        **kwargs: parser kwargs; ``resource_name`` / ``source_name`` are consulted.

    Returns:
        display_stem: human-readable name for the title / metadata fields.
        path_stem: ``display_stem`` with spaces -> underscores, for URI segments.
        original_filename: the resource's internal filename.
    """
    explicit_name = kwargs.get("resource_name") or kwargs.get("source_name")
    if explicit_name:
        # Strip a trailing extension only when it is a known media extension, so
        # a filename-like value ("photo.png") does not double its extension,
        # while a name that merely contains a dot ("meeting.v1") is preserved.
        candidate = Path(explicit_name)
        display_stem = (
            candidate.stem if candidate.suffix.lower() in MEDIA_EXTENSIONS else explicit_name
        )
        path_stem = display_stem.replace(" ", "_")
        original_filename = f"{path_stem}{ext}"
    else:
        display_stem = file_path.stem
        path_stem = file_path.stem.replace(" ", "_")
        # Preserve the historical fallback exactly: replace spaces anywhere in
        # the basename (including inside the extension), not just in the stem.
        original_filename = file_path.name.replace(" ", "_")
    return display_stem, path_stem, original_filename
