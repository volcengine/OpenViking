# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""
VikingFS: OpenViking file system abstraction layer

Encapsulates the AGFS binding client, providing file operation interface based on Viking URI.
Responsibilities:
- URI conversion (viking:// <-> /local/)
- L0/L1 reading (.abstract.md, .overview.md)
- Relation management (.relations.json)
- Semantic search (vector retrieval + rerank)
- Vector sync (sync vector store on rm/mv)

This package is assembled from mixin submodules. The ``VikingFS`` class
combines all mixins; shared constants, helpers, and singleton management
live in :mod:`openviking.storage.viking_fs._base`.
"""

import asyncio
import contextvars
import hashlib
import json
import os
import re
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Callable, Dict, List, Optional, TypeVar, Union

from openviking.core.context import ContextLevel
from openviking.core.namespace import (
    canonicalize_uri,
    is_hidden_by_actor_peer_view,
    may_include_hidden_actor_peers,
)
from openviking.core.namespace import (
    is_accessible as namespace_is_accessible,
)
from openviking.core.retrieval_targets import resolve_retrieval_targets
from openviking.pyagfs import AsyncAGFSClient
from openviking.pyagfs.exceptions import (
    AGFSClientError,
    AGFSDirectoryNotEmptyError,
    AGFSHTTPError,
    AGFSInvalidOperationError,
    AGFSNotFoundError,
    AGFSNotSupportedError,
    AGFSPathNotFoundError,
    AGFSResourceExhaustedError,
)
from openviking.resource.watch_storage import is_watch_task_control_uri
from openviking.server.error_mapping import is_not_found_error, map_exception
from openviking.server.identity import RequestContext, Role
from openviking.storage.expr import And, PathScope, RawDSL
from openviking.storage.internal_names import STORAGE_INTERNAL_ENTRY_NAMES
from openviking.telemetry import get_current_telemetry
from openviking.utils.image_search import build_multimodal_embedding_input
from openviking.utils.time_utils import format_iso8601, get_current_timestamp, parse_iso_datetime
from openviking_cli.exceptions import (
    FailedPreconditionError,
    InvalidArgumentError,
    NotFoundError,
    PermissionDeniedError,
    ResourceExhaustedError,
)
from openviking_cli.session.user_id import UserIdentifier
from openviking_cli.utils.config.grep_config import GrepEngine
from openviking_cli.utils.logger import get_logger
from openviking_cli.utils.uri import VikingURI

# Import mixins
from openviking.storage.viking_fs import _base as _base_mod
from openviking.storage.viking_fs._access import _AccessMixin
from openviking.storage.viking_fs._base import (
    LS_ALL_NODES,
    SNAPSHOT_DIFF_MAX_FILE_BYTES,
    SNAPSHOT_DIFF_MAX_LINES,
    SNAPSHOT_DIFF_MAX_OUTPUT_BYTES,
    SNAPSHOT_DIFF_TIMEOUT_MS,
    RelationEntry,
    _ABSTRACT_WORKER_COUNT,
    _DEFAULT_GREP_FILE_CONCURRENCY,
    _T,
    _ensure_non_empty_search_query,
    _get_abstract_worker_count,
    _get_cpu_count,
    _is_directory_not_empty_error,
    _prepare_snapshot_diff,
    _snapshot_line_count,
    enable_viking_fs_recorder,
    get_viking_fs,
    init_viking_fs,
    logger,
)
from openviking.storage.viking_fs._grep import _GrepMixin
from openviking.storage.viking_fs._ops import _OpsMixin
from openviking.storage.viking_fs._semantic import _SemanticMixin
from openviking.storage.viking_fs._snapshot import _SnapshotMixin
from openviking.storage.viking_fs._sync import SyncDiff, _SyncMixin
from openviking.storage.viking_fs._vector import _VectorMixin

if TYPE_CHECKING:
    from openviking.storage.viking_vector_index_backend import VikingVectorIndexBackend
    from openviking_cli.utils.config import GrepConfig, RerankConfig, RetrievalConfig


# ========== VikingFS Main Class ==========


class VikingFS(
    _AccessMixin,
    _OpsMixin,
    _SyncMixin,
    _GrepMixin,
    _SemanticMixin,
    _SnapshotMixin,
    _VectorMixin,
):
    """RAGFS-based OpenViking file system.

    APIs are divided into two categories:
    - RAGFS basic commands (direct forwarding): read, ls, write, mkdir, rm, mv, grep, stat
    - VikingFS specific capabilities: abstract, overview, find, search, relations, link, unlink

    Uses Rust binding mode: Use RAGFSBindingClient to directly use RAGFS implementation
    """

    def __init__(
        self,
        agfs: Any,
        query_embedder: Optional[Any] = None,
        rerank_config: Optional["RerankConfig"] = None,
        vector_store: Optional["VikingVectorIndexBackend"] = None,
        retrieval_config: Optional["RetrievalConfig"] = None,
        grep_config: Optional["GrepConfig"] = None,
        timeout: int = 10,
        encryptor: Optional[Any] = None,
    ):
        self.agfs = agfs
        self._async_agfs = AsyncAGFSClient(agfs)
        self.query_embedder = query_embedder
        self.rerank_config = rerank_config
        self.vector_store = vector_store
        self.retrieval_config = retrieval_config
        self.grep_config = grep_config
        self._encryptor = encryptor
        self._count_cache: Dict[str, tuple] = {}  # cache_key → (count, timestamp)
        self._count_cache_max_size = 1024
        self._fulltext_available: Optional[bool] = None  # cached result of _collection_has_fulltext
        self._bound_ctx: contextvars.ContextVar[Optional[RequestContext]] = contextvars.ContextVar(
            "vikingfs_bound_ctx", default=None
        )
        self._background_tasks: set = set()
        self._user_deletion_guard: Optional[Callable[[str, str], bool]] = None


VikingFS.__module__ = __name__

# Provide _instance on the package module that transparently proxies to
# _base._instance so that test fixtures (conftest.py) can save/restore the
# singleton across init_viking_fs boundaries.  PEP 562 only gives us
# __getattr__; we also need to intercept writes so that assigning to
# ``openviking.storage.viking_fs._instance`` updates the real singleton in
# ``_base`` where ``get_viking_fs``/``init_viking_fs`` read/write it.
import sys as _sys


class _ModuleProxy:  # pragma: no cover - trivial proxy
    def __init__(self, wrapped):
        object.__setattr__(self, "_wrapped", wrapped)

    def __getattr__(self, name):
        if name == "_instance":
            return _base_mod._instance
        return getattr(object.__getattribute__(self, "_wrapped"), name)

    def __setattr__(self, name, value):
        if name == "_instance":
            _base_mod._instance = value
            return
        setattr(object.__getattribute__(self, "_wrapped"), name, value)

    def __delattr__(self, name):
        delattr(object.__getattribute__(self, "_wrapped"), name)

    def __dir__(self):
        return dir(object.__getattribute__(self, "_wrapped"))


_sys.modules[__name__] = _ModuleProxy(_sys.modules[__name__])  # type: ignore[assignment]

__all__ = [
    "LS_ALL_NODES",
    "SNAPSHOT_DIFF_MAX_FILE_BYTES",
    "SNAPSHOT_DIFF_MAX_LINES",
    "SNAPSHOT_DIFF_MAX_OUTPUT_BYTES",
    "SNAPSHOT_DIFF_TIMEOUT_MS",
    "RelationEntry",
    "SyncDiff",
    "VikingFS",
    "enable_viking_fs_recorder",
    "get_viking_fs",
    "init_viking_fs",
]
