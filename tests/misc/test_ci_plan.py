"""Check which changes require the full Python regression suite."""

import runpy
from pathlib import Path

import pytest


plan = runpy.run_path(
    str(Path(__file__).resolve().parents[2] / ".github/scripts/ci_plan.py")
)["plan"]


@pytest.mark.parametrize(
    "path",
    [
        "openviking/service/core.py",
        "openviking_cli/utils/config/vlm_config.py",
        "sdk/python/openviking_sdk/client.py",
        "crates/ragfs/src/core/mountable.rs",
        "crates/ragfs-python/src/lib.rs",
        "src/index/index_engine.cpp",
        "third_party/CMakeLists.txt",
        "tests/unit/test_vlm_response_formats.py",
        "tests/storage/test_context_update_plan.py",
        "tests/misc/test_ci_plan.py",
        "pyproject.toml",
        "uv.lock",
        "Cargo.lock",
        ".github/workflows/_test_full.yml",
        ".github/actions/native-runtime/action.yml",
        ".github/scripts/ci_plan.py",
    ],
)
def test_python_and_shared_runtime_changes_run_python_tests(path):
    assert plan([path])["python_changed"]


@pytest.mark.parametrize(
    "path",
    [
        "crates/ov_cli/src/client.rs",
        "openviking_gateway/app.py",
        "tests/gateway/test_storage_ports.py",
        "examples/openclaw-plugin/index.ts",
        "web-studio/src/lib/admin.ts",
        "docs/zh/api/01-overview.md",
    ],
)
def test_independent_changes_skip_full_python_suite(path):
    assert not plan([path])["python_changed"]


def test_manual_runs_and_mixed_changes_keep_python_coverage():
    assert plan([], full=True)["python_changed"]
    assert plan(["sdk/python/openviking_sdk/client.py", "openviking/service/core.py"])[
        "python_changed"
    ]
    assert not plan([])["python_changed"]


@pytest.mark.parametrize(
    "path",
    [
        "tests/vectordb/test_cuvs_config.py",
        "tests/vectordb/test_cuvs_index.py",
        "tests/vectordb/test_cuvs_collection.py",
        "tests/vectordb/test_str_to_uint64.py",
        "openviking/storage/vectordb/index.py",
        "uv.lock",
    ],
)
def test_delegated_cuvs_regressions_still_run(path):
    assert plan([path])["cuvs_changed"]
