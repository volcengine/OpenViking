# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Transport and TLS contracts for shared MySQL connection settings."""

import ssl

import pytest

from openviking.storage.mysql_config import mysql_connection_options

PARAMS = {"user": "u", "password": "p@ss:/?#%", "database": "d"}


def test_mysql_connection_preserves_credentials_socket_and_pool_settings():
    url, options = mysql_connection_options(
        {
            **PARAMS,
            "unix_socket": "/tmp/mysql.sock",
            "port": "3307",
            "maxsize": 2,
            "connect_timeout": 3,
        }
    )
    assert url.drivername == "mysql+aiomysql"
    assert url.password == PARAMS["password"]
    assert url.port == 3307
    assert url.query == {"charset": "utf8mb4"}
    assert options == {
        "pool_size": 2,
        "max_overflow": 0,
        "pool_pre_ping": True,
        "pool_recycle": 3600,
        "connect_args": {"connect_timeout": 3, "unix_socket": "/tmp/mysql.sock"},
        "isolation_level": "READ COMMITTED",
    }


def test_mysql_tls_requires_certificate_verification():
    _, options = mysql_connection_options({**PARAMS, "ssl": True})
    context = options["connect_args"]["ssl"]
    assert isinstance(context, ssl.SSLContext)
    assert context.verify_mode == ssl.CERT_REQUIRED
    assert context.check_hostname


def test_mysql_tls_mapping_loads_ca_and_client_certificate(monkeypatch):
    from openviking.storage import mysql_config

    class Context:
        check_hostname = True

        def load_cert_chain(self, cert, key):
            self.client_certificate = (cert, key)

    context = Context()
    ca_files = []

    def create_context(*, cafile):
        ca_files.append(cafile)
        return context

    monkeypatch.setattr(mysql_config.ssl_module, "create_default_context", create_context)
    _, options = mysql_connection_options(
        {
            **PARAMS,
            "ssl": {
                "ca": "/ca.pem",
                "cert": "/client.pem",
                "key": "/client.key",
                "check_hostname": False,
            },
        }
    )
    assert ca_files == ["/ca.pem"]
    assert context.client_certificate == ("/client.pem", "/client.key")
    assert not context.check_hostname
    assert options["connect_args"]["ssl"] is context


@pytest.mark.parametrize(
    ("override", "message"),
    [
        ({"unknown": True}, "Unknown MySQL connection parameters"),
        ({"ssl": {"unknown": True}}, "Unknown MySQL TLS options"),
        ({"ssl": "invalid"}, "ssl must be"),
    ],
)
def test_mysql_connection_rejects_unknown_or_invalid_options(override, message):
    with pytest.raises(ValueError, match=message):
        mysql_connection_options({**PARAMS, **override})
