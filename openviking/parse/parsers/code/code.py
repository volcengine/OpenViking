# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Parse a repository already materialized by a source accessor."""

import time
from pathlib import Path
from typing import Any, List, Optional, Set, Tuple, Union

from openviking.observability.log_safety import safe_path_for_log, safe_uri_for_log
from openviking.parse.base import (
    NodeType,
    ParseResult,
    ResourceNode,
    create_parse_result,
)
from openviking.parse.parsers.base_parser import BaseParser
from openviking.parse.parsers.upload_utils import upload_directory
from openviking.utils import parse_code_hosting_url
from openviking_cli.utils.logger import get_logger

logger = get_logger(__name__)


class CodeRepositoryParser(BaseParser):
    """Write a local repository to the parse artifact store with its directory layout."""

    @property
    def supported_extensions(self) -> List[str]:
        # This parser is primarily invoked by URLTypeDetector, not by file extension
        return [".git", ".zip"]

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
        # Always work through a ParseOutputStore. Local mode threads one in via
        # kwargs; otherwise default to the AGFS-backed store so the parser has a
        # single, backend-agnostic write path (no viking_fs/store branching).
        output_store = kwargs.get("parse_output_store")
        if output_store is None:
            from openviking.parse.output import AgfsParseOutputStore

            output_store = AgfsParseOutputStore(viking_fs=self._get_viking_fs())
        artifact_ref = None

        # Check if source is already a local directory (should always be true)
        if not source_path.is_dir():
            raise ValueError(
                f"CodeRepositoryParser only accepts local directories. "
                f"Source type: {type(source)}, value: {source}"
            )

        logger.debug("Parsing code repository source=%s", safe_path_for_log(str(source_path)))

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
            # original_source is the full code-hosting URL that the user provided
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

            # 3. Allocate the artifact root through the store. Local mode writes
            # to a shared local dir; AGFS mode writes to viking://temp. Either way
            # the layout is <root>/repository/... and the parser code is identical.
            artifact_ref = await output_store.create_artifact(root_type="dir")
            temp_viking_uri = artifact_ref.root
            target_root_uri = f"{temp_viking_uri}/repository"

            logger.debug(
                "Uploading repository artifacts target=%s", safe_uri_for_log(target_root_uri)
            )

            # 4. Upload to the artifact store (filtering on the fly)
            file_count, upload_failures = await self._upload_directory(
                local_dir,
                output_store=output_store,
                artifact_ref=artifact_ref,
                ignore_dirs=kwargs.get("ignore_dirs"),
                include=kwargs.get("include"),
                exclude=kwargs.get("exclude"),
            )
            if upload_failures:
                detail = "; ".join(upload_failures[:5])
                if len(upload_failures) > 5:
                    detail += f"; ... {len(upload_failures) - 5} more"
                raise RuntimeError(
                    f"Failed to upload {len(upload_failures)} repository file(s): {detail}"
                )

            logger.debug(
                "Repository artifacts uploaded target=%s files=%d",
                safe_uri_for_log(target_root_uri),
                file_count,
            )

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
            #   3. If source_path is the original code-hosting URL,
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
            result.artifact_ref = artifact_ref
            result.meta["file_count"] = file_count
            result.meta["repo_name"] = repo_name
            if branch:
                result.meta["repo_ref"] = branch
            if commit:
                result.meta["repo_commit"] = commit
            resolved_head_sha = source_meta.get("resolved_head_sha")
            if resolved_head_sha:
                result.meta["resolved_head_sha"] = resolved_head_sha

            return result

        except Exception as e:
            logger.debug(
                "Repository parsing failed source=%s error_type=%s",
                safe_uri_for_log(str(source)),
                type(e).__name__,
            )
            if artifact_ref is not None:
                try:
                    await output_store.cleanup(artifact_ref)
                except Exception as cleanup_error:
                    logger.info(
                        "Repository artifact cleanup failed path=%s error_type=%s",
                        safe_path_for_log(str(artifact_ref.root)),
                        type(cleanup_error).__name__,
                    )
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

    async def _upload_directory(
        self,
        local_dir: Path,
        *,
        output_store: Any,
        artifact_ref: Any,
        ignore_dirs: Optional[Union[Set[str], List[str], str]] = None,
        include: Optional[str] = None,
        exclude: Optional[str] = None,
    ) -> Tuple[int, List[str]]:
        """Recursively upload the repository into the artifact store under repository/."""
        return await upload_directory(
            local_dir,
            "repository",
            store=output_store,
            artifact_ref=artifact_ref,
            ignore_dirs=ignore_dirs,
            include=include,
            exclude=exclude,
        )
