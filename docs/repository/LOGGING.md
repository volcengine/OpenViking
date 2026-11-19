# Developer Logging Guide

This document defines log level semantics, content boundaries, and implementation rules for every OpenViking runtime (Python, Rust, the C++ engine, TypeScript). It targets contributors, not operators; `log.level`, `log.format`, and `log.output` remain documented in the [configuration guide](../en/guides/01-configuration.md).

The purpose-based level rules apply to OpenViking-owned code. Third-party dependencies retain their level semantics and join the common output described in 8.10. This document includes target requirements that are not yet implemented; sections 7 and 9 describe the current state and rollout. Policy examples do not imply that the corresponding configuration is already supported.

Related entry points:

- Implementation: `openviking_cli/utils/logger.py` (`get_logger`, `reconfigure_logging`, OTel bridge)
- Config model: `openviking_cli/utils/config/log_config.py`
- Audit event bus: `openviking/observability/events.py`, `openviking/observability/usage_audit/`
- Contribution flow: [CONTRIBUTING.md](../../CONTRIBUTING.md)

---

## 1. Level semantics

A record's level is decided by **what the record is for**, not by how serious it feels.

| Level | Sole purpose | Allowed | Forbidden |
| --- | --- | --- | --- |
| `CRITICAL` | Process cannot continue | Fatal startup failure, unrecoverable critical state | Ordinary business errors, retryable failures |
| `ERROR` | Failure that needs a human | Unhandled exceptions, dependency outage that fails a request, data-consistency risk, background job after retries are exhausted, audit/security mechanism itself failing | Expected business rejections (4xx belongs at `INFO`), single failures during retries, successfully degraded requests, slow calls, skipped bad records |
| `WARNING` | **Audit events only** | Permission and role changes, account and membership operations, key lifecycle, authentication failures, configuration and data deletion, and every ROOT-identity call in `api_key` mode (3.5) | Fallbacks, retries and backoff, missing dependencies, slow calls, skipped/ignored input, bad records, cache misses, side-channel telemetry failures |
| `INFO` | Fallback logic, state transitions, and request rejections | Retries and backoff, degradation and fallback, circuit-breaker transitions, primary/backup switching, startup and shutdown, one-off migrations, aggregate statistics, 4xx business rejections (bad arguments, missing resources, unauthorized) | Per-request business detail on success, per-item detail inside loops, payload content |
| `DEBUG` | Business process | Request input/output summaries, state-machine transitions, retrieval and parsing intermediates, branch decisions, cache operation detail | Sensitive values, oversized objects, per-item output in hot loops |

Additional rules:

- `ERROR` and `CRITICAL` must not appear on a normal control-flow path. If a retry, a fallback, or a client-side message resolves the failure, it is not `ERROR`.
- A successful model response does not resolve a failed conversation or memory write. Log a failed write at `ERROR` when recovery depends on a future client turn resending the history, since the data may be lost.
- `WARNING` is a **restricted level**. Its boundary is fixed by section 3. Anything that merely "seems worth noting" is `INFO`.
- `INFO` carries the signal "the system handled it itself". Fallbacks, retries, backoff, and circuit-breaker transitions must be visible there, but must not require immediate action.
- `DEBUG` is the default outlet for business process. Assume `DEBUG` first, then check the audit list in section 3 and the fallback and rejection list in section 5.
- Whichever level you choose, **which fields you may emit** is governed by the security rules in section 4. Level compliance does not imply content compliance.
- 4xx is always `INFO`: never `ERROR` (it is not a fault), never `DEBUG` (caller misuse would be invisible at the default level).
- ROOT invocation is the single exception to the level rules: an endpoint may be `INFO` or `DEBUG`, but a call from a ROOT identity in `api_key` mode is audited at `WARNING` (3.5). Dev and trusted modes are excluded.

### Decision order

```text
Is this a security-sensitive user or admin action?
  Yes → WARNING (audit, section 3)
  No  ↓
Did the request or task ultimately fail in a way that needs a human?
  Yes → ERROR
  No  ↓
Does this describe the system absorbing a problem (retry, fallback, circuit breaker, failback)?
  Yes → INFO
  No  ↓
DEBUG
```

### Common misjudgements

| Situation | Level | Reason |
| --- | --- | --- |
| Bad client argument, missing resource, unauthorized (4xx) | `INFO` | Expected outcome, not a fault, but it reveals caller misuse and must stay observable |
| Authentication failure, authorization attempt (the security side of 401/403) | `INFO` response log plus audit `WARNING` | Two records with different purposes; see 3.1 |
| A ROOT identity in `api_key` mode calling any endpoint, including read-only | `WARNING` | Superuser invocations always leave an audit record regardless of the endpoint's business level; see 3.5 |
| First attempt fails, second succeeds | `INFO` (report the retry) | System recovered |
| Retries exhausted, request fails | `ERROR` (exactly once) | Needs a human |
| Primary provider fails, backup succeeds | `INFO` | Successful fallback |
| Backup succeeds but output quality drops or fields are missing | `INFO` plus an explicit field | Still a fallback, not audit |
| Dependency unavailable, default value used (e.g. empty summary) | `INFO` | Fallback |
| Dependency unavailable, nothing to fall back to, feature is down | `ERROR` | Fault |
| Conversation or memory write fails, while the model response succeeds | `ERROR` | Returning a response does not ensure persistence; a later client turn is not a guaranteed retry |
| Call exceeds a slow-call threshold | `INFO` (if needed for trend) or `DEBUG` | Not an error |
| Telemetry/metrics write itself fails | `INFO`; `ERROR` on first occurrence | Side channel must not pollute business levels |
| Cache lock unavailable, read through to the database | `INFO` | Fallback |
| N records in a loop, M are malformed | One `INFO` summary plus `DEBUG` detail | Noise control |
| Unknown config field or invalid value ignored | `INFO` | Compatibility fallback |
| Malformed `X-Request-ID`, regenerated | `INFO` | Client hygiene, not a security event |

---

## 2. Why not "severity"

Severity-based levels drift with each developer's judgement: one person calls a failure `WARNING`, another calls the same failure `ERROR`. This repository shows exactly that outcome — `WARNING` is the most used level (section 7).

Purpose-based levels are objective and decidable:

- Needs a human → `ERROR`, wired straight to alerting.
- Needs an accountability trail → `WARNING` on `openviking.audit`, routed to the audit pipeline by audit event fields.
- The system healed itself, or the caller was correctly rejected → `INFO`, wired straight to stability dashboards.
- Everything else → `DEBUG`, enabled only while debugging.

Owned code can use these rules to select consumers. Third-party records in a mixed stream must be routed using their logger and event fields; `WARNING` alone does not identify an audit record.

---

## 3. Audit logging (the only legitimate use of `WARNING` in owned code)

### 3.1 Events that must be covered

The following actions must produce an audit record. Most of them currently produce none (section 7.3); adding them is purely additive.

**Accounts and membership**

- Account creation and deletion; account state changes (enable, disable, migrate)
- Inviting, joining, and removing members; role changes (`set_role`)
- Resetting or overriding account-level settings (including `reset_memory_template`)

**Permissions**

- ACL grant, revoke, delete (`grant_acl` / `revoke_acl` / `delete_acl`)
- Cross-account or cross-user resource authorization
- Granting or reclaiming admin and ROOT privileges

**Keys and credentials**

- API key creation, regeneration, revocation, migration
- OAuth/OIDC identity binding and unbinding; trusted-identity bulk registration
- Any export of key material

**Authentication outcomes**

- Login failure, token validation failure, signature verification failure
- Authorization denial (role mismatch, cross-tenant access)
- Authentication health check transitioning from healthy to unhealthy

Note that this group is an audit record and is not the same line as the `INFO` response log emitted when a request returns 4xx. The response log says "this request was rejected"; the audit record says "who tried what, when". They use different loggers, fields, and consumers and may coexist. Ordinary 4xx (bad arguments, missing resource) is `INFO` only and never enters audit.

**Configuration and data**

- Server config, user config, and runtime config writes and rollbacks
- Account data deletion, task deletion, snapshot deletion, bulk export (ovpack)
- Privacy entry writes and cleanup

### 3.2 Required fields

Field values are also subject to section 4: `peer_id`, keys, and bodies must not enter audit storage.

| Field | Meaning |
| --- | --- |
| `ts` | UTC timestamp, ISO 8601 with fixed millisecond precision; format in 8.10.1 |
| `event` | Registered stable name with an `audit.` prefix, e.g. `audit.account.create`, `audit.acl.grant` |
| `result` | `success` / `failure` |
| `actor_id` / `actor_role` / `actor_type` | Actor identity; known `actor_type` values are `user`, `admin`, `root`, and `service`. When identity cannot be established, such as an authentication failure, the corresponding fields are `null`; never treat a supplied username as an authenticated principal |
| `account_id` | Account owning the target; `null` for a ROOT invocation with no account scope, never a fabricated default account |
| `target_type` / `target_id` | Object type and safe identifier, e.g. `user`, `api_key`, `config`, `http_route`; never a key value or raw URI |
| `action` | Generated by the helper from the last segment of `event` |
| `request_id` / `trace_id` | Taken from execution context, or `null` without the corresponding context |
| `source_ip` / `user_agent` | Entry-point metadata processed under 4.2; `null` when unavailable or unsafe to extract |
| `error_code` | A stable code is required on failure; `null` on success; never an exception message or stack trace |

Every field above must exist. Explicitly nullable fields use `null`, rendered in text under 8.10. `actor_role` is the effective role and `actor_type` is the kind of principal; background services use `service`, not a new `system` alias. ROOT invocations also require the HTTP fields in 3.5.

### 3.3 Output form

Current scope: the logging-standard rollout changes existing log levels, content, formatting and initialization. The existing Usage/Audit store persists usage and HTTP request records, but does not handle the security-operation `audit.*` events described here. The audit helper, event coverage, schema, persistence, queries and retention for those events remain a separate feature backlog; this rollout does not add them.

Audit needs **two sinks**, not one:

1. **Structured event**: the target is a queryable, retention-managed primary channel through the event bus and `usage_audit` projection. The existing `try_publish_event()` is best-effort, and the current projection does not handle these `audit.*` events. Implementation must add event handling, persistence, and failure reporting; calling the publisher does not prove that audit data was persisted.
2. **Log copy** on the dedicated `openviking.audit` logger at `WARNING`. This is the side-channel copy for log collection, SIEM, and offline forensics.

Collection of log copies must check both `logger=openviking.audit` and an explicit audit `event` field, and validate the required fields. Do not route every `WARNING` in the process into audit; see 8.10.6 for third-party warnings.

All call sites use the proposed `log_audit()` helper. The examples below describe its target interface, **not an API currently available for import**. Its responsibilities are:

- Callers explicitly supply `event`, `result`, actor, account, target, and `error_code`; do not infer identity or targets from arbitrary request parameters.
- The helper captures event time `ts`, derives `action`, and obtains `request_id`, `trace_id`, `source_ip`, and `user_agent` from execution context. Without context, retain those fields with `null` values.
- Validate and transform fields under 4.2, then send **the same complete record** to both destinations. The log copy uses the fixed message `"audit event"` with all business fields in `extra`, rendered by the formatter in 8.10. Collectors must not parse message text to discover event names or identities.

For example, after an ACL grant completes:

```python
log_audit(
    event="audit.acl.grant",
    result="success",
    actor_id=actor_id,
    actor_role=actor_role,
    actor_type="admin",
    account_id=account_id,
    target_type="user",
    target_id=target_user_id,
    error_code=None,
)
```

This example assumes an authenticated administrator; other principals supply their actual `actor_type`. On failure, use `result="failure"` and the corresponding stable `error_code`, keeping the same schema for all other fields.

Constraints:

- Audit **after** execution and record the actual outcome; write both success and failure, never success only.
- At a validation, publication, or persistence failure boundary, the audit infrastructure emits one safe `ERROR` without rolling back the business transaction. Do not swallow failures and claim that both writes succeeded.
- Audit records must never be swallowed by a business-layer `except Exception`.
- Never put plaintext keys, passwords, tokens, or full request bodies in an audit message.




### 3.4 Anti-pattern

```python
# Wrong: an account migration logged at INFO, invisible to the audit pipeline
logger.info("Migrated API key for user %s in account %s", user_id, account_id)

# Right: a background migration service, using the target interface in 3.3
log_audit(
    event="audit.api_key.migrate",
    result="success",
    actor_id="api-key-migrator",
    actor_role=service_role,
    actor_type="service",
    account_id=account_id,
    target_type="api_key",
    target_id=user_id,
    error_code=None,
)
```

Here `service_role` is the service's actual effective role, and `target_id` is the stable ID of the user owning the key, not the key value. For background work without request context, the helper fills correlation fields with `null`.

### 3.5 ROOT invocations

ROOT is the highest-privilege identity. **Under `api_key` auth mode, every call made under a ROOT identity must leave one `WARNING` audit record**, even when the call is read-only and even when it has no business impact.

Rationale: when ROOT is misused or leaked, the only way to reconstruct what happened is the trail. A read-only call can still read across tenants, so it is no less dangerous than a write. A ROOT call to an ordinary business endpoint would normally be `INFO`; here `WARNING` wins.

#### Trigger

Both conditions must hold:

1. The auth mode is `api_key` (`AuthMode.API_KEY`);
2. The resolved `identity.role == Role.ROOT`, that is, the caller holds the root API key (`server/api_keys/key_mgr.py:131`).

#### Non-triggering cases

| Case | Reason |
| --- | --- |
| `dev` mode (`auth/plugins/dev.py:41`) | No authentication; every request is synthesized as ROOT and is not a real superuser |
| `trusted` mode (`auth/plugins/trusted.py:183`) | ROOT is injected by the gateway (admin paths, the `/health` control probe) and does not represent a real actor |
| Custom auth modes | Semantics are unknown, so nothing is recorded by default; a mode that needs audit should call `log_audit()` in its own plugin |
| Unauthenticated routes (`/health`, `/ready`, `/metrics`) | Never produce a ROOT identity |
| Internal ROOT contexts (TTL, semantic processor, viking_fs, system_assets, pack_service, core) | Do not pass through the auth entry point and therefore never trigger |

#### Where to implement

`_build_request_context()` (`server/auth/__init__.py:113`) is the only choke point that sees `identity.role`, the auth mode, and `request.state` at once. Do not scatter this across the 38 `Role.ROOT` references in `openviking/server` (18 of which are equality checks).

Prefer "mark on entry, emit on completion" so one record carries both the caller and the outcome:

```python
# _build_request_context(): mark only, do not emit
if ctx.role == Role.ROOT and _auth_mode(request) == AuthMode.API_KEY.value:
    request.state.root_audit = {
        "actor_id": identity.user_id or "root-key",
        "account_id": identity.account_id,
    }
```

```python
# On request completion: emit exactly one record
if (root_audit := getattr(request.state, "root_audit", None)) is not None:
    log_audit(
        event="audit.root.invoke",
        result="success" if status_code < 400 else "failure",
        actor_id=root_audit["actor_id"],
        actor_role="root",
        actor_type="root",
        account_id=root_audit["account_id"],
        target_type="http_route",
        target_id=http_route,
        http_route=http_route,
        http_method=method,
        http_status_code=status_code,
        error_code=None if status_code < 400 else error_code,
    )
```

`http_route` must be the matched route template, never the raw URL with path parameters or a query. The failure `error_code` comes from response classification, with the fixed fallback `HTTP_REQUEST_FAILED` for unclassified failures. Call the helper before execution-context cleanup so it can inject correlation and entry-point fields.

`_auth_mode()` already exists (`server/auth/__init__.py:32`); do not read the config again. Note that it falls back to `api_key` when no config is available, so unit tests must inject the auth mode explicitly or their requests will be treated as real ROOT.

#### Required fields

All fields from 3.2, with `event=audit.root.invoke`, `action=invoke`, `actor_role=root`, `actor_type=root`, `target_type=http_route`, and the route template as `target_id`; additionally, `http_method`, `http_route`, and `http_status_code`.

- The root API key has no account or user; write `root-key` as `actor_id`, not `default/default`.
- Record failures too, with `result=failure` and an `error_code`.
- Mark high-risk actions (ROOT reading plaintext keys, cross-account reads or writes) with `sensitive=true` so they can be alerted separately.

#### Constraints

- One record per request. Do not emit once on entry and again on completion.
- No sampling. Sampling an audit trail is giving it up. If ROOT polling (Studio task polling, SDK health checks) makes the volume large, reduce the polling frequency or use a non-ROOT identity; do not drop samples server-side.
- Content is still governed by section 4: no request bodies, no raw path parameters, no context content.

---

## 4. Log security rules

Log files, trace attributes, and audit storage are three independent egress paths; a leak in any one of them is a leak. This section governs **fields**.

### 4.1 Allowlist, not denylist

Deny by default. Only the "Allowed" column in 4.2 may be emitted verbatim; every other field must be transformed (hash, truncate, redact, relativize) or reduced to an aggregate (length, count, duration, boolean). When classification is unclear, treat the field as forbidden.

### 4.2 Field classification

| Class | Fields | Handling |
| --- | --- | --- |
| Allowed | Logging-framework-generated `ts`, `level`, `logger`, `pid` | Emit verbatim; logger names are fixed module or component names, never built from user input |
| Allowed | `account_id`, `user_id`, `request_id`, `trace_id`, `span_id`, `operation`, `http_method`, `http_route`, `http_status_code`, `duration_ms`, `role`, `task_id`, `task_type`, `task_status`, `task_stage`, retry counts, queue depth, `error_code`, `error_type`, `file_type` | Use validated identity/correlation IDs, fixed enums, exception type names, or numbers; `http_route` is a route template only |
| Allowed | `event`, `action`, `result`, `actor_role`, `actor_type`, `target_type` | Registered events and fixed enums validated under 3.2, never user-authored free text |
| Allowed | `degraded`, `sensitive`, `truncated`, `payload`, `total`, `failed`, `skipped`, `original_length` | Booleans or nonnegative counts without content |
| Allowed after validation/transformation | `actor_id`, `target_id` | Authenticated principal IDs, fixed service IDs, or resolved object IDs; transform paths, URIs, and peer segments under 4.4–4.7. Never use keys, credentials, or arbitrary request text. For `target_type=http_route`, use only the route template |
| Allowed after validation/transformation | `source_ip` | A single validated IP parsed from transport metadata or a trusted proxy; never the raw forwarding header; `null` if uncertain |
| Allowed after validation/transformation | `user_agent` | Only allowlist-parsed product family and version summaries, without other free text and limited to 256 bytes; `null` if unsafe to parse, never the raw request header |
| Allowed after transformation | Local paths, `viking://` URIs, `resource_id`, exception messages | Apply 4.4–4.8 |
| Forbidden | `peer_id`, keys and credentials, context content, absolute paths, full request/response bodies, model input and output | Never emit |

Task metadata (`task_id`, `task_type`, status, stage, durations, retry and error counts, queue depth) may be logged directly; it is the most useful troubleshooting dimension. Task `meta` is the exception: log it wholesale only after confirming its values contain no URIs, paths, or secrets; otherwise log key names without values.

`resource_id` may be logged only as an opaque deployment label. If it comes from an environment variable and could be a path or URL, treat it under 4.7.

### 4.3 The existing injection baseline

`_get_log_context()` currently injects only `trace_id`, `span_id`, `request_id`, `operation`, `account_id`, and `user_id`; `RootSpanAttributes.to_log_fields()` deliberately excludes `url_path` and `url_query`. That is the existing baseline for this section, and any newly injected field is subject to 4.2.

However, `ObservabilityContext.extra` is merged into log fields unconditionally, so `extra` is not exempt.

### 4.4 `peer_id`

Never emit it to logs, trace attributes, or audit fields, including four indirect forms:

1. URIs such as `viking://user/{user_id}/peers/{peer_id}/...` embed the peer id;
2. `RequestContext.actor_peer_id`;
3. Object serialization (`%r`, `%s`, `asdict()`, `model_dump()`);
4. Resource locators inside task `meta`.

```python
# Wrong: the full URI exposes the peer
logger.info("rebuild failed uri=%s", "viking://user/u1/peers/p9/memories")

# Right: truncate to the user level, keep only a correlation hash for the peer
logger.info("rebuild failed user=%s peer=%s", user_id, short_hash(peer_id))
```

When the same peer must be correlated across log lines, use an irreversible short hash (`sha256(peer_id)[:8]`). The hash is meaningful only within one deployment; do not reuse conclusions across environments.

### 4.5 Keys and credentials

The following are forbidden, together with any derived form that allows reconstruction (prefix, suffix, length combination):

- API keys, user keys, `submission_token`, `submission_hash`
- OAuth access/refresh tokens, JWT, OIDC assertions
- Cookies, `Authorization`, signature headers, pre-signed URLs
- Credentials inside database or Redis connection strings
- Encryption keys and plaintext entries managed by `ov privacy`

The single exception: when diagnostic output must show a key, use `mask_api_key()` (last four characters only) behind an explicit diagnostic switch.

`repr=False` (`RequestContext.api_key`, `TaskRecord.auth`) only blocks `repr()`; it does not block `asdict()`, `model_dump()`, or `__dict__`. The rule is therefore **never log a whole object**, rather than relying on `repr=False`.

### 4.6 Context content

"Context content" means any text produced by a user, a model, or an external resource that has not been reduced to an equivalent summary:

- prompts, system prompts, messages, tool call arguments and results
- session bodies, memory bodies, summary bodies, rule bodies, skill bodies
- document, web, and code bodies or fragments; retrieved chunk text
- user input and model output

Allowed substitutes: character count, token count, item count, language, file type, `sha256[:8]`, hit/miss.

The existing `log_payloads` switch is the counter-example: `models/rerank/jev_rerank.py:191` and `service/rule_jev.py:76` log the complete request and response payload at **`WARNING`** when enabled, writing queries and document bodies to disk. Direction: default off (already the default), demote to `DEBUG` when enabled, mark `payload=true`, apply redaction, and permit it only in troubleshooting environments.

### 4.7 Paths

Absolute paths are forbidden. They expose the deployment directory, the host user name, and structures such as `workspace/{account_id}/{user_id}/peers/{peer_id}/` — leaking the peer id together with the tenant layout.

Rules:

- Log only a **relative path** inside the workspace, a bare file name, or an extension;
- Never log user-controlled paths verbatim (zip entry names, markdown image paths, upload file names, WebDAV paths); they may contain `../`, absolute paths, or peer segments;
- For correlation, use `relative path + sha256[:8]`;
- Treat `viking://` URIs like local paths and handle the peer segment per 4.4;
- The same applies inside exception messages. If paths cannot be reliably extracted and transformed, omit the raw exception and record its type and a stable error code. The existing `sanitize_error()` does not handle paths and does not establish that the text is safe.

34 log sites currently print `file_path`, `path_str`, or `resolved_path` directly (e.g. `parse/parsers/markdown.py:845`, `parse/parsers/text_encoding.py:74`) and need remediation.

### 4.8 Exceptions and error messages

Exception strings often embed the offending value (`InvalidArgumentError("bad uri: ...")`), so `logger.warning("...: %s", e)` is itself an exfiltration channel.

- Default to `type(exc).__name__`, a program-defined stable error code, and explicitly allowed structured fields; never use `str(exc)` as an error code.
- `task_domain.sanitize_error()` currently replaces only some credential prefixes and truncates length. It does not handle absolute paths, peer URIs, or ordinary `password=` values. It is not a general log sanitizer, and calling it does not authorize exception-text output.
- `sanitize_public_http_error()` handles known credential patterns and size limits, but does not guarantee removal of paths, peers, or context content. Existing helpers provide partial protection and do not replace the field allowlist.
- Emit exception summaries only after scenario-specific safe extraction, redaction, and validation; omit raw exception text until that capability exists. Full stack traces are permitted only at `ERROR` for explicit local troubleshooting, after handling sensitive exception messages, source lines, and paths. Direct `exc_info=True` must not bypass this requirement.
- Never emit `repr(exc.args)` or an untreated exception object.

### 4.9 All three egress paths

| Path | Existing protection | Requirement |
| --- | --- | --- |
| Log file/stdout | Common formatter and `log_safety.py` field allowlist | Call sites still own safe message content; a formatter cannot sanitize arbitrary text |
| OTel trace attributes | `to_log_fields()` already excludes `url_path` and `url_query` | New span attributes obey this section too; keep `url.path` only when needed |
| Audit storage | Partial protection from `sanitize_public_http_error()`, `sanitize_error()`, `SENSITIVE_TASK_KEYS` | Still validate/transform fields under this section; do not assume existing helpers cover paths, peers, or content |

### 4.10 Suggested implementation and checks

- Use `openviking/observability/log_safety.py`: `short_hash()`, `safe_uri_for_log()`, `safe_path_for_log()`, `safe_log_fields()`. Define scenario-specific extraction of allowed fields for exception summaries rather than promising to sanitize arbitrary exception text with a general regex.
- `repr=False` is a fallback, not a line of defence.
- CI: AST-scan logger calls for forbidden identifiers (`peer_id`, `api_key`, `token`, `prompt`, `content`, `messages`, absolute-path variables); warn first, then block.
- Tests: unit-test the redaction helpers; add one failure case each for audit and `log_payloads`.

### 4.11 Security review checklist

- [ ] Every new log field is in the 4.2 allowlist or has been transformed per 4.4–4.8.
- [ ] No `peer_id`, and no full URI that embeds a peer segment.
- [ ] No keys, credentials, tokens, cookies, or connection strings.
- [ ] No prompts, messages, bodies, chunks, or model output.
- [ ] No absolute paths; user-controlled paths are transformed.
- [ ] No `%r` / `%s` / `asdict()` / `model_dump()` of a whole object.
- [ ] `extra` fields are equally compliant.

---

## 5. Fallback, retry, and 4xx rejection logging (`INFO`)

The reader of `INFO` is an operator: they need to know the system is struggling on its own, or that a caller was correctly rejected, without being paged. Every fallback record states **which transition**, **which attempt**, and **what the outcome was**.

### 5.1 Retries and backoff

```python
logger.info(
    "retry operation=%s attempt=%d/%d delay=%.2fs error=%s",
    operation_name,
    attempt + 1,
    retry_limit,
    delay,
    type(exc).__name__,
)
```

- One `INFO` per retry, **never `WARNING`**.
- After retries are exhausted, emit one `ERROR` — exactly one.
- Aggregate batch retries; do not log one line per record.
- During retries, default to the exception type and a stable error code. Summaries and final-failure stack traces still require the explicit troubleshooting and safe handling conditions in 4.8.

### 5.2 Degradation and fallbacks

```python
logger.info(
    "vlm unavailable; using empty summary file_type=%s",
    file_type,
)
```

- Always record **which fallback fired** and **what the degraded behaviour is**; otherwise operators cannot distinguish "by design" from "silently degraded".
- When degradation makes the result incomplete (empty summary, missing fields), mark it explicitly, e.g. `degraded=true`, so downstream can filter.
- Never log a fallback at `WARNING`; that turns `WARNING` back into a "worth noting" bin.

### 5.3 Circuit breakers and state transitions

`INFO` records every state-machine transition: `CLOSED → OPEN`, `OPEN → HALF_OPEN`, `HALF_OPEN → CLOSED`, plus primary/backup switch and failback. `openviking/utils/circuit_breaker.py` already follows this and can serve as the reference.

### 5.4 Startup, shutdown, migration

Process start/stop, one-off data migrations, cache warmup, and background task lifecycle are all `INFO`. Do not flood single-request lifecycles with `INFO`.

### 5.5 4xx business rejections

Bad client arguments, missing resources, and unauthorized requests are always `INFO`:

- Not a fault, so never `ERROR`; otherwise caller misuse floods alerting;
- It reveals a real caller problem (SDK misuse, missing permission, mistyped URI) and must stay diagnosable, so never `DEBUG`;
- It scales with requests and stays bounded, so it does not make `INFO` noisy.

```python
logger.info(
    "request rejected status=%d error_code=%s route=%s",
    400,
    code,
    http_route,
)
```

Constraints:

- One rejection record per request; do not log in both the service layer and the router.
- No `ERROR` and no stack trace for a 4xx.
- The security side of 401/403 (authentication failure, authorization attempt) additionally produces an audit `WARNING` per 3.1; ordinary 4xx (bad arguments, 404) never enters audit.
- The response log and the audit record serve different consumers and may coexist, but one log line must not pretend to be both.

---

## 6. Business process logging (`DEBUG`)

The default outlet. This is the business process of the **success path**; rejected requests belong to `INFO` per 5.5. Requirements:

- Record **the basis for a decision**, not vacuous progress. `"start to process"` / `"done"` carry no information.
- Summarize large objects: URI, counts, lengths, hashes, durations. Never whole documents, whole prompts, or whole response bodies.
- Guard hot paths before building the message:

```python
if logger.isEnabledFor(logging.DEBUG):
    logger.debug("retrieval candidates=%s", summarize(candidates))
```

- Intermediates go to `DEBUG` with a size cap (2 KB recommended); truncate and mark `truncated=true`.

---

## 7. Current state of the repository

Numbers come from the current `main`, scoped to `openviking/`, `openviking_cli/`, and `openviking_gateway/`, excluding `tests/`, `benchmark/`, and `bot/`.

### 7.1 Level distribution

| Package | DEBUG | INFO | WARNING | ERROR | EXCEPTION | CRITICAL |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| `openviking/` | 266 | 385 | **471** | 247 | 88 | 4 |
| `openviking_cli/` | 5 | 6 | 17 | 2 | 0 | 0 |
| `openviking_gateway/` | 1 | 0 | 5 | 0 | 8 | 0 |
| Total | 272 | 391 | **493** | 249 | 96 | 4 |

`WARNING` is the most used level: 26% more than `INFO` and 81% more than `DEBUG`. That is the opposite of "`WARNING` is audit only".

### 7.2 What `WARNING` actually contains

Of 493 `WARNING` calls:

- ~190 mention failure semantics (`fail`, `error`, `unavailable`, `invalid`, `unable`, `exception`)
- ~41 mention fallback semantics (`fallback`, `retry`, `degrade`, `skip`, `ignor`, `disabled`, `missing`)
- about **7** match audit semantics by keyword (`account`, `permission`, `role`, `acl`, `api_key`, `revoke`, `grant`), and those are all failure paths (e.g. "trusted identity batch registration failed; will retry"), not audit events

About 98% of `WARNING` usage should move down to `INFO` or `DEBUG` under this policy, while the audit events that belong at `WARNING` mostly do not exist yet.

Representative anti-patterns:

| Location | Current | Should be |
| --- | --- | --- |
| `openviking/utils/model_retry.py:491` | `WARNING` after a retryable error, with backoff | `INFO` |
| `openviking/models/embedder/base.py:422` | `WARNING` for a slow call (≥3s) | `INFO` |
| `openviking/server/account_stores/redis_mysql.py:485` | `WARNING` when the lock is unavailable and MySQL is read | `INFO` |
| `openviking/server/account_stores/file.py:248` | `WARNING` when the account store reload fails and retries | `INFO` |
| `openviking/storage/queuefs/semantic_processor.py:1357` | `WARNING` when VLM is unavailable and an empty summary is used | `INFO` |
| `openviking/observability/http_observability_middleware.py:557` | `WARNING` for a metrics write failure | `INFO` (`ERROR` on first occurrence) |
| `openviking/server/request_id.py:77` | `WARNING` for a regenerated `X-Request-ID` | `INFO` |

### 7.3 Audit gaps

| Module | Log calls | Note |
| --- | ---: | --- |
| `openviking/server/api_keys/key_mgr.py` | 0 | Account creation, user registration, key regeneration, role setting, and deletion are entirely unlogged |
| `openviking/server/routers/acl.py` | 0 | ACL get/set/delete/grant/revoke are entirely unlogged |
| `openviking/server/routers/admin.py` | 6 | `create_account` and `delete_account` have no audit; all 6 are cleanup-phase exceptions |
| `openviking/server/routers/system.py` | 2 | Identity resolution failure at `WARNING`; it is a fallback |

The problem is not merely "`WARNING` is used in the wrong places" — **the audit capability itself is missing**. That is the highest-priority item.

### 7.4 Implementation baseline before module rollout

The inventory below records the baseline before PR #7769 and the runtime foundation batch. The foundation now uses `INFO` by default, migrates tracer to stdlib logging, unifies Python/root and Rust/C++ output, and disables duplicate Uvicorn access logs in standalone servers. Bootstrap status retains direct console prints. Security-operation audit capabilities remain a separate feature backlog; existing log call-site cleanup continues in later batches.

1. **The default level suppresses `INFO`**: `LogConfig.level` defaults to `WARNING` (`openviking_cli/utils/config/log_config.py:12`) while the fallback in `_load_log_config()` is `INFO` (`openviking_cli/utils/logger.py:684`). Once fallbacks and retries move to `INFO`, default deployments will not see them at all.
2. **Non-lazy formatting**: enabling ruff's `G` rules reports 480 issues — 449 `G004` (f-string logging), 27 `G201` (`.error(..., exc_info=True)` instead of `.exception()`), and 4 `G001`. `pyproject.toml` currently selects only `E/W/F/I/C/B`, so CI does not catch them.
3. **Two logging stacks**: the codebase is stdlib `logging` plus `get_logger()`, but `openviking/telemetry/tracer.py` still uses `loguru`, whose independent output bypasses the common formatter.
4. **Per-runtime configuration**: the Rust binding receives `log.level` and `log.output` through `openviking/utils/agfs_utils.py`, while the C++ engine converts Python formats through `openviking/storage/vectordb/utils/logging_init.py`. Sharing configuration does not unify the final output. Rust still uses its own tracing formatter and maps the `CRITICAL` setting to `ERROR`; format and level mappings lack unified validation.
5. **Duplicate access logs**: `openviking.observability.http` logs one completion record per request (`openviking/server/request_id.py:86`) and `uvicorn.access` logs another; at `log.level=INFO` a single request produces two lines.
6. **`print()` remains**: 334 `print(` calls in library code, mostly on the startup path in `openviking/server/bootstrap.py`, bypassing level control and redaction.
7. **Plain text only**: `_build_standard_handler()` installs a single `logging.Formatter(format_string)` with no JSON option; `request_id` is prepended into the message body (`openviking_cli/utils/logger.py:489`), so collectors cannot split on fields.
8. **No loop-level aggregation**: `local_collection.py` (18 `WARNING` calls) and `semantic_processor.py` (11) log inside per-item paths, so a batch failure floods the log.
9. **Multiple formats in one stream**: Python's default timestamp has no timezone and uses comma-separated milliseconds; Rust emits UTC, microseconds, and `WARN`; loguru uses different separators; startup `print()` calls have no log header. Sending these outputs to systemd or a container collector does not normalize application log formats.
10. **Third-party root logger configuration**: the current MCP dependency calls `logging.basicConfig()` when constructing `FastMCP`. If root has no handlers and Rich is available, it installs `RichHandler`. Records from MCP, APScheduler, and other loggers propagating to root then use short dates, columns, wrapping, and omitted repeated timestamps. `_MANAGED_LOGGER_ROOTS` currently covers only `openviking`, `openviking_cli`, and `uvicorn`, leaving this output unmanaged.

---

## 8. Additional rules beyond the level mapping

The level mapping alone is not enough to land this policy. The following rules cover the gaps.

### 8.1 Log once

Log once at **the boundary that decides the final outcome**: the HTTP response boundary, background-task terminal state, or startup entry point chooses the outcome and level. Intermediate layers propagate or wrap exceptions without prematurely recording a final failure. A layer that catches an exception and completes a fallback logs `INFO`; a retry records the attempt and backoff, not a final `ERROR`. If an intermediate layer returns an error object, the boundary that consumes it and decides the outcome still owns the record.

```python
# Wrong: an intermediate layer logs early and the boundary logs again
except Exception as exc:
    logger.exception("compile tag validation failed")
    raise OpenVikingError(...) from exc

# Right: raise in the middle, log at the boundary
except (ConfigPatchError, ValueError) as exc:
    raise InvalidArgumentError(str(exc)) from exc
```

The boundary records a 4xx rejection, fallback, or final failure under sections 1 and 5. If the framework requires that boundary to re-raise, explicitly ensure that later handlers do not record the same failure again. Exception fields and stack traces still obey 4.8.

### 8.2 Lazy formatting

Python stdlib logging uses placeholders such as `%s`, never f-strings or `.format()`:

```python
# Wrong
logger.warning(f"Failed to resolve identity: {e}")
# Right
logger.info("identity resolution failed error_type=%s", type(e).__name__)
```

Placeholders avoid string-formatting work when the level is disabled and preserve the original message template for aggregation. Lazy formatting is not redaction: forbidden values must not be emitted through either f-strings or `%s`. Other runtimes use their own lazy or structured logging interfaces.

### 8.3 Prefer structured fields

Put dimensions that need search, aggregation, or alerting in `extra`, not only in the message:

```python
logger.info(
    "HTTP request completed method=%s route=%s status=%d duration_ms=%.1f",
    method, route, status_code, duration_ms,
    extra={"http_method": method, "http_route": route,
           "http_status_code": status_code, "duration_ms": duration_ms},
)
```

Canonical field names: `request_id`, `trace_id`, `account_id`, `user_id`, `operation`, `http_route`, `duration_ms`, `result`, `degraded`. Audit fields use the complete schema in 3.2 without aliases such as `actor`, `account`, or `route`.

### 8.4 Sensitive data

Section 4 is the single source of truth for field-level rules. Use `mask_api_key()` only for the explicit diagnostic display allowed in 4.5. See 4.8 for the limits of `sanitize_public_http_error()` and `sanitize_error()`; calling them does not make arbitrary exception text safe to emit. Apply the same field rules to logs, traces, and persisted audit records.

### 8.5 Noise control

- Loops and batches: one summary plus `DEBUG` detail, with `total` / `failed` / `skipped` in the summary.
- Keep one `ERROR` for a final failure at the deciding boundary; repeated diagnostic detail may use `DEBUG`. Deduplication must not promote audit `WARNING` or fallback `INFO` to `ERROR`.
- Configure third-party thresholds centrally at the server logging entry point, not with `setLevel` in business callers. Do not bulk-rewrite their level semantics to reduce noise; see 8.10.6.
- High-frequency paths (per request, per chunk, per candidate) default to `DEBUG` and guard with `isEnabledFor`.

### 8.6 Record size

Keep a message under 2 KB and structured field values under 1 KB. Truncate and mark `truncated=true` with `original_length=<n>`. Never `%r` a large object or a full exception context.

### 8.7 Message style

- English only, so cross-team greps and English docs line up.
- Lowercase start, no trailing period, no "Oops" or "Something went wrong".
- Identify the source through `logger` in the log header; messages no longer need a handwritten `[module]` prefix. Existing prefixes can be removed gradually; stable event or operation names may remain.
- Keep templates stable and put only variables in placeholders, otherwise aggregation explodes into discrete values.

### 8.8 What not to log

- No function enter/exit tracing; use tracing or a profiler.
- No logging for its own sake: every record answers at least one of "what happened", "who is affected", "what to look at next".
- Do not let a log line be the only evidence of a business decision; record important branches in metrics or the event bus.

### 8.9 Tests

- Assert **levels and structured fields** with `caplog`; do not assert full message text.
- Cover audit paths explicitly, at least success and failure.
- Do not mock-assert that a logger was called.
- Format and integration tests must inspect final output streams, including third-party and native components; `caplog` cannot replace the acceptance checks in 8.10.7.

### 8.10 Common output format and initialization

Bootstrap progress, dependency checks, startup results and failure diagnostics use direct `print(..., flush=True)` output so operators can see them regardless of `log.level` or file logging. Errors go to stderr; other status messages go to stdout. This console output still obeys the content rules in section 4, and is checked separately from formatted runtime logs.

The following target requirements cover server logs from Python, Rust, C++, TypeScript, third-party dependencies, and child-process output merged into server logs. See 8.10.4–8.10.5 for standalone CLI and SDK host boundaries. Convert formats in initialization and adapter layers; business code must not assemble log headers.

#### 8.10.1 Time

- Use UTC and ISO 8601 with the fixed format `YYYY-MM-DDTHH:mm:ss.SSSZ`, for example `2026-11-13T22:56:01.028Z`. Convert to UTC before emitting `Z`; never append `Z` to local time.
- Use the event's creation time, captured before asynchronous queueing, rather than write or collection time. Normalize higher native precision to milliseconds at output.
- Apply this during startup, operation, and shutdown, independently of host timezone, locale, or terminal width. Do not mix comma-separated milliseconds, short dates, or timezone-less timestamps, and do not omit timestamps on consecutive events.

#### 8.10.2 Level names and mapping

Final output uses only uppercase `DEBUG`, `INFO`, `WARNING`, `ERROR`, and `CRITICAL`. **Configuration threshold mapping and output name normalization are separate requirements and must be checked separately**: accepting `WARNING` does not mean that output no longer says `WARN`.

| Native level or alias | Output name | Constraint |
| --- | --- | --- |
| `debug` / `DEBUG` | `DEBUG` | Normalize case |
| `info` / `INFO` | `INFO` | Normalize case |
| `warn` / `WARN` / `warning` / `WARNING` | `WARNING` | Do not retain the `WARN` abbreviation |
| `error` / `ERROR` | `ERROR` | Preserve event semantics |
| `fatal` / `FATAL` / `critical` / `CRITICAL` | `CRITICAL` | Map an existing fatal level, never promote an ordinary error |
| `trace` / `TRACE` | `DEBUG` | Normalize native TRACE only when explicitly enabled; this table does not enable it by default |

- Validate and normalize configuration centrally before passing it to each runtime. Document supported configuration aliases explicitly and reject unknown values; components must not independently fall back to different levels.
- Rust tracing has no native `CRITICAL`. The current mapping of a `CRITICAL` threshold to `ERROR` is lossy and cannot select only fatal events; document this limitation. A boundary capable of emitting `CRITICAL` should record events that prevent the process from continuing. Never rename every Rust `ERROR` to `CRITICAL`.
- Normalizing output names does not reclassify third-party events; see 8.10.6.

#### 8.10.3 Log header and fields

Text output has the fixed order `ts level logger pid request_id trace_id message`, followed by additional structured fields. For example:

```text
2026-11-13T22:56:01.028Z INFO openviking.server.bootstrap pid=452394 request_id=- trace_id=- message="server started"
```

- `logger` is the full Python logger name, Rust target, or stable component name; `pid` identifies the process that created the event. Do not hide the source only in a message prefix.
- Nullable fields use `-` in text and `null` in structured events or future JSON output. This includes unavailable `request_id` and `trace_id`; do not invent request identifiers for startup or background events.
- Quote and escape `message` and appended string values using JSON string rules; preserve numeric and boolean types. Newlines, carriage returns, and control characters must not break record boundaries. All values still require the security handling in section 4 before encoding.
- Inject correlation fields once in the common adapter, without duplicating them inside the message. Explicitly register source files and line numbers as optional fields under 4.2 if needed; paths still obey 4.7.
- The current adapters accept only the common format and normalize the former built-in format on load. Other custom formats fail validation. Any future custom `log.format` support must retain these required fields and the time and level conventions. Report unsupported cross-runtime conversions explicitly rather than silently reverting to component defaults. A future JSON formatter must use the same field names and semantics, not a separate schema.

#### 8.10.4 Initialization and output ownership

- The server entry point owns logging configuration. Install minimal common output before loading dependencies that configure logging or creating components, and retain the same format contract after loading configuration. Report bootstrap configuration-loading failures directly to stderr with `print(..., flush=True)`.
- Integrate Python root and managed loggers, including at least Uvicorn, MCP, and APScheduler. Define handler ownership and propagation so each event appears once per destination; audit dual writes and OTel export are separate destinations.
- Business modules must not install their own `basicConfig`, RichHandler, or formatter. Handle dependency initialization side effects in the integration layer so later components cannot change output formats or thresholds.
- Migrate owned loguru calls to `get_logger()`. If retained temporarily, bridge them in one direction to the common output and remove duplicate sinks. Rust/C++ must implement the same contract in native adapters; replacing `%l` / `%(levelname)s` alone does not establish format consistency.
- Initialize each worker separately. Repeated initialization and configuration reloads must not accumulate handlers, duplicate output, or lose correlation fields. Explicitly require a restart for native settings that cannot update live; updating Python alone must not be presented as a global change.
- Managed child processes retain their own PID and component name. Output merged into the same stream must follow the same contract; unadapted output stays in clearly identified separate log files and must not appear as parent-process events.
- When embedded as an SDK, do not clear or reconfigure the host's root logger. Global ownership belongs only to the standalone server entry point; an embedding host explicitly chooses its integration.

#### 8.10.5 Non-interactive output and `print()` boundaries

- Server stdout/stderr, files, systemd, and container collection output use UTF-8 without ANSI colors, Rich columns, automatic wrapping, progress bars, or omitted repeated timestamps. Each event occupies one physical line; wrapping by a viewer is not application output.
- Redact permitted stack traces under 4.8, then escape newlines within the same record. Do not emit bare continuation lines that cannot be attributed to an event. JSON mode likewise uses one object per line.
- Bootstrap progress, dependency checks, startup results and bootstrap-managed process diagnostics retain direct `print(..., flush=True)` output. Other runtime logs use the common logger. Console messages still require safe content; raw credentials, exception text and child-process bodies must not be printed.
- Interactive CLI help, command results, and progress displays may use dedicated terminal rendering; these are not server runtime logs. When stdout carries machine-readable results or a stdio protocol, diagnostic logs must go to stderr without contaminating that stream.

#### 8.10.6 Third-party semantics and audit isolation

- The purpose-based level rules in sections 1–3 apply to owned code. Third-party dependencies are not required to reserve `WARNING` for audit; an APScheduler missed-execution warning remains an operational warning.
- Preserve third-party level semantics and logger names, normalizing only timestamps, name spelling, fields, and output handling. Do not bulk-demote third-party `WARNING` to `INFO` to satisfy the audit convention.
- Control noise through centrally configured logger thresholds and explicit targeted rules. Rules that can hide third-party failures need focused validation. Do not pass format acceptance by disabling third-party logs.
- Identify audit records through the dedicated logger and explicit audit event fields under 3.2–3.3. Third-party `WARNING` records neither enter audit nor replace audit events that owned code must produce.

#### 8.10.7 Acceptance checks

For changes to logging initialization or adapters, capture actual stdout/stderr or log files and verify at least:

- Under UTC and Asia/Shanghai environments, a fixed event time produces the same UTC timestamp with identical precision and format.
- Formatted runtime logs for normal requests, exceptions, shutdown, Python, MCP/APScheduler and Rust/C++ satisfy the required fields and single-line contract. Check bootstrap startup/configuration-failure messages separately: they remain visible on stdout/stderr under high log thresholds and file output.
- Non-TTY redirection contains no ANSI sequences, Rich columns, omitted timestamps, or bare continuation lines. Components honor thresholds such as `INFO` and `WARNING`, with dedicated cases for native level-mapping limitations.
- Repeated initialization, reloads, and multiple workers produce each event exactly once per destination, with the correct PID and no request-context leakage into other requests.
- A third-party `WARNING` remains operational while an `openviking.audit` event reaches audit; the two are not confused.

Assert fields, formats, and record counts rather than complete business messages. Native components and child processes require verification of their actual output, not just assertions that a Python logger was called.

---

## 9. Rollout

Submit module groups in order and refresh from upstream before starting each group. Batches 3 and 4 share one PR; subsequent batches start after the preceding group merges.

| Batch | Scope | Status |
| --- | --- | --- |
| 1 | Model logs and safe payload summaries | Merged in #7769 |
| 2 | Logging initialization, observability logs, Python/Rust/C++ output | Merged in #7778; C++ compatibility fixes in #7794 |
| 3 | Existing authentication, account, ACL, and ROOT-related logs | Merged in #7806 with batch 4; adds no audit capabilities |
| 4 | Existing business service logs | Merged in #7806 with batch 3 |
| 5 | Parsing, ingestion, and connectors | Merged in #7899 |
| 6 | Storage and queues, including Watch | Merged in #7933 |
| 7 | Retrieval, core, and utilities | Merged in #7937 |
| 8 | Sessions, memory, compilation, CLI, and Gateway | Implemented; independent PR |

The priority inventory below is the original policy backlog. Apply existing-log remediation within the module batches above; new audit capabilities remain outside this rollout.

| Priority | Item | Scope | Note |
| --- | --- | --- | --- |
| P0 | Add audit events (accounts, membership, roles, ACL, API keys, config, deletion) | New code, no reclassification | Covers the 7.3 gaps; highest value, lowest risk |
| P0 | Demote `log_payloads` full payloads from `WARNING` to `DEBUG` and force redaction | 2 sites (`jev_rerank.py`, `rule_jev.py`) | Enabling the switch currently writes queries and bodies at `WARNING`, violating 4.6 |
| P0 | ROOT invocation audit: mark in `_build_request_context`, emit one `WARNING` on completion | One choke point | `api_key` mode only; dev/trusted and internal contexts never trigger, see 3.5 |
| P0 | Decide the default log level | One field plus docs | If `WARNING` stays, document that fallbacks/retries are invisible by default |
| P0 | Add the `openviking.audit` logger and a `log_audit()` helper | Prerequisite for audit integration | Centralize schema, field transformations, event projection, and dual-write failure reporting; verify persistence before integrating business events |
| P1 | Enable ruff `G` rules | CI | Scope existing violations with `per-file-ignores`, then burn down 480 |
| P1 | Add `log_safety.py` (`short_hash`, `safe_uri_for_log`, `safe_path_for_log`, `safe_log_fields`) | New module | Single entry point for the transformations in 4.4–4.8 |
| P1 | Unify server logging initialization and final output | Python/root, Uvicorn, MCP/APScheduler, tracer, Rust/C++ | Implement 8.10, including visible bootstrap console output, tracer loguru migration/bridging, and actual-output acceptance; do not wait for repository-wide cleanup |
| P2 | Demote `WARNING` per 7.2 | ~190 sites | Batch by module, one regression test per batch |
| P2 | Aggregate loop logging | ~30 files | Summary plus detail |
| P2 | Remediate path logging (34 sites) and indirect `peer_id` leakage | ~34 sites | Relative path plus short hash |
| P3 | De-duplicate access logs | 2 sites | Decide owner: `uvicorn.access` or `observability.http` |
| P3 | Clean up remaining runtime-log `print()` calls | Remaining sites across the repository | Retain bootstrap console output; distinguish interactive CLI display and protocol output under 8.10.5 instead of replacing mechanically |
| P4 | Optional JSON formatter | 1 handler | For field-based collection |

### PR checklist

- [ ] Every new log line matches the level semantics in section 1; no fallback or retry at `WARNING`.
- [ ] Security-sensitive actions go through `log_audit()`, record both outcomes, and include all fields.
- [ ] No f-string or `.format()` logging.
- [ ] Final failures are recorded once at the deciding boundary; intermediate layers do not log prematurely or duplicate stacks, and fallbacks/retries use `INFO`.
- [ ] Audit fields are classified under 4.2 and examples/calls use the complete 3.2 schema; arbitrary exception text is not emitted merely after passing it through existing sanitizers.
- [ ] No keys, tokens, bodies, or full payloads in logs.
- [ ] No `peer_id`, no full URI embedding a peer segment, no absolute paths (4.4, 4.7).
- [ ] Endpoints reachable by ROOT are confirmed to hit the 3.5 audit rather than only `INFO`.
- [ ] Walked the security checklist in 4.11.
- [ ] Loops and batches aggregate instead of flooding.
- [ ] Messages are English and template-stable, with the source identified by the header's logger.
- [ ] Server output meets 8.10: UTC millisecond timestamps, common level names, required fields, and one physical line per event.
- [ ] No new independent handlers, Rich output, or runtime-log `print()` calls; initialization and reloads do not duplicate records.
- [ ] Third-party semantics are preserved; audit routing uses the dedicated logger and event fields rather than all `WARNING` records.
- [ ] Logging initialization or cross-runtime adapters have actual-output acceptance coverage under 8.10.7.

---

## 10. Quick reference

The level purposes below apply to owned code; see 8.10 for third-party semantics and the common output contract.

```text
Audit (permissions/accounts/keys/config/deletion)  → WARNING  openviking.audit
Failure that needs a human                         → ERROR
Retries, backoff, fallback, circuit breaker, failback → INFO
4xx rejections (arguments/missing/unauthorized)    → INFO
Any ROOT call in api_key mode                  → WARNING  openviking.audit
Business process, intermediates, success detail    → DEBUG
Process cannot continue                            → CRITICAL
```

```text
Allowed:       account_id, user_id, request_id, trace_id, task_*, durations, counts, error codes
Transform:     local path → relative path; URI → strip peer segment; exception → type + stable error code + allowed fields
Never emit:    peer_id, keys and credentials, context content, absolute paths, full payloads
```

```python
from openviking_cli.utils.logger import get_logger

logger = get_logger(__name__)

logger.debug("retrieval candidates=%d level=%s", len(candidates), level)   # process
logger.info("retry operation=embed attempt=%d/%d delay=%.2fs", n, limit, d) # fallback
logger.error("ingest task failed after retries task_id=%s error_code=%s", task_id, error_code)
```
