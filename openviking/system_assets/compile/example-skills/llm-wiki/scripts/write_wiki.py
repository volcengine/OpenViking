#!/usr/bin/env python3
"""Write Wiki Markdown through ov with tags derived from its conflict trailer."""

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

_MANUAL_FIX_PLAN = "用户手动解决内容冲突，修正后的内容如正文所示"
_JSON_WHITESPACE = re.compile(r"[ \t\r\n]*")


def parse_conflict_records(payload: str) -> list[tuple[dict, str]]:
    """Parse comma-separated records and retain their original JSON for lossless filtering."""
    decoder = json.JSONDecoder()
    records = []
    start = _JSON_WHITESPACE.match(payload).end()
    while start < len(payload):
        record, end = decoder.raw_decode(payload, start)
        if not isinstance(record, dict) or record.get("state") not in ("fixed", "unfixed"):
            raise ValueError("each conflict record must be an object with state fixed or unfixed")
        records.append((record, payload[start:end]))
        end = _JSON_WHITESPACE.match(payload, end).end()
        if end == len(payload):
            break
        if payload[end] != ",":
            raise ValueError("conflict records must be separated by commas")
        start = _JSON_WHITESPACE.match(payload, end + 1).end()
        if start == len(payload):
            raise ValueError("conflict records must not end with a trailing comma")
    return records


def prepare_page(content: str) -> tuple[str, int, int]:
    """Return cleaned Markdown, the unresolved count, and the number of dropped records.

    Keep all human resolutions and prefer unresolved records for matching text.
    A missing human resolution anchor prevents the write.
    """
    blocks = list(re.finditer(r"<!--\s*CONFLICTS_CONTENT\b(.*?)-->", content, re.DOTALL))
    if len(blocks) != 1 or content[blocks[0].end() :].strip():
        raise ValueError("expected exactly one closed, trailing CONFLICTS_CONTENT comment")
    block = blocks[0]
    records = parse_conflict_records(block.group(1))
    body = re.sub(
        r"\A\ufeff?---[ \t]*\r?\n.*?^(?:---|\.\.\.)[ \t]*(?:\r?\n|$)",
        "",
        content[: block.start()],
        flags=re.MULTILINE | re.DOTALL,
    )
    # Match the Wiki viewer's search scope, including its treatment of HTML comments.
    body = re.sub(
        r"<!--.*?(?:-->|$)",
        lambda comment: re.sub(r"[^\r\n]", " ", comment.group()),
        body,
        flags=re.DOTALL,
    )
    human_records = set()
    human_anchors = set()
    automatic_records = {}
    for index, (record, _) in enumerate(records):
        anchor = record.get("match_text")
        anchored = isinstance(anchor, str) and bool(anchor.strip()) and anchor in body
        if record.get("fix_plan") == _MANUAL_FIX_PLAN:
            if not anchored:
                raise ValueError(f"cannot locate human resolution anchor: {anchor!r}")
            human_records.add(index)
            human_anchors.add(anchor)
        elif anchored:
            previous = automatic_records.get(anchor)
            if previous is None or (
                records[previous][0]["state"] == "fixed" and record["state"] == "unfixed"
            ):
                automatic_records[anchor] = index

    keep = human_records | {
        index
        for anchor, index in automatic_records.items()
        if anchor not in human_anchors or records[index][0]["state"] == "unfixed"
    }
    retained = [item for index, item in enumerate(records) if index in keep]
    unresolved = sum(record["state"] == "unfixed" for record, _ in retained)
    dropped = len(records) - len(retained)
    if dropped:
        payload = ",\n".join(raw for _, raw in retained)
        payload = f"\n{payload}\n" if payload else "\n"
        content = content[: block.start(1)] + payload + content[block.end(1) :]
    return content, unresolved, dropped


def main() -> int:
    """Parse CLI arguments, validate input, and return ov's exit status.

    Exactly one inline content value or UTF-8 file path is required. The input file
    stays intact when records are removed. Validation errors exit before any write.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("uri", help="target Viking URI")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--content", help="complete Wiki Markdown")
    source.add_argument("--from-file", help="UTF-8 file containing complete Wiki Markdown")
    parser.add_argument(
        "--mode",
        choices=("replace", "append", "create"),
        default="replace",
        help="write mode; append counts conflicts in submitted content only",
    )
    args = parser.parse_args()

    try:
        content = (
            Path(args.from_file).read_bytes().decode("utf-8")
            if args.from_file is not None
            else args.content
        )
        # shortcut: append counts input only; read remote content if whole-page counts are needed.
        content, count, dropped = prepare_page(content)
    except (OSError, UnicodeError, ValueError) as error:
        parser.error(str(error))

    if dropped:
        print(
            f"write_wiki: dropped {dropped} invalid or duplicate conflict records", file=sys.stderr
        )
    command = [
        "ov",
        "write",
        args.uri,
        "--mode",
        args.mode,
        "--processing-mode",
        "semantic_and_vectors",
        "--tags",
        f"has_conflict={'true' if count else 'false'},conflict_num={count}",
        "--tag-mode",
        "append",
    ]
    try:
        if args.from_file is not None and dropped:
            with TemporaryDirectory(prefix="ov-wiki-") as directory:
                cleaned = Path(directory) / "page.md"
                cleaned.write_bytes(content.encode("utf-8"))
                return subprocess.run(
                    [*command, "--from-file", str(cleaned)], check=False
                ).returncode
        if args.from_file is not None:
            command.extend(["--from-file", args.from_file])
        else:
            command.append("--content=" + content)
        return subprocess.run(command, check=False).returncode
    except OSError as error:
        print(f"write_wiki: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
