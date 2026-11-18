# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""PostgreSQL dialect and connection settings for the relational account store."""

from sqlalchemy import exists, func, literal, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.engine import URL

from openviking.server.account_stores.sql import _DEFAULT_ACCOUNT_ID, SQLAccountStore
from openviking.server.account_stores.sql_schema import Account
from openviking_cli.exceptions import InvalidArgumentError


class PostgresAccountStore(SQLAccountStore):
    """Account storage in PostgreSQL; no Redis or pgvector extension required."""

    provider_name = "PostgreSQL"
    dependency_extra = "postgres"

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
            raise InvalidArgumentError(
                f"Unknown PostgreSQL account store parameters: {', '.join(sorted(unknown))}"
            )
        missing = [name for name in ("user", "database") if not values.get(name)]
        if missing:
            raise InvalidArgumentError(f"PostgreSQL account store requires: {', '.join(missing)}")
        try:
            maxsize = int(values.get("maxsize", 10))
            port = int(values.get("port", 5432))
            timeout = int(values.get("connect_timeout", 10))
        except (TypeError, ValueError) as exc:
            raise InvalidArgumentError(
                "maxsize, port and connect_timeout must be integers"
            ) from exc
        if maxsize < 1 or not 1 <= port <= 65535 or timeout < 1:
            raise InvalidArgumentError(
                "maxsize and connect_timeout must be positive; port must be between 1 and 65535"
            )
        sslmode = values.get("sslmode", "prefer")
        if sslmode not in {"disable", "allow", "prefer", "require", "verify-ca", "verify-full"}:
            raise InvalidArgumentError("Invalid PostgreSQL sslmode")
        connect_args = {
            "connect_timeout": timeout,
            "sslmode": sslmode,
            # Keep database-generated timestamps and returned values in UTC.
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

    def _default_account_insert(self):
        return (
            insert(Account)
            .from_select(
                ["service_name", "account_id"],
                select(literal(self._service_name), literal(_DEFAULT_ACCOUNT_ID)).where(
                    ~exists().where(Account.service_name == self._service_name)
                ),
            )
            .on_conflict_do_nothing(index_elements=["service_name", "account_id"])
        )

    def _revocation_timestamp(self):
        return func.clock_timestamp()
