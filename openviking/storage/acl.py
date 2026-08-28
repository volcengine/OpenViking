"""Context-backed ACL storage and permission resolution."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING, Any, Awaitable, Callable, Iterable, Mapping, Sequence

from openviking.core.identifiers import validate_identifier_part, validate_user_id
from openviking.core.namespace import uri_parts
from openviking.server.identity import RequestContext, Role
from openviking.storage.expr import FilterExpr, Or, PathScope
from openviking_cli.exceptions import InvalidArgumentError

if TYPE_CHECKING:
    from openviking.storage.viking_vector_index_backend import VikingVectorIndexBackend


class AclLevel(str, Enum):
    READ = "read"
    WRITE = "write"
    MANAGE = "manage"


class AclAction(str, Enum):
    READ = "read"
    WRITE = "write"
    MANAGE = "manage"


class CreatorAclGrant(str, Enum):
    DIRECT = "direct"
    INHERITED = "inherited"


_LEVEL_MASK = {
    AclLevel.READ: 1,
    AclLevel.WRITE: 3,
    AclLevel.MANAGE: 7,
}
_MASK_LEVEL = {mask: level for level, mask in _LEVEL_MASK.items()}
ACL_GRANT_FIELDS = ("acl_direct_grants", "acl_inherited_grants")
ACL_CONTEXT_FIELDS = frozenset(("acl_enabled", *ACL_GRANT_FIELDS))
ACL_CREATOR_GRANT_FIELD = "_acl_creator_grant"
_ACL_OUTPUT_FIELDS = ["uri", *sorted(ACL_CONTEXT_FIELDS)]


@dataclass(frozen=True)
class AclEntry:
    principal: str
    level: AclLevel

    def to_dict(self) -> dict[str, str]:
        return {"principal": self.principal, "level": self.level.value}


@dataclass(frozen=True)
class DirectAcl:
    entries: tuple[AclEntry, ...] = ()

    @classmethod
    def from_entries(
        cls, entries: Iterable[AclEntry | Mapping[str, Any]]
    ) -> "DirectAcl":
        return cls(tuple(_normalize_entries(entries)))

    @property
    def empty(self) -> bool:
        return not self.entries

    def union(self, other: "DirectAcl") -> "DirectAcl":
        return DirectAcl.from_entries((*self.entries, *other.entries))

    def principals_for(self, action: AclAction) -> frozenset[str]:
        required_mask = _LEVEL_MASK[AclLevel(action.value)]
        return frozenset(
            entry.principal
            for entry in self.entries
            if _LEVEL_MASK[entry.level] & required_mask == required_mask
        )

    def context_fields(self, prefix: str) -> dict[str, Any]:
        return {f"{prefix}_grants": [_encode_grant(entry) for entry in self.entries]}

    @classmethod
    def from_context_fields(cls, record: Mapping[str, Any], prefix: str) -> "DirectAcl":
        raw_grants = record.get(f"{prefix}_grants") or []
        if not isinstance(raw_grants, (list, tuple)):
            raise RuntimeError(f"Invalid ACL grants for {prefix}: expected a list")
        return cls.from_entries(_decode_grant(grant) for grant in raw_grants)


@dataclass(frozen=True)
class EffectiveAcl:
    enabled: bool
    direct: DirectAcl
    inherited: DirectAcl

    @property
    def permissions(self) -> DirectAcl:
        return self.inherited.union(self.direct)

    def context_fields(self) -> dict[str, Any]:
        return {
            "acl_enabled": self.enabled,
            **self.direct.context_fields("acl_direct"),
            **self.inherited.context_fields("acl_inherited"),
        }


def normalize_acl_principal(principal: Any) -> str:
    if not isinstance(principal, str):
        raise InvalidArgumentError("principal must be a string")
    normalized = principal.strip()
    kind, separator, identifier = normalized.partition(":")
    if not separator or kind not in {"user", "group"}:
        raise InvalidArgumentError("principal must use user:<id> or group:<id>")
    if kind == "user":
        if identifier != "*":
            error = validate_user_id(identifier)
            if error:
                raise InvalidArgumentError(error)
    else:
        if identifier == "*":
            raise InvalidArgumentError("group:* is not supported")
        error = validate_identifier_part(identifier, "group_id")
        if error:
            raise InvalidArgumentError(error)
    return normalized


def acl_principals(ctx: RequestContext) -> frozenset[str]:
    return frozenset(
        [
            f"user:{ctx.user.user_id}",
            "user:*",
            *(f"group:{value}" for value in ctx.group_ids),
        ]
    )


def normalize_acl_level(value: Any) -> AclLevel:
    if isinstance(value, AclLevel):
        return value
    try:
        return AclLevel(str(value).strip())
    except ValueError as exc:
        raise InvalidArgumentError("ACL level must be read, write, or manage") from exc


def _encode_grant(entry: AclEntry) -> str:
    return f"{_LEVEL_MASK[entry.level]}:{entry.principal}"


def _decode_grant(raw: Any) -> AclEntry:
    if not isinstance(raw, str):
        raise RuntimeError("Invalid ACL grant token: expected a string")
    raw_mask, separator, raw_principal = raw.partition(":")
    try:
        level = _MASK_LEVEL[int(raw_mask)] if separator else None
        principal = normalize_acl_principal(raw_principal)
    except (InvalidArgumentError, KeyError, ValueError) as exc:
        raise RuntimeError(f"Invalid ACL grant token: {raw!r}") from exc
    if level is None or raw != _encode_grant(AclEntry(principal, level)):
        raise RuntimeError(f"Invalid ACL grant token: {raw!r}")
    return AclEntry(principal, level)


def acl_grant_tokens(principals: Iterable[str], action: AclAction) -> list[str]:
    """Return exact scalar-index tokens that satisfy *action* for the principals."""
    required_mask = _LEVEL_MASK[AclLevel(action.value)]
    return [
        _encode_grant(AclEntry(principal, level))
        for principal in sorted(set(principals))
        for level, mask in _LEVEL_MASK.items()
        if mask & required_mask == required_mask
    ]


def _normalize_entries(entries: Iterable[AclEntry | Mapping[str, Any]]) -> list[AclEntry]:
    highest: dict[str, AclLevel] = {}
    for raw in entries:
        if isinstance(raw, AclEntry):
            principal, raw_level = raw.principal, raw.level
        else:
            principal = raw.get("principal")
            raw_level = raw.get("level", "")
        principal = normalize_acl_principal(principal)
        level = normalize_acl_level(raw_level)
        current = highest.get(principal)
        if current is None or _LEVEL_MASK[level] > _LEVEL_MASK[current]:
            highest[principal] = level
    return [AclEntry(principal, highest[principal]) for principal in sorted(highest)]


def is_acl_uri(uri: str) -> bool:
    return uri_parts(uri)[:1] == ["resources"]


def acl_ancestors(uri: str) -> list[str]:
    """Return ACL-bearing ancestors from the resource root through *uri*."""
    parts = uri_parts(uri)
    if parts[:1] != ["resources"]:
        raise InvalidArgumentError("ACL is only supported for viking://resources")
    return [f"viking://{'/'.join(parts[:depth])}" for depth in range(1, len(parts) + 1)]


def has_implicit_manage(ctx: RequestContext, uri: str) -> bool:
    return is_acl_uri(uri) and ctx.role == Role.ADMIN


def acl_allows(acl: EffectiveAcl, ctx: RequestContext, action: AclAction) -> bool:
    principals = acl.permissions.principals_for(action)
    return not principals.isdisjoint(acl_principals(ctx))


class AclManager:
    """Stores direct and inherited ACL fields in the context collection."""

    def __init__(
        self,
        context_store: "VikingVectorIndexBackend",
        auto_protect_new_content: Callable[[str], Awaitable[bool]] | None = None,
    ) -> None:
        self._context_store = context_store
        self._auto_protect_new_content = auto_protect_new_content

    @staticmethod
    def _effective_from_record(record: Mapping[str, Any]) -> EffectiveAcl:
        direct = DirectAcl.from_context_fields(record, "acl_direct")
        inherited = DirectAcl.from_context_fields(record, "acl_inherited")
        return EffectiveAcl(
            enabled=bool(record.get("acl_enabled", False))
            or not direct.empty
            or not inherited.empty,
            direct=direct,
            inherited=inherited,
        )

    @classmethod
    def _effective_from_records(cls, records: Sequence[Mapping[str, Any]]) -> EffectiveAcl:
        if not records:
            return EffectiveAcl(False, DirectAcl(), DirectAcl())
        values = {cls._effective_from_record(record) for record in records}
        if len(values) != 1:
            uri = records[0].get("uri", "<unknown>")
            raise RuntimeError(f"Inconsistent ACL fields for context URI: {uri}")
        return values.pop()

    @staticmethod
    def _group_by_uri(records: Sequence[Mapping[str, Any]]) -> dict[str, list[Mapping[str, Any]]]:
        grouped: dict[str, list[Mapping[str, Any]]] = {}
        for record in records:
            uri = record.get("uri")
            if uri:
                grouped.setdefault(str(uri), []).append(record)
        return grouped

    async def _records_for_uris(
        self,
        uris: Iterable[str],
        ctx: RequestContext,
    ) -> list[dict[str, Any]]:
        unique = sorted(set(uris))
        records: list[dict[str, Any]] = []
        for offset in range(0, len(unique), 100):
            conditions = [PathScope("uri", uri, depth=0) for uri in unique[offset : offset + 100]]
            records.extend(
                await self._scroll_all(Or(conditions), _ACL_OUTPUT_FIELDS, ctx)
            )
        return records

    async def _scroll_all(
        self,
        filter_expr: FilterExpr,
        output_fields: list[str],
        ctx: RequestContext,
    ) -> list[dict[str, Any]]:
        records: list[dict[str, Any]] = []
        cursor: str | None = None
        while True:
            page, cursor = await self._context_store.scroll(
                filter=filter_expr,
                limit=500,
                cursor=cursor,
                output_fields=output_fields,
                ctx=ctx,
            )
            records.extend(page)
            if cursor is None:
                return records

    async def _subtree_records(self, uri: str, ctx: RequestContext) -> list[dict[str, Any]]:
        refs = await self._scroll_all(PathScope("uri", uri, depth=-1), ["id"], ctx)
        ids = [str(record["id"]) for record in refs if record.get("id")]
        records: list[dict[str, Any]] = []
        for offset in range(0, len(ids), 500):
            records.extend(
                await self._context_store.get_strict(ids[offset : offset + 500], ctx=ctx)
            )
        return records

    async def get_direct(self, uri: str, ctx: RequestContext) -> DirectAcl:
        acl_ancestors(uri)
        records = await self._records_for_uris([uri], ctx)
        return self._effective_from_records(records).direct

    async def resolve_many(
        self, uris: Iterable[str], ctx: RequestContext
    ) -> dict[str, EffectiveAcl]:
        unique_uris = list(dict.fromkeys(uris))
        paths = {uri: acl_ancestors(uri) for uri in unique_uris}
        exact_records = await self._records_for_uris(unique_uris, ctx)
        exact_groups = self._group_by_uri(exact_records)
        result = {
            uri: self._effective_from_records(exact_groups[uri])
            for uri in unique_uris
            if uri in exact_groups
        }

        missing = [uri for uri in unique_uris if uri not in result]
        if missing:
            ancestor_records = await self._records_for_uris(
                (ancestor for uri in missing for ancestor in paths[uri]), ctx
            )
            ancestor_groups = self._group_by_uri(ancestor_records)
            direct_map = {
                uri: self._effective_from_records(records).direct
                for uri, records in ancestor_groups.items()
            }
            for uri in missing:
                inherited = DirectAcl()
                for ancestor in paths[uri][:-1]:
                    inherited = inherited.union(direct_map.get(ancestor, DirectAcl()))
                direct = direct_map.get(uri, DirectAcl())
                result[uri] = EffectiveAcl(
                    enabled=not inherited.empty or not direct.empty,
                    direct=direct,
                    inherited=inherited,
                )
        return result

    async def resolve(self, uri: str, ctx: RequestContext) -> EffectiveAcl:
        return (await self.resolve_many([uri], ctx))[uri]

    async def materialize_context_records(
        self, records: Sequence[dict[str, Any]], ctx: RequestContext
    ) -> list[dict[str, Any]]:
        resource_uris: set[str] = set()
        for record in records:
            uri = record.get("uri")
            if not uri:
                continue
            resource_uri = str(uri)
            if not is_acl_uri(resource_uri):
                continue
            resource_uris.add(resource_uri)
        if not resource_uris:
            return list(records)

        existing = await self._records_for_uris(resource_uris, ctx)
        existing_groups = self._group_by_uri(existing)
        existing_acl = {
            uri: self._effective_from_records(items) for uri, items in existing_groups.items()
        }
        new_uris = resource_uris.difference(existing_acl)
        parents: dict[str, str | None] = {}
        for uri in new_uris:
            ancestors = acl_ancestors(uri)
            parents[uri] = ancestors[-2] if len(ancestors) > 1 else None

        parent_acl = await self.resolve_many([parent for parent in parents.values() if parent], ctx)
        auto_protect_new_content: bool | None = None

        materialized: list[dict[str, Any]] = []
        for record in records:
            record = dict(record)
            raw_creator_grant = record.pop(ACL_CREATOR_GRANT_FIELD, None)
            creator_grant = (
                CreatorAclGrant(raw_creator_grant) if raw_creator_grant is not None else None
            )
            source_uri = str(record.get("uri") or "")
            if source_uri not in resource_uris:
                materialized.append(record)
                continue
            effective = existing_acl.get(source_uri)
            if effective is None:
                parent = parents[source_uri]
                inherited = parent_acl[parent].permissions if parent else DirectAcl()
                direct = DirectAcl()
                creator = (record.get("user") or {}).get("user_id")
                protect_created = bool(parent and parent_acl[parent].enabled)
                if (
                    creator
                    and creator_grant is not None
                    and parent
                    and not protect_created
                    and self._auto_protect_new_content is not None
                ):
                    if auto_protect_new_content is None:
                        auto_protect_new_content = await self._auto_protect_new_content(
                            ctx.account_id
                        )
                    protect_created = auto_protect_new_content
                if creator and creator_grant is not None and protect_created:
                    creator_acl = DirectAcl.from_entries(
                        [AclEntry(f"user:{creator}", AclLevel.MANAGE)]
                    )
                    if creator_grant == CreatorAclGrant.DIRECT:
                        direct = creator_acl
                    else:
                        inherited = inherited.union(creator_acl)
                effective = EffectiveAcl(not direct.empty or not inherited.empty, direct, inherited)
            materialized.append({**record, **effective.context_fields()})
        return materialized

    async def materialize_moved_record(
        self, record: Mapping[str, Any], new_uri: str, ctx: RequestContext
    ) -> dict[str, Any]:
        if not is_acl_uri(new_uri):
            return EffectiveAcl(False, DirectAcl(), DirectAcl()).context_fields()
        ancestors = acl_ancestors(new_uri)
        parent = ancestors[-2] if len(ancestors) > 1 else None
        inherited = (await self.resolve(parent, ctx)).permissions if parent else DirectAcl()
        source_uri = str(record.get("uri") or "")
        direct = (
            DirectAcl.from_context_fields(record, "acl_direct")
            if is_acl_uri(source_uri)
            else DirectAcl()
        )
        return EffectiveAcl(
            enabled=not direct.empty or not inherited.empty,
            direct=direct,
            inherited=inherited,
        ).context_fields()

    async def _apply_subtree(
        self,
        root_uri: str,
        records: Sequence[dict[str, Any]],
        ctx: RequestContext,
        *,
        root_direct: DirectAcl | None = None,
    ) -> EffectiveAcl:
        grouped = self._group_by_uri(records)
        if root_uri not in grouped:
            raise InvalidArgumentError("ACL target has no context record; index it first")

        direct_map = {
            uri: self._effective_from_records(items).direct for uri, items in grouped.items()
        }
        if root_direct is not None:
            direct_map[root_uri] = root_direct

        root_ancestors = acl_ancestors(root_uri)
        parent = root_ancestors[-2] if len(root_ancestors) > 1 else None
        base = (await self.resolve(parent, ctx)).permissions if parent else DirectAcl()
        effective_by_uri: dict[str, EffectiveAcl] = {}
        root_depth = len(root_ancestors)
        for uri in grouped:
            ancestors = acl_ancestors(uri)
            inherited = base
            for ancestor in ancestors[root_depth - 1 : -1]:
                inherited = inherited.union(direct_map.get(ancestor, DirectAcl()))
            direct = direct_map.get(uri, DirectAcl())
            effective_by_uri[uri] = EffectiveAcl(
                enabled=not direct.empty or not inherited.empty,
                direct=direct,
                inherited=inherited,
            )

        updated = [
            {**record, **effective_by_uri[str(record["uri"])].context_fields()}
            for record in records
            if str(record.get("uri") or "") in effective_by_uri
        ]
        ids = await self._context_store._upsert_many_raw(updated, ctx=ctx)
        if len(ids) != len(updated):
            raise RuntimeError(f"Failed to update {len(updated) - len(ids)} context ACL record(s)")
        return effective_by_uri[root_uri]

    async def refresh_context_subtree(self, uri: str, ctx: RequestContext) -> None:
        records = await self._subtree_records(uri, ctx)
        if records:
            await self._apply_subtree(uri, records, ctx)

    async def set_direct(
        self,
        uri: str,
        entries: Sequence[AclEntry | Mapping[str, Any]],
        ctx: RequestContext,
    ) -> EffectiveAcl:
        if uri_parts(uri) == ["resources"]:
            raise InvalidArgumentError("ACL cannot be set on viking://resources")
        proposed = DirectAcl.from_entries(entries)
        old_records = await self._subtree_records(uri, ctx)
        try:
            effective = await self._apply_subtree(
                uri, old_records, ctx, root_direct=proposed
            )
        except Exception:
            if old_records:
                await self._context_store._upsert_many_raw(old_records, ctx=ctx)
            raise
        return effective

    @staticmethod
    def to_report(uri: str, effective: EffectiveAcl) -> dict[str, Any]:
        return {
            "uri": uri,
            "acl_enabled": effective.enabled,
            "direct_entries": [entry.to_dict() for entry in effective.direct.entries],
            "inherited_entries": [entry.to_dict() for entry in effective.inherited.entries],
            "effective_entries": [entry.to_dict() for entry in effective.permissions.entries],
        }
