"""Execute memory category extraction requests with an OpenAI-compatible LLM."""

from __future__ import annotations

import argparse
import json
import os
import re
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[3]
DEFAULT_OUTPUT_ROOT = ROOT / "outputs" / "open_benchmarks" / "memory_category_annotation_llm_v0"
CATEGORY_ID_PATTERN = re.compile(r"^[a-z0-9_][a-z0-9_-]*:[a-z0-9_][a-z0-9_-]*$")
CATEGORY_PART_PATTERN = re.compile(r"^[a-z0-9_][a-z0-9_-]*$")
CATEGORY_PART_MAX_LENGTH = 64
CATEGORY_SOURCE_ENUM = {
    "llm_prompt",
    "tool_schema",
    "uri_title_metadata",
    "manual_taxonomy",
    "outcome_policy",
    "rule_fallback",
    "existing_catalog",
    "annotation_catalog",
    "mixed",
}


@dataclass(frozen=True)
class WorkerResult:
    status: str
    raw_output: str
    returncode: int | None
    duration_seconds: float | None
    usage: dict[str, Any]
    error: dict[str, Any] | None
    artifacts: dict[str, str]

    @property
    def succeeded(self) -> bool:
        return self.status == "succeeded" and self.returncode in (0, None) and bool(self.raw_output.strip())


def _safe_key(value: str) -> str:
    return "".join(ch if ch.isalnum() or ch in {"-", "_", "."} else "_" for ch in value)[:180]


def _iter_requests(path: Path) -> list[dict[str, Any]]:
    rows = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            raise SystemExit(f"invalid request JSONL at line {line_number}: {exc}") from exc
        if not row.get("prompt"):
            raise SystemExit(f"request line {line_number} missing prompt")
        rows.append(row)
    if not rows:
        raise SystemExit(f"no requests found: {path}")
    return rows


def _iter_jsonl_objects(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            raise SystemExit(f"invalid JSONL at {path}:{line_number}: {exc}") from exc
        if not isinstance(row, dict):
            raise SystemExit(f"expected object at {path}:{line_number}")
        rows.append(row)
    return rows


def _required_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise SystemExit(f"missing required environment variable: {name}")
    return value


def _load_backend_config() -> dict[str, str]:
    return {
        "api_key_env": "ARK_API_KEY",
        "base_url": os.environ.get("ARK_BASE_URL", "https://ark.cn-beijing.volces.com/api/v3").rstrip("/"),
        "model": os.environ.get("DOUBAO_MODEL", "doubao-seed-2-0-pro-260215"),
        "provider": os.environ.get("LLM_PROVIDER_NAME", "volcengine_ark"),
    }


def _chat_completion(
    *,
    prompt: str,
    backend: dict[str, str],
    max_tokens: int,
    timeout_seconds: int,
    retry_count: int,
) -> dict[str, Any]:
    payload = {
        "model": backend["model"],
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0,
        "max_tokens": max_tokens,
    }
    url = backend["base_url"] + "/chat/completions"
    last_error: dict[str, Any] | None = None
    for attempt in range(retry_count + 1):
        request = urllib.request.Request(
            url,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={
                "Authorization": "Bearer " + _required_env(backend["api_key_env"]),
                "Content-Type": "application/json",
            },
            method="POST",
        )
        started = time.time()
        try:
            with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
                response_payload = json.loads(response.read().decode("utf-8"))
            message = response_payload.get("choices", [{}])[0].get("message", {})
            return {
                "content": message.get("content", ""),
                "duration_seconds": round(time.time() - started, 4),
                "error": None,
                "returncode": 0,
                "status_code": 200,
                "usage": response_payload.get("usage", {}),
            }
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            last_error = {"body_excerpt": body[:1000], "code": exc.code, "type": "HTTPError"}
            if exc.code not in {408, 409, 429, 500, 502, 503, 504}:
                break
        except Exception as exc:  # noqa: BLE001 - backend failures are diagnostic.
            last_error = {"message": str(exc), "type": type(exc).__name__}
        if attempt < retry_count:
            time.sleep(min(2**attempt, 8))
    return {
        "content": "",
        "duration_seconds": None,
        "error": last_error,
        "returncode": 1,
        "status_code": (last_error or {}).get("code"),
        "usage": {},
    }


def _run_worker(
    *,
    prompt: str,
    run_dir: Path,
    backend: dict[str, str],
    max_tokens: int,
    timeout_seconds: int,
    retry_count: int,
) -> WorkerResult:
    run_dir.mkdir(parents=True, exist_ok=True)
    prompt_path = run_dir / "prompt.txt"
    last_message_path = run_dir / "last_message.txt"
    meta_path = run_dir / "backend_response_meta.json"
    prompt_path.write_text(prompt, encoding="utf-8")
    response = _chat_completion(
        prompt=prompt,
        backend=backend,
        max_tokens=max_tokens,
        timeout_seconds=timeout_seconds,
        retry_count=retry_count,
    )
    raw_output = str(response.get("content") or "")
    last_message_path.write_text(raw_output, encoding="utf-8")
    provider_meta = {
        "api_key_env": backend["api_key_env"],
        "base_url": backend["base_url"],
        "duration_seconds": response.get("duration_seconds"),
        "error": response.get("error"),
        "model": backend["model"],
        "provider": backend["provider"],
        "returncode": response.get("returncode"),
        "status_code": response.get("status_code"),
        "usage": response.get("usage") or {},
    }
    meta_path.write_text(json.dumps(provider_meta, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return WorkerResult(
        status="succeeded" if response.get("returncode") == 0 and raw_output.strip() else "failed",
        raw_output=raw_output,
        returncode=response.get("returncode"),
        duration_seconds=response.get("duration_seconds"),
        usage=response.get("usage") or {},
        error=response.get("error"),
        artifacts={
            "last_message_path": str(last_message_path),
            "meta_path": str(meta_path),
            "prompt_path": str(prompt_path),
        },
    )


def _extract_json_object(text: str) -> dict[str, Any]:
    stripped = text.strip()
    if stripped.startswith("```"):
        lines = stripped.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]
        stripped = "\n".join(lines).strip()
    try:
        payload = json.loads(stripped)
        if isinstance(payload, dict):
            return payload
    except json.JSONDecodeError:
        pass
    start = stripped.find("{")
    end = stripped.rfind("}")
    if start >= 0 and end > start:
        payload = json.loads(stripped[start : end + 1])
        if isinstance(payload, dict):
            return payload
    raise ValueError("LLM output did not contain a JSON object")


def _normalize_annotation(annotation: dict[str, Any], *, request_id: str, subject: dict[str, Any] | None) -> dict[str, Any]:
    annotation["schema_version"] = "memory_category_annotation.v0"
    annotation["annotation_id"] = request_id
    annotation["request_id"] = request_id
    annotation["producer"] = "llm_prompt"
    annotation["subject"] = subject
    return annotation


def _validate_with_jsonschema(annotation: dict[str, Any], schema: dict[str, Any] | None) -> list[str] | None:
    if not schema:
        return None
    try:
        import jsonschema  # type: ignore[import-not-found]
    except Exception:
        return None
    try:
        jsonschema.validate(annotation, schema)
    except Exception as exc:
        return [str(exc)]
    return []


def _basic_validate_annotation(annotation: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if annotation.get("schema_version") != "memory_category_annotation.v0":
        errors.append("$.schema_version must be 'memory_category_annotation.v0'")
    if not isinstance(annotation.get("annotation_id"), str) or not annotation.get("annotation_id"):
        errors.append("$.annotation_id must be a non-empty string")
    subject = annotation.get("subject")
    if not isinstance(subject, dict):
        errors.append("$.subject must be an object")
    else:
        if not subject.get("subject_type"):
            errors.append("$.subject.subject_type is required")
        if not subject.get("subject_id"):
            errors.append("$.subject.subject_id is required")
    category = annotation.get("category")
    if not isinstance(category, dict):
        errors.append("$.category must be an object")
    else:
        for field in ("category1", "category2"):
            value = category.get(field)
            if not isinstance(value, str) or not value:
                errors.append(f"$.category.{field} is required")
            elif not CATEGORY_PART_PATTERN.fullmatch(value):
                errors.append(
                    f"$.category.{field} must be a reusable slug id using only "
                    "lowercase letters, numbers, '_' or '-'"
                )
            elif len(value) > CATEGORY_PART_MAX_LENGTH:
                errors.append(
                    f"$.category.{field} must be a compact reusable slug id "
                    f"with at most {CATEGORY_PART_MAX_LENGTH} characters; "
                    "put detailed applicability boundaries in $.applicability instead"
                )
        category3 = category.get("category3")
        if category3 is not None and (
            not isinstance(category3, str) or not CATEGORY_PART_PATTERN.fullmatch(category3)
        ):
            errors.append(
                "$.category.category3 must be null or a reusable slug id using only "
                "lowercase letters, numbers, '_' or '-'"
            )
        elif isinstance(category3, str) and len(category3) > CATEGORY_PART_MAX_LENGTH:
            errors.append(
                f"$.category.category3 must be a compact reusable slug id "
                f"with at most {CATEGORY_PART_MAX_LENGTH} characters; "
                "put detailed applicability boundaries in $.applicability instead"
            )
        if category.get("category_source") not in CATEGORY_SOURCE_ENUM:
            errors.append(
                "$.category.category_source must be one of "
                + ", ".join(sorted(CATEGORY_SOURCE_ENUM))
            )
        confidence = category.get("confidence")
        if not isinstance(confidence, (int, float)) or confidence < 0 or confidence > 1:
            errors.append("$.category.confidence must be a number in [0, 1]")
        catalog_match = category.get("catalog_match")
        if isinstance(catalog_match, dict):
            matched_category_id = catalog_match.get("matched_category_id")
            if matched_category_id is not None and not CATEGORY_ID_PATTERN.fullmatch(str(matched_category_id)):
                errors.append(
                    "$.category.catalog_match.matched_category_id must be '<category1>:<category2>' "
                    "or null"
                )
    if not isinstance(annotation.get("safety"), dict):
        errors.append("$.safety must be an object")
    return errors


def _load_schema(schema_path: Path | None) -> dict[str, Any] | None:
    if not schema_path:
        return None
    if not schema_path.is_file():
        raise SystemExit(f"schema file not found: {schema_path}")
    return json.loads(schema_path.read_text(encoding="utf-8"))


def _validate_annotation(annotation: dict[str, Any], schema: dict[str, Any] | None) -> list[str]:
    jsonschema_errors = _validate_with_jsonschema(annotation, schema)
    basic_errors = _basic_validate_annotation(annotation)
    if jsonschema_errors is None:
        return basic_errors
    return [*jsonschema_errors, *basic_errors]


def _validate_annotations_file(path: Path, schema: dict[str, Any] | None) -> dict[str, Any]:
    errors: list[dict[str, Any]] = []
    count = 0
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        count += 1
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            errors.append({"line_number": line_number, "message": f"invalid JSON: {exc}"})
            continue
        for message in _validate_annotation(row, schema):
            errors.append({"line_number": line_number, "message": message})
    return {
        "status": "passed" if not errors else "failed",
        "event_count": count,
        "error_count": len(errors),
        "errors": errors,
    }


def _validation_retry_prompt(base_prompt: str, *, errors: list[str], previous_output: str | None) -> str:
    details = "\n".join(f"- {error}" for error in errors[:12]) or "- unknown validation failure"
    previous = ""
    if previous_output:
        previous = (
            "\n\nPrevious invalid output, for reference only. Do not copy invalid enum values or malformed ids:\n"
            "```json\n"
            f"{previous_output.strip()[:6000]}\n"
            "```"
        )
    return (
        f"{base_prompt.rstrip()}\n\n"
        "Your previous answer failed the required JSON schema validation. "
        "Return one corrected JSON object only, with no markdown and no explanation.\n"
        "Do not invent schema fields. Do not normalize invalid enum values in prose; choose one enum value from the schema. "
        "category1/category2/category3 must be compact slug ids, not prose sentences; use lowercase snake_case. "
        f"Each category id part must be at most {CATEGORY_PART_MAX_LENGTH} characters; put detailed state, precondition, "
        "confirmation, or eligibility boundaries in applicability fields instead of the category id. "
        "For query-side subjects, category ids must describe the reusable business action, skill, artifact type, or "
        "applicability boundary; do not encode decision-node mechanics such as first_user, pre_write, before_write, "
        "classification, query, or retrieval into category1/category2/category3. "
        "If category.catalog_match.matched_category_id is present, it must be the canonical '<category1>:<category2>' id; "
        "otherwise set it to null.\n\n"
        "Validation errors:\n"
        f"{details}"
        f"{previous}"
    )


def _relative(path: Path) -> str:
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--requests", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument("--run-id", default=None)
    parser.add_argument("--schema-path", type=Path, default=None)
    parser.add_argument("--max-tokens", type=int, default=4000)
    parser.add_argument("--timeout-seconds", type=int, default=120)
    parser.add_argument("--retry-count", type=int, default=1, help="Schema-validation retry count per request.")
    parser.add_argument("--api-retry-count", type=int, default=1, help="Transport/backend retry count per LLM attempt.")
    parser.add_argument("--limit", type=int, default=0, help="0 means all requests")
    parser.add_argument(
        "--resume-existing",
        action="store_true",
        help="Reuse existing parsed annotations/execution rows in the same run_id and only run missing requests.",
    )
    args = parser.parse_args()

    requests_path = args.requests.resolve()
    request_rows = _iter_requests(requests_path)
    if args.limit > 0:
        request_rows = request_rows[: args.limit]
    request_ids = {str(row.get("request_id") or f"request_{index}") for index, row in enumerate(request_rows)}
    request_id_by_subject_id = {
        str(row.get("subject", {}).get("subject_id")): str(row.get("request_id") or f"request_{index}")
        for index, row in enumerate(request_rows)
        if isinstance(row.get("subject"), dict) and row.get("subject", {}).get("subject_id")
    }

    run_id = args.run_id or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    output_root = args.output_root if args.output_root.is_absolute() else ROOT / args.output_root
    run_root = output_root / _safe_key(run_id)
    run_root.mkdir(parents=True, exist_ok=True)

    schema_path = args.schema_path
    schema = _load_schema(schema_path.resolve() if schema_path else None)
    backend_config = _load_backend_config()
    annotations_path = run_root / "annotations.jsonl"
    execution_rows_path = run_root / "execution_rows.jsonl"
    validation_path = run_root / "validation_report.json"
    summary_path = run_root / "run_summary.json"

    loaded_annotations: list[dict[str, Any]] = _iter_jsonl_objects(annotations_path) if args.resume_existing else []
    annotations: list[dict[str, Any]] = []
    dropped_existing_annotations: list[dict[str, Any]] = []
    for line_index, row in enumerate(loaded_annotations, start=1):
        validation_errors = _validate_annotation(row, schema)
        if validation_errors:
            dropped_existing_annotations.append(
                {
                    "annotation_id": row.get("annotation_id"),
                    "line_number": line_index,
                    "request_id": row.get("request_id"),
                    "validation_errors": validation_errors,
                }
            )
            continue
        annotations.append(row)
    execution_rows: list[dict[str, Any]] = _iter_jsonl_objects(execution_rows_path) if args.resume_existing else []
    parsed_request_ids: set[str] = set()
    for row in annotations:
        request_id = row.get("request_id")
        if isinstance(request_id, str) and request_id:
            parsed_request_ids.add(request_id)
            continue
        annotation_id = row.get("annotation_id")
        if isinstance(annotation_id, str) and annotation_id in request_ids:
            parsed_request_ids.add(annotation_id)
            continue
        subject = row.get("subject") if isinstance(row.get("subject"), dict) else {}
        subject_id = subject.get("subject_id")
        if isinstance(subject_id, str) and subject_id in request_id_by_subject_id:
            parsed_request_ids.add(request_id_by_subject_id[subject_id])
    if args.resume_existing and len(parsed_request_ids) > len(annotations):
        raise SystemExit(f"cannot resume because parsed request ids exceed annotations: {annotations_path}")

    for index, request_row in enumerate(request_rows):
        request_id = str(request_row.get("request_id") or f"request_{index}")
        if args.resume_existing and request_id in parsed_request_ids:
            continue
        request_dir = run_root / f"{index:04d}_{_safe_key(request_id)}"
        attempts: list[dict[str, Any]] = []
        parse_error: dict[str, Any] | None = None
        validation_errors: list[str] = []
        previous_output: str | None = None
        last_result = None
        annotation: dict[str, Any] | None = None
        for attempt_index in range(args.retry_count + 1):
            prompt = (
                request_row["prompt"]
                if attempt_index == 0
                else _validation_retry_prompt(
                    request_row["prompt"],
                    errors=validation_errors or ([parse_error["message"]] if parse_error else []),
                    previous_output=previous_output,
                )
            )
            attempt_dir = request_dir / f"attempt_{attempt_index + 1:02d}"
            result = _run_worker(
                prompt=prompt,
                run_dir=attempt_dir,
                backend=backend_config,
                max_tokens=args.max_tokens,
                timeout_seconds=args.timeout_seconds,
                retry_count=args.api_retry_count,
            )
            last_result = result
            parse_error = None
            validation_errors = []
            candidate: dict[str, Any] | None = None
            previous_output = result.raw_output if result.succeeded else None
            if result.succeeded:
                try:
                    candidate = _extract_json_object(result.raw_output)
                    candidate = _normalize_annotation(
                        candidate,
                        request_id=request_id,
                        subject=request_row.get("subject"),
                    )
                    validation_errors = _validate_annotation(candidate, schema)
                except Exception as exc:  # noqa: BLE001 - LLM output parsing is diagnostic.
                    parse_error = {"type": type(exc).__name__, "message": str(exc)}
            else:
                validation_errors = [f"worker failed with status={result.status} returncode={result.returncode}"]
            attempt_row = {
                "attempt_index": attempt_index + 1,
                "status": (
                    "parsed"
                    if candidate and not validation_errors and not parse_error
                    else "schema_failed"
                    if candidate and validation_errors
                    else "parse_failed"
                    if parse_error
                    else "worker_failed"
                ),
                "worker_status": result.status,
                "returncode": result.returncode,
                "parse_error": parse_error,
                "validation_errors": validation_errors,
                "artifacts": {key: _relative(Path(value)) for key, value in result.artifacts.items()},
                "usage": result.usage,
                "duration_seconds": result.duration_seconds,
            }
            attempts.append(attempt_row)
            if candidate and not validation_errors and not parse_error:
                annotation = candidate
                break
        row = {
            "request_id": request_id,
            "status": "parsed" if annotation else "failed",
            "worker_status": last_result.status if last_result else "not_run",
            "returncode": last_result.returncode if last_result else None,
            "parse_error": parse_error,
            "validation_errors": validation_errors,
            "attempt_count": len(attempts),
            "attempts": attempts,
            "subject": request_row.get("subject"),
            "artifacts": attempts[-1]["artifacts"] if attempts else {},
            "usage": attempts[-1]["usage"] if attempts else {},
            "duration_seconds": sum(float(attempt.get("duration_seconds") or 0.0) for attempt in attempts),
        }
        execution_rows.append(row)
        if annotation:
            annotations.append(annotation)
            parsed_request_ids.add(request_id)
        annotations_path.write_text(
            "".join(json.dumps(item, ensure_ascii=False, sort_keys=True) + "\n" for item in annotations),
            encoding="utf-8",
        )
        execution_rows_path.write_text(
            "".join(json.dumps(item, ensure_ascii=False, sort_keys=True) + "\n" for item in execution_rows),
            encoding="utf-8",
        )

    validation_report = _validate_annotations_file(annotations_path, schema) if annotations else None
    if validation_report:
        validation_path.write_text(
            json.dumps(validation_report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    summary = {
        "schema_version": "memory_category_annotation_llm_summary.v0",
        "run_id": run_id,
        "status": "passed" if validation_report and validation_report["status"] == "passed" and len(annotations) == len(request_rows) else "failed",
        "request_count": len(request_rows),
        "annotation_count": len(annotations),
        "failed_count": len(request_rows) - len(annotations),
        "backend": "openai_compatible",
        "backend_provider": backend_config.get("provider"),
        "backend_model": backend_config.get("model"),
        "backend_api_key_env": backend_config.get("api_key_env"),
        "dropped_existing_annotation_count": len(dropped_existing_annotations),
        "dropped_existing_annotations": dropped_existing_annotations[:20],
        "max_tokens": args.max_tokens,
        "schema_retry_count": args.retry_count,
        "api_retry_count": args.api_retry_count,
        "validation_retry_enabled": True,
        "requests_path": _relative(requests_path),
        "schema_path": _relative(schema_path.resolve()) if schema_path else None,
        "annotations_path": _relative(annotations_path),
        "execution_rows_path": _relative(execution_rows_path),
        "validation_report_path": _relative(validation_path) if validation_report else None,
        "claim_boundary": "llm_prompt_generated_category_annotation_runtime_safe_visible_context_only",
        "validation_status": validation_report["status"] if validation_report else "not_run_no_annotations",
    }
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if summary["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
