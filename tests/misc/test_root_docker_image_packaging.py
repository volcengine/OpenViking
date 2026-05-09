import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


def _read_text(relative_path: str) -> str:
    return (REPO_ROOT / relative_path).read_text(encoding="utf-8")


def _extract_rust_version(pattern: str, text: str) -> str:
    match = re.search(pattern, text, re.MULTILINE)
    assert match, f"Pattern not found: {pattern}"
    return match.group("version")


def test_root_dockerfile_copies_bot_sources_into_build_context():
    dockerfile = _read_text("Dockerfile")

    assert "COPY bot/ bot/" in dockerfile


def test_dockerfile_and_makefile_share_the_same_minimum_rust_version():
    dockerfile = _read_text("Dockerfile")
    makefile = _read_text("Makefile")

    docker_rust_version = _extract_rust_version(
        r"^FROM rust:(?P<version>[0-9.]+)-trixie AS rust-toolchain$",
        dockerfile,
    )
    make_rust_version = _extract_rust_version(
        r"^MIN_RUST_VERSION := (?P<version>[0-9.]+)$",
        makefile,
    )

    assert docker_rust_version == make_rust_version


def test_root_dockerfile_does_not_bake_zero_openviking_version_by_default():
    dockerfile = _read_text("Dockerfile")

    assert "ARG OPENVIKING_VERSION=0.0.0" not in dockerfile
    assert "ENV SETUPTOOLS_SCM_PRETEND_VERSION_FOR_OPENVIKING" not in dockerfile
    assert "COPY .git/ .git/" not in dockerfile
    assert 'if [ -n "${OPENVIKING_VERSION:-}" ]; then' in dockerfile
    assert "OPENVIKING_VERSION build arg is required" in dockerfile


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
    assert "sys.executable," in setup_py
    assert '"maturin",' in setup_py
    assert '"build",' in setup_py
    assert '"--release",' in setup_py
    assert '"--features",' in setup_py
    assert '"s3",' in setup_py
    assert '"--out",' in setup_py
    assert "tmpdir," in setup_py
    assert 'shutil.which("maturin")' not in setup_py


def test_root_build_system_honors_ci_compiler_overrides_and_requires_ragfs_for_wheels():
    setup_py = _read_text("setup.py")
    build_workflow = _read_text(".github/workflows/_build.yml")

    assert 'os.environ.get("CC")' in setup_py
    assert 'os.environ.get("CXX")' in setup_py
    assert "OV_REQUIRE_RAGFS_BUILD" in setup_py
    assert 'echo "CC=clang" >> "$GITHUB_ENV"' in build_workflow
    assert 'echo "CXX=clang++" >> "$GITHUB_ENV"' in build_workflow
    assert 'echo "OV_REQUIRE_RAGFS_BUILD=1" >> "$GITHUB_ENV"' in build_workflow


def test_rust_crates_declare_the_repo_minimum_rust_version():
    makefile = _read_text("Makefile")
    min_rust_version = _extract_rust_version(
        r"^MIN_RUST_VERSION := (?P<version>[0-9.]+)$",
        makefile,
    )

    for relative_path in (
        "crates/ov_cli/Cargo.toml",
        "crates/ragfs/Cargo.toml",
        "crates/ragfs-python/Cargo.toml",
    ):
        cargo_toml = _read_text(relative_path)
        assert f'rust-version = "{min_rust_version}"' in cargo_toml
