# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""HTTP transport for Task, Work, Watch and Compile operations."""

from functools import cached_property

from openviking.resource.watch_types import PermissionDeniedError
from openviking.service.task_store import TaskAlreadyActive
from openviking.storage.internal_http import InternalStoreHTTP, ServiceError
from openviking_cli.exceptions import ConflictError, InvalidArgumentError, NotFoundError


class HTTPTaskService(InternalStoreHTTP):
    @cached_property
    def tasks(self):
        from .adapters import HTTPTaskWorkStore

        return HTTPTaskWorkStore(self)

    @cached_property
    def watches(self):
        from .adapters import HTTPWatchStore

        return HTTPWatchStore(self)

    @staticmethod
    def raise_service_error(error: ServiceError, status: int, mutating: bool) -> None:
        details = error.details
        if status == 404 and error.code.lower() == "not_found":
            resource = details.get("resource")
            resource_type = details.get("type")
            if (
                isinstance(resource, str)
                and resource
                and isinstance(resource_type, str)
                and resource_type
            ):
                raise NotFoundError(resource, resource_type)
        if status == 409 and error.code == "task_already_active":
            task_type = details.get("task_type")
            resource_id = details.get("resource_id")
            if isinstance(task_type, str) and (resource_id is None or isinstance(resource_id, str)):
                raise TaskAlreadyActive(task_type, resource_id)
        if status == 409 and error.code == "conflict":
            resource = details.get("resource")
            if resource is None or isinstance(resource, str):
                raise ConflictError(error.message, resource=resource)
        if status == 403 and error.code == "permission_denied":
            raise PermissionDeniedError(error.message)
        if status == 422 and error.code == "invalid_argument":
            raise InvalidArgumentError(error.message)
        if status == 422 and error.code == "invalid_value":
            raise ValueError(error.message)
        InternalStoreHTTP.raise_service_error(error, status, mutating)
