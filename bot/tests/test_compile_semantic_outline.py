"""Semantic outline planning: brief export, deterministic join, fail-open decisions."""

from collections import Counter
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from vikingbot.compile.ops import outline as outline_op
from vikingbot.compile.plan import SemanticOutline
from vikingbot.compile.results import Record
from vikingbot.config.schema import DirectBackendConfig

ROOT = "viking://resources/django"


class MemoryFiles:
    def __init__(self):
        self.values = {}

    async def put(self, key, value):
        self.values[key] = value
        return key

    async def get(self, key):
        return self.values.get(key)


def _make_runtime():
    files = {
        "README.rst": [(1, 50, "root readme part 1"), (51, 90, "root readme part 2")],
        "docs/README.rst": [(1, 20, "docs readme")],
        "django/apps.py": [(1, 40, "apps")],
        "django/db/models/base.py": [(1, 30, "base 1"), (31, 60, "base 2")],
    }
    evidence, records, shards = {}, [], MemoryFiles()
    index = 0
    for path, chunks in files.items():
        uri = f"{ROOT}/{path}"
        for start, end, text in chunks:
            index += 1
            rid = f"r{index:02d}"
            evidence[rid] = {
                "uri": uri,
                "start_line": start,
                "end_line": end,
                "hash": f"h{rid}",
            }
            records.append(Record(rid, f"sources/{rid}", [rid], "", {}, []))
            shards.values[f"sources/{rid}"] = {"text": text, "uri": uri}
    runtime = SimpleNamespace(
        target="viking://resources/django-wiki",
        request=SimpleNamespace(from_=[ROOT], instruction="Build the wiki."),
        evidence=evidence,
        metrics=Counter(),
        warnings=[],
        files=shards,
        skill="skill text",
        model=SimpleNamespace(ask=AsyncMock()),
    )
    return runtime, records


@pytest.mark.asyncio
async def test_planning_brief_uses_shortest_readme_and_bodyless_tree():
    runtime, records = _make_runtime()
    brief = await outline_op.export_planning_brief(runtime, records, {"files": 4})
    assert brief["readme_path"] == "README.rst"
    assert "root readme part 1" in brief["readme"]
    assert "root readme part 2" in brief["readme"]
    assert "docs readme" not in brief["readme"]
    assert brief["file_count"] == 4
    assert brief["tree_folded"] is False
    assert runtime.metrics["planning_brief_folded"] == 0


@pytest.mark.asyncio
async def test_planning_brief_folds_oversized_tree(monkeypatch):
    runtime, records = _make_runtime()
    monkeypatch.setattr(outline_op, "_PLANNING_TREE_MAX_FILES", 2)
    monkeypatch.setattr(outline_op, "_PLANNING_BRIEF_MAX_CHARS", 1)
    brief = await outline_op.export_planning_brief(runtime, records, {})
    assert brief["tree_folded"] is True
    assert len(brief["paths"]) == 2
    assert runtime.metrics["planning_brief_folded"] == 1


@pytest.mark.asyncio
async def test_expand_joins_file_assignments_and_meters_drops():
    runtime, records = _make_runtime()
    planned = SemanticOutline.model_validate(
        {
            "pages": [
                {
                    "id": "index",
                    "path": "index.md",
                    "page_kind": "index",
                    "file_paths": ["README.rst", "docs/README.rst"],
                },
                {
                    "id": "apps",
                    "path": "deep/apps.md",
                    "page_kind": "deep",
                    "file_paths": ["django/apps.py", "django/apps.py", "ghost/missing.py"],
                },
            ]
        }
    )
    outline = await outline_op.expand_semantic_outline(runtime, records, {}, planned)
    assert [page.id for page in outline.pages] == ["index", "apps"]
    apps_page = outline.pages[1]
    assert len(apps_page.sources) == 1
    assert apps_page.sources[0].uri.endswith("django/apps.py")
    assert runtime.metrics["semantic_outline_files_assigned"] == 3
    assert runtime.metrics["semantic_outline_files_dropped"] == 1
    assert any("ghost/missing.py" in warning for warning in runtime.warnings)
    assert "semantic-outline" in runtime.files.values


@pytest.mark.asyncio
async def test_expand_covers_all_ranges_of_multichunk_file():
    runtime, records = _make_runtime()
    planned = SemanticOutline.model_validate(
        {
            "pages": [
                {
                    "id": "models",
                    "path": "deep/models.md",
                    "file_paths": ["django/db/models/base.py"],
                }
            ]
        }
    )
    outline = await outline_op.expand_semantic_outline(runtime, records, {}, planned)
    assert len(outline.pages[0].sources) == 2


@pytest.mark.asyncio
async def test_expand_fails_when_page_has_only_unknown_paths():
    runtime, records = _make_runtime()
    planned = SemanticOutline.model_validate(
        {"pages": [{"id": "x", "path": "x.md", "file_paths": ["nope.py"]}]}
    )
    with pytest.raises(ValueError):
        await outline_op.expand_semantic_outline(runtime, records, {}, planned)


@pytest.mark.asyncio
async def test_semantic_decide_fail_open():
    runtime, _ = _make_runtime()
    runtime.model.ask = AsyncMock(side_effect=RuntimeError("boom"))
    assert await outline_op.semantic_decide(runtime, {}) is None
    assert runtime.warnings


@pytest.mark.asyncio
async def test_semantic_decide_persists_decision():
    runtime, _ = _make_runtime()
    decision = SimpleNamespace(model_dump=lambda: {"use_semantic_outline": True})
    runtime.model.ask = AsyncMock(return_value=decision)
    assert await outline_op.semantic_decide(runtime, {}) is decision
    assert runtime.files.values["semantic-outline-decision"] == {
        "use_semantic_outline": True
    }


def test_agent_exec_gate_defaults_off():
    config = DirectBackendConfig()
    assert config.allow_compile_agent_exec is False
    assert config.allow_compile_exec is False
