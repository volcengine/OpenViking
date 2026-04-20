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


def _uri_parts(uri: str) -> list[str]:
    normalized = VikingURI.normalize(uri).rstrip("/")
    if normalized == "viking:":
        normalized = "viking://"
    if normalized == "viking://":
        return []
    return [part for part in normalized[len("viking://") :].split("/") if part]


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

    parts = _uri_parts(uri)
    if not parts:
        return ResolvedNamespace(uri="viking://", scope="", is_container=True)

    scope = parts[0]
    if scope == "user":
        return _resolve_user_uri(parts, ctx=ctx, require_canonical=require_canonical)
    if scope == "agent":
        return _resolve_agent_uri(parts, ctx=ctx, require_canonical=require_canonical)
    if scope == "session":
        return _resolve_session_uri(parts)
    if scope in {"resources", "temp", "queue"}:
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
