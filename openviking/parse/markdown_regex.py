# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Shared regular expressions for Markdown parsing and post-processing."""

import re

# Markdown image reference with one level of balanced parentheses in the target.
# Keeping this shared prevents ingestion and post-commit URI rewriting from
# disagreeing on the sidecar mapping key for paths like
# ``文档_17 (17号项目)/image1.png``.
MARKDOWN_IMAGE_PATTERN = re.compile(r"!\[([^\]]*)\]\(((?:[^()]|\([^()]*\))+)\)")
