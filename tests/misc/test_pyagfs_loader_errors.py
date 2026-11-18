"""Retain native loading causes through the public binding entry points."""

import builtins
from types import SimpleNamespace

import pytest

import openviking.pyagfs as pyagfs


@pytest.fixture
def installed_import_failure(monkeypatch):
    original = builtins.__import__
    failure = ImportError("synthetic dependency could not be loaded")

    def load(name, *args, **kwargs):
        if name == "ragfs_python":
            raise failure
        return original(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", load)
    return failure


def test_missing_artifact_retains_installed_import_cause(
    tmp_path, monkeypatch, installed_import_failure
):
    monkeypatch.setattr(pyagfs, "_LIB_DIR", tmp_path)
    with pytest.raises(ImportError) as raised:
        pyagfs.get_binding_client()
    assert "no compatible vendored native extension" in str(raised.value)
    assert str(tmp_path) in str(raised.value)
    assert "synthetic dependency" in str(raised.value)
    assert raised.value.__cause__.__cause__ is installed_import_failure


def test_corrupt_native_artifact_preserves_real_loader_failure(
    tmp_path, monkeypatch, installed_import_failure
):
    path = tmp_path / "ragfs_python.abi3.so"
    path.write_bytes(b"synthetic invalid native artifact")
    monkeypatch.setattr(pyagfs, "_LIB_DIR", tmp_path)
    with pytest.raises(ImportError) as raised:
        pyagfs.get_binding_client()
    assert str(path) in str(raised.value)
    native = raised.value.__cause__.__cause__
    assert isinstance(native, ImportError)
    assert str(native) in str(raised.value)
    assert native is not installed_import_failure


def test_broken_installed_extension_still_allows_valid_vendored_fallback(
    monkeypatch, installed_import_failure
):
    client = type("SyntheticClient", (), {})
    module = SimpleNamespace()
    loader = SimpleNamespace(
        exec_module=lambda loaded: setattr(loaded, "RAGFSBindingClient", client)
    )
    monkeypatch.setattr(pyagfs, "_find_ragfs_so", lambda: "synthetic.abi3.so")
    monkeypatch.setattr(
        pyagfs.importlib.util, "spec_from_file_location", lambda *a: SimpleNamespace(loader=loader)
    )
    monkeypatch.setattr(pyagfs.importlib.util, "module_from_spec", lambda spec: module)
    assert pyagfs.get_binding_client() == (client, None)


def test_successful_installed_extension_keeps_precedence(monkeypatch):
    original = builtins.__import__
    client = type("SyntheticClient", (), {})

    def load(name, *args, **kwargs):
        return (
            SimpleNamespace(RAGFSBindingClient=client)
            if name == "ragfs_python"
            else original(name, *args, **kwargs)
        )

    monkeypatch.setattr(builtins, "__import__", load)
    monkeypatch.setattr(pyagfs, "_find_ragfs_so", lambda: pytest.fail("installed import must win"))
    assert pyagfs.get_binding_client() == (client, None)


def test_missing_loader_spec_is_an_actionable_import_error(monkeypatch, installed_import_failure):
    monkeypatch.setattr(pyagfs, "_find_ragfs_so", lambda: "synthetic.abi3.so")
    monkeypatch.setattr(pyagfs.importlib.util, "spec_from_file_location", lambda *a: None)
    with pytest.raises(ImportError, match="Unable to load Rust binding spec"):
        pyagfs.get_binding_client()
