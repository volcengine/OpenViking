#!/usr/bin/env python3
"""Upload offline local trace JSONL into the current environment's OTel backend.

The JSONL file is produced by:

    server.observability.traces.enabled = true
    server.observability.traces.protocol = "local"

Run this script in a support/debug environment whose local ov.conf configures a
remote OTel trace exporter (protocol "grpc" or "http"). The script reads the
remote endpoint, headers, TLS settings, and service_name from that ov.conf.

Usage:
    python tests/upload_offline_trace.py --file traces.jsonl
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Iterable


REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def _load_trace_config(config_path: str | None):
    from openviking.server.config import load_server_config

    cfg = load_server_config(config_path).observability.traces
    protocol = str(cfg.protocol).lower()
    if not cfg.enabled:
        raise SystemExit(
            "Current ov.conf has server.observability.traces.enabled=false; "
            "upload requires enabled=true with protocol grpc or http."
        )
    if protocol not in {"grpc", "http"}:
        raise SystemExit(
            "Current ov.conf upload protocol must be grpc or http; "
            f"got protocol={cfg.protocol!r}."
        )
    if not str(cfg.endpoint).strip():
        raise SystemExit("Current ov.conf trace endpoint is empty.")
    if protocol == "http" and not (
        cfg.endpoint.startswith("http://") or cfg.endpoint.startswith("https://")
    ):
        raise SystemExit(
            "OTLP/HTTP endpoint must include scheme, "
            "e.g. http://localhost:4318/v1/traces"
        )
    return cfg


def _iter_input_files(path: Path, include_rotated: bool) -> list[Path]:
    path = path.expanduser()
    if not include_rotated:
        return [path]

    rotated: list[tuple[int, Path]] = []
    for candidate in path.parent.glob(f"{path.name}.*"):
        suffix = candidate.name[len(path.name) + 1 :]
        if suffix.isdigit():
            rotated.append((int(suffix), candidate))

    # Larger suffixes are older with RotatingFile-style naming:
    # traces.jsonl.2 -> traces.jsonl.1 -> traces.jsonl
    ordered = [candidate for _, candidate in sorted(rotated, key=lambda item: item[0], reverse=True)]
    ordered.append(path)
    return ordered


def _parse_request(line: str):
    from google.protobuf.json_format import ParseDict
    from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import (
        ExportTraceServiceRequest,
    )

    data = json.loads(line)
    request = ExportTraceServiceRequest()
    ParseDict(data, request, ignore_unknown_fields=False)
    return request


def _span_count(request) -> int:
    return sum(
        len(scope_spans.spans)
        for resource_spans in request.resource_spans
        for scope_spans in resource_spans.scope_spans
    )


def _trace_ids(request) -> list[str]:
    seen: set[str] = set()
    trace_ids: list[str] = []
    for resource_spans in request.resource_spans:
        for scope_spans in resource_spans.scope_spans:
            for span in scope_spans.spans:
                trace_id = bytes(span.trace_id).hex()
                if trace_id and trace_id not in seen:
                    seen.add(trace_id)
                    trace_ids.append(trace_id)
    return trace_ids


def _override_service_name(request, service_name: str) -> None:
    for resource_spans in request.resource_spans:
        attrs = resource_spans.resource.attributes
        for attr in attrs:
            if attr.key == "service.name":
                attr.value.Clear()
                attr.value.string_value = service_name
                break
        else:
            attr = attrs.add()
            attr.key = "service.name"
            attr.value.string_value = service_name


def _make_exporter(cfg):
    protocol = str(cfg.protocol).lower()
    headers = {str(key): str(value) for key, value in (cfg.headers or {}).items()}
    if protocol == "grpc":
        from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import (
            OTLPSpanExporter as OTLPGrpcSpanExporter,
        )

        try:
            return OTLPGrpcSpanExporter(
                endpoint=cfg.endpoint,
                insecure=cfg.tls.insecure,
                headers=headers,
                timeout=60,
            )
        except TypeError:
            return OTLPGrpcSpanExporter(
                endpoint=cfg.endpoint,
                headers=headers,
                timeout=60,
            )

    if protocol == "http":
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
            OTLPSpanExporter as OTLPHttpSpanExporter,
        )

        return OTLPHttpSpanExporter(
            endpoint=cfg.endpoint,
            headers=headers,
            timeout=60,
        )

    raise AssertionError(f"unsupported protocol after validation: {cfg.protocol}")


def _upload_request(exporter, protocol: str, request) -> None:
    if protocol == "grpc":
        client = getattr(exporter, "_client", None)
        if client is None:
            raise RuntimeError("gRPC exporter client is not initialized")
        client.Export(
            request=request,
            metadata=getattr(exporter, "_headers", None),
            timeout=60,
        )
        return

    if protocol == "http":
        response = exporter._export(request.SerializePartialToString(), timeout_sec=60)
        if not response.ok:
            raise RuntimeError(
                f"HTTP export failed: status={response.status_code}, reason={response.reason}"
            )
        return

    raise AssertionError(f"unsupported protocol after validation: {protocol}")


def _upload_with_retries(exporter, protocol: str, request, *, attempts: int = 3) -> None:
    last_exc: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            _upload_request(exporter, protocol, request)
            return
        except Exception as exc:
            last_exc = exc
            if attempt == attempts:
                break
            sleep_seconds = min(2 ** (attempt - 1), 5)
            print(
                f"Upload failed on attempt {attempt}/{attempts}: {exc}; "
                f"retrying in {sleep_seconds}s...",
                file=sys.stderr,
            )
            time.sleep(sleep_seconds)
    raise RuntimeError(f"upload failed after {attempts} attempts: {last_exc}") from last_exc


def upload_files(files: Iterable[Path], cfg) -> dict:
    protocol = str(cfg.protocol).lower()
    service_name = str(cfg.service_name)
    exporter = _make_exporter(cfg)
    summary = {
        "loaded_files": [],
        "missing_files": [],
        "uploaded_batches": 0,
        "uploaded_spans": 0,
        "uploaded_trace_ids": [],
        "skipped_invalid_lines": 0,
    }
    uploaded_trace_id_set: set[str] = set()

    try:
        for file_path in files:
            file_path = file_path.expanduser()
            if not file_path.exists():
                summary["missing_files"].append(str(file_path))
                continue
            summary["loaded_files"].append(str(file_path))
            with file_path.open("r", encoding="utf-8") as fp:
                for line_no, line in enumerate(fp, start=1):
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        request = _parse_request(line)
                    except Exception as exc:
                        summary["skipped_invalid_lines"] += 1
                        print(
                            f"Skipping invalid JSONL line {file_path}:{line_no}: {exc}",
                            file=sys.stderr,
                        )
                        continue

                    spans = _span_count(request)
                    if spans == 0:
                        continue
                    trace_ids = _trace_ids(request)
                    _override_service_name(request, service_name)
                    _upload_with_retries(exporter, protocol, request)
                    summary["uploaded_batches"] += 1
                    summary["uploaded_spans"] += spans
                    for trace_id in trace_ids:
                        if trace_id not in uploaded_trace_id_set:
                            uploaded_trace_id_set.add(trace_id)
                            summary["uploaded_trace_ids"].append(trace_id)
    finally:
        try:
            exporter.shutdown()
        except Exception:
            pass

    return summary


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Upload local OTLP JSONL traces using current ov.conf grpc/http trace config."
    )
    parser.add_argument(
        "--file",
        required=True,
        help="Path to traces.jsonl copied from the customer/local environment.",
    )
    parser.add_argument(
        "--config",
        default=None,
        help="Optional ov.conf path for the upload environment. Defaults to OpenViking config lookup.",
    )
    parser.add_argument(
        "--no-include-rotated",
        action="store_true",
        help="Only upload --file; by default numeric rotated files are included old-to-new.",
    )
    args = parser.parse_args()

    cfg = _load_trace_config(args.config)
    main_file = Path(args.file).expanduser()
    if not main_file.exists():
        print(f"Error: main file not found: {main_file}", file=sys.stderr)
        return 1
    files = _iter_input_files(main_file, include_rotated=not args.no_include_rotated)
    summary = upload_files(files, cfg)

    print("Loaded files:")
    for item in summary["loaded_files"]:
        print(f"  {item}")
    if summary["missing_files"]:
        print("Missing files:")
        for item in summary["missing_files"]:
            print(f"  {item}")
    print("Uploaded:")
    print(f"  batches: {summary['uploaded_batches']}")
    print(f"  spans: {summary['uploaded_spans']}")
    print(f"  trace_ids: {len(summary['uploaded_trace_ids'])}")
    for trace_id in summary["uploaded_trace_ids"]:
        print(f"    {trace_id}")
    print(f"  skipped_invalid_lines: {summary['skipped_invalid_lines']}")
    print(f"  endpoint: {cfg.endpoint}")
    print(f"  protocol: {cfg.protocol}")
    print(f"  service_name: {cfg.service_name}")
    if summary["uploaded_batches"] == 0:
        print(
            "Error: no batches were uploaded (file is empty or all lines were invalid).",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
