# Optional OpenViking MCP tools

Tools listed here are not part of the core set: whether they exist depends on
the server version and hosting mode. Only read the sections for tools that
actually appear in this session's registered tool list, and never call one
that is absent — SKILL.md's core loop works without any of them.

Availability summary:

| Tool | Server requirement | Managed cloud service |
|---|---|---|
| `tree` | ≥ 0.4.14 | after the cloud rolls to 0.4.14 |
| `write`, `edit` | ≥ 0.4.14 | after the cloud rolls to 0.4.14 |
| `list_watches`, `cancel_watch` | ≥ 0.3.18, self-hosted / private | not exposed |

The managed cloud runs stateless multi-instance serving, so account-level
stateful tools (`list_watches`, `cancel_watch`) are trimmed there even when
the underlying version has them.

## `tree(uri, level_limit?)`

Directory tree of a `viking://` scope, deeper than one `list` level. Use it to
orient inside an unfamiliar scope before deciding what to `read`; prefer
`list` for a single known directory — it is cheaper.

## `write(uri, content, mode?)` and `edit(uri, ...)`

Exact-document persistence at a known URI, complementing `remember` (which
lets the server file extracted memories on its own):

- `write` replaces, appends to, or creates a file. Creating requires the
  parent directory to exist.
- `edit` performs targeted string replacement inside an existing file. Prefer
  `edit` over rewriting whole files, and re-`read` the file first if your copy
  of its content might be stale.

Use them for curated notes under `viking://~/` (your own user root) and shared
reference material under `viking://resources/`. When neither is registered,
fall back to `remember` as SKILL.md describes.

## `list_watches()` and `cancel_watch(to_uri)`

Manage auto-refresh subscriptions created by `add_resource` with a watch
interval (private / self-hosted deployments only). `list_watches` shows the
account's active subscriptions; `cancel_watch` stops one by its target URI.
Only touch watches the user asked about — cancelling someone's subscription is
destructive.
