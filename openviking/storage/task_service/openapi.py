# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Export the explicit v1 contract as OpenAPI 3.1.

Run with ``python -m openviking.storage.task_service.openapi <output.json>``.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from pydantic import TypeAdapter

from openviking.storage.internal_http import ErrorResponse, ResultEnvelope

from .compile import COMPILE_ROUTES
from .protocol import OPERATIONS, PROTOCOL_VERSION


def build_openapi() -> dict[str, Any]:
    operations = {
        **OPERATIONS,
        **{("compile", name): route for name, route in COMPILE_ROUTES.items()},
    }
    adapters = [
        ("error", "serialization", TypeAdapter(ErrorResponse)),
    ]
    for (domain, action), op in operations.items():
        name = f"{domain}.{action}"
        adapters.extend(
            [
                (f"{name}.request", "validation", TypeAdapter(op.request)),
                (f"{name}.response", "serialization", TypeAdapter(ResultEnvelope[op.response])),
            ]
        )
    schemas, definitions = TypeAdapter.json_schemas(
        adapters, ref_template="#/components/schemas/{model}"
    )

    def response(key: str, description: str) -> dict[str, Any]:
        return {
            "description": description,
            "content": {"application/json": {"schema": schemas[(key, "serialization")]}},
        }

    paths: dict[str, Any] = {}
    for (domain, action), op in operations.items():
        name = f"{domain}.{action}"
        paths[op.path] = {
            "post": {
                "operationId": f"{domain}_{action}",
                "tags": [domain],
                "summary": name,
                "description": (
                    "Atomic store mutation. Do not automatically retry after an ambiguous failure."
                    if op.mutating
                    else "Authoritative store read. The request body carries typed query parameters."
                ),
                "x-store-mutating": op.mutating,
                "requestBody": {
                    "required": True,
                    "content": {
                        "application/json": {"schema": schemas[(f"{name}.request", "validation")]}
                    },
                },
                "responses": {
                    "200": response(
                        f"{name}.response",
                        "Typed result, including business no-op/rejection or null if specified",
                    ),
                    "401": response("error", "Invalid service token"),
                    "403": response("error", "Permission denied"),
                    "409": response("error", "Conflict"),
                    "422": response("error", "Invalid argument"),
                    "500": response("error", "Service failure; mutation outcome may be unknown"),
                    "default": response("error", "Service failure"),
                },
            }
        }
        if op.request_secrets or op.response_secrets:
            paths[op.path]["post"]["x-store-field-encryption"] = {
                "format": "ovenc1",
                "request_paths": list(op.request_secrets),
                "response_result_paths": list(op.response_secrets),
            }
        if domain == "compile" and action in {"update_config", "create_run"}:
            paths[op.path]["post"]["responses"]["404"] = response(
                "error",
                "Compile configuration not found. Return code not_found, with "
                "details.type = 'compile configuration' and details.resource = compile_name.",
            )
    return {
        "openapi": "3.1.0",
        "info": {
            "title": "OpenViking internal TaskService",
            "version": PROTOCOL_VERSION,
            "description": (
                "Internal Task/Work, Watch and Compile persistence. "
                "The token selects the library; request fields cannot override that scope. "
                "When field_encryption_key is configured, OV encrypts the designated credentials. "
                "TaskService stores and returns their opaque values without decrypting them. "
                "String credentials use ovenc1: values; object credentials use a $ovenc1 envelope."
            ),
        },
        "paths": paths,
        "security": [{"LibraryToken": []}],
        "components": {
            "schemas": definitions["$defs"],
            "securitySchemes": {"LibraryToken": {"type": "http", "scheme": "bearer"}},
        },
    }


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit("Usage: python -m openviking.storage.task_service.openapi <output.json>")
    output = Path(sys.argv[1])
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(build_openapi(), ensure_ascii=False, indent=2) + "\n")


if __name__ == "__main__":
    main()
