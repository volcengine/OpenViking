# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
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
import tempfile
import time
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
        Parse code repository.

        Args:
            source: Repository URL (git/http) or local zip path
            instruction: Processing instruction (unused in parser phase)
            **kwargs: Additional arguments

        Returns:
            ParseResult with temp_dir_path pointing to the uploaded content
        """
        start_time = time.time()
        source_str = str(source)
        temp_local_dir = None
        branch = None
        commit = None

        try:
            # 1. Prepare local temp directory
            temp_local_dir = tempfile.mkdtemp(prefix="ov_repo_")
            logger.info(f"Created local temp dir: {temp_local_dir}")

            # 2. Fetch content (Clone or Extract)
            repo_name = "repository"
            if source_str.startswith(("http://", "https://", "git://", "ssh://")):
                repo_url, branch, commit = self._parse_repo_source(source_str, **kwargs)
                repo_name = await self._git_clone(
                    repo_url,
                    temp_local_dir,
                    branch=branch,
                    commit=commit,
                )
            elif str(source).endswith(".zip"):
                repo_name = await self._extract_zip(source_str, temp_local_dir)
            else:
                raise ValueError(f"Unsupported source for CodeRepositoryParser: {source}")

            # 3. Create VikingFS temp URI
            viking_fs = self._get_viking_fs()
            temp_viking_uri = self._create_temp_uri()
            # The structure in temp should be: viking://temp/{uuid}/{repo_name}/...
            target_root_uri = f"{temp_viking_uri}/{repo_name}"

            logger.info(f"Uploading to VikingFS: {target_root_uri}")

            # 4. Upload to VikingFS (filtering on the fly)
            file_count = await self._upload_directory(
                Path(temp_local_dir), target_root_uri, viking_fs
            )

            logger.info(f"Uploaded {file_count} files to {target_root_uri}")

            # 5. Create result
            # Root node is just a placeholder, TreeBuilder relies on temp_dir_path
            root = ResourceNode(
                type=NodeType.ROOT,
                content_path=None,
                meta={"name": repo_name, "type": "repository"},
            )

            result = create_parse_result(
                root=root,
                source_path=source_str,
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
            return create_parse_result(
                root=ResourceNode(type=NodeType.ROOT, content_path=None),
                source_path=source_str,
                source_format="repository",
                parser_name="CodeRepositoryParser",
                parse_time=time.time() - start_time,
                warnings=[f"Failed to parse repository: {str(e)}"],
            )

        finally:
            # Cleanup local temp dir
            if temp_local_dir and os.path.exists(temp_local_dir):
                try:
                    shutil.rmtree(temp_local_dir)
                    logger.debug(f"Cleaned up local temp dir: {temp_local_dir}")
                except Exception as e:
                    logger.warning(f"Failed to cleanup local temp dir {temp_local_dir}: {e}")

    async def parse_content(
        self, content: str, source_path: Optional[str] = None, instruction: str = "", **kwargs
    ) -> ParseResult:
        """Not supported for repositories."""
        raise NotImplementedError("CodeRepositoryParser does not support parse_content")

    def _parse_repo_source(
        self, source: str, **kwargs
    ) -> Tuple[str, Optional[str], Optional[str]]:
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
                branch = unquote(parts[idx + 1])
        return branch, commit

    def _normalize_repo_url(self, url: str) -> str:
        if url.startswith(("http://", "https://", "git://", "ssh://")):
            parsed = urlparse(url)
            path_parts = [p for p in parsed.path.split("/") if p]
            base_parts = path_parts
            git_index = next((i for i, p in enumerate(path_parts) if p.endswith(".git")), None)
            if git_index is not None:
                base_parts = path_parts[: git_index + 1]
            elif parsed.netloc in ["github.com", "gitlab.com"] and len(path_parts) >= 2:
                base_parts = path_parts[:2]
            base_path = "/" + "/".join(base_parts)
            return parsed._replace(path=base_path, query="", fragment="").geturl()
        return url

    def _get_repo_name(self, url: str) -> str:
        name_source = url
        if url.startswith(("http://", "https://", "git://", "ssh://")):
            name_source = urlparse(url).path.rstrip("/")
        elif ":" in url and not url.startswith("file://"):
            name_source = url.split(":", 1)[1]
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
            raise RuntimeError(f"Git command failed: {' '.join(args)}: {error_msg}")
        return stdout.decode().strip()

    async def _has_commit(self, repo_dir: str, commit: str) -> bool:
        try:
            await self._run_git(["git", "-C", repo_dir, "rev-parse", "--verify", commit])
            return True
        except RuntimeError:
            return False

    async def _git_clone(
        self,
        url: str,
        target_dir: str,
        branch: Optional[str] = None,
        commit: Optional[str] = None,
    ) -> str:
        """
        Clone git repository.

        Returns:
            Repository name (e.g. "OpenViking" from "https://.../OpenViking.git")
        """
        # Extract repo name from URL
        name = self._get_repo_name(url)

        # Clone into a subdirectory to keep structure clean
        # But here we clone content directly into target_dir?
        # Actually, git clone <url> <dir> clones INTO <dir>.
        # But if we want the repo name directory to exist in VikingFS, we should clone into target_dir/name?
        # No, parse logic says:
        # temp_local_dir contains the files (e.g. .git, src, README)
        # We upload temp_local_dir content to viking://temp/{uuid}/{repo_name}/

        # So we clone current content directly into temp_local_dir
        # git clone --depth 1 url target_dir

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
                    await self._run_git(["git", "-C", target_dir, "fetch", "--all", "--tags", "--prune"])
                except RuntimeError:
                    pass
                ok = await self._has_commit(target_dir, commit)
                if not ok:
                    try:
                        await self._run_git(["git", "-C", target_dir, "fetch", "--unshallow", "origin"])
                    except RuntimeError:
                        pass
                ok = await self._has_commit(target_dir, commit)
                if not ok:
                    await self._run_git(
                        ["git", "-C", target_dir, "fetch", "origin", "+refs/heads/*:refs/remotes/origin/*"]
                    )
                    ok = await self._has_commit(target_dir, commit)
                    if not ok:
                        raise RuntimeError(f"Failed to fetch commit {commit} from {url}")
            await self._run_git(["git", "-C", target_dir, "checkout", commit])

        return name

    async def _extract_zip(self, zip_path: str, target_dir: str) -> str:
        """Extract zip file."""
        import zipfile

        # We assume it's a local path if passed here?
        # Actually logic in parse() handles local path check before calling here?
        # Or if it's a URL ending in zip, HTMLParser might have downloaded it?
        # Wait, HTMLParser handles download. If we are here, source IS a path or URL.
        # If it's a URL, we need to download it first?
        # CodeRepositoryParser is designed to handle "source" which can be URL.
        # So I need to download zip if it is a URL.

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
