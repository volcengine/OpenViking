# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""
Code Repository Parser.

Handles git repositories and zip archives of codebases.
Implements V5.0 asynchronous architecture:
- Physical move (Clone -> Temp VikingFS)
- No LLM generation in parser phase
"""

import asyncio
import os
import shutil
import stat
import time
import urllib.request
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any, List, Optional, Tuple, Union
from urllib.parse import unquote, urlparse

from openviking.parse.base import (
    NodeType,
    ParseResult,
    ResourceNode,
    create_parse_result,
)
from openviking.parse.parsers.base_parser import BaseParser
from openviking.parse.parsers.constants import (
    CODE_EXTENSIONS,
    DOCUMENTATION_EXTENSIONS,
    FILE_TYPE_CODE,
    FILE_TYPE_DOCUMENTATION,
    FILE_TYPE_OTHER,
    IGNORE_DIRS,
    IGNORE_EXTENSIONS,
)
from openviking.parse.parsers.upload_utils import upload_directory
from openviking.utils import is_github_url, parse_code_hosting_url
from openviking_cli.utils.config import get_openviking_config
from openviking_cli.utils.logger import get_logger

logger = get_logger(__name__)


class CodeRepositoryParser(BaseParser):
    """
    Parser for code repositories (Git/Zip).

    Features:
    - Shallow clone for Git repositories
    - Automatic filtering of non-code directories (.git, node_modules, etc.)
    - Direct mapping to VikingFS temp directory
    - Preserves directory structure without chunking

    代码仓库入库处理流程

    输入: https://github.com/markwhen/gogetxueqiu
        ↓
    [GitAccessor] → LocalResource
                    - path: /tmp/.../extracted/repo
                    - source_type: SourceType.GIT
                    - original_source: "https://github.com/markwhen/gogetxueqiu"
                    - meta: {repo_name: "markwhen/gogetxueqiu", ...}
        ↓
    [media_processor] → 所有目录都用 DirectoryParser！← 简化了！
        ↓
    [DirectoryParser.parse()]
        ├─→ 检测到 (path/.git).exists() ← 新增！
        ├─→ 收集 git 元数据
        └─→ 委托给 CodeRepositoryParser.parse()
        ↓
    [CodeRepositoryParser.parse()]
        - source_path = original_source (https://github.com/...)
        ↓
    [TreeBuilder.finalize_from_temp()]
        - 从 source_path 解析出 "markwhen/gogetxueqiu"
        - root_uri = "viking://resources/markwhen/gogetxueqiu"
    """

    # Class constants imported from constants.py
    IGNORE_DIRS = IGNORE_DIRS
    IGNORE_EXTENSIONS = IGNORE_EXTENSIONS

    @property
    def supported_extensions(self) -> List[str]:
        # This parser is primarily invoked by URLTypeDetector, not by file extension
        return [".git", ".zip"]

    def _detect_file_type(self, file_path: Path) -> str:
        """
        Detect file type based on extension for potential metadata tagging.

        Returns:
            "code" for programming language files
            "documentation" for documentation files (md, txt, rst, etc.)
            "other" for other text files
            "binary" for binary files (already filtered by IGNORE_EXTENSIONS)
        """
        extension = file_path.suffix.lower()

        if extension in CODE_EXTENSIONS:
            return FILE_TYPE_CODE
        elif extension in DOCUMENTATION_EXTENSIONS:
            return FILE_TYPE_DOCUMENTATION
        else:
            # For other text files not in the lists
            return FILE_TYPE_OTHER

    async def parse(self, source: Union[str, Path], instruction: str = "", **kwargs) -> ParseResult:
        """
        Parse code repository (only accepts local directories, as network fetching is done in Accessor layer).

        Args:
            source: Local directory path (already fetched by DataAccessor)
            instruction: Processing instruction (unused in parser phase)
            **kwargs: Additional arguments
                _source_meta: Metadata from DataAccessor (must contain repo_name, repo_ref, repo_commit)
                original_source: Original URL (for repo name extraction if needed)

        Returns:
            ParseResult with temp_dir_path pointing to the uploaded content
        """
        start_time = time.time()
        source_path = Path(source)

        # Check if source is already a local directory (should always be true)
        if not source_path.is_dir():
            raise ValueError(
                f"CodeRepositoryParser only accepts local directories. "
                f"Source type: {type(source)}, value: {source}"
            )

        logger.info(f"[CodeRepositoryParser] Parsing code repository: {source_path}")

        try:
            # Get metadata from DataAccessor
            # _source_meta comes from GitAccessor and contains:
            #   - repo_name: in "org/repo" format (e.g. "volcengine/OpenViking")
            #   - repo_ref: branch name if specified
            #   - repo_commit: commit hash if specified
            source_meta = kwargs.get("_source_meta", {})
            repo_name = source_meta.get("repo_name", "repository")
            branch = source_meta.get("repo_ref")
            commit = source_meta.get("repo_commit")

            # If repo_name is still default, try to extract from original source
            # original_source is the full GitHub/GitLab URL that the user provided
            # (e.g. "https://github.com/volcengine/OpenViking")
            if repo_name == "repository":
                original_source = kwargs.get("original_source") or source_meta.get(
                    "original_source"
                )
                if original_source:
                    parsed_org_repo = parse_code_hosting_url(original_source)
                    if parsed_org_repo:
                        repo_name = parsed_org_repo

            local_dir = source_path

            # 3. Create VikingFS temp URI
            viking_fs = self._get_viking_fs()
            temp_viking_uri = self._create_temp_uri()
            # The structure in temp should be: viking://temp/{uuid}/repository/...
            # Use simple name 'repository' for temp, TreeBuilder will rename it to org/repo later
            target_root_uri = f"{temp_viking_uri}/repository"

            logger.info(f"Uploading to VikingFS: {target_root_uri}")

            # 4. Upload to VikingFS (filtering on the fly)
            file_count = await self._upload_directory(local_dir, target_root_uri, viking_fs)

            logger.info(f"Uploaded {file_count} files to {target_root_uri}")

            # 5. Create result
            # Root node is just a placeholder, TreeBuilder relies on temp_dir_path
            root = ResourceNode(
                type=NodeType.ROOT,
                content_path=None,
                meta={"name": repo_name, "type": "repository"},
            )

            # Use original URL as source_path instead of local temp dir for proper org/repo parsing in TreeBuilder
            # source_path is CRITICAL:
            #   1. TreeBuilder uses source_path to parse org/repo via parse_code_hosting_url()
            #   2. If source_path is a local path (like /tmp/.../OpenViking), parsing fails
            #   3. If source_path is the original GitHub URL (https://github.com/volcengine/OpenViking),
            #      TreeBuilder can correctly extract "volcengine/OpenViking"
            #
            # Priority order:
            #   1. First check kwargs["original_source"] - this is set by media_processor
            #   2. Then check source_meta (rarely has it)
            #   3. Fall back to local path only as last resort
            original_source = kwargs.get("original_source") or source_meta.get("original_source")
            result = create_parse_result(
                root=root,
                source_path=original_source or str(source),
                source_format="repository",
                parser_name="CodeRepositoryParser",
                parse_time=time.time() - start_time,
            )
            result.temp_dir_path = temp_viking_uri  # Points to parent of repo_name
            result.meta["file_count"] = file_count
            result.meta["repo_name"] = repo_name
            if branch:
                result.meta["repo_ref"] = branch
            if commit:
                result.meta["repo_commit"] = commit

            return result

        except Exception as e:
            logger.error(f"Failed to parse repository {source}: {e}", exc_info=True)
            # Use original URL for error case as well - still important for TreeBuilder
            # Even on failure, we want TreeBuilder to potentially get org/repo from the URL
            original_source = kwargs.get("original_source") or source_meta.get("original_source")
            return create_parse_result(
                root=ResourceNode(type=NodeType.ROOT, content_path=None),
                source_path=original_source or str(source),
                source_format="repository",
                parser_name="CodeRepositoryParser",
                parse_time=time.time() - start_time,
                warnings=[f"Failed to parse repository: {str(e)}"],
            )

    async def parse_content(
        self, content: str, source_path: Optional[str] = None, instruction: str = "", **kwargs
    ) -> ParseResult:
        """Not supported for repositories."""
        raise NotImplementedError("CodeRepositoryParser does not support parse_content")

    def _parse_repo_source(self, source: str, **kwargs) -> Tuple[str, Optional[str], Optional[str]]:
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
        if url.startswith(("http://", "https://", "git://", "ssh://")):
            parsed = urlparse(url)
            path_parts = [p for p in parsed.path.split("/") if p]
            base_parts = path_parts
            git_index = next((i for i, p in enumerate(path_parts) if p.endswith(".git")), None)
            if git_index is not None:
                base_parts = path_parts[: git_index + 1]

            config = get_openviking_config()
            if (
                parsed.netloc in config.code.github_domains + config.code.gitlab_domains
                and len(path_parts) >= 2
            ):
                base_parts = path_parts[:2]
            base_path = "/" + "/".join(base_parts)
            return parsed._replace(path=base_path, query="", fragment="").geturl()
        return url

    def _get_repo_name(self, url: str) -> str:
        """Get repository name with organization for GitHub/GitLab URLs.

        For https://github.com/volcengine/OpenViking, returns "volcengine/OpenViking"
        For other URLs, falls back to just the repo name.
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

    async def _run_git(self, args: List[str], cwd: Optional[str] = None) -> str:
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
        try:
            await self._run_git(["git", "-C", repo_dir, "rev-parse", "--verify", commit])
            return True
        except RuntimeError:
            return False

    @staticmethod
    def _is_github_url(url: str) -> bool:
        """Return True for github.com URLs (supports ZIP archive API)."""
        return is_github_url(url)

    async def _github_zip_download(
        self,
        repo_url: str,
        branch: Optional[str],
        target_dir: str,
    ) -> Tuple[Path, str]:
        """Download a GitHub repo as a ZIP archive and extract it.

        Uses the GitHub archive API (single HTTPS GET, no git history).

        Returns:
            (content_dir, repo_name) — content_dir is the extracted repo root.
        """
        repo_name = self._get_repo_name(repo_url)

        # Build archive URL from owner/repo path components.
        parsed = urlparse(repo_url)
        path_parts = [p for p in parsed.path.split("/") if p]
        owner = path_parts[0]
        repo_raw = path_parts[1]
        # Strip .git suffix for the archive URL (git clone keeps it, ZIP API does not).
        repo_slug = repo_raw[:-4] if repo_raw.endswith(".git") else repo_raw

        if branch:
            zip_url = f"https://github.com/{owner}/{repo_slug}/archive/{branch}.zip"
        else:
            zip_url = f"https://github.com/{owner}/{repo_slug}/archive/HEAD.zip"

        logger.info(f"Downloading GitHub ZIP: {zip_url}")

        zip_path = os.path.join(target_dir, "_archive.zip")
        extract_dir = os.path.join(target_dir, "_extracted")
        os.makedirs(extract_dir, exist_ok=True)

        # Download (blocking HTTP; run in thread pool to avoid stalling event loop).
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

        # Safe extraction with Zip Slip validation (mirrors _extract_zip logic).
        target = Path(extract_dir).resolve()
        with zipfile.ZipFile(zip_path, "r") as zf:
            for info in zf.infolist():
                mode = info.external_attr >> 16
                if info.is_dir() or stat.S_ISDIR(mode):
                    continue
                if stat.S_ISLNK(mode):
                    logger.warning(f"Skipping symlink entry in GitHub ZIP: {info.filename}")
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

        # Remove downloaded archive to free disk space.
        try:
            os.unlink(zip_path)
        except OSError:
            pass

        # GitHub ZIPs have a single top-level directory: {repo}-{branch}/ or {repo}-{sha}/.
        # Return that directory as the content root so callers see bare repo files.
        top_level = [d for d in Path(extract_dir).iterdir() if d.is_dir()]
        content_dir = top_level[0] if len(top_level) == 1 else Path(extract_dir)

        logger.info(f"GitHub ZIP extracted to {content_dir} ({repo_name})")
        return content_dir, repo_name

    async def _git_clone(
        self,
        url: str,
        target_dir: str,
        branch: Optional[str] = None,
        commit: Optional[str] = None,
    ) -> str:
        """Clone a git repository into target_dir; return the repo name.

        Uses --depth 1 for speed. If a specific commit is requested, it is
        fetched and checked out after the shallow clone.

        Returns:
            Repository name derived from the URL (e.g. "OpenViking").
        """
        name = self._get_repo_name(url)
        logger.info(f"Cloning {url} to {target_dir}...")

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

        return name

    async def _extract_zip(self, zip_path: str, target_dir: str) -> str:
        """Extract a local zip file into target_dir; return the archive stem as the repo name."""
        if zip_path.startswith(("http://", "https://")):
            # TODO: implement download logic or rely on caller?
            # For now, assume it's implemented if needed, but raise error as strictly we only support git URL for now as per plan
            raise NotImplementedError(
                "Zip URL download not yet implemented in CodeRepositoryParser"
            )

        path = Path(zip_path)
        name = path.stem

        with zipfile.ZipFile(zip_path, "r") as zip_ref:
            target = Path(target_dir).resolve()
            for info in zip_ref.infolist():
                mode = info.external_attr >> 16
                # Skip directory entries (check both name convention and external attrs)
                if info.is_dir() or stat.S_ISDIR(mode):
                    continue
                # Skip symlink entries to prevent symlink-based escapes
                if stat.S_ISLNK(mode):
                    logger.warning(f"Skipping symlink entry in zip: {info.filename}")
                    continue
                # Reject entries with suspicious raw path components before extraction
                raw = info.filename.replace("\\", "/")
                raw_parts = [p for p in raw.split("/") if p]
                if ".." in raw_parts:
                    raise ValueError(f"Zip Slip detected: entry {info.filename!r} contains '..'")
                if PurePosixPath(raw).is_absolute() or (len(raw) >= 2 and raw[1] == ":"):
                    raise ValueError(
                        f"Zip Slip detected: entry {info.filename!r} is an absolute path"
                    )
                # Normalize the member name the same way zipfile does
                # (strip drive/UNC, remove empty/"."/ ".." components) then verify
                arcname = info.filename.replace("/", os.sep)
                if os.path.altsep:
                    arcname = arcname.replace(os.path.altsep, os.sep)
                arcname = os.path.splitdrive(arcname)[1]
                arcname = os.sep.join(p for p in arcname.split(os.sep) if p not in ("", ".", ".."))
                if not arcname:
                    continue  # entry normalizes to empty path, skip
                member_path = (Path(target_dir) / arcname).resolve()
                if not member_path.is_relative_to(target):
                    raise ValueError(
                        f"Zip Slip detected: entry {info.filename!r} escapes target directory"
                    )
                # Extract single member and verify the actual path on disk
                extracted = Path(zip_ref.extract(info, target_dir)).resolve()
                if not extracted.is_relative_to(target):
                    # Best-effort cleanup of the escaped file
                    try:
                        extracted.unlink(missing_ok=True)
                    except OSError as cleanup_err:
                        logger.warning(
                            f"Failed to clean up escaped file {extracted}: {cleanup_err}"
                        )
                    raise ValueError(
                        f"Zip Slip detected: entry {info.filename!r} escapes target directory"
                    )

        return name

    async def _upload_directory(self, local_dir: Path, viking_uri_base: str, viking_fs: Any) -> int:
        """Recursively upload directory to VikingFS using shared upload utilities."""
        count, _ = await upload_directory(local_dir, viking_uri_base, viking_fs)
        return count
