#!/usr/bin/env python3
"""Write Wiki Markdown through ov with tags derived from its conflict trailer."""

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path


def count_unfixed(content: str) -> int:
    """Count unresolved records in a page's sole trailing conflict comment.

    Content contains comma-separated JSON objects without an outer array. Raise
    ValueError for an absent/malformed trailer or a record without a valid state;
    invalid metadata must not silently clear a page's unresolved-conflict tags.
    """
    blocks = list(re.finditer(r"<!--\s*CONFLICTS_CONTENT\b(.*?)-->", content, re.DOTALL))
    if len(blocks) != 1 or content[blocks[0].end() :].strip():
        raise ValueError("expected exactly one closed, trailing CONFLICTS_CONTENT comment")
    records = json.loads("[" + blocks[0].group(1) + "]")
    for record in records:
        if not isinstance(record, dict) or record.get("state") not in ("fixed", "unfixed"):
            raise ValueError("each conflict record must be an object with state fixed or unfixed")
    return sum(record["state"] == "unfixed" for record in records)


def main() -> int:
    """Parse CLI arguments, validate input, and return ov's exit status.

    Exactly one inline content value or UTF-8 file path is required. Content, URI,
    file path, and write mode pass through unchanged; inherited ov configuration,
    output streams, and credentials apply. Validation errors exit before any write.
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
        count = count_unfixed(content)
    except (OSError, UnicodeError, ValueError) as error:
        parser.error(str(error))

    command = ["ov", "write", args.uri]
    if args.from_file is not None:
        command.extend(["--from-file", args.from_file])
    else:
        command.append("--content=" + args.content)
    command.extend(
        [
            "--mode",
            args.mode,
            "--processing-mode",
            "semantic_and_vectors",
            "--tags",
            f"has_conflict={'true' if count else 'false'},conflict_num={count}",
            "--tag-mode",
            "append",
        ]
    )
    try:
        return subprocess.run(command, check=False).returncode
    except OSError as error:
        print(f"write_wiki: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
