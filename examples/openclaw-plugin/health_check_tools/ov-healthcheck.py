#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import sys
import ssl
import time
import uuid
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

DEFAULT_GATEWAY_URL = "http://127.0.0.1:19789"
DEFAULT_OPENVIKING_URL = "http://127.0.0.1:1933"
DEFAULT_AGENT_ID = "default"
DEFAULT_SESSION_SCAN_LIMIT = 0

HEALTHCHECK_PREFIX = (
    "[OPENVIKING-HEALTHCHECK] This is an automated healthcheck. "
    "All data below is synthetic and should be ignored after the check completes. "
    "Probe marker: {probe}. "
)

SEED_MESSAGES = [
    (
        HEALTHCHECK_PREFIX
        + "Please remember the following SYNTHETIC test data: "
        "my name is Lin Zhou, I am rebuilding an order platform, "
        "my backend stack is Go, PostgreSQL, and Redis, and the current project progress is 70 percent. "
        "Reply briefly."
    ),
    (
        "[OPENVIKING-HEALTHCHECK] More synthetic test data for the same probe session. "
        "Our Kafka topic is order_events_v2, "
        "the payment callback service runs on payment-cb.internal:9443, "
        "and the main latency alert is P99 over 450ms for 3 minutes."
    ),
    (
        "[OPENVIKING-HEALTHCHECK] Additional synthetic test data. "
        "The inventory service exhausted its connection pool. "
        "We fixed it by raising max_open_conns from 80 to 160 and by adding a circuit breaker."
    ),
    (
        "[OPENVIKING-HEALTHCHECK] Synthetic preference for this test session only: "
        "keep answers concise, put the conclusion first, then the reason if needed."
    ),
]

FOLLOW_UP_QUESTION = (
    "[OPENVIKING-HEALTHCHECK] Based on the synthetic test data above, "
    "summarize the backend stack and current project progress in one short sentence."
)
FOLLOW_UP_KEYWORDS = ["go", "postgresql", "redis", "70"]

RECALL_QUESTION = (
    "[OPENVIKING-HEALTHCHECK] Based on the synthetic test data from the healthcheck, "
    "reply with the Kafka topic and payment callback service address in one line."
)
RECALL_KEYWORDS = ["order_events_v2", "payment-cb.internal:9443"]

MEMORY_QUERY = "Lin Zhou Go PostgreSQL Redis order_events_v2"


def supports_color() -> bool:
    return sys.stdout.isatty() and os.environ.get("NO_COLOR") is None


def color(text: str, code: str) -> str:
    if not supports_color():
        return text
    return f"\033[{code}m{text}\033[0m"


def green(text: str) -> str:
    return color(text, "32")


def yellow(text: str) -> str:
    return color(text, "33")


def red(text: str) -> str:
    return color(text, "31")


def cyan(text: str) -> str:
    return color(text, "36")


def bold(text: str) -> str:
    return color(text, "1")


class Recorder:
    def __init__(self) -> None:
        self.results: list[dict[str, str]] = []

    def add(self, status: str, label: str, detail: str = "") -> None:
        self.results.append({"status": status, "label": label, "detail": detail})
        prefix = {
            "PASS": green("PASS"),
            "WARN": yellow("WARN"),
            "FAIL": red("FAIL"),
            "INFO": cyan("INFO"),
            "SKIP": cyan("SKIP"),
        }.get(status, status)
        line = f"[{prefix}] {label}"
        if detail:
            line += f" ({detail})"
        print(line)

    def has_failures(self) -> bool:
        return any(item["status"] == "FAIL" for item in self.results)

    def has_warnings(self) -> bool:
        return any(item["status"] == "WARN" for item in self.results)

    def counts(self) -> dict[str, int]:
        summary = {"PASS": 0, "WARN": 0, "FAIL": 0, "INFO": 0, "SKIP": 0}
        for item in self.results:
            summary[item["status"]] = summary.get(item["status"], 0) + 1
        return summary

    def write_json(self, path: Path) -> None:
        path.write_text(json.dumps(self.results, indent=2, ensure_ascii=True), encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="End-to-end healthcheck for the OpenClaw OpenViking plugin.",
    )
    parser.add_argument("--gateway", default="", help=f"Gateway base URL (default: {DEFAULT_GATEWAY_URL})")
    parser.add_argument(
        "--openviking",
        default="",
        help=f"OpenViking base URL (default: {DEFAULT_OPENVIKING_URL})",
    )
    parser.add_argument("--token", default="", help="Gateway bearer token. Auto-discovered when possible.")
    parser.add_argument(
        "--openviking-api-key",
        default="",
        help="OpenViking API key. Auto-discovered from plugin config when possible.",
    )
    parser.add_argument("--agent-id", default="", help=f"OpenViking agent id (default: {DEFAULT_AGENT_ID})")
    parser.add_argument("--user-id", default="", help="User id for the real conversation session.")
    parser.add_argument(
        "--openclaw-config",
        default="",
        help="Path to openclaw.json. Auto-discovered when omitted.",
    )
    parser.add_argument("--delay", type=float, default=1.0, help="Delay between chat turns in seconds.")
    parser.add_argument(
        "--capture-wait",
        type=float,
        default=4.0,
        help="Wait time after chat before reading OpenViking session state.",
    )
    parser.add_argument(
        "--commit-wait",
        type=float,
        default=300.0,
        help="Max seconds to wait for commit, archive, and memory extraction to complete (default: 300).",
    )
    parser.add_argument(
        "--session-scan-limit",
        type=int,
        default=DEFAULT_SESSION_SCAN_LIMIT,
        help="Maximum recent sessions to inspect while locating the probe session (0 = scan all).",
    )
    parser.add_argument(
        "--strict-warnings",
        action="store_true",
        help="Exit with status 1 when warnings exist.",
    )
    parser.add_argument(
        "--chat-timeout",
        type=float,
        default=120.0,
        help="Timeout in seconds for each Gateway chat request (default: 120).",
    )
    parser.add_argument(
        "--insecure",
        action="store_true",
        help="Disable SSL certificate verification (useful for self-signed certs in remote mode).",
    )
    parser.add_argument("--json-out", default="", help="Optional JSON report output path.")
    parser.add_argument("--verbose", "-v", action="store_true", help="Print extra debug information.")
    return parser.parse_args()


def load_json(path: Path) -> dict[str, Any] | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    return data if isinstance(data, dict) else None


def to_positive_int(value: Any) -> int | None:
    if isinstance(value, int) and value > 0:
        return value
    if isinstance(value, str) and value.strip():
        try:
            parsed = int(value.strip())
        except ValueError:
            return None
        return parsed if parsed > 0 else None
    return None


def normalize_local_host(value: Any, fallback: str = "127.0.0.1") -> str:
    if not isinstance(value, str):
        return fallback
    host = value.strip()
    if not host or host in {"0.0.0.0", "::", "[::]"}:
        return fallback
    return host


def discover_openclaw_config(explicit: str) -> Path | None:
    if explicit:
        path = Path(explicit).expanduser()
        return path if path.exists() else None

    candidates: list[Path] = []
    state_dir = os.environ.get("OPENCLAW_STATE_DIR")
    if state_dir:
        candidates.append(Path(state_dir).expanduser() / "openclaw.json")
    candidates.extend(
        [
            Path.cwd() / "config" / ".openclaw" / "openclaw.json",
            Path.home() / ".openclaw" / "openclaw.json",
        ],
    )
    for path in sorted(Path.home().glob(".openclaw*/openclaw.json")):
        if path not in candidates:
            candidates.append(path)
    for path in candidates:
        if path.exists():
            return path
    return None


def extract_plugin_entry(config: dict[str, Any]) -> dict[str, Any]:
    plugins = config.get("plugins")
    if not isinstance(plugins, dict):
        return {}
    entries = plugins.get("entries")
    if not isinstance(entries, dict):
        return {}
    entry = entries.get("openviking")
    return entry if isinstance(entry, dict) else {}


def extract_plugin_config(config: dict[str, Any]) -> dict[str, Any]:
    entry = extract_plugin_entry(config)
    plugin_config = entry.get("config")
    return plugin_config if isinstance(plugin_config, dict) else {}


def extract_openviking_config_path(plugin_config: dict[str, Any]) -> Path | None:
    raw = resolve_env_placeholders(str(plugin_config.get("configPath", "")).strip())
    if not raw:
        return None
    path = Path(raw).expanduser()
    return path if path.exists() else None


def load_openviking_config(plugin_config: dict[str, Any]) -> tuple[Path | None, dict[str, Any] | None]:
    path = extract_openviking_config_path(plugin_config)
    if not path:
        return None, None
    return path, load_json(path)


def extract_context_slot(config: dict[str, Any]) -> str:
    plugins = config.get("plugins")
    if not isinstance(plugins, dict):
        return ""
    slots = plugins.get("slots")
    if not isinstance(slots, dict):
        return ""
    value = slots.get("contextEngine")
    return value.strip() if isinstance(value, str) else ""


def iter_gateway_entries(config: dict[str, Any]) -> list[dict[str, Any]]:
    gateways = config.get("gateways")
    entries: list[dict[str, Any]] = []
    if isinstance(gateways, list):
        entries.extend(item for item in gateways if isinstance(item, dict))
    elif isinstance(gateways, dict):
        for value in gateways.values():
            if isinstance(value, dict):
                entries.append(value)
            elif isinstance(value, list):
                entries.extend(item for item in value if isinstance(item, dict))
    return entries


def guess_gateway_url(config: dict[str, Any]) -> str:
    gateway = config.get("gateway")
    if isinstance(gateway, dict):
        port = to_positive_int(gateway.get("port"))
        if port:
            bind = gateway.get("bind")
            host = "127.0.0.1"
            if isinstance(bind, str) and bind == "custom":
                custom_host = gateway.get("host")
                if isinstance(custom_host, str) and custom_host.strip():
                    host = custom_host.strip()
            return f"http://{host}:{port}"

    for entry in iter_gateway_entries(config):
        if entry.get("type") != "openresponses-http":
            continue
        port = to_positive_int(entry.get("port"))
        if port:
            host = entry.get("host")
            if not isinstance(host, str) or not host.strip():
                host = "127.0.0.1"
            scheme = "https" if entry.get("https") is True or entry.get("tls") is True else "http"
            return f"{scheme}://{host.strip()}:{port}"
    return DEFAULT_GATEWAY_URL


def guess_openviking_url(plugin_config: dict[str, Any], ov_config: dict[str, Any] | None) -> str:
    env_url = os.environ.get("OPENVIKING_BASE_URL") or os.environ.get("OPENVIKING_URL")
    if env_url:
        return env_url.rstrip("/")
    mode = plugin_config.get("mode")
    if mode == "remote":
        base_url = plugin_config.get("baseUrl")
        if isinstance(base_url, str) and base_url.strip():
            return base_url.rstrip("/")
    if isinstance(ov_config, dict):
        server = ov_config.get("server")
        if isinstance(server, dict):
            host = normalize_local_host(server.get("host"))
            port = to_positive_int(server.get("port"))
            if port:
                return f"http://{host}:{port}"
    port = to_positive_int(plugin_config.get("port"))
    if port:
        return f"http://127.0.0.1:{port}"
    if isinstance(plugin_config.get("baseUrl"), str) and plugin_config["baseUrl"].strip():
        return plugin_config["baseUrl"].rstrip("/")
    return DEFAULT_OPENVIKING_URL


def resolve_env_placeholders(value: str) -> str:
    """Replace ``${VAR}`` placeholders with environment values.

    If any referenced variable is undefined the function returns the
    partially-resolved string as-is.  This is intentional: we prefer
    leaving the raw placeholder visible (so the caller or the user can
    spot it) over silently producing a broken path.
    """
    if "${" not in value:
        return value
    resolved = value
    while True:
        start = resolved.find("${")
        if start < 0:
            return resolved
        end = resolved.find("}", start + 2)
        if end < 0:
            return resolved
        name = resolved[start + 2:end]
        env_value = os.environ.get(name)
        if env_value is None:
            return resolved
        resolved = resolved[:start] + env_value + resolved[end + 1:]


def openviking_log_path(ov_config: dict[str, Any] | None) -> Path:
    if isinstance(ov_config, dict):
        storage = ov_config.get("storage")
        if isinstance(storage, dict):
            workspace = storage.get("workspace")
            if isinstance(workspace, str) and workspace.strip():
                return Path(workspace).expanduser() / "log" / "openviking.log"
    return Path.home() / ".openviking" / "data" / "log" / "openviking.log"


def discover_gateway_token(config: dict[str, Any] | None) -> str:
    if config:
        gateway = config.get("gateway")
        if isinstance(gateway, dict):
            auth = gateway.get("auth")
            if isinstance(auth, dict):
                token = auth.get("token")
                if isinstance(token, str) and token.strip():
                    return token.strip()
    env_token = os.environ.get("OPENCLAW_GATEWAY_TOKEN")
    return env_token.strip() if env_token else ""


def discover_gateway_auth_mode(config: dict[str, Any] | None) -> str:
    if not config:
        return ""
    gateway = config.get("gateway")
    if not isinstance(gateway, dict):
        return ""
    auth = gateway.get("auth")
    if not isinstance(auth, dict):
        return ""
    mode = auth.get("mode")
    return mode.strip() if isinstance(mode, str) else ""


def http_json(
    base_url: str,
    path: str,
    method: str = "GET",
    body: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
    timeout: float = 30.0,
    insecure: bool = False,
) -> Any:
    url = f"{base_url.rstrip('/')}{path}"
    request_headers = {"Accept": "application/json"}
    if headers:
        request_headers.update(headers)
    data: bytes | None = None
    if body is not None:
        request_headers["Content-Type"] = "application/json"
        data = json.dumps(body).encode("utf-8")
    request = urllib.request.Request(url, data=data, method=method, headers=request_headers)
    ssl_ctx: ssl.SSLContext | None = None
    if insecure:
        ssl_ctx = ssl.create_default_context()
        ssl_ctx.check_hostname = False
        ssl_ctx.verify_mode = ssl.CERT_NONE
    try:
        with urllib.request.urlopen(request, timeout=timeout, context=ssl_ctx) as response:
            raw = response.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"{method} {url} failed with HTTP {exc.code}: {raw[:400]}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"{method} {url} failed: {exc.reason}") from exc

    if not raw.strip():
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"{method} {url} returned non-JSON response: {raw[:400]}") from exc


def extract_result(payload: Any) -> Any:
    if isinstance(payload, dict) and "result" in payload:
        return payload["result"]
    return payload


def extract_reply_text(payload: dict[str, Any]) -> str:
    for item in payload.get("output", []):
        if not isinstance(item, dict):
            continue
        if item.get("type") == "message" and item.get("role") == "assistant":
            for part in item.get("content", []):
                if isinstance(part, dict) and part.get("type") in ("text", "output_text"):
                    text = part.get("text")
                    if isinstance(text, str):
                        return text
    return ""


def count_keyword_hits(text: str, keywords: list[str]) -> tuple[int, list[str]]:
    haystack = text.lower()
    hits = [item for item in keywords if item.lower() in haystack]
    return len(hits), hits


def flatten_context_text(context: dict[str, Any] | None) -> str:
    if not isinstance(context, dict):
        return ""
    chunks: list[str] = []
    overview = context.get("latest_archive_overview")
    if isinstance(overview, str):
        chunks.append(overview)
    for item in context.get("pre_archive_abstracts", []):
        if isinstance(item, dict):
            abstract = item.get("abstract")
            if isinstance(abstract, str):
                chunks.append(abstract)
    for message in context.get("messages", []):
        if not isinstance(message, dict):
            continue
        for part in message.get("parts", []):
            if not isinstance(part, dict):
                continue
            text = part.get("text")
            if isinstance(text, str):
                chunks.append(text)
            abstract = part.get("abstract")
            if isinstance(abstract, str):
                chunks.append(abstract)
    return "\n".join(chunks)


def poll_task(
    openviking_url: str,
    task_id: str,
    headers: dict[str, str],
    timeout_seconds: float = 120.0,
    insecure: bool = False,
) -> dict[str, Any] | None:
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        payload = http_json(
            openviking_url,
            f"/api/v1/tasks/{urllib.parse.quote(task_id, safe='')}",
            headers=headers,
            timeout=15.0,
            insecure=insecure,
        )
        result = extract_result(payload)
        if isinstance(result, dict):
            status = result.get("status")
            if status in {"completed", "failed"}:
                return result
        time.sleep(0.5)
    return None


def extract_memory_total(payload: dict[str, Any] | None) -> int:
    if not isinstance(payload, dict):
        return 0
    memories = payload.get("memories_extracted")
    if not isinstance(memories, dict):
        return 0
    if isinstance(memories.get("total"), int):
        return memories["total"]
    return sum(
        value for key, value in memories.items()
        if key != "total" and isinstance(value, int)
    )


def wait_for_commit_visibility(
    inspector: "OpenVikingInspector",
    session_id: str,
    timeout_seconds: float = 60.0,
    verbose: bool = False,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    """Poll until commit_count > 0, archive overview exists, AND memories are extracted."""
    deadline = time.time() + timeout_seconds
    latest_detail: dict[str, Any] | None = None
    latest_context: dict[str, Any] | None = None
    last_status = ""
    while time.time() < deadline:
        detail = inspector.get_session(session_id)
        context = inspector.get_context(session_id)
        latest_detail = detail
        latest_context = context

        commit_ok = isinstance(detail, dict) and isinstance(detail.get("commit_count"), int) and detail["commit_count"] > 0
        overview_ok = isinstance(context, dict) and isinstance(context.get("latest_archive_overview"), str) and bool(context["latest_archive_overview"].strip())
        memory_ok = extract_memory_total(detail) > 0

        status = f"commit={'yes' if commit_ok else 'no'} overview={'yes' if overview_ok else 'no'} memory={'yes' if memory_ok else 'no'}"
        if verbose and status != last_status:
            remaining = max(0, deadline - time.time())
            print(f"  waiting: {status} ({remaining:.0f}s remaining)")
            last_status = status

        if commit_ok and overview_ok and memory_ok:
            return detail, context
        time.sleep(5.0)
    return latest_detail, latest_context


class OpenVikingInspector:
    def __init__(self, base_url: str, api_key: str, agent_id: str, *, insecure: bool = False) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.agent_id = agent_id or DEFAULT_AGENT_ID
        self.insecure = insecure

    def headers(self) -> dict[str, str]:
        headers: dict[str, str] = {}
        if self.api_key:
            headers["X-API-Key"] = self.api_key
        if self.agent_id:
            headers["X-OpenViking-Agent"] = self.agent_id
        return headers

    def health(self) -> bool:
        try:
            payload = http_json(self.base_url, "/health", headers=self.headers(), timeout=5.0, insecure=self.insecure)
        except Exception:
            return False
        result = extract_result(payload)
        return isinstance(result, dict) and result.get("status") == "ok"

    def list_sessions(self) -> list[dict[str, Any]]:
        payload = http_json(self.base_url, "/api/v1/sessions", headers=self.headers(), timeout=15.0, insecure=self.insecure)
        result = extract_result(payload)
        if isinstance(result, list):
            return [item for item in result if isinstance(item, dict)]
        return []

    def get_session(self, session_id: str) -> dict[str, Any] | None:
        payload = http_json(
            self.base_url,
            f"/api/v1/sessions/{urllib.parse.quote(session_id, safe='')}",
            headers=self.headers(),
            timeout=15.0,
            insecure=self.insecure,
        )
        result = extract_result(payload)
        return result if isinstance(result, dict) else None

    def get_context(self, session_id: str, token_budget: int = 128000) -> dict[str, Any] | None:
        payload = http_json(
            self.base_url,
            f"/api/v1/sessions/{urllib.parse.quote(session_id, safe='')}/context?token_budget={token_budget}",
            headers=self.headers(),
            timeout=20.0,
            insecure=self.insecure,
        )
        result = extract_result(payload)
        return result if isinstance(result, dict) else None

    def commit(self, session_id: str) -> dict[str, Any] | None:
        payload = http_json(
            self.base_url,
            f"/api/v1/sessions/{urllib.parse.quote(session_id, safe='')}/commit",
            method="POST",
            body={},
            headers=self.headers(),
            timeout=30.0,
            insecure=self.insecure,
        )
        result = extract_result(payload)
        if not isinstance(result, dict):
            return None
        task_id = result.get("task_id")
        if isinstance(task_id, str) and task_id:
            task = poll_task(self.base_url, task_id, self.headers(), insecure=self.insecure)
            if isinstance(task, dict):
                result["status"] = task.get("status")
                task_result = task.get("result")
                if isinstance(task_result, dict) and "memories_extracted" in task_result:
                    result["memories_extracted"] = task_result.get("memories_extracted")
                if task.get("status") == "failed":
                    result["error"] = task.get("error")
        return result

    def search_memories(self, query: str, limit: int = 5) -> list[dict[str, Any]]:
        payload = http_json(
            self.base_url,
            "/api/v1/search/find",
            method="POST",
            body={"query": query, "target_uri": "viking://user/memories", "limit": limit},
            headers=self.headers(),
            timeout=20.0,
            insecure=self.insecure,
        )
        result = extract_result(payload)
        if isinstance(result, dict):
            memories = result.get("memories")
            if isinstance(memories, list):
                return [item for item in memories if isinstance(item, dict)]
        return []


def send_gateway_message(
    gateway_url: str,
    token: str,
    user_id: str,
    message: str,
    timeout: float = 120.0,
    insecure: bool = False,
) -> dict[str, Any]:
    headers: dict[str, str] = {}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    payload = http_json(
        gateway_url,
        "/v1/responses",
        method="POST",
        body={"model": "openclaw", "input": message, "user": user_id},
        headers=headers,
        timeout=timeout,
        insecure=insecure,
    )
    return payload if isinstance(payload, dict) else {}


def gateway_health(gateway_url: str, token: str, insecure: bool = False) -> bool:
    headers: dict[str, str] = {}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    try:
        payload = http_json(gateway_url, "/health", headers=headers, timeout=5.0, insecure=insecure)
    except Exception:
        return False
    result = extract_result(payload)
    if not isinstance(result, dict):
        return False
    if result.get("ok") is True:
        return True
    status = result.get("status")
    return status in {"ok", "live", "healthy"}


def find_session_with_probe(
    inspector: OpenVikingInspector,
    probe: str,
    session_scan_limit: int,
    verbose: bool,
) -> tuple[str | None, dict[str, Any] | None, dict[str, Any] | None]:
    sessions = inspector.list_sessions()
    candidates = [
        item for item in sessions
        if not str(item.get("session_id", "")).startswith("memory-store-")
    ]
    candidates.sort(
        key=lambda item: str(item.get("updated_at", "") or item.get("created_at", "")),
        reverse=True,
    )
    scan = candidates if session_scan_limit <= 0 else candidates[: session_scan_limit]
    for item in scan:
        session_id = str(item.get("session_id", "")).strip()
        if not session_id:
            continue
        context = inspector.get_context(session_id)
        text = flatten_context_text(context)
        if probe in text:
            detail = inspector.get_session(session_id)
            return session_id, detail, context
        if verbose:
            print(f"  scanned session {session_id} without probe marker")
    return None, None, None


def maybe_detail(text: str, enabled: bool) -> str:
    return text if enabled else ""


def main() -> int:
    args = parse_args()
    recorder = Recorder()

    config_path = discover_openclaw_config(args.openclaw_config)
    config = load_json(config_path) if config_path else None
    plugin_config = extract_plugin_config(config or {})
    plugin_entry = extract_plugin_entry(config or {})
    ov_config_path, ov_config = load_openviking_config(plugin_config)

    gateway_url = (args.gateway or os.environ.get("OPENCLAW_GATEWAY_URL") or "").rstrip("/")
    if not gateway_url:
        gateway_url = guess_gateway_url(config or {})

    openviking_url = (args.openviking or "").rstrip("/")
    if not openviking_url:
        openviking_url = guess_openviking_url(plugin_config, ov_config)

    token = (args.token or discover_gateway_token(config)).strip()
    gateway_auth_mode = discover_gateway_auth_mode(config)
    openviking_api_key = (
        args.openviking_api_key
        or resolve_env_placeholders(str(plugin_config.get("apiKey", "")).strip())
        or (
            str(ov_config.get("server", {}).get("root_api_key", "")).strip()
            if isinstance(ov_config, dict)
            else ""
        )
        or str(os.environ.get("OPENVIKING_API_KEY", "")).strip()
    )
    agent_id = (args.agent_id or str(plugin_config.get("agentId", "")).strip() or DEFAULT_AGENT_ID)
    user_id = args.user_id or f"ov-healthcheck-{uuid.uuid4().hex[:8]}"
    probe = f"probe-{uuid.uuid4().hex[:8]}"
    ov_log_path = openviking_log_path(ov_config)

    print(bold("OpenViking Plugin Healthcheck"))
    print(f"Gateway: {gateway_url}")
    print(f"OpenViking: {openviking_url}")
    print(f"User ID: {user_id}")
    print(f"Probe: {probe}")
    if config_path:
        print(f"Config: {config_path}")
    if ov_config_path:
        print(f"OV Config: {ov_config_path}")
    print()

    if config_path and config:
        recorder.add("PASS", "OpenClaw config discovered", str(config_path))
    else:
        recorder.add("WARN", "OpenClaw config not discovered", "falling back to defaults and explicit arguments")

    if config:
        slot = extract_context_slot(config)
        if slot == "openviking":
            recorder.add("PASS", "plugins.slots.contextEngine is openviking")
        elif slot:
            recorder.add("FAIL", "plugins.slots.contextEngine is not openviking", slot)
        else:
            recorder.add("WARN", "plugins.slots.contextEngine is missing")

        enabled = plugin_entry.get("enabled")
        if enabled is False:
            recorder.add("FAIL", "plugins.entries.openviking.enabled is false")
        elif plugin_entry:
            recorder.add("PASS", "plugins.entries.openviking entry exists")
        else:
            recorder.add("WARN", "plugins.entries.openviking entry missing")

        if plugin_config:
            mode = str(plugin_config.get("mode", "local"))
            recorder.add("INFO", "Plugin mode", mode)
            if ov_config_path:
                recorder.add("INFO", "OpenViking config discovered", str(ov_config_path))
            if plugin_config.get("autoCapture") is False:
                recorder.add("WARN", "autoCapture is disabled", "afterTurn capture checks are likely to fail")
            if plugin_config.get("autoRecall") is False:
                recorder.add("WARN", "autoRecall is disabled", "fresh-session recall check will be skipped")
        else:
            recorder.add("WARN", "Plugin config missing", "using detected defaults")

    if token:
        recorder.add("PASS", "Gateway token available")
    elif gateway_auth_mode == "none":
        recorder.add("INFO", "Gateway token not required", "gateway.auth.mode=none")
    else:
        recorder.add("WARN", "Gateway token not found", "continuing without Authorization header")

    inspector = OpenVikingInspector(openviking_url, openviking_api_key, agent_id, insecure=args.insecure)

    if gateway_health(gateway_url, token, insecure=args.insecure):
        recorder.add("PASS", "Gateway health check succeeded")
    else:
        recorder.add("FAIL", "Gateway health check failed", gateway_url)

    if inspector.health():
        recorder.add("PASS", "OpenViking health check succeeded")
    else:
        recorder.add("FAIL", "OpenViking health check failed", openviking_url)

    if recorder.has_failures():
        print()
        print(red("Stopping because service health or config checks already failed."))
        return 1

    print()
    print(bold("Phase 1: real conversation"))
    for index, template in enumerate(SEED_MESSAGES, start=1):
        message = template.format(probe=probe)
        try:
            payload = send_gateway_message(gateway_url, token, user_id, message, timeout=args.chat_timeout, insecure=args.insecure)
            reply = extract_reply_text(payload)
            if reply:
                recorder.add("PASS", f"Chat turn {index} succeeded", f"reply_len={len(reply)}")
                if args.verbose:
                    print(f"  reply preview: {reply[:180]}")
            else:
                recorder.add("WARN", f"Chat turn {index} returned no assistant text")
                if args.verbose:
                    print(json.dumps(payload, indent=2, ensure_ascii=True)[:1200])
        except Exception as exc:
            recorder.add("FAIL", f"Chat turn {index} failed", str(exc))
            break
        if index < len(SEED_MESSAGES):
            time.sleep(max(0.0, args.delay))

    if recorder.has_failures():
        print()
        print(red("Stopping because the real conversation flow did not complete."))
        return 1

    if args.capture_wait > 0:
        print()
        print(f"Waiting {args.capture_wait:.1f}s for afterTurn capture...")
        time.sleep(args.capture_wait)

    print()
    print(bold("Phase 2: OpenViking session inspection"))
    try:
        session_id, _session_detail, session_context = find_session_with_probe(
            inspector,
            probe=probe,
            session_scan_limit=args.session_scan_limit,
            verbose=args.verbose,
        )
    except Exception as exc:
        recorder.add("FAIL", "Failed to inspect OpenViking sessions", str(exc))
        session_id = None
        session_context = None

    if session_id:
        recorder.add("PASS", "Probe session located in OpenViking", session_id)
    else:
        recorder.add("FAIL", "Probe session not found in OpenViking", "afterTurn capture may be broken")

    if session_context:
        flattened = flatten_context_text(session_context)
        if probe in flattened:
            recorder.add("PASS", "Captured session context contains the probe marker")
        else:
            recorder.add("FAIL", "Captured session context is missing the probe marker")

        _, hits = count_keyword_hits(flattened, ["go", "postgresql", "redis", "70"])
        if len(hits) >= 2:
            recorder.add("PASS", "Captured session context contains seeded facts", ",".join(hits))
        else:
            recorder.add("WARN", "Captured session context contains too few seeded facts", ",".join(hits))
    else:
        recorder.add("FAIL", "Failed to read OpenViking session context")

    if not session_id:
        print()
        print(red("Stopping because no matching OpenViking session was found."))
        return 1

    print()
    print(bold("Phase 3: commit, context, and memory checks"))
    memory_extraction_confirmed = False
    try:
        commit_result = inspector.commit(session_id)
    except Exception as exc:
        commit_result = None
        recorder.add("FAIL", "OpenViking commit request failed", str(exc))

    if isinstance(commit_result, dict):
        status = str(commit_result.get("status", ""))
        if status == "failed":
            recorder.add("FAIL", "OpenViking commit finished with failure", str(commit_result.get("error", "")))
        elif status:
            recorder.add("PASS", "OpenViking commit accepted", status)
        else:
            recorder.add("WARN", "OpenViking commit returned no explicit status")
    elif session_id:
        recorder.add("FAIL", "OpenViking commit returned no usable payload")

    print(f"Waiting up to {args.commit_wait:.0f}s for commit, archive, and memory extraction...")
    try:
        latest_session_detail, latest_context = wait_for_commit_visibility(
            inspector, session_id, timeout_seconds=args.commit_wait, verbose=args.verbose,
        )
    except Exception as exc:
        latest_session_detail = None
        latest_context = None
        recorder.add("FAIL", "Failed to reload session state after commit", str(exc))

    if latest_session_detail:
        commit_count = latest_session_detail.get("commit_count")
        if isinstance(commit_count, int) and commit_count > 0:
            recorder.add("PASS", "Session commit_count is greater than zero", str(commit_count))
        else:
            recorder.add("FAIL", "Session commit_count is still zero after waiting", str(commit_count))

        total_memories = extract_memory_total(latest_session_detail)
        if total_memories > 0:
            recorder.add("PASS", "Memory extraction produced results", f"total={total_memories}")
            memory_extraction_confirmed = True
        else:
            recorder.add("FAIL", "Memory extraction produced no results after waiting")

    if latest_context:
        overview = latest_context.get("latest_archive_overview")
        if isinstance(overview, str) and overview.strip():
            recorder.add("PASS", "Context endpoint returned latest_archive_overview")
        else:
            recorder.add("FAIL", "Context endpoint has no archive overview after waiting")
    else:
        recorder.add("FAIL", "Context endpoint could not be read after commit")

    try:
        memory_hits = inspector.search_memories(MEMORY_QUERY, limit=5)
    except Exception as exc:
        memory_hits = []
        recorder.add("INFO", "Direct backend memory search request failed", str(exc))

    if memory_hits:
        best_preview = json.dumps(memory_hits[0], ensure_ascii=True)[:200]
        recorder.add("PASS", "Memory search returned results", best_preview)
    else:
        recorder.add("INFO", "Direct backend memory search returned no results", MEMORY_QUERY)

    print()
    print(bold("Phase 4: follow-up through Gateway"))
    try:
        follow_up_payload = send_gateway_message(gateway_url, token, user_id, FOLLOW_UP_QUESTION, timeout=args.chat_timeout, insecure=args.insecure)
        follow_up_reply = extract_reply_text(follow_up_payload)
        if follow_up_reply:
            hit_count, hits = count_keyword_hits(follow_up_reply, FOLLOW_UP_KEYWORDS)
            if hit_count >= 2:
                recorder.add("PASS", "Same-session follow-up recalled earlier facts", ",".join(hits))
            else:
                recorder.add("WARN", "Same-session follow-up did not recall enough facts", maybe_detail(follow_up_reply[:220], args.verbose))
        else:
            recorder.add("WARN", "Same-session follow-up returned no assistant text")
    except Exception as exc:
        recorder.add("FAIL", "Same-session follow-up failed", str(exc))

    auto_recall_enabled = plugin_config.get("autoRecall") is not False
    if auto_recall_enabled:
        fresh_user_id = f"{user_id}-fresh-{uuid.uuid4().hex[:4]}"
        try:
            recall_payload = send_gateway_message(gateway_url, token, fresh_user_id, RECALL_QUESTION, timeout=args.chat_timeout, insecure=args.insecure)
            recall_reply = extract_reply_text(recall_payload)
            if recall_reply:
                hit_count, hits = count_keyword_hits(recall_reply, RECALL_KEYWORDS)
                if hit_count >= 2:
                    recorder.add("PASS", "Fresh-session recall returned seeded stack facts", ",".join(hits))
                else:
                    recorder.add("WARN", "Fresh-session recall was inconclusive", maybe_detail(recall_reply[:220], args.verbose))
            else:
                recorder.add("WARN", "Fresh-session recall returned no assistant text")
        except Exception as exc:
            recorder.add("WARN", "Fresh-session recall request failed", str(exc))
    else:
        recorder.add("SKIP", "Fresh-session recall skipped", "autoRecall is disabled in plugin config")

    print()
    print(bold("Summary"))
    counts = recorder.counts()
    print(
        f"PASS={counts['PASS']} "
        f"WARN={counts['WARN']} "
        f"FAIL={counts['FAIL']} "
        f"SKIP={counts['SKIP']}"
    )

    if args.json_out:
        output_path = Path(args.json_out).expanduser()
        recorder.write_json(output_path)
        print(f"JSON report: {output_path}")

    print()
    if recorder.has_failures():
        print(red("Healthcheck failed."))
        print("Suggested next steps:")
        print("  1. Confirm `openclaw config get plugins.slots.contextEngine` returns `openviking`.")
        print("  2. Confirm both `/health` endpoints are reachable.")
        print("  3. Inspect `openclaw logs --follow` for `openviking:` lines.")
        print(f"  4. Inspect `{ov_log_path}` for backend errors.")
        return 1

    if args.strict_warnings and recorder.has_warnings():
        print(yellow("Healthcheck completed with warnings."))
        return 1

    if recorder.has_warnings():
        print(yellow("Healthcheck passed, but some checks were inconclusive."))
    else:
        print(green("Healthcheck passed."))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
