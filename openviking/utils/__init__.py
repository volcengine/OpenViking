# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""Utility functions and helpers."""

from openviking.utils.code_hosting_utils import (
    is_code_hosting_url,
    is_github_url,
    is_gitlab_url,
    parse_code_hosting_url,
)
from openviking.utils.time_utils import get_current_timestamp
from openviking_cli.utils.async_utils import run_async
from openviking_cli.utils.llm import StructuredLLM, parse_json_from_response, parse_json_to_model
from openviking_cli.utils.logger import default_logger, get_logger
from openviking_cli.utils.uri import VikingURI

__all__ = [
    "VikingURI",
    "get_logger",
    "default_logger",
    "get_current_timestamp",
    "StructuredLLM",
    "parse_json_from_response",
    "parse_json_to_model",
    "run_async",
    "parse_code_hosting_url",
    "is_github_url",
    "is_gitlab_url",
    "is_code_hosting_url",
]
