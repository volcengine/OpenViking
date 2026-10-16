---
description: Give any API-key model client OpenViking memory by pointing it at OpenViking Gateway.
---

# OpenViking Gateway

OpenViking Gateway is a model API proxy. Any client that lets you change its base URL can use it to get OpenViking memory. You change two settings: the base URL becomes the gateway address, and the provider's API key becomes a gateway key. There is no plugin to install and no code or prompt to change.

For an animated walkthrough of how the gateway works, see [openviking.ai/gateway](https://openviking.ai/gateway).

While it forwards a request, the gateway does three things:

| Capability | What the gateway does |
| --- | --- |
| Automatic recall | For each new user message, the gateway searches OpenViking, appends the relevant memory to that message, and then passes it to the model. |
| Model-driven memory | The model can call OpenViking tools to search, read, write and import material. The gateway runs the tools, so the client does not declare or implement any of them. |
| Saving and compaction | The gateway saves conversations to OpenViking, which extracts new memories from them. When a conversation nears the model's context window, the gateway has the same model write a summary that replaces the earlier part. |

**Who it is for**: clients that cannot install a plugin or MCP server, such as chat apps, SDK scripts and low-code platforms, and teams that want to manage providers, keys and memory settings in one place. **Not for**: clients signed in directly with a subscription (use a [subscription proxy upstream](#custom-upstreams) instead), and clients such as Cursor and Trae whose model calls leave from the vendor's servers.

The gateway supports three model APIs: Anthropic Messages, OpenAI Chat Completions and OpenAI Responses (when every request carries the full history). It forwards each request to a model endpoint you configure, called an **upstream** in these docs. A request goes out in the same API it came in; the gateway never converts between APIs.

OpenViking Gateway is in beta. This page covers how it works and how to connect clients. For deployment, managing it in Studio and troubleshooting, see [OpenViking Gateway deployment and operations](22-gateway-operations.md).

> **Note**: OpenViking Gateway and VikingBot Gateway (`vikingbot gateway`) are different components. VikingBot Gateway handles remote access and chat platforms.

## Custom upstreams

Any service that speaks one of the three APIs can be an upstream, not only a model provider. Two common uses:

- **Billing, quotas and load balancing.** The gateway does none of these. If you need them, put a service such as LiteLLM or new-api behind the gateway and add it as an upstream.
- **Subscription quota.** Clients cannot sign in to the gateway with a subscription, but you can add a reverse proxy such as [CLIProxyAPI](https://github.com/router-for-me/CLIProxyAPI) as an upstream. It exposes subscription accounts such as ChatGPT (Codex) and Claude as an API, so Codex, for example, can use a ChatGPT subscription through the gateway. Tibo, who leads Codex at OpenAI, has [publicly walked through](https://x.com/thsottiaux/status/2076119366647894371) connecting a Codex subscription this way. **Check that this complies with your provider's terms.**

To add one, choose *Generic* as the provider; see [Upstreams](22-gateway-operations.md#upstreams).

## Architecture at a glance

![OpenViking Gateway in its two forms. Embedded: clients call the OpenViking Server port, which passes everything under /gateway (model APIs, uploads and gateway management) to the gateway in the same process; the gateway searches and saves memory over loopback HTTP with the user's own OpenViking key. Standalone: openviking-gateway on its own port serves the same /gateway paths and a gateway-only Studio, and calls an existing OpenViking service over HTTP(S). Either way the gateway forwards each request to the upstream model provider in the same API, sessions and memories live in OpenViking, and the gateway keeps only two SQLite files (encrypted when OpenViking encryption is on)](../../images/gateway/architecture.en.svg)

- **Where it runs**: once enabled, inside OpenViking Server on the server's port. You can also run `openviking-gateway` on its own against an existing OpenViking service, open source or OpenViking Cloud. Either way, every gateway URL is under `/gateway`, such as `/gateway/v1`, so clients use the same paths. See [Deployment options](22-gateway-operations.md#deployment-options).
- **How it relates to OpenViking**: the gateway only calls OpenViking's public APIs. Embedding and memory extraction happen in OpenViking.
- **Where the data lives**: sessions and memories live in OpenViking. The gateway host keeps only two SQLite files, encrypted when OpenViking encryption is on, one for upstreams, keys and settings and one for conversation state. See [Security and data](22-gateway-operations.md#security-and-data).
- **When something fails**: if OpenViking is down, conversations go on without memory and saving retries on its own. If the gateway is down, every model call that goes through it fails.
- **What you manage**: users in an account share its **upstreams** (model endpoints and their API keys) and **context profiles** (memory settings such as the recall budget, whether conversations are saved and whether OpenViking tools are offered). Each gateway key binds one OpenViking user, one context profile and a set of upstreams. Who may change what is in the [access matrix](../api/25-gateway.md#credentials-and-account-scope).

## What happens once you connect

Two terms first. **Recall** means searching OpenViking for memory related to the current message and appending it to that message. **Prompt caching** is a provider billing feature: the part of a request that matches the start of the previous request is billed at the cached rate, and everything from the first differing byte on is billed at the full rate. The cached rate is usually a small fraction of the full rate; see each provider's pricing.

What you get: relevant memory on every new message, your user profile at the start of a new conversation, conversations saved to OpenViking and turned into new memories, and automatic compaction of long conversations. With OpenViking tools on, the model can also search, read and write memory itself (see the [next section](#agentic-memory-for-any-client)).

What it costs: the first model call of each new message can wait up to 2 extra seconds; added memory is billed as input tokens, mostly at the cached rate on later requests; and the turn that triggers compaction makes one extra model call.

![What happens in one turn: the gateway recognizes the conversation and the request type, puts earlier memory back exactly as it was, recalls only for the new message, forwards the request to the upstream in the same API and queues the turn for saving; below, how replay keeps the prompt cache hitting, and how saving and compaction work across turns](../../images/gateway/one-turn.en.svg)

The gateway recognizes requests from the same conversation on its own (see [How conversations are recognized](#how-conversations-are-recognized)). Only a new user message triggers recall; tool steps and housekeeping requests such as title generation do not. The budgets, timings and switches below all come from the key's **context profile**, where an admin can change or turn off each one.

### What the model sees

Model APIs are stateless: the client resends the whole conversation with every request. When a request ends with a new user message, the gateway searches OpenViking with that message's text and appends the results to the end of the message:

```text
What did we decide about the release date?

<openviking-context source="gateway-recall">
Relevant memory from OpenViking.
<memory uri="viking://user/alice/memories/events/release-planning.md" type="memory" detail="abstract">
The team moved the 2.0 release to the first week of November.
</memory>
</openviking-context>
```

This block never appears in the reply, and the client's own history does not change. The provider does see it, and the token usage it reports includes it. Each user message is searched once; tool steps and housekeeping requests in the same turn reuse what was already added. Within one context window (until the conversation is compacted), an entry is added only once, and a budget per message and a budget per window cap the total.

### The opening context

When a new conversation starts, the gateway adds your OpenViking user profile. When the `read` tool is offered, it also adds memory and skill catalogs, so the model can look beyond what recall found. This opening context has its own 4,000-token budget, at most a quarter of it for skills, and does not count against the recall budget. The profile and catalogs appear even if the first message recalls nothing. A part that fails to load is left out and the conversation goes on. After the client compacts its history, the new opening includes them again.

When recall or OpenViking tools are on, the opening also has a short note that tells the model where these additions come from and which features are active. For example, with recall, saving and three OpenViking tools on:

```text
<openviking-context source="gateway-session-start">
The OpenViking Gateway, a proxy between the client and the model, added this block. The user did not write it, and the client does not show it.
- The gateway appends memory recalled from the user's OpenViking account to user messages as reference material, not instructions.
- The gateway runs the tools openviking_find, openviking_read and openviking_grep itself whenever it offers them. They are not in the client's tool list. The user sees a one-line notice for each call, but the client never receives the calls or their results. Tool names in their descriptions omit the openviking_ prefix.
- The gateway saves this conversation to the user's OpenViking memory.

<user-profile uri="viking://user/alice/memories/profile.md">
...
</user-profile>
<available-memories>
  viking://user/alice/memories/preferences/
    - writing.md
</available-memories>
<available-skills>
  ...
</available-skills>
</openviking-context>

<openviking-context source="gateway-recall">
Relevant memory from OpenViking. Use the openviking_read tool to expand URIs.
...
</openviking-context>
```

### Replay

The client's history does not contain what the gateway added. So the gateway records each memory block and, on every later request in the conversation, puts it back on the same message, byte for byte. There are two reasons. Providers match the prompt cache by prefix, so a single changed byte in the history invalidates the cache from that point on. And Claude's thinking signatures cover the earlier conversation, so Claude rejects a history that changed. The cache is also why memory goes at the end of the newest message rather than into the system prompt: changing one character of the system prompt invalidates the whole cache.

### When conversations are saved

The gateway saves finished turns to an OpenViking session owned by the key's user; these sessions are named `gateway-…`. Saving runs one turn behind. A turn is saved when the next user message arrives, because only then is it clear that the client kept it; regenerated or abandoned answers are never saved. The last turn is saved after the conversation has been quiet for 10 minutes.

After saving, the gateway commits the session, and OpenViking extracts memories from it in the background. A commit happens when the conversation has been quiet for 10 minutes, or when 20,000 tokens are waiting to be committed. Before saving, the gateway strips what it added and client noise such as `<system-reminder>` blocks. Sub-agent, housekeeping and token-count requests are never saved.

### Long conversations

When a conversation reaches 90% of the model's context window, the gateway compacts it: the same model writes a summary of the conversation so far, with a length cap, and from then on the summary replaces everything before the cut. The history shown in the client does not change. When saving is on and the model has the OpenViking `grep` and `read` tools, the summary ends with directions for finding details in the saved conversation. Each compaction costs one extra model request and one provider cache miss.

The gateway needs to know the model's context window. If neither the upstream nor the context profile sets it, the gateway assumes 1,000,000 tokens. For a model with a smaller window, ask an admin to set the real one; otherwise the provider rejects long conversations as too long before compaction starts. Details are in [Long conversations](22-gateway-operations.md#long-conversations).

## Agentic memory for any client

With automatic recall, the gateway guesses what the model needs. With OpenViking tools on, the model decides for itself what to search, read and remember. The gateway runs the tools, so this works even when the client's request has no tools at all.

![One reply with OpenViking tools: the gateway adds recalled memory and the OpenViking tool definitions to the client's request; when the model calls an OpenViking tool, the gateway runs it with the user's OpenViking key, feeds the result back and asks the model again until it gives a final answer; the client receives one continuous reply with notice lines, and the tool rounds are replayed unchanged in the next turn](../../images/gateway/tool-loop.en.svg)

- **Several steps in one reply**: the gateway intercepts the model's OpenViking tool calls, runs them with the user's OpenViking key, feeds the results back and asks the model again, until the model stops calling them. The client receives one continuous reply, streaming or not.
- **The client's own tools still work**: in the same reply, the model can call both OpenViking tools and client tools such as Bash. Client tool calls go to the client unchanged and pass through its permission prompts as usual.
- **The next turn remembers**: tool calls and results are not in the client's history, so the gateway stores these rounds and replays them unchanged on the next turn. The model remembers what it found, and the prompt cache keeps working.

### What each kind of client gets

| Client | Without the gateway | Through the gateway, with OpenViking tools on |
| --- | --- | --- |
| Chat apps, such as Cherry Studio and Open WebUI | To use memory you set up MCP or write tools yourself; a plain chat only exchanges text. | The model searches memory and reads the original text by URI. With `remember` checked, it notes down what the user asks it to remember; with `add_resource` checked, it imports files the user attaches. This works even in a plain chat mode with no tools. |
| SDK scripts and low-code platforms | One question, one answer; multi-step work needs a loop you write. | One request covers "search → read → grep for exact matches → answer", with no code changes. |
| Coding agents such as Claude Code and Codex (without the OpenViking plugin or MCP server) | Their own tools, but no OpenViking. | OpenViking tools and client tools such as Bash mix in the same reply. If you want to approve each tool call, the plugin or MCP server is still the better choice. |

### Which tools the model can use

The tool list comes straight from the MCP tools your OpenViking server provides, each with an `openviking_` prefix. Tools that OpenViking adds later become available on their own. The common groups:

| Purpose | Tools | What the model uses them for |
| --- | --- | --- |
| Search | `find`, `search`, `grep`, `glob` | Semantic search over memory and resources; exact lookups by regular expression or file name. |
| Browse and read | `list`, `tree`, `read` | Expanding a recalled entry to its original text by URI; browsing memory and skill directories. |
| Write | `remember`, `write`, `edit` | Storing a long-term memory; writing or editing memory files. |
| Import | `add_resource`, `add_skill` | Importing a web page or repository by URL, importing a chat attachment, creating a skill from text, all without a shell on the client. |
| Delete and permissions | `forget`, `set_acl` and others | Deleting memory permanently; changing access to shared resources. |

### Defaults

New context profiles have OpenViking tools on, but only the read-only tools for searching, browsing and reading are selected. Tools that change data, such as `remember`, `add_resource` and `forget`, stay off until an admin checks them. Rounds, total time and the token budget are unlimited by default, so the model keeps using tools until it is done.

The client must send the full history every turn; with Responses it must also set `store: false`. The gateway does not offer its tools when the client forces a specific tool, asks for structured output, or already has OpenViking tools of its own. For the full default tool list, the limits, file imports and every reason tools are left out, see [OpenViking tools](22-gateway-operations.md#openviking-tools).

### What users see

The model's text streams as usual. Each OpenViking tool call leaves a one-line notice in the reply. It appears when the call starts, and `done`, `failed` or `skipped` is added when the call ends:

```text
> OpenViking find: "the release date we agreed on" — done
> OpenViking read: viking://user/alice/memories/release.md — done
```

A notice shows only the tool name and what it works on, such as the query or URI. Users never see the full arguments or the results. The model does not see the notices, and the gateway does not save them to OpenViking; an admin can turn them off in the context profile. The token usage the client receives is the sum of every model call in the reply.

### Risks and controls

> **Note**: Selected tools run without the client's permission prompts, including tools that write or delete data. A notice reports what already happened; it does not ask for approval. The model can never do more than the user's own OpenViking permissions allow.

- **What admins control**: the tools switch and the per-tool checkboxes in the context profile, the **Allow OpenViking tools** switch on each upstream, and the limits on tool rounds, total time and tokens. For profiles used by chat apps, uncheck at least `forget` and `set_acl`.
- **Gateway tools or a plugin/MCP server**: plugins and MCP servers show each call and its result in the client and ask for approval first, which makes them more transparent for Claude Code and Codex. Gateway tools are mainly for clients that cannot use a plugin or MCP server.

**Experimental**: a context profile can also let the model manage its own context window. The model gets two more tools, one that reports how full the window is and one that starts a fresh window after it writes hand-off notes, and the gateway reminds it as the window fills up. This is off by default; see [Experimental: agent-managed context windows](22-gateway-operations.md#experimental-agent-managed-context-windows).

## Gateway or plugin?

OpenViking also connects to Claude Code, Codex, OpenCode, pi, OpenClaw, Hermes and other agents through plugins (see [Agent Integrations](../agent-integrations/01-overview.md)). The two approaches complement each other, and their differences all follow from where each one runs:

| | OpenViking Gateway | Agent plugin |
| --- | --- | --- |
| Where it runs | Between the client and the model provider. It sees only the requests sent to the model. | Inside the agent. It sees the agent's sessions, events and local workspace. |
| Clients it covers | Anything that lets you set a base URL and API key: chat apps, SDK and API apps, low-code platforms, coding agents. | Agents that have an OpenViking plugin. |
| Clients it cannot cover | Clients signed in directly with a subscription (a Claude login in Claude Code, a ChatGPT login in Codex; use a [subscription proxy upstream](#custom-upstreams) instead) and clients whose model calls leave from the vendor's servers, such as Cursor and Trae. | Clients without an extension API. |
| Per-project memory | It cannot see the working directory or repository. Memory belongs to an OpenViking user, so to keep projects apart, give each project a gateway key bound to a different OpenViking user. | Detects the workspace and repository automatically. |
| Visibility | Added memory is invisible in the client. OpenViking tool calls show a one-line notice in the reply by default, without results. Studio's request log records recall counts and tool rounds, not content. Tool calls skip the client's permission prompts. | Tool calls appear in the transcript and go through the agent's permission prompts. |
| When conversations are saved | One turn behind: a turn is saved when the next message arrives, the last turn after a quiet period. | On the agent's own events, such as the end of each turn. |
| Setup and upgrades | One service for everyone, nothing to install on each machine. Upstreams, keys and memory settings are managed in one place. | Installed and upgraded on each machine. |
| Keys, data and failures | Holds provider API keys and unsaved conversation text centrally (encrypted when OpenViking encryption is on), and adds one hop to every model call. If the gateway is down, model calls through it fail. | Provider keys stay with each agent, and only saved conversations reach OpenViking. No extra service to run. |

Keep the plugin for Claude Code, Codex and pi when you need per-project memory or want to see and approve every tool call. Use the gateway for clients without a plugin, and when you want to manage providers, keys and memory settings centrally.

**When both are in use, the gateway steps aside.** If a request shows signs of an OpenViking plugin, the gateway stops adding memory, saving turns and offering OpenViking tools for that conversation, and only forwards it. That keeps the same content from being added or saved twice. It looks for two signs:

- memory blocks that plugins insert: `<openviking-context>`, `<relevant-memories>`, `<relevant-memory>` or `<memory-context>`;
- an `X-OpenViking-Plugin` request header, which plugin and app authors can send to opt out explicitly.

The decision holds for the rest of that conversation and does not affect others. To use gateway memory again, start a new conversation without the plugin. Studio's request log flags these requests **OpenViking plugin in use**.

OpenViking MCP tools alone, without a plugin, do not count. The gateway still adds memory and saves turns; it only stops offering its own OpenViking tools, so the model does not see two sets with the same names (the request log shows `tool_name_collision`).

## Quick start

Start with a working OpenViking installation whose `ov.conf` has embedding and VLM models set up (see [Quick Start](../getting-started/02-quickstart.md)). The gateway is part of the core package; there is nothing extra to install. Pick the path that matches your server. To run the gateway against an existing OpenViking service without changing its configuration, see [Standalone deployment](22-gateway-operations.md#standalone-deployment).

### Local server (no API key)

For a server bound to `127.0.0.1` with the default local authentication.

1. **Turn the gateway on.** Run `openviking-server init` and answer yes to **Enable OpenViking Gateway on the same port?**. With an existing configuration, choose **Update gateway** from the menu. You can also merge `{"gateway": {"enabled": true}}` into `ov.conf`.
2. **Start or restart** `openviking-server`.
3. **Open the gateway in Studio.** Open <http://127.0.0.1:1933/studio/> and choose **OpenViking Gateway** in the sidebar. No key is needed. If the default account has no upstreams yet, the gateway imports the server's VLM settings as upstreams at startup and creates a context profile, so the first two steps of the **Get started** checklist on the Overview tab are already done. An imported Coding Plan upstream needs your confirmation before use; see [Import OpenViking model settings](22-gateway-operations.md#import-openviking-model-settings). If nothing could be imported, add an upstream as described in [Upstreams](22-gateway-operations.md#upstreams).
4. **Issue a gateway key.** On the Keys tab, choose **Issue key**, enter a name, pick the context profile and an upstream, and issue it. The key reads and writes memory as the local `default` user. The **Copy your gateway key** dialog shows the full `ovgw_…` key only once, so copy it before you close the dialog.

Then [send a test request](#send-a-test-request) to `http://127.0.0.1:1933/gateway`.

### Remote server (API keys)

For a server that people reach over the network, with `api_key` authentication.

1. **Turn the gateway on.** On the server host, run `openviking-server init`, choose **Remote (0.0.0.0)** for the bind address, and answer yes to **Enable OpenViking Gateway on the same port?**. The next question, **Allow users to configure their own gateway upstreams?**, decides who may change upstreams and context profiles:
   - Yes: every signed-in user can configure their account's upstreams and context profiles.
   - No: init generates a gateway management key starting with `ovgm_`, prints it and saves it to `gateway.management_key` in `ov.conf`. Only requests that carry this key can change upstreams and context profiles; see [Gateway management key](22-gateway-operations.md#gateway-management-key).

   Then restart the server. Alternatively, sign in to Studio with the root key and choose **Enable gateway** on the OpenViking Gateway page: Studio writes `ov.conf` and restarts the server (single-worker server only).
2. **Get an OpenViking API key.** Use your own user API key, or use the root key to create an account and user as described in [Authentication](04-authentication.md).
3. **Sign in to Studio.** Open `https://ov.example.com/studio/`, enter your API key under **Connection Settings**, and choose **OpenViking Gateway** in the sidebar.
4. **Add an upstream.** On the Upstreams tab, choose **Add upstream**, pick the provider and the protocol your client uses (the test request below uses Chat Completions), paste the provider's API key and save. Choose **Test** in the upstream list to check that the gateway reaches the provider. If the tab is read-only, the deployer set a gateway management key; see [Manage the gateway in Studio](22-gateway-operations.md#manage-the-gateway-in-studio).
5. **Create a context profile.** On the Profiles tab, choose **Create with recommended settings**.
6. **Issue a gateway key** for yourself, as in step 4 of the local path. To issue keys for other users, see [Gateway keys](22-gateway-operations.md#gateway-keys).

### Send a test request

For a local server, use `http://127.0.0.1:1933/gateway` as the address. Replace `<model>` with a model the upstream serves:

```bash
export GATEWAY_URL=https://ov.example.com/gateway
export GATEWAY_KEY='ovgw_...'

curl -s "$GATEWAY_URL/v1/models" -H "Authorization: Bearer $GATEWAY_KEY"

curl -s "$GATEWAY_URL/v1/chat/completions" \
  -H "Authorization: Bearer $GATEWAY_KEY" \
  -H "Content-Type: application/json" \
  -H "X-OpenViking-Session: quickstart-1" \
  -d '{"model": "<model>", "messages": [{"role": "user", "content": "Remember that I prefer short answers."}]}'
```

The first command lists the models and aliases configured on the upstream; the list is empty if the upstream accepts any model name. The second should return a normal reply from your provider.

### Confirm that memory works

1. **The request went through the gateway.** Open the Requests tab. Your request shows as a **New message** with status 200. Once OpenViking has something relevant, the **Memory** column shows how many entries were added (for example `+3`); on a brand-new account it stays empty.
2. **The conversation is saved.** Send a second message with the same `X-OpenViking-Session` value; the first turn is saved right away. Connect Studio as the key's user, and a `gateway-…` session appears on the **Sessions** page.
3. **Memories are extracted.** After 10 quiet minutes the gateway commits the session, and OpenViking extracts memories in the background, which takes a moment. Then start a new conversation with another session value and ask something that depends on the memory, such as "How long should your answers be?". The **Memory** column should show recalled entries, and the reply should use them.

To see results sooner while you try things out, create a second context profile with a shorter **Save the latest reply after** time, issue a key with it, and start a new conversation with that key. Profile changes only reach conversations that start afterwards.

## Connect clients

Every client needs the gateway address and a gateway key. The gateway address is the server's address followed by `/gateway`. The examples use `https://ov.example.com/gateway`; replace it with your gateway address, which Studio shows at the top of the OpenViking Gateway page and on the Connect tab. Anthropic clients use the gateway address as it is, OpenAI-compatible clients add `/v1`, and Ark SDKs add `/api/v3`. The client's key must also be bound to an enabled upstream that speaks the client's protocol and serves the model it requests.

| Client | Upstream protocol | Base URL | How its conversations are recognized |
| --- | --- | --- | --- |
| [Claude Code](#claude-code) | Anthropic Messages | `https://ov.example.com/gateway` | Claude Code's session header |
| [Codex CLI](#codex-cli) | Responses | `https://ov.example.com/gateway/v1` | Codex's session header |
| [Chat clients and SDKs](#chat-clients-and-sdks) | Chat Completions (or the API your SDK uses) | `https://ov.example.com/gateway/v1` | `X-OpenViking-Session`, which you send yourself |
| [Open WebUI](#open-webui) | Chat Completions | `https://ov.example.com/gateway/v1` | An `X-OpenViking-Session` header set on the connection |
| [OpenCode](#opencode) | Chat Completions | `https://ov.example.com/gateway/v1` | OpenCode's session header |
| [pi](#pi) | Chat Completions | `https://ov.example.com/gateway/v1` | Usually matched from the conversation history |
| [Volcano Engine Ark and BytePlus ModelArk SDKs](#volcano-engine-ark-and-byteplus-modelark-sdks) | Any of the three | `https://ov.example.com/gateway/api/v3` or `https://ov.example.com/gateway/api/compatible` | Depends on the headers the client sends |

The Open WebUI, OpenCode and pi setups follow each client's documented provider settings. Treat them as a starting point and check the result: send two messages in one conversation, then expand both requests on the Requests tab. They should belong to the same conversation, and the first turn should be saved once the second message arrives.

### Claude Code

Claude Code uses Anthropic Messages. Set three environment variables before you start it:

```bash
export ANTHROPIC_BASE_URL=https://ov.example.com/gateway
export ANTHROPIC_AUTH_TOKEN='<gateway-key>'
export CLAUDE_CODE_GATEWAY_HINT_HEADERS=1
```

- Leave `/v1` off the address; Claude Code adds the path itself.
- `CLAUDE_CODE_GATEWAY_HINT_HEADERS=1` makes Claude Code label sub-agent, compaction and background requests, so the gateway does not recall memory for them or save them as conversation turns.
- Claude Code sends its own session ID, which the gateway uses to recognize the conversation, including after `--resume`.
- The upstream must accept the model names Claude Code requests: leave its model list empty, list those names, or map them to the provider's model with model aliases.
- A Claude subscription login does not work through the gateway; requests with a subscription token are rejected. Configure the upstream with a provider API key.
- If the OpenViking plugin is also installed, the gateway steps aside for those conversations (see [Gateway or plugin?](#gateway-or-plugin)). With only the OpenViking MCP server, the gateway still adds memory and leaves the OpenViking tools to the MCP server.

### Codex CLI

Codex uses the Responses API and sends the full history with every request, which is what the gateway needs. Add a provider to `~/.codex/config.toml`. The two top-level settings must come before any `[section]`:

```toml
model_provider = "openviking"
model = "<model>"

[model_providers.openviking]
name = "OpenViking Gateway"
base_url = "https://ov.example.com/gateway/v1"
wire_api = "responses"
env_key = "OPENVIKING_GATEWAY_KEY"
```

Then set the gateway key in the environment Codex runs in:

```bash
export OPENVIKING_GATEWAY_KEY='<gateway-key>'
```

- The upstream bound to the key must use Responses and serve `<model>`.
- Codex first tries a WebSocket connection. The gateway declines it, and Codex falls back to HTTP.
- Codex sends its own session ID, which the gateway uses to recognize the conversation, including after `codex resume`.
- Codex may warn that it has no metadata for an unfamiliar model name. The warning does not affect requests.
- A ChatGPT login does not work through the gateway. Configure the upstream with a provider API key.

### Chat clients and SDKs

Any client or SDK that supports the OpenAI Chat Completions API can connect. Enter these settings wherever the client asks for an OpenAI-compatible provider:

```text
Base URL: https://ov.example.com/gateway/v1
API key: <gateway-key>
Model: <model>
X-OpenViking-Session: <conversation-id>
```

The `X-OpenViking-Session` header is optional but recommended. It tells the gateway which conversation a request belongs to. Use a value that stays the same within a conversation and differs between conversations, such as your app's chat ID. Without it, the gateway matches conversations by their history; see [How conversations are recognized](#how-conversations-are-recognized).

With the OpenAI Python SDK:

```python
from openai import OpenAI

client = OpenAI(base_url="https://ov.example.com/gateway/v1", api_key="<gateway-key>")

reply = client.chat.completions.create(
    model="<model>",
    messages=[{"role": "user", "content": "What did we decide about the release date?"}],
    extra_headers={"X-OpenViking-Session": "chat-42"},
)
print(reply.choices[0].message.content)
```

SDKs for the other two APIs work the same way, as long as the upstream uses the same API as the SDK:

- Point the Anthropic SDK at `https://ov.example.com/gateway`.
- For the Responses API, use `https://ov.example.com/gateway/v1`, send the full history in every request and set `store: false`. Other Responses requests are forwarded without memory.

When you stream Chat Completions, also ask for usage (`"stream_options": {"include_usage": true}`). Otherwise the provider reports no token counts for streamed replies, Studio cannot show usage, and the gateway has to estimate how full the context window is.

### Open WebUI

In Open WebUI, add an OpenAI-compatible connection (Admin Panel → Settings → Connections) with the base URL `https://ov.example.com/gateway/v1` and the gateway key. Add these custom headers to the connection, so that each chat is its own conversation and the gateway recognizes Open WebUI's background tasks:

```json
{
  "X-OpenViking-Session": "{{CHAT_ID}}",
  "X-OpenViking-Task": "{{TASK}}"
}
```

Also set `RAG_SYSTEM_CONTEXT=true` in Open WebUI's environment. Open WebUI then puts content retrieved from attachments into the system message instead of rewriting your message for one turn, which keeps the conversation history stable between requests.

- **One connection key means one memory owner.** Every Open WebUI user who chats through this connection reads and writes the memory of the OpenViking user behind the key. Give the connection to one person, or accept that its users share memory.
- Open WebUI also sends background requests (titles, tags, follow-up suggestions) through the same connection. The gateway recognizes title and summary requests. If the request log shows other background tasks as **New message**, point Open WebUI's task model at a connection that does not go through the gateway.

### OpenCode

Add a provider to `~/.config/opencode/opencode.json`, merged with your existing settings:

```json
{
  "provider": {
    "openviking": {
      "npm": "@ai-sdk/openai-compatible",
      "name": "OpenViking",
      "options": {
        "baseURL": "https://ov.example.com/gateway/v1",
        "apiKey": "{env:OPENVIKING_GATEWAY_KEY}"
      },
      "models": {
        "<model>": {}
      }
    }
  }
}
```

Export the gateway key as `OPENVIKING_GATEWAY_KEY`, then select `openviking/<model>` in OpenCode. The upstream must use Chat Completions. If the OpenViking plugin for OpenCode is also installed, the gateway steps aside for those conversations.

### pi

Add a provider to pi's model configuration, `~/.pi/agent/models.json`:

```json
{
  "providers": {
    "openviking": {
      "baseUrl": "https://ov.example.com/gateway/v1",
      "apiKey": "$OPENVIKING_GATEWAY_KEY",
      "api": "openai-completions",
      "models": [
        {
          "id": "<model>"
        }
      ]
    }
  }
}
```

- `apiKey` reads the gateway key from the `OPENVIKING_GATEWAY_KEY` environment variable; export it before you start pi. Keep the leading `$`, or pi sends the string itself as the key.
- The upstream must use Chat Completions.
- pi usually sends no session header, so the gateway matches its conversations by their history (see [How conversations are recognized](#how-conversations-are-recognized)).
- If pi's own OpenViking extension is active, the gateway steps aside for those conversations. Use one or the other.

### Volcano Engine Ark and BytePlus ModelArk SDKs

Clients and SDKs already set up for Volcano Engine Ark, or BytePlus ModelArk (its international edition), only need the address replaced. Under `/gateway`, the gateway accepts Ark's own paths (`/api/v3/chat/completions`, `/api/v3/responses`, `/api/v3/models` and `/api/compatible/v1/messages`) as well as the standard `/v1` paths:

| Original address | Gateway address |
| --- | --- |
| `https://ark.cn-beijing.volces.com/api/v3` | `https://ov.example.com/gateway/api/v3` |
| `https://ark.ap-southeast.bytepluses.com/api/v3` | `https://ov.example.com/gateway/api/v3` |
| `https://ark.cn-beijing.volces.com/api/compatible` (Anthropic-compatible) | `https://ov.example.com/gateway/api/compatible` |

Use the gateway key in place of the Ark API key. With the Volcano Engine Ark Python SDK:

```python
from volcenginesdkarkruntime import Ark

client = Ark(base_url="https://ov.example.com/gateway/api/v3", api_key="<gateway-key>")
```

The path a client uses only decides which API it speaks. The request still goes to an upstream bound to the key that uses that API and serves the model, usually one whose provider is Volcano Engine Ark or BytePlus ModelArk (see [Upstreams](22-gateway-operations.md#upstreams)).

## How conversations are recognized

The gateway needs to know which conversation a request belongs to. A conversation keeps one context profile and one upstream, and is saved to one OpenViking session. The gateway takes the first of these request headers that is present:

1. `X-OpenViking-Session`
2. `thread-id`
3. `x-claude-code-session-id` (Claude Code)
4. `x-opencode-session-id` (OpenCode)
5. `x-session-id`
6. `session-id` (Codex CLI)

If none is present, the gateway looks at the latest assistant reply in the history. If an earlier conversation produced that reply, the request joins it; otherwise it starts a new conversation. Two conversations that only share an opening message are never merged. This handles ordinary back-and-forth chat, but retries, regenerated answers and identical conversations are ambiguous and may start a new conversation. So send `X-OpenViking-Session` whenever your client lets you set headers.

Conversations belong to the OpenViking user behind the key, separately for each API. Two gateway keys of the same user that send the same session value share one conversation; the same session value on a different API is a different conversation. The gateway removes all `X-OpenViking-*` headers before forwarding, so providers never see them.

A request that does not join an existing conversation still gets memory: new messages are recalled for, and memory added earlier is replayed, because the gateway finds it by the messages themselves. The difference is that the request starts a new conversation. Its turns are saved to a new OpenViking session, its memory budget starts from zero, and it uses the key's current context profile.

## What to expect

- **Recall waits up to 2 seconds.** The first model call of each new message waits for the OpenViking search, at most the profile's **Time limit** (2 seconds by default). If OpenViking times out or is down, the message goes to the model without memory and never gets it later.
- **Settings apply to new conversations.** A conversation keeps the context profile and upstream it started with. Long-running clients such as Claude Code and Codex can continue one conversation for days, so a profile change reaches them only when they start a new one. The gateway deletes a conversation's state after 30 days without use.
- **Changed history is saved again.** If the client edits or deletes earlier messages, regenerates an answer after it was saved, or compacts the conversation itself, the gateway saves the current full history to a new OpenViking session. Turns already saved stay in the old session.
- **Very long sessions can hit the request size limit.** After the gateway compacts a conversation, the client still sends the full history but sees small usage numbers, so clients that compact by usage rarely compact on their own. Request bodies over 32 MiB are rejected; operators can change the limit (see [Long conversations](22-gateway-operations.md#long-conversations)).
- **One key means one memory owner.** Everyone who shares a key shares the memory of the OpenViking user behind it. Issue one key per person, and separate keys per client if you want different context profiles.
- **Subscription logins are not supported.** Requests with a Claude subscription token are rejected. Configure the upstream with a provider API key.
- **Responses needs the full history.** Responses requests that rely on state stored at the provider (`previous_response_id`, `conversation`, `background`), or that do not set `store: false`, are forwarded without memory. Later lookups of those responses still go to the upstream that created them.
- **Other endpoints pass through.** Endpoints other than the three model APIs and the model list, such as embeddings, are forwarded without memory to the highest-priority upstream bound to the key that serves the requested model.

## Next steps

- [OpenViking Gateway deployment and operations](22-gateway-operations.md): deploy with Docker Compose, Helm or a standalone process; manage upstreams, context profiles and keys; troubleshoot.
- [OpenViking Gateway Management API](../api/25-gateway.md): the access matrix and management endpoints.
- [Authentication](04-authentication.md): create accounts, users and their keys.
- [Public Access & Reverse Proxy](12-public-access.md): put OpenViking behind HTTPS.
- [Agent Integrations](../agent-integrations/01-overview.md): plugins for agents that support them.
- [MCP Integration](06-mcp-integration.md): give MCP clients OpenViking tools directly.
