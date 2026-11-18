# Workflow observations

The opt-in Console workflow view queries retained observations from the existing
Usage/Audit event bus and writer. It does not replay model requests. It shows
model usage, execution stages and known collector losses without presenting a
complete provider bill or inferring an unobserved critical path.

## Enable

```json
{
  "server": {
    "observability": {
      "usage_audit": {
        "enabled": true,
        "performance_enabled": true,
        "performance_retention_days": 14,
        "backend": "postgres",
        "postgres": {
          "dsn": "postgresql://user:password@localhost/openviking",
          "schema_name": "openviking_usage_audit"
        }
      }
    }
  }
}
```

`performance_enabled` defaults to `false`; retention defaults to 14 days (1–366).
Existing queue, model, retry, concurrency and Usage/Audit retention defaults are
unchanged. The default backend remains SQLite. Use a shared PostgreSQL database
and the same dedicated schema for multi-instance collection; selecting a backend
does not migrate existing history. PostgreSQL requires the `postgres` extra.

The independent Bot gateway reads this same explicit configuration and starts
the same Usage/Audit owner only when performance collection is enabled. Compile
uses its existing authenticated connection and Task ID to bind diagnostic Task
context. Stages do not supply authorization or add model calls. A standalone Bot
using a separate local SQLite file is not visible in another server's Console.

The feature adds versioned observation/receipt/producer tables alongside existing
projections. Existing historical rollups are preserved, not backfilled into
invented model attempts or workflows. Unsupported future observation versions
fail closed. When switching on or off, coordinate writers: an old or disabled
writer does not participate in the new receipt protocol.

## Query

`GET /api/v1/console/performance` accepts:

| Parameter | Meaning |
| --- | --- |
| `start`, `end` | Timezone-aware ISO instants, half-open range, at most 31 days |
| `view` | `summary` (default), `series`, `workflows`, `details` |
| `workflow_id`, `task_id`, `request_id` | Optional exact observation locators |
| `user_id` | Optional user filter for account administrators; users cannot query another user |
| `model`, `status` | Optional exact filters |
| `page_size` | 1–100, default 50 |
| `cursor` | Next-page cursor, bound to the principal and filters |
| `observed_before` | Ingestion boundary returned by a prior list, for matching detail reads |

Account scope always comes from authentication. USER queries are forced to the
same user; ADMIN/ROOT remain within the selected account. A matching workflow ID
in another account/user never supplies access or gets merged into this user's
workflow. IDs are correlation metadata, not identity credentials.

A snapshot uses a serialized, monotonic per-account ingestion clock, rather than
an event-time cutoff. Late arrivals cannot enter an existing page sequence even
when their event timestamp is older. The public API does not expose global
sequence numbers. Retention or an explicit purge can still remove rows; the
boundary is not an archival database snapshot.

Reads materialize at most 20,000 observations per query. `coverage=truncated`
means totals cover only that bounded subset: narrow the time range/filters.
`coverage=retention_limited` means the request extends before `retained_after`.
Otherwise `coverage=observed` refers only to the retained observation set, never
proof that every real operation was captured.

## Usage and time semantics

`summary.token_totals` exposes input, output, cached-input and reasoning-output
separately. Each has `known_tokens` and `unknown_events`. Missing fields stay
unknown; a supplied zero stays zero. Cache/reasoning are subsets and are never
added to input/output. Cache hit rate uses only actual-report observations with both cached
input and its input denominator known, and reports the covered event count.

`usage_quality`, known fields and completeness retain provider/producer facts.
`reported_token_totals`, `estimated_token_totals` and `unverified_token_totals` remain separate; the known subtotal must not be presented as a reported bill. Incomplete reports have a separate event count even when some fields are already known. Legacy numeric observations can have unverified provenance; they are not
retroactively relabelled provider-reported. Explicit unknown usage and unverified
provenance have separate counts. `attempt_observations` describes tagged provider
invocations; an invocation can fail before any bytes are sent. Adapter calls and
legacy observations do not prove physical RPC coverage.

A shared carrier is counted once and is not assigned to an inherited first Task.
This view does not invent per-Task token allocations when a batching owner has
not supplied a valid allocation contract. Embedding scheduling and allocation
are separate features.

`duration_by_kind` keeps Task, HTTP, queue, model and individual stage names
separate. `observed_span_seconds` is cumulative span work, which can overlap;
do not add nested stages/layers to obtain wall-clock latency. The union of known
started intervals is reported separately from their cumulative durations. Neither
is a critical path or a complete latency measurement. Queue wait is an estimate of end-to-end minus processing time only when wall clocks are comparable; missing timestamps or rollback leave it unknown. Cross-Pod clock skew can affect this estimate. Stage events contain safe
names, IDs, timestamps and outcomes, not prompts, output text or error bodies.

## Delivery, receipts and limitations

When enabled, event receipt claims, observation inserts and existing usage/audit
projection writes share the same existing transaction. Re-delivery of the same
event ID/body does not add usage again. A conflicting reuse fails the batch
rather than silently changing the old fact. Cross-account/user/event-type keys
are isolated. Failed transactions roll back the receipt with the projection.
No new retry loop is introduced for ambiguous writes.

Detailed observations use `performance_retention_days`. Receipt acceptance lasts
at least as long as all enabled projection retention windows. If usage or audit
retention is unlimited, receipts are retained without age expiry too; choose
finite windows if bounded receipt age is required. A persisted acceptance
watermark prevents an expired event being counted again after clock rollback.
Shorter detail retention never truncates a longer legacy usage window.

`collection.producers` contains scoped delivery counters from observed producer
lifetimes: accepted, persisted, dropped, failed, uncertain and pending. Deliveries
are not distinct RPCs. Clean shutdown is recorded for observed producers. A crash
before a producer's first successful flush can leave no producer record; failures
to persist health and overflow of the bounded in-memory principal tracker also
remain possible. `unobserved_producers_possible=true` and `billing_complete=false`
always remain explicit. Counter periods differ from the selected event range.

The existing event bus and bounded writer are best effort. Closing a worker does
not magically recover events that were never admitted or durably received. No
complete accounting, exactly-once model execution or universal provider coverage
is promised. The Studio panel shows these limits, unknown fields, scoped
workflows/details, covered cache metrics and explicit truncation, and handles an
older/disabled server without applying another connection's cached state.
