# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Scrapy downloader middlewares for the recursive web crawler."""

from scrapy.exceptions import IgnoreRequest


class RequestValidatorMiddleware:
    """Pre-flight SSRF guard: validate every outbound URL, including redirects/retries."""

    def process_request(self, request, spider):
        validator = getattr(spider.config, "request_validator", None)
        if validator is None:
            return None
        try:
            validator(request.url)
        except Exception as exc:
            raise IgnoreRequest(f"Blocked by request_validator: {exc}") from exc
        return None
