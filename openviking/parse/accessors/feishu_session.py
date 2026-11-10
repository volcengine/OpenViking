# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Request-scoped Feishu configuration and API clients."""

import os
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from typing import Any, Generator, Optional
from urllib.parse import urlparse

from lark_oapi.core.cache import ICache

from openviking_cli.utils.config.parser_config import FeishuConfig

from .feishu_token import (
    resolve_feishu_tenant_token_cache,
)


def _default_api_domain(source_url: str) -> str:
    host = (urlparse(source_url).hostname or "").lower()
    if host.endswith(("larksuite.com", "larkoffice.com")):
        return "https://open.larksuite.com"
    return "https://open.feishu.cn"


@dataclass(frozen=True, slots=True)
class FeishuAccessContext:
    """Immutable configuration snapshot for one accessor operation."""

    config: FeishuConfig = field(repr=False)

    @classmethod
    def from_request(
        cls,
        source_url: str,
        *,
        config: Optional[FeishuConfig] = None,
    ) -> "FeishuAccessContext":
        effective_config = config
        if effective_config is None:
            effective_config = replace(
                FeishuConfig(),
                domain=_default_api_domain(source_url),
            )
        return cls(config=effective_config)


class FeishuApiSession:
    """Own lark-oapi clients for exactly one accessor operation."""

    def __init__(
        self,
        context: FeishuAccessContext,
        *,
        tenant_token_cache: ICache | None = None,
    ):
        self.context = context
        self._clients: dict[bool, Any] = {}
        self._tenant_token_cache = resolve_feishu_tenant_token_cache(tenant_token_cache)

    def client(self, *, use_user_token: bool) -> Any:
        cached = self._clients.get(use_user_token)
        if cached is not None:
            return cached

        try:
            import lark_oapi as lark
        except ImportError:
            raise ImportError(
                "lark-oapi is required for Feishu document parsing. "
                "Install it with: pip install lark-oapi>=1.0.0"
            )

        builder = (
            lark.Client.builder()
            .domain(self.context.config.domain)
            .timeout(self.context.config.request_timeout)
        )
        if use_user_token:
            builder = builder.enable_set_token(True)
        else:
            config = self.context.config
            app_id = config.app_id or os.getenv("FEISHU_APP_ID", "")
            app_secret = config.app_secret or os.getenv("FEISHU_APP_SECRET", "")
            if not app_id or not app_secret:
                raise ValueError(
                    "Feishu credentials not configured. Set FEISHU_APP_ID and "
                    "FEISHU_APP_SECRET environment variables, or configure in ov.conf."
                )
            builder = (
                builder.app_id(app_id)
                .app_secret(app_secret)
                .cache(self._tenant_token_cache)
            )

        client = builder.build()
        self._clients[use_user_token] = client
        return client

    @contextmanager
    def tenant_token_cache_scope(self) -> Generator[None, None, None]:
        """Bind Account credentials while lark-oapi obtains a tenant token."""
        config = self.context.config
        app_id = config.app_id or os.getenv("FEISHU_APP_ID", "")
        app_secret = config.app_secret or os.getenv("FEISHU_APP_SECRET", "")
        if not app_id or not app_secret:
            raise ValueError(
                "Feishu credentials not configured. Set FEISHU_APP_ID and "
                "FEISHU_APP_SECRET environment variables, or configure in ov.conf."
            )
        with self._tenant_token_cache.sdk_scope(
            app_id=app_id,
            app_secret=app_secret,
            domain=config.domain,
        ):
            yield
