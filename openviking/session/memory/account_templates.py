# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Account-scoped template files with a restricted public editing contract."""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Awaitable, Callable, Coroutine
from copy import deepcopy
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

import yaml
from jinja2 import Environment, TemplateSyntaxError

from openviking.core.namespace_policy import SYSTEM_MEMORY_CUSTOM_ROOT
from openviking.pyagfs import AGFSAlreadyExistsError, AGFSNotFoundError, AsyncAGFSClient
from openviking.pyagfs.async_client import fs_ctx_from_agfs_path
from openviking.session.memory.dataclass import MemoryTypeSchema
from openviking.session.memory.memory_type_registry import MemoryTypeRegistry, get_default_registry
from openviking.session.memory.utils.content_template import (
    CONTENT_TEMPLATE_FIELDS,
    ContentTemplateError,
    validate_content_template,
)
from openviking.session.memory.utils.description_template import (
    MAX_DESCRIPTION_CHARS,
    DescriptionTemplateError,
    validate_description_template,
)
from openviking.storage.errors import LockAcquisitionError, ResourceBusyError
from openviking_cli.exceptions import FailedPreconditionError, InvalidArgumentError, NotFoundError
from openviking_cli.session.user_id import validate_account_id
from openviking_cli.utils.logger import get_logger

if TYPE_CHECKING:
    from openviking.storage.viking_fs import VikingFS

logger = get_logger(__name__)
_MAX_CONFIG_BYTES = 1024 * 1024
_TEMPLATE_LOCK_TIMEOUT_SECONDS = 10.0
_TEMPLATE_LOCK_RETRY_SECONDS = 0.05


class _ReadableTemplateDumper(yaml.SafeDumper):
    pass


def _represent_template_string(dumper: yaml.SafeDumper, value: str) -> yaml.nodes.ScalarNode:
    style = "|" if "\n" in value else None
    return dumper.represent_scalar("tag:yaml.org,2002:str", value, style=style)


_ReadableTemplateDumper.add_representer(str, _represent_template_string)


# Field names select existing fields; only their descriptions are editable.
EDITABLE_MEMORY_TEMPLATE_FIELDS = {
    "profile": ("content",),
    "events": ("event_name", "summary"),
    "preferences": ("topic", "content"),
    "entities": ("category", "name", "content"),
    "soul": ("core_truths", "boundaries", "vibe", "continuity"),
    "identity": ("creature", "name", "vibe", "avatar", "emoji", "introduction"),
}
_EDITABLE_CONTENT_TEMPLATES = set(CONTENT_TEMPLATE_FIELDS)


def _validate_memory_type(memory_type: str) -> None:
    if (
        not memory_type
        or any(c in memory_type for c in ("/", "\\", "\0"))
        or memory_type in {".", ".."}
    ):
        raise InvalidArgumentError("memory_type must be a single file name")


def account_memory_template_uri(memory_type: str) -> str:
    _validate_memory_type(memory_type)
    return f"{SYSTEM_MEMORY_CUSTOM_ROOT}/{memory_type}.yaml"


def _managed_memory_type_for_uri(uri: str) -> str | None:
    prefix = f"{SYSTEM_MEMORY_CUSTOM_ROOT}/"
    if not uri.startswith(prefix):
        return None
    filename = uri.removeprefix(prefix)
    if "/" in filename or not filename.endswith(".yaml"):
        return None
    memory_type = filename.removesuffix(".yaml")
    return memory_type or None


def normalize_account_memory_template_write(
    uri: str,
    content: str | bytes,
    mode: str,
    *,
    registry: MemoryTypeRegistry | None = None,
) -> str | bytes:
    """Enforce managed template rules and replace server-owned metadata."""
    memory_type = _managed_memory_type_for_uri(uri)
    if memory_type is None:
        return content
    registry = registry or get_default_registry()
    schema = registry.get(memory_type)
    if schema is None:
        return content
    if memory_type not in EDITABLE_MEMORY_TEMPLATE_FIELDS:
        raise InvalidArgumentError(f"Memory template is not editable: {memory_type}")
    if mode == "append":
        raise InvalidArgumentError("append is not supported for managed memory templates")

    try:
        raw = content.encode("utf-8") if isinstance(content, str) else content
        if len(raw) > _MAX_CONFIG_BYTES:
            raise ValueError("A memory template must not exceed 1 MiB")
        data = yaml.safe_load(raw)
        if not isinstance(data, dict):
            raise ValueError("Expected a YAML mapping")
        supplied = dict(data)
        supplied.pop("_updated_at", None)
        defaults = memory_template_data(schema)
        complete = _complete_template(defaults, supplied, memory_type)
        if complete != supplied:
            raise ValueError("generic writes require the complete deployment template")
        complete["_updated_at"] = datetime.now(timezone.utc).isoformat()
        normalized = yaml.dump(
            complete,
            Dumper=_ReadableTemplateDumper,
            allow_unicode=True,
            sort_keys=False,
        )
        normalized_raw = normalized.encode("utf-8")
        if len(normalized_raw) > _MAX_CONFIG_BYTES:
            raise ValueError("A memory template must not exceed 1 MiB")
        return normalized if isinstance(content, str) else normalized_raw
    except DescriptionTemplateError as exc:
        raise InvalidArgumentError(
            str(exc),
            details={"field": exc.field, "reason": exc.reason, "line": exc.line},
        ) from exc
    except ContentTemplateError as exc:
        raise InvalidArgumentError(
            str(exc),
            details={"field": "content_template", "reason": exc.reason, "line": exc.line},
        ) from exc
    except (
        UnicodeDecodeError,
        ValueError,
        TypeError,
        KeyError,
        AttributeError,
        yaml.YAMLError,
        TemplateSyntaxError,
    ) as exc:
        raise InvalidArgumentError(f"Invalid memory template: {exc}") from exc


def validate_account_memory_template_transfer(
    uri: str, *, registry: MemoryTypeRegistry | None = None
) -> None:
    """Reject cp/mv targets that would bypass a registered template contract."""
    memory_type = _managed_memory_type_for_uri(uri)
    if memory_type is None:
        return
    registry = registry or get_default_registry()
    if registry.get(memory_type) is not None:
        raise InvalidArgumentError(
            f"Registered memory templates must be updated through write or the admin API: {uri}"
        )


def account_memory_template_path(account_id: str, memory_type: str) -> str:
    error = validate_account_id(account_id)
    if error:
        raise InvalidArgumentError(error)
    _validate_memory_type(memory_type)
    relative_root = SYSTEM_MEMORY_CUSTOM_ROOT.removeprefix("viking://")
    return f"/local/{account_id}/{relative_root}/{memory_type}.yaml"


def legacy_account_memory_template_path(account_id: str, memory_type: str) -> str:
    error = validate_account_id(account_id)
    if error:
        raise InvalidArgumentError(error)
    _validate_memory_type(memory_type)
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


def _validate_template(
    data: dict,
    memory_type: str,
    *,
    validate_content: bool = True,
    validate_descriptions: bool = True,
    deployment_defaults: dict | None = None,
) -> MemoryTypeSchema:
    if data.get("memory_type") != memory_type:
        raise ValueError("memory_type must match the template in the request path")
    schema = MemoryTypeRegistry(load_schemas=False)._parse_memory_type(data)
    names = [field.name for field in schema.fields]
    if any(not name for name in names) or len(names) != len(set(names)):
        raise ValueError("Template field names must be nonempty and unique")
    defaults = deployment_defaults or {}
    if validate_descriptions:
        validate_description_template(schema.description)
        for field in schema.fields:
            validate_description_template(
                field.description, field=f"fields.{field.name}.description"
            )
    if memory_type in _EDITABLE_CONTENT_TEMPLATES and schema.content_template is not None:
        # Only an exact match to server-owned deployment defaults inherits the
        # legacy renderer. Never infer trust from persisted/client-supplied flags.
        schema._account_content_template = schema.content_template != defaults.get(
            "content_template"
        )
        if validate_content and schema._account_content_template:
            validate_content_template(schema.content_template, memory_type)
    # Other deployment-owned template fields retain their existing syntax check.
    env = Environment()
    for template in (
        schema.directory,
        schema.filename_template,
        schema.content_template,
        schema.embedding_template,
        schema.overview_template,
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
            # Python len(str) counts Unicode code points, not UTF-8 bytes.
            if key == "description" and len(value) > MAX_DESCRIPTION_CHARS:
                raise ValueError(f"{location} must not exceed 50000 Unicode characters")
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
    _validate_template(data, memory_type, deployment_defaults=defaults)
    return data


async def _read_raw(client: AsyncAGFSClient, path: str) -> bytes | None:
    raw = await _read_unchecked_raw(client, path)
    if raw is not None and len(raw) > _MAX_CONFIG_BYTES:
        raise FailedPreconditionError("Invalid persisted account memory template")
    return raw


async def _read_unchecked_raw(client: AsyncAGFSClient, path: str) -> bytes | None:
    try:
        result = await client.read(path)
    except AGFSNotFoundError:
        return None
    raw = getattr(result, "content", result)
    if isinstance(raw, str):
        raw = raw.encode("utf-8")
    if not isinstance(raw, bytes):
        raise FailedPreconditionError("Invalid persisted account memory template")
    return raw


def _parse_template(raw: bytes | None, memory_type: str) -> dict | None:
    if raw is None:
        return None
    try:
        data = yaml.safe_load(raw)
        if not isinstance(data, dict):
            raise ValueError("Expected a YAML mapping")
        # Keep well-formed older configurations readable/resettable even if their
        # Jinja is outside the new contract. Never execute them unchecked.
        _validate_template(data, memory_type, validate_content=False, validate_descriptions=False)
        return data
    except (ValueError, TypeError, AttributeError, yaml.YAMLError, TemplateSyntaxError) as exc:
        raise FailedPreconditionError(f"Invalid persisted memory template: {memory_type}") from exc


async def _finish_storage_call(
    operation: Coroutine[Any, Any, Any],
    *,
    on_cancel: Callable[[Any], Awaitable[None]] | None = None,
):
    """Drain submitted native I/O before propagating cancellation.

    Cancelling an asyncio waiter cannot stop its worker thread. Keep the worker's
    result reachable, and (for acquisition) release a lease won during cancellation.
    Locked writes include their own release in a finally block.
    """
    task = asyncio.create_task(operation)
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:

        async def finish():
            try:
                result = await task
            except Exception:
                logger.debug("Template storage call failed while cancelling", exc_info=True)
            else:
                if on_cancel is not None:
                    await on_cancel(result)

        cleanup = asyncio.create_task(finish())
        while not cleanup.done():
            try:
                await asyncio.shield(cleanup)
            except asyncio.CancelledError:
                # Repeated cancellation must not interrupt lease cleanup either.
                continue
        cleanup.result()
        raise


async def _acquire_template_lock(client: AsyncAGFSClient, paths: list[str], uri: str):
    # Keep the cross-process lock, but never wait for contention in a worker:
    # the holder needs that same executor to finish its I/O and release the lease.
    loop = asyncio.get_running_loop()
    deadline = loop.time() + _TEMPLATE_LOCK_TIMEOUT_SECONDS
    primary_path = paths[0]

    async def release_cancelled_acquire(lease):
        await client.pathlock_release(lease, fs_ctx=fs_ctx_from_agfs_path(primary_path))

    while True:
        try:
            return await _finish_storage_call(
                client.pathlock_acquire_exact_batch(paths, timeout_secs=0.0),
                on_cancel=release_cancelled_acquire,
            )
        except LockAcquisitionError as exc:
            remaining = deadline - loop.time()
            if remaining > 0:
                await asyncio.sleep(min(_TEMPLATE_LOCK_RETRY_SECONDS, remaining))
                if loop.time() < deadline:
                    continue
            raise ResourceBusyError(
                "Another memory template operation is in progress. Please retry.",
                uri=uri,
                conflict_type="memory_templates_busy",
            ) from exc


async def read_account_memory_template(
    viking_fs: VikingFS, account_id: str, memory_type: str
) -> dict | None:
    path = account_memory_template_path(account_id, memory_type)
    legacy_path = legacy_account_memory_template_path(account_id, memory_type)
    client = AsyncAGFSClient(viking_fs.agfs)
    # Publication switches a fully-written staging file into place. Readers may
    # see either complete version (or no override after DELETE), without a lock.
    raw = await _read_raw(client, path)
    if raw is not None:
        return _parse_template(raw, memory_type)
    return _parse_template(await _read_raw(client, legacy_path), memory_type)


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
    encoded = None
    if template is not None:
        try:
            complete = _complete_template(defaults, template, memory_type)
        except DescriptionTemplateError as exc:
            raise InvalidArgumentError(
                str(exc),
                details={"field": exc.field, "reason": exc.reason, "line": exc.line},
            ) from exc
        except ContentTemplateError as exc:
            raise InvalidArgumentError(
                str(exc),
                details={"field": "content_template", "reason": exc.reason, "line": exc.line},
            ) from exc
        except (ValueError, TypeError, KeyError, AttributeError, TemplateSyntaxError) as exc:
            raise InvalidArgumentError(f"Invalid memory template: {exc}") from exc
        # Saving an unchanged default form (including per-field resets) removes
        # the override through the same locked path as DELETE. Compare before
        # adding publication metadata; any remaining edit keeps it custom.
        if complete == defaults:
            complete = None
        else:
            complete["_updated_at"] = datetime.now(timezone.utc).isoformat()
            encoded = yaml.dump(
                complete,
                Dumper=_ReadableTemplateDumper,
                allow_unicode=True,
                sort_keys=False,
            ).encode("utf-8")
            if len(encoded) > _MAX_CONFIG_BYTES:
                raise InvalidArgumentError("A memory template must not exceed 1 MiB")

    path = account_memory_template_path(account_id, memory_type)
    legacy_path = legacy_account_memory_template_path(account_id, memory_type)
    backup_path = f"{path}.backup"
    staging_path = f"{path}.{uuid.uuid4().hex}.tmp" if complete is not None else None
    client = AsyncAGFSClient(viking_fs.agfs)
    lock_paths = [path, legacy_path, backup_path]
    if staging_path is not None:
        lock_paths.append(staging_path)
    lease = await _acquire_template_lock(
        client, lock_paths, account_memory_template_uri(memory_type)
    )
    return await _finish_storage_call(
        _update_locked_template(
            client,
            path,
            legacy_path,
            backup_path,
            staging_path,
            memory_type,
            complete,
            encoded,
            lease,
        )
    )


async def _publish_template(
    client: AsyncAGFSClient, path: str, staging_path: str, encoded: bytes, fs_ctx: dict
) -> None:
    """Publish a complete file through same-mount AGFS rename/object replacement.

    The caller's lease covers BOTH paths. LocalFS renames the staged file; S3
    copies the whole staged object to the destination before deleting the source.
    Never overwrite/restore the active file in place, including on failure.
    """
    try:
        await client.write(staging_path, encoded, fs_ctx=fs_ctx)
        try:
            await client.mv(staging_path, path, fs_ctx=fs_ctx)
        except Exception:
            # An S3 move can publish successfully and then fail deleting its
            # source. Acknowledge only a verified published version; if state
            # cannot be verified, propagate the error without a destructive rollback.
            try:
                published = await _read_raw(client, path) == encoded
            except Exception:
                published = False
            if not published:
                raise
            logger.warning("Template move reported an error after publication: %s", path)
    finally:
        try:
            await client.rm(staging_path, fs_ctx=fs_ctx)
        except AGFSNotFoundError:
            pass
        except Exception:
            # Cleanup cannot undo a published version or mask the original error.
            logger.warning(
                "Failed to clean up staged memory template: %s", staging_path, exc_info=True
            )


async def _update_locked_template(
    client: AsyncAGFSClient,
    path: str,
    legacy_path: str,
    backup_path: str,
    staging_path: str | None,
    memory_type: str,
    complete: dict | None,
    encoded: bytes | None,
    lease,
):
    fs_ctx = fs_ctx_from_agfs_path(path)
    lease_ref = getattr(lease, "lease_ref", None) or getattr(lease, "id", None)
    if isinstance(lease, dict):
        lease_ref = lease.get("lease_ref", lease_ref)
    if lease_ref:
        fs_ctx["lease_ref"] = lease_ref
    try:
        raw = await _read_unchecked_raw(client, path)
        legacy_raw = await _read_unchecked_raw(client, legacy_path)
        active_raw = raw if raw is not None else legacy_raw
        current = None
        if active_raw is not None and len(active_raw) <= _MAX_CONFIG_BYTES:
            try:
                current = _parse_template(active_raw, memory_type)
            except FailedPreconditionError:
                # Explicit PUT/DELETE is the recovery path for damaged persisted
                # content, so allow the operation to replace or remove it.
                pass
        if complete is None and active_raw is None:
            await _remove_obsolete_backup(client, backup_path, fs_ctx)
            return None
        if complete is not None and raw is not None and current is not None:
            if {k: v for k, v in current.items() if k != "_updated_at"} == {
                k: v for k, v in complete.items() if k != "_updated_at"
            }:
                if legacy_raw is not None:
                    try:
                        await client.rm(legacy_path, fs_ctx=fs_ctx)
                    except AGFSNotFoundError:
                        pass
                    except Exception as exc:
                        raise FailedPreconditionError(
                            "Memory template is published, but legacy cleanup failed. "
                            "Retry the request.",
                            details={
                                "published": True,
                                "legacy_cleanup_pending": True,
                                "retryable": True,
                            },
                        ) from exc
                await _remove_obsolete_backup(client, backup_path, fs_ctx)
                return current
        try:
            await client.ensure_parent_dirs(path)
        except AGFSAlreadyExistsError:
            pass
        if complete is None:
            try:
                await client.rm(legacy_path, fs_ctx=fs_ctx)
            except AGFSNotFoundError:
                pass
            except Exception as exc:
                raise FailedPreconditionError(
                    "Failed to remove the legacy memory template. Retry the request.",
                    details={"legacy_cleanup_pending": True, "retryable": True},
                ) from exc
            try:
                await client.rm(path, fs_ctx=fs_ctx)
            except AGFSNotFoundError:
                pass
            except Exception as exc:
                raise FailedPreconditionError(
                    "Failed to remove the current memory template. Retry the request.",
                    details={"current_cleanup_pending": True, "retryable": True},
                ) from exc
        else:
            assert staging_path is not None and encoded is not None
            await _publish_template(client, path, staging_path, encoded, fs_ctx)
            try:
                await client.rm(legacy_path, fs_ctx=fs_ctx)
            except AGFSNotFoundError:
                pass
            except Exception as exc:
                raise FailedPreconditionError(
                    "Memory template is published, but legacy cleanup failed. Retry the request.",
                    details={
                        "published": True,
                        "legacy_cleanup_pending": True,
                        "retryable": True,
                    },
                ) from exc
        await _remove_obsolete_backup(client, backup_path, fs_ctx)
        logger.info("Updated account memory template: %s reset=%s", path, complete is None)
        return complete
    finally:
        await client.pathlock_release(lease, fs_ctx=fs_ctx_from_agfs_path(path))


async def _remove_obsolete_backup(client: AsyncAGFSClient, backup_path: str, fs_ctx: dict) -> None:
    try:
        await client.rm(backup_path, fs_ctx=fs_ctx)
    except AGFSNotFoundError:
        pass
    except Exception:
        logger.warning("Failed to remove obsolete memory template backup: %s", backup_path)


async def resolve_account_memory_registry(
    viking_fs: VikingFS, account_id: str, registry: MemoryTypeRegistry
) -> MemoryTypeRegistry:
    """Snapshot account types over server-owned deployment defaults, without mutating them."""
    schemas = registry.list_all(include_disabled=True)
    templates = await asyncio.gather(
        *(read_account_memory_template(viking_fs, account_id, s.memory_type) for s in schemas)
    )
    resolved = MemoryTypeRegistry(load_schemas=False)
    for schema, template in zip(schemas, templates, strict=True):
        try:
            resolved.register(
                _validate_template(
                    template,
                    schema.memory_type,
                    deployment_defaults=memory_template_data(schema),
                )
                if template is not None
                else schema.model_copy(deep=True)
            )
        except (ContentTemplateError, DescriptionTemplateError) as exc:
            raise FailedPreconditionError(
                f"Invalid account {getattr(exc, 'field', 'content_template')} "
                f"for {schema.memory_type}; republish or reset it"
            ) from exc
    return resolved
