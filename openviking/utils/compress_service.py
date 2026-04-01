# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Compress service for batch memory abstract reduction."""

from typing import Any, Dict, List

from openviking.server.identity import RequestContext
from openviking.storage.viking_fs import get_viking_fs
from openviking_cli.utils.logger import get_logger

logger = get_logger(__name__)


class CompressService:
    """Scan a directory and re-summarize memories with abstracts exceeding a target length."""

    def __init__(self, max_abstract_length: int = 128):
        self.max_abstract_length = max_abstract_length

    async def compress_directory(
        self,
        uri: str,
        ctx: RequestContext,
        dry_run: bool = False,
    ) -> Dict[str, Any]:
        """Scan directory for memories with verbose abstracts and re-summarize.

        Returns stats: files_scanned, files_compressed, estimated_tokens_saved.
        """
        viking_fs = get_viking_fs()
        if not viking_fs:
            return {"status": "error", "message": "VikingFS not available"}

        try:
            entries = await viking_fs.list_directory(uri, ctx=ctx)
        except Exception as e:
            logger.error("Failed to list directory %s: %s", uri, e)
            return {"status": "error", "message": str(e)}

        files_scanned = 0
        files_compressed = 0
        chars_saved = 0
        verbose_files: List[Dict[str, Any]] = []

        for entry in entries:
            entry_uri = entry.get("uri", "")
            if not entry_uri.endswith(".md"):
                continue
            files_scanned += 1

            abstract = entry.get("abstract", "")
            if len(abstract) <= self.max_abstract_length:
                continue

            excess = len(abstract) - self.max_abstract_length
            verbose_files.append(
                {
                    "uri": entry_uri,
                    "current_length": len(abstract),
                    "excess": excess,
                }
            )

            if not dry_run:
                try:
                    truncated = abstract[: self.max_abstract_length].rsplit(" ", 1)[0] + "..."
                    await viking_fs.write_metadata(entry_uri, {"abstract": truncated}, ctx=ctx)
                    files_compressed += 1
                    chars_saved += excess
                except Exception as e:
                    logger.warning("Failed to compress %s: %s", entry_uri, e)
            else:
                files_compressed += 1
                chars_saved += excess

        return {
            "status": "ok",
            "files_scanned": files_scanned,
            "files_compressed": files_compressed,
            "chars_saved": chars_saved,
            "dry_run": dry_run,
            "verbose_files": verbose_files[:20],
        }
