# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Lines the gateway shows the user and the model never sees.

Tool notices name each call the hidden tool loop runs: ``> OpenViking search: "blue" — done``.
The recall notice leads the first reply of a turn with what OpenViking added to it:
``> OpenViking recall: 2 items (2 memories) — a, b``.

They leave the history by different routes. A tool notice belongs to the visible span its
K.HIDDEN record replaces, and that record's anchor includes it, so it stays until replay swaps
the span; history that cannot be replayed loses it by pattern. The recall notice is in no span:
each request strips it from reply starts before anything else, and recorded replies go without
it, so both agree. Capture removes both kinds from the assistant text it keeps.
"""

import re

# Rendering below must match these patterns: they are how the gateway finds its lines again.
TOOL_LINE = r"> OpenViking \w+(?:: [^\n]+)? — (?:done|failed|skipped)"
RECALL_LINE = r"> OpenViking (?:context|recall|recall failed): [^\n]+"
# A tool line, with the blank lines that separate it.
TOOL_NOTICE = re.compile(rf"^{TOOL_LINE}$\n*", re.M)
# The recall notice as rendered: its lines, then a blank line.
RECALL_NOTICE = re.compile(rf"(?:{RECALL_LINE}\n)+\n")
NOTICE_LINES = re.compile(rf"^(?:{TOOL_LINE}|{RECALL_LINE})$\n*", re.M)

# What the session-start block holds, in the order the recall notice names it.
SESSION_PARTS = {
    "profile": "user profile",
    "memories": "memory index",
    "skills": "skill list",
    "history": "earlier sessions",
}
CATEGORIES = {
    **dict.fromkeys(("events", "entities", "preferences", "experiences", "memories"), "memory"),
    "resources": "resource",
    "skills": "skill",
}
PLURALS = {"memory": "memories", "resource": "resources", "skill": "skills", "item": "items"}
# Recall outcomes that are not worth a line: nothing found, or recall did not run.
QUIET_REASONS = {"recalled", "empty", "disabled", "budget", "reminder"}
FAILED_REASONS = {
    "openviking_unavailable": "OpenViking unavailable",
    "openviking_http_401": "OpenViking rejected the key (401)",
    "openviking_version_mismatch": "OpenViking version mismatch",
    "recall_timeout": "OpenViking timed out",
}
NAMES_SHOWN = 3


def clip(value, limit=80):
    value = " ".join(value.split())
    return value if len(value) <= limit else value[: limit - 1] + "…"


def tool_head(name, target):
    """A tool line before its outcome: the call and what it acts on."""
    return "> OpenViking " + name + (": " + target if target else "")


def tool_tail(failed, skipped):
    """The outcome that completes a tool line once the call has run."""
    return " — skipped" if skipped else " — failed" if failed else " — done"


def counted(number, noun):
    return f"{number} {noun if number == 1 else PLURALS[noun]}"


def category(entry):
    """memory, resource or skill, from the server's category or else the URI; else ""."""
    kind = CATEGORIES.get(entry.get("category"))
    if kind:
        return kind
    uri = entry.get("uri") or ""
    return next((k for k in ("memory", "resource", "skill") if f"/{PLURALS[k]}/" in uri), "")


def entry_name(uri):
    return clip(uri.rstrip("/").rsplit("/", 1)[-1].removesuffix(".md"), 40)


def recall_line(entries):
    """``> OpenViking recall: 4 items (3 memories, 1 resource) — a, b, +2 more``."""
    if not entries:
        return "> OpenViking recall: context added"
    kinds = [category(entry) for entry in entries]
    groups = ", ".join(
        counted(kinds.count(kind), kind)
        for kind in ("memory", "resource", "skill")
        if kind in kinds
    )
    line = "> OpenViking recall: " + counted(len(entries), "item")
    line += f" ({groups})" if groups else ""
    names = [entry_name(e["uri"]) for e in entries if isinstance(e.get("uri"), str)]
    names = [name for name in names if name]
    if names:
        more = len(entries) - min(len(names), NAMES_SHOWN)
        line += " — " + ", ".join(names[:NAMES_SHOWN]) + (f", +{more} more" if more else "")
    return line


def failure_line(reason):
    if reason in FAILED_REASONS:
        text = FAILED_REASONS[reason]
    elif reason.startswith("openviking_http_"):
        text = "OpenViking HTTP " + reason.removeprefix("openviking_http_")
    else:
        text = reason
    # The grammar needs text after the colon.
    return "> OpenViking recall failed: " + (clip(text) or "unknown error")


def recall_notice(parts, reason, entries):
    """The recall notice for one injection, or "" when there is nothing to show.

    ``parts`` are the SESSION_PARTS keys the session-start block holds (none after
    the first injection), ``reason`` is the recall decision's and ``entries`` the
    search entries.
    """
    lines = []
    shown = [SESSION_PARTS[part] for part in SESSION_PARTS if part in parts]
    if shown:
        lines.append("> OpenViking context: " + ", ".join(shown))
    if reason == "recalled":
        lines.append(recall_line(entries))
    elif reason and reason not in QUIET_REASONS:
        lines.append(failure_line(reason))
    return "\n".join(lines) + "\n\n" if lines else ""


def without_recall_notice(text):
    """``text`` without the recall notice it starts with; "" when it held nothing else."""
    # Bounded, so relaying a long reply never scans or copies it to look.
    if not text[:64].lstrip().startswith("> OpenViking "):
        return text
    # A client may have trimmed the whitespace after a notice that stood alone.
    if RECALL_NOTICE.fullmatch(text.strip() + "\n\n"):
        return ""
    match = RECALL_NOTICE.match(text)
    return text[match.end() :] if match else text


def without_notices(text):
    """``text`` without any line the gateway rendered, wherever it stands."""
    return NOTICE_LINES.sub("", text)
