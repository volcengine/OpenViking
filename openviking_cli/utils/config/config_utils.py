# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Shared helpers for config validation and error formatting."""

import difflib
import logging
from collections.abc import Mapping
from typing import Any, Optional, get_args, get_origin

from pydantic import BaseModel, ValidationError


def suggest_closest_field(field_name: str, valid_fields: set[str]) -> Optional[str]:
    """Return the closest matching field name when it is similar enough."""
    close_matches = difflib.get_close_matches(field_name, sorted(valid_fields), n=1, cutoff=0.6)
    if close_matches:
        return close_matches[0]
    return None


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


def warn_unknown_fields(
    *,
    data: Mapping[str, Any],
    valid_fields: set[str],
    logger: logging.Logger,
    path_prefix: str = "",
) -> None:
    """Warn about keys the owner of one config section does not declare.

    Unknown fields stay ignored so legacy configuration keeps loading; the
    warning keeps the mismatch visible to whoever wrote the file. Only field
    names are logged, never values.
    """
    messages = []
    for key in data:
        if key in valid_fields:
            continue
        field_path = f"{path_prefix}.{key}" if path_prefix else key
        # Config files are JSON, but from_dict() is also called programmatically;
        # a diagnostic must never be the reason loading fails.
        messages.append(f"Ignoring unknown config field '{field_path!s}'")
    if messages:
        logger.warning("%s", "\n".join(messages))


def _child_model_type(annotation: Any) -> Optional[type[BaseModel]]:
    """Return the model nested values must validate against, when unambiguous."""
    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        return annotation

    args = get_args(annotation)
    if not args or any(get_origin(arg) in (dict, Mapping) for arg in args):
        return None

    models = {model for model in (_unwrap_model_type(arg) for arg in args) if model is not None}
    if len(models) == 1:
        return models.pop()
    return None


def warn_unknown_config_fields(
    *,
    data: Mapping[str, Any],
    model: type[BaseModel],
    logger: logging.Logger,
    path_prefix: str = "",
    extra_valid_fields: Optional[set[str]] = None,
) -> None:
    """Warn about config keys the model does not declare, recursing into sub-models.

    ``extra_valid_fields`` lists keys consumed by another owner (for example the
    ``server`` section) so they are not reported as unknown.
    """
    warn_unknown_fields(
        data=data,
        valid_fields=set(model.model_fields) | (extra_valid_fields or set()),
        logger=logger,
        path_prefix=path_prefix,
    )

    for key, value in data.items():
        field = model.model_fields.get(key)
        if field is None:
            continue
        child_model = _child_model_type(field.annotation)
        if child_model is None:
            continue
        child_prefix = f"{path_prefix}.{key}" if path_prefix else key
        is_sequence = isinstance(value, (list, tuple))
        for index, item in enumerate(value if is_sequence else (value,)):
            if not isinstance(item, Mapping):
                continue
            warn_unknown_config_fields(
                data=item,
                model=child_model,
                logger=logger,
                path_prefix=f"{child_prefix}[{index}]" if is_sequence else child_prefix,
            )


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
