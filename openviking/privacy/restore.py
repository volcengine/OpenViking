# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Resolve Privacy references to the current user's active values."""

import re
from typing import Any, Literal

from openviking.privacy.models import UserPrivacyConfigVersion
from openviking.privacy.reference import PrivacyFieldReference, parse_privacy_field_reference
from openviking.privacy.service import UserPrivacyConfigService
from openviking.server.identity import RequestContext
from openviking_cli.exceptions import InvalidArgumentError, NotFoundError, ResourceExhaustedError

_REFERENCE = re.compile(r"\{\{[ \t]*ov_privacy:[^{}\r\n]+\}\}")
MAX_RESTORE_TARGETS = 1024


class PrivacyRestorer:
    """Collect placeholders once and restore them using their original positions."""

    def __init__(
        self,
        privacy: UserPrivacyConfigService,
        ctx: RequestContext,
        *,
        category: Literal["skill", "session"],
        target_key: str,
    ):
        self._privacy = privacy
        self._ctx = ctx
        self._category = category
        self._target_key = target_key
        field = r"([^}:]+)" if category == "skill" else r"([A-Za-z0-9_-]{8})"
        self._placeholder = re.compile(
            r"\{\{ov_privacy:" + category + ":" + re.escape(target_key) + ":" + field + r"\}\}"
        )
        self._configs: dict[tuple[str, str], UserPrivacyConfigVersion | None] = {}
        self._targets: set[tuple[str, str]] = set()
        self._matches: dict[str, list[re.Match[str]]] = {}
        self.fields: dict[str, PrivacyFieldReference] = {}
        self.values: dict[str, Any] = {}

    def collect_text(self, content: str) -> str:
        """Record matches for later replacement and return the input text."""
        if content in self._matches:
            return content
        matches = self._matches[content] = []
        for match in _REFERENCE.finditer(content):
            placeholder = match[0]
            if placeholder not in self.fields:
                location = parse_privacy_field_reference(placeholder)
                if location is None:
                    own = self._placeholder.fullmatch(placeholder)
                    if own is not None:
                        location = PrivacyFieldReference(
                            self._ctx.user.user_id, self._category, self._target_key, own[1]
                        )
                if location is None or location.owner_user_id != self._ctx.user.user_id:
                    continue
                self._targets.add((location.category, location.target_key))
                if len(self._targets) > MAX_RESTORE_TARGETS:
                    raise ResourceExhaustedError("Privacy reference target limit exceeded")
                self.fields[placeholder] = location
            matches.append(match)
        return content

    async def get_current(self, category: str, target_key: str) -> UserPrivacyConfigVersion | None:
        target = (category, target_key)
        if target not in self._configs:
            try:
                self._configs[target] = await self._privacy.get_current(
                    self._ctx, category, target_key
                )
            except (NotFoundError, FileNotFoundError, InvalidArgumentError):
                self._configs[target] = None
        return self._configs[target]

    async def load_values(self) -> None:
        """Load active values for the collected fields."""
        for placeholder, location in self.fields.items():
            current = await self.get_current(location.category, location.target_key)
            if current is None or location.field not in current.values:
                continue
            value = current.values[location.field]
            if value is None or value == "":
                continue
            self.values[placeholder] = value

    def restore_text(self, content: str) -> str:
        """Splice values into original matches without scanning inserted content."""
        chunks = []
        start = 0
        for match in self._matches.get(content, ()):
            if match[0] in self.values:
                chunks.extend((content[start : match.start()], str(self.values[match[0]])))
                start = match.end()
        if not chunks:
            return content
        chunks.append(content[start:])
        return "".join(chunks)
