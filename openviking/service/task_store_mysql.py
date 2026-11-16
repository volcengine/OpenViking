# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""MySQL connection settings for the transactional Task/Work store."""

import ssl as ssl_module

from sqlalchemy.engine import URL
from sqlalchemy.exc import IntegrityError

from openviking.service.task_store_sql import SQLTaskWorkStore


class MySQLTaskWorkStore(SQLTaskWorkStore):
    provider_name = "MySQL"
    dependency_extra = "mysql"
    dialect_name = "mysql"

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
            "ssl",
            "unix_socket",
        }
        unknown = values.keys() - allowed
        if unknown:
            raise ValueError(f"Unknown MySQL task store parameters: {', '.join(sorted(unknown))}")
        missing = [name for name in ("user", "password", "database") if name not in values]
        if missing:
            raise ValueError(f"MySQL task store requires: {', '.join(missing)}")
        connect_args: dict[str, object] = {
            "connect_timeout": values.get("connect_timeout", 10),
        }
        if socket := values.get("unix_socket"):
            connect_args["unix_socket"] = socket
        ssl = values.get("ssl")
        if isinstance(ssl, dict):
            unknown_ssl = ssl.keys() - {"ca", "cert", "key", "check_hostname"}
            if unknown_ssl:
                raise ValueError(f"Unknown MySQL TLS options: {sorted(unknown_ssl)}")
            context = ssl_module.create_default_context(cafile=ssl.get("ca"))
            context.check_hostname = bool(ssl.get("check_hostname", True))
            if ssl.get("cert"):
                context.load_cert_chain(ssl["cert"], ssl.get("key"))
            connect_args["ssl"] = context
        elif ssl is True:
            connect_args["ssl"] = ssl_module.create_default_context()
        elif ssl not in (None, False):
            raise ValueError("ssl must be a boolean or TLS options mapping")
        return URL.create(
            "mysql+aiomysql",
            username=str(values["user"]),
            password=str(values["password"]),
            host=str(values.get("host", "127.0.0.1")),
            port=int(str(values.get("port", 3306))),
            database=str(values["database"]),
            query={"charset": "utf8mb4"},
        ), {
            "pool_size": int(values.get("maxsize", 10)),
            "max_overflow": 0,
            "pool_pre_ping": True,
            "pool_recycle": 3600,
            "connect_args": connect_args,
            "isolation_level": "READ COMMITTED",
        }

    @staticmethod
    def _is_duplicate_error(exc: IntegrityError) -> bool:
        args = getattr(getattr(exc, "orig", None), "args", ())
        return bool(args and args[0] == 1062)
