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
| Save conversations | Writes finished turns to an OpenViking session and commits it, so OpenViking can extract memories. | Save the latest reply after: 600 s · Commit after: 20,000 tokens · Keep recent messages: 10 |
| Long conversations | When a conversation nears the context window, has the same model write a summary that replaces the earlier messages. | Compaction: on · Compact at: 0.9 of the window · Summary length: 8,000 tokens · Agent-managed context windows: off |
| OpenViking tools | Lets the model use OpenViking's tools while it answers. | On · read-only tools selected; see [OpenViking tools](04-openviking-tools.md) |

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
| **Opening context budget** | 4,000 tokens | The most the profile and the memory and skill catalogs may use at the start. |
| **Show recall summary** | Off | Starts the reply with a one-line summary; see [Recall summary](#recall-summary). |
| **Sources** | Memories, resources, skills | What recall searches. |
| **Budget per message** | 1,600 tokens | The most memory added to one message. |
| **Budget per context window** | 30,000 tokens | The most memory added within one context window. |
| **Relevance threshold** | 0.35 | Only results scoring at least this much, from 0 to 1, are added. Higher means fewer but closer matches. |
| **Time limit** | 2 s | How long the search may take. |
| **Query length** (under **Advanced settings**) | 8,000 characters | How much of the message is used as the search query. |
| **Limit by category** (under **Advanced settings**) | Off | The most entries each category may add. |

How the settings work together:

- **Opening context.** **User profile at conversation start** works independently of recall. When the OpenViking `read` tool (`openviking_read`) is offered, memory and skill catalogs are added at the start as well. Skills get at most a quarter of the **Opening context budget**, and 0 leaves all of it out. This budget does not count against the recall budget; see [The opening context](01-how-it-works.md#the-opening-context).
- **What is added.** OpenViking selects and formats the recalled content within the budget. It may also return just a URI for the model to read later.
- **Search query.** The query is the user message with client noise removed, cut to **Query length** characters. Messages shorter than 3 characters are not searched.
- **Budgets.** Each message gets at most the smaller of **Budget per message** and what is left of **Budget per context window**. With less than 64 tokens left, the search is skipped. The budget per context window starts over whenever the conversation is compacted, by the gateway or by the client. These token counts are conservative estimates, not the provider's billing counts.
- **Turning recall off.** Setting **Budget per context window** to 0 turns recall off; **Budget per message** is at least 64.
- **Time limit.** If OpenViking does not answer within the **Time limit**, the message goes to the model without memory and does not get it later, even if the client retries.
- **Limit by category.** It sets a maximum number of entries for each of six categories: events, entities, preferences, experiences, resources and skills. Only categories above 0 are searched. When it is off, all sources are ranked together.

### Recall summary {#recall-summary}

The recall summary is a short note at the start of the reply that names what OpenViking added to a new user message, or says why recall failed. It is off by default; turn on **Show recall summary** in the **Recall memory** section of the [context profile](#context-profiles). The model never sees it. For example:

```text
> OpenViking context: user profile, memory index, skill list, earlier sessions
> OpenViking recall: 4 items (3 memories, 1 resource) — booking_duplicate_handling, user_lang_pref, +2 more
```

- The `context` line appears only in the first reply of a conversation. It lists what the opening actually contained: the user profile, the memory catalog, the skill catalog, and where earlier parts of the conversation are stored in OpenViking.
- The `recall` line counts the recalled entries by kind and names up to three. If the search fails, it gives the reason instead, for example `> OpenViking recall failed: OpenViking unavailable`. It is left out when nothing was found, recall is off or the budget is used up, so after the first reply such messages get no summary.
- Tool steps and other follow-up requests show no summary. When the client retries the same message, the summary is the same as the first time.
- In Anthropic Messages the summary is its own text block, in Responses its own assistant message, and in Chat Completions the start of the reply text. Requests that ask for structured output, and Chat Completions requests with `n` above 1, get no summary.
- When the client sends the reply back with the next message, the gateway removes the summary before forwarding, and it is not saved to OpenViking.

## Save conversations {#save-conversations}

The **Save conversations** section (on by default) controls [saving](../guides/15-gateway.md#key-terms): the gateway writes finished turns to an OpenViking session and commits it, so OpenViking can extract memories. How the gateway decides that a turn is finished is explained in [When conversations are saved](01-how-it-works.md#when-conversations-are-saved).

| Setting | Default | What it controls |
| --- | --- | --- |
| **Save the latest reply after** | 600 s | How long the latest turn waits for a follow-up before the gateway saves it and commits the session. |
| **Commit after** | 20,000 tokens | How many tokens may wait in the session before it is committed. |
| **Keep recent messages** | 10 | How many messages stay in the session after a commit. |

- A turn is saved when the next user message arrives. **Save the latest reply after** covers the last turn, so short conversations get committed too.
- The session is committed whenever **Commit after** tokens are waiting. After each commit, OpenViking extracts memories in the background.
- After a commit, the session keeps the newest whole turns, at least **Keep recent messages** messages.

## Long conversations and OpenViking tools {#long-conversations-and-tools}

The last two sections of the editor each have their own page:

- **Long conversations** holds **Compaction** with **Compact at** and **Summary length**, the experimental **Agent-managed context windows** with **Soft reminder at** and **Hard reminder at**, and **Default context window** under **Advanced settings**. See [Long conversations in the gateway](05-long-conversations.md#long-conversations); for models with a smaller window, set the window as described in [Set the model's context window](05-long-conversations.md#context-window).
- **OpenViking tools** holds the **Tools** list and **Show tool calls**, with **Rounds per request**, **Time limit per call**, **Result size**, **Total time** and **Token budget** under **Advanced settings**. See [Choose tools](04-openviking-tools.md#choose-tools) and [Limits](04-openviking-tools.md#limits). The gateway runs the selected tools without the client's permission prompts; read [Risks and controls](04-openviking-tools.md#risks-and-controls) before checking tools that change data.

The API name and range of every setting are in the [Configuration reference](13-configuration.md#context-profile-settings).

## Let clients override settings {#client-overrides}

**Let clients override settings**, below the profile's **Name**, is on by default. It lets a client change the profile's settings for a conversation: the client sends the fields to change as a JSON object in an `X-OpenViking-Context` request header, with the API names from the [Configuration reference](13-configuration.md#context-profile-settings). For example:

```text
X-OpenViking-Context: {"recall":false,"max_tokens":800,"context_types":["memory"]}
```

- **What it can change.** Every setting except `name` and `client_overrides`. Fields the header leaves out keep the profile's values. `{"disabled_tools":[]}` offers every OpenViking tool, including the ones that change data and any the server adds later.
- **When it applies.** As with a saved profile change, a conversation keeps the settings it started with: only the header on a conversation's first request shapes it. Later headers are still checked, so an invalid one, or any header once the switch is off, still fails.
- **Errors.** With the switch off, any request that carries the header gets 403 `context_override_not_allowed` instead of being served with the saved settings. With it on, the request gets 400 `context_override_invalid` when the header:
  - is not a JSON object;
  - names an unknown field, `name` or `client_overrides`;
  - holds a value out of range.
- **In the request log.** [Request details](10-overview-and-requests.md#request-details) list the names of the fields the client sent in the header, never their values.

The switch defaults on, so anyone holding a key that uses the profile can change any field, including budgets and write tools. On the `default` profile, that includes every client using an [OpenViking API key directly](09-gateway-keys.md#openviking-keys). Turn it off on profiles that must stay locked. How each client sends the header is in [Override profile settings](03-connect-clients.md#override-profile-settings).

## Next steps {#next-steps}

- [Issue a gateway key](09-gateway-keys.md#issue-a-key) that uses the profile.
- Check recall and saving per request in the [request log](10-overview-and-requests.md#requests).
- If memory never shows up, see [No memory is added](12-troubleshooting.md#no-memory-is-added).
- Follow one turn end to end in [How OpenViking Gateway works](01-how-it-works.md#what-happens-once-you-connect).
