# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Read-only, opt-in discovery; never install extensions or infer readiness."""

from __future__ import annotations

from typing import Any


def _probe_error(error) -> dict[str, Any]:
    code = getattr(error, "pgcode", None)
    return {
        "state": "unknown"
        if code in {"42P01", "42703", "42883", "0A000"}
        else ("permission_denied" if code == "42501" else "error"),
        "sqlstate": code,
        "error": str(error),
    }


def _optional_probe(cursor, name, read):
    """Missing optional catalogs/functions mean unknown, not absence of a feature."""
    cursor.execute(f"SAVEPOINT {name}")
    try:
        result = read()
    except Exception as error:
        cursor.execute(f"ROLLBACK TO SAVEPOINT {name}")
        result = _probe_error(error)
    cursor.execute(f"RELEASE SAVEPOINT {name}")
    return result


def _setting_bool(value):
    if isinstance(value, str):
        value = value.lower()
        if value in {"on", "true", "1"}:
            return True
        if value in {"off", "false", "0"}:
            return False
    return None


def _probe_server_settings(cursor):
    cursor.execute(
        """SELECT name, setting FROM pg_catalog.pg_settings
           WHERE name IN (
               'server_encoding', 'client_encoding', 'default_transaction_read_only',
               'transaction_read_only', 'pgxc_node_name', 'pgxc_node_type',
               'node_role', 'cluster_run_mode', 'enable_multiple_nodes',
               'default_table_access_method', 'default_storage_engine',
               'default_table_storage_type', 'default_table_orientation',
               'enable_default_ustore_table') ORDER BY name"""
    )
    return {"state": "available", "values": dict(cursor.fetchall())}


def _probe_recovery(cursor):
    cursor.execute("SELECT pg_catalog.pg_is_in_recovery()")
    value = cursor.fetchone()[0]
    if not isinstance(value, bool):
        return {"state": "unknown", "in_recovery": None}
    return {"state": "available", "in_recovery": value}


def _probe_identity(cursor):
    cursor.execute("SELECT current_user, session_user")
    current, session = cursor.fetchone()
    return {"state": "available", "current_user": current, "session_user": session}


def _probe_index_methods(cursor):
    cursor.execute("SELECT amname FROM pg_catalog.pg_am ORDER BY amname")
    return {"state": "available", "names": [row[0] for row in cursor.fetchall()]}


def _probe_sparse_candidates(cursor):
    # Names are only candidate hints, never proof of a supported adapter path.
    names = ("sparsevec", "sparse_vector", "sparsevector")
    cursor.execute(
        """SELECT t.oid, n.nspname, t.typname,
                  pg_catalog.pg_type_is_visible(t.oid),
                  has_type_privilege(t.oid, 'USAGE')
           FROM pg_catalog.pg_type t
           JOIN pg_catalog.pg_namespace n ON n.oid = t.typnamespace
           WHERE t.typname IN (%s, %s, %s) AND t.typtype = 'b'
           ORDER BY n.nspname, t.typname""",
        names,
    )
    types = cursor.fetchall()
    cursor.execute(
        """SELECT o.oprleft, n.nspname, o.oprname,
                  pg_catalog.pg_operator_is_visible(o.oid),
                  has_function_privilege(o.oprcode, 'EXECUTE')
           FROM pg_catalog.pg_operator o
           JOIN pg_catalog.pg_namespace n ON n.oid = o.oprnamespace
           JOIN pg_catalog.pg_type t ON t.oid = o.oprleft
           WHERE t.typname IN (%s, %s, %s) AND t.typtype = 'b'
             AND o.oprright = t.oid AND o.oprname IN ('<->', '<#>', '<=>', '<+>')
           ORDER BY o.oprleft, n.nspname, o.oprname""",
        names,
    )
    operators: dict[Any, list[dict[str, Any]]] = {}
    for oid, schema, name, visible, permitted in cursor.fetchall():
        operators.setdefault(oid, []).append(
            {
                "schema": schema,
                "name": name,
                "visible": bool(visible),
                "executable": bool(permitted),
            }
        )
    candidates = []
    for oid, schema, name, visible, permitted in types:
        distance_ops = operators.get(oid, [])
        visible_ops = [op for op in distance_ops if op["visible"]]
        if not visible or not visible_ops:
            state = "unsupported"
        elif not permitted or not any(op["executable"] for op in visible_ops):
            state = "permission_denied"
        else:
            state = "available"
        candidates.append(
            {
                "schema": schema,
                "type": name,
                "visible": bool(visible),
                "usage_permitted": bool(permitted),
                "state": state,
                "operators": distance_ops,
            }
        )
    states = {candidate["state"] for candidate in candidates}
    state = (
        "available"
        if "available" in states
        else ("permission_denied" if "permission_denied" in states else "unsupported")
    )
    return {"state": state, "candidates": candidates}


def _probe_additional_capabilities(cursor, result):
    settings = _optional_probe(cursor, "ov_cap_settings", lambda: _probe_server_settings(cursor))
    values = settings.get("values", {})
    recovery = _optional_probe(cursor, "ov_cap_recovery", lambda: _probe_recovery(cursor))
    identity = _optional_probe(cursor, "ov_cap_identity", lambda: _probe_identity(cursor))
    distributed = _setting_bool(values.get("enable_multiple_nodes"))
    node_role = values.get("pgxc_node_type") or values.get("node_role")
    # A node name is not evidence of distributed mode: single-node openGauss
    # also exposes pgxc_node_name. Only explicit mode/role signals are used.
    mode = "unknown"
    distributed_role = str(node_role).lower() in {"coordinator", "datanode"}
    conflicting_signals = distributed is False and distributed_role
    if not conflicting_signals:
        if distributed is not None:
            mode = "distributed" if distributed else "single"
        elif distributed_role:
            mode = "distributed"
    deployment = {
        "state": "available"
        if mode != "unknown"
        else (settings["state"] if settings["state"] != "available" else "unknown"),
        "mode": mode,
        "conflicting_signals": conflicting_signals,
        "node_role": node_role,
        "node_name": values.get("pgxc_node_name"),
        "cluster_run_mode": values.get("cluster_run_mode"),
    }
    in_recovery = recovery.get("in_recovery")
    server = {
        "settings": settings,
        "deployment": deployment,
        "recovery": recovery,
        "role": {
            "state": recovery["state"],
            "value": "unknown"
            if in_recovery is None
            else ("standby" if in_recovery else "primary"),
        },
        "identity": identity,
        "read_only": {
            "state": "available"
            if _setting_bool(values.get("default_transaction_read_only")) is not None
            else (settings["state"] if settings["state"] != "available" else "unknown"),
            "default_transaction_read_only": _setting_bool(
                values.get("default_transaction_read_only")
            ),
            "transaction_read_only": _setting_bool(values.get("transaction_read_only")),
            "transaction_forced_read_only_by_probe": True,
        },
        "encoding": {
            "state": "available"
            if values.get("server_encoding") is not None
            else (settings["state"] if settings["state"] != "available" else "unknown"),
            "server": values.get("server_encoding"),
            "client": values.get("client_encoding"),
        },
    }
    result["server"] = server
    defaults = {
        name: values[name]
        for name in (
            "default_table_access_method",
            "default_storage_engine",
            "default_table_storage_type",
            "default_table_orientation",
            "enable_default_ustore_table",
        )
        if name in values
    }
    result["storage"] = {
        "state": "available"
        if defaults
        else (settings["state"] if settings["state"] != "available" else "unknown"),
        "observed_defaults": defaults,
        "table_storage_verified": False,
    }
    result["index_methods"] = _optional_probe(
        cursor, "ov_cap_methods", lambda: _probe_index_methods(cursor)
    )
    sparse = _optional_probe(cursor, "ov_cap_sparse", lambda: _probe_sparse_candidates(cursor))
    sparse.setdefault("candidates", [])
    sparse.update(
        {
            "implemented": False,
            "index_support_verified": False,
            "discovery_scope": "known_type_names_and_same_type_distance_operators",
        }
    )
    result["sparse"] = {"native": sparse}


def probe_fulltext_capabilities(conn) -> dict[str, Any]:
    """Own and roll back a READ ONLY transaction on an idle DB-API connection.

    The caller must serialize shared-connection access. Active transactions are
    rejected, not committed/rolled back. Catalog availability is not a guarantee
    that a particular table/storage layout can build an index. Independent
    savepoints isolate engine and optional deployment/sparse catalog failures.
    server.read_only.transaction_read_only describes this forced-read-only probe,
    NOT the instance's write capability. Storage observations are defaults only.
    sparse.native.available means a known visible type/operator candidate with
    usage/execute privileges, never an implemented or index-verified search path.
    """
    local = getattr(conn, "_local", None)
    physical = getattr(local, "connection", None) if local is not None else None
    target = physical if physical is not None else conn
    status_fn = getattr(target, "get_transaction_status", None)
    status = status_fn() if callable(status_fn) else None
    if isinstance(status, int) and status != 0:
        raise RuntimeError("Full-text capability probing requires an idle connection")
    if getattr(target, "autocommit", False) is True:
        raise ValueError("Full-text capability probing requires autocommit=False")

    result: dict[str, Any] = {
        "server_version": None,
        "bm25": {"state": "unknown"},
        "server": {"state": "unknown"},
        "storage": {"state": "unknown", "table_storage_verified": False},
        "index_methods": {"state": "unknown", "names": []},
        "sparse": {
            "native": {
                "state": "unknown",
                "implemented": False,
                "index_support_verified": False,
                "candidates": [],
            }
        },
    }
    cursor = None
    try:
        cursor = conn.cursor()
        # DB-API starts BEGIN implicitly; a nested BEGIN READ ONLY can be ignored.
        cursor.execute("SET TRANSACTION READ ONLY")
        cursor.execute("SELECT version()")
        result["server_version"] = cursor.fetchone()[0]
        cursor.execute("SAVEPOINT ov_capability_probe")
        try:
            cursor.execute(
                """SELECT
                    EXISTS (SELECT 1 FROM pg_catalog.pg_am WHERE amname = 'bm25'),
                    EXISTS (SELECT 1 FROM pg_catalog.pg_operator o
                        WHERE o.oprname = '<&>' AND o.oprleft = 'text'::regtype
                        AND o.oprright = 'text'::regtype
                        AND o.oprresult IN ('real'::regtype, 'double precision'::regtype,
                                            'numeric'::regtype)
                        AND pg_catalog.pg_operator_is_visible(o.oid)),
                    EXISTS (SELECT 1 FROM pg_catalog.pg_operator o
                        WHERE o.oprname = '<&>' AND o.oprleft = 'text'::regtype
                        AND o.oprright = 'text'::regtype
                        AND pg_catalog.pg_operator_is_visible(o.oid)
                        AND has_function_privilege(o.oprcode, 'EXECUTE'))"""
            )
            method, operator, permitted = cursor.fetchone()
            result["bm25"] = {
                "state": "unsupported"
                if not (method and operator)
                else ("available" if permitted else "permission_denied"),
                "access_method": bool(method),
                "text_operator": bool(operator),
            }
        except Exception as error:
            cursor.execute("ROLLBACK TO SAVEPOINT ov_capability_probe")
            result["bm25"] = _probe_error(error)
        cursor.execute("RELEASE SAVEPOINT ov_capability_probe")
        # Optional server/catalog observations never revoke successful engine probes.
        try:
            _probe_additional_capabilities(cursor, result)
        except Exception as error:
            result["additional_probe"] = _probe_error(error)
    except Exception as error:
        result["bm25"] = _probe_error(error)
    finally:
        if cursor is not None:
            try:
                conn.rollback()
            finally:
                cursor.close()
    return result
