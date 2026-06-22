from pathlib import Path

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
