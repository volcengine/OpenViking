# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Access control, URI/path conversion, and visibility mixin for VikingFS."""

import hashlib
import inspect
import re
from contextlib import contextmanager
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Union

from openviking.core.context import ContextLevel
from openviking.core.namespace import (
    is_accessible as namespace_is_accessible,
)
from openviking.core.namespace import (
    is_hidden_by_actor_peer_view,
    may_include_hidden_actor_peers,
)
from openviking.core.namespace_policy import is_system_custom_asset_uri, is_system_uri
from openviking.resource.watch_storage import is_watch_task_control_uri
from openviking.server.error_mapping import is_not_found_error
from openviking.server.identity import RequestContext, Role
from openviking.storage.acl import (
    AclAction,
    AclEntry,
    AclLevel,
    AclMode,
    AclSpec,
    AclUpdate,
    acl_allows,
    acl_ancestors,
    has_implicit_manage,
    is_acl_uri,
    normalize_acl_level,
    normalize_acl_principal,
)
from openviking.storage.internal_names import is_storage_internal_name
from openviking_cli.exceptions import (
    FailedPreconditionError,
    InvalidArgumentError,
    NotFoundError,
    PermissionDeniedError,
)
from openviking_cli.session.user_id import UserIdentifier
from openviking_cli.utils.uri import VikingURI


class _AccessMixin:
    """URI normalization, access control, path conversion, and visibility helpers."""

    _GLOB_PAGE_SIZE_DEFAULT = 1024

    # Maximum bytes for a single filename component (filesystem limit is typically 255)
    _MAX_FILENAME_BYTES = 255

    _ROOT_PATH = "/local"

    # First path segments that the Rust git enumerate.rs prunes from snapshots,
    # plus the runtime lock name. Mirrors INTERNAL_FIRST_SEGMENTS in
    # crates/ragfs/src/git/enumerate.rs so that
    # callers fail fast in Python with a clear error rather than passing a
    # path that the Rust side will silently drop.
    _GIT_INTERNAL_FIRST_SEGMENTS = frozenset(
        {
            "_system",
            "tasks",
            "temp",
            "queue",
            "upload",
            ".path.ovlock",
        }
    )

    _DEFAULT_GIT_AUTHOR_NAME = "viking-bot"
    _DEFAULT_GIT_AUTHOR_EMAIL = "bot@viking.local"

    _OVGITIGNORE_TREE_PATH = ".ovgitignore"

    # Must stay in sync with `OVGITIGNORE_MAX_BYTES` in the Rust layer
    # (`crates/ragfs/src/git/ignore.rs`); enforced here at write time so a bad
    # file can never be persisted only to poison every later commit.
    _OVGITIGNORE_MAX_BYTES = 64 * 1024

    _DIR_MARKER_LEVELS = {
        ".abstract.md": ContextLevel.ABSTRACT,
        ".overview.md": ContextLevel.OVERVIEW,
    }
    _NO_VECTOR_DERIVED = frozenset({".relations.json", ".ovgitignore"})

    def set_deletion_guard(self, guard: Optional[Callable[[str, str], bool]]) -> None:
        self._deletion_guard = guard

    @staticmethod
    def _default_ctx() -> RequestContext:
        return RequestContext(user=UserIdentifier.the_default_user(), role=Role.ROOT)

    def _ctx_or_default(self, ctx: Optional[RequestContext]) -> RequestContext:
        if ctx is not None:
            return ctx
        bound = self._bound_ctx.get()
        return bound or self._default_ctx()

    def _require_request_context(self, ctx: Optional[RequestContext]) -> RequestContext:
        """Resolve an account context without inventing the default account."""
        if ctx is None and self._bound_ctx.get() is None:
            raise RuntimeError(
                "Account request context is required for account-scoped operations"
            )
        return self._ctx_or_default(ctx)

    @contextmanager
    def bind_request_context(self, ctx: RequestContext):
        """Temporarily bind ctx for legacy internal call paths without explicit ctx param."""
        token = self._bound_ctx.set(ctx)
        try:
            yield
        finally:
            self._bound_ctx.reset(token)

    @staticmethod
    def _safe_uri_parts(uri: str) -> List[str]:
        """Split a canonical URI and reject unsafe path traversal forms."""
        if not uri.startswith("viking://"):
            raise ValueError("URI must start with 'viking://'")
        parts = [p for p in uri[len("viking://") :].strip("/").split("/") if p]

        for part in parts:
            if part in {".", ".."}:
                raise PermissionDeniedError(
                    f"Unsafe URI traversal segment '{part}' in {uri}",
                    resource=uri,
                )
            if "\\" in part:
                raise PermissionDeniedError(
                    f"Unsafe URI path separator '\\\\' in component '{part}' of {uri}",
                    resource=uri,
                )
            if len(part) >= 2 and part[1] == ":" and part[0].isalpha():
                raise PermissionDeniedError(
                    f"Unsafe URI drive-prefixed component '{part}' in {uri}",
                    resource=uri,
                )

        return parts

    # TODO: Once pathlock moves down into ragfs, stop reconstructing the
    # encrypted mount-relative path in Python and derive the lock target from
    # the same backend-side source of truth.
    def _encrypted_mount_relative_path(self, path: str) -> tuple[str, str]:
        """Return the mount prefix and mount-relative path used by Rust encrypted writes."""
        normalized = path if path.startswith("/") else f"/{path}"
        parts = [part for part in normalized.split("/") if part]
        if len(parts) < 2:
            return "", normalized
        return f"/{parts[0]}", f"/{'/'.join(parts[1:])}"

    def _encrypted_temp_path(self, path: str) -> str:
        """Build the deterministic internal `.encrypt` temp-file path for a final file path."""
        mount_prefix, relative_path = self._encrypted_mount_relative_path(path)
        relative_parts = [part for part in relative_path.split("/") if part]
        if len(relative_parts) >= 2 and relative_parts[0] == "local":
            temp_root = f"/local/{relative_parts[1]}/temp/.encrypt_stage"
        elif len(relative_parts) >= 2:
            temp_root = f"/{relative_parts[0]}/temp/.encrypt_stage"
        else:
            temp_root = "/temp/.encrypt_stage"
        digest = hashlib.sha256(relative_path.encode("utf-8")).hexdigest()
        return f"{mount_prefix}{temp_root}/{digest}.encrypt"

    @staticmethod
    def _system_permission_allows(
        uri: str,
        ctx: RequestContext,
        action: AclAction,
    ) -> bool:
        """Apply the fixed role and path policy for the account system namespace."""
        if ctx.role == Role.ROOT:
            return True
        if action is AclAction.READ:
            return True
        return (
            ctx.role == Role.ADMIN
            and action in {AclAction.WRITE, AclAction.MANAGE}
            and is_system_custom_asset_uri(uri)
        )

    async def _can_access_many(
        self,
        uris: Sequence[str],
        ctx: Optional[RequestContext],
        *,
        action: AclAction = AclAction.READ,
    ) -> Dict[str, bool]:
        if not isinstance(action, AclAction):
            raise TypeError(f"action must be AclAction, got {type(action).__name__}")
        real_ctx = self._ctx_or_default(ctx)
        result: Dict[str, bool] = {}
        valid: List[str] = []
        for uri in dict.fromkeys(uris):
            try:
                self._safe_uri_parts(uri)
            except ValueError:
                result[uri] = False
            else:
                valid.append(uri)

        remaining: List[str] = []
        for uri in valid:
            if is_system_uri(uri):
                result[uri] = self._system_permission_allows(uri, real_ctx, action)
            else:
                remaining.append(uri)
        if not remaining:
            return result

        acl_manager = self.acl_manager
        if acl_manager is None or not await acl_manager.is_enabled(real_ctx.account_id):
            result.update({uri: self._is_accessible(uri, real_ctx) for uri in remaining})
            return result

        pending: List[str] = []
        for uri in remaining:
            if is_watch_task_control_uri(uri):
                result[uri] = self._is_accessible(uri, real_ctx)
                continue
            if not is_acl_uri(uri):
                result[uri] = self._is_accessible(uri, real_ctx)
                continue
            if real_ctx.bypass_acl or has_implicit_manage(real_ctx, uri):
                result[uri] = True
            else:
                pending.append(uri)

        effective = await acl_manager.resolve_many(pending, real_ctx) if pending else {}
        for uri in pending:
            result[uri] = acl_allows(effective[uri], real_ctx, action)
        return result

    async def _ensure_access(
        self,
        uri: str,
        ctx: Optional[RequestContext],
        *,
        action: AclAction = AclAction.READ,
    ) -> None:
        await self._ensure_access_many([uri], ctx, action=action)

    async def _ensure_destination_access(self, uri: str, ctx: Optional[RequestContext]) -> bool:
        """Authorize copy/move/import destinations and return whether the target exists."""
        try:
            stat = await self._async_agfs.stat(self._uri_to_path(uri, ctx=ctx))
        except Exception as exc:
            if not is_not_found_error(exc):
                raise
            parent = VikingURI(uri).parent
            if parent is None:
                raise InvalidArgumentError(f"Destination has no parent: {uri}") from exc
            checked_uri, action, exists = parent.uri, AclAction.WRITE, False
        else:
            checked_uri = uri
            action = AclAction.MANAGE if stat["isDir"] else AclAction.WRITE
            exists = True

        # Non-ACL namespaces authorize the target path using their fixed policy.
        if not is_acl_uri(uri):
            checked_uri = uri
        # Capability checks must not apply the namespace-root deletion guard.
        access = await self._can_access_many([checked_uri], ctx, action=action)
        if not access[checked_uri]:
            raise PermissionDeniedError(
                f"Access denied for {checked_uri}: {action.value} permission required",
                resource=checked_uri,
            )
        return exists

    async def _ensure_access_many(
        self,
        uris: Sequence[str],
        ctx: Optional[RequestContext],
        *,
        action: AclAction,
    ) -> None:
        real_ctx = self._ctx_or_default(ctx)
        access = await self._can_access_many(uris, real_ctx, action=action)
        denied = next((uri for uri in uris if not access.get(uri, False)), None)
        if denied is not None:
            raise PermissionDeniedError(
                f"Access denied for {denied}: {action.value} permission required",
                resource=denied,
            )

        if action is AclAction.READ:
            return

        await self._ensure_identity_not_deleting(real_ctx)
        for uri in uris:
            self._safe_uri_parts(uri)
            if uri == "viking://" and real_ctx.role == Role.USER:
                raise PermissionDeniedError(
                    "Writing the account root requires an administrator",
                    resource=uri,
                )
            if is_hidden_by_actor_peer_view(uri, real_ctx) or may_include_hidden_actor_peers(
                uri, real_ctx
            ):
                raise PermissionDeniedError(f"Access denied for {uri}", resource=uri)
            if action is AclAction.MANAGE:
                self._ensure_supported_delete_namespace(uri)
                canonical_parts = self._safe_uri_parts(uri)
                if real_ctx.role != Role.ROOT and (
                    canonical_parts == ["resources"]
                    or (canonical_parts[:1] == ["user"] and len(canonical_parts) == 2)
                ):
                    raise PermissionDeniedError(
                        "Deleting a namespace root requires root access; use a concrete content "
                        "path instead.",
                        resource=uri,
                    )
            self._ensure_supported_write_namespace(uri)
            if real_ctx.role != Role.ROOT and uri.rstrip("/") == "viking://temp":
                raise PermissionDeniedError(
                    "Temp root is read-only for non-root users",
                    resource=uri,
                )

    async def _ensure_descendant_access(
        self, uri: str, ctx: Optional[RequestContext], *, action: AclAction
    ) -> None:
        """Check shared descendants after the requested node has been authorized."""
        real_ctx = self._ctx_or_default(ctx)
        if (
            not is_acl_uri(uri)
            or real_ctx.bypass_acl
            or has_implicit_manage(real_ctx, uri)
            or not await self._acl_enabled(real_ctx)
        ):
            return
        async for denied in self.acl_manager.iter_denied_uris(uri, real_ctx, action=action):
            # A leftover index entry or virtual chunk is not copied filesystem content.
            if await self.exists(denied, ctx=real_ctx):
                raise PermissionDeniedError(
                    f"Access denied for {denied}: {action.value} permission required",
                    resource=denied,
                )

    async def _ensure_retrieval_scope(self, uri: str, ctx: Optional[RequestContext]) -> None:
        self._safe_uri_parts(uri)
        if await self._acl_enabled(ctx) and is_acl_uri(uri):
            return
        await self._ensure_access(uri, ctx)

    async def _acl_enabled(self, ctx: Optional[RequestContext]) -> bool:
        real_ctx = self._ctx_or_default(ctx)
        return self.acl_manager is not None and await self.acl_manager.is_enabled(
            real_ctx.account_id
        )

    async def _ensure_acl_manage(self, uri: str, ctx: Optional[RequestContext]) -> RequestContext:
        if self.acl_manager is None:
            raise RuntimeError("ACL is not initialized")
        real_ctx = self._ctx_or_default(ctx)
        self._safe_uri_parts(uri)
        acl_ancestors(uri)
        if has_implicit_manage(real_ctx, uri):
            return real_ctx
        if await self.acl_manager.is_enabled(real_ctx.account_id):
            effective = await self.acl_manager.resolve(uri, real_ctx)
            if acl_allows(effective, real_ctx, AclAction.MANAGE):
                return real_ctx
        raise PermissionDeniedError(
            f"ACL management denied for {uri}: manage permission required", resource=uri
        )

    async def _ensure_acl_target_exists(self, uri: str, ctx: RequestContext) -> bool:
        """Return whether the ACL target is a directory; raise if it is missing."""
        try:
            stat = await self._async_agfs.stat(self._uri_to_path(uri, ctx=ctx))
        except Exception as exc:
            if is_not_found_error(exc):
                raise NotFoundError(uri, "resource") from exc
            raise
        return bool(stat.get("isDir", False)) if isinstance(stat, dict) else False

    async def _acquire_acl_target_lock(self, uri: str, ctx: RequestContext) -> Dict[str, Any]:
        """Lock an existing ACL target: Exact for a file, Tree for a directory.

        The existence check runs before the lock so a missing target returns
        NotFound instead of materializing a directory for lock metadata.
        """
        is_dir = await self._ensure_acl_target_exists(uri, ctx)
        path = self._uri_to_path(uri, ctx=ctx)
        acquire = (
            self._async_agfs.pathlock_acquire_tree
            if is_dir
            else self._async_agfs.pathlock_acquire_exact
        )
        return await acquire(path)

    async def get_acl(self, uri: str, ctx: Optional[RequestContext] = None) -> Dict[str, Any]:
        real_ctx = await self._ensure_acl_manage(uri, ctx)
        await self._ensure_acl_target_exists(uri, real_ctx)
        effective = await self.acl_manager.resolve(uri, real_ctx)
        return self.acl_manager.to_report(uri, effective)

    async def prepare_acl_update(
        self,
        uri: str,
        acl: AclSpec | Mapping[str, Any],
        ctx: RequestContext,
    ) -> AclUpdate:
        """Authorize an explicit ACL against the permissions before the write."""
        spec = AclSpec.model_validate(acl)
        if len(acl_ancestors(uri)) == 1:
            raise InvalidArgumentError("ACL cannot be set on viking://resources")
        await self._ensure_acl_manage(uri, ctx)
        return AclUpdate(uri=uri, acl=spec)

    async def set_acl(
        self,
        uri: str,
        entries: Sequence[AclEntry | Mapping[str, Any]] | None = None,
        ctx: Optional[RequestContext] = None,
        *,
        acl_mode: AclMode | None = None,
    ) -> Dict[str, Any]:
        real_ctx = await self._ensure_acl_manage(uri, ctx)
        lease = await self._acquire_acl_target_lock(uri, real_ctx)
        try:
            await self._ensure_acl_manage(uri, real_ctx)
            await self._ensure_acl_target_exists(uri, real_ctx)
            effective = await self.acl_manager.set_acl(uri, entries, real_ctx, acl_mode=acl_mode)
            return self.acl_manager.to_report(uri, effective)
        finally:
            await self._async_agfs.pathlock_release(lease)

    async def grant_acl(
        self,
        uri: str,
        principal: str,
        level: AclLevel | str,
        ctx: Optional[RequestContext] = None,
    ) -> Dict[str, Any]:
        return await self._update_acl_entry(uri, principal, level, ctx)

    async def revoke_acl(
        self,
        uri: str,
        principal: str,
        ctx: Optional[RequestContext] = None,
    ) -> Dict[str, Any]:
        return await self._update_acl_entry(uri, principal, None, ctx)

    async def _update_acl_entry(
        self,
        uri: str,
        principal: str,
        level: Optional[AclLevel | str],
        ctx: Optional[RequestContext],
    ) -> Dict[str, Any]:
        principal = normalize_acl_principal(principal)
        normalized_level = normalize_acl_level(level) if level is not None else None
        real_ctx = await self._ensure_acl_manage(uri, ctx)
        lease = await self._acquire_acl_target_lock(uri, real_ctx)
        try:
            await self._ensure_acl_manage(uri, real_ctx)
            await self._ensure_acl_target_exists(uri, real_ctx)
            direct = await self.acl_manager.get_direct(uri, real_ctx)
            entries = {entry.principal: entry for entry in direct.entries}
            if normalized_level is None:
                entries.pop(principal, None)
            else:
                entries[principal] = AclEntry(principal, normalized_level)
            effective = await self.acl_manager.set_acl(uri, list(entries.values()), real_ctx)
            return self.acl_manager.to_report(uri, effective)
        finally:
            await self._async_agfs.pathlock_release(lease)

    async def delete_acl(self, uri: str, ctx: Optional[RequestContext] = None) -> Dict[str, Any]:
        return await self.set_acl(uri, [], acl_mode=AclMode.INHERIT, ctx=ctx)

    async def _ensure_identity_not_deleting(self, ctx: RequestContext) -> None:
        guard = getattr(self, "_deletion_guard", None)
        if ctx.role != Role.ROOT and guard is not None:
            result = guard(ctx.account_id, ctx.user.user_id)
            if await result if inspect.isawaitable(result) else result:
                raise FailedPreconditionError("Identity deletion is in progress")

    def _ensure_supported_delete_namespace(self, normalized_uri: str) -> None:
        parts = [p for p in normalized_uri[len("viking://") :].strip("/").split("/") if p]
        if not parts:
            raise PermissionDeniedError(
                "Deleting viking:// is not supported; use a concrete scope instead.",
                resource=normalized_uri,
            )
        if parts == ["user"]:
            raise PermissionDeniedError(
                "Deleting viking://user is not supported; use viking://~/... or an "
                "explicit viking://user/{user_id}/... path instead.",
                resource=normalized_uri,
            )
        if parts == ["agent"]:
            raise PermissionDeniedError(
                "Deleting viking://agent root is not supported; use a concrete "
                "agent sub-path (e.g. viking://agent/skills/...) instead.",
                resource=normalized_uri,
            )

    def _ensure_supported_write_namespace(self, normalized_uri: str) -> None:
        parts = [p for p in normalized_uri[len("viking://") :].strip("/").split("/") if p]
        if parts == ["user"]:
            raise PermissionDeniedError(
                "Writing viking://user is not supported; use viking://~/... or an "
                "explicit viking://user/{user_id}/... path instead.",
                resource=normalized_uri,
            )
        if parts and parts[0] == "session":
            raise PermissionDeniedError(
                f"Writing {normalized_uri} is not supported; use user-owned namespaces instead.",
                resource=normalized_uri,
            )

    def _pathlock_fs_ctx(
        self,
        ctx: Optional[RequestContext],
        lease_ref: Optional[Dict[str, Any] | str],
    ) -> Dict[str, str] | None:
        """Build an AGFS fs_ctx carrying account_id and an opaque pathlock lease_ref."""
        if lease_ref is None:
            return None
        if isinstance(lease_ref, str):
            ref = lease_ref
        elif isinstance(lease_ref, dict):
            ref = lease_ref.get("lease_ref")
        else:
            raise ValueError("lease_ref must be a non-empty string or lease dictionary")
        if not isinstance(ref, str) or not ref:
            raise ValueError("lease_ref must contain a non-empty lease_ref")
        return {"account_id": self._ctx_or_default(ctx).account_id, "lease_ref": ref}

    # ========== Tree Traversal (Refactored) ==========

    def _is_name_visible_at_path(self, name: str, parent_path: str) -> bool:
        """Check if name would appear in _ls_entries(parent_path).

        At account root (/local/{account}), uses LISTABLE_SCOPES whitelist.
        At other levels, uses the shared storage internal-name blacklist.
        """
        parts = [p for p in parent_path.strip("/").split("/") if p]
        if len(parts) == 2 and parts[0] == "local":
            return name in VikingURI.LISTABLE_SCOPES
        return not is_storage_internal_name(name)

    def _ancestor_is_filtered(self, entry_path: str, base_path: str) -> bool:
        """Check if any ancestor directory of entry_path would be filtered by _ls_entries.

        Walks from base_path (exclusive) to entry's parent directory (exclusive),
        checking each component against _is_name_visible_at_path.
        """
        base_parts = [p for p in base_path.strip("/").split("/") if p]
        entry_parts = [p for p in entry_path.strip("/").split("/") if p]

        for i in range(len(base_parts), len(entry_parts) - 1):
            name = entry_parts[i]
            parent_parts = entry_parts[:i]
            parent_path = "/" + "/".join(parent_parts) if parent_parts else "/"
            if not self._is_name_visible_at_path(name, parent_path):
                return True
        return False

    def _is_path_entry_visible(
        self,
        entry_path: str,
        name: str,
        base_path: str,
        ctx: RequestContext,
        *,
        acl_enabled: bool,
    ) -> bool:
        """Check visibility for one flattened path entry returned by Rust."""
        if self._ancestor_is_filtered(entry_path, base_path):
            return False

        entry_parts = [p for p in entry_path.strip("/").split("/") if p]
        if entry_parts:
            parent_parts = entry_parts[:-1]
            parent_path = "/" + "/".join(parent_parts) if parent_parts else "/"
            if not self._is_name_visible_at_path(name, parent_path):
                return False

        if not acl_enabled:
            uri = self._path_to_uri(entry_path, ctx=ctx)
            if not self._is_accessible(uri, ctx):
                return False

        return True

    def _is_tree_entry_visible(
        self,
        entry: Dict[str, Any],
        base_path: str,
        ctx: RequestContext,
        *,
        acl_enabled: bool,
    ) -> bool:
        """Check visibility for a single TreeEntry returned by Rust tree_directory."""
        entry_path = entry["path"]
        entry_info = entry.get("info", {})
        name = entry_info.get("name") or entry_path.rstrip("/").rsplit("/", 1)[-1]
        return self._is_path_entry_visible(
            entry_path,
            name,
            base_path,
            ctx,
            acl_enabled=acl_enabled,
        )

    def _glob_page_size(self, node_limit: Optional[int]) -> int:
        """Return the backend page size used by glob_directory."""
        if node_limit is None or node_limit <= 0:
            return self._GLOB_PAGE_SIZE_DEFAULT
        return node_limit

    async def _iter_visible_tree_entries(
        self,
        uri: str,
        show_all_hidden: bool = False,
        node_limit: Optional[int] = None,
        level_limit: Optional[int] = None,
        offset: int = 0,
        sort_by: Optional[str] = None,
        sort_order: str = "asc",
        ctx: Optional[RequestContext] = None,
        directories_only: bool = False,
    ):
        """Yield one visible tree page after namespace and ACL filtering."""
        real_ctx = self._ctx_or_default(ctx)
        path = self._uri_to_path(uri, ctx=ctx)
        if not await self._agfs_path_exists(path):
            raise NotFoundError(uri, "directory")

        if node_limit == 0:
            return
        raw_offset = 0
        raw_limit = None if node_limit is None else max(node_limit, 256)
        remaining_offset = offset
        yielded = 0
        acl_enabled = await self._acl_enabled(real_ctx)
        expose_resource_names = acl_enabled and is_acl_uri(uri)
        denied_directories: set[str] = set()

        while True:
            raw_entries = await self._async_agfs.tree_directory(
                path,
                show_hidden=show_all_hidden,
                node_limit=raw_limit,
                level_limit=level_limit,
                offset=raw_offset,
                sort_by=sort_by,
                sort_order=sort_order,
                directories_only=directories_only,
            )
            if not raw_entries:
                return

            candidates: List[tuple] = []
            for entry in raw_entries:
                if not self._is_tree_entry_visible(
                    entry,
                    path,
                    real_ctx,
                    acl_enabled=acl_enabled,
                ):
                    continue
                entry_uri = self._alias_uri_for_path(
                    request_uri=uri,
                    base_path=path,
                    entry_path=entry["path"],
                    ctx=ctx,
                )
                candidates.append((entry, entry_uri))
                remaining_limit = None if node_limit is None else node_limit - yielded
                if (
                    not acl_enabled
                    and remaining_limit is not None
                    and len(candidates) >= (remaining_offset + remaining_limit)
                ):
                    break

            if not acl_enabled:
                visible = candidates
            else:
                access = await self._can_access_many(
                    [entry_uri for _, entry_uri in candidates], real_ctx
                )
                if expose_resource_names:
                    denied_directories.update(
                        {
                            entry["path"].rstrip("/")
                            for entry, entry_uri in candidates
                            if entry.get("info", {}).get("isDir", False)
                            and not access.get(entry_uri, False)
                        }
                    )
                    visible = []
                    base = path.rstrip("/")
                    for entry, entry_uri in candidates:
                        parent = entry["path"].rstrip("/").rsplit("/", 1)[0]
                        blocked = False
                        while parent.startswith(f"{base}/"):
                            if parent in denied_directories:
                                blocked = True
                                break
                            parent = parent.rsplit("/", 1)[0]
                        if blocked:
                            continue
                        if access.get(entry_uri, False):
                            visible.append((entry, entry_uri))
                        else:
                            denied_entry = dict(entry)
                            denied_entry["access"] = "denied"
                            visible.append((denied_entry, entry_uri))
                else:
                    visible = [item for item in candidates if access.get(item[1], False)]

            for item in visible:
                if remaining_offset:
                    remaining_offset -= 1
                    continue
                yield item
                yielded += 1
                if node_limit is not None and yielded >= node_limit:
                    return

            if raw_limit is None or len(raw_entries) < raw_limit:
                return
            raw_offset += len(raw_entries)

    # ========== URI Conversion ==========

    @staticmethod
    def _shorten_component(component: str, max_bytes: int = 255) -> str:
        """Shorten a path component if its UTF-8 encoding exceeds max_bytes."""
        if len(component.encode("utf-8")) <= max_bytes:
            return component
        hash_suffix = hashlib.sha256(component.encode("utf-8")).hexdigest()[:8]
        # Trim to fit within max_bytes after adding hash suffix
        prefix = component
        target = max_bytes - len(f"_{hash_suffix}".encode("utf-8"))
        while len(prefix.encode("utf-8")) > target and prefix:
            prefix = prefix[:-1]
        return f"{prefix}_{hash_suffix}"

    def _uri_to_path(self, uri: str, ctx: Optional[RequestContext] = None) -> str:
        """Map virtual URI to account-isolated AGFS path.

        Pure prefix replacement: viking://{remainder} -> /local/{account_id}/{remainder}.
        No implicit space injection — URIs must include space segments explicitly.
        """
        real_ctx = self._ctx_or_default(ctx)
        account_id = real_ctx.account_id
        parts = self._safe_uri_parts(uri)
        if parts[:1] == ["session"]:
            raise ValueError(f"Legacy session URI is not accepted internally: {uri}")
        if not parts:
            return f"/local/{account_id}"

        safe_parts = [self._shorten_component(p, self._MAX_FILENAME_BYTES) for p in parts]
        return f"/local/{account_id}/{'/'.join(safe_parts)}"

    def _alias_uri_for_path(
        self,
        *,
        request_uri: str,
        base_path: str,
        entry_path: str,
        ctx: Optional[RequestContext],
    ) -> str:
        base = base_path.rstrip("/")
        request_root = request_uri if request_uri == "viking://" else request_uri.rstrip("/")
        preserve_request_alias = request_uri in {"viking://", "viking://user"}
        rel_path = entry_path[len(base) :].strip("/") if entry_path.startswith(base) else ""
        if entry_path.startswith(base):
            separator = "" if request_root.endswith("://") else "/"
            candidate_uri = request_root if not rel_path else f"{request_root}{separator}{rel_path}"
            if preserve_request_alias:
                return candidate_uri
            try:
                if self._uri_to_path(candidate_uri, ctx=ctx) == entry_path:
                    return candidate_uri
            except Exception:
                pass
        return self._path_to_uri(entry_path, ctx=ctx)

    async def _agfs_path_exists(self, path: str) -> bool:
        try:
            await self._async_agfs.stat(path)
            return True
        except Exception as exc:
            if is_not_found_error(exc):
                return False
            raise

    async def _list_read_path_items(
        self,
        uri: str,
        raw_offset: int = 0,
        raw_limit: Optional[int] = None,
        sort_by: Optional[str] = None,
        sort_order: str = "asc",
        ctx: Optional[RequestContext] = None,
    ) -> tuple[List[tuple[Dict[str, Any], str]], int, bool]:
        """Return one mapped RagFS page, consumed count, and exhaustion state."""
        path = self._uri_to_path(uri, ctx=ctx)
        try:
            raw_entries = await self._ls_entries(
                path,
                offset=raw_offset,
                limit=raw_limit,
                sort_by=sort_by,
                sort_order=sort_order,
                filter_internal=False,
                ctx=ctx,
            )
        except Exception as exc:
            if is_not_found_error(exc):
                raise NotFoundError(uri, "directory") from exc
            raise

        entries = self._filter_ls_entries(path, raw_entries)
        items = [
            (
                entry,
                self._alias_uri_for_path(
                    request_uri=uri,
                    base_path=path,
                    entry_path=f"{path.rstrip('/')}/{entry.get('name', '')}",
                    ctx=ctx,
                ),
            )
            for entry in entries
        ]
        exhausted = raw_limit is None or len(raw_entries) < raw_limit
        return items, len(raw_entries), exhausted

    def _path_to_uri(self, path: str, ctx: Optional[RequestContext] = None) -> str:
        """/local/{account}/... -> viking://...

        Pure prefix replacement: strips /local/{account_id}/ and prepends viking://.
        No implicit space stripping.
        """
        if path.startswith("/local/"):
            inner = path[7:].strip("/")
            if not inner:
                return "viking://"
            real_ctx = self._ctx_or_default(ctx)
            parts = [p for p in inner.split("/") if p]
            if parts and parts[0] == real_ctx.account_id:
                parts = parts[1:]
            if not parts:
                return "viking://"
            return f"viking://{'/'.join(parts)}"
        raise ValueError(f"AGFS path must start with '/local/': {path!r}")

    def _looks_like_legacy_temp_leaf(self, value: str) -> bool:
        return bool(re.match(r"^\d{8}_[0-9a-f]{6}$", value or ""))

    def _is_legacy_temp_uri_parts(self, parts: List[str]) -> bool:
        if len(parts) < 2 or parts[0] != "temp" or not self._looks_like_legacy_temp_leaf(parts[1]):
            return False
        if len(parts) == 2:
            return True
        return not self._looks_like_legacy_temp_leaf(parts[2])

    def _is_accessible(self, uri: str, ctx: RequestContext) -> bool:
        """Check whether a URI is visible/accessible under current request context."""
        parts = self._safe_uri_parts(uri)
        if ctx.role == Role.ROOT:
            return True
        if is_hidden_by_actor_peer_view(uri, ctx):
            return False
        if not parts:
            return True
        if is_watch_task_control_uri(uri):
            return False

        scope = parts[0]
        if scope == "resources":
            return True
        if scope == "temp":
            if len(parts) == 1:
                return True
            if parts[1] == ctx.user.user_space_name():
                return True
            return self._is_legacy_temp_uri_parts(parts)
        if scope == "upload":
            return ctx.role == Role.ROOT
        if scope == "_system":
            return False
        return namespace_is_accessible(uri, ctx)

    def _handle_agfs_read(self, result: Union[bytes, Any, None]) -> bytes:
        """Handle AGFSClient read return types consistently."""
        if isinstance(result, bytes):
            return result
        elif result is None:
            return b""
        elif hasattr(result, "content") and result.content is not None:
            return result.content
        else:
            # Try to convert to bytes
            try:
                return str(result).encode("utf-8")
            except Exception:
                return b""

    def _decode_bytes(self, data: bytes) -> str:
        """Robustly decode bytes to string."""
        if not data:
            return ""
        try:
            return data.decode("utf-8")
        except UnicodeDecodeError:
            try:
                # Try common encoding for Windows/legacy files in China
                return data.decode("gbk")
            except UnicodeDecodeError:
                try:
                    return data.decode("latin-1")
                except UnicodeDecodeError:
                    return data.decode("utf-8", errors="replace")

    async def _is_path_locked_async(self, path: str) -> bool:
        """Best-effort async path-lock lookup; returns False on any error."""
        try:
            return await self._async_agfs.pathlock_is_locked(path)
        except Exception:
            return False

    def _gitignore_agfs_path(self, ctx: Optional[RequestContext] = None) -> str:
        real_ctx = self._ctx_or_default(ctx)
        return f"/local/{real_ctx.account_id}/{self._OVGITIGNORE_TREE_PATH}"

    def _uri_to_tree_path(self, uri: str, ctx: Optional[RequestContext] = None) -> str:
        """Convert a viking:// URI to an account-relative git tree path.

        ``viking://resources/proj_a/docs/a.md`` -> ``resources/proj_a/docs/a.md``.

        Pure prefix stripping: removes the ``viking://`` scheme and any
        ``/local/{account}/`` segment. Internal scopes that the Rust git layer
        would prune (`_system`, `tasks`, `temp`, `queue`, `upload`) and the
        runtime lock name (`.path.ovlock`) are rejected with ``ValueError``
        — passing them through silently would result in a no-op commit and
        confuse callers.
        """
        parts = self._safe_uri_parts(uri)
        if not parts:
            raise ValueError(f"git tree path cannot be the account root: {uri!r}")
        first = parts[0]
        if first == "session":
            raise ValueError(f"Legacy session URI is not accepted internally: {uri}")
        if first in self._GIT_INTERNAL_FIRST_SEGMENTS:
            raise ValueError(f"git tree path rejects internal scope/segment {first!r}: {uri!r}")
        return "/".join(parts)

    def _tree_path_to_uri(self, tree_path: str) -> str:
        """Convert an account-relative git tree path to a viking:// URI.

        Inverse of :py:meth:`_uri_to_tree_path`.
        """
        cleaned = tree_path.strip("/")
        if not cleaned:
            raise ValueError("tree path must not be empty")
        return f"viking://{cleaned}"

    def _classify_restore_path(self, tree_path: str, *, deleted: bool) -> Optional[tuple]:
        """Classify a restore-affected tree path into a vector maintenance task.

        Returns a ``(op, uri, level)`` triple, or ``None`` when the path has no
        vector side-effect:

        - ``dir/.abstract.md`` / ``dir/.overview.md`` → recompute (write) or
          delete (removal) ONLY that directory's L0/L1 vector:
          ``("reindex_marker"|"delete", dir_uri, ABSTRACT|OVERVIEW)``.
        - ``.relations.json`` → ``None`` (not a vector text source).
        - anything else (a source file) → reindex (write) or delete (removal)
          its DETAIL vector:
          ``("reindex_file", file_uri, DETAIL)`` / ``("delete", file_uri, DETAIL)``.

        ``None`` is also returned for a directory marker at the account root
        (no parent directory to scope an L0/L1 vector to).
        """
        parent, _, name = tree_path.rpartition("/")
        if name in self._NO_VECTOR_DERIVED:
            return None
        level = self._DIR_MARKER_LEVELS.get(name)
        if level is not None:
            if not parent:
                return None
            dir_uri = self._tree_path_to_uri(parent)
            op = "delete" if deleted else "reindex_marker"
            return (op, dir_uri, level)
        # Source file.
        file_uri = self._tree_path_to_uri(tree_path)
        op = "delete" if deleted else "reindex_file"
        return (op, file_uri, ContextLevel.DETAIL)
