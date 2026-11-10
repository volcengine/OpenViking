# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""
Transaction module for OpenViking.

Path-lock management has been moved to the Rust ragfs layer.
Python-side lock operations now go through RAGFSBindingClient.pathlock_* methods.
"""

__all__: list[str] = []
