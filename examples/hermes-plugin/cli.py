"""``hermes openviking doctor`` and ``hermes openviking status``.

Hermes imports this module during argparse setup, before (and without) the
provider module, so everything past ``register_cli`` imports ``core`` at call time.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Optional

_MCP_INITIALIZE = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {
        "protocolVersion": "2025-03-26",
        "capabilities": {},
        "clientInfo": {"name": "openviking-hermes-doctor", "version": "0"},
    },
}


def register_cli(subparser) -> None:
    """Build ``hermes openviking {doctor,status}`` on the ``hermes openviking`` parser."""
    subs = subparser.add_subparsers(dest="openviking_command")
    doctor = subs.add_parser("doctor", help="Check the OpenViking plugin, config, server and local state")
    doctor.add_argument("--offline", action="store_true", help="Skip every network check")
    doctor.add_argument("--json", action="store_true", help="Print the report as JSON")
    status = subs.add_parser("status", help="One-screen summary of the OpenViking connection")
    status.add_argument("--offline", action="store_true", help="Skip every network check")
    subparser.set_defaults(func=openviking_command)


def openviking_command(args, *, transport: Any = None, hermes_home: Optional[str] = None) -> int:
    """Route ``hermes openviking`` subcommands; no subcommand means ``status``."""
    sub = getattr(args, "openviking_command", None) or "status"
    if sub not in ("doctor", "status"):
        print(f"Unknown openviking command: {sub}\nAvailable: doctor, status")
        return 2
    report = collect_report(hermes_home, offline=bool(getattr(args, "offline", False)), transport=transport)
    if sub == "doctor" and getattr(args, "json", False):
        print(json.dumps(report, indent=2, sort_keys=True, default=str))
    else:
        print(format_doctor(report) if sub == "doctor" else format_status(report))
    return 0 if report["ok"] else 1


def mask_secret(value: str) -> str:
    """``""`` stays empty; otherwise only the last four characters of a long secret show."""
    value = value or ""
    if not value:
        return ""
    return f"****{value[-4:]}" if len(value) > 12 else "****"


def _source(key: str, *, env: Optional[dict], ovcli: dict, config: dict) -> str:
    env_key = f"OPENVIKING_{key.upper()}"
    if env is not None and env.get(env_key) is not None and (env[env_key].strip() or key in ("account", "user")):
        return f"env {env_key}"
    if ovcli.get(key):
        return "ovcli profile"
    if key != "api_key" and str(config.get(key) or "").strip():
        return "config.yaml memory.openviking"
    return "default" if key != "api_key" else "none"


def collect_report(hermes_home: Optional[str] = None, *, offline: bool = False, transport: Any = None) -> dict:
    """Everything ``doctor`` knows, as plain data. Secrets are masked before they get here."""
    from .core.connection import (
        _load_hermes_openviking_config,
        _profile_openviking_env,
        _resolve_connection_settings,
    )
    from .core.host import get_hermes_home
    from .core.http import _read_plugin_version
    from .core.ovcli import _ovcli_values_for, _resolve_ovcli_config_path
    from .core.state_store import StateStoreMixin
    from .core.tool_catalog import cache_path, read_disk_cache

    home = str(hermes_home or get_hermes_home())
    report: dict = {
        "ok": True,
        "problems": [],
        "plugin": {"version": _read_plugin_version(), "location": str(Path(__file__).resolve().parent)},
        "hermes_home": home,
    }

    env = _profile_openviking_env(home)
    config = _load_hermes_openviking_config(home, env=env)
    ovcli = _ovcli_values_for(config, env=env)
    try:
        settings = _resolve_connection_settings(config, env=env)
    except ValueError as exc:  # an endpoint that fails validation
        report["ok"] = False
        report["problems"].append(f"Invalid connection settings: {exc}")
        settings = {}
    report["config"] = {
        "source": str(Path(home) / "config.yaml"),
        "use_ovcli_config": bool(config.get("use_ovcli_config")),
        "ovcli_config_path": str(_resolve_ovcli_config_path(str(config.get("ovcli_config_path") or ""), env=env))
        if config.get("use_ovcli_config") else "",
        "values": {k: (mask_secret(str(v)) if k == "api_key" else v) for k, v in settings.items()},
        "sources": {k: _source(k, env=env, ovcli=ovcli, config=config) for k in settings},
    }
    report["credential_source"] = report["config"]["sources"].get("api_key", "none")

    endpoint = settings.get("endpoint", "")
    tools = read_disk_cache(home, endpoint) if endpoint else None
    report["tool_cache"] = {
        "path": str(cache_path(home)),
        "state": "missing" if not cache_path(home).exists() else ("stale" if tools is None else "fresh"),
        "tools": len(tools) if tools is not None else 0,
    }
    pending = StateStoreMixin._pending_sessions(SimpleNamespace(_hermes_home=home))
    report["pending_sessions"] = [{"session_id": sid, "owner_run_id": owner, "marker": str(path)}
                                  for sid, owner, path, _key in pending]

    if offline or not endpoint:
        report["server"] = {"checked": False}
        report["mcp"] = {"checked": False}
    else:
        report["server"], report["mcp"] = _check_server(settings, transport)
        if not report["server"].get("identified"):
            report["problems"].append(report["server"].get("error") or "OpenViking server is not identified")
        elif not report["server"].get("authenticated"):
            report["problems"].append(report["server"].get("error") or "Authenticated status call failed")
        if not report["mcp"].get("reachable"):
            report["problems"].append(report["mcp"].get("error") or "/mcp is not reachable")
    if pending:
        report["problems"].append(f"{len(pending)} session(s) not yet committed to OpenViking")
    report["ok"] = not report["problems"]
    return report


def _check_server(settings: dict, transport: Any) -> tuple[dict, dict]:
    from .core.health import _identity_failure
    from .core.http import (
        _OPENVIKING_IDENTIFIED_STATES,
        _format_openviking_exception,
        _openviking_user_agent,
        _probe_openviking_identity,
        _VikingClient,
        build_openviking_headers,
    )

    server: dict = {"checked": True, "endpoint": settings["endpoint"], "identified": False, "authenticated": False}
    try:
        client = _VikingClient(settings["endpoint"], settings["api_key"], settings["account"], settings["user"],
                               settings["agent"], transport=transport)
    except ImportError as exc:
        server["error"] = str(exc)
        return server, {"checked": False, "reachable": False, "error": str(exc)}
    try:
        identity, health = _probe_openviking_identity(client)
        server["identity"] = identity
        server["version"] = health.get("version") if isinstance(health, dict) else None
        server["identified"] = identity in _OPENVIKING_IDENTIFIED_STATES
        if not server["identified"]:
            server["error"] = _identity_failure(identity, "OpenViking server")
    except Exception as exc:
        server["error"] = f"GET /health failed: {_format_openviking_exception(exc)}"
    if server["identified"]:
        try:
            result = (client.validate_auth() or {}).get("result") or {}
            server["authenticated"] = True
            server["user"] = result.get("user") if isinstance(result, dict) else None
            server["account"] = result.get("account") if isinstance(result, dict) else None
        except Exception as exc:
            server["error"] = f"GET /api/v1/system/status failed: {_format_openviking_exception(exc)}"

    mcp: dict = {"checked": True, "url": f"{settings['endpoint']}/mcp", "reachable": False}
    headers = build_openviking_headers(api_key=settings["api_key"], account=settings["account"],
                                       user=settings["user"], trusted_identity=not settings["api_key"],
                                       actor_peer_id=settings["agent"], user_agent=_openviking_user_agent())
    headers["Accept"] = "application/json, text/event-stream"
    try:
        resp = client._httpx.post(mcp["url"], json=_MCP_INITIALIZE, headers=headers, timeout=3.0)
        mcp["status_code"] = resp.status_code
        mcp["reachable"] = 200 <= resp.status_code < 300
        if not mcp["reachable"]:
            mcp["error"] = f"/mcp answered HTTP {resp.status_code}"
    except Exception as exc:
        mcp["error"] = f"/mcp failed: {_format_openviking_exception(exc)}"
    return server, mcp


def format_doctor(report: dict) -> str:
    cfg = report["config"]
    lines = [
        f"OpenViking plugin {report['plugin']['version']} at {report['plugin']['location']}",
        f"Hermes home: {report['hermes_home']}",
        f"Config: {cfg['source']}" + (f" (ovcli: {cfg['ovcli_config_path']})" if cfg["use_ovcli_config"] else ""),
    ]
    for key, value in cfg["values"].items():
        lines.append(f"  {key}: {value or '(empty)'}  [{cfg['sources'][key]}]")
    lines.append(f"Credential source: {report['credential_source']}")
    server, mcp = report["server"], report["mcp"]
    if not server.get("checked"):
        lines.append("Server: not checked (offline)")
    else:
        state = "ok" if server.get("authenticated") else ("reachable" if server.get("identified") else "FAILED")
        lines.append(f"Server: {state} identity={server.get('identity', '-')} version={server.get('version') or '-'}"
                     f" user={server.get('user') or '-'}")
    if mcp.get("checked"):
        lines.append(f"MCP: {'ok' if mcp.get('reachable') else 'FAILED'} {mcp['url']}")
    else:
        lines.append("MCP: not checked (offline)")
    cache = report["tool_cache"]
    lines.append(f"Tool cache: {cache['state']} ({cache['tools']} tools) {cache['path']}")
    lines.append(f"Uncommitted sessions: {len(report['pending_sessions'])}")
    for item in report["pending_sessions"]:
        lines.append(f"  {item['session_id']}")
    lines.append("Problems:" if report["problems"] else "No problems found.")
    lines.extend(f"  - {p}" for p in report["problems"])
    return "\n".join(lines)


def format_status(report: dict) -> str:
    values, server = report["config"]["values"], report["server"]
    if not server.get("checked"):
        state = "not checked"
    elif server.get("authenticated"):
        state = f"connected (user {server.get('user') or '-'}, OpenViking {server.get('version') or '?'})"
    else:
        state = "unreachable" if not server.get("identified") else "authentication failed"
    return "\n".join([
        f"OpenViking {report['plugin']['version']}: {state}",
        f"Endpoint: {values.get('endpoint', '-')}  key: {values.get('api_key') or 'none'}",
        f"Tools cached: {report['tool_cache']['tools']}  uncommitted sessions: {len(report['pending_sessions'])}",
    ])
