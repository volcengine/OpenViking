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
from collections import OrderedDict
from dataclasses import dataclass
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from openviking.storage.vectordb.store.data import CandidateData, DeltaRecord

logger = logging.getLogger(__name__)


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


def estimate_cuvs_memory(
    *,
    vector_count: int,
    dimension: int,
    algorithm: str,
    build_params: Mapping[str, Any],
    filter_cache_size: int,
    safety_factor: float,
) -> CuVSMemoryEstimate:
    """Estimate peak VRAM without changing the explicit cuVS backend behavior.

    The estimate accounts for the float32 dataset, retained and intermediate
    CAGRA graphs, and the configured number of cached filter bitsets.  cuVS and
    allocator workspaces vary by release and build algorithm, so the configured
    safety factor intentionally covers the remaining uncertainty.
    """

    vector_count = max(0, int(vector_count))
    dimension = max(0, int(dimension))
    safety_factor = float(safety_factor)
    if safety_factor < 1.0:
        raise ValueError("cuVS auto memory safety factor must be at least 1.0")

    vector_bytes = vector_count * dimension * 4
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


class _CuVSRuntime:
    """Small adapter around the public cuVS Python API."""

    def __init__(
        self,
        algorithm: str,
        metric: str,
        build_params: Mapping[str, Any],
        search_params: Mapping[str, Any],
    ):
        try:
            import cupy as cp
            from cuvs.neighbors import brute_force, cagra, filters

            device_count = cp.cuda.runtime.getDeviceCount()
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
        self.algorithm = algorithm
        self.metric = metric
        self.build_params = dict(build_params)
        self.search_params = dict(search_params)
        self.dataset = None

    def memory_info(self) -> Tuple[int, int]:
        free_bytes, total_bytes = self.cp.cuda.runtime.memGetInfo()
        return int(free_bytes), int(total_bytes)

    def release_index(self) -> None:
        self.dataset = None
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
        self.dataset = self.cp.asarray(dataset, dtype=self.cp.float32)
        if self.algorithm == "brute_force":
            return self.brute_force.build(self.dataset, metric=self.metric)
        params = self.cagra.IndexParams(metric=self.metric, **self.build_params)
        return self.cagra.build(params, self.dataset)

    def _prefilter(self, mask: Sequence[bool]):
        return self.filters.from_bitset(self.prepare_filter(mask))

    def prepare_filter(self, mask: Sequence[bool]):
        """Pack a host mask once and retain its device allocation for reuse."""

        word_count = (len(mask) + 31) // 32
        words = [0] * word_count
        for index, included in enumerate(mask):
            if included:
                words[index // 32] |= 1 << (index % 32)
        return self.cp.asarray(words, dtype=self.cp.uint32)

    def prepare_filter_words(self, words: Sequence[int]):
        """Copy an already packed native filter bitmap to the device."""

        return self.cp.asarray(words, dtype=self.cp.uint32)

    def search(
        self,
        index: Any,
        query: Sequence[float],
        limit: int,
        mask: Optional[Any],
    ) -> Tuple[List[int], List[float]]:
        queries = self.cp.asarray([query], dtype=self.cp.float32)
        if mask is None:
            prefilter = None
        elif isinstance(mask, self.cp.ndarray) and mask.dtype == self.cp.uint32:
            prefilter = self.filters.from_bitset(mask)
        else:
            prefilter = self._prefilter(mask)
        if self.algorithm == "brute_force":
            distances, neighbors = self.brute_force.search(
                index, queries, limit, prefilter=prefilter
            )
        else:
            search_params = dict(self.search_params)
            configured_itopk = int(search_params.get("itopk_size", 64))
            minimum_itopk = ((limit + 31) // 32) * 32
            search_params["itopk_size"] = max(configured_itopk, minimum_itopk)
            params = self.cagra.SearchParams(**search_params)
            distances, neighbors = self.cagra.search(
                params, index, queries, limit, filter=prefilter
            )
        host_neighbors = self.cp.asnumpy(neighbors)[0].tolist()
        host_distances = self.cp.asnumpy(distances)[0].tolist()
        return [int(item) for item in host_neighbors], [float(item) for item in host_distances]

    def close(self) -> None:
        self.release_index()


@dataclass(frozen=True)
class _Record:
    vector: Tuple[float, ...]
    fields: Mapping[str, Any]


@dataclass(frozen=True)
class _CachedFilter:
    prepared: Any
    eligible_count: int
    route_native: bool = False
    native_threshold: int = 0


@dataclass(frozen=True)
class _ResolvedNativeFilter:
    bitset_words: Tuple[int, ...]
    eligible_count: int
    route_native: bool
    native_threshold: int


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

        self.fallback_to_native = bool(config.get("fallback_to_native", True))
        self.filter_cache_size = int(config.get("filter_cache_size", 16))
        if self.filter_cache_size < 0:
            raise ValueError("cuVS filter_cache_size cannot be negative")
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
        )
        self._records: Dict[int, _Record] = {}
        self._labels: List[int] = []
        self._index: Any = None
        self._dirty = True
        self._filter_cache: OrderedDict[str, _CachedFilter] = OrderedDict()
        self._lock = threading.RLock()
        logger.info(
            "Initialized cuVS dense index: algorithm=%s metric=%s dimension=%d",
            self.algorithm,
            self._metric,
            self.dimension,
        )

    @property
    def size(self) -> int:
        with self._lock:
            return len(self._records)

    def _prepare_vector(self, vector: Sequence[float]) -> Tuple[float, ...]:
        if len(vector) != self.dimension:
            raise ValueError(
                f"cuVS vector dimension mismatch: expected {self.dimension}, got {len(vector)}"
            )
        prepared = _normalize(vector) if self.normalize_vectors else [float(v) for v in vector]
        return tuple(prepared)

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
                    vector=self._prepare_vector(candidate.vector),
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
                    vector=self._prepare_vector(record.vector),
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
        self._dirty = True
        self._filter_cache.clear()

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
        self._filter_cache[cache_key] = cached
        while len(self._filter_cache) > self.filter_cache_size:
            self._filter_cache.popitem(last=False)

    def _resolve_native_filter(
        self,
        filters: Mapping[str, Any],
        native_filter_resolver: Callable[[Mapping[str, Any]], Tuple[Sequence[int], int]],
    ) -> _ResolvedNativeFilter:
        words, eligible_count = native_filter_resolver(filters)
        native_threshold = (
            self.auto_path_filter_native_threshold
            if _filter_uses_field_type(filters, self.field_types, "path")
            else self.auto_filter_native_threshold
        )
        route_native = (
            self.auto_memory and native_threshold > 0 and eligible_count <= native_threshold
        )
        return _ResolvedNativeFilter(
            bitset_words=tuple(int(word) for word in words),
            eligible_count=int(eligible_count),
            route_native=route_native,
            native_threshold=native_threshold,
        )

    def _prepare_filter(
        self,
        filters: Mapping[str, Any],
        native_filter_resolver: Optional[
            Callable[[Mapping[str, Any]], Tuple[Sequence[int], int]]
        ] = None,
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
            if route_native:
                prepared = None
            else:
                prepare_filter_words = getattr(self._runtime, "prepare_filter_words", None)
                if prepare_filter_words is not None:
                    prepared = prepare_filter_words(resolved.bitset_words)
                else:
                    prepared = tuple(
                        bool(resolved.bitset_words[row // 32] & (1 << (row % 32)))
                        for row in range(len(self._labels))
                    )
        else:
            mask = [
                matches_filter(self._records[label].fields, filters, self.field_types)
                for label in self._labels
            ]
            prepare_filter = getattr(self._runtime, "prepare_filter", None)
            prepared = prepare_filter(mask) if prepare_filter is not None else tuple(mask)
            eligible_count = sum(mask)
            route_native = False
            native_threshold = 0
        cached = _CachedFilter(
            prepared=prepared,
            eligible_count=eligible_count,
            route_native=route_native,
            native_threshold=native_threshold,
        )
        self._cache_filter(cache_key, cached)
        return cached

    def _rebuild_if_needed(
        self,
        native_filter_layout_registrar: Optional[Callable[[Sequence[int]], None]] = None,
        *,
        filter_layout_is_current: bool = False,
    ) -> None:
        if not self._dirty:
            return
        self._labels = list(self._records)
        if not self._labels:
            self._index = None
            self._dirty = False
            return
        if self.auto_memory:
            self._index = None
            release_index = getattr(self._runtime, "release_index", None)
            if release_index is not None:
                release_index()
            estimate = estimate_cuvs_memory(
                vector_count=len(self._labels),
                dimension=self.dimension,
                algorithm=self.algorithm,
                build_params=self._build_params,
                filter_cache_size=self.filter_cache_size,
                safety_factor=self.auto_memory_safety_factor,
            )
            try:
                free_bytes, total_bytes = self._runtime.memory_info()
            except Exception as exc:
                raise CuVSMemoryBudgetError(
                    "cuVS auto mode could not read free GPU memory"
                ) from exc
            usable_bytes = max(0, free_bytes - self.auto_memory_reserve_bytes)
            if estimate.estimated_peak_bytes > usable_bytes:
                raise CuVSMemoryBudgetError(
                    "cuVS auto mode kept native search because the estimated GPU peak "
                    f"({estimate.estimated_peak_bytes} bytes) exceeds the usable free "
                    f"memory ({usable_bytes} of {total_bytes} bytes after reserve)"
                )
        dataset = [self._records[label].vector for label in self._labels]
        self._index = None
        try:
            self._index = self._runtime.build(dataset)
        except Exception as exc:
            is_out_of_memory = getattr(self._runtime, "is_out_of_memory", None)
            if self.auto_memory and is_out_of_memory is not None and is_out_of_memory(exc):
                release_index = getattr(self._runtime, "release_index", None)
                if release_index is not None:
                    release_index()
                raise CuVSMemoryBudgetError(
                    "cuVS auto mode fell back to native search after a GPU allocation failure"
                ) from exc
            raise
        if native_filter_layout_registrar is not None and not filter_layout_is_current:
            native_filter_layout_registrar(self._labels)
        self._dirty = False
        logger.info("Built cuVS %s index with %d vectors", self.algorithm, len(self._labels))

    def search(
        self,
        query_vector: Sequence[float],
        limit: int,
        filters: Optional[Mapping[str, Any]],
        native_filter_resolver: Optional[
            Callable[[Mapping[str, Any]], Tuple[Sequence[int], int]]
        ] = None,
        native_filter_layout_registrar: Optional[Callable[[Sequence[int]], None]] = None,
    ) -> Tuple[List[int], List[float]]:
        if limit <= 0:
            return [], []
        query = self._prepare_vector(query_vector)
        with self._lock:
            cached_filter: Optional[_CachedFilter] = None
            resolved_native_filter: Optional[_ResolvedNativeFilter] = None
            filter_layout_is_current = False

            # Auto mode decides whether a selective filter should remain native
            # before paying GPU admission or rebuild costs. A dirty native
            # layout is refreshed against the pending cuVS row order only; the
            # live GPU row mapping is not changed until a build actually runs.
            if filters and self.auto_memory and native_filter_resolver is not None:
                cache_key = self._filter_cache_key(filters)
                cached_filter = self._get_cached_filter(cache_key)
                if cached_filter is None:
                    if self._dirty and native_filter_layout_registrar is not None:
                        native_filter_layout_registrar(list(self._records))
                        filter_layout_is_current = True
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
                filter_layout_is_current=filter_layout_is_current,
            )
            if self._index is None:
                return [], []

            mask: Optional[Any] = None
            if filters:
                cached_filter = cached_filter or self._prepare_filter(
                    filters,
                    native_filter_resolver,
                    resolved_native_filter,
                )
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
                result_limit = min(limit, len(self._labels))

            offsets, distances = self._runtime.search(self._index, query, result_limit, mask)
            labels: List[int] = []
            scores: List[float] = []
            for offset, distance in zip(offsets, distances, strict=True):
                if offset < 0 or offset >= len(self._labels):
                    continue
                labels.append(self._labels[offset])
                scores.append(1.0 - distance if self.distance == "l2" else distance)
            return labels, scores

    def close(self) -> None:
        with self._lock:
            self._index = None
            self._labels = []
            self._filter_cache.clear()
            self._runtime.close()
