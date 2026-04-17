import hashlib
import re


class UserIdentifier(object):
    def __init__(self, account_id: str, user_id: str, agent_id: str):
        self._account_id = account_id
        self._user_id = user_id
        self._agent_id = agent_id

        verr = self._validate_error()
        if verr:
            raise ValueError(verr)

    @classmethod
    def the_default_user(cls, default_username: str = "default"):
        return cls("default", default_username, "default")

    def _validate_error(self) -> str:
        """Validate the user identifier. all fields must be non-empty strings, and chars only in [a-zA-Z0-9_-]."""
        pattern = re.compile(r"^[a-zA-Z0-9_-]+$")
        if not self._account_id:
            return "account_id is empty"
        if not pattern.match(self._account_id):
            return "account_id must be alpha-numeric string."
        if not self._user_id:
            return "user_id is empty"
        if not pattern.match(self._user_id):
            return "user_id must be alpha-numeric string."
        if not self._agent_id:
            return "agent_id is empty"
        if not pattern.match(self._agent_id):
            return "agent_id must be alpha-numeric string."
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
