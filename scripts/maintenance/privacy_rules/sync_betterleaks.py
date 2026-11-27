#!/usr/bin/env python3
# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Regenerate the privacy detectors imported from Betterleaks.

Run from the repository root with Python 3.11+:

    python scripts/maintenance/privacy_rules/sync_betterleaks.py --ref v2.0.0-rc.15

The script downloads config/betterleaks.toml at the given tag or commit (or reads
--toml), keeps the rules whose secret starts with a fixed provider prefix, converts
them to the Rust regex dialect, and rewrites
crates/ragfs/src/privacy_extractor_rules_betterleaks.json. It prints the rule ids
added and removed relative to the committed file. Run `cargo test -p ragfs
privacy_extractor` afterwards.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import urllib.request
from pathlib import Path

import tomllib

REPOSITORY = "https://github.com/betterleaks/betterleaks"
RAW_URL = "https://raw.githubusercontent.com/betterleaks/betterleaks/{ref}/config/betterleaks.toml"
OUTPUT = Path(__file__).resolve().parents[3] / (
    "crates/ragfs/src/privacy_extractor_rules_betterleaks.json"
)

# Formats that privacy_extractor_rules.json covers, or deliberately rejects, with its own fixtures.
HAND_WRITTEN = re.compile(
    r"^(?:anthropic|aws-access-token|github|jwt|openai|private-key"
    r"|slack-(?:app|bot|config-access|legacy|user)-)"
)
MIN_PREFIX = 3

ASCII_CLASS = {"w": "0-9A-Za-z_", "d": "0-9", "s": r"\t\n\f\r "}
QUANTIFIER = re.compile(r"\{\d+(?:,\d*)?\}")


class Skip(Exception):
    pass


def to_rust(pattern: str) -> str:
    """Go RE2 matches \\w, \\d, \\s and \\b as ASCII; Rust's str regex defaults to Unicode."""
    out: list[str] = []
    i, in_class = 0, False
    while i < len(pattern):
        char = pattern[i]
        if char == "\\":
            escape = pattern[i + 1 : i + 2]
            if escape in ASCII_CLASS:
                body = ASCII_CLASS[escape]
                out.append(body if in_class else f"[{body}]")
            elif escape == "b" and not in_class:
                out.append(r"(?-u:\b)")
            elif (
                escape == ""
                or escape in "WDSBQEpPAzC<>"
                or escape.isdigit()
                or pattern.startswith("x{", i + 1)
            ):
                raise Skip(f"unsupported escape \\{escape}")
            else:
                out.append(pattern[i : i + 2])
            i += 2
            continue
        if in_class:
            if posix := re.match(r"\[:[a-z]+:\]", pattern[i:]):
                out.append(posix.group())
                i += posix.end()
                continue
            if char == "[" or (char in "&~-" and pattern[i + 1 : i + 2] == char):
                raise Skip("unsupported character class syntax")
            in_class = char != "]"
        elif char == "[":
            in_class = True
            out.append(char)
            # A leading ] or ^] is a literal member of the class.
            negated = pattern[i + 1 : i + 2] == "^"
            if pattern[i + 1 + negated : i + 2 + negated] == "]":
                raise Skip("unsupported character class syntax")
            i += 1
            continue
        elif char == "{" and not QUANTIFIER.match(pattern, i):
            out.append("\\")
        out.append(char)
        i += 1
    return "".join(out)


def group_end(pattern: str, start: int) -> int:
    """Index of the parenthesis closing the group opened at start."""
    depth, i, in_class = 0, start, False
    while i < len(pattern):
        char = pattern[i]
        if char == "\\":
            i += 2
            continue
        if in_class:
            in_class = char != "]"
        elif char == "[":
            in_class = True
        elif char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
            if depth == 0:
                return i
        i += 1
    raise Skip("unbalanced group")


def capture_groups(pattern: str) -> list[int]:
    groups, i, in_class = [], 0, False
    while i < len(pattern):
        char = pattern[i]
        if char == "\\":
            i += 2
            continue
        if in_class:
            in_class = char != "]"
        elif char == "[":
            in_class = True
        elif char == "(" and (pattern[i + 1 : i + 2] != "?" or pattern[i + 2 : i + 3] in "P<"):
            groups.append(i)
        i += 1
    return groups


def secret_pattern(regex: str) -> str:
    """Reduce `[(?i)][\\b](SECRET)[delimiter]` to SECRET; the extractor checks token boundaries."""
    flags = ""
    if regex.startswith("(?i)"):
        flags, regex = "(?i)", regex[4:]
    regex = regex.removeprefix(r"\b")
    groups = capture_groups(regex)
    if not groups:
        body = regex.removesuffix(r"\b")
    elif groups[0] == 0 and regex[1:2] != "?":
        end = group_end(regex, 0)
        body, trailer = regex[1:end], regex[end + 1 :]
        if trailer not in ("", r"\b") and not (
            trailer.startswith("(?:")
            and group_end(trailer, 0) == len(trailer) - 1
            and not capture_groups(trailer)
        ):
            raise Skip("secret is not the whole token")
        for start in reversed(capture_groups(body)):
            if body[start + 1 : start + 2] == "?":
                raise Skip("named capture group")
            body = f"{body[: start + 1]}?:{body[start + 1 :]}"
    else:
        raise Skip("secret is not the whole token")
    return f"{flags}{body}"


def literal_prefix(pattern: str) -> tuple[int, bool]:
    """Length of the literal run every match starts with, and whether that is the whole pattern."""
    alternatives, depth, start, i, in_class = [], 0, 0, 0, False
    while i < len(pattern):
        char = pattern[i]
        if char == "\\":
            i += 2
            continue
        if in_class:
            in_class = char != "]"
        elif char == "[":
            in_class = True
        elif char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
        elif char == "|" and depth == 0:
            alternatives.append(pattern[start:i])
            start = i + 1
        i += 1
    alternatives.append(pattern[start:])
    prefixes = [_sequence_prefix(alternative) for alternative in alternatives]
    return min(count for count, _ in prefixes), all(whole for _, whole in prefixes)


def _sequence_prefix(sequence: str) -> tuple[int, bool]:
    count, i = 0, 0
    while i < len(sequence):
        if match := re.match(r"\(\?[a-zA-Z-]+\)", sequence[i:]):
            i += match.end()
            continue
        char = sequence[i]
        if char == "(":
            end = group_end(sequence, i) + 1
            inner = re.sub(r"^\?[a-zA-Z-]*:", "", sequence[i + 1 : end - 1])
            width, whole = (0, False) if inner.startswith("?") else literal_prefix(inner)
        elif match := re.match(r"\[[0-9A-Za-z_]{1,4}\]", sequence[i:]):
            end, width, whole = i + match.end(), 1, True
        elif match := re.match(r"\\[^0-9A-Za-z]|[^\\\[.^$|)+*?{]", sequence[i:]):
            end, width, whole = i + match.end(), 1, True
        else:
            return count, False
        if sequence[end : end + 1] in ("?", "*", "+", "{"):
            return count, False
        count += width
        if not whole:
            return count, False
        i = end
    return count, True


def convert(rules: list[dict]) -> tuple[list[dict], dict[str, str]]:
    detectors, skipped = [], {}
    for rule in rules:
        rule_id = rule["id"]
        try:
            if HAND_WRITTEN.match(rule_id):
                raise Skip("covered by a hand-written detector")
            if not rule.get("regex") or rule.get("path"):
                raise Skip("matches file paths")
            if rule.get("skipReport"):
                raise Skip("only reported together with another rule")
            if rule.get("valueGroup"):
                raise Skip("secret is not the whole token")
            pattern = secret_pattern(rule["regex"])
            prefix, fixed = literal_prefix(pattern)
            if prefix < MIN_PREFIX:
                raise Skip("no fixed prefix")
            if fixed:
                raise Skip("fixed string without a secret part")
            detectors.append({"id": rule_id, "pattern": to_rust(pattern), "reject_repeated": True})
        except Skip as reason:
            skipped[rule_id] = str(reason)
    return sorted(detectors, key=lambda detector: detector["id"]), skipped


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--ref", required=True, help="Betterleaks tag or commit")
    parser.add_argument("--toml", type=Path, help="local copy of config/betterleaks.toml")
    args = parser.parse_args()

    if args.toml:
        source = args.toml.read_bytes()
    else:
        with urllib.request.urlopen(RAW_URL.format(ref=args.ref), timeout=60) as response:
            source = response.read()
    detectors, skipped = convert(tomllib.loads(source.decode())["rules"])

    previous = set()
    if OUTPUT.exists():
        previous = {detector["id"] for detector in json.loads(OUTPUT.read_text())["detectors"]}
    current = {detector["id"] for detector in detectors}

    header = {
        "repository": REPOSITORY,
        "ref": args.ref,
        "sha256": hashlib.sha256(source).hexdigest(),
        "license": "MIT, see BETTERLEAKS_LICENSE.txt",
        "generator": "scripts/maintenance/privacy_rules/sync_betterleaks.py",
    }
    lines = ",\n".join(f"    {json.dumps(detector)}" for detector in detectors)
    OUTPUT.write_text(
        f'{{\n  "source": {json.dumps(header)},\n  "detectors": [\n{lines}\n  ]\n}}\n'
    )

    print(f"{len(detectors)} detectors written to {OUTPUT}")
    for label, ids in (("added", current - previous), ("removed", previous - current)):
        if ids:
            print(f"{label} ({len(ids)}): {', '.join(sorted(ids))}")
    reasons: dict[str, int] = {}
    for reason in skipped.values():
        reasons[reason] = reasons.get(reason, 0) + 1
    for reason, count in sorted(reasons.items(), key=lambda item: -item[1]):
        print(f"skipped {count}: {reason}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
