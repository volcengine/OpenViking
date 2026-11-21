#!/usr/bin/env python3
"""Safely seed per-index sparse-term owner points in a current Qdrant sidecar."""

from __future__ import annotations

import argparse
import json
import os
import sys
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from itertools import zip_longest
from pathlib import Path
from typing import Any, Iterator, Mapping
from urllib.parse import quote

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from openviking.storage.vectordb.collection.qdrant_rest import (  # noqa: E402
    QdrantError,
    QdrantRestClient,
)
from openviking.storage.vectordb.qdrant_sparse import (  # noqa: E402
    parse_sparse_point,
    sparse_owner_point_id,
)
from openviking.storage.vectordb.qdrant_utils import (  # noqa: E402
    is_qdrant_migration_marker,
)
from scripts.maintenance.qdrant_migrate import (  # noqa: E402
    _META_MARKER_ID,
    MigrationError,
    SparseMigrationError,
    _ScrollOffsets,
    _SparseDictionaryManifest,
    _validate_cursor,
)

_META_VECTOR_NAME = "meta"
_SPARSE_TERM_MARKER = "_openviking_sparse_term"
_BATCH_SIZE = 100
_ALLOWED_STATES = {"ready", "active", "retained"}
_PROVENANCE_PAIRS = (
    ("target_collection", "target_metadata_collection"),
    ("source_collection", "source_metadata_collection"),
)


class SparseUpgradeError(RuntimeError):
    """A sparse dictionary upgrade preflight or conversion failure."""


@dataclass(frozen=True)
class SparseUpgradePlan:
    """Read-only sparse owner state captured from one metadata scan."""

    data_collection: str
    metadata_collection: str
    term_count: int
    owner_count: int
    missing_owner_count: int
    migration_state: str | None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class _Binding:
    term: str
    index: int


class SparseDictionaryUpgrade:
    """Validate and seed immutable per-index owner points."""

    def __init__(
        self,
        *,
        client: Any,
        data_collection: str,
        metadata_collection: str,
    ) -> None:
        self._client = client
        self.data_collection = self._name(data_collection, "data collection")
        self.metadata_collection = self._name(metadata_collection, "metadata collection")
        if self.data_collection == self.metadata_collection:
            raise ValueError("data and metadata collection names must differ")

    @staticmethod
    def _name(value: str, description: str) -> str:
        normalized = str(value or "").strip()
        if not normalized:
            raise ValueError(f"{description} must not be empty")
        return normalized

    def _path(self, collection: str, suffix: str = "") -> str:
        return f"/collections/{quote(collection, safe='')}{suffix}"

    def _ensure_supported_version(self) -> None:
        try:
            self._client.ensure_supported_version()
        except (QdrantError, ValueError) as exc:
            raise SparseUpgradeError(str(exc)) from exc

    def _collection_info(self, collection: str) -> Mapping[str, Any]:
        try:
            response = self._client.request("GET", self._path(collection))
        except QdrantError as exc:
            raise SparseUpgradeError(
                f"target collection {collection!r} is not readable: {exc}"
            ) from exc
        result = response.get("result", response)
        if not isinstance(result, Mapping):
            raise SparseUpgradeError(f"Qdrant collection response is malformed: {collection}")
        return result

    def _retrieve(self, ids: list[str]) -> list[dict[str, Any]]:
        if not ids:
            return []
        try:
            response = self._client.request(
                "POST",
                self._path(self.metadata_collection, "/points"),
                {
                    "ids": ids,
                    "with_payload": True,
                    "with_vector": False,
                },
                params={"consistency": "all"},
            )
        except QdrantError as exc:
            raise SparseUpgradeError(f"metadata point lookup failed: {exc}") from exc
        result = response.get("result", response)
        if not isinstance(result, list) or any(not isinstance(point, dict) for point in result):
            raise SparseUpgradeError("Qdrant metadata point lookup response is malformed")
        return result

    def _load_marker(self) -> dict[str, Any]:
        points = self._retrieve([_META_MARKER_ID])
        if len(points) != 1:
            raise SparseUpgradeError("metadata collection has no unique OpenViking marker")
        point = points[0]
        if str(point.get("id")) != _META_MARKER_ID:
            raise SparseUpgradeError("metadata marker point ID does not match the marker ID")
        marker = point.get("payload")
        if not isinstance(marker, Mapping):
            raise SparseUpgradeError("OpenViking metadata marker has an invalid payload")
        return dict(marker)

    def _validate_marker(self, marker: Mapping[str, Any]) -> None:
        if type(marker.get("_openviking_meta_version")) is not int or (
            marker["_openviking_meta_version"] != 1
        ):
            raise SparseUpgradeError("OpenViking metadata marker version must be 1")
        if marker.get("collection_name") != self.data_collection:
            raise SparseUpgradeError(
                "OpenViking metadata marker data collection does not match the target"
            )
        if marker.get("metadata_collection_name") != self.metadata_collection:
            raise SparseUpgradeError(
                "OpenViking metadata marker metadata collection does not match the target"
            )
        if marker.get("sparse_enabled") is not True:
            raise SparseUpgradeError("OpenViking metadata marker does not enable sparse vectors")

        setup_complete = marker.get("setup_complete")
        if setup_complete is not None and setup_complete is not True:
            raise SparseUpgradeError("OpenViking metadata marker is not setup-complete")
        state = marker.get("migration_state")
        logical_collection = marker.get("logical_collection")
        if is_qdrant_migration_marker(marker):
            migration_id = marker.get("migration_id")
            if (
                not isinstance(migration_id, str)
                or not migration_id.strip()
                or not isinstance(logical_collection, str)
                or not logical_collection.strip()
            ):
                raise SparseUpgradeError(
                    "OpenViking migration marker has incomplete provenance"
                )
            if setup_complete is not True:
                raise SparseUpgradeError(
                    "OpenViking migration marker requires setup_complete=True"
                )
            if not isinstance(state, str) or state not in _ALLOWED_STATES:
                raise SparseUpgradeError(
                    f"OpenViking metadata marker has an unsupported migration state: {state!r}"
                )

        elif "logical_collection" in marker and (
            not isinstance(logical_collection, str) or not logical_collection.strip()
        ):
            raise SparseUpgradeError(
                "OpenViking metadata marker has invalid logical_collection"
            )

        for left, right in _PROVENANCE_PAIRS:
            left_present = left in marker
            right_present = right in marker
            if left_present != right_present:
                raise SparseUpgradeError(
                    f"OpenViking metadata marker has incomplete provenance: {left}/{right}"
                )
            if left_present and (
                not isinstance(marker[left], str)
                or not marker[left].strip()
                or not isinstance(marker[right], str)
                or not marker[right].strip()
            ):
                raise SparseUpgradeError(
                    f"OpenViking metadata marker has invalid provenance: {left}/{right}"
                )
            if left == "target_collection" and left_present and (
                marker[left] != self.data_collection
                or marker[right] != self.metadata_collection
            ):
                raise SparseUpgradeError(
                    "OpenViking metadata marker target provenance does not match the target"
                )

    @staticmethod
    def _validate_row_provenance(
        payload: Mapping[str, Any],
        marker: Mapping[str, Any],
    ) -> None:
        logical_present = "logical_collection" in payload
        migration_present = "migration_id" in payload
        if logical_present != migration_present:
            raise SparseUpgradeError(
                "sparse dictionary point has incomplete migration provenance"
            )
        if not logical_present:
            return
        logical_collection = payload.get("logical_collection")
        migration_id = payload.get("migration_id")
        if (
            not isinstance(logical_collection, str)
            or not logical_collection.strip()
            or not isinstance(migration_id, str)
            or not migration_id.strip()
        ):
            raise SparseUpgradeError(
                "sparse dictionary point has invalid migration provenance"
            )
        marker_logical = marker.get("logical_collection")
        marker_migration = marker.get("migration_id")
        if (
            not isinstance(marker_logical, str)
            or not marker_logical.strip()
            or not isinstance(marker_migration, str)
            or not marker_migration.strip()
            or logical_collection != marker_logical
            or migration_id != marker_migration
        ):
            raise SparseUpgradeError(
                "sparse dictionary point provenance does not match the metadata marker"
            )

    def _count_points(self) -> int:
        try:
            response = self._client.request(
                "POST",
                self._path(self.metadata_collection, "/points/count"),
                {"exact": True, "filter": {}},
                params={"consistency": "all"},
            )
        except QdrantError as exc:
            raise SparseUpgradeError(f"metadata point count failed: {exc}") from exc
        result = response.get("result", response)
        if not isinstance(result, Mapping):
            raise SparseUpgradeError("Qdrant metadata point count response is malformed")
        count = result.get("count")
        if type(count) is not int or count < 0:
            raise SparseUpgradeError("Qdrant metadata point count is invalid")
        return count

    def _scroll_points(self) -> Iterator[dict[str, Any]]:
        offset: int | str | None = None
        with _ScrollOffsets() as offsets:
            if not offsets.add(offset):
                raise SparseUpgradeError("metadata scroll repeated its initial page offset")
            while True:
                body: dict[str, Any] = {
                    "limit": _BATCH_SIZE,
                    "with_payload": True,
                    "with_vector": False,
                }
                if offset is not None:
                    body["offset"] = offset
                try:
                    response = self._client.request(
                        "POST",
                        self._path(self.metadata_collection, "/points/scroll"),
                        body,
                        params={"consistency": "all"},
                    )
                except QdrantError as exc:
                    raise SparseUpgradeError(f"metadata scroll failed: {exc}") from exc
                result = response.get("result", response)
                if not isinstance(result, Mapping):
                    raise SparseUpgradeError("Qdrant metadata scroll response is malformed")
                page = result.get("points")
                if not isinstance(page, list) or any(
                    not isinstance(point, dict) for point in page
                ):
                    raise SparseUpgradeError("Qdrant metadata scroll points are malformed")
                for point in page:
                    if point.get("id") is None:
                        raise SparseUpgradeError(
                            "metadata scroll returned a point without an ID"
                        )
                    yield point
                next_offset = result.get("next_page_offset")
                try:
                    next_offset = _validate_cursor(
                        next_offset,
                        field_name="Qdrant next_page_offset",
                    )
                except MigrationError as exc:
                    raise SparseUpgradeError(str(exc)) from exc
                if next_offset is None:
                    return
                if not page:
                    raise SparseUpgradeError(
                        "metadata scroll returned an empty page with a next offset"
                    )
                if not offsets.add(next_offset):
                    raise SparseUpgradeError("metadata scroll repeated its page offset")
                offset = next_offset

    def _plan(self, manifest: _SparseDictionaryManifest, marker: Mapping[str, Any]) -> SparseUpgradePlan:
        term_count = 0
        owner_count = 0
        for _term, index in manifest._connection.execute(
            "SELECT term, sparse_index FROM sparse_dictionary ORDER BY term"
        ):
            term_count += 1
            if manifest.has_owner(int(index)):
                owner_count += 1
        return SparseUpgradePlan(
            data_collection=self.data_collection,
            metadata_collection=self.metadata_collection,
            term_count=term_count,
            owner_count=owner_count,
            missing_owner_count=term_count - owner_count,
            migration_state=marker.get("migration_state"),
        )

    @staticmethod
    def _manifest_bindings(
        manifest: _SparseDictionaryManifest,
    ) -> Iterator[tuple[str, int]]:
        for term, index in manifest._connection.execute(
            "SELECT term, sparse_index FROM sparse_dictionary ORDER BY term"
        ):
            yield str(term), int(index)

    @staticmethod
    def _manifest_legacy_points(
        manifest: _SparseDictionaryManifest,
    ) -> Iterator[tuple[str, str]]:
        for point_id, term in manifest._connection.execute(
            "SELECT point_id, term FROM sparse_dictionary_points ORDER BY point_id"
        ):
            normalized_term = str(term)
            index = manifest.index_for_term(normalized_term)
            if index is None:
                raise SparseUpgradeError(
                    f"sparse dictionary point {point_id!r} has no term binding"
                )
            if str(point_id) != sparse_owner_point_id(index):
                yield str(point_id), normalized_term

    def _missing_owner_batches(
        self,
        manifest: _SparseDictionaryManifest,
    ) -> Iterator[list[_Binding]]:
        batch: list[_Binding] = []
        for term, index in self._manifest_bindings(manifest):
            if manifest.has_owner(index):
                continue
            batch.append(_Binding(term=term, index=index))
            if len(batch) == _BATCH_SIZE:
                yield batch
                batch = []
        if batch:
            yield batch

    def _compare_manifests(
        self,
        initial: _SparseDictionaryManifest,
        final: _SparseDictionaryManifest,
    ) -> None:
        missing = object()
        for initial_binding, final_binding in zip_longest(
            self._manifest_bindings(initial),
            self._manifest_bindings(final),
            fillvalue=missing,
        ):
            if initial_binding != final_binding:
                raise SparseUpgradeError(
                    "sparse dictionary bindings changed during owner conversion"
                )
            _term, index = final_binding
            if not final.has_owner(index):
                raise SparseUpgradeError(
                    f"sparse dictionary owner is missing after conversion: index={index}"
                )

        for initial_point, final_point in zip_longest(
            self._manifest_legacy_points(initial),
            self._manifest_legacy_points(final),
            fillvalue=missing,
        ):
            if initial_point != final_point:
                raise SparseUpgradeError(
                    "legacy sparse dictionary points changed during owner conversion"
                )

    @contextmanager
    def _open_snapshot(
        self,
    ) -> Iterator[tuple[dict[str, Any], _SparseDictionaryManifest, SparseUpgradePlan]]:
        self._collection_info(self.data_collection)
        self._collection_info(self.metadata_collection)
        marker = self._load_marker()
        self._validate_marker(marker)
        expected_count = self._count_points()
        with _SparseDictionaryManifest() as manifest:
            scanned_count = 0
            marker_seen = False
            for point in self._scroll_points():
                scanned_count += 1
                point_id = str(point["id"])
                if point_id == _META_MARKER_ID:
                    if marker_seen:
                        raise SparseUpgradeError(
                            f"metadata scroll returned duplicate point ID {point_id!r}"
                        )
                    marker_seen = True
                    continue
                payload = point.get("payload")
                if not isinstance(payload, Mapping):
                    raise SparseUpgradeError(
                        f"sparse dictionary point {point_id!r} has an invalid payload"
                    )
                self._validate_row_provenance(payload, marker)
                try:
                    term, index = parse_sparse_point(point)
                    manifest.add(term, index, point_id=point_id)
                except (SparseMigrationError, ValueError) as exc:
                    raise SparseUpgradeError(str(exc)) from exc
            if not marker_seen:
                raise SparseUpgradeError("metadata collection scroll did not include its marker")
            if scanned_count != expected_count:
                raise SparseUpgradeError(
                    "metadata scroll count differs from exact metadata point count: "
                    f"scanned={scanned_count} exact={expected_count}"
                )
            final_count = self._count_points()
            if final_count != expected_count:
                raise SparseUpgradeError(
                    "metadata collection changed during metadata scan: "
                    f"before={expected_count} after={final_count}"
                )
            yield marker, manifest, self._plan(manifest, marker)

    def preflight(self) -> SparseUpgradePlan:
        """Validate the target and printable sparse owner state without writing."""

        self._ensure_supported_version()
        with self._open_snapshot() as (_marker, _manifest, plan):
            return plan

    @staticmethod
    def _expected_payload(
        term: str,
        index: int,
        marker: Mapping[str, Any],
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            _SPARSE_TERM_MARKER: True,
            "term": term,
            "index": index,
        }
        if "logical_collection" in marker and "migration_id" in marker:
            payload["logical_collection"] = marker["logical_collection"]
            payload["migration_id"] = marker["migration_id"]
        return payload

    def _validate_owner_point(
        self,
        point: Mapping[str, Any],
        *,
        term: str,
        index: int,
        marker: Mapping[str, Any],
    ) -> None:
        expected_id = sparse_owner_point_id(index)
        if str(point.get("id")) != expected_id:
            raise SparseUpgradeError(
                f"sparse owner readback returned the wrong point ID for index {index}"
            )
        try:
            actual_term, actual_index = parse_sparse_point(point)
        except ValueError as exc:
            raise SparseUpgradeError(str(exc)) from exc
        if actual_term != term or actual_index != index:
            raise SparseUpgradeError(
                f"sparse owner readback does not match term {term!r} and index {index}"
            )
        payload = point["payload"]
        self._validate_row_provenance(payload, marker)
        expected = self._expected_payload(term, index, marker)
        for field, value in expected.items():
            if payload.get(field) != value:
                raise SparseUpgradeError(
                    f"sparse owner readback has an invalid {field!r} for index {index}"
                )

    def _assert_marker_unchanged(self, expected: Mapping[str, Any]) -> None:
        current = self._load_marker()
        self._validate_marker(current)
        if dict(current) != dict(expected):
            raise SparseUpgradeError("metadata marker changed during sparse owner conversion")

    def _write_owner_batch(
        self,
        batch: list[_Binding],
        *,
        marker: Mapping[str, Any],
    ) -> None:
        points = [
            {
                "id": sparse_owner_point_id(binding.index),
                "vector": {_META_VECTOR_NAME: [0.0]},
                "payload": self._expected_payload(binding.term, binding.index, marker),
            }
            for binding in batch
        ]
        if not points:
            return
        try:
            response = self._client.request(
                "PUT",
                self._path(self.metadata_collection, "/points"),
                {
                    "points": points,
                    "update_filter": {
                        "must_not": [
                            {
                                "has_id": [point["id"] for point in points]
                            }
                        ]
                    },
                },
                params={
                    "wait": "true",
                    "ordering": "strong",
                },
            )
        except QdrantError as exc:
            raise SparseUpgradeError(f"sparse owner write failed: {exc}") from exc
        result = response.get("result", response)
        if not isinstance(result, Mapping) or result.get("status") != "completed":
            raise SparseUpgradeError("sparse owner write did not complete")

    def _read_owner_batch(
        self,
        batch: list[_Binding],
        *,
        marker: Mapping[str, Any],
    ) -> None:
        ids = [sparse_owner_point_id(binding.index) for binding in batch]
        points = self._retrieve(ids)
        actual_ids = [str(point.get("id")) for point in points]
        if len(actual_ids) != len(set(actual_ids)):
            raise SparseUpgradeError("sparse owner readback returned duplicate point IDs")
        actual = {str(point.get("id")): point for point in points}
        if set(actual) != set(ids):
            missing = sorted(set(ids) - set(actual))
            raise SparseUpgradeError(f"sparse owner readback is missing points: {missing!r}")
        for binding in batch:
            self._validate_owner_point(
                actual[sparse_owner_point_id(binding.index)],
                term=binding.term,
                index=binding.index,
                marker=marker,
            )

    def convert(
        self,
        *,
        confirm: bool,
        lock_held: bool,
        barrier_held: bool,
        old_writers_stopped: bool,
    ) -> dict[str, Any]:
        """Seed missing owner slots without deleting or rewriting any alias."""

        for name, value in (
            ("confirm", confirm),
            ("lock-held", lock_held),
            ("barrier-held", barrier_held),
            ("old-writers-stopped", old_writers_stopped),
        ):
            if value is not True:
                raise SparseUpgradeError(f"convert requires --{name}")

        self._ensure_supported_version()
        with self._open_snapshot() as (marker, manifest, _initial_plan):
            for batch in self._missing_owner_batches(manifest):
                self._assert_marker_unchanged(marker)
                self._write_owner_batch(batch, marker=marker)
                self._read_owner_batch(batch, marker=marker)

            self._assert_marker_unchanged(marker)
            with self._open_snapshot() as (final_marker, final_manifest, final_plan):
                if final_marker != marker:
                    raise SparseUpgradeError(
                        "metadata marker changed during sparse owner conversion"
                    )
                self._compare_manifests(manifest, final_manifest)
                if final_plan.missing_owner_count:
                    raise SparseUpgradeError(
                        "sparse owner conversion finished with missing owner points"
                    )
                result = final_plan.to_dict()
            self._assert_marker_unchanged(marker)
            return result


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default=os.environ.get("QDRANT_URL"))
    parser.add_argument(
        "--target-data-collection",
        "--data-collection",
        dest="data_collection",
        required=True,
    )
    parser.add_argument(
        "--target-metadata-collection",
        "--metadata-collection",
        dest="metadata_collection",
        required=True,
    )
    parser.add_argument("--timeout-seconds", type=float, required=True)
    subparsers = parser.add_subparsers(dest="command")
    subparsers.add_parser("preflight", help="validate and print a read-only plan")
    convert_parser = subparsers.add_parser(
        "convert",
        help="seed missing per-index owner points",
    )
    for flag in ("confirm", "lock-held", "barrier-held", "old-writers-stopped"):
        convert_parser.add_argument(f"--{flag}", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if not args.url:
        print("Qdrant URL is required via --url or QDRANT_URL", file=sys.stderr)
        return 2
    try:
        client = QdrantRestClient(
            args.url,
            api_key=os.environ.get("QDRANT_API_KEY"),
            timeout_seconds=args.timeout_seconds,
        )
        operation = SparseDictionaryUpgrade(
            client=client,
            data_collection=args.data_collection,
            metadata_collection=args.metadata_collection,
        )
        result = (
            operation.convert(
                confirm=args.confirm,
                lock_held=args.lock_held,
                barrier_held=args.barrier_held,
                old_writers_stopped=args.old_writers_stopped,
            )
            if args.command == "convert"
            else operation.preflight()
        )
        payload = result.to_dict() if isinstance(result, SparseUpgradePlan) else result
        print(json.dumps(payload, sort_keys=True))
        return 0
    except (QdrantError, SparseUpgradeError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
