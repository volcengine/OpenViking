"""Regression tests for the Lark/Uvicorn/WebSockets compatibility boundary."""

from __future__ import annotations

import importlib
import json
import os
import subprocess
import sys
import warnings
from pathlib import Path


def test_lark_sdk_no_longer_imports_deprecated_websocket_symbols() -> None:
    """The SDK must not trigger WebSockets legacy-symbol warnings at import."""

    with warnings.catch_warnings(record=True) as seen:
        warnings.simplefilter("always")
        importlib.import_module("lark_oapi.ws.client")

    websocket_warnings = [
        warning
        for warning in seen
        if "websocket" in str(warning.message).lower()
        or "websockets" in str(warning.message).lower()
    ]
    assert websocket_warnings == []


def test_uvicorn_sansio_websocket_protocol_imports_without_deprecation() -> None:
    """The selected Uvicorn protocol must avoid the deprecated legacy adapter."""

    with warnings.catch_warnings(record=True) as seen:
        warnings.simplefilter("always")
        importlib.import_module("uvicorn.protocols.websockets.auto")
        importlib.import_module("uvicorn.protocols.websockets.websockets_sansio_impl")

    assert [warning for warning in seen if issubclass(warning.category, DeprecationWarning)] == []


def test_lark_upstream_warning_ledger_is_explicit() -> None:
    """Keep the two known SDK warnings visible without filtering or patching them.

    A fresh interpreter is required because ``lark_oapi`` eagerly imports its
    WebSocket client.  The assertion is intentionally exact: a new warning is
    a regression, while disappearance of either known warning means the
    upstream version changed and the ledger must be reviewed before updating
    the dependency.
    """

    script = """
import json
import warnings

with warnings.catch_warnings(record=True) as captured:
    warnings.simplefilter("always")
    import lark_oapi  # noqa: F401

print(json.dumps([
    {
        "category": item.category.__name__,
        "message": str(item.message),
        "filename": item.filename.replace("\\\\", "/"),
    }
    for item in captured
]))
"""
    env = os.environ.copy()
    env.pop("PYTHONWARNINGS", None)
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=Path(__file__).resolve().parents[1],
        env=env,
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert result.stderr == ""
    records = json.loads(result.stdout)

    assert sorted(
        (
            record["category"],
            record["message"],
            Path(record["filename"]).as_posix().split("site-packages/")[-1],
        )
        for record in records
    ) == sorted(
        [
            (
                "DeprecationWarning",
                "datetime.datetime.utcfromtimestamp() is deprecated and scheduled for removal in a future version. Use timezone-aware objects to represent datetimes in UTC: datetime.datetime.fromtimestamp(timestamp, datetime.UTC).",
                "lark_oapi/ws/pb/google/protobuf/internal/well_known_types.py",
            ),
            (
                "DeprecationWarning",
                "There is no current event loop",
                "lark_oapi/ws/client.py",
            ),
        ]
    )
