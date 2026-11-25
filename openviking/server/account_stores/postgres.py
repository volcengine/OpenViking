# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""PostgreSQL dialect and connection settings for the relational account store."""

from sqlalchemy import exists, func, literal, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.engine import URL

from openviking.server.account_stores.sql import _DEFAULT_ACCOUNT_ID, SQLAccountStore
from openviking.server.account_stores.sql_schema import Account
from openviking.storage.postgres_config import postgres_connection_options
from openviking_cli.exceptions import InvalidArgumentError


class PostgresAccountStore(SQLAccountStore):
    """Account storage in PostgreSQL; no Redis or pgvector extension required."""

    provider_name = "PostgreSQL"
    dependency_extra = "postgres"

    @staticmethod
    def _connection_options(values: dict[str, object]) -> tuple[URL, dict[str, object]]:
        try:
            url, options = postgres_connection_options(values, context="account store")
        except ValueError as exc:
            raise InvalidArgumentError(str(exc)) from exc
        # Keep database-generated timestamps and returned values in UTC.
        options["connect_args"]["options"] = "-c timezone=UTC"
        return url, options

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
