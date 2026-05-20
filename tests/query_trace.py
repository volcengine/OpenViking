#!/usr/bin/env python3
"""Query Jaeger trace by trace ID and pretty-print for debugging.

Usage:
    python tests/query_trace.py <trace_id> [--detail span_id] [--errors-only] [--raw] [--no-color]

Designed to be LLM-friendly: concise, structured output that won't blow up context windows.
"""

import argparse
import base64
import json
import os
import sys
from pathlib import Path
from typing import Optional


def _load_jaeger_config() -> dict:
    """Load Jaeger config from .env file.

    Required .env variables:
      TLS_OTEL_JAEGER_BASE_URL  - e.g. https://tls-cn-beijing.volces.com:16686
      TLS_OTEL_JAEGER_AUTH_USER - Basic auth username
      TLS_OTEL_AK               - Access key
      TLS_OTEL_SK               - Secret key
    """
    from dotenv import load_dotenv

    # Load .env from project root or ~/.env
    for p in [
        Path(__file__).resolve().parent.parent / ".env",
        Path.home() / ".env",
    ]:
        if p.exists():
            load_dotenv(p)
            break

    jaeger_base_url = os.environ.get("TLS_OTEL_JAEGER_BASE_URL", "")
    auth_user = os.environ.get("TLS_OTEL_JAEGER_AUTH_USER", "")
    ak = os.environ.get("TLS_OTEL_AK", "")
    sk = os.environ.get("TLS_OTEL_SK", "")

    missing = [
        k
        for k, v in [
            ("TLS_OTEL_JAEGER_BASE_URL", jaeger_base_url),
            ("TLS_OTEL_JAEGER_AUTH_USER", auth_user),
            ("TLS_OTEL_AK", ak),
            ("TLS_OTEL_SK", sk),
        ]
        if not v
    ]
    if missing:
        raise SystemExit(
            f"Missing .env variables: {', '.join(missing)}. Add them to .env or ~/.openviking/.env"
        )

    return {
        "jaeger_base_url": jaeger_base_url,
        "auth_user": auth_user,
        "ak": ak,
        "sk": sk,
    }


NOISY_TAGS = {
    "internal.span.format",
    "sampler.param",
    "sampler.type",
    "sampler.decision",
    "otel.library.version",
    "otel.resource.service.name",
    "jaeger.version",
    "telemetry.sdk.version",
    "telemetry.sdk.name",
    "telemetry.sdk.language",
    "transport",
}


def fetch_trace(trace_id: str) -> Optional[dict]:
    cfg = _load_jaeger_config()
    jaeger_base_url = cfg["jaeger_base_url"]
    auth_user = cfg["auth_user"]
    ak = cfg["ak"]
    sk = cfg["sk"]

    auth_pass = f"{ak}#{sk}"
    auth_header = base64.b64encode(f"{auth_user}:{auth_pass}".encode()).decode()

    # Normalize trace ID to 32 hex chars
    if len(trace_id) == 16:
        trace_id = "0" * 16 + trace_id
    elif len(trace_id) != 32:
        print(
            f"Invalid trace ID length: {len(trace_id)}. Expected 16 or 32 hex chars.",
            file=sys.stderr,
        )
        return None

    url = f"{jaeger_base_url}/api/traces/{trace_id}"

    try:
        import requests

        resp = requests.get(url, headers={"Authorization": f"Basic {auth_header}"}, timeout=15)
        if resp.status_code == 401:
            print(
                "Authentication failed (401). Check telemetry.tracer ak/sk in ov.conf.",
                file=sys.stderr,
            )
            return None
        if resp.status_code == 404:
            print(
                f"Trace {trace_id} not found or expired (Jaeger retains traces ~7 days).",
                file=sys.stderr,
            )
            return None
        resp.raise_for_status()
        return resp.json()
    except requests.exceptions.ConnectionError:
        print(f"Cannot connect to Jaeger at {jaeger_base_url}. Check network/VPN.", file=sys.stderr)
        return None
    except Exception as e:
        print(f"Failed to fetch trace: {e}", file=sys.stderr)
        return None


def build_span_tree(spans: list) -> tuple:
    span_map = {s["spanID"]: s for s in spans}
    children_map: dict[str, list] = {}
    roots = []

    for span in spans:
        parent_id = None
        for ref in span.get("references", []):
            if ref.get("refType") == "CHILD_OF" and ref.get("spanID") in span_map:
                parent_id = ref["spanID"]
                break
        if parent_id:
            children_map.setdefault(parent_id, []).append(span)
        else:
            roots.append(span)

    # Sort by startTime
    roots.sort(key=lambda s: s.get("startTime", 0))
    for children in children_map.values():
        children.sort(key=lambda s: s.get("startTime", 0))

    return roots, children_map, span_map


def is_error_span(span: dict) -> bool:
    for tag in span.get("tags", []):
        key = tag.get("key", "")
        value = tag.get("value")
        if key == "error" and value:
            return True
        if key == "otel.status_code" and str(value) == "ERROR":
            return True
    for log in span.get("logs", []):
        for field in log.get("fields", []):
            if field.get("key") == "event" and field.get("value") == "error":
                return True
    return False


def format_duration(microseconds: int) -> str:
    if microseconds < 1000:
        return f"{microseconds}us"
    if microseconds < 1_000_000:
        ms = microseconds / 1000
        return f"{ms:.0f}ms" if ms == int(ms) else f"{ms:.1f}ms"
    s = microseconds / 1_000_000
    return f"{s:.2f}s"


def format_offset(microseconds: int) -> str:
    if microseconds < 1000:
        return f"+{microseconds}us"
    if microseconds < 1_000_000:
        ms = microseconds / 1000
        return f"+{ms:.0f}ms" if ms == int(ms) else f"+{ms:.1f}ms"
    s = microseconds / 1_000_000
    return f"+{s:.2f}s"


def format_timestamp(microseconds: int) -> str:
    from datetime import datetime, timezone

    dt = datetime.fromtimestamp(microseconds / 1_000_000, tz=timezone.utc)
    return dt.strftime("%Y-%m-%d %H:%M:%S") + f".{microseconds % 1_000_000 // 1000:03d}"


def extract_tags(span: dict) -> dict:
    tags = {}
    for tag in span.get("tags", []):
        key = tag.get("key", "")
        if key in NOISY_TAGS:
            continue
        value = tag.get("value")
        tag_type = tag.get("type", "string")
        if tag_type == "bool":
            value = str(value).lower()
        elif tag_type in ("int64", "float64"):
            value = str(value)
        else:
            value = str(value)
            # Allow longer values for error-related fields
            max_len = (
                2000
                if key.startswith("error.") or key in ("exception.stacktrace", "exception.message")
                else 200
            )
            if len(value) > max_len:
                value = value[:max_len] + "..."
        tags[key] = value
    return tags


def extract_error_info(span: dict) -> list[str]:
    lines = []
    tags = {t["key"]: t.get("value") for t in span.get("tags", [])}
    if "error.message" in tags:
        lines.append(f"error.message: {tags['error.message']}")
    if "error.type" in tags:
        lines.append(f"error.type: {tags['error.type']}")
    if "error.stack_trace" in tags:
        stack = str(tags["error.stack_trace"])
        stack_lines = stack.strip().split("\n")[:5]
        for sl in stack_lines:
            lines.append(f"  {sl.strip()}")
    return lines


def format_span_line(span: dict, depth: int, trace_start_us: int, use_color: bool) -> str:
    indent = "  " * depth
    start = span.get("startTime", 0)
    offset = start - trace_start_us
    duration = span.get("duration", 0)
    name = span.get("operationName", "?")
    status = "ERROR" if is_error_span(span) else "OK"

    offset_str = format_offset(offset)
    dur_str = format_duration(duration)

    if use_color and status == "ERROR":
        line = f"{indent}{offset_str}  \033[31m{name}  {dur_str}  {status}\033[0m"
    else:
        line = f"{indent}{offset_str}  {name}  {dur_str}  {status}"
    return line


def print_tree(roots, children_map, span_map, trace_start_us, mode, detail_span_ids, use_color):
    detail_span_ids = detail_span_ids or []

    def should_show(span, depth) -> bool:
        if mode == "all":
            return True
        if mode == "errors":
            # Show error spans and their ancestor chain
            if is_error_span(span):
                return True
            # Check if any descendant is an error
            return has_error_descendant(span["spanID"], children_map)

        return True

    def has_error_descendant(span_id, children_map):
        for child in children_map.get(span_id, []):
            if is_error_span(child):
                return True
            if has_error_descendant(child["spanID"], children_map):
                return True
        return False

    def walk(span, depth):
        show = should_show(span, depth)
        if not show:
            return

        print(format_span_line(span, depth, trace_start_us, use_color))

        # Show error details inline
        if is_error_span(span):
            error_lines = extract_error_info(span)
            indent = "  " * (depth + 1)
            for el in error_lines:
                if use_color:
                    print(f"{indent}\033[31m{el}\033[0m")
                else:
                    print(f"{indent}{el}")

        for child in children_map.get(span["spanID"], []):
            walk(child, depth + 1)

    for root in roots:
        walk(root, 0)

    # Print detail sections for requested spans
    if detail_span_ids:
        for sid in detail_span_ids:
            span = span_map.get(sid)
            if not span:
                print(f"\nSpan {sid} not found in this trace.", file=sys.stderr)
                continue
            print(f"\n--- Detail: span {sid} ({span.get('operationName', '?')}) ---")
            tags = extract_tags(span)
            if tags:
                print("Tags:")
                for k, v in tags.items():
                    print(f"  {k}: {v}")
            logs = span.get("logs", [])
            if logs:
                print("Logs:")
                for log in logs:
                    ts = log.get("timestamp", 0)
                    ts_str = format_timestamp(ts)
                    fields = {f["key"]: f.get("value") for f in log.get("fields", [])}
                    event = fields.pop("event", "")
                    msg = fields.pop("message", "")
                    parts = [f"[{ts_str}]"]
                    if event:
                        parts.append(f"event={event}")
                    if msg:
                        parts.append(msg)
                    for k, v in fields.items():
                        v_str = str(v)
                        max_len = (
                            2000 if k in ("exception.stacktrace", "exception.message") else 200
                        )
                        if len(v_str) > max_len:
                            v_str = v_str[:max_len] + "..."
                        parts.append(f"{k}={v_str}")
                    print("  " + ", ".join(parts))


def main():
    parser = argparse.ArgumentParser(description="Query Jaeger trace by trace ID")
    parser.add_argument("trace_id", help="Trace ID (16 or 32 hex chars)")
    parser.add_argument(
        "--detail", action="append", default=[], help="Span ID to show full detail (can repeat)"
    )
    parser.add_argument(
        "--errors-only", action="store_true", help="Only show error spans and their parent chain"
    )
    parser.add_argument("--raw", action="store_true", help="Output raw JSON")
    parser.add_argument("--no-color", action="store_true", help="Disable ANSI colors")
    args = parser.parse_args()

    use_color = not args.no_color and sys.stdout.isatty()

    data = fetch_trace(args.trace_id)
    if not data:
        sys.exit(1)

    if args.raw:
        print(json.dumps(data, indent=2, ensure_ascii=False))
        return

    traces = data.get("data", [])
    if not traces:
        print(f"Trace {args.trace_id} not found or expired.", file=sys.stderr)
        sys.exit(1)

    for trace in traces:
        spans = trace.get("spans", [])
        processes = trace.get("processes", {})
        trace_id = trace.get("traceID", args.trace_id)

        if not spans:
            print(f"Trace {trace_id} has no spans.", file=sys.stderr)
            continue

        roots, children_map, span_map = build_span_tree(spans)
        trace_start = min(s.get("startTime", 0) for s in spans)
        trace_end = max(s.get("startTime", 0) + s.get("duration", 0) for s in spans)
        trace_duration = trace_end - trace_start
        error_count = sum(1 for s in spans if is_error_span(s))

        # Count spans per service
        service_counts: dict[str, int] = {}
        for s in spans:
            pid = s.get("processID", "")
            svc = processes.get(pid, {}).get("serviceName", "unknown")
            service_counts[svc] = service_counts.get(svc, 0) + 1

        # Header
        svc_str = ", ".join(f"{k}({v})" for k, v in sorted(service_counts.items()))
        print(
            f"Trace: {trace_id[:16]}... | Time: {format_timestamp(trace_start)} | Duration: {format_duration(trace_duration)} | Spans: {len(spans)} | Errors: {error_count}"
        )
        if svc_str:
            print(f"Services: {svc_str}")
        print()

        mode = "errors" if args.errors_only else "all"
        print_tree(roots, children_map, span_map, trace_start, mode, args.detail, use_color)


if __name__ == "__main__":
    main()
