# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Resolve skill sources for server-side discovery, import, and updates."""

import asyncio
import os
import shutil
from contextlib import asynccontextmanager
from pathlib import Path, PurePosixPath
from urllib.parse import unquote, urlparse

from openviking.core.skill_loader import SkillLoader
from openviking.parse.accessors.git_accessor import GitAccessor
from openviking.server.local_input_guard import deny_direct_local_skill_input
from openviking.utils.code_hosting_utils import is_github_url, parse_git_repo_url
from openviking.utils.network_guard import ensure_public_remote_target
from openviking.utils.skill_processor import SkillProcessor, validate_skill_name
from openviking_cli.exceptions import InvalidArgumentError


def parse_git_skill_source(source: str) -> dict:
    """Parse a Git URL into repository, revision, and skill subdirectory."""
    parsed = urlparse(source)
    parts = parsed.path.strip("/").split("/")
    subdir = None
    ref = None
    repo_url = source
    if is_github_url(source) and len(parts) > 4 and parts[2] == "tree":
        # In tree/<ref>/skills/..., <ref> may contain slashes.
        split = parts.index("skills", 4) if "skills" in parts[4:] else 4
        ref = unquote("/".join(parts[3:split]))
        subdir = unquote("/".join(parts[split:]))
        repo_url = parsed._replace(path="/" + "/".join(parts[:2]), query="", fragment="").geturl()
    repo = parse_git_repo_url(repo_url)
    if repo is None:
        raise InvalidArgumentError("Unsupported Git skill source URL")
    return {
        "type": "git",
        "source": source,
        "clone_url": repo.clone_url,
        "ref_name": ref or repo.branch or repo.commit,
        "subdir": subdir,
    }


def _skill_paths(root: Path, names: list[str], repo_root: Path | None) -> list[Path]:
    paths = []
    # A SKILL.md owns its whole subtree, including nested SKILL.md attachments.
    for directory, dirs, files in os.walk(root):
        dirs[:] = sorted(name for name in dirs if name != ".git")
        if "SKILL.md" in files:
            paths.append(Path(directory))
            dirs.clear()
    if not paths:
        raise InvalidArgumentError("No SKILL.md found in the skill source")
    names = list(dict.fromkeys(name.strip() for name in names))
    selected = paths
    if names and names != ["*"]:
        selected = []
        for name in names:
            validate_skill_name(name)
            matches = [path for path in paths if path.name == name]
            if len(matches) != 1:
                raise InvalidArgumentError(
                    f"Skill '{name}' must identify exactly one source directory"
                )
            selected.append(matches[0])
    if repo_root is not None:
        # A cloned symlink must never import files outside the repository.
        for path in selected:
            for entry in path.rglob("*"):
                if not entry.resolve().is_relative_to(repo_root):
                    raise InvalidArgumentError("Skill file escapes the source repository")
    return selected


@asynccontextmanager
async def resolve_skill_source(
    data,
    *,
    names: list[str] | None = None,
    allow_local_path_resolution: bool = False,
    source_metadata: dict | None = None,
    git_source: dict | None = None,
):
    """Yield (skill data, source metadata) pairs; retain files until processing ends.

    Only trusted temp uploads may resolve local paths. Recorded Git metadata is
    accepted for updates, but is validated again before any network or file I/O.
    """
    resource = None
    repo_root = None
    cleanup_path = None
    names = names or []
    try:
        if (
            isinstance(data, str)
            and data.startswith(("https://", "http://", "git@", "ssh://", "git://"))
            and "\n" not in data
        ):
            git_source = git_source or parse_git_skill_source(data)
        if git_source is not None:
            clone_url = git_source.get("clone_url")
            if not isinstance(clone_url, str) or parse_git_repo_url(clone_url) is None:
                raise InvalidArgumentError("Invalid recorded Git skill source")
            subdir = git_source.get("subdir") or ""
            if (
                not isinstance(subdir, str)
                or "\\" in subdir
                or PurePosixPath(subdir).is_absolute()
                or ".." in PurePosixPath(subdir).parts
            ):
                raise InvalidArgumentError("Skill source subdir must stay inside the repository")
            await asyncio.to_thread(ensure_public_remote_target, clone_url)
            resource = await GitAccessor().access(clone_url, ref=git_source.get("ref_name"))
            repo_root = resource.path.resolve()
            data = (repo_root / subdir).resolve()
            if not data.is_relative_to(repo_root) or not data.is_dir():
                raise InvalidArgumentError(
                    "Skill source subdir is not a directory inside the repository"
                )
            # Git transport metadata is not skill content.
            await asyncio.to_thread(shutil.rmtree, repo_root / ".git", ignore_errors=True)
            (repo_root / ".git_source_repo").unlink(missing_ok=True)
        elif allow_local_path_resolution:
            data, cleanup_path = SkillProcessor._resolve_skill_path(Path(data))
        elif isinstance(data, str):
            deny_direct_local_skill_input(data)

        if not isinstance(data, Path) or not data.is_dir():
            if names:
                raise InvalidArgumentError("Skill selection requires a directory or Git source")
            yield [(data, source_metadata)]
            return

        paths = await asyncio.to_thread(_skill_paths, data, names, repo_root)
        targets = []
        for path in paths:
            metadata = source_metadata
            if git_source is not None:
                metadata = {
                    **git_source,
                    "subdir": path.relative_to(repo_root).as_posix(),
                }
            targets.append((path, metadata))
        yield targets
    finally:
        if resource is not None:
            await asyncio.to_thread(resource.cleanup)
        if cleanup_path:
            await asyncio.to_thread(shutil.rmtree, cleanup_path, ignore_errors=True)


def describe_skill_sources(targets: list[tuple]) -> dict:
    """Describe source skills without persisting content or invoking models."""
    skills = []
    for data, _metadata in targets:
        if not isinstance(data, Path):
            raise InvalidArgumentError("Listing skills requires a directory or Git source")
        skill = SkillLoader.load(str(data / "SKILL.md" if data.is_dir() else data))
        skills.append(
            {"name": skill["name"], "description": skill["description"], "path": data.name}
        )
    return {"skills": skills, "total": len(skills)}
