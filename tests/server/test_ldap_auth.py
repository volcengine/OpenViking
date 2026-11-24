from unittest.mock import Mock

import pytest

from openviking.server.auth.ldap_config import LDAPConfig
from openviking.server.auth.plugins.ldap import LDAPAuthPlugin


@pytest.mark.parametrize(
    "pattern",
    [
        "uid=%(username)s,ou=users,dc=example,dc=com",
        "uid=%s,ou=users,dc=example,dc=com",
    ],
)
def test_direct_bind_preserves_escaped_identity_without_server_attributes(pattern: str) -> None:
    username = "victim,ou=attacker"
    dn = r"uid=victim\,ou\=attacker,ou=users,dc=example,dc=com"
    connection = Mock()
    plugin = LDAPAuthPlugin()
    plugin._config = LDAPConfig(
        host="directory.test", base_dn="dc=example,dc=com", user_dn_pattern=pattern
    )
    plugin._new_connection = Mock(return_value=connection)
    plugin._search_user_attributes = Mock(return_value=(None, {}))

    attributes = plugin._authenticate_ldap(username, "test-password")

    connection.simple_bind_s.assert_called_once_with(dn, "test-password")
    assert attributes == {"uid": [username.encode()], "dn": [dn.encode()]}
