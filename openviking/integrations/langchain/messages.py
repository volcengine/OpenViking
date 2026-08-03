# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Compatibility wrapper for :mod:`langchain_openviking.messages`."""

from openviking.integrations.langchain import _forward_legacy_module

_forward_legacy_module("messages", globals())
