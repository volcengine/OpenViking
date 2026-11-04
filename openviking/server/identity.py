# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Identity and role types for OpenViking multi-tenant HTTP Server."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, Any, ClassVar, List, Optional

if TYPE_CHECKING:
    from openviking.storage.acl import AclAction
    from openviking.storage.expr import FilterExpr
    from openviking.storage.viking_fs import VikingFS

from openviking.core.global_policy import (
    GlobalAccessPolicy,
    GlobalPolicyError,
    PolicyPathScope,
    PolicyScopePlan,
)
from openviking_cli.session.user_id import UserIdentifier


class Role(str):
    """Role type — supports built-in roles and custom plugin-defined roles.

    Built-in roles (root/admin/user/global) are available as class attributes.
    Custom roles can be registered via ``Role.register(name)``.

    Token downgrade checks use an explicit allowlist table, not privilege
    ranks. GLOBAL is orthogonal to ADMIN/USER: a GLOBAL identity may only
    carry a GLOBAL token, and no other role may carry a GLOBAL token.

    Role comparison is string-based, so ``Role.ROOT == "root"`` and
    ``Role("custom") == "custom"`` both work.
    """

    ROOT = "root"
    ADMIN = "admin"
    USER = "user"
    GLOBAL = "global"

    # Token downgrade allowlist: key = the role currently registered for the
    # identity, value = the token roles that identity may still present.
    # Authorization is a direct lookup here; there is no privilege rank.
    _TOKEN_ROLE_ALLOWLIST: dict[str, frozenset[str]] = {
        ROOT: frozenset({ROOT, ADMIN, USER}),
        ADMIN: frozenset({ADMIN, USER}),
        USER: frozenset({USER}),
        GLOBAL: frozenset({GLOBAL}),
    }

    @classmethod
    def register(
        cls,
        name: str,
        rank: int | None = None,
        *,
        allowed_token_roles: Iterable[str] | None = None,
    ) -> None:
        """Register a custom role and its token-downgrade allowlist.

        Args:
            name: Role identifier string.
            rank: Deprecated and ignored. Accepted only so existing callers
                that pass a legacy rank keep working.
            allowed_token_roles: Token roles this custom role may present.
                The role itself is always included. When omitted, the custom
                role may only present its own role.

        The allowlist is stored in the same table used for built-in roles, so
        authorization never compares privilege ranks.
        """
        if not isinstance(name, str) or not name:
            raise ValueError("Custom role name must be a non-empty string")
        if name in (cls.ROOT, cls.ADMIN, cls.USER, cls.GLOBAL):
            raise ValueError("Built-in roles cannot be registered as custom roles")
        if rank is not None and (not isinstance(rank, int) or isinstance(rank, bool)):
            raise ValueError("Custom role rank must be an integer")
        if allowed_token_roles is None:
            allowed_token_roles = {name}
        else:
            # A registered role must always be able to present its own token.
            allowed_token_roles = {*allowed_token_roles, name}
        cls._TOKEN_ROLE_ALLOWLIST[name] = frozenset(allowed_token_roles)

    @classmethod
    def parse(cls, value: object) -> Role:
        """Validate a role before it enters an identity or request context.

        Construction remains string-compatible. Only enabled built-in roles
        and explicitly registered custom roles may become internal identities.
        """
        if not isinstance(value, str) or value not in cls._TOKEN_ROLE_ALLOWLIST:
            raise ValueError("Unknown or inactive identity role")
        return cls(value)

    def allows_token_role(self, authorized_role: str) -> bool:
        """Check a saved authorization role against this registered role.

        This preserves legacy token compatibility, not operation permissions.
        The decision is a direct allowlist lookup keyed by the registered role;
        GLOBAL is orthogonal to the other roles and never crosses over.
        """
        authorized = self.parse(authorized_role)
        allowed = self._TOKEN_ROLE_ALLOWLIST.get(str(self), frozenset())
        return authorized in allowed

    def __eq__(self, other: object) -> bool:
        if isinstance(other, Role):
            return str(self) == str(other)
        if isinstance(other, str):
            return str(self) == other
        return NotImplemented

    def __hash__(self) -> int:
        return hash(str(self))


# Roles that may be assigned when creating an account user. ROOT stays
# control-plane only; GLOBAL is creation-only and is never a role-change target.
ACCOUNT_USER_CREATION_ROLES: tuple[Role, ...] = (Role.USER, Role.ADMIN, Role.GLOBAL)

# Roles an existing account user may be changed to. GLOBAL identities are
# created explicitly by ROOT and cannot be promoted or demoted in place.
ACCOUNT_USER_ASSIGNABLE_ROLES: tuple[Role, ...] = (Role.USER, Role.ADMIN)


class AuthMode(str, Enum):
    """Authentication modes for OpenViking server.

    Built-in modes. Custom modes are plain strings not in this enum.
    """

    API_KEY = "api_key"
    TRUSTED = "trusted"
    DEV = "dev"


@dataclass
class ResolvedIdentity:
    """Output of auth middleware: raw identity resolved from API Key."""

    role: Role
    account_id: Optional[str] = None
    user_id: Optional[str] = None
    # API-key snapshots provide resolved groups (including empty). Trusted
    # plugins may supply asserted groups; absent groups use the local registry.
    group_ids: tuple[str, ...] | None = None
    # True when this identity was minted from an OAuth-issued bearer token;
    # downstream checks (e.g. ROOT-requires-explicit-tenant headers) can skip
    # rules that target raw API-key auth, since OAuth claims already pin
    # account/user.
    from_oauth: bool = False

    def __post_init__(self) -> None:
        self.role = Role.parse(self.role)


class AccessDecision(str, Enum):
    ALLOWED = "allowed"
    CONTAINER = "container"
    DENIED = "denied"


@dataclass(frozen=True)
class DataAccessScope:
    # Owner-root files exposed by default to delegated reads and listings.
    _DEFAULT_READABLE_FILES: ClassVar[tuple[str, ...]] = ("GLOBAL_POLICY.md",)

    owner_user_id: str
    operation: AclAction
    plan: PolicyScopePlan
    policy_hash: str | None
    request_root: str
    requested_targets: tuple[str, ...]
    effective_targets: tuple[str, ...]

    @classmethod
    def read_plan(cls, policy: GlobalAccessPolicy | None) -> PolicyScopePlan:
        """Combine ordinary policy grants with default-readable owner-root files."""
        plan = policy.restrict_scope() if policy is not None else PolicyScopePlan("denied", (), ())
        return PolicyScopePlan(
            "allowed",
            (
                *plan.includes,
                *(PolicyPathScope(name, False) for name in cls._DEFAULT_READABLE_FILES),
            ),
            tuple(s for s in plan.excludes if s.path not in cls._DEFAULT_READABLE_FILES),
        )

    def query_filter(self) -> FilterExpr:
        """Filter before query limits; public paths retain actor ACL filtering."""
        from openviking.storage.expr import And, Or, PathScope, RawDSL

        public_filters = [
            PathScope("uri", "viking://resources", depth=-1),
            PathScope("uri", "viking://agent", depth=-1),
        ]
        if self.plan.status != "allowed" or not self.plan.includes:
            # No owner content is in scope (e.g. a public target survived while
            # every owner target was denied); keep only the public roots. An
            # empty include list must not degrade to an unfiltered scan.
            return Or(public_filters)

        def scope_uri(scope):
            return self.request_root + ("/" + scope.path if scope.path else "")

        owner_filters = [
            Or(
                [
                    PathScope("uri", scope_uri(s), depth=-1 if s.recursive else 0)
                    for s in self.plan.includes
                ]
            )
        ]
        owner_filters.append(
            RawDSL({"op": "must_not", "field": "uri", "conds": [self.request_root], "para": "-d=0"})
        )
        owner_filters.extend(
            RawDSL(
                {
                    "op": "must_not",
                    "field": "uri",
                    "conds": [scope_uri(s)],
                    "para": "-d=-1" if s.recursive else "-d=0",
                }
            )
            for s in self.plan.excludes
        )
        return Or([*public_filters, And(owner_filters)])

    def _is_fully_allowed_directory(self, path: str | None) -> bool:
        """Whether the policy grants the directory and every descendant."""
        try:
            restricted = self.plan.restrict_scope(path)
        except GlobalPolicyError:
            return False
        return (
            restricted.status == "allowed"
            and not restricted.excludes
            and any(scope.path == path and scope.recursive for scope in restricted.includes)
        )

    def allows_overview(self, uri: str) -> bool:
        """L1 overview is exposed only for directories with no denied descendants.

        A partially allowed container may still expose its L0 abstract, but its
        overview aggregates child summaries and would otherwise leak the
        denied subtree.
        """
        uri = uri.rstrip("/")
        if uri.endswith("/.overview.md"):
            uri = uri[: -len("/.overview.md")]
        if uri != self.request_root and not uri.startswith(self.request_root + "/"):
            return False
        path = uri[len(self.request_root) + 1 :] or None
        return self._is_fully_allowed_directory(path)

    def decide(self, uri: str) -> AccessDecision:
        uri = uri.rstrip("/")
        if uri != self.request_root and not uri.startswith(self.request_root + "/"):
            return AccessDecision.DENIED
        if not any(uri == t or uri.startswith(t + "/") for t in self.requested_targets):
            return AccessDecision.DENIED
        path = uri[len(self.request_root) + 1 :] or None
        # Default-readable entries grant only the file itself, never a subtree.
        if path and "/" in path and path.split("/", 1)[0] in self._DEFAULT_READABLE_FILES:
            return AccessDecision.DENIED
        if path is not None and (path == ".overview.md" or path.endswith("/.overview.md")):
            if not self.allows_overview(uri):
                return AccessDecision.DENIED
        if path is not None and self.plan.allows(path):
            return AccessDecision.ALLOWED
        if self.plan.has_allowed_descendant(path):
            return AccessDecision.CONTAINER
        return AccessDecision.DENIED


def data_owner_id(ctx: RequestContext) -> str:
    return ctx.data_access.owner_user_id if ctx.data_access else ctx.user.user_id


def data_user_root(ctx: RequestContext) -> str:
    return f"viking://user/{data_owner_id(ctx)}"


def is_delegated(ctx: RequestContext) -> bool:
    return ctx.data_access is not None


@dataclass
class RequestContext:
    """Request-level context, flows through Router -> Service -> VikingFS."""

    user: UserIdentifier
    role: Role
    # Account-scoped groups resolved by the server for this request. Callers do
    # not supply these values through headers or ACL payloads.
    group_ids: tuple[str, ...] = ()
    # Request-level view filter for the current user's peers collection. This does
    # not change tenant/user identity or session ownership.
    actor_peer_id: Optional[str] = None
    # Mirrors ResolvedIdentity.from_oauth. Routes that mint OAuth state
    # (OTP issuance, oauth-verify) reject callers with from_oauth=True to
    # prevent a stolen access token from laundering itself into a long-lived
    # refresh-token chain.
    from_oauth: bool = False
    # Raw API key from the request — used by Connector to call back into OV
    # on behalf of the original user
    api_key: Optional[str] = field(default=None, repr=False)
    # Trusted background workers may maintain derived content without replaying
    # the triggering user's resource ACL. Tenant and namespace checks still apply.
    bypass_acl: bool = False
    data_access: DataAccessScope | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        self.role = Role.parse(self.role)
        if self.data_access is not None:
            from openviking.storage.acl import AclAction

            if (
                self.role != Role.GLOBAL
                or self.bypass_acl
                or self.data_access.operation is not AclAction.READ
            ):
                raise ValueError("Delegated data access requires GLOBAL READ without ACL bypass")

    @property
    def account_id(self) -> str:
        return self.user.account_id


@dataclass
class ToolContext:
    """Tool-level context, containing request context and additional tool-specific information."""

    viking_fs: VikingFS
    request_ctx: RequestContext
    default_search_uris: List[str] = field(default_factory=list)
    transaction_handle: Optional[Any] = None
    read_file_contents: Optional[Any] = None  # 用于记录已读取的文件内容
    page_id_map: Optional[Any] = None  # PageIdMap for annotating read results

    @property
    def user(self):
        return self.request_ctx.user

    @property
    def role(self):
        return self.request_ctx.role

    @property
    def account_id(self) -> str:
        return self.request_ctx.user.account_id
