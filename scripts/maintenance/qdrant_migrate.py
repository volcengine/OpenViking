#!/usr/bin/env python3
"""Migrate a pre-#3872 Qdrant collection into the current OpenViking layout.

The command deliberately implements a blue-green copy: the source collection
and its legacy metadata sidecar are read only, while a separate target
collection receives current-format metadata and records.  It is safe to
re-run after an interrupted copy; points that already belong to the same
logical record are retained rather than overwritten.

The offline ``apply`` phase requires a frozen source compatibility window;
online ``prepare``/``backfill``/``reconcile`` phases keep legacy serving
available and only the final barrier-held reconcile/verify and cutover require
a short write barrier.  Keep the source for rollback/audit and perform any
application cutover separately.  The pre-#3872 global metadata sidecar defaults to
``__openviking_meta``; pass an override when the old deployment used a
different sidecar name.  Missing ``owner_user_id`` values are derived from
user-scoped URIs when possible; ownerless roots remain ownerless.  ACL fields
are copied as-is: missing or malformed legacy ACL fields remain fail-open until
records are rewritten.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import sqlite3
import struct
import subprocess
import sys
import tempfile
import time
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping
from urllib.parse import quote, urlsplit

# Make ``python scripts/maintenance/qdrant_migrate.py`` work from a checkout
# without requiring an editable install.
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from openviking.core.namespace import owner_fields_for_uri  # noqa: E402
from openviking.storage.acl import (  # noqa: E402
    ACL_CONTEXT_FIELDS,
    ACL_MODE_FIELD,
    AclMode,
    DirectAcl,
)
from openviking.storage.vectordb.collection.qdrant_rest import (  # noqa: E402
    QdrantError,
    QdrantRestClient,
    _validate_timeout_seconds,
    validate_qdrant_version,
)
from openviking.storage.vectordb.qdrant_sparse import (  # noqa: E402
    parse_sparse_point,
    sparse_owner_point_id,
    stable_sparse_index,
)
from openviking.storage.vectordb.qdrant_utils import (  # noqa: E402
    pending_work,
    qdrant_payload_field_schema,
    to_qdrant_point_id,
)

_META_VERSION = 1
_META_MARKER_ID = to_qdrant_point_id("openviking:metadata")
_META_VECTOR_NAME = "meta"
_ORIGINAL_ID_FIELD = "_openviking_original_id"
_INTERNAL_PAYLOAD_FIELDS = {"uri_depth", "scope_roots"}
_ACL_FIELDS = set(ACL_CONTEXT_FIELDS)
_SECURITY_PAYLOAD_FIELDS = {
    "uri",
    "account_id",
    "owner_user_id",
    "acl_enabled",
    *_ACL_FIELDS,
}
_CONTEXT_TYPES = {"memory", "resource", "skill"}
_SPARSE_TERM_MARKER = "_openviking_sparse_term"
_LEGACY_UINT64_MAX = 2**64 - 1
_QDRANT_SPARSE_INDEX_MAX = 0x7FFF_FFFF
_QDRANT_ID_NAMESPACE = uuid.UUID("4b6bb5a8-7f1f-5b1a-9d4c-b93f29b1d67c")
_INTEGER_RE = re.compile(r"^[+-]?[0-9]+$")
MIGRATOR_VERSION = "qdrant-blue-green-v1"
MAX_RECONCILIATION_ROUNDS = 3
_DEPLOYMENT_HOOK_NAMES = (
    "drain_legacy_writes",
    "remove_legacy_from_serving_path",
    "rollout_current",
    "wait_current_ready",
    "smoke_current_read_only",
    "remove_current_from_serving_path",
    "restore_legacy",
    "verify_legacy_read_path",
    "current_target_has_accepted_writes",
    "assert_target_not_served",
)
MIGRATION_STATES = frozenset(
    {"building", "ready", "cutting_over", "active", "retained", "rolled_back", "failed"}
)
_MIGRATION_STATE_SETUP = {
    "building": False,
    "failed": False,
    "rolled_back": False,
    "ready": True,
    "cutting_over": True,
    "active": True,
    "retained": True,
}


def _legacy_collection_metadata_id(collection_key: str) -> str:
    """Return the deterministic pre-#3872 collection metadata point ID."""

    return str(
        uuid.uuid5(
            uuid.NAMESPACE_URL,
            f"openviking:qdrant:collection:{collection_key}",
        )
    )


def _legacy_index_metadata_id(collection_key: str, index_name: str) -> str:
    """Return the deterministic pre-#3872 index metadata point ID."""

    return str(
        uuid.uuid5(
            uuid.NAMESPACE_URL,
            f"openviking:qdrant:index:{collection_key}:{index_name}",
        )
    )


class MigrationError(RuntimeError):
    """A migration preflight or apply failure."""


class SparseMigrationError(MigrationError):
    """Sparse vectors cannot be copied without an authoritative term map."""


@dataclass(frozen=True)
class CollectionLayout:
    """The physical vector layout discovered from a Qdrant collection."""

    dense_vector_name: str
    sparse_vector_name: str
    vector_dimension: int
    distance: str
    dense_datatype: str | None
    sparse_enabled: bool
    sparse_modifier: str | None
    sparse_datatype: str | None


@dataclass(frozen=True)
class LegacyMetadata:
    """The old QdrantMetaStore collection/index documents."""

    schema: dict[str, Any]
    indexes: dict[str, dict[str, Any]]


@dataclass(frozen=True)
class SourceSnapshot:
    """Validated source state captured at one point in time."""

    source_count: int
    fingerprint: str
    acl_incomplete_count: int
    sparse_term_count: int
    sparse_term_fingerprint: str
    transformed_source_fingerprint: str = ""
    target_content_fingerprint: str = ""


class _ScanManifest:
    """Disk-backed IDs and fingerprints for one bounded source/target scan."""

    def __init__(self) -> None:
        descriptor, self._path = tempfile.mkstemp(
            prefix="openviking-qdrant-migrate-",
            suffix=".sqlite3",
        )
        os.close(descriptor)
        self._connection = sqlite3.connect(self._path)
        self._connection.executescript(
            """
            PRAGMA journal_mode=DELETE;
            CREATE TABLE source_points (
                raw_type TEXT NOT NULL,
                raw_id TEXT NOT NULL,
                PRIMARY KEY (raw_type, raw_id)
            );
            CREATE TABLE logical_points (
                logical_id TEXT PRIMARY KEY,
                raw_type TEXT NOT NULL,
                raw_id TEXT NOT NULL
            );
            CREATE TABLE source_targets (
                target_id TEXT PRIMARY KEY,
                logical_id TEXT NOT NULL,
                fingerprint TEXT NOT NULL,
                content_fingerprint TEXT NOT NULL
            );
            CREATE TABLE sparse_terms (
                term TEXT PRIMARY KEY
            );
            CREATE TABLE target_points (
                target_id TEXT PRIMARY KEY,
                original_id TEXT,
                content_fingerprint TEXT,
                matched INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE target_logical_points (
                logical_id TEXT PRIMARY KEY,
                target_id TEXT NOT NULL
            );
            """
        )

    def close(self) -> None:
        try:
            self._connection.close()
        finally:
            Path(self._path).unlink(missing_ok=True)

    def __enter__(self) -> "_ScanManifest":
        return self

    def __exit__(self, _exc_type: Any, _exc_value: Any, _traceback: Any) -> None:
        self.close()

    @property
    def connection(self) -> sqlite3.Connection:
        return self._connection

    def add_source(
        self,
        *,
        raw_point_id: Any,
        logical_id: str,
        target_id: str,
        fingerprint: str,
        content_fingerprint: str,
        terms: Iterable[str],
    ) -> None:
        raw_key = (type(raw_point_id).__name__, str(raw_point_id))
        try:
            self._connection.execute(
                "INSERT INTO source_points(raw_type, raw_id) VALUES (?, ?)",
                raw_key,
            )
        except sqlite3.IntegrityError as exc:
            raise MigrationError(
                f"source pagination returned duplicate point id {raw_point_id!r}"
            ) from exc
        try:
            self._connection.execute(
                "INSERT INTO logical_points(logical_id, raw_type, raw_id) VALUES (?, ?, ?)",
                (logical_id, *raw_key),
            )
        except sqlite3.IntegrityError as exc:
            raise MigrationError(
                f"physical target point-id collision for duplicate logical source record id "
                f"{logical_id!r}"
            ) from exc
        try:
            self._connection.execute(
                """
                INSERT INTO source_targets(
                    target_id, logical_id, fingerprint, content_fingerprint
                ) VALUES (?, ?, ?, ?)
                """,
                (target_id, logical_id, fingerprint, content_fingerprint),
            )
        except sqlite3.IntegrityError as exc:
            existing = self._connection.execute(
                "SELECT logical_id FROM source_targets WHERE target_id = ?",
                (target_id,),
            ).fetchone()
            previous = existing[0] if existing else "<unknown>"
            raise MigrationError(
                f"physical target point-id collision between {previous!r} and {logical_id!r}"
            ) from exc
        self._connection.executemany(
            "INSERT OR IGNORE INTO sparse_terms(term) VALUES (?)",
            ((term,) for term in terms),
        )

    def add_target(self, point_id: Any, original_id: Any) -> None:
        point_id = _canonical_uuid_string(
            point_id,
            field_name="target physical point id",
        )
        if original_id is None or str(original_id) == "":
            raise MigrationError(f"target point {point_id!r} is missing {_ORIGINAL_ID_FIELD}")
        logical_id = str(original_id)
        try:
            self._connection.execute(
                "INSERT INTO target_logical_points(logical_id, target_id) VALUES (?, ?)",
                (logical_id, str(point_id)),
            )
        except sqlite3.IntegrityError as exc:
            existing = self._connection.execute(
                "SELECT target_id FROM target_logical_points WHERE logical_id = ?",
                (logical_id,),
            ).fetchone()
            previous = existing[0] if existing else "<unknown>"
            raise MigrationError(
                "target contains duplicate logical record id "
                f"{logical_id!r}: targets={previous!r},{point_id!r}"
            ) from exc
        try:
            self._connection.execute(
                "INSERT INTO target_points(target_id, original_id) VALUES (?, ?)",
                (point_id, logical_id),
            )
        except sqlite3.IntegrityError as exc:
            raise MigrationError(
                f"target pagination returned duplicate point id {point_id!r}"
            ) from exc

    def has_source_target(self, target_id: str) -> bool:
        return (
            self._connection.execute(
                "SELECT 1 FROM source_targets WHERE target_id = ?",
                (target_id,),
            ).fetchone()
            is not None
        )

    def delete_target(self, target_id: str) -> None:
        self._connection.execute(
            "UPDATE target_points SET matched = 1 WHERE target_id = ?",
            (target_id,),
        )

    def remove_target(self, target_id: str) -> None:
        self._connection.execute(
            "DELETE FROM target_points WHERE target_id = ?",
            (target_id,),
        )

    def set_target_content_fingerprint(self, target_id: str, fingerprint: str) -> None:
        updated = self._connection.execute(
            """
            UPDATE target_points
            SET content_fingerprint = ?
            WHERE target_id = ?
            """,
            (fingerprint, target_id),
        )
        if updated.rowcount != 1:
            raise MigrationError(f"target point {target_id!r} was not present in the scan manifest")

    def first_extra_target(self) -> str | None:
        row = self._connection.execute(
            """
            SELECT target_id
            FROM target_points
            WHERE matched = 0
              AND target_id NOT IN (SELECT target_id FROM source_targets)
            ORDER BY target_id
            LIMIT 1
            """
        ).fetchone()
        return row[0] if row is not None else None

    def iter_sparse_terms(self) -> Iterable[str]:
        rows = self._connection.execute("SELECT term FROM sparse_terms ORDER BY term")
        for (term,) in rows:
            yield str(term)

    def source_fingerprint(self) -> str:
        rows = self._connection.execute(
            "SELECT fingerprint FROM source_targets ORDER BY fingerprint"
        )
        return _fingerprint_values(
            (str(fingerprint) for (fingerprint,) in rows),
            ordered=True,
        )

    def transformed_source_fingerprint(self) -> str:
        rows = self._connection.execute(
            """
            SELECT content_fingerprint
            FROM source_targets
            ORDER BY content_fingerprint
            """
        )
        return _fingerprint_values(
            (str(fingerprint) for (fingerprint,) in rows),
            ordered=True,
        )

    def target_content_fingerprint(self) -> str:
        rows = self._connection.execute(
            """
            SELECT content_fingerprint
            FROM target_points
            WHERE content_fingerprint IS NOT NULL
            ORDER BY content_fingerprint
            """
        )
        expected = self._connection.execute("SELECT COUNT(*) FROM target_points").fetchone()
        complete = self._connection.execute(
            "SELECT COUNT(*) FROM target_points WHERE content_fingerprint IS NOT NULL"
        ).fetchone()
        if expected is None or complete is None or int(complete[0]) != int(expected[0]):
            raise MigrationError("target content fingerprint is incomplete")
        return _fingerprint_values(
            (str(fingerprint) for (fingerprint,) in rows),
            ordered=True,
        )

    def sparse_term_count(self) -> int:
        row = self._connection.execute("SELECT COUNT(*) FROM sparse_terms").fetchone()
        return int(row[0]) if row is not None else 0

    def sparse_term_fingerprint(self) -> str:
        rows = self._connection.execute("SELECT term FROM sparse_terms ORDER BY term")
        return _fingerprint_values(
            (str(term) for (term,) in rows),
            ordered=True,
        )


class _SparseDictionaryManifest:
    """Disk-backed target sparse term/index bindings for one bounded scan."""

    def __init__(self) -> None:
        descriptor, self._path = tempfile.mkstemp(
            prefix="openviking-qdrant-sparse-",
            suffix=".sqlite3",
        )
        os.close(descriptor)
        self._connection = sqlite3.connect(self._path)
        self._connection.execute("PRAGMA journal_mode=DELETE")
        self._connection.execute(
            """
            CREATE TABLE sparse_dictionary (
                term TEXT PRIMARY KEY,
                sparse_index INTEGER UNIQUE NOT NULL
            )
            """
        )
        self._connection.execute(
            """
            CREATE TABLE sparse_dictionary_points (
                point_id TEXT PRIMARY KEY,
                term TEXT NOT NULL
            )
            """
        )

    def close(self) -> None:
        try:
            self._connection.close()
        finally:
            Path(self._path).unlink(missing_ok=True)

    def __enter__(self) -> "_SparseDictionaryManifest":
        return self

    def __exit__(self, _exc_type: Any, _exc_value: Any, _traceback: Any) -> None:
        self.close()

    def add(self, term: str, index: int, *, point_id: str | None = None) -> None:
        if point_id is not None:
            try:
                self._connection.execute(
                    "INSERT INTO sparse_dictionary_points(point_id, term) VALUES (?, ?)",
                    (point_id, term),
                )
            except sqlite3.IntegrityError as exc:
                raise SparseMigrationError(
                    f"target sparse dictionary contains duplicate point {point_id!r}"
                ) from exc
        existing_index = self.index_for_term(term)
        if existing_index is not None and existing_index != index:
            raise SparseMigrationError(
                "target sparse dictionary maps source term to a different index: "
                f"term={term!r} existing={existing_index} expected={index}"
            )
        existing_term = self.term_for_index(index)
        if existing_term is not None and existing_term != term:
            raise SparseMigrationError(
                "target sparse dictionary collision: "
                f"index={index} existing_term={existing_term!r} source_term={term!r}"
            )
        self._connection.execute(
            "INSERT OR IGNORE INTO sparse_dictionary(term, sparse_index) VALUES (?, ?)",
            (term, index),
        )

    def index_for_term(self, term: str) -> int | None:
        row = self._connection.execute(
            "SELECT sparse_index FROM sparse_dictionary WHERE term = ?",
            (term,),
        ).fetchone()
        return int(row[0]) if row is not None else None

    def term_for_index(self, index: int) -> str | None:
        row = self._connection.execute(
            "SELECT term FROM sparse_dictionary WHERE sparse_index = ?",
            (index,),
        ).fetchone()
        return str(row[0]) if row is not None else None

    def has_term(self, term: str) -> bool:
        return self.index_for_term(term) is not None

    def has_index(self, index: int) -> bool:
        return self.term_for_index(index) is not None

    def has_owner(self, index: int) -> bool:
        owner_id = sparse_owner_point_id(index)
        row = self._connection.execute(
            "SELECT 1 FROM sparse_dictionary_points WHERE point_id = ?",
            (owner_id,),
        ).fetchone()
        return row is not None


class _ScrollOffsets:
    """Disk-backed cycle detection for one Qdrant scroll."""

    def __init__(self) -> None:
        descriptor, self._path = tempfile.mkstemp(
            prefix="openviking-qdrant-scroll-",
            suffix=".sqlite3",
        )
        os.close(descriptor)
        self._connection = sqlite3.connect(self._path)
        self._connection.execute("CREATE TABLE offsets (offset_key TEXT PRIMARY KEY)")

    def __enter__(self) -> "_ScrollOffsets":
        return self

    def __exit__(self, _exc_type: Any, _exc_value: Any, _traceback: Any) -> None:
        try:
            self._connection.close()
        finally:
            Path(self._path).unlink(missing_ok=True)

    def add(self, value: Any) -> bool:
        try:
            self._connection.execute(
                "INSERT INTO offsets(offset_key) VALUES (?)",
                (_cursor_key(value),),
            )
        except sqlite3.IntegrityError:
            return False
        return True


@dataclass
class MigrationPlan:
    """Read-only migration plan returned by :meth:`QdrantMigration.preflight`."""

    source_collection: str
    target_collection: str
    source_metadata_collection: str
    target_metadata_collection: str
    logical_collection: str
    migration_id: str
    migrator_version: str
    source_count: int
    target_count: int
    target_absent: bool
    target_state: str | None
    dense_vector_name: str
    sparse_vector_name: str
    vector_dimension: int
    distance: str
    dense_datatype: str | None
    sparse_enabled: bool
    sparse_modifier: str | None
    sparse_datatype: str | None
    sparse_weight: float
    source_fingerprint: str = ""
    metadata_fingerprint: str = ""
    sparse_map_fingerprint: str = ""
    acl_incomplete_count: int = 0
    sparse_term_count: int = 0
    sparse_term_fingerprint: str = ""
    batch_size: int = 100
    timeout_seconds: float = 10.0

    def to_dict(self) -> dict[str, Any]:
        """Return JSON-safe plan data suitable for CLI output."""

        return asdict(self)


@dataclass(frozen=True)
class MigrationResult:
    """Summary of a completed (or resumed) apply."""

    source_count: int
    migrated_count: int
    skipped_count: int
    target_count: int
    target_collection: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _result(response: Mapping[str, Any]) -> Any:
    value = response.get("result", response)
    return value


def _status(exc: BaseException) -> int | None:
    return getattr(exc, "status", None)


def _canonical_distance(value: Any) -> str:
    normalized = str(value or "cosine").strip().lower()
    mapping = {
        "cosine": "Cosine",
        "ip": "Dot",
        "dot": "Dot",
        "l2": "Euclid",
        "euclid": "Euclid",
    }
    if normalized not in mapping:
        raise MigrationError(f"unsupported Qdrant distance metric: {value!r}")
    return mapping[normalized]


def _optional_layout_value(value: Any, *, field_name: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise MigrationError(f"source {field_name} is invalid")
    return value.strip().lower()


def _path_depth(path: str) -> int:
    return len([part for part in path.split("/") if part])


def _scope_roots(path: str) -> list[str]:
    parts = [part for part in path.split("/") if part]
    return ["/", *("/" + "/".join(parts[:index]) for index in range(1, len(parts) + 1))]


def _normalize_uri(value: Any, *, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise MigrationError(f"{field_name} must be a non-empty OpenViking URI")
    stripped = value.strip()
    if stripped.startswith("viking://"):
        stripped = stripped[len("viking://") :]
    elif not stripped.startswith("/"):
        raise MigrationError(f"{field_name} is not a canonical OpenViking URI: {value!r}")
    normalized = "/" + stripped.lstrip("/")
    return normalized.rstrip("/") or "/"


def _finite_vector(value: Any, *, field_name: str, dimension: int | None = None) -> list[float]:
    if not isinstance(value, list):
        raise MigrationError(f"{field_name} must be a dense vector list")
    if dimension is not None and len(value) != dimension:
        raise MigrationError(
            f"{field_name} dimension mismatch: expected {dimension}, got {len(value)}"
        )
    try:
        if any(isinstance(item, bool) for item in value):
            raise TypeError("boolean vector element")
        vector = [float(item) for item in value]
    except (TypeError, ValueError) as exc:
        raise MigrationError(f"{field_name} contains a non-numeric value") from exc
    if not all(math.isfinite(item) for item in vector):
        raise MigrationError(f"{field_name} contains a non-finite value")
    return vector


def _canonical_float32(value: Any, *, field_name: str) -> float:
    try:
        if isinstance(value, bool):
            raise TypeError("boolean vector element")
        numeric = float(value)
        canonical = struct.unpack("!f", struct.pack("!f", numeric))[0]
    except (OverflowError, TypeError, ValueError, struct.error) as exc:
        raise MigrationError(f"{field_name} contains a value outside float32") from exc
    if not math.isfinite(canonical):
        raise MigrationError(f"{field_name} contains a non-finite value")
    return canonical


def _point_fingerprint(
    *,
    source_point_id: Any,
    transformed: Mapping[str, Any],
) -> str:
    try:
        encoded = json.dumps(
            {
                "source_point_id": [type(source_point_id).__name__, str(source_point_id)],
                "point": transformed,
            },
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise MigrationError(f"source point {source_point_id!r} is not JSON-serializable") from exc
    return hashlib.sha256(encoded).hexdigest()


def _fingerprint_values(values: Iterable[str], *, ordered: bool = False) -> str:
    digest = hashlib.sha256()
    first = True
    iterable = values if ordered else sorted(values)
    for value in iterable:
        if not first:
            digest.update(b"\n")
        digest.update(str(value).encode("utf-8"))
        first = False
    return digest.hexdigest()


def _canonical_uuid_string(value: Any, *, field_name: str) -> str:
    if not isinstance(value, str):
        raise MigrationError(
            f"{field_name} must be a canonical UUID string; deterministic target IDs are required"
        )
    try:
        canonical = str(uuid.UUID(value))
    except (AttributeError, TypeError, ValueError) as exc:
        raise MigrationError(
            f"{field_name} must be a canonical UUID string; deterministic target IDs are required"
        ) from exc
    if canonical != value:
        raise MigrationError(
            f"{field_name} must be a canonical UUID string; deterministic target IDs are required"
        )
    return value


def _canonical_point_content_fingerprint(point: Mapping[str, Any]) -> str:
    """Fingerprint canonical target content without retaining the content."""

    point_id = _canonical_uuid_string(
        point.get("id"),
        field_name="point id",
    )
    payload = point.get("payload")
    if not isinstance(payload, Mapping):
        raise MigrationError(f"point {point_id!r} has an invalid payload")
    vectors = point.get("vector", point.get("vectors"))
    if not isinstance(vectors, Mapping):
        raise MigrationError(f"point {point_id!r} has an invalid vector payload")
    canonical_vectors: dict[str, Any] = {}
    for name, value in vectors.items():
        if not isinstance(name, str):
            raise MigrationError(f"point {point_id!r} has an invalid vector name")
        if isinstance(value, list):
            canonical_vectors[name] = [
                _canonical_float32(
                    item,
                    field_name=f"point {point_id!r} vector {name!r}",
                )
                for item in value
            ]
            continue
        if not isinstance(value, Mapping):
            raise MigrationError(f"point {point_id!r} vector {name!r} is malformed")
        indices = value.get("indices")
        weights = value.get("values")
        if (
            not isinstance(indices, list)
            or not isinstance(weights, list)
            or len(indices) != len(weights)
        ):
            raise MigrationError(f"point {point_id!r} vector {name!r} is malformed")
        canonical_vectors[name] = {
            "indices": [
                _sparse_index(
                    index,
                    field_name=f"point {point_id!r} vector {name!r} index",
                )
                for index in indices
            ],
            "values": [
                _canonical_float32(
                    weight,
                    field_name=f"point {point_id!r} vector {name!r} value",
                )
                for weight in weights
            ],
        }
    try:
        encoded = json.dumps(
            {
                "id": point_id,
                "payload": dict(payload),
                "vectors": canonical_vectors,
            },
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise MigrationError(f"point {point_id!r} content is not JSON-serializable") from exc
    return hashlib.sha256(encoded).hexdigest()


def _metadata_fingerprint(metadata: LegacyMetadata) -> str:
    try:
        encoded = json.dumps(
            {"schema": metadata.schema, "indexes": metadata.indexes},
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise MigrationError("legacy metadata is not JSON-serializable") from exc
    return hashlib.sha256(encoded).hexdigest()


def _sparse_map_fingerprint(sparse_map: Mapping[int, str]) -> str:
    """Fingerprint the authoritative sparse map used for this plan."""

    try:
        encoded = json.dumps(
            {str(index): term for index, term in sorted(sparse_map.items())},
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise MigrationError("sparse map is not JSON-serializable") from exc
    return hashlib.sha256(encoded).hexdigest()


def _validate_legacy_logical_id(value: Any) -> int | str:
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        raise MigrationError("legacy logical ID must be a uint64 integer or non-empty string")
    if isinstance(value, int) and not 0 <= value <= _LEGACY_UINT64_MAX:
        raise MigrationError(f"legacy logical ID integer is outside uint64 range: {value!r}")
    if isinstance(value, str) and not value:
        raise MigrationError("legacy logical ID must not be empty")
    return value


def _legacy_qdrant_point_id(value: Any) -> Any:
    value = _validate_legacy_logical_id(value)
    if isinstance(value, int):
        return value
    value_string = str(value)
    try:
        return str(uuid.UUID(value_string))
    except (AttributeError, TypeError, ValueError):
        return str(uuid.uuid5(_QDRANT_ID_NAMESPACE, value_string))


def _acl_complete(payload: Mapping[str, Any]) -> bool:
    if not all(field in payload for field in _ACL_FIELDS):
        return False
    try:
        mode = AclMode(payload[ACL_MODE_FIELD])
    except (TypeError, ValueError):
        return False
    if not isinstance(payload["acl_direct_grants"], list) or not isinstance(
        payload["acl_inherited_grants"], list
    ):
        return False
    if mode == AclMode.NONE and (payload["acl_direct_grants"] or payload["acl_inherited_grants"]):
        return False
    try:
        DirectAcl.from_context_fields(payload, "acl_direct")
        DirectAcl.from_context_fields(payload, "acl_inherited")
    except (RuntimeError, TypeError, ValueError):
        return False
    return True


def _typed_equal(left: Any, right: Any) -> bool:
    """Compare payload JSON values without treating booleans as integers."""

    if isinstance(left, bool) or isinstance(right, bool):
        return type(left) is type(right) and left == right
    if isinstance(left, Mapping) or isinstance(right, Mapping):
        if not isinstance(left, Mapping) or not isinstance(right, Mapping):
            return False
        if set(left) != set(right):
            return False
        return all(_typed_equal(left[key], right[key]) for key in left)
    if isinstance(left, list) or isinstance(right, list):
        if not isinstance(left, list) or not isinstance(right, list):
            return False
        return len(left) == len(right) and all(
            _typed_equal(left_item, right_item)
            for left_item, right_item in zip(left, right, strict=True)
        )
    return left == right


def _normalize_owner_user_id(
    payload: dict[str, Any],
    *,
    uri: str,
    point_id: Any,
    source_keys: set[str],
) -> None:
    expected_owner = owner_fields_for_uri(f"viking://{uri.lstrip('/')}").get("owner_user_id")
    actual_owner = payload.get("owner_user_id")
    if actual_owner is None:
        if expected_owner is None:
            payload.pop("owner_user_id", None)
            source_keys.discard("owner_user_id")
        else:
            payload["owner_user_id"] = expected_owner
        return
    if not isinstance(actual_owner, str) or not actual_owner.strip():
        raise MigrationError(f"point {point_id!r} has an invalid owner_user_id")
    if expected_owner is not None and actual_owner != expected_owner:
        raise MigrationError(f"point {point_id!r} owner_user_id does not match uri {uri!r}")


def _assert_security_payload(
    payload: Mapping[str, Any],
    expected_payload: Mapping[str, Any],
    *,
    point_id: str,
) -> None:
    for field_name in _SECURITY_PAYLOAD_FIELDS:
        expected_present = field_name in expected_payload
        actual_present = field_name in payload
        if expected_present != actual_present or (
            expected_present and not _typed_equal(payload[field_name], expected_payload[field_name])
        ):
            label = (
                "ACL field"
                if field_name in _ACL_FIELDS or field_name == "acl_enabled"
                else "security field"
            )
            raise MigrationError(f"target point {point_id!r} {label} {field_name!r} differs")


def _sparse_index(
    value: Any,
    *,
    field_name: str,
    allow_numeric_string: bool = False,
    minimum: int = 0,
) -> int:
    if isinstance(value, bool):
        raise SparseMigrationError(f"{field_name} must be an integer")
    if isinstance(value, int):
        index = value
    elif allow_numeric_string and isinstance(value, str) and _INTEGER_RE.fullmatch(value.strip()):
        index = int(value)
    else:
        raise SparseMigrationError(f"{field_name} must be an integer")
    if not minimum <= index <= _QDRANT_SPARSE_INDEX_MAX:
        raise SparseMigrationError(
            f"{field_name} must be between {minimum} and {_QDRANT_SPARSE_INDEX_MAX}"
        )
    return index


def _sparse_term(value: Any, *, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise SparseMigrationError(f"{field_name} must be a non-empty string")
    return value


def _cursor_key(value: int | str | None) -> str:
    """Serialize an opaque Qdrant cursor without changing its JSON type."""

    return json.dumps(
        [type(value).__name__, value],
        separators=(",", ":"),
        ensure_ascii=False,
    )


def _validate_cursor(
    value: Any,
    *,
    field_name: str = "Qdrant page offset",
) -> int | str | None:
    """Validate Qdrant's unsigned-integer/UUID page cursor."""

    if value is None:
        return None
    if isinstance(value, bool):
        raise MigrationError(f"{field_name} must be an unsigned integer or UUID string")
    if isinstance(value, int):
        if value < 0 or value > _LEGACY_UINT64_MAX:
            raise MigrationError(f"{field_name} must be between 0 and {_LEGACY_UINT64_MAX}")
        return value
    if isinstance(value, str):
        if not value or not value.strip():
            raise MigrationError(f"{field_name} must be a non-empty UUID string")
        try:
            uuid.UUID(value)
        except ValueError as exc:
            raise MigrationError(f"{field_name} is not a valid UUID string") from exc
        return value
    raise MigrationError(f"{field_name} must be an unsigned integer or UUID string")


def _legacy_sparse_map(value: Mapping[Any, Any] | None) -> dict[int, str]:
    """Normalize either ``old-index -> term`` or ``term -> old-index`` JSON."""

    if value is None:
        return {}
    normalized: dict[int, str] = {}
    terms_to_indexes: dict[str, int] = {}
    for raw_key, raw_value in value.items():
        # A JSON object turns integer keys into strings.  Prefer the reverse
        # form when the value is an integer so {"123": 111} remains a
        # numeric-looking term instead of being parsed as old-index -> term.
        if isinstance(raw_key, int) and not isinstance(raw_key, bool):
            raw_index, raw_term = raw_key, raw_value
        elif isinstance(raw_value, int) and not isinstance(raw_value, bool):
            raw_index, raw_term = raw_value, raw_key
        elif isinstance(raw_key, str) and _INTEGER_RE.fullmatch(raw_key.strip()):
            raw_index, raw_term = raw_key, raw_value
        else:
            raise SparseMigrationError(
                "sparse map entries must be {old_index: term} or {term: old_index}"
            )
        old_index = _sparse_index(
            raw_index,
            field_name="sparse map index",
            allow_numeric_string=isinstance(raw_index, str),
        )
        term = _sparse_term(raw_term, field_name="sparse map term")
        if old_index in normalized and normalized[old_index] != term:
            raise SparseMigrationError(
                f"sparse map contains conflicting terms for old index {old_index}"
            )
        previous_index = terms_to_indexes.get(term)
        if previous_index is not None and previous_index != old_index:
            raise SparseMigrationError(f"sparse map maps term {term!r} to multiple old indexes")
        normalized[old_index] = term
        terms_to_indexes[term] = old_index
    return normalized


def _schema_fields(schema: Mapping[str, Any], *, label: str) -> dict[str, Mapping[str, Any]]:
    fields = schema.get("Fields", [])
    if fields is None:
        return {}
    if not isinstance(fields, list):
        raise MigrationError(f"{label} schema Fields must be a list")
    result: dict[str, Mapping[str, Any]] = {}
    for field_item in fields:
        if not isinstance(field_item, Mapping) or not field_item.get("FieldName"):
            raise MigrationError(f"{label} schema has a malformed field")
        name = str(field_item["FieldName"])
        if name in result:
            raise MigrationError(f"{label} schema has duplicate field {name!r}")
        result[name] = field_item
    return result


def _validate_legacy_schema(schema: Mapping[str, Any]) -> None:
    if not isinstance(schema, Mapping):
        raise MigrationError("legacy collection metadata schema must be an object")
    _schema_fields(schema, label="legacy")
    collection_name = schema.get("CollectionName")
    if collection_name is not None and (
        not isinstance(collection_name, str) or not collection_name.strip()
    ):
        raise MigrationError("legacy collection metadata has an invalid CollectionName")
    scalar_index = schema.get("ScalarIndex")
    if scalar_index is not None and not isinstance(scalar_index, (list, tuple, set)):
        raise MigrationError("legacy collection metadata ScalarIndex must be a list")
    if isinstance(scalar_index, (list, tuple, set)) and any(
        not isinstance(field, str) or not field.strip() for field in scalar_index
    ):
        raise MigrationError("legacy collection metadata ScalarIndex has an invalid field")


def _validate_scalar_index(value: Any, *, label: str) -> None:
    """Validate the ScalarIndex shapes understood by the adapter."""

    if value is None:
        return
    if isinstance(value, Mapping):
        fields = value.keys()
    elif isinstance(value, (list, tuple, set)):
        fields = value
    else:
        raise MigrationError(f"{label} ScalarIndex is malformed")
    if any(not isinstance(field, str) or not field.strip() for field in fields):
        raise MigrationError(f"{label} ScalarIndex has an invalid field")


def _validate_schema_subset(
    source: Mapping[str, Any],
    target: Mapping[str, Any],
) -> None:
    """Require source fields/indexes to remain compatible with a newer target."""

    source_collection = source.get("CollectionName")
    if source_collection is not None and target.get("CollectionName") != source_collection:
        raise MigrationError("target current marker schema changed CollectionName")

    source_fields = _schema_fields(source, label="source")
    target_fields = _schema_fields(target, label="target")
    for name, source_field in source_fields.items():
        target_field = target_fields.get(name)
        if target_field is None:
            raise MigrationError(f"target current marker schema is missing field {name!r}")
        source_type = str(source_field.get("FieldType") or "").strip().lower()
        target_type = str(target_field.get("FieldType") or "").strip().lower()
        if source_type != target_type:
            raise MigrationError(
                f"target current marker schema changed field type for {name!r}: "
                f"source={source_type!r} target={target_type!r}"
            )
        for key in ("Dim", "IsPrimaryKey"):
            if key in source_field and target_field.get(key) != source_field[key]:
                raise MigrationError(f"target current marker schema changed field {name!r} {key}")

    source_scalar = source.get("ScalarIndex")
    target_scalar = target.get("ScalarIndex")
    if isinstance(source_scalar, (list, tuple, set)):
        if not isinstance(target_scalar, (list, tuple, set)):
            raise MigrationError("target current marker schema has no ScalarIndex list")
        missing = set(map(str, source_scalar)) - set(map(str, target_scalar))
        if missing:
            raise MigrationError(
                f"target current marker schema is missing scalar indexes: {sorted(missing)!r}"
            )


class DeploymentHooks:
    """Run the reviewed, operator-owned lifecycle commands."""

    def __init__(self, commands: Mapping[str, Any]) -> None:
        if not isinstance(commands, Mapping):
            raise MigrationError("deployment hooks JSON must be an object")
        keys = set(commands)
        expected = set(_DEPLOYMENT_HOOK_NAMES)
        if keys != expected:
            missing = sorted(expected - keys)
            extra = sorted(keys - expected)
            raise MigrationError(
                f"deployment hook keys differ: missing={missing!r} extra={extra!r}"
            )
        self._commands: dict[str, list[str]] = {}
        for name in _DEPLOYMENT_HOOK_NAMES:
            command = commands[name]
            if (
                not isinstance(command, list)
                or not command
                or any(
                    not isinstance(argument, str) or not argument.strip() or "\x00" in argument
                    for argument in command
                )
            ):
                raise MigrationError(f"deployment hook {name!r} must be a non-empty argv list")
            self._commands[name] = list(command)

    @classmethod
    def from_path(cls, path: str) -> "DeploymentHooks":
        try:
            value = json.loads(Path(path).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise MigrationError(f"cannot read deployment hooks {path}: {exc}") from exc
        return cls(value)

    def _run(self, name: str, migration: "QdrantMigration") -> str:
        timeout_seconds = _validate_timeout_seconds(migration.timeout_seconds)
        environment = os.environ.copy()
        environment.update(
            {
                "OV_LOGICAL_COLLECTION": migration.logical_collection,
                "OV_MIGRATION_ID": migration.migration_id,
                "OV_SOURCE_COLLECTION": migration.source_collection,
                "OV_SOURCE_METADATA_COLLECTION": migration.source_metadata_collection,
                "OV_TARGET_COLLECTION": migration.target_collection,
                "OV_TARGET_METADATA_COLLECTION": migration.target_metadata_collection,
                "OV_TIMEOUT_SECONDS": str(timeout_seconds),
                "OV_MIGRATOR_VERSION": migration.migrator_version,
            }
        )
        try:
            completed = subprocess.run(
                self._commands[name],
                check=True,
                shell=False,
                capture_output=True,
                text=True,
                timeout=timeout_seconds,
                env=environment,
            )
        except subprocess.TimeoutExpired as exc:
            raise MigrationError(f"deployment hook {name!r} timed out") from exc
        except (OSError, subprocess.CalledProcessError) as exc:
            raise MigrationError(f"deployment hook {name!r} failed") from exc
        stdout = completed.stdout
        return stdout if isinstance(stdout, str) else ""

    def _assert_success(self, name: str, migration: "QdrantMigration") -> None:
        self._run(name, migration)

    def drain_legacy_writes(self, migration: "QdrantMigration") -> None:
        self._assert_success("drain_legacy_writes", migration)

    def remove_legacy_from_serving_path(self, migration: "QdrantMigration") -> None:
        self._assert_success("remove_legacy_from_serving_path", migration)

    def rollout_current(self, migration: "QdrantMigration") -> None:
        self._assert_success("rollout_current", migration)

    def wait_current_ready(self, migration: "QdrantMigration") -> None:
        self._assert_success("wait_current_ready", migration)

    def smoke_current_read_only(self, migration: "QdrantMigration") -> None:
        self._assert_success("smoke_current_read_only", migration)

    def remove_current_from_serving_path(self, migration: "QdrantMigration") -> None:
        self._assert_success("remove_current_from_serving_path", migration)

    def restore_legacy(self, migration: "QdrantMigration") -> None:
        self._assert_success("restore_legacy", migration)

    def verify_legacy_read_path(self, migration: "QdrantMigration") -> None:
        self._assert_success("verify_legacy_read_path", migration)

    def current_target_has_accepted_writes(
        self,
        migration: "QdrantMigration",
    ) -> bool:
        value = self._run("current_target_has_accepted_writes", migration).strip()
        if value not in {"true", "false"}:
            raise MigrationError(
                "deployment hook 'current_target_has_accepted_writes' must print only true or false"
            )
        return value == "true"

    def assert_target_not_served(self, migration: "QdrantMigration") -> None:
        self._assert_success("assert_target_not_served", migration)


class QdrantMigration:
    """Copy a legacy Qdrant collection into the current OpenViking format."""

    def __init__(
        self,
        *,
        client: Any,
        source_collection: str,
        target_collection: str,
        source_metadata_collection: str | None = None,
        target_metadata_collection: str | None = None,
        batch_size: int = 100,
        dense_vector_name: str | None = None,
        sparse_vector_name: str | None = None,
        sparse_map: Mapping[Any, Any] | None = None,
        logical_collection: str,
        migration_id: str,
        timeout_seconds: float = 10.0,
        migrator_version: str = MIGRATOR_VERSION,
    ) -> None:
        self._client = client
        self.timeout_seconds = _validate_timeout_seconds(timeout_seconds)
        client_timeout = getattr(client, "timeout_seconds", None)
        if client_timeout is not None and not math.isclose(
            _validate_timeout_seconds(client_timeout),
            self.timeout_seconds,
            rel_tol=0.0,
            abs_tol=1e-9,
        ):
            raise ValueError("Qdrant client timeout_seconds must match migration timeout_seconds")
        self.source_collection = self._name(source_collection, "source collection")
        self.target_collection = self._name(target_collection, "target collection")
        if self.source_collection == self.target_collection:
            raise ValueError("source and target collections must differ")
        # pre-#3872 QdrantMetaStore used one global sidecar by default.
        self.source_metadata_collection = source_metadata_collection or "__openviking_meta"
        self.target_metadata_collection = target_metadata_collection or (
            f"{self.target_collection}__openviking_meta"
        )
        self.source_metadata_collection = self._name(
            self.source_metadata_collection,
            "source metadata collection",
        )
        self.target_metadata_collection = self._name(
            self.target_metadata_collection,
            "target metadata collection",
        )
        collection_names = {
            "source collection": self.source_collection,
            "target collection": self.target_collection,
            "source metadata collection": self.source_metadata_collection,
            "target metadata collection": self.target_metadata_collection,
        }
        seen_names: dict[str, str] = {}
        for description, name in collection_names.items():
            previous = seen_names.get(name)
            if previous is not None:
                raise ValueError(
                    f"{description} must differ from {previous}; "
                    "all migration collections must be pairwise distinct"
                )
            seen_names[name] = description
        self.batch_size = int(batch_size)
        if self.batch_size <= 0:
            raise ValueError("batch_size must be positive")
        self._dense_vector_name_override = dense_vector_name
        self._sparse_vector_name_override = sparse_vector_name
        self._sparse_map = _legacy_sparse_map(sparse_map)
        self.logical_collection = self._name(logical_collection, "logical collection")
        self.migration_id = self._name(migration_id, "migration ID")
        if migrator_version != MIGRATOR_VERSION:
            raise ValueError(
                f"migrator_version must be {MIGRATOR_VERSION!r}; got {migrator_version!r}"
            )
        self.migrator_version = MIGRATOR_VERSION

    @staticmethod
    def _name(value: str, description: str) -> str:
        normalized = str(value or "").strip()
        if not normalized:
            raise ValueError(f"{description} must not be empty")
        return normalized

    def _path(self, collection: str, suffix: str = "") -> str:
        return f"/collections/{quote(collection, safe='')}{suffix}"

    def _timeout_param(self) -> int:
        return max(1, math.ceil(self.timeout_seconds))

    def _request(
        self,
        method: str,
        path: str,
        body: dict[str, Any] | None = None,
        *,
        params: dict[str, Any] | None = None,
        mutation: bool = False,
    ) -> dict[str, Any]:
        method = method.upper()
        request_params = dict(params or {})
        segments = [segment for segment in urlsplit(path).path.rstrip("/").split("/") if segment]
        is_collection_endpoint = len(segments) == 2 and segments[0] == "collections"
        is_point_upsert_endpoint = (
            len(segments) == 3 and segments[0] == "collections" and segments[2] == "points"
        )
        is_point_delete_endpoint = (
            len(segments) == 4
            and segments[0] == "collections"
            and segments[2:4] == ["points", "delete"]
        )
        is_index_endpoint = (
            len(segments) in {3, 4} and segments[0] == "collections" and segments[2] == "index"
        )
        requires_completed = False
        requires_true_result = False
        if mutation:
            if (method == "PUT" and is_point_upsert_endpoint) or (
                method == "POST" and is_point_delete_endpoint
            ):
                request_params.update({"wait": "true", "ordering": "strong"})
                requires_completed = True
            elif is_index_endpoint:
                request_params.update({"wait": "true", "timeout": self._timeout_param()})
                requires_completed = True
            elif is_collection_endpoint:
                request_params.pop("wait", None)
                request_params.pop("ordering", None)
                request_params["timeout"] = self._timeout_param()
                requires_true_result = True
        response = self._client.request(method, path, body, params=request_params or None)
        if not isinstance(response, dict):
            raise MigrationError(f"Qdrant returned a non-object response for {method} {path}")
        if requires_completed:
            result = response.get("result")
            if not isinstance(result, Mapping) or result.get("status") != "completed":
                raise MigrationError(f"Qdrant {method} {path} did not complete")
        if requires_true_result and response.get("result") is not True:
            raise MigrationError(f"Qdrant {method} {path} did not complete")
        return response

    def _assert_strong_ordering_support(self) -> None:
        response = self._request("GET", "/")
        value = _result(response)
        version = value.get("version") if isinstance(value, Mapping) else None
        try:
            validate_qdrant_version(version)
        except QdrantError as exc:
            raise MigrationError(str(exc)) from exc

    def _wait_collection_ready(
        self,
        collection: str,
        *,
        payload_fields: set[str] | None = None,
    ) -> None:
        deadline = time.monotonic() + self.timeout_seconds
        while True:
            info = self._collection_info(collection)
            status = info.get("status")
            optimizer_status = info.get("optimizer_status")
            if not isinstance(status, str) or not isinstance(optimizer_status, (str, Mapping)):
                raise MigrationError(
                    f"Qdrant collection readiness response is malformed for {collection}"
                )
            normalized_status = status.strip().lower()
            if normalized_status in {"red", "error", "failed"}:
                raise MigrationError(f"Qdrant collection {collection} is not ready: {status}")
            if isinstance(optimizer_status, Mapping):
                raise MigrationError(f"Qdrant collection {collection} optimizer reported an error")
            normalized_optimizer = optimizer_status.strip().lower()
            if normalized_optimizer in {"red", "error", "failed"}:
                raise MigrationError(
                    f"Qdrant collection {collection} optimizer is not ready: {optimizer_status}"
                )
            pending = any(
                pending_work(info[name])
                for name in ("update_queue", "deferred")
                if name in info
            )
            payload_schema = info.get("payload_schema")
            indexes_visible = payload_fields is None or (
                isinstance(payload_schema, Mapping) and payload_fields <= set(payload_schema)
            )
            if (
                normalized_status == "green"
                and normalized_optimizer == "ok"
                and not pending
                and indexes_visible
            ):
                return
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                if payload_fields is not None:
                    visible = set(payload_schema) if isinstance(payload_schema, Mapping) else set()
                    missing = sorted(payload_fields - visible)
                    if missing:
                        raise MigrationError(
                            f"target collection is missing payload index: {missing!r}"
                        )
                raise MigrationError(f"Qdrant collection {collection} did not become ready")
            time.sleep(min(0.1, remaining))

    def _exists(self, collection: str) -> bool:
        try:
            self._request("GET", self._path(collection))
        except Exception as exc:
            if _status(exc) == 404:
                return False
            raise
        return True

    def _collection_info(self, collection: str) -> dict[str, Any]:
        try:
            response = self._request("GET", self._path(collection))
        except Exception as exc:
            if _status(exc) == 404:
                raise MigrationError(f"Qdrant collection does not exist: {collection}") from exc
            raise
        value = _result(response)
        if not isinstance(value, dict):
            raise MigrationError(f"invalid Qdrant collection response for {collection}")
        return value

    @staticmethod
    def _params(info: Mapping[str, Any]) -> Mapping[str, Any]:
        config = info.get("config")
        if isinstance(config, Mapping):
            params = config.get("params")
            if isinstance(params, Mapping):
                return params
        params = info.get("params")
        if isinstance(params, Mapping):
            return params
        return info

    def _layout(
        self,
        info: Mapping[str, Any],
        *,
        honor_overrides: bool = True,
    ) -> CollectionLayout:
        params = self._params(info)
        vectors = params.get("vectors")
        if not isinstance(vectors, Mapping):
            raise MigrationError("Qdrant collection has no dense vector configuration")

        dense_name: str | None = None
        dense_config: Mapping[str, Any] | None = None
        dense_override = self._dense_vector_name_override if honor_overrides else None
        if "size" in vectors:
            if dense_override:
                raise MigrationError(
                    "cannot rename an unnamed source dense vector; remove --dense-vector-name"
                )
            dense_name = dense_override or "vector"
            dense_config = vectors
        else:
            named = [
                (str(name), value)
                for name, value in vectors.items()
                if isinstance(value, Mapping) and "size" in value
            ]
            if dense_override:
                selected = next(
                    ((name, value) for name, value in named if name == dense_override),
                    None,
                )
                if selected is None:
                    raise MigrationError(
                        f"configured dense vector {dense_override!r} "
                        "is absent from the source collection"
                    )
                dense_name, dense_config = selected
            elif len(named) == 1:
                dense_name, dense_config = named[0]
            else:
                raise MigrationError(
                    "source collection has multiple named dense vectors; "
                    "pass --dense-vector-name to select one"
                )

        assert dense_name is not None and dense_config is not None
        raw_dimension = dense_config.get("size")
        if type(raw_dimension) is not int or raw_dimension <= 0:
            raise MigrationError("source dense vector size must be a positive integer")
        dimension = raw_dimension

        sparse_vectors = params.get("sparse_vectors")
        if sparse_vectors is None:
            sparse_vectors = info.get("sparse_vectors")
        sparse_enabled = isinstance(sparse_vectors, Mapping) and bool(sparse_vectors)
        sparse_name: str | None = None
        sparse_override = self._sparse_vector_name_override if honor_overrides else None
        if isinstance(sparse_vectors, Mapping):
            names = [str(name) for name in sparse_vectors]
            if sparse_override:
                if sparse_override not in names:
                    raise MigrationError(
                        f"configured sparse vector {sparse_override!r} "
                        "is absent from the source collection"
                    )
                sparse_name = sparse_override
            elif len(names) == 1:
                sparse_name = names[0]
            elif names:
                raise SparseMigrationError(
                    "source collection has multiple named sparse vectors; "
                    "pass --sparse-vector-name to select one"
                )
        sparse_name = sparse_name or sparse_override or "sparse_vector"

        distance = _canonical_distance(dense_config.get("distance"))
        dense_datatype = _optional_layout_value(
            dense_config.get("datatype"),
            field_name="dense vector datatype",
        )
        sparse_modifier: str | None = None
        sparse_datatype: str | None = None
        if isinstance(sparse_vectors, Mapping) and sparse_name in sparse_vectors:
            sparse_config = sparse_vectors[sparse_name]
            if sparse_config is not None and not isinstance(sparse_config, Mapping):
                raise MigrationError("source sparse vector configuration is invalid")
            if isinstance(sparse_config, Mapping):
                sparse_modifier = _optional_layout_value(
                    sparse_config.get("modifier"),
                    field_name="sparse vector modifier",
                )
                sparse_index = sparse_config.get("index")
                if sparse_index is not None and not isinstance(sparse_index, Mapping):
                    raise MigrationError("source sparse vector index configuration is invalid")
                if isinstance(sparse_index, Mapping):
                    sparse_datatype = _optional_layout_value(
                        sparse_index.get("datatype"),
                        field_name="sparse vector index datatype",
                    )
        return CollectionLayout(
            dense_vector_name=dense_name,
            sparse_vector_name=sparse_name,
            vector_dimension=dimension,
            distance=distance,
            dense_datatype=dense_datatype,
            sparse_enabled=sparse_enabled,
            sparse_modifier=sparse_modifier,
            sparse_datatype=sparse_datatype,
        )

    def _count(
        self,
        collection: str,
        *,
        filter: Mapping[str, Any] | None = None,
    ) -> int:
        response = self._request(
            "POST",
            self._path(collection, "/points/count"),
            {"exact": True, "filter": dict(filter or {})},
            params={"consistency": "all"},
        )
        value = _result(response)
        if not isinstance(value, Mapping):
            raise MigrationError(f"invalid Qdrant count response for {collection}")
        count = value.get("count")
        if type(count) is not int or count < 0:
            raise MigrationError(f"invalid Qdrant count for {collection}")
        return count

    def _scroll(
        self,
        collection: str,
        *,
        with_vectors: bool,
        filter: Mapping[str, Any] | None = None,
    ) -> Iterable[dict[str, Any]]:
        offset: int | str | None = None
        with _ScrollOffsets() as offsets:
            offsets.add(offset)
            while True:
                page, next_offset = self._scroll_page(
                    collection,
                    offset=offset,
                    with_vectors=with_vectors,
                    filter=filter,
                )
                for point in page:
                    yield point
                if not page:
                    return
                if next_offset is None:
                    return
                if not offsets.add(next_offset):
                    raise MigrationError(f"Qdrant scroll repeated its page offset for {collection}")
                offset = next_offset

    def _scroll_page(
        self,
        collection: str,
        *,
        offset: int | str | None,
        with_vectors: bool,
        filter: Mapping[str, Any] | None = None,
    ) -> tuple[list[dict[str, Any]], int | str | None]:
        """Read one bounded Qdrant page and preserve its opaque cursor."""

        offset = _validate_cursor(offset, field_name="Qdrant scroll offset")
        body: dict[str, Any] = {
            "limit": self.batch_size,
            "with_payload": True,
            "with_vector": with_vectors,
        }
        if filter:
            body["filter"] = dict(filter)
        if offset is not None:
            body["offset"] = offset
        response = self._request(
            "POST",
            self._path(collection, "/points/scroll"),
            body,
            params={"consistency": "all"},
        )
        value = _result(response)
        if not isinstance(value, Mapping):
            raise MigrationError(f"invalid Qdrant scroll response for {collection}")
        raw_page = value.get("points")
        if not isinstance(raw_page, list):
            raise MigrationError(f"invalid Qdrant scroll points for {collection}")
        if any(not isinstance(point, dict) for point in raw_page):
            raise MigrationError(f"invalid Qdrant scroll point for {collection}")
        page = [point for point in raw_page if isinstance(point, dict)]
        next_offset = _validate_cursor(
            value.get("next_page_offset"),
            field_name="Qdrant next_page_offset",
        )
        if next_offset is not None and _cursor_key(next_offset) == _cursor_key(offset):
            raise MigrationError(f"Qdrant scroll repeated its page offset for {collection}")
        if not page and next_offset is not None:
            raise MigrationError(
                f"Qdrant scroll returned an empty page with a next offset for {collection}"
            )
        return page, next_offset

    def _retrieve(
        self,
        collection: str,
        point_ids: list[str],
        *,
        with_vectors: bool,
    ) -> list[dict[str, Any]]:
        if not point_ids:
            return []
        response = self._request(
            "POST",
            self._path(collection, "/points"),
            {
                "ids": point_ids,
                "with_payload": True,
                "with_vector": with_vectors,
            },
            params={"consistency": "all"},
        )
        value = _result(response)
        if not isinstance(value, list):
            raise MigrationError(f"invalid Qdrant point lookup response for {collection}")
        if any(not isinstance(point, dict) for point in value):
            raise MigrationError(f"invalid Qdrant point lookup point for {collection}")
        return value

    def _legacy_metadata(self) -> LegacyMetadata:
        if not self._exists(self.source_metadata_collection):
            raise MigrationError(
                f"legacy metadata collection does not exist: {self.source_metadata_collection}"
            )
        metadata_filter = {
            "must": [
                {
                    "key": "collection_key",
                    "match": {"value": self.source_collection},
                }
            ]
        }
        collection_docs: list[dict[str, Any]] = []
        index_docs: list[dict[str, Any]] = []
        metadata_scanned = 0
        metadata_count = self._count(
            self.source_metadata_collection,
            filter=metadata_filter,
        )
        for point in self._scroll(
            self.source_metadata_collection,
            with_vectors=False,
            filter=metadata_filter,
        ):
            metadata_scanned += 1
            point_id = point.get("id")
            payload = point.get("payload")
            if not isinstance(payload, Mapping):
                continue
            kind = payload.get("kind")
            if kind == "collection":
                expected_point_id = _legacy_collection_metadata_id(self.source_collection)
                if point_id is None or str(point_id) != expected_point_id:
                    raise MigrationError(
                        "legacy collection metadata point-id does not match "
                        f"deterministic encoding: expected={expected_point_id!r} "
                        f"found={point_id!r}"
                    )
                collection_docs.append(dict(payload))
            elif kind == "index":
                index_name = payload.get("index_name")
                if not isinstance(index_name, str) or not index_name.strip():
                    raise MigrationError("legacy index metadata has an empty index name")
                expected_point_id = _legacy_index_metadata_id(
                    self.source_collection,
                    index_name,
                )
                if point_id is None or str(point_id) != expected_point_id:
                    raise MigrationError(
                        f"legacy index metadata point-id does not match deterministic "
                        f"encoding for {index_name!r}: expected={expected_point_id!r} "
                        f"found={point_id!r}"
                    )
                index_docs.append(dict(payload))
            else:
                raise MigrationError(
                    f"legacy metadata has an unknown kind for {self.source_collection!r}: {kind!r}"
                )
        if metadata_scanned != metadata_count:
            raise MigrationError(
                "legacy metadata count mismatch: "
                f"exact count={metadata_count} paginated count={metadata_scanned}"
            )
        if len(collection_docs) != 1:
            raise MigrationError(
                f"expected one legacy collection metadata document for {self.source_collection}, "
                f"found {len(collection_docs)}"
            )
        collection_doc = collection_docs[0]
        schema = collection_doc.get("meta")
        if not isinstance(schema, dict):
            raise MigrationError("legacy collection metadata has no schema object")
        _validate_legacy_schema(schema)
        indexes: dict[str, dict[str, Any]] = {}
        for document in index_docs:
            name = document.get("index_name")
            meta = document.get("meta")
            if not isinstance(name, str) or not isinstance(meta, dict):
                raise MigrationError("legacy index metadata is malformed")
            if not name.strip():
                raise MigrationError("legacy index metadata has an empty index name")
            _validate_scalar_index(
                meta.get("ScalarIndex"),
                label=f"legacy index metadata for {name!r}",
            )
            if isinstance(meta.get("VectorIndex"), (list, tuple, set)) or (
                "VectorIndex" in meta and not isinstance(meta.get("VectorIndex"), Mapping)
            ):
                raise MigrationError(f"legacy index metadata VectorIndex is malformed for {name!r}")
            if name in indexes:
                raise MigrationError(f"duplicate legacy index metadata: {name}")
            indexes[name] = dict(meta)
        if not indexes:
            raise MigrationError(
                f"legacy metadata has no index documents for {self.source_collection}"
            )
        return LegacyMetadata(
            schema=dict(schema),
            indexes=indexes,
        )

    @staticmethod
    def _payload(point: Mapping[str, Any]) -> dict[str, Any]:
        value = point.get("payload")
        if not isinstance(value, Mapping):
            raise MigrationError(f"Qdrant point {point.get('id')!r} has no payload")
        return dict(value)

    def _source_vectors(
        self,
        point: Mapping[str, Any],
        layout: CollectionLayout,
    ) -> tuple[list[float] | None, dict[str, list[Any]] | None]:
        vectors = point.get("vector")
        if vectors is None:
            vectors = point.get("vectors")
        if isinstance(vectors, list):
            dense_value: Any = vectors
            sparse_value = None
        elif isinstance(vectors, Mapping):
            dense_value = vectors.get(layout.dense_vector_name)
            if dense_value is None and layout.dense_vector_name == "vector":
                # Qdrant represents an unnamed dense vector alongside named
                # sparse vectors as {"": [...], "sparse_name": {...}}.
                dense_value = vectors.get("")
            sparse_value = vectors.get(layout.sparse_vector_name)
            if sparse_value is None:
                candidates = [
                    value
                    for name, value in vectors.items()
                    if name != layout.dense_vector_name
                    and isinstance(value, Mapping)
                    and "indices" in value
                    and "values" in value
                ]
                if self._sparse_vector_name_override:
                    if candidates:
                        raise SparseMigrationError(
                            f"point {point.get('id')!r} is missing configured sparse vector "
                            f"{layout.sparse_vector_name!r}"
                        )
                elif len(candidates) == 1:
                    sparse_value = candidates[0]
                elif len(candidates) > 1:
                    raise SparseMigrationError(
                        f"point {point.get('id')!r} has multiple sparse vectors"
                    )
        else:
            raise MigrationError(f"Qdrant point {point.get('id')!r} has no vector payload")
        dense = (
            _finite_vector(
                dense_value,
                field_name=f"point {point.get('id')!r} dense vector",
                dimension=layout.vector_dimension,
            )
            if dense_value is not None
            else None
        )
        if sparse_value is None:
            if dense is None:
                raise MigrationError(
                    f"point {point.get('id')!r} has neither a dense nor sparse vector"
                )
            return dense, None
        if not isinstance(sparse_value, Mapping):
            raise SparseMigrationError(f"point {point.get('id')!r} sparse vector is malformed")
        indices = sparse_value.get("indices")
        values = sparse_value.get("values")
        if (
            not isinstance(indices, list)
            or not isinstance(values, list)
            or len(indices) != len(values)
        ):
            raise SparseMigrationError(
                f"point {point.get('id')!r} sparse indices/values are not parallel lists"
            )
        if not indices:
            raise SparseMigrationError(f"point {point.get('id')!r} sparse vector has no entries")
        return dense, {"indices": list(indices), "values": list(values)}

    def _transform_point(
        self,
        point: Mapping[str, Any],
        *,
        layout: CollectionLayout,
        schema: Mapping[str, Any],
    ) -> tuple[str, dict[str, Any], set[str]]:
        payload = self._payload(point)
        original_id = payload.get(_ORIGINAL_ID_FIELD)
        if original_id is None or str(original_id) == "":
            raise MigrationError(
                f"point {point.get('id')!r} is missing {_ORIGINAL_ID_FIELD} (original id)"
            )
        _validate_legacy_logical_id(original_id)
        logical_id = str(original_id)
        source_keys = set(payload)
        payload[_ORIGINAL_ID_FIELD] = logical_id

        if "uri" not in payload:
            raise MigrationError(f"point {point.get('id')!r} is missing uri")
        payload["uri"] = _normalize_uri(payload["uri"], field_name="uri")
        if "parent_uri" in payload and payload["parent_uri"] is not None:
            payload["parent_uri"] = _normalize_uri(
                payload["parent_uri"],
                field_name="parent_uri",
            )
        _normalize_owner_user_id(
            payload,
            uri=payload["uri"],
            point_id=point.get("id"),
            source_keys=source_keys,
        )
        payload["uri_depth"] = _path_depth(payload["uri"])
        payload["scope_roots"] = _scope_roots(payload["uri"])

        field_names = {
            str(field.get("FieldName"))
            for field in schema.get("Fields", [])
            if isinstance(field, Mapping) and field.get("FieldName")
        }
        if "level" in payload or "level" in field_names:
            level = payload.get("level")
            if isinstance(level, bool) or not isinstance(level, (int, float)):
                raise MigrationError(f"point {point.get('id')!r} has no valid level")
            if isinstance(level, float) and (not math.isfinite(level) or not level.is_integer()):
                raise MigrationError(f"point {point.get('id')!r} has no valid level")
            level_int = int(level)
            if level_int not in (0, 1, 2):
                raise MigrationError(f"point {point.get('id')!r} has unsupported level {level!r}")
            payload["level"] = level_int
        if "context_type" in payload or "context_type" in field_names:
            context_type = payload.get("context_type")
            if not isinstance(context_type, str) or context_type not in _CONTEXT_TYPES:
                raise MigrationError(
                    f"point {point.get('id')!r} has invalid context_type {context_type!r}"
                )
        for identity_field in ("account_id",):
            if identity_field in payload or identity_field in field_names:
                value = payload.get(identity_field)
                if not isinstance(value, str) or not value.strip():
                    raise MigrationError(f"point {point.get('id')!r} is missing {identity_field}")

        covered = source_keys | _INTERNAL_PAYLOAD_FIELDS
        if not covered.issubset(payload):
            missing = sorted(covered - set(payload))
            raise MigrationError(
                f"point {point.get('id')!r} lost payload fields during migration: {missing}"
            )

        dense, sparse = self._source_vectors(point, layout)
        vectors: dict[str, Any] = {}
        if dense is not None:
            vectors[layout.dense_vector_name] = dense
        terms: set[str] = set()
        if sparse is not None:
            if not layout.sparse_enabled:
                raise SparseMigrationError(
                    "source points contain sparse vectors but the collection has no "
                    "sparse-vector configuration"
                )
            if not self._sparse_map:
                raise SparseMigrationError(
                    "source sparse vectors require an authoritative old-index-to-term mapping"
                )
            new_indices: list[int] = []
            new_values: list[float] = []
            by_index: dict[int, float] = {}
            target_terms: dict[int, str] = {}
            for raw_index, raw_value in zip(sparse["indices"], sparse["values"], strict=True):
                old_index = _sparse_index(
                    raw_index,
                    field_name=f"point {point.get('id')!r} sparse index",
                )
                if isinstance(raw_value, bool):
                    raise SparseMigrationError(
                        f"point {point.get('id')!r} contains an invalid sparse entry"
                    )
                try:
                    weight = float(raw_value)
                except (TypeError, ValueError) as exc:
                    raise SparseMigrationError(
                        f"point {point.get('id')!r} contains an invalid sparse entry"
                    ) from exc
                if not math.isfinite(weight):
                    raise SparseMigrationError(
                        f"point {point.get('id')!r} contains a non-finite sparse weight"
                    )
                term = self._sparse_map.get(old_index)
                if term is None:
                    raise SparseMigrationError(
                        "authoritative sparse mapping is missing old index "
                        f"{old_index} for point {point.get('id')!r}"
                    )
                new_index = stable_sparse_index(term)
                previous_term = target_terms.get(new_index)
                if previous_term is not None and previous_term != term:
                    raise SparseMigrationError(
                        "sparse term collision after migration: "
                        f"index={new_index} terms={previous_term!r},{term!r}"
                    )
                target_terms[new_index] = term
                by_index[new_index] = by_index.get(new_index, 0.0) + weight
                terms.add(term)
            new_indices = sorted(by_index)
            new_values = [by_index[index] for index in new_indices]
            vectors[layout.sparse_vector_name] = {
                "indices": new_indices,
                "values": new_values,
            }
        target_point = {
            "id": to_qdrant_point_id(logical_id),
            "vector": vectors,
            "payload": payload,
        }
        return logical_id, target_point, terms

    def _record_source_point(
        self,
        manifest: _ScanManifest,
        point: Mapping[str, Any],
        *,
        transformed: Mapping[str, Any],
        terms: Iterable[str],
    ) -> None:
        """Record one transformed source point in the bounded scan manifest."""

        raw_point_id = point.get("id")
        if raw_point_id is None:
            raise MigrationError("source collection contains a point without an id")
        raw_payload = self._payload(point)
        raw_original_id = raw_payload.get(_ORIGINAL_ID_FIELD)
        if raw_original_id is not None:
            expected_point_id = _legacy_qdrant_point_id(raw_original_id)
            if str(raw_point_id) != str(expected_point_id):
                raise MigrationError(
                    f"source point-id does not match legacy encoding for "
                    f"{raw_original_id!r}: expected={expected_point_id!r} "
                    f"found={raw_point_id!r}"
                )
        fingerprint_payload = dict(transformed["payload"])
        if "owner_user_id" in raw_payload:
            fingerprint_payload["owner_user_id"] = raw_payload["owner_user_id"]
        else:
            fingerprint_payload.pop("owner_user_id", None)
        fingerprint_point = dict(transformed)
        fingerprint_point["payload"] = fingerprint_payload
        fingerprint = _point_fingerprint(
            source_point_id=raw_point_id,
            transformed=fingerprint_point,
        )
        content_fingerprint = _canonical_point_content_fingerprint(transformed)
        manifest.add_source(
            raw_point_id=raw_point_id,
            logical_id=str(transformed["payload"][_ORIGINAL_ID_FIELD]),
            target_id=str(transformed["id"]),
            fingerprint=fingerprint,
            content_fingerprint=content_fingerprint,
            terms=terms,
        )

    def _scan_source(
        self,
        *,
        layout: CollectionLayout,
        schema: Mapping[str, Any],
        manifest: _ScanManifest | None = None,
        point_callback: Callable[[dict[str, Any]], None] | None = None,
    ) -> SourceSnapshot:
        source_count = self._count(self.source_collection)
        acl_incomplete_count = 0
        source_scanned = 0
        owned_manifest = manifest is None
        scan = manifest or _ScanManifest()
        try:
            for point in self._scroll(self.source_collection, with_vectors=True):
                source_scanned += 1
                _, transformed, terms = self._transform_point(
                    point,
                    layout=layout,
                    schema=schema,
                )
                self._record_source_point(
                    scan,
                    point,
                    transformed=transformed,
                    terms=terms,
                )
                payload = transformed["payload"]
                if not _acl_complete(payload):
                    acl_incomplete_count += 1
                if point_callback is not None:
                    point_callback(transformed)
            if source_scanned != source_count:
                raise MigrationError(
                    "source count mismatch: "
                    f"exact count={source_count} paginated count={source_scanned}"
                )
            self._validate_sparse_terms(scan.iter_sparse_terms())
            return SourceSnapshot(
                source_count=source_count,
                fingerprint=scan.source_fingerprint(),
                acl_incomplete_count=acl_incomplete_count,
                sparse_term_count=scan.sparse_term_count(),
                sparse_term_fingerprint=scan.sparse_term_fingerprint(),
            )
        finally:
            if owned_manifest:
                scan.close()

    def _marker_payload(
        self,
        *,
        layout: CollectionLayout,
        metadata: LegacyMetadata,
        sparse_weight: float,
        source_fingerprint: str,
        metadata_fingerprint: str,
        sparse_map_fingerprint: str,
        setup_complete: bool,
        acl_incomplete_count: int,
        sparse_term_count: int,
        sparse_term_fingerprint: str,
        migration_state: str = "building",
        source_count: int = 0,
        target_count: int = 0,
        transformed_source_fingerprint: str = "",
        target_content_fingerprint: str = "",
    ) -> dict[str, Any]:
        if migration_state not in MIGRATION_STATES:
            raise MigrationError(f"invalid migration state: {migration_state!r}")
        expected_setup = _MIGRATION_STATE_SETUP[migration_state]
        if setup_complete is not expected_setup:
            raise MigrationError("migration_state and setup_complete disagree")
        return {
            "_openviking_meta_version": _META_VERSION,
            "collection_name": self.target_collection,
            "metadata_collection_name": self.target_metadata_collection,
            "logical_collection": self.logical_collection,
            "migration_id": self.migration_id,
            "migrator_version": self.migrator_version,
            "migration_state": migration_state,
            "source_collection": self.source_collection,
            "source_metadata_collection": self.source_metadata_collection,
            "source_fingerprint": source_fingerprint,
            "transformed_source_fingerprint": transformed_source_fingerprint,
            "target_content_fingerprint": target_content_fingerprint,
            "metadata_fingerprint": metadata_fingerprint,
            "sparse_map_fingerprint": sparse_map_fingerprint,
            "setup_complete": setup_complete,
            "last_source_cursor": None,
            "backfill_complete": False,
            "source_count": source_count,
            "target_count": target_count,
            "acl_incomplete_count": acl_incomplete_count,
            "sparse_term_count": sparse_term_count,
            "sparse_term_fingerprint": sparse_term_fingerprint,
            "schema": metadata.schema,
            "dense_vector_name": layout.dense_vector_name,
            "sparse_vector_name": layout.sparse_vector_name,
            "vector_dim": layout.vector_dimension,
            "vector_dimension": layout.vector_dimension,
            "distance": layout.distance,
            "dense_datatype": layout.dense_datatype,
            "sparse_enabled": layout.sparse_enabled,
            "sparse_modifier": layout.sparse_modifier,
            "sparse_datatype": layout.sparse_datatype,
            "sparse_weight": sparse_weight,
            "indexes": metadata.indexes,
        }

    def _sparse_weight(self, metadata: LegacyMetadata, *, sparse_enabled: bool) -> float:
        values: list[float] = []
        for meta in metadata.indexes.values():
            if meta.get("SparseWeight") is not None:
                try:
                    value = float(meta["SparseWeight"])
                except (TypeError, ValueError) as exc:
                    raise MigrationError("legacy SparseWeight is invalid") from exc
                if not math.isfinite(value) or not 0.0 <= value <= 1.0:
                    raise MigrationError("legacy SparseWeight must be between 0 and 1")
                values.append(value)
                continue
            vector_index = meta.get("VectorIndex")
            if (
                isinstance(vector_index, Mapping)
                and vector_index.get("SearchWithSparseLogitAlpha") is not None
            ):
                try:
                    value = float(vector_index["SearchWithSparseLogitAlpha"])
                except (TypeError, ValueError) as exc:
                    raise MigrationError("legacy sparse alpha is invalid") from exc
                if not math.isfinite(value) or not 0.0 <= value <= 1.0:
                    raise MigrationError("legacy sparse alpha must be between 0 and 1")
                values.append(value)
        if values and any(value != values[0] for value in values[1:]):
            raise MigrationError("legacy indexes have conflicting sparse weights")
        if values:
            return values[0]
        return 0.5 if sparse_enabled else 0.0

    def _load_current_marker(self) -> dict[str, Any] | None:
        if not self._exists(self.target_metadata_collection):
            return None
        points = self._retrieve(
            self.target_metadata_collection,
            [_META_MARKER_ID],
            with_vectors=False,
        )
        if not points:
            return None
        payload = points[0].get("payload")
        return dict(payload) if isinstance(payload, Mapping) else None

    def _validate_marker_ownership(self, marker: Mapping[str, Any]) -> None:
        expected_fields = {
            "_openviking_meta_version": _META_VERSION,
            "collection_name": self.target_collection,
            "metadata_collection_name": self.target_metadata_collection,
            "logical_collection": self.logical_collection,
            "migration_id": self.migration_id,
            "migrator_version": self.migrator_version,
            "source_collection": self.source_collection,
            "source_metadata_collection": self.source_metadata_collection,
        }
        for field_name, expected in expected_fields.items():
            if marker.get(field_name) != expected:
                label = "migration ID" if field_name == "migration_id" else field_name
                raise MigrationError(
                    f"target current marker {label} differs: "
                    f"expected={expected!r} target={marker.get(field_name)!r}"
                )
        for field_name, expected in (
            ("target_collection", self.target_collection),
            ("target_metadata_collection", self.target_metadata_collection),
        ):
            if field_name in marker and marker[field_name] != expected:
                raise MigrationError(
                    f"target current marker {field_name} differs: "
                    f"expected={expected!r} target={marker.get(field_name)!r}"
                )
        state = marker.get("migration_state")
        if not isinstance(state, str) or state not in MIGRATION_STATES:
            raise MigrationError(f"target current marker has an invalid migration state: {state!r}")
        setup_complete = marker.get("setup_complete")
        if setup_complete is not _MIGRATION_STATE_SETUP[state]:
            raise MigrationError(
                "target current marker migration_state and setup_complete disagree"
            )
        vector_dim = marker.get("vector_dim")
        vector_dimension = marker.get("vector_dimension")
        if (
            isinstance(vector_dim, bool)
            or not isinstance(vector_dim, int)
            or vector_dim <= 0
            or isinstance(vector_dimension, bool)
            or not isinstance(vector_dimension, int)
            or vector_dimension <= 0
            or vector_dim != vector_dimension
        ):
            raise MigrationError("target current marker has inconsistent vector dimensions")

    def _transition(
        self,
        target_state: str,
        *,
        setup_complete: bool | None = None,
        allow_active_rollback: bool = False,
    ) -> dict[str, Any]:
        """Write and verify a migration state marker owned by this controller."""

        if not isinstance(target_state, str) or target_state not in MIGRATION_STATES:
            raise MigrationError(f"invalid migration state: {target_state!r}")
        expected_setup = _MIGRATION_STATE_SETUP[target_state]
        if setup_complete is None:
            setup_complete = expected_setup
        if setup_complete is not expected_setup:
            raise MigrationError("migration_state and setup_complete disagree")
        marker = self._load_current_marker()
        if marker is None:
            raise MigrationError("target metadata collection has no migration marker")
        self._validate_marker_ownership(marker)
        previous_state = marker["migration_state"]
        allowed_previous = {
            "building": {"building", "failed", "ready"},
            "ready": {"building", "ready"},
            "cutting_over": {"ready", "cutting_over"},
            "active": {"cutting_over", "active"},
            "retained": {"active", "retained"},
            "rolled_back": {"cutting_over", "rolled_back"},
            "failed": {"building", "ready", "cutting_over", "failed"},
        }
        if previous_state not in allowed_previous[target_state]:
            if not (
                target_state == "rolled_back"
                and previous_state == "active"
                and allow_active_rollback
            ):
                raise MigrationError(
                    f"cannot transition migration state from {previous_state!r} to {target_state!r}"
                )
        updated = dict(marker)
        updated["migration_state"] = target_state
        updated["setup_complete"] = setup_complete
        self._write_marker(updated, expected_state=previous_state)
        verified = self._load_current_marker()
        if verified is None:
            raise MigrationError("migration state marker disappeared after write")
        self._validate_marker_ownership(verified)
        if (
            verified.get("migration_state") != target_state
            or verified.get("setup_complete") is not setup_complete
        ):
            raise MigrationError("migration state marker was not persisted")
        return verified

    def _validate_metadata_layout(self, collection: str) -> None:
        layout = self._layout(
            self._collection_info(collection),
            honor_overrides=False,
        )
        if (
            layout.dense_vector_name != _META_VECTOR_NAME
            or layout.vector_dimension != 1
            or layout.distance != "Dot"
            or layout.sparse_enabled
        ):
            raise MigrationError(
                f"target metadata collection has an incompatible vector layout: {collection}"
            )

    def _validate_existing_target(
        self,
        *,
        target_info: Mapping[str, Any] | None,
        marker: Mapping[str, Any],
        layout: CollectionLayout,
        metadata: LegacyMetadata,
    ) -> None:
        required_fields = {
            "_openviking_meta_version",
            "collection_name",
            "metadata_collection_name",
            "logical_collection",
            "migration_id",
            "migrator_version",
            "migration_state",
            "source_collection",
            "source_metadata_collection",
            "source_fingerprint",
            "transformed_source_fingerprint",
            "target_content_fingerprint",
            "metadata_fingerprint",
            "sparse_map_fingerprint",
            "setup_complete",
            "last_source_cursor",
            "backfill_complete",
            "source_count",
            "target_count",
            "acl_incomplete_count",
            "sparse_term_count",
            "sparse_term_fingerprint",
            "schema",
            "dense_vector_name",
            "sparse_vector_name",
            "vector_dim",
            "distance",
            "dense_datatype",
            "sparse_enabled",
            "sparse_modifier",
            "sparse_datatype",
            "sparse_weight",
            "indexes",
        }
        missing_fields = sorted(field for field in required_fields if field not in marker)
        if missing_fields:
            raise MigrationError(
                f"target current marker is missing required fields: {missing_fields!r}"
            )
        if marker.get("_openviking_meta_version") != _META_VERSION:
            raise MigrationError(
                "target metadata collection has no valid current marker "
                f"(expected version {_META_VERSION})"
            )
        for field_name, expected in (
            ("collection_name", self.target_collection),
            ("metadata_collection_name", self.target_metadata_collection),
            ("logical_collection", self.logical_collection),
            ("migration_id", self.migration_id),
            ("migrator_version", self.migrator_version),
            ("source_collection", self.source_collection),
            ("source_metadata_collection", self.source_metadata_collection),
        ):
            if marker.get(field_name) != expected:
                if field_name == "migration_id":
                    raise MigrationError(
                        "target current marker migration ID differs: "
                        f"expected={expected!r} target={marker.get(field_name)!r}"
                    )
                raise MigrationError(
                    f"target current marker {field_name} differs: "
                    f"expected={expected!r} target={marker.get(field_name)!r}"
                )
        for field_name, expected in (
            ("target_collection", self.target_collection),
            ("target_metadata_collection", self.target_metadata_collection),
        ):
            if field_name in marker and marker[field_name] != expected:
                raise MigrationError(
                    f"target current marker {field_name} differs: "
                    f"expected={expected!r} target={marker.get(field_name)!r}"
                )
        migration_state = marker.get("migration_state")
        if not isinstance(migration_state, str) or migration_state not in MIGRATION_STATES:
            raise MigrationError(
                f"target current marker has an invalid migration state: {migration_state!r}"
            )
        expected_setup = _MIGRATION_STATE_SETUP[migration_state]
        if marker.get("setup_complete") is not expected_setup:
            raise MigrationError(
                "target current marker migration_state and setup_complete disagree"
            )
        for field_name in ("source_count", "target_count", "sparse_term_count"):
            value = marker.get(field_name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise MigrationError(f"target current marker has an invalid {field_name}")
        if not isinstance(marker.get("backfill_complete"), bool):
            raise MigrationError("target current marker has an invalid backfill_complete flag")
        cursor = marker.get("last_source_cursor")
        try:
            _validate_cursor(
                cursor,
                field_name="target current marker last_source_cursor",
            )
        except MigrationError as exc:
            raise MigrationError("target current marker has an invalid last_source_cursor") from exc
        if marker["backfill_complete"] and cursor is not None:
            raise MigrationError("target current marker has an inconsistent completion cursor")
        if (
            not isinstance(marker.get("source_fingerprint"), str)
            or not marker["source_fingerprint"]
        ):
            raise MigrationError("target current marker has no valid source fingerprint")
        for field_name in (
            "transformed_source_fingerprint",
            "target_content_fingerprint",
        ):
            if not isinstance(marker.get(field_name), str):
                raise MigrationError(f"target current marker has an invalid {field_name}")
        if (
            not isinstance(marker.get("metadata_fingerprint"), str)
            or not marker["metadata_fingerprint"]
        ):
            raise MigrationError("target current marker has no valid metadata fingerprint")
        if (
            not isinstance(marker.get("sparse_map_fingerprint"), str)
            or not marker["sparse_map_fingerprint"]
        ):
            raise MigrationError("target current marker has no valid sparse-map fingerprint")
        if (
            not isinstance(marker.get("sparse_term_fingerprint"), str)
            or not marker["sparse_term_fingerprint"]
        ):
            raise MigrationError("target current marker has no valid sparse-term fingerprint")
        if not isinstance(marker.get("setup_complete"), bool):
            raise MigrationError("target current marker has an invalid setup_complete flag")
        acl_incomplete_count = marker.get("acl_incomplete_count")
        if (
            isinstance(acl_incomplete_count, bool)
            or not isinstance(acl_incomplete_count, int)
            or acl_incomplete_count < 0
        ):
            raise MigrationError("target current marker has an invalid ACL-incomplete count")
        if not isinstance(marker.get("schema"), Mapping):
            raise MigrationError("target current marker has no schema")
        dense_vector_name = marker.get("dense_vector_name")
        sparse_vector_name = marker.get("sparse_vector_name")
        vector_dim = marker.get("vector_dim")
        dense_datatype = marker.get("dense_datatype")
        sparse_enabled = marker.get("sparse_enabled")
        sparse_modifier = marker.get("sparse_modifier")
        sparse_datatype = marker.get("sparse_datatype")
        vector_dimension = marker.get("vector_dimension")
        if (
            not isinstance(dense_vector_name, str)
            or not dense_vector_name
            or not isinstance(sparse_vector_name, str)
            or not sparse_vector_name
            or isinstance(vector_dim, bool)
            or not isinstance(vector_dim, int)
            or vector_dim <= 0
            or (dense_datatype is not None and not isinstance(dense_datatype, str))
            or not isinstance(sparse_enabled, bool)
            or (sparse_modifier is not None and not isinstance(sparse_modifier, str))
            or (sparse_datatype is not None and not isinstance(sparse_datatype, str))
            or isinstance(vector_dimension, bool)
            or not isinstance(vector_dimension, int)
            or vector_dimension <= 0
        ):
            raise MigrationError("target current marker has an invalid vector layout")
        if vector_dimension != vector_dim:
            raise MigrationError("target current marker has inconsistent vector dimensions")
        try:
            marker_distance = _canonical_distance(marker["distance"])
            marker_sparse_weight = float(marker["sparse_weight"])
        except (TypeError, ValueError) as exc:
            raise MigrationError(
                "target current marker has invalid distance or sparse weight"
            ) from exc
        if (
            marker_distance != layout.distance
            or not math.isfinite(marker_sparse_weight)
            or not 0.0 <= marker_sparse_weight <= 1.0
        ):
            raise MigrationError("target current marker has an incompatible search policy")
        marker_layout = {
            "dense_vector_name": dense_vector_name,
            "sparse_vector_name": sparse_vector_name,
            "vector_dim": vector_dim,
            "vector_dimension": vector_dimension,
            "distance": marker_distance,
            "dense_datatype": dense_datatype,
            "sparse_enabled": sparse_enabled,
            "sparse_modifier": sparse_modifier,
            "sparse_datatype": sparse_datatype,
        }
        expected_layout = {
            "dense_vector_name": layout.dense_vector_name,
            "sparse_vector_name": layout.sparse_vector_name,
            "vector_dim": layout.vector_dimension,
            "distance": layout.distance,
            "dense_datatype": layout.dense_datatype,
            "sparse_enabled": layout.sparse_enabled,
            "sparse_modifier": layout.sparse_modifier,
            "sparse_datatype": layout.sparse_datatype,
        }
        for key, expected in expected_layout.items():
            if marker_layout[key] != expected:
                raise MigrationError(
                    f"target current marker differs for {key}: "
                    f"source={expected!r} target={marker_layout[key]!r}"
                )
        indexes = marker.get("indexes")
        if not isinstance(indexes, Mapping) or any(
            not isinstance(name, str) or not name.strip() or not isinstance(value, Mapping)
            for name, value in indexes.items()
        ):
            raise MigrationError("target current marker has invalid indexes")
        for index_name, index_meta in indexes.items():
            _validate_scalar_index(
                index_meta.get("ScalarIndex"),
                label=f"target current marker index {index_name!r}",
            )
        _validate_schema_subset(metadata.schema, marker["schema"])
        for index_name in metadata.indexes:
            target_index = indexes.get(index_name)
            if not isinstance(target_index, Mapping):
                raise MigrationError(f"target current marker is missing index {index_name!r}")
        if target_info is None:
            target_layout = None
        else:
            target_layout = self._layout(target_info)
            for field_name in (
                "dense_vector_name",
                "sparse_vector_name",
                "vector_dimension",
                "distance",
                "dense_datatype",
                "sparse_enabled",
                "sparse_modifier",
                "sparse_datatype",
            ):
                expected = getattr(layout, field_name)
                actual = getattr(target_layout, field_name)
                if expected != actual:
                    raise MigrationError(
                        f"target collection layout differs for {field_name}: "
                        f"source={expected!r} target={actual!r}"
                    )
            if marker["setup_complete"]:
                self._validate_payload_indexes(marker["schema"], indexes)

        expected_indexes = set(metadata.indexes)
        actual_indexes = set(indexes)
        if actual_indexes != expected_indexes:
            missing = sorted(expected_indexes - actual_indexes)
            extra = sorted(actual_indexes - expected_indexes)
            raise MigrationError(
                f"target current marker index map differs: missing={missing!r} extra={extra!r}"
            )
        for index_name, source_index in metadata.indexes.items():
            target_index = indexes[index_name]
            if target_index != source_index:
                raise MigrationError(f"target current marker index {index_name!r} changed")

    def _assert_source_layout(self, expected: CollectionLayout, *, phase: str) -> None:
        actual = self._layout(self._collection_info(self.source_collection))
        if actual != expected:
            raise MigrationError(
                f"source physical layout changed {phase}; rerun preflight with source writes frozen"
            )

    def _delete_points(
        self,
        collection: str,
        point_ids: list[str],
        *,
        expected_state: str,
    ) -> None:
        if not point_ids:
            return
        self._assert_owned_target_mutation(collection, expected_state=expected_state)
        self._request(
            "POST",
            self._path(collection, "/points/delete"),
            {"points": point_ids},
            mutation=True,
        )

    def _reconcile_round(
        self,
        *,
        layout: CollectionLayout,
        schema: Mapping[str, Any],
        metadata: LegacyMetadata,
        state: str,
    ) -> SourceSnapshot:
        """Rebuild the target from one bounded, source-authoritative scan."""

        del metadata  # The caller pins these values around the round.
        stats = {"migrated_count": 0, "skipped_count": 0, "deleted_count": 0}
        with _ScanManifest() as scan:
            pending: list[dict[str, Any]] = []

            def upsert_pending() -> None:
                if not pending:
                    return
                migrated, skipped = self._upsert_target_batch(
                    pending,
                    expected_state=state,
                )
                stats["migrated_count"] += migrated
                stats["skipped_count"] += skipped
                pending.clear()

            def point_callback(transformed: dict[str, Any]) -> None:
                if not _acl_complete(transformed["payload"]) and not getattr(
                    self, "_reconcile_allow_acl_fail_open", False
                ):
                    raise MigrationError(
                        f"point {transformed.get('id')!r} lacks complete ACL fields"
                    )
                pending.append(transformed)
                if len(pending) >= self.batch_size:
                    upsert_pending()

            source = self._scan_source(
                layout=layout,
                schema=schema,
                manifest=scan,
                point_callback=point_callback,
            )
            upsert_pending()

            # The map is input-sized and therefore bounded independently of
            # the point count; write only terms from the authoritative policy.
            self._write_sparse_dictionary(
                self._sparse_map.values(),
                expected_state=state,
            )

            pending_deletes: list[str] = []
            for point in self._scroll(self.target_collection, with_vectors=True):
                point_id = point.get("id")
                if point_id is None:
                    raise MigrationError("target collection contains a point without an id")
                point_id = str(point_id)
                payload = point.get("payload")
                if not isinstance(payload, Mapping):
                    raise MigrationError(f"target point {point_id!r} has no payload")
                original_id = payload.get(_ORIGINAL_ID_FIELD)
                if original_id is None or not str(original_id):
                    raise MigrationError(
                        f"target point {point_id!r} is missing {_ORIGINAL_ID_FIELD}"
                    )
                scan.add_target(point_id, original_id)
                expected_id = str(to_qdrant_point_id(str(original_id)))
                if expected_id != point_id:
                    raise MigrationError(
                        f"target point-id {point_id!r} does not match "
                        f"deterministic encoding for {original_id!r}"
                    )
                if not scan.has_source_target(point_id):
                    pending_deletes.append(point_id)
                    if len(pending_deletes) >= self.batch_size:
                        self._delete_points(
                            self.target_collection,
                            pending_deletes,
                            expected_state=state,
                        )
                        for deleted_id in pending_deletes:
                            scan.remove_target(deleted_id)
                        stats["deleted_count"] += len(pending_deletes)
                        pending_deletes.clear()
                else:
                    scan.set_target_content_fingerprint(
                        point_id,
                        _canonical_point_content_fingerprint(point),
                    )
            if pending_deletes:
                self._delete_points(
                    self.target_collection,
                    pending_deletes,
                    expected_state=state,
                )
                for deleted_id in pending_deletes:
                    scan.remove_target(deleted_id)
                stats["deleted_count"] += len(pending_deletes)

            self._reconcile_last_stats = stats
            return SourceSnapshot(
                source_count=source.source_count,
                fingerprint=source.fingerprint,
                acl_incomplete_count=source.acl_incomplete_count,
                sparse_term_count=source.sparse_term_count,
                sparse_term_fingerprint=source.sparse_term_fingerprint,
                transformed_source_fingerprint=scan.transformed_source_fingerprint(),
                target_content_fingerprint=scan.target_content_fingerprint(),
            )

    def _persist_reconcile_receipt(
        self,
        source: SourceSnapshot,
        *,
        target_count: int,
        expected_state: str,
        metadata_fingerprint: str,
        sparse_map_fingerprint: str,
    ) -> None:
        marker = self._load_current_marker()
        if marker is None:
            raise MigrationError("target marker disappeared before reconcile receipt")
        self._validate_marker_ownership(marker)
        if (
            marker.get("migration_state") != expected_state
            or marker.get("setup_complete") is not _MIGRATION_STATE_SETUP[expected_state]
        ):
            raise MigrationError("target marker state changed before reconcile receipt")
        if (
            marker.get("metadata_fingerprint") != metadata_fingerprint
            or marker.get("sparse_map_fingerprint") != sparse_map_fingerprint
        ):
            raise MigrationError("target marker fingerprints changed before reconcile receipt")
        updated = dict(marker)
        updated.update(
            {
                "source_count": source.source_count,
                "source_fingerprint": source.fingerprint,
                "acl_incomplete_count": source.acl_incomplete_count,
                "sparse_term_count": source.sparse_term_count,
                "sparse_term_fingerprint": source.sparse_term_fingerprint,
                "target_count": target_count,
                "transformed_source_fingerprint": source.transformed_source_fingerprint,
                "target_content_fingerprint": source.target_content_fingerprint,
                "migration_state": expected_state,
                "setup_complete": _MIGRATION_STATE_SETUP[expected_state],
            }
        )
        self._write_marker(updated, expected_state=expected_state)

    def reconcile(
        self,
        *,
        confirm: bool,
        plan: MigrationPlan,
        barrier_held: bool = False,
        allow_acl_fail_open: bool = False,
        lock_held: bool = False,
    ) -> dict[str, Any]:
        """Converge an owned target to the rolling source snapshot."""

        if not confirm:
            raise MigrationError("reconcile requires explicit confirm=True / --confirm")
        if not lock_held:
            raise MigrationError(
                "reconcile requires external source lock acknowledgement via "
                "lock_held=True / --lock-held"
            )
        if not isinstance(plan, MigrationPlan):
            raise MigrationError("reconcile requires a reviewed migration plan")

        marker: Mapping[str, Any] | None = None
        try:
            marker = self._load_current_marker()
            if marker is None:
                raise MigrationError("reconcile requires an owned target marker")
            self._validate_marker_ownership(marker)
            self._assert_prepare_plan_identity(plan)
            layout = self._layout_from_plan(plan)
            metadata = self._legacy_metadata()
            self._validate_source_metadata_layout(metadata.schema, layout)
            pinned_metadata_fingerprint = _metadata_fingerprint(metadata)
            pinned_sparse_map_fingerprint = _sparse_map_fingerprint(self._sparse_map)
            if pinned_metadata_fingerprint != plan.metadata_fingerprint:
                raise MigrationError("legacy metadata fingerprint differs from the reviewed plan")
            if pinned_sparse_map_fingerprint != plan.sparse_map_fingerprint:
                raise MigrationError(
                    "authoritative sparse map fingerprint differs from the reviewed plan"
                )
            if marker.get("metadata_fingerprint") != pinned_metadata_fingerprint:
                raise MigrationError("target marker metadata fingerprint is stale")
            if marker.get("sparse_map_fingerprint") != pinned_sparse_map_fingerprint:
                raise MigrationError("target marker sparse-map fingerprint is stale")
            self._validate_metadata_layout(self.target_metadata_collection)
            self._validate_existing_target(
                target_info=self._collection_info(self.target_collection),
                marker=marker,
                layout=layout,
                metadata=metadata,
            )

            state = marker.get("migration_state")
            if barrier_held:
                if state != "cutting_over" or marker.get("setup_complete") is not True:
                    raise MigrationError("barrier-held reconcile requires a cutting_over target")
            elif state not in {"building", "ready", "failed"}:
                raise MigrationError(
                    "reconcile may mutate only a building, ready, or failed target; "
                    f"current state is {state!r}"
                )
            if not barrier_held:
                marker = self._transition("building")

            self._reconcile_allow_acl_fail_open = allow_acl_fail_open
            previous: SourceSnapshot | None = None
            final_source: SourceSnapshot | None = None
            total_migrated = 0
            total_skipped = 0
            total_deleted = 0
            rounds = 0
            target_count = 0
            for round_number in range(1, MAX_RECONCILIATION_ROUNDS + 1):
                rounds = round_number
                current_marker = self._load_current_marker()
                if current_marker is None:
                    raise MigrationError("target marker disappeared during reconcile")
                self._validate_marker_ownership(current_marker)
                expected_state = "cutting_over" if barrier_held else "building"
                if (
                    current_marker.get("migration_state") != expected_state
                    or current_marker.get("setup_complete")
                    is not _MIGRATION_STATE_SETUP[expected_state]
                ):
                    raise MigrationError("target marker state changed during reconcile")
                before_metadata = self._legacy_metadata()
                before_metadata_fingerprint = _metadata_fingerprint(before_metadata)
                before_sparse_map_fingerprint = _sparse_map_fingerprint(self._sparse_map)
                if before_metadata_fingerprint != pinned_metadata_fingerprint:
                    raise MigrationError(
                        "legacy metadata fingerprint changed before reconcile round"
                    )
                if before_sparse_map_fingerprint != pinned_sparse_map_fingerprint:
                    raise MigrationError(
                        "authoritative sparse map fingerprint changed before reconcile round"
                    )

                source = self._reconcile_round(
                    layout=layout,
                    schema=before_metadata.schema,
                    metadata=before_metadata,
                    state=expected_state,
                )
                round_stats = getattr(self, "_reconcile_last_stats", {})
                total_migrated += int(round_stats.get("migrated_count", 0))
                total_skipped += int(round_stats.get("skipped_count", 0))
                total_deleted += int(round_stats.get("deleted_count", 0))

                after_metadata = self._legacy_metadata()
                after_metadata_fingerprint = _metadata_fingerprint(after_metadata)
                after_sparse_map_fingerprint = _sparse_map_fingerprint(self._sparse_map)
                if after_metadata_fingerprint != pinned_metadata_fingerprint:
                    raise MigrationError("legacy metadata fingerprint changed during reconcile")
                if after_sparse_map_fingerprint != pinned_sparse_map_fingerprint:
                    raise MigrationError(
                        "authoritative sparse map fingerprint changed during reconcile"
                    )
                target_count = self._count(self.target_collection)
                self._persist_reconcile_receipt(
                    source,
                    target_count=target_count,
                    expected_state=expected_state,
                    metadata_fingerprint=pinned_metadata_fingerprint,
                    sparse_map_fingerprint=pinned_sparse_map_fingerprint,
                )
                if (
                    previous is not None
                    and source.source_count == previous.source_count
                    and source.fingerprint == previous.fingerprint
                    and source.acl_incomplete_count == previous.acl_incomplete_count
                    and source.sparse_term_count == previous.sparse_term_count
                    and source.sparse_term_fingerprint == previous.sparse_term_fingerprint
                    and target_count == source.source_count
                ):
                    final_source = source
                    break
                previous = source

            if final_source is None:
                raise MigrationError(
                    "source did not converge after "
                    f"round {MAX_RECONCILIATION_ROUNDS}: "
                    f"source_count={source.source_count} target_count={target_count}"
                )

            current_marker = self._load_current_marker()
            if current_marker is None:
                raise MigrationError("target marker disappeared before reconcile completion")
            self._validate_marker_ownership(current_marker)
            expected_final_state = "cutting_over" if barrier_held else "building"
            if (
                current_marker.get("migration_state") != expected_final_state
                or current_marker.get("setup_complete")
                is not _MIGRATION_STATE_SETUP[expected_final_state]
            ):
                raise MigrationError("target marker state changed before reconcile completion")
            if (
                current_marker.get("metadata_fingerprint") != pinned_metadata_fingerprint
                or current_marker.get("sparse_map_fingerprint") != pinned_sparse_map_fingerprint
            ):
                raise MigrationError(
                    "target marker fingerprints changed before reconcile completion"
                )
            final_state = expected_final_state
            return {
                "source_count": final_source.source_count,
                "target_count": target_count,
                "migrated_count": total_migrated,
                "skipped_count": total_skipped,
                "deleted_count": total_deleted,
                "rounds": rounds,
                "target_collection": self.target_collection,
                "migration_state": final_state,
            }
        except Exception:
            if marker is not None and not barrier_held:
                try:
                    current = self._load_current_marker()
                    if current is not None:
                        self._validate_marker_ownership(current)
                        if (
                            current.get("migration_state") == "building"
                            and current.get("setup_complete") is False
                        ):
                            failed = dict(current)
                            failed["migration_state"] = "failed"
                            failed["setup_complete"] = False
                            self._write_marker(failed, expected_state="building")
                except Exception:
                    pass
            raise
        finally:
            self._reconcile_allow_acl_fail_open = False

    def _scan_target_ids(self, manifest: _ScanManifest) -> int:
        scanned = 0
        for point in self._scroll(self.target_collection, with_vectors=False):
            scanned += 1
            point_id = point.get("id")
            point_id = _canonical_uuid_string(
                point_id,
                field_name="target physical point id",
            )
            payload = point.get("payload")
            original_id = payload.get(_ORIGINAL_ID_FIELD) if isinstance(payload, Mapping) else None
            manifest.add_target(point_id, original_id)
        return scanned

    @staticmethod
    def _expected_payload_indexes(
        schema: Mapping[str, Any],
        indexes: Mapping[str, Mapping[str, Any]],
    ) -> dict[str, str]:
        fields = schema.get("Fields", [])
        field_list = fields if isinstance(fields, list) else []
        expected: dict[str, str] = {
            "uri_depth": "integer",
            "scope_roots": "keyword",
        }
        for metadata in indexes.values():
            scalar = metadata.get("ScalarIndex")
            if isinstance(scalar, Mapping):
                scalar = list(scalar)
            if isinstance(scalar, (list, tuple, set)):
                for field_name in scalar:
                    name = str(field_name)
                    expected[name] = (
                        "integer"
                        if name == "uri_depth"
                        else qdrant_payload_field_schema(name, field_list)
                    )
        return expected

    def _payload_schema(self, info: Mapping[str, Any]) -> Mapping[str, Any]:
        value = info.get("payload_schema")
        if not isinstance(value, Mapping):
            raise MigrationError("Qdrant collection response has no payload_schema")
        return value

    @staticmethod
    def _physical_index_type(value: Mapping[str, Any]) -> str | None:
        raw_type = value.get("data_type") or value.get("type")
        params = value.get("params")
        if raw_type is None and isinstance(params, Mapping):
            raw_type = params.get("type") or params.get("data_type")
        if raw_type is None:
            return None
        normalized = str(raw_type).strip().lower()
        aliases = {
            "int": "integer",
            "int64": "integer",
            "uint": "integer",
            "uint64": "integer",
            "double": "float",
            "bool": "bool",
            "boolean": "bool",
            "datetime": "datetime",
            "date_time": "datetime",
        }
        return aliases.get(normalized, normalized)

    def _validate_payload_indexes(
        self,
        schema: Mapping[str, Any],
        indexes: Mapping[str, Mapping[str, Any]],
    ) -> None:
        physical = self._payload_schema(self._collection_info(self.target_collection))
        for field_name, expected_type in self._expected_payload_indexes(schema, indexes).items():
            value = physical.get(field_name)
            if not isinstance(value, Mapping):
                raise MigrationError(f"target collection is missing payload index {field_name!r}")
            actual_type = self._physical_index_type(value)
            if actual_type != expected_type:
                raise MigrationError(
                    f"target payload index {field_name!r} has incompatible schema: "
                    f"expected={expected_type!r} target={actual_type!r}"
                )

    def _existing_sparse_dictionary(self) -> _SparseDictionaryManifest:
        dictionary = _SparseDictionaryManifest()
        try:
            if not self._exists(self.target_metadata_collection):
                return dictionary
            for point in self._scroll(self.target_metadata_collection, with_vectors=False):
                point_id = point.get("id")
                if point_id is not None and str(point_id) == _META_MARKER_ID:
                    continue
                try:
                    term, index = parse_sparse_point(point)
                except (TypeError, ValueError) as exc:
                    raise SparseMigrationError(
                        f"target metadata collection contains an invalid sparse point "
                        f"{point_id!r}: {exc}"
                    ) from exc
                payload = point["payload"]
                has_logical = "logical_collection" in payload
                has_migration = "migration_id" in payload
                if has_logical != has_migration:
                    raise SparseMigrationError(
                        "target sparse dictionary point has incomplete migration provenance"
                    )
                if has_logical and (
                    payload.get("logical_collection") != self.logical_collection
                    or payload.get("migration_id") != self.migration_id
                ):
                    raise SparseMigrationError(
                        "target sparse dictionary point belongs to another migration; "
                        "stable term mapping cannot be trusted"
                    )
                dictionary.add(term, index, point_id=str(point_id))
        except Exception:
            dictionary.close()
            raise
        return dictionary

    def _assert_sparse_dictionary_complete(self, terms: Iterable[str]) -> None:
        term_set = set(terms)
        if not term_set:
            return
        with self._existing_sparse_dictionary() as dictionary:
            missing_terms = sorted(term for term in term_set if not dictionary.has_term(term))
            missing_owners = sorted(
                term for term in term_set if not dictionary.has_owner(stable_sparse_index(term))
            )
            if missing_terms or missing_owners:
                detail: list[str] = []
                if missing_terms:
                    detail.append(f"terms={missing_terms!r}")
                if missing_owners:
                    detail.append(f"owners={missing_owners!r}")
                raise SparseMigrationError(
                    "target sparse dictionary is incomplete after write: " + ", ".join(detail)
                )
            for term in term_set:
                expected_index = stable_sparse_index(term)
                actual_index = dictionary.index_for_term(term)
                if actual_index != expected_index:
                    raise SparseMigrationError(
                        "target sparse dictionary has an incompatible index for "
                        f"{term!r}: expected={expected_index} found={actual_index}"
                    )

    def _validate_sparse_terms(self, terms: Iterable[str]) -> None:
        target_metadata_exists = self._exists(self.target_metadata_collection)
        with self._existing_sparse_dictionary() as existing:
            with _ScanManifest() as planned_manifest:
                planned = planned_manifest.connection
                planned.execute(
                    "CREATE TABLE planned_sparse_terms (term TEXT PRIMARY KEY, sparse_index INTEGER UNIQUE)"
                )
                for term in terms:
                    if not isinstance(term, str) or not term.strip():
                        raise SparseMigrationError("source sparse dictionary has an invalid term")
                    index = stable_sparse_index(term)
                    try:
                        planned.execute(
                            "INSERT INTO planned_sparse_terms(term, sparse_index) VALUES (?, ?)",
                            (term, index),
                        )
                    except sqlite3.IntegrityError as exc:
                        existing_row = planned.execute(
                            "SELECT term FROM planned_sparse_terms WHERE sparse_index = ?",
                            (index,),
                        ).fetchone()
                        existing_term = existing_row[0] if existing_row else "<unknown>"
                        if existing_term != term:
                            raise SparseMigrationError(
                                "sparse term collision after migration: "
                                f"index={index} terms={existing_term!r},{term!r}"
                            ) from exc
                        continue
                    existing_term = existing.term_for_index(index)
                    if existing_term is not None and existing_term != term:
                        raise SparseMigrationError(
                            "target sparse dictionary collision: "
                            f"index={index} existing_term={existing_term!r} source_term={term!r}"
                        )
                    existing_index = existing.index_for_term(term)
                    if existing_index is not None and existing_index != index:
                        raise SparseMigrationError(
                            "target sparse dictionary maps source term to a different index: "
                            f"term={term!r} existing={existing_index} expected={index}"
                        )
                    if not target_metadata_exists:
                        continue
                    expected_ids = (
                        to_qdrant_point_id(f"openviking:sparse:{term}"),
                        sparse_owner_point_id(index),
                    )
                    points = self._retrieve(
                        self.target_metadata_collection,
                        list(expected_ids),
                        with_vectors=False,
                    )
                    for point in points:
                        try:
                            actual_term, actual_index = parse_sparse_point(point)
                        except (TypeError, ValueError) as exc:
                            raise SparseMigrationError(
                                f"target sparse dictionary point-id collision for term "
                                f"{term!r}: point={point.get('id')!r}: {exc}"
                            ) from exc
                        if actual_term != term or actual_index != index:
                            raise SparseMigrationError(
                                "target sparse dictionary point-id collision for term "
                                f"{term!r}: point={point.get('id')!r}"
                            )

    def _validate_source_metadata_layout(
        self,
        schema: Mapping[str, Any],
        layout: CollectionLayout,
    ) -> None:
        fields = schema.get("Fields", [])
        if not isinstance(fields, list):
            raise MigrationError("legacy collection metadata schema Fields must be a list")
        dense_fields = [
            field
            for field in fields
            if isinstance(field, Mapping)
            and str(field.get("FieldType") or "").strip().lower() == "vector"
        ]
        if len(dense_fields) != 1:
            raise MigrationError(
                "legacy collection metadata must declare exactly one dense vector field"
            )
        dense_field = dense_fields[0]
        raw_dimension = dense_field.get("Dim")
        if (
            isinstance(raw_dimension, bool)
            or not isinstance(raw_dimension, int)
            or raw_dimension != layout.vector_dimension
        ):
            raise MigrationError(
                "legacy dense vector metadata dimension does not match the "
                f"physical source layout: metadata={raw_dimension!r} "
                f"physical={layout.vector_dimension}"
            )
        declared_dense_name = str(dense_field.get("FieldName"))
        if declared_dense_name != layout.dense_vector_name and not (
            self._dense_vector_name_override
            and declared_dense_name == "vector"
            and layout.dense_vector_name == self._dense_vector_name_override
        ):
            raise MigrationError(
                "legacy dense vector metadata name does not match the physical "
                f"source layout: metadata={declared_dense_name!r} "
                f"physical={layout.dense_vector_name!r}"
            )

        sparse_fields = [
            field
            for field in fields
            if isinstance(field, Mapping)
            and str(field.get("FieldType") or "").strip().lower() == "sparse_vector"
        ]
        if not layout.sparse_enabled:
            if sparse_fields:
                raise MigrationError(
                    "legacy metadata declares a sparse vector but the physical "
                    "source layout has no sparse vectors"
                )
            return
        if len(sparse_fields) != 1:
            raise MigrationError(
                "legacy collection metadata must declare exactly one sparse vector "
                "when the physical source layout enables sparse vectors"
            )
        declared_sparse_name = str(sparse_fields[0].get("FieldName"))
        if declared_sparse_name != layout.sparse_vector_name and not (
            self._sparse_vector_name_override
            and declared_sparse_name == "sparse_vector"
            and layout.sparse_vector_name == self._sparse_vector_name_override
        ):
            raise MigrationError(
                "legacy sparse vector metadata name does not match the physical "
                f"source layout: metadata={declared_sparse_name!r} "
                f"physical={layout.sparse_vector_name!r}"
            )

    def preflight(self) -> MigrationPlan:
        """Read and validate source/target state without mutating Qdrant."""

        self._assert_strong_ordering_support()
        if not self._exists(self.source_collection):
            raise MigrationError(f"source collection does not exist: {self.source_collection}")
        source_info = self._collection_info(self.source_collection)
        layout = self._layout(source_info)
        metadata = self._legacy_metadata()
        self._validate_source_metadata_layout(metadata.schema, layout)
        metadata_fingerprint = _metadata_fingerprint(metadata)
        sparse_weight = self._sparse_weight(metadata, sparse_enabled=layout.sparse_enabled)
        sparse_map_fingerprint = _sparse_map_fingerprint(self._sparse_map)

        target_exists = self._exists(self.target_collection)
        target_count = 0
        marker: dict[str, Any] | None = None
        target_metadata_exists = self._exists(self.target_metadata_collection)
        if target_exists or target_metadata_exists:
            marker = self._load_current_marker()
            if marker is None:
                subject = (
                    "target metadata collection"
                    if target_metadata_exists and not target_exists
                    else "target current marker"
                )
                raise MigrationError(f"{subject} is missing; refusing to adopt or overwrite it")
            self._validate_metadata_layout(self.target_metadata_collection)
            self._validate_existing_target(
                target_info=(
                    self._collection_info(self.target_collection) if target_exists else None
                ),
                marker=marker,
                layout=layout,
                metadata=metadata,
            )
            if not target_exists and marker.get("setup_complete") is not False:
                raise MigrationError(
                    "target collection is missing for a completed migration marker"
                )
            if target_exists:
                target_count = self._count(self.target_collection)
                if marker.get("setup_complete") is False and target_count:
                    # The incomplete marker still owns the target, but the
                    # count remains a mutable observation for resume.
                    target_count = self._count(self.target_collection)

        with _ScanManifest() as scan:
            source = self._scan_source(
                layout=layout,
                schema=metadata.schema,
                manifest=scan,
            )
            if target_exists:
                target_scanned = self._scan_target_ids(scan)
                if target_scanned != target_count:
                    raise MigrationError(
                        "target count mismatch: "
                        f"exact count={target_count} paginated count={target_scanned}"
                    )
                for target_id, existing_original in scan.connection.execute(
                    "SELECT target_id, original_id FROM target_points"
                ):
                    source_row = scan.connection.execute(
                        "SELECT logical_id FROM source_targets WHERE target_id = ?",
                        (target_id,),
                    ).fetchone()
                    if existing_original is None or not str(existing_original):
                        raise MigrationError(
                            f"target point {target_id} is missing {_ORIGINAL_ID_FIELD}; "
                            "refusing to overwrite it"
                        )
                    expected_target_id = str(to_qdrant_point_id(str(existing_original)))
                    if expected_target_id != str(target_id):
                        raise MigrationError(
                            f"target point-id {target_id} does not match deterministic "
                            f"encoding for {existing_original!r}"
                        )
                    if source_row is not None and str(existing_original) != str(source_row[0]):
                        raise MigrationError(
                            f"target point-id collision for {target_id}: "
                            f"existing={existing_original!r} source={source_row[0]!r}"
                        )

        if marker is not None:
            if marker.get("metadata_fingerprint") != metadata_fingerprint:
                raise MigrationError(
                    "target current marker metadata fingerprint differs from the source metadata"
                )
            if marker.get("sparse_map_fingerprint") != sparse_map_fingerprint:
                raise MigrationError(
                    "target current marker sparse-map fingerprint differs from the sparse map"
                )

        return MigrationPlan(
            source_collection=self.source_collection,
            target_collection=self.target_collection,
            source_metadata_collection=self.source_metadata_collection,
            target_metadata_collection=self.target_metadata_collection,
            logical_collection=self.logical_collection,
            migration_id=self.migration_id,
            migrator_version=self.migrator_version,
            source_count=source.source_count,
            target_count=target_count,
            target_absent=not target_exists and not target_metadata_exists,
            target_state=marker.get("migration_state") if marker else None,
            dense_vector_name=layout.dense_vector_name,
            sparse_vector_name=layout.sparse_vector_name,
            vector_dimension=layout.vector_dimension,
            distance=layout.distance,
            dense_datatype=layout.dense_datatype,
            sparse_enabled=layout.sparse_enabled,
            sparse_modifier=layout.sparse_modifier,
            sparse_datatype=layout.sparse_datatype,
            sparse_weight=sparse_weight,
            source_fingerprint=source.fingerprint,
            metadata_fingerprint=metadata_fingerprint,
            sparse_map_fingerprint=sparse_map_fingerprint,
            acl_incomplete_count=source.acl_incomplete_count,
            sparse_term_count=source.sparse_term_count,
            sparse_term_fingerprint=source.sparse_term_fingerprint,
            batch_size=self.batch_size,
            timeout_seconds=self.timeout_seconds,
        )

    def _create_collection(
        self,
        name: str,
        body: dict[str, Any],
        *,
        allow_conflict: bool = False,
    ) -> bool:
        try:
            response = self._request(
                "PUT",
                self._path(name),
                body,
                mutation=True,
            )
        except Exception as exc:
            if _status(exc) == 409:
                if allow_conflict:
                    return False
                error = MigrationError(f"target collection appeared during migration: {name}")
                error.status = 409  # type: ignore[attr-defined]
                raise error from exc
            raise
        if response.get("result") is not True:
            raise MigrationError(f"Qdrant collection creation did not complete for {name}")
        return True

    def _assert_owned_target_mutation(
        self,
        collection: str,
        *,
        expected_state: str,
    ) -> dict[str, Any]:
        """Re-read ownership immediately before a target mutation."""

        if collection not in {
            self.target_collection,
            self.target_metadata_collection,
        }:
            raise MigrationError(f"refusing mutation outside owned target pair: {collection!r}")
        marker = self._load_current_marker()
        if marker is None:
            raise MigrationError("target migration marker disappeared before mutation")
        self._validate_marker_ownership(marker)
        if marker["migration_state"] != expected_state:
            raise MigrationError(
                "target migration state changed before mutation: "
                f"expected state={expected_state!r} "
                f"observed state={marker['migration_state']!r}"
            )
        return marker

    def _delete_collection(
        self,
        name: str,
        *,
        allow_unmarked: bool = False,
        expected_state: str | None = None,
    ) -> None:
        if not allow_unmarked:
            if expected_state is None:
                raise MigrationError("owned collection deletion requires an expected state")
            self._assert_owned_target_mutation(name, expected_state=expected_state)
        try:
            response = self._request(
                "DELETE",
                self._path(name),
                mutation=True,
            )
        except Exception as exc:
            if _status(exc) != 404:
                raise
            return
        if response.get("result") is not True:
            raise MigrationError(f"Qdrant collection deletion did not complete for {name}")

    def _write_points(
        self,
        collection: str,
        points: list[dict[str, Any]],
        *,
        allow_unmarked: bool = False,
        expected_state: str | None = None,
        insert_only: bool = False,
    ) -> None:
        if not points:
            return
        if allow_unmarked:
            self._assert_collection_layout(
                self.target_collection,
                layout=self._layout(
                    self._collection_info(self.source_collection),
                ),
            )
            self._assert_collection_layout(
                self.target_metadata_collection,
                layout=self._layout(self._collection_info(self.source_collection)),
                metadata=True,
            )
            self._assert_empty_collection(self.target_collection)
            self._assert_empty_collection(self.target_metadata_collection)
        else:
            if expected_state is None:
                raise MigrationError("owned point write requires an expected state")
            self._assert_owned_target_mutation(collection, expected_state=expected_state)
        body: dict[str, Any] = {"points": points}
        if insert_only:
            owner_ids = [str(point["id"]) for point in points]
            if len(owner_ids) != len(set(owner_ids)):
                raise MigrationError("insert-only point batch contains duplicate point IDs")
            # Qdrant >=1.16 enforces update_filter per point, preserving an
            # existing owner even when writers race.
            body["update_filter"] = {
                "must_not": [{"has_id": owner_ids}],
            }
        self._request(
            "PUT",
            self._path(collection, "/points"),
            body,
            mutation=True,
        )

    def _write_marker(
        self,
        marker: Mapping[str, Any],
        *,
        allow_unmarked: bool = False,
        expected_state: str | None = None,
    ) -> None:
        points = [
            {
                "id": _META_MARKER_ID,
                "vector": {_META_VECTOR_NAME: [0.0]},
                "payload": dict(marker),
            }
        ]
        self._write_points(
            self.target_metadata_collection,
            points,
            allow_unmarked=allow_unmarked,
            expected_state=expected_state,
        )

    def _write_indexes(
        self,
        schema: Mapping[str, Any],
        indexes: Mapping[str, Mapping[str, Any]],
        *,
        expected_state: str,
    ) -> None:
        for field_name, field_schema in self._expected_payload_indexes(schema, indexes).items():
            self._assert_owned_target_mutation(
                self.target_collection,
                expected_state=expected_state,
            )
            try:
                self._request(
                    "PUT",
                    self._path(self.target_collection, "/index"),
                    {
                        "field_name": field_name,
                        "field_schema": field_schema,
                    },
                    mutation=True,
                )
            except Exception as exc:
                if _status(exc) != 409:
                    raise
        self._wait_collection_ready(
            self.target_collection,
            payload_fields=set(self._expected_payload_indexes(schema, indexes)),
        )
        self._validate_payload_indexes(schema, indexes)

    def _write_sparse_dictionary(self, terms: Iterable[str], *, expected_state: str) -> None:
        term_list = sorted(set(terms))
        if not term_list:
            return
        self._validate_sparse_terms(term_list)
        with self._existing_sparse_dictionary() as existing:
            missing = [
                term
                for term in term_list
                if not existing.has_owner(stable_sparse_index(term))
            ]
        for offset in range(0, len(missing), self.batch_size):
            points = [
                {
                    "id": sparse_owner_point_id(stable_sparse_index(term)),
                    "vector": {_META_VECTOR_NAME: [0.0]},
                    "payload": {
                        _SPARSE_TERM_MARKER: True,
                        "term": term,
                        "index": stable_sparse_index(term),
                        "logical_collection": self.logical_collection,
                        "migration_id": self.migration_id,
                    },
                }
                for term in missing[offset : offset + self.batch_size]
            ]
            self._write_points(
                self.target_metadata_collection,
                points,
                expected_state=expected_state,
                insert_only=True,
            )
        self._assert_sparse_dictionary_complete(term_list)

    @staticmethod
    def _layout_from_plan(plan: MigrationPlan) -> CollectionLayout:
        return CollectionLayout(
            dense_vector_name=plan.dense_vector_name,
            sparse_vector_name=plan.sparse_vector_name,
            vector_dimension=plan.vector_dimension,
            distance=plan.distance,
            dense_datatype=plan.dense_datatype,
            sparse_enabled=plan.sparse_enabled,
            sparse_modifier=plan.sparse_modifier,
            sparse_datatype=plan.sparse_datatype,
        )

    @staticmethod
    def _prepare_reviewed_plan_fields(plan: MigrationPlan) -> dict[str, Any]:
        """Return only inputs that an online prepare must keep pinned."""

        fields = QdrantMigration._reviewed_plan_fields(plan)
        for name in (
            "source_count",
            "source_fingerprint",
            "acl_incomplete_count",
            "sparse_term_count",
            "sparse_term_fingerprint",
        ):
            fields.pop(name, None)
        return fields

    def _target_collection_body(self, layout: CollectionLayout) -> dict[str, Any]:
        return {
            "vectors": {
                layout.dense_vector_name: {
                    "size": layout.vector_dimension,
                    "distance": layout.distance,
                    **(
                        {"datatype": layout.dense_datatype}
                        if layout.dense_datatype is not None
                        else {}
                    ),
                }
            },
            **(
                {
                    "sparse_vectors": {
                        layout.sparse_vector_name: {
                            **(
                                {"modifier": layout.sparse_modifier}
                                if layout.sparse_modifier is not None
                                else {}
                            ),
                            **(
                                {"index": {"datatype": layout.sparse_datatype}}
                                if layout.sparse_datatype is not None
                                else {}
                            ),
                        }
                    }
                }
                if layout.sparse_enabled
                else {}
            ),
        }

    @staticmethod
    def _metadata_collection_body() -> dict[str, Any]:
        return {"vectors": {_META_VECTOR_NAME: {"size": 1, "distance": "Dot"}}}

    def _assert_empty_collection(self, collection: str) -> None:
        if self._count(collection) != 0:
            raise MigrationError(f"unmarked target collection {collection} is not empty")
        if any(self._scroll(collection, with_vectors=False)):
            raise MigrationError(f"unmarked target collection {collection} is not empty")

    def _assert_collection_layout(
        self,
        collection: str,
        *,
        layout: CollectionLayout,
        metadata: bool = False,
    ) -> None:
        if metadata:
            self._validate_metadata_layout(collection)
            return
        actual = self._layout(
            self._collection_info(collection),
            honor_overrides=False,
        )
        if actual != layout:
            raise MigrationError(
                f"target collection layout differs for {collection}: "
                f"expected={layout!r} target={actual!r}"
            )

    def _assert_pre_marker_collection(
        self,
        collection: str,
        *,
        layout: CollectionLayout,
        metadata: bool,
    ) -> None:
        self._assert_collection_layout(
            collection,
            layout=layout,
            metadata=metadata,
        )
        self._assert_empty_collection(collection)
        if metadata and self._load_current_marker() is not None:
            raise MigrationError("target metadata collection already contains a migration marker")

    def _assert_prepare_plan_identity(self, plan: MigrationPlan) -> None:
        expected_identity = {
            "source_collection": self.source_collection,
            "target_collection": self.target_collection,
            "source_metadata_collection": self.source_metadata_collection,
            "target_metadata_collection": self.target_metadata_collection,
            "logical_collection": self.logical_collection,
            "migration_id": self.migration_id,
            "migrator_version": self.migrator_version,
        }
        for field_name, expected in expected_identity.items():
            if getattr(plan, field_name) != expected:
                raise MigrationError(f"reviewed plan {field_name} does not match this migration")
        if plan.batch_size != self.batch_size or not math.isclose(
            plan.timeout_seconds,
            self.timeout_seconds,
            rel_tol=0.0,
            abs_tol=1e-9,
        ):
            raise MigrationError(
                "reviewed plan batch_size or timeout_seconds does not match this migration"
            )
        if plan.sparse_map_fingerprint != _sparse_map_fingerprint(self._sparse_map):
            raise MigrationError(
                "reviewed plan sparse-map fingerprint does not match this migration"
            )

        layout = self._layout(self._collection_info(self.source_collection))
        metadata = self._legacy_metadata()
        self._validate_source_metadata_layout(metadata.schema, layout)
        if layout != self._layout_from_plan(plan):
            raise MigrationError("reviewed plan vector layout does not match the source")
        if _metadata_fingerprint(metadata) != plan.metadata_fingerprint:
            raise MigrationError("reviewed plan metadata fingerprint does not match the source")
        if self._sparse_weight(metadata, sparse_enabled=layout.sparse_enabled) != (
            plan.sparse_weight
        ):
            raise MigrationError("reviewed plan sparse search policy does not match the source")

    def _validate_owned_prepare_marker(
        self,
        *,
        marker: Mapping[str, Any],
        layout: CollectionLayout,
        metadata: LegacyMetadata,
    ) -> None:
        self._validate_marker_ownership(marker)
        state = marker.get("migration_state")
        if state not in {"building", "failed"}:
            raise MigrationError(
                "prepare may resume only a building or failed migration target; "
                f"current state is {state!r}"
            )
        self._validate_metadata_layout(self.target_metadata_collection)
        self._validate_existing_target(
            target_info=(
                self._collection_info(self.target_collection)
                if self._exists(self.target_collection)
                else None
            ),
            marker=marker,
            layout=layout,
            metadata=metadata,
        )

    def _cleanup_pre_marker_orphan(
        self,
        *,
        reviewed_plan: MigrationPlan,
        confirm: bool,
        lock_held: bool,
    ) -> None:
        """Delete only an explicitly reviewed, empty, unmarked target pair."""

        if not confirm:
            raise MigrationError(
                "pre-marker orphan cleanup requires explicit confirm=True / --confirm"
            )
        if not lock_held:
            raise MigrationError(
                "pre-marker orphan cleanup requires external source lock "
                "acknowledgement via lock_held=True / --lock-held"
            )
        if not isinstance(reviewed_plan, MigrationPlan):
            raise MigrationError("pre-marker orphan cleanup requires a reviewed plan")
        if reviewed_plan.target_absent is not True:
            raise MigrationError(
                "pre-marker orphan cleanup requires a reviewed target_absent=True plan"
            )
        self._assert_prepare_plan_identity(reviewed_plan)
        if (
            reviewed_plan.target_collection != self.target_collection
            or reviewed_plan.target_metadata_collection != self.target_metadata_collection
        ):
            raise MigrationError("reviewed plan target names do not match this migration")

        layout = self._layout_from_plan(reviewed_plan)
        names = (
            (self.target_collection, False),
            (self.target_metadata_collection, True),
        )

        def recheck_pair() -> None:
            for collection, is_metadata in names:
                if not self._exists(collection):
                    continue
                self._assert_pre_marker_collection(
                    collection,
                    layout=layout,
                    metadata=is_metadata,
                )

        recheck_pair()
        for collection, _is_metadata in names:
            if not self._exists(collection):
                continue
            # Recheck immediately before each delete; a marker or point that
            # appears after the first check is never adopted or removed.
            recheck_pair()
            if not self._exists(collection):
                continue
            self._delete_collection(collection, allow_unmarked=True)

    def _create_target_pair(
        self,
        layout: CollectionLayout,
        metadata: LegacyMetadata,
    ) -> None:
        """Create or safely resume the two empty physical target collections."""

        marker_before = self._load_current_marker()
        if marker_before is not None:
            self._validate_owned_prepare_marker(
                marker=marker_before,
                layout=layout,
                metadata=metadata,
            )
        created_by_attempt: list[tuple[str, bool]] = []
        try:
            definitions = (
                (
                    self.target_collection,
                    self._target_collection_body(layout),
                    False,
                ),
                (
                    self.target_metadata_collection,
                    self._metadata_collection_body(),
                    True,
                ),
            )
            for collection, body, is_metadata in definitions:
                if self._exists(collection):
                    if marker_before is None:
                        raise MigrationError(
                            f"unmarked target collection already exists: {collection}"
                        )
                    continue
                try:
                    created = self._create_collection(collection, body)
                except Exception as exc:
                    if _status(exc) == 409 or _status(exc.__cause__) == 409:
                        created = False
                    else:
                        raise
                if not created:
                    marker = self._load_current_marker()
                    if marker is None:
                        raise MigrationError(
                            f"target collection appeared without an owned migration "
                            f"marker: {collection}"
                        )
                    self._validate_owned_prepare_marker(
                        marker=marker,
                        layout=layout,
                        metadata=metadata,
                    )
                    marker_before = marker
                    continue
                self._wait_collection_ready(collection)
                self._assert_pre_marker_collection(
                    collection,
                    layout=layout,
                    metadata=is_metadata,
                )
                created_by_attempt.append((collection, is_metadata))

            self._wait_collection_ready(self.target_collection)
            self._wait_collection_ready(self.target_metadata_collection)
            self._assert_collection_layout(
                self.target_collection,
                layout=layout,
            )
            self._assert_collection_layout(
                self.target_metadata_collection,
                layout=layout,
                metadata=True,
            )
            if marker_before is None:
                self._assert_empty_collection(self.target_collection)
                self._assert_empty_collection(self.target_metadata_collection)
        except BaseException:
            if marker_before is None:
                try:
                    if self._load_current_marker() is None:
                        for collection, is_metadata in reversed(created_by_attempt):
                            if not self._exists(collection):
                                continue
                            try:
                                self._assert_pre_marker_collection(
                                    collection,
                                    layout=layout,
                                    metadata=is_metadata,
                                )
                                self._delete_collection(collection, allow_unmarked=True)
                            except BaseException:
                                # The pair is no longer provably ours.
                                pass
                except BaseException:
                    pass
            raise

    def prepare(
        self,
        *,
        confirm: bool,
        plan: MigrationPlan,
        allow_acl_fail_open: bool = False,
        lock_held: bool = False,
    ) -> dict[str, Any]:
        """Safely create/resume an owned building target without copying data."""

        if not confirm:
            raise MigrationError("prepare requires explicit confirm=True / --confirm")
        if not lock_held:
            raise MigrationError(
                "prepare requires external source lock acknowledgement via "
                "lock_held=True / --lock-held"
            )
        if not isinstance(plan, MigrationPlan):
            raise MigrationError("prepare requires a reviewed migration plan")

        target_exists = self._exists(self.target_collection)
        target_metadata_exists = self._exists(self.target_metadata_collection)
        existing_marker = self._load_current_marker() if target_metadata_exists else None
        if (target_exists or target_metadata_exists) and existing_marker is None:
            if not plan.target_absent:
                raise MigrationError(
                    "unmarked target exists without a reviewed target_absent=True plan"
                )
            self._cleanup_pre_marker_orphan(
                reviewed_plan=plan,
                confirm=confirm,
                lock_held=lock_held,
            )

        current_plan = self.preflight()
        if self._prepare_reviewed_plan_fields(plan) != self._prepare_reviewed_plan_fields(
            current_plan
        ):
            raise MigrationError("provided migration plan is stale; rerun preflight before prepare")
        if not plan.target_absent and current_plan.target_absent:
            raise MigrationError("reviewed target disappeared; rerun preflight before prepare")
        if current_plan.acl_incomplete_count and not allow_acl_fail_open:
            raise MigrationError(
                f"{current_plan.acl_incomplete_count} records lack ACL fields; "
                "refusing prepare without --allow-acl-fail-open"
            )

        layout = self._layout_from_plan(current_plan)
        metadata = self._legacy_metadata()
        self._validate_source_metadata_layout(metadata.schema, layout)
        existing_marker = self._load_current_marker()
        if existing_marker is not None and existing_marker.get("migration_state") == "ready":
            self._validate_marker_ownership(existing_marker)
            self._validate_metadata_layout(self.target_metadata_collection)
            self._validate_existing_target(
                target_info=self._collection_info(self.target_collection),
                marker=existing_marker,
                layout=layout,
                metadata=metadata,
            )
            self._assert_sparse_dictionary_complete(self._sparse_map.values())
            return existing_marker
        self._create_target_pair(layout, metadata)

        marker = self._load_current_marker()
        if marker is None:
            # This is the only path that writes the first marker.  The exact
            # empty/layout checks are repeated after collection creation so an
            # unmarked race cannot be adopted.
            self._assert_collection_layout(
                self.target_collection,
                layout=layout,
            )
            self._assert_collection_layout(
                self.target_metadata_collection,
                layout=layout,
                metadata=True,
            )
            self._assert_empty_collection(self.target_collection)
            self._assert_empty_collection(self.target_metadata_collection)
            marker = self._marker_payload(
                layout=layout,
                metadata=metadata,
                sparse_weight=current_plan.sparse_weight,
                source_fingerprint=current_plan.source_fingerprint,
                metadata_fingerprint=current_plan.metadata_fingerprint,
                sparse_map_fingerprint=current_plan.sparse_map_fingerprint,
                setup_complete=False,
                acl_incomplete_count=current_plan.acl_incomplete_count,
                sparse_term_count=current_plan.sparse_term_count,
                sparse_term_fingerprint=current_plan.sparse_term_fingerprint,
                source_count=current_plan.source_count,
                target_count=current_plan.target_count,
            )
            try:
                self._write_marker(marker, allow_unmarked=True)
            except BaseException:
                try:
                    self._cleanup_pre_marker_orphan(
                        reviewed_plan=plan,
                        confirm=confirm,
                        lock_held=lock_held,
                    )
                except BaseException:
                    pass
                raise
        else:
            self._validate_owned_prepare_marker(
                marker=marker,
                layout=layout,
                metadata=metadata,
            )
            updated = dict(marker)
            updated.update(
                {
                    "migration_state": "building",
                    "setup_complete": False,
                    "source_fingerprint": current_plan.source_fingerprint,
                    "source_count": current_plan.source_count,
                    "acl_incomplete_count": current_plan.acl_incomplete_count,
                    "sparse_term_count": current_plan.sparse_term_count,
                    "sparse_term_fingerprint": current_plan.sparse_term_fingerprint,
                    "target_count": current_plan.target_count,
                }
            )
            if updated != marker:
                self._write_marker(updated, expected_state=marker["migration_state"])

        marker = self._load_current_marker()
        if marker is None:
            raise MigrationError("target marker disappeared after prepare")
        self._validate_owned_prepare_marker(
            marker=marker,
            layout=layout,
            metadata=metadata,
        )
        marker_schema = marker.get("schema")
        marker_indexes = marker.get("indexes")
        if not isinstance(marker_schema, Mapping) or not isinstance(marker_indexes, Mapping):
            raise MigrationError("target marker has invalid index metadata")
        self._write_indexes(
            marker_schema,
            marker_indexes,
            expected_state="building",
        )
        self._write_sparse_dictionary(
            self._sparse_map.values(),
            expected_state="building",
        )
        self._wait_collection_ready(self.target_collection)
        self._wait_collection_ready(self.target_metadata_collection)
        final_marker = self._load_current_marker()
        if final_marker is None:
            raise MigrationError("target marker disappeared during prepare")
        self._validate_owned_prepare_marker(
            marker=final_marker,
            layout=layout,
            metadata=metadata,
        )
        return final_marker

    @staticmethod
    def _assert_source_snapshot(
        expected: MigrationPlan,
        actual: SourceSnapshot,
        *,
        phase: str,
    ) -> None:
        if (
            actual.source_count != expected.source_count
            or actual.fingerprint != expected.source_fingerprint
            or actual.acl_incomplete_count != expected.acl_incomplete_count
            or actual.sparse_term_count != expected.sparse_term_count
            or actual.sparse_term_fingerprint != expected.sparse_term_fingerprint
        ):
            raise MigrationError(
                f"source changed {phase}; rerun preflight with source writes frozen"
            )

    @staticmethod
    def _reviewed_plan_fields(plan: MigrationPlan) -> dict[str, Any]:
        fields = plan.to_dict()
        for name in (
            "target_count",
            "target_absent",
            "target_state",
        ):
            fields.pop(name, None)
        return fields

    @staticmethod
    def _assert_target_vectors(
        point: Mapping[str, Any],
        expected: Mapping[str, Any],
        *,
        exact: bool = True,
        sparse_dictionary: _SparseDictionaryManifest | None = None,
    ) -> None:
        actual_vectors = point.get("vector")
        if actual_vectors is None:
            actual_vectors = point.get("vectors")
        expected_vectors = expected.get("vector")
        if not isinstance(actual_vectors, Mapping) or not isinstance(expected_vectors, Mapping):
            raise MigrationError(f"target point {point.get('id')!r} has an invalid vector payload")
        if set(actual_vectors) != set(expected_vectors):
            raise MigrationError(
                f"target point {point.get('id')!r} vector names differ: "
                f"expected={sorted(expected_vectors)!r} "
                f"target={sorted(actual_vectors)!r}"
            )
        for name, expected_value in expected_vectors.items():
            actual_value = actual_vectors.get(name)
            if isinstance(expected_value, list):
                actual_normalized = _finite_vector(
                    actual_value,
                    field_name=f"target point {point.get('id')!r} vector {name!r}",
                    dimension=len(expected_value),
                )
                expected_normalized = _finite_vector(
                    expected_value,
                    field_name=f"expected point vector {name!r}",
                )
                actual_normalized = [
                    _canonical_float32(
                        value,
                        field_name=f"target point {point.get('id')!r} vector {name!r}",
                    )
                    for value in actual_normalized
                ]
                expected_normalized = [
                    _canonical_float32(
                        value,
                        field_name=f"expected point vector {name!r}",
                    )
                    for value in expected_normalized
                ]
                if exact and actual_normalized != expected_normalized:
                    raise MigrationError(
                        f"target point {point.get('id')!r} vector {name!r} differs"
                    )
                continue
            if not isinstance(expected_value, Mapping) or not isinstance(actual_value, Mapping):
                raise MigrationError(f"target point {point.get('id')!r} vector {name!r} differs")
            if set(actual_value) != {"indices", "values"}:
                raise MigrationError(
                    f"target point {point.get('id')!r} sparse vector {name!r} is malformed"
                )
            actual_indices = actual_value.get("indices")
            actual_values = actual_value.get("values")
            expected_indices = expected_value.get("indices")
            expected_values = expected_value.get("values")
            if (
                not isinstance(actual_indices, list)
                or not isinstance(actual_values, list)
                or len(actual_indices) != len(actual_values)
                or not isinstance(expected_indices, list)
                or not isinstance(expected_values, list)
                or len(expected_indices) != len(expected_values)
            ):
                raise MigrationError(
                    f"target point {point.get('id')!r} sparse vector {name!r} is malformed"
                )
            actual_index_values = [
                _sparse_index(
                    index,
                    field_name=f"target point {point.get('id')!r} sparse index",
                )
                for index in actual_indices
            ]
            expected_index_values = [
                _sparse_index(
                    index,
                    field_name=f"expected point sparse index {name!r}",
                )
                for index in expected_indices
            ]
            if sparse_dictionary is not None:
                missing_dictionary_indexes = sorted(
                    index
                    for index in set(actual_index_values)
                    if not sparse_dictionary.has_index(index)
                )
                if missing_dictionary_indexes:
                    raise MigrationError(
                        f"target point {point.get('id')!r} sparse vector {name!r} "
                        "contains indexes missing from the target sparse dictionary: "
                        f"{missing_dictionary_indexes!r}"
                    )
            try:
                actual_weight_values = [
                    _canonical_float32(
                        value,
                        field_name=(f"target point {point.get('id')!r} sparse vector {name!r}"),
                    )
                    for value in actual_values
                ]
                expected_weight_values = [
                    _canonical_float32(
                        value,
                        field_name=f"expected point sparse vector {name!r}",
                    )
                    for value in expected_values
                ]
            except MigrationError:
                raise
            except (TypeError, ValueError) as exc:
                raise MigrationError(
                    f"target point {point.get('id')!r} sparse vector {name!r} is malformed"
                ) from exc
            if (
                len(set(actual_index_values)) != len(actual_index_values)
                or not all(math.isfinite(value) for value in actual_weight_values)
                or (
                    exact
                    and (
                        actual_index_values != expected_index_values
                        or actual_weight_values != expected_weight_values
                    )
                )
            ):
                raise MigrationError(
                    f"target point {point.get('id')!r} sparse vector {name!r} differs"
                )

    def _verify_point_pair(
        self,
        source_point: Mapping[str, Any],
        target_point: Mapping[str, Any],
        schema: Mapping[str, Any],
        layout: CollectionLayout,
        allow_acl_fail_open: bool,
    ) -> None:
        """Compare one transformed source point with its target point exactly."""

        source_payload = source_point.get("payload")
        if not isinstance(source_payload, Mapping):
            raise MigrationError(f"source point {source_point.get('id')!r} has an invalid payload")
        if "uri_depth" in source_payload and "scope_roots" in source_payload:
            expected = {
                "id": source_point.get("id"),
                "vector": source_point.get("vector", source_point.get("vectors")),
                "payload": dict(source_payload),
            }
        else:
            _, expected, _ = self._transform_point(
                source_point,
                layout=layout,
                schema=schema,
            )

        expected_id = expected.get("id")
        actual_id = target_point.get("id")
        if expected_id is None:
            raise MigrationError(f"target point {actual_id!r} has no expected target id")
        actual_id = _canonical_uuid_string(
            actual_id,
            field_name=f"target point {actual_id!r} physical id",
        )
        if actual_id != expected_id:
            raise MigrationError(
                f"target point {actual_id!r} has the wrong target id; expected={expected_id!r}"
            )
        expected_payload = expected.get("payload")
        actual_payload = target_point.get("payload")
        if not isinstance(expected_payload, Mapping) or not isinstance(actual_payload, Mapping):
            raise MigrationError(f"target point {actual_id!r} has an invalid payload")
        original_id = actual_payload.get(_ORIGINAL_ID_FIELD)
        expected_original_id = expected_payload.get(_ORIGINAL_ID_FIELD)
        if original_id is None or expected_original_id is None:
            raise MigrationError(f"target point {actual_id!r} is missing {_ORIGINAL_ID_FIELD}")
        if str(original_id) != str(expected_original_id):
            raise MigrationError(f"target point {actual_id!r} has the wrong original id")
        self._validate_target_payload(
            actual_payload,
            expected_payload,
            schema=schema,
            allow_acl_fail_open=allow_acl_fail_open,
            point_id=str(actual_id),
        )
        if not _typed_equal(actual_payload, expected_payload):
            raise MigrationError(f"target point {actual_id!r} payload differs from copied source")
        self._assert_target_vectors(target_point, expected, exact=True)

    def _validate_final_target(
        self,
        *,
        source: SourceSnapshot,
        layout: CollectionLayout,
        schema: Mapping[str, Any],
        allow_acl_fail_open: bool,
    ) -> tuple[int, str, str]:
        target_count = self._count(self.target_collection)
        with self._existing_sparse_dictionary() as sparse_by_index:
            with _ScanManifest() as manifest:
                target_scanned = self._scan_target_ids(manifest)
                if target_scanned != target_count:
                    raise MigrationError(
                        "target count mismatch after copy: "
                        f"exact count={target_count} paginated count={target_scanned}"
                    )
                pending: list[dict[str, Any]] = []

                def validate_batch(points: list[dict[str, Any]]) -> None:
                    actual_points = {
                        _canonical_uuid_string(
                            point.get("id"),
                            field_name="target physical point id",
                        ): point
                        for point in self._retrieve(
                            self.target_collection,
                            [str(point["id"]) for point in points],
                            with_vectors=True,
                        )
                        if point.get("id") is not None
                    }
                    for expected in points:
                        target_id = str(expected["id"])
                        actual = actual_points.get(target_id)
                        if actual is None:
                            raise MigrationError(
                                f"target records differ after copy: missing={target_id!r}"
                            )
                        self._verify_point_pair(
                            expected,
                            actual,
                            schema=schema,
                            layout=layout,
                            allow_acl_fail_open=allow_acl_fail_open,
                        )
                        self._assert_target_vectors(
                            actual,
                            expected,
                            exact=True,
                            sparse_dictionary=sparse_by_index,
                        )
                        manifest.set_target_content_fingerprint(
                            target_id,
                            _canonical_point_content_fingerprint(actual),
                        )
                        manifest.delete_target(target_id)

                def validate_point(expected: dict[str, Any]) -> None:
                    pending.append(expected)
                    if len(pending) >= self.batch_size:
                        validate_batch(pending)
                        pending.clear()

                actual_source = self._scan_source(
                    layout=layout,
                    schema=schema,
                    manifest=manifest,
                    point_callback=validate_point,
                )
                # The caller already validated the physical layout; this summary
                # is only used to guard the stream against source drift.
                if (
                    actual_source.source_count != source.source_count
                    or actual_source.fingerprint != source.fingerprint
                    or actual_source.acl_incomplete_count != source.acl_incomplete_count
                    or actual_source.sparse_term_count != source.sparse_term_count
                    or actual_source.sparse_term_fingerprint != source.sparse_term_fingerprint
                ):
                    raise MigrationError("source changed final verification")
                if pending:
                    validate_batch(pending)
                extra = manifest.first_extra_target()
                if extra is not None:
                    raise MigrationError(f"target records differ after copy: extras={extra!r}")
                transformed_source_fingerprint = manifest.transformed_source_fingerprint()
                target_content_fingerprint = manifest.target_content_fingerprint()
        self._assert_sparse_dictionary_complete(self._sparse_map.values())
        return (
            target_count,
            transformed_source_fingerprint,
            target_content_fingerprint,
        )

    @staticmethod
    def _validate_target_payload(
        payload: Mapping[str, Any],
        expected_payload: Mapping[str, Any],
        *,
        schema: Mapping[str, Any],
        allow_acl_fail_open: bool,
        point_id: str,
    ) -> None:
        """Validate security-sensitive payload fields without clobbering newer data."""

        _assert_security_payload(payload, expected_payload, point_id=point_id)
        uri = payload.get("uri")
        normalized_uri = _normalize_uri(uri, field_name=f"target point {point_id!r} uri")
        if uri != normalized_uri:
            raise MigrationError(f"target point {point_id!r} uri is not canonical")
        uri_depth = payload.get("uri_depth")
        if (
            isinstance(uri_depth, bool)
            or not isinstance(uri_depth, int)
            or uri_depth != _path_depth(normalized_uri)
        ):
            raise MigrationError(f"target point {point_id!r} has invalid uri_depth")
        scope_roots = payload.get("scope_roots")
        if scope_roots != _scope_roots(normalized_uri):
            raise MigrationError(f"target point {point_id!r} has invalid scope_roots")

        schema_fields = {
            str(field.get("FieldName"))
            for field in schema.get("Fields", [])
            if isinstance(field, Mapping) and field.get("FieldName")
        }
        for identity_field in ("account_id",):
            if identity_field in expected_payload or identity_field in schema_fields:
                value = payload.get(identity_field)
                if not isinstance(value, str) or not value.strip():
                    raise MigrationError(f"target point {point_id!r} is missing {identity_field}")
        expected_owner = owner_fields_for_uri(f"viking://{normalized_uri.lstrip('/')}").get(
            "owner_user_id"
        )
        actual_owner = payload.get("owner_user_id")
        if expected_owner is not None and actual_owner != expected_owner:
            raise MigrationError(f"target point {point_id!r} owner_user_id does not match uri")

        expected_acl_fields = _ACL_FIELDS & set(expected_payload)
        actual_acl_fields = _ACL_FIELDS & set(payload)
        if (
            len(expected_acl_fields) == len(_ACL_FIELDS) and not _acl_complete(expected_payload)
        ) or (len(actual_acl_fields) == len(_ACL_FIELDS) and not _acl_complete(payload)):
            if not allow_acl_fail_open:
                raise MigrationError(f"target point {point_id!r} has malformed ACL fields")
        if not _acl_complete(expected_payload) and not allow_acl_fail_open:
            raise MigrationError(f"target point {point_id!r} lacks complete ACL fields")

    def _verify_target_marker(
        self,
        marker: Mapping[str, Any],
        *,
        expected_state: str | None = None,
    ) -> None:
        """Validate marker ownership and the requested state without writing."""

        if not isinstance(marker, Mapping):
            raise MigrationError("target current marker is malformed")
        self._validate_marker_ownership(marker)
        state = marker.get("migration_state")
        if expected_state is not None and state != expected_state:
            raise MigrationError(f"target current marker must be {expected_state!r}; got {state!r}")
        if state == "rolled_back" and marker.get("setup_complete") is True:
            raise MigrationError("rolled_back target cannot be serving-ready")

    def _revalidate_verify_state(
        self,
        *,
        marker: Mapping[str, Any],
        layout: CollectionLayout,
        metadata_fingerprint: str,
        sparse_map_fingerprint: str,
        target_count: int,
    ) -> None:
        """Re-read pinned physical state immediately before publication."""

        fresh_metadata = self._legacy_metadata()
        self._validate_source_metadata_layout(fresh_metadata.schema, layout)
        if _metadata_fingerprint(fresh_metadata) != metadata_fingerprint:
            raise MigrationError("legacy metadata changed during verification")
        if _sparse_map_fingerprint(self._sparse_map) != sparse_map_fingerprint:
            raise MigrationError("authoritative sparse map changed during verification")
        self._assert_source_layout(layout, phase="final verification")
        self._validate_metadata_layout(self.target_metadata_collection)
        target_info = self._collection_info(self.target_collection)
        self._validate_existing_target(
            target_info=target_info,
            marker=marker,
            layout=layout,
            metadata=fresh_metadata,
        )
        expected_payload_fields = set(
            self._expected_payload_indexes(
                fresh_metadata.schema,
                fresh_metadata.indexes,
            )
        )
        self._validate_payload_indexes(
            fresh_metadata.schema,
            fresh_metadata.indexes,
        )
        self._wait_collection_ready(
            self.target_collection,
            payload_fields=expected_payload_fields,
        )
        self._wait_collection_ready(self.target_metadata_collection)
        self._assert_sparse_dictionary_complete(self._sparse_map.values())
        if self._count(self.target_collection) != target_count:
            raise MigrationError("target count changed during verification")

    def verify(
        self,
        *,
        plan: MigrationPlan,
        allow_acl_fail_open: bool = False,
        final: bool = False,
        barrier_held: bool = False,
        confirm: bool = False,
        lock_held: bool = False,
    ) -> dict[str, Any]:
        """Stream an exact source/target audit and publish readiness once."""

        if not isinstance(plan, MigrationPlan):
            raise MigrationError("verify requires a reviewed migration plan")
        if final and not barrier_held:
            raise MigrationError("final verify requires barrier_held=True / --barrier-held")
        if final and not confirm:
            raise MigrationError("final verify requires explicit confirm=True / --confirm")
        if final and not lock_held:
            raise MigrationError(
                "final verify requires external source lock acknowledgement via "
                "lock_held=True / --lock-held"
            )
        self._assert_prepare_plan_identity(plan)
        layout = self._layout_from_plan(plan)
        metadata = self._legacy_metadata()
        self._validate_source_metadata_layout(metadata.schema, layout)
        metadata_fingerprint = _metadata_fingerprint(metadata)
        sparse_map_fingerprint = _sparse_map_fingerprint(self._sparse_map)
        if metadata_fingerprint != plan.metadata_fingerprint:
            raise MigrationError("legacy metadata fingerprint differs from the reviewed plan")
        if sparse_map_fingerprint != plan.sparse_map_fingerprint:
            raise MigrationError(
                "authoritative sparse map fingerprint differs from the reviewed plan"
            )

        marker = self._load_current_marker()
        if marker is None:
            raise MigrationError("verify requires an owned target marker")
        self._verify_target_marker(
            marker,
            expected_state="cutting_over" if final else None,
        )
        state = marker["migration_state"]
        if final:
            if marker.get("setup_complete") is not True:
                raise MigrationError(
                    "final verify requires a cutting_over target with setup_complete=true"
                )
        elif state == "cutting_over":
            raise MigrationError("cutting_over verification requires final=True")
        elif state not in {"building", "ready", "failed", "active", "retained", "rolled_back"}:
            raise MigrationError(f"verify does not support target state {state!r}")

        self._validate_metadata_layout(self.target_metadata_collection)
        target_info = self._collection_info(self.target_collection)
        self._validate_existing_target(
            target_info=target_info,
            marker=marker,
            layout=layout,
            metadata=metadata,
        )
        expected_payload_fields = set(
            self._expected_payload_indexes(
                metadata.schema,
                metadata.indexes,
            )
        )
        self._validate_payload_indexes(metadata.schema, metadata.indexes)
        self._wait_collection_ready(
            self.target_collection,
            payload_fields=expected_payload_fields,
        )
        self._wait_collection_ready(self.target_metadata_collection)
        self._assert_sparse_dictionary_complete(self._sparse_map.values())

        marker_source = SourceSnapshot(
            source_count=marker["source_count"],
            fingerprint=marker["source_fingerprint"],
            acl_incomplete_count=marker["acl_incomplete_count"],
            sparse_term_count=marker["sparse_term_count"],
            sparse_term_fingerprint=marker["sparse_term_fingerprint"],
        )
        (
            target_count,
            transformed_source_fingerprint,
            target_content_fingerprint,
        ) = self._validate_final_target(
            source=marker_source,
            layout=layout,
            schema=metadata.schema,
            allow_acl_fail_open=allow_acl_fail_open,
        )

        current = self._load_current_marker()
        if current is None:
            raise MigrationError("target current marker disappeared during verify")
        self._verify_target_marker(
            current,
            expected_state="cutting_over" if final else state,
        )
        if (
            current.get("metadata_fingerprint") != metadata_fingerprint
            or current.get("sparse_map_fingerprint") != sparse_map_fingerprint
            or current.get("source_count") != marker_source.source_count
            or current.get("source_fingerprint") != marker_source.fingerprint
            or current.get("acl_incomplete_count") != marker_source.acl_incomplete_count
            or current.get("sparse_term_count") != marker_source.sparse_term_count
            or current.get("sparse_term_fingerprint") != marker_source.sparse_term_fingerprint
        ):
            raise MigrationError("target marker changed during verification")
        if transformed_source_fingerprint != target_content_fingerprint:
            raise MigrationError(
                "canonical transformed-source and target content fingerprints differ"
            )

        self._revalidate_verify_state(
            marker=current,
            layout=layout,
            metadata_fingerprint=metadata_fingerprint,
            sparse_map_fingerprint=sparse_map_fingerprint,
            target_count=target_count,
        )
        latest = self._load_current_marker()
        if latest is None:
            raise MigrationError("target current marker disappeared before publication")
        self._verify_target_marker(
            latest,
            expected_state="cutting_over" if final else state,
        )
        for field_name in (
            "metadata_fingerprint",
            "sparse_map_fingerprint",
            "source_count",
            "source_fingerprint",
            "acl_incomplete_count",
            "sparse_term_count",
            "sparse_term_fingerprint",
        ):
            if latest.get(field_name) != current.get(field_name):
                raise MigrationError("target marker changed during final verification")
        current = latest
        if current["migration_state"] in {
            "ready",
            "active",
            "retained",
            "rolled_back",
        }:
            if current.get("target_count") != target_count:
                raise MigrationError(
                    "target marker target count differs from the audited target count"
                )
            if (
                current.get("transformed_source_fingerprint") != transformed_source_fingerprint
                or current.get("target_content_fingerprint") != target_content_fingerprint
            ):
                raise MigrationError(
                    "target marker content fingerprints differ from the audited target"
                )

        result_state = state
        if state == "building" and not final:
            if not confirm:
                raise MigrationError(
                    "verify requires explicit confirm=True / --confirm when publishing ready"
                )
            if not lock_held:
                raise MigrationError(
                    "verify requires external source lock acknowledgement via "
                    "lock_held=True / --lock-held when publishing ready"
                )
            updated = dict(current)
            updated.update(
                {
                    "migration_state": "ready",
                    "setup_complete": True,
                    "target_count": target_count,
                    "verification_complete": True,
                    "verified_source_fingerprint": marker_source.fingerprint,
                    "transformed_source_fingerprint": transformed_source_fingerprint,
                    "target_content_fingerprint": target_content_fingerprint,
                }
            )
            self._write_marker(updated, expected_state=state)
            result_state = "ready"
        elif final:
            updated = dict(current)
            updated.update(
                {
                    "migration_state": "cutting_over",
                    "setup_complete": True,
                    "target_count": target_count,
                    "verification_complete": True,
                    "verified_source_fingerprint": marker_source.fingerprint,
                    "transformed_source_fingerprint": transformed_source_fingerprint,
                    "target_content_fingerprint": target_content_fingerprint,
                }
            )
            self._write_marker(updated, expected_state="cutting_over")
            result_state = "cutting_over"

        verified = self._load_current_marker()
        if verified is None:
            raise MigrationError("target current marker disappeared after verify")
        try:
            self._verify_target_marker(verified, expected_state=result_state)
        except MigrationError as exc:
            raise MigrationError(f"target completion marker was not persisted: {exc}") from exc
        if verified.get("setup_complete") is not (
            result_state in {"ready", "cutting_over", "active", "retained"}
        ):
            raise MigrationError("target verification marker has an invalid setup gate")
        return {
            "source_count": marker_source.source_count,
            "target_count": target_count,
            "target_collection": self.target_collection,
            "migration_state": result_state,
            "verification_complete": True,
            "transformed_source_fingerprint": transformed_source_fingerprint,
            "target_content_fingerprint": target_content_fingerprint,
        }

    @staticmethod
    def _validate_deployment_hooks(hooks: Any) -> None:
        if hooks is None:
            raise MigrationError("deployment hooks are required for this lifecycle operation")
        missing = [
            name for name in _DEPLOYMENT_HOOK_NAMES if not callable(getattr(hooks, name, None))
        ]
        if missing:
            raise MigrationError(f"deployment hooks are missing required operations: {missing!r}")

    @staticmethod
    def _hook_bool(value: Any, *, operation: str) -> bool:
        if not isinstance(value, bool):
            raise MigrationError(f"deployment hook {operation!r} must return a boolean")
        return value

    def cutover(
        self,
        *,
        confirm: bool,
        plan: MigrationPlan,
        barrier_held: bool = False,
        hooks: Any,
        allow_acl_fail_open: bool = False,
        lock_held: bool = False,
        resume: bool = False,
    ) -> dict[str, Any]:
        """Cut over an owned ready target while the operator holds the barrier."""

        if not confirm:
            raise MigrationError("cutover requires explicit confirm=True / --confirm")
        if not barrier_held:
            raise MigrationError("cutover requires barrier_held=True / --barrier-held")
        if not lock_held:
            raise MigrationError(
                "cutover requires external source lock acknowledgement via "
                "lock_held=True / --lock-held"
            )
        if not isinstance(plan, MigrationPlan):
            raise MigrationError("cutover requires a reviewed migration plan")
        self._validate_deployment_hooks(hooks)
        self._assert_prepare_plan_identity(plan)

        marker = self._load_current_marker()
        if marker is None:
            raise MigrationError("cutover requires an owned target marker")
        self._validate_marker_ownership(marker)
        state = marker["migration_state"]
        if state == "ready":
            if resume:
                raise MigrationError("cutover --resume requires a cutting_over target")
            self._transition("cutting_over")
        elif state == "cutting_over":
            if not resume:
                raise MigrationError(
                    "cutover requires --resume for an interrupted cutting_over target"
                )
        else:
            raise MigrationError(
                "cutover requires a ready target or an interrupted cutting_over target; "
                f"current state is {state!r}"
            )

        if resume:
            accepted = self._hook_bool(
                hooks.current_target_has_accepted_writes(self),
                operation="current_target_has_accepted_writes",
            )
            if accepted:
                raise MigrationError(
                    "resumed cutover refuses to overwrite accepted current-format writes"
                )
            hooks.remove_current_from_serving_path(self)
            hooks.assert_target_not_served(self)

        hooks.drain_legacy_writes(self)
        hooks.remove_legacy_from_serving_path(self)
        self.reconcile(
            confirm=True,
            plan=plan,
            barrier_held=True,
            allow_acl_fail_open=allow_acl_fail_open,
            lock_held=True,
        )
        self.verify(
            plan=plan,
            allow_acl_fail_open=allow_acl_fail_open,
            final=True,
            barrier_held=True,
            confirm=True,
            lock_held=True,
        )
        hooks.rollout_current(self)
        hooks.wait_current_ready(self)
        hooks.smoke_current_read_only(self)
        accepted = self._hook_bool(
            hooks.current_target_has_accepted_writes(self),
            operation="current_target_has_accepted_writes",
        )
        if accepted:
            raise MigrationError(
                "cutover refuses to publish active after accepted current-format writes"
            )
        active = self._transition("active")
        return {
            "migration_state": active["migration_state"],
            "target_collection": self.target_collection,
            "barrier_held": True,
            "writes_resumed": False,
        }

    def rollback(
        self,
        *,
        confirm: bool,
        barrier_held: bool = False,
        no_current_format_writes_accepted: bool = False,
        hooks: Any,
        lock_held: bool = False,
    ) -> dict[str, Any]:
        """Restore the legacy serving path without deleting either collection."""

        if not confirm:
            raise MigrationError("rollback requires explicit confirm=True / --confirm")
        if not barrier_held:
            raise MigrationError("rollback requires barrier_held=True / --barrier-held")
        if not lock_held:
            raise MigrationError(
                "rollback requires external source lock acknowledgement via "
                "lock_held=True / --lock-held"
            )
        if not no_current_format_writes_accepted:
            raise MigrationError("rollback requires --no-current-format-writes-accepted")
        self._validate_deployment_hooks(hooks)

        marker = self._load_current_marker()
        if marker is None:
            raise MigrationError("rollback requires an owned target marker")
        self._validate_marker_ownership(marker)
        state = marker["migration_state"]
        if state not in {"cutting_over", "active"}:
            raise MigrationError(
                f"rollback requires a cutting_over or active target; current state is {state!r}"
            )
        accepted = self._hook_bool(
            hooks.current_target_has_accepted_writes(self),
            operation="current_target_has_accepted_writes",
        )
        if accepted:
            raise MigrationError("rollback refuses after accepted current-format writes")
        hooks.remove_current_from_serving_path(self)
        hooks.assert_target_not_served(self)
        hooks.restore_legacy(self)
        hooks.verify_legacy_read_path(self)
        rolled_back = self._transition(
            "rolled_back",
            allow_active_rollback=state == "active",
        )
        return {
            "migration_state": rolled_back["migration_state"],
            "target_collection": self.target_collection,
            "source_collection": self.source_collection,
            "barrier_held": True,
            "writes_resumed": False,
        }

    def _validate_retire_pair(
        self,
        *,
        plan: MigrationPlan,
        expected_state: str,
        target_exists: bool,
    ) -> dict[str, Any]:
        """Recheck ownership and physical identity immediately before deletion."""

        self._assert_prepare_plan_identity(plan)
        marker = self._load_current_marker()
        if marker is None:
            raise MigrationError("retire requires an owned target marker")
        self._validate_marker_ownership(marker)
        if marker.get("migration_state") != expected_state:
            raise MigrationError(f"retire target state changed from {expected_state!r}")
        layout = self._layout_from_plan(plan)
        metadata = self._legacy_metadata()
        self._validate_source_metadata_layout(metadata.schema, layout)
        self._validate_metadata_layout(self.target_metadata_collection)
        self._validate_existing_target(
            target_info=(self._collection_info(self.target_collection) if target_exists else None),
            marker=marker,
            layout=layout,
            metadata=metadata,
        )
        return marker

    def retire(
        self,
        *,
        confirm: bool,
        plan: MigrationPlan,
        lock_held: bool = False,
        hooks: Any,
    ) -> dict[str, Any]:
        """Retain the audit marker, then remove only the non-serving target pair."""

        if not confirm:
            raise MigrationError("retire requires explicit confirm=True / --confirm")
        if not lock_held:
            raise MigrationError(
                "retire requires external source lock acknowledgement via "
                "lock_held=True / --lock-held"
            )
        if not isinstance(plan, MigrationPlan):
            raise MigrationError("retire requires a reviewed migration plan")
        self._validate_deployment_hooks(hooks)
        self._assert_prepare_plan_identity(plan)

        target_exists = self._exists(self.target_collection)
        metadata_exists = self._exists(self.target_metadata_collection)
        marker = self._load_current_marker() if metadata_exists else None
        if marker is None:
            if not plan.target_absent:
                raise MigrationError(
                    "retire requires an owned marker or a reviewed target_absent=True plan"
                )
            hooks.assert_target_not_served(self)
            self._cleanup_pre_marker_orphan(
                reviewed_plan=plan,
                confirm=confirm,
                lock_held=lock_held,
            )
            return {
                "migration_state": "orphan_cleaned",
                "target_collection": self.target_collection,
                "target_metadata_collection": self.target_metadata_collection,
            }

        self._validate_marker_ownership(marker)
        state = marker["migration_state"]
        if state not in {"active", "retained"}:
            raise MigrationError(
                f"retire supports only active or retained targets; current state is {state!r}"
            )
        if plan.target_absent or plan.target_state not in {"active", "retained"}:
            raise MigrationError("retire requires a reviewed active or retained target plan")
        if state == "active" and not target_exists:
            raise MigrationError("active retire target collection is missing")
        self._validate_retire_pair(
            plan=plan,
            expected_state=state,
            target_exists=target_exists,
        )
        hooks.assert_target_not_served(self)

        if state == "active":
            marker = self._transition("retained")
            self._validate_marker_ownership(marker)
            state = "retained"

        if target_exists:
            self._validate_retire_pair(
                plan=plan,
                expected_state="retained",
                target_exists=True,
            )
            hooks.assert_target_not_served(self)
            self._delete_collection(self.target_collection, expected_state="retained")
            if self._exists(self.target_collection):
                raise MigrationError(
                    "target data collection remains after a successful delete receipt"
                )
        else:
            if self._exists(self.target_collection):
                raise MigrationError("target data collection reappeared during retained retry")
            self._validate_retire_pair(
                plan=plan,
                expected_state="retained",
                target_exists=False,
            )

        if self._exists(self.target_collection):
            raise MigrationError("target data collection reappeared before metadata deletion")
        self._validate_retire_pair(
            plan=plan,
            expected_state="retained",
            target_exists=False,
        )
        hooks.assert_target_not_served(self)
        if self._exists(self.target_collection):
            raise MigrationError("target data collection reappeared before metadata deletion")
        self._delete_collection(
            self.target_metadata_collection,
            expected_state="retained",
        )
        if self._exists(self.target_metadata_collection):
            raise MigrationError(
                "target metadata collection remains after a successful delete receipt"
            )
        return {
            "migration_state": "retained",
            "target_collection": self.target_collection,
            "target_metadata_collection": self.target_metadata_collection,
        }

    @staticmethod
    def _assert_marker_fingerprints(
        marker: Mapping[str, Any],
        plan: MigrationPlan,
    ) -> None:
        if marker.get("source_fingerprint") != plan.source_fingerprint:
            raise MigrationError("target current marker source fingerprint changed after preflight")
        if marker.get("metadata_fingerprint") != plan.metadata_fingerprint:
            raise MigrationError(
                "target current marker metadata fingerprint changed after preflight"
            )
        if marker.get("sparse_map_fingerprint") != plan.sparse_map_fingerprint:
            raise MigrationError(
                "target current marker sparse-map fingerprint changed after preflight"
            )

    def backfill(
        self,
        *,
        confirm: bool,
        plan: MigrationPlan,
        allow_acl_fail_open: bool = False,
        lock_held: bool = False,
    ) -> dict[str, Any]:
        """Copy bounded source pages and durably advance the opaque cursor."""

        if not confirm:
            raise MigrationError("backfill requires explicit confirm=True / --confirm")
        if not lock_held:
            raise MigrationError(
                "backfill requires external source lock acknowledgement via "
                "lock_held=True / --lock-held"
            )
        if not isinstance(plan, MigrationPlan):
            raise MigrationError("backfill requires a reviewed migration plan")

        self._assert_prepare_plan_identity(plan)
        layout = self._layout_from_plan(plan)
        metadata = self._legacy_metadata()
        self._validate_source_metadata_layout(metadata.schema, layout)
        marker = self._load_current_marker()
        if marker is None:
            raise MigrationError("backfill requires an owned prepared target marker")
        self._validate_owned_prepare_marker(
            marker=marker,
            layout=layout,
            metadata=metadata,
        )
        for field_name in ("metadata_fingerprint", "sparse_map_fingerprint"):
            if marker.get(field_name) != getattr(plan, field_name):
                raise MigrationError(f"target current marker {field_name} changed after prepare")
        self._assert_sparse_dictionary_complete(self._sparse_map.values())
        if marker.get("acl_incomplete_count") and not allow_acl_fail_open:
            raise MigrationError(
                f"{marker['acl_incomplete_count']} records lack ACL fields; "
                "refusing backfill without --allow-acl-fail-open"
            )
        if marker.get("backfill_complete") is True:
            return {
                "source_count": marker["source_count"],
                "migrated_count": 0,
                "skipped_count": 0,
                "target_count": marker["target_count"],
                "target_collection": self.target_collection,
                "last_source_cursor": marker["last_source_cursor"],
                "backfill_complete": True,
                "migration_state": marker["migration_state"],
            }
        cursor = _validate_cursor(
            marker.get("last_source_cursor"),
            field_name="target current marker last_source_cursor",
        )
        migrated = 0
        skipped = 0
        with _ScanManifest() as manifest, _ScrollOffsets() as offsets:
            if not offsets.add(cursor):
                raise MigrationError("Qdrant backfill repeated its page offset")
            while True:
                page, next_cursor = self._scroll_page(
                    self.source_collection,
                    offset=cursor,
                    with_vectors=True,
                )
                next_cursor = _validate_cursor(
                    next_cursor,
                    field_name="Qdrant next_page_offset",
                )
                if next_cursor is not None and _cursor_key(next_cursor) == _cursor_key(cursor):
                    raise MigrationError("Qdrant backfill repeated its page offset")
                pending: list[dict[str, Any]] = []
                for point in page:
                    _, transformed, terms = self._transform_point(
                        point,
                        layout=layout,
                        schema=metadata.schema,
                    )
                    if not _acl_complete(transformed["payload"]) and not allow_acl_fail_open:
                        raise MigrationError(f"point {point.get('id')!r} lacks complete ACL fields")
                    self._record_source_point(
                        manifest,
                        point,
                        transformed=transformed,
                        terms=terms,
                    )
                    pending.append(transformed)
                    if len(pending) >= self.batch_size:
                        migrated_batch, skipped_batch = self._upsert_target_batch(
                            pending,
                            expected_state="building",
                        )
                        migrated += migrated_batch
                        skipped += skipped_batch
                        pending.clear()
                if pending:
                    migrated_batch, skipped_batch = self._upsert_target_batch(
                        pending,
                        expected_state="building",
                    )
                    migrated += migrated_batch
                    skipped += skipped_batch

                if next_cursor is not None and not offsets.add(next_cursor):
                    raise MigrationError("Qdrant backfill repeated its page offset")
                updated = dict(marker)
                updated.update(
                    {
                        "migration_state": "building",
                        "setup_complete": False,
                        "last_source_cursor": next_cursor,
                        "backfill_complete": next_cursor is None,
                        "target_count": self._count(self.target_collection),
                    }
                )
                self._write_marker(updated, expected_state="building")
                persisted = self._load_current_marker()
                if persisted is None:
                    raise MigrationError("target marker disappeared after backfill batch")
                self._validate_owned_prepare_marker(
                    marker=persisted,
                    layout=layout,
                    metadata=metadata,
                )
                if _cursor_key(persisted.get("last_source_cursor")) != _cursor_key(
                    next_cursor
                ) or persisted.get("backfill_complete") is not (next_cursor is None):
                    raise MigrationError("backfill cursor was not persisted")
                marker = persisted
                if marker["backfill_complete"]:
                    break
                cursor = _validate_cursor(
                    marker.get("last_source_cursor"),
                    field_name="target current marker last_source_cursor",
                )

        return {
            "source_count": marker["source_count"],
            "migrated_count": migrated,
            "skipped_count": skipped,
            "target_count": marker["target_count"],
            "target_collection": self.target_collection,
            "last_source_cursor": marker["last_source_cursor"],
            "backfill_complete": marker["backfill_complete"],
            "migration_state": marker["migration_state"],
        }

    def apply(
        self,
        *,
        confirm: bool = False,
        plan: MigrationPlan | None = None,
        allow_acl_fail_open: bool = False,
        lock_held: bool = False,
    ) -> MigrationResult:
        """Apply a plan; mutation requires confirmation and an external lock."""

        if not confirm:
            raise MigrationError("apply requires explicit confirm=True / --confirm")
        if plan is None:
            raise MigrationError("apply requires a reviewed migration plan")
        if not lock_held:
            raise MigrationError(
                "apply requires external source lock acknowledgement via "
                "lock_held=True / --lock-held"
            )
        if plan.target_absent:
            target_exists = self._exists(self.target_collection)
            target_metadata_exists = self._exists(self.target_metadata_collection)
            if target_exists or target_metadata_exists:
                existing_marker = self._load_current_marker() if target_metadata_exists else None
                if existing_marker is None:
                    # preflight cannot inspect an unmarked pair. Prove the
                    # reviewed source is still frozen before destructive
                    # orphan cleanup instead of deleting first and discovering
                    # a stale plan afterward.
                    self._assert_prepare_plan_identity(plan)
                    layout = self._layout_from_plan(plan)
                    metadata = self._legacy_metadata()
                    self._validate_source_metadata_layout(metadata.schema, layout)
                    if _metadata_fingerprint(metadata) != plan.metadata_fingerprint:
                        raise MigrationError(
                            "legacy metadata changed after preflight; "
                            "rerun preflight with writes frozen"
                        )
                    self._assert_source_layout(layout, phase="preflight")
                    source = self._scan_source(
                        layout=layout,
                        schema=metadata.schema,
                    )
                    self._assert_source_snapshot(plan, source, phase="preflight")
                    if plan.acl_incomplete_count and not allow_acl_fail_open:
                        raise MigrationError(
                            f"{plan.acl_incomplete_count} records lack ACL fields; "
                            "refusing cutover without --allow-acl-fail-open"
                        )
                    self._cleanup_pre_marker_orphan(
                        reviewed_plan=plan,
                        confirm=confirm,
                        lock_held=lock_held,
                    )
        current_plan = self.preflight()
        if self._reviewed_plan_fields(plan) != self._reviewed_plan_fields(current_plan):
            raise MigrationError("provided migration plan is stale; rerun preflight before apply")
        plan = current_plan
        if plan.target_state == "ready":
            raise MigrationError(
                "apply refuses a ready target; use the explicit migration "
                "reconciliation/verification phases instead"
            )
        layout = CollectionLayout(
            dense_vector_name=plan.dense_vector_name,
            sparse_vector_name=plan.sparse_vector_name,
            vector_dimension=plan.vector_dimension,
            distance=plan.distance,
            dense_datatype=plan.dense_datatype,
            sparse_enabled=plan.sparse_enabled,
            sparse_modifier=plan.sparse_modifier,
            sparse_datatype=plan.sparse_datatype,
        )
        metadata = self._legacy_metadata()
        self._validate_source_metadata_layout(metadata.schema, layout)
        metadata_fingerprint = _metadata_fingerprint(metadata)
        if metadata_fingerprint != plan.metadata_fingerprint:
            raise MigrationError(
                "legacy metadata changed after preflight; rerun preflight with writes frozen"
            )
        source = self._scan_source(
            layout=layout,
            schema=metadata.schema,
        )
        self._assert_source_snapshot(plan, source, phase="preflight")
        if plan.acl_incomplete_count and not allow_acl_fail_open:
            raise MigrationError(
                f"{plan.acl_incomplete_count} records lack ACL fields; "
                "refusing cutover without --allow-acl-fail-open"
            )
        self._assert_source_layout(layout, phase="target setup")
        self.prepare(
            confirm=confirm,
            plan=plan,
            allow_acl_fail_open=allow_acl_fail_open,
            lock_held=lock_held,
        )
        # prepare may return a snapshot that became ready before the first
        # source point is copied. Re-read the owned marker and enforce the
        # mutable prepare-state contract at the copy boundary.
        marker_incomplete = self._load_current_marker()
        if marker_incomplete is None:
            raise MigrationError("target marker disappeared before copy")
        self._validate_owned_prepare_marker(
            marker=marker_incomplete,
            layout=layout,
            metadata=metadata,
        )
        self._assert_marker_fingerprints(marker_incomplete, plan)

        backfill_result = self.backfill(
            confirm=confirm,
            plan=plan,
            allow_acl_fail_open=allow_acl_fail_open,
            lock_held=lock_held,
        )
        migrated = int(backfill_result["migrated_count"])
        skipped = int(backfill_result["skipped_count"])

        self._assert_source_layout(layout, phase="final verification")
        final_source = self._scan_source(layout=layout, schema=metadata.schema)
        self._assert_source_snapshot(plan, final_source, phase="apply")
        final_metadata = self._legacy_metadata()
        if _metadata_fingerprint(final_metadata) != plan.metadata_fingerprint:
            raise MigrationError(
                "legacy metadata changed during apply; rerun preflight with writes frozen"
            )
        verification = self.verify(
            plan=plan,
            allow_acl_fail_open=allow_acl_fail_open,
            confirm=confirm,
            lock_held=lock_held,
        )
        target_count = int(verification["target_count"])

        return MigrationResult(
            source_count=plan.source_count,
            migrated_count=migrated,
            skipped_count=skipped,
            target_count=target_count,
            target_collection=self.target_collection,
        )

    def _upsert_target_batch(
        self,
        points: list[dict[str, Any]],
        *,
        expected_state: str,
    ) -> tuple[int, int]:
        return self._apply_batch(points, expected_state=expected_state)

    def _apply_batch(
        self,
        points: list[dict[str, Any]],
        *,
        expected_state: str,
    ) -> tuple[int, int]:
        existing = {
            str(point.get("id")): point
            for point in self._retrieve(
                self.target_collection,
                [str(point["id"]) for point in points],
                with_vectors=True,
            )
            if point.get("id") is not None
        }
        write: list[dict[str, Any]] = []
        skipped = 0
        for point in points:
            point_id = str(point["id"])
            current = existing.get(point_id)
            if current is None:
                write.append(point)
                continue
            payload = current.get("payload")
            current_id = payload.get(_ORIGINAL_ID_FIELD) if isinstance(payload, Mapping) else None
            source_id = point["payload"][_ORIGINAL_ID_FIELD]
            if current_id is None or str(current_id) != str(source_id):
                raise MigrationError(
                    f"target point-id collision for {point_id}: "
                    f"existing={current_id!r} source={source_id!r}"
                )
            expected_payload = point["payload"]
            vectors_match = False
            if payload == expected_payload:
                try:
                    self._assert_target_vectors(current, point, exact=True)
                except MigrationError:
                    pass
                else:
                    vectors_match = True
            if vectors_match:
                skipped += 1
            else:
                write.append(point)
        self._write_points(
            self.target_collection,
            write,
            expected_state=expected_state,
        )
        return len(write), skipped


def _load_sparse_map(path: str | None) -> dict[str, Any] | None:
    if not path:
        return None
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise MigrationError(f"cannot read sparse map {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise MigrationError("sparse map JSON must be an object")
    return value


def _load_plan(path: str | None) -> MigrationPlan | None:
    if not path:
        return None
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise MigrationError(f"cannot read migration plan {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise MigrationError("migration plan JSON must be an object")
    expected_fields = {
        "source_collection",
        "target_collection",
        "source_metadata_collection",
        "target_metadata_collection",
        "logical_collection",
        "migration_id",
        "migrator_version",
        "source_count",
        "target_count",
        "target_absent",
        "target_state",
        "dense_vector_name",
        "sparse_vector_name",
        "vector_dimension",
        "distance",
        "dense_datatype",
        "sparse_enabled",
        "sparse_modifier",
        "sparse_datatype",
        "sparse_weight",
        "source_fingerprint",
        "metadata_fingerprint",
        "sparse_map_fingerprint",
        "acl_incomplete_count",
        "sparse_term_count",
        "sparse_term_fingerprint",
        "batch_size",
        "timeout_seconds",
    }
    missing = sorted(expected_fields - set(value))
    extra = sorted(set(value) - expected_fields)
    if missing or extra:
        raise MigrationError(f"migration plan fields differ: missing={missing!r} extra={extra!r}")

    def text(name: str, *, non_empty: bool = True) -> str:
        item = value[name]
        if not isinstance(item, str) or (non_empty and not item):
            raise MigrationError(f"migration plan field {name!r} must be a string")
        return item

    def integer(name: str, *, non_negative: bool = False) -> int:
        item = value[name]
        if isinstance(item, bool) or not isinstance(item, int):
            raise MigrationError(f"migration plan field {name!r} must be an integer")
        if non_negative and item < 0:
            raise MigrationError(f"migration plan field {name!r} must be non-negative")
        return item

    sparse_weight = value["sparse_weight"]
    if (
        isinstance(sparse_weight, bool)
        or not isinstance(sparse_weight, (int, float))
        or not math.isfinite(float(sparse_weight))
    ):
        raise MigrationError("migration plan sparse_weight must be finite")
    for name in ("target_absent", "sparse_enabled"):
        if not isinstance(value[name], bool):
            raise MigrationError(f"migration plan field {name!r} must be a boolean")
    target_state = value["target_state"]
    if target_state is not None and (
        not isinstance(target_state, str) or target_state not in MIGRATION_STATES
    ):
        raise MigrationError("migration plan target_state is invalid")
    if value["target_absent"] and target_state is not None:
        raise MigrationError("migration plan target_state must be null when target_absent is true")
    if value["migrator_version"] != MIGRATOR_VERSION:
        raise MigrationError(
            "migration plan migrator_version does not match the running controller"
        )
    for name in ("dense_datatype", "sparse_modifier", "sparse_datatype"):
        if value[name] is not None and (not isinstance(value[name], str) or not value[name]):
            raise MigrationError(f"migration plan field {name!r} must be a string or null")
    sparse_term_count = integer("sparse_term_count", non_negative=True)
    sparse_term_fingerprint = text("sparse_term_fingerprint")
    batch_size = integer("batch_size")
    if batch_size <= 0:
        raise MigrationError("migration plan batch_size must be positive")
    vector_dimension = integer("vector_dimension")
    if vector_dimension <= 0:
        raise MigrationError("migration plan vector_dimension must be positive")
    timeout_seconds = value["timeout_seconds"]
    if (
        isinstance(timeout_seconds, bool)
        or not isinstance(timeout_seconds, (int, float))
        or not math.isfinite(float(timeout_seconds))
        or float(timeout_seconds) <= 0
    ):
        raise MigrationError("migration plan timeout_seconds must be positive and finite")

    return MigrationPlan(
        source_collection=text("source_collection"),
        target_collection=text("target_collection"),
        source_metadata_collection=text("source_metadata_collection"),
        target_metadata_collection=text("target_metadata_collection"),
        logical_collection=text("logical_collection"),
        migration_id=text("migration_id"),
        migrator_version=text("migrator_version"),
        source_count=integer("source_count", non_negative=True),
        target_count=integer("target_count", non_negative=True),
        target_absent=value["target_absent"],
        target_state=target_state,
        dense_vector_name=text("dense_vector_name"),
        sparse_vector_name=text("sparse_vector_name"),
        vector_dimension=vector_dimension,
        distance=text("distance"),
        dense_datatype=value["dense_datatype"],
        sparse_enabled=value["sparse_enabled"],
        sparse_modifier=value["sparse_modifier"],
        sparse_datatype=value["sparse_datatype"],
        sparse_weight=float(sparse_weight),
        source_fingerprint=text("source_fingerprint"),
        metadata_fingerprint=text("metadata_fingerprint"),
        sparse_map_fingerprint=text("sparse_map_fingerprint"),
        acl_incomplete_count=integer("acl_incomplete_count", non_negative=True),
        sparse_term_count=sparse_term_count,
        sparse_term_fingerprint=sparse_term_fingerprint,
        batch_size=batch_size,
        timeout_seconds=float(timeout_seconds),
    )


def _warn_acl_risk(plan: MigrationPlan | None, allow_acl_fail_open: bool) -> None:
    if allow_acl_fail_open and plan is not None and plan.acl_incomplete_count:
        print(
            f"warning: allowing fail-open ACL fields for {plan.acl_incomplete_count} records",
            file=sys.stderr,
        )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default=os.environ.get("QDRANT_URL"))
    parser.add_argument("--api-key", default=os.environ.get("QDRANT_API_KEY"))
    parser.add_argument("--source-collection", required=True)
    parser.add_argument("--target-collection", required=True)
    parser.add_argument("--source-metadata-collection")
    parser.add_argument("--target-metadata-collection")
    parser.add_argument("--logical-collection", required=True)
    parser.add_argument("--migration-id", required=True)
    parser.add_argument("--batch-size", type=int, default=100)
    parser.add_argument("--timeout-seconds", type=float, required=True)
    parser.add_argument("--dense-vector-name")
    parser.add_argument("--sparse-vector-name")
    parser.add_argument(
        "--sparse-map",
        help="JSON file containing old sparse index -> term (or term -> old index)",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("preflight", help="validate and print a read-only plan")
    prepare_parser = subparsers.add_parser(
        "prepare",
        help="create or resume an owned building target",
    )
    prepare_parser.add_argument("--confirm", action="store_true")
    prepare_parser.add_argument("--lock-held", action="store_true")
    prepare_parser.add_argument("--allow-acl-fail-open", action="store_true")
    prepare_parser.add_argument(
        "--plan",
        required=True,
        help="reviewed JSON plan produced by the preflight command",
    )
    backfill_parser = subparsers.add_parser(
        "backfill",
        help="copy source pages into an owned building target",
    )
    backfill_parser.add_argument("--confirm", action="store_true")
    backfill_parser.add_argument("--lock-held", action="store_true")
    backfill_parser.add_argument("--allow-acl-fail-open", action="store_true")
    backfill_parser.add_argument(
        "--plan",
        required=True,
        help="reviewed JSON plan produced by the preflight command",
    )
    reconcile_parser = subparsers.add_parser(
        "reconcile",
        help="reconcile the target from a rolling source snapshot",
    )
    reconcile_parser.add_argument("--confirm", action="store_true")
    reconcile_parser.add_argument("--lock-held", action="store_true")
    reconcile_parser.add_argument("--barrier-held", action="store_true")
    reconcile_parser.add_argument("--allow-acl-fail-open", action="store_true")
    reconcile_parser.add_argument("--plan", required=True)
    verify_parser = subparsers.add_parser(
        "verify",
        help="audit target contents and publish readiness",
    )
    verify_parser.add_argument("--confirm", action="store_true")
    verify_parser.add_argument("--lock-held", action="store_true")
    verify_parser.add_argument("--barrier-held", action="store_true")
    verify_parser.add_argument("--final", action="store_true")
    verify_parser.add_argument("--allow-acl-fail-open", action="store_true")
    verify_parser.add_argument("--plan", required=True)
    cutover_parser = subparsers.add_parser(
        "cutover",
        help="cut over a ready target while the operator holds the barrier",
    )
    cutover_parser.add_argument("--confirm", action="store_true")
    cutover_parser.add_argument("--lock-held", action="store_true")
    cutover_parser.add_argument("--barrier-held", action="store_true")
    cutover_parser.add_argument("--resume", action="store_true")
    cutover_parser.add_argument("--allow-acl-fail-open", action="store_true")
    cutover_parser.add_argument("--plan", required=True)
    cutover_parser.add_argument("--deployment-hooks", required=True)
    rollback_parser = subparsers.add_parser(
        "rollback",
        help="restore the legacy serving path while the barrier is held",
    )
    rollback_parser.add_argument("--confirm", action="store_true")
    rollback_parser.add_argument("--lock-held", action="store_true")
    rollback_parser.add_argument("--barrier-held", action="store_true")
    rollback_parser.add_argument(
        "--no-current-format-writes-accepted",
        action="store_true",
    )
    rollback_parser.add_argument("--deployment-hooks", required=True)
    retire_parser = subparsers.add_parser(
        "retire",
        help="remove a retained, non-serving target pair",
    )
    retire_parser.add_argument("--confirm", action="store_true")
    retire_parser.add_argument("--lock-held", action="store_true")
    retire_parser.add_argument("--plan", required=True)
    retire_parser.add_argument("--deployment-hooks", required=True)
    apply_parser = subparsers.add_parser("apply", help="copy records into the target")
    apply_parser.add_argument(
        "--confirm",
        action="store_true",
        help="required acknowledgement that target Qdrant collections will be written",
    )
    apply_parser.add_argument(
        "--lock-held",
        action="store_true",
        help="acknowledge that the external per-source write lock is held",
    )
    apply_parser.add_argument(
        "--allow-acl-fail-open",
        action="store_true",
        help="acknowledge that records missing ACL fields remain fail-open",
    )
    apply_parser.add_argument(
        "--plan",
        required=True,
        help="reviewed JSON plan produced by the preflight command",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if not args.url:
        print("Qdrant URL is required via --url or QDRANT_URL", file=sys.stderr)
        return 2
    try:
        sparse_map = _load_sparse_map(args.sparse_map)
        client = QdrantRestClient(
            args.url,
            api_key=args.api_key,
            timeout_seconds=args.timeout_seconds,
        )
        migration = QdrantMigration(
            client=client,
            source_collection=args.source_collection,
            target_collection=args.target_collection,
            source_metadata_collection=args.source_metadata_collection,
            target_metadata_collection=args.target_metadata_collection,
            batch_size=args.batch_size,
            dense_vector_name=args.dense_vector_name,
            sparse_vector_name=args.sparse_vector_name,
            sparse_map=sparse_map,
            logical_collection=args.logical_collection,
            migration_id=args.migration_id,
            timeout_seconds=args.timeout_seconds,
        )
        if args.command == "preflight":
            print(json.dumps(migration.preflight().to_dict(), sort_keys=True))
        elif args.command == "prepare":
            reviewed_plan = _load_plan(args.plan)
            _warn_acl_risk(reviewed_plan, args.allow_acl_fail_open)
            print(
                json.dumps(
                    migration.prepare(
                        confirm=args.confirm,
                        plan=reviewed_plan,
                        allow_acl_fail_open=args.allow_acl_fail_open,
                        lock_held=args.lock_held,
                    ),
                    sort_keys=True,
                )
            )
        elif args.command == "backfill":
            reviewed_plan = _load_plan(args.plan)
            _warn_acl_risk(reviewed_plan, args.allow_acl_fail_open)
            print(
                json.dumps(
                    migration.backfill(
                        confirm=args.confirm,
                        plan=reviewed_plan,
                        allow_acl_fail_open=args.allow_acl_fail_open,
                        lock_held=args.lock_held,
                    ),
                    sort_keys=True,
                )
            )
        elif args.command == "reconcile":
            reviewed_plan = _load_plan(args.plan)
            _warn_acl_risk(reviewed_plan, args.allow_acl_fail_open)
            print(
                json.dumps(
                    migration.reconcile(
                        confirm=args.confirm,
                        plan=reviewed_plan,
                        barrier_held=args.barrier_held,
                        allow_acl_fail_open=args.allow_acl_fail_open,
                        lock_held=args.lock_held,
                    ),
                    sort_keys=True,
                )
            )
        elif args.command == "verify":
            reviewed_plan = _load_plan(args.plan)
            _warn_acl_risk(reviewed_plan, args.allow_acl_fail_open)
            print(
                json.dumps(
                    migration.verify(
                        plan=reviewed_plan,
                        allow_acl_fail_open=args.allow_acl_fail_open,
                        final=args.final,
                        barrier_held=args.barrier_held,
                        confirm=args.confirm,
                        lock_held=args.lock_held,
                    ),
                    sort_keys=True,
                )
            )
        elif args.command == "cutover":
            reviewed_plan = _load_plan(args.plan)
            _warn_acl_risk(reviewed_plan, args.allow_acl_fail_open)
            hooks = DeploymentHooks.from_path(args.deployment_hooks)
            print(
                json.dumps(
                    migration.cutover(
                        confirm=args.confirm,
                        plan=reviewed_plan,
                        barrier_held=args.barrier_held,
                        hooks=hooks,
                        allow_acl_fail_open=args.allow_acl_fail_open,
                        lock_held=args.lock_held,
                        resume=args.resume,
                    ),
                    sort_keys=True,
                )
            )
        elif args.command == "rollback":
            hooks = DeploymentHooks.from_path(args.deployment_hooks)
            print(
                json.dumps(
                    migration.rollback(
                        confirm=args.confirm,
                        barrier_held=args.barrier_held,
                        no_current_format_writes_accepted=(args.no_current_format_writes_accepted),
                        hooks=hooks,
                        lock_held=args.lock_held,
                    ),
                    sort_keys=True,
                )
            )
        elif args.command == "retire":
            reviewed_plan = _load_plan(args.plan)
            hooks = DeploymentHooks.from_path(args.deployment_hooks)
            print(
                json.dumps(
                    migration.retire(
                        confirm=args.confirm,
                        plan=reviewed_plan,
                        lock_held=args.lock_held,
                        hooks=hooks,
                    ),
                    sort_keys=True,
                )
            )
        else:
            reviewed_plan = _load_plan(args.plan)
            _warn_acl_risk(reviewed_plan, args.allow_acl_fail_open)
            print(
                json.dumps(
                    migration.apply(
                        confirm=args.confirm,
                        plan=reviewed_plan,
                        allow_acl_fail_open=args.allow_acl_fail_open,
                        lock_held=args.lock_held,
                    ).to_dict(),
                    sort_keys=True,
                )
            )
    except (MigrationError, QdrantError, ValueError) as exc:
        print(f"qdrant migration failed: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
