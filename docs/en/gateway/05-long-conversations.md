---
description: How OpenViking Gateway compacts long conversations, which context window to set, and what compaction costs.
---

# Long conversations in the gateway

This page is for admins who set up context profiles and upstreams, and for users whose long conversation was cut short or rejected as too long. It explains when and how the gateway summarizes a long conversation, what the model receives afterwards, and what that costs. The setting to check first is the model's [context window](#context-window): if it is not set, the gateway assumes 1,000,000 tokens.

## What compaction does {#long-conversations}

[Compaction](../guides/15-gateway.md#key-terms) keeps a conversation inside the model's context window, the most tokens one request can hold, and it is on by default. When a conversation nears the window, the same model writes a summary of the conversation so far, and from then on the summary replaces everything before the cut. The history shown in the client does not change. When saving is on and the model has the OpenViking `grep` and `read` tools, the summary ends with directions for [finding what was cut](#finding-what-was-cut).

In Studio: **Profiles** → **Long conversations**.

| Setting | Default | What it does |
| --- | --- | --- |
| **Compaction** | On | Replace the earlier part of a conversation with a summary when it nears the context window. |
| **Compact at** | 0.9 of the window | How full the window must be before the gateway compacts. |
| **Summary length** | 8,000 tokens | The most tokens the model may write for one summary. |
| **Default context window** | Not set (1,000,000 tokens assumed) | The window for models the upstream does not list; see [Set the model's context window](#context-window). |
| **Agent-managed context windows** | Off | Experimental: the model starts fresh windows itself; see [below](#experimental-agent-managed-context-windows). |

A conversation keeps the context profile it started with, so profile changes reach only new conversations (see [When changes take effect](06-manage-in-studio.md#when-changes-take-effect)). Ranges and API names are in the [configuration reference](13-configuration.md#context-profile-settings).

## When it compacts {#when-it-compacts}

On every new user message and every tool step (a request in which the client sends back results from its own tools; the **Tool step** [request type](10-overview-and-requests.md#request-types)), the gateway estimates how full the context window is: the token usage the provider reported for this conversation's previous reply, plus an estimate for the messages added since. When the estimate reaches **Compact at** (0.9 of the window by default), the gateway compacts before it forwards the request.

Exceptions:

- **No usage from the provider.** Without the reported usage (for example, streamed Chat Completions without `stream_options.include_usage`), the gateway estimates the whole request.
- **Requests that never trigger it.** Housekeeping, sub-agent and token-count requests never trigger compaction.
- **OpenViking tool rounds are not checked.** The gateway checks the window only when a request arrives, so the OpenViking tool rounds it runs inside one request are never compacted. Rounds and the token budget have no limit by default; to keep one request inside the window, set **Rounds per request** or **Token budget** (see [Limits](04-openviking-tools.md#limits)).

## Set the model's context window {#context-window}

The gateway has to know each model's context window to compact in time. It takes the window from the first of these that is set:

1. The upstream's **Context windows** entry for the model, matched by the model name sent to the upstream (after aliases).
2. The context profile's **Default context window**.
3. Otherwise, 1,000,000 tokens.

For a model whose window is smaller than 1,000,000 tokens, set one of the first two. Otherwise the gateway compacts too late, and the provider rejects the conversation as too long before compaction ever starts. If you cannot change upstreams or context profiles, ask whoever manages the gateway (see [Who can change what](06-manage-in-studio.md#who-can-change-what)).

To set and check a model's window:

1. In Studio, open **Upstreams**, edit the upstream, and under **Models** → **Context windows** add the model name with its window in tokens (at least 1,024). Saved upstream settings apply at once. To cover every model the upstream does not list instead, set **Default context window** under **Profiles** → **Long conversations**; that reaches new conversations only.
2. Send a message, then expand the request on the **Requests** tab. The **Context window** line should read "About … of *N* tokens", where *N* is the window you entered.

## How the summary is written {#how-the-summary-is-written}

The gateway sends one extra, non-streaming request to the same upstream and model. The client's system prompt, tools and thinking settings stay the same, so most of the request hits the provider's [prompt cache](01-how-it-works.md#replay). Settings that a plain-text summary cannot follow are removed: a forced tool choice becomes "no tool call", and structured output formats and stop sequences are dropped.

The request contains the conversation up to the cut, plus an instruction for the model to summarize it for itself. An earlier summary is folded into the new one. The summary covers:

- the user's goals and the latest request, word for word;
- key decisions and current progress;
- files, paths and identifiers;
- errors and how they were fixed;
- open items;
- key facts from the latest tool results.

The summary is plain text of at most **Summary length** (8,000) tokens. Thinking counts against the output cap, and some models (such as DeepSeek V4.1) think by default even when the request has no thinking settings, so the gateway always allows 16,000 tokens of output on top of the summary length; when the client sets an explicit thinking budget, it adds that budget instead. The provider bills this request like any other.

## What the model receives afterwards {#what-the-model-receives-afterwards}

Where the cut falls depends on the request:

| Request | Where the cut falls | What the model receives |
| --- | --- | --- |
| New user message | Right before that message | The system and developer messages, the summary as one user message, and the new message |
| Tool step | After the latest tool result | The system and developer messages and the summary, from which it continues the task |

The summary message starts by saying that the gateway replaced the earlier part of the conversation and that the user did not write it. It ends with the conversation's opening gateway note and user profile, so these survive compaction.

Every later request that resends this history, including housekeeping and token-count requests, gets the same replacement; sub-agents usually send their own separate history and are not affected. Each compaction costs one provider cache miss at the cut, and caching works normally after that.

**Nothing before the cut is kept word for word**, not even the latest turns. The model's earlier replies and their thinking depend on the history being replaced, and keeping only part of it would break Claude's thinking signatures. Everything generated after the cut is sent as usual, thinking included.

### Finding what was cut {#finding-what-was-cut}

The conversation itself is still saved in OpenViking. When **Save conversations** is on and the conversation has the `openviking_grep` and `openviking_read` tools, the summary ends with directions:

- the conversation's OpenViking session (`viking://user/<user_id>/sessions/<session_id>/`) and where its messages are stored;
- how to search them: grep the session for a specific pattern to get line numbers, then read the lines around a match.

When a cut falls in the middle of a turn, the part before it is saved to OpenViking right away, so the model can search it within the same turn.

## See compaction in the request log {#see-compaction-in-the-request-log}

Expand a request on the **Requests** tab (see [Requests](10-overview-and-requests.md#requests)). Its **Context window** line shows:

| Line | Meaning |
| --- | --- |
| About *X* of *Y* tokens (*P*%) | How full the window was estimated to be, and the window size *Y* the gateway used |
| Sent with the earlier history replaced | The request carried a summary in place of the history before the cut |
| Summary of about *N* tokens written in *T* | This request triggered compaction, and the summary took *T* to write |
| Compaction failed (*reason*), so the full history was sent | See [When compaction fails](#when-compaction-fails) |
| Summary kept, but usage is still at or above Compact at (`still_over_threshold`) | The summary was written and used, but the conversation still fills the window; see [When compaction fails](#when-compaction-fails) |

## When compaction fails {#when-compaction-fails}

If the summary request fails, or the reply calls a tool, is empty or is cut off at the length limit, the gateway sends the full history instead. The request detail shows **Compaction failed** with the reason, and the gateway does not try to compact this conversation again for 60 seconds.

One case keeps the summary: if usage is still at or above **Compact at** after the summary is written (for example because the system prompt and tools alone fill most of the window), the gateway uses the summary, records the failure, and also waits 60 seconds before trying again.

Each reason and its fix are listed under [Long conversations hit the context limit](12-troubleshooting.md#long-conversations-hit-the-context-limit).

## Side effects {#side-effects}

**No Working Memory summaries.** The summary comes from the conversation's own model, not from OpenViking's session summaries. New OpenViking sessions that the gateway creates have Working Memory turned off: commits still archive messages and extract memories, but no session summary is written.

**The memory budget starts over.** **Budget per context window** (see [Recall memory](08-context-profiles.md#recall-memory)) resets after each compaction, so memory added before the cut may be added again.

**The client still sends the full history.** After the gateway compacts, the client keeps sending its full history, and the gateway replaces the part before the cut on every request. The client sees small usage numbers, so clients that compact by usage rarely compact on their own.

**Very long sessions can hit the request size limit.** Because the client's history keeps growing, a very long session can eventually exceed the request size limit: request bodies over 32 MiB are rejected with HTTP 413 (see [Clients get an error from the gateway](12-troubleshooting.md#clients-get-an-error-from-the-gateway)). Operators can change the limit with `max_body_bytes` (see [Gateway fields](13-configuration.md#gateway-fields)); a reverse proxy in front of the gateway must allow bodies at least as large (see [Reverse proxy and HTTPS](../guides/22-gateway-operations.md#reverse-proxy-and-https)).

**A client's own compaction starts a new session.** When the client compacts or edits its history, the gateway saves the new history to a new OpenViking session. If the search tools are available, the opening gateway note of the new history tells the model where the earlier sessions are.

## Experimental: agent-managed context windows {#experimental-agent-managed-context-windows}

With **Agent-managed context windows** on (off by default), the model decides when to start a fresh context window and writes its own hand-off notes, instead of waiting for the gateway's summary. The gateway gives it two tools and reminds it as the window fills up. The setting only works in conversations that get [OpenViking tools](04-openviking-tools.md#agentic-memory-for-any-client); other conversations are [compacted](#long-conversations) as usual, and when compaction is on, it remains the fallback if the model never starts a new window.

In Studio: **Profiles** → **Long conversations** → **Agent-managed context windows** (marked **Experimental**), with **Soft reminder at** (0.7) and **Hard reminder at** (0.85). How well this works depends on the model, and the behavior may change in later releases.

### What the model gets {#what-the-model-gets}

Two extra tools. They are not in the profile's **Tools** list; only this setting controls them.

| Tool | What it does |
| --- | --- |
| `openviking_new_context(reason, notes, next_steps)` | Starts a new window; `next_steps` is optional. |
| `openviking_context_remaining()` | Returns the window number, estimated tokens used and left, the user turns in this window, the time since the user's previous message, and a one-line recommendation. |

`openviking_new_context` must be the only tool call in its round. Called together with other tools, every call in the round returns an error and the window is not reset. It also returns an error if the gateway already replaced the context at the same point in this request (for example, it just compacted).

The model also sees:

- **One more line in the opening gateway note**, telling the model that it manages its own context windows with these two tools.
- **A status line**: after each new user message, the memory block ends with `[context-status] window wN · ~X/Y tokens (P%) · T since your previous message`.
- **A soft reminder**: when the window reaches **Soft reminder at** (0.7), the gateway reminds the model once to start a new window after the current step, with notes that keep the exact paths, line numbers and values it will need.
- **A hard reminder**: at **Hard reminder at** (0.85), it tells the model to start a new window now, and repeats on every step until the model does or compaction takes over.

Reminders are attached to the new user message, or to the client's tool result in a tool step. They are not added between the gateway's own OpenViking tool rounds.

### When the model starts a new window {#when-the-model-starts-a-new-window}

When the model calls `openviking_new_context`, the gateway replaces the conversation so far with a window header, and the model carries on in the same reply. The header contains, in order:

1. that this is window N, started by the model, and that the user did not write it;
2. the reason, notes and next steps;
3. the user's latest message, word for word;
4. how to search earlier windows (under the same conditions as for [compaction](#finding-what-was-cut));
5. the conversation's opening gateway note.

Later requests rebuild the same window from the client's history. The content before the reset is saved to OpenViking right away and stays searchable; the reason and notes appear only in the header and are not saved to OpenViking. Each reset costs one provider cache miss but needs no summary request.

In the request log, the **Context window** line of each request shows the window number ("Window *N*, managed by the model"), "The model started a new window" when it did, and which reminder the model received ("Reminded the model to start a new window soon" or "Told the model to start a new window now").

## Next steps {#next-steps}

- [Long conversations hit the context limit](12-troubleshooting.md#long-conversations-hit-the-context-limit): what each compaction failure reason means and how to fix it.
- [Models](07-upstreams.md#models): where an upstream's **Context windows** sit among the other model settings.
- [Context profiles](08-context-profiles.md#context-profiles): the other sections of a profile and their defaults.
- [OpenViking tools in the gateway](04-openviking-tools.md#limits): limit the rounds and tokens of one request.
