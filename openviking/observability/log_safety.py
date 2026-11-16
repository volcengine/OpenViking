# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Field allowlist shared by log, trace and audit adapters."""

import hashlib
import ipaddress
import math
import re
from pathlib import Path
from typing import Any, Mapping

DEFAULT_LOG_FORMAT = (
    "%(asctime)s %(levelname)s %(name)s pid=%(process)d "
    "request_id=%(request_id)s trace_id=%(trace_id)s message=%(message)s"
)
LEGACY_LOG_FORMAT = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"

LOG_FIELDS = frozenset(
    "ts account_id user_id request_id trace_id span_id operation http_method http_route "
    "http_status_code duration_ms role task_id task_type task_status task_stage "
    "attempt retry_limit delay queue_depth error_code error_type file_type event action result "
    "actor_id actor_role actor_type target_type target_id source_ip user_agent "
    "degraded sensitive truncated payload total failed skipped original_length".split()
)
_TEXT_ID = re.compile(r"^[A-Za-z0-9_.:/{}@-]{1,256}$")
_COUNTS = frozenset(
    "http_status_code duration_ms attempt retry_limit delay queue_depth total failed skipped original_length".split()
)
_FLAGS = frozenset("degraded sensitive truncated payload".split())
_USER_AGENT = re.compile(
    r"^(curl|python-httpx|python-requests|OpenViking)/([0-9]+(?:\.[0-9]+){0,3})$"
)


def short_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:8]


def safe_uri_for_log(uri: str) -> str:
    """Keep only the URI owner prefix, with an opaque locator for everything below it."""
    if re.fullmatch(r"viking://user/[A-Za-z0-9_.:-]+/hash/[0-9a-f]{8}", uri):
        return uri
    if uri.startswith("viking://user/"):
        owner = uri[len("viking://user/") :].split("/", 1)[0]
        if _TEXT_ID.fullmatch(owner):
            return f"viking://user/{owner}/hash/{short_hash(uri)}"
    return f"uri:{short_hash(uri)}"


def safe_path_for_log(path: str, workspace: str | Path | None = None) -> str:
    """Return an opaque locator; arbitrary file names and tenant layouts stay private."""
    if workspace is not None:
        try:
            relative = Path(path).resolve().relative_to(Path(workspace).resolve())
            return f"workspace:{short_hash(str(relative))}"
        except (ValueError, OSError):
            pass
    return f"path:{short_hash(path)}"


def safe_log_fields(fields: Mapping[str, Any]) -> dict[str, Any]:
    """Keep bounded primitive fields; never stringify arbitrary objects or exceptions."""
    safe: dict[str, Any] = {}
    for key, value in fields.items():
        if key not in LOG_FIELDS:
            continue
        if key == "source_ip" and value is not None:
            try:
                safe[key] = (
                    str(ipaddress.ip_address(value))
                    if isinstance(value, str) and "%" not in value
                    else None
                )
            except ValueError:
                safe[key] = None
            continue
        if key == "user_agent" and value is not None:
            safe[key] = (
                value
                if isinstance(value, str) and len(value) <= 256 and _USER_AGENT.fullmatch(value)
                else None
            )
            continue
        if value is None:
            safe[key] = None
        elif key in _FLAGS:
            if isinstance(value, bool):
                safe[key] = value
        elif key in _COUNTS:
            if (
                isinstance(value, (int, float))
                and not isinstance(value, bool)
                and value >= 0
                and (
                    (isinstance(value, int) and value.bit_length() <= 63)
                    or (isinstance(value, float) and math.isfinite(value))
                )
            ):
                safe[key] = value
        elif isinstance(value, str):
            if key in {"actor_id", "target_id"}:
                if value.startswith("viking://"):
                    safe[key] = safe_uri_for_log(value)
                    continue
                if "/" in value and fields.get("target_type") != "http_route":
                    safe[key] = safe_path_for_log(value)
                    continue
            if "peers/" not in value and "://" not in value and _TEXT_ID.fullmatch(value):
                safe[key] = value
    return safe


AUDIT_EVENTS = frozenset(
    "audit.account.create audit.account.delete audit.account.update "
    "audit.user.create audit.user.delete audit.user.join audit.user.remove audit.user.role.set "
    "audit.group.create audit.group.delete audit.group.member.add audit.group.member.remove "
    "audit.acl.grant audit.acl.revoke audit.acl.delete "
    "audit.api_key.create audit.api_key.regenerate audit.api_key.revoke audit.api_key.migrate audit.api_key.export "
    "audit.identity.bind audit.identity.unbind audit.identity.register "
    "audit.auth.failure audit.auth.unauthorized audit.auth.health "
    "audit.config.write audit.config.rollback audit.config.delete audit.data.delete audit.data.export "
    "audit.privacy.write audit.privacy.delete audit.task.delete audit.snapshot.delete audit.root.invoke".split()
)
AUDIT_REQUIRED_FIELDS = frozenset(
    "ts event result actor_id actor_role actor_type account_id target_type target_id action "
    "request_id trace_id source_ip user_agent error_code".split()
)


def validate_audit_record(fields: Mapping[str, Any]) -> dict[str, Any]:
    """Validate both log and persistence copies against the same closed schema."""
    safe = safe_log_fields(fields)
    if not AUDIT_REQUIRED_FIELDS.issubset(safe):
        raise ValueError("audit fields missing or unsafe")
    if safe["event"] not in AUDIT_EVENTS:
        raise ValueError("unregistered audit event")
    if safe["result"] not in {"success", "failure"}:
        raise ValueError("invalid audit result")
    if safe["actor_type"] not in {None, "user", "admin", "root", "service"}:
        raise ValueError("invalid audit actor type")
    if safe["actor_role"] not in {None, "user", "admin", "root", "global", "service"}:
        raise ValueError("invalid audit actor role")
    if safe["target_type"] not in {
        "account",
        "user",
        "group",
        "acl",
        "api_key",
        "identity",
        "config",
        "data",
        "privacy",
        "task",
        "snapshot",
        "http_route",
    }:
        raise ValueError("invalid audit target type")
    if safe["action"] != safe["event"].rsplit(".", 1)[-1]:
        raise ValueError("invalid audit action")
    if safe["result"] == "failure":
        if not isinstance(safe["error_code"], str) or not re.fullmatch(
            r"[A-Z][A-Z0-9_]{0,127}", safe["error_code"]
        ):
            raise ValueError("audit failure requires a stable error code")
    elif safe["error_code"] is not None:
        raise ValueError("successful audit must not have an error code")
    if not isinstance(safe["ts"], str) or not re.fullmatch(
        r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z", safe["ts"]
    ):
        raise ValueError("audit timestamp must be UTC milliseconds")
    if safe["event"] == "audit.root.invoke" and not {
        "http_method",
        "http_route",
        "http_status_code",
    }.issubset(safe):
        raise ValueError("root audit requires HTTP result fields")
    return safe
