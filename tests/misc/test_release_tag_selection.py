import sys
from pathlib import Path
from types import ModuleType

ROOT = Path(__file__).resolve().parents[2]


def test_main_package_versioning_ignores_non_main_release_tags() -> None:
    pyproject = (ROOT / "pyproject.toml").read_text()

    assert 'tag_regex = "^v(?P<version>[0-9]+(?:\\\\.[0-9]+)*)$"' in pyproject
    assert (
        'git_describe_command = "git describe --dirty --tags --long --match v[0-9]*"' in pyproject
    )


def test_python_sdk_versioning_uses_sdk_only_at_sign_tags() -> None:
    pyproject = (ROOT / "sdk/python/pyproject.toml").read_text()

    assert 'tag_regex = "^python-sdk@(?P<version>[0-9]+(?:\\\\.[0-9]+)*)$"' in pyproject
    assert (
        'git_describe_command = "git describe --dirty --tags --long --match python-sdk@*"'
        in pyproject
    )
    assert "python-sdk/v" not in pyproject


def test_build_support_versioning_uses_main_release_tags_only(monkeypatch) -> None:
    from scripts.build_support import versioning

    captured_kwargs = {}
    fake_setuptools_scm = ModuleType("setuptools_scm")

    def fake_get_version(**kwargs):
        captured_kwargs.update(kwargs)
        return "0.3.18"

    fake_setuptools_scm.get_version = fake_get_version
    monkeypatch.setitem(sys.modules, "setuptools_scm", fake_setuptools_scm)

    assert versioning._get_scm_version(ROOT) == "0.3.18"
    assert captured_kwargs["tag_regex"] == r"^v(?P<version>[0-9]+(?:\.[0-9]+)*)$"
    assert (
        captured_kwargs["git_describe_command"]
        == "git describe --dirty --tags --long --match v[0-9]*"
    )


def test_release_build_pins_cli_and_wheel_to_git_version() -> None:
    workflow = (ROOT / ".github/workflows/_build.yml").read_text()

    resolve_command = (
        "env -u OPENVIKING_VERSION "
        "-u SETUPTOOLS_SCM_PRETEND_VERSION_FOR_OPENVIKING "
        "uv run --no-sync python -c"
    )
    pretend_version_export = (
        'echo "SETUPTOOLS_SCM_PRETEND_VERSION_FOR_OPENVIKING=$OPENVIKING_VERSION" >> "$GITHUB_ENV"'
    )

    assert workflow.count(resolve_command) == 2
    assert workflow.count(pretend_version_export) == 2
    assert workflow.count('git describe --tags --exact-match "$GITHUB_SHA"') == 2
    assert workflow.count("rm -f openviking/_version.py") == 2
    assert workflow.count("rm -rf openviking.egg-info") == 2
