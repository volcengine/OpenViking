"""The CI coverage gate must reject skipped or missing real-backend tests."""

import importlib.util
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / ".github/scripts/database_integration.py"
spec = importlib.util.spec_from_file_location("database_integration", SCRIPT)
ci = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ci)


def test_schema_parser_ignores_commented_migration_template():
    sql = "-- DDL may commit implicitly; review rollout.\n-- ALTER TABLE example ADD col INT;\n"
    assert ci.schema_statements(sql) == []
    assert ci.schema_statements(sql + "CREATE TABLE example (id INT);") == [
        "CREATE TABLE example (id INT)"
    ]


def report(tmp_path, *, skipped=None, omitted=None):
    suite = ET.Element("testsuite")
    cases = [("tests.server.test_account_services", name) for name in sorted(ci.ACCOUNT_CASES)] + [
        ("tests.server.test_redis_mysql_account_store", "test_auth[encrypted]"),
        ("tests.service.test_task_work_contract", "test_history[mysql-stage]"),
        ("tests.service.test_task_work_contract", "test_history[postgres-stage]"),
        ("tests.resource.test_watch_store", "test_watch[mysql]"),
    ]
    for module, name in cases:
        if name == omitted:
            continue
        case = ET.SubElement(suite, "testcase", classname=module, name=name)
        if name == skipped:
            ET.SubElement(case, "skipped")
    # File-only skips are intentional and do not qualify as database coverage.
    file_case = ET.SubElement(
        suite,
        "testcase",
        classname="tests.resource.test_watch_store",
        name="test_mysql_running_scan_does_not_decrypt_private_state[file]",
    )
    ET.SubElement(file_case, "skipped")
    path = tmp_path / "results.xml"
    ET.ElementTree(suite).write(path)
    return path


def test_verifier_accepts_real_coverage_and_expected_file_skip(tmp_path):
    assert ci.verify(report(tmp_path)) == {
        "accounts": 2,
        "redis_mysql": 1,
        "task_work_mysql": 1,
        "task_work_postgres": 1,
        "watch_mysql": 1,
    }


@pytest.mark.parametrize(
    "name",
    [
        *sorted(ci.ACCOUNT_CASES),
        "test_auth[encrypted]",
        "test_history[mysql-stage]",
        "test_history[postgres-stage]",
        "test_watch[mysql]",
    ],
)
@pytest.mark.parametrize("status", ["skipped", "omitted"])
def test_verifier_rejects_skipped_or_missing_backend_coverage(tmp_path, name, status):
    with pytest.raises(RuntimeError):
        ci.verify(report(tmp_path, **{status: name}))


def test_verifier_rejects_empty_report(tmp_path):
    path = tmp_path / "empty.xml"
    path.write_text("<testsuites/>")
    with pytest.raises(RuntimeError, match="No passing database cases"):
        ci.verify(path)


@pytest.mark.parametrize("status", ["skipped", "failure", "error"])
def test_verifier_rejects_incomplete_case_even_when_its_suite_has_passes(tmp_path, status):
    path = report(tmp_path)
    tree = ET.parse(path)
    case = ET.SubElement(
        tree.getroot(),
        "testcase",
        classname="tests.server.test_redis_mysql_account_store",
        name="test_another_snapshot",
    )
    ET.SubElement(case, status)
    tree.write(path)
    with pytest.raises(RuntimeError, match="test_another_snapshot did not pass"):
        ci.verify(path)
