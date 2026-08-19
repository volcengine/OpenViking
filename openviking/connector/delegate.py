# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Connector delegation for resource imports.

ResourceService hands an add_resource request to :class:`ConnectorDelegate`
when the source belongs to the external Connector integration; the native
service keeps only that hand-off. This module owns the whole delegation
lifecycle:

- the routing decision (:meth:`ConnectorDelegate.should_delegate`): a
  three-state verdict — delegate, degrade to the standard pipeline, or raise;
- payload construction and submission (:meth:`ConnectorDelegate.submit`);
- background monitoring (:meth:`ConnectorDelegate._monitor`), closing the OV
  TaskRecord from the remote task's terminal state.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any, Awaitable, Callable, Dict, List, Optional, Set, Tuple

import httpx

from openviking.connector.client import ConnectorClient
from openviking.connector.routing import (
    CONNECTOR_ARGS_AUTH_CONFIG_KEY,
    CONNECTOR_CREDENTIAL_ARGS,
    CONNECTOR_SUPPORTED_ARGS,
    credential_arg_names,
    detect_connector_add_type,
    is_full_commit_sha,
)
from openviking.parse.mode import ParseMode
from openviking.resource.processing_mode import (
    DEFAULT_PROCESSING_MODE,
    VECTORS_ONLY,
    ProcessingMode,
)
from openviking.server.identity import RequestContext
from openviking.utils.tags import normalize_search_tags
from openviking_cli.exceptions import InternalError, InvalidArgumentError
from openviking_cli.utils import get_logger

logger = get_logger(__name__)


class ConnectorDelegate:
    """Routes add_resource requests to the external Connector service.

    Collaborators are injected so the delegate stays free of service
    lifecycle concerns:

    - ``viking_fs``: used only for the parent-existence pre-check when
      ``create_parent`` is not requested.
    - ``background_tasks``: the ResourceService task set; monitors register
      here so ``close_background_tasks`` keeps cancelling them on shutdown.
    - ``link_reason_memory``: bound ResourceService method that links one
      reason memory to the import root — shared semantics with the native
      pipeline, so it is called back rather than duplicated.
    """

    def __init__(
        self,
        *,
        viking_fs: Any,
        background_tasks: Set["asyncio.Task[Any]"],
        link_reason_memory: Callable[..., Awaitable[None]],
    ) -> None:
        self._viking_fs = viking_fs
        self._background_tasks = background_tasks
        self._link_reason_memory = link_reason_memory

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
            f"[ConnectorDelegate] Connector does not support {detail} for path {path}; "
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
        elif to != "viking://resources" and not to.startswith("viking://resources/"):
            unsupported.append("to outside the public resources root (viking://resources/...)")
        if watch_interval > 0:
            unsupported.append("watch_interval>0 (Connector imports cannot be watched yet)")
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
            unknown_args = sorted(set(connector_args) - supported_args)
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

    async def submit(
        self,
        path: str,
        ctx: RequestContext,
        to: Optional[str],
        reason: str = "",
        declared_add_type: Optional[str] = None,
        connector_args: Optional[Dict[str, Any]] = None,
        tags: Optional[List[str]] = None,
        tag_mode: str = "replace",
        **kwargs: Any,
    ) -> Dict[str, Any]:
        """Route add_resource to the external Connector service."""
        from openviking.service.task_tracker import get_task_tracker
        from openviking_cli.utils.config.open_viking_config import get_openviking_config

        config = get_openviking_config().connector
        if not ctx.api_key:
            raise InvalidArgumentError("Connector import requires an API key in the request.")

        resolved = self.resolve_add_type(path, declared_add_type)
        if resolved is None:
            raise InvalidArgumentError(f"'{path}' does not match any Connector source type.")
        add_type, _ = resolved

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
            forwarded_args = {
                key: value
                for key, value in (connector_args or {}).items()
                if key != CONNECTOR_ARGS_AUTH_CONFIG_KEY and value is not None
            }
            declared_auth = (connector_args or {}).get(CONNECTOR_ARGS_AUTH_CONFIG_KEY)
            if declared_auth is not None and not isinstance(declared_auth, dict):
                raise InvalidArgumentError(
                    "args.auth_config must be an object of credential fields."
                )
            auth_config = {
                str(key): str(value) for key, value in (declared_auth or {}).items() if value
            }

        tos_path: Optional[str] = None
        param_config: Optional[Dict[str, Any]] = None
        if add_type == "tos":
            source_path = path[len("tos://") :].strip()
            if not source_path:
                raise InvalidArgumentError(
                    "Connector TOS import requires path='tos://<bucket>/<path>'."
                )
            tos_path = source_path
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

        client = ConnectorClient(
            doc_add_url=config.connector,
            task_info_url=config.tracker,
            account_id=ctx.account_id,
        )
        extra_params = None
        if tags is not None:
            extra_params = {
                "tags": normalize_search_tags(tags),
                "tag_mode": tag_mode,
            }

        task_tracker = get_task_tracker()
        task = await task_tracker.create(
            "connector_import",
            resource_id=task_resource_id,
            account_id=ctx.account_id,
            user_id=ctx.user.user_id,
        )
        try:
            result = await client.submit_doc_add(
                add_type=add_type,
                api_key=ctx.api_key,
                tos_path=tos_path,
                to=task_resource_id,
                include_child=True,
                param_config=param_config,
                auth_config=auth_config or None,
                extra_params=extra_params,
            )

            connector_task_key = result.get("task_key") or result.get("TaskKey") or ""
            if not connector_task_key:
                raise InternalError(
                    f"Connector accepted the import but returned no task key: {result}"
                )
        except asyncio.CancelledError:
            await task_tracker.fail(
                task.task_id,
                "connector task submission cancelled",
                account_id=ctx.account_id,
                user_id=ctx.user.user_id,
            )
            raise
        except Exception as exc:
            await task_tracker.fail(
                task.task_id,
                str(exc),
                account_id=ctx.account_id,
                user_id=ctx.user.user_id,
            )
            raise

        monitor = self._monitor(
            client=client,
            connector_task_key=connector_task_key,
            ov_task_id=task.task_id,
            poll_interval_ms=config.poll_interval_ms,
            timeout_seconds=config.timeout_seconds,
            ctx=ctx,
            reason=reason,
            link_root_uri=task_resource_id or "viking://resources",
        )

        background = asyncio.create_task(monitor)
        self._background_tasks.add(background)
        background.add_done_callback(self._background_tasks.discard)

        response = {
            "status": "accepted",
            "task_id": task.task_id,
            "connector_task_key": connector_task_key,
        }
        if task_resource_id:
            response["resource_id"] = task_resource_id
        return response

    async def _monitor(
        self,
        client: ConnectorClient,
        connector_task_key: str,
        ov_task_id: str,
        poll_interval_ms: int,
        timeout_seconds: int,
        ctx: RequestContext,
        reason: str = "",
        link_root_uri: str = "",
    ) -> Dict[str, Any]:
        """Poll the Connector task until terminal state, then update OV TaskRecord.

        Returns a terminal payload: ``{"status": "completed", ...}`` on
        success, ``{"status": "failed", "error": ...}`` otherwise. When
        *reason* is set, a successful import links one reason memory to
        *link_root_uri* (the import root), matching the native add_resource
        reason semantics of one reason entry referencing the resource root.
        """
        from openviking.service.task_tracker import get_task_tracker

        task_tracker = get_task_tracker()
        await task_tracker.start(
            ov_task_id,
            account_id=ctx.account_id,
            user_id=ctx.user.user_id,
        )

        poll_interval = poll_interval_ms / 1000.0
        deadline = time.perf_counter() + timeout_seconds
        terminal_statuses = {"succeeded", "failed", "cancelled"}

        try:
            while time.perf_counter() < deadline:
                await asyncio.sleep(poll_interval)
                try:
                    info = await client.get_task_info(connector_task_key, ctx.api_key)
                except httpx.HTTPStatusError as exc:
                    status_code = exc.response.status_code
                    if status_code not in {408, 429} and status_code < 500:
                        raise
                    logger.warning(
                        "[ConnectorDelegate] Transient Connector task polling HTTP error "
                        f"for {connector_task_key}: {status_code}; retrying"
                    )
                    continue
                except httpx.RequestError as exc:
                    logger.warning(
                        "[ConnectorDelegate] Transient Connector task polling error "
                        f"for {connector_task_key}: {exc}; retrying"
                    )
                    continue
                status = (info.get("Status") or info.get("status") or "").lower()

                await task_tracker.update_stage(
                    ov_task_id,
                    f"connector:{status}",
                    account_id=ctx.account_id,
                    user_id=ctx.user.user_id,
                )

                if status in terminal_statuses:
                    if status == "succeeded":
                        completion: Dict[str, Any] = {
                            "connector_status": status,
                            "connector_task_key": connector_task_key,
                        }
                        if (reason or "").strip() and link_root_uri:
                            link_result: Dict[str, Any] = {"root_uri": link_root_uri}
                            await self._link_reason_memory(
                                result=link_result,
                                ctx=ctx,
                                reason=reason,
                                source_name=None,
                                timeout=None,
                            )
                            for key in ("memory_linking", "warnings"):
                                if key in link_result:
                                    completion[key] = link_result[key]
                        await task_tracker.complete(
                            ov_task_id,
                            completion,
                            account_id=ctx.account_id,
                            user_id=ctx.user.user_id,
                        )
                        return {"status": "completed", **completion}
                    error_msg = info.get("ErrorMessage") or info.get("error_message") or status
                    failure = f"connector task {status}: {error_msg}"
                    await task_tracker.fail(
                        ov_task_id,
                        failure,
                        account_id=ctx.account_id,
                        user_id=ctx.user.user_id,
                    )
                    return {"status": "failed", "error": failure}

            timeout_msg = f"connector task timed out after {timeout_seconds}s"
            await task_tracker.fail(
                ov_task_id,
                timeout_msg,
                account_id=ctx.account_id,
                user_id=ctx.user.user_id,
            )
            return {"status": "failed", "error": timeout_msg}
        except asyncio.CancelledError:
            await task_tracker.fail(
                ov_task_id,
                "background connector task monitoring cancelled",
                account_id=ctx.account_id,
                user_id=ctx.user.user_id,
            )
            raise
        except Exception as exc:
            logger.error(f"[ConnectorDelegate] Connector task monitor error: {exc}")
            await task_tracker.fail(
                ov_task_id,
                str(exc),
                account_id=ctx.account_id,
                user_id=ctx.user.user_id,
            )
            return {"status": "failed", "error": str(exc)}
