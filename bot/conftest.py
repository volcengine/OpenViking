"""Shared isolation for both standalone VikingBot test trees."""

import os
import tempfile
from pathlib import Path


# Keep the bot harness from reading a developer's personal CLI profile. Tests
# that need a profile override this environment variable explicitly.
_TEST_CONFIG_TMP = tempfile.TemporaryDirectory(prefix="openviking-bot-config-")
_TEST_CLI_CONFIG_PATH = Path(_TEST_CONFIG_TMP.name) / "ovcli.conf"
_TEST_CLI_CONFIG_PATH.write_text("{}\n", encoding="utf-8")
_TEST_CLI_CONFIG_PATH.chmod(0o600)
os.environ["OPENVIKING_CLI_CONFIG_FILE"] = str(_TEST_CLI_CONFIG_PATH)
