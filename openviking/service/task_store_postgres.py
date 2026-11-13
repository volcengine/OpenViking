# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""PostgreSQL connection settings for the transactional Task/Work store."""

from sqlalchemy.engine import URL
from sqlalchemy.exc import IntegrityError

from openviking.service.task_store_sql import SQLTaskWorkStore


class PostgresTaskWorkStore(SQLTaskWorkStore):
    """Shared durable Task/Work storage; no Redis or pgvector required."""

    provider_name = "PostgreSQL"
    dependency_extra = "postgres"
    dialect_name = "postgresql"

    @staticmethod
    def _connection_options(values: dict[str, object]) -> tuple[URL, dict[str, object]]:
        allowed = {
            "user",
            "password",
            "database",
            "host",
            "port",
            "maxsize",
            "connect_timeout",
            "sslmode",
            "sslrootcert",
            "sslcert",
            "sslkey",
        }
        unknown = values.keys() - allowed
        if unknown:
            raise ValueError(
                f"Unknown PostgreSQL task store parameters: {', '.join(sorted(unknown))}"
            )
        missing = [name for name in ("user", "database") if not values.get(name)]
        if missing:
            raise ValueError(f"PostgreSQL task store requires: {', '.join(missing)}")
        try:
            maxsize = int(values.get("maxsize", 10))
            port = int(values.get("port", 5432))
            timeout = int(values.get("connect_timeout", 10))
        except (TypeError, ValueError) as exc:
            raise ValueError("maxsize, port and connect_timeout must be integers") from exc
        if maxsize < 1 or not 1 <= port <= 65535 or timeout < 1:
            raise ValueError(
                "maxsize and connect_timeout must be positive; port must be between 1 and 65535"
            )
        sslmode = values.get("sslmode", "prefer")
        if sslmode not in {"disable", "allow", "prefer", "require", "verify-ca", "verify-full"}:
            raise ValueError("Invalid PostgreSQL sslmode")
        connect_args = {
            "connect_timeout": timeout,
            "sslmode": sslmode,
            "options": "-c timezone=UTC",
        }
        for name in ("sslrootcert", "sslcert", "sslkey"):
            if name in values:
                connect_args[name] = str(values[name])
        return URL.create(
            "postgresql+psycopg",
            username=str(values["user"]),
            password=str(values["password"]) if values.get("password") is not None else None,
            host=str(values.get("host", "127.0.0.1")),
            port=port,
            database=str(values["database"]),
        ), {
            "pool_size": maxsize,
            "max_overflow": 0,
            "pool_pre_ping": True,
            "pool_recycle": 3600,
            "connect_args": connect_args,
            "isolation_level": "READ COMMITTED",
        }

    @staticmethod
    def _is_duplicate_error(exc: IntegrityError) -> bool:
        return getattr(exc.orig, "sqlstate", None) == "23505"
