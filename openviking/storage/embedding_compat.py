# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Embedding compatibility metadata for vector collections."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from openviking_cli.exceptions import EmbeddingCompatibilityError
from openviking_cli.utils import get_logger

logger = get_logger(__name__)

EMBEDDING_META_FILE = "embedding_meta.json"
EMBEDDING_META_VERSION = 1


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def resolve_embedding_meta_path(vectordb_config: Any) -> Optional[Path]:
    """Return the local sidecar metadata path when supported."""
    if getattr(vectordb_config, "backend", "") != "local":
        return None

    workspace = getattr(vectordb_config, "path", None)
    collection_name = getattr(vectordb_config, "name", None) or "context"
    if not workspace:
        return None

    return Path(workspace) / "vectordb" / collection_name / EMBEDDING_META_FILE


def build_embedding_metadata(config: Any) -> dict[str, Any]:
    """Build the persisted metadata payload for the active embedding config."""
    embedding_identity = config.embedding.compatibility_identity()
    return {
        "schema_version": EMBEDDING_META_VERSION,
        "collection_name": config.storage.vectordb.name or "context",
        "vectordb_backend": config.storage.vectordb.backend,
        "embedding": embedding_identity,
        "recorded_at": _utc_now_iso(),
    }


def load_embedding_metadata(vectordb_config: Any) -> Optional[dict[str, Any]]:
    """Load embedding metadata from the local sidecar file."""
    meta_path = resolve_embedding_meta_path(vectordb_config)
    if meta_path is None or not meta_path.exists():
        return None

    return json.loads(meta_path.read_text(encoding="utf-8"))


def persist_embedding_metadata(
    config: Any, payload: Optional[dict[str, Any]] = None
) -> Optional[Path]:
    """Persist embedding metadata for the active config when backend supports it."""
    meta_path = resolve_embedding_meta_path(config.storage.vectordb)
    if meta_path is None:
        return None

    meta_path.parent.mkdir(parents=True, exist_ok=True)
    data = dict(payload or build_embedding_metadata(config))
    data["recorded_at"] = _utc_now_iso()
    meta_path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return meta_path


def _extract_vector_dim(collection_info: Optional[dict[str, Any]]) -> Optional[int]:
    if not collection_info:
        return None
    vector_dim = collection_info.get("vector_dim")
    if isinstance(vector_dim, int) and vector_dim > 0:
        return vector_dim
    return None


def _extract_count(collection_info: Optional[dict[str, Any]]) -> int:
    if not collection_info:
        return 0
    count = collection_info.get("count")
    if isinstance(count, int) and count >= 0:
        return count
    return 0


def _format_component(component: Optional[dict[str, Any]]) -> str:
    if not component:
        return "none"
    provider = component.get("provider") or "unknown"
    model = component.get("model") or "unknown"
    dimension = component.get("dimension")
    extras: list[str] = []
    if isinstance(dimension, int) and dimension > 0:
        extras.append(f"dim={dimension}")
    if component.get("query_param"):
        extras.append(f"query={component['query_param']}")
    if component.get("document_param"):
        extras.append(f"document={component['document_param']}")
    if component.get("version"):
        extras.append(f"version={component['version']}")
    if component.get("input"):
        extras.append(f"input={component['input']}")
    suffix = f" ({', '.join(extras)})" if extras else ""
    return f"{provider}/{model}{suffix}"


def format_embedding_identity(identity: Optional[dict[str, Any]]) -> str:
    """Render a human-readable embedding identity summary."""
    if not identity:
        return "unknown"
    parts = [identity.get("mode", "unknown")]
    for key in ("dense", "sparse", "hybrid"):
        component = identity.get(key)
        if component:
            parts.append(f"{key}={_format_component(component)}")
    text_source = identity.get("text_source")
    if text_source:
        parts.append(f"text_source={text_source}")
    return ", ".join(parts)


def _build_rebuild_command(config_path: Optional[str]) -> str:
    cmd = ["openviking-rebuild-vectors", "--all-accounts"]
    if config_path:
        cmd.extend(["--config", config_path])
    return " ".join(cmd)


def _raise_mismatch(
    *,
    previous_payload: Optional[dict[str, Any]],
    current_payload: dict[str, Any],
    config_path: Optional[str],
    metadata_path: Optional[Path],
    reason: str,
) -> None:
    previous_identity = (previous_payload or {}).get("embedding")
    current_identity = current_payload.get("embedding")
    rebuild_command = _build_rebuild_command(config_path)
    meta_str = str(metadata_path) if metadata_path else None
    message = (
        f"{reason} Existing vectors are incompatible with the current embedding config. "
        f"Previous: {format_embedding_identity(previous_identity)}. "
        f"Current: {format_embedding_identity(current_identity)}. "
        f"Run `{rebuild_command}` and start the server again."
    )
    raise EmbeddingCompatibilityError(
        message,
        previous=previous_identity,
        current=current_identity,
        metadata_path=meta_str,
        rebuild_command=rebuild_command,
    )


async def ensure_embedding_collection_compatibility(
    storage: Any,
    config: Any,
    *,
    config_path: Optional[str] = None,
) -> Optional[Path]:
    """Validate local vector collection compatibility and persist metadata baseline."""
    current_payload = build_embedding_metadata(config)
    meta_path = resolve_embedding_meta_path(config.storage.vectordb)

    if meta_path is None:
        logger.info(
            "Skipping embedding compatibility metadata: vectordb backend %s does not support local sidecars",
            config.storage.vectordb.backend,
        )
        return None

    collection_info = await storage.get_collection_info()
    record_count = _extract_count(collection_info)
    current_dim = config.embedding.dimension

    try:
        stored_payload = load_embedding_metadata(config.storage.vectordb)
    except json.JSONDecodeError as exc:
        _raise_mismatch(
            previous_payload=None,
            current_payload=current_payload,
            config_path=config_path,
            metadata_path=meta_path,
            reason=f"Embedding metadata file is corrupted: {exc}.",
        )
    except OSError as exc:
        raise EmbeddingCompatibilityError(
            f"Failed to read embedding metadata from {meta_path}: {exc}",
            metadata_path=str(meta_path),
        ) from exc

    if stored_payload is None:
        existing_dim = _extract_vector_dim(collection_info)
        if record_count > 0 and existing_dim and existing_dim != current_dim:
            baseline = {
                "embedding": {
                    "mode": "unknown",
                    "dense": {"provider": "unknown", "model": "unknown", "dimension": existing_dim},
                }
            }
            _raise_mismatch(
                previous_payload=baseline,
                current_payload=current_payload,
                config_path=config_path,
                metadata_path=meta_path,
                reason=(
                    "Vector collection dimension does not match the current embedding dimension, "
                    "and no embedding metadata baseline exists."
                ),
            )

        written_path = persist_embedding_metadata(config, current_payload)
        if record_count > 0:
            logger.warning(
                "Vector collection already contains %s records but has no embedding metadata. "
                "Recorded the current embedding config as the new baseline at %s. "
                "If this workspace was upgraded after an embedding-model switch, run %s once to rebuild.",
                record_count,
                written_path,
                _build_rebuild_command(config_path),
            )
        return written_path

    stored_identity = stored_payload.get("embedding")
    current_identity = current_payload.get("embedding")
    if stored_identity != current_identity:
        _raise_mismatch(
            previous_payload=stored_payload,
            current_payload=current_payload,
            config_path=config_path,
            metadata_path=meta_path,
            reason="Embedding configuration changed.",
        )

    return persist_embedding_metadata(config, current_payload)
