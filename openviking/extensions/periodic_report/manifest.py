# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Manifest for the optional periodic-report archive provider."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

EXTENSION_MANIFEST: dict[str, Any] = {
    "schema_version": "openviking_extension_manifest_v0",
    "id": "openviking.periodic-report.archive",
    "version": "0.1.0",
    "protocol_version": "periodic_report_sink_v0",
    "default_enabled": False,
    "provider_kind": "optional",
    "entrypoint": ("openviking.extensions.periodic_report:PeriodicReportArchiveProvider"),
    "requires_capability": {
        "capability_id": "periodic-report",
        "activation_schema": "periodic_report_activation_v0",
        "profile_enabled": True,
        "sink_binding_schema": "periodic_report_sink_binding_v0",
        "accepted_dependency_policies": ["optional", "required"],
    },
    "capabilities": {
        "report.archive.write": {"version": "v0", "method": "archive_sink"},
        "report.archive.readback": {"version": "v0", "method": "readback"},
        "report.query": {"version": "v0", "method": "query"},
    },
    "boundary": {
        "owns": ["archive_write", "archive_readback", "archive_query"],
        "does_not_own": [
            "trigger",
            "cadence",
            "source_selection",
            "rendering",
            "lark_delivery",
        ],
        "failure_scope": "archive_sink_only",
    },
}


def get_extension_manifest() -> dict[str, Any]:
    """Return an isolated manifest copy for an extension registry."""

    return deepcopy(EXTENSION_MANIFEST)
