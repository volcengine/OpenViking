# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Restore user-owned references only at the public Session content boundary."""

import json
from collections.abc import Callable
from typing import Any

from openviking.privacy.restore import PrivacyRestorer
from openviking.privacy.service import UserPrivacyConfigService
from openviking.privacy.session_extractor import session_privacy_target
from openviking.server.identity import RequestContext
from openviking_cli.exceptions import InvalidArgumentError

_TEXT_FIELDS = {
    "text": ("text",),
    "context": ("abstract",),
    "tool": ("tool_output", "tool_output_externalization_error"),
}


def get_session_id_from_uri(uri: str, session_root: str) -> str | None:
    prefix = session_root + "/"
    if not uri.startswith(prefix):
        return None
    session_id, *path = uri[len(prefix) :].split("/")
    if not session_id:
        return None
    if path == ["messages.jsonl"]:
        return session_id
    if len(path) == 3 and path[0] == "history" and path[1].startswith("archive_"):
        return session_id if path[2] == "messages.jsonl" else None
    if len(path) == 3 and path[0] == "tool-results" and path[2] == "output.txt":
        return session_id
    return None


async def restore_session_content(
    privacy: UserPrivacyConfigService,
    content: str,
    ctx: RequestContext,
    *,
    session_id: str,
    jsonl: bool = False,
) -> str:
    """Resolve user-owned references from current values without rewriting disk."""
    restorer = PrivacyRestorer(
        privacy, ctx, category="session", target_key=session_privacy_target(session_id)
    )
    if not jsonl:
        restorer.collect_text(content)
        await restorer.load_values()
        return restorer.restore_text(content)

    lines = content.split("\n")

    def map_content(replace_text: Callable[[str], str]) -> None:
        def structured(value: Any) -> Any:
            if isinstance(value, str):
                if value in restorer.values:
                    return restorer.values[value]
                return replace_text(value)
            if isinstance(value, list):
                return [structured(item) for item in value]
            if isinstance(value, dict):
                result = {}
                for key, item in value.items():
                    clean_key = replace_text(key)
                    if clean_key in result:
                        raise InvalidArgumentError("Session privacy restored object key collision")
                    result[clean_key] = structured(item)
                return result
            return value

        for line, message in zip(lines, messages, strict=True):
            if not line.strip():
                continue
            if not isinstance(message, dict):
                raise TypeError("Session message must be an object")
            for part in message.get("parts", []):
                for field in _TEXT_FIELDS.get(part.get("type"), ()):
                    if isinstance(part.get(field), str):
                        part[field] = replace_text(part[field])
                if part.get("type") == "tool" and "tool_input" in part:
                    part["tool_input"] = structured(part["tool_input"])

    try:
        messages = [json.loads(line) if line.strip() else None for line in lines]
        map_content(restorer.collect_text)
    except (ValueError, TypeError, AttributeError):
        raise InvalidArgumentError("Session privacy requires valid message JSONL") from None

    await restorer.load_values()
    if not restorer.values:
        return content

    map_content(restorer.restore_text)
    return "\n".join(
        json.dumps(message, ensure_ascii=False) if line.strip() else line
        for line, message in zip(lines, messages, strict=True)
    )
