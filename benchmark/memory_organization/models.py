from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

_FACT_RE = re.compile(r"\[(F\d{2})\]")


@dataclass(frozen=True, slots=True)
class OrganizationCase:
    case_id: str
    category: str
    canonical_topics: tuple[str, ...]
    initial_files: dict[str, str]
    expected_files: dict[str, tuple[str, ...]]
    expected_replacements: dict[str, str]
    max_fact_lines_per_file: int | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "OrganizationCase":
        return cls(
            case_id=str(data["case_id"]),
            category=str(data["category"]),
            canonical_topics=tuple(data["canonical_topics"]),
            initial_files={str(k): str(v) for k, v in data["initial_files"].items()},
            expected_files={
                str(topic): tuple(str(marker) for marker in markers)
                for topic, markers in data["expected_files"].items()
            },
            expected_replacements={
                str(k): str(v) for k, v in data.get("expected_replacements", {}).items()
            },
            max_fact_lines_per_file=(
                int(data["max_fact_lines_per_file"])
                if data.get("max_fact_lines_per_file") is not None
                else None
            ),
        )

    @property
    def expected_markers(self) -> set[str]:
        return {marker for markers in self.expected_files.values() for marker in markers}


def load_cases(path: Path) -> list[OrganizationCase]:
    return [OrganizationCase.from_dict(item) for item in json.loads(path.read_text())]


def fact_markers(content: str) -> list[str]:
    return _FACT_RE.findall(content or "")


def fact_lines(content: str) -> list[tuple[str, str]]:
    """Return marker/fact pairs while ignoring optional Markdown bullets."""
    records: list[tuple[str, str]] = []
    for line in (content or "").splitlines():
        match = re.match(r"^\s*(?:[-*]\s*)?\[(F\d{2})\]\s*(.*?)\s*$", line)
        if match:
            records.append((match.group(1), " ".join(match.group(2).split())))
    return records


def topic_from_uri(uri: str) -> str:
    return Path(uri).stem
