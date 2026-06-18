"""Namespace helpers for account/user/session URIs."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from openviking.core.identifiers import validate_user_id
from openviking.core.peer_id import normalize_peer_id
from openviking.server.identity import RequestContext
from openviking_cli.utils.uri import VikingURI

_CONTENT_TYPES_BY_SCOPE = {
    "user": {"memories": "memory", "resources": "resource", "skills": "skill"},
    "agent": {"memories": "memory", "resources": "resource", "skills": "skill"},
}
_PEER_CONTENT_SEGMENTS = frozenset({"memories", "resources"})
_USER_RELATIVE_ROOT_SEGMENTS = frozenset({"peers", "privacy", "sessions"})
_CONTENT_SEGMENT_BY_KIND = {"resource": "resources", "skill": "skills"}


class NamespaceShapeError(ValueError):
    """Raised when a URI does not match the supported namespace shape."""


@dataclass(frozen=True)
class ResolvedNamespace:
    """Canonicalized namespace information for a URI."""

    uri: str
    scope: str
    owner_user_id: Optional[str] = None
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
        return self.scope == "agent" and len(self.parts) == 2

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
    """Return the first content segment after a user namespace root."""
    if len(parts) >= 3 and parts[0] == "agent" and parts[2] in _CONTENT_TYPES_BY_SCOPE["agent"]:
        return 2
    if len(parts) < 2 or parts[0] != "user":
        return None
    if parts[1] in _CONTENT_TYPES_BY_SCOPE["user"]:
        return 1
    if len(parts) >= 4 and parts[1] == "peers" and parts[3] in _PEER_CONTENT_SEGMENTS:
        return 3
    if len(parts) >= 5 and parts[2] == "peers" and parts[4] in _PEER_CONTENT_SEGMENTS:
        return 4
    if len(parts) >= 3 and parts[2] in _CONTENT_TYPES_BY_SCOPE["user"]:
        return 2
    return None


def _is_namespace_root_parts(parts: tuple[str, ...], scope: str) -> bool:
    return scope == "user" and parts[:1] == ("user",) and len(parts) == 2


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
    return ctx.user.user_id


def canonical_session_root(ctx: RequestContext) -> str:
    return f"{canonical_user_root(ctx)}/sessions"


def canonical_session_uri(ctx: RequestContext, session_id: Optional[str] = None) -> str:
    root = canonical_session_root(ctx)
    if not session_id:
        return root
    return f"{root}/{session_id}"


def canonical_user_content_root(ctx: RequestContext, kind: str) -> str:
    segment = _CONTENT_SEGMENT_BY_KIND[kind]
    return f"{canonical_user_root(ctx)}/{segment}"


def legacy_session_uri(session_id: Optional[str] = None) -> str:
    if not session_id:
        return "viking://session"
    return f"viking://session/{session_id}"


def is_session_uri(uri: str) -> bool:
    parts = uri_parts(uri)
    if parts[:1] == ["session"]:
        return True
    if parts[:2] == ["user", "sessions"]:
        return True
    return len(parts) >= 3 and parts[0] == "user" and parts[2] == "sessions"


def visible_roots(ctx: RequestContext) -> list[str]:
    return [
        "viking://resources",
        canonical_user_root(ctx),
    ]


def is_hidden_by_actor_peer_view(uri: str, ctx: RequestContext) -> bool:
    """Return whether uri points to another peer hidden by the actor peer view."""
    suffix = _actor_peer_view_user_suffix(uri, ctx)
    return bool(
        suffix and len(suffix) >= 2 and suffix[0] == "peers" and suffix[1] != ctx.actor_peer_id
    )


def may_include_hidden_actor_peers(uri: str, ctx: RequestContext) -> bool:
    """Return whether recursive data under uri may include hidden peers."""
    suffix = _actor_peer_view_user_suffix(uri, ctx)
    return suffix is not None and (not suffix or suffix == ["peers"])


def _actor_peer_view_user_suffix(uri: str, ctx: RequestContext) -> Optional[list[str]]:
    """Return uri's suffix under the current user root when actor peer view is active.

    The actor peer view filters only the current user's ``peers`` collection.
    It applies to filesystem and retrieval views, but does not change
    tenant/user identity or hide non-peer user content.
    """
    if not ctx.actor_peer_id:
        return None
    try:
        canonical_uri = canonicalize_uri(uri, ctx)
    except NamespaceShapeError:
        return None
    parts = uri_parts(canonical_uri)
    user_root_parts = ["user", ctx.user.user_id]
    if parts[: len(user_root_parts)] != user_root_parts:
        return None
    return parts[len(user_root_parts) :]


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
        return ResolvedNamespace(uri=VikingURI.normalize(uri).rstrip("/"), scope=scope)
    if scope == "session":
        return _resolve_session_uri(parts, ctx=ctx, require_canonical=require_canonical)
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

    if target.scope in {"", "resources", "temp", "queue"}:
        return True
    if target.scope == "upload":
        return False
    if target.scope == "user":
        if target.owner_user_id and target.owner_user_id != ctx.user.user_id:
            return False
        return True
    if target.scope == "agent":
        parts = uri_parts(target.uri)
        if ctx.actor_peer_id and len(parts) >= 2 and parts[1] != ctx.actor_peer_id:
            return False
        return True
    return True


def is_content_root_uri(
    uri: str,
    ctx: RequestContext,
    *,
    kind: str,
) -> bool:
    try:
        canonical_uri = canonicalize_uri(uri, ctx)
    except (ValueError, NamespaceShapeError):
        return False
    parts = uri_parts(canonical_uri)
    if kind == "resource" and parts == ["resources"]:
        return True
    classification = classify_uri(canonical_uri)
    return (
        classification.context_type == kind
        and classification.content_index is not None
        and len(parts) == classification.content_index + 1
    )


def is_content_namespace_root_uri(uri: str, ctx: RequestContext) -> bool:
    try:
        canonical_uri = canonicalize_uri(uri, ctx)
    except (ValueError, NamespaceShapeError):
        return False
    parts = uri_parts(canonical_uri)
    if parts == ["resources"]:
        return True
    classification = classify_uri(canonical_uri)
    return (
        classification.content_index is not None and len(parts) == classification.content_index + 1
    )


def _validate_peer_id_segments(parts: list[str]) -> None:
    if len(parts) >= 3 and parts[0] == "user" and parts[1] == "peers":
        _require_peer_id_segment(parts[2])
        return
    if len(parts) >= 4 and parts[0] == "user" and parts[2] == "peers":
        _require_peer_id_segment(parts[3])


def _require_peer_id_segment(peer_id: str) -> None:
    try:
        if normalize_peer_id(peer_id) is None:
            raise ValueError("peer_id must not be empty")
    except ValueError as exc:
        raise NamespaceShapeError(str(exc)) from exc


def owner_fields_for_uri(
    uri: str,
    ctx: Optional[RequestContext] = None,
    *,
    user=None,
    account_id: Optional[str] = None,
) -> dict:
    resolved_ctx = ctx
    if resolved_ctx is None and user is not None:
        from openviking.server.identity import Role

        resolved_ctx = RequestContext(
            user=user,
            role=Role.ROOT,
        )
    if resolved_ctx is None and account_id:
        from openviking.server.identity import Role
        from openviking_cli.session.user_id import UserIdentifier

        resolved_ctx = RequestContext(
            user=UserIdentifier(account_id, "default"),
            role=Role.ROOT,
        )

    try:
        resolved = resolve_uri(uri, ctx=resolved_ctx)
    except NamespaceShapeError:
        return {
            "uri": VikingURI.normalize(uri).rstrip("/"),
            "owner_user_id": None,
        }
    return {
        "uri": resolved.uri,
        "owner_user_id": resolved.owner_user_id,
    }


def owner_space_for_uri(uri: str, ctx: RequestContext) -> str:
    """Derive the legacy owner_space bucket for vector records from URI scope and context."""
    parts = uri_parts(uri)
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

    if _is_current_user_relative_uri(parts, ctx):
        if require_canonical:
            raise NamespaceShapeError(f"Shorthand user URI is not allowed here: {normalized}")
        if ctx is None:
            raise NamespaceShapeError(f"User shorthand URI requires request context: {normalized}")
        suffix = parts[1:]
        return resolve_uri(
            "/".join([canonical_user_root(ctx)[len("viking://") :], *suffix]), ctx=ctx
        )

    second = parts[1]
    user_id = second
    validation_error = validate_user_id(user_id)
    if validation_error:
        raise NamespaceShapeError(f"Invalid user_id: {validation_error}")
    if len(parts) == 2:
        return ResolvedNamespace(
            uri=f"viking://user/{user_id}",
            scope="user",
            owner_user_id=user_id,
        )

    suffix = parts[2:]
    canonical = f"viking://user/{user_id}"
    if suffix:
        canonical = f"{canonical}/{'/'.join(suffix)}"
    _validate_peer_id_segments(parts)
    return ResolvedNamespace(
        uri=canonical,
        scope="user",
        owner_user_id=user_id,
    )


def _is_current_user_relative_uri(parts: list[str], ctx: Optional[RequestContext]) -> bool:
    if len(parts) < 2 or parts[0] != "user":
        return False
    if ctx is not None and parts[1] == ctx.user.user_id:
        return False
    return _is_user_relative_root_segment(parts[1])


def _is_user_relative_root_segment(segment: str) -> bool:
    return segment in _CONTENT_TYPES_BY_SCOPE["user"] or segment in _USER_RELATIVE_ROOT_SEGMENTS


def _resolve_session_uri(
    parts: list[str],
    ctx: Optional[RequestContext],
    *,
    require_canonical: bool,
) -> ResolvedNamespace:
    normalized = "viking://" + "/".join(parts)
    if require_canonical:
        raise NamespaceShapeError(f"Legacy session URI is not allowed here: {normalized}")
    if ctx is not None:
        if len(parts) == 1:
            return ResolvedNamespace(
                uri=canonical_session_uri(ctx),
                scope="user",
                owner_user_id=ctx.user.user_id,
                is_container=True,
            )
        session_id = parts[1]
        suffix = parts[2:]
        canonical = canonical_session_uri(ctx, session_id)
        if suffix:
            canonical = f"{canonical}/{'/'.join(suffix)}"
        return ResolvedNamespace(
            uri=canonical,
            scope="user",
            owner_user_id=ctx.user.user_id,
        )

    if len(parts) == 1:
        return ResolvedNamespace(uri="viking://session", scope="session", is_container=True)
    session_id = parts[1]
    canonical = f"viking://session/{session_id}"
    if len(parts) > 2:
        canonical = f"{canonical}/{'/'.join(parts[2:])}"
    return ResolvedNamespace(uri=canonical, scope="session")
