# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""Tests for the pre-seed Skill source-filter op and its fail-open contract."""

from __future__ import annotations

import posixpath
import subprocess
import sys
from collections import Counter
from types import SimpleNamespace

import pytest
from vikingbot.compile.models import COMPILE_STAGING_ROOT
from vikingbot.compile.ops import source_filter as sf
from vikingbot.compile.plan import FilterDecision

ROOT = "viking://resources/django"
PROVIDER = "scripts/filter_sources.py"

# A minimal whitelist filter: drop locale/static/vendor segments and compiled messages.
_GOOD_SCRIPT = """
import json, sys
manifest = json.load(open(sys.argv[1], encoding="utf-8"))
keep = []
for item in manifest["files"]:
    parts = [p.lower() for p in item["path"].split("/")]
    if any(p in ("locale", "locales", "static", "vendor") for p in parts):
        continue
    if item["path"].lower().endswith((".po", ".mo")):
        continue
    keep.append(item["id"])
json.dump({"keep": keep}, open(sys.argv[2], "w", encoding="utf-8"))
"""
_FAIL_SCRIPT = "import sys\nsys.exit(1)\n"
_BAD_JSON_SCRIPT = "open(sys.argv[2], 'w').write('not-json')\n" if False else """
import sys
open(sys.argv[2], "w", encoding="utf-8").write("not-json")
"""
_EMPTY_KEEP_SCRIPT = """
import json, sys
json.dump({"keep": []}, open(sys.argv[2], "w", encoding="utf-8"))
"""
_UNKNOWN_ID_SCRIPT = """
import json, sys
json.dump({"keep": ["999"]}, open(sys.argv[2], "w", encoding="utf-8"))
"""
_NON_DICT_SCRIPT = """
import json, sys
json.dump(["0", "1"], open(sys.argv[2], "w", encoding="utf-8"))
"""


class _FakeSandbox:
    def __init__(self, tmp_path):
        self.sandbox_cwd = str(tmp_path)

    def _abs(self, rel):
        return posixpath.join(self.sandbox_cwd, rel)

    async def write_file(self, rel, content):
        path = self._abs(rel)
        import os

        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(content)

    async def read_file(self, rel):
        with open(self._abs(rel), encoding="utf-8") as handle:
            return handle.read()

    async def execute(self, command, timeout=60):
        # Run under the test interpreter instead of relying on a `python3` on PATH.
        completed = subprocess.run(
            command.replace("python3", sys.executable, 1),
            shell=True,
            cwd=self.sandbox_cwd,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        if completed.returncode != 0:
            raise RuntimeError(f"exit {completed.returncode}: {completed.stderr}")
        return completed.stdout


class _FakeFiles:
    def __init__(self, sandbox):
        self.sandbox = sandbox
        self.shards: dict[str, object] = {}

    async def put(self, path, value):
        self.shards[path] = value
        return f"{COMPILE_STAGING_ROOT}/{path}.json"

    async def get(self, path):
        return self.shards.get(path)


class _FakeResources:
    def __init__(self, sandbox, scripts):
        self.scripts = scripts
        self.local_root = posixpath.join(
            sandbox.sandbox_cwd, COMPILE_STAGING_ROOT, "skills", "testhex"
        )
        self._sandbox = sandbox

    def path(self, path):
        if ".." in path.split("/"):
            raise ValueError("traversal rejected")
        return path

    async def read(self, path):
        if path not in self.scripts:
            raise FileNotFoundError(path)
        content = self.scripts[path]
        import os

        target = os.path.join(self.local_root, *path.split("/"))
        os.makedirs(os.path.dirname(target), exist_ok=True)
        with open(target, "w", encoding="utf-8") as handle:
            handle.write(content)
        return content


class _FakeModel:
    def __init__(self, decision):
        self._decision = decision
        self.ask_count = 0

    async def ask(self, stage, system, data, schema, validate=None):
        self.ask_count += 1
        if isinstance(self._decision, Exception):
            raise self._decision
        return self._decision


def _runtime(tmp_path, *, decision, scripts=None, exec_enabled=True):
    sandbox = _FakeSandbox(tmp_path)
    bundle = {PROVIDER: _GOOD_SCRIPT} if scripts is None else scripts
    resources = _FakeResources(sandbox, bundle)
    return SimpleNamespace(
        target="viking://resources/code-wiki-test/out",
        skill="skill text naming scripts/filter_sources.py",
        model=_FakeModel(decision),
        files=_FakeFiles(sandbox),
        sandbox=sandbox,
        resources=resources,
        compile_exec_enabled=exec_enabled,
        warnings=[],
        metrics=Counter(),
        request=SimpleNamespace(from_=[ROOT]),
    )


def _ordered():
    return [
        f"{ROOT}/django/db/models/base.py",
        f"{ROOT}/django/conf/locale/en/LC_MESSAGES/django.po",
        f"{ROOT}/django/contrib/admin/static/admin/js/app.js",
        f"{ROOT}/django/contrib/admin/static/admin/js/vendor/jquery.js",
        f"{ROOT}/django/contrib/staticfiles/handlers.py",
        f"{ROOT}/django/utils/translation/trans_real.py",
    ]


async def test_apply_keeps_whitelist_and_records_metrics(tmp_path):
    runtime = _runtime(
        tmp_path, decision=FilterDecision(use_filter=True, provider=PROVIDER, reason="named")
    )

    filtered = await sf.apply(runtime, _ordered(), [ROOT])

    assert filtered == [
        f"{ROOT}/django/db/models/base.py",
        f"{ROOT}/django/contrib/staticfiles/handlers.py",
        f"{ROOT}/django/utils/translation/trans_real.py",
    ]
    assert runtime.metrics["source_filter_calls"] == 1
    assert runtime.metrics["source_files_total"] == 6
    assert runtime.metrics["source_files_kept"] == 3
    assert runtime.metrics["source_files_dropped"] == 3
    decision_shard = runtime.files.shards["filter-decision"]
    assert decision_shard["use_filter"] is True
    result_shard = runtime.files.shards["filter-result"]
    assert result_shard["kept"] == 3 and result_shard["dropped"] == 3
    # Original list must not be mutated and manifest ids stay aligned with positions.
    assert len(_ordered()) == 6
    assert any("kept 3/6" in warning for warning in runtime.warnings)


async def test_manifest_uses_repository_relative_paths(tmp_path):
    runtime = _runtime(
        tmp_path, decision=FilterDecision(use_filter=True, provider=PROVIDER)
    )
    await sf.apply(runtime, _ordered(), [ROOT])

    # The executed script only works when the manifest exposes relative paths and ids.
    assert runtime.model.ask_count == 1


async def test_disabled_exec_keeps_all_files(tmp_path):
    runtime = _runtime(
        tmp_path,
        decision=FilterDecision(use_filter=True, provider=PROVIDER),
        exec_enabled=False,
    )

    ordered = _ordered()
    assert await sf.apply(runtime, ordered, [ROOT]) == ordered
    assert runtime.metrics["source_filter_calls"] == 0
    assert any("compile exec is disabled" in warning for warning in runtime.warnings)


@pytest.mark.parametrize(
    "decision",
    [
        None,
        FilterDecision(use_filter=False, provider=PROVIDER),
        FilterDecision(use_filter=True, provider=None),
    ],
)
async def test_declined_decision_keeps_all_files(tmp_path, decision):
    runtime = _runtime(tmp_path, decision=decision)

    ordered = _ordered()
    assert await sf.apply(runtime, ordered, [ROOT]) == ordered
    assert runtime.metrics["source_filter_calls"] == 0


async def test_decision_error_fails_open(tmp_path):
    runtime = _runtime(tmp_path, decision=RuntimeError("model unavailable"))

    ordered = _ordered()
    assert await sf.apply(runtime, ordered, [ROOT]) == ordered
    assert any("decision failed" in warning for warning in runtime.warnings)


async def test_missing_provider_script_fails_open(tmp_path):
    runtime = _runtime(
        tmp_path,
        decision=FilterDecision(use_filter=True, provider=PROVIDER),
        scripts={},
    )

    ordered = _ordered()
    assert await sf.apply(runtime, ordered, [ROOT]) == ordered
    assert runtime.metrics["source_filter_calls"] == 0


@pytest.mark.parametrize(
    ("script", "expected_fragment"),
    [
        (_FAIL_SCRIPT, "failed; keeping all files"),
        (_BAD_JSON_SCRIPT, "failed; keeping all files"),
        (_EMPTY_KEEP_SCRIPT, "empty, malformed or out-of-scope"),
        (_UNKNOWN_ID_SCRIPT, "empty, malformed or out-of-scope"),
        (_NON_DICT_SCRIPT, "empty, malformed or out-of-scope"),
    ],
)
async def test_script_failures_fail_open(tmp_path, script, expected_fragment):
    runtime = _runtime(
        tmp_path,
        decision=FilterDecision(use_filter=True, provider=PROVIDER),
        scripts={PROVIDER: script},
    )

    ordered = _ordered()
    assert await sf.apply(runtime, ordered, [ROOT]) == ordered
    assert runtime.metrics["source_filter_calls"] == 0
    assert any(expected_fragment in warning for warning in runtime.warnings)


async def test_empty_input_returns_early(tmp_path):
    runtime = _runtime(
        tmp_path, decision=FilterDecision(use_filter=True, provider=PROVIDER)
    )

    assert await sf.apply(runtime, [], [ROOT]) == []
    assert runtime.model.ask_count == 0
