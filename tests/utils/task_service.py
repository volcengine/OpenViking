# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""In-process HTTP peer backed by real stores, used only for contract tests."""

import inspect
import json
from typing import Any

import httpx

from openviking.resource.watch_types import PermissionDeniedError
from openviking.service.task_store import TaskAlreadyActive
from openviking.storage.task_service import HTTPTaskService
from openviking.storage.task_service.protocol import (
    OPERATIONS,
    TaskCreationDTO,
    TaskRecordDTO,
)
from openviking_cli.exceptions import ConflictError, InvalidArgumentError
from openviking_cli.utils.config.http_store_config import HTTPStoreConfig

ROUTES = {route.path: (domain, name, route) for (domain, name), route in OPERATIONS.items()}


class StoreHTTPPeer:
    def __init__(self, *, tasks=None, watches=None) -> None:
        self.stores = {"tasks": tasks, "watches": watches}
        self.requests: list[httpx.Request] = []

    def client(self) -> HTTPTaskService:
        return HTTPTaskService(
            HTTPStoreConfig(base_url="http://kernel", token="library-token"),
            transport=httpx.MockTransport(self.handle),
        )

    async def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        assert request.method == "POST"
        assert request.headers["Authorization"] == "Bearer library-token"
        assert "x-api-key" not in request.headers
        domain, name, op = ROUTES[request.url.path]
        params = op.request.model_validate_json(request.content)
        values = {key: getattr(params, key) for key in type(params).model_fields}
        if isinstance(values.get("task"), TaskRecordDTO):
            values["task"] = values["task"].to_record()
        method = getattr(self.stores[domain], name)
        positional = []
        keyword = {}
        for key, param in inspect.signature(method).parameters.items():
            if param.kind == inspect.Parameter.KEYWORD_ONLY:
                keyword[key] = values[key]
            else:
                positional.append(values[key])
        try:
            result = await method(*positional, **keyword)
        except TaskAlreadyActive as exc:
            return self.error(
                409,
                "task_already_active",
                str(exc),
                {"task_type": exc.task_type, "resource_id": exc.resource_id},
            )
        except ConflictError as exc:
            return self.error(409, "conflict", str(exc), {"resource": exc.details.get("resource")})
        except PermissionDeniedError as exc:
            return self.error(403, "permission_denied", str(exc))
        except InvalidArgumentError as exc:
            return self.error(422, "invalid_argument", str(exc))
        except ValueError as exc:
            return self.error(422, "invalid_value", str(exc))
        if domain == "tasks":
            if name == "create":
                result = TaskCreationDTO(
                    task=TaskRecordDTO.from_record(result.task), created=result.created
                )
            elif name == "delete_tasks":
                result = op.response.model_validate(result, from_attributes=True)
            elif name in {"get_task", "get_task_with_events", "get_durable_task"}:
                result = TaskRecordDTO.from_record(result) if result is not None else None
            elif name in {"list", "list_durable"}:
                result = [TaskRecordDTO.from_record(task) for task in result]
        return httpx.Response(
            200, json={"result": json.loads(op.response_adapter.dump_json(result))}
        )

    @staticmethod
    def error(status: int, code: str, message: str, details: dict[str, Any] | None = None):
        return httpx.Response(
            status, json={"error": {"code": code, "message": message, "details": details or {}}}
        )
