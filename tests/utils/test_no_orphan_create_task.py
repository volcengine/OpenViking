# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""No call site may throw away the result of ``asyncio.create_task``.

The loop holds only a weak reference to a running task, so a discarded task
object can be collected mid-await. This walks the package rather than trusting a
reviewer to notice the next one, because the mistake looks correct: the call
reads like "start this", and nothing about it suggests the work can vanish.

A task that is awaited, returned, assigned, gathered, or appended to a list is
fine — it has a reference. Only a bare expression statement is reported.
"""

import ast
from pathlib import Path

PACKAGE_ROOT = Path(__file__).resolve().parents[2] / "openviking"


def _is_create_task_call(node: ast.AST) -> bool:
    if not isinstance(node, ast.Call):
        return False
    func = node.func
    if isinstance(func, ast.Attribute) and func.attr == "create_task":
        value = func.value
        if isinstance(value, ast.Name) and value.id == "asyncio":
            return True
        if isinstance(value, ast.Attribute) and value.attr == "asyncio":
            return True
    return False


def _orphan_create_tasks(path: Path) -> list[int]:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except SyntaxError:
        return []
    return [
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.Expr) and _is_create_task_call(node.value)
    ]


def test_no_create_task_result_is_discarded():
    offenders = []
    for path in sorted(PACKAGE_ROOT.rglob("*.py")):
        for lineno in _orphan_create_tasks(path):
            offenders.append(f"{path.relative_to(PACKAGE_ROOT.parent)}:{lineno}")

    assert offenders == [], (
        "asyncio.create_task(...) used as a bare statement, so nothing holds the task and "
        "it can be garbage-collected mid-execution. Use "
        "openviking.utils.background_tasks.spawn_background_task instead, or keep the task "
        "yourself:\n  " + "\n  ".join(offenders)
    )
