"""Regression checks for dependencies shared by the bot extra."""

import importlib.metadata
import inspect
import re
from pathlib import Path

import charset_normalizer
import requests
import urllib3
from openviking_sdk.client import AsyncHTTPClient

ROOT = Path(__file__).resolve().parents[2]


def _locked_version(package: str) -> str:
    lock = (ROOT / "uv.lock").read_text()
    match = re.search(
        rf'^\[\[package\]\]\nname = "{re.escape(package)}"\nversion = "([^"]+)"',
        lock,
        re.MULTILINE,
    )
    assert match is not None, f"{package} is missing from uv.lock"
    return match.group(1)


def test_bot_extra_locked_chardet_is_supported_by_requests() -> None:
    """Importing Requests must tolerate chardet selected by readability-lxml."""
    assert requests.__version__ == _locked_version("requests")
    requests.check_compatibility(
        urllib3.__version__,
        _locked_version("chardet"),
        charset_normalizer.__version__,
    )


def test_locked_openviking_sdk_supports_remote_skill_integrity() -> None:
    """The SDK installed with VikingBot must accept its remote-Skill request."""
    assert importlib.metadata.version("openviking-sdk") == _locked_version("openviking-sdk")
    inspect.signature(AsyncHTTPClient.get_skill).bind_partial(
        None,
        "example-skill",
        target_uri="viking://agent/skills",
        include_integrity=True,
    )
