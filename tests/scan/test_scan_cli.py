# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Tests for ``openviking-server scan``: a local, read-only directory report."""

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from openviking.parse.registry import ParserRegistry
from openviking.scan import cli as scan_cli

RUNNER = CliRunner()

TEN_MB = 10 * 1024 * 1024


@pytest.fixture
def registry() -> ParserRegistry:
    return ParserRegistry()


@pytest.fixture
def tree(tmp_path: Path) -> Path:
    """One file per verdict, plus files the server-side walk rules skip."""
    (tmp_path / "readme.md").write_text("# README", encoding="utf-8")
    (tmp_path / "main.py").write_text("print(1)", encoding="utf-8")
    (tmp_path / "notes.xyz").write_text("unknown", encoding="utf-8")
    # Frontend-blocked AND rejected by the server: the blocked verdict wins.
    (tmp_path / "bundle.7z").write_bytes(b"7z\xbc\xaf\x27\x1c")
    # Frontend-blocked but the server's registry accepts it: the two layers differ.
    (tmp_path / "icon.ico").write_bytes(b"\x00\x00\x01\x00")
    # Sparse file just over the per-file upload ceiling.
    with (tmp_path / "big.md").open("wb") as fh:
        fh.seek(TEN_MB)
        fh.write(b"x")

    nested = tmp_path / "nested"
    nested.mkdir()
    (nested / "deep.py").write_text("x = 1", encoding="utf-8")

    (tmp_path / ".hidden.md").write_text("dot file", encoding="utf-8")
    (tmp_path / "empty.md").write_bytes(b"")
    ignored = tmp_path / "node_modules"
    ignored.mkdir()
    (ignored / "pkg.js").write_text("x", encoding="utf-8")
    return tmp_path


def _verdicts(report: scan_cli.ScanReport) -> dict[str, str]:
    return {row.rel_path: row.verdict for row in report.files}


def test_counts_every_verdict(tree: Path, registry: ParserRegistry) -> None:
    report = scan_cli.build_report(tree, registry=registry)

    assert report.counts == {
        "importable": 3,
        "unsupported": 1,
        "upload_blocked": 2,
        "too_large": 1,
    }
    assert sorted(report.skipped)  # dot file, empty file, node_modules


def test_separates_server_rejection_from_upload_layer_limits(
    tree: Path, registry: ParserRegistry
) -> None:
    verdicts = _verdicts(scan_cli.build_report(tree, registry=registry))

    # Source files are admitted by the text path, not just the parser registry.
    assert verdicts["main.py"] == "importable"
    assert verdicts["nested/deep.py"] == "importable"
    # No parser and not text -- the server cannot take it.
    assert verdicts["notes.xyz"] == "unsupported"
    # The upload UI refuses these before the server ever sees them.
    assert verdicts["bundle.7z"] == "upload_blocked"
    assert verdicts["icon.ico"] == "upload_blocked"
    assert verdicts["big.md"] == "too_large"


def test_upload_rules_are_read_from_the_frontend_module() -> None:
    rules = scan_cli.load_upload_rules()

    assert rules is not None
    assert {".pyc", ".dll", ".7z", ".tar", ".exe"} <= set(rules.blocked_extensions)
    assert rules.max_files == 10
    assert rules.max_file_size_bytes == TEN_MB


def test_absent_frontend_module_yields_no_rules(tmp_path: Path) -> None:
    assert scan_cli.load_upload_rules(tmp_path / "upload.ts") is None


def test_frontend_module_that_changed_shape_raises(tmp_path: Path) -> None:
    reshaped = tmp_path / "upload.ts"
    reshaped.write_text("export const MAX_UPLOAD_FILES = 10\n", encoding="utf-8")

    with pytest.raises(RuntimeError, match="changed shape"):
        scan_cli.load_upload_rules(reshaped)


def test_falls_back_to_server_verdicts_when_rules_are_absent(
    tree: Path, registry: ParserRegistry, monkeypatch: pytest.MonkeyPatch
) -> None:
    def _no_rules(*_args, **_kwargs):
        return None

    monkeypatch.setattr(scan_cli, "load_upload_rules", _no_rules)

    report = scan_cli.build_report(tree, registry=registry)

    assert report.counts["upload_blocked"] == 0
    assert report.counts["too_large"] == 0
    assert report.counts["importable"] == 5


def test_cli_writes_jsonl_without_a_server(tree: Path) -> None:
    out = tree / "out.jsonl"
    result = RUNNER.invoke(scan_cli.app, [str(tree), "--json", str(out)])

    assert result.exit_code == 0, result.output
    assert "importable" in result.output
    rows = [json.loads(line) for line in out.read_text(encoding="utf-8").splitlines()]
    assert len(rows) == 7
    assert {row["verdict"] for row in rows} == {
        "importable",
        "unsupported",
        "upload_blocked",
        "too_large",
    }
    assert all({"path", "ext", "size", "verdict"} <= set(row) for row in rows)


def test_missing_directory_fails_loudly(tmp_path: Path) -> None:
    result = RUNNER.invoke(scan_cli.app, [str(tmp_path / "nope")])

    assert result.exit_code == 1
    assert "error" in result.output.lower()
