# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Shared PostgreSQL transport settings and store-specific compatibility."""

import base64
from pathlib import Path

import pytest

from openviking.resource.watch_store_postgres import PostgresWatchStore
from openviking.server.account_stores.postgres import PostgresAccountStore
from openviking.service.task_store_postgres import PostgresTaskWorkStore
from openviking.storage.postgres_config import postgres_connection_options
from openviking_cli.exceptions import InvalidArgumentError

PARAMS = {"user": "u", "password": "p@ss:/?#%", "database": "d"}
ENCRYPTION_KEY = base64.urlsafe_b64encode(b"k" * 32).decode()
STORES = [
    pytest.param(PostgresWatchStore, "Watch store", ValueError, False, id="watch"),
    pytest.param(PostgresTaskWorkStore, "task store", ValueError, True, id="task"),
    pytest.param(PostgresAccountStore, "account store", InvalidArgumentError, True, id="account"),
]


def test_postgres_connection_preserves_credentials_tls_and_pool_settings():
    params = {
        **PARAMS,
        "host": "db.example",
        "port": "5433",
        "maxsize": "2",
        "connect_timeout": "3",
        "sslmode": "verify-full",
        "sslrootcert": Path("/ca.pem"),
        "sslcert": Path("/client.pem"),
        "sslkey": Path("/client.key"),
    }
    original = params.copy()
    url, options = postgres_connection_options(params)
    assert params == original
    assert url.drivername == "postgresql+psycopg"
    assert url.username == PARAMS["user"]
    assert url.password == PARAMS["password"]
    assert url.host == "db.example"
    assert url.port == 5433
    assert url.database == PARAMS["database"]
    assert options == {
        "pool_size": 2,
        "max_overflow": 0,
        "pool_pre_ping": True,
        "pool_recycle": 3600,
        "connect_args": {
            "connect_timeout": 3,
            "sslmode": "verify-full",
            "sslrootcert": "/ca.pem",
            "sslcert": "/client.pem",
            "sslkey": "/client.key",
        },
        "isolation_level": "READ COMMITTED",
    }


@pytest.mark.parametrize("store_class,context,error_type,utc", STORES)
@pytest.mark.parametrize(
    "sslmode", ["disable", "allow", "prefer", "require", "verify-ca", "verify-full"]
)
def test_postgres_stores_share_tls_rules_and_preserve_timezone(
    store_class, context, error_type, utc, sslmode
):
    params = {**PARAMS, "sslmode": sslmode, "sslrootcert": "/ca.pem"}
    url, options = store_class._connection_options(params)
    shared_url, shared_options = postgres_connection_options(params)
    assert url == shared_url
    if utc:
        shared_options["connect_args"]["options"] = "-c timezone=UTC"
    assert options == shared_options
    # Store options must not leak into later shared calls.
    assert "options" not in postgres_connection_options(params)[1]["connect_args"]


@pytest.mark.parametrize("store_class,context,error_type,utc", STORES)
@pytest.mark.parametrize(
    "override,message",
    [
        ({"user": ""}, "PostgreSQL {context} requires: user"),
        ({"database": None}, "PostgreSQL {context} requires: database"),
        ({"port": 0}, "port must be between 1 and 65535"),
        ({"port": 65536}, "port must be between 1 and 65535"),
        ({"port": "invalid"}, "must be integers"),
        ({"maxsize": None}, "must be integers"),
        ({"maxsize": 0}, "must be positive"),
        ({"connect_timeout": -1}, "must be positive"),
        ({"sslmode": "invalid"}, "Invalid PostgreSQL sslmode"),
        ({"ssl": True}, "Unknown PostgreSQL {context} parameters: ssl"),
        ({"options": "-c timezone=UTC"}, "Unknown PostgreSQL {context} parameters: options"),
    ],
)
def test_postgres_stores_preserve_validation_errors(
    store_class, context, error_type, utc, override, message
):
    with pytest.raises(error_type, match=message.format(context=context)):
        store_class._connection_options({**PARAMS, **override})


@pytest.mark.parametrize("password", [None, ""])
def test_postgres_password_is_optional(password):
    url, _ = postgres_connection_options({**PARAMS, "password": password})
    assert url.password == password
    without_password = {name: value for name, value in PARAMS.items() if name != "password"}
    assert postgres_connection_options(without_password)[0].password is None


def test_postgres_stores_consume_their_own_parameters():
    watch_params = {**PARAMS, "table": "custom_watch", "credential_encryption_key": ENCRYPTION_KEY}
    task_params = {**PARAMS, "table": "custom_tasks", "service_name": "task-service"}
    account_params = {
        **PARAMS,
        "credential_storage": "encrypted",
        "credential_encryption_key": ENCRYPTION_KEY,
    }
    originals = [params.copy() for params in (watch_params, task_params, account_params)]
    watch = PostgresWatchStore(service_name="watch-service", params=watch_params)
    task = PostgresTaskWorkStore(params=task_params)
    account = PostgresAccountStore(service_name="account-service", params=account_params)
    assert watch._tasks.name == "custom_watch"
    assert task._service_name == "task-service"
    assert account._credential_storage == "encrypted"
    assert account._credential_cipher is not None
    assert [watch_params, task_params, account_params] == originals
