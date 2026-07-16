# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Client for the external Connector service (knowledge-base doc/add pipeline)."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

import httpx

from openviking_cli.exceptions import InternalError
from openviking_cli.utils import get_logger

logger = get_logger(__name__)


def _unwrap_connector_response(payload: Any) -> Dict[str, Any]:
    """Validate the Connector business envelope and return its data object.

    The inner APIs return HTTP 200 for both success and business errors, so
    ``raise_for_status`` alone is insufficient. Flat dictionaries remain
    accepted for compatibility with Connector deployments that do not wrap
    responses in ``code/data``.
    """
    if not isinstance(payload, dict):
        raise InternalError("Connector returned a non-object JSON response")

    if "code" not in payload and "data" not in payload:
        return payload

    code = payload.get("code")
    if code not in (0, "0"):
        message = payload.get("message") or "unknown Connector error"
        raise InternalError(f"Connector request failed (code={code}): {message}")

    data = payload.get("data")
    if not isinstance(data, dict):
        raise InternalError("Connector success response contains no data object")
    return data


class ConnectorClient:
    """Wraps the Connector service's doc/add and task/info APIs."""

    def __init__(self, doc_add_url: str, task_info_url: str, account_id: str = "") -> None:
        self._doc_add_url = doc_add_url
        self._task_info_url = task_info_url
        self._headers = {"V-Account-Id": account_id} if account_id else {}

    def _request_headers(self, api_key: str) -> Dict[str, str]:
        """Build the headers required by the inner Connector endpoints."""
        headers = dict(self._headers)
        token = api_key if api_key.lower().startswith("bearer ") else f"Bearer {api_key}"
        headers["Authorization"] = token
        return headers

    async def submit_doc_add(
        self,
        add_type: str,
        api_key: str,
        *,
        tos_path: Optional[str] = None,
        path_prefix: Optional[List[str]] = None,
        include_child: bool = True,
        extra_params: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Submit a document import job via the configured doc/add endpoint.

        Returns the Connector response dict (contains task key / id on success).
        """
        payload: Dict[str, Any] = {
            "add_type": add_type,
            "backend": "ov",
            "include_child": include_child,
        }
        if tos_path is not None:
            payload["tos_path"] = tos_path
        if path_prefix is not None:
            payload["path_prefix"] = path_prefix
        if extra_params:
            # Authentication belongs exclusively in the Authorization header.
            payload.update({key: value for key, value in extra_params.items() if key != "api_key"})

        async with httpx.AsyncClient(timeout=30.0) as client:
            rsp = await client.post(
                self._doc_add_url,
                json=payload,
                headers=self._request_headers(api_key),
            )
        rsp.raise_for_status()
        return _unwrap_connector_response(rsp.json())

    async def get_task_info(self, task_key: str, api_key: str) -> Dict[str, Any]:
        """Query task status via the configured task/info endpoint.

        Connector task statuses: pending / running / succeeded / failed / cancelled.
        """
        async with httpx.AsyncClient(timeout=10.0) as client:
            rsp = await client.post(
                self._task_info_url,
                json={"TaskKey": task_key},
                headers=self._request_headers(api_key),
            )
        rsp.raise_for_status()
        data = _unwrap_connector_response(rsp.json())
        task = data.get("Task") or data.get("task")
        if task is None:
            return data
        if not isinstance(task, dict):
            raise InternalError("Connector task response contains an invalid Task object")
        return task
