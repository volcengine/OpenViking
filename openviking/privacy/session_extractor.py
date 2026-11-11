# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Protect registered Session credentials before externalization and persistence."""

import asyncio
import hashlib
import re
from collections.abc import Callable, Iterable, Iterator
from contextlib import nullcontext
from copy import deepcopy
from dataclasses import dataclass, replace
from secrets import token_urlsafe
from typing import Any, TypeVar

from openviking.message.part import ContextPart, ImagePart, Part, TextPart, ToolPart
from openviking.privacy.reference import (
    build_privacy_field_reference,
    parse_privacy_field_reference,
)
from openviking.privacy.rules import prepare_privacy_extractor
from openviking.privacy.sanitizer import (
    DEFAULT_SANITIZER_MAX_CONTENT_BYTES,
    DEFAULT_SANITIZER_MAX_REPLACEMENTS,
    MIN_PROTECTED_VALUE_CHARS,
    PrivacyReplacement,
    PrivacySanitizer,
)
from openviking.privacy.service import UserPrivacyConfigService
from openviking.privacy.validation import validate_privacy_identifier
from openviking.server.identity import RequestContext
from openviking.server.user_config import ResolvedPrivacyConfig
from openviking_cli.exceptions import (
    AlreadyExistsError,
    InvalidArgumentError,
    ResourceExhaustedError,
)

MAX_DEPTH = 32
MAX_NODES = 100_000
MAX_FIELD_ID_ATTEMPTS = 8
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
    """Protect copies and discover new values when the user's mode enables discovery."""
    target_key = session_privacy_target(session_id)
    labels = None if target_key == session_id else {"session_id": session_id}
    settings = await privacy.get_settings(ctx)
    if settings.mode == "off":
        return await asyncio.to_thread(deepcopy, messages_spec)
    async with (
        privacy._config_lock(ctx, "session", target_key)
        if settings.discovery_enabled
        else nullcontext()
    ) as lease:
        existing_references = {}
        if settings.discovery_enabled and settings.custom_rules:
            current = await privacy.get_current(ctx, "session", target_key)
            values = current.values if current else {}
            for field, value in sorted(values.items()):
                if not isinstance(value, str) or not 0 < len(value) < MIN_PROTECTED_VALUE_CHARS:
                    continue
                reference = (
                    build_session_placeholder(target_key, field)
                    if re.fullmatch(r"[A-Za-z0-9_-]{8}", field)
                    else build_privacy_field_reference(
                        ctx.user.user_id, "session", target_key, field
                    )
                )
                existing_references.setdefault(value, reference)
        matcher = await _prepare_session_matcher(sanitizer, ctx, target_key, settings=settings)
        for _ in range(MAX_FIELD_ID_ATTEMPTS):
            sanitized, added = await asyncio.to_thread(
                extract_session_privacy_values,
                target_key,
                messages_spec,
                match_registered=matcher,
                settings=settings,
                existing_references=existing_references,
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
                    **({"owner_lease_ref": lease} if lease is not None else {}),
                )
            except AlreadyExistsError:
                continue
            break
        else:
            raise ResourceExhaustedError("Could not allocate unique Session privacy field IDs")
        # Refresh the matcher to include newly saved values.
        matcher = await _prepare_session_matcher(sanitizer, ctx, target_key, settings=settings)
        rewriter = _SessionReferenceRewriter(matcher)
        return await asyncio.to_thread(rewriter.transform_messages, sanitized)


def extract_session_privacy_values(
    target_key: str,
    messages_spec: list[dict],
    *,
    match_registered: _Matcher | None = None,
    settings: ResolvedPrivacyConfig | None = None,
    existing_references: dict[str, str] | None = None,
) -> tuple[list[dict], dict[str, str]]:
    """Return protected copies and discoveries without modifying caller-owned data."""
    extraction = _SessionExtraction(
        target_key, match_registered or (lambda _: []), settings, existing_references
    )
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
    sanitizer: PrivacySanitizer | None,
    ctx: RequestContext,
    target_key: str,
    *,
    settings: ResolvedPrivacyConfig,
) -> _Matcher:
    """Use Session placeholders for matching eight-character fields in this Session."""
    if sanitizer is None:
        return lambda _: []
    matcher = await sanitizer.prepare_matcher(ctx, scope=("session", target_key), settings=settings)

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
    content_bytes: int = 0
    matches: int = 0

    def check_node(self, depth: int) -> None:
        self.nodes += 1
        if depth > MAX_DEPTH or self.nodes > MAX_NODES:
            raise InvalidArgumentError("Session privacy structure limit exceeded")

    def check_text(self, text: str | None) -> None:
        if not text:
            return
        self.content_bytes += len(text.encode("utf-8"))
        if self.content_bytes > DEFAULT_SANITIZER_MAX_CONTENT_BYTES:
            raise InvalidArgumentError("Session privacy text limit exceeded")

    def record_match(self) -> None:
        self.matches += 1
        if self.matches > DEFAULT_SANITIZER_MAX_REPLACEMENTS:
            raise InvalidArgumentError("Session privacy match limit exceeded")


class _PayloadTransformer:
    """Traverse payload fields and apply the text handler to string values."""

    def __init__(self, budget: _ExtractionBudget | None = None):
        self.budget = budget

    def check_node(self, depth: int) -> None:
        if self.budget is not None:
            self.budget.check_node(depth)

    def transform_text(self, text: _Text) -> _Text:
        if self.budget is not None:
            self.budget.check_text(text)
        return text

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
                result[clean_key] = self.transform_value(item, depth + 1)
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
    """Discover credential formats while copying message payloads."""

    budget: _ExtractionBudget

    def __init__(
        self,
        target_key: str,
        value_matcher: _Matcher,
        settings: ResolvedPrivacyConfig | None,
        existing_references: dict[str, str] | None = None,
    ):
        super().__init__(_ExtractionBudget())
        self.target_key = target_key
        self.rule_matcher = (
            prepare_privacy_extractor(settings=settings)
            if settings is not None and settings.discovery_enabled
            else None
        )
        self.value_matcher = value_matcher
        self.values: dict[str, str] = {}
        # Reuse discovered short values without enabling substring matching for them.
        self.text_references = dict(existing_references or {})

    def transform_part(self, part: Part) -> Part:
        if not isinstance(part, (TextPart, ContextPart, ToolPart, ImagePart)):
            raise InvalidArgumentError("Session privacy received unsupported Part type")
        return super().transform_part(replace(part))

    def get_reference(self, value: str) -> str:
        self.budget.record_match()
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
        if self.rule_matcher is not None:
            spans.extend((match.start, match.end, None) for match in self.rule_matcher(text))
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
    chunks: list[str] = []
    cursor = 0
    for match in replacements:
        chunks.extend((text[cursor : match.start], match.reference))
        cursor = match.end
    chunks.append(text[cursor:])
    return "".join(chunks)
