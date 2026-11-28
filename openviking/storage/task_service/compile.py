# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Remote adapter for the current CompileStore config and run contracts."""

from collections.abc import AsyncIterator, Mapping
from typing import Any

from openviking.service.compile_targets import CompileConfigKey
from openviking.storage.internal_http import InternalServiceError, Route, WireModel

from .http_client import HTTPTaskService
from .protocol import BASE_PATH

COMPILE_BASE_PATH = f"{BASE_PATH}/compile"
_CONFIG_PAGE_SIZE = 128


class CreateConfigRequest(WireModel):
    account_id: str
    owner_user_id: str
    compile_name: str
    record: dict


class ListConfigsRequest(WireModel):
    account_id: str | None = None
    user_id: str | None = None
    compile_name: str | None = None
    cursor: str | None = None
    limit: int | None = None
    fresh: bool = False
    auto_update: bool | None = None
    after_key: CompileConfigKey | None = None


class UpdateConfigRequest(WireModel):
    account_id: str
    owner_user_id: str
    compile_name: str
    changes: dict
    expected: Mapping[str, Any] | None = None


class CleanupRequest(WireModel):
    account_id: str
    owner_user_id: str | None = None


class CreateRunRequest(WireModel):
    account_id: str
    owner_user_id: str
    compile_name: str
    run: dict


class ListRunsRequest(WireModel):
    account_id: str | None = None
    owner_user_id: str | None = None
    compile_name: str | None = None
    task_id: str | None = None
    limit: int | None = None
    cursor: str | None = None
    keys_only: bool = False
    finished_before: float | None = None
    statuses: frozenset[str] | None = None
    after_key: tuple[float, int, str] | None = None


class UpdateRunRequest(WireModel):
    account_id: str
    owner_user_id: str
    compile_name: str
    task_id: str
    changes: dict
    expected: Mapping[str, Any] | None = None
    replace: bool = False


class DeleteRunRequest(WireModel):
    account_id: str
    owner_user_id: str
    compile_name: str
    task_id: str
    expected: Mapping[str, Any]


COMPILE_ROUTES: dict[str, Route] = {
    "create_config": Route(
        f"{COMPILE_BASE_PATH}/configs/create",
        CreateConfigRequest,
        type(None),
        mutating=True,
    ),
    "list_configs": Route(
        f"{COMPILE_BASE_PATH}/configs/list",
        ListConfigsRequest,
        list[tuple[str, str, dict]],
    ),
    "update_config": Route(
        f"{COMPILE_BASE_PATH}/configs/update",
        UpdateConfigRequest,
        bool,
        mutating=True,
    ),
    "delete_config": Route(
        f"{COMPILE_BASE_PATH}/configs/cleanup", CleanupRequest, type(None), mutating=True
    ),
    "create_run": Route(f"{COMPILE_BASE_PATH}/runs/create", CreateRunRequest, bool, mutating=True),
    "list_runs": Route(f"{COMPILE_BASE_PATH}/runs/list", ListRunsRequest, list[dict]),
    "update_run": Route(f"{COMPILE_BASE_PATH}/runs/update", UpdateRunRequest, bool, mutating=True),
    "delete_run": Route(f"{COMPILE_BASE_PATH}/runs/delete", DeleteRunRequest, bool, mutating=True),
}


class HTTPCompileStore:
    def __init__(self, http: HTTPTaskService) -> None:
        self._http = http

    async def load(self) -> None:
        await self._http.load()

    async def close(self) -> None:
        await self._http.close()

    async def create_config(
        self, account_id: str, owner_user_id: str, compile_name: str, record: dict
    ) -> None:
        await self._http.request(
            COMPILE_ROUTES["create_config"],
            CreateConfigRequest(
                account_id=account_id,
                owner_user_id=owner_user_id,
                compile_name=compile_name,
                record=record,
            ),
        )

    async def update_config(
        self,
        account_id: str,
        owner_user_id: str,
        compile_name: str,
        changes: dict,
        expected: Mapping[str, Any] | None = None,
    ) -> bool:
        return await self._http.request(
            COMPILE_ROUTES["update_config"],
            UpdateConfigRequest(
                account_id=account_id,
                owner_user_id=owner_user_id,
                compile_name=compile_name,
                changes=changes,
                expected=expected,
            ),
        )

    async def list_configs(
        self,
        account_id: str | None = None,
        user_id: str | None = None,
        compile_name: str | None = None,
        cursor: str | None = None,
        limit: int | None = None,
        fresh: bool = False,
        auto_update: bool | None = None,
        after_key: CompileConfigKey | None = None,
    ) -> AsyncIterator[tuple[str, str, dict]]:
        remaining = limit
        while remaining is None or remaining > 0:
            size = _CONFIG_PAGE_SIZE if remaining is None else min(remaining, _CONFIG_PAGE_SIZE)
            page = await self._http.request(
                COMPILE_ROUTES["list_configs"],
                ListConfigsRequest(
                    account_id=account_id,
                    user_id=user_id,
                    compile_name=compile_name,
                    cursor=cursor,
                    limit=size,
                    fresh=fresh,
                    auto_update=auto_update,
                    after_key=after_key,
                ),
            )
            if len(page) > size:
                raise InternalServiceError("invalid_response", 200, False)
            has_next = len(page) == size and (remaining is None or remaining > size)
            if has_next:
                account, name, record = page[-1]
                context = record.get("execution_context")
                owner = context.get("owner_user_id") if isinstance(context, dict) else None
                if not isinstance(owner, str) or not owner or (account, name, owner) == after_key:
                    raise InternalServiceError("invalid_response", 200, False)
                after_key = (account, name, owner)
            for row in page:
                yield row
            if not has_next:
                return
            if remaining is not None:
                remaining -= len(page)

    async def delete_config(self, account_id: str, owner_user_id: str | None = None) -> None:
        if not account_id:
            raise ValueError("Compile account cleanup requires a non-empty account_id")
        await self._http.request(
            COMPILE_ROUTES["delete_config"],
            CleanupRequest(account_id=account_id, owner_user_id=owner_user_id),
        )

    async def list_runs(
        self,
        account_id: str | None = None,
        owner_user_id: str | None = None,
        compile_name: str | None = None,
        task_id: str | None = None,
        limit: int | None = None,
        cursor: str | None = None,
        keys_only: bool = False,
        finished_before: float | None = None,
        statuses: frozenset[str] | None = None,
        after_key: tuple[float, int, str] | None = None,
    ) -> list[dict]:
        return await self._http.request(
            COMPILE_ROUTES["list_runs"],
            ListRunsRequest(
                account_id=account_id,
                owner_user_id=owner_user_id,
                compile_name=compile_name,
                task_id=task_id,
                limit=limit,
                cursor=cursor,
                keys_only=keys_only,
                finished_before=finished_before,
                statuses=statuses,
                after_key=after_key,
            ),
        )

    async def create_run(
        self, account_id: str, owner_user_id: str, compile_name: str, run: dict
    ) -> bool:
        return await self._http.request(
            COMPILE_ROUTES["create_run"],
            CreateRunRequest(
                account_id=account_id,
                owner_user_id=owner_user_id,
                compile_name=compile_name,
                run=run,
            ),
        )

    async def update_run(
        self,
        account_id: str,
        owner_user_id: str,
        compile_name: str,
        task_id: str,
        changes: dict,
        expected: Mapping[str, Any] | None = None,
        replace: bool = False,
    ) -> bool:
        return await self._http.request(
            COMPILE_ROUTES["update_run"],
            UpdateRunRequest(
                account_id=account_id,
                owner_user_id=owner_user_id,
                compile_name=compile_name,
                task_id=task_id,
                changes=changes,
                expected=expected,
                replace=replace,
            ),
        )

    async def delete_run(
        self,
        account_id: str,
        owner_user_id: str,
        compile_name: str,
        task_id: str,
        expected: Mapping[str, Any],
    ) -> bool:
        return await self._http.request(
            COMPILE_ROUTES["delete_run"],
            DeleteRunRequest(
                account_id=account_id,
                owner_user_id=owner_user_id,
                compile_name=compile_name,
                task_id=task_id,
                expected=expected,
            ),
        )
