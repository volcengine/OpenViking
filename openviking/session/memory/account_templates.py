# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Account-scoped template files with a restricted public editing contract."""

from __future__ import annotations

import asyncio
from copy import deepcopy
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

import yaml
from jinja2 import Environment, TemplateSyntaxError

from openviking.pyagfs import AGFSAlreadyExistsError, AGFSNotFoundError, AsyncAGFSClient
from openviking.pyagfs.async_client import fs_ctx_from_agfs_path
from openviking.session.memory.dataclass import MemoryTypeSchema
from openviking.session.memory.memory_type_registry import MemoryTypeRegistry
from openviking.storage.errors import LockAcquisitionError, ResourceBusyError
from openviking_cli.exceptions import FailedPreconditionError, InvalidArgumentError, NotFoundError
from openviking_cli.session.user_id import validate_account_id
from openviking_cli.utils.logger import get_logger

if TYPE_CHECKING:
    from openviking.storage.viking_fs import VikingFS

logger = get_logger(__name__)
_MAX_CONFIG_BYTES = 1024 * 1024

# Field names select existing fields; only their descriptions are editable.
EDITABLE_MEMORY_TEMPLATE_FIELDS = {
    "profile": ("content",),
    "events": ("event_name", "summary"),
    "preferences": ("topic", "content"),
    "entities": ("category", "name", "content"),
    "soul": ("core_truths", "boundaries", "vibe", "continuity"),
    "identity": ("creature", "name", "vibe", "avatar", "emoji", "introduction"),
}
_EDITABLE_CONTENT_TEMPLATES = {"events", "soul", "identity"}


def account_memory_template_path(account_id: str, memory_type: str) -> str:
    error = validate_account_id(account_id)
    if error:
        raise InvalidArgumentError(error)
    if (
        not memory_type
        or any(c in memory_type for c in ("/", "\\", "\0"))
        or memory_type in {".", ".."}
    ):
        raise InvalidArgumentError("memory_type must be a single file name")
    return f"/local/{account_id}/_system/memory_templates/{memory_type}.yaml"


def memory_template_data(schema: MemoryTypeSchema) -> dict[str, Any]:
    """Serialize using the existing YAML vocabulary, including field 'type'."""
    data = schema.model_dump(mode="json")
    for field in data["fields"]:
        field["type"] = field.pop("field_type")
    return data


def default_memory_template(registry: MemoryTypeRegistry, memory_type: str) -> dict[str, Any]:
    """Return deployment defaults for a type exposed by the management API."""
    schema = registry.get(memory_type)
    if schema is None:
        raise NotFoundError(f"Memory template not found: {memory_type}")
    if memory_type not in EDITABLE_MEMORY_TEMPLATE_FIELDS:
        raise InvalidArgumentError(f"Memory template is not editable: {memory_type}")
    return memory_template_data(schema)


def _validate_template(data: dict, memory_type: str) -> MemoryTypeSchema:
    if data.get("memory_type") != memory_type:
        raise ValueError("memory_type must match the template in the request path")
    schema = MemoryTypeRegistry(load_schemas=False)._parse_memory_type(data)
    names = [field.name for field in schema.fields]
    if any(not name for name in names) or len(names) != len(set(names)):
        raise ValueError("Template field names must be nonempty and unique")
    # Validate syntax without evaluating templates or restricting variables.
    env = Environment()
    for template in (
        schema.description,
        schema.directory,
        schema.filename_template,
        schema.content_template,
        schema.embedding_template,
        schema.overview_template,
        *(field.description for field in schema.fields),
    ):
        if template is not None:
            env.parse(template)
    return schema


def _apply_editable_values(target: dict, supplied: dict, editable: set[str], path: str) -> None:
    for key, value in supplied.items():
        location = f"{path}.{key}" if path else key
        if key not in target:
            raise ValueError(f"Unknown configuration field: {location}")
        if key in editable:
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{location} must be a nonempty string")
            target[key] = value
        elif type(value) is not type(target[key]) or value != target[key]:
            # Unchanged locked values are accepted so GET effective -> PUT works.
            raise ValueError(f"{location} is locked and must match the default template")


def _complete_template(defaults: dict, supplied: dict, memory_type: str) -> dict:
    data = deepcopy(defaults)
    editable = {"description"}
    if memory_type in _EDITABLE_CONTENT_TEMPLATES:
        editable.add("content_template")
    _apply_editable_values(
        data, {key: value for key, value in supplied.items() if key != "fields"}, editable, ""
    )
    if "fields" in supplied:
        if not isinstance(supplied["fields"], list):
            raise ValueError("fields must be a list")
        fields = {field["name"]: field for field in data["fields"]}
        seen = set()
        for field in supplied["fields"]:
            if not isinstance(field, dict) or not isinstance(field.get("name"), str):
                raise ValueError("Each fields entry must identify an existing field by name")
            name = field["name"]
            if name not in fields:
                raise ValueError(f"Unknown template field: fields.{name}")
            if name in seen:
                raise ValueError(f"Duplicate template field: fields.{name}")
            seen.add(name)
            editable = (
                {"description"} if name in EDITABLE_MEMORY_TEMPLATE_FIELDS[memory_type] else set()
            )
            _apply_editable_values(fields[name], field, editable, f"fields.{name}")
    # Never remove, reorder, or replace fields omitted from a partial request.
    _validate_template(data, memory_type)
    return data


async def _read_raw(client: AsyncAGFSClient, path: str) -> bytes | None:
    try:
        result = await client.read(path)
    except AGFSNotFoundError:
        return None
    raw = getattr(result, "content", result)
    if isinstance(raw, str):
        raw = raw.encode("utf-8")
    if not isinstance(raw, bytes) or len(raw) > _MAX_CONFIG_BYTES:
        raise FailedPreconditionError("Invalid persisted account memory template")
    return raw


def _parse_template(raw: bytes | None, memory_type: str) -> dict | None:
    if raw is None:
        return None
    try:
        data = yaml.safe_load(raw)
        if not isinstance(data, dict):
            raise ValueError("Expected a YAML mapping")
        _validate_template(data, memory_type)
        return data
    except (ValueError, TypeError, AttributeError, yaml.YAMLError, TemplateSyntaxError) as exc:
        raise FailedPreconditionError(f"Invalid persisted memory template: {memory_type}") from exc


async def _acquire_template_lock(client: AsyncAGFSClient, path: str):
    # Read and write share a lock: not every backend replaces file content atomically.
    try:
        return await client.pathlock_acquire_exact(path, timeout_secs=10.0)
    except LockAcquisitionError as exc:
        raise ResourceBusyError(
            "Another memory template operation is in progress. Please retry.",
            uri=path,
            conflict_type="memory_templates_busy",
        ) from exc


async def read_account_memory_template(
    viking_fs: VikingFS, account_id: str, memory_type: str
) -> dict | None:
    path = account_memory_template_path(account_id, memory_type)
    client = AsyncAGFSClient(viking_fs.agfs)
    lease = await _acquire_template_lock(client, path)
    try:
        return _parse_template(await _read_raw(client, path), memory_type)
    finally:
        await client.pathlock_release(lease)


def memory_template_result(
    registry: MemoryTypeRegistry, custom: dict | None, memory_type: str
) -> dict:
    defaults = default_memory_template(registry, memory_type)
    effective = dict(custom) if custom is not None else deepcopy(defaults)
    updated_at = effective.pop("_updated_at", None)
    return {
        "memory_type": memory_type,
        "status": "custom" if custom is not None else "system_default",
        "updated_at": updated_at,
        "defaults": defaults,
        "effective": effective,
    }


async def update_account_memory_template(
    viking_fs: VikingFS,
    account_id: str,
    memory_type: str,
    template: dict | None,
    registry: MemoryTypeRegistry,
) -> dict | None:
    """Publish a full template, or delete its override to restore deployment defaults."""
    defaults = default_memory_template(registry, memory_type)
    complete = None
    if template is not None:
        try:
            complete = _complete_template(defaults, template, memory_type)
        except (ValueError, TypeError, KeyError, AttributeError, TemplateSyntaxError) as exc:
            raise InvalidArgumentError(f"Invalid memory template: {exc}") from exc
        complete["_updated_at"] = datetime.now(timezone.utc).isoformat()
        encoded = yaml.safe_dump(complete, allow_unicode=True, sort_keys=False).encode("utf-8")
        if len(encoded) > _MAX_CONFIG_BYTES:
            raise InvalidArgumentError("A memory template must not exceed 1 MiB")

    path = account_memory_template_path(account_id, memory_type)
    client = AsyncAGFSClient(viking_fs.agfs)
    lease = await _acquire_template_lock(client, path)
    fs_ctx = fs_ctx_from_agfs_path(path)
    lease_ref = getattr(lease, "lease_ref", None) or getattr(lease, "id", None)
    if isinstance(lease, dict):
        lease_ref = lease.get("lease_ref", lease_ref)
    if lease_ref:
        fs_ctx["lease_ref"] = lease_ref
    try:
        raw = await _read_raw(client, path)
        current = _parse_template(raw, memory_type)
        if complete is None and current is None:
            return None
        if complete is not None and current is not None:
            if {k: v for k, v in current.items() if k != "_updated_at"} == {
                k: v for k, v in complete.items() if k != "_updated_at"
            }:
                return current
        try:
            await client.ensure_parent_dirs(path)
        except AGFSAlreadyExistsError:
            pass
        if raw is not None:
            await client.write(path + ".backup", raw)
        try:
            if complete is None:
                await client.rm(path, fs_ctx=fs_ctx)
            else:
                await client.write(path, encoded, fs_ctx=fs_ctx)
        except Exception:
            try:
                if raw is None:
                    await client.rm(path, fs_ctx=fs_ctx)
                else:
                    await client.write(path, raw, fs_ctx=fs_ctx)
            except AGFSNotFoundError:
                pass
            except Exception:
                logger.exception("Failed to roll back memory template: %s", path)
            raise
        logger.info("Updated account memory template: %s reset=%s", path, complete is None)
        return complete
    finally:
        await client.pathlock_release(lease)


async def resolve_account_memory_registry(
    viking_fs: VikingFS, account_id: str, registry: MemoryTypeRegistry
) -> MemoryTypeRegistry:
    """Snapshot registered types before filtering, without mutating shared defaults."""
    schemas = registry.list_all(include_disabled=True)
    templates = await asyncio.gather(
        *(read_account_memory_template(viking_fs, account_id, s.memory_type) for s in schemas)
    )
    resolved = MemoryTypeRegistry(load_schemas=False)
    for schema, template in zip(schemas, templates, strict=True):
        resolved.register(
            _validate_template(template, schema.memory_type)
            if template is not None
            else schema.model_copy(deep=True)
        )
    return resolved
