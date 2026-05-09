# API Response Changelog

Running log of response-shape changes introduced by the typed-response
rollout. Each entry is framed for downstream consumers (SDK codegen,
frontend, external integrations) rather than server-side implementers.

The contract guarantees in this log are narrower than a full semver
bump:

- **Preserved across every PR**: HTTP method, path, request parameters,
  status codes, response field *names* and *types*.
- **Not preserved**: `null` vs absent-key for Optional fields, OpenAPI
  schema diffs (strictly additive — go from `any` to concrete types).

If your client reads with `if obj.field` / `obj.field ?? default` you
are unaffected by any entry below. If you rely on `"field" in obj`
existence checks, read the impact rows.

---

## PR #1 — sessions / content / search / bot (21 endpoints)

**Null field omission** — the following endpoints stop emitting keys
whose value is `null`. Numeric `0`, empty string `""`, empty list `[]`,
and `false` are preserved as-is — only `null` values are affected.

| Endpoint | Fields that may disappear |
|----------|---------------------------|
| `GET /api/v1/sessions/{id}` | `memories_extracted`, `last_commit_at`, `llm_token_usage`, `embedding_token_usage` — unset during early session lifecycle |
| `GET /api/v1/sessions/{id}/context` | `latest_archive_overview`, `stats` |
| `POST /api/v1/sessions/{id}/extract` | On memory/resource contexts: skill-only fields (`name`, `description`, `tags`); on any context: unset optional fields (`parent_uri`, `vector`, `meta`, `level`, `user`) |

**Already-idempotent endpoints** (used `.model_dump(exclude_none=True)`
prior to the PR — no observable change):

- `POST /api/v1/sessions/{id}/commit`
- `POST /api/v1/content/write`
- `POST /api/v1/search/find`
- `POST /api/v1/search/search`

**Polymorphic response type** (new explicit Union in OpenAPI):

- `GET /api/v1/content/read` — `result` is typed as `string | object`
  where the `object` variant carries a parsed memory JSON document
  emitted by `deserialize_content`. Branch on runtime type:
  `typeof result === 'string'` in TS, `isinstance(result, str)` in
  Python.

**Bot proxy endpoints** — untouched null behavior (proxy preserves
upstream):

- `GET /bot/v1/health`
- `POST /bot/v1/chat`

**Forward-compat safeguard** — every model that wraps historical
service-layer `dict` output (for instance `CommitResult`,
`ContextItem`, `SearchResult`) sets `extra='allow'` so any field added
server-side in the future reaches the client even before the schema
is updated.

---

## PR #2 — resources / filesystem / relations / pack (13 endpoints)

**Null field omission**:

| Endpoint | Fields that may disappear |
|----------|---------------------------|
| `GET /api/v1/fs/stat` | `meta`, `tags`, `abstract`, `rel_path`, `mode` when unset — AGFS may or may not populate them depending on the resource type |
| `GET /api/v1/fs/ls`, `GET /api/v1/fs/tree` | Per-entry: same as `/stat` above |
| `GET /api/v1/relations` | Future optional fields on `RelationEntry` (currently only `uri` / `reason`, both required) |
| `POST /api/v1/resources` | `warnings`, `temp_uri`, `queue_status` — populated only under specific upload conditions |
| `POST /api/v1/skills` | `queue_status` — populated only when `wait=True` |

**Already-idempotent endpoints** (used `.model_dump(exclude_none=True)`
prior — no observable change):

- `POST /api/v1/resources/temp_upload`
- `POST /api/v1/resources`
- `POST /api/v1/skills`

**Alias-backed field**: `mv` and `link/unlink` responses contain a
`"from"` key. Client code may safely read it as `response.result.from`
(TS property access) or `response["result"]["from"]` (Python dict).
There is no `"from_"` key — the alias is the wire format.

**Polymorphic list response** (`FSListResult` in OpenAPI):

- `GET /api/v1/fs/ls` and `GET /api/v1/fs/tree` — when `simple=true`
  the `result` is a list of URI strings; otherwise a list of detailed
  `FileStat` objects. Branch on the first element type if the client
  needs to handle both modes.

**New trivial wrappers**: `URIRef` (`{"uri": str}`) for mkdir / rm /
pack-import; `FromTo` for mv / unlink; `LinkResult` for link.

---

## Convention for future PRs (PR #3+)

Each follow-up PR that enables `ExcludeNoneRoute` on a router must add
an entry here with:

1. The router and endpoint list.
2. A "Fields that may disappear" table for any Optional response
   field — even if zero fields are currently affected, document that
   explicitly so SDK maintainers know the invariant.
3. A note on whether the endpoint was already-idempotent (no
   observable change) or newly-affected.
