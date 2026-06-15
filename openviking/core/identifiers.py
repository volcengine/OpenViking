"""Shared account, user, and peer identifier validation."""

from __future__ import annotations

import re
from typing import Optional

_VALIDATION_PATTERN = re.compile(r"^[a-zA-Z0-9_.@-]+$")


def validate_identifier_part(part: str, part_name: str) -> str | None:
    """Validate a single identifier path segment."""
    if not isinstance(part, str):
        return f"{part_name} must be a string."
    if not part:
        return f"{part_name} is empty"
    if part in {".", ".."}:
        return f"{part_name} must not be '.' or '..'"
    if not _VALIDATION_PATTERN.match(part):
        return f"{part_name} must be alpha_numeric string."
    if part.count("@") > 1:
        return f"{part_name} must have at most one @."
    return None


def normalize_identifier_part(part: Optional[str], part_name: str) -> Optional[str]:
    """Normalize an optional identifier path segment and validate shared safety rules."""
    if part is None:
        return None
    if not isinstance(part, str):
        raise ValueError(f"{part_name} must be a string.")
    normalized = part.strip()
    if not normalized:
        return None
    validation_error = validate_identifier_part(normalized, part_name)
    if validation_error:
        raise ValueError(validation_error)
    return normalized


def validate_account_id(account_id: str) -> str | None:
    """Validate an account id."""
    validation_error = validate_identifier_part(account_id, "account_id")
    if validation_error:
        return validation_error
    if account_id.startswith("_"):
        return "account_id cannot start with underscore _."
    return None


def validate_user_id(user_id: str) -> str | None:
    """Validate a user id."""
    return validate_identifier_part(user_id, "user_id")
