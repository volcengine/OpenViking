# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""OVPack dense vector snapshot helpers."""

from __future__ import annotations

import hashlib
import struct
import zipfile
from typing import Any

from openviking.core.namespace import context_type_for_uri, owner_fields_for_uri
from openviking.server.identity import RequestContext
from openviking.storage.ovpack.format import (
    OVPACK_DENSE_PATH,
    dense_values_bytes,
    internal_zip_path,
    join_uri,
    sha256_hex,
)
from openviking.storage.ovpack.manifest import manifest_dense_info
from openviking.storage.ovpack.policy import NON_VECTOR_SCOPES
from openviking.storage.ovpack.validation import dense_record_count, record_dense_ref
from openviking.utils.time_utils import get_current_timestamp
from openviking_cli.exceptions import InvalidArgumentError
from openviking_cli.utils.logger import get_logger
from openviking_cli.utils.uri import VikingURI

logger = get_logger(__name__)


def _index_meta_is_hybrid(index_meta: Any) -> bool:
    if not isinstance(index_meta, dict):
        return False
    vector_index = index_meta.get("VectorIndex")
    if not isinstance(vector_index, dict):
        return False
    index_type = str(vector_index.get("IndexType") or "").lower()
    return "hybrid" in index_type


def _vector_store_index_meta_is_hybrid(vector_store: Any) -> bool:
    candidates = [vector_store, getattr(vector_store, "_manager", None)]
    for candidate in candidates:
        if candidate is None:
            continue
        index_name = getattr(candidate, "_index_name", None)
        try:
            backend = candidate._get_default_backend()
            index_name = index_name or getattr(backend, "_index_name", None)
            collection = backend._get_collection()
            if index_name and hasattr(collection, "get_index_meta_data"):
                if _index_meta_is_hybrid(collection.get_index_meta_data(index_name)):
                    return True
        except Exception:
            continue
    return False


def dense_snapshot_unsupported_reason(vector_store=None) -> str | None:
    """Return why dense vector snapshots are unsupported in the current runtime."""
    if _vector_store_index_meta_is_hybrid(vector_store):
        return "current vector index type is hybrid"

    return None


def ensure_dense_snapshot_supported(vector_store=None) -> None:
    reason = dense_snapshot_unsupported_reason(vector_store)
    if reason:
        raise InvalidArgumentError(
            "ovpack vector snapshots only support pure dense vector indexes",
            details={"reason": reason},
        )


def embedding_snapshot_metadata(dimensions: int | None) -> dict[str, Any]:
    metadata: dict[str, Any] = {}
    try:
        from openviking_cli.utils.config import get_openviking_config

        embedding_cfg = get_openviking_config().embedding
        model_cfg = embedding_cfg.hybrid or embedding_cfg.dense
        if model_cfg:
            metadata = {
                "provider": model_cfg.provider,
                "model": model_cfg.model,
                "input": model_cfg.input,
                "query_param": model_cfg.query_param,
                "document_param": model_cfg.document_param,
            }
            if dimensions is None:
                dimensions = model_cfg.get_effective_dimension()
    except Exception:
        pass

    if dimensions is not None:
        metadata["dimensions"] = dimensions
    return {key: value for key, value in metadata.items() if value is not None}


def build_dense_snapshot_manifest(
    index_records: list[dict[str, Any]],
    dense_values: list[float],
) -> tuple[bytes, dict[str, Any]] | None:
    if not dense_values:
        return None

    dense_bytes = dense_values_bytes(dense_values)
    dense_dimensions_set: set[int] = set()
    dense_count = 0
    for record in index_records:
        dense = record_dense_ref(record)
        if dense is None:
            continue
        dense_dimensions_set.add(dense["dimensions"])
        dense_count += 1
    if len(dense_dimensions_set) != 1:
        raise InvalidArgumentError(
            "Cannot export ovpack vectors with mixed dimensions",
            details={"dimensions": sorted(dense_dimensions_set)},
        )
    dense_dimensions = next(iter(dense_dimensions_set))
    return dense_bytes, {
        "path": OVPACK_DENSE_PATH,
        "count": dense_count,
        "dtype": "float32",
        "byte_order": "little",
        "dimensions": dense_dimensions,
        "sha256": sha256_hex(dense_bytes),
        "embedding": embedding_snapshot_metadata(dense_dimensions),
    }


def read_dense_vectors(
    zf: zipfile.ZipFile,
    manifest: dict[str, Any],
    base_name: str,
    index_records: list[dict[str, Any]],
) -> dict[str, list[float]]:
    dense_info = manifest_dense_info(manifest)
    if dense_info is None:
        return {}
    data = zf.read(internal_zip_path(base_name, OVPACK_DENSE_PATH))
    vectors: dict[str, list[float]] = {}
    for record in index_records:
        record_id = record.get("record_id")
        dense = record_dense_ref(record)
        if not isinstance(record_id, str) or dense is None:
            continue
        offset = dense["offset"] * 4
        dimensions = dense["dimensions"]
        values = struct.unpack_from(f"<{dimensions}f", data, offset)
        vectors[record_id] = [float(value) for value in values]
    return vectors


def current_embedding_metadata() -> dict[str, Any]:
    try:
        from openviking_cli.utils.config import get_openviking_config

        embedding_cfg = get_openviking_config().embedding
        model_cfg = embedding_cfg.hybrid or embedding_cfg.dense
        if not model_cfg:
            return {}
        return {
            "provider": model_cfg.provider,
            "model": model_cfg.model,
            "input": model_cfg.input,
            "query_param": model_cfg.query_param,
            "document_param": model_cfg.document_param,
            "dimensions": model_cfg.get_effective_dimension(),
        }
    except Exception:
        return {}


def _embedding_snapshot_compatible(manifest: dict[str, Any]) -> tuple[bool, str]:
    dense_info = manifest_dense_info(manifest)
    if dense_info is None:
        return False, "missing dense vector snapshot"

    package_embedding = dense_info.get("embedding")
    if not isinstance(package_embedding, dict):
        return False, "missing embedding metadata"

    current_embedding = current_embedding_metadata()
    if not current_embedding:
        return False, "current embedding metadata is unavailable"

    fields = ("provider", "model", "input", "query_param", "document_param", "dimensions")
    for field in fields:
        package_value = package_embedding.get(field)
        current_value = current_embedding.get(field)
        if package_value != current_value:
            return False, f"embedding {field} mismatch"
    return True, ""


def choose_vector_restore_action(
    manifest: dict[str, Any],
    index_records: list[dict[str, Any]],
    dense_vectors: dict[str, list[float]],
    *,
    vector_store,
    vector_mode: str,
) -> str:
    if vector_mode == "recompute":
        return "recompute"

    unsupported_reason = dense_snapshot_unsupported_reason(vector_store)
    if unsupported_reason:
        if vector_mode == "require":
            raise InvalidArgumentError(
                "ovpack dense vector snapshot cannot be restored into a sparse or hybrid index",
                details={"reason": unsupported_reason},
            )
        logger.info(
            "[ovpack] Recomputing vectors because dense snapshot restore is unsupported: "
            f"{unsupported_reason}"
        )
        return "recompute"

    dense_count = dense_record_count(index_records)
    if dense_count == 0 or not dense_vectors:
        if vector_mode == "require":
            raise InvalidArgumentError(
                "ovpack package does not contain a dense vector snapshot",
                details={"vector_mode": vector_mode},
            )
        return "recompute"

    if dense_count != len(dense_vectors):
        if vector_mode == "require":
            raise InvalidArgumentError("ovpack dense vector snapshot is incomplete")
        return "recompute"

    if not vector_store or not hasattr(vector_store, "upsert"):
        if vector_mode == "require":
            raise InvalidArgumentError("Vector restore requires a writable vector store")
        return "recompute"

    compatible, reason = _embedding_snapshot_compatible(manifest)
    if not compatible:
        if vector_mode == "require":
            raise InvalidArgumentError(
                "ovpack dense vector snapshot is incompatible with current embedding config",
                details={"reason": reason},
            )
        logger.info(f"[ovpack] Recomputing vectors because snapshot is incompatible: {reason}")
        return "recompute"

    return "restore"


def _vector_record_id(target_uri: str, level: int, ctx: RequestContext) -> str:
    if level == 0:
        seed_uri = (
            target_uri if target_uri.endswith("/.abstract.md") else f"{target_uri}/.abstract.md"
        )
    elif level == 1:
        seed_uri = (
            target_uri if target_uri.endswith("/.overview.md") else f"{target_uri}/.overview.md"
        )
    else:
        seed_uri = target_uri
    return hashlib.md5(f"{ctx.account_id}:{seed_uri}".encode("utf-8")).hexdigest()


async def _upsert_vector_snapshot_record(
    vector_store,
    target_uri: str,
    record: dict[str, Any],
    dense_vector: list[float],
    ctx: RequestContext,
) -> None:
    level = int(record.get("level", 2))
    scalars = dict(record.get("scalars") or {})
    owner_fields = owner_fields_for_uri(target_uri, ctx=ctx)
    timestamp = get_current_timestamp()
    payload = {
        **scalars,
        "id": _vector_record_id(target_uri, level, ctx),
        "uri": target_uri,
        "context_type": context_type_for_uri(target_uri),
        "level": level,
        "created_at": timestamp,
        "updated_at": timestamp,
        "active_count": 0,
        "account_id": ctx.account_id,
        "owner_user_id": owner_fields.get("owner_user_id"),
        "owner_agent_id": owner_fields.get("owner_agent_id"),
        "vector": dense_vector,
    }
    if not payload.get("abstract"):
        payload["abstract"] = str(record.get("text") or "")

    try:
        await vector_store.upsert(payload, ctx=ctx)
    except TypeError:
        await vector_store.upsert(payload)


async def restore_vector_snapshot(
    vector_store,
    root_uri: str,
    index_records: list[dict[str, Any]],
    dense_vectors: dict[str, list[float]],
    ctx: RequestContext,
) -> None:
    for record in index_records:
        record_id = record.get("record_id")
        if not isinstance(record_id, str) or record_id not in dense_vectors:
            continue
        rel_path = record.get("path")
        if not isinstance(rel_path, str):
            continue
        target_uri = join_uri(root_uri, rel_path)
        if VikingURI(target_uri).scope in NON_VECTOR_SCOPES:
            continue
        await _upsert_vector_snapshot_record(
            vector_store,
            target_uri,
            record,
            dense_vectors[record_id],
            ctx,
        )
