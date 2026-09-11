# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Local MVCC catalog for versioned CodeGraph generations."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Iterable, Optional, Sequence

from openviking.codegraph.models import (
    CodeGraphHit,
    GraphExpansion,
    GraphManifest,
    ReadView,
    RevisionRef,
)
from openviking.codegraph.sqlite_index import CodeGraphIndex

MAX_EPOCH = 2**63 - 1
_CATALOG_SCHEMA_VERSION = 1
_CATALOG_SCHEMA = """
CREATE TABLE IF NOT EXISTS metadata (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS revisions (
    revision_id TEXT PRIMARY KEY,
    repo_id TEXT NOT NULL,
    commit_sha TEXT NOT NULL,
    source_snapshot_oid TEXT NOT NULL,
    graph_generation TEXT NOT NULL,
    index_path TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS bindings (
    repo_id TEXT NOT NULL,
    revision_id TEXT NOT NULL REFERENCES revisions(revision_id),
    valid_from INTEGER NOT NULL,
    valid_to INTEGER NOT NULL,
    PRIMARY KEY(repo_id, valid_from),
    CHECK(valid_from >= 0),
    CHECK(valid_to > valid_from)
);

CREATE INDEX IF NOT EXISTS bindings_lookup_idx
ON bindings(repo_id, valid_from, valid_to);
"""


class CatalogConflictError(RuntimeError):
    """Raised when publication is based on an obsolete catalog epoch."""


class InvalidRevisionError(RuntimeError):
    """Raised when a revision does not match its sealed graph."""


class LocalCodeGraphCatalog:
    """SQLite-backed local catalog with cross-process transactional CAS."""

    def __init__(self, path: Path, *, account_id: str):
        if not account_id:
            raise ValueError("account_id is required")
        self.path = path
        self.account_id = account_id
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=30.0, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=FULL")
        return conn

    def _initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        conn = self._connect()
        try:
            conn.executescript(_CATALOG_SCHEMA)
            conn.execute(
                "INSERT OR IGNORE INTO metadata(key, value) VALUES ('schema_version', ?)",
                (str(_CATALOG_SCHEMA_VERSION),),
            )
            conn.execute(
                "INSERT OR IGNORE INTO metadata(key, value) VALUES ('account_id', ?)",
                (self.account_id,),
            )
            conn.execute("INSERT OR IGNORE INTO metadata(key, value) VALUES ('epoch', '0')")
            stored_account = conn.execute(
                "SELECT value FROM metadata WHERE key = 'account_id'"
            ).fetchone()
            stored_schema = conn.execute(
                "SELECT value FROM metadata WHERE key = 'schema_version'"
            ).fetchone()
            if stored_account is None or stored_account["value"] != self.account_id:
                raise ValueError("catalog belongs to a different account")
            if stored_schema is None or int(stored_schema["value"]) != _CATALOG_SCHEMA_VERSION:
                raise RuntimeError("unsupported CodeGraph catalog schema")
        finally:
            conn.close()

    def current_epoch(self) -> int:
        conn = self._connect()
        try:
            row = conn.execute("SELECT value FROM metadata WHERE key = 'epoch'").fetchone()
            if row is None:
                raise RuntimeError("catalog epoch is missing")
            return int(row["value"])
        finally:
            conn.close()

    def acquire_view(self) -> ReadView:
        return ReadView(account_id=self.account_id, epoch=self.current_epoch())

    @staticmethod
    def _validate_manifest(manifest: GraphManifest) -> None:
        index = CodeGraphIndex(manifest.index_path)
        expected = {
            "repo_id": manifest.repo_id,
            "revision_id": manifest.revision_id,
            "commit_sha": manifest.commit_sha,
            "source_snapshot_oid": manifest.source_snapshot_oid,
            "graph_generation": manifest.graph_generation,
            "graph_id_schema_version": str(manifest.graph_id_schema_version),
        }
        actual = index.metadata
        mismatches = {
            key: (expected_value, actual.get(key))
            for key, expected_value in expected.items()
            if str(expected_value) != actual.get(key)
        }
        if mismatches:
            raise InvalidRevisionError(f"manifest does not match sealed graph: {mismatches}")

    def publish(self, manifest: GraphManifest, *, expected_epoch: int) -> RevisionRef:
        """Publish one repository revision using an epoch compare-and-swap."""

        self._validate_manifest(manifest)
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            actual_row = conn.execute("SELECT value FROM metadata WHERE key = 'epoch'").fetchone()
            actual_epoch = int(actual_row["value"]) if actual_row else -1
            if actual_epoch != expected_epoch:
                raise CatalogConflictError(
                    f"catalog epoch conflict: expected {expected_epoch}, actual {actual_epoch}"
                )
            if actual_epoch >= MAX_EPOCH - 1:
                raise OverflowError("catalog epoch space is exhausted")
            next_epoch = actual_epoch + 1

            existing = conn.execute(
                "SELECT * FROM revisions WHERE revision_id = ?",
                (manifest.revision_id,),
            ).fetchone()
            revision_values = (
                manifest.revision_id,
                manifest.repo_id,
                manifest.commit_sha,
                manifest.source_snapshot_oid,
                manifest.graph_generation,
                str(manifest.index_path.resolve()),
            )
            if existing is None:
                conn.execute(
                    """
                    INSERT INTO revisions(
                        revision_id, repo_id, commit_sha, source_snapshot_oid,
                        graph_generation, index_path
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    revision_values,
                )
            elif tuple(existing[key] for key in existing.keys()) != revision_values:
                raise InvalidRevisionError(
                    f"revision_id {manifest.revision_id!r} is already bound to other data"
                )

            current = conn.execute(
                """
                SELECT revision_id
                FROM bindings
                WHERE repo_id = ? AND valid_to = ?
                """,
                (manifest.repo_id, MAX_EPOCH),
            ).fetchall()
            if len(current) > 1:
                raise RuntimeError(f"multiple current revisions for repo {manifest.repo_id!r}")
            if current:
                conn.execute(
                    """
                    UPDATE bindings
                    SET valid_to = ?
                    WHERE repo_id = ? AND valid_to = ?
                    """,
                    (next_epoch, manifest.repo_id, MAX_EPOCH),
                )
            conn.execute(
                """
                INSERT INTO bindings(repo_id, revision_id, valid_from, valid_to)
                VALUES (?, ?, ?, ?)
                """,
                (manifest.repo_id, manifest.revision_id, next_epoch, MAX_EPOCH),
            )
            conn.execute(
                "UPDATE metadata SET value = ? WHERE key = 'epoch'",
                (str(next_epoch),),
            )
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
        finally:
            conn.close()

        return RevisionRef(
            repo_id=manifest.repo_id,
            revision_id=manifest.revision_id,
            commit_sha=manifest.commit_sha,
            source_snapshot_oid=manifest.source_snapshot_oid,
            graph_generation=manifest.graph_generation,
            index_path=manifest.index_path.resolve(),
            published_epoch=next_epoch,
        )

    def resolve(self, repo_id: str, view: ReadView) -> RevisionRef:
        if view.account_id != self.account_id:
            raise PermissionError("read view belongs to a different account")
        current = self.current_epoch()
        if view.epoch < 0 or view.epoch > current:
            raise ValueError(f"invalid read view epoch: {view.epoch}")

        conn = self._connect()
        try:
            row = conn.execute(
                """
                SELECT r.*, b.valid_from AS published_epoch
                FROM bindings b
                JOIN revisions r ON r.revision_id = b.revision_id
                WHERE b.repo_id = ?
                  AND b.valid_from <= ?
                  AND b.valid_to > ?
                """,
                (repo_id, view.epoch, view.epoch),
            ).fetchone()
        finally:
            conn.close()
        if row is None:
            raise KeyError(f"repository {repo_id!r} is not present at epoch {view.epoch}")
        return RevisionRef(
            repo_id=str(row["repo_id"]),
            revision_id=str(row["revision_id"]),
            commit_sha=str(row["commit_sha"]),
            source_snapshot_oid=str(row["source_snapshot_oid"]),
            graph_generation=str(row["graph_generation"]),
            index_path=Path(str(row["index_path"])),
            published_epoch=int(row["published_epoch"]),
        )


class VersionedCodeGraph:
    """Minimal query facade proving catalog-pinned graph reads."""

    def __init__(self, catalog: LocalCodeGraphCatalog):
        self.catalog = catalog

    def acquire_view(self) -> ReadView:
        return self.catalog.acquire_view()

    def search(
        self,
        repo_id: str,
        query: str,
        *,
        view: Optional[ReadView] = None,
        limit: int = 20,
        offset: int = 0,
        allowed_file_ids: Optional[Iterable[int]] = None,
    ) -> tuple[ReadView, RevisionRef, list[CodeGraphHit]]:
        selected_view = view or self.acquire_view()
        revision = self.catalog.resolve(repo_id, selected_view)
        hits = CodeGraphIndex(revision.index_path).search(
            query,
            limit=limit,
            offset=offset,
            allowed_file_ids=allowed_file_ids,
        )
        return selected_view, revision, hits

    def expand(
        self,
        repo_id: str,
        seed_ids: Sequence[str],
        *,
        view: ReadView,
        max_depth: int = 2,
        max_fanout: int = 20,
        max_nodes: int = 50,
        allowed_file_ids: Optional[Iterable[int]] = None,
    ) -> tuple[RevisionRef, GraphExpansion]:
        revision = self.catalog.resolve(repo_id, view)
        expansion = CodeGraphIndex(revision.index_path).expand(
            seed_ids,
            max_depth=max_depth,
            max_fanout=max_fanout,
            max_nodes=max_nodes,
            allowed_file_ids=allowed_file_ids,
        )
        return revision, expansion
