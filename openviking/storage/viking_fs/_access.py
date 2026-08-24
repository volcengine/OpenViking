# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Access control, URI/path conversion, and visibility mixin for VikingFS."""

import hashlib
import json
import re
from contextlib import contextmanager
from typing import Any, Callable, Dict, List, Optional, Union

from openviking.core.context import ContextLevel
from openviking.core.namespace import (
    is_accessible as namespace_is_accessible,
)
from openviking.core.namespace import (
    is_hidden_by_actor_peer_view,
    may_include_hidden_actor_peers,
)
from openviking.resource.watch_storage import is_watch_task_control_uri
from openviking.server.error_mapping import is_not_found_error
from openviking.server.identity import RequestContext, Role
from openviking.storage.internal_names import STORAGE_INTERNAL_ENTRY_NAMES
from openviking_cli.exceptions import (
    FailedPreconditionError,
    NotFoundError,
    PermissionDeniedError,
)
from openviking_cli.session.user_id import UserIdentifier
from openviking_cli.utils.uri import VikingURI


class _AccessMixin:
    """URI normalization, access control, path conversion, and visibility helpers."""

    # Over-fetch multiplier for bounded tree traversal. When a node_limit is
    # set, we push down node_limit * this factor as the raw-node limit to Rust,
    # leaving headroom for ACL/internal-name filtering before re-fetching.
    _TREE_OVERFETCH_FACTOR = 4
    _GLOB_PAGE_SIZE_DEFAULT = 1024

    # Maximum bytes for a single filename component (filesystem limit is typically 255)
    _MAX_FILENAME_BYTES = 255

    _ROOT_PATH = "/local"

    # First path segments that the Rust git enumerate.rs prunes from snapshots,
    # plus the runtime lock name. Mirrors INTERNAL_FIRST_SEGMENTS in
    # crates/ragfs/src/git/enumerate.rs and VikingFS._INTERNAL_NAMES so that
    # callers fail fast in Python with a clear error rather than passing a
    # path that the Rust side will silently drop.
    _GIT_INTERNAL_FIRST_SEGMENTS = frozenset({
        "_system",
        "tasks",
        "temp",
        "queue",
        "upload",
        ".path.ovlock",
    })

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

    def set_user_deletion_guard(self, guard: Optional[Callable[[str, str], bool]]) -> None:
        self._user_deletion_guard = guard

    @staticmethod
    def _default_ctx() -> RequestContext:
        return RequestContext(user=UserIdentifier.the_default_user(), role=Role.ROOT)

    def _ctx_or_default(self, ctx: Optional[RequestContext]) -> RequestContext:
        if ctx is not None:
            return ctx
        bound = self._bound_ctx.get()
        return bound or self._default_ctx()

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

    def _ensure_access(self, uri: str, ctx: Optional[RequestContext]) -> None:
        real_ctx = self._ctx_or_default(ctx)
        if not self._is_accessible(uri, real_ctx):
            raise PermissionDeniedError(f"Access denied for {uri}", resource=uri)

    def _ensure_mutable_access(self, uri: str, ctx: Optional[RequestContext]) -> None:
        self._ensure_access(uri, ctx)
        real_ctx = self._ctx_or_default(ctx)
        self._ensure_user_not_deleting(real_ctx)
        if is_hidden_by_actor_peer_view(uri, real_ctx) or may_include_hidden_actor_peers(uri, real_ctx):
            raise PermissionDeniedError(f"Access denied for {uri}", resource=uri)
        self._ensure_supported_write_namespace(uri)
        if real_ctx.role != Role.ROOT and uri.rstrip("/") == "viking://temp":
            raise PermissionDeniedError(
                "Temp root is read-only for non-root users",
                resource=uri,
            )

    def _ensure_delete_access(self, uri: str, ctx: Optional[RequestContext]) -> None:
        self._ensure_access(uri, ctx)
        real_ctx = self._ctx_or_default(ctx)
        self._ensure_user_not_deleting(real_ctx)
        if is_hidden_by_actor_peer_view(uri, real_ctx) or may_include_hidden_actor_peers(uri, real_ctx):
            raise PermissionDeniedError(f"Access denied for {uri}", resource=uri)
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
        if real_ctx.role != Role.ROOT and uri.rstrip("/") == "viking://temp":
            raise PermissionDeniedError(
                "Temp root is read-only for non-root users",
                resource=uri,
            )

    def _ensure_user_not_deleting(self, ctx: RequestContext) -> None:
        guard = getattr(self, "_user_deletion_guard", None)
        if (
            ctx.role != Role.ROOT
            and guard is not None
            and guard(ctx.account_id, ctx.user.user_id)
        ):
            raise FailedPreconditionError("User deletion is in progress")

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
            # Parity with _ensure_supported_write_namespace, which forbids the
            # account-shared viking://agent root: deleting it would recursively
            # wipe every account's agent skills/endpoints/tools/payments. Concrete
            # sub-paths (viking://agent/skills/...) remain deletable.
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
        if parts and parts[0] == "agent":
            if len(parts) >= 2 and parts[1] not in {"skills", "endpoints", "tools", "payments"}:
                raise PermissionDeniedError(
                    "viking://agent/{agent_id} is deprecated. Use viking://user/.../peers/{agent_id} instead.",
                    resource=normalized_uri,
                )
            if len(parts) < 2:
                raise PermissionDeniedError(
                    "Writing to viking://agent root is not supported.",
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
        return name not in STORAGE_INTERNAL_ENTRY_NAMES

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
        self, entry_path: str, name: str, base_path: str, ctx: RequestContext
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

        uri = self._path_to_uri(entry_path, ctx=ctx)
        if not self._is_accessible(uri, ctx):
            return False

        return True

    def _is_tree_entry_visible(
        self, entry: Dict[str, Any], base_path: str, ctx: RequestContext
    ) -> bool:
        """Check visibility for a single TreeEntry returned by Rust tree_directory."""
        entry_path = entry["path"]
        entry_info = entry.get("info", {})
        name = entry_info.get("name") or entry_path.rstrip("/").rsplit("/", 1)[-1]
        return self._is_path_entry_visible(entry_path, name, base_path, ctx)

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
        ctx: Optional[RequestContext] = None,
    ):
        """Shared generator: fetch raw TreeEntry list from Rust, yield (entry, uri) tuples.

        node_limit counts ACL-visible entries (see design §6.5), so the user's
        node_limit cannot be pushed directly to Rust — doing so would truncate
        before filtering and drop entries that should be visible.

        To keep memory bounded without changing that semantic, we push down an
        *amplified* raw-node limit (node_limit * _TREE_OVERFETCH_FACTOR). If ACL
        filtering leaves fewer than node_limit visible entries while Rust still
        returned a full page (i.e. more raw nodes may exist), we double the raw
        limit and re-fetch. Because Rust truncates a deterministic sorted prefix,
        this yields exactly the same result as an unbounded fetch, while avoiding
        materializing the entire prefix in the common case.

        When node_limit is None (full-tree callers), no limit is pushed down.
        level_limit IS always passed to Rust.
        """
        real_ctx = self._ctx_or_default(ctx)
        primary_path = self._uri_to_path(uri, ctx=ctx)
        path: Optional[str] = None
        for candidate_path in self._read_paths(uri, ctx=ctx):
            if not await self._read_path_visible(uri, candidate_path, primary_path, real_ctx):
                continue
            if await self._agfs_path_exists(candidate_path):
                path = candidate_path
                break
        if path is None:
            if self._is_session_root_uri(uri):
                return
            raise NotFoundError(uri, "directory")

        if node_limit is None:
            raw_limit: Optional[int] = None
        else:
            raw_limit = max(node_limit * self._TREE_OVERFETCH_FACTOR, node_limit)

        while True:
            raw_entries = await self._async_agfs.tree_directory(
                path,
                show_hidden=show_all_hidden,
                node_limit=raw_limit,
                level_limit=level_limit,
            )

            visible: List[tuple] = []
            for entry in raw_entries:
                if node_limit is not None and len(visible) >= node_limit:
                    break
                if not self._is_tree_entry_visible(entry, path, real_ctx):
                    continue
                if not await self._read_path_visible(uri, entry["path"], primary_path, real_ctx):
                    continue
                entry_uri = self._alias_uri_for_path(
                    request_uri=uri,
                    base_path=path,
                    entry_path=entry["path"],
                    ctx=ctx,
                )
                visible.append((entry, entry_uri))

            # If we still lack enough visible entries but Rust returned a full
            # page (raw_limit reached), more raw nodes may exist — re-fetch with
            # a doubled limit. Otherwise Rust is exhausted and we yield as-is.
            need_more = (
                node_limit is not None
                and len(visible) < node_limit
                and raw_limit is not None
                and len(raw_entries) >= raw_limit
            )
            if need_more:
                raw_limit *= 2
                continue

            for item in visible:
                yield item
            return

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
        if parts and parts[0] == "agent" and self._is_legacy_agent_id_uri(uri):
            # Old format: viking://agent/{agent_id}/... — direct mapping for read-only compat
            safe_parts = [
                self._shorten_component(p, self._MAX_FILENAME_BYTES) for p in parts
            ]
            return f"/local/{account_id}/{'/'.join(safe_parts)}"
        if not parts:
            return f"/local/{account_id}"

        safe_parts = [self._shorten_component(p, self._MAX_FILENAME_BYTES) for p in parts]
        return f"/local/{account_id}/{'/'.join(safe_parts)}"

    def _legacy_session_path(self, uri: str, ctx: Optional[RequestContext] = None) -> str:
        """Map a legacy viking://session URI to its pre-user-namespace path."""
        real_ctx = self._ctx_or_default(ctx)
        parts = self._safe_uri_parts(uri)
        safe_parts = [self._shorten_component(p, self._MAX_FILENAME_BYTES) for p in parts]
        return f"/local/{real_ctx.account_id}/{'/'.join(safe_parts)}"

    def _legacy_user_session_path(
        self, uri: str, ctx: Optional[RequestContext] = None
    ) -> Optional[str]:
        """Return the legacy nested /session/{user_id}/{session_id} candidate."""
        real_ctx = self._ctx_or_default(ctx)
        parts = self._safe_uri_parts(uri)
        if len(parts) <= 3 or parts[0] != "user" or parts[2] != "sessions":
            return None
        nested_parts = ["session", parts[1], *parts[3:]]
        safe_parts = [self._shorten_component(p, self._MAX_FILENAME_BYTES) for p in nested_parts]
        return f"/local/{real_ctx.account_id}/{'/'.join(safe_parts)}"

    def _legacy_session_alias(self, uri: str) -> Optional[str]:
        """Return the old storage alias for a canonical user session URI."""
        parts = self._safe_uri_parts(uri)
        if len(parts) < 3 or parts[0] != "user" or parts[2] != "sessions":
            return None
        suffix = parts[3:]
        return "viking://session" + (f"/{'/'.join(suffix)}" if suffix else "")

    def _is_session_root_uri(self, uri: str) -> bool:
        return self._legacy_session_alias(uri) == "viking://session"

    def _is_legacy_agent_id_uri(self, uri: str) -> bool:
        parts = self._safe_uri_parts(uri)
        return bool(
            parts
            and parts[0] == "agent"
            and len(parts) >= 2
            and parts[1] not in {"skills", "endpoints", "tools", "payments"}
        )

    def _read_paths(self, uri: str, ctx: Optional[RequestContext] = None) -> List[str]:
        """Return read candidates for a URI, including legacy alias fallbacks."""
        paths = [self._uri_to_path(uri, ctx=ctx)]

        legacy_uri = self._legacy_session_alias(uri)
        if legacy_uri:
            for candidate in (
                self._legacy_session_path(legacy_uri, ctx=ctx),
                self._legacy_user_session_path(uri, ctx=ctx),
            ):
                if candidate and candidate not in paths:
                    paths.append(candidate)
        return paths

    async def _read_path_visible(
        self,
        request_uri: str,
        path: str,
        primary_path: str,
        ctx: RequestContext,
    ) -> bool:
        if path == primary_path:
            return True
        if self._legacy_session_alias(request_uri):
            owner_user_id = self._safe_uri_parts(request_uri)[1]
            return await self._legacy_session_path_visible(
                path, owner_user_id=owner_user_id
            )
        return True

    def _alias_uri_for_path(
        self,
        *,
        request_uri: str,
        base_path: str,
        entry_path: str,
        ctx: Optional[RequestContext],
    ) -> str:
        base = base_path.rstrip("/")
        request_parts = self._safe_uri_parts(request_uri)
        request_root = (
            request_uri if request_uri == "viking://" else request_uri.rstrip("/")
        )
        preserve_request_alias = request_uri in {"viking://", "viking://user"} or bool(
            request_parts
            and request_parts[0] == "agent"
            and self._is_legacy_agent_id_uri(request_uri)
        )
        rel_path = entry_path[len(base) :].strip("/") if entry_path.startswith(base) else ""
        if entry_path.startswith(base):
            separator = "" if request_root.endswith("://") else "/"
            candidate_uri = request_root if not rel_path else f"{request_root}{separator}{rel_path}"
            if self._legacy_session_alias(request_uri):
                return candidate_uri
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

    async def _looks_like_legacy_session_dir(self, path: str) -> bool:
        for leaf in (".meta.json", "messages.jsonl", "history", "tool-results", "tools"):
            if await self._agfs_path_exists(f"{path}/{leaf}"):
                return True
        return False

    async def _legacy_session_owner(self, session_root_path: str) -> str:
        try:
            raw = self._handle_agfs_read(
                await self._async_agfs.read(f"{session_root_path}/.meta.json")
            )
        except Exception as exc:
            if is_not_found_error(exc):
                return ""
            raise
        try:
            data = json.loads(self._decode_bytes(raw))
        except json.JSONDecodeError:
            return ""
        if not isinstance(data, dict):
            return ""
        for key in ("created_by_user_id", "user_id", "owner_user_id", "created_by"):
            value = data.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        return ""

    async def _legacy_session_visible(
        self,
        session_root_path: str,
        ctx: RequestContext,
        *,
        owner_hint: Optional[str] = None,
    ) -> bool:
        if ctx.role == Role.ROOT:
            return True
        owner = await self._legacy_session_owner(session_root_path)
        if owner:
            return owner == ctx.user.user_id
        if owner_hint:
            return owner_hint == ctx.user.user_id
        return True

    async def _legacy_session_path_visible(
        self,
        path: str,
        *,
        owner_user_id: str,
    ) -> bool:
        parts = [p for p in path.strip("/").split("/") if p]
        try:
            session_index = parts.index("session")
        except ValueError:
            return True
        suffix = parts[session_index + 1 :]
        if not suffix:
            return True

        root_prefix = "/" + "/".join(parts[: session_index + 1])
        direct_root = f"{root_prefix}/{suffix[0]}"
        if await self._looks_like_legacy_session_dir(direct_root):
            owner = await self._legacy_session_owner(direct_root)
            return bool(owner) and owner == owner_user_id

        if len(suffix) >= 2:
            if suffix[0] != owner_user_id:
                return False
            nested_root = f"{root_prefix}/{suffix[0]}/{suffix[1]}"
            if await self._looks_like_legacy_session_dir(nested_root):
                owner = await self._legacy_session_owner(nested_root)
                return not owner or owner == owner_user_id
        return True

    async def _legacy_session_root_items(
        self,
        path: str,
        ctx: RequestContext,
        output_root_uri: str,
        owner_user_id: str,
    ) -> List[tuple[Dict[str, Any], str]]:
        try:
            entries = await self._ls_entries(path)
        except Exception as exc:
            if is_not_found_error(exc):
                return []
            raise

        items: List[tuple[Dict[str, Any], str]] = []
        for entry in entries:
            name = entry.get("name", "")
            if not name or name in {".", ".."} or not entry.get("isDir"):
                continue
            child_path = f"{path.rstrip('/')}/{name}"
            if await self._looks_like_legacy_session_dir(child_path):
                legacy_owner = await self._legacy_session_owner(child_path)
                if legacy_owner == owner_user_id:
                    items.append((entry, f"{output_root_uri}/{name}"))
                continue

            if name != owner_user_id:
                continue
            try:
                nested_entries = await self._ls_entries(child_path)
            except Exception as exc:
                if is_not_found_error(exc):
                    continue
                raise
            for nested in nested_entries:
                nested_name = nested.get("name", "")
                if not nested_name or nested_name in {".", ".."} or not nested.get("isDir"):
                    continue
                nested_path = f"{child_path.rstrip('/')}/{nested_name}"
                if not await self._looks_like_legacy_session_dir(nested_path):
                    continue
                if await self._legacy_session_visible(nested_path, ctx, owner_hint=name):
                    items.append((nested, f"{output_root_uri}/{nested_name}"))
        return items

    async def _session_root_items(
        self,
        uri: str,
        ctx: RequestContext,
    ) -> List[tuple[Dict[str, Any], str]]:
        primary_path = self._uri_to_path(uri, ctx=ctx)
        output_root_uri = uri.rstrip("/")
        owner_user_id = self._safe_uri_parts(uri)[1]
        by_name: Dict[str, tuple[Dict[str, Any], str]] = {}
        try:
            for entry in await self._ls_entries(primary_path, ctx=ctx):
                name = entry.get("name", "")
                if not name or name in {".", ".."}:
                    continue
                by_name[name] = (entry, f"{output_root_uri}/{name}")
        except Exception as exc:
            if not is_not_found_error(exc):
                raise

        legacy_path = self._legacy_session_path("viking://session", ctx=ctx)
        for entry, entry_uri in await self._legacy_session_root_items(
            legacy_path, ctx, output_root_uri, owner_user_id
        ):
            name = entry.get("name", "")
            if name and name not in by_name:
                by_name[name] = (entry, entry_uri)
        return list(by_name.values())

    async def _list_read_path_items(
        self,
        uri: str,
        ctx: Optional[RequestContext] = None,
    ) -> List[tuple[Dict[str, Any], str]]:
        real_ctx = self._ctx_or_default(ctx)
        if self._is_session_root_uri(uri):
            return await self._session_root_items(uri, real_ctx)

        primary_path = self._uri_to_path(uri, ctx=ctx)
        merge_paths = self._legacy_session_alias(uri) is not None
        found_path = False
        last_not_found: Optional[Exception] = None
        by_uri: Dict[str, tuple[Dict[str, Any], str]] = {}

        for path in self._read_paths(uri, ctx=ctx):
            if not await self._read_path_visible(uri, path, primary_path, real_ctx):
                continue
            try:
                entries = await self._ls_entries(path, ctx=ctx)
            except Exception as exc:
                if is_not_found_error(exc):
                    last_not_found = exc
                    continue
                raise

            found_path = True
            for entry in entries:
                entry_uri = self._alias_uri_for_path(
                    request_uri=uri,
                    base_path=path,
                    entry_path=f"{path.rstrip('/')}/{entry.get('name', '')}",
                    ctx=ctx,
                )
                by_uri.setdefault(entry_uri, (entry, entry_uri))
            if not merge_paths:
                break

        if found_path:
            return list(by_uri.values())
        raise NotFoundError(uri, "directory") from last_not_found

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
        if scope == "agent":
            # New format: agent/skills/..., agent/endpoints/... — globally readable (account scope)
            if len(parts) >= 2 and parts[1] in {"skills", "endpoints", "tools", "payments"}:
                return True
            # Old format: agent/{agent_id}/... — actor_peer_id match for read-only access
            if not ctx.actor_peer_id or len(parts) < 2:
                return True
            return parts[1] == ctx.actor_peer_id
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
