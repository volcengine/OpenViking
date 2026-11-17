import pytest

from openviking.server.auth.plugins.ldap import LDAPAuthPlugin, _format_user_dn


@pytest.mark.parametrize(
    "pattern",
    [
        "uid=%(username)s,ou=users,dc=example,dc=com",
        "uid=%s,ou=users,dc=example,dc=com",
    ],
)
def test_format_user_dn_escapes_username(pattern: str) -> None:
    dn = _format_user_dn(
        pattern,
        "victim,ou=attacker",
    )

    assert dn == r"uid=victim\,ou\=attacker,ou=users,dc=example,dc=com"


def test_attrs_from_dn_round_trips_escaped_rdn_value() -> None:
    username = "victim,ou=attacker"

    dn = _format_user_dn(
        "uid=%s,ou=users,dc=example,dc=com",
        username,
    )

    assert dn == r"uid=victim\,ou\=attacker,ou=users,dc=example,dc=com"
    assert LDAPAuthPlugin._attrs_from_dn(dn) == {"uid": [username.encode()]}


async def test_ldap_rejection_logs_omit_directory_credentials_and_error_text(caplog, monkeypatch):
    import logging
    from types import SimpleNamespace
    from unittest.mock import AsyncMock, Mock

    from starlette.requests import Request

    from openviking.server.auth.plugins import ldap as module
    from openviking_cli.exceptions import UnauthenticatedError

    class DirectoryError(Exception):
        pass

    secret = "cn=private-directory-user,dc=private password=secret"
    monkeypatch.setattr(module, "LDAP_AVAILABLE", True)
    monkeypatch.setattr(module, "ldap", SimpleNamespace(LDAPError=DirectoryError), raising=False)
    monkeypatch.setattr(module.logger, "handlers", [caplog.handler])
    caplog.set_level(logging.DEBUG, logger=module.logger.name)
    plugin = LDAPAuthPlugin()
    plugin._config = SimpleNamespace()
    plugin._mapper = object()
    plugin._extract_credentials = AsyncMock(return_value=(secret, "private-password"))
    plugin._authenticate_ldap = Mock(side_effect=DirectoryError(secret))
    with pytest.raises(UnauthenticatedError):
        await plugin.resolve_identity(Request({"type": "http", "headers": []}))
    assert len(caplog.records) == 1
    record = caplog.records[0]
    assert record.levelno == logging.INFO
    assert record.error_type == "DirectoryError"
    assert record.error_code == "UNAUTHENTICATED"
    assert secret not in repr(record.__dict__)
    assert "private-password" not in repr(record.__dict__)
    assert record.exc_info is None
