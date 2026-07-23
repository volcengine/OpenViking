import pytest

from openviking_cli.session.user_id import UserIdentifier


class TestUserIdentifier:
    def test_same_user_produces_same_space(self):
        u1 = UserIdentifier("acct", "alice")
        u2 = UserIdentifier("acct", "alice")
        assert u1.user_space_name() == u2.user_space_name()
        assert u1 == u2

    def test_non_user_identifier_comparison_is_false(self):
        user = UserIdentifier("acct", "alice")

        assert user != object()

    def test_different_users_produce_different_spaces(self):
        u1 = UserIdentifier("acct", "alpha")
        u2 = UserIdentifier("acct", "beta")
        assert u1.user_space_name() != u2.user_space_name()

    @pytest.mark.parametrize("user_id", [".", "..", "team:alice"])
    def test_user_id_rejects_unsafe_path_segments(self, user_id):
        with pytest.raises(ValueError):
            UserIdentifier("acct", user_id)
