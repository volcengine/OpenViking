"""Namespace policy helpers for account/user/agent/session URIs."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from openviking.server.identity import AccountNamespacePolicy, RequestContext
from openviking_cli.utils.uri import VikingURI

_USER_SHORTHAND_SEGMENTS = {"memories", "profile.md", ".abstract.md", ".overview.md"}
_AGENT_SHORTHAND_SEGMENTS = {
    "memories",
    "skills",
    "instructions",
    "workspaces",
    ".abstract.md",
    ".overview.md",
}
_CROSS_SCOPE_OWNER_SEGMENT = {"user": "agent", "agent": "user"}
_CONTENT_TYPES_BY_SCOPE = {
    "user": {"memories": "memory"},
    "agent": {"memories": "memory", "skills": "skill"},
}


class NamespaceShapeError(ValueError):
    """Raised when a URI does not match the active namespace policy shape."""


@dataclass(frozen=True)
class ResolvedNamespace:
    """Canonicalized namespace information for a URI."""

    uri: str
    scope: str
    owner_user_id: Optional[str] = None
    owner_agent_id: Optional[str] = None
    is_container: bool = False


@dataclass(frozen=True)
class UriClassification:
    """Viking URI classification derived from path structure."""

    parts: tuple[str, ...]
    scope: str
    content_index: Optional[int]
    context_type: str

    @property
    def is_memory(self) -> bool:
        return self.context_type == "memory"

    @property
    def is_skill(self) -> bool:
        return self.context_type == "skill"

    @property
    def is_user_namespace_root(self) -> bool:
        return _is_namespace_root_parts(self.parts, "user")

    @property
    def is_agent_namespace_root(self) -> bool:
        return _is_namespace_root_parts(self.parts, "agent")

    @property
    def is_memory_root(self) -> bool:
        return (
            self.is_memory
            and self.content_index is not None
            and len(self.parts) == self.content_index + 1
        )

    @property
    def is_skill_namespace(self) -> bool:
        return (
            self.is_skill
            and self.content_index is not None
            and len(self.parts) == self.content_index + 1
        )

    @property
    def is_skill_root(self) -> bool:
        return (
            self.is_skill
            and self.content_index is not None
            and len(self.parts) == self.content_index + 2
        )


def uri_parts(uri: str) -> list[str]:
    """Return normalized Viking URI path segments without query parameters."""
    normalized = VikingURI.normalize(uri.split("?", 1)[0]).rstrip("/")
    if normalized == "viking:":
        normalized = "viking://"
    if normalized == "viking://":
        return []
    return [part for part in normalized[len("viking://") :].split("/") if part]


def uri_depth(uri: str) -> int:
    """Return the number of normalized Viking URI path segments."""
    return len(uri_parts(uri))


def uri_leaf_name(uri: str) -> str:
    """Return the final normalized Viking URI path segment."""
    parts = uri_parts(uri)
    return parts[-1] if parts else ""


def relative_uri_path(root_uri: str, uri: str) -> str:
    """Return uri's slash-separated path relative to root_uri, or empty when not nested."""
    root_parts = uri_parts(root_uri)
    parts = uri_parts(uri)
    if parts == root_parts or parts[: len(root_parts)] != root_parts:
        return ""
    return "/".join(parts[len(root_parts) :])


def _content_segment_index(parts: tuple[str, ...]) -> Optional[int]:
    """Return the first content segment after a user/agent namespace root."""
    if len(parts) < 2 or parts[0] not in _CONTENT_TYPES_BY_SCOPE:
        return None
    if parts[1] in _CONTENT_TYPES_BY_SCOPE[parts[0]]:
        return 1
    if len(parts) < 3:
        return None
    cross_scope_segment = _CROSS_SCOPE_OWNER_SEGMENT[parts[0]]
    if len(parts) >= 5 and parts[2] == cross_scope_segment:
        return 4
    return 2


def _is_namespace_root_parts(parts: tuple[str, ...], scope: str) -> bool:
    return parts[:1] == (scope,) and (
        len(parts) == 2 or (len(parts) == 4 and parts[2] == _CROSS_SCOPE_OWNER_SEGMENT[scope])
    )


def classify_uri(uri: str) -> UriClassification:
    parts = tuple(uri_parts(uri))
    content_index = _content_segment_index(parts)
    context_type = "resource"
    if content_index is not None:
        context_type = _CONTENT_TYPES_BY_SCOPE.get(parts[0], {}).get(
            parts[content_index], "resource"
        )
    return UriClassification(
        parts=parts,
        scope=parts[0] if parts else "",
        content_index=content_index,
        context_type=context_type,
    )


def context_type_for_uri(uri: str) -> str:
    return classify_uri(uri).context_type


def canonical_user_root(ctx: RequestContext) -> str:
    return f"viking://user/{user_space_fragment(ctx)}"


def user_space_fragment(ctx: RequestContext) -> str:
    return to_user_space(ctx.namespace_policy, ctx.user.user_id, ctx.user.agent_id)


def to_user_space(namespace_policy, user_id, agent_id) -> str:
    if namespace_policy.isolate_user_scope_by_agent:
        return f"{user_id}/agent/{agent_id}"
    return user_id


def canonical_agent_root(ctx: RequestContext) -> str:
    return f"viking://agent/{agent_space_fragment(ctx)}"


def agent_space_fragment(ctx: RequestContext) -> str:
    return to_agent_space(ctx.namespace_policy, ctx.user.user_id, ctx.user.agent_id)


def to_agent_space(namespace_policy, user_id, agent_id) -> str:
    if namespace_policy.isolate_agent_scope_by_user:
        return f"{agent_id}/user/{user_id}"
    return agent_id


def canonical_session_uri(session_id: Optional[str] = None) -> str:
    if not session_id:
        return "viking://session"
    return f"viking://session/{session_id}"


def visible_roots(ctx: RequestContext) -> list[str]:
    return [
        "viking://resources",
        "viking://session",
        canonical_user_root(ctx),
        canonical_agent_root(ctx),
    ]


def resolve_uri(
    uri: str,
    ctx: Optional[RequestContext] = None,
    *,
    require_canonical: bool = False,
) -> ResolvedNamespace:
    """Resolve a URI into a canonical URI and owner tuple."""

    parts = uri_parts(uri)
    if not parts:
        return ResolvedNamespace(uri="viking://", scope="", is_container=True)

    scope = parts[0]
    if scope == "user":
        return _resolve_user_uri(parts, ctx=ctx, require_canonical=require_canonical)
    if scope == "agent":
        return _resolve_agent_uri(parts, ctx=ctx, require_canonical=require_canonical)
    if scope == "session":
        return _resolve_session_uri(parts)
    if scope in {"resources", "temp", "queue", "upload"}:
        return ResolvedNamespace(uri=VikingURI.normalize(uri).rstrip("/"), scope=scope)
    return ResolvedNamespace(uri=VikingURI.normalize(uri).rstrip("/"), scope=scope)


def canonicalize_uri(uri: str, ctx: Optional[RequestContext] = None) -> str:
    return resolve_uri(uri, ctx=ctx).uri


def is_accessible(uri: str, ctx: RequestContext) -> bool:
    if getattr(ctx.role, "value", ctx.role) == "root":
        return True

    try:
        target = resolve_uri(uri, ctx=ctx)
    except NamespaceShapeError:
        return False

    if target.scope in {"", "resources", "temp", "queue", "session"}:
        return True
    if target.scope == "upload":
        return False
    if target.scope == "user":
        if target.owner_user_id and target.owner_user_id != ctx.user.user_id:
            return False
        if (
            ctx.namespace_policy.isolate_user_scope_by_agent
            and target.owner_agent_id is not None
            and target.owner_agent_id != ctx.user.agent_id
        ):
            return False
        return True
    if target.scope == "agent":
        if target.owner_agent_id and target.owner_agent_id != ctx.user.agent_id:
            return False
        if (
            ctx.namespace_policy.isolate_agent_scope_by_user
            and target.owner_user_id is not None
            and target.owner_user_id != ctx.user.user_id
        ):
            return False
        return True
    return True


def owner_fields_for_uri(
    uri: str,
    ctx: Optional[RequestContext] = None,
    *,
    user=None,
    account_id: Optional[str] = None,
    policy: Optional[AccountNamespacePolicy] = None,
) -> dict:
    resolved_ctx = ctx
    if resolved_ctx is None and user is not None:
        from openviking.server.identity import Role

        resolved_ctx = RequestContext(
            user=user,
            role=Role.ROOT,
            namespace_policy=policy or AccountNamespacePolicy(),
        )
    if resolved_ctx is None and account_id:
        from openviking.server.identity import Role
        from openviking_cli.session.user_id import UserIdentifier

        resolved_ctx = RequestContext(
            user=UserIdentifier(account_id, "default", "default"),
            role=Role.ROOT,
            namespace_policy=policy or AccountNamespacePolicy(),
        )

    try:
        resolved = resolve_uri(uri, ctx=resolved_ctx)
    except NamespaceShapeError:
        return {
            "uri": VikingURI.normalize(uri).rstrip("/"),
            "owner_user_id": None,
            "owner_agent_id": None,
        }
    return {
        "uri": resolved.uri,
        "owner_user_id": resolved.owner_user_id,
        "owner_agent_id": resolved.owner_agent_id,
    }


def owner_space_for_uri(uri: str, ctx: RequestContext) -> str:
    """Derive the legacy owner_space bucket for vector records from URI scope and context."""
    parts = uri_parts(uri)
    if parts[:1] == ["agent"]:
        return agent_space_fragment(ctx)
    if parts[:1] in (["user"], ["session"]):
        return user_space_fragment(ctx)
    return ""


def _resolve_user_uri(
    parts: list[str],
    ctx: Optional[RequestContext],
    *,
    require_canonical: bool,
) -> ResolvedNamespace:
    normalized = "viking://" + "/".join(parts)
    if len(parts) == 1:
        return ResolvedNamespace(uri="viking://user", scope="user", is_container=True)

    second = parts[1]
    if second in _USER_SHORTHAND_SEGMENTS:
        if require_canonical:
            raise NamespaceShapeError(f"Shorthand user URI is not allowed here: {normalized}")
        if ctx is None:
            raise NamespaceShapeError(f"User shorthand URI requires request context: {normalized}")
        suffix = parts[1:]
        return resolve_uri(
            "/".join([canonical_user_root(ctx)[len("viking://") :], *suffix]), ctx=ctx
        )

    user_id = second
    policy = _require_policy(ctx)
    if len(parts) == 2:
        if policy.isolate_user_scope_by_agent:
            return ResolvedNamespace(
                uri=f"viking://user/{user_id}",
                scope="user",
                owner_user_id=user_id,
                is_container=True,
            )
        return ResolvedNamespace(
            uri=f"viking://user/{user_id}",
            scope="user",
            owner_user_id=user_id,
        )

    if policy.isolate_user_scope_by_agent:
        if len(parts) < 4 or parts[2] != "agent":
            raise NamespaceShapeError(
                f"User URI must include /agent/{{agent_id}} under current policy: {normalized}"
            )
        agent_id = parts[3]
        suffix = parts[4:]
        canonical = f"viking://user/{user_id}/agent/{agent_id}"
        if suffix:
            canonical = f"{canonical}/{'/'.join(suffix)}"
        return ResolvedNamespace(
            uri=canonical,
            scope="user",
            owner_user_id=user_id,
            owner_agent_id=agent_id,
        )

    suffix = parts[2:]
    canonical = f"viking://user/{user_id}"
    if suffix:
        canonical = f"{canonical}/{'/'.join(suffix)}"
    return ResolvedNamespace(
        uri=canonical,
        scope="user",
        owner_user_id=user_id,
    )


def _resolve_agent_uri(
    parts: list[str],
    ctx: Optional[RequestContext],
    *,
    require_canonical: bool,
) -> ResolvedNamespace:
    normalized = "viking://" + "/".join(parts)
    if len(parts) == 1:
        return ResolvedNamespace(uri="viking://agent", scope="agent", is_container=True)

    second = parts[1]
    if second in _AGENT_SHORTHAND_SEGMENTS:
        if require_canonical:
            raise NamespaceShapeError(f"Shorthand agent URI is not allowed here: {normalized}")
        if ctx is None:
            raise NamespaceShapeError(f"Agent shorthand URI requires request context: {normalized}")
        suffix = parts[1:]
        return resolve_uri(
            "/".join([canonical_agent_root(ctx)[len("viking://") :], *suffix]), ctx=ctx
        )

    agent_id = second
    policy = _require_policy(ctx)
    if len(parts) == 2:
        if policy.isolate_agent_scope_by_user:
            return ResolvedNamespace(
                uri=f"viking://agent/{agent_id}",
                scope="agent",
                owner_agent_id=agent_id,
                is_container=True,
            )
        return ResolvedNamespace(
            uri=f"viking://agent/{agent_id}",
            scope="agent",
            owner_agent_id=agent_id,
        )

    if policy.isolate_agent_scope_by_user:
        if len(parts) < 4 or parts[2] != "user":
            raise NamespaceShapeError(
                f"Agent URI must include /user/{{user_id}} under current policy: {normalized}"
            )
        user_id = parts[3]
        suffix = parts[4:]
        canonical = f"viking://agent/{agent_id}/user/{user_id}"
        if suffix:
            canonical = f"{canonical}/{'/'.join(suffix)}"
        return ResolvedNamespace(
            uri=canonical,
            scope="agent",
            owner_user_id=user_id,
            owner_agent_id=agent_id,
        )

    suffix = parts[2:]
    canonical = f"viking://agent/{agent_id}"
    if suffix:
        canonical = f"{canonical}/{'/'.join(suffix)}"
    return ResolvedNamespace(
        uri=canonical,
        scope="agent",
        owner_agent_id=agent_id,
    )


def _resolve_session_uri(parts: list[str]) -> ResolvedNamespace:
    if len(parts) == 1:
        return ResolvedNamespace(uri="viking://session", scope="session", is_container=True)
    session_id = parts[1]
    canonical = f"viking://session/{session_id}"
    if len(parts) > 2:
        canonical = f"{canonical}/{'/'.join(parts[2:])}"
    return ResolvedNamespace(uri=canonical, scope="session")


def _require_policy(ctx: Optional[RequestContext]) -> AccountNamespacePolicy:
    if ctx is None:
        return AccountNamespacePolicy()
    return ctx.namespace_policy
