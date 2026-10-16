# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""openGauss collection metadata and SPQ distribution operations."""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from openviking_cli.utils import get_logger

from .sql import (
    _META_TABLE,
    _field_to_column_ddl,
    _is_undefined_table_error,
    _quote_identifier,
    _validate_identifier,
)

logger = get_logger(__name__)


def _validate_distributed_environment(conn) -> None:
    cursor = conn.cursor()
    try:
        cursor.execute(
            """
            SELECT EXISTS (
                SELECT 1 FROM pg_extension WHERE extname IN ('spq', 'spq_plugin_v2')
            ),
            EXISTS (
                SELECT 1 FROM pg_proc WHERE proname = 'create_distributed_table'
            ),
            EXISTS (
                SELECT 1 FROM pg_proc WHERE proname = 'create_reference_table'
            )
            """
        )
        extension_exists, distributed_function_exists, reference_function_exists = cursor.fetchone()
        if not extension_exists:
            raise RuntimeError(
                "openGauss distributed mode requires the spq extension on the CN node"
            )
        if not distributed_function_exists:
            raise RuntimeError(
                "openGauss distributed mode requires create_distributed_table on the CN node"
            )
        if not reference_function_exists:
            logger.info(
                "opengauss_adapter: CN has no create_reference_table; metadata tables "
                "will use spq hash distribution"
            )

        cursor.execute(
            """
            SELECT COUNT(*)
            FROM pg_dist_node
            WHERE isactive = true
              AND (noderole IS NULL OR noderole = 'primary')
            """
        )
        worker_count = int(cursor.fetchone()[0])
        if worker_count < 1:
            raise RuntimeError("openGauss distributed mode requires at least one active DN worker")

        _probe_distributed_workers(cursor)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        cursor.close()


def _probe_distributed_workers(cursor) -> None:
    """Verify that every node which will host shards accepts connections from the CN.

    Shards only live on DN workers, so ``run_command_on_workers`` is the authoritative
    probe. ``run_command_on_all_nodes`` also dials the CN itself when the coordinator is
    registered in ``pg_dist_node``; many spq deployments never configure that loopback
    authentication, so it is used only as a fallback when the worker variant is missing.
    """
    cursor.execute(
        """
        SELECT EXISTS (SELECT 1 FROM pg_proc WHERE proname = 'run_command_on_workers'),
               EXISTS (SELECT 1 FROM pg_proc WHERE proname = 'run_command_on_all_nodes')
        """
    )
    has_worker_probe, has_all_nodes_probe = cursor.fetchone()
    if has_worker_probe:
        probe_sql = (
            "SELECT nodename || ':' || nodeport, success, result "
            "FROM run_command_on_workers('SELECT 1')"
        )
    elif has_all_nodes_probe:
        probe_sql = (
            "SELECT nodeid, success, result FROM run_command_on_all_nodes('SELECT 1', true, false)"
        )
    else:
        return
    try:
        cursor.execute(probe_sql)
        rows = cursor.fetchall()
    except Exception as exc:
        # spq implements these helpers in PL/pgSQL and raises on the first unreachable
        # node instead of reporting ``success = false`` rows.
        raise RuntimeError(f"openGauss distributed workers are not reachable: {exc}") from exc
    failed_nodes = [(node, result) for node, success, result in rows if not success]
    if failed_nodes:
        raise RuntimeError(f"openGauss distributed workers are not reachable: {failed_nodes}")


def _distributed_table_kind(conn, table_name: str) -> Optional[str]:
    _validate_identifier(table_name, kind="openGauss distributed table name")
    cursor = conn.cursor()
    try:
        cursor.execute(
            """
            SELECT CASE partmethod
                WHEN 'n' THEN 'reference'
                ELSE 'distributed'
            END
            FROM pg_dist_partition
            WHERE logicalrelid = %s::regclass
            """,
            (table_name,),
        )
        row = cursor.fetchone()
        conn.commit()
        return row[0] if row else None
    except Exception:
        conn.rollback()
        raise
    finally:
        cursor.close()


def _verify_distributed_table_kind(conn, table_name: str, expected_kind: str) -> None:
    actual_kind = _distributed_table_kind(conn, table_name)
    if actual_kind != expected_kind:
        raise RuntimeError(
            f"openGauss table {table_name!r} expected {expected_kind} catalog state, "
            f"got {actual_kind or 'local'}"
        )


def _is_table_already_distributed(conn, table_name: str) -> bool:
    """Return True if *table_name* is registered in the distribution catalog.

    Checks ``pg_dist_partition`` populated by spqplugin_v2 or a Citus-compatible
    extension.
    The function returns False gracefully when the extension is not installed
    (undefined ``pg_dist_partition`` relation); any other backend failure
    propagates to the caller.
    """
    cur = conn.cursor()
    try:
        cur.execute(
            """
            SELECT 1 FROM pg_dist_partition
            WHERE logicalrelid = %s::regclass
            """,
            (table_name,),
        )
        row = cur.fetchone()
        conn.commit()
        return row is not None
    except Exception as error:
        conn.rollback()
        if _is_undefined_table_error(error):
            return False
        raise
    finally:
        cur.close()


def _try_make_distributed_table(
    conn,
    table_name: str,
    shard_count: int = 32,
    distribution_column: str = "id",
) -> None:
    """Convert *table_name* to a distributed table via ``create_distributed_table``.

    Distributes by *distribution_column* (hash partitioning) with the given
    *shard_count*, using the openGauss spqplugin_v2 positional signature
    ``create_distributed_table(name, column, 'hash', shard_count)``.
    This is a no-op if the table is already distributed.
    Failures are raised because distributed mode must not silently degrade
    to a standalone table.
    """
    _validate_identifier(table_name, kind="openGauss distributed table name")
    _validate_identifier(distribution_column, kind="openGauss distribution column name")
    if _is_table_already_distributed(conn, table_name):
        _verify_distributed_table_kind(conn, table_name, "distributed")
        logger.info("opengauss_adapter: table '%s' is already distributed, skipping", table_name)
        return
    cur = conn.cursor()
    try:
        cur.execute(
            f"SELECT create_distributed_table(%s, %s, 'hash', {int(shard_count)})",
            (table_name, distribution_column),
        )
        conn.commit()
        _verify_distributed_table_kind(conn, table_name, "distributed")
        logger.info(
            "opengauss_adapter: distributed table '%s' created with %d shards",
            table_name,
            shard_count,
        )
    except Exception as error:
        conn.rollback()
        raise RuntimeError(
            f"Failed to distribute openGauss table {table_name!r}; "
            "ensure spq_plugin_v2 is installed on the CN node"
        ) from error
    finally:
        cur.close()


def _try_make_metadata_table_distributed(
    conn,
    table_name: str,
    distribution_column: str,
    shard_count: int = 32,
) -> None:
    """Distribute metadata according to the capabilities of the connected CN.

    Citus-compatible deployments may expose ``create_reference_table``. The
    official openGauss spqplugin_v2 API exposes hash-distributed tables only,
    so metadata falls back to hash distribution by its primary key.
    """
    _validate_identifier(table_name, kind="openGauss metadata table name")
    _validate_identifier(distribution_column, kind="openGauss metadata distribution column")
    if _is_table_already_distributed(conn, table_name):
        actual_kind = _distributed_table_kind(conn, table_name)
        if actual_kind not in {"distributed", "reference"}:
            raise RuntimeError(f"openGauss metadata table {table_name!r} has invalid catalog state")
        return

    cursor = conn.cursor()
    try:
        cursor.execute(
            "SELECT EXISTS (SELECT 1 FROM pg_proc WHERE proname='create_reference_table')"
        )
        supports_reference_tables = bool(cursor.fetchone()[0])
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        cursor.close()

    if supports_reference_tables:
        cursor = conn.cursor()
        try:
            cursor.execute("SELECT create_reference_table(%s)", (table_name,))
            conn.commit()
        except Exception as error:
            conn.rollback()
            raise RuntimeError(
                f"Failed to create openGauss reference metadata table {table_name!r}"
            ) from error
        finally:
            cursor.close()
        _verify_distributed_table_kind(conn, table_name, "reference")
        return

    _try_make_distributed_table(
        conn,
        table_name,
        shard_count=shard_count,
        distribution_column=distribution_column,
    )


def _create_collection_table(
    conn,
    name: str,
    meta: Dict[str, Any],
    dim: int,
    distributed: bool = False,
    shard_count: int = 32,
    on_table_created=None,
) -> None:
    """Create the collection data table and optionally distribute it.

    In distributed mode the table is converted to a distributed table keyed on
    ``id`` after creation.  The caller must have already created / ensured the
    metadata tables so their distributed form is initialized first.
    """
    fields: List[Dict[str, Any]] = meta.get("Fields", [])
    col_ddls = []
    has_vector = False

    for field in fields:
        ftype = field.get("FieldType") or field.get("field_type") or field.get("type", "string")
        if ftype == "vector":
            has_vector = True
            continue
        field_name = field.get("FieldName") or field.get("field_name") or field.get("name", "")
        if field_name == "content":
            continue
        ddl = _field_to_column_ddl(field)
        if ddl:
            col_ddls.append(ddl)

    if has_vector and dim > 0:
        col_ddls.append(f"vector vector({dim})")

    col_defs = ", ".join(col_ddls) if col_ddls else ""
    sep = ", " if col_defs else ""
    quoted_name = _quote_identifier(name, kind="openGauss collection name")
    sql = f"""
        CREATE TABLE {quoted_name} (
            id VARCHAR(256) PRIMARY KEY{sep}{col_defs}
        )
    """
    cur = conn.cursor()
    try:
        cur.execute(sql)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        cur.close()

    if on_table_created is not None:
        on_table_created()

    if distributed:
        _try_make_distributed_table(conn, name, shard_count)


def _ensure_meta_table(conn, distributed: bool = False):
    """Create the global collection metadata table if it doesn't exist.

    In distributed mode the table becomes a reference table when supported;
    standard spqplugin_v2 uses hash distribution by ``table_name``.
    """
    cur = conn.cursor()
    try:
        cur.execute(
            f"""
            CREATE TABLE IF NOT EXISTS "{_META_TABLE}" (
                table_name VARCHAR(256) PRIMARY KEY,
                meta_json  TEXT NOT NULL
            )
            """
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        cur.close()

    if distributed:
        _try_make_metadata_table_distributed(conn, _META_TABLE, "table_name")


def _save_collection_meta(conn, name: str, meta: Dict[str, Any], distributed: bool = False):
    _ensure_meta_table(conn, distributed=distributed)
    cur = conn.cursor()
    try:
        # Use UPDATE -> INSERT for distributed compatibility
        meta_json = json.dumps(meta)
        cur.execute(
            f'UPDATE "{_META_TABLE}" SET meta_json = %s WHERE table_name = %s',
            (meta_json, name),
        )
        if cur.rowcount == 0:
            cur.execute(
                f'INSERT INTO "{_META_TABLE}" (table_name, meta_json) VALUES (%s, %s)',
                (name, meta_json),
            )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        cur.close()


def _load_collection_meta(conn, name: str, distributed: bool = False) -> Optional[Dict[str, Any]]:
    _ensure_meta_table(conn, distributed=distributed)
    cur = conn.cursor()
    try:
        cur.execute(
            f'SELECT meta_json FROM "{_META_TABLE}" WHERE table_name = %s',
            (name,),
        )
        row = cur.fetchone()
        conn.commit()
    except Exception:
        # A backend failure (broken connection, missing SELECT privilege,
        # catalog corruption, ...) must never masquerade as "collection does
        # not exist": the caller could then enter the creation flow and
        # rewrite metadata for a table that actually exists.
        conn.rollback()
        raise
    finally:
        cur.close()

    return json.loads(row[0]) if row else None
