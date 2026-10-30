import json
import sys
from types import SimpleNamespace

import pytest

from benchmark.aml import runtime


def config(tmp_path):
    path = tmp_path / "native.conf"
    path.write_text(
        json.dumps(
            {
                "vlm": {"model": "source-fixture"},
                "embedding": {"hybrid": {"model": "embedding-fixture"}},
            }
        )
    )
    return path


@pytest.mark.parametrize("service", ["native", "adapter"])
def test_wrapper_classifies_configured_models_and_forwards_server_arguments(
    tmp_path, monkeypatch, service
):
    path = config(tmp_path)
    database = tmp_path / "shared.sqlite"
    budgets = tmp_path / "budgets.json"
    installs = []
    calls = []
    monkeypatch.setattr(runtime, "install", lambda *args, **kwargs: installs.append((args, kwargs)))
    monkeypatch.setattr(
        runtime.importlib,
        "import_module",
        lambda name: SimpleNamespace(main=lambda: calls.append((name, sys.argv[1:]))),
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "runtime",
            service,
            "--native-config",
            str(path),
            "--admission-db",
            str(database),
            "--admission-budgets",
            str(budgets),
            "--host",
            "127.0.0.1",
            "--port",
            "9000",
        ],
    )
    runtime.main()
    assert installs == [
        (
            ("source-fixture", "embedding-fixture"),
            {
                "native": service == "native",
                "phase": f"aml-{service}",
                "database": database,
                "budget_file": budgets,
            },
        )
    ]
    expected = ["--host", "127.0.0.1", "--port", "9000"]
    if service == "native":
        expected = ["--config", str(path), *expected]
    assert calls == [
        ("openviking.server.bootstrap" if service == "native" else "benchmark.aml.server", expected)
    ]


def test_wrapper_requires_explicit_shared_database_before_starting_services(tmp_path, monkeypatch):
    monkeypatch.delenv("AML_MODEL_ADMISSION_DB", raising=False)
    monkeypatch.setattr(
        sys, "argv", ["runtime", "adapter", "--native-config", str(config(tmp_path))]
    )
    monkeypatch.setattr(
        runtime, "install", lambda *args, **kwargs: pytest.fail("No implicit ledger")
    )
    with pytest.raises(SystemExit) as error:
        runtime.main()
    assert error.value.code == 2
