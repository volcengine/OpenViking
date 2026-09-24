# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Model-agnostic PATCH request validation.

The admin PATCH endpoints accept a nested dict and must reject anything the
runtime config surface does not allow *before* any merge or persistence. This
module enforces the structural rules that do not depend on the concrete config
model, so a single generic router can serve every section without per-field
branches:

1. every component of a modified path must be declared with
   :func:`RuntimeField`; marking a section does not implicitly grant write
   access to its ordinary ``Field`` descendants;
2. cluster-vs-account scope is confined by *which model is validated against*
   (``OpenVikingConfig`` for a cluster PATCH, ``AccountConfig`` for an account
   PATCH) — there is no per-field scope flag;
3. objects merge recursively, arrays replace wholesale (indices are not
   independently patchable), while typed model list elements are checked against
   their own RuntimeField schema;
4. ``None`` is the three-state "delete this override". Deleting a section is
   allowed only when its whole model subtree is writable for the operation.

Type and cross-field validation of the *merged* result still happens later when
the merged dict is parsed back into the real config model; this layer only gates
the request shape, not the semantic validity of individual values.
"""

from __future__ import annotations

import copy
import dataclasses
import types
import typing
from typing import Any

from pydantic import BaseModel

from openviking_cli.utils.config.runtime_field import (
    collect_frozen_paths,
    collect_runtime_field_paths,
    is_dynamic,
    is_runtime_field,
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
    frozen = collect_frozen_paths(model)
    _walk(patch, (), allowed, frozen, model, creating)
    if not creating:
        _reject_frozen(patch, (), frozen)


def filter_runtime_fields(model: type[BaseModel], settings: dict[str, Any] | None) -> dict:
    """Build the effective runtime document from a persisted settings document.

    This is intentionally different from :func:`validate_patch`. A ConfigSource
    document is trusted persisted state, not a post-creation write request, so
    ``dynamic=False`` RuntimeFields must still be loaded on every node. The
    filter only prevents known ordinary ``Field`` values and fields unknown to
    this binary from becoming effective. The raw document remains in the
    source for forward-compatible round trips.
    """
    if not isinstance(settings, dict):
        return {}

    def walk(cls: type[Any], node: dict[str, Any]) -> dict:
        fields = _model_fields(cls)
        aliases = {
            field.alias: name
            for name, field in fields.items()
            if getattr(field, "alias", None)
        }
        result: dict = {}
        for key, value in node.items():
            name = key if key in fields else aliases.get(key)
            field = fields.get(name)
            if field is None or not is_runtime_field(field):
                continue
            nested = _unwrap_model(_field_annotation(field))
            if nested is not None and isinstance(value, dict):
                result[key] = walk(nested, value)
            else:
                result[key] = copy.deepcopy(value)
        return result

    return walk(model, settings)


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
    frozen: set[tuple[str, ...]],
    root_model: type[BaseModel],
    creating: bool,
) -> None:
    for key, value in node.items():
        if not isinstance(key, str):
            raise ConfigPatchError("patch keys must be strings", path=prefix)
        path = prefix + (key,)
        if path not in allowed:
            raise ConfigPatchError("field is not modifiable by the config API", path=path)
        if value is None:
            _validate_subtree_delete(path, allowed, frozen, root_model, creating)
            continue
        # Non-dict values (scalar / list-replace) are leaves.
        if not isinstance(value, dict):
            if isinstance(value, list):
                _validate_model_list(
                    value,
                    path,
                    _field_at_path(root_model, path),
                    creating=creating,
                )
            continue
        nested_model = _model_at_path(root_model, path)
        if nested_model is not None:
            _walk(value, path, allowed, frozen, root_model, creating)


def _validate_model_list(
    values: list[Any],
    path: tuple[str, ...],
    field: Any,
    *,
    creating: bool,
) -> None:
    """Validate RuntimeField elements inside a typed model list.

    Lists remain atomic PATCH values: callers replace the whole list rather than
    addressing an index. When the element type is a Pydantic model, however, its
    object shape is still part of the request contract and must not silently
    discard unknown or non-runtime fields.
    """
    element_model = _list_element_model(_field_annotation(field) if field is not None else None)
    if element_model is None:
        return

    fields = _model_fields(element_model)
    aliases = {
        item.alias: name
        for name, item in fields.items()
        if getattr(item, "alias", None)
    }
    for index, item in enumerate(values):
        if not isinstance(item, dict):
            # Pydantic will report the element type error when the merged
            # configuration is constructed. This layer only checks request
            # surface permissions.
            continue
        for key, value in item.items():
            if not isinstance(key, str):
                raise ConfigPatchError(
                    "patch keys must be strings",
                    path=path + (str(index),),
                )
            name = key if key in fields else aliases.get(key)
            child = fields.get(name)
            child_path = path + (str(index), key)
            if child is None or not is_runtime_field(child):
                raise ConfigPatchError(
                    "field is not modifiable by the config API",
                    path=child_path,
                )
            if not creating and not is_dynamic(child):
                raise ConfigPatchError(
                    "field is create-only and cannot be changed after creation",
                    path=child_path,
                )
            nested_model = _unwrap_model(_field_annotation(child))
            if nested_model is not None and isinstance(value, dict):
                _validate_model_object(value, nested_model, child_path, creating=creating)
            elif isinstance(value, list):
                _validate_model_list(
                    value,
                    child_path,
                    child,
                    creating=creating,
                )


def _validate_model_object(
    node: dict[str, Any],
    model: type[Any],
    path: tuple[str, ...],
    *,
    creating: bool,
) -> None:
    """Validate a model nested inside a typed list element."""
    fields = _model_fields(model)
    aliases = {
        item.alias: name
        for name, item in fields.items()
        if getattr(item, "alias", None)
    }
    for key, value in node.items():
        if not isinstance(key, str):
            raise ConfigPatchError("patch keys must be strings", path=path)
        name = key if key in fields else aliases.get(key)
        child = fields.get(name)
        child_path = path + (key,)
        if child is None or not is_runtime_field(child):
            raise ConfigPatchError(
                "field is not modifiable by the config API",
                path=child_path,
            )
        if not creating and not is_dynamic(child):
            raise ConfigPatchError(
                "field is create-only and cannot be changed after creation",
                path=child_path,
            )
        nested_model = _unwrap_model(_field_annotation(child))
        if nested_model is not None and isinstance(value, dict):
            _validate_model_object(value, nested_model, child_path, creating=creating)
        elif isinstance(value, list):
            _validate_model_list(value, child_path, child, creating=creating)


def _validate_subtree_delete(
    path: tuple[str, ...],
    allowed: set[tuple[str, ...]],
    frozen: set[tuple[str, ...]],
    root_model: type[BaseModel],
    creating: bool,
) -> None:
    """Require every field removed by a section-level ``None`` to be writable."""
    nested_model = _model_at_path(root_model, path)
    if nested_model is None:
        return
    for descendant in _model_field_paths(nested_model, path):
        if descendant not in allowed:
            raise ConfigPatchError(
                "field is not modifiable by the config API", path=descendant
            )
        if not creating and descendant in frozen:
            raise ConfigPatchError(
                "field is create-only and cannot be changed after creation",
                path=descendant,
            )


def _model_field_paths(
    model: type[Any],
    prefix: tuple[str, ...],
    ancestors: frozenset[type[Any]] = frozenset(),
) -> list[tuple[str, ...]]:
    if model in ancestors:
        return []
    paths: list[tuple[str, ...]] = []
    next_ancestors = ancestors | {model}
    for name, field in _model_fields(model).items():
        path = prefix + (name,)
        paths.append(path)
        nested = _unwrap_model(_field_annotation(field))
        if nested is not None:
            paths.extend(_model_field_paths(nested, path, next_ancestors))
    return paths


def _model_at_path(
    root_model: type[BaseModel],
    path: tuple[str, ...],
) -> type[Any] | None:
    model = root_model
    for part in path:
        field = _model_fields(model).get(part)
        if field is None:
            return None
        nested = _unwrap_model(_field_annotation(field))
        if nested is None:
            return None
        model = nested
    return model


def _field_at_path(
    root_model: type[BaseModel],
    path: tuple[str, ...],
) -> Any | None:
    """Return the declared field at a model-only path."""
    model = root_model
    for index, part in enumerate(path):
        field = _model_fields(model).get(part)
        if field is None:
            return None
        if index == len(path) - 1:
            return field
        nested = _unwrap_model(_field_annotation(field))
        if nested is None:
            return None
        model = nested
    return None


def _list_element_model(annotation: Any) -> type[Any] | None:
    """Resolve a typed list's model element, including Optional wrappers."""
    origin = typing.get_origin(annotation)
    if origin in (typing.Union, types.UnionType):
        for arg in typing.get_args(annotation):
            if arg is type(None):
                continue
            element = _list_element_model(arg)
            if element is not None:
                return element
        return None
    if origin not in (list, tuple, set, frozenset):
        return None
    args = typing.get_args(annotation)
    if not args:
        return None
    return _unwrap_model(args[0])


def _model_fields(model: type[Any]) -> dict[str, Any]:
    if isinstance(model, type) and issubclass(model, BaseModel):
        return dict(model.model_fields)
    if dataclasses.is_dataclass(model):
        return {field.name: field for field in dataclasses.fields(model)}
    return {}


def _field_annotation(field: Any) -> Any:
    return getattr(field, "annotation", getattr(field, "type", None))


def _unwrap_model(annotation: Any) -> type[Any] | None:
    origin = typing.get_origin(annotation)
    if origin in (typing.Union, types.UnionType):
        for arg in typing.get_args(annotation):
            if arg is type(None):
                continue
            nested = _unwrap_model(arg)
            if nested is not None:
                return nested
        return None
    if isinstance(annotation, type) and (
        issubclass(annotation, BaseModel) or dataclasses.is_dataclass(annotation)
    ):
        return annotation
    return None
