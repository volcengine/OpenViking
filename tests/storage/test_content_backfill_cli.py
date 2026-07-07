import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest


def _load_script_module():
    path = (
        Path(__file__).resolve().parents[2]
        / "scripts"
        / "maintenance"
        / "vikingdb_content_backfill"
        / "backfill_vikingdb_content.py"
    )
    spec = importlib.util.spec_from_file_location("backfill_vikingdb_content_cli", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


backfill = _load_script_module()
build_options = backfill.build_options
default_run_dir = backfill.default_run_dir
validate_backend = backfill.validate_backend


def test_default_run_dir_lives_next_to_script(monkeypatch):
    monkeypatch.setattr(
        backfill,
        "datetime",
        SimpleNamespace(now=lambda: SimpleNamespace(strftime=lambda fmt: "20260706-120000")),
    )

    run_dir = default_run_dir()

    assert run_dir.name == "20260706-120000"
    assert run_dir.parent.name == "result"
    assert run_dir.parent.parent == Path("scripts/maintenance/vikingdb_content_backfill")


def test_build_options_defaults_to_dry_run(tmp_path):
    args = SimpleNamespace(
        run_dir=tmp_path,
        execute=False,
        rewrite_non_empty=False,
        batch_size=100,
        limit=7,
        fail_fast=True,
        record_candidates=True,
        record_skipped=True,
    )

    options = build_options(args)

    assert options.run_dir == tmp_path
    assert options.execute is False
    assert options.limit == 7
    assert options.fail_fast is True
    assert options.record_candidates is True
    assert options.record_skipped is True


def test_validate_backend_accepts_vikingdb_modes():
    validate_backend("volcengine")
    validate_backend("vikingdb")


def test_validate_backend_rejects_non_vikingdb_backend():
    with pytest.raises(SystemExit):
        validate_backend("local")
