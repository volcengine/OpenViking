---
description: What OpenViking Gateway stores, who can reach it, what providers see, and how data is kept, deleted and backed up.
---

# OpenViking Gateway security and data

This page is for operators and security reviewers approving a gateway deployment, and for users who want to know what the gateway keeps about them. Sessions and memories always live in OpenViking under its own rules; the gateway keeps only its settings, the state it needs to continue conversations, and metadata-only request logs, in two SQLite files under `storage_path`. Each section below answers one review question and can be linked on its own.

## Isolation by user {#isolation-by-user}

Each [gateway key](../guides/15-gateway.md#key-terms) is bound to one OpenViking user. The gateway searches memory, saves conversations and calls tools with that user's own OpenViking key, so it can never do more than the user can. Each user's conversation state is kept separate from every other user's.

## What the gateway stores {#what-the-gateway-stores}

Everything the gateway keeps is in two SQLite databases in `storage_path` (default `~/.openviking/gateway`):

| File | What it holds |
| --- | --- |
| `management.sqlite3` | Upstreams, context profiles, gateway keys, request logs, and which upstream created each Responses response |
| `kernel.sqlite3` | The state of each conversation: the memory text added to messages, OpenViking tool rounds and their results, summaries, conversation text waiting to be saved, and the reasoning of model replies when **Restore reasoning the client drops** is on |

Some of this is sensitive:

- `management.sqlite3` holds upstream API keys, header values and the OpenViking keys bound to gateway keys. Gateway keys themselves are stored only as a hash and a short prefix.
- `kernel.sqlite3` holds memory text word for word, because the gateway needs it to [replay](01-how-it-works.md#replay) what it added in earlier turns.

Only the system user that runs the gateway can read the files. With [encryption](../guides/22-gateway-operations.md#encryption) on, every stored value is encrypted as well; with it off, the credentials above are in plain text, protected only by file permissions.

## What request logs record {#what-request-logs-record}

Request logs hold metadata only, never content:

| Recorded | Never recorded |
| --- | --- |
| Request type, model, status, upstream, gateway request ID, and upstream response ID | Message text |
| A hash of the conversation ID | Recalled memory |
| Token counts, memory counts and timing | Tool arguments or results |
| Saving status and issues | Any key |

The gateway writes no HTTP access log, and its error messages never repeat submitted values. To read the logs in Studio, see [Requests](10-overview-and-requests.md#requests).

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

Gateway data expires on its own. A cleanup job runs every hour:

| Data | Kept for | Setting |
| --- | --- | --- |
| Conversation state | 30 days after last use | `session_ttl_days` |
| Request logs | 30 days | `log_retention_days` |
| Which upstream created each Responses response | 30 days | `response_ttl_seconds` |
| Sessions and memories in OpenViking | OpenViking's own rules | — |

Minimums and where to set these are in [Gateway fields](13-configuration.md#gateway-fields).

## Deleting data {#deleting-data}

Revoking a gateway key, or the bound OpenViking key becoming invalid, does not delete user data. There are three ways to delete it:

| Who | How | What is deleted |
| --- | --- | --- |
| Account admin or root | **Delete this user's gateway data…** in Studio ([steps](09-gateway-keys.md#delete-a-users-gateway-data)), or `DELETE /gateway/admin/users/{user_id}/data` | One user: revokes their gateway keys and deletes their conversation state and logs, including content not yet saved |
| Root | `DELETE /gateway/admin/account/data?account_id=<account>` | All gateway data of an account |
| Embedded deployment, automatically | Deleting a user or account in OpenViking | The matching gateway data |

A standalone deployment has no automatic step; its data stays until it is explicitly deleted or [expires](#retention).

None of this touches memories and sessions already stored in OpenViking. The reverse also holds: after you delete a memory in OpenViking, the copy the gateway saved for an ongoing conversation is still replayed until that conversation expires. To clear it now, delete the user's gateway data.

## Backups and recovery {#backups}

Back up these, together:

- The whole `storage_path` directory: both databases and their `-wal` and `-shm` files.
- With encryption on, OpenViking's encryption root key (not the root API key), as the [Encryption guide](../guides/08-encryption.md) describes.

For a consistent copy, stop the gateway first, or use SQLite's online backup while it runs:

```bash
sqlite3 kernel.sqlite3 ".backup kernel.backup.sqlite3"
sqlite3 management.sqlite3 ".backup management.backup.sqlite3"
```

What losing each piece means:

| Lost | What happens | What to do |
| --- | --- | --- |
| `kernel.sqlite3` | Memory added earlier cannot be replayed. Each ongoing conversation takes one provider cache miss, Claude conversations lose their earlier thinking once ([**Memory record missing**](12-troubleshooting.md#issues-on-degraded-requests)), and turns not yet saved are lost. | None; ongoing conversations recover after one miss |
| `management.sqlite3` | Upstreams, context profiles, keys and request logs are gone. | Set them up again and issue new keys |
| Encryption root key (encryption on) | The encrypted values in both databases cannot be read. | Start again with an empty `storage_path` |

## Next steps {#next-steps}

- [Encryption](../guides/22-gateway-operations.md#encryption): turn on encryption for stored credentials and conversation state.
- [Delete a user's gateway data](09-gateway-keys.md#delete-a-users-gateway-data): the Studio steps for removing one user.
- [Routine checks](../guides/22-gateway-operations.md#day-to-day-operations): what to check regularly, including backups.
- [Access matrix](../api/25-gateway.md#credentials-and-account-scope): every operation and the credential it needs.
