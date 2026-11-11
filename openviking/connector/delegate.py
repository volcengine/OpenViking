# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Connector delegation for resource imports.

ResourceService hands an add_resource request to :class:`ConnectorDelegate`
when the source belongs to the external Connector integration; the native
service keeps only that hand-off. This module owns the whole delegation
lifecycle:

- the routing decision (:meth:`ConnectorDelegate.should_delegate`): a
  three-state verdict — delegate, degrade to the standard pipeline, or raise;
- payload construction and queue publication (:meth:`ConnectorDelegate.create`);
- synchronous submission followed by durable monitoring in the ExternalTask queue.
"""

from __future__ import annotations

import base64
import json
import re
import time
from dataclasses import replace
from typing import Any, Awaitable, Callable, Dict, List, Mapping, Optional, Set, Tuple
from urllib.parse import urlsplit

import httpx

from openviking.connector.auth import (
    OAUTH_REF_ARG,
    confluence_auth_config,
    document_auth_config,
    extract_credential_reference,
    project_auth_config,
    resolve_connector_auth_config,
)
from openviking.connector.client import ConnectorClient
from openviking.connector.routing import (
    CONNECTOR_ARGS_AUTH_CONFIG_KEY,
    CONNECTOR_CREDENTIAL_ARGS,
    CONNECTOR_SUPPORTED_ARGS,
    credential_arg_names,
    detect_connector_add_type,
    is_full_commit_sha,
)
from openviking.core.namespace import NamespaceShapeError
from openviking.core.uri_validation import matches_content_kind
from openviking.crypto.encryptor import MAGIC as ENCRYPTED_ENVELOPE_MAGIC
from openviking.crypto.encryptor import build_credential_encryptor
from openviking.metrics.datasources.resource import ResourceIngestionEventDataSource
from openviking.observability.http_error_context import (
    sanitize_public_error_details,
    sanitize_public_http_error,
)
from openviking.parse.mode import ParseMode
from openviking.resource.processing_mode import (
    DEFAULT_PROCESSING_MODE,
    VECTORS_ONLY,
    ProcessingMode,
)
from openviking.server.identity import RequestContext
from openviking.service.external_task_service import (
    ExternalTaskCancellation,
    ExternalTaskError,
    ExternalTaskSnapshot,
    get_external_task_service,
)
from openviking.service.task_domain import TaskPatch
from openviking.service.task_tracker import TaskRecord, TaskStatus, get_task_tracker
from openviking.service.task_tracker_concurrency import run_to_completion
from openviking.storage.acl import AclUpdate
from openviking.utils.tags import normalize_search_tags
from openviking_cli.exceptions import InternalError, InvalidArgumentError
from openviking_cli.session.user_id import UserIdentifier
from openviking_cli.utils import get_logger
from openviking_cli.utils.config.open_viking_config import get_openviking_config

logger = get_logger(__name__)

_TOS_BUCKET_PATTERN = re.compile(r"^[a-z0-9](?:[a-z0-9-]{1,61}[a-z0-9])$")
CONNECTOR_DISPLAY_RESOURCE_CONFIG_ARG = "resource_config"


def _safe_http_response(response: httpx.Response) -> Any:
    """Return a bounded, credential-redacted response body for logs."""
    try:
        payload = response.json()
    except (TypeError, ValueError):
        payload = response.text
    if isinstance(payload, dict):
        return sanitize_public_error_details(payload)
    return sanitize_public_http_error(code="CONNECTOR_HTTP_ERROR", message=payload).message


def _validate_tos_uri(
    value: Any,
    field: str,
    *,
    allow_bucket_without_slash: bool = False,
) -> None:
    """Validate a Connector TOS URI without exposing it in client errors."""
    error = InvalidArgumentError(f"{field} must be a valid TOS URI.")
    if (
        not isinstance(value, str)
        or value != value.strip()
        or not value.startswith("tos://")
        or any(char in "?#%" or ord(char) < 0x20 or ord(char) == 0x7F for char in value)
    ):
        raise error

    try:
        parsed = urlsplit(value)
        hostname = parsed.hostname
        port = parsed.port
    except ValueError:
        raise error from None
    if (
        parsed.scheme != "tos"
        or parsed.username is not None
        or parsed.password is not None
        or port is not None
        or not hostname
        or hostname != parsed.netloc
        or not _TOS_BUCKET_PATTERN.fullmatch(hostname)
        or (not parsed.path and not allow_bucket_without_slash)
        or (parsed.path and not parsed.path.startswith("/"))
        or parsed.path.startswith("//")
        or parsed.query
        or parsed.fragment
    ):
        raise error


def _is_resource_target(to: str) -> bool:
    """True when *to* lies in a resources tree: the public root or a user's own."""
    try:
        return matches_content_kind(to, "resource")
    except (ValueError, NamespaceShapeError):
        return False


class ConnectorDelegate:
    """Prepare imports and execute their Connector provider lifecycle."""

    task_type = "connector_import"
    task_id_prefix = ""
    poll_max_attempts = 3

    def __init__(
        self,
        *,
        viking_fs: Any,
        resource_memory_link_service: Any = None,
    ) -> None:
        self._viking_fs = viking_fs
        self._resource_memory_link_service = resource_memory_link_service
        self._watch_credential_encryptor: Any = None

    _WATCH_AUTH_PROVIDER = "connector_encrypted"
    _WATCH_PLAINTEXT_AUTH_PROVIDER = "connector_plaintext"

    def _watch_encryptor(self) -> Any:
        encryptor = getattr(self._viking_fs, "_encryptor", None)
        if encryptor is None:
            if self._watch_credential_encryptor is None:
                self._watch_credential_encryptor = build_credential_encryptor(
                    get_openviking_config().encryption
                )
            encryptor = self._watch_credential_encryptor
        return encryptor

    async def create_watch_auth_state(
        self,
        *,
        api_key: str,
        account_id: str,
        add_type: str,
        path: str,
        connector_args: Optional[Dict[str, Any]],
        connector_auth_config: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Build the private request state needed to replay a Connector watch."""
        if not api_key:
            raise InvalidArgumentError("Connector watch requires an API key.")
        payload = {
            "api_key": api_key,
            "account_id": account_id,
            "add_type": add_type,
            "path": path,
            "connector_args": dict(connector_args or {}),
        }
        # Watches persist only the secret-free credential reference; the actual
        # auth_config is resolved anew each round so refreshed tokens stay current.
        if connector_auth_config:
            payload["connector_auth_config"] = dict(connector_auth_config)
        try:
            plaintext = json.dumps(
                payload,
                ensure_ascii=False,
                separators=(",", ":"),
            ).encode("utf-8")
            ciphertext = await self._watch_encryptor().encrypt(account_id, plaintext)
        except InvalidArgumentError:
            raise
        except Exception as exc:
            raise InvalidArgumentError("Failed to encrypt Connector watch credentials.") from exc
        return {
            "provider": self._WATCH_AUTH_PROVIDER,
            "ciphertext": base64.b64encode(ciphertext).decode("ascii"),
        }

    @classmethod
    def is_watch_auth_state(cls, auth_state: Optional[Dict[str, Any]]) -> bool:
        return isinstance(auth_state, dict) and auth_state.get("provider") in {
            cls._WATCH_AUTH_PROVIDER,
            cls._WATCH_PLAINTEXT_AUTH_PROVIDER,
        }

    async def restore_watch_request(
        self,
        auth_state: Dict[str, Any],
        *,
        account_id: str,
        path: str,
    ) -> Tuple[str, str, Dict[str, Any], Optional[Dict[str, Any]]]:
        """Restore and validate a source-bound Connector watch request.

        Returns ``(api_key, add_type, connector_args, connector_auth_config)``;
        ``connector_auth_config`` is the secret-free credential reference and is
        None for watches that predate it or use the legacy args channel.
        """
        try:
            provider = auth_state.get("provider")
            if provider == self._WATCH_PLAINTEXT_AUTH_PROVIDER:
                payload = auth_state.get("request")
            elif provider == self._WATCH_AUTH_PROVIDER:
                encoded = auth_state.get("ciphertext")
                if not isinstance(encoded, str) or not encoded:
                    raise ValueError("missing ciphertext")
                ciphertext = base64.b64decode(encoded, validate=True)
                if not ciphertext.startswith(ENCRYPTED_ENVELOPE_MAGIC):
                    raise ValueError("invalid encrypted envelope")
                plaintext = await self._watch_encryptor().decrypt(account_id, ciphertext)
                payload = json.loads(plaintext.decode("utf-8"))
            else:
                raise ValueError("unknown Connector watch provider")
            if (
                not isinstance(payload, dict)
                or payload.get("account_id") != account_id
                or payload.get("path") != path
            ):
                raise ValueError("watch binding mismatch")
            api_key = payload.get("api_key")
            add_type = payload.get("add_type")
            connector_args = payload.get("connector_args")
            connector_auth_config = payload.get("connector_auth_config")
            if (
                not isinstance(api_key, str)
                or not api_key
                or not isinstance(add_type, str)
                or not add_type
                or not isinstance(connector_args, dict)
                or (
                    connector_auth_config is not None
                    and not isinstance(connector_auth_config, dict)
                )
            ):
                raise ValueError("invalid watch request")
            return (
                api_key,
                add_type,
                dict(connector_args),
                dict(connector_auth_config) if connector_auth_config else None,
            )
        except InvalidArgumentError:
            raise
        except Exception as exc:
            raise InvalidArgumentError("Stored Connector watch credentials are invalid.") from exc

    @staticmethod
    def resolve_add_type(path: str, declared_add_type: Optional[str]) -> Optional[Tuple[str, bool]]:
        """Resolve the Connector ``(add_type, connector_only)`` for *path*.

        Probes the path when no add_type is declared. A declared add_type is
        validated against the probe: registry types (tos/git) must match it
        exactly, and other types must not claim a path that probes as a
        registry type. A declared request is an explicit routing instruction,
        so it is always connector-only: it never degrades to the standard
        pipeline.
        """
        detected = detect_connector_add_type(path)
        if declared_add_type is None:
            return detected
        probed = detected[0] if detected else None
        if declared_add_type in CONNECTOR_SUPPORTED_ARGS:
            if probed != declared_add_type:
                raise InvalidArgumentError(
                    f"add_type '{declared_add_type}' does not match the source path"
                    + (f", which is a '{probed}' source" if probed else "")
                    + "."
                )
        elif probed is not None:
            raise InvalidArgumentError(
                f"add_type '{declared_add_type}' does not match the source path, "
                f"which is a '{probed}' source."
            )
        return (declared_add_type, True)

    @classmethod
    def supported_args(cls, path: str, declared_add_type: Optional[str]) -> Set[str]:
        """Connector-owned ``args`` fields for the resolved source type."""
        resolved = cls.resolve_add_type(path, declared_add_type)
        if resolved is None:
            return set()
        return set(CONNECTOR_SUPPORTED_ARGS.get(resolved[0], frozenset()))

    def should_delegate(
        self,
        path: str,
        *,
        ctx: Optional[RequestContext] = None,
        declared_add_type: Optional[str] = None,
        to: Optional[str] = None,
        parent: Optional[str] = None,
        wait: bool = False,
        instruction: str = "",
        build_index: bool = True,
        summarize: bool = False,
        processing_mode: ProcessingMode = DEFAULT_PROCESSING_MODE,
        parse_mode: ParseMode = ParseMode.DEFAULT,
        watch_interval: float = 0,
        connector_args: Optional[Dict[str, Any]] = None,
        kwargs: Optional[Dict[str, Any]] = None,
    ) -> bool:
        """Decide whether a top-level resource path belongs to Connector.

        Returns True to delegate to the Connector, False to route to the
        standard pipeline. Source types are detected the same way the
        standard pipeline routes them (see connector.routing), not by raw
        URL scheme. A source type only the Connector can import (tos)
        raises InvalidArgumentError when the Connector is disabled, does
        not allow the type, or cannot honor the request parameters —
        degrading such a request would only fail later with a misleading
        parse error. Source types the standard pipeline can also handle
        degrade to it instead when parameters are unsupported, unless the
        request carries Connector-only credentials; credentialed requests
        fail closed so secrets never enter a durable native job.
        """
        from openviking_cli.utils.config.open_viking_config import get_openviking_config

        resolved = self.resolve_add_type(path, declared_add_type)
        if resolved is None:
            return False
        add_type, connector_only = resolved
        connector_args = connector_args or {}
        credential_args = credential_arg_names(add_type, connector_args)

        config = get_openviking_config().connector
        if not config.enable or add_type not in config.allowed_add_types:
            if declared_add_type is not None:
                raise InvalidArgumentError(
                    f"add_type '{add_type}' requires the Connector integration, "
                    "which is disabled or does not allow this source type."
                )
            if credential_args:
                raise InvalidArgumentError(
                    f"Credential args {credential_args} for '{add_type}' require Connector "
                    "import, but the Connector is disabled or does not allow this source type."
                )
            if connector_only:
                raise InvalidArgumentError(
                    f"'{path}' can only be imported through the Connector integration, "
                    "which is disabled or does not allow this source type."
                )
            return False

        if add_type == "git":
            from openviking.parse.accessors.git_accessor import GitAccessor

            _, _, commit = GitAccessor()._parse_repo_source(path, **connector_args)
            if commit and not is_full_commit_sha(str(commit)):
                # The plugin fetches pinned commits by SHA over the wire and
                # cannot resolve abbreviated forms; the standard pipeline
                # resolves them locally after a full clone.
                if declared_add_type is not None:
                    raise InvalidArgumentError(
                        "Connector git import cannot resolve an abbreviated commit SHA; "
                        "pass a full 40-character SHA or drop add_type to use the "
                        "standard pipeline."
                    )
                if credential_args:
                    raise InvalidArgumentError(
                        f"Credential args {credential_args} for 'git' cannot fall back to the "
                        "standard import pipeline; pass a full 40-character commit SHA."
                    )
                logger.info(
                    "Connector git import degrades to the standard pipeline: "
                    "abbreviated commit SHA %r needs a local clone to resolve.",
                    commit,
                )
                return False

        unsupported = self._unsupported_params(
            add_type=add_type,
            wait=wait,
            instruction=instruction,
            build_index=build_index,
            summarize=summarize,
            processing_mode=processing_mode,
            parse_mode=parse_mode,
            watch_interval=watch_interval,
            connector_args=connector_args,
            kwargs=kwargs or {},
            to=to,
            parent=parent,
        )
        if not unsupported:
            return True
        detail = "; ".join(unsupported)
        if connector_only:
            raise InvalidArgumentError(f"Connector import does not support: {detail}")
        if credential_args:
            raise InvalidArgumentError(
                f"Credential args {credential_args} for '{add_type}' cannot fall back to the "
                f"standard import pipeline. Connector import does not support: {detail}"
            )
        logger.info(
            f"[ConnectorDelegate] Connector does not support {detail}; "
            "falling back to the standard import pipeline"
        )
        return False

    @staticmethod
    def _unsupported_params(
        *,
        add_type: str,
        wait: bool,
        instruction: str,
        build_index: bool,
        summarize: bool,
        processing_mode: ProcessingMode,
        parse_mode: ParseMode,
        watch_interval: float,
        connector_args: Dict[str, Any],
        kwargs: Dict[str, Any],
        to: Optional[str] = None,
        parent: Optional[str] = None,
    ) -> List[str]:
        """add_resource params the Connector delegation cannot honor.

        Returns an empty list when the request is fully supported.
        """
        unsupported: List[str] = []
        if parse_mode is ParseMode.NO_SPLIT:
            unsupported.append("parse_mode=no_split")
        if wait:
            unsupported.append("wait=true (Connector imports run asynchronously)")
        if parent:
            unsupported.append("parent targets (Connector imports require an exact 'to' target)")
        if not to:
            unsupported.append("missing exact 'to' target")
        elif not _is_resource_target(to):
            unsupported.append(
                "to outside a resources tree (viking://resources/... or "
                "viking://user/<user_id>/resources/...)"
            )
        if instruction:
            unsupported.append("instruction")
        if not build_index:
            unsupported.append("build_index=false")
        if summarize:
            unsupported.append("summarize=true")
        if processing_mode == VECTORS_ONLY:
            unsupported.append("processing_mode=vectors_only")
        if kwargs.get("strict"):
            unsupported.append("strict=true (Connector imports fail per file, not all-or-nothing)")
        for field in ("ignore_dirs", "include", "exclude"):
            if field == "exclude" and add_type == "tos" and field in connector_args:
                continue
            if kwargs.get(field):
                unsupported.append(f"{field} (Connector imports cannot filter the source tree)")
        if kwargs.get("preserve_structure") is False:
            unsupported.append(
                "preserve_structure=false (Connector always mirrors the source directory tree)"
            )
        if not kwargs.get("directly_upload_media", True):
            unsupported.append("directly_upload_media=false")
        if kwargs.get("source_name"):
            unsupported.append("source_name")
        if add_type in CONNECTOR_SUPPORTED_ARGS:
            supported_args = CONNECTOR_SUPPORTED_ARGS.get(
                add_type, frozenset()
            ) | CONNECTOR_CREDENTIAL_ARGS.get(add_type, frozenset())
            unknown_args = sorted(
                set(connector_args) - supported_args - {CONNECTOR_DISPLAY_RESOURCE_CONFIG_ARG}
            )
            if unknown_args:
                supported_hint = (
                    f"supported: {sorted(supported_args)}"
                    if supported_args
                    else "no args supported"
                )
                unsupported.append(f"args keys {unknown_args} ({supported_hint} for '{add_type}')")
        else:
            # Declared add_types outside the registry forward args wholesale
            # to the plugin via param_config; only the reserved credentials
            # key is inspected here.
            declared_auth = connector_args.get(CONNECTOR_ARGS_AUTH_CONFIG_KEY)
            if declared_auth is not None and not isinstance(declared_auth, dict):
                unsupported.append("args.auth_config that is not an object of credential fields")
        return unsupported

    async def create(
        self,
        path: str,
        ctx: RequestContext,
        to: Optional[str],
        *,
        wait_for_completion: bool = False,
        task_id: Optional[str] = None,
        on_task_created: Optional[Callable[[TaskRecord], Awaitable[None]]] = None,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        """Submit an import, enqueue monitoring, and return its durable receipt."""
        task = await run_to_completion(
            lambda: self._enqueue(
                path,
                ctx,
                to,
                task_id=task_id,
                on_task_created=on_task_created,
                **kwargs,
            )
        )
        tracker = get_task_tracker()
        owner = {"account_id": task.account_id, "user_id": task.user_id}
        key = task.meta.get("connector_task_key")
        if not key:
            raise InternalError("Connector submission did not persist its task key")
        response = {
            "status": "accepted",
            "task_id": task.task_id,
            "connector_task_key": key,
            "resource_id": task.resource_id,
        }
        if wait_for_completion:
            completed = await tracker.wait(task.task_id, **owner, timeout=None)
            response.update(completed.result or {})
            response["status"] = completed.status.value
            if completed.error:
                response["error"] = completed.error
        return response

    async def _enqueue(
        self,
        path: str,
        ctx: RequestContext,
        to: Optional[str],
        reason: str = "",
        declared_add_type: Optional[str] = None,
        connector_args: Optional[Dict[str, Any]] = None,
        connector_auth_config: Optional[Dict[str, Any]] = None,
        tags: Optional[List[str]] = None,
        tag_mode: str = "replace",
        connector_states: Optional[Dict[str, Any]] = None,
        task_id: Optional[str] = None,
        on_task_created: Optional[Callable[[TaskRecord], Awaitable[None]]] = None,
        acl_update: Optional[AclUpdate] = None,
        **kwargs: Any,
    ) -> TaskRecord:
        """Persist the request and submit before publishing its monitoring Work."""
        config = get_openviking_config().connector
        if not ctx.api_key:
            raise InvalidArgumentError("Connector import requires an API key in the request.")

        resolved = self.resolve_add_type(path, declared_add_type)
        if resolved is None:
            raise InvalidArgumentError(f"'{path}' does not match any Connector source type.")
        add_type, _ = resolved
        if add_type == "tos":
            _validate_tos_uri(path, "path", allow_bucket_without_slash=True)

        task_resource_id = to or ""
        if not task_resource_id:
            raise InvalidArgumentError("Connector import requires an exact 'to' target.")

        if not kwargs.get("create_parent", False):
            # Match the native pipeline default: unless create_parent=true is
            # given, the parent of the exact target must pre-exist.
            parent_uri, _, _ = task_resource_id.rpartition("/")
            if parent_uri.startswith("viking://") and not await self._viking_fs.exists(
                parent_uri, ctx
            ):
                raise InvalidArgumentError(
                    f"Parent directory '{parent_uri}' does not exist; "
                    "pass create_parent=true to create it."
                )

        # Credentials are separated into the dedicated auth_config field before
        # forwarding: Connector and plugin logs redact auth_config while logging
        # param_config verbatim. The incoming OpenViking HTTP body may still be
        # captured when the explicitly unsafe observability body dump is enabled.
        if add_type in CONNECTOR_SUPPORTED_ARGS:
            forwarded_args = {
                key: value
                for key, value in (connector_args or {}).items()
                if key in CONNECTOR_SUPPORTED_ARGS.get(add_type, frozenset()) and value
            }
            auth_config = {
                key: str(value)
                for key, value in (connector_args or {}).items()
                if key in CONNECTOR_CREDENTIAL_ARGS.get(add_type, frozenset()) and value
            }
        else:
            # Declared add_type outside the registry: the plugin owns the args
            # semantics, so they travel wholesale through param_config. The
            # caller supplies credentials under the reserved args.auth_config
            # key, lifted into the top-level auth_config field.
            display_only_args = {
                CONNECTOR_ARGS_AUTH_CONFIG_KEY,
                CONNECTOR_DISPLAY_RESOURCE_CONFIG_ARG,
            }
            forwarded_args = {
                key: value
                for key, value in (connector_args or {}).items()
                if key not in display_only_args and value is not None
            }
            declared_auth = (connector_args or {}).get(CONNECTOR_ARGS_AUTH_CONFIG_KEY)
            if declared_auth is not None and not isinstance(declared_auth, dict):
                raise InvalidArgumentError(
                    "args.auth_config must be an object of credential fields."
                )
            auth_config = {
                str(key): str(value) for key, value in (declared_auth or {}).items() if value
            }

        if add_type in {"meego", "feishu_project"} and OAUTH_REF_ARG in (connector_args or {}):
            auth_config = await project_auth_config(connector_args or {}, ctx)
            forwarded_args.pop(OAUTH_REF_ARG, None)
        if add_type == "feishu_doc" and OAUTH_REF_ARG in (connector_args or {}):
            auth_config = await document_auth_config(connector_args or {}, ctx)
            forwarded_args.pop(OAUTH_REF_ARG, None)
        if add_type == "confluence" and OAUTH_REF_ARG in (connector_args or {}):
            auth_config = await confluence_auth_config(connector_args or {}, ctx)
            forwarded_args.pop(OAUTH_REF_ARG, None)

        # Imports carrying an OpenViking-managed credential reference resolve the
        # full auth_config through the unified resolver just before forwarding,
        # so a refreshed token is always current. This is platform-agnostic:
        # the kernel forwards whatever auth_config the resolver assembled for the
        # target Connector without inspecting its fields.
        credential_reference = extract_credential_reference(connector_auth_config)
        if credential_reference is not None:
            if add_type in CONNECTOR_SUPPORTED_ARGS:
                raise InvalidArgumentError(
                    f"add_type '{add_type}' does not accept a managed credential reference."
                )
            auth_config = await resolve_connector_auth_config(credential_reference, ctx)

        tos_path: Optional[str] = None
        param_config: Optional[Dict[str, Any]] = None
        if add_type == "tos":
            tos_args = connector_args or {}
            if "tos_prefix" not in tos_args:
                if "exclude" in tos_args:
                    raise InvalidArgumentError("args.exclude requires args.tos_prefix.")
                source_path = path[len("tos://") :].strip()
                if not source_path:
                    raise InvalidArgumentError(
                        "Connector TOS import requires path='tos://<bucket>/<path>'."
                    )
                tos_path = source_path
            else:
                tos_prefix = tos_args["tos_prefix"]
                if not isinstance(tos_prefix, list) or not tos_prefix:
                    raise InvalidArgumentError(
                        "args.tos_prefix must be a non-empty list of TOS URIs."
                    )
                for index, source in enumerate(tos_prefix):
                    _validate_tos_uri(source, f"args.tos_prefix[{index}]")
                if tos_prefix[0] != path:
                    raise InvalidArgumentError("path must equal the first item in args.tos_prefix.")
                exclude = tos_args.get("exclude", [])
                if not isinstance(exclude, list):
                    raise InvalidArgumentError("args.exclude must be a list of TOS URIs.")
                for index, source in enumerate(exclude):
                    _validate_tos_uri(source, f"args.exclude[{index}]")
                param_config = {"tos_prefix": tos_prefix}
                if exclude:
                    param_config["exclude"] = exclude
        elif add_type == "git":
            from openviking.parse.accessors.git_accessor import GitAccessor

            # Source-specific settings stay in param_config. The exact target
            # is transported separately as the top-level ``to`` field.
            repo_url, branch, commit = GitAccessor()._parse_repo_source(
                path.strip(), **forwarded_args
            )
            param_config = {"repo_url": repo_url}
            if commit:
                # Routing already degraded abbreviated SHAs to the standard
                # pipeline. Preserve the native accessor's historical precedence:
                # when both commit and branch/ref are present, send only the full
                # commit SHA so the plugin receives an unambiguous pinned request.
                param_config["commit"] = str(commit)
            elif branch:
                param_config["branch"] = str(branch)
        else:
            # Declared add_type outside the registry: the plugin owns the
            # source semantics. The top-level path travels under the agreed
            # param_config["path"] key -- args can never carry "path", it is
            # a reserved core add_resource field.
            param_config = dict(forwarded_args)
            param_config["path"] = path

        extra_params: Optional[Dict[str, Any]] = None
        if tag_mode == "clear":
            extra_params = {"tag_mode": tag_mode}
        elif tags is not None:
            normalized_tags = normalize_search_tags(tags)
            if normalized_tags:
                extra_params = {
                    "tags": normalized_tags,
                    "tag_mode": tag_mode,
                }

        submit_kwargs: Dict[str, Any] = {
            "add_type": add_type,
            "tos_path": tos_path,
            "to": task_resource_id,
            "include_child": True,
            "param_config": param_config,
            "extra_params": extra_params,
        }
        if connector_states is not None:
            submit_kwargs["stream_states"] = connector_states
        payload = {
            "submit": submit_kwargs,
            "reason": reason,
            "acl_update": (
                acl_update.model_dump(mode="json", exclude_none=True)
                if acl_update is not None
                else None
            ),
            "identity": {
                "role": str(ctx.role),
                "group_ids": list(ctx.group_ids),
                "actor_peer_id": ctx.actor_peer_id,
                "bypass_acl": ctx.bypass_acl,
            },
            "deadline": None,
            "timeout_seconds": config.timeout_seconds,
        }
        private_payload = {"api_key": ctx.api_key, "auth_config": auth_config}

        async def submit_before_enqueue(task: TaskRecord) -> None:
            # Register the Watch/run before Connector can call back into OV.
            if on_task_created is not None:
                await on_task_created(task)
            if task.meta.get("connector_task_key"):
                return
            tracker = get_task_tracker()
            owner = {"account_id": task.account_id, "user_id": task.user_id}
            try:
                await tracker.start(task.task_id, stage="submitting", **owner)
                current = await tracker.get(task.task_id, **owner)
                if (
                    current is None
                    or current.is_terminal()
                    or current.status == TaskStatus.CANCELLING
                    or current.error
                ):
                    raise InternalError("Connector Task ended before submission")
                key = await self.submit(
                    task.task_id, payload, private_payload, {"account_id": ctx.account_id}
                )
                request = {**payload, "deadline": time.time() + config.timeout_seconds}
                patched = await tracker.patch_task(
                    task.task_id,
                    TaskPatch(
                        auth={"external_task_id": key},
                        meta={**self.submission_meta(key), "request": request},
                    ),
                    **owner,
                )
                if not patched.is_applied:
                    raise InternalError("Connector Task ended before submission was persisted")
            except BaseException as exc:
                await tracker.fail(
                    task.task_id,
                    sanitize_public_http_error(
                        code="CONNECTOR_SUBMISSION_FAILED",
                        message=str(exc) or "connector task submission cancelled",
                    ).message,
                    **owner,
                )
                raise

        return await get_external_task_service().create(
            self.task_type,
            resource_id=task_resource_id,
            payload=payload,
            private_payload=private_payload,
            ctx=ctx,
            task_id=task_id,
            on_task_created=submit_before_enqueue,
        )

    @property
    def poll_interval_seconds(self) -> float:
        return get_openviking_config().connector.poll_interval_ms / 1000.0

    @staticmethod
    def submission_meta(external_task_id: str) -> Dict[str, Any]:
        return {"connector_task_key": external_task_id}

    @staticmethod
    def _client(account_id: str) -> ConnectorClient:
        config = get_openviking_config().connector
        return ConnectorClient(
            doc_add_url=config.connector, task_info_url=config.tracker, account_id=account_id
        )

    @staticmethod
    def _timeout(payload: Mapping[str, Any]) -> ExternalTaskSnapshot:
        return ExternalTaskSnapshot(
            status="failed",
            error_code="DEADLINE_EXCEEDED",
            error_message=f"connector task timed out after {payload['timeout_seconds']}s",
        )

    async def on_start(self, task: TaskRecord) -> ExternalTaskSnapshot | None:
        if task.auth.get("external_terminal_snapshot") is not None:
            return None
        return None

    async def submit(
        self,
        ov_task_id: str,
        payload: Mapping[str, Any],
        private_payload: Mapping[str, Any],
        connection: Mapping[str, Any],
    ) -> str:
        """Submit an import; the caller persists the returned key before monitoring."""
        request = {
            **payload["submit"],
            "api_key": private_payload["api_key"],
            "auth_config": private_payload.get("auth_config") or None,
        }
        safe_request = {**request, "api_key": "[REDACTED]"}
        if safe_request["auth_config"] is not None:
            safe_request["auth_config"] = "[REDACTED]"
        logger.info(
            "[ConnectorDelegate] Connector task add request: ov_task_id=%s request=%s",
            ov_task_id,
            sanitize_public_error_details(safe_request),
        )
        try:
            result = await self._client(connection["account_id"]).submit_doc_add(**request)
        except httpx.HTTPStatusError as exc:
            logger.error(
                "[ConnectorDelegate] Connector task add response: ov_task_id=%s status=%s "
                "response=%s",
                ov_task_id,
                exc.response.status_code,
                _safe_http_response(exc.response),
            )
            raise ExternalTaskError(
                "CONNECTOR_SUBMISSION_FAILED",
                sanitize_public_http_error(code="CONNECTOR_SUBMISSION_FAILED", message=exc).message,
                transient=exc.response.status_code in {408, 429} or exc.response.status_code >= 500,
            ) from exc
        except httpx.RequestError as exc:
            raise ExternalTaskError(
                "CONNECTOR_SUBMISSION_FAILED",
                "Connector submission transport failed",
                transient=True,
            ) from exc
        logger.info(
            "[ConnectorDelegate] Connector task add response: ov_task_id=%s response=%s",
            ov_task_id,
            sanitize_public_error_details(result),
        )
        key = result.get("task_key") or result.get("TaskKey")
        if not isinstance(key, str) or not key.strip():
            raise ExternalTaskError(
                "INVALID_RESPONSE",
                "Connector accepted the import but returned no task key",
                transient=False,
            )
        ResourceIngestionEventDataSource.record_import(
            pipeline="connector",
            source_type="repository" if request["add_type"] == "git" else "document",
            account_id=connection["account_id"],
        )
        return key.strip()

    async def get(
        self,
        external_task_id: str,
        connection: Mapping[str, Any],
        *,
        payload: Mapping[str, Any] | None = None,
        private_payload: Mapping[str, Any] | None = None,
    ) -> ExternalTaskSnapshot:
        assert payload is not None and private_payload is not None

        def active(status: str, *, stage: str | None = None) -> ExternalTaskSnapshot:
            if time.time() >= payload["deadline"]:
                return self._timeout(payload)
            return ExternalTaskSnapshot(status=status, stage=stage)

        try:
            info = await self._client(connection["account_id"]).get_task_info(
                external_task_id, private_payload["api_key"]
            )
        except httpx.HTTPStatusError as exc:
            code = exc.response.status_code
            retrying = code in {408, 429} or code >= 500
            logger.warning(
                "[ConnectorDelegate] Connector task info error response: "
                "connector_task_key=%s status=%s response=%s retrying=%s",
                external_task_id,
                code,
                _safe_http_response(exc.response),
                retrying,
            )
            if retrying:
                return active("running")
            raise ExternalTaskError(
                "CONNECTOR_POLL_FAILED", "connector task monitoring failed", transient=False
            ) from exc
        except httpx.RequestError:
            return active("running")
        status = str(info.get("Status") or info.get("status") or "").lower()
        if status in {"pending", "running"}:
            return active(status, stage=f"connector:{status}")
        if status == "succeeded":
            result: Dict[str, Any] = {
                "connector_status": status,
                "connector_task_key": external_task_id,
            }
            states = info.get("StreamStates")
            if states is None:
                states = info.get("stream_states")
            if states is not None:
                if not isinstance(states, dict):
                    raise ExternalTaskError(
                        "INVALID_RESPONSE",
                        "Connector task response contains invalid stream states",
                        transient=False,
                    )
                result["connector_states"] = states
            return ExternalTaskSnapshot(
                status="completed", stage="connector:succeeded", result=result
            )
        if status in {"failed", "cancelled"}:
            error = sanitize_public_http_error(
                code="CONNECTOR_TASK_FAILED",
                message=info.get("ErrorMessage") or info.get("error_message") or status,
            ).message
            return ExternalTaskSnapshot(
                status=status,
                stage=f"connector:{status}",
                error_code="CONNECTOR_TASK_FAILED",
                error_message=f"connector task {status}: {error}",
            )
        raise ExternalTaskError(
            "INVALID_RESPONSE", f"Unknown Connector task status: {status}", transient=False
        )

    async def on_finish(
        self, task: TaskRecord, snapshot: ExternalTaskSnapshot
    ) -> ExternalTaskSnapshot:
        """Apply resource effects before the framework records the outcome."""
        if task.status == TaskStatus.CANCELLING and snapshot.status == "completed":
            # Cancellation may be persisted before this worker receives its notification.
            return ExternalTaskSnapshot(status="cancelled")
        request = task.meta["request"]
        assert task.account_id is not None and task.user_id is not None
        private = task.auth["external_request_private"]
        identity = request["identity"]
        ctx = RequestContext(
            user=UserIdentifier(task.account_id, task.user_id),
            role=identity["role"],
            group_ids=tuple(identity["group_ids"]),
            actor_peer_id=identity["actor_peer_id"],
            bypass_acl=identity["bypass_acl"],
            api_key=private["api_key"],
        )
        result = dict(snapshot.result or {})
        if snapshot.status == "completed":
            if request["acl_update"] is not None:
                update = AclUpdate.model_validate(request["acl_update"])
                await self._viking_fs.set_acl(
                    update.uri, update.acl.entries, ctx=ctx, acl_mode=update.acl.acl_mode
                )
            if request["reason"].strip() and self._resource_memory_link_service is not None:
                linked = task.meta.get("connector_reason_result")
                if linked is None:
                    try:
                        linked = {
                            "memory_linking": await self._resource_memory_link_service.on_resource_added(
                                ctx=ctx,
                                resource_uri=task.resource_id,
                                reason=request["reason"],
                                source_name=None,
                            )
                        }
                    except Exception as exc:
                        linked = {"warnings": [f"Memory linking failed: {exc}"]}
                    await get_task_tracker().patch_task(
                        task.task_id,
                        TaskPatch(meta={"connector_reason_result": linked}),
                        account_id=task.account_id,
                        user_id=task.user_id,
                    )
                result.update(linked)
        return replace(snapshot, result=result)

    async def on_cancel(
        self,
        task: TaskRecord,
        *,
        external_task_id: str | None,
        payload: Mapping[str, Any],
        private_payload: Mapping[str, Any],
        connection: Mapping[str, Any],
        failure: Exception | None,
    ) -> ExternalTaskCancellation:
        """Stop local monitoring; Connector currently has no remote cancellation API."""
        snapshot = (
            ExternalTaskSnapshot(
                status="failed",
                error_code=getattr(failure, "code", None) or "INTERNAL",
                error_message=sanitize_public_http_error(
                    code="CONNECTOR_TASK_FAILED",
                    message=str(failure) or type(failure).__name__,
                ).message,
            )
            if failure is not None
            else ExternalTaskSnapshot(status="cancelled")
        )
        return ExternalTaskCancellation(external_task_id, snapshot)
