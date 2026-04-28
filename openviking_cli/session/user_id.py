import hashlib
import logging
import re
from typing import Optional

logger = logging.getLogger(__name__)

# Validation pattern reused across different modules
# Note: hyphen (-) must be at the end or escaped to avoid being interpreted as a range
_VALIDATION_PATTERN = re.compile(r"^[a-zA-Z0-9_.@-]+$")


def validate_identifier_part(part: str, part_name: str) -> Optional[str]:
    """Validate a single part of an identifier (account_id, user_id, or agent_id).

    Returns an error message if invalid, None if valid.
    """
    if not part:
        return f"{part_name} is empty"
    if not _VALIDATION_PATTERN.match(part):
        return f"{part_name} must be alpha_numeric string."
    if part.count("@") > 1:
        return f"{part_name} must have at most one @."
    return None


def validate_account_id(account_id: str) -> Optional[str]:
    """Validate an account_id. Returns an error message if invalid, None if valid."""
    verr = validate_identifier_part(account_id, "account_id")
    if verr:
        return verr
    if account_id.startswith("_"):
        return "account_id cannot start with underscore _."
    return None


def validate_user_id(user_id: str) -> Optional[str]:
    """Validate a user_id. Returns an error message if invalid, None if valid."""
    return validate_identifier_part(user_id, "user_id")


def validate_agent_id(agent_id: str) -> Optional[str]:
    """Validate an agent_id. Returns an error message if invalid, None if valid."""
    return validate_identifier_part(agent_id, "agent_id")


class UserIdentifier(object):
    def __init__(self, account_id: str, user_id: str, agent_id: str):
        self._account_id = account_id
        self._user_id = user_id
        self._agent_id = agent_id

        verr = self._validate_error()
        if verr:
            logger.error(
                f"Invalid user identifier: {verr}. account_id={self._account_id} user_id={self._user_id} agent_id={self._agent_id}"
            )
            raise ValueError(verr)

    @classmethod
    def the_default_user(cls, default_username: str = "default"):
        return cls("default", default_username, "default")

    def _validate_error(self) -> str:
        """Validate the user identifier using shared validation functions."""
        verr = validate_account_id(self._account_id)
        if verr:
            return verr
        verr = validate_user_id(self._user_id)
        if verr:
            return verr
        verr = validate_agent_id(self._agent_id)
        if verr:
            return verr
        return ""

    @property
    def account_id(self) -> str:
        return self._account_id

    @property
    def user_id(self) -> str:
        return self._user_id

    @property
    def agent_id(self) -> str:
        return self._agent_id

    def user_space_name(self) -> str:
        """User-level space name."""
        return self._user_id

    def _agent_space_source(self) -> str:
        """Return the legacy source string used by deprecated hash-based agent helpers.

        This helper is kept only for backward-compatible tooling paths. Service-side
        namespace resolution is now driven by per-account namespace policy instead.
        """
        return f"{self._user_id}:{self._agent_id}"

    def agent_space_name(self) -> str:
        """Return the legacy hash-based agent space for backward-compatible helpers only.

        New server-side agent URIs no longer derive from this hash helper.
        """
        return hashlib.md5(self._agent_space_source().encode()).hexdigest()[:12]

    def memory_space_uri(self) -> str:
        return f"viking://agent/{self.agent_space_name()}/memories"

    def work_space_uri(self) -> str:
        return f"viking://agent/{self.agent_space_name()}/workspaces"

    def to_dict(self):
        return {
            "account_id": self._account_id,
            "user_id": self._user_id,
            "agent_id": self._agent_id,
        }

    @classmethod
    def from_dict(cls, data: dict):
        return cls(data["account_id"], data["user_id"], data["agent_id"])

    def __str__(self) -> str:
        return f"{self._account_id}:{self._user_id}:{self._agent_id}"

    def __repr__(self) -> str:
        return self.__str__()

    def __eq__(self, other):
        return (
            self._account_id == other._account_id
            and self._user_id == other._user_id
            and self._agent_id == other._agent_id
        )
