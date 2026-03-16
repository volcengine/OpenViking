import importlib
import importlib.util
import platform
import sys
import types
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
ENGINE_INIT = REPO_ROOT / "openviking" / "storage" / "vectordb" / "engine" / "__init__.py"


def _install_package_stubs(monkeypatch):
    packages = {
        "openviking": REPO_ROOT / "openviking",
        "openviking.storage": REPO_ROOT / "openviking" / "storage",
        "openviking.storage.vectordb": REPO_ROOT / "openviking" / "storage" / "vectordb",
    }
    for name, path in packages.items():
        module = types.ModuleType(name)
        module.__path__ = [str(path)]  # type: ignore[attr-defined]
        monkeypatch.setitem(sys.modules, name, module)


def _load_engine_module(
    monkeypatch, *, machine, available_backends, cpu_variants, env_variant=None
):
    _install_package_stubs(monkeypatch)

    monkeypatch.setattr(platform, "machine", lambda: machine)
    if env_variant is None:
        monkeypatch.delenv("OV_ENGINE_VARIANT", raising=False)
    else:
        monkeypatch.setenv("OV_ENGINE_VARIANT", env_variant)

    original_import_module = importlib.import_module
    original_find_spec = importlib.util.find_spec

    def fake_import_module(name, package=None):
        if package == "openviking.storage.vectordb.engine" and name == "._x86_caps":
            caps = types.SimpleNamespace(
                get_supported_variants=lambda: list(cpu_variants),
            )
            return caps

        if package == "openviking.storage.vectordb.engine" and name.startswith("._"):
            backend_name = name[2:].lstrip("_")
            if backend_name not in available_backends:
                raise ModuleNotFoundError(name)
            return types.SimpleNamespace(
                BACKEND_NAME=backend_name,
                IndexEngine=f"IndexEngine:{backend_name}",
                PersistStore=f"PersistStore:{backend_name}",
                VolatileStore=f"VolatileStore:{backend_name}",
            )

        return original_import_module(name, package)

    def fake_find_spec(name, package=None):
        fullname = importlib.util.resolve_name(name, package) if name.startswith(".") else name
        if fullname == "openviking.storage.vectordb.engine._x86_caps":
            return object()
        if fullname.startswith("openviking.storage.vectordb.engine."):
            backend_name = fullname.rsplit(".", 1)[-1].lstrip("_")
            if backend_name in available_backends:
                return object()
            return None
        return original_find_spec(name, package)

    monkeypatch.setattr(importlib, "import_module", fake_import_module)
    monkeypatch.setattr(importlib.util, "find_spec", fake_find_spec)

    spec = importlib.util.spec_from_file_location(
        "openviking.storage.vectordb.engine",
        ENGINE_INIT,
        submodule_search_locations=[str(ENGINE_INIT.parent)],
    )
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, "openviking.storage.vectordb.engine", module)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_engine_loader_auto_selects_best_supported_x86_backend(monkeypatch):
    module = _load_engine_module(
        monkeypatch,
        machine="x86_64",
        available_backends={"x86_sse3", "x86_avx2", "x86_avx512"},
        cpu_variants={"x86_sse3", "x86_avx2"},
    )

    assert module.ENGINE_VARIANT == "x86_avx2"
    assert module.IndexEngine == "IndexEngine:x86_avx2"
    assert module.AVAILABLE_ENGINE_VARIANTS == ("x86_sse3", "x86_avx2", "x86_avx512")


def test_engine_loader_uses_native_backend_on_non_x86(monkeypatch):
    module = _load_engine_module(
        monkeypatch,
        machine="arm64",
        available_backends={"native"},
        cpu_variants=set(),
    )

    assert module.ENGINE_VARIANT == "native"
    assert module.PersistStore == "PersistStore:native"
    assert module.AVAILABLE_ENGINE_VARIANTS == ("native",)


def test_engine_loader_rejects_forced_unsupported_variant(monkeypatch):
    with pytest.raises(ImportError, match="x86_avx512"):
        _load_engine_module(
            monkeypatch,
            machine="x86_64",
            available_backends={"x86_sse3", "x86_avx2"},
            cpu_variants={"x86_sse3", "x86_avx2"},
            env_variant="x86_avx512",
        )
