# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

"""Read and validate native metrics without retaining a second metrics state."""

from __future__ import annotations

import math
import re
from typing import Any

from openviking.metrics.core.base import ReadEnvelope

from .base import DomainStatsMetricDataSource

_NAME = re.compile(r"[a-zA-Z_:][a-zA-Z0-9_:]*")
_LABEL = re.compile(r"[a-zA-Z_][a-zA-Z0-9_]*")
_FORBIDDEN_LABELS = frozenset(
    {
        "mount",
        "account_id",
        "path",
        "resource_uri",
        "user_id",
        "session_id",
        "owner",
        "owner_id",
        "lock_owner",
        "lock_path",
        "backend_error",
        "error",
        "error_message",
        "error_text",
    }
)
_FIELDS = {
    "counter": {"name", "labels", "type", "value", "scale"},
    "gauge": {"name", "labels", "type", "value"},
    "histogram": {
        "name",
        "labels",
        "type",
        "bucket_bounds",
        "bucket_counts",
        "count",
        "sum",
        "scale",
    },
}


def _u64(value: object) -> int:
    """Return an unsigned 64-bit integer input, rejecting booleans and out-of-range values."""
    if type(value) is not int or not 0 <= value <= 2**64 - 1:
        raise ValueError("native metric integer must fit u64")
    return value


def _finite(value: object) -> float:
    """Return a finite numeric input as float, rejecting booleans and nonnumeric values."""
    if type(value) not in (int, float) or not math.isfinite(value):
        raise ValueError("native metric value must be finite")
    return float(value)


class RagfsMetricDataSource(DomainStatsMetricDataSource):
    """Read one flat native metrics batch and validate it before Registry mutation."""

    def __init__(self, *, service: Any = None) -> None:
        """Keep the service reference for late client resolution; return None."""
        self._service = service

    def read_metrics(self) -> ReadEnvelope[list[dict[str, object]]]:
        """Return one validated native batch, or a failed envelope with an empty list."""
        return self.safe_read(self._read, default=[])

    def _read(self) -> list[dict[str, object]]:
        """Read the current service client's metrics once and return validated raw records."""
        client = getattr(self._service, "_agfs_client", None)
        if client is None:
            raise RuntimeError("RAGFS client is not available")
        records = client.metrics()
        if not isinstance(records, list):
            raise ValueError("native metrics must be a list")
        seen = set()
        families = {}
        for record in records:
            if not isinstance(record, dict):
                raise ValueError("native metric must be a dict")
            name, labels, kind = record.get("name"), record.get("labels"), record.get("type")
            if (
                not isinstance(name, str)
                or not _NAME.fullmatch(name)
                or name.startswith("openviking_")
            ):
                raise ValueError("native metric name must be valid and unprefixed")
            if not isinstance(kind, str) or kind not in _FIELDS or set(record) != _FIELDS[kind]:
                raise ValueError("native metric fields do not match its type")
            if not isinstance(labels, dict) or any(
                not isinstance(key, str)
                or not isinstance(value, str)
                or not _LABEL.fullmatch(key)
                or key.startswith("__")
                or key in _FORBIDDEN_LABELS
                or (kind == "histogram" and key == "le")
                for key, value in labels.items()
            ):
                raise ValueError("native metric labels are invalid or forbidden")
            identity = (name, tuple(sorted(labels.items())))
            if identity in seen:
                raise ValueError("duplicate native metric series")
            seen.add(identity)
            scale, bounds = None, ()
            if kind == "gauge":
                _finite(record["value"])
            else:
                scale = _finite(record["scale"])
                if scale <= 0:
                    raise ValueError("native metric scale must be positive")
                if kind == "counter":
                    _finite(_u64(record["value"]) * scale)
                else:
                    raw_bounds, counts = record["bucket_bounds"], record["bucket_counts"]
                    if not isinstance(raw_bounds, list) or not isinstance(counts, list):
                        raise ValueError("native histogram bounds and counts must be lists")
                    bounds = tuple(_u64(bound) for bound in raw_bounds)
                    scaled_bounds = tuple(_finite(bound * scale) for bound in bounds)
                    if any(
                        left >= right for left, right in zip(bounds, bounds[1:], strict=False)
                    ) or any(
                        left >= right
                        for left, right in zip(scaled_bounds, scaled_bounds[1:], strict=False)
                    ):
                        raise ValueError("native histogram bounds must remain increasing")
                    count = _u64(record["count"])
                    if len(counts) != len(bounds) + 1 or sum(_u64(n) for n in counts) != count:
                        raise ValueError("native histogram counts do not match bounds or count")
                    _finite(_u64(record["sum"]) * scale)
            shape = (kind, tuple(sorted(labels)), scale, bounds)
            if name in families and families[name] != shape:
                raise ValueError("inconsistent native metric family")
            families[name] = shape
        return records
