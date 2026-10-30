# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Thread-local transaction and pooled connection lifecycle."""

from __future__ import annotations

import math
import threading
import time

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
        except Exception:
            self._connection_proxy._mark_broken(self._connection)
            raise
        finally:
            self._connection_proxy.release(self._connection)


class _PooledConnectionProxy:
    """Thread-local transactions with bounded waits for pooled connections.

    Cursors must be used and closed by their creating thread. Closing the proxy
    invalidates outstanding connections, just like ThreadedConnectionPool.closeall.
    The timeout bounds capacity waits, not driver connect or network operations.
    """

    def __init__(self, pool, timeout: float = 10.0):
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)):
            raise ValueError("connection pool timeout must be a finite non-negative number")
        if not math.isfinite(timeout) or timeout < 0:
            raise ValueError("connection pool timeout must be a finite non-negative number")
        self._pool = pool
        self._timeout = float(timeout)
        self._local = threading.local()
        self._condition = threading.Condition()
        self._closed = False

    @staticmethod
    def _is_pool_exhausted(error: Exception) -> bool:
        # Keep ordinary mocks usable without importing the optional driver.
        if str(error) != "connection pool exhausted":
            return False
        psycopg2 = _import_psycopg2()
        return isinstance(error, psycopg2.pool.PoolError)

    def _clear_local(self) -> None:
        self._local.connection = None
        self._local.cursor_count = 0
        self._local.broken = False

    def _mark_broken(self, connection) -> None:
        if connection is getattr(self._local, "connection", None):
            self._local.broken = True

    def _putconn(self, connection, *, close: bool) -> None:
        with self._condition:
            try:
                if not self._closed:
                    self._pool.putconn(connection, close=close)
            finally:
                self._condition.notify_all()

    def _checkout(self):
        deadline = time.monotonic() + self._timeout
        with self._condition:
            if self._closed:
                raise RuntimeError("openGauss connection pool is closed")
            connection = getattr(self._local, "connection", None)
            if connection is not None and (
                getattr(connection, "closed", False) or getattr(self._local, "broken", False)
            ):
                if getattr(self._local, "cursor_count", 0) > 0:
                    # A replacement here would silently commit a different transaction.
                    raise RuntimeError("openGauss transaction connection is broken")
                self._clear_local()
                self._putconn(connection, close=True)
                connection = None
            if connection is not None:
                return connection
            while True:
                if self._closed:
                    raise RuntimeError("openGauss connection pool is closed")
                try:
                    connection = self._pool.getconn()
                except Exception as error:
                    if not self._is_pool_exhausted(error):
                        raise
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise TimeoutError(
                            "timed out waiting for an openGauss pooled connection"
                        ) from error
                    # Periodic rechecks also support connections returned directly to the pool.
                    self._condition.wait(min(remaining, 0.1))
                    continue
                try:
                    connection.autocommit = False
                except Exception:
                    self._putconn(connection, close=True)
                    raise
                self._local.connection = connection
                self._local.cursor_count = 0
                self._local.broken = False
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
        connection = self._checkout()
        try:
            connection.commit()
        except Exception:
            self._mark_broken(connection)
            raise
        finally:
            if getattr(self._local, "cursor_count", 0) == 0:
                self.release(connection)

    def rollback(self) -> None:
        connection = getattr(self._local, "connection", None)
        if connection is None:
            return
        try:
            if not self._closed and not getattr(connection, "closed", False):
                connection.rollback()
        except Exception:
            self._mark_broken(connection)
            raise
        finally:
            if getattr(self._local, "cursor_count", 0) == 0:
                self.release(connection)

    def release(self, connection) -> None:
        if connection is not getattr(self._local, "connection", None):
            return
        cursor_count = max(getattr(self._local, "cursor_count", 1) - 1, 0)
        self._local.cursor_count = cursor_count
        if cursor_count > 0:
            return
        close_connection = bool(
            getattr(connection, "closed", False) or getattr(self._local, "broken", False)
        )
        self._clear_local()
        with self._condition:
            if self._closed:
                return
            try:
                if not close_connection:
                    connection.rollback()
            except Exception:
                close_connection = True
            self._putconn(connection, close=close_connection)

    def close(self) -> None:
        with self._condition:
            if self._closed:
                return
            # Publish closure before closeall and wake waiters even if the driver fails.
            self._closed = True
            self._clear_local()
            try:
                self._pool.closeall()
            finally:
                self._condition.notify_all()


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
