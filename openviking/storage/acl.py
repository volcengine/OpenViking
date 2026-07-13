"""Context-backed ACL storage and permission resolution."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Iterable, Literal, Mapping, Sequence

from openviking.core.identifiers import validate_identifier_part, validate_user_id
from openviking.core.namespace import canonicalize_uri, uri_parts
from openviking.server.identity import RequestContext, Role
from openviking.storage.expr import Or, PathScope
from openviking_cli.exceptions import InvalidArgumentError

if TYPE_CHECKING:
    from openviking.storage.viking_vector_index_backend import VikingVectorIndexBackend

AclLevel = Literal["viewer", "editor", "manager"]
AclAction = Literal["read", "write", "manage"]

_LEVEL_RANK: dict[str, int] = {"viewer": 1, "editor": 2, "manager": 3}
_ACL_PREFIXES = ("acl_direct", "acl_inherited")
ACL_PRINCIPAL_FIELDS = tuple(
    f"{prefix}_{action}_principal_ids"
    for prefix in _ACL_PREFIXES
    for action in ("read", "write", "manage")
)
ACL_CONTEXT_FIELDS = frozenset(("acl_enabled", *ACL_PRINCIPAL_FIELDS))
_ACL_OUTPUT_FIELDS = ["uri", *sorted(ACL_CONTEXT_FIELDS)]


@dataclass(frozen=True)
class AclEntry:
    principal: str
    level: AclLevel

    def to_dict(self) -> dict[str, str]:
        return {"principal": self.principal, "level": self.level}


@dataclass(frozen=True)
class DirectAcl:
    read: frozenset[str] = frozenset()
    write: frozenset[str] = frozenset()
    manage: frozenset[str] = frozenset()

    @property
    def empty(self) -> bool:
        return not (self.read or self.write or self.manage)

    def union(self, other: "DirectAcl") -> "DirectAcl":
        return DirectAcl(
            read=self.read | other.read,
            write=self.write | other.write,
            manage=self.manage | other.manage,
        )

    def context_fields(self, prefix: str) -> dict[str, Any]:
        return {
            f"{prefix}_read_principal_ids": sorted(self.read),
            f"{prefix}_write_principal_ids": sorted(self.write),
            f"{prefix}_manage_principal_ids": sorted(self.manage),
        }

    @classmethod
    def from_context_fields(cls, record: Mapping[str, Any], prefix: str) -> "DirectAcl":
        manage = frozenset(record.get(f"{prefix}_manage_principal_ids") or [])
        write = frozenset(record.get(f"{prefix}_write_principal_ids") or []) | manage
        read = frozenset(record.get(f"{prefix}_read_principal_ids") or []) | write
        return cls(read=read, write=write, manage=manage)


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


def _normalize_entries(entries: Iterable[AclEntry | Mapping[str, Any]]) -> list[AclEntry]:
    highest: dict[str, str] = {}
    for raw in entries:
        if isinstance(raw, AclEntry):
            principal, level = raw.principal, raw.level
        else:
            principal = raw.get("principal")
            level = str(raw.get("level", "")).strip()
        principal = normalize_acl_principal(principal)
        if level not in _LEVEL_RANK:
            raise InvalidArgumentError("ACL level must be viewer, editor, or manager")
        current = highest.get(principal)
        if current is None or _LEVEL_RANK[level] > _LEVEL_RANK[current]:
            highest[principal] = level
    return [AclEntry(principal, highest[principal]) for principal in sorted(highest)]


def entries_to_direct(entries: Iterable[AclEntry | Mapping[str, Any]]) -> DirectAcl:
    read: set[str] = set()
    write: set[str] = set()
    manage: set[str] = set()
    for entry in _normalize_entries(entries):
        read.add(entry.principal)
        if entry.level in {"editor", "manager"}:
            write.add(entry.principal)
        if entry.level == "manager":
            manage.add(entry.principal)
    return DirectAcl(frozenset(read), frozenset(write), frozenset(manage))


def direct_to_entries(acl: DirectAcl) -> list[AclEntry]:
    entries: list[AclEntry] = []
    for principal in sorted(acl.read | acl.write | acl.manage):
        if principal in acl.manage:
            level: AclLevel = "manager"
        elif principal in acl.write:
            level = "editor"
        else:
            level = "viewer"
        entries.append(AclEntry(principal, level))
    return entries


def acl_ancestors(uri: str) -> list[str]:
    """Return ACL-bearing ancestors from the resource root through *uri*."""
    parts = uri_parts(uri)
    if parts[:1] != ["resources"]:
        raise InvalidArgumentError("ACL is only supported for viking://resources")
    return [f"viking://{'/'.join(parts[:depth])}" for depth in range(1, len(parts) + 1)]


def is_implicit_manager(ctx: RequestContext, uri: str) -> bool:
    return uri_parts(uri)[:1] == ["resources"] and ctx.role == Role.ADMIN


def acl_allows(acl: EffectiveAcl, ctx: RequestContext, action: AclAction) -> bool:
    principals = getattr(acl.permissions, action)
    return not principals.isdisjoint(acl_principals(ctx))


class AclManager:
    """Stores direct and inherited ACL fields in the context collection."""

    def __init__(self, context_store: "VikingVectorIndexBackend") -> None:
        self._context_store = context_store
        context_store.acl_manager = self

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
            cursor: str | None = None
            while True:
                page, cursor = await self._context_store.scroll(
                    filter=Or(conditions),
                    limit=500,
                    cursor=cursor,
                    output_fields=_ACL_OUTPUT_FIELDS,
                    ctx=ctx,
                )
                records.extend(page)
                if cursor is None:
                    break
        return records

    async def _subtree_records(self, uri: str, ctx: RequestContext) -> list[dict[str, Any]]:
        refs: list[dict[str, Any]] = []
        cursor: str | None = None
        while True:
            page, cursor = await self._context_store.scroll(
                filter=PathScope("uri", uri, depth=-1),
                limit=500,
                cursor=cursor,
                output_fields=["id"],
                ctx=ctx,
            )
            refs.extend(page)
            if cursor is None:
                break
        ids = [str(record["id"]) for record in refs if record.get("id")]
        records: list[dict[str, Any]] = []
        for offset in range(0, len(ids), 500):
            records.extend(
                await self._context_store.get_strict(ids[offset : offset + 500], ctx=ctx)
            )
        return records

    async def get_direct(self, uri: str, ctx: RequestContext) -> DirectAcl:
        canonical_uri = canonicalize_uri(uri, ctx)
        acl_ancestors(canonical_uri)
        records = await self._records_for_uris([canonical_uri], ctx)
        return self._effective_from_records(records).direct

    async def resolve_many(
        self, uris: Iterable[str], ctx: RequestContext
    ) -> dict[str, EffectiveAcl]:
        canonical_uris = list(dict.fromkeys(canonicalize_uri(uri, ctx) for uri in uris))
        paths = {uri: acl_ancestors(uri) for uri in canonical_uris}
        exact_records = await self._records_for_uris(canonical_uris, ctx)
        exact_groups = self._group_by_uri(exact_records)
        result = {
            uri: self._effective_from_records(exact_groups[uri])
            for uri in canonical_uris
            if uri in exact_groups
        }

        missing = [uri for uri in canonical_uris if uri not in result]
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
        canonical_uri = canonicalize_uri(uri, ctx)
        return (await self.resolve_many([canonical_uri], ctx))[canonical_uri]

    async def materialize_context_records(
        self, records: Sequence[dict[str, Any]], ctx: RequestContext
    ) -> list[dict[str, Any]]:
        canonical_by_uri: dict[str, str] = {}
        for record in records:
            uri = record.get("uri")
            if not uri:
                continue
            canonical = canonicalize_uri(str(uri), ctx)
            try:
                acl_ancestors(canonical)
            except InvalidArgumentError:
                continue
            canonical_by_uri[str(uri)] = canonical
        if not canonical_by_uri:
            return list(records)

        existing = await self._records_for_uris(canonical_by_uri.values(), ctx)
        existing_groups = self._group_by_uri(existing)
        existing_acl = {
            uri: self._effective_from_records(items) for uri, items in existing_groups.items()
        }
        new_uris = [uri for uri in canonical_by_uri.values() if uri not in existing_acl]
        parents: dict[str, str | None] = {}
        for uri in new_uris:
            ancestors = acl_ancestors(uri)
            parents[uri] = ancestors[-2] if len(ancestors) > 1 else None
        parent_acl = await self.resolve_many([parent for parent in parents.values() if parent], ctx)

        materialized: list[dict[str, Any]] = []
        for record in records:
            source_uri = str(record.get("uri") or "")
            canonical = canonical_by_uri.get(source_uri)
            if not canonical:
                materialized.append(record)
                continue
            effective = existing_acl.get(canonical)
            if effective is None:
                parent = parents[canonical]
                inherited = parent_acl[parent].permissions if parent else DirectAcl()
                effective = EffectiveAcl(not inherited.empty, DirectAcl(), inherited)
            materialized.append({**record, **effective.context_fields()})
        return materialized

    async def materialize_moved_record(
        self, record: Mapping[str, Any], new_uri: str, ctx: RequestContext
    ) -> dict[str, Any]:
        if uri_parts(new_uri)[:1] != ["resources"]:
            return EffectiveAcl(False, DirectAcl(), DirectAcl()).context_fields()
        ancestors = acl_ancestors(new_uri)
        parent = ancestors[-2] if len(ancestors) > 1 else None
        inherited = (await self.resolve(parent, ctx)).permissions if parent else DirectAcl()
        source_uri = str(record.get("uri") or "")
        direct = (
            DirectAcl.from_context_fields(record, "acl_direct")
            if uri_parts(source_uri)[:1] == ["resources"]
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
        for uri in grouped:
            ancestors = acl_ancestors(uri)
            try:
                root_index = ancestors.index(root_uri)
            except ValueError:
                continue
            inherited = base
            for ancestor in ancestors[root_index:-1]:
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
        ids = await self._context_store.upsert_many(updated, ctx=ctx, _acl_materialized=True)
        if len(ids) != len(updated):
            raise RuntimeError(f"Failed to update {len(updated) - len(ids)} context ACL record(s)")
        return effective_by_uri[root_uri]

    async def refresh_context_subtree(self, uri: str, ctx: RequestContext) -> None:
        canonical_uri = canonicalize_uri(uri, ctx)
        records = await self._subtree_records(canonical_uri, ctx)
        if records:
            await self._apply_subtree(canonical_uri, records, ctx)

    async def set_direct(
        self,
        uri: str,
        entries: Sequence[AclEntry | Mapping[str, Any]],
        ctx: RequestContext,
    ) -> EffectiveAcl:
        canonical_uri = canonicalize_uri(uri, ctx)
        proposed = entries_to_direct(entries)
        old_records = await self._subtree_records(canonical_uri, ctx)
        try:
            effective = await self._apply_subtree(
                canonical_uri, old_records, ctx, root_direct=proposed
            )
        except Exception:
            if old_records:
                await self._context_store.upsert_many(old_records, ctx=ctx, _acl_materialized=True)
            raise
        return effective

    @staticmethod
    def to_report(uri: str, effective: EffectiveAcl) -> dict[str, Any]:
        return {
            "uri": uri,
            "acl_enabled": effective.enabled,
            "direct_entries": [entry.to_dict() for entry in direct_to_entries(effective.direct)],
            "inherited_entries": [
                entry.to_dict() for entry in direct_to_entries(effective.inherited)
            ],
            "effective_entries": [
                entry.to_dict() for entry in direct_to_entries(effective.permissions)
            ],
        }

    async def report(self, uri: str, ctx: RequestContext) -> dict[str, Any]:
        canonical_uri = canonicalize_uri(uri, ctx)
        return self.to_report(canonical_uri, await self.resolve(canonical_uri, ctx))
