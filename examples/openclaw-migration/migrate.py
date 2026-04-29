#!/usr/bin/env python3
# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""
OpenClaw → OpenViking Memory Migration Tool

Imports plain-Markdown memory files from an OpenClaw workspace directly into
OpenViking's memory system using MemoryExtractor.create_memory() +
SessionCompressor._index_memory().  Zero LLM calls — only file reads,
memory writes, and embedding jobs enqueued.

Usage:
    python migrate.py [OPTIONS]

Examples:
    # Dry run — preview without writing
    python migrate.py --dry-run

    # Real migration with defaults
    python migrate.py

    # Custom paths
    python migrate.py \\
        --openclaw-dir ~/myworkspace \\
        --ov-data-dir ./ov-data \\
        --user-id myuser
"""

from __future__ import annotations

import asyncio
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import List, NamedTuple, Optional

# ---------------------------------------------------------------------------
# CLI argument parsing (stdlib only — no extra deps)
# ---------------------------------------------------------------------------

import argparse


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Migrate OpenClaw memory files into OpenViking.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--openclaw-dir",
        default=str(Path.home() / ".openclaw" / "workspace"),
        help="Path to OpenClaw workspace (default: ~/.openclaw/workspace)",
    )
    parser.add_argument(
        "--ov-data-dir",
        default="./data",
        help="OpenViking data directory (default: ./data)",
    )
    parser.add_argument(
        "--account-id",
        default="default",
        help="Account ID (default: default)",
    )
    parser.add_argument(
        "--user-id",
        default="default",
        help="User ID (default: default)",
    )
    parser.add_argument(
        "--agent-id",
        default="default",
        help="Agent ID (default: default)",
    )
    parser.add_argument(
        "--category",
        default=None,
        choices=["entities", "events", "cases", "preferences"],
        help="Override category for ALL files (skips auto-classification)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview what would be imported without writing anything",
    )
    return parser.parse_args()


# ---------------------------------------------------------------------------
# File classification
# ---------------------------------------------------------------------------

_DAILY_LOG_RE = re.compile(r"^\d{4}-\d{2}-\d{2}\.md$")
_SESSION_SUMMARY_RE = re.compile(r"^\d{4}-\d{2}-\d{2}-.+\.md$")


def classify_file(path: Path, category_override: Optional[str] = None) -> str:
    """Return the OpenViking MemoryCategory value for an OpenClaw file.

    Mapping:
      MEMORY.md / memory.md  → entities
      YYYY-MM-DD.md          → events
      YYYY-MM-DD-slug.md     → cases
      anything else          → entities  (safe fallback)

    A non-None *category_override* takes precedence over all rules.
    """
    if category_override is not None:
        return category_override

    name = path.name
    if name.lower() in ("memory.md",):
        return "entities"
    if _DAILY_LOG_RE.match(name):
        return "events"
    if _SESSION_SUMMARY_RE.match(name):
        return "cases"
    return "entities"


# ---------------------------------------------------------------------------
# Content helpers
# ---------------------------------------------------------------------------

def build_abstract(content: str) -> str:
    """Return a one-line abstract for a memory file.

    Strategy:
      1. First non-empty line, truncated to 100 chars.
      2. If every leading line is blank/whitespace, fall back to the first
         100 chars of the raw content (stripped).
    """
    for line in content.splitlines():
        stripped = line.strip()
        if stripped:
            return stripped[:100]
    # All lines blank — use raw content prefix
    return content.strip()[:100]


def build_overview(content: str) -> str:
    """Return a medium-detail overview (first 5 non-empty lines, max 500 chars)."""
    lines: List[str] = []
    for line in content.splitlines():
        if line.strip():
            lines.append(line)
        if len(lines) >= 5:
            break
    overview = "\n".join(lines)
    return overview[:500]


# ---------------------------------------------------------------------------
# File discovery
# ---------------------------------------------------------------------------

class MemFile(NamedTuple):
    path: Path
    category: str
    content: str


def discover_files(openclaw_dir: Path, category_override: Optional[str]) -> List[MemFile]:
    """Walk the OpenClaw workspace and return all importable .md files."""
    if not openclaw_dir.exists():
        return []

    candidates: List[MemFile] = []

    # Root-level MEMORY.md / memory.md
    for name in ("MEMORY.md", "memory.md"):
        p = openclaw_dir / name
        if p.is_file():
            content = p.read_text(encoding="utf-8", errors="replace")
            candidates.append(
                MemFile(
                    path=p,
                    category=classify_file(p, category_override),
                    content=content,
                )
            )

    # memory/ sub-directory
    mem_dir = openclaw_dir / "memory"
    if mem_dir.is_dir():
        for p in sorted(mem_dir.rglob("*.md")):
            if p.is_file():
                content = p.read_text(encoding="utf-8", errors="replace")
                candidates.append(
                    MemFile(
                        path=p,
                        category=classify_file(p, category_override),
                        content=content,
                    )
                )

    return candidates


# ---------------------------------------------------------------------------
# Dry-run display
# ---------------------------------------------------------------------------

def _display_name(file: MemFile, openclaw_dir: Path) -> str:
    try:
        return str(file.path.relative_to(openclaw_dir))
    except ValueError:
        return file.path.name


def print_dry_run(files: List[MemFile], openclaw_dir: Path) -> None:
    print()
    print("OpenClaw → OpenViking Migration (DRY RUN)")
    print(f"Found {len(files)} file(s) in {openclaw_dir}")
    print()

    if not files:
        print("  (no files found — nothing to import)")
        return

    col_w = max(len(_display_name(f, openclaw_dir)) for f in files)
    for f in files:
        name = _display_name(f, openclaw_dir)
        chars = len(f.content)
        print(f"  {name:<{col_w}}  →  {f.category:<12}  ({chars:,} chars)")

    total_chars = sum(len(f.content) for f in files)
    print()
    print(
        f"Would import: {len(files)} file(s) | 0 LLM calls | "
        f"~{len(files)} embedding job(s) queued | {total_chars:,} chars total"
    )
    print("Run without --dry-run to proceed.")
    print()


# ---------------------------------------------------------------------------
# Async migration core
# ---------------------------------------------------------------------------

async def _migrate_async(
    files: List[MemFile],
    openclaw_dir: Path,
    ov_data_dir: str,
    account_id: str,
    user_id: str,
    agent_id: str,
) -> None:
    """Initialize OpenViking and write each file as a memory."""

    # Late imports so the script can be imported without OV installed
    # (e.g. unit tests for classify_file / build_abstract).
    try:
        import openviking as ov
        from openviking.server.identity import RequestContext, Role
        from openviking.session.compressor import SessionCompressor
        from openviking.session.memory_extractor import (
            CandidateMemory,
            MemoryCategory,
            MemoryExtractor,
        )
        from openviking_cli.session.user_id import UserIdentifier
    except ImportError as exc:
        print(f"ERROR: Could not import OpenViking — is it installed? ({exc})")
        sys.exit(1)

    # -- Boot embedded OV ---------------------------------------------------
    print(f"Initializing OpenViking at {ov_data_dir!r} …")
    client = ov.OpenViking(path=ov_data_dir)
    client.initialize()
    print("  OpenViking initialized.")

    # -- Build identity & context -------------------------------------------
    user = UserIdentifier(account_id, user_id, agent_id)
    ctx = RequestContext(user=user, role=Role.ROOT)

    session_id = f"openclaw-migration-{int(datetime.now(timezone.utc).timestamp())}"

    # -- Wire compressor with the VikingDB already initialised by OV --------
    # Access path: SyncOpenViking → AsyncOpenViking._service (property) → OpenVikingService
    vikingdb = client._async_client._service.vikingdb_manager
    if vikingdb is None:
        print("ERROR: VikingDBManager not available after initialize(). Aborting.")
        sys.exit(1)

    extractor = MemoryExtractor()
    compressor = SessionCompressor(vikingdb=vikingdb)

    # -- Migrate each file ---------------------------------------------------
    ok = 0
    failed = 0

    for f in files:
        display = _display_name(f, openclaw_dir)
        try:
            category = MemoryCategory(f.category)
        except ValueError:
            print(f"  SKIP  {display}  (unknown category {f.category!r})")
            failed += 1
            continue

        candidate = CandidateMemory(
            category=category,
            abstract=build_abstract(f.content),
            overview=build_overview(f.content),
            content=f.content,
            source_session=session_id,
            user=str(user),
            language="auto",
        )

        memory = await extractor.create_memory(
            candidate, str(user._user_id), session_id, ctx
        )
        if memory is None:
            print(f"  FAIL  {display}  (create_memory returned None)")
            failed += 1
            continue

        indexed = await compressor._index_memory(memory, ctx, change_type="added")
        if indexed:
            print(f"  OK    {display}  →  {f.category}")
            ok += 1
        else:
            print(f"  WARN  {display}  (indexed={indexed})")
            ok += 1

    print()
    print(f"Done: {ok} imported, {failed} failed.")

    try:
        client.close()
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    args = _parse_args()

    openclaw_dir = Path(args.openclaw_dir).expanduser().resolve()
    files = discover_files(openclaw_dir, args.category)

    if args.dry_run:
        print_dry_run(files, openclaw_dir)
        return

    if not files:
        print(f"No .md files found in {openclaw_dir}. Nothing to import.")
        return

    asyncio.run(
        _migrate_async(
            files=files,
            openclaw_dir=openclaw_dir,
            ov_data_dir=args.ov_data_dir,
            account_id=args.account_id,
            user_id=args.user_id,
            agent_id=args.agent_id,
        )
    )


if __name__ == "__main__":
    main()
