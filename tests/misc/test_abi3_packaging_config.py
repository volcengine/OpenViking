from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


def _read_text(relative_path: str) -> str:
    return (REPO_ROOT / relative_path).read_text(encoding="utf-8")


def test_packaging_only_includes_abi3_engine_extensions():
    setup_py = _read_text("setup.py")
    pyproject = _read_text("pyproject.toml")

    assert "storage/vectordb/engine/*.abi3.so" in setup_py
    assert "storage/vectordb/engine/*.abi3.so" in pyproject
    assert "storage/vectordb/engine/*.so" not in setup_py
    assert "storage/vectordb/engine/*.so" not in pyproject


def test_release_workflows_default_to_single_cp310_and_drop_pybind11():
    build_workflow = _read_text(".github/workflows/_build.yml")
    release_workflow = _read_text(".github/workflows/release.yml")
    lite_workflow = _read_text(".github/workflows/_test_lite.yml")
    full_workflow = _read_text(".github/workflows/_test_full.yml")
    codeql_workflow = _read_text(".github/workflows/_codeql.yml")
    uv_lock = _read_text("uv.lock")

    assert "default: '[\"3.10\"]'" in build_workflow
    assert "default: '[\"3.10\"]'" in release_workflow
    assert "python_json: ${{ inputs.python_json || '[\"3.10\"]' }}" in release_workflow

    for workflow_text in (
        build_workflow,
        lite_workflow,
        full_workflow,
        codeql_workflow,
    ):
        assert "pybind11" not in workflow_text
    assert 'name = "pybind11"' not in uv_lock


def test_release_build_workflow_no_longer_defines_extra_wheel_verify_jobs():
    build_workflow = _read_text(".github/workflows/_build.yml")

    assert "verify-linux-abi3-wheel:" not in build_workflow
    assert "verify-macos-14-wheel-on-macos-15:" not in build_workflow


def test_abi3_backend_releases_gil_and_rejects_invalid_storage_op_type():
    backend_source = _read_text("src/abi3_engine_backend.cpp")

    assert "PyEval_SaveThread" in backend_source
    assert "PyEval_RestoreThread" in backend_source
    assert "Invalid storage op type" in backend_source


def test_repo_no_longer_contains_pybind11_engine_bindings():
    assert not (REPO_ROOT / "src" / "pybind11_interface.cpp").exists()
    assert not (REPO_ROOT / "src" / "cpu_feature_probe.cpp").exists()
    assert not (REPO_ROOT / "src" / "py_accessors.h").exists()


def test_python_engine_exports_only_live_abi3_api():
    import openviking.storage.vectordb.engine as engine

    assert not hasattr(engine, "FetchDataResult")
