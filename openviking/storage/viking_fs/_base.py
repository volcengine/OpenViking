# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Shared constants, helpers, dataclasses, and singleton management for VikingFS."""

import os
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Dict, List, Optional, TypeVar

from openviking.utils.time_utils import get_current_timestamp
from openviking_cli.exceptions import (
    InvalidArgumentError,
    ResourceExhaustedError,
)
from openviking_cli.utils.logger import get_logger

if TYPE_CHECKING:
    from openviking.storage.viking_vector_index_backend import VikingVectorIndexBackend
    from openviking_cli.utils.config import GrepConfig, RerankConfig, RetrievalConfig

logger = get_logger(__name__)

# Sentinel node_limit for internal callers that MUST enumerate an entire
# directory. ``ls()`` defaults to ``node_limit=1000`` to protect agent-facing
# context from being flooded, but internal system operations (parse merge,
# temp->final sync, summary DAG, vectorization) must see every child or they
# silently drop entries beyond the cap — e.g. a >1000-doc directory ingest only
# materializes its first 1000 subdirectories. Pass this explicitly at those
# call sites.
LS_ALL_NODES = 2**31 - 1
SNAPSHOT_DIFF_MAX_FILE_BYTES = 10 * 1024 * 1024
SNAPSHOT_DIFF_MAX_OUTPUT_BYTES = 20 * 1024 * 1024
SNAPSHOT_DIFF_MAX_LINES = 100_000
SNAPSHOT_DIFF_TIMEOUT_MS = 500
_T = TypeVar("_T")


def _snapshot_line_count(text: str) -> int:
    if not text:
        return 0
    line_breaks = (
        "\n",
        "\r",
        "\v",
        "\f",
        "\x1c",
        "\x1d",
        "\x1e",
        "\x85",
        "\u2028",
        "\u2029",
    )
    count = sum(text.count(separator) for separator in line_breaks)
    count -= text.count("\r\n")
    return count if text.endswith(line_breaks) else count + 1


def _prepare_snapshot_diff(
    *,
    path: str,
    before_bytes: Optional[bytes],
    after_bytes: Optional[bytes],
    max_lines: int,
) -> tuple[str, str, str]:
    try:
        before = before_bytes.decode("utf-8") if before_bytes is not None else ""
        after = after_bytes.decode("utf-8") if after_bytes is not None else ""
    except UnicodeDecodeError as exc:
        raise InvalidArgumentError("snapshot diff only supports UTF-8 text files") from exc

    for text in (before, after):
        line_count = _snapshot_line_count(text)
        if line_count > max_lines:
            raise ResourceExhaustedError(
                f"snapshot diff line count limit exceeded ({max_lines} lines per file)",
                details={"limit_lines": max_lines, "path": path},
            )

    if before_bytes is None:
        change_type = "added"
    elif after_bytes is None:
        change_type = "deleted"
    elif before_bytes == after_bytes:
        change_type = "unchanged"
    else:
        change_type = "modified"

    return change_type, before, after


def _ensure_non_empty_search_query(query: str, image_url: Optional[str] = None) -> None:
    if not query.strip() and not image_url:
        raise InvalidArgumentError("Search query or image_url must not be empty.")


def _is_directory_not_empty_error(message: str) -> bool:
    """Check if an error message indicates a directory not empty error.

    Handles multiple possible error message formats from different backends.
    """
    msg = message.lower()
    return any(
        pattern in msg
        for pattern in [
            "directory not empty",
            "dir not empty",
            "directory is not empty",
        ]
    )


def _get_cpu_count() -> int:
    """Return the number of CPUs available to this process.

    Tries process_cpu_count (Python 3.13+, cgroup-aware),
    falls back to sched_getaffinity (Linux),
    then os.cpu_count (may report host CPUs in containers).
    """
    if hasattr(os, "process_cpu_count"):
        return os.process_cpu_count() or 1
    try:
        return len(os.sched_getaffinity(0))
    except (AttributeError, NotImplementedError):
        return os.cpu_count() or 1


def _get_abstract_worker_count() -> int:
    default = max(4, min(12, min(32, _get_cpu_count() + 4) // 2))
    env_val = os.getenv("OPENVIKING_FILE_OPS_CONCURRENCY")
    if env_val is not None:
        try:
            return max(1, int(env_val))
        except ValueError:
            pass
    return max(1, default)


_ABSTRACT_WORKER_COUNT = _get_abstract_worker_count()
_DEFAULT_GREP_FILE_CONCURRENCY = 32


# ========== Dataclass ==========


@dataclass
class RelationEntry:
    """Relation table entry."""

    id: str
    uris: List[str]
    reason: str = ""
    created_at: str = field(default_factory=get_current_timestamp)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "uris": self.uris,
            "reason": self.reason,
            "created_at": self.created_at,
        }

    @staticmethod
    def from_dict(data: Dict[str, Any]) -> "RelationEntry":
        return RelationEntry(**data)


# ========== Singleton Pattern ==========

_instance: Optional["Any"] = None


def init_viking_fs(
    agfs: Any,
    query_embedder: Optional[Any] = None,
    rerank_config: Optional["RerankConfig"] = None,
    vector_store: Optional["VikingVectorIndexBackend"] = None,
    retrieval_config: Optional["RetrievalConfig"] = None,
    grep_config: Optional["GrepConfig"] = None,
    timeout: int = 10,
    enable_recorder: bool = False,
    encryptor: Optional[Any] = None,
):
    """Initialize VikingFS singleton.

    Args:
        agfs: Pre-initialized AGFS client (HTTP or Binding)
        query_embedder: Embedder instance
        rerank_config: Rerank configuration
        retrieval_config: Retrieval ranking configuration
        grep_config: Grep engine configuration
        vector_store: Vector store instance
        enable_recorder: Whether to enable IO recording
        encryptor: FileEncryptor instance for encryption/decryption
    """
    from openviking.storage.viking_fs import VikingFS

    global _instance

    _instance = VikingFS(
        agfs=agfs,
        query_embedder=query_embedder,
        rerank_config=rerank_config,
        vector_store=vector_store,
        retrieval_config=retrieval_config,
        grep_config=grep_config,
        encryptor=encryptor,
    )

    if enable_recorder:
        _enable_viking_fs_recorder(_instance)

    return _instance


def _enable_viking_fs_recorder(viking_fs) -> None:
    """
    Enable recorder for a VikingFS instance.

    This wraps the VikingFS instance with recording capabilities.
    Called automatically when enable_recorder=True in init_viking_fs.

    Args:
        viking_fs: VikingFS instance to enable recording for
    """
    from openviking.eval.recorder import RecordingVikingFS, get_recorder

    recorder = get_recorder()
    if not recorder.enabled:
        from openviking.eval.recorder import init_recorder

        init_recorder(enabled=True)

    global _instance
    _instance = RecordingVikingFS(viking_fs)
    logger.info("[VikingFS] IO Recorder enabled")


def enable_viking_fs_recorder() -> None:
    """
    Enable recorder for the global VikingFS singleton.

    This function wraps the existing VikingFS's AGFS client with recording.
    Must be called after init_viking_fs().
    """
    global _instance
    if _instance is None:
        raise RuntimeError("VikingFS not initialized. Call init_viking_fs() first.")
    _enable_viking_fs_recorder(_instance)


def get_viking_fs():
    """Get VikingFS singleton."""
    if _instance is None:
        raise RuntimeError("VikingFS not initialized. Call init_viking_fs() first.")
    return _instance
