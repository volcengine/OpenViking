"""Validate conflict records emitted by the opt-in VikingBot workers."""

import json
import re

from openviking.storage.manual_conflicts import (
    _CONFLICT_BLOCK,
    _MANUAL_RESOLUTION,
    _parse_conflict_records,
)
from openviking_cli.exceptions import InvalidArgumentError


def validate_compile_conflicts(
    edited: str, *, previous: str | None = None, history: tuple[str, ...] = ()
) -> None:
    """Validate opt-in Compile output while preserving authoritative human decisions.

    This checks the shared trailer and protected visible text, not factual truth or
    retrieval coverage. Unchanged historical files remain byte-for-byte valid even
    when they predate the current automatic-record schema.
    """
    from collections import Counter

    from markdown_it import MarkdownIt

    if edited == previous:
        return
    fields = {
        "match_text",
        "problem_description",
        "reason",
        "severity",
        "state",
        "begin_time",
        "fix_plan",
        "fix_time",
        "resolution_basis",
        "conflict_type",
        "resolved_by",
    }

    def parse(content: str | None, *, required: bool = False):
        if not content:
            if required:
                raise ValueError(
                    "Keep one trailing CONFLICTS_CONTENT comment, empty if no conflicts"
                )
            return [], ""
        # Frontmatter, code examples and comment payloads are not prose anchors.
        body = re.sub(r"\A---\s*\n.*?\n---\s*\n", "", content, count=1, flags=re.S)
        tokens = MarkdownIt("commonmark").parse(body)
        comments = [
            child.content.lstrip()
            for token in tokens
            for child in ([token] if token.type == "html_block" else token.children or [])
            if child.type in {"html_block", "html_inline"}
            and re.match(r"<!--\s*CONFLICTS_CONTENT\b", child.content.lstrip())
        ]
        if not comments:
            if required:
                raise ValueError(
                    "Keep one trailing CONFLICTS_CONTENT comment, empty if no conflicts"
                )
            return [], visible(tokens)
        if len(comments) != 1:
            raise ValueError("Keep exactly one conflict trailer")
        block = _CONFLICT_BLOCK.fullmatch(comments[0].strip())
        if block is None or not body.rstrip().endswith(block.group()):
            raise ValueError("Conflict metadata must be one closed trailing comment")
        if "--" in block.group(1):
            raise ValueError("Unicode-escape consecutive hyphens inside conflict metadata")
        try:
            records = _parse_conflict_records(block.group(1))
        except InvalidArgumentError as exc:
            raise ValueError("Unreadable conflict history; retain the affected page") from exc
        return records, visible(tokens)

    def visible(tokens):
        return "\n".join(
            "".join(
                child.content
                if child.type in {"text", "code_inline"}
                else "\n"
                if child.type in {"softbreak", "hardbreak"}
                else ""
                for child in token.children or []
            )
            for token in tokens
            if token.type == "inline"
        )

    def identity(record):
        return json.dumps(record, sort_keys=True, ensure_ascii=False, allow_nan=False)

    def human(records):
        return [record for record in records if record.get("fix_plan") == _MANUAL_RESOLUTION]

    old, old_text = parse(previous)
    records, text = parse(edited, required=True)
    protected = human(old)
    allowed = list(protected)
    for content in history:
        try:
            historical, prose = parse(content)
        except ValueError:
            continue  # Unreadable unrelated history cannot grant human-record authority.
        allowed.extend(
            record
            for record in human(historical)
            if isinstance(record.get("match_text"), str) and record["match_text"] in prose
        )
    for record in protected:
        anchor = record.get("match_text")
        if (
            not isinstance(anchor, str)
            or not anchor
            or anchor not in old_text
            or anchor not in text
        ):
            raise ValueError("Retain the protected human statement and its original anchor")
    if Counter(identity(record) for record in protected) - Counter(
        identity(record) for record in human(records)
    ):
        raise ValueError("Keep every original human resolution record unchanged")
    for record in records:
        anchor = record.get("match_text")
        if not isinstance(anchor, str) or not anchor or anchor not in text:
            raise ValueError("Conflict match_text must occur verbatim in visible page prose")
        if record.get("fix_plan") == _MANUAL_RESOLUTION:
            if record not in allowed:
                raise ValueError("Do not invent or modify a human resolution")
            continue
        if set(record) != fields:
            raise ValueError(
                "New automatic records require exactly the eleven Skill conflict fields"
            )
        if any(
            not isinstance(record[k], str) or not record[k].strip()
            for k in ("problem_description", "reason")
        ):
            raise ValueError("Conflict description and evidence reason must be nonempty")
        if record["severity"] not in {"high", "medium", "low"} or record["conflict_type"] not in {
            "fact",
            "opinion",
            "human_conclusion",
        }:
            raise ValueError("Invalid conflict severity or type")
        if type(record["begin_time"]) is not int or record["begin_time"] < 0:
            raise ValueError("Conflict begin_time must be nonnegative integer Unix seconds")
        if record["state"] == "unfixed":
            if record["fix_time"] is not None or any(
                record[k] != "" for k in ("fix_plan", "resolution_basis", "resolved_by")
            ):
                raise ValueError("Unresolved conflict repair fields must remain empty")
        elif record["state"] == "fixed":
            if (
                type(record["fix_time"]) is not int
                or record["fix_time"] < record["begin_time"]
                or record["resolved_by"] != "compile_agent"
                or any(
                    not isinstance(record[k], str) or not record[k].strip()
                    for k in ("fix_plan", "resolution_basis")
                )
            ):
                raise ValueError("Automatic resolution requires a valid time, action and evidence")
        else:
            raise ValueError("Conflict state must be fixed or unfixed")
        identity(record)  # Reject non-JSON constants even inside additional descriptive values.
