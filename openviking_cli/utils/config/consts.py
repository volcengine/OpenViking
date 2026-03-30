# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Configuration constants for OpenViking."""

from pathlib import Path

DEFAULT_CONFIG_DIR = Path.home() / ".openviking"
SYSTEM_CONFIG_DIR = Path("/etc/openviking")

OPENVIKING_CONFIG_ENV = "OPENVIKING_CONFIG_FILE"
OPENVIKING_CLI_CONFIG_ENV = "OPENVIKING_CLI_CONFIG_FILE"
OPENVIKING_PROMPT_TEMPLATES_DIR_ENV = "OPENVIKING_PROMPT_TEMPLATES_DIR"

DEFAULT_OV_CONF = "ov.conf"
DEFAULT_OVCLI_CONF = "ovcli.conf"
