import re

from openviking_cli.utils import get_logger

logger = get_logger(__name__)

# Validation pattern reused across different modules
# Note: hyphen (-) must be at the end or escaped to avoid being interpreted as a range
_VALIDATION_PATTERN = re.compile(r"^[a-zA-Z0-9_.@-]+$")


def validate_identifier_part(part: str, part_name: str) -> str | None:
    """Validate a single part of an identifier (account_id or user_id).

    Returns an error message if invalid, None if valid.
    """
    if not part:
        return f"{part_name} is empty"
    if not _VALIDATION_PATTERN.match(part):
        return f"{part_name} must be alpha_numeric string."
    if part.count("@") > 1:
        return f"{part_name} must have at most one @."
    return None


def validate_account_id(account_id: str) -> str | None:
    """Validate an account_id. Returns an error message if invalid, None if valid."""
    verr = validate_identifier_part(account_id, "account_id")
    if verr:
        return verr
    if account_id.startswith("_"):
        return "account_id cannot start with underscore _."
    return None


def validate_user_id(user_id: str) -> str | None:
    """Validate a user_id. Returns an error message if invalid, None if valid."""
    return validate_identifier_part(user_id, "user_id")


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

    def memory_space_uri(self) -> str:
        return f"viking://user/{self.user_space_name()}/memories"

    def work_space_uri(self) -> str:
        return f"viking://user/{self.user_space_name()}/workspaces"

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
