# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

"""Strip the blocks an agent host wraps around a user turn, keeping the user's own words.

A host such as a chat bridge or a coding CLI often hands over a user turn that is mostly
its own scaffolding: routing notes, reminders, quoted history. Stored or searched whole,
that scaffolding crowds out the turn it surrounds. In the flattened text a tag the host
injected looks exactly like the same tag the user typed while talking about it, so this
module only removes a block where a host puts one and otherwise leaves the text alone:

* ``extract`` profiles keep the body of one tag (e.g. ``<user_message>``). Every tag
  inside that body is the user's and survives verbatim.
* strip-only profiles remove whole-line blocks from the start and end of the text only.
  A tag quoted mid-sentence, in inline code or inside a fenced block is never touched.

A turn that does not match, or whose structure is broken (an unclosed tag), comes back
unchanged. A turn that matches and leaves nothing behind comes back empty, which is how
callers tell "host-only turn" from "no envelope".
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Pattern, Sequence, Tuple

_FLAGS = re.IGNORECASE


def _open_re(tag: str) -> str:
    return r"<" + re.escape(tag) + r"(?:\s[^>]*)?>"


def _close_re(tag: str) -> str:
    return r"</" + re.escape(tag) + r"\s*>"


@dataclass(frozen=True)
class HostEnvelope:
    """One host's wrapping, described by tag names.

    ``detect_any``: the envelope applies only if one of these tags opens somewhere.
    ``strip``: blocks to remove. With ``extract`` they are skipped on the way to the body,
    because a host note may quote the body tag literally; without it they are removed
    from the edges of the text.
    ``extract``: tag whose body is the user's turn; everything outside it is dropped.
    """

    name: str
    detect_any: Tuple[str, ...]
    strip: Tuple[str, ...] = ()
    extract: Optional[str] = None
    _detect: Pattern[str] = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        if not self.detect_any:
            raise ValueError(f"host envelope {self.name!r} needs at least one detect tag")
        pattern = "|".join(_open_re(tag) for tag in self.detect_any)
        object.__setattr__(self, "_detect", re.compile(pattern, _FLAGS))

    def apply(self, text: str) -> Optional[str]:
        """Return the normalized turn, or ``None`` when the envelope does not apply."""
        if not text or not self._detect.search(text):
            return None
        result = (
            _extract(text, self.strip, self.extract)
            if self.extract
            else _strip_edges(text, self.strip)
        )
        if result is None or result == text:
            return None
        return result


@dataclass(frozen=True)
class Normalized:
    text: str
    envelope: Optional[str] = None  # name of the envelope that matched, if any

    @property
    def matched(self) -> bool:
        return self.envelope is not None


def normalize(text: str, envelopes: Sequence[HostEnvelope]) -> Normalized:
    """Apply the first envelope that matches, in the order given.

    Only one envelope runs. When a bridge wraps a CLI, the CLI's notes sit outside the
    bridge's body tag and are already gone once that body is extracted; running a second
    envelope over the body would only remove tags the user wrote.
    """
    for envelope in envelopes:
        result = envelope.apply(text)
        if result is not None:
            return Normalized(result, envelope.name)
    return Normalized(text)


def _extract(text: str, strip: Iterable[str], tag: str) -> Optional[str]:
    """Body of the first ``tag`` that opens outside a strip block, up to its *last* close.

    Ending at the last close keeps a body that quotes the closing tag. Returns ``None``
    when the body tag is missing or something it depends on is unclosed.
    """
    body_open = re.compile(_open_re(tag), _FLAGS)
    strip_opens = [
        (re.compile(_open_re(t), _FLAGS), re.compile(_close_re(t), _FLAGS)) for t in strip
    ]
    pos = 0
    while True:
        found = body_open.search(text, pos)
        if found is None:
            return None
        skip = None
        for open_re, close_re in strip_opens:
            hit = open_re.search(text, pos, found.start())
            if hit is not None and (skip is None or hit.start() < skip[0].start()):
                skip = (hit, close_re)
        if skip is None:
            break
        close = skip[1].search(text, skip[0].end())
        if close is None:
            return None
        pos = close.end()
    closes = list(re.finditer(_close_re(tag), text[found.end() :], _FLAGS))
    if not closes:
        return None
    return text[found.end() : found.end() + closes[-1].start()].strip()


def _strip_edges(text: str, strip: Iterable[str]) -> Optional[str]:
    """Remove whole-line ``strip`` blocks from the start and the end of ``text``.

    A block counts only if its opening and closing tags each sit on a line of their own,
    which is how hosts emit them. Anything that does not fit is left as it is.
    """
    tags = tuple(strip)
    if not tags:
        return None
    opens = [re.compile(r"^[ \t]*" + _open_re(t) + r"[ \t]*$", _FLAGS) for t in tags]
    closes = [re.compile(r"^[ \t]*" + _close_re(t) + r"[ \t]*$", _FLAGS) for t in tags]
    lines = text.split("\n")
    start, end = 0, len(lines)
    changed = False

    while True:
        while start < end and not lines[start].strip():
            start += 1
        kind = next(
            (i for i, rx in enumerate(opens) if start < end and rx.match(lines[start])), None
        )
        if kind is None:
            break
        close = next((j for j in range(start + 1, end) if closes[kind].match(lines[j])), None)
        if close is None:
            break
        start, changed = close + 1, True

    while True:
        while end > start and not lines[end - 1].strip():
            end -= 1
        kind = next(
            (i for i, rx in enumerate(closes) if end > start and rx.match(lines[end - 1])), None
        )
        if kind is None:
            break
        opening = next(
            (j for j in range(end - 2, start - 1, -1) if opens[kind].match(lines[j])), None
        )
        if opening is None:
            break
        end, changed = opening, True

    if not changed:
        return None
    return "\n".join(lines[start:end]).strip()


# Mirrors ``_HOST_BLOCK_TAGS`` in ``openviking/ingest/sources/workbuddy.py``; a parity
# test fails when the two drift apart.
_WORKBUDDY_HOST_BLOCK_TAGS = (
    "system-reminder",
    "memory_and_skills_reminder",
    "automation_system_reminder",
    "previous_user_message",
    "previous_assistant_message",
    "previous_tool_call",
    "cb_summary",
    "conversation_history_summary",
    "additional_data",
    "user_info",
    "identity_context",
    "craft_mode",
    "connector-status",
)

BUILTIN_ENVELOPES: Dict[str, HostEnvelope] = {
    envelope.name: envelope
    for envelope in (
        HostEnvelope(
            name="claude-code",
            detect_any=("system-reminder", "task-notification"),
            strip=("system-reminder", "task-notification"),
        ),
        HostEnvelope(
            name="workbuddy",
            detect_any=("user_query",),
            strip=_WORKBUDDY_HOST_BLOCK_TAGS,
            extract="user_query",
        ),
    )
}


def resolve_envelopes(names: Iterable[str]) -> Tuple[List[HostEnvelope], List[str]]:
    """Look built-in envelopes up by name, in order; unknown names are returned, not raised."""
    found: List[HostEnvelope] = []
    unknown: List[str] = []
    for raw in names:
        name = str(raw or "").strip().lower()
        if not name:
            continue
        if name in BUILTIN_ENVELOPES:
            found.append(BUILTIN_ENVELOPES[name])
        else:
            unknown.append(name)
    return found, unknown
