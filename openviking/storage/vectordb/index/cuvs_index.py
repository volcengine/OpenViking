# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""cuVS-backed dense vector search for the embedded VectorDB.

cuVS is an index library rather than a complete vector database.  This module
therefore owns only the dense vectors and their label mapping.  OpenViking's
existing local engine remains responsible for durable records, scalar indexes,
sparse retrieval, and crash recovery.

The first implementation deliberately favors correctness and simple lifecycle
semantics: upserts and deletes update a host-side snapshot and invalidate the
GPU index.  The next search rebuilds the cuVS index in one batch.  This makes
all OpenViking mutations work with both brute-force and CAGRA even though cuVS
does not expose the same update/delete contract for every index type.
"""

from __future__ import annotations

import json
import logging
import math
import re
import threading
import time
import traceback
from array import array
from collections import OrderedDict
from contextlib import nullcontext
from dataclasses import dataclass, field
from typing import (
    Any,
    Callable,
    Dict,
    Iterable,
    Iterator,
    List,
    Mapping,
    Optional,
    Sequence,
    Tuple,
    Union,
    overload,
)

from openviking.storage.vectordb.store.data import CandidateData, DeltaRecord

logger = logging.getLogger(__name__)

_FP32_BYTES = 4
_FP32_UPLOAD_BATCH_BYTES = 64 * 1024 * 1024

NativeFilterEvaluation = Union[
    Tuple[Sequence[int], int],
    Tuple[Sequence[int], int, int],
]
NativeFilterResolver = Callable[[Mapping[str, Any]], NativeFilterEvaluation]


class CuVSUnavailableError(RuntimeError):
    """Raised when the configured cuVS runtime cannot be used."""


class CuVSMemoryBudgetError(RuntimeError):
    """Raised when auto mode cannot safely admit a cuVS index into free VRAM."""


class CuVSNativeRouteError(RuntimeError):
    """Raised when auto mode predicts that a filtered native search is cheaper."""


class UnsupportedCuVSFilterError(ValueError):
    """Raised when a filter cannot be translated to a cuVS prefilter."""


@dataclass(frozen=True)
class CuVSMemoryEstimate:
    """Conservative GPU-memory estimate used only for auto-admission."""

    vector_bytes: int
    graph_bytes: int
    build_graph_bytes: int
    filter_cache_bytes: int
    estimated_peak_bytes: int


@dataclass
class CuVSSearchTelemetry:
    """Low-cardinality timings and route metadata for one dense query."""

    algorithm: str
    auto_mode: bool
    dtype: str = "float32"
    max_concurrent_gpu_searches: int = 1
    route_reason: str = "pending"
    filter_kind: str = "none"
    filter_cache_hit: bool = False
    native_filter_reused: bool = False
    build_performed: bool = False
    eligible_count: Optional[int] = None
    records_generation: int = 0
    index_size: int = 0
    memory_estimated_peak_bytes: Optional[int] = None
    memory_free_bytes: Optional[int] = None
    memory_usable_bytes: Optional[int] = None
    total_ms: float = 0.0
    preflight_ms: float = 0.0
    queue_ms: float = 0.0
    build_ms: float = 0.0
    filter_prepare_ms: float = 0.0
    gpu_search_ms: float = 0.0
    native_search_ms: float = 0.0

    def as_dict(self) -> Dict[str, Any]:
        return {
            "algorithm": self.algorithm,
            "auto_mode": self.auto_mode,
            "dtype": self.dtype,
            "max_concurrent_gpu_searches": self.max_concurrent_gpu_searches,
            "route_reason": self.route_reason,
            "filter_kind": self.filter_kind,
            "filter_cache_hit": self.filter_cache_hit,
            "native_filter_reused": self.native_filter_reused,
            "build_performed": self.build_performed,
            "eligible_count": self.eligible_count,
            "records_generation": self.records_generation,
            "index_size": self.index_size,
            "memory_estimated_peak_bytes": self.memory_estimated_peak_bytes,
            "memory_free_bytes": self.memory_free_bytes,
            "memory_usable_bytes": self.memory_usable_bytes,
            "total_ms": round(self.total_ms, 3),
            "preflight_ms": round(self.preflight_ms, 3),
            "queue_ms": round(self.queue_ms, 3),
            "build_ms": round(self.build_ms, 3),
            "filter_prepare_ms": round(self.filter_prepare_ms, 3),
            "gpu_search_ms": round(self.gpu_search_ms, 3),
            "native_search_ms": round(self.native_search_ms, 3),
        }


def estimate_cuvs_memory(
    *,
    vector_count: int,
    dimension: int,
    algorithm: str,
    build_params: Mapping[str, Any],
    filter_cache_size: int,
    safety_factor: float,
    dtype: str = "float32",
) -> CuVSMemoryEstimate:
    """Estimate peak VRAM without changing the explicit cuVS backend behavior.

    The estimate accounts for the configured device-dataset dtype, retained and
    intermediate CAGRA graphs, and the configured number of cached filter bitsets.
    cuVS and allocator workspaces vary by release and build algorithm, so the
    configured safety factor intentionally covers the remaining uncertainty.
    """

    vector_count = max(0, int(vector_count))
    dimension = max(0, int(dimension))
    safety_factor = float(safety_factor)
    if safety_factor < 1.0:
        raise ValueError("cuVS auto memory safety factor must be at least 1.0")

    if dtype not in {"float32", "float16"}:
        raise ValueError(f"Unsupported cuVS memory-estimate dtype: {dtype!r}")
    vector_bytes = vector_count * dimension * (2 if dtype == "float16" else 4)
    graph_bytes = 0
    build_graph_bytes = 0
    if algorithm == "cagra":
        graph_degree = max(0, int(build_params.get("graph_degree", 64)))
        intermediate_graph_degree = max(0, int(build_params.get("intermediate_graph_degree", 128)))
        graph_bytes = vector_count * graph_degree * 4
        build_graph_bytes = vector_count * intermediate_graph_degree * 4

    filter_word_count = (vector_count + 31) // 32
    filter_cache_bytes = filter_word_count * 4 * max(0, int(filter_cache_size))
    known_peak_bytes = vector_bytes + graph_bytes + build_graph_bytes + filter_cache_bytes
    estimated_peak_bytes = math.ceil(known_peak_bytes * safety_factor)
    return CuVSMemoryEstimate(
        vector_bytes=vector_bytes,
        graph_bytes=graph_bytes,
        build_graph_bytes=build_graph_bytes,
        filter_cache_bytes=filter_cache_bytes,
        estimated_peak_bytes=estimated_peak_bytes,
    )


class _CuVSMemoryCoordinator:
    """Serialize admission and builds per GPU across local collections."""

    def __init__(self):
        self._lock = threading.Lock()
        self._device_locks: Dict[int, threading.Lock] = {}

    def build_lock(self, runtime: Any) -> threading.Lock:
        device_id = int(getattr(runtime, "device_id", 0))
        with self._lock:
            lock = self._device_locks.get(device_id)
            if lock is None:
                lock = threading.Lock()
                self._device_locks[device_id] = lock
            return lock


_CUVS_MEMORY_COORDINATOR = _CuVSMemoryCoordinator()


def _normalize(vector: Sequence[float]) -> List[float]:
    norm = math.sqrt(sum(float(value) * float(value) for value in vector))
    if norm == 0:
        return [float(value) for value in vector]
    return [float(value) / norm for value in vector]


def _normalize_path(value: str) -> str:
    stripped = value.strip()
    return stripped if stripped.startswith("/") else f"/{stripped}"


def _path_matches(value: Any, expected: Any, depth: Optional[int]) -> bool:
    if not isinstance(value, str) or not isinstance(expected, str):
        return False
    value_path = _normalize_path(value).rstrip("/") or "/"
    expected_path = _normalize_path(expected).rstrip("/") or "/"
    if value_path == expected_path:
        relative_depth = 0
    elif expected_path == "/":
        relative_depth = len([part for part in value_path.split("/") if part])
    elif value_path.startswith(expected_path + "/"):
        suffix = value_path[len(expected_path) + 1 :]
        relative_depth = len([part for part in suffix.split("/") if part])
    else:
        return False

    if depth is None or depth < 0:
        return True
    return relative_depth <= depth


def _parse_depth(para: Any) -> Optional[int]:
    if para in (None, ""):
        return None
    if not isinstance(para, str):
        raise UnsupportedCuVSFilterError(f"Unsupported path filter parameter: {para!r}")
    match = re.fullmatch(r"\s*-d=(-?\d+)\s*", para)
    if not match:
        raise UnsupportedCuVSFilterError(f"Unsupported path filter parameter: {para!r}")
    return int(match.group(1))


def _value_matches(value: Any, conditions: Sequence[Any]) -> bool:
    if isinstance(value, list):
        return any(condition in value for condition in conditions)
    return value in conditions


def _contains(value: Any, substring: Any) -> bool:
    if not isinstance(substring, str):
        return False
    if isinstance(value, str):
        return substring in value
    if isinstance(value, list):
        return any(substring in item for item in value if isinstance(item, str))
    return False


def _in_range(value: Any, node: Mapping[str, Any]) -> bool:
    if value is None:
        return False
    try:
        if node.get("gt") is not None and not value > node["gt"]:
            return False
        if node.get("gte") is not None and not value >= node["gte"]:
            return False
        if node.get("lt") is not None and not value < node["lt"]:
            return False
        if node.get("lte") is not None and not value <= node["lte"]:
            return False
    except TypeError:
        return False
    return True


def _filter_uses_field_type(
    node: Optional[Mapping[str, Any]],
    field_types: Mapping[str, str],
    expected_type: str,
) -> bool:
    if not node or not isinstance(node, Mapping):
        return False
    nested = node.get("filter")
    if isinstance(nested, Mapping) and _filter_uses_field_type(nested, field_types, expected_type):
        return True
    field = node.get("field")
    if isinstance(field, str) and str(field_types.get(field, "")).lower() == expected_type:
        return True
    children = node.get("conds")
    if isinstance(children, list):
        return any(
            _filter_uses_field_type(child, field_types, expected_type)
            for child in children
            if isinstance(child, Mapping)
        )
    return False


def matches_filter(
    fields: Mapping[str, Any],
    node: Optional[Mapping[str, Any]],
    field_types: Mapping[str, str],
) -> bool:
    """Evaluate the scalar-filter subset supported by the cuVS backend.

    The supported DSL is the one emitted by ``CollectionAdapter`` for normal
    OpenViking search: ``and``, ``or``, ``must``, ``must_not``, ``contains``,
    ``range``, ``range_out``, and path depth parameters.  Unsupported nodes are
    rejected so the caller can safely fall back to the native local engine.
    """

    if not node:
        return True
    if not isinstance(node, Mapping):
        raise UnsupportedCuVSFilterError(f"Filter node must be an object: {node!r}")
    if "filter" in node and len(node) == 1:
        nested = node.get("filter")
        if nested is None:
            return True
        if not isinstance(nested, Mapping):
            raise UnsupportedCuVSFilterError("The filter wrapper must contain an object")
        return matches_filter(fields, nested, field_types)

    op = str(node.get("op", "")).lower()
    if op in {"and", "or"}:
        children = node.get("conds", [])
        if not isinstance(children, list):
            raise UnsupportedCuVSFilterError(f"{op} filter conds must be a list")
        results = [matches_filter(fields, child, field_types) for child in children]
        return all(results) if op == "and" else any(results)

    field = node.get("field")
    if not isinstance(field, str):
        raise UnsupportedCuVSFilterError(f"Filter field must be a string: {node!r}")
    field_type = str(field_types.get(field, "")).lower()
    if field_type in {"date_time", "geo_point"}:
        # Those fields require OpenViking's type conversion logic.  Falling back
        # avoids subtly different results for timezone and geo comparisons.
        raise UnsupportedCuVSFilterError(f"cuVS prefilter does not support {field_type} fields")
    value = fields.get(field)

    if op in {"must", "must_not"}:
        conditions = node.get("conds", [])
        if not isinstance(conditions, list):
            raise UnsupportedCuVSFilterError(f"{op} filter conds must be a list")
        if field_type == "path":
            depth = _parse_depth(node.get("para"))
            matched = any(_path_matches(value, condition, depth) for condition in conditions)
        else:
            if node.get("para") not in (None, ""):
                raise UnsupportedCuVSFilterError(
                    f"Filter parameters are only supported for path fields: {node!r}"
                )
            matched = _value_matches(value, conditions)
        return matched if op == "must" else not matched

    if op == "contains":
        return _contains(value, node.get("substring"))
    if op == "range":
        return _in_range(value, node)
    if op == "range_out":
        return not _in_range(value, node)

    raise UnsupportedCuVSFilterError(f"Unsupported cuVS filter operation: {op!r}")


@dataclass(frozen=True)
class _PackedFP32Rows(Sequence[Sequence[float]]):
    """Immutable FP32 rows captured for one cuVS rebuild.

    The row blobs keep mutation snapshots cheap and safe: an upsert replaces a
    blob instead of modifying storage which a background rebuild may still be
    reading.  Runtime uploads concatenate only a bounded number of rows at a
    time, so a rebuild does not require another full host-side dataset copy.
    """

    rows: Tuple[bytes, ...]
    dimension: int

    def __len__(self) -> int:
        return len(self.rows)

    @overload
    def __getitem__(self, index: int) -> Sequence[float]: ...

    @overload
    def __getitem__(self, index: slice) -> Sequence[Sequence[float]]: ...

    def __getitem__(
        self, index: Union[int, slice]
    ) -> Union[Sequence[float], Sequence[Sequence[float]]]:
        if isinstance(index, slice):
            return tuple(memoryview(row).cast("f") for row in self.rows[index])
        return memoryview(self.rows[index]).cast("f")

    def __iter__(self) -> Iterator[Sequence[float]]:
        for row in self.rows:
            yield memoryview(row).cast("f")

    @property
    def nbytes(self) -> int:
        return len(self.rows) * self.dimension * _FP32_BYTES

    def iter_packed_batches(
        self,
        max_bytes: int,
    ) -> Iterator[Tuple[int, int, bytes]]:
        """Yield row-aligned host buffers bounded by ``max_bytes`` when possible."""

        if max_bytes <= 0:
            raise ValueError("cuVS FP32 upload batch size must be positive")
        row_bytes = self.dimension * _FP32_BYTES
        if row_bytes <= 0:
            return
        rows_per_batch = max(1, max_bytes // row_bytes)
        for start in range(0, len(self.rows), rows_per_batch):
            end = min(start + rows_per_batch, len(self.rows))
            yield start, end, b"".join(self.rows[start:end])


class _CuVSRuntime:
    """Small adapter around the public cuVS Python API."""

    def __init__(
        self,
        algorithm: str,
        metric: str,
        build_params: Mapping[str, Any],
        search_params: Mapping[str, Any],
        dtype: str,
    ):
        try:
            import cupy as cp
            from cuvs.common import Resources
            from cuvs.neighbors import brute_force, cagra, filters

            device_count = cp.cuda.runtime.getDeviceCount()
            device_id = cp.cuda.runtime.getDevice()
        except Exception as exc:
            raise CuVSUnavailableError(
                "cuVS backend requires Python 3.11+, a CUDA-capable NVIDIA GPU, and the "
                "matching cuvs-cu12 or cuvs-cu13 Python package"
            ) from exc
        if device_count < 1:
            raise CuVSUnavailableError("cuVS backend requires at least one visible CUDA device")

        self.cp = cp
        self.brute_force = brute_force
        self.cagra = cagra
        self.filters = filters
        self.Resources = Resources
        self.device_id = int(device_id)
        self.dtype = dtype
        self.device_dtype = cp.float16 if dtype == "float16" else cp.float32
        self.algorithm = algorithm
        self.metric = metric
        self.build_params = dict(build_params)
        self.search_params = dict(search_params)
        # Resources are borrowed per admitted search rather than retained by
        # worker thread. This keeps their lifetime on device_id and bounds the
        # registry even when callers churn short-lived host threads.
        self._resource_condition = threading.Condition(threading.Lock())
        self._available_resources: List[Any] = []
        self._owned_resources: List[Any] = []
        self._resource_limit = 1
        self._resources_closed = False

    def set_max_concurrent_searches(self, value: int) -> None:
        limit = max(1, int(value))
        with self._resource_condition:
            self._resource_limit = limit
            self._resource_condition.notify_all()

    def device_scope(self):
        """Activate the device captured when this runtime was constructed."""

        return self.cp.cuda.Device(self.device_id)

    def memory_info(self) -> Tuple[int, int]:
        # CUDA's current device is thread-local.  Searches and background
        # rebuilds can run on threads other than the one which constructed the
        # runtime, so every CUDA entry point must restore the captured device.
        with self.device_scope():
            free_bytes, total_bytes = self.cp.cuda.runtime.memGetInfo()
            return int(free_bytes), int(total_bytes)

    def release_index(self) -> None:
        with self.device_scope():
            try:
                self.cp.get_default_memory_pool().free_all_blocks()
            except Exception:
                logger.debug("Could not release unused CuPy memory-pool blocks", exc_info=True)

    def is_out_of_memory(self, exc: Exception) -> bool:
        out_of_memory_type = getattr(self.cp.cuda.memory, "OutOfMemoryError", ())
        if out_of_memory_type and isinstance(exc, out_of_memory_type):
            return True
        message = str(exc).lower()
        return any(
            marker in message for marker in ("out of memory", "memory allocation", "bad_alloc")
        )

    def build(self, dataset: Sequence[Sequence[float]]):
        with self.device_scope():
            device_dataset = None
            packed_batch = None
            host_batch = None
            index = None
            try:
                if isinstance(dataset, _PackedFP32Rows):
                    # CuPy already depends on NumPy, but keep the import on the
                    # GPU-only path so native OpenViking users do not gain a
                    # new import-time dependency through this module.
                    import numpy as np

                    device_dataset = self.cp.empty(
                        (len(dataset), dataset.dimension),
                        dtype=self.device_dtype,
                    )
                    for start, end, packed_batch in dataset.iter_packed_batches(
                        _FP32_UPLOAD_BATCH_BYTES
                    ):
                        host_batch = np.frombuffer(packed_batch, dtype=np.float32).reshape(
                            end - start, dataset.dimension
                        )
                        if self.dtype == "float16":
                            host_batch = host_batch.astype(np.float16)
                        # ndarray.set() without an explicit stream performs a
                        # synchronous host-to-device copy.  The bounded host
                        # buffer can therefore be released before the next batch.
                        device_dataset[start:end].set(host_batch)
                        host_batch = None
                        packed_batch = None
                else:
                    device_dataset = self.cp.asarray(dataset, dtype=self.device_dtype)
                if self.algorithm == "brute_force":
                    index = self.brute_force.build(device_dataset, metric=self.metric)
                    return _CuVSRuntimeIndex(index=index, dataset=device_dataset)
                params = self.cagra.IndexParams(metric=self.metric, **self.build_params)
                index = self.cagra.build(params, device_dataset)
                return _CuVSRuntimeIndex(index=index, dataset=device_dataset)
            except Exception as exc:
                # These can be the last Python references after a partial build.
                # Drop them before Device.__exit__ restores the caller's device.
                index = None
                device_dataset = None
                host_batch = None
                packed_batch = None
                # Python/Cython exception frames can retain CUDA arguments even
                # after the locals above are cleared. Preserve stack locations
                # while releasing frame locals under the captured device.
                traceback.clear_frames(exc.__traceback__)
                raise

    def _acquire_resources(self):
        with self.device_scope():
            with self._resource_condition:
                while not self._resources_closed:
                    if self._available_resources:
                        return self._available_resources.pop()
                    if len(self._owned_resources) < self._resource_limit:
                        resources = self.Resources()
                        self._owned_resources.append(resources)
                        return resources
                    self._resource_condition.wait()
                raise RuntimeError("cuVS runtime is closed")

    def _return_resources(self, resources: Any, *, reusable: bool) -> None:
        with self._resource_condition:
            owned_index = next(
                (index for index, owned in enumerate(self._owned_resources) if owned is resources),
                None,
            )
            if owned_index is None:
                return
            if reusable and not self._resources_closed:
                self._available_resources.append(resources)
            else:
                self._owned_resources.pop(owned_index)
            self._resource_condition.notify()

    def _prefilter(self, mask: Sequence[bool]):
        with self.device_scope():
            return self.filters.from_bitset(self.prepare_filter(mask))

    def prepare_filter(self, mask: Sequence[bool]):
        """Pack a host mask once and retain its device allocation for reuse."""

        with self.device_scope():
            word_count = (len(mask) + 31) // 32
            words = [0] * word_count
            for index, included in enumerate(mask):
                if included:
                    words[index // 32] |= 1 << (index % 32)
            return self.cp.asarray(words, dtype=self.cp.uint32)

    def prepare_filter_words(self, words: Sequence[int]):
        """Copy an already packed native filter bitmap to the device."""

        with self.device_scope():
            return self.cp.asarray(words, dtype=self.cp.uint32)

    def search(
        self,
        runtime_index: "_CuVSRuntimeIndex",
        query: Sequence[float],
        limit: int,
        mask: Optional[Any],
    ) -> Tuple[List[int], List[float]]:
        with self.device_scope():
            queries = None
            prefilter = None
            distances = None
            neighbors = None
            resources = None
            resource_kwargs = None
            resource_reusable = False
            try:
                index = runtime_index.index
                # Always provide an explicit resource, including the serialized
                # max_concurrent_gpu_searches=1 case.  cuVS synchronizes an
                # implicit resource before returning, but an owned reusable
                # resource also pins worker-thread churn to a known stream and
                # gives filter/result lifetimes an explicit synchronization
                # boundary inside this admitted search.
                resources = self._acquire_resources()
                resource_kwargs = {"resources": resources}
                queries = self.cp.asarray([query], dtype=self.device_dtype)
                if mask is None:
                    prefilter = None
                elif isinstance(mask, self.cp.ndarray) and mask.dtype == self.cp.uint32:
                    prefilter = self.filters.from_bitset(mask)
                else:
                    prefilter = self._prefilter(mask)
                if self.algorithm == "brute_force":
                    distances, neighbors = self.brute_force.search(
                        index,
                        queries,
                        limit,
                        prefilter=prefilter,
                        **resource_kwargs,
                    )
                else:
                    search_params = dict(self.search_params)
                    configured_itopk = int(search_params.get("itopk_size", 64))
                    minimum_itopk = ((limit + 31) // 32) * 32
                    search_params["itopk_size"] = max(configured_itopk, minimum_itopk)
                    params = self.cagra.SearchParams(**search_params)
                    distances, neighbors = self.cagra.search(
                        params,
                        index,
                        queries,
                        limit,
                        filter=prefilter,
                        **resource_kwargs,
                    )
                resources.sync()
                host_neighbors = self.cp.asnumpy(neighbors)[0].tolist()
                host_distances = self.cp.asnumpy(distances)[0].tolist()
                result = (
                    [int(item) for item in host_neighbors],
                    [float(item) for item in host_distances],
                )
                # Reuse only after the complete call, including host result
                # materialization, succeeds.  Any exception discards a
                # potentially poisoned resource from the pool.
                resource_reusable = True
                return result
            except Exception as exc:
                traceback.clear_frames(exc.__traceback__)
                raise
            finally:
                # Search temporaries otherwise outlive Device.__exit__ as frame
                # locals and may free allocations on the worker's prior device.
                queries = None
                prefilter = None
                distances = None
                neighbors = None
                try:
                    if resources is not None:
                        self._return_resources(resources, reusable=resource_reusable)
                finally:
                    resource_kwargs = None
                    resources = None

    def close(self) -> None:
        with self.device_scope():
            with self._resource_condition:
                self._resources_closed = True
                self._available_resources.clear()
                owned_resources = self._owned_resources
                self._owned_resources = []
                self._resource_condition.notify_all()
            owned_resources.clear()
            self.release_index()


@dataclass(frozen=True)
class _Record:
    vector: bytes
    fields: Mapping[str, Any]


@dataclass(frozen=True)
class _CuVSRuntimeIndex:
    index: Any
    dataset: Any


@dataclass(frozen=True)
class _CuVSIndexSnapshot:
    runtime_index: Any
    labels: Tuple[int, ...]
    generation: int


@dataclass
class _CuVSBuildCandidate:
    runtime_index: Any
    labels: Tuple[int, ...]
    generation: int
    consumed: bool = False


@dataclass(frozen=True)
class _CachedFilter:
    prepared: Any
    eligible_count: int
    route_native: bool = False
    native_threshold: int = 0
    native_filter_token: int = 0


@dataclass(frozen=True)
class _ResolvedNativeFilter:
    bitset_words: Tuple[int, ...]
    eligible_count: int
    route_native: bool
    native_threshold: int
    native_filter_token: int = 0


@dataclass
class _NativeFilterPreflightFlight:
    generation: int
    done: threading.Event = field(default_factory=threading.Event)
    route_count: Optional[int] = None
    eligible_count: int = 0
    error: Optional[BaseException] = None
    stale: bool = False
    participants: int = 1


class CuVSDenseIndex:
    """Mutable OpenViking label space backed by a lazily rebuilt cuVS index."""

    _SUPPORTED_ALGORITHMS = {"brute_force", "cagra"}

    def __init__(
        self,
        *,
        dimension: int,
        distance: str,
        normalize_vectors: bool,
        field_types: Mapping[str, str],
        config: Mapping[str, Any],
        runtime: Optional[Any] = None,
        auto_memory: bool = False,
    ):
        self.dimension = int(dimension)
        self.distance = distance.lower()
        self.normalize_vectors = bool(normalize_vectors)
        self.field_types = dict(field_types)
        self.auto_memory = bool(auto_memory)
        self.algorithm = str(config.get("algorithm", "brute_force")).lower()
        if self.algorithm not in self._SUPPORTED_ALGORITHMS:
            raise ValueError(
                f"Unsupported cuVS algorithm {self.algorithm!r}; "
                f"choose one of {sorted(self._SUPPORTED_ALGORITHMS)}"
            )
        if self.distance not in {"ip", "l2"}:
            raise ValueError(f"Unsupported OpenViking distance for cuVS: {self.distance!r}")
        self.dtype = str(config.get("dtype", "float32")).lower()
        if self.dtype not in {"float32", "float16"}:
            raise ValueError(
                f"Unsupported cuVS dtype {self.dtype!r}; choose 'float32' or 'float16'"
            )

        self.fallback_to_native = bool(config.get("fallback_to_native", True))
        self.filter_cache_size = int(config.get("filter_cache_size", 16))
        if self.filter_cache_size < 0:
            raise ValueError("cuVS filter_cache_size cannot be negative")
        self.max_concurrent_gpu_searches = int(config.get("max_concurrent_gpu_searches", 1))
        if self.max_concurrent_gpu_searches < 1:
            raise ValueError("cuVS max_concurrent_gpu_searches must be at least 1")
        self.auto_memory_reserve_bytes = (
            int(config.get("auto_memory_reserve_mb", 1024)) * 1024 * 1024
        )
        if self.auto_memory_reserve_bytes < 0:
            raise ValueError("cuVS auto memory reserve cannot be negative")
        self.auto_memory_safety_factor = float(config.get("auto_memory_safety_factor", 2.0))
        if self.auto_memory_safety_factor < 1.0:
            raise ValueError("cuVS auto memory safety factor must be at least 1.0")
        self.auto_filter_native_threshold = int(config.get("auto_filter_native_threshold", 2000))
        if self.auto_filter_native_threshold < 0:
            raise ValueError("cuVS auto filter native threshold cannot be negative")
        self.auto_path_filter_native_threshold = int(
            config.get("auto_path_filter_native_threshold", 200)
        )
        if self.auto_path_filter_native_threshold < 0:
            raise ValueError("cuVS auto path filter native threshold cannot be negative")
        self._metric = "inner_product" if self.distance == "ip" else "sqeuclidean"
        build_params = dict(config.get("build_params", {}))
        search_params = dict(config.get("search_params", {}))
        self._build_params = build_params
        if "metric" in build_params:
            raise ValueError(
                "Set the cuVS metric through storage.vectordb.distance_metric, "
                "not cuvs.build_params.metric"
            )
        if self.algorithm == "brute_force" and (build_params or search_params):
            raise ValueError("cuVS build_params/search_params are only valid for CAGRA")
        self._runtime = runtime or _CuVSRuntime(
            self.algorithm,
            self._metric,
            build_params,
            search_params,
            self.dtype,
        )
        set_max_concurrent_searches = getattr(self._runtime, "set_max_concurrent_searches", None)
        if set_max_concurrent_searches is not None:
            set_max_concurrent_searches(self.max_concurrent_gpu_searches)
        self._records: Dict[int, _Record] = {}
        self._snapshot: Optional[_CuVSIndexSnapshot] = None
        self._dirty = True
        self._filter_cache: OrderedDict[str, _CachedFilter] = OrderedDict()
        self._preflight_filter_cache: OrderedDict[str, _ResolvedNativeFilter] = OrderedDict()
        self._preflight_flights: Dict[Tuple[int, str], _NativeFilterPreflightFlight] = {}
        self._lock = threading.RLock()
        self._idle_condition = threading.Condition(self._lock)
        self._active_searches = 0
        self._gpu_search_gate = threading.BoundedSemaphore(self.max_concurrent_gpu_searches)
        self._filter_layout_lock = threading.Lock()
        self._records_generation = 0
        self._filter_layout_generation = -1
        self._closed = False
        self._close_complete = False
        logger.info(
            "Initialized cuVS dense index: algorithm=%s metric=%s dimension=%d",
            self.algorithm,
            self._metric,
            self.dimension,
        )

    def _runtime_device_scope(self):
        device_scope = getattr(self._runtime, "device_scope", None)
        return device_scope() if callable(device_scope) else nullcontext()

    @property
    def size(self) -> int:
        with self._lock:
            return len(self._records)

    @property
    def host_shadow_nbytes(self) -> int:
        """Return the compact FP32 vector payload size, excluding Python metadata."""

        with self._lock:
            return len(self._records) * self.dimension * _FP32_BYTES

    @property
    def needs_rebuild(self) -> bool:
        with self._lock:
            return self._dirty

    def _prepare_vector_values(self, vector: Sequence[float]) -> List[float]:
        if len(vector) != self.dimension:
            raise ValueError(
                f"cuVS vector dimension mismatch: expected {self.dimension}, got {len(vector)}"
            )
        return _normalize(vector) if self.normalize_vectors else [float(v) for v in vector]

    def _prepare_vector(self, vector: Sequence[float]) -> Tuple[float, ...]:
        return tuple(self._prepare_vector_values(vector))

    def _pack_vector(self, vector: Sequence[float]) -> bytes:
        packed = array("f", self._prepare_vector_values(vector))
        if packed.itemsize != _FP32_BYTES:
            raise RuntimeError("cuVS host vector storage requires 4-byte IEEE FP32 values")
        return packed.tobytes()

    @staticmethod
    def _parse_fields(value: str) -> Mapping[str, Any]:
        if not value:
            return {}
        try:
            parsed = json.loads(value)
        except (TypeError, json.JSONDecodeError):
            return {}
        return parsed if isinstance(parsed, dict) else {}

    def add_candidates(self, candidates: Iterable[CandidateData]) -> None:
        with self._lock:
            for candidate in candidates:
                if not candidate.vector:
                    continue
                self._records[int(candidate.label)] = _Record(
                    vector=self._pack_vector(candidate.vector),
                    fields=self._parse_fields(candidate.fields),
                )
            self._invalidate()

    def upsert(self, records: Iterable[DeltaRecord]) -> None:
        with self._lock:
            changed = False
            for record in records:
                if not record.vector:
                    continue
                self._records[int(record.label)] = _Record(
                    vector=self._pack_vector(record.vector),
                    fields=self._parse_fields(record.fields),
                )
                changed = True
            if changed:
                self._invalidate()

    def delete(self, records: Iterable[DeltaRecord]) -> None:
        with self._lock:
            changed = False
            for record in records:
                if self._records.pop(int(record.label), None) is not None:
                    changed = True
            if changed:
                self._invalidate()

    def _invalidate(self) -> None:
        self._records_generation += 1
        self._dirty = True
        self._cancel_preflight_flights_()
        with self._runtime_device_scope():
            self._filter_cache.clear()
        self._preflight_filter_cache.clear()

    def _cancel_preflight_flights_(self) -> None:
        """Mark in-flight projections stale and wake waiters under ``_lock``."""

        flights = tuple(self._preflight_flights.values())
        self._preflight_flights.clear()
        for flight in flights:
            flight.stale = True
            flight.done.set()

    @staticmethod
    def _filter_cache_key(filters: Mapping[str, Any]) -> Optional[str]:
        try:
            return json.dumps(filters, sort_keys=True, separators=(",", ":"))
        except (TypeError, ValueError):
            return None

    def _get_cached_filter(self, cache_key: Optional[str]) -> Optional[_CachedFilter]:
        if cache_key is None:
            return None
        cached = self._filter_cache.pop(cache_key, None)
        if cached is not None:
            self._filter_cache[cache_key] = cached
        return cached

    def _cache_filter(self, cache_key: Optional[str], cached: _CachedFilter) -> None:
        if cache_key is None or self.filter_cache_size <= 0:
            return
        self._preflight_filter_cache.pop(cache_key, None)
        with self._runtime_device_scope():
            self._filter_cache[cache_key] = cached
            while len(self._filter_cache) > self.filter_cache_size:
                self._filter_cache.popitem(last=False)

    def _get_preflight_filter(self, cache_key: Optional[str]) -> Optional[_ResolvedNativeFilter]:
        if cache_key is None:
            return None
        cached = self._preflight_filter_cache.pop(cache_key, None)
        if cached is not None:
            self._preflight_filter_cache[cache_key] = cached
        return cached

    def _cache_preflight_filter(
        self,
        cache_key: Optional[str],
        resolved: _ResolvedNativeFilter,
    ) -> None:
        if cache_key is None:
            return
        self._preflight_filter_cache[cache_key] = resolved
        capacity = max(1, self.filter_cache_size)
        while len(self._preflight_filter_cache) > capacity:
            self._preflight_filter_cache.popitem(last=False)

    def _lookup_preflight_route_(
        self,
        cache_key: Optional[str],
    ) -> Optional[Tuple[Optional[int], int]]:
        cached = self._get_cached_filter(cache_key)
        if cached is not None:
            return (
                cached.eligible_count if cached.route_native else None,
                cached.eligible_count,
            )
        resolved = self._get_preflight_filter(cache_key)
        if resolved is None:
            return None
        return (
            resolved.eligible_count if resolved.route_native else None,
            resolved.eligible_count,
        )

    def _store_preflight_route_(
        self,
        cache_key: Optional[str],
        resolved: _ResolvedNativeFilter,
    ) -> Tuple[Optional[int], int, bool]:
        existing = self._lookup_preflight_route_(cache_key)
        if existing is not None:
            route_count, eligible_count = existing
            return route_count, eligible_count, True
        if not resolved.route_native and resolved.eligible_count != 0:
            self._cache_preflight_filter(cache_key, resolved)
            return None, resolved.eligible_count, False
        cached = _CachedFilter(
            prepared=None,
            eligible_count=resolved.eligible_count,
            route_native=resolved.route_native,
            native_threshold=resolved.native_threshold,
            native_filter_token=resolved.native_filter_token,
        )
        self._cache_filter(cache_key, cached)
        return (
            cached.eligible_count if cached.route_native else None,
            cached.eligible_count,
            False,
        )

    def native_filter_threshold(self, filters: Mapping[str, Any]) -> int:
        return (
            self.auto_path_filter_native_threshold
            if _filter_uses_field_type(filters, self.field_types, "path")
            else self.auto_filter_native_threshold
        )

    def native_filter_token(self, filters: Mapping[str, Any]) -> int:
        cache_key = self._filter_cache_key(filters)
        with self._lock:
            cached = self._get_cached_filter(cache_key)
            if cached is None or not cached.route_native:
                return 0
            return cached.native_filter_token

    def _ensure_native_filter_layout(
        self,
        native_filter_layout_registrar: Callable[[Sequence[int]], None],
    ) -> Optional[int]:
        with self._filter_layout_lock:
            with self._lock:
                generation = self._records_generation
                if self._closed:
                    return None
                if self._filter_layout_generation == generation:
                    return generation
                ordered_labels = list(self._records)

            native_filter_layout_registrar(ordered_labels)

            with self._lock:
                if self._closed or self._records_generation != generation:
                    return None
                self._filter_layout_generation = generation
                return generation

    def preflight_native_count(
        self,
        filters: Mapping[str, Any],
        native_filter_resolver: NativeFilterResolver,
        native_filter_layout_registrar: Callable[[Sequence[int]], None],
        telemetry: Optional[CuVSSearchTelemetry] = None,
    ) -> Optional[int]:
        """Return the native-route candidate count, or None for the cuVS path."""

        started = time.perf_counter()
        try:
            if not self.auto_memory or not filters:
                return None
            cache_key = self._filter_cache_key(filters)
            with self._lock:
                if self._closed:
                    return None
                if telemetry is not None:
                    telemetry.filter_kind = (
                        "path"
                        if _filter_uses_field_type(filters, self.field_types, "path")
                        else "scalar"
                    )
                    telemetry.records_generation = self._records_generation
                    telemetry.index_size = len(self._records)
                native_threshold = self.native_filter_threshold(filters)
                if native_threshold <= 0:
                    return None
                cached_route = self._lookup_preflight_route_(cache_key)
                if cached_route is not None:
                    route_count, eligible_count = cached_route
                    if telemetry is not None:
                        telemetry.filter_cache_hit = True
                        telemetry.eligible_count = eligible_count
                    return route_count

            # Preserve the existing _filter_layout_lock -> _lock order. Flight
            # coordination starts only after layout registration releases both.
            generation = self._ensure_native_filter_layout(native_filter_layout_registrar)
            if generation is None:
                return None

            flight_key: Optional[Tuple[int, str]] = None
            flight: Optional[_NativeFilterPreflightFlight] = None
            is_owner = True
            try:
                with self._lock:
                    if self._closed or self._records_generation != generation:
                        return None
                    cached_route = self._lookup_preflight_route_(cache_key)
                    if cached_route is not None:
                        route_count, eligible_count = cached_route
                        if telemetry is not None:
                            telemetry.filter_cache_hit = True
                            telemetry.eligible_count = eligible_count
                        return route_count
                    if cache_key is not None:
                        flight_key = (generation, cache_key)
                        flight = self._preflight_flights.get(flight_key)
                        if flight is None:
                            flight = _NativeFilterPreflightFlight(generation=generation)
                            self._preflight_flights[flight_key] = flight
                        else:
                            flight.participants += 1
                            is_owner = False

                if not is_owner:
                    # Never wait while holding _lock. Invalidation and close can
                    # therefore mark this flight stale and wake every waiter.
                    flight.done.wait()
                    with self._lock:
                        stale = (
                            flight.stale or self._closed or self._records_generation != generation
                        )
                        error = flight.error
                        route_count = flight.route_count
                        eligible_count = flight.eligible_count
                    if stale:
                        return None
                    if error is not None:
                        raise error
                    if telemetry is not None:
                        telemetry.filter_cache_hit = True
                        telemetry.eligible_count = eligible_count
                    return route_count

                # _resolve_native_filter only snapshots state under _lock; the
                # potentially expensive native resolver itself runs unlocked.
                resolved = self._resolve_native_filter(filters, native_filter_resolver)
                with self._lock:
                    stale = (
                        self._closed
                        or self._records_generation != generation
                        or (flight is not None and flight.stale)
                    )
                    if flight is not None and self._preflight_flights.get(flight_key) is flight:
                        self._preflight_flights.pop(flight_key, None)
                    if stale:
                        if flight is not None:
                            flight.stale = True
                            flight.done.set()
                        return None
                    route_count, eligible_count, cache_hit = self._store_preflight_route_(
                        cache_key,
                        resolved,
                    )
                    if flight is not None:
                        flight.route_count = route_count
                        flight.eligible_count = eligible_count
                        flight.done.set()
            except BaseException as exc:
                if is_owner and flight is not None:
                    with self._lock:
                        if self._preflight_flights.get(flight_key) is flight:
                            self._preflight_flights.pop(flight_key, None)
                        if not flight.stale:
                            flight.error = exc
                        flight.done.set()
                raise

            if telemetry is not None:
                telemetry.filter_cache_hit = cache_hit
                telemetry.eligible_count = eligible_count
            return route_count
        finally:
            if telemetry is not None:
                telemetry.preflight_ms += (time.perf_counter() - started) * 1000.0

    def _resolve_native_filter(
        self,
        filters: Mapping[str, Any],
        native_filter_resolver: NativeFilterResolver,
    ) -> _ResolvedNativeFilter:
        with self._lock:
            projection_generation = self._records_generation
            projection_layout_generation = self._filter_layout_generation
            projection_row_count = len(self._records)
        evaluation = native_filter_resolver(filters)
        if len(evaluation) == 2:
            words, eligible_count = evaluation
            native_filter_token = 0
        else:
            words, eligible_count, native_filter_token = evaluation
        bitset_words = tuple(int(word) for word in words)
        eligible_count = int(eligible_count)
        native_threshold = self.native_filter_threshold(filters)
        route_native = (
            self.auto_memory and native_threshold > 0 and eligible_count <= native_threshold
        )
        if not route_native and eligible_count > 0:
            # Resolver work intentionally runs outside the records lock. Only
            # enforce the ABI against the same registered layout snapshot;
            # callers discard the result when a concurrent mutation wins.
            with self._lock:
                projection_is_stable = (
                    projection_layout_generation == projection_generation
                    and self._records_generation == projection_generation
                    and self._filter_layout_generation == projection_generation
                )
            required_words = (projection_row_count + 31) // 32
            if projection_is_stable and len(bitset_words) < required_words:
                raise RuntimeError(
                    "Native filter resolver returned an incomplete bitset for GPU routing: "
                    f"got {len(bitset_words)} words for {projection_row_count} rows "
                    f"(expected at least {required_words})"
                )
        return _ResolvedNativeFilter(
            bitset_words=bitset_words,
            eligible_count=eligible_count,
            route_native=route_native,
            native_threshold=native_threshold,
            native_filter_token=int(native_filter_token),
        )

    def _prepare_filter(
        self,
        filters: Mapping[str, Any],
        labels: Sequence[int],
        native_filter_resolver: Optional[NativeFilterResolver] = None,
        resolved_native_filter: Optional[_ResolvedNativeFilter] = None,
    ) -> _CachedFilter:
        cache_key = self._filter_cache_key(filters)
        cached = self._get_cached_filter(cache_key)
        if cached is not None:
            return cached

        if native_filter_resolver is not None:
            resolved = resolved_native_filter or self._resolve_native_filter(
                filters, native_filter_resolver
            )
            eligible_count = resolved.eligible_count
            native_threshold = resolved.native_threshold
            route_native = resolved.route_native
            if route_native or eligible_count == 0:
                prepared = None
            else:
                prepare_filter_words = getattr(self._runtime, "prepare_filter_words", None)
                if prepare_filter_words is not None:
                    prepared = prepare_filter_words(resolved.bitset_words)
                else:
                    prepared = tuple(
                        bool(resolved.bitset_words[row // 32] & (1 << (row % 32)))
                        for row in range(len(labels))
                    )
        else:
            mask = [
                matches_filter(self._records[label].fields, filters, self.field_types)
                for label in labels
            ]
            eligible_count = sum(mask)
            prepare_filter = getattr(self._runtime, "prepare_filter", None)
            if eligible_count == 0:
                prepared = None
            elif prepare_filter is not None:
                prepared = prepare_filter(mask)
            else:
                prepared = tuple(mask)
            route_native = False
            native_threshold = 0
        cached = _CachedFilter(
            prepared=prepared,
            eligible_count=eligible_count,
            route_native=route_native,
            native_threshold=native_threshold,
            native_filter_token=(
                resolved_native_filter.native_filter_token
                if resolved_native_filter is not None
                else 0
            ),
        )
        self._cache_filter(cache_key, cached)
        return cached

    def _check_auto_memory_budget(
        self,
        vector_count: int,
        telemetry: Optional[CuVSSearchTelemetry],
    ) -> None:
        """Raise before a cuVS build when the estimated GPU peak is unsafe."""

        estimate = estimate_cuvs_memory(
            vector_count=vector_count,
            dimension=self.dimension,
            algorithm=self.algorithm,
            build_params=self._build_params,
            filter_cache_size=self.filter_cache_size,
            safety_factor=self.auto_memory_safety_factor,
            dtype=self.dtype,
        )
        if telemetry is not None:
            telemetry.memory_estimated_peak_bytes = estimate.estimated_peak_bytes
        try:
            free_bytes, total_bytes = self._runtime.memory_info()
        except Exception as exc:
            raise CuVSMemoryBudgetError("cuVS auto mode could not read free GPU memory") from exc
        usable_bytes = max(0, free_bytes - self.auto_memory_reserve_bytes)
        if telemetry is not None:
            telemetry.memory_free_bytes = free_bytes
            telemetry.memory_usable_bytes = usable_bytes
        if estimate.estimated_peak_bytes > usable_bytes:
            raise CuVSMemoryBudgetError(
                "cuVS auto mode kept native search because the estimated GPU peak "
                f"({estimate.estimated_peak_bytes} bytes) exceeds the usable free "
                f"memory ({usable_bytes} of {total_bytes} bytes after reserve)"
            )

    def prepare_rebuild(
        self,
        telemetry: Optional[CuVSSearchTelemetry] = None,
    ) -> Optional[_CuVSBuildCandidate]:
        with self._lock:
            if not self._dirty:
                return None
            vector_count = len(self._records)
            generation = self._records_generation
            can_release_unused = self._active_searches == 0
            if can_release_unused:
                with self._runtime_device_scope():
                    self._snapshot = None

        build_started = time.perf_counter()
        try:
            if vector_count == 0:
                return _CuVSBuildCandidate(
                    runtime_index=None,
                    labels=(),
                    generation=generation,
                )

            # Reject stable, known-insufficient budgets before copying every
            # Python vector into a build dataset. Do not hold ``self._lock``
            # while taking the device build lock: foreground rebuilds already
            # use the opposite order through ``_search_admitted``.
            if self.auto_memory:
                with _CUVS_MEMORY_COORDINATOR.build_lock(self._runtime):
                    release_index = getattr(self._runtime, "release_index", None)
                    if release_index is not None and can_release_unused:
                        release_index()
                    self._check_auto_memory_budget(vector_count, telemetry)

            with self._lock:
                if self._records_generation != generation:
                    return _CuVSBuildCandidate(
                        runtime_index=None,
                        labels=(),
                        generation=generation,
                    )
                labels = tuple(self._records)
                dataset = _PackedFP32Rows(
                    tuple(self._records[label].vector for label in labels),
                    self.dimension,
                )

            with _CUVS_MEMORY_COORDINATOR.build_lock(self._runtime):
                # Admission is checked again while the per-device build lock is
                # held. Another collection may have allocated GPU memory while
                # this collection materialized its host dataset.
                if self.auto_memory:
                    self._check_auto_memory_budget(len(labels), telemetry)
                try:
                    if telemetry is not None:
                        telemetry.build_performed = True
                    runtime_index = self._runtime.build(dataset)
                except Exception as exc:
                    is_out_of_memory = getattr(self._runtime, "is_out_of_memory", None)
                    if self.auto_memory and is_out_of_memory is not None and is_out_of_memory(exc):
                        release_index = getattr(self._runtime, "release_index", None)
                        if release_index is not None and can_release_unused:
                            release_index()
                        raise CuVSMemoryBudgetError(
                            "cuVS auto mode fell back to native search after a GPU "
                            "allocation failure"
                        ) from exc
                    raise
            return _CuVSBuildCandidate(
                runtime_index=runtime_index,
                labels=labels,
                generation=generation,
            )
        finally:
            if telemetry is not None:
                telemetry.build_ms += (time.perf_counter() - build_started) * 1000.0

    def commit_rebuild(
        self,
        candidate: _CuVSBuildCandidate,
        native_filter_layout_registrar: Optional[Callable[[Sequence[int]], None]] = None,
    ) -> bool:
        with self._lock:
            with self._runtime_device_scope():
                if candidate.consumed:
                    raise RuntimeError("cuVS rebuild candidate has already been consumed")
                if self._records_generation != candidate.generation:
                    candidate.consumed = True
                    candidate.runtime_index = None
                    return False
                if (
                    native_filter_layout_registrar is not None
                    and self._filter_layout_generation != candidate.generation
                ):
                    try:
                        native_filter_layout_registrar(candidate.labels)
                    except Exception:
                        candidate.consumed = True
                        candidate.runtime_index = None
                        raise
                    self._filter_layout_generation = candidate.generation
                runtime_index = candidate.runtime_index
                candidate.consumed = True
                candidate.runtime_index = None
                self._snapshot = (
                    _CuVSIndexSnapshot(
                        runtime_index=runtime_index,
                        labels=candidate.labels,
                        generation=candidate.generation,
                    )
                    if runtime_index is not None
                    else None
                )
            self._dirty = False
            logger.info(
                "Built cuVS %s index with %d vectors",
                self.algorithm,
                len(candidate.labels),
            )
            return True

    def discard_rebuild(self, candidate: _CuVSBuildCandidate) -> None:
        """Release an uncommitted GPU build candidate on the runtime device."""

        with self._lock:
            with self._runtime_device_scope():
                if candidate.consumed:
                    return
                candidate.consumed = True
                candidate.runtime_index = None

    def _rebuild_if_needed(
        self,
        native_filter_layout_registrar: Optional[Callable[[Sequence[int]], None]] = None,
        telemetry: Optional[CuVSSearchTelemetry] = None,
    ) -> None:
        while self.needs_rebuild:
            candidate = self.prepare_rebuild(telemetry)
            if candidate is None:
                return
            if self.commit_rebuild(candidate, native_filter_layout_registrar):
                return

    def search(
        self,
        query_vector: Sequence[float],
        limit: int,
        filters: Optional[Mapping[str, Any]],
        native_filter_resolver: Optional[NativeFilterResolver] = None,
        native_filter_layout_registrar: Optional[Callable[[Sequence[int]], None]] = None,
        telemetry: Optional[CuVSSearchTelemetry] = None,
    ) -> Tuple[List[int], List[float]]:
        if limit <= 0:
            return [], []
        queue_started = time.perf_counter()
        self._gpu_search_gate.acquire()
        if telemetry is not None:
            telemetry.queue_ms += (time.perf_counter() - queue_started) * 1000.0
        try:
            return self._search_admitted(
                query_vector,
                limit,
                filters,
                native_filter_resolver,
                native_filter_layout_registrar,
                telemetry,
            )
        finally:
            self._gpu_search_gate.release()

    def _search_admitted(
        self,
        query_vector: Sequence[float],
        limit: int,
        filters: Optional[Mapping[str, Any]],
        native_filter_resolver: Optional[NativeFilterResolver] = None,
        native_filter_layout_registrar: Optional[Callable[[Sequence[int]], None]] = None,
        telemetry: Optional[CuVSSearchTelemetry] = None,
    ) -> Tuple[List[int], List[float]]:
        query = self._prepare_vector(query_vector)
        if self.auto_memory and native_filter_layout_registrar is not None:
            self._ensure_native_filter_layout(native_filter_layout_registrar)
        with self._lock:
            if self._closed:
                raise RuntimeError("cuVS dense index is closed")
            if telemetry is not None:
                telemetry.filter_kind = (
                    "path"
                    if filters and _filter_uses_field_type(filters, self.field_types, "path")
                    else "scalar"
                    if filters
                    else "none"
                )
                telemetry.records_generation = self._records_generation
                telemetry.index_size = len(self._records)
            cached_filter: Optional[_CachedFilter] = None
            resolved_native_filter: Optional[_ResolvedNativeFilter] = None
            filter_layout_is_current = self._filter_layout_generation == self._records_generation

            # Auto mode decides whether a selective filter should remain native
            # before paying GPU admission or rebuild costs. A dirty native
            # layout is refreshed against the pending cuVS row order only; the
            # live GPU row mapping is not changed until a build actually runs.
            if filters and self.auto_memory and native_filter_resolver is not None:
                cache_key = self._filter_cache_key(filters)
                cached_filter = self._get_cached_filter(cache_key)
                if cached_filter is not None and telemetry is not None:
                    telemetry.filter_cache_hit = True
                    telemetry.eligible_count = cached_filter.eligible_count
                if cached_filter is None:
                    resolved_native_filter = self._get_preflight_filter(cache_key)
                    if resolved_native_filter is not None and telemetry is not None:
                        telemetry.filter_cache_hit = True
                        telemetry.eligible_count = resolved_native_filter.eligible_count
                    if resolved_native_filter is None:
                        if (
                            self._dirty
                            and native_filter_layout_registrar is not None
                            and not filter_layout_is_current
                        ):
                            native_filter_layout_registrar(list(self._records))
                            filter_layout_is_current = True
                            self._filter_layout_generation = self._records_generation
                        resolved_native_filter = self._resolve_native_filter(
                            filters, native_filter_resolver
                        )
                    if (
                        resolved_native_filter.route_native
                        or resolved_native_filter.eligible_count == 0
                    ):
                        cached_filter = _CachedFilter(
                            prepared=None,
                            eligible_count=resolved_native_filter.eligible_count,
                            route_native=resolved_native_filter.route_native,
                            native_threshold=resolved_native_filter.native_threshold,
                            native_filter_token=resolved_native_filter.native_filter_token,
                        )
                        self._cache_filter(cache_key, cached_filter)

                if cached_filter is not None:
                    if cached_filter.eligible_count == 0:
                        return [], []
                    if cached_filter.route_native:
                        raise CuVSNativeRouteError(
                            "cuVS auto mode routed a selective filter to native search "
                            f"({cached_filter.eligible_count} candidates <= "
                            f"{cached_filter.native_threshold})"
                        )

            self._rebuild_if_needed(
                native_filter_layout_registrar,
                telemetry=telemetry,
            )
            snapshot = self._snapshot
            if snapshot is None:
                return [], []

            mask: Optional[Any] = None
            if filters:
                filter_started = time.perf_counter()
                if cached_filter is None:
                    cached_filter = self._get_cached_filter(self._filter_cache_key(filters))
                    if cached_filter is not None and telemetry is not None:
                        telemetry.filter_cache_hit = True
                cached_filter = cached_filter or self._prepare_filter(
                    filters,
                    snapshot.labels,
                    native_filter_resolver,
                    resolved_native_filter,
                )
                if telemetry is not None:
                    telemetry.filter_prepare_ms += (time.perf_counter() - filter_started) * 1000.0
                    telemetry.eligible_count = cached_filter.eligible_count
                mask = cached_filter.prepared
                eligible_count = cached_filter.eligible_count
                if eligible_count == 0:
                    return [], []
                if cached_filter.route_native:
                    raise CuVSNativeRouteError(
                        "cuVS auto mode routed a selective filter to native search "
                        f"({eligible_count} candidates <= "
                        f"{cached_filter.native_threshold})"
                    )
                result_limit = min(limit, eligible_count)
            else:
                result_limit = min(limit, len(snapshot.labels))
            self._active_searches += 1

        labels: List[int] = []
        scores: List[float] = []
        try:
            gpu_started = time.perf_counter()
            try:
                offsets, distances = self._runtime.search(
                    snapshot.runtime_index,
                    query,
                    result_limit,
                    mask,
                )
            finally:
                if telemetry is not None:
                    telemetry.gpu_search_ms += (time.perf_counter() - gpu_started) * 1000.0
            for offset, distance in zip(offsets, distances, strict=True):
                if offset < 0 or offset >= len(snapshot.labels):
                    continue
                labels.append(snapshot.labels[offset])
                scores.append(1.0 - distance if self.distance == "l2" else distance)
        finally:
            try:
                # A concurrent rebuild can remove the index/cache's owning
                # references while this search still holds its immutable snapshot
                # and prepared filter. Release those last local references before
                # advertising the search as idle or restoring another CUDA device.
                with self._runtime_device_scope():
                    mask = None
                    cached_filter = None
                    resolved_native_filter = None
                    snapshot = None
            finally:
                with self._lock:
                    self._active_searches -= 1
                    if self._active_searches == 0:
                        self._idle_condition.notify_all()
        return labels, scores

    def close(self) -> None:
        with self._lock:
            if self._close_complete:
                return
            if not self._closed:
                self._closed = True
                self._records_generation += 1
                self._filter_layout_generation = -1
                self._cancel_preflight_flights_()
            while self._active_searches:
                self._idle_condition.wait()
            with self._runtime_device_scope():
                self._snapshot = None
                self._filter_cache.clear()
                self._preflight_filter_cache.clear()
                try:
                    self._runtime.close()
                finally:
                    # A failed recovery can otherwise keep this partially packed
                    # host shadow alive through the constructor traceback.
                    self._records.clear()
            self._close_complete = True
