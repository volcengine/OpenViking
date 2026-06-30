# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Heuristics for deciding whether a page likely needs browser rendering."""

import re

from bs4 import BeautifulSoup


_SPA_EMPTY_PATTERNS = (
    "You need to enable JavaScript to run this app.",
    "This app works best with JavaScript enabled.",
    "Please enable JavaScript to continue.",
    "JavaScript is required to use this application.",
    "Enable JavaScript to view this page.",
)

_MIN_VISIBLE_TEXT_CHARS = 200


def should_render_with_playwright(html: str) -> bool:
    """Return True when static HTML looks like a client-rendered shell."""
    html = html or ""
    html_lower = html.lower()
    if any(pattern.lower() in html_lower for pattern in _SPA_EMPTY_PATTERNS):
        return True
    if re.search(r'id=["\'](?:root|app)["\']', html_lower):
        return True
    if "__next_data__" in html_lower:
        return True
    if html_lower.count("<script") >= 5 and visible_body_text_len(html) < _MIN_VISIBLE_TEXT_CHARS:
        return True
    return False


def visible_body_text_len(html: str) -> int:
    """Approximate visible text length without running page JavaScript."""
    try:
        soup = BeautifulSoup(html or "", "html.parser")
        body = soup.body or soup
        for el in body(["script", "style", "noscript"]):
            el.decompose()
        return len(body.get_text(strip=True))
    except Exception:
        return 0
