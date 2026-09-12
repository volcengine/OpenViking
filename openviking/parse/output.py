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

from abc import ABC, abstractmethod
from dataclasses import dataclass
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


def build_parse_output_store(
    *,
    viking_fs: Any = None,
    backend: Optional[Literal["agfs", "local"]] = None,
) -> ParseOutputStore:
    """Return the configured artifact store.

    Step 2a only wires the AGFS backend; ``backend`` is accepted so callers can
    pass the resolved config without another branch once local lands in 2b.
    """
    if backend in (None, "agfs"):
        return AgfsParseOutputStore(viking_fs=viking_fs)
    raise ValueError(f"unsupported parse output backend: {backend}")


# Re-exported so callers importing the sanitizer alongside the store stay in one
# module; keeps parser edits from sprinkling path_safety imports everywhere.
__all__ = [
    "ArtifactEntry",
    "AgfsParseOutputStore",
    "ParseArtifactRef",
    "ParseOutputStore",
    "build_parse_output_store",
    "sanitize_relative_viking_path",
]
