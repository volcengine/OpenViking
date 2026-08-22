"""Regression tests for fail-closed dependency-audit exception handling."""

import importlib.util
import json
import sys
from datetime import date, timedelta
from pathlib import Path

import pytest


def _load_verifier_module():
    path = Path(__file__).resolve().parents[1] / "scripts" / "verify_security_audit_baseline.py"
    spec = importlib.util.spec_from_file_location("security_audit_baseline", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


verifier = _load_verifier_module()


def _entry(**overrides):
    entry = {
        "id": "PYSEC-test",
        "ecosystem": "pip",
        "package_path": "uv.lock:example@1.0.0",
        "scanner_command": verifier.SCANNER_COMMANDS["pip"],
        "scanner_version": "2.10.0",
        "expires_on": (date.today() + timedelta(days=1)).isoformat(),
        "owner": "security",
        "rationale": "test-only exception",
        "removal_condition": "remove after upgrade",
    }
    entry.update(overrides)
    return entry


def test_baseline_requires_every_contract_field(tmp_path):
    entry = _entry()
    entry.pop("owner")
    path = tmp_path / "baseline.json"
    path.write_text(json.dumps([entry]))

    with pytest.raises(ValueError, match="exactly"):
        verifier.load_baseline(path, date.today())


def test_baseline_rejects_expired_exception(tmp_path):
    path = tmp_path / "baseline.json"
    path.write_text(json.dumps([_entry(expires_on=(date.today() - timedelta(days=1)).isoformat())]))

    with pytest.raises(ValueError, match="expired"):
        verifier.load_baseline(path, date.today())


def test_finding_identity_includes_ecosystem_and_package_path():
    same_id = "GHSA-example"

    assert verifier.Finding(same_id, "npm", "bot/package-lock.json:node_modules/example") != verifier.Finding(
        same_id, "npm", "web-studio/package-lock.json:node_modules/example"
    )
