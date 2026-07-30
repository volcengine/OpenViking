# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Regression guards for fire-and-forget ``asyncio.create_task`` call sites.

Python's asyncio only keeps *weak* references to tasks created via
``asyncio.create_task``: if the caller drops the returned Task without storing
it somewhere, the garbage collector may reclaim the Task mid-execution and
silently abort the background work. ``openviking/server/routers/watches.py``
documents this exact hazard and fixes it by registering each task in a
module-level ``set[asyncio.Task]`` and clearing it via ``add_done_callback``.

The tests below statically verify that every ``asyncio.create_task(...)`` in
the historically-affected files does the same — either by registering the task
into a module-level ``set`` (the ``watches.py`` pattern) or into an instance
``set`` that ``close_background_tasks`` drains on shutdown
(the ``ResourceService`` / ``ConnectorDelegate`` pattern).

If a new fire-and-forget site is added without a strong-ref, this test fails
loudly at CI time instead of silently in production.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]

# (file, expected number of create_task call sites) — bumping the count is
# fine, but every listed site must satisfy the strong-ref invariant below.
TARGET_FILES = [
    (REPO_ROOT / "openviking/server/routers/admin.py", 1),
    (REPO_ROOT / "openviking/service/reindex_executor.py", 1),
    (REPO_ROOT / "openviking/service/resource_service.py", 1),
]


def _find_create_task_calls(tree: ast.AST) -> list[ast.Call]:
    """Return every ``asyncio.create_task(...)`` call node in *tree*."""
    calls: list[ast.Call] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if (
            isinstance(func, ast.Attribute)
            and func.attr == "create_task"
            and isinstance(func.value, ast.Name)
            and func.value.id == "asyncio"
        ):
            calls.append(node)
    return calls


def _statement_lineno(node: ast.AST, source_lines: list[str]) -> int:
    """Return the top-level statement line containing *node* — for error messages."""
    return getattr(node, "lineno", 0)


def _has_strong_ref_evidence(source: str, call_lineno: int) -> bool:
    """Detect whether the ``create_task`` at *call_lineno* is followed by a
    task-set registration + done-callback within a small window.

    Accepts either:

    - The module-level ``set`` pattern used by ``watches.py``::

          task = asyncio.create_task(...)
          _BACKGROUND_TASKS.add(task)
          task.add_done_callback(_BACKGROUND_TASKS.discard)

    - The instance ``set`` pattern used by ``ResourceService``::

          task = asyncio.create_task(...)
          self._background_tasks.add(task)
          task.add_done_callback(self._background_tasks.discard)

    We use a lightweight textual scan over a 12-line window rather than a
    full data-flow analysis: the goal is to *guard against regressions*, not
    to prove correctness. A regressed site will simply lack both markers.
    """
    lines = source.splitlines()
    # Look at the block containing the call: from a few lines before (to catch
    # ``task = asyncio.create_task(...)`` where ``task`` is used later) through
    # the next ~12 lines. ``asyncio.create_task`` blocks span multiple lines in
    # the affected files (multi-line kwargs).
    start = max(0, call_lineno - 3)
    end = min(len(lines), call_lineno + 25)
    window = "\n".join(lines[start:end])
    has_set_add = ".add(" in window and ("_background_tasks" in window.lower() or "_BACKGROUND" in window)
    has_done_cb = "add_done_callback" in window
    return has_set_add and has_done_cb


@pytest.mark.parametrize("path,expected_count", TARGET_FILES, ids=lambda p: str(p))
def test_create_task_sites_have_strong_ref(path: Path, expected_count: int) -> None:
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(path))
    calls = _find_create_task_calls(tree)

    assert len(calls) >= expected_count, (
        f"{path}: expected at least {expected_count} asyncio.create_task(...) "
        f"site(s), found {len(calls)}. If a site was removed, adjust "
        f"TARGET_FILES in this test."
    )

    offenders: list[str] = []
    for call in calls:
        if not _has_strong_ref_evidence(source, call.lineno):
            offenders.append(f"  {path}:{call.lineno}")

    assert not offenders, (
        "asyncio.create_task(...) without a strong-reference registration + "
        "done_callback detected. Follow the pattern in "
        "openviking/server/routers/watches.py (module-level set) or "
        "openviking/service/resource_service.py (instance _background_tasks).\n"
        "Offending sites:\n" + "\n".join(offenders)
    )
