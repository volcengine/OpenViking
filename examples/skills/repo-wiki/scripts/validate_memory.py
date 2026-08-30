#!/usr/bin/env python3
"""Validate authored repo-memory bundles."""

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any


BASELINE_FILES = [
    Path("PROFILE.md"),
    Path("resources/commits.md"),
    Path("resources/prs.md"),
    Path("resources/issues.md"),
    Path("raw/git-commits.json"),
]

PROVIDER_RAW_FILES = [Path("raw/github-facets.json"), Path("raw/gitlab-facets.json")]
PROVIDER_RESOURCE_FILES = [Path("resources/prs.md"), Path("resources/issues.md")]
DISABLED_RESOURCE_SOURCES = {"history_disabled", "provider_skipped_local_only", "provider_unavailable", "github_resource_facets_unavailable", "gitlab_resource_facets_unavailable", "provider_unavailable_local_only"}
PLACEHOLDER_RE = re.compile(r"\[([^\]\n]+)\]")
LINK_TARGET_AFTER_BRACKET_RE = re.compile(r"[ \t\r\n]*\(")


def relative(path: Path, memory: Path) -> str:
    return path.relative_to(memory).as_posix()


def resolve_memory_root(path: Path) -> Path:
    candidate = path.expanduser().resolve()
    if candidate.name == ".repo_memory":
        return candidate
    return candidate / ".repo_memory"


def parse_frontmatter(text: str) -> dict[str, Any]:
    if not text.startswith("---\n"):
        return {}
    end = text.find("\n---", 4)
    if end == -1:
        return {}
    values: dict[str, Any] = {}
    for line in text[4:end].splitlines():
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        key = key.strip()
        value = value.strip()
        if not key:
            continue
        if (value.startswith('"') and value.endswith('"')) or (value.startswith("'") and value.endswith("'")):
            values[key] = value[1:-1]
        elif re.fullmatch(r"-?\d+", value):
            values[key] = int(value)
        elif value.lower() == "true":
            values[key] = True
        elif value.lower() == "false":
            values[key] = False
        else:
            values[key] = value
    return values


def body_after_frontmatter(text: str) -> str:
    if not text.startswith("---\n"):
        return text
    end = text.find("\n---", 4)
    if end == -1:
        return text
    return text[end + 4 :].lstrip("\n")


def item_section_count(text: str) -> int:
    body = body_after_frontmatter(text)
    lines = body.splitlines()
    title_seen = False
    count = 0
    for line in lines:
        if line.startswith("# ") and not title_seen:
            title_seen = True
            continue
        if title_seen and line.startswith("## "):
            count += 1
    return count


def placeholder_matches(text: str) -> list[str]:
    text = strip_markdown_code(text)
    matches: list[str] = []
    for match in PLACEHOLDER_RE.finditer(text):
        if LINK_TARGET_AFTER_BRACKET_RE.match(text, match.end()):
            continue
        value = match.group(1).strip()
        if not value or value.startswith("#"):
            continue
        if re.search(r"[A-Za-z]", value):
            matches.append(f"[{value}]")
    return matches


def strip_markdown_code(text: str) -> str:
    without_fences = re.sub(r"```.*?```", "", text, flags=re.DOTALL)
    return re.sub(r"`[^`\n]*`", "", without_fences)


def check_exists(memory: Path, rel_path: Path, errors: list[str], checked: list[str]) -> bool:
    path = memory / rel_path
    checked.append(rel_path.as_posix())
    if not path.exists():
        errors.append(f"{rel_path.as_posix()}: required file is missing")
        return False
    if not path.is_file():
        errors.append(f"{rel_path.as_posix()}: expected a file")
        return False
    return True


def validate_json(path: Path, memory: Path, errors: list[str], checked: list[str]) -> None:
    checked.append(relative(path, memory))
    try:
        json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        errors.append(f"{relative(path, memory)}: invalid JSON at line {exc.lineno}, column {exc.colno}: {exc.msg}")
    except OSError as exc:
        errors.append(f"{relative(path, memory)}: could not read JSON: {exc}")


def raw_source_points_to_provider(raw_source: Any) -> bool:
    return isinstance(raw_source, str) and raw_source.endswith(("github-facets.json", "gitlab-facets.json"))


def validate_markdown(path: Path, memory: Path, errors: list[str], warnings: list[str], checked: list[str]) -> None:
    checked.append(relative(path, memory))
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        errors.append(f"{relative(path, memory)}: could not read Markdown: {exc}")
        return

    frontmatter = parse_frontmatter(text)
    rel = relative(path, memory)
    if not frontmatter.get("schema"):
        errors.append(f"{rel}: frontmatter field 'schema' is missing")
    if path.name == "PROFILE.md":
        source_tree = str(frontmatter.get("source_tree") or "")
        if re.fullmatch(r"[0-9a-fA-F]{40}", source_tree) is None:
            errors.append(f"{rel}: source_tree must be a full Git tree SHA")

    placeholders = placeholder_matches(text)
    if placeholders:
        errors.append(f"{rel}: unresolved bracket placeholder(s): {', '.join(placeholders)}")

    if path.parent.name != "resources":
        return

    for field in ["source", "resource_count", "trust_state", "raw_source"]:
        if field not in frontmatter:
            errors.append(f"{rel}: frontmatter field '{field}' is missing")

    expected = frontmatter.get("resource_count")
    actual = item_section_count(text)
    if isinstance(expected, int):
        if expected != actual:
            errors.append(f"{rel}: resource_count is {expected}, but found {actual} item section(s)")
    elif expected is not None:
        errors.append(f"{rel}: resource_count must be an integer")

    source = frontmatter.get("source")
    raw_source = frontmatter.get("raw_source")
    if "source" in frontmatter and not source:
        errors.append(f"{rel}: frontmatter field 'source' must not be empty")
    if "trust_state" in frontmatter and not frontmatter.get("trust_state"):
        errors.append(f"{rel}: frontmatter field 'trust_state' must not be empty")
    if source in DISABLED_RESOURCE_SOURCES and isinstance(expected, int) and expected != 0:
        errors.append(f"{rel}: disabled or unavailable resource source {source!r} must use resource_count 0")
    if source in DISABLED_RESOURCE_SOURCES and raw_source:
        errors.append(f"{rel}: disabled or unavailable resource source {source!r} must use an empty raw_source")
    if path.name in {"commits.md", "prs.md", "issues.md"} and source not in DISABLED_RESOURCE_SOURCES and raw_source == "":
        errors.append(f"{rel}: empty raw_source requires a disabled or unavailable source")
    if path.name in {"prs.md", "issues.md"} and raw_source_points_to_provider(raw_source):
        provider_path = (path.parent / raw_source).resolve()
        if not provider_path.exists():
            errors.append(f"{rel}: provider raw evidence is missing for raw_source {raw_source!r}")


def provider_raw_paths(memory: Path) -> list[Path]:
    return [memory / rel for rel in PROVIDER_RAW_FILES if (memory / rel).exists()]


def validate(memory: Path) -> dict[str, Any]:
    errors: list[str] = []
    warnings: list[str] = []
    checked: list[str] = []

    if not memory.exists():
        errors.append(f"{memory}: .repo_memory directory is missing")
        return {"ok": False, "errors": errors, "warnings": warnings, "checked": checked}
    if not memory.is_dir():
        errors.append(f"{memory}: expected .repo_memory to be a directory")
        return {"ok": False, "errors": errors, "warnings": warnings, "checked": checked}

    for rel_path in BASELINE_FILES:
        check_exists(memory, rel_path, errors, checked)

    markdown_paths = sorted(
        path
        for path in memory.rglob("*.md")
        if path.is_file()
    )
    for path in markdown_paths:
        if path.exists() and path.is_file():
            validate_markdown(path, memory, errors, warnings, checked)

    json_paths = sorted(path for path in (memory / "raw").rglob("*.json") if path.is_file())
    provider_raw = provider_raw_paths(memory)

    if provider_raw:
        for rel_path in PROVIDER_RESOURCE_FILES:
            check_exists(memory, rel_path, errors, checked)

    seen_json: set[Path] = set()
    for path in json_paths:
        if path.exists() and path.is_file() and path not in seen_json:
            seen_json.add(path)
            validate_json(path, memory, errors, checked)

    checked = sorted(dict.fromkeys(checked))
    return {"ok": not errors, "errors": errors, "warnings": warnings, "checked": checked}


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Validate a repo-memory bundle.")
    parser.add_argument("path", help="Path to a repository or its .repo_memory directory")
    parser.add_argument("--pretty", action="store_true", help="Pretty-print JSON output")
    return parser.parse_args(argv)


def main(argv: list[str]) -> int:
    args = parse_args(argv)
    report = validate(resolve_memory_root(Path(args.path)))
    print(json.dumps(report, ensure_ascii=False, indent=2 if args.pretty else None))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
