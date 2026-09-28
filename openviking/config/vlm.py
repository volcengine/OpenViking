# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Account-aware VLM selection and call-scoped client lifecycle."""

from __future__ import annotations

import asyncio
import inspect
import threading
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Literal, Protocol

from openviking.config.account_config import AccountConfig
from openviking.config.manager import ConfigChangeEvent, RuntimeConfigManager
from openviking.config.scope import ScopeKind
from openviking.models.vlm.token_usage import TokenUsageTracker
from openviking_cli.utils.config import get_openviking_config
from openviking_cli.utils.config.open_viking_config import OpenVikingConfig
from openviking_cli.utils.config.vlm_config import VLMConfig

VLMRole = Literal["vlm", "query_planner"]
_RequestKey = tuple[str, VLMRole]
_ResourceKey = tuple[str, ...]


class VLMHandle(Protocol):
    """Async model-call surface returned by a resolver."""

    model: str | None
    max_concurrent: int
    max_tokens: int | None
    thinking: bool
    token_tracker: TokenUsageTracker

    def is_available(self) -> bool: ...

    def supports_media(self, *args, **kwargs) -> bool | Awaitable[bool]: ...

    async def get_completion_async(self, *args, **kwargs): ...

    async def get_vision_completion_async(self, *args, **kwargs): ...

    async def get_media_completion_async(self, *args, **kwargs): ...


class VLMResolver(Protocol):
    """Resolve a call-safe model handle for one Account."""

    async def get_vlm(self, account_id: str) -> VLMHandle: ...

    async def get_query_planner(self, account_id: str) -> VLMHandle: ...

    async def has_dedicated_query_planner(self, account_id: str) -> bool: ...


@dataclass(frozen=True)
class _VLMView:
    account: AccountConfig
    cluster: OpenVikingConfig


@dataclass(frozen=True)
class _VLMSelection:
    key: _ResourceKey
    config: VLMConfig
    tracker: TokenUsageTracker


@dataclass(eq=False)
class _VLMResource:
    key: _ResourceKey
    config: VLMConfig
    tracker: TokenUsageTracker
    bindings: int = 0
    borrowers: int = 0
    retired: bool = False
    closed: bool = False

    def close_if_idle(self) -> None:
        if self.retired and not self.borrowers and not self.closed:
            self.closed = True
            self.config.close()


class AccountBoundVLM:
    """Long-lived Account proxy; every model call uses the current resource."""

    def __init__(
        self,
        provider: "AccountVLMProvider",
        account_id: str,
        role: VLMRole,
        config: VLMConfig,
        tracker: TokenUsageTracker,
    ) -> None:
        self._provider = provider
        self._account_id = account_id
        self._role = role
        self._settings = self._copy_settings(config)
        self._tracker = tracker

    @staticmethod
    def _copy_settings(config: VLMConfig) -> VLMConfig:
        return VLMConfig.model_validate(config.model_dump(mode="python"))

    def _refresh(self, resource: _VLMResource) -> None:
        self._settings = self._copy_settings(resource.config)
        self._tracker = resource.tracker

    def __getattr__(self, name: str) -> Any:
        if name in VLMConfig.model_fields:
            return getattr(self._settings, name)
        raise AttributeError(name)

    @property
    def token_tracker(self) -> TokenUsageTracker:
        return self._tracker

    def get_token_usage(self) -> dict:
        return self._tracker.to_dict()

    def reset_token_usage(self) -> None:
        self._tracker.reset()

    def is_available(self) -> bool:
        return self._settings.is_available()

    async def supports_media(self, *args, **kwargs) -> bool:
        return await self._provider._invoke_maybe_async(
            self,
            "supports_media",
            *args,
            **kwargs,
        )

    async def get_completion_async(self, *args, **kwargs):
        return await self._provider._invoke(
            self,
            "get_completion_async",
            *args,
            **kwargs,
        )

    async def get_vision_completion_async(self, *args, **kwargs):
        return await self._provider._invoke(
            self,
            "get_vision_completion_async",
            *args,
            **kwargs,
        )

    async def get_media_completion_async(self, *args, **kwargs):
        return await self._provider._invoke(
            self,
            "get_media_completion_async",
            *args,
            **kwargs,
        )


class AccountVLMProvider:
    """Select, cache, borrow and retire Account VLM resources.

    Cluster fallback keeps pre-materialization Account documents working.
    New provisioning should persist complete Account-owned model settings
    instead of introducing additional runtime dependencies on Cluster config.
    """

    def __init__(
        self,
        manager: RuntimeConfigManager[OpenVikingConfig, AccountConfig],
    ) -> None:
        self._manager = manager
        self._bindings: dict[_RequestKey, _VLMResource] = {}
        self._resources: dict[_ResourceKey, _VLMResource] = {}
        self._retired: set[_VLMResource] = set()
        self._usage: dict[str, TokenUsageTracker] = {}
        self._planner_usage: dict[str, TokenUsageTracker] = {}
        self._lock = threading.RLock()
        self._epoch = 0
        self._closed = False
        manager.add_update_consumer(
            scope=ScopeKind.ACCOUNT,
            sections={"vlm", "query_planner"},
            consumer=self._on_account_config_change,
        )
        manager.add_update_consumer(
            scope=ScopeKind.CLUSTER,
            sections={"vlm", "query_planner"},
            consumer=self._on_cluster_config_change,
        )

    async def get_vlm(self, account_id: str) -> AccountBoundVLM:
        return await self._new_handle(account_id, "vlm")

    async def get_query_planner(self, account_id: str) -> AccountBoundVLM:
        return await self._new_handle(account_id, "query_planner")

    async def _new_handle(self, account_id: str, role: VLMRole) -> AccountBoundVLM:
        resource = await self._resource_for(account_id, role)
        return AccountBoundVLM(self, account_id, role, resource.config, resource.tracker)

    async def _view(self, account_id: str) -> _VLMView:
        if not account_id:
            raise ValueError("account_id is required")
        return await self._manager.resolve_account(
            account_id,
            lambda view: _VLMView(account=view.account, cluster=view.cluster),
        )

    async def _select(self, account_id: str, role: VLMRole) -> _VLMSelection:
        view = await self._view(account_id)
        if role == "vlm":
            return self._select_vlm(account_id, view)
        return self._select_query_planner(account_id, view)

    def _account_tracker(
        self,
        trackers: dict[str, TokenUsageTracker],
        account_id: str,
    ) -> TokenUsageTracker:
        with self._lock:
            return trackers.setdefault(account_id, TokenUsageTracker())

    def _select_vlm(self, account_id: str, view: _VLMView) -> _VLMSelection:
        account_vlm = view.account.vlm
        if account_vlm is None:
            return _VLMSelection(
                ("account", account_id, "vlm"),
                view.cluster.vlm,
                self._account_tracker(self._usage, account_id),
            )
        return _VLMSelection(
            ("account", account_id, "vlm"),
            account_vlm.to_vlm_config(view.cluster.vlm),
            self._account_tracker(self._usage, account_id),
        )

    def _select_query_planner(
        self,
        account_id: str,
        view: _VLMView,
    ) -> _VLMSelection:
        if view.account.query_planner is not None:
            return _VLMSelection(
                ("account", account_id, "query_planner"),
                view.account.query_planner.to_vlm_config(view.cluster.get_query_planner()),
                self._account_tracker(self._planner_usage, account_id),
            )
        if view.account.vlm is not None:
            return self._select_vlm(account_id, view)
        if view.cluster.query_planner is not None and view.cluster.query_planner._has_any_config():
            return _VLMSelection(
                ("account", account_id, "query_planner"),
                view.cluster.query_planner,
                self._account_tracker(self._planner_usage, account_id),
            )
        return self._select_vlm(account_id, view)

    @staticmethod
    def _create_resource(selection: _VLMSelection) -> _VLMResource:
        config = VLMConfig.model_validate(selection.config.model_dump(mode="python"))
        config.set_token_usage_tracker(selection.tracker)
        return _VLMResource(selection.key, config, selection.tracker)

    async def _resource_for(self, account_id: str, role: VLMRole) -> _VLMResource:
        request_key = (account_id, role)
        while True:
            with self._lock:
                if self._closed:
                    raise RuntimeError("Account VLM provider is closed")
                cached = self._bindings.get(request_key)
                if cached is not None:
                    return cached
                epoch = self._epoch

            selection = await self._select(account_id, role)
            candidate = self._create_resource(selection)
            with self._lock:
                if self._closed:
                    candidate.retired = True
                    candidate.close_if_idle()
                    raise RuntimeError("Account VLM provider is closed")
                if self._epoch != epoch:
                    candidate.retired = True
                    candidate.close_if_idle()
                    continue
                resource = self._resources.get(selection.key)
                if resource is None:
                    resource = candidate
                    self._resources[selection.key] = resource
                else:
                    candidate.retired = True
                    candidate.close_if_idle()
                existing = self._bindings.get(request_key)
                if existing is not None:
                    return existing
                self._bindings[request_key] = resource
                resource.bindings += 1
                return resource

    async def _borrow(self, account_id: str, role: VLMRole) -> _VLMResource:
        request_key = (account_id, role)
        while True:
            resource = await self._resource_for(account_id, role)
            with self._lock:
                if self._closed:
                    raise RuntimeError("Account VLM provider is closed")
                if self._bindings.get(request_key) is not resource or resource.retired:
                    continue
                resource.borrowers += 1
                return resource

    async def _invoke(
        self,
        handle: AccountBoundVLM,
        method: str,
        *args,
        **kwargs,
    ):
        resource = await self._borrow(handle._account_id, handle._role)
        handle._refresh(resource)
        try:
            return await getattr(resource.config, method)(*args, **kwargs)
        finally:
            self._release(resource)

    async def _invoke_maybe_async(
        self,
        handle: AccountBoundVLM,
        method: str,
        *args,
        **kwargs,
    ):
        resource = await self._borrow(handle._account_id, handle._role)
        handle._refresh(resource)
        try:
            result = getattr(resource.config, method)(*args, **kwargs)
            return await result if inspect.isawaitable(result) else result
        finally:
            self._release(resource)

    def _release(self, resource: _VLMResource) -> None:
        with self._lock:
            resource.borrowers -= 1
            resource.close_if_idle()
            if resource.closed:
                self._retired.discard(resource)

    async def _on_account_config_change(self, event: ConfigChangeEvent) -> None:
        account_id = event.scope.key
        if account_id is not None:
            self._invalidate(lambda key: key[0] == account_id)

    async def _on_cluster_config_change(self, event: ConfigChangeEvent) -> None:
        del event
        self._invalidate(lambda _key: True)

    def _invalidate(self, matches: Callable[[_RequestKey], bool]) -> None:
        with self._lock:
            self._epoch += 1
            removed = [
                self._bindings.pop(key)
                for key in list(self._bindings)
                if matches(key)
            ]
            for resource in removed:
                resource.bindings -= 1
                if resource.bindings:
                    continue
                self._resources.pop(resource.key, None)
                resource.retired = True
                self._retired.add(resource)
                resource.close_if_idle()
                if resource.closed:
                    self._retired.discard(resource)

    async def close(self) -> None:
        with self._lock:
            self._closed = True
        self._invalidate(lambda _key: True)
        while True:
            with self._lock:
                if not self._retired:
                    return
            await asyncio.sleep(0.01)

    def get_token_usage(self, account_id: str) -> dict:
        with self._lock:
            tracker = self._usage.get(account_id, TokenUsageTracker())
        return tracker.to_dict()

    def get_node_token_usage(self) -> dict:
        with self._lock:
            trackers = (*self._usage.values(), *self._planner_usage.values())
        return TokenUsageTracker.merge(*trackers).to_dict()

    async def has_dedicated_query_planner(self, account_id: str) -> bool:
        view = await self._view(account_id)
        if view.account.query_planner is not None or view.account.vlm is not None:
            return True
        planner = view.cluster.query_planner
        return planner is not None and planner._has_any_config()


class ClusterVLMResolver:
    """Resolve static Cluster model handles for offline and standalone flows."""

    def __init__(
        self,
        config_provider: Callable[[], OpenVikingConfig] = get_openviking_config,
    ) -> None:
        self._config_provider = config_provider

    def get_vlm_sync(self) -> VLMConfig:
        return self._config_provider().vlm

    def get_query_planner_sync(self) -> VLMConfig:
        return self._config_provider().get_query_planner()

    async def get_vlm(self, account_id: str) -> VLMConfig:
        del account_id
        return self.get_vlm_sync()

    async def get_query_planner(self, account_id: str) -> VLMConfig:
        del account_id
        return self.get_query_planner_sync()

    async def has_dedicated_query_planner(self, account_id: str) -> bool:
        del account_id
        planner = self._config_provider().query_planner
        return planner is not None and planner._has_any_config()
