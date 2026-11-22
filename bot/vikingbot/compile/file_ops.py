"""File boundary checks and target reads for Markdown publication."""

from __future__ import annotations

import base64
import json
import re
from collections import Counter
from typing import TYPE_CHECKING

import yaml
from markdown_it import MarkdownIt

from openviking.core.skill_loader import validate_skill_format
from openviking.storage.manual_conflicts import read_manual_conflict_records
from openviking.utils.path_safety import safe_join_viking_uri
from openviking_cli.exceptions import InvalidArgumentError, OpenVikingError
from vikingbot.compile.renderer import strip_okf_frontmatter, validate_relative_file_path

if TYPE_CHECKING:
    from vikingbot.compile.pipeline import Pipeline


def encode_content(value: str | bytes) -> dict:
    """Snapshot text or binary bytes for JSON storage; only UTF-8 bodies become text."""
    if isinstance(value, bytes):
        try:
            value = value.decode("utf-8")
        except UnicodeDecodeError:
            return {"content": None, "content_base64": base64.b64encode(value).decode("ascii")}
    return {"content": value, "content_base64": None}


def file_bytes(file: dict) -> bytes:
    """Restore exactly one text or Base64 body, rejecting missing or ambiguous content."""
    if file is None:
        raise ValueError("Missing stored file")
    text, encoded = file.get("content"), file.get("content_base64")
    if (text is None) == (encoded is None):
        raise ValueError("A file requires exactly one text or binary body")
    return text.encode("utf-8") if text is not None else base64.b64decode(encoded, validate=True)


async def load_old(runtime: Pipeline, path: str) -> str | bytes | None:
    """Read a selected target body once in this execution; only NOT_FOUND means absence."""
    path = validate_relative_file_path(path)
    if path not in runtime.old:
        uri = safe_join_viking_uri(runtime.target, path)
        try:
            entry = await runtime.client.stat(uri)
            if entry.get("isDir"):
                raise ValueError("Selected old target must be a file")
            payload = await runtime.client.download_bytes(uri)
            try:
                runtime.old[path] = payload.decode("utf-8")
            except UnicodeDecodeError:
                runtime.old[path] = payload
            runtime.metrics["history_body_reads"] += 1
        except OpenVikingError as exc:
            if exc.code != "NOT_FOUND":
                raise
            runtime.old[path] = None
    return runtime.old[path]


def validate_wiki_conflict_update(previous: str | bytes | None, candidate: str) -> None:
    """Require existing human decisions and their exact anchors to survive a write."""
    if isinstance(previous, bytes):
        raise ValueError("Wiki conflict history must be readable UTF-8")
    previous = previous or ""
    try:
        old_records = read_manual_conflict_records(previous)
        new_records = read_manual_conflict_records(candidate)
    except InvalidArgumentError as exc:
        raise ValueError(f"Invalid Wiki conflict history: {exc}") from exc
    if Counter(json.dumps(record, sort_keys=True) for record in old_records) != Counter(
        json.dumps(record, sort_keys=True) for record in new_records
    ):
        raise ValueError("Wiki conflict output must preserve the existing human resolutions")
    if not old_records:
        return
    try:
        bodies = [
            strip_okf_frontmatter(text.removeprefix("\ufeff")) for text in (previous, candidate)
        ]
    except (ValueError, yaml.YAMLError) as exc:
        raise ValueError("Cannot read the Markdown body for human conflict anchors") from exc
    for body in bodies:
        # The Wiki reader only exposes records in trailing comments, outside code examples.
        trailer = []
        for block in reversed(MarkdownIt().parse(body)):
            if (
                block.type != "html_block"
                or not re.match(r"\s*<!--\s*CONFLICTS_CONTENT\b", block.content)
                or not block.content.rstrip().endswith("-->")
            ):
                break
            trailer.append(block.content)
        if len(read_manual_conflict_records("\n".join(reversed(trailer)))) != len(old_records):
            raise ValueError("Human conflict resolutions must remain in trailing Markdown comments")
    visible = [re.sub(r"<!--.*?(?:-->|\Z)", "\x00", body, flags=re.DOTALL) for body in bodies]
    for record in old_records:
        anchor = record.get("match_text")
        if record["state"] != "fixed" or not isinstance(anchor, str) or not anchor.strip():
            raise ValueError("Invalid human conflict resolution in history")
        if any(anchor not in body for body in visible):
            raise ValueError("Human conflict resolution anchor is missing from the Markdown body")


def validate_skill_output(files: dict[str, str | None]) -> None:
    """Check one Skill directory and its SKILL.md using the shared format validator.

    Paths are already checked by file validation. Raise ValueError for structural
    errors; content quality and task-specific attachments remain the Skill's concern.
    """
    directories = {path.split("/")[0] for path in files}
    if len(directories) != 1 or any("/" not in path for path in files):
        raise ValueError("Output must contain one <skill-name>/ directory with SKILL.md")
    name = directories.pop()
    main = f"{name}/SKILL.md"
    if main not in files:
        raise ValueError(f"Missing {main}")
    if files[main] is None:
        raise ValueError("SKILL.md must contain UTF-8 text")
    result = validate_skill_format(files[main], strict=True, skill_dir_name=name, source_path=main)
    if not result["valid"]:
        raise ValueError("; ".join(issue["message"] for issue in result["errors"]))


class DocumentReader:
    """Expose only selected draft files in bounded character windows for routing and review."""

    name = "read_document"
    description = (
        "Read an assigned Markdown draft by its workspace path; follow end for continuation."
    )
    parameters = {
        "type": "object",
        "properties": {
            "path": {"type": "string"},
            "offset": {"type": "integer", "minimum": 0},
            "limit": {"type": "integer", "minimum": 1, "maximum": 8000},
        },
        "required": ["path"],
    }

    def __init__(self, runtime, documents):
        self.runtime = runtime
        self.allowed = {d.workspace_path for d in documents}

    async def execute(self, path, offset=0, limit=8000):
        """Read a permitted draft; reject unknown paths and invalid character ranges."""
        if path not in self.allowed:
            raise ValueError("Document is outside this assignment")
        if (
            type(offset) is not int
            or offset < 0
            or type(limit) is not int
            or not 1 <= limit <= 8000
        ):
            raise ValueError("Invalid Markdown character range")
        content = (await self.runtime.files.sandbox.read_file_bytes(path)).decode("utf-8")
        if offset > len(content):
            raise ValueError("Offset is outside the document")
        end = min(len(content), offset + limit)
        return json.dumps(
            {
                "path": path,
                "text": content[offset:end],
                "end": end,
                "total_chars": len(content),
                "complete": offset == 0 and end == len(content),
            },
            ensure_ascii=False,
        )
