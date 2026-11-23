# Developer Logging Guide

Choose a log level by the record's purpose. These rules apply to OpenViking-owned code; third-party dependencies retain their level semantics. Logging changes do not introduce new audit APIs or storage.

Log configuration is documented in the [configuration guide](../en/guides/01-configuration.md). Python output is implemented in [logger.py](../../openviking_cli/utils/logger.py); safe field helpers are in [log_safety.py](../../openviking/observability/log_safety.py).

## 1. Choose the level

| Level | Purpose | Examples |
| --- | --- | --- |
| `CRITICAL` | The process cannot continue | Fatal startup failure, unrecoverable process state |
| `ERROR` | A final failure needs intervention | Failed request or task, exhausted retries, risk of data loss, security mechanism failure |
| `WARNING` | Security audit only | Authentication failure, permission or membership changes, key lifecycle, sensitive configuration/data operations |
| `INFO` | Recovery, state changes, and expected rejections | Retry/backoff, fallback, circuit-breaker transitions, startup/shutdown, migration, batch summary, 4xx rejection |
| `DEBUG` | Business process and diagnostic detail | Decision inputs, intermediate results, cache behaviour, successful operation details |

- A retry attempt is `INFO`; its final failure is one `ERROR`. Record a fallback where it actually runs, state the resulting behaviour, and mark incomplete results with `degraded=true`.
- A successful model response does not resolve a failed conversation or memory write. If recovery depends on a future client turn resending history, log the write failure at `ERROR`.
- Expected 4xx responses are `INFO`, without a stack trace. Authentication failure or authorization denial also needs a separate security audit `WARNING`.
- Every ROOT-identity call in `api_key` mode needs an unsampled audit `WARNING`, including reads. This rule excludes dev/trusted modes, unauthenticated routes, and internally constructed ROOT contexts.
- Telemetry/metrics failures must not fail the business operation: the first failure is `ERROR`, subsequent occurrences are `INFO`. Ordinary retries, fallbacks, slow calls, and skipped bad records never use audit `WARNING`.

## 2. Write useful, bounded records

- Log the final outcome once at the boundary that decides it. Intermediate layers propagate errors or use `DEBUG` detail.
- Use stable English templates with a lowercase start and no trailing period. The header identifies the module; avoid handwritten module prefixes and empty “started/done” messages.
- Python uses `get_logger(__name__)` and lazy arguments: `logger.info("retry attempt=%d", attempt)`. Do not use f-strings, string concatenation, `%` interpolation, or `.format()` to build log messages. Other runtimes use their native structured/lazy interfaces.
- Put searchable dimensions in `extra`, using canonical names such as `operation`, `http_route`, `http_status_code`, `duration_ms`, `error_code`, `error_type`, `total`, `failed`, and `skipped`.
- In loops and batches, emit one `INFO` degradation summary with counts and `DEBUG` details. High-frequency process detail belongs at `DEBUG`. Check `isEnabledFor()` before expensive log-only work; simple counts and hashes can remain inline.
- Keep messages within 2 KB and field values within 1 KB. Truncation must preserve valid UTF-8 and set `truncated=true` and `original_length`.

## 3. Keep content safe

The same rules apply to messages, `extra`, trace attributes, audit records, and child-process diagnostics. A formatter or credential-pattern sanitizer does not make arbitrary text safe.

| Content | Rule |
| --- | --- |
| Trusted identity/correlation IDs, task IDs/status, fixed operations/error codes, exception types, counts, durations, file types | Use validated IDs, fixed enums, or primitive values. Unknown fields are forbidden by default. |
| Routes and entry-point metadata | Use route templates, never raw paths or queries. IPs come from validated transport/trusted-proxy metadata; user agents contain only an allowlisted product/version summary, at most 256 bytes. Omit unsafe values. |
| Paths, URIs, resource locators | Use `safe_path_for_log()` / `safe_uri_for_log()` or an opaque `short_hash()`. Verbatim names are allowed only when trusted and nonsensitive; user-controlled or tenant paths require transformation. |
| Exceptions | Prefer `type(exc).__name__` and a stable `error_code`. Do not emit raw exception text or objects; `sanitize_error()` and `sanitize_public_http_error()` provide only partial protection. |
| Credentials, plaintext Privacy values, `peer_id`, absolute paths, prompts, messages, document/memory/skill bodies, model input/output, tool arguments/results, full request/response bodies | Never emit. Summarize with counts, lengths, types, or hashes. This includes peer segments embedded in URIs. |
| Whole objects and task `meta` | Do not dump objects with `%s`, `%r`, `asdict()`, or `model_dump()`. Select safe fields; arbitrary metadata values stay out. `repr=False` does not protect serialization. |

- Keys, tokens, cookies, signature headers, pre-signed URLs, connection-string credentials, and reconstructable key fragments are forbidden. An explicitly enabled diagnostic key display may use `mask_api_key()` to show only the last four characters.
- Stack traces are limited to explicit local troubleshooting at `ERROR`, after cleaning exception messages, source lines, and paths. `exc_info=True` cannot bypass this requirement.
- Allowed metadata does not authorize embedding raw content in a message. Prefer safe summaries even at `DEBUG` and with `log_payloads` enabled.

```python
from openviking.observability.log_safety import safe_uri_for_log
from openviking_cli.utils import get_logger

logger = get_logger(__name__)
logger.info(
    "resource read failed; using cached result error_type=%s",
    type(exc).__name__,
    extra={"target_id": safe_uri_for_log(uri), "error_type": type(exc).__name__, "degraded": True},
)
```

## 4. Use the common output

```text
2026-11-13T22:56:01.028Z INFO openviking.server.bootstrap pid=452394 request_id=- trace_id=- message="server started"
```

- Order: `ts level logger pid request_id trace_id message`, then structured fields. Use the event's creation time in UTC with fixed milliseconds. Keep the originating PID and a fixed module/component name.
- Levels are `DEBUG`, `INFO`, `WARNING`, `ERROR`, `CRITICAL`. Normalize `TRACE→DEBUG`, `WARN→WARNING`, and `FATAL→CRITICAL`; normalization must not change third-party event semantics. Rust has no native `CRITICAL`; mapping its threshold to `ERROR` cannot select fatal events alone.
- Each event occupies one UTF-8 line, without ANSI colours, Rich layouts, wrapping, or progress bars. JSON-quote messages and string fields; keep numbers/booleans typed. Missing IDs are `-` in text and `null` in structured records. Escape control characters.
- Inject correlation IDs in the shared adapter. Business exception metadata must not overwrite the request/task `request_id` with a provider request ID.
- The server entry point owns handlers, thresholds, and format for root, Uvicorn, MCP, APScheduler, and native output. Business modules must not install `basicConfig`, independent handlers, or loguru sinks. Only the common format is supported; worker initialization and reloads must not duplicate records. Native settings requiring restart must say so.
- SDK imports must not reconfigure the host root logger. Managed child output merged into server logs retains its own PID/component and follows the same format; unadapted child output goes to a separate identified file.
- **Bootstrap progress, dependency checks, startup results, and failures use direct `print(..., flush=True)`**, independently of log thresholds/files. Failures go to stderr and other status to stdout; content still obeys section 3.
- Interactive CLI results/progress and protocol output have their own rendering. Diagnostics must not contaminate machine-readable stdout or a stdio protocol.

## 5. Security audit requirements

Audit covers accounts/membership, ACL and privileges, key lifecycle and identity binding, authentication/authorization failures, sensitive configuration/data writes, deletion/export, and ROOT calls described in section 1.

- Record the actual success or failure once after execution; never sample the audit trail. Route audit copies by both `logger=openviking.audit` and a registered `audit.*` event, not by `WARNING` alone.
- A complete audit record contains `ts`, `event`, `action`, `result`, `actor_id`, `actor_role`, `actor_type`, `account_id`, `target_type`, `target_id`, `request_id`, `trace_id`, `source_ip`, `user_agent`, and `error_code`. `action` is the event's final segment; `result` is `success`/`failure`; actor types are `user`/`admin`/`root`/`service`.
- Unknown identity, unavailable context, and successful `error_code` are `null`. Do not infer an authenticated actor from supplied request text. ROOT records also include the route template, HTTP method/status, and `sensitive=true` for high-risk operations; a root API key uses `actor_id=root-key`, never a fabricated user/account.
- Use the same safe fields for the log copy and a queryable persisted audit event. Verify persistence; a best-effort event publication or a Usage/Audit HTTP record is not proof that a security event was stored. Audit infrastructure failures produce a safe `ERROR` without rolling back the business operation.

## 6. Check before merging

- Level matches the actual outcome; retry/fallback and batch degradation remain visible, and final failures are recorded once.
- Messages, fields, exceptions, and nested objects contain no forbidden values; IDs come from trusted context.
- Run Ruff `G` checks on changed Python logging calls. These check call style, not level semantics or content safety.
- Reuse existing regression tests. Change log assertions only when needed; avoid new test files and mock-call tests. Temporary output captures can verify failure paths.
- For initialization/native-adapter changes, inspect actual output under UTC and Asia/Shanghai, redirected/non-TTY output, repeated initialization/reload, worker processes, and different thresholds. Check timestamps, levels, fields, single-line output, duplicate records, and context isolation; bootstrap prints are checked separately.
