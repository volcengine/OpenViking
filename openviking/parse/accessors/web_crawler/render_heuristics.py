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
SHELL_VISIBLE_TEXT_CHARS = 40

# Text markers of JS anti-bot interstitials that briefly show before the real
# page loads (e.g. volcengine's proof-of-work gate renders only "Please wait...").
CHALLENGE_MARKERS = (
    "please wait",
    "checking your browser",
    "verifying you are human",
    "just a moment",
    "attention required",
)


def should_render_with_playwright(html: str) -> bool:
    """Return True when static HTML looks like a client-rendered shell."""
    html = html or ""
    html_lower = html.lower()
    if any(pattern.lower() in html_lower for pattern in _SPA_EMPTY_PATTERNS):
        return True
    visible_len = visible_body_text_len(html)
    if visible_len < SHELL_VISIBLE_TEXT_CHARS:
        return True
    # __NEXT_DATA__ only signals a Next.js app; most such pages are SSR/SSG and
    # already ship full body text in the static HTML. Only render when the
    # static body is also too thin to be the real content.
    if "__next_data__" in html_lower and visible_len < _MIN_VISIBLE_TEXT_CHARS:
        return True
    if re.search(r'id=["\'](?:root|app)["\']', html_lower) and visible_len < _MIN_VISIBLE_TEXT_CHARS:
        return True
    if html_lower.count("<script") >= 5 and visible_len < _MIN_VISIBLE_TEXT_CHARS:
        return True
    return False


def looks_like_unrendered_page(html: str) -> bool:
    """Return True when HTML is an empty shell or an anti-bot challenge page.

    Used to reject content that must not be stored as real page text: the
    static SPA shell or a JS interstitial such as "Please wait...".
    """
    try:
        html = html or ""
        soup = BeautifulSoup(html, "html.parser")
        body = soup.body or soup
        for el in body(["script", "style", "noscript"]):
            el.decompose()
        text = body.get_text(strip=True)
        lowered = text.lower()
        if any(marker in lowered for marker in CHALLENGE_MARKERS):
            return True
        return len(text) < SHELL_VISIBLE_TEXT_CHARS
    except Exception:
        return True


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
