"""The standalone bot harness must not inherit a developer's CLI profile."""

from __future__ import annotations

import json
import os
from pathlib import Path


def test_harness_uses_private_disposable_ovcli_config() -> None:
    """Offline bot tests must resolve a private empty profile by default."""
    config_path = Path(os.environ["OPENVIKING_CLI_CONFIG_FILE"])

    assert config_path.name == "ovcli.conf"
    assert config_path.is_file()
    assert config_path.stat().st_mode & 0o777 == 0o600
    assert json.loads(config_path.read_text(encoding="utf-8")) == {}
    assert config_path != Path.home() / ".openviking" / "ovcli.conf"
