# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Extract structured data from SSR-embedded JSON in SPA pages."""

import json
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional
from urllib.parse import urljoin

from openviking_cli.utils.logger import get_logger

logger = get_logger(__name__)


@dataclass
class SSRDocInfo:
    title: str = ""
    url: str = ""
    content_type: Optional[str] = None
    content: Optional[str] = None


@dataclass
class SSRExtractResult:
    source: str = ""
    child_urls: List[str] = field(default_factory=list)
    docs: List[SSRDocInfo] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)


class SSRDataExtractor:
    SSR_PATTERNS: List[tuple] = [
        (
            r"window\._ROUTER_DATA\s*=\s*(\{[\s\S]*?\})\s*</script>",
            "_extract_volcengine",
        ),
        (
            r"__NEXT_DATA__\s*=\s*(\{[\s\S]*?\})\s*</script>",
            "_extract_nextjs",
        ),
        (
            r'<script type="application/ld\+json">\s*(\{[\s\S]*?\})\s*</script>',
            "_extract_jsonld",
        ),
    ]

    def extract(self, html: str, base_url: str) -> Optional[SSRExtractResult]:
        for pattern, handler_name in self.SSR_PATTERNS:
            match = re.search(pattern, html, re.DOTALL)
            if not match:
                continue
            handler = getattr(self, handler_name, None)
            if not handler:
                continue
            try:
                data = json.loads(match.group(1))
                result = handler(data, base_url)
                if result and (result.child_urls or result.docs):
                    logger.debug(
                        f"[SSR] Extracted from {handler_name}: "
                        f"urls={len(result.child_urls)} docs={len(result.docs)}"
                    )
                    return result
            except (json.JSONDecodeError, Exception) as e:
                logger.debug(f"[SSR] Failed to parse {handler_name}: {e}")
                continue
        return None

    def _extract_volcengine(
        self, data: dict, base_url: str
    ) -> Optional[SSRExtractResult]:
        loader = data.get("loaderData", {})
        child_urls: List[str] = []
        docs: List[SSRDocInfo] = []

        for _key, page_data in loader.items():
            if not isinstance(page_data, dict):
                continue

            doc_list_map = page_data.get("docListMap", {})
            cur_lib = page_data.get("curLib", {})
            lib_id = cur_lib.get("LibraryID")

            if doc_list_map and lib_id:
                for _nav_id, sections in doc_list_map.items():
                    self._collect_volcengine_docs(sections, lib_id, child_urls)

            cur_doc = page_data.get("curDoc", {})
            if cur_doc and cur_doc.get("Content"):
                docs.append(
                    SSRDocInfo(
                        title=cur_doc.get("Title", ""),
                        url=base_url,
                        content_type=cur_doc.get("ContentType"),
                        content=cur_doc["Content"],
                    )
                )

        if child_urls or docs:
            return SSRExtractResult(
                source="volcengine_ssr",
                child_urls=child_urls,
                docs=docs,
            )
        return None

    def _collect_volcengine_docs(
        self, sections: Any, lib_id: Any, child_urls: List[str]
    ) -> None:
        if not isinstance(sections, dict):
            return
        for _section_id, section_data in sections.items():
            if isinstance(section_data, dict) and "value" in section_data:
                doc = section_data["value"]
                doc_id = doc.get("DocumentID")
                if doc_id:
                    child_urls.append(
                        f"https://www.volcengine.com/docs/{lib_id}/{doc_id}"
                    )
                for child_id in section_data.get("children", []):
                    child_urls.append(
                        f"https://www.volcengine.com/docs/{lib_id}/{child_id}"
                    )
            elif isinstance(section_data, list):
                for child_id in section_data:
                    if isinstance(child_id, int):
                        child_urls.append(
                            f"https://www.volcengine.com/docs/{lib_id}/{child_id}"
                        )

    def _extract_nextjs(
        self, data: dict, base_url: str
    ) -> Optional[SSRExtractResult]:
        props = data.get("props", {}).get("pageProps", {})
        child_urls: List[str] = []

        for _key, value in props.items():
            if isinstance(value, dict):
                links = value.get("links", [])
                for link in links:
                    href = link.get("href", "")
                    if href.startswith("http"):
                        child_urls.append(href)
                    elif href.startswith("/"):
                        child_urls.append(urljoin(base_url, href))

        if child_urls:
            return SSRExtractResult(source="nextjs_ssr", child_urls=child_urls)
        return None

    def _extract_jsonld(
        self, data: dict, base_url: str
    ) -> Optional[SSRExtractResult]:
        child_urls: List[str] = []
        if data.get("@type") == "ItemList":
            for item in data.get("itemListElement", []):
                url = item.get("url", "")
                if url:
                    child_urls.append(url)
        if child_urls:
            return SSRExtractResult(source="jsonld", child_urls=child_urls)
        return None
