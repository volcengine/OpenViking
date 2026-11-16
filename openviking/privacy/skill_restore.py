# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Read-time restore helpers for skill privacy placeholders."""

from openviking.privacy.restore import PrivacyRestorer
from openviking.privacy.service import UserPrivacyConfigService
from openviking.privacy.skill_placeholder import build_placeholder
from openviking.server.identity import RequestContext


def get_skill_name_from_uri(uri: str) -> str | None:
    normalized = uri.strip().rstrip("/")
    marker = "/skills/"
    suffix = "/SKILL.md"
    if marker not in normalized or not normalized.endswith(suffix):
        return None
    start = normalized.rfind(marker)
    middle = normalized[start + len(marker) : -len(suffix)]
    if not middle or "/" in middle:
        return None
    return middle


async def restore_skill_content(
    privacy: UserPrivacyConfigService,
    content: str,
    ctx: RequestContext,
    *,
    skill_name: str,
) -> str:
    restorer = PrivacyRestorer(privacy, ctx, category="skill", target_key=skill_name)
    restorer.collect_text(content)
    await restorer.load_values()
    current = await restorer.get_current("skill", skill_name)
    values = current.values if current else {}
    unresolved_entries: list[str] = []

    for placeholder, location in restorer.fields.items():
        if placeholder == build_placeholder(skill_name, location.field):
            raw_value = values.get(location.field)
            if raw_value is None or raw_value == "":
                shown_value = "<missing>" if raw_value is None else '""'
                unresolved_entries.append(f"{location.field}={shown_value}")

    used_fields = {
        location.field
        for location in restorer.fields.values()
        if location.category == "skill" and location.target_key == skill_name
    }
    extra_config_entries = [
        f"{key}={value}"
        for key, value in values.items()
        if key not in used_fields and value is not None and value != ""
    ]

    restored = restorer.restore_text(content)
    if current and (unresolved_entries or extra_config_entries):
        restored += "\n\n[Privacy Config Notice]\n"
        if unresolved_entries:
            restored += "Missing config: " + ", ".join(unresolved_entries) + "\n"
        if extra_config_entries:
            restored += (
                "Configured but not referenced in content: "
                + ", ".join(extra_config_entries)
                + "\n"
            )

    return restored
