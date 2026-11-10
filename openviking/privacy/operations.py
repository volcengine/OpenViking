# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Privacy operations shared by HTTP and MCP boundaries."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from openviking.privacy.reference import (
    build_privacy_field_reference,
    parse_privacy_field_selector,
)
from openviking.privacy.validation import validate_privacy_field_name, validate_privacy_identifier
from openviking.server.identity import RequestContext
from openviking_cli.exceptions import InvalidArgumentError, NotFoundError, PermissionDeniedError

if TYPE_CHECKING:
    from openviking.privacy.service import UserPrivacyConfigService


def require_privacy_owner(owner_user_id: str, ctx: RequestContext) -> None:
    """Reject references belonging to another user, including for administrators."""
    if owner_user_id != ctx.user.user_id:
        raise PermissionDeniedError(
            f"Privacy reference belongs to user {owner_user_id}", resource=owner_user_id
        )


def privacy_field_metadata(
    ctx: RequestContext, category: str, target_key: str, values: dict[str, Any]
) -> list[dict[str, Any]]:
    """Describe fields without including any of their stored values."""
    return [
        {
            "field": field,
            "configured": value is not None and str(value) != "",
            "reference": build_privacy_field_reference(
                ctx.user.user_id, category, target_key, field
            ),
        }
        for field, value in sorted(values.items())
    ]


async def build_privacy_tree(
    privacy: UserPrivacyConfigService | None,
    ctx: RequestContext,
    category: str | None = None,
) -> dict[str, Any]:
    """List the caller's active targets and safe field metadata."""
    if category is not None:
        validate_privacy_identifier(category, "category")
    result: dict[str, Any] = {"owner_user_id": ctx.user.user_id, "categories": []}
    if privacy is None:
        return result
    categories = [category] if category is not None else await privacy.list_categories(ctx)
    for name in categories:
        targets = []
        for target_key in await privacy.list_targets(ctx, name):
            current = await privacy.get_current(ctx, name, target_key)
            if current is None:
                # A directory retained for its lock is not a saved configuration.
                continue
            targets.append(
                {
                    "target_key": target_key,
                    "version": current.version,
                    "fields": privacy_field_metadata(ctx, name, target_key, current.values),
                }
            )
        result["categories"].append({"category": name, "targets": targets})
    return result


async def resolve_privacy_field(
    privacy: UserPrivacyConfigService | None, ctx: RequestContext, selector: str
) -> dict[str, Any]:
    """Resolve one current field, enforcing the reference's owner before reading."""
    reference = parse_privacy_field_selector(selector, current_user_id=ctx.user.user_id)
    if reference is None:
        raise InvalidArgumentError(
            "selector must be an owner-bound Privacy reference or a field-level Privacy URI"
        )
    require_privacy_owner(reference.owner_user_id, ctx)
    validate_privacy_field_name(reference.field, location="field")
    if privacy is None or not await privacy.exists(ctx, reference.category, reference.target_key):
        raise NotFoundError(f"{reference.category}/{reference.target_key}", "privacy config")
    current = await privacy.get_current(ctx, reference.category, reference.target_key)
    if current is None or reference.field not in current.values:
        raise NotFoundError(selector, "privacy config field")
    value = current.values[reference.field]
    return {
        "owner_user_id": reference.owner_user_id,
        "category": reference.category,
        "target_key": reference.target_key,
        "field": reference.field,
        "version": current.version,
        "configured": value is not None and str(value) != "",
        "reference": reference.render(),
        "value": value,
    }
