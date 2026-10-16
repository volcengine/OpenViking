# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Irreversible, offline credential masking at the Session append boundary.

This deliberately detects defined formats and field contexts, not arbitrary
high-entropy strings. No original-value map is retained. Resource references,
images, identifiers and historical content are outside this boundary.
"""

from __future__ import annotations

import asyncio
import re
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, replace
from typing import Any

from openviking.message.part import ContextPart, ImagePart, TextPart, ToolPart
from openviking_cli.exceptions import InvalidArgumentError

REDACTED = "[REDACTED]"
MAX_TEXT_CHARS = 4 * 1024 * 1024
MAX_BATCH_CHARS = 16 * 1024 * 1024
MAX_DEPTH = 32
MAX_NODES = 100_000
MAX_MATCHES = 100_000

# Python regex scans hold the GIL. A single dedicated worker bounds concurrent
# scan allocations and prevents many scanners from starving the caller loop.
_MASKING_EXECUTOR = ThreadPoolExecutor(max_workers=1, thread_name_prefix="ov-session-masking")

# Define field aliases once; exact names accept camel case as well as separators.
_SENSITIVE_FIELDS = frozenset(
    {
        "ak",
        "sk",
        "api_key",
        "access_key",
        "access_key_id",
        "secret_key",
        "secret_access_key",
        "access_key_secret",
        "secret",
        "token",
        "access_token",
        "refresh_token",
        "session_token",
        "auth_token",
        "security_token",
        "bot_token",
        "client_secret",
        "app_secret",
        "private_key",
        "signing_key",
        "signing_secret",
        "webhook_secret",
        "password",
        "passwd",
        "pwd",
        "authorization",
        "proxy_authorization",
        "x_api_key",
        "credential",
        "credentials",
    }
)
_KEY_TRANSLATION = str.maketrans("", "", "-_ ")
_SENSITIVE_NAMES = frozenset(name.translate(_KEY_TRANSLATION) for name in _SENSITIVE_FIELDS)
# Only unambiguous credential names qualify arbitrary namespaces. A generic
# '*_key' or '*_token' suffix could also describe business keys or usage metrics.
_SENSITIVE_SUFFIXES = tuple(
    "_" + name
    for name in sorted(_SENSITIVE_FIELDS)
    if name
    not in {
        "ak",
        "sk",
        "secret",
        "token",
        "authorization",
        "proxy_authorization",
        "credential",
        "credentials",
    }
)
_ENV_PREFIXES = ("aws", "volcengine", "volc", "openviking", "openai", "anthropic", "root")
_NORMALIZED_NAMES = _SENSITIVE_NAMES | frozenset(
    prefix + name for prefix in _ENV_PREFIXES for name in _SENSITIVE_NAMES
)
_TOKEN_CHARS = r"A-Za-z0-9_./+=~\-"
# Consume the complete token, including punctuation allowed in credential bodies.
# A strict minimum avoids masking ordinary prose such as 'sk-example'.
# Each format defines its literal hints, prefix pattern and body pattern once.
# Detection, prefix preservation and idempotence all derive from this table.
_TOKEN_FORMATS = (
    (("sk-",), r"sk-(?:proj-|svcacct-|ant-(?:api\d+-)?)?", rf"[{_TOKEN_CHARS}*]{{16,}}"),
    (("ak-", "ark-"), r"(?:ak|ark)-", rf"[{_TOKEN_CHARS}*]{{16,}}"),
    (
        ("ghp_", "gho_", "ghu_", "ghs_", "ghr_", "github_pat_"),
        r"(?:gh[pousr]_|github_pat_)",
        rf"[{_TOKEN_CHARS}*]{{16,}}",
    ),
    (("AKLT", "AKTP"), r"(?:AKLT|AKTP)", rf"[{_TOKEN_CHARS}*]{{12,}}"),
    (("AKIA", "ASIA"), r"(?:AKIA|ASIA)", rf"[A-Z0-9]{{16}}(?![{_TOKEN_CHARS}])"),
)
_TOKEN_HINTS = tuple(hint for hints, _, _ in _TOKEN_FORMATS for hint in hints)
_PREFIX = "(?:" + "|".join(prefix for _, prefix, _ in _TOKEN_FORMATS) + ")"
_PREFIX_RE = re.compile(_PREFIX)
_TOKEN_RE = re.compile(
    r"(?<![\w-])(?:" + "|".join(prefix + body for _, prefix, body in _TOKEN_FORMATS) + ")"
)
_AUTH_RE = re.compile(rf"(?i)\b(?:Bearer|Basic)[ \t]+(?P<value>\[REDACTED\]|[{_TOKEN_CHARS}*]+)")
# Bound field names; don't use a greedy '.*' expression over message bodies.
_ASSIGNMENT_RE = re.compile(
    r"(?<![\w-])(?P<key>api[ ]key|secret[ ]key|[A-Za-z_][A-Za-z0-9_-]{0,127})"
    r"[\"']?\]?[ \t]*[:=：][ \t]*",
    re.IGNORECASE,
)
_CLI_OPTION_RE = re.compile(
    r"(?<![\w-])(?:--(?P<key>[A-Za-z_][A-Za-z0-9_-]{0,127})"
    r"(?:[ \t]*=[ \t]*|[ \t]+)|-(?P<short>[uU])(?:[ \t]+|(?=[^\s=])))"
)
_CANONICAL_MASK_RE = re.compile(rf"(?:{_PREFIX})?[{_TOKEN_CHARS}]{{2}}\*{{4}}[{_TOKEN_CHARS}]{{2}}")
_PLAIN_VALUE_RE = re.compile(rf"[{_TOKEN_CHARS}]+")


def _is_sensitive_key(key: str) -> bool:
    lowered = key.lower().replace("-", "_")
    return (
        lowered.endswith(_SENSITIVE_SUFFIXES)
        or lowered.translate(_KEY_TRANSLATION) in _NORMALIZED_NAMES
    )


def _value_span(text: str, start: int) -> tuple[int, int]:
    """Locate one assignment value on the original text, preserving quotes."""
    end = start
    if start < len(text) and text[start] in "\"'":
        quote = text[start]
        start += 1
        end = start
        while end < len(text):
            if text[end] == "\\":
                end = min(len(text), end + 2)
            elif text[end] == quote:
                break
            else:
                end += 1
    else:
        while end < len(text) and not text[end].isspace() and text[end] not in ",;，；\"'<>}{][":
            end += 1
    return start, end


def _shell_value_span(text: str, start: int) -> tuple[int, int]:
    """Locate one shell word, including quoted fragments and escaped spaces.

    This only locates recorded text; it never expands or executes shell input.
    A single enclosing quote pair is preserved. Concatenated or unclosed quote
    fragments are part of the masked span so no credential fragment survives.
    """
    encoded_quotes = text.startswith('\\"', start)
    quote = '"' if encoded_quotes else None
    width = 2 if encoded_quotes else 1
    end = start + 2 if encoded_quotes else start
    first_quote_end = None
    while end < len(text):
        char = text[end]
        if encoded_quotes and char == "\\" and quote != "'":
            run_end = end + 1
            while run_end < len(text) and text[run_end] == "\\":
                run_end += 1
            if run_end == end + 1 and run_end < len(text) and text[run_end] == '"':
                if quote:
                    quote = None
                    if first_quote_end is None:
                        first_quote_end = end
                else:
                    quote = '"'
                end = run_end + 1
            else:
                end = min(len(text), run_end + 1)
        elif char == "\\" and quote != "'":
            end = min(len(text), end + 2)
        elif quote:
            if char == quote and (not encoded_quotes or quote == "'"):
                quote = None
                if first_quote_end is None:
                    first_quote_end = end
            end += 1
        elif char in "\"'":
            if encoded_quotes and char == '"':
                break  # The enclosing JSON string, outside the shell word.
            quote = char
            end += 1
        elif char.isspace() or char in ";&|()<>":
            break
        else:
            end += 1
    if start < end and (encoded_quotes or text[start] in "\"'") and first_quote_end == end - width:
        return start + width, end - width
    return start, end


def _mask(value: Any, *, preserve_auth_scheme: bool = False) -> str:
    if not isinstance(value, str):
        return REDACTED
    if preserve_auth_scheme:
        auth = _AUTH_RE.fullmatch(value)
        if auth:
            return value[: auth.start("value")] + _mask(auth["value"])
    if not value or value == REDACTED or _CANONICAL_MASK_RE.fullmatch(value):
        return value
    # Preserve only long, plain token bodies. Complex values, escaped strings and
    # short credentials get no partial disclosure.
    prefix_match = _PREFIX_RE.match(value)
    prefix = prefix_match.group() if prefix_match else ""
    body = value[len(prefix) :]
    if len(body) < 20 or not _PLAIN_VALUE_RE.fullmatch(value):
        return REDACTED
    return f"{prefix}{body[:2]}****{body[-2:]}"


@dataclass
class _Budget:
    chars: int = 0
    nodes: int = 0
    matches: int = 0

    def text(self, value: str) -> None:
        self.chars += len(value)
        if len(value) > MAX_TEXT_CHARS or self.chars > MAX_BATCH_CHARS:
            raise InvalidArgumentError("Session masking text limit exceeded")

    def node(self, depth: int) -> None:
        self.nodes += 1
        if depth > MAX_DEPTH or self.nodes > MAX_NODES:
            raise InvalidArgumentError("Session masking structure limit exceeded")

    def match(self) -> None:
        self.matches += 1
        if self.matches > MAX_MATCHES:
            raise InvalidArgumentError("Session masking match limit exceeded")


def mask_text(text: str, *, _budget: _Budget | None = None) -> str:
    """Mask supported credentials; reject oversized inputs without echoing them."""
    budget = _budget if _budget is not None else _Budget()
    if not isinstance(text, str):
        raise InvalidArgumentError("Session masking requires text content")
    budget.text(text)
    spans: list[tuple[int, int]] = []
    covered_until = 0
    assignments = _ASSIGNMENT_RE.finditer(text) if any(c in text for c in "=:：") else ()
    for match in assignments:
        if match.start() < covered_until or not _is_sensitive_key(match["key"]):
            continue
        start = match.end()
        # Let the authentication rule retain the scheme (including quoted headers).
        auth_start = start + 1 if start < len(text) and text[start] in "\"'" else start
        if match["key"].translate(_KEY_TRANSLATION).lower() in {
            "authorization",
            "proxyauthorization",
        } and _AUTH_RE.match(text, auth_start):
            continue
        if start == len(text):
            continue
        start, end = _value_span(text, start)
        if end > start:
            budget.match()
            spans.append((start, end))
            covered_until = end
    options = _CLI_OPTION_RE.finditer(text) if "--" in text or "-u" in text or "-U" in text else ()
    cli_covered_until = 0
    for match in options:
        if match.start() < cli_covered_until:
            continue
        key = match["key"] or "user"
        normalized = key.translate(_KEY_TRANSLATION).lower()
        combined_auth = normalized in {"user", "proxyuser"}
        # curl's bearer option names a token; --user/-u/-U combine user:password.
        if normalized == "oauth2bearer":
            key = "access_token"
        if not combined_auth and not _is_sensitive_key(key):
            continue
        start = match.end()
        while text.startswith(("\\\n", "\\\r\n"), start):
            start += 3 if text.startswith("\\\r\n", start) else 2
            while start < len(text) and text[start] in " \t":
                start += 1
        if text.startswith("--", start):
            continue  # Missing value; the next option is not the credential.
        start, end = _shell_value_span(text, start)
        if end > start and (not combined_auth or ":" in text[start:end]):
            cli_covered_until = end
            if normalized in {"authorization", "proxyauthorization"}:
                auth = _AUTH_RE.fullmatch(text, start, end)
                if auth:
                    start, end = auth.span("value")
            budget.match()
            spans.append((start, end))
    # Avoid Unicode lowercasing allocations for content without an auth hint.
    lowered = text.lower() if "b" in text or "B" in text else ""
    detectors = []
    if any(hint in text for hint in _TOKEN_HINTS):
        detectors.append((_TOKEN_RE, 0))
    if "bearer" in lowered or "basic" in lowered:
        detectors.append((_AUTH_RE, "value"))
    for regex, group in detectors:
        for match in regex.finditer(text):
            budget.match()
            spans.append(match.span(group))
    if not spans:
        return text
    # Merge overlapping detections on the original input, then replace once.
    merged: list[tuple[int, int]] = []
    for start, end in sorted(spans):
        if merged and start < merged[-1][1]:
            merged[-1] = (merged[-1][0], max(end, merged[-1][1]))
        else:
            merged.append((start, end))
    chunks: list[str] = []
    offset = 0
    for start, end in merged:
        chunks.extend((text[offset:start], _mask(text[start:end])))
        offset = end
    chunks.append(text[offset:])
    return "".join(chunks)


def _copy_value(value: Any, budget: _Budget, depth: int = 0) -> Any:
    budget.node(depth)
    if isinstance(value, str):
        return mask_text(value, _budget=budget)
    if isinstance(value, dict):
        result = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise InvalidArgumentError("Session masking requires string object keys")
            clean_key = mask_text(key, _budget=budget)
            if clean_key in result:
                raise InvalidArgumentError("Session masking object key collision")
            if _is_sensitive_key(key):
                # Dropped containers need no traversal; strings still count
                # toward the input text budget before masking.
                if isinstance(item, str):
                    budget.text(item)
                clean = _mask(
                    item,
                    preserve_auth_scheme=key.translate(_KEY_TRANSLATION).lower()
                    in {"authorization", "proxyauthorization"},
                )
            else:
                clean = _copy_value(item, budget, depth + 1)
            result[clean_key] = clean
        return result
    if isinstance(value, (list, tuple)):
        copied = [_copy_value(item, budget, depth + 1) for item in value]
        return tuple(copied) if isinstance(value, tuple) else copied
    if value is None or isinstance(value, (bool, int, float)):
        return value
    raise InvalidArgumentError("Session masking requires JSON-compatible tool input")


def mask_message_specs(messages_spec: list[dict]) -> list[dict]:
    """Sanitize the entire batch before any externalization or append.

    Content fields get private copies. Identity, routing and resource reference
    fields keep their original meanings. Exceptions never fall back to raw data.
    """
    budget = _Budget()
    result = []
    for spec in messages_spec:
        budget.node(0)
        copied = dict(spec)
        parts = []
        for part in spec.get("parts", []):
            budget.node(0)
            if isinstance(part, TextPart):
                parts.append(replace(part, text=mask_text(part.text, _budget=budget)))
            elif isinstance(part, ContextPart):
                parts.append(replace(part, abstract=mask_text(part.abstract, _budget=budget)))
            elif isinstance(part, ToolPart):
                parts.append(
                    replace(
                        part,
                        tool_input=_copy_value(part.tool_input, budget),
                        tool_output=mask_text(part.tool_output, _budget=budget),
                        tool_output_externalization_error=mask_text(
                            part.tool_output_externalization_error, _budget=budget
                        ),
                    )
                )
            elif isinstance(part, ImagePart):
                parts.append(replace(part))
            else:
                raise InvalidArgumentError("Session masking received unsupported Part type")
        if "parts" in spec:
            copied["parts"] = parts
        result.append(copied)
    return result


async def mask_message_specs_async(messages_spec: list[dict]) -> list[dict]:
    """Run the pure batch scanner on a dedicated, serialized worker thread."""
    return await asyncio.get_running_loop().run_in_executor(
        _MASKING_EXECUTOR, mask_message_specs, messages_spec
    )
