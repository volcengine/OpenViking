from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


def _read_text(relative_path: str) -> str:
    return (REPO_ROOT / relative_path).read_text(encoding="utf-8")


def test_root_dockerfile_copies_bot_sources_into_build_context():
    dockerfile = _read_text("Dockerfile")

    assert "COPY bot/ bot/" in dockerfile


def test_openviking_package_includes_console_static_assets():
    pyproject = _read_text("pyproject.toml")
    setup_py = _read_text("setup.py")

    assert '"console/static/**/*"' in pyproject
    assert '"console/static/**/*"' in pyproject.split("vikingbot = [", maxsplit=1)[0]
    assert '"console/static/**/*"' in setup_py


def test_build_workflow_invokes_maturin_via_python_module():
    workflow = _read_text(".github/workflows/_build.yml")

    assert "Build ragfs-python and extract into openviking/lib/" not in workflow
    assert "uv run python -m maturin build --release" not in workflow
    assert "uv run python <<PY" not in workflow


def test_ragfs_python_uses_pyo3_version_with_python_314_support():
    cargo_toml = _read_text("crates/ragfs-python/Cargo.toml")

    assert 'pyo3 = { version = "0.27"' in cargo_toml


def test_root_build_system_includes_maturin_for_isolated_builds():
    pyproject = _read_text("pyproject.toml")
    setup_py = _read_text("setup.py")

    assert '"maturin>=1.0,<2.0",' in pyproject
    assert '[sys.executable, "-m", "maturin", "build", "--release", "--out", tmpdir]' in setup_py
    assert 'shutil.which("maturin")' not in setup_py
