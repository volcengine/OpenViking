# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""Shared helpers for config validation and error formatting."""

import difflib
from typing import Any, Optional, get_args, get_origin

from pydantic import BaseModel, ValidationError


def suggest_closest_field(field_name: str, valid_fields: set[str]) -> Optional[str]:
    """Return the closest matching field name when it is similar enough."""
    close_matches = difflib.get_close_matches(field_name, sorted(valid_fields), n=1, cutoff=0.6)
    if close_matches:
        return close_matches[0]
    return None


def raise_unknown_config_fields(
    *,
    data: dict[str, Any],
    valid_fields: set[str],
    context_name: str,
) -> None:
    """Raise a user-friendly error for unexpected config fields."""
    unknown_fields = [key for key in data if key not in valid_fields]
    if not unknown_fields:
        return

    errors = []
    for field_name in unknown_fields:
        suggestion = suggest_closest_field(field_name, valid_fields)
        if suggestion:
            errors.append(
                f"Unknown config field '{field_name}' in {context_name} "
                f"(did you mean '{suggestion}'?)"
            )
        else:
            errors.append(f"Unknown config field '{field_name}' in {context_name}")

    raise ValueError("\n".join(errors))


def _unwrap_model_type(annotation: Any) -> Optional[type[BaseModel]]:
    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        return annotation

    origin = get_origin(annotation)
    if origin is None:
        return None

    for arg in get_args(annotation):
        model_type = _unwrap_model_type(arg)
        if model_type is not None:
            return model_type
    return None


def _get_model_at_path(
    root_model: type[BaseModel], path: tuple[Any, ...]
) -> Optional[type[BaseModel]]:
    current_model = root_model
    for part in path:
        field = current_model.model_fields.get(str(part))
        if field is None:
            return None
        next_model = _unwrap_model_type(field.annotation)
        if next_model is None:
            return None
        current_model = next_model
    return current_model


def format_validation_error(
    *,
    root_model: type[BaseModel],
    error: ValidationError,
    path_prefix: str = "",
) -> str:
    """Render a pydantic ValidationError as a concise config error message."""
    formatted_errors = []

    for item in error.errors():
        loc = tuple(item.get("loc", ()))
        path_parts = [path_prefix] if path_prefix else []
        path_parts.extend(str(part) for part in loc)
        path = ".".join(path_parts)
        error_type = item.get("type", "")

        if error_type == "extra_forbidden" and loc:
            parent_model = _get_model_at_path(root_model, loc[:-1])
            invalid_field = str(loc[-1])
            message = f"Unknown config field '{path}'" if path else "Unknown config field"

            if parent_model is not None:
                suggestion = suggest_closest_field(
                    invalid_field,
                    set(parent_model.model_fields.keys()),
                )
                if suggestion:
                    suggested_parts = [path_prefix] if path_prefix else []
                    suggested_parts.extend(str(part) for part in loc[:-1])
                    suggested_parts.append(suggestion)
                    message += f" (did you mean '{'.'.join(suggested_parts)}'?)"

            formatted_errors.append(message)
            continue

        if path:
            formatted_errors.append(
                f"Invalid value for '{path}': {item.get('msg', 'validation failed')}"
            )
        else:
            formatted_errors.append(f"Invalid config value: {item.get('msg', 'validation failed')}")

    return "\n".join(formatted_errors)
