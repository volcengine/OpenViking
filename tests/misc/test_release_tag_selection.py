import sys
from pathlib import Path
from types import ModuleType

import pytest

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
    from build_support import versioning

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


def test_release_version_must_be_an_exact_head_tag() -> None:
    from build_support.release_version import build_environment

    with pytest.raises(ValueError, match="expected vX.Y.Z"):
        build_environment("0.4.16", "", {"v0.4.16"})
    with pytest.raises(ValueError, match="does not point at HEAD"):
        build_environment("v0.4.16", "", {"v0.4.15"})


def test_non_release_build_keeps_scm_version() -> None:
    from build_support.release_version import build_environment

    assert build_environment("", "0.4.17.dev3", ()) == {
        "OPENVIKING_VERSION": "0.4.17.dev3"
    }


def test_release_build_configures_python_and_cli_versions() -> None:
    from build_support.release_version import build_environment

    assert build_environment("v0.4.16", "", {"v0.4.16"}) == {
        "OPENVIKING_VERSION": "0.4.16",
        "SETUPTOOLS_SCM_PRETEND_VERSION_FOR_OPENVIKING": "0.4.16",
    }
