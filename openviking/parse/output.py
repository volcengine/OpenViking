# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Parse output store abstraction.

Parsers write their intermediate artifacts (the temp tree that TreeBuilder later
persists into the final resource location) through a small store interface rather
than reaching for the global VikingFS singleton directly. This decouples "which
backend holds the artifact" from "how a parser lays out its output".

The AGFS implementation preserves the original shared-temp behavior, while the
local implementation keeps parser artifacts on a configured shared local path.

Design notes:
- Business rules (filtering, encoding, flatten, name conflicts, sidecar
  retention) live in the parsers / shared helpers, NOT in the store. A store
  only moves bytes and directory entries for a single artifact.
- :class:`ParseArtifactRef` is the serializable handle that crosses the queue in
  place of a bare ``viking://temp/...`` string; runtime store objects never do.
- Relative paths inside an artifact are validated with
  :func:`sanitize_relative_viking_path` so a parser cannot escape its root.
"""

from __future__ import annotations

import asyncio
import json
import shutil
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional

from openviking.utils.content_hash import content_md5
from openviking.utils.path_safety import safe_join_viking_uri, sanitize_relative_viking_path

_BACKENDS = frozenset({"agfs", "local"})
_ROOT_TYPES = frozenset({"dir", "file"})

# Sidecar at the artifact root mapping each business file's artifact-relative
# path to the md5 of its final (encoding-normalized) bytes. The incremental diff
# reads this so it can compare fingerprints without re-reading file contents.
ARTIFACT_MANIFEST_NAME = ".artifact_manifest.json"


@dataclass(frozen=True)
class ParseArtifactRef:
    """Serializable handle to one parse artifact.

    ``root`` is backend-scoped (an AGFS ``viking://temp/<uuid>`` URI, or a local
    absolute directory). ``resource_rel`` is the artifact-relative path of the
    resource root inside ``root`` (empty when the root is the resource itself).
    """

    backend: str
    root: str
    resource_rel: str = ""
    root_type: str = "dir"

    def to_dict(self) -> dict[str, Any]:
        return {
            "backend": self.backend,
            "root": self.root,
            "resource_rel": self.resource_rel,
            "root_type": self.root_type,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ParseArtifactRef":
        if not isinstance(data, dict):
            raise ValueError("parse artifact ref must be an object")
        backend = data.get("backend")
        root = data.get("root")
        resource_rel = data.get("resource_rel") or ""
        root_type = data.get("root_type") or "dir"
        if backend not in _BACKENDS:
            raise ValueError(f"parse artifact ref backend must be one of {sorted(_BACKENDS)}")
        if not isinstance(root, str) or not root:
            raise ValueError("parse artifact ref root must be a non-empty string")
        if not isinstance(resource_rel, str):
            raise ValueError("parse artifact ref resource_rel must be a string")
        if root_type not in _ROOT_TYPES:
            raise ValueError(f"parse artifact ref root_type must be one of {sorted(_ROOT_TYPES)}")
        return cls(
            backend=backend,
            root=root,
            resource_rel=resource_rel,
            root_type=root_type,
        )


@dataclass(frozen=True)
class ArtifactEntry:
    """One directory entry inside an artifact, with an artifact-relative path."""

    name: str
    rel_path: str
    is_dir: bool


class ParseOutputStore(ABC):
    """Backend-agnostic storage for a parser's intermediate artifacts.

    Methods take a :class:`ParseArtifactRef` plus an artifact-relative path; the
    store resolves the absolute location. Only byte and directory operations live
    here — parsers keep owning layout and filtering decisions.
    """

    backend: str

    @abstractmethod
    async def create_artifact(self, *, root_type: str = "dir") -> ParseArtifactRef:
        """Allocate a fresh artifact root and return its reference."""

    @abstractmethod
    async def mkdir(self, ref: ParseArtifactRef, rel_path: str = "") -> None: ...

    @abstractmethod
    async def write_bytes(self, ref: ParseArtifactRef, rel_path: str, content: bytes) -> None: ...

    @abstractmethod
    async def read_bytes(self, ref: ParseArtifactRef, rel_path: str) -> bytes: ...

    @abstractmethod
    async def list(self, ref: ParseArtifactRef, rel_path: str = "") -> List[ArtifactEntry]: ...

    @abstractmethod
    async def cleanup(self, ref: ParseArtifactRef) -> None:
        """Delete the artifact. Safe to call more than once."""

    @abstractmethod
    async def move_file(
        self,
        source_ref: ParseArtifactRef,
        source_rel: str,
        target_ref: ParseArtifactRef,
        target_rel: str,
    ) -> None:
        """Move one file between artifacts owned by this store."""

    # -- text convenience -------------------------------------------------
    async def write_text(
        self, ref: ParseArtifactRef, rel_path: str, content: str, *, encoding: str = "utf-8"
    ) -> None:
        await self.write_bytes(ref, rel_path, content.encode(encoding))

    async def read_text(
        self, ref: ParseArtifactRef, rel_path: str, *, encoding: str = "utf-8"
    ) -> str:
        return (await self.read_bytes(ref, rel_path)).decode(encoding)


def _join_artifact_rel(parent: str, child: str) -> str:
    parent = (parent or "").strip("/")
    child = (child or "").strip("/")
    joined = f"{parent}/{child}" if parent and child else parent or child
    return sanitize_relative_viking_path(joined) if joined else ""


class ParseArtifactWriter:
    """Request-scoped writer that owns one parse artifact and its manifest."""

    def __init__(self, store: ParseOutputStore, ref: ParseArtifactRef) -> None:
        if ref.backend != store.backend:
            raise ValueError(
                f"artifact backend {ref.backend!r} does not match store {store.backend!r}"
            )
        self.store = store
        self.ref = ref
        self._md5_by_rel: Dict[str, str] = {}
        self._finalized = False

    @classmethod
    async def create(
        cls, store: ParseOutputStore, *, root_type: str = "dir"
    ) -> "ParseArtifactWriter":
        return cls(store, await store.create_artifact(root_type=root_type))

    async def mkdir(self, rel_path: str = "") -> None:
        await self.store.mkdir(self.ref, self.relative_path(rel_path))

    def relative_path(self, path: str) -> str:
        """Normalize an artifact-relative path or a path rooted at this artifact."""
        value = str(path or "")
        root = self.ref.root.rstrip("/")
        if value == root:
            return ""
        if value.startswith(root + "/"):
            value = value[len(root) + 1 :]
        return _join_artifact_rel("", value)

    async def write_bytes(self, rel_path: str, content: bytes, *, md5: str | None = None) -> None:
        rel_path = self.relative_path(rel_path)
        if not rel_path or rel_path == ARTIFACT_MANIFEST_NAME:
            raise ValueError("artifact business file path must not be the manifest path")
        await self.store.write_bytes(self.ref, rel_path, content)
        self._md5_by_rel[rel_path] = md5 or content_md5(content)
        self._finalized = False

    async def write_text(self, rel_path: str, content: str, *, encoding: str = "utf-8") -> None:
        await self.write_bytes(rel_path, content.encode(encoding))

    async def finalize(self, *, resource_rel: str = "") -> ParseArtifactRef:
        if not self._finalized:
            await write_artifact_manifest(self.store, self.ref, self._md5_by_rel)
            self._finalized = True
        return ParseArtifactRef(
            backend=self.ref.backend,
            root=self.ref.root,
            resource_rel=_join_artifact_rel("", resource_rel),
            root_type=self.ref.root_type,
        )

    async def cleanup(self) -> None:
        await self.store.cleanup(self.ref)

    def record_md5(self, rel_path: str, md5: str) -> None:
        if md5:
            self._md5_by_rel[self.relative_path(rel_path)] = md5
            self._finalized = False


async def create_parse_artifact_writer(
    store: ParseOutputStore | None = None,
    *,
    viking_fs: Any = None,
    ctx: Any = None,
    root_type: str = "dir",
) -> ParseArtifactWriter:
    """Create one request-scoped writer, defaulting to the AGFS backend."""
    resolved_store = store or AgfsParseOutputStore(viking_fs=viking_fs, ctx=ctx)
    return await ParseArtifactWriter.create(resolved_store, root_type=root_type)


async def copy_artifact_tree(
    *,
    source_store: ParseOutputStore,
    source_ref: ParseArtifactRef,
    source_rel: str,
    target: ParseArtifactWriter,
    target_rel: str,
    allowed_hidden: frozenset[str] = frozenset(),
    flatten_single_output: bool = False,
) -> int:
    """Transfer one artifact subtree into another and return the file count."""
    source_rel = _join_artifact_rel("", source_rel)
    target_rel = _join_artifact_rel("", target_rel)
    source_manifest = await read_artifact_manifest(source_store, source_ref)
    copied = 0
    source_entries = [
        entry
        for entry in await source_store.list(source_ref, source_rel)
        if entry.rel_path != ARTIFACT_MANIFEST_NAME
        and (not entry.name.startswith(".") or entry.name in allowed_hidden)
    ]
    if flatten_single_output and len(source_entries) == 1 and source_entries[0].is_dir:
        wrapper = source_entries[0]
        wrapper_entries = [
            entry
            for entry in await source_store.list(source_ref, wrapper.rel_path)
            if not entry.name.startswith(".") or entry.name in allowed_hidden
        ]
        if len(wrapper_entries) == 1 and not wrapper_entries[0].is_dir:
            source_entries = [wrapper_entries[0]]

    async def _copy(src_dir: str, dst_dir: str, entries: List[ArtifactEntry] | None = None) -> None:
        nonlocal copied
        if dst_dir:
            await target.mkdir(dst_dir)
        for entry in (
            entries if entries is not None else await source_store.list(source_ref, src_dir)
        ):
            if entry.rel_path == ARTIFACT_MANIFEST_NAME:
                continue
            if entry.name.startswith(".") and entry.name not in allowed_hidden:
                continue
            child_dst = _join_artifact_rel(dst_dir, entry.name)
            if entry.is_dir:
                await _copy(entry.rel_path, child_dst)
            else:
                md5 = source_manifest.get(entry.rel_path, "")
                if source_store is target.store:
                    if not md5:
                        md5 = content_md5(await source_store.read_bytes(source_ref, entry.rel_path))
                    await target.store.move_file(source_ref, entry.rel_path, target.ref, child_dst)
                    target.record_md5(child_dst, md5)
                else:
                    await target.write_bytes(
                        child_dst,
                        await source_store.read_bytes(source_ref, entry.rel_path),
                        md5=md5,
                    )
                copied += 1

    await _copy(source_rel, target_rel, source_entries)
    return copied


class AgfsParseOutputStore(ParseOutputStore):
    """Artifact store backed by the global VikingFS ``viking://temp`` space.

    Every operation forwards to the VikingFS singleton exactly as parsers did
    before this abstraction existed, so enabling it changes no behaviour.
    """

    backend = "agfs"

    def __init__(self, viking_fs: Any = None, ctx: Any = None) -> None:
        self._viking_fs = viking_fs
        self._ctx = ctx
        self._cleaned: set[str] = set()

    def _fs(self) -> Any:
        if self._viking_fs is not None:
            return self._viking_fs
        from openviking.storage.viking_fs import get_viking_fs

        return get_viking_fs()

    @staticmethod
    def _resolve(ref: ParseArtifactRef, rel_path: str) -> str:
        rel = (rel_path or "").strip("/")
        if not rel:
            return ref.root
        return safe_join_viking_uri(ref.root, rel)

    async def create_artifact(self, *, root_type: str = "dir") -> ParseArtifactRef:
        if root_type not in _ROOT_TYPES:
            raise ValueError(f"root_type must be one of {sorted(_ROOT_TYPES)}")
        fs = self._fs()
        root = fs.create_temp_uri(ctx=self._ctx) if self._ctx is not None else fs.create_temp_uri()
        return ParseArtifactRef(backend=self.backend, root=root, root_type=root_type)

    async def mkdir(self, ref: ParseArtifactRef, rel_path: str = "") -> None:
        kwargs = {"ctx": self._ctx} if self._ctx is not None else {}
        await self._fs().mkdir(self._resolve(ref, rel_path), exist_ok=True, **kwargs)

    async def write_bytes(self, ref: ParseArtifactRef, rel_path: str, content: bytes) -> None:
        fs = self._fs()
        uri = self._resolve(ref, rel_path)
        kwargs = {"ctx": self._ctx} if self._ctx is not None else {}
        if hasattr(fs, "write_file_bytes"):
            await fs.write_file_bytes(uri, content, **kwargs)
        else:
            await fs.write_file(uri, content.decode("utf-8"), **kwargs)

    async def read_bytes(self, ref: ParseArtifactRef, rel_path: str) -> bytes:
        fs = self._fs()
        uri = self._resolve(ref, rel_path)
        kwargs = {"ctx": self._ctx} if self._ctx is not None else {}
        if hasattr(fs, "read_file_bytes"):
            return await fs.read_file_bytes(uri, **kwargs)
        content = await fs.read(uri, **kwargs)
        return content.encode("utf-8") if isinstance(content, str) else content

    async def list(self, ref: ParseArtifactRef, rel_path: str = "") -> List[ArtifactEntry]:
        from openviking.storage.viking_fs import LS_ALL_NODES

        base = self._resolve(ref, rel_path)
        kwargs = {"ctx": self._ctx} if self._ctx is not None else {}
        entries = await self._fs().ls(base, show_all_hidden=True, node_limit=LS_ALL_NODES, **kwargs)
        rel_prefix = (rel_path or "").strip("/")
        result: List[ArtifactEntry] = []
        for entry in entries:
            name = entry.get("name")
            if not name or name in {".", ".."}:
                continue
            child_rel = f"{rel_prefix}/{name}" if rel_prefix else str(name)
            result.append(
                ArtifactEntry(
                    name=str(name),
                    rel_path=child_rel,
                    is_dir=bool(entry.get("isDir")),
                )
            )
        return result

    async def cleanup(self, ref: ParseArtifactRef) -> None:
        if ref.root in self._cleaned:
            return
        self._cleaned.add(ref.root)
        kwargs = {"ctx": self._ctx} if self._ctx is not None else {}
        await self._fs().delete_temp(ref.root, **kwargs)

    async def move_file(
        self,
        source_ref: ParseArtifactRef,
        source_rel: str,
        target_ref: ParseArtifactRef,
        target_rel: str,
    ) -> None:
        kwargs = {"ctx": self._ctx} if self._ctx is not None else {}
        await self._fs().move_file(
            self._resolve(source_ref, source_rel),
            self._resolve(target_ref, target_rel),
            **kwargs,
        )


class LocalParseOutputStore(ParseOutputStore):
    """Artifact store backed by a local directory tree.

    Artifacts stay on the parsing worker until add-resources commits the required
    bytes to the formal AGFS tree. They are not handed to asynchronous semantic
    workers on the SemanticPlan path.

    Case-only name collisions are detected explicitly rather than relying on the
    host filesystem's case sensitivity, so behaviour matches across platforms.
    """

    backend = "local"

    def __init__(self, local_root: str) -> None:
        if not local_root:
            raise ValueError("LocalParseOutputStore requires a local_root")
        self._root = Path(local_root).expanduser().resolve()
        self._root.mkdir(parents=True, exist_ok=True)

    def _resolve(self, ref: ParseArtifactRef, rel_path: str) -> Path:
        artifact_root = Path(ref.root).resolve()
        # The artifact root must stay inside the configured store root.
        if artifact_root != self._root and self._root not in artifact_root.parents:
            raise ValueError("parse artifact root escapes the configured local_root")
        rel = (rel_path or "").strip("/")
        if not rel:
            return artifact_root
        safe_rel = sanitize_relative_viking_path(rel)
        target = artifact_root.joinpath(*Path(safe_rel).parts).resolve()
        if artifact_root != target and artifact_root not in target.parents:
            raise ValueError(f"resolved path escapes the artifact root: {rel_path}")
        return target

    @staticmethod
    def _check_case_conflict(target: Path) -> None:
        parent = target.parent
        if not parent.is_dir():
            return
        lower = target.name.casefold()
        for existing in parent.iterdir():
            if existing.name != target.name and existing.name.casefold() == lower:
                raise ValueError(
                    f"case-only name conflict: {target.name} vs existing {existing.name}"
                )

    async def create_artifact(self, *, root_type: str = "dir") -> ParseArtifactRef:
        if root_type not in _ROOT_TYPES:
            raise ValueError(f"root_type must be one of {sorted(_ROOT_TYPES)}")
        artifact_root = self._root / f"artifact-{uuid.uuid4().hex}"
        await asyncio.to_thread(artifact_root.mkdir, parents=True, exist_ok=False)
        return ParseArtifactRef(
            backend=self.backend,
            root=str(artifact_root),
            root_type=root_type,
        )

    async def mkdir(self, ref: ParseArtifactRef, rel_path: str = "") -> None:
        target = self._resolve(ref, rel_path)
        await asyncio.to_thread(target.mkdir, parents=True, exist_ok=True)

    async def write_bytes(self, ref: ParseArtifactRef, rel_path: str, content: bytes) -> None:
        target = self._resolve(ref, rel_path)

        def _write() -> None:
            target.parent.mkdir(parents=True, exist_ok=True)
            self._check_case_conflict(target)
            target.write_bytes(content)

        await asyncio.to_thread(_write)

    async def read_bytes(self, ref: ParseArtifactRef, rel_path: str) -> bytes:
        target = self._resolve(ref, rel_path)
        return await asyncio.to_thread(target.read_bytes)

    async def list(self, ref: ParseArtifactRef, rel_path: str = "") -> List[ArtifactEntry]:
        base = self._resolve(ref, rel_path)

        def _scan() -> List[ArtifactEntry]:
            if not base.is_dir():
                return []
            rel_prefix = (rel_path or "").strip("/")
            entries: List[ArtifactEntry] = []
            for child in sorted(base.iterdir(), key=lambda p: p.name):
                child_rel = f"{rel_prefix}/{child.name}" if rel_prefix else child.name
                entries.append(
                    ArtifactEntry(
                        name=child.name,
                        rel_path=child_rel,
                        is_dir=child.is_dir(),
                    )
                )
            return entries

        return await asyncio.to_thread(_scan)

    async def cleanup(self, ref: ParseArtifactRef) -> None:
        artifact_root = self._resolve(ref, "")
        await asyncio.to_thread(shutil.rmtree, artifact_root, ignore_errors=True)

    async def move_file(
        self,
        source_ref: ParseArtifactRef,
        source_rel: str,
        target_ref: ParseArtifactRef,
        target_rel: str,
    ) -> None:
        source = self._resolve(source_ref, source_rel)
        target = self._resolve(target_ref, target_rel)

        def _move() -> None:
            target.parent.mkdir(parents=True, exist_ok=True)
            self._check_case_conflict(target)
            shutil.move(str(source), str(target))

        await asyncio.to_thread(_move)


@dataclass(frozen=True)
class ResolvedDocRoot:
    """The single document root located inside a parse artifact.

    ``doc_name`` is the original (un-sanitized) entry name; ``doc_rel`` is its
    artifact-relative path (the file itself when flattened to a single file).
    """

    doc_name: str
    doc_rel: str
    root_is_file: bool


async def resolve_artifact_doc_root(
    store: ParseOutputStore,
    ref: ParseArtifactRef,
    *,
    flatten_single_file: bool = False,
) -> ResolvedDocRoot:
    """Locate the single document root inside an artifact, backend-agnostically.

    Parsers lay out both AGFS and local artifacts the same way — exactly one
    document directory under the root — so this structural walk is shared. It
    only reads the artifact via ``store``; resolving the final target URI against
    the live resource tree is a separate, VikingFS-only concern handled by the
    caller (TreeBuilder.resolve_target_uri).
    """
    top = [
        entry
        for entry in await store.list(ref, "")
        if entry.name not in {".", "..", ARTIFACT_MANIFEST_NAME}
    ]
    doc_dirs = [e for e in top if e.is_dir]
    if flatten_single_file and not doc_dirs:
        files = [entry for entry in top if not entry.is_dir]
        if len(files) == 1:
            return ResolvedDocRoot(
                doc_name=files[0].name,
                doc_rel=files[0].rel_path,
                root_is_file=True,
            )
    if len(doc_dirs) != 1:
        raise ValueError(
            f"expected exactly 1 document directory in artifact {ref.root}, found {len(doc_dirs)}"
        )

    doc_entry = doc_dirs[0]
    doc_name = doc_entry.name
    doc_rel = doc_entry.rel_path
    root_is_file = False

    if flatten_single_file:
        children = [
            entry
            for entry in await store.list(ref, doc_rel)
            if entry.name not in {".", "..", ARTIFACT_MANIFEST_NAME}
        ]
        if len(children) == 1 and not children[0].is_dir:
            doc_name = children[0].name
            doc_rel = children[0].rel_path
            root_is_file = True

    return ResolvedDocRoot(doc_name=doc_name, doc_rel=doc_rel, root_is_file=root_is_file)


def build_parse_output_store(
    *,
    viking_fs: Any = None,
    backend: Optional[Literal["agfs", "local"]] = None,
    local_root: Optional[str] = None,
) -> ParseOutputStore:
    """Return the configured artifact store.

    ``backend`` defaults to AGFS. The local backend requires ``local_root``.
    """
    if backend in (None, "agfs"):
        return AgfsParseOutputStore(viking_fs=viking_fs)
    if backend == "local":
        if not local_root:
            raise ValueError("local parse output backend requires storage.parse_output.local_root")
        return LocalParseOutputStore(local_root=local_root)
    raise ValueError(f"unsupported parse output backend: {backend}")


def store_for_artifact_ref(
    ref: ParseArtifactRef,
    *,
    viking_fs: Any = None,
    ctx: Any = None,
) -> ParseOutputStore:
    """Return the store that owns ``ref``, resolving the local root from config.

    Callers that receive a serialized ref across the queue (semantic worker,
    add-resource cleanup, post-process) must reconstruct the matching backend;
    this centralizes that mapping so the local-root lookup lives in one place.
    """
    if ref.backend == "local":
        from openviking_cli.utils.config import get_openviking_config

        parse_output = get_openviking_config().storage.parse_output
        return build_parse_output_store(
            backend="local", local_root=parse_output.resolved_local_root()
        )
    return AgfsParseOutputStore(viking_fs=viking_fs, ctx=ctx)


async def read_artifact_manifest(store: ParseOutputStore, ref: ParseArtifactRef) -> Dict[str, str]:
    """Return the md5 manifest (artifact-relative path -> md5), or empty if absent.

    A missing/unreadable/malformed manifest yields ``{}`` so the incremental diff
    falls back to comparing file bytes instead of assuming equality.
    """
    try:
        raw = await store.read_bytes(ref, ARTIFACT_MANIFEST_NAME)
        loaded = json.loads(raw.decode("utf-8"))
    except Exception:
        return {}
    if not isinstance(loaded, dict):
        return {}
    return {str(key): str(value) for key, value in loaded.items()}


async def write_artifact_manifest(
    store: ParseOutputStore, ref: ParseArtifactRef, md5_by_rel: Dict[str, str]
) -> None:
    """Persist the md5 manifest at the artifact root using the shared encoding."""
    await store.write_bytes(
        ref,
        ARTIFACT_MANIFEST_NAME,
        json.dumps(md5_by_rel, ensure_ascii=False, sort_keys=True).encode("utf-8"),
    )


# Re-exported so callers importing the sanitizer alongside the store stay in one
# module; keeps parser edits from sprinkling path_safety imports everywhere.
__all__ = [
    "ARTIFACT_MANIFEST_NAME",
    "ArtifactEntry",
    "AgfsParseOutputStore",
    "LocalParseOutputStore",
    "ParseArtifactRef",
    "ParseArtifactWriter",
    "ParseOutputStore",
    "ResolvedDocRoot",
    "build_parse_output_store",
    "copy_artifact_tree",
    "create_parse_artifact_writer",
    "read_artifact_manifest",
    "resolve_artifact_doc_root",
    "sanitize_relative_viking_path",
    "store_for_artifact_ref",
    "write_artifact_manifest",
]
