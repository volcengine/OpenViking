# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""PostgreSQL Watch metadata, occupancy and encrypted source credentials."""

from typing import Any, Sequence

from sqlalchemy import JSON, func, literal_column
from sqlalchemy.engine import URL

from openviking.resource.watch_store_sql import SQLWatchStore


class PostgresWatchStore(SQLWatchStore):
    dialect_name = "postgresql"
    target_prefix_index_chars = 128

    @property
    def provider(self) -> str:
        return "postgres"

    @property
    def _target_key_expression(self) -> Any:
        # Keep index constants literal so prepared generic plans can match the DDL.
        return func.substr(self._tasks.c.target_uri_key, literal_column("1"), literal_column("128"))

    @staticmethod
    def _connection_options(values: dict[str, Any]) -> tuple[URL, dict[str, Any]]:
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
                f"Unknown PostgreSQL Watch store parameters: {', '.join(sorted(unknown))}"
            )
        missing = [name for name in ("user", "database") if not values.get(name)]
        if missing:
            raise ValueError(f"PostgreSQL Watch store requires: {', '.join(missing)}")
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
        connect_args = {"connect_timeout": timeout, "sslmode": sslmode}
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

    def _public_payload(self, names: Sequence[str]) -> Any:
        args: list[Any] = []
        for name in names:
            args.extend([name, self._tasks.c.payload[name]])
        return func.json_build_object(*args, type_=JSON)
