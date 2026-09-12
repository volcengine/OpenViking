# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Parse output store abstraction.

Parsers write their intermediate artifacts (the temp tree that TreeBuilder later
persists into the final resource location) through a small store interface rather
than reaching for the global VikingFS singleton directly. This decouples "which
backend holds the artifact" from "how a parser lays out its output".

Step 2a ships only :class:`AgfsParseOutputStore`, which forwards 1:1 to the
existing VikingFS singleton so behaviour is unchanged. A local-directory backend
(single-machine deployments) is added in Step 2b behind the same interface.

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
import shutil
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Any, List, Literal, Optional

from openviking.utils.path_safety import safe_join_viking_uri, sanitize_relative_viking_path

_BACKENDS = frozenset({"agfs", "local"})
_ROOT_TYPES = frozenset({"dir", "file"})


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
    async def mkdir(self, ref: ParseArtifactRef, rel_path: str = "") -> None:
        ...

    @abstractmethod
    async def write_bytes(self, ref: ParseArtifactRef, rel_path: str, content: bytes) -> None:
        ...

    @abstractmethod
    async def read_bytes(self, ref: ParseArtifactRef, rel_path: str) -> bytes:
        ...

    @abstractmethod
    async def list(self, ref: ParseArtifactRef, rel_path: str = "") -> List[ArtifactEntry]:
        ...

    @abstractmethod
    async def cleanup(self, ref: ParseArtifactRef) -> None:
        """Delete the artifact. Safe to call more than once."""

    # -- text convenience -------------------------------------------------
    async def write_text(
        self, ref: ParseArtifactRef, rel_path: str, content: str, *, encoding: str = "utf-8"
    ) -> None:
        await self.write_bytes(ref, rel_path, content.encode(encoding))

    async def read_text(
        self, ref: ParseArtifactRef, rel_path: str, *, encoding: str = "utf-8"
    ) -> str:
        return (await self.read_bytes(ref, rel_path)).decode(encoding)


class AgfsParseOutputStore(ParseOutputStore):
    """Artifact store backed by the global VikingFS ``viking://temp`` space.

    Every operation forwards to the VikingFS singleton exactly as parsers did
    before this abstraction existed, so enabling it changes no behaviour.
    """

    backend = "agfs"

    def __init__(self, viking_fs: Any = None) -> None:
        self._viking_fs = viking_fs
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
        root = self._fs().create_temp_uri()
        return ParseArtifactRef(backend=self.backend, root=root, root_type=root_type)

    async def mkdir(self, ref: ParseArtifactRef, rel_path: str = "") -> None:
        await self._fs().mkdir(self._resolve(ref, rel_path), exist_ok=True)

    async def write_bytes(self, ref: ParseArtifactRef, rel_path: str, content: bytes) -> None:
        await self._fs().write_file_bytes(self._resolve(ref, rel_path), content)

    async def read_bytes(self, ref: ParseArtifactRef, rel_path: str) -> bytes:
        return await self._fs().read_file_bytes(self._resolve(ref, rel_path))

    async def list(self, ref: ParseArtifactRef, rel_path: str = "") -> List[ArtifactEntry]:
        base = self._resolve(ref, rel_path)
        entries = await self._fs().ls(base)
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
        await self._fs().delete_temp(ref.root)


class LocalParseOutputStore(ParseOutputStore):
    """Artifact store backed by a local directory tree.

    For single-machine deployments where every worker in the SOURCE/POST_PROCESS
    chain shares the same filesystem, artifacts stay on local disk instead of
    round-tripping through AGFS temp. Enabling this is a deployment constraint
    (see StorageConfig.parse_output); the store itself only moves bytes.

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
        artifact_root = Path(ref.root)
        await asyncio.to_thread(shutil.rmtree, artifact_root, ignore_errors=True)


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
    top = [e for e in await store.list(ref, "") if e.name not in {".", ".."}]
    doc_dirs = [e for e in top if e.is_dir]
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
            e for e in await store.list(ref, doc_rel) if e.name not in {".", ".."}
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

    ``backend`` defaults to AGFS. The local backend requires ``local_root`` and
    is only safe when every worker in the parse/post-process chain shares that
    path (a deployment constraint owned by the operator, not auto-detected).
    """
    if backend in (None, "agfs"):
        return AgfsParseOutputStore(viking_fs=viking_fs)
    if backend == "local":
        if not local_root:
            raise ValueError("local parse output backend requires storage.parse_output.local_root")
        return LocalParseOutputStore(local_root=local_root)
    raise ValueError(f"unsupported parse output backend: {backend}")


# Re-exported so callers importing the sanitizer alongside the store stay in one
# module; keeps parser edits from sprinkling path_safety imports everywhere.
__all__ = [
    "ArtifactEntry",
    "AgfsParseOutputStore",
    "LocalParseOutputStore",
    "ParseArtifactRef",
    "ParseOutputStore",
    "ResolvedDocRoot",
    "build_parse_output_store",
    "resolve_artifact_doc_root",
    "sanitize_relative_viking_path",
]
