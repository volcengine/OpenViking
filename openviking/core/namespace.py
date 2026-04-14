"""Account-scoped namespace policy and canonical URI resolution helpers."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Optional

from openviking_cli.utils.uri import VikingURI

NAMESPACE_POLICY_PATH_TEMPLATE = "/local/{account_id}/_system/setting.json"

_ROOT_METADATA_FILES = {".abstract.md", ".overview.md"}
_USER_STRUCTURE_DIRS = {"memories", "profile.md"}
_AGENT_STRUCTURE_DIRS = {"memories", "skills", "instructions", "workspaces"}
_SESSION_STRUCTURE_DIRS = {"messages.jsonl", "history", "tools", ".meta.json"}


@dataclass(frozen=True)
class NamespacePolicy:
    """Persisted account-level namespace isolation policy."""

    isolate_user_scope_by_agent: bool = False
    isolate_agent_scope_by_user: bool = False

    def to_dict(self) -> dict[str, bool]:
        return {
            "isolate_user_scope_by_agent": self.isolate_user_scope_by_agent,
            "isolate_agent_scope_by_user": self.isolate_agent_scope_by_user,
        }

    @classmethod
    def from_dict(cls, data: Optional[dict[str, Any]]) -> "NamespacePolicy":
        if not isinstance(data, dict):
            return cls()
        return cls(
            isolate_user_scope_by_agent=bool(data.get("isolate_user_scope_by_agent", False)),
            isolate_agent_scope_by_user=bool(data.get("isolate_agent_scope_by_user", False)),
        )


def namespace_policy_path(account_id: str) -> str:
    """Return the AGFS path for the persisted namespace policy."""
    return NAMESPACE_POLICY_PATH_TEMPLATE.format(account_id=account_id)


class NamespaceResolver:
    """Resolve canonical user/agent/session URIs for a request identity."""

    def __init__(self, policy: Optional[NamespacePolicy] = None):
        self.policy = policy or NamespacePolicy()

    @staticmethod
    def _join_uri(base_uri: str, *parts: str) -> str:
        suffix = "/".join(part.strip("/") for part in parts if part and part.strip("/"))
        if not suffix:
            return base_uri
        return f"{base_uri.rstrip('/')}/{suffix}"

    def user_root(self, user: Any) -> str:
        parts = [user.user_id]
        if self.policy.isolate_user_scope_by_agent:
            parts.extend(["agent", user.agent_id])
        return VikingURI.build("user", *parts)

    def agent_root(self, user: Any) -> str:
        parts = [user.agent_id]
        if self.policy.isolate_agent_scope_by_user:
            parts.extend(["user", user.user_id])
        return VikingURI.build("agent", *parts)

    @staticmethod
    def session_root(session_id: Optional[str] = None) -> str:
        if session_id:
            return VikingURI.build("session", session_id)
        return VikingURI.build("session")

    def canonicalize_uri(self, uri: str, user: Any) -> str:
        """Canonicalize shorthand and legacy URIs for the given user."""
        normalized = VikingURI.normalize(uri)
        parts = [part for part in normalized[len("viking://") :].strip("/").split("/") if part]
        if not parts:
            return normalized

        scope = parts[0]
        if scope == "user":
            return self._canonicalize_user_uri(normalized, parts, user)
        if scope == "agent":
            return self._canonicalize_agent_uri(normalized, parts, user)
        if scope == "session":
            return self._canonicalize_session_uri(normalized, parts, user)
        return normalized

    def is_visible(self, uri: str, user: Any) -> bool:
        """Check whether a canonicalized URI is visible to the given user."""
        canonical = self.canonicalize_uri(uri, user)
        parts = [part for part in canonical[len("viking://") :].strip("/").split("/") if part]
        if not parts:
            return True

        scope = parts[0]
        if scope in {"resources", "temp", "queue"}:
            return True
        if scope == "session":
            return True
        if len(parts) == 1:
            return True
        if len(parts) == 2 and parts[1] in _ROOT_METADATA_FILES:
            return True
        if scope == "user":
            expected_root = self.user_root(user)
            return canonical == expected_root or canonical.startswith(f"{expected_root}/")
        if scope == "agent":
            expected_root = self.agent_root(user)
            return canonical == expected_root or canonical.startswith(f"{expected_root}/")
        return True

    def _canonicalize_user_uri(self, normalized: str, parts: list[str], user: Any) -> str:
        if len(parts) == 1:
            return normalized

        second = parts[1]
        if second in _ROOT_METADATA_FILES:
            return normalized

        canonical_root = self.user_root(user)
        if second in _USER_STRUCTURE_DIRS:
            return self._join_uri(canonical_root, *parts[1:])

        if second != user.user_id:
            return normalized

        remainder = parts[2:]
        if self.policy.isolate_user_scope_by_agent:
            if len(remainder) >= 2 and remainder[0] == "agent" and remainder[1] == user.agent_id:
                return normalized
            return self._join_uri(canonical_root, *remainder)

        if len(remainder) >= 2 and remainder[0] == "agent" and remainder[1] == user.agent_id:
            return self._join_uri(canonical_root, *remainder[2:])
        return normalized

    def _canonicalize_agent_uri(self, normalized: str, parts: list[str], user: Any) -> str:
        if len(parts) == 1:
            return normalized

        second = parts[1]
        if second in _ROOT_METADATA_FILES:
            return normalized

        canonical_root = self.agent_root(user)
        if second in _AGENT_STRUCTURE_DIRS:
            return self._join_uri(canonical_root, *parts[1:])

        current_aliases = {user.agent_id}
        agent_space_name = getattr(user, "agent_space_name", None)
        if callable(agent_space_name):
            current_aliases.add(agent_space_name())
        if second not in current_aliases:
            return normalized

        remainder = parts[2:]
        if self.policy.isolate_agent_scope_by_user:
            if len(remainder) >= 2 and remainder[0] == "user" and remainder[1] == user.user_id:
                return self._join_uri(canonical_root, *remainder[2:])
            return self._join_uri(canonical_root, *remainder)

        if len(remainder) >= 2 and remainder[0] == "user" and remainder[1] == user.user_id:
            return self._join_uri(canonical_root, *remainder[2:])
        if second == user.agent_id:
            return normalized
        return self._join_uri(canonical_root, *remainder)

    def _canonicalize_session_uri(self, normalized: str, parts: list[str], user: Any) -> str:
        if len(parts) <= 1:
            return normalized

        if len(parts) >= 4 and parts[1] == user.user_id:
            return self._join_uri(self.session_root(parts[2]), *parts[3:])

        if (
            len(parts) == 3
            and parts[1] == user.user_id
            and parts[2] not in _SESSION_STRUCTURE_DIRS
            and parts[2] not in _ROOT_METADATA_FILES
        ):
            return self.session_root(parts[2])

        return normalized


def _ensure_parent_dirs(viking_fs: Any, path: str) -> None:
    parts = [part for part in path.lstrip("/").split("/") if part]
    for index in range(1, len(parts)):
        parent = "/" + "/".join(parts[:index])
        try:
            viking_fs.agfs.mkdir(parent)
        except Exception:
            pass


def _coerce_bytes(viking_fs: Any, result: Any) -> bytes:
    if hasattr(viking_fs, "_handle_agfs_read"):
        return viking_fs._handle_agfs_read(result)
    if isinstance(result, bytes):
        return result
    if result is None:
        return b""
    if hasattr(result, "content") and result.content is not None:
        return result.content
    return str(result).encode("utf-8")


async def load_namespace_policy(viking_fs: Any, account_id: str) -> NamespacePolicy:
    """Read the persisted account policy, falling back to defaults."""
    path = namespace_policy_path(account_id)
    try:
        try:
            raw = viking_fs.agfs.read(path, 0, -1)
        except TypeError:
            raw = viking_fs.agfs.read(path)
        payload = _coerce_bytes(viking_fs, raw)
        payload = await viking_fs.decrypt_bytes(account_id, payload)
        data = json.loads(payload.decode("utf-8"))
        return NamespacePolicy.from_dict(data)
    except Exception:
        return NamespacePolicy()


async def persist_namespace_policy(
    viking_fs: Any,
    account_id: str,
    policy: Optional[NamespacePolicy] = None,
) -> NamespacePolicy:
    """Persist an account's namespace policy, creating the settings file if needed."""
    resolved = policy or NamespacePolicy()
    path = namespace_policy_path(account_id)
    content = json.dumps(resolved.to_dict(), ensure_ascii=False, indent=2).encode("utf-8")
    content = await viking_fs.encrypt_bytes(account_id, content)
    _ensure_parent_dirs(viking_fs, path)
    viking_fs.agfs.write(path, content)
    cache = getattr(viking_fs, "_namespace_policy_cache", None)
    if isinstance(cache, dict):
        cache[account_id] = resolved
    return resolved
