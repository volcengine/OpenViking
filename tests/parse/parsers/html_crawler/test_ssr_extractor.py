# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Tests for SSR embedded data extraction."""

import json

from openviking.parse.parsers.html_crawler.ssr_extractor import SSRDataExtractor


def test_extract_volcengine_router_data_returns_doc_and_child_urls():
    data = {
        "loaderData": {
            "page": {
                "curLib": {"LibraryID": 84313},
                "docListMap": {
                    "nav": {
                        "section": {
                            "value": {"DocumentID": 1001},
                            "children": [1002],
                        }
                    }
                },
                "curDoc": {
                    "Title": "Quick Start",
                    "ContentType": "markdown",
                    "Content": "# Quick Start",
                },
            }
        }
    }
    html = f"<script>window._ROUTER_DATA = {json.dumps(data)}</script>"

    result = SSRDataExtractor().extract(html, "https://www.volcengine.com/docs/84313/1000")

    assert result is not None
    assert result.source == "volcengine_ssr"
    assert result.child_urls == [
        "https://www.volcengine.com/docs/84313/1001",
        "https://www.volcengine.com/docs/84313/1002",
    ]
    assert len(result.docs) == 1
    assert result.docs[0].title == "Quick Start"
    assert result.docs[0].content == "# Quick Start"


def test_extract_jsonld_item_list_returns_child_urls():
    data = {
        "@type": "ItemList",
        "itemListElement": [
            {"url": "https://example.com/docs/a"},
            {"url": "https://example.com/docs/b"},
        ],
    }
    html = (
        '<script type="application/ld+json">'
        f"{json.dumps(data)}"
        "</script>"
    )

    result = SSRDataExtractor().extract(html, "https://example.com/docs")

    assert result is not None
    assert result.source == "jsonld"
    assert result.child_urls == [
        "https://example.com/docs/a",
        "https://example.com/docs/b",
    ]
