---
description: What OpenViking Gateway stores, who can reach it, what providers see, and how data is kept, deleted and backed up.
---

# OpenViking Gateway security and data

This page is for operators and security reviewers approving a gateway deployment, and for users who want to know what the gateway keeps about them. Sessions and memories always live in OpenViking under its own rules; the gateway keeps only its settings, the state it needs to continue conversations, and metadata-only request logs, in local SQLite by default or in configured Redis storage. Each section below answers one review question and can be linked on its own.

## Isolation by user {#isolation-by-user}

Each [gateway key](../guides/15-gateway.md#key-terms) is bound to one OpenViking user. The gateway searches memory, saves conversations and calls tools with that user's own OpenViking key, so it can never do more than the user can. Each user's conversation state is kept separate from every other user's. With `tenancy: key`, different OpenViking keys also have separate partitions when their account and user identities match; rotating the key starts a new partition.

## What the gateway stores {#what-the-gateway-stores}

By default, data is in two SQLite databases in `storage_path` (default `~/.openviking/gateway`). Either storage port can use [Redis](13-configuration.md#storage) instead:

| File | What it holds |
| --- | --- |
| `management.sqlite3` (or Redis management) | Upstreams, context profiles, gateway keys, request logs when using the default `store` sink, and which upstream created each Responses response |
| `kernel.sqlite3` (or Redis kernel) | The state of each conversation: the memory text added to messages, OpenViking tool rounds and their results, summaries, conversation text waiting to be saved, and the reasoning of model replies when **Restore reasoning the client drops** is on |

Some of this is sensitive:

- `management.sqlite3` holds upstream API keys, header values and the OpenViking keys bound to gateway keys. Gateway keys themselves are stored only as a hash and a short prefix.
- `kernel.sqlite3` holds memory text word for word, because the gateway needs it to [replay](01-how-it-works.md#replay) what it added in earlier turns.

Only the system user that runs the gateway can read SQLite files. Redis access is controlled by its network and ACL configuration. With [encryption](../guides/22-gateway-operations.md#encryption) on, every stored value is encrypted as well; with it off, the credentials above are in plain text, protected by file permissions or Redis access controls. All replicas must mount the same encryption root key from a Secret; a missing entry on a read-only mount prevents startup. Redis keys contain hashes, but indexes can contain original account, object and session identifiers; use TLS as well as value encryption.

## What request logs record {#what-request-logs-record}

Request logs hold metadata only, never content:

| Recorded | Never recorded |
| --- | --- |
| Request type, model, status, upstream | Message text |
| A hash of the conversation ID | Recalled memory |
| Token counts, memory counts and timing | Tool arguments or results |
| Saving status and issues | Any key |

The gateway writes no HTTP access log, and its error messages never repeat submitted values. The default `store` sink makes request logs available in Studio; see [Requests](10-overview-and-requests.md#requests). The `stdout` sink emits the same metadata with account and user identifiers as plain JSON; storage encryption does not apply to stdout. Log collection and access are managed by your deployment; see [Monitoring](../guides/22-gateway-operations.md#monitoring).

## What providers see {#what-providers-see}

Every [upstream](../guides/15-gateway.md#key-terms) receives the full model input, including memory the gateway added and OpenViking tool results. Only add providers you trust with that data.

Some things never leave the gateway: `X-OpenViking-*` headers, client credentials and cookies are never forwarded to upstreams. When an upstream takes each client's own provider key, that key reaches the provider only as the upstream's credential.

## Access control {#access-control}

Four things grant access through the gateway, and each has a limit worth knowing:

| Credential | What it allows | What to watch |
| --- | --- | --- |
| [Gateway key](09-gateway-keys.md#gateway-keys), sent in `Authorization: Bearer` or `x-api-key` | Model requests as the bound user: recall memory, save conversations, call OpenViking tools | Can be revoked at any time on the **Keys** tab |
| [Gateway management key](../guides/22-gateway-operations.md#gateway-management-key) | Only changes to upstreams and context profiles | Grants no access to memory or logs and no right to issue keys |
| An account admin's right to issue keys | Issuing gateway keys for any user in the account | Amounts to reading the memory of every user in the account |
| One-time token in an upload or download link | Calls to `/gateway/uploads` and `/gateway/downloads`, which need no gateway key | The token travels in the query string |

Two of these need care:

- **Issuing keys.** A gateway key lets its holder recall memory and call tools as that user, so give the right to issue keys only to admins you trust. When OpenViking stores only key hashes, the user has to supply their own OpenViking key.
- **Upload and download links.** Each link is authorized by a one-time token signed by OpenViking, and the gateway only forwards it to OpenViking's upload or download endpoint. Keep their query strings out of proxy logs.

The full rules, including the management API, are in the [access matrix](../api/25-gateway.md#credentials-and-account-scope). What each role can do in Studio is in [Who can change what](06-manage-in-studio.md#who-can-change-what).

## Retention {#retention}

Gateway data expires on its own. SQLite runs an hourly cleanup job; Redis uses key TTLs:

| Data | Kept for | Setting |
| --- | --- | --- |
| Conversation state | 30 days after last use | `session_ttl_seconds` |
| Cached OpenViking keys and their user records | Configured TTL from creation, without renewal | `session_ttl_seconds` |
| Stored request logs | 30 days | `log_retention_days` |
| Which upstream created each Responses response | 30 days | `response_ttl_seconds` |
| Sessions and memories in OpenViking | OpenViking's own rules | — |

When an OpenViking key record expires, or is evicted by a Redis backend using `volatile-lru`, capture pauses with `gateway_key_revoked` because it cannot read the stored credential. The user's next request recreates the record and resumes capture in the same scope. If the user never returns, the last pending turn is not saved to OpenViking. This state is a cache and can be lost. The existing short process-local cache can delay visibility of expiry, like revocation.

Redis keeps at most 10,000 request-log entries per account. Reads skip entries older than `log_retention_days`; those entries stay stored until newer ones push them past the cap, or until the account logs nothing for `log_retention_days`. Redis needs no hourly cleanup. Redis Responses routing objects expire by TTL and are not scanned during user deletion; revoked keys cannot access them.

Minimums and where to set these are in [Gateway fields](13-configuration.md#gateway-fields). Retention and deletion of stdout logs are controlled by the log collector, independently of gateway cleanup.

## Redis conversation state {#redis-conversation-state}

With `storage.kernel: redis`, conversation state is a cache with a TTL of `session_ttl_seconds`. Writes renew it; reading a record alone does not.

Eviction has observable effects:

| Lost state | Effect |
| --- | --- |
| Replay, recalled memory or restored reasoning | Earlier decisions cannot be replayed; the next request takes a cache miss. Lost compaction summaries can require another summary or leave the request over the model's context limit. |
| Pending capture document | Unsaved turns can be lost. Rebuilding from client history can save previously delivered messages again; a worker whose document disappeared stops using its old lease. |
| Observation or thinking state | Recall bookkeeping resets, and earlier reasoning may no longer be recognized. |
| Tool receipt while another request waits | The result is reported as timed out or unknown. The gateway does not retry that operation; inspect its state before retrying a write. |

Asynchronous Redis failover can lose recent writes or leases, causing occasional duplicate capture. Persist Redis to preserve pending work and deletions; restoring a snapshot taken before a deletion makes the deleted data visible again. See [Redis memory policy](../guides/22-gateway-operations.md#redis-memory-policy).

With encryption on, values are encrypted. Key names are hashed, but index members contain original session IDs, and record kinds, versions, scheduling times and lease owners stay in plain text. Protect Redis access and backups accordingly.

## Deleting data {#deleting-data}

Revoking a gateway key, or the bound OpenViking key becoming invalid, does not delete user data. There are three ways to delete it:

| Who | How | What is deleted |
| --- | --- | --- |
| Account admin or root | **Delete this user's gateway data…** in Studio ([steps](09-gateway-keys.md#delete-a-users-gateway-data)), or `DELETE /gateway/admin/users/{user_id}/data` | One user: revokes their gateway keys and deletes their conversation state and logs, including content not yet saved |
| Root | `DELETE /gateway/admin/account/data?account_id=<account>` | All gateway data of an account |
| Embedded deployment, automatically | Deleting a user or account in OpenViking | The matching gateway data |

A standalone deployment has no automatic step; its data stays until it is explicitly deleted or [expires](#retention). With [`management_api: false`](13-configuration.md#gateway-fields), the deletion API is also unavailable: deleting an OpenViking user does not notify the standalone gateway, so its conversation state remains until `session_ttl_seconds` expires. Request logs and response mappings follow their own retention settings. Embedded deployments retain the server's automatic deletion hook in account tenancy. In key tenancy, the hook cannot locate the `k_` partitions from the OpenViking account and user IDs; their cached data instead follows the retention settings above.

None of this touches memories and sessions already stored in OpenViking. The reverse also holds: after you delete a memory in OpenViking, the copy the gateway saved for an ongoing conversation is still replayed until that conversation expires. To clear it now, delete the user's gateway data.

## Backups and recovery {#backups}

Back up these, together:

- For Redis, enable AOF and back up Redis persistence together with the encryption root key. Without persistence, restarting Redis loses management settings and pending conversation data.
- For SQLite, the whole `storage_path` directory: both databases and their `-wal` and `-shm` files.
- With encryption on, OpenViking's encryption root key (not the root API key), as the [Encryption guide](../guides/08-encryption.md) describes.

For a consistent copy, stop the gateway first, or use SQLite's online backup while it runs:

```bash
sqlite3 kernel.sqlite3 ".backup kernel.backup.sqlite3"
sqlite3 management.sqlite3 ".backup management.backup.sqlite3"
```

What losing each piece means:

| Lost | What happens | What to do |
| --- | --- | --- |
| `kernel.sqlite3` (or Redis kernel) | Memory added earlier cannot be replayed. Each ongoing conversation takes one provider cache miss, Claude conversations lose their earlier thinking once ([**Memory record missing**](12-troubleshooting.md#issues-on-degraded-requests)), and turns not yet saved are lost. | None; ongoing conversations recover after one miss |
| `management.sqlite3` (or Redis management) | Upstreams, context profiles, keys and request logs are gone. | Restore a backup, or set them up again and issue new keys; OpenViking key records rebuild on later requests |
| Encryption root key (encryption on) | The encrypted values in both databases cannot be read. | Start again with an empty `storage_path` (SQLite), or a new `storage.key_prefix` that the ACL key pattern still covers (for example `ovgw:2:`), or delete all keys under the old prefix (Redis) |

## Next steps {#next-steps}

- [Encryption](../guides/22-gateway-operations.md#encryption): turn on encryption for stored credentials and conversation state.
- [Delete a user's gateway data](09-gateway-keys.md#delete-a-users-gateway-data): the Studio steps for removing one user.
- [Routine checks](../guides/22-gateway-operations.md#day-to-day-operations): what to check regularly, including backups.
- [Access matrix](../api/25-gateway.md#credentials-and-account-scope): every operation and the credential it needs.
