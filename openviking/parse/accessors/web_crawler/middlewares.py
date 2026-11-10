# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Scrapy downloader middlewares for the recursive web crawler."""

import ipaddress
from collections.abc import Mapping
from urllib.parse import urlparse
from urllib.request import proxy_bypass_environment

from scrapy.exceptions import IgnoreRequest

_PARSER_PROXY_MANAGED = "_openviking_parser_proxy_managed"


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


def _proxy_value(environment: Mapping[str, str], scheme: str) -> str | None:
    return environment.get(f"{scheme}_proxy") or environment.get(f"{scheme.upper()}_PROXY")


def _bypasses_proxy(environment: Mapping[str, str], host: str, port: int | None) -> bool:
    no_proxy = _proxy_value(environment, "no")
    if not no_proxy:
        return False
    host_with_port = f"{host}:{port}" if port is not None else host
    if proxy_bypass_environment(host_with_port, {"no": no_proxy}):
        return True
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return False
    for value in no_proxy.split(","):
        try:
            if address in ipaddress.ip_network(value.strip(), strict=False):
                return True
        except ValueError:
            continue
    return False


class ParserNetworkProxyMiddleware:
    """Apply the parser adapter's child-only proxy policy to each Scrapy request."""

    def process_request(self, request, spider):
        # Scrapy copies request.meta when following redirects.  Keep a marker
        # for requests whose proxy was selected here so a redirect can choose
        # the proxy for its *new* scheme instead of reusing the old one.  A
        # caller-supplied proxy remains authoritative and is never rewritten.
        managed = bool(request.meta.get(_PARSER_PROXY_MANAGED))
        if "proxy" in request.meta and not managed:
            return None
        environment = getattr(spider, "network_env", None) or {}
        parsed = urlparse(request.url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            return None
        if not any(_proxy_value(environment, scheme) for scheme in ("http", "https", "all")):
            # No parser proxy was supplied.  Leave Scrapy's normal
            # HttpProxyMiddleware behavior unchanged for deployments that do
            # not install the adapter.
            return None
        request.meta[_PARSER_PROXY_MANAGED] = True
        if _bypasses_proxy(environment, parsed.hostname, parsed.port):
            # ``HttpProxyMiddleware`` runs later and otherwise sees the child
            # proxy environment and installs a proxy again (notably for CIDR
            # NO_PROXY entries).  A None proxy is its documented opt-out.
            request.meta["proxy"] = None
            return None
        proxy = _proxy_value(environment, parsed.scheme) or _proxy_value(environment, "all")
        request.meta["proxy"] = proxy
        return None
