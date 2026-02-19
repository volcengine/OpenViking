# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
import json
from typing import Any, Dict, Optional

import requests

from openviking_cli.utils.logger import default_logger as logger

# Default request timeout (seconds)
DEFAULT_TIMEOUT = 30

# VikingDB API Version
VIKING_DB_VERSION = "2025-06-09"

# SDK Action to VikingDB API path and method mapping
VIKINGDB_APIS = {
    # Collection APIs
    "ListVikingdbCollection": ("/api/vikingdb/ListCollection", "POST"),
    "CreateVikingdbCollection": ("/api/vikingdb/CreateCollection", "POST"),
    "DeleteVikingdbCollection": ("/api/vikingdb/DeleteCollection", "POST"),
    "UpdateVikingdbCollection": ("/api/vikingdb/UpdateCollection", "POST"),
    "GetVikingdbCollection": ("/api/vikingdb/GetCollection", "POST"),
    # Index APIs
    "ListVikingdbIndex": ("/api/vikingdb/ListIndex", "POST"),
    "CreateVikingdbIndex": ("/api/vikingdb/CreateIndex", "POST"),
    "DeleteVikingdbIndex": ("/api/vikingdb/DeleteIndex", "POST"),
    "UpdateVikingdbIndex": ("/api/vikingdb/UpdateIndex", "POST"),
    "GetVikingdbIndex": ("/api/vikingdb/GetIndex", "POST"),
    # ApiKey APIs
    "ListVikingdbApiKey": ("/api/vikingdb/list", "POST"),
    "CreateVikingdbApiKey": ("/api/vikingdb/create", "POST"),
    "DeleteVikingdbApiKey": ("/api/vikingdb/delete", "POST"),
    "UpdateVikingdbApiKey": ("/api/vikingdb/update", "POST"),
    "ListVikingdbApiKeyResources": ("/api/apikey/resource/list", "POST"),
}


class VikingDBClient:
    """
    Client for VikingDB private deployment.
    Uses custom host and headers for authentication/context.
    """

    def __init__(self, host: str, headers: Optional[Dict[str, str]] = None):
        """
        Initialize VikingDB client.

        Args:
            host: VikingDB service host (e.g., "http://localhost:8080")
            headers: Custom headers for requests
        """
        self.host = host.rstrip("/")
        self.headers = headers or {}

        if not self.host:
            raise ValueError("Host is required for VikingDBClient")

    def do_req(
        self,
        method: str,
        path: str = "/",
        req_params: Optional[Dict[str, Any]] = None,
        req_body: Optional[Dict[str, Any]] = None,
    ) -> requests.Response:
        """
        Perform HTTP request to VikingDB service.

        Args:
            method: HTTP method (GET, POST, etc.)
            path: Request path
            req_params: Query parameters
            req_body: Request body

        Returns:
            requests.Response object
        """
        if not path.startswith("/"):
            path = "/" + path

        url = f"{self.host}{path}"
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
        }
        headers.update(self.headers)

        try:
            response = requests.request(
                method=method,
                url=url,
                headers=headers,
                params=req_params,
                data=json.dumps(req_body) if req_body is not None else None,
                timeout=DEFAULT_TIMEOUT,
            )
            return response
        except Exception as e:
            logger.error(f"Request to {url} failed: {e}")
            raise e
