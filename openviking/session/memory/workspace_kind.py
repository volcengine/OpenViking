# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Semantic definitions for the memory user namespace.

The storage model always uses the existing user and peer URI scopes.  A
workspace kind only tells the extraction model what the user namespace means;
it does not change URI routing or isolation.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional

import yaml


@dataclass(frozen=True)
class WorkspaceKindDefinition:
    """Prompt-facing semantics for one user namespace kind."""

    kind: str
    display_name: str
    shared_scope_label: str
    shared_scope_instruction: str
    private_scope_instruction: str
    resource_scope_instruction: str

    @classmethod
    def from_dict(cls, data: Dict[str, Any], *, source: Path) -> "WorkspaceKindDefinition":
        required = (
            "kind",
            "display_name",
            "shared_scope_label",
            "shared_scope_instruction",
            "private_scope_instruction",
            "resource_scope_instruction",
        )
        missing = [
            key
            for key in required
            if not isinstance(data.get(key), str) or not data[key].strip()
        ]
        if missing:
            raise ValueError(
                f"Workspace kind {source} is missing required fields: {', '.join(missing)}"
            )

        kind = data["kind"].strip().lower()
        if not kind.replace("_", "").isalnum():
            raise ValueError(f"Workspace kind {source} has invalid kind: {kind!r}")
        if kind != source.stem:
            raise ValueError(
                f"Workspace kind {source} declares kind {kind!r}; expected {source.stem!r}"
            )

        return cls(
            kind=kind,
            display_name=data["display_name"].strip(),
            shared_scope_label=data["shared_scope_label"].strip(),
            shared_scope_instruction=data["shared_scope_instruction"].strip(),
            private_scope_instruction=data["private_scope_instruction"].strip(),
            resource_scope_instruction=data["resource_scope_instruction"].strip(),
        )

    def extraction_instructions(self) -> str:
        """Render the semantic policy block inserted into the extractor prompt."""
        return f"""The current OpenViking user namespace represents a {self.shared_scope_label},
not necessarily an individual human.

- Shared-scope memory: {self.shared_scope_instruction}
- Private actor memory: {self.private_scope_instruction}
- Resource memory: {self.resource_scope_instruction}

When a memory belongs to the {self.shared_scope_label}, omit peer_id.
When a memory is private to one actor, set peer_id to an allowed peer_id value.
Do not invent peer_id values. The user namespace and peer namespaces are
storage scopes; use the configured workspace semantics rather than assuming
that "user" means a person.
"""


def _bundled_workspace_kinds_dir() -> Path:
    return Path(__file__).resolve().parents[2] / "prompts" / "templates" / "memory" / "workspaces"


def _workspace_kind_path(kind: str, custom_dir: Optional[str]) -> Path:
    if custom_dir and (custom_path := Path(custom_dir).expanduser() / f"{kind}.yaml").exists():
        return custom_path
    if custom_dir and (
        custom_path := Path(custom_dir).expanduser() / "workspaces" / f"{kind}.yaml"
    ).exists():
        return custom_path
    return _bundled_workspace_kinds_dir() / f"{kind}.yaml"


def load_workspace_kind(kind: str, custom_dir: str = "") -> WorkspaceKindDefinition:
    """Load a built-in or custom workspace-kind definition."""
    path = _workspace_kind_path(kind.strip().lower(), custom_dir)
    if not path.exists():
        available = sorted(p.stem for p in _bundled_workspace_kinds_dir().glob("*.yaml"))
        raise ValueError(
            f"Unknown memory.workspace_kind {kind!r}; expected one of: {', '.join(available)}"
        )
    with path.open("r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle) or {}
    if not isinstance(data, dict):
        raise ValueError(f"Workspace kind definition must be a YAML object: {path}")
    return WorkspaceKindDefinition.from_dict(data, source=path)
