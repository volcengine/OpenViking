# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Protect registered Session credentials before externalization and persistence."""

import asyncio
import hashlib
import re
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass, replace
from secrets import token_urlsafe
from typing import Any, TypeVar

from openviking.message.part import ContextPart, ImagePart, Part, TextPart, ToolPart
from openviking.privacy.reference import parse_privacy_field_reference
from openviking.privacy.rules import (
    MAX_MATCHES,
    MAX_TEXT_CHARS,
    find_privacy_matches,
    is_sensitive_key,
)
from openviking.privacy.sanitizer import PrivacyReplacement, PrivacySanitizer
from openviking.privacy.service import UserPrivacyConfigService
from openviking.privacy.validation import validate_privacy_identifier
from openviking.server.identity import RequestContext
from openviking_cli.exceptions import (
    AlreadyExistsError,
    InvalidArgumentError,
    ResourceExhaustedError,
)

MAX_BATCH_CHARS = 16 * 1024 * 1024
MAX_DEPTH = 32
MAX_NODES = 100_000
MAX_FIELD_ID_ATTEMPTS = 8
# TODO: Revisit new-value discovery after fixing false positives that pollute
# Session privacy storage. Keep registered-value matching and restoration active.
# This is intentionally disabled in code, not exposed as a runtime setting.
_SESSION_PRIVACY_DISCOVERY_ENABLED = False
_Matcher = Callable[[str], list[PrivacyReplacement]]
_Text = TypeVar("_Text", str, None)
_HASHED_TARGET = re.compile(r"_[0-9a-f]{24}")


async def extract_and_store_session_privacy(
    session_id: str,
    messages_spec: list[dict],
    ctx: RequestContext,
    privacy: UserPrivacyConfigService,
    sanitizer: PrivacySanitizer | None,
) -> list[dict]:
    """Protect copies with registered values; new-value discovery is paused."""
    target_key = session_privacy_target(session_id)
    labels = None if target_key == session_id else {"session_id": session_id}
    matcher = await _prepare_session_matcher(sanitizer, ctx, target_key)
    for _ in range(MAX_FIELD_ID_ATTEMPTS):
        sanitized, added = await asyncio.to_thread(
            extract_session_privacy_values,
            target_key,
            messages_spec,
            match_registered=matcher,
        )
        if not added:
            return sanitized
        try:
            await privacy.insert_values(
                ctx,
                "session",
                target_key,
                added,
                updated_by=ctx.user.user_id,
                change_reason="Session message extraction",
                labels=labels,
            )
        except AlreadyExistsError:
            continue
        break
    else:
        raise ResourceExhaustedError("Could not allocate unique Session privacy field IDs")
    # Refresh the matcher to include newly saved values.
    matcher = await _prepare_session_matcher(sanitizer, ctx, target_key)
    rewriter = _SessionReferenceRewriter(matcher)
    return await asyncio.to_thread(rewriter.transform_messages, sanitized)


def extract_session_privacy_values(
    target_key: str,
    messages_spec: list[dict],
    *,
    match_registered: _Matcher | None = None,
) -> tuple[list[dict], dict[str, Any]]:
    """Return protected copies and discoveries without modifying caller-owned data."""
    extraction = _SessionExtraction(target_key, match_registered or (lambda _: []))
    return extraction.transform_messages(messages_spec), extraction.values


def session_privacy_target(session_id: str) -> str:
    if not _HASHED_TARGET.fullmatch(session_id):
        # Use valid Privacy identifiers as-is
        try:
            return validate_privacy_identifier(session_id, "target_key")
        except InvalidArgumentError:
            pass
    # hash other IDs and digest-shaped IDs.
    digest = hashlib.sha256(session_id.encode("utf-8", "surrogatepass")).hexdigest()
    return f"_{digest[:24]}"


def build_session_placeholder(target_key: str, key: str) -> str:
    return f"{{{{ov_privacy:session:{target_key}:{key}}}}}"


async def _prepare_session_matcher(
    sanitizer: PrivacySanitizer | None, ctx: RequestContext, target_key: str
) -> _Matcher:
    """Use Session placeholders for matching eight-character fields in this Session."""
    if sanitizer is None:
        return lambda _: []
    matcher = await sanitizer.prepare_matcher(ctx, prefer_credentials=True)

    def find(text: str) -> list[PrivacyReplacement]:
        matches: list[PrivacyReplacement] = []
        for match in matcher(text):
            reference = parse_privacy_field_reference(match.reference)
            if (
                reference is not None
                and reference.owner_user_id == ctx.user.user_id
                and reference.category == "session"
                and reference.target_key == target_key
                and re.fullmatch(r"[A-Za-z0-9_-]{8}", reference.field)
            ):
                match = replace(
                    match, reference=build_session_placeholder(target_key, reference.field)
                )
            matches.append(match)
        return matches

    return find


@dataclass
class _ExtractionBudget:
    nodes: int = 0
    chars: int = 0
    matches: int = 0

    def check_node(self, depth: int) -> None:
        self.nodes += 1
        if depth > MAX_DEPTH or self.nodes > MAX_NODES:
            raise InvalidArgumentError("Session privacy structure limit exceeded")

    def check_text(self, text: str | None) -> None:
        if not text:
            return
        self.chars += len(text)
        if len(text) > MAX_TEXT_CHARS or self.chars > MAX_BATCH_CHARS:
            raise InvalidArgumentError("Session privacy text limit exceeded")

    def record_match(self) -> None:
        self.matches += 1
        if self.matches > MAX_MATCHES:
            raise InvalidArgumentError("Session privacy match limit exceeded")


class _PayloadTransformer:
    """Traverse only payload fields; default text and field handlers preserve values."""

    def __init__(self, budget: _ExtractionBudget | None = None):
        self.budget = budget

    def check_node(self, depth: int) -> None:
        if self.budget is not None:
            self.budget.check_node(depth)

    def transform_text(self, text: _Text) -> _Text:
        if self.budget is not None:
            self.budget.check_text(text)
        return text

    def transform_field(self, key: str, value: Any, depth: int) -> Any:
        return self.transform_value(value, depth)

    def transform_value(self, value: Any, depth: int = 0) -> Any:
        self.check_node(depth)
        if isinstance(value, str):
            return self.transform_text(value)
        if isinstance(value, dict):
            result = {}
            for key, item in value.items():
                if not isinstance(key, str):
                    raise InvalidArgumentError("Session privacy requires string object keys")
                clean_key = self.transform_text(key)
                if clean_key in result:
                    raise InvalidArgumentError("Session privacy object key collision")
                result[clean_key] = self.transform_field(key, item, depth + 1)
            return result
        if isinstance(value, (list, tuple)):
            items = [self.transform_value(item, depth + 1) for item in value]
            return tuple(items) if isinstance(value, tuple) else items
        if value is None or isinstance(value, (bool, int, float)):
            return value
        raise InvalidArgumentError("Session privacy requires JSON-compatible tool input")

    def transform_part(self, part: Part) -> Part:
        if isinstance(part, TextPart):
            part.text = self.transform_text(part.text)
        elif isinstance(part, ContextPart):
            part.abstract = self.transform_text(part.abstract)
        elif isinstance(part, ToolPart):
            part.tool_input = self.transform_value(part.tool_input)
            part.tool_output = self.transform_text(part.tool_output)
            part.tool_output_externalization_error = self.transform_text(
                part.tool_output_externalization_error
            )
        elif not isinstance(part, ImagePart):
            raise InvalidArgumentError("Session privacy received unsupported Part type")
        return part

    def transform_messages(self, messages_spec: list[dict]) -> list[dict]:
        result = []
        for spec in messages_spec:
            self.check_node(0)
            copied = dict(spec)
            parts = []
            for part in spec.get("parts", []):
                self.check_node(0)
                parts.append(self.transform_part(part))
            if "parts" in copied:
                copied["parts"] = parts
            result.append(copied)
        return result


class _SessionExtraction(_PayloadTransformer):
    """Discover values while copying inputs; sensitive fields are captured whole."""

    budget: _ExtractionBudget

    def __init__(self, target_key: str, value_matcher: _Matcher):
        super().__init__(_ExtractionBudget())
        self.target_key = target_key
        self.value_matcher = value_matcher
        self.values: dict[str, Any] = {}
        self.text_references: dict[str, str] = {}
        # Same input budget, with identity handlers: validate and copy, never extract.
        self.literal = _PayloadTransformer(self.budget)

    def transform_part(self, part: Part) -> Part:
        if not isinstance(part, (TextPart, ContextPart, ToolPart, ImagePart)):
            raise InvalidArgumentError("Session privacy received unsupported Part type")
        return super().transform_part(replace(part))

    def transform_field(self, key: str, value: Any, depth: int) -> Any:
        if not _SESSION_PRIVACY_DISCOVERY_ENABLED or not is_sensitive_key(key):
            return self.transform_value(value, depth)
        copied = self.literal.transform_value(value, depth)
        # Empty strings, references and environment variables retain their meaning.
        # A neutral name captures Authorization including its authentication scheme.
        if isinstance(copied, str) and not find_privacy_matches(copied, field_name="password"):
            return copied
        return self.get_reference(copied) if copied is not None else None

    def get_reference(self, value: Any) -> str:
        self.budget.record_match()
        if isinstance(value, str):
            matches = self.value_matcher(value)
            if len(matches) == 1 and matches[0].start == 0 and matches[0].end == len(value):
                return matches[0].reference
        for _ in range(MAX_FIELD_ID_ATTEMPTS):
            # Six random bytes encode to eight URL-safe characters.
            key = token_urlsafe(6)
            if key not in self.values:
                self.values[key] = value
                return build_session_placeholder(self.target_key, key)
        raise ResourceExhaustedError("Could not allocate unique Session privacy field IDs")

    def transform_text(self, text: _Text) -> _Text:
        super().transform_text(text)
        if text is None or not text:
            return text
        spans: list[tuple[int, int, str | None]] = [
            (match.start, match.end, match.reference) for match in self.value_matcher(text)
        ]
        if _SESSION_PRIVACY_DISCOVERY_ENABLED:
            spans.extend((match.start, match.end, None) for match in find_privacy_matches(text))
        replacements = []
        for start, end, reference in _merge_spans(spans):
            if reference is None:
                value = text[start:end]
                reference = self.text_references.get(value)
                if reference is None:
                    reference = self.get_reference(value)
                    self.text_references[value] = reference
                else:
                    self.budget.record_match()
            else:
                self.budget.record_match()
            replacements.append(PrivacyReplacement(start, end, reference))
        return _replace_spans(text, replacements)


class _SessionReferenceRewriter(_PayloadTransformer):
    """Protect batch-wide repeats without rebinding references to other fields."""

    def __init__(self, value_matcher: _Matcher):
        super().__init__()
        self.value_matcher = value_matcher

    def transform_text(self, text: _Text) -> _Text:
        if text is None:
            return text
        return _replace_spans(text, self.value_matcher(text))


def _merge_spans(
    spans: list[tuple[int, int, str | None]],
) -> Iterator[tuple[int, int, str | None]]:
    """Union overlapping hits; reuse a registered reference only for the full union."""
    if not spans:
        return
    spans.sort(key=lambda span: (span[0], -span[1]))
    start, end, reference = spans[0]
    for left, right, candidate in spans[1:]:
        if left >= end:
            yield start, end, reference
            start, end, reference = left, right, candidate
        elif right > end:
            end, reference = right, None
        elif left == start and right == end and candidate is not None:
            reference = candidate
    yield start, end, reference


def _replace_spans(text: str, replacements: Iterable[PrivacyReplacement]) -> str:
    chunks = []
    cursor = 0
    for match in replacements:
        chunks.extend((text[cursor : match.start], match.reference))
        cursor = match.end
    chunks.append(text[cursor:])
    return "".join(chunks)
