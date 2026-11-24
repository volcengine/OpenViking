---
description: Create context profiles that decide how OpenViking Gateway recalls and saves memory for each gateway key.
---

# Manage gateway context profiles

In Studio: **Profiles** tab. A context profile decides how OpenViking Gateway recalls memory, saves conversations, handles long conversations and offers OpenViking tools for every gateway key that uses it. This page walks through each section of the profile editor and its defaults; changing profiles needs write access, see [Who can change what](06-manage-in-studio.md#who-can-change-what).

## What a context profile is {#context-profiles}

A [context profile](../guides/15-gateway.md#key-terms) is a named set of memory settings. Each [gateway key](09-gateway-keys.md#gateway-keys) uses one profile, chosen when the key is issued, and one profile can serve many keys. Clients that use an [OpenViking API key directly](09-gateway-keys.md#openviking-keys) use the account's profile with the ID `default` (the one Quick mode creates), or the deployment's `default_profile` when there is none; they do not count as keys using it. For example, a "Coding" profile might recall memories, resources and skills, while a "Chat" profile recalls only memories, with a smaller budget.

In embedded dev mode, startup seeds `default_profile` into the `default` account once; later edits to `ov.conf` do not change that saved profile, so edit it in Studio.

Besides its name and the [Let clients override settings](#client-overrides) switch, a profile has four sections. The defaults below are also the recommended settings:

| Section | What it does | Main settings and defaults |
| --- | --- | --- |
| Recall memory | Searches OpenViking with each new user message and appends what is relevant to that message. | Sources: memories, resources, skills · Budget per message: 1,600 tokens · Budget per context window: 30,000 tokens · Relevance threshold: 0.35 · Time limit: 2 s |
| Save conversations | Writes each finished turn to an OpenViking session and commits it, so OpenViking can extract memories. | Commit when idle for: 600 s · Commit after: 20,000 tokens · Keep recent messages: 10 |
| Long conversations | When a conversation nears the context window, has the same model write a summary that replaces the earlier messages. | Compaction: on · Compact at: 0.9 of the window · Summary length: 8,000 tokens · Agent-managed context windows: off |
| OpenViking tools | Lets the model use OpenViking's tools while it answers. | On · every tool selected; see [OpenViking tools](04-openviking-tools.md) |

Each section has its own switch, and Long conversations has one each for compaction and agent-managed context windows. Recall and saving are independent: a profile can recall without saving, or save without recalling.

Saved changes reach only conversations that start afterwards; see [When changes take effect](06-manage-in-studio.md#when-changes-take-effect).

### Create, duplicate or delete a profile {#manage-profiles}

1. Open the **Profiles** tab. While it is empty, choose **Create with recommended settings** to create a profile named "Default" in one click, or **Customize** to open the editor first. Once a profile exists, use **New profile** instead.
2. In the editor, enter a **Name**; it is shown when someone chooses a profile for a gateway key. Below it, **Let clients override settings** is on; see [Let clients override settings](#client-overrides). Change the sections described on this page as needed.
3. Choose **Create profile**, or **Save** when you edit an existing one. You should see a card for the profile that summarizes each section and counts the keys that use it (**Not used by any key** at first).

Other actions:

- **Duplicate**, on a card or in the editor's **More actions** (⋯) menu, opens a copy named "Copy of …". It is the quickest way to make a variant.
- **Reset to recommended**, in the same menu, restores the recommended settings of the four sections; the name and **Let clients override settings** stay as they are. Save to apply them.
- A profile that keys use cannot be deleted. Revoke those keys first, or, if they are still needed, issue replacements that use another profile.
- While Studio is read-only, cards show **View** instead of **Edit**, and creating, duplicating and deleting are unavailable; see [Unlock editing](06-manage-in-studio.md#unlock-editing).

## Recall memory {#recall-memory}

The **Recall memory** section (on by default) controls [recall](../guides/15-gateway.md#key-terms): for each new user message, the gateway searches OpenViking and appends what is relevant to that message. What the model then receives is shown in [What the model sees](01-how-it-works.md#what-the-model-sees). The settings, in the order the editor shows them:

| Setting | Default | What it controls |
| --- | --- | --- |
| **User profile at conversation start** | On | Gives the model your OpenViking user profile when a conversation starts. |
| **Soul at conversation start** | On | Gives the model your `soul.md`, the standing instructions you keep in OpenViking, when a conversation starts, and tells the model to follow it. The soul is added whole. |
| **Opening context budget** | 4,000 tokens | The most the profile and the memory and skill catalogs may use at the start. The soul is not counted. |
| **Show recall summary** | Off | Starts the reply with a one-line summary; see [Recall summary](#recall-summary). |
| **Sources** | Memories, resources, skills | What recall searches. |
| **Budget per message** | 1,600 tokens | The most memory added to one message. |
| **Budget per context window** | 30,000 tokens | The most memory added within one context window. |
| **Relevance threshold** | 0.35 | Only results scoring at least this much, from 0 to 1, are added. Higher means fewer but closer matches. |
| **Time limit** | 2 s | How long the search may take. |
| **Query length** (under **Advanced settings**) | 8,000 characters | How much of the message is used as the search query. |
| **Peer scope** (under **Advanced settings**) | All peers, current peer first | Which peers' memories recall and OpenViking tools can see besides the user's own. |
| **Limit by category** (under **Advanced settings**) | Off | The most entries each category may add. |

How the settings work together:

- **Opening context.** **User profile at conversation start** and **Soul at conversation start** work independently of recall and of each other. When the OpenViking `read` tool (`openviking_read`) is offered, memory and skill catalogs are added at the start as well. Skills get at most a quarter of the **Opening context budget**, and 0 leaves the profile and catalogs out. The soul is added whole and is not counted against the budget; only its own switch leaves it out. This budget does not count against the recall budget; see [The opening context](01-how-it-works.md#the-opening-context).
- **What is added.** OpenViking selects and formats the recalled content within the budget. It may also return just a URI for the model to read later.
- **Search query.** The query is the user message with client noise removed, cut to **Query length** characters. Messages shorter than 3 characters are not searched.
- **Budgets.** Each message gets at most the smaller of **Budget per message** and what is left of **Budget per context window**. With less than 64 tokens left, the search is skipped. The budget per context window starts over whenever the conversation is compacted, by the gateway or by the client. These token counts are conservative estimates, not the provider's billing counts.
- **Turning recall off.** Setting **Budget per context window** to 0 turns recall off; **Budget per message** is at least 64.
- **Time limit.** If OpenViking does not answer within the **Time limit**, the message goes to the model without memory and does not get it later, even if the client retries.
- **Peer scope.** It matters mainly for keys whose requests name a [peer](03-connect-clients.md#peer). **All peers, current peer first** adds the request's peer's memories to the user's own and can still bring back other peers' with a small score penalty. **Current peer only** keeps other peers' memories out of recall and out of OpenViking tools; a request without a peer recalls as with the default. See [Recall only the current peer](03-connect-clients.md#peer-scope).
- **Limit by category.** It sets a maximum number of entries for each of six categories: events, entities, preferences, experiences, resources and skills. Only categories above 0 are searched. When it is off, all sources are ranked together.

### Recall summary {#recall-summary}

The recall summary is a short note at the start of the reply that names what OpenViking added to a new user message, or says why recall failed. It is off by default; turn on **Show recall summary** in the **Recall memory** section of the [context profile](#context-profiles). The model never sees it. For example:

```text
> OpenViking context: soul, user profile, memory index, skill list, earlier sessions
> OpenViking recall: 4 items (3 memories, 1 resource) — booking_duplicate_handling, user_lang_pref, +2 more
```

- The `context` line appears only in the first reply of a conversation. It lists what the opening actually contained: the soul, the user profile, the memory catalog, the skill catalog, and where earlier parts of the conversation are stored in OpenViking.
- The `recall` line counts the recalled entries by kind and names up to three. If the search fails, it gives the reason instead, for example `> OpenViking recall failed: OpenViking unavailable`. It is left out when nothing was found, recall is off or the budget is used up, so after the first reply such messages get no summary.
- Tool steps and other follow-up requests show no summary. When the client retries the same message, the summary is the same as the first time.
- In Anthropic Messages the summary is its own text block, in Responses its own assistant message, and in Chat Completions the start of the reply text. Requests that ask for structured output, and Chat Completions requests with `n` above 1, get no summary.
- When the client sends the reply back with the next message, the gateway removes the summary before forwarding, and it is not saved to OpenViking.

## Save conversations {#save-conversations}

The **Save conversations** section (on by default) controls [saving](../guides/15-gateway.md#key-terms): the gateway writes each finished turn to an OpenViking session as soon as its reply ends, and commits the session, so OpenViking archives the saved messages and extracts memories from them. When a turn counts as finished is explained in [When conversations are saved](01-how-it-works.md#when-conversations-are-saved).

| Setting | Default | What it controls |
| --- | --- | --- |
| **Commit when idle for** | 600 s | How long after the conversation's last finished turn everything left in the session is committed. |
| **Commit after** | 20,000 tokens | How many uncommitted tokens at the end of a turn make the session be committed at once. |
| **Keep recent messages** | 10 | How many of the latest messages a **Commit after** commit leaves in the session. |

- By default the gateway makes both commits, which works with any OpenViking server. It creates the OpenViking session with OpenViking's own automatic commits off, checks **Commit after** when each turn ends, and times **Commit when idle for** for each conversation; each newer finished turn starts the wait again, and a save in the middle of a turn or a failed save leaves the waiting commit as it is. After each commit, OpenViking extracts memories in the background.
- The idle commit archives everything left in the session and keeps no recent messages; **Keep recent messages** applies only to **Commit after** commits and counts single messages.
- A failed idle commit stays due and is tried again every minute until it succeeds, except while OpenViking is marked unavailable. One that OpenViking refuses, because it rejects the key or no longer has the session (HTTP 401, 403 or 404), is dropped instead, and the conversation's next finished turn starts a new wait. A failed **Commit after** commit is not retried by itself; the idle commit or the next turn's threshold check covers it. Commit failures appear only in the gateway log (`could not commit`) and do not change the request record.
- The idle commit's due time and the ID of the stored key record it uses are recorded with the conversation's saving state in the gateway storage, without any conversation text, so a restart, a crash or a replaced pod does not lose a waiting commit: every gateway process looks once a minute for idle commits that have been due for a minute and makes them, so such a commit comes a minute or two after its due time. An idle commit always takes the OpenViking key from the stored key record at the time it is made: when that record is gone by then (the key was revoked or expired), the commit is dropped, and the conversation's next finished turn starts a new wait. At shutdown the gateway neither makes nor waits for idle commits.
- When the session already exists (for example, created by an earlier gateway version or under the other commit mode), the gateway sets its automatic commit policy (`auto_commit_policy`) to the current mode's settings on the conversation's first save, and again on the first save after `openviking_auto_commit` changes.
- On OpenViking Server 0.5.0+, the session follows the user's own memory policy by default, including its [memory type](../api/16-memory.md) limits, and the gateway's commits never write Working Memory; on older servers the gateway creates the session with Working Memory off. To set a session policy with specific types, set `capture_memory_types` to their names, for example `["preferences","events"]`, with the management API or the `X-OpenViking-Context` header; an empty list saves conversations without extracting memories. Studio has no control for this setting and keeps its value when you save the profile. A name OpenViking does not know makes every save of the conversation fail; see [Configuration reference](13-configuration.md#profile-saving).

**Leaving commits to OpenViking.** With `openviking_auto_commit: true` in the [gateway configuration](13-configuration.md#gateway-fields), the gateway neither commits nor keeps timers: it writes the three settings into the session's `auto_commit_policy`, and OpenViking's session auto-commit makes the commits. It is meant for OpenViking servers that have session auto-commit enabled. The one exception comes right after the setting is switched on: idle commits that were already waiting are still made by the gateway, because OpenViking does not commit those sessions until their conversations save another turn. The settings mean the same, with these differences from the default mode:

- **Commit after** works on every server. When a save crosses it while an earlier commit of the session is still extracting memories, OpenViking skips that commit; the next save or the idle commit makes it.
- The idle commit runs only when the OpenViking server has `memory.session_auto_commit.enabled: true` (`memory.session_auto_commit.idle_enabled` on 0.4.x servers), which is off by default in the open-source server; see [Session Auto Commit Configuration](../guides/01-configuration.md#session-auto-commit-configuration). Without it, a conversation that stays below **Commit after** is saved as messages but never archived, so no memories are extracted from it.
- OpenViking requires **Commit when idle for** to be at least 300 s; smaller values count as 300 s. With the server's default `local` idle backend (`idle_backend: local`), OpenViking looks for quiet sessions every `check_interval_seconds` (600 by default), so the idle commit can come up to one interval later; the `redis` backend commits on time.
- On OpenViking Server 0.5.0+, Working Memory follows the user's own memory policy, which leaves it off unless the user turns it on; in the default mode the gateway's commits never write Working Memory.

## Long conversations and OpenViking tools {#long-conversations-and-tools}

The last two sections of the editor each have their own page:

- **Long conversations** holds **Compaction** with **Compact at** and **Summary length**, the experimental **Agent-managed context windows** with **Soft reminder at** and **Hard reminder at**, and **Default context window** under **Advanced settings**. See [Long conversations in the gateway](05-long-conversations.md#long-conversations); for models with a smaller window, set the window as described in [Set the model's context window](05-long-conversations.md#context-window).
- **OpenViking tools** holds the **Tools** list and **Show tool calls**, with **Rounds per request**, **Time limit per call**, **Result size**, **Total time** and **Token budget** under **Advanced settings**. See [Choose tools](04-openviking-tools.md#choose-tools) and [Limits](04-openviking-tools.md#limits). The gateway runs the selected tools without the client's permission prompts; before you leave tools that change data selected, read [Risks and controls](04-openviking-tools.md#risks-and-controls).

The API name and range of every setting are in the [Configuration reference](13-configuration.md#context-profile-settings).

## Let clients override settings {#client-overrides}

**Let clients override settings**, below the profile's **Name**, is on by default. It lets a client change the profile's settings for a conversation: the client sends the fields to change as a JSON object in an `X-OpenViking-Context` request header, with the API names from the [Configuration reference](13-configuration.md#context-profile-settings). For example:

```text
X-OpenViking-Context: {"recall":false,"max_tokens":800,"context_types":["memory"]}
```

- **What it can change.** Every setting except `name` and `client_overrides`. Fields the header leaves out keep the profile's values. Tool access only narrows: `disabled_tools` in the header adds to the tools the profile unchecks, and `gateway_tools` can turn OpenViking tools off but not back on, so `{"disabled_tools":[]}` or `{"gateway_tools":true}` offers nothing the profile withholds.
- **When it applies.** As with a saved profile change, a conversation keeps the settings it started with: only the header on a conversation's first request shapes it. Later headers are still checked, so an invalid one, or any header once the switch is off, still fails.
- **Errors.** With the switch off, any request that carries the header gets 403 `context_override_not_allowed` instead of being served with the saved settings. With it on, the request gets 400 `context_override_invalid` when the header:
  - is not a JSON object;
  - names an unknown field, `name` or `client_overrides`;
  - holds a value out of range.
- **In the request log.** [Request details](10-overview-and-requests.md#request-details) list the names of the fields the client sent in the header, never their values.

The switch defaults on, so anyone holding a key that uses the profile can change any field, including budgets, and withhold more tools. On the `default` profile, that includes every client using an [OpenViking API key directly](09-gateway-keys.md#openviking-keys). Turn it off on profiles that must stay locked. How each client sends the header is in [Override profile settings](03-connect-clients.md#override-profile-settings).

## Next steps {#next-steps}

- [Issue a gateway key](09-gateway-keys.md#issue-a-key) that uses the profile.
- Check recall and saving per request in the [request log](10-overview-and-requests.md#requests).
- If memory never shows up, see [No memory is added](12-troubleshooting.md#no-memory-is-added).
- Follow one turn end to end in [How OpenViking Gateway works](01-how-it-works.md#what-happens-once-you-connect).
