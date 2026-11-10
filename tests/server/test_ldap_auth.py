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
