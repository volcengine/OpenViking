# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""
Git Repository Accessor.

Fetches git repositories and zip archives of codebases to local directories.
This is the DataAccessor layer extracted from CodeRepositoryParser.
"""

import asyncio
import os
import shutil
import stat
import tempfile
import urllib.request
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any, Optional, Tuple, Union
from urllib.parse import unquote, urlparse

from openviking.utils import is_github_url, is_gitlab_url, parse_code_hosting_url
from openviking.utils.code_hosting_utils import (
    _domain_matches,
    _extract_azure_devops_repo_parts,
    is_code_hosting_url,
    is_git_repo_url,
    validate_git_ssh_uri,
)
from openviking_cli.utils.config import get_openviking_config
from openviking_cli.utils.logger import get_logger

from .base import DataAccessor, LocalResource, SourceType

logger = get_logger(__name__)


class GitAccessor(DataAccessor):
    """
    Accessor for Git repositories and code archives.

    Supports:
    - Git SSH URLs: git@github.com:org/repo.git
    - Git HTTP/HTTPS URLs: https://github.com/org/repo.git
    - GitHub/GitLab repository pages: https://github.com/org/repo
    - Local ZIP files (containing code repositories)
    """

    PRIORITY = 80  # Higher than generic HTTP, lower than specific services

    @property
    def priority(self) -> int:
        return self.PRIORITY

    def can_handle(self, source: Union[str, Path]) -> bool:
        """
        Check if this accessor can handle the source.

        Handles:
        - git@ SSH URLs
        - git://, ssh:// URLs
        - GitHub/GitLab repository URLs (http/https)
        - Local paths ending with .git (NOT .zip - those go to ZipParser)
        """
        source_str = str(source)

        # Git protocol URLs
        if source_str.startswith(("git@", "git://", "ssh://")):
            try:
                if source_str.startswith("git@"):
                    validate_git_ssh_uri(source_str)
                return is_code_hosting_url(source_str)
            except ValueError:
                return False

        # HTTP/HTTPS URLs to code hosting repos
        if source_str.startswith(("http://", "https://")):
            return is_git_repo_url(source_str)

        # Local .git files (NOT .zip - .zip goes to ZipParser via LocalAccessor)
        if isinstance(source, Path):
            path = source
        else:
            path = Path(source_str)

        suffix = path.suffix.lower()
        return suffix == ".git"

    async def access(self, source: Union[str, Path], **kwargs) -> LocalResource:
        """
        Fetch the git repository or code archive to a local directory.

        Args:
            source: Repository URL (git/http) or local zip path
            **kwargs: Additional arguments (branch, commit, etc.)

        Returns:
            LocalResource pointing to the local directory
        """
        source_str = str(source)
        temp_local_dir = None
        branch = kwargs.get("branch") or kwargs.get("ref")
        commit = kwargs.get("commit")

        try:
            # Create local temp directory (non-blocking)
            temp_local_dir = await asyncio.to_thread(tempfile.mkdtemp, prefix="ov_git_")
            logger.info(f"[GitAccessor] Created local temp dir for git: {temp_local_dir}")

            # Fetch content (Clone or Extract)
            repo_name = "repository"
            local_dir = Path(temp_local_dir)

            if source_str.startswith("git@"):
                # git@ SSH URL
                repo_name = await self._git_clone(
                    source_str,
                    temp_local_dir,
                    branch=branch,
                    commit=commit,
                )
            elif source_str.startswith(("http://", "https://", "git://", "ssh://")):
                repo_url, branch, commit = self._parse_repo_source(source_str, **kwargs)
                if self._is_github_url(repo_url):
                    # Try GitHub ZIP API first, fall back to git clone
                    try:
                        local_dir, repo_name = await self._github_zip_download(
                            repo_url, branch or commit, temp_local_dir
                        )
                    except Exception as zip_exc:
                        logger.warning(
                            f"[GitAccessor] GitHub ZIP download failed, falling back to git clone: {zip_exc}"
                        )

                        # Clean up any partial content before cloning
                        def _cleanup():
                            for p in Path(temp_local_dir).iterdir():
                                if p.is_file():
                                    p.unlink(missing_ok=True)
                                elif p.is_dir():
                                    shutil.rmtree(p, ignore_errors=True)

                        await asyncio.to_thread(_cleanup)
                        repo_name = await self._git_clone(
                            repo_url,
                            temp_local_dir,
                            branch=branch,
                            commit=commit,
                        )
                elif self._is_gitlab_url(repo_url):
                    # Try GitLab ZIP API first, fall back to git clone
                    try:
                        local_dir, repo_name = await self._gitlab_zip_download(
                            repo_url, branch or commit, temp_local_dir
                        )
                    except Exception as zip_exc:
                        logger.warning(
                            f"[GitAccessor] GitLab ZIP download failed, falling back to git clone: {zip_exc}"
                        )

                        # Clean up any partial content before cloning
                        def _cleanup():
                            for p in Path(temp_local_dir).iterdir():
                                if p.is_file():
                                    p.unlink(missing_ok=True)
                                elif p.is_dir():
                                    shutil.rmtree(p, ignore_errors=True)

                        await asyncio.to_thread(_cleanup)
                        repo_name = await self._git_clone(
                            repo_url,
                            temp_local_dir,
                            branch=branch,
                            commit=commit,
                        )
                else:
                    # Non-GitHub/GitLab URL: use git clone
                    repo_name = await self._git_clone(
                        repo_url,
                        temp_local_dir,
                        branch=branch,
                        commit=commit,
                    )
            else:
                raise ValueError(f"Unsupported source for GitAccessor: {source}")

            # Build metadata
            # repo_name is in "org/repo" format (e.g. "volcengine/OpenViking")
            # This is extracted via parse_code_hosting_url() from the original URL
            meta = {"repo_name": repo_name}
            if branch:
                meta["repo_ref"] = branch
            if commit:
                meta["repo_commit"] = commit

            return LocalResource(
                path=local_dir,
                source_type=SourceType.GIT,
                original_source=source_str,  # Full original URL (critical for TreeBuilder!)
                meta=meta,
                is_temporary=True,
            )

        except Exception as e:
            logger.error(
                f"[GitAccessor] Failed to access git repository {source}: {e}", exc_info=True
            )
            # Clean up on error
            if temp_local_dir and os.path.exists(temp_local_dir):
                try:
                    shutil.rmtree(temp_local_dir, ignore_errors=True)
                except Exception:
                    pass
            raise

    def _parse_repo_source(self, source: str, **kwargs) -> Tuple[str, Optional[str], Optional[str]]:
        """Parse repository source URL to extract branch/commit info."""
        branch = kwargs.get("branch") or kwargs.get("ref")
        commit = kwargs.get("commit")
        repo_url = source
        if source.startswith(("http://", "https://", "git://", "ssh://")):
            parsed = urlparse(source)
            repo_url = parsed._replace(query="", fragment="").geturl()
            if commit is None or branch is None:
                branch, commit = self._extract_ref_from_url(parsed, branch, commit)
        repo_url = self._normalize_repo_url(repo_url)
        return repo_url, branch, commit

    def _extract_ref_from_url(
        self,
        parsed: Any,
        branch: Optional[str],
        commit: Optional[str],
    ) -> Tuple[Optional[str], Optional[str]]:
        """Extract branch/commit from URL path."""
        if parsed.path:
            path_branch, path_commit = self._parse_ref_from_path(parsed.path)
            commit = path_commit or commit
            # If commit is present in path, ignore branch entirely
            if commit:
                branch = None
            else:
                branch = branch or path_branch
        return branch, commit

    def _parse_ref_from_path(self, path: str) -> Tuple[Optional[str], Optional[str]]:
        """Parse ref from URL path components."""
        parts = [p for p in path.split("/") if p]
        branch = None
        commit = None
        if "commit" in parts:
            idx = parts.index("commit")
            if idx + 1 < len(parts):
                commit = parts[idx + 1]
        if "tree" in parts:
            idx = parts.index("tree")
            if idx + 1 < len(parts):
                ref = unquote(parts[idx + 1])
                if self._looks_like_sha(ref):
                    commit = ref
                else:
                    branch = ref
        return branch, commit

    @staticmethod
    def _looks_like_sha(ref: str) -> bool:
        """Return True if ref looks like a git commit SHA (7-40 hex chars)."""
        return 7 <= len(ref) <= 40 and all(c in "0123456789abcdefABCDEF" for c in ref)

    def _normalize_repo_url(self, url: str) -> str:
        """Normalize repository URL to base form."""
        if url.startswith(("http://", "https://", "git://", "ssh://")):
            parsed = urlparse(url)
            path_parts = [p for p in parsed.path.split("/") if p]
            base_parts = path_parts
            git_index = next((i for i, p in enumerate(path_parts) if p.endswith(".git")), None)
            if git_index is not None:
                base_parts = path_parts[: git_index + 1]

            config = get_openviking_config()
            if _domain_matches(parsed, getattr(config.code, "azure_devops_domains", [])):
                azure_repo_parts = _extract_azure_devops_repo_parts(path_parts)
                if azure_repo_parts:
                    base_parts = path_parts[: len(azure_repo_parts) + 1]

            if _domain_matches(parsed, config.code.github_domains + config.code.gitlab_domains):
                base_parts = path_parts[:2]
            base_path = "/" + "/".join(base_parts)
            return parsed._replace(path=base_path, query="", fragment="").geturl()
        return url

    def _get_repo_name(self, url: str) -> str:
        """Get repository name with organization for GitHub/GitLab URLs.

        Returns repo name in "org/repo" format (e.g. "volcengine/OpenViking")
        when possible. This is important for the final root_uri in VikingFS.

        Example:
          - Input: "https://github.com/volcengine/OpenViking"
          - Returns: "volcengine/OpenViking"
        """
        # First try to parse as code hosting URL
        parsed_org_repo = parse_code_hosting_url(url)
        if parsed_org_repo:
            return parsed_org_repo

        # Fallback for other URLs
        name_source = url
        if url.startswith(("http://", "https://", "git://", "ssh://")):
            name_source = urlparse(url).path.rstrip("/")
        elif ":" in url and not url.startswith("file://"):
            name_source = url.split(":", 1)[1]

        # Original logic for non-GitHub/GitLab URLs
        name = name_source.rstrip("/").split("/")[-1]
        if name.endswith(".git"):
            name = name[:-4]
        name = "".join(c if c.isalnum() or c in "-_" else "_" for c in name)
        return name or "repository"

    async def _run_git(self, args: list[str], cwd: Optional[str] = None) -> str:
        """Run a git command."""
        proc = await asyncio.create_subprocess_exec(
            *args,
            cwd=cwd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
        if proc.returncode != 0:
            error_msg = stderr.decode().strip()
            user_msg = "Git command failed."
            if "Could not resolve hostname" in error_msg:
                user_msg = (
                    "Git command failed: could not resolve hostname. Check the URL or your network."
                )
            elif "Permission denied" in error_msg or "publickey" in error_msg:
                user_msg = (
                    "Git command failed: authentication error. Check your SSH keys or credentials."
                )
            raise RuntimeError(
                f"{user_msg} Command: git {' '.join(args[1:])}. Details: {error_msg}"
            )
        return stdout.decode().strip()

    async def _has_commit(self, repo_dir: str, commit: str) -> bool:
        """Check if a commit exists in the repository."""
        try:
            await self._run_git(["git", "-C", repo_dir, "rev-parse", "--verify", commit])
            return True
        except RuntimeError:
            return False

    @staticmethod
    def _is_github_url(url: str) -> bool:
        """Return True for github.com URLs (supports ZIP archive API)."""
        return is_github_url(url)

    @staticmethod
    def _is_gitlab_url(url: str) -> bool:
        """Return True for gitlab.com URLs (supports ZIP archive API)."""
        return is_gitlab_url(url)

    async def _git_clone(
        self,
        url: str,
        target_dir: str,
        branch: Optional[str] = None,
        commit: Optional[str] = None,
    ) -> str:
        """Clone a git repository into target_dir; return the repo name."""
        name = self._get_repo_name(url)
        logger.info(f"[GitAccessor] Cloning {url} to {target_dir}...")

        clone_args = [
            "git",
            "clone",
            "--depth",
            "1",
            "--recursive",
        ]
        if branch and not commit:
            clone_args.extend(["--branch", branch])
        clone_args.extend([url, target_dir])
        await self._run_git(clone_args)

        if commit:
            try:
                await self._run_git(["git", "-C", target_dir, "fetch", "origin", commit])
            except RuntimeError:
                try:
                    await self._run_git(
                        ["git", "-C", target_dir, "fetch", "--all", "--tags", "--prune"]
                    )
                except RuntimeError:
                    pass
                ok = await self._has_commit(target_dir, commit)
                if not ok:
                    try:
                        await self._run_git(
                            ["git", "-C", target_dir, "fetch", "--unshallow", "origin"]
                        )
                    except RuntimeError:
                        pass
                ok = await self._has_commit(target_dir, commit)
                if not ok:
                    await self._run_git(
                        [
                            "git",
                            "-C",
                            target_dir,
                            "fetch",
                            "origin",
                            "+refs/heads/*:refs/remotes/origin/*",
                        ]
                    )
                    ok = await self._has_commit(target_dir, commit)
                    if not ok:
                        raise RuntimeError(f"Failed to fetch commit {commit} from {url}")
            await self._run_git(["git", "-C", target_dir, "checkout", commit])

        # Add .git_source_repo marker file with original URL (for consistency)
        def _write_marker():
            marker_file = Path(target_dir) / ".git_source_repo"
            marker_file.write_text(url, encoding="utf-8")

        await asyncio.to_thread(_write_marker)

        return name

    async def _github_zip_download(
        self,
        repo_url: str,
        branch: Optional[str],
        target_dir: str,
    ) -> Tuple[Path, str]:
        """Download a GitHub repo as a ZIP archive and extract it."""
        repo_name = self._get_repo_name(repo_url)

        # Build archive URL from owner/repo path components.
        parsed = urlparse(repo_url)
        path_parts = [p for p in parsed.path.split("/") if p]
        owner = path_parts[0]
        repo_raw = path_parts[1]
        # Strip .git suffix for the archive URL
        repo_slug = repo_raw[:-4] if repo_raw.endswith(".git") else repo_raw

        if branch:
            zip_url = f"https://github.com/{owner}/{repo_slug}/archive/{branch}.zip"
        else:
            zip_url = f"https://github.com/{owner}/{repo_slug}/archive/HEAD.zip"

        logger.info(f"[GitAccessor] Downloading GitHub ZIP: {zip_url}")

        zip_path = os.path.join(target_dir, "_archive.zip")
        extract_dir = os.path.join(target_dir, "_extracted")
        os.makedirs(extract_dir, exist_ok=True)

        # Download (blocking HTTP; run in thread pool)
        def _download() -> None:
            headers = {"User-Agent": "OpenViking"}
            github_token = os.environ.get("GITHUB_TOKEN")
            if github_token:
                headers["Authorization"] = f"token {github_token}"

            req = urllib.request.Request(zip_url, headers=headers)
            with urllib.request.urlopen(req, timeout=1800) as resp, open(zip_path, "wb") as f:
                shutil.copyfileobj(resp, f)

        try:
            await asyncio.to_thread(_download)
        except Exception as exc:
            raise RuntimeError(f"Failed to download GitHub ZIP {zip_url}: {exc}")

        # Safe extraction with Zip Slip validation (non-blocking)
        def _extract_zip():
            target = Path(extract_dir).resolve()
            with zipfile.ZipFile(zip_path, "r") as zf:
                for info in zf.infolist():
                    mode = info.external_attr >> 16
                    if info.is_dir() or stat.S_ISDIR(mode):
                        continue
                    if stat.S_ISLNK(mode):
                        logger.warning(
                            f"[GitAccessor] Skipping symlink entry in GitHub ZIP: {info.filename}"
                        )
                        continue
                    raw = info.filename.replace("\\", "/")
                    raw_parts = [p for p in raw.split("/") if p]
                    if ".." in raw_parts:
                        raise ValueError(f"Zip Slip detected in GitHub archive: {info.filename!r}")
                    if PurePosixPath(raw).is_absolute():
                        raise ValueError(f"Zip Slip detected in GitHub archive: {info.filename!r}")
                    extracted = Path(zf.extract(info, extract_dir)).resolve()
                    if not extracted.is_relative_to(target):
                        extracted.unlink(missing_ok=True)
                        raise ValueError(f"Zip Slip detected in GitHub archive: {info.filename!r}")

        await asyncio.to_thread(_extract_zip)

        # Remove downloaded archive to free disk space (non-blocking)
        def _cleanup_archive():
            try:
                os.unlink(zip_path)
            except OSError:
                pass

        await asyncio.to_thread(_cleanup_archive)

        # GitHub ZIPs have a single top-level directory (non-blocking)
        def _find_content_dir() -> Path:
            top_level = [d for d in Path(extract_dir).iterdir() if d.is_dir()]
            return top_level[0] if len(top_level) == 1 else Path(extract_dir)

        content_dir = await asyncio.to_thread(_find_content_dir)

        # Add .git_source_repo marker file with original URL
        def _write_marker():
            marker_file = content_dir / ".git_source_repo"
            marker_file.write_text(repo_url, encoding="utf-8")

        await asyncio.to_thread(_write_marker)

        logger.info(f"[GitAccessor] GitHub ZIP extracted to {content_dir} ({repo_name})")
        return content_dir, repo_name

    async def _gitlab_zip_download(
        self,
        repo_url: str,
        branch: Optional[str],
        target_dir: str,
    ) -> Tuple[Path, str]:
        """Download a GitLab repo as a ZIP archive and extract it."""
        repo_name = self._get_repo_name(repo_url)

        # Build archive URL from owner/repo path components.
        # GitLab archive URL format: https://gitlab.com/{owner}/{repo}/-/archive/{ref}/{repo}-{ref}.zip
        parsed = urlparse(repo_url)
        path_parts = [p for p in parsed.path.split("/") if p]
        owner = path_parts[0]
        repo_raw = path_parts[1]
        # Strip .git suffix for the archive URL
        repo_slug = repo_raw[:-4] if repo_raw.endswith(".git") else repo_raw

        ref = branch or "HEAD"
        # GitLab uses the format: /{owner}/{repo}/-/archive/{ref}/{repo}-{ref}.zip
        zip_url = f"{parsed.scheme}://{parsed.netloc}/{owner}/{repo_slug}/-/archive/{ref}/{repo_slug}-{ref}.zip"

        logger.info(f"[GitAccessor] Downloading GitLab ZIP: {zip_url}")

        zip_path = os.path.join(target_dir, "_archive.zip")
        extract_dir = os.path.join(target_dir, "_extracted")
        os.makedirs(extract_dir, exist_ok=True)

        # Download (blocking HTTP; run in thread pool)
        def _download() -> None:
            headers = {"User-Agent": "OpenViking"}

            req = urllib.request.Request(zip_url, headers=headers)
            with urllib.request.urlopen(req, timeout=1800) as resp, open(zip_path, "wb") as f:
                shutil.copyfileobj(resp, f)

        try:
            await asyncio.to_thread(_download)
        except Exception as exc:
            raise RuntimeError(f"Failed to download GitLab ZIP {zip_url}: {exc}")

        # Safe extraction with Zip Slip validation (non-blocking)
        def _extract_zip():
            target = Path(extract_dir).resolve()
            with zipfile.ZipFile(zip_path, "r") as zf:
                for info in zf.infolist():
                    mode = info.external_attr >> 16
                    if info.is_dir() or stat.S_ISDIR(mode):
                        continue
                    if stat.S_ISLNK(mode):
                        logger.warning(
                            f"[GitAccessor] Skipping symlink entry in GitLab ZIP: {info.filename}"
                        )
                        continue
                    raw = info.filename.replace("\\", "/")
                    raw_parts = [p for p in raw.split("/") if p]
                    if ".." in raw_parts:
                        raise ValueError(f"Zip Slip detected in GitLab archive: {info.filename!r}")
                    if PurePosixPath(raw).is_absolute():
                        raise ValueError(f"Zip Slip detected in GitLab archive: {info.filename!r}")
                    extracted = Path(zf.extract(info, extract_dir)).resolve()
                    if not extracted.is_relative_to(target):
                        extracted.unlink(missing_ok=True)
                        raise ValueError(f"Zip Slip detected in GitLab archive: {info.filename!r}")

        await asyncio.to_thread(_extract_zip)

        # Remove downloaded archive to free disk space (non-blocking)
        def _cleanup_archive():
            try:
                os.unlink(zip_path)
            except OSError:
                pass

        await asyncio.to_thread(_cleanup_archive)

        # GitLab ZIPs have a single top-level directory: {repo}-{ref}/ (non-blocking)
        def _find_content_dir() -> Path:
            top_level = [d for d in Path(extract_dir).iterdir() if d.is_dir()]
            return top_level[0] if len(top_level) == 1 else Path(extract_dir)

        content_dir = await asyncio.to_thread(_find_content_dir)

        # Add .git_source_repo marker file with original URL
        def _write_marker():
            marker_file = content_dir / ".git_source_repo"
            marker_file.write_text(repo_url, encoding="utf-8")

        await asyncio.to_thread(_write_marker)

        logger.info(f"[GitAccessor] GitLab ZIP extracted to {content_dir} ({repo_name})")
        return content_dir, repo_name

    async def _extract_zip(self, zip_path: str, target_dir: str) -> str:
        """Extract a local zip file into target_dir."""
        if zip_path.startswith(("http://", "https://")):
            raise NotImplementedError("Zip URL download not yet implemented in GitAccessor")

        path = Path(zip_path)
        name = path.stem

        # Extract zip file (non-blocking)
        def _extract_zip_sync():
            with zipfile.ZipFile(zip_path, "r") as zip_ref:
                target = Path(target_dir).resolve()
                for info in zip_ref.infolist():
                    mode = info.external_attr >> 16
                    # Skip directory entries
                    if info.is_dir() or stat.S_ISDIR(mode):
                        continue
                    # Skip symlink entries
                    if stat.S_ISLNK(mode):
                        logger.warning(
                            f"[GitAccessor] Skipping symlink entry in zip: {info.filename}"
                        )
                        continue
                    # Reject entries with suspicious raw path components
                    raw = info.filename.replace("\\", "/")
                    raw_parts = [p for p in raw.split("/") if p]
                    if ".." in raw_parts:
                        raise ValueError(
                            f"Zip Slip detected: entry {info.filename!r} contains '..'"
                        )
                    if PurePosixPath(raw).is_absolute() or (len(raw) >= 2 and raw[1] == ":"):
                        raise ValueError(
                            f"Zip Slip detected: entry {info.filename!r} is an absolute path"
                        )
                    # Normalize and verify
                    arcname = info.filename.replace("/", os.sep)
                    if os.path.altsep:
                        arcname = arcname.replace(os.path.altsep, os.sep)
                    arcname = os.path.splitdrive(arcname)[1]
                    arcname = os.sep.join(
                        p for p in arcname.split(os.sep) if p not in ("", ".", "..")
                    )
                    if not arcname:
                        continue
                    member_path = (Path(target_dir) / arcname).resolve()
                    if not member_path.is_relative_to(target):
                        raise ValueError(
                            f"Zip Slip detected: entry {info.filename!r} escapes target directory"
                        )
                    # Extract and verify
                    extracted = Path(zip_ref.extract(info, target_dir)).resolve()
                    if not extracted.is_relative_to(target):
                        # Best-effort cleanup
                        try:
                            extracted.unlink(missing_ok=True)
                        except OSError as cleanup_err:
                            logger.warning(
                                f"[GitAccessor] Failed to clean up escaped file {extracted}: {cleanup_err}"
                            )
                        raise ValueError(
                            f"Zip Slip detected: entry {info.filename!r} escapes target directory"
                        )

        await asyncio.to_thread(_extract_zip_sync)

        return name
