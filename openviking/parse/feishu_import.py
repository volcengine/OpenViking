# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Runtime-only Feishu content references and directory download budget."""

import hashlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from openviking_cli.exceptions import InvalidArgumentError


def recursive_wiki(options: dict[str, Any]) -> bool:
    value = options.get("feishu_recursive", False)
    if not isinstance(value, bool):
        raise InvalidArgumentError("feishu_recursive must be a boolean")
    return value


@dataclass(frozen=True)
class FeishuContent:
    path: Path
    url: str
    token: str
    kind: str  # url, markdown, file

    def checkpoint_key(self, root: Path) -> str:
        relative = self.path.resolve().relative_to(root.resolve()).as_posix()
        identity = f"{relative}\n{self.url}\n{self.token}"
        return hashlib.sha256(identity.encode("utf-8")).hexdigest()


@dataclass
class FeishuImportPlan:
    root: Path
    use_understanding: bool = False
    source_url: str = ""
    entries: list[FeishuContent] = field(default_factory=list)
    reserved: set[Path] = field(default_factory=set)
    max_nodes: int = 5000
    max_depth: int = 20
    max_bytes: int = 1024 * 1024 * 1024
    nodes: int = 0
    downloaded_bytes: int = 0
    download_exhausted: bool = False

    def visit(self, depth: int) -> None:
        if depth > self.max_depth or self.nodes >= self.max_nodes:
            raise ValueError("Feishu import node/depth limit exceeded")
        self.nodes += 1

    def account_download(self, size: int) -> None:
        if self.downloaded_bytes + size > self.max_bytes:
            self.download_exhausted = True
            raise ValueError("Feishu import download byte limit exceeded")
        self.downloaded_bytes += size

    def add(self, path: Path, url: str, token: str, kind: str) -> None:
        self.entries.append(FeishuContent(path, url, token, kind))
