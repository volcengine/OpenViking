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
import tempfile
import time
from pathlib import Path
from typing import Any, List, Optional, Union

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
from openviking.utils.logger import get_logger

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

        try:
            # 1. Prepare local temp directory
            temp_local_dir = tempfile.mkdtemp(prefix="ov_repo_")
            logger.info(f"Created local temp dir: {temp_local_dir}")

            # 2. Fetch content (Clone or Extract)
            repo_name = "repository"
            if source_str.startswith(("http://", "https://", "git://", "ssh://")):
                repo_name = await self._git_clone(source_str, temp_local_dir)
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

    async def _git_clone(self, url: str, target_dir: str) -> str:
        """
        Clone git repository.

        Returns:
            Repository name (e.g. "OpenViking" from "https://.../OpenViking.git")
        """
        # Extract repo name from URL
        clean_url = url.rstrip("/")
        if clean_url.endswith(".git"):
            name = clean_url.split("/")[-1][:-4]
        else:
            name = clean_url.split("/")[-1]

        # Sanitize name
        name = "".join(c if c.isalnum() or c in "-_" else "_" for c in name)
        if not name:
            name = "repository"

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

        proc = await asyncio.create_subprocess_exec(
            "git",
            "clone",
            "--depth",
            "1",
            "--recursive",  # Also clone submodules? Maybe risky for huge repos
            url,
            target_dir,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        stdout, stderr = await proc.communicate()

        if proc.returncode != 0:
            error_msg = stderr.decode()
            raise RuntimeError(f"Git clone failed: {error_msg}")

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
            zip_ref.extractall(target_dir)

        return name

    async def _upload_directory(self, local_dir: Path, viking_uri_base: str, viking_fs: Any) -> int:
        """Recursively upload directory to VikingFS using shared upload utilities."""
        count, _ = await upload_directory(local_dir, viking_uri_base, viking_fs)
        return count
