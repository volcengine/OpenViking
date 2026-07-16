from openviking.core.identifiers import (
    normalize_identifier_part,
    validate_account_id,
    validate_identifier_part,
    validate_user_id,
)
from openviking_cli.utils import get_logger

logger = get_logger(__name__)

__all__ = [
    "UserIdentifier",
    "normalize_identifier_part",
    "validate_account_id",
    "validate_identifier_part",
    "validate_user_id",
]


class UserIdentifier(object):
    def __init__(self, account_id: str, user_id: str):
        self._account_id = account_id
        self._user_id = user_id

        verr = self._validate_error()
        if verr:
            logger.error(
                f"Invalid user identifier: {verr}. account_id={self._account_id} user_id={self._user_id}"
            )
            raise ValueError(verr)

    @classmethod
    def the_default_user(cls, default_username: str = "default"):
        return cls("default", default_username)

    def _validate_error(self) -> str:
        """Validate the user identifier using shared validation functions."""
        verr = validate_account_id(self._account_id)
        if verr:
            return verr
        verr = validate_user_id(self._user_id)
        if verr:
            return verr
        return ""

    @property
    def account_id(self) -> str:
        return self._account_id

    @property
    def user_id(self) -> str:
        return self._user_id

    def user_space_name(self) -> str:
        """User-level space name."""
        return self._user_id

    def to_dict(self):
        return {
            "account_id": self._account_id,
            "user_id": self._user_id,
        }

    @classmethod
    def from_dict(cls, data: dict):
        return cls(data["account_id"], data["user_id"])

    def __str__(self) -> str:
        return f"{self._account_id}:{self._user_id}"

    def __repr__(self) -> str:
        return self.__str__()

    def __eq__(self, other):
        return self._account_id == other._account_id and self._user_id == other._user_id
