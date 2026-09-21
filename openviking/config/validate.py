# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Model-agnostic PATCH request validation.

The admin PATCH endpoints accept a nested dict and must reject anything the
runtime config surface does not allow *before* any merge or persistence. This
module enforces the structural rules that do not depend on the concrete config
model, so a single generic router can serve every section without per-field
branches:

1. top-level sections must be declared with :func:`RuntimeField`. If a section
   has marked descendants, input paths are restricted to those declarations;
   otherwise the whole section is accepted for subsequent model validation;
2. cluster-vs-account scope is confined by *which model is validated against*
   (``OpenVikingConfig`` for a cluster PATCH, ``AccountConfig`` for an account
   PATCH) — there is no per-field scope flag;
3. objects merge recursively, arrays replace wholesale (a list value is a leaf);
4. ``None`` is the three-state "delete this override" and is allowed on any
   surface leaf path, except a ``dynamic=False`` field on a non-creating PATCH.

Type and cross-field validation of the *merged* result still happens later when
the merged dict is parsed back into the real config model; this layer only gates
the request shape, not the semantic validity of individual values.
"""

from __future__ import annotations

import types
import typing
from typing import Any

from pydantic import BaseModel

from openviking_cli.utils.config.runtime_field import (
    collect_frozen_paths,
    collect_runtime_field_paths,
)


class ConfigPatchError(ValueError):
    """Raised when a PATCH request references a field the API may not modify."""

    def __init__(self, message: str, *, path: tuple[str, ...] | None = None) -> None:
        self.path = path
        if path:
            message = f"{message} (at '{'.'.join(path)}')"
        super().__init__(message)


def validate_patch(
    model: type[BaseModel],
    patch: dict[str, Any],
    *,
    creating: bool = False,
) -> None:
    """Validate a three-state PATCH request against ``model``.

    ``model`` is the scope's config model: pass ``OpenVikingConfig`` for a
    cluster PATCH and ``AccountConfig`` for an account PATCH. Only that model's
    ``RuntimeField`` surface paths are accepted, so scope isolation falls out of
    the model choice with no per-field flag.

    ``creating=False`` (the default, i.e. an update to an existing object) also
    rejects any path under a ``dynamic=False`` field: those may only be set at
    creation time and are immutable afterwards. ``creating=True`` skips that gate
    so initial-settings validation can accept them.

    Raises :class:`ConfigPatchError` on the first offending path. A valid patch
    returns ``None``.
    """
    if not isinstance(patch, dict):
        raise ConfigPatchError("patch must be a JSON object")
    allowed = collect_runtime_field_paths(model)
    _walk(patch, (), allowed, model)
    if not creating:
        _reject_frozen(patch, (), collect_frozen_paths(model))


def _reject_frozen(
    node: dict[str, Any],
    prefix: tuple[str, ...],
    frozen: set[tuple[str, ...]],
) -> None:
    """Reject any PATCH path that touches (or nests under) a create-only field."""
    if not frozen:
        return
    for key, value in node.items():
        path = prefix + (key,)
        if path in frozen:
            raise ConfigPatchError(
                "field is create-only and cannot be changed after creation", path=path
            )
        if isinstance(value, dict):
            _reject_frozen(value, path, frozen)


def _walk(
    node: dict[str, Any],
    prefix: tuple[str, ...],
    allowed: set[tuple[str, ...]],
    root_model: type[BaseModel],
) -> None:
    for key, value in node.items():
        if not isinstance(key, str):
            raise ConfigPatchError("patch keys must be strings", path=prefix)
        path = prefix + (key,)
        if path not in allowed:
            raise ConfigPatchError("field is not modifiable by the config API", path=path)
        # None (delete) and non-dict values (scalar / list-replace) are leaves.
        if value is None or not isinstance(value, dict):
            continue
        # Sections with no marked descendants are validated as a whole by the
        # model constructor. Keep API input strict even though persisted config
        # models intentionally ignore unknown fields for version compatibility.
        if _has_children(path, allowed):
            _walk(value, path, allowed, root_model)
        else:
            nested_model = _model_at_path(root_model, path)
            if nested_model is not None:
                _reject_unknown_model_fields(value, path, nested_model)


def _has_children(prefix: tuple[str, ...], allowed: set[tuple[str, ...]]) -> bool:
    plen = len(prefix)
    return any(len(p) > plen and p[:plen] == prefix for p in allowed)


def _model_at_path(
    root_model: type[BaseModel],
    path: tuple[str, ...],
) -> type[BaseModel] | None:
    model = root_model
    for part in path:
        field = model.model_fields.get(part)
        if field is None:
            return None
        nested = _unwrap_model(field.annotation)
        if nested is None:
            return None
        model = nested
    return model


def _reject_unknown_model_fields(
    node: dict[str, Any],
    prefix: tuple[str, ...],
    model: type[BaseModel],
) -> None:
    accepted: dict[str, str] = {}
    for field_name, field in model.model_fields.items():
        accepted[field_name] = field_name
        for alias in (field.alias, field.validation_alias):
            if isinstance(alias, str):
                accepted[alias] = field_name

    for key, value in node.items():
        field_name = accepted.get(key)
        path = prefix + (key,)
        if field_name is None:
            raise ConfigPatchError("field is not recognized", path=path)
        if not isinstance(value, dict):
            continue
        nested = _unwrap_model(model.model_fields[field_name].annotation)
        if nested is not None:
            _reject_unknown_model_fields(value, path, nested)


def _unwrap_model(annotation: Any) -> type[BaseModel] | None:
    origin = typing.get_origin(annotation)
    if origin in (typing.Union, types.UnionType):
        for arg in typing.get_args(annotation):
            if arg is type(None):
                continue
            nested = _unwrap_model(arg)
            if nested is not None:
                return nested
        return None
    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        return annotation
    return None
