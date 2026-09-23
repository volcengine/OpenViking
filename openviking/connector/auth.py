# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Task-scoped access to centrally managed Feishu OAuth credentials."""

import asyncio
import math
import time
from contextlib import asynccontextmanager, contextmanager
from contextvars import ContextVar
from threading import Lock
from typing import TYPE_CHECKING, Any, Dict, Optional

from openviking.connector.client import ConnectorClient
from openviking.server.identity import RequestContext
from openviking_cli.exceptions import InternalError, InvalidArgumentError

if TYPE_CHECKING:
    from openviking.parse.accessors.base import LocalResource

OAUTH_REF_ARG = "openviking_oauth_ref"
EXTERNAL_FEISHU_PROVIDER = "feishu_external"
current_feishu_token: ContextVar[Optional["ExternalFeishuToken"]] = ContextVar(
    "external_feishu_token", default=None
)


def external_auth_url() -> str:
    from openviking_cli.utils.config.open_viking_config import get_openviking_config

    return get_openviking_config().connector.auth


def validate_feishu_auth_args(args: Dict[str, Any]) -> None:
    if OAUTH_REF_ARG in args and any(
        key in args
        for key in (
            "feishu_access_token",
            "feishu_refresh_token",
            "feishu_app_id",
            "feishu_app_secret",
            "lark_file",
        )
    ):
        raise InvalidArgumentError(
            "OAuth references cannot be combined with explicit Feishu credentials."
        )


@asynccontextmanager
async def feishu_auth_scope(
    connector,
    *,
    path: str,
    ctx: RequestContext,
    args: Dict[str, Any],
    state: Optional[Dict[str, Any]] = None,
    prepared: bool = False,
):
    """Bind external auth for source preparation or a restored task; leave local auth alone."""
    validate_feishu_auth_args(args)
    provider = None
    if is_external_feishu_auth(state):
        if not prepared:
            api_key, restored_args = await restore_feishu_request(
                connector, state, path=path, ctx=ctx
            )
            provider = ExternalFeishuToken(restored_args[OAUTH_REF_ARG], api_key)
    elif OAUTH_REF_ARG in args:
        from openviking.parse.accessors.feishu_accessor import FeishuAccessor

        if not FeishuAccessor._is_feishu_url(path):
            raise InvalidArgumentError("OAuth references require a Feishu document URL.")
        reference = validate_oauth_ref(args.pop(OAUTH_REF_ARG), ctx.user.user_id)
        provider = ExternalFeishuToken(reference, ctx.api_key or "")
        state = {
            "provider": EXTERNAL_FEISHU_PROVIDER,
            "credentials": await connector.create_watch_auth_state(
                api_key=ctx.api_key,
                account_id=ctx.account_id,
                add_type="feishu_doc",
                path=path,
                connector_args={OAUTH_REF_ARG: reference},
            ),
        }
    if provider is not None:
        args["feishu_access_token"] = await asyncio.to_thread(provider.get_token)
    with feishu_token_scope(provider):
        yield state


async def restore_feishu_request(
    connector, state: Dict[str, Any], *, path: str, ctx: RequestContext
) -> tuple[str, Dict[str, Any]]:
    api_key, add_type, args = await connector.restore_watch_request(
        state.get("credentials", {}), account_id=ctx.account_id, path=path
    )
    if add_type != "feishu_doc":
        raise InvalidArgumentError("Stored external Feishu credentials are invalid.")
    reference = validate_oauth_ref(args.get(OAUTH_REF_ARG), ctx.user.user_id)
    return api_key, {OAUTH_REF_ARG: reference}


def validate_oauth_ref(value: Any, ov_user_id: str) -> Dict[str, str]:
    keys = {"account_id", "user_id", "ov_user_id", "platform", "type"}
    if (
        not isinstance(value, dict)
        or set(value) != keys
        or any(not isinstance(value[key], str) or not value[key].strip() for key in keys)
    ):
        raise InvalidArgumentError("args.openviking_oauth_ref contains an invalid OAuth reference.")
    ref = {key: value[key].strip() for key in keys}
    if ref["type"] != "oauth" or ref["platform"] != "feishu_doc":
        raise InvalidArgumentError("Native Feishu imports require a feishu_doc OAuth reference.")
    if ref["ov_user_id"] != ov_user_id:
        raise InvalidArgumentError(
            "OAuth reference does not belong to the current OpenViking user."
        )
    return ref


def is_external_feishu_auth(state: Optional[Dict[str, Any]]) -> bool:
    return isinstance(state, dict) and state.get("provider") == EXTERNAL_FEISHU_PROVIDER


class ExternalFeishuToken:
    """Cache only access tokens, within one execution; never refresh OAuth locally."""

    def __init__(self, reference: Dict[str, str], api_key: str):
        self._url = external_auth_url()
        if not self._url:
            raise InvalidArgumentError(
                "connector.auth is required for external Feishu authorization."
            )
        if not api_key:
            raise InvalidArgumentError("External Feishu authorization requires an API key.")
        self._reference = reference
        self._api_key = api_key
        self._client = ConnectorClient("", "", account_id=reference["account_id"])
        self._lock = Lock()
        self._token = ""
        self._valid_until = 0.0
        self._error: Optional[InternalError] = None

    def get_token(self) -> str:
        with self._lock:
            if self._error is not None:
                raise self._error
            if self._token and time.time() < self._valid_until:
                return self._token
            try:
                data = self._client.get_oauth_access_token(
                    self._url, self._api_key, self._reference
                )
            except InternalError as exc:
                self._error = exc
                raise
            token = data.get("access_token")
            expires_at = data.get("expires_at", 0)
            now = time.time()
            if (
                not isinstance(token, str)
                or not token.strip()
                or type(expires_at) not in (int, float)
                or not math.isfinite(expires_at)
                or expires_at < 0
                or (expires_at > 0 and expires_at <= now)
            ):
                self._error = InternalError(
                    "External OAuth returned an invalid or expired access token."
                )
                raise self._error
            self._token = token.strip()
            self._valid_until = min(now + 60, expires_at - 30) if expires_at else now + 60
            return self._token


def check_feishu_auth(resource: "LocalResource") -> "LocalResource":
    """Reject and clean partial downloads before parsing, including missing embedded media."""
    provider = current_feishu_token.get()
    if provider is not None and provider._error is not None:
        resource.cleanup()
        raise provider._error
    return resource


@contextmanager
def feishu_token_scope(token: Optional[ExternalFeishuToken]):
    """Context variables also follow asyncio.to_thread into the synchronous SDK."""
    handle = current_feishu_token.set(token)
    try:
        yield
    finally:
        current_feishu_token.reset(handle)
