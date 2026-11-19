---
description: Read the cards, columns, request types and saving status on OpenViking Gateway's Overview and Requests tabs in Studio.
---

# Gateway overview and request log

This page explains every card, column, label and saving status on the gateway's **Overview** and **Requests** tabs, and which values mean the gateway is healthy. In Studio, open **OpenViking Gateway** and choose the **Overview** or **Requests** tab. Both tabs stay empty until a client sends its first request through the gateway; see [Connect clients](03-connect-clients.md#connect-clients).

## Who sees which requests {#requests-and-overview}

Both tabs read the gateway's request log: one record per model request that went through the gateway. Users see only their own requests; account admins and root see the whole account's. The Overview figures are computed from the same records, so they cover the same requests.

With `request_log_sink: "stdout"`, new records go only to stdout. The Requests page and Overview's request figures stay empty on a fresh deployment; previously stored records remain visible until they expire. Use the [stdout monitoring workflow](../guides/22-gateway-operations.md#monitoring) for new requests.

The log holds metadata only, never prompts, replies or memory text; see [What request logs record](11-security-and-data.md#what-request-logs-record). Stored records are kept for 30 days by default; stdout retention belongs to the log collector. See [Retention](11-security-and-data.md#retention).

## Overview {#overview}

The **Overview** tab summarizes the latest 10,000 requests in the request log. Until the first request arrives, it also shows the **Get started** checklist; see [Get started checklist and Quick mode](06-manage-in-studio.md#get-started-checklist).

| Card | What it shows | Healthy value | If it looks wrong |
| --- | --- | --- | --- |
| **Requests** | The number of model requests, when the last one arrived, and their output tokens | Grows as clients send requests | [Check the connection](03-connect-clients.md#check-the-connection) |
| **First-call cache hit rate** | The share of input tokens the provider served from its [prompt cache](01-how-it-works.md#replay) on the first model call of each new user message | Close to what the provider reaches without the gateway. This is the most important sign of a healthy gateway. | [The first-call cache hit rate dropped](12-troubleshooting.md#the-first-call-cache-hit-rate-dropped) |
| **Within-turn cache hit rate** | The same share for later calls in a turn, such as tool steps and OpenViking tool rounds | Usually high | — |
| **Memory entries recalled** | Memory entries added to new messages, and how long an OpenViking search took on average (counting only messages that searched) | Grows once the user has memories in OpenViking; 0 is expected on a brand-new account | [No memory is added](12-troubleshooting.md#no-memory-is-added) |
| **OpenViking** | **Connected**, **Degraded** or **Starting**, the OpenViking version, and the reason when degraded. Its **Saving conversations** part counts conversations whose latest record says a finished turn was not saved. | **Connected**, and no saving problems in recent requests | [OpenViking is degraded](12-troubleshooting.md#openviking-is-degraded), [Conversations do not appear in OpenViking](12-troubleshooting.md#conversations-do-not-appear-in-openviking) |
| **Degraded requests** | How often each issue occurred, with an explanation and a suggested action | **No degraded requests** | [Issues on degraded requests](12-troubleshooting.md#issues-on-degraded-requests) |
| **Recent requests** | The latest requests, with a link to the **Requests** tab | — | [Requests](#requests) |

A clear drop in the first-call rate usually means the history the gateway sends no longer matches the previous request. While OpenViking is unreachable, requests still reach the model, just without memory, and the turns that end meanwhile are not saved; the next turn that ends after OpenViking is back writes them. When the **Saving conversations** part or the **Degraded requests** card reports a problem, **Show in Requests** opens the **Requests** tab filtered to **Issues**.

### Long conversations missing the cache {#long-conversations-missing-the-cache}

A warning such as "2 long conversations keep missing the prompt cache" appears below the four number cards at the top (**Requests**, **First-call cache hit rate**, **Within-turn cache hit rate** and **Memory entries recalled**) when a conversation's last 3 model calls each sent at least 32,000 input tokens, less than 5 minutes apart, and the provider reported 0 cached tokens every time. It lists the model, upstream and streak of each conversation. When they are all Anthropic conversations whose upstream never wrote the cache either, the warning suggests checking that the client sets `cache_control`.

The cache cannot hit across these changes, so a streak starts over at each of them:

- a [compaction](05-long-conversations.md#long-conversations), or the model starting a new context window
- a switch to another model or upstream
- gateway tools appearing in or disappearing from the requests

When an upstream has not reported any cache hit lately, you see a note instead: long conversations through that upstream keep showing a 0% cache hit rate. For causes and fixes, see [The first-call cache hit rate dropped](12-troubleshooting.md#the-first-call-cache-hit-rate-dropped).

## Requests {#requests}

The **Requests** tab lists the latest 1,000 records of the request log, newest first, and refreshes every 30 seconds while you are on the first page. Filters and search cover those 1,000 records.

Every model request is logged, including the ones the gateway refuses itself, such as a model the key does not allow, no matching upstream or a body over the size limit, and the ones that fail at the provider or end because the client went away. A request refused before its key was confirmed, such as an invalid gateway key, is not listed: it belongs to no account. A request refused before memory preparation shows as **Forwarded**. See [Clients get an error from the gateway](12-troubleshooting.md#clients-get-an-error-from-the-gateway).

### Filter and search {#filter-and-search}

| Filter | Shows |
| --- | --- |
| **All** | Every record |
| **New messages** | Requests that start a turn with a new user message |
| **Tool steps** | Requests that continue a turn, for example with tool results |
| **Issues** | Degraded requests, requests whose turn could not be saved (**Failed**), and requests with HTTP 400 or above, from the provider (see [The upstream returned an error](12-troubleshooting.md#the-upstream-returned-an-error)) or from the gateway (see [Clients get an error from the gateway](12-troubleshooting.md#clients-get-an-error-from-the-gateway)) |
| **Request type** | One of the [request types](#request-types) |

Search matches the model, conversation ID, request ID, key name or prefix, and upstream name. The log stores only a hash of the conversation ID, so Studio hashes a pasted conversation ID in the browser before matching. This works only when Studio is opened over HTTPS or on localhost; over plain HTTP, searching by conversation ID finds nothing.

### Columns {#columns}

| Column | Shows |
| --- | --- |
| **Time** | When the request arrived |
| **Type** | The [request type](#request-types) |
| **Model** | The model name the client sent |
| **Status** | The HTTP status the client received: the provider's, or the gateway's own when the gateway refused or failed the request |
| **Tokens** | Input tokens, the cached share and output tokens |
| **Memory** | What [recall](../guides/15-gateway.md#key-terms) did in this request (see below) |
| **Saving** | The request's [saving status](#saving-status); hover for the reason |
| **Issue** | The issue, for a degraded request |

The **Memory** column uses this notation:

| Shown | Meaning |
| --- | --- |
| `+3` | This request's search added 3 memory entries; the search time is shown next to it. |
| `↺ 4` | Memory added to 4 earlier messages was replayed unchanged. |
| **Recall failed** | This request's memory search did not finish, for example because OpenViking was unreachable, rejected the key, or did not answer within the context profile's **Time limit**. Hover for the reason, and see [No memory is added](12-troubleshooting.md#no-memory-is-added). |

### Request details {#request-details}

Expand a row to see:

- the conversation ID, upstream, gateway key and duration
- the token breakdown
- the memory search result
- context window use, and whether the history was compacted or the model started a new window
- the saving status and, for a request that saved its turn, how many messages it wrote and the OpenViking session
- OpenViking tool details, including why tools were not offered
- the profile settings the client sent in `X-OpenViking-Context`, by name only; they take effect only on a conversation's first request

For a request with an issue, the expanded row also explains what happened and what to do.

### Request types {#request-types}

Every record has one type. The type decides whether the request searches memory and whether its turn is saved.

| Type | Meaning | Memory |
| --- | --- | --- |
| New message | A request whose last message is a new user message | Searched once, earlier memory replayed; saves the turn when its reply ends it |
| Tool step | The same turn continuing, for example with tool results | Earlier memory replayed; the one whose reply ends the turn saves all of it |
| Housekeeping | Title, summary, compaction, suggestion and permission-check requests; once a conversation has sent client tools, a separate request in it with neither tools nor earlier replies, such as Claude Code summarizing a fetched page. Operators can add a client's own requests in [Client rules](13-configuration.md#client-rules) | Earlier memory replayed; never saved, but Claude Code's or Codex's compaction request inside a turn saves the history before it |
| Sub-agent | Requests from a client's sub-agents | Earlier memory replayed; never saved |
| Token count | Token-counting requests (`/gateway/v1/messages/count_tokens`) | Earlier memory replayed, so the count matches what is actually sent |
| Forwarded | Requests passed through unchanged, without memory: other endpoints, Responses requests that rely on provider-side state, compressed or unreadable bodies | None |

## Saving status {#saving-status}

The **Saving** column shows whether the gateway [saves](../guides/15-gateway.md#key-terms) the conversation's finished turns to OpenViking, and whether this request's save worked. Normally it shows **On**.

| Status | Meaning | What the gateway does |
| --- | --- | --- |
| **On** (`active`) | Saving is on; if this request ended a turn, the turn was written to OpenViking. | The request whose reply ends a turn writes the turn to OpenViking. |
| **Off** (`disabled`) | The conversation's [context profile](08-context-profiles.md#save-conversations) does not save. | Nothing is saved. |
| **Failed** (`failed`) | This request ended a turn, but writing it to OpenViking failed or took longer than 30 seconds. Each turn is tried once. | Nothing retries it. The next turn that ends in this conversation writes what is missing, as long as the client resends the history unchanged. If no turn follows, this turn is not saved. |

The expanded row and the **Saving** badge's tooltip show the reason:

| Reason shown | Code | Meaning |
| --- | --- | --- |
| OpenViking rejected the key. It may be wrong or revoked. | `openviking_http_401` | The user's OpenViking key is no longer valid. |
| OpenViking is unreachable or didn't answer in time | `openviking_unavailable` | OpenViking is down or unreachable. |
| OpenViking didn't answer in time | `openviking_timeout` | OpenViking is too slow. |
| OpenViking stopped answering for this account, so it was skipped for now | `openviking_circuit_open` | Recent calls for this account went unanswered; see [OpenViking is degraded](12-troubleshooting.md#openviking-is-degraded). |
| OpenViking returned HTTP … or OpenViking reported error … | `openviking_http_<status>`, `openviking_<CODE>` | OpenViking refused the write, for example `402` (not subscribed), `429` (rate limited), or `400` for an unknown memory type in `capture_memory_types`. |
| Saving to OpenViking timed out | `save_timeout` | Saving took longer than 30 seconds. |
| Saving to OpenViking failed | `save_failed` | Another error; the gateway log has a "could not save a finished turn" warning with the traceback. |
| Resynced by an administrator | `manual_reset` | Not a failure: someone used **Resync conversation…** (see below), the status stays **On**, and the conversation's later records keep showing it. |

A conversation started under an earlier gateway version may keep showing the reason recorded then (for example `history_changed`); while the status is **On**, nothing needs to be done. If saving keeps failing, see [Conversations do not appear in OpenViking](12-troubleshooting.md#conversations-do-not-appear-in-openviking).

### Resync a conversation {#resync-a-conversation}

Resync starts a new OpenViking session for a conversation, and the gateway writes the full history to it when the conversation's next turn ends. Use it when saving kept failing and you want the whole conversation saved once the cause is fixed, or when you want a clean, complete copy of a conversation in a session of its own. What was already saved stays in the old session, so OpenViking may extract some memories twice.

1. On the **Requests** tab, expand a record of the conversation. Resync needs the key shown on the record; if that key has been revoked, pick a newer record of the same conversation.
2. Choose **Resync conversation…**, then **Resync**. You should see "The conversation will be saved again when its next turn ends."
3. Wait for the next turn in that conversation to end. Nothing happens until then; then the gateway saves the full history to the new session, and the conversation's records show the reason "Resynced by an administrator".

## Next steps {#next-steps}

- [Troubleshoot OpenViking Gateway](12-troubleshooting.md#common-symptoms): match a card, label or status to its cause and fix.
- [Replay and prompt caching](01-how-it-works.md#replay): why the first-call cache hit rate matters.
- [Monitoring](../guides/22-gateway-operations.md#monitoring): check the gateway's state outside Studio.
- [Security and data](11-security-and-data.md#what-request-logs-record): what the log records and how long it is kept.
