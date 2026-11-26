---
description: Keep a user's registered and newly typed secrets out of model requests with OpenViking Gateway, while client tools still get the real value.
---

# Secret sealing in the gateway

A secret pasted into a conversation goes to the model provider with that message, and again with every later request. With **Seal secrets** on in a context profile, the gateway replaces the user's registered secrets, and new tokens the user types, with references such as <code v-pre>{{ov_privacy:alice:viking://~/privacy/credential/github#/values/token}}</code> before the request leaves for the provider. When the model writes a reference into a client tool call, the client receives the real value.

In Studio: **Profiles** → a profile → **Seal secrets**. It needs OpenViking Server 0.5.7 or later; see [Turn it on](#turn-it-on).

Only whole registered values, and tokens OpenViking's detector recognises, are replaced. New tokens in tool output are stored only with **Also store secrets found in tool output**. Sealing is off by default. Once it is on, a request that cannot be sealed is refused rather than sent unsealed; see [When sealing cannot be done](#when-sealing-cannot-be-done). References already in a request, such as <code v-pre>{{ov_privacy:…}}</code> spans a client wrote, are left as they are.

## How sealing works {#how-sealing-works}

![One new token through one turn: you paste a GitHub token, the gateway has OpenViking store it and replace it with a reference, the model receives only the reference and a note asking it to save the token under a proper name, then writes the reference into a tool call; the gateway swaps in the real value for the local tool, and seals the tool's output again before the model reads it; a request that cannot be sealed is refused](../../images/gateway/seal-journey.en.svg)

Sealing works in two directions. Before a request reaches the model, the gateway sends its text to OpenViking, which replaces every registered value with a reference. Before a client tool runs, the references the model wrote into its arguments are turned back into values. Assistant text is never unsealed, and the gateway's own records keep the reference.

A reference names the owner, the Privacy entry and the field. It is not the secret: it is bound to its owner, and only that owner's OpenViking identity can turn it back into the value. The endpoint behind it is [Seal texts](../api/10-privacy.md#seal-texts).

A new token takes two steps: OpenViking stores it in the `auto` category under a generated name, and the model is then asked to save it under a proper name. See [New tokens](#new-tokens).

## Turn it on {#turn-it-on}

1. Check that OpenViking Server is 0.5.7 or later, the release that has `POST /api/v1/privacy-configs/seal`. Only the release counts, so a development build such as `0.5.7.dev44` qualifies.
2. In Studio: **Profiles** → the profile the gateway key uses → turn on **Seal secrets**, below **Let clients override settings**.
3. Optionally, turn on the switch below it.

| Studio label | API name | Default | What it does |
| --- | --- | --- | --- |
| **Seal secrets** | `seal_secrets` | Off | Replaces the user's registered secrets, and new tokens the user types, with references, and gives client tools the real value. |
| **Also store secrets found in tool output** | `enroll_tool_secrets` | Off | Also stores new tokens from tool results and attached blocks. Only works while **Seal secrets** is on. |

**Also store secrets found in tool output** is off by default because of false positives: a test token in fixtures or a JWT in a log gets stored and replaced, and the model can no longer see it, so a task such as decoding that JWT fails. The cost of leaving it off is that tokens in tool output that were never saved go to the provider as they are.

- The `X-OpenViking-Context` header cannot change either; a header that names one gets 400 `context_override_invalid` (see [Let clients override settings](08-context-profiles.md#client-overrides)).
- Both are read from the profile as it is now, also for conversations that started earlier.

To check that it works, look at the request log: it counts the sealed texts of each request in `secrets_sealed`; see [In the request log](#request-log).

## What gets sealed {#what-gets-sealed}

| Content | Registered values replaced | New tokens stored |
| --- | --- | --- |
| User messages, in every turn | Yes | Only in the latest one, with a note to the model ([New tokens](#new-tokens)) |
| Tool results, and blocks the client attaches to user messages (file contents, reminders), in every turn of the request | Yes | Only with **Also store secrets found in tool output**; never with a note |
| Assistant text, the system prompt or instructions, tool definitions | Yes | Never |
| Earlier values of a credential the user has since rotated, anywhere in the history | Yes, with the reference of their field | — |
| What the gateway adds itself: recalled memory, compaction summaries, and the OpenViking tool rounds the gateway runs inside a request, which the client never sees | Yes, in a final sweep before the request leaves | — |
| Model names, tool names, IDs, signed reasoning | Left as they are | — |
| Non-JSON bodies, such as file uploads | Forwarded unchanged | — |

### Which values match {#which-values-match}

- Values registered in the user's Privacy `credential` and `auto` categories. When a value is in both, the `credential` reference is used.
- Earlier versions of a credential too, so a token the user has rotated stays sealed in the client's history.
- Not sealed: values shorter than 6 characters, values in the user's `never_mask` list, and tokens the detector does not recognise.
- Only whole values match. A value that appears in a tool result only in JSON-escaped form, such as non-ASCII characters written as `\uXXXX` or a value with quotes or backslashes in it, is not recognised there.

## New tokens {#new-tokens}

A token the user types in the latest message, that OpenViking's detector recognises and that is not registered yet, is stored in the `auto` category under a generated name, such as `github-k3j2m8`, and sealed as well. Its reference reads `…/privacy/auto/github-k3j2m8#/values/token`.

The model is told the reference and asked, unless the token is clearly for one-off use, to save it under a proper name in `credential` with `privacy_upsert`, which removes the `auto` entry. If the model does not save it, the entry stays in `auto`, still sealed under its temporary reference, and is not cleaned up automatically.

Only the seal endpoint writes into `auto`; `privacy_upsert` and the other write calls reject `category=auto`.

Values stored from tool output, with **Also store secrets found in tool output** on, go to `auto` too, but never cause the note. They accumulate there, one entry per value, until someone deletes them. One call to the seal endpoint stores at most 1024 new values; a call that would store more fails, and the request is handled as in [When sealing cannot be done](#when-sealing-cannot-be-done).

### What the model is told {#what-the-model-is-told}

The model gets two kinds of text:

- **The explanation of references**: references exist only in the model's view (files on disk, real command output and the tools it calls hold the real value), and the model must not mask or cut secrets itself.
- **The note on a temporary entry**: the latest user message holds a reference to a temporary `auto` entry, and the model is asked to save it under a proper name.

How each one reaches the model depends on whether the gateway adds context to the conversation:

| Conversation | Explanation of references: where and when | Note on a temporary entry |
| --- | --- | --- |
| The gateway adds context (no OpenViking plugin detected) | In the session-start context. A conversation that started before **Seal secrets** was turned on gets it with its next turn, once. | Part of the turn's added context, and repeated as recorded on later turns. |
| The gateway detected an OpenViking plugin | A short version, added to the latest user message, before any note, on every request where sealing replaced a value. | Added to the latest user message on every request of the turn, identically; it stops once the entry is saved under a name. |

- The note is given whenever the latest user message holds a reference to a temporary `auto` entry, whichever request or gateway instance stored it. Housekeeping requests (such as title generation) and token-count requests never get the note or the short explanation.
- Saving, renaming or rotating a credential does not start a new conversation in the gateway: references compare as equal when the gateway matches a history to its conversation.

## Tool calls {#tool-calls}

- When the model writes a reference into the arguments of one of the client's tool calls, the client receives the value instead, so a shell command works as written.
- These keep the reference: assistant text, which is never unsealed; the gateway's own records; OpenViking's own tools (any tool whose name contains `openviking`); and arguments that run `ov privacy`. `ov privacy run` resolves the reference itself and gives the value only to the child process, so the reference is passed through unchanged.
- A reference that no longer resolves, for example after its entry was renamed, stays as it is. This is not a failure.

### Streaming {#streaming}

- With **Seal secrets** on, tool-call arguments are no longer streamed piece by piece.
- When the gateway runs OpenViking tools or shows a recall notice in the reply, each round's tool calls reach the client once they are complete.
- Otherwise the client gets the provider's reply as it was sent, with the same response ID, status, events and errors. Text streams as it arrives; from the first client tool call on, the rest of the reply is held until it ends.

## When sealing cannot be done {#when-sealing-cannot-be-done}

With **Seal secrets** on, nothing goes to the model unsealed: a request that cannot be sealed is refused, and a reply whose tool calls cannot be unsealed fails. The error message says which case it is.

| Case | The client gets | What to do |
| --- | --- | --- |
| OpenViking is older than 0.5.7 | 503 `secret_seal_failed`, naming the version found and the one required. The seal endpoint is not called. | Upgrade OpenViking, or turn **Seal secrets** off. |
| OpenViking's version cannot be read: `/health` fails or the server is unreachable | 503 `secret_seal_failed` | Restore the connection to OpenViking, or turn **Seal secrets** off. |
| A seal call fails, times out (after 10 seconds) or returns a malformed answer | 503 `secret_seal_failed` | Retry once OpenViking answers again, or turn **Seal secrets** off. |
| A seal call would store more than 1024 new values | 503 `secret_seal_failed`; the same request fails again | Send less content in one request, or turn **Seal secrets** off. |
| A text is larger than the seal endpoint accepts | 400 `secret_seal_invalid_request` | Send less content in one request, or turn **Seal secrets** off. |
| The body is compressed or cannot be parsed safely: invalid JSON, very large integers, duplicate keys | 400 `secret_seal_invalid_request` | Send an uncompressed, valid JSON body, or turn **Seal secrets** off. |
| The request's tool calls could not be unsealed: tools defined on an endpoint the gateway does not understand, legacy `functions`, or a Responses request with `background: true`, whose reply is fetched later | 400 `secret_seal_invalid_request` | Use `tools` on a supported endpoint, without `background`, or turn **Seal secrets** off. |
| The references in a reply's tool calls cannot be resolved, or a streamed reply cannot be read | 502 `secret_unseal_failed`; a reply that is already streaming ends with it as the stream's error event | Check that OpenViking is reachable, or turn **Seal secrets** off. |

- The gateway reads the version again at most 30 seconds later, so sealing works again after an upgrade or once OpenViking is back, without a restart.
- The gateway asks providers for uncompressed replies. A whole (not streamed) reply that comes back compressed anyway goes to the client as it came, references included.
- A sealing pass is all or nothing: when one of its calls fails, none of its replacements are used, and nothing is kept from the failed call.
- The errors clients receive: [Clients get an error from the gateway](12-troubleshooting.md#clients-get-an-error-from-the-gateway).

## What it does not cover {#not-covered}

- It protects against secrets leaving by accident in normal use, not against a malicious model or an untrusted machine.
- Non-JSON bodies, such as file uploads, are forwarded unchanged.
- Values shorter than 6 characters, values in `never_mask`, and tokens the detector does not recognise.
- Values with no recognisable format, such as a database password, are sealed only after you save them once.
- Values in a tool result that appear only in JSON-escaped form.
- New tokens in tool results and attachments, unless **Also store secrets found in tool output** is on; registered values in them are still replaced.
- Auto-enrolment has no confirmation step. To undo a false positive, delete the entry (`ov privacy delete auto <target> --yes`) and add the value to `never_mask` (see [Privacy configs](../concepts/13-privacy.md)).
- OpenViking's own tools are recognised by name: if the client registers the OpenViking MCP server under a name without `openviking` (such as `ov`), references in calls to its tools, for example `privacy_upsert` or `write`, reach the tool as values.
- The client's own transcript still holds what the user pasted and the tool arguments the gateway unsealed.

## In the request log {#request-log}

The request log counts, for each request:

| Field | Counts |
| --- | --- |
| `secrets_sealed` | Sealed texts, including those in the final sweep over what the gateway added |
| `secrets_enrolled` | Values this request stored, each once |
| `secrets_unsealed` | Distinct references unsealed in the reply's tool calls |
| `seal_calls` | Calls to the seal endpoint |
| `seal_cache_hits` | Cache hits |
| `seal_ms` | Time spent sealing, in milliseconds |

The log never records a value or a reference.

## How the gateway talks to OpenViking {#requirements}

- The gateway reads the server's version from `/health` with the user's own OpenViking key, in the same cached check that identifies the key. Only the release counts, so `0.5.7.dev44` qualifies.
- While the version is older or cannot be read, the gateway does not call the seal endpoint at all and refuses the request.
- Each request calls the seal endpoint with the user's own key. Results are kept in the gateway's memory, up to 64 MiB per process, never in shared storage; nothing is kept from a call that failed.
- Each call to the seal or resolve endpoint is limited to 10 seconds.
- Seal and resolve calls count toward the per-account [OpenViking circuit breaker](12-troubleshooting.md#openviking-is-degraded) like any other call: repeated timeouts, 5xx or 429 answers open it, and while it is open that account's requests go to the model without memory, or are refused with 503 `secret_seal_failed` while **Seal secrets** is on.

## Next steps {#next-steps}

- [Seal texts](../api/10-privacy.md#seal-texts): the OpenViking endpoint the gateway calls.
- [Seal secrets](08-context-profiles.md#seal-secrets) in the context profile.
