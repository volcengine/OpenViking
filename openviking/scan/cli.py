# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""``openviking-server scan``: report what a local directory contains.

Read-only and server-free: it walks a directory and says, per file, whether the
server can parse it, whether the upload UI would refuse it, and whether it is too
large for a single upload. Nothing is written to VikingFS and no config is read.

Why a subcommand instead of a standalone script: a script has to *copy* the
admission rules, and the copies drift. Measured on a real corpus, the previous
standalone script's mirrored list was wrong on both sides -- it missed files the
server accepts and kept files the server rejects. Here nothing is copied -- the
classification is imported:

  ``ParserRegistry`` / ``is_text_file``   ->  ``openviking.parse.directory_scan``
  blocked extensions, size and batch caps ->  the frontend module that owns them
"""

from __future__ import annotations

import json
import logging
import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Union

import typer

from openviking.parse.directory_scan import (
    CLASS_PROCESSABLE,
    DirectoryScanResult,
    scan_directory,
)
from openviking.parse.registry import ParserRegistry, get_registry

app = typer.Typer(
    add_completion=False,
    help="Report what a local directory contains and what of it can be imported.",
)

VERDICT_IMPORTABLE = "importable"
VERDICT_UNSUPPORTED = "unsupported"
VERDICT_UPLOAD_BLOCKED = "upload_blocked"
VERDICT_TOO_LARGE = "too_large"
VERDICT_ORDER = (
    VERDICT_IMPORTABLE,
    VERDICT_UNSUPPORTED,
    VERDICT_UPLOAD_BLOCKED,
    VERDICT_TOO_LARGE,
)

# The frontend module that owns the upload-layer limits. Read, never mirrored:
# a copy here is a copy that goes stale.
_UPLOAD_TS = Path(__file__).resolve().parents[2] / "web-studio/src/routes/resources/-lib/upload.ts"
_BLOCKED_SET_RE = re.compile(r"BLOCKED_EXTENSIONS\s*=\s*new Set\(\[(.*?)\]\)", re.S)
_QUOTED_RE = re.compile(r"'([^']*)'")
_MAX_FILES_RE = re.compile(r"\bMAX_UPLOAD_FILES\s*=\s*([\d\s*]+)")
_MAX_SIZE_RE = re.compile(r"\bMAX_UPLOAD_FILE_SIZE_BYTES\s*=\s*([\d\s*]+)")


@dataclass(frozen=True)
class UploadRules:
    """The upload layer's extra limits, owned by the frontend."""

    blocked_extensions: frozenset
    max_files: int
    max_file_size_bytes: int


def _int_product(expr: str) -> int:
    """Evaluate a ``10 * 1024 * 1024`` style integer literal."""
    product = 1
    for factor in expr.split("*"):
        product *= int(factor.strip())
    return product


def load_upload_rules(source: Optional[Path] = None) -> Optional[UploadRules]:
    """Read the upload limits out of the frontend module that owns them.

    Returns None when that module is not on disk (an installed wheel without the
    ``web-studio`` sources, or a moved file). The caller then reports the
    server-side verdicts alone instead of inventing a limit.

    A module that *is* there but no longer parses raises: that is this reader's
    assumptions going stale, and reporting it as "limits unknown" would look
    exactly like the missing-file case it must not be confused with.
    """
    path = source if source is not None else _UPLOAD_TS
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise RuntimeError(f"cannot read {path}: {exc}") from exc

    blocked = _BLOCKED_SET_RE.search(text)
    max_files = _MAX_FILES_RE.search(text)
    max_size = _MAX_SIZE_RE.search(text)
    if blocked is None or max_files is None or max_size is None:
        raise RuntimeError(
            f"{path}: no BLOCKED_EXTENSIONS / MAX_UPLOAD_FILES /"
            " MAX_UPLOAD_FILE_SIZE_BYTES found -- the module changed shape"
        )
    return UploadRules(
        blocked_extensions=frozenset(_QUOTED_RE.findall(blocked.group(1))),
        max_files=_int_product(max_files.group(1)),
        max_file_size_bytes=_int_product(max_size.group(1)),
    )


def extension_of(name: str) -> str:
    """Extension as the frontend reads it: last dot, and only past position 0.

    ``isBlockedFile`` in upload.ts returns false when the dot sits at index 0, so
    a file literally named ``.7z`` has no extension there either.
    """
    dot = name.rfind(".")
    return name[dot:].lower() if dot > 0 else ""


def _basename(rel_path: str) -> str:
    return rel_path.rsplit("/", 1)[-1]


def classify(ext: str, size: int, server_accepts: bool, rules: Optional[UploadRules]) -> str:
    """One file's verdict, in the order the upload UI itself applies them.

    A blocked extension outranks the server's answer: the UI refuses the file
    before the server ever sees it, so ``upload_blocked`` is what would happen to
    it. The size cap is checked last, which means ``too_large`` reads as
    "importable, but not in one request". Files the server rejects and the UI does
    not block (``notes.xyz``) stay ``unsupported`` -- the parser-level answer,
    which is the distinction this report exists to keep.
    """
    if rules is not None and ext in rules.blocked_extensions:
        return VERDICT_UPLOAD_BLOCKED
    if not server_accepts:
        return VERDICT_UNSUPPORTED
    if rules is not None and size > rules.max_file_size_bytes:
        return VERDICT_TOO_LARGE
    return VERDICT_IMPORTABLE


@dataclass(frozen=True)
class ScannedFile:
    """A file the server's walk kept, with its verdict."""

    rel_path: str
    ext: str
    size: int
    verdict: str


@dataclass
class ScanReport:
    """Everything one scan produced: kept files, skipped paths, rules in force."""

    root: Path
    rules: Optional[UploadRules] = None
    files: List[ScannedFile] = field(default_factory=list)
    skipped: List[str] = field(default_factory=list)

    @property
    def counts(self) -> Dict[str, int]:
        counts = Counter(row.verdict for row in self.files)
        return {verdict: counts[verdict] for verdict in VERDICT_ORDER}

    @property
    def total_bytes(self) -> int:
        return sum(row.size for row in self.files if row.size > 0)


def _size_of(path: Path) -> int:
    """Byte size, or -1 when stat fails (the JSONL keeps -1 as "unreadable")."""
    try:
        return path.stat().st_size
    except OSError:
        return -1


def build_report(
    root: Union[str, Path],
    registry: Optional[ParserRegistry] = None,
) -> ScanReport:
    """Walk ``root`` with the server's own rules and classify every file it keeps."""
    effective_registry = registry if registry is not None else get_registry()
    rules = load_upload_rules()
    # scan_directory logs the unsupported files it found; this command reports
    # exactly that, in full, so the warning would only be a truncation of it.
    # Quiet it for the call only -- this is a library logger, not ours to keep.
    scan_logger = logging.getLogger("openviking.parse.directory_scan")
    level = scan_logger.level
    scan_logger.setLevel(logging.ERROR)
    try:
        scanned: DirectoryScanResult = scan_directory(root, registry=effective_registry)
    finally:
        scan_logger.setLevel(level)

    report = ScanReport(root=Path(root), rules=rules, skipped=list(scanned.skipped))
    for classified in (*scanned.processable, *scanned.unsupported):
        size = _size_of(classified.path)
        ext = extension_of(_basename(classified.rel_path))
        report.files.append(
            ScannedFile(
                rel_path=classified.rel_path,
                ext=ext,
                size=size,
                verdict=classify(
                    ext,
                    size,
                    server_accepts=classified.classification == CLASS_PROCESSABLE,
                    rules=rules,
                ),
            )
        )
    report.files.sort(key=lambda row: row.rel_path)
    return report


def _human_bytes(size: int) -> str:
    if size < 1024:
        return f"{size} B"
    if size < 1024 * 1024:
        return f"{size / 1024:.1f} KB"
    if size < 1024 * 1024 * 1024:
        return f"{size / 1024 / 1024:.1f} MB"
    return f"{size / 1024 / 1024 / 1024:.1f} GB"


def write_jsonl(path: Path, report: ScanReport) -> None:
    """Write one JSON object per file, in the shape the corpus tooling expects."""
    with path.open("w", encoding="utf-8") as handle:
        for row in report.files:
            handle.write(
                json.dumps(
                    {
                        "path": row.rel_path,
                        "ext": row.ext,
                        "size": row.size,
                        "verdict": row.verdict,
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )


def _render(report: ScanReport) -> None:
    counts = report.counts
    rules = report.rules
    typer.echo(f"\n{report.root}")
    typer.echo(f"  {len(report.files)} files, {_human_bytes(report.total_bytes)}")
    for verdict in VERDICT_ORDER:
        if counts[verdict]:
            typer.echo(f"    {verdict:<14} {counts[verdict]:>7}")
    if report.skipped:
        typer.echo(
            f"    {'skipped':<14} {len(report.skipped):>7}"
            "  (dot files, empty files, symlinks, gitignore, ignore dirs)"
        )

    unsupported = Counter(
        row.ext or "(no extension)" for row in report.files if row.verdict == VERDICT_UNSUPPORTED
    )
    if unsupported:
        # "unsupported" here is about format: no parser and no text path reads it.
        shown = ", ".join(f"{ext}×{count}" for ext, count in unsupported.most_common(12))
        typer.echo(f"    unsupported formats (no parser reads them): {shown}")

    if rules is None:
        typer.echo(
            "    upload limits unknown: web-studio/src/routes/resources/-lib/upload.ts"
            " not found; server verdicts only"
        )
        return

    ceiling = (
        f"    upload ceiling: {rules.max_files} files per request,"
        f" {_human_bytes(rules.max_file_size_bytes)} each"
    )
    overflow = counts[VERDICT_IMPORTABLE] - rules.max_files
    if overflow > 0:
        ceiling += f" -- {overflow} importable files would not fit in one request"
    typer.echo(ceiling)


@app.command("scan")
def scan_command(
    path: Path = typer.Argument(
        ..., help="Directory to report on. Read-only: no server and no config needed."
    ),
    json_out: Optional[Path] = typer.Option(
        None, "--json", help="Also write one JSON object per file (JSONL)."
    ),
) -> None:
    """Report what a local directory contains and what of it can be imported."""
    try:
        report = build_report(path)
    except (OSError, RuntimeError) as exc:  # bad path, or unreadable upload rules
        typer.echo(f"error: {exc}")
        raise typer.Exit(1) from exc

    _render(report)
    if json_out is not None:
        write_jsonl(json_out, report)
        typer.echo(f"    jsonl: {json_out} ({len(report.files)} rows)")


if __name__ == "__main__":
    app()
