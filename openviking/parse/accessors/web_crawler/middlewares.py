# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Scrapy downloader middlewares for the recursive web crawler."""

from urllib.parse import urlparse

from scrapy.exceptions import IgnoreRequest

from openviking.parse.accessors.web_crawler.resolver import pin_verified_address


class RequestValidatorMiddleware:
    """Validate and pin every outbound URL, including redirects and retries."""

    def process_request(self, request, spider):
        validator = getattr(spider.config, "request_validator", None)
        if validator is None:
            return None
        try:
            verified_address = validator(request.url)
            if verified_address is not None:
                host = urlparse(request.url).hostname
                if not host:
                    raise ValueError("request URL has no destination host")
                pin_verified_address(host, verified_address)
        except Exception as exc:
            raise IgnoreRequest(f"Blocked by request_validator: {exc}") from exc
        return None
