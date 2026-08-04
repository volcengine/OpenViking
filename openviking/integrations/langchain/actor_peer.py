# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Compatibility wrapper for :mod:`langchain_openviking.actor_peer`."""

from openviking.integrations.langchain import _forward_legacy_module

_forward_legacy_module("actor_peer", globals())
