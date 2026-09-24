# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Declarative marker for config fields the runtime config API may touch.

A plain Pydantic ``Field`` is not on the config API surface: PATCH requests
targeting it are rejected. Every component of a writable nested path must use
:func:`RuntimeField`; marking a section does not grant access to its ordinary
``Field`` descendants. ``RuntimeField`` records lifecycle metadata so generic
code can decide what the API may write without per-field branching:

- ``dynamic`` — the field's writable lifecycle, a single boolean covering all
  mutability. ``True`` (default): writable both at creation and by later PATCH.
  ``False``: writable only when the owning object is first created (account or
  cluster config); any later PATCH that touches it is rejected. When a
  ``dynamic=False`` field is not given at creation it is fixed to its default
  (which may use the deprecated compatibility ``fallback`` behavior).
- ``fallback`` — deprecated compatibility metadata for legacy whole-section
  fallback. It selects one complete Cluster field when the corresponding
  top-level Account field is unset; it never recursively merges fields.

These attributes are stored on the field's ``json_schema_extra`` under the
``x-runtime-config`` key and read back through the helpers below. New Account
configuration should not use ``fallback``. When business behavior needs an
unset Account setting to use Cluster configuration, the business resolver
should combine the independently published Account and Cluster models.

Whether a field is cluster-wide or per-account is *not* one of these attributes:
it is decided by which model declares the field. Fields on ``OpenVikingConfig``
are cluster-wide; fields on ``AccountConfig`` are per-account. A field is never
made account-scoped by wrapping its type.
"""

from __future__ import annotations

import types
import typing
from dataclasses import is_dataclass
from typing import Any, Optional

from pydantic import BaseModel
from pydantic.fields import FieldInfo
from pydantic_core import PydanticUndefined

RUNTIME_CONFIG_KEY = "x-runtime-config"


def RuntimeField(  # noqa: N802
    *,
    dynamic: bool = True,
    fallback: Optional[str] = None,
    **kwargs: Any,
) -> Any:
    """Opt a config field into the runtime config API and declare its attributes.

    A single declaration carries both attributes below. Cluster-vs-account scope
    is not an argument here: it follows from which model declares the field
    (``OpenVikingConfig`` = cluster, ``AccountConfig`` = per-account).

    Args:
        dynamic: ``True`` (default) lets the field be set both at creation and by
            any later PATCH. ``False`` restricts writes to creation only; a later
            PATCH that touches it (or ``null``-deletes it via a parent) is
            rejected. A field declared with a plain ``Field`` — i.e. without
            ``RuntimeField`` — is never on the config surface at all.
        fallback: Deprecated compatibility-only Cluster field path. It applies
            only when a top-level Account section is entirely unset and returns
            that complete Cluster section; it does not merge nested fields.
            New code should leave this as ``None`` and implement defaults in its
            business resolver. Only meaningful on ``AccountConfig``.
    """
    from pydantic import Field

    extra = dict(kwargs.pop("json_schema_extra", {}) or {})
    extra[RUNTIME_CONFIG_KEY] = {
        "dynamic": dynamic,
        "fallback": fallback,
        "fallback_deprecated": fallback is not None,
    }
    return Field(json_schema_extra=extra, **kwargs)


def _runtime_meta(field: FieldInfo) -> Optional[dict]:
    extra = field.json_schema_extra
    if not isinstance(extra, dict):
        return None
    meta = extra.get(RUNTIME_CONFIG_KEY)
    return meta if isinstance(meta, dict) else None


def is_runtime_field(field: FieldInfo) -> bool:
    """Whether the field was declared with :func:`RuntimeField` (on the surface)."""
    return _runtime_meta(field) is not None


def is_dynamic(field: FieldInfo) -> bool:
    """Whether a RuntimeField is writable by PATCH after creation.

    ``False`` for a create-only field, and for any field not declared with
    :func:`RuntimeField` at all.
    """
    meta = _runtime_meta(field)
    return bool(meta and meta.get("dynamic"))


def fallback_of(field: FieldInfo) -> Optional[str]:
    """Return a legacy whole-section Cluster fallback path, else ``None``.

    Deprecated: this helper exists for compatibility with fields that already
    declare ``fallback``. New business code should resolve Account and Cluster
    configuration explicitly.
    """
    meta = _runtime_meta(field)
    return meta.get("fallback") if meta else None


def resolve_fallback(cluster: Any, path: str) -> Any:
    """Read a (possibly dotted) fallback path off the cluster config object."""
    value = cluster
    for part in path.split("."):
        value = getattr(value, part)
    return value


def _unwrap_model(annotation: Any) -> Optional[type[BaseModel]]:
    """Resolve the concrete ``BaseModel`` subclass an annotation points to.

    Handles ``Optional[...]``/``Union[...]`` wrappers so nested section fields
    can be traversed for validation; returns ``None`` for scalars and lists.
    """
    origin = typing.get_origin(annotation)
    if origin in (typing.Union, types.UnionType):
        for arg in typing.get_args(annotation):
            if arg is type(None):
                continue
            model = _unwrap_model(arg)
            if model is not None:
                return model
        return None
    if isinstance(annotation, type) and (
        issubclass(annotation, BaseModel) or is_dataclass(annotation)
    ):
        return annotation
    return None


def collect_runtime_field_paths(model: type[BaseModel]) -> set[tuple[str, ...]]:
    """Collect dotted field paths that are on ``model``'s config surface.

    Collect explicitly marked paths, including their marked parent sections,
    regardless of ``dynamic``. Every component of a writable nested path must
    be marked: a ``RuntimeField`` section does not implicitly make ordinary
    ``Field`` descendants writable.

    Whether a non-creating PATCH may write a path is further gated by
    ``dynamic`` (see :func:`collect_frozen_paths`). Cluster-vs-account scope is
    expressed by which model the field is declared on (``OpenVikingConfig`` vs
    ``AccountConfig``), so validating against the account model already confines
    the result to account fields.
    """
    paths: set[tuple[str, ...]] = set()

    def walk(cls: type[BaseModel], prefix: tuple[str, ...]) -> None:
        for name, field in model_fields(cls).items():
            if not is_runtime_field(field):
                continue
            path = prefix + (name,)
            nested = _unwrap_model(field.annotation)
            if nested is not None:
                walk(nested, path)
            paths.add(path)

    for name, field in model.model_fields.items():
        if not is_runtime_field(field):
            continue
        walk_root = _unwrap_model(field.annotation)
        path = (name,)
        if walk_root is not None:
            walk(walk_root, path)
        paths.add(path)
    return paths


def collect_frozen_paths(model: type[BaseModel]) -> set[tuple[str, ...]]:
    """Collect all field paths marked ``dynamic=False`` on ``model``."""
    paths: set[tuple[str, ...]] = set()

    def walk(cls: type[BaseModel], prefix: tuple[str, ...], ancestors: set[type]) -> None:
        if cls in ancestors:
            return
        next_ancestors = ancestors | {cls}
        for name, field in model_fields(cls).items():
            if not is_runtime_field(field):
                continue
            path = prefix + (name,)
            if not is_dynamic(field):
                paths.add(path)
            nested = _unwrap_model(field.annotation)
            if nested is not None:
                walk(nested, path, next_ancestors)

    walk(model, (), set())
    return paths


def model_fields(model: type) -> dict[str, FieldInfo]:
    """Expose declarations on both Pydantic and existing parser dataclasses."""
    if issubclass(model, BaseModel):
        return typing.cast(type[BaseModel], model).model_fields
    return {
        name: FieldInfo.from_annotated_attribute(
            annotation, getattr(model, name, PydanticUndefined)
        )
        for name, annotation in typing.get_type_hints(model, include_extras=True).items()
    }
