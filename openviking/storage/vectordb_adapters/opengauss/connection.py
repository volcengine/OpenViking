# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Thread-local transaction and pooled connection lifecycle."""

from __future__ import annotations

import threading

from openviking_cli.utils import get_logger

logger = get_logger(__name__)


class _PooledCursor:
    def __init__(self, connection_proxy: "_PooledConnectionProxy", connection, cursor):
        self._connection_proxy = connection_proxy
        self._connection = connection
        self._cursor = cursor
        self._closed = False

    def __getattr__(self, name: str):
        return getattr(self._cursor, name)

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            self._cursor.close()
        finally:
            self._connection_proxy.release(self._connection)


class _PooledConnectionProxy:
    """Compatibility connection backed by a psycopg2 threaded connection pool."""

    def __init__(self, pool):
        self._pool = pool
        self._local = threading.local()
        self._closed = False

    def _checkout(self):
        if self._closed:
            raise RuntimeError("openGauss connection pool is closed")
        connection = getattr(self._local, "connection", None)
        if connection is not None and getattr(connection, "closed", False):
            self._local.connection = None
            self._local.cursor_count = 0
            self._pool.putconn(connection, close=True)
            connection = None
        if connection is None:
            connection = self._pool.getconn()
            try:
                connection.autocommit = False
            except Exception:
                self._pool.putconn(connection, close=True)
                raise
            self._local.connection = connection
            self._local.cursor_count = 0
        return connection

    def cursor(self, *args, **kwargs):
        connection = self._checkout()
        self._local.cursor_count = getattr(self._local, "cursor_count", 0) + 1
        try:
            cursor = connection.cursor(*args, **kwargs)
        except Exception:
            self.release(connection)
            raise
        return _PooledCursor(self, connection, cursor)

    def commit(self) -> None:
        self._checkout().commit()

    def rollback(self) -> None:
        connection = getattr(self._local, "connection", None)
        if connection is not None and not getattr(connection, "closed", False):
            connection.rollback()

    def release(self, connection) -> None:
        if connection is not getattr(self._local, "connection", None):
            return
        cursor_count = max(getattr(self._local, "cursor_count", 1) - 1, 0)
        self._local.cursor_count = cursor_count
        if cursor_count > 0:
            return
        self._local.connection = None
        close_connection = bool(getattr(connection, "closed", False))
        try:
            if not close_connection:
                connection.rollback()
        except Exception:
            close_connection = True
        self._pool.putconn(connection, close=close_connection)

    def close(self) -> None:
        if self._closed:
            return
        connection = getattr(self._local, "connection", None)
        if connection is not None:
            self._local.connection = None
            self._local.cursor_count = 0
            self._pool.putconn(connection, close=True)
        self._pool.closeall()
        self._closed = True


def _create_connection_pool(
    *,
    host: str,
    port: int,
    user: str,
    password: str,
    db_name: str,
    min_size: int,
    max_size: int,
):
    psycopg2 = _import_psycopg2()
    return psycopg2.pool.ThreadedConnectionPool(
        minconn=min_size,
        maxconn=max_size,
        host=host,
        port=port,
        user=user,
        password=password,
        dbname=db_name,
        options="-c client_encoding=UTF8 -c search_path=public",
        connect_timeout=10,
    )


def _import_psycopg2():
    """Import psycopg2 (openGauss connector)."""
    try:
        import psycopg2  # noqa: PLC0415
        import psycopg2.pool  # noqa: PLC0415

        return psycopg2
    except ImportError as e:
        raise ImportError(
            "psycopg2 is required for the openGauss backend. Install it via:\n"
            '  pip install "openviking[opengauss]"\n'
            "or install the openGauss-connector-python-psycopg2 package, "
            "which exposes the same psycopg2 module interface."
        ) from e
