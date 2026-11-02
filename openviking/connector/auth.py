# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Task-scoped access to centrally managed external OAuth credentials."""

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
CONNECTOR_AUTH_CONFIG_FIELD = "connector_auth_config"
CREDENTIAL_REF_KEY = "openviking_credential_ref"
_CREDENTIAL_REF_REQUIRED_KEYS = (
    "account_id",
    "resource_id",
    "user_id",
    "ov_user_id",
    "platform",
    "type",
)
EXTERNAL_FEISHU_PROVIDER = "feishu_external"
current_feishu_token: ContextVar[Optional["ExternalFeishuToken"]] = ContextVar(
    "external_feishu_token", default=None
)


def external_auth_url() -> str:
    from openviking_cli.utils.config.open_viking_config import get_openviking_config

    return get_openviking_config().connector.auth


def credential_resolve_url() -> str:
    from openviking_cli.utils.config.open_viking_config import get_openviking_config

    return get_openviking_config().connector.credential_resolve_url()


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
    api_key, add_type, args, _ = await connector.restore_watch_request(
        state.get("credentials", {}), account_id=ctx.account_id, path=path
    )
    if add_type != "feishu_doc":
        raise InvalidArgumentError("Stored external Feishu credentials are invalid.")
    reference = validate_oauth_ref(args.get(OAUTH_REF_ARG), ctx.user.user_id)
    return api_key, {OAUTH_REF_ARG: reference}


def validate_oauth_ref(
    value: Any, ov_user_id: str, *, platform: str = "feishu_doc"
) -> Dict[str, str]:
    keys = {"account_id", "user_id", "ov_user_id", "platform", "type"}
    if (
        not isinstance(value, dict)
        or set(value) != keys
        or any(not isinstance(value[key], str) or not value[key].strip() for key in keys)
    ):
        raise InvalidArgumentError("args.openviking_oauth_ref contains an invalid OAuth reference.")
    ref = {key: value[key].strip() for key in keys}
    if ref["type"] != "oauth" or ref["platform"] != platform:
        raise InvalidArgumentError(f"This import requires a {platform} OAuth reference.")
    if ref["ov_user_id"] != ov_user_id:
        raise InvalidArgumentError(
            "OAuth reference does not belong to the current OpenViking user."
        )
    return ref


def extract_credential_reference(
    connector_auth_config: Optional[Dict[str, Any]],
) -> Optional[Dict[str, Any]]:
    """Return the private credential reference, or None when absent.

    The top-level ``connector_auth_config`` is managed by OpenViking and only
    ever carries one key, ``openviking_credential_ref``. Imports that keep the
    legacy ``args.openviking_oauth_ref`` channel (Feishu) leave it unset, so a
    present reference is the single platform-agnostic signal that this import
    resolves credentials through the unified resolver.
    """
    if not connector_auth_config:
        return None
    if not isinstance(connector_auth_config, dict):
        raise InvalidArgumentError("connector_auth_config must be an object.")
    reference = connector_auth_config.get(CREDENTIAL_REF_KEY)
    if reference is None:
        return None
    if set(connector_auth_config) != {CREDENTIAL_REF_KEY}:
        raise InvalidArgumentError("connector_auth_config contains unexpected fields.")
    return validate_credential_reference(reference)


def validate_credential_reference(value: Any) -> Dict[str, Any]:
    """Validate the OpenViking-managed credential reference shape.

    ``binding_version`` is optional (DingTalk sends a positive integer; Feishu
    omits it). All other fields are non-empty strings and ``type`` is ``oauth``.
    The reference is forwarded verbatim to the unified resolver, so the kernel
    never inspects platform-specific credential fields here.
    """
    if not isinstance(value, dict):
        raise InvalidArgumentError("connector_auth_config contains an invalid credential reference.")
    extra = set(value) - set(_CREDENTIAL_REF_REQUIRED_KEYS) - {"binding_version"}
    if extra or any(key not in value for key in _CREDENTIAL_REF_REQUIRED_KEYS):
        raise InvalidArgumentError("connector_auth_config contains an invalid credential reference.")
    reference: Dict[str, Any] = {}
    for key in _CREDENTIAL_REF_REQUIRED_KEYS:
        field = value[key]
        if not isinstance(field, str) or not field.strip():
            raise InvalidArgumentError(
                "connector_auth_config contains an invalid credential reference."
            )
        reference[key] = field.strip()
    if reference["type"] != "oauth":
        raise InvalidArgumentError("connector_auth_config credential reference must be an OAuth type.")
    if "binding_version" in value:
        binding_version = value["binding_version"]
        if isinstance(binding_version, bool) or not isinstance(binding_version, int):
            raise InvalidArgumentError("connector_auth_config binding_version must be an integer.")
        reference["binding_version"] = binding_version
    return reference


async def resolve_connector_auth_config(
    reference: Dict[str, Any], ctx: RequestContext
) -> Dict[str, Any]:
    """Resolve the current Connector ``auth_config`` for a credential reference.

    Each import and each watch round resolves credentials just before use so a
    refreshed token is always current; the resolver assembles the full
    ``auth_config`` for the target Connector, and the kernel forwards it
    verbatim without persisting any token or application secret.
    """
    resolve_url = credential_resolve_url()
    if not resolve_url:
        raise InvalidArgumentError(
            "connector.auth is required to resolve external Connector credentials."
        )
    api_key = ctx.api_key or ""
    if not api_key:
        raise InvalidArgumentError("External Connector authorization requires an API key.")
    client = ConnectorClient("", "", account_id=reference.get("account_id", ""))
    data = await asyncio.to_thread(
        client.resolve_connector_credential, resolve_url, api_key, reference
    )
    auth_config = data.get("auth_config")
    if not isinstance(auth_config, dict) or not auth_config:
        raise InternalError("External Connector credential resolution returned no auth_config.")
    return {str(key): value for key, value in auth_config.items()}


async def project_auth_config(args: Dict[str, Any], ctx: RequestContext) -> Dict[str, str]:
    """Resolve one credential snapshot without changing the stored watch request."""
    validate_feishu_auth_args(args)
    if args.get("auth_config") or any(
        key in args for key in ("user_access_token", "user_key", "plugin_id", "plugin_secret")
    ):
        raise InvalidArgumentError(
            "OAuth references cannot be combined with explicit project credentials."
        )
    reference = validate_oauth_ref(args[OAUTH_REF_ARG], ctx.user.user_id, platform="feishu_project")
    provider = ExternalFeishuToken(reference, ctx.api_key or "")
    credentials = await asyncio.to_thread(provider.get_credentials)
    account = credentials.get("account")
    user_key = account.get("id") if isinstance(account, dict) else None
    if not isinstance(user_key, str) or not user_key.strip():
        raise InternalError("External OAuth returned no project user key.")
    return {"user_access_token": credentials["access_token"], "user_key": user_key.strip()}


async def document_auth_config(args: Dict[str, Any], ctx: RequestContext) -> Dict[str, Any]:
    """Resolve enumeration credentials and retain a private reference for child imports."""
    validate_feishu_auth_args(args)
    if "auth_config" in args:
        raise InvalidArgumentError("OAuth references cannot be combined with auth_config.")
    reference = validate_oauth_ref(args[OAUTH_REF_ARG], ctx.user.user_id)
    provider = ExternalFeishuToken(reference, ctx.api_key or "")
    token = await asyncio.to_thread(provider.get_token)
    return {"lark_access_token": token, OAUTH_REF_ARG: reference}


async def confluence_auth_config(args: Dict[str, Any], ctx: RequestContext) -> Dict[str, str]:
    """Resolve a Confluence OAuth reference into the plugin's Bearer-token config."""
    if args.get("auth_config") or any(
        key in args for key in ("access_token", "api_token", "email")
    ):
        raise InvalidArgumentError(
            "OAuth references cannot be combined with explicit Confluence credentials."
        )
    reference = validate_oauth_ref(args[OAUTH_REF_ARG], ctx.user.user_id, platform="confluence")
    provider = ExternalFeishuToken(reference, ctx.api_key or "")
    return {"access_token": await asyncio.to_thread(provider.get_token)}


def is_external_feishu_auth(state: Optional[Dict[str, Any]]) -> bool:
    return isinstance(state, dict) and state.get("provider") == EXTERNAL_FEISHU_PROVIDER


class ExternalFeishuToken:
    """Cache access credentials within one execution; never refresh OAuth locally."""

    def __init__(self, reference: Dict[str, str], api_key: str):
        self._url = external_auth_url()
        if not self._url:
            raise InvalidArgumentError(
                "connector.auth is required for external OAuth authorization."
            )
        if not api_key:
            raise InvalidArgumentError("External OAuth authorization requires an API key.")
        self._reference = reference
        self._api_key = api_key
        self._client = ConnectorClient("", "", account_id=reference["account_id"])
        self._lock = Lock()
        self._credentials: Dict[str, Any] = {}
        self._valid_until = 0.0
        self._error: Optional[InternalError] = None

    def get_token(self) -> str:
        return self.get_credentials()["access_token"]

    def get_credentials(self) -> Dict[str, Any]:
        with self._lock:
            if self._error is not None:
                raise self._error
            if self._credentials and time.time() < self._valid_until:
                return self._credentials
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
            self._credentials = {**data, "access_token": token.strip()}
            self._valid_until = min(now + 60, expires_at - 30) if expires_at else now + 60
            return self._credentials


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
