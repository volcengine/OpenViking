---
description: Point Claude Code, Codex, chat apps, SDKs and other clients at OpenViking Gateway, and check the connection.
---

# Connect clients to OpenViking Gateway

This page gives the exact settings for each client and a way to check that they work. Every client needs three things: the gateway address, a gateway key (or, when the deployment turns on `openviking_keys`, the user's OpenViking API key; see [Use an OpenViking API key directly](09-gateway-keys.md#openviking-keys)), and an enabled upstream bound to that key that speaks the client's protocol and serves the model it requests. No gateway key yet? Start with [Get started](02-quickstart.md).

Two limits apply to every client:

- A client cannot sign in to the gateway with a subscription login. The upstream needs a provider API key, or a subscription proxy: Codex can use a ChatGPT subscription through a CLIProxyAPI upstream (see [Use a ChatGPT subscription](#codex-chatgpt-subscription)). For other subscription services, check their terms yourself (see also [Behaviors to expect](01-how-it-works.md#what-to-expect)).
- Responses clients must send the full conversation history with every request (see [Client requirements](04-openviking-tools.md#client-requirements)).

## Client settings at a glance {#connect-clients}

The gateway address is the server's address followed by `/gateway`. In Studio, it appears at the top of the OpenViking Gateway page and on the **Connect** tab. The examples use `https://ov.example.com/gateway`; replace it with your own. Anthropic clients use the gateway address as it is, OpenAI-compatible clients add `/v1`, and Ark SDKs add `/api/v3`.

| Client | Upstream protocol | Base URL | How its conversations are recognized |
| --- | --- | --- | --- |
| [Claude Code](#claude-code) | Anthropic Messages | `https://ov.example.com/gateway` | Claude Code's session header |
| [Codex CLI](#codex-cli) | Responses | `https://ov.example.com/gateway/v1` | Codex's session header |
| [Chat clients and SDKs](#chat-clients-and-sdks) | Chat Completions (or the API your SDK uses) | `https://ov.example.com/gateway/v1` | `X-OpenViking-Session`, which you send yourself |
| [Open WebUI](#open-webui) | Chat Completions | `https://ov.example.com/gateway/v1` | An `X-OpenViking-Session` header set on the connection |
| [OpenCode](#opencode) | Chat Completions, Anthropic Messages or Responses | `https://ov.example.com/gateway/v1` | OpenCode's session header |
| [pi](#pi) | Chat Completions, Anthropic Messages or Responses | `https://ov.example.com/gateway/v1` (Anthropic Messages: `https://ov.example.com/gateway`) | Usually matched from the conversation history |
| [DSH](#dsh) | Chat Completions, Anthropic Messages or Responses | `https://ov.example.com/gateway/v1` (Anthropic Messages: `https://ov.example.com/gateway`) | Usually matched from the conversation history |
| [Volcano Engine Ark and BytePlus ModelArk SDKs](#volcano-engine-ark-and-byteplus-modelark-sdks) | Any of the three | `https://ov.example.com/gateway/api/v3` or `https://ov.example.com/gateway/api/compatible` | Depends on the headers the client sends |

The last column is explained in [How conversations are recognized](#how-conversations-are-recognized).

## When each client sends its own provider key {#bring-your-own-provider-key}

By default the gateway holds the provider API key, and clients send only the gateway key. An [upstream](07-upstreams.md#upstreams) can instead have **Who provides the API key** set to **Each client sends its own key** (see [Credentials](07-upstreams.md#credentials)). Then the client keeps the gateway key in its usual API key setting and also sends its own provider API key in an `X-OpenViking-Upstream-Key` header on every request. In Studio, the **Bring your own provider key** card on the **Connect** tab lists which of your upstreams need it.

Without the header, every request fails with 401 `Upstream API key is missing`. How each client sends it is in [Add a header to a client](#add-a-header).

## Override profile settings {#override-profile-settings}

A client can change the settings of its key's context profile for a new conversation with an `X-OpenViking-Context` header. Its value is a JSON object of the fields to change, for example `{"recall":false}` to skip recall, or `{"disabled_tools":[]}` to offer every OpenViking tool, including the ones that change data. The profile must have **Let clients override settings** on, which is the default; what the header may contain and the errors it can get are in [Let clients override settings](08-context-profiles.md#client-overrides). How each client sends it is in [Add a header to a client](#add-a-header).

A conversation keeps the settings it started with, so a changed header takes effect in the next new conversation, for example with a new `X-OpenViking-Session` value. Like every `X-OpenViking-*` header, it is not forwarded to the provider.

## Add a header to a client {#add-a-header}

The two headers above go on every request. To add one:

| Client | How to add a header |
| --- | --- |
| Claude Code | `export ANTHROPIC_CUSTOM_HEADERS='<Name>: <value>'`. For several headers, put each on its own line, for example `export ANTHROPIC_CUSTOM_HEADERS=$'X-OpenViking-Upstream-Key: <provider-key>\nX-OpenViking-Context: {"recall":false}'` |
| Codex CLI | In the provider block, `http_headers` for a fixed value, for example `http_headers = { "X-OpenViking-Context" = '{"recall":false}' }`, or `env_http_headers` to read the value from an environment variable, for example `env_http_headers = { "X-OpenViking-Upstream-Key" = "PROVIDER_API_KEY" }` with the provider key exported as `PROVIDER_API_KEY` |
| OpenAI and Anthropic SDKs | `default_headers` on the client, or `extra_headers` on each request |
| Open WebUI | Add it to the connection's custom headers, next to the session headers |

## Claude Code {#claude-code}

Claude Code uses Anthropic Messages with the base URL `https://ov.example.com/gateway`. Set three environment variables before you start it:

```bash
export ANTHROPIC_BASE_URL=https://ov.example.com/gateway
export ANTHROPIC_AUTH_TOKEN='<gateway-key>'
export CLAUDE_CODE_GATEWAY_HINT_HEADERS=1
```

- Leave `/v1` off the address; Claude Code adds the path itself.
- `CLAUDE_CODE_GATEWAY_HINT_HEADERS=1` makes Claude Code label sub-agent, compaction and background requests, so the gateway does not recall memory for them or save them as conversation turns.
- Claude Code sends its own session ID, which the gateway uses to recognize the conversation, including after `--resume`.
- The upstream must accept the model names Claude Code requests: leave its model list empty, list those names, or map them to the provider's model with model aliases (see [Models](07-upstreams.md#models)).
- A Claude subscription login does not work through the gateway; requests with a subscription token are rejected. Configure the upstream with a provider API key.
- If the OpenViking plugin is also installed, the gateway steps aside for those conversations (see [When both are in use](../guides/15-gateway.md#when-both-are-in-use)). With only the OpenViking MCP server, the gateway still adds memory and leaves the OpenViking tools to the MCP server.

Next, [check the connection](#check-the-connection).

## Codex CLI {#codex-cli}

Codex uses the Responses API with the base URL `https://ov.example.com/gateway/v1`. It sends the full history with every request, which is what the gateway needs. Add a provider to `~/.codex/config.toml`. The two top-level settings must come before any `[section]`:

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
- To use a ChatGPT subscription instead of an API key, see the next section.

### Use a ChatGPT subscription {#codex-chatgpt-subscription}

Codex can use your ChatGPT subscription through the gateway. Codex does not send its ChatGPT login to the gateway; instead, [CLIProxyAPI](https://github.com/router-for-me/CLIProxyAPI) (CPA) sits behind the gateway as an upstream, signs in with your ChatGPT account and serves it as a Responses API. OpenAI accepts this setup: Tibo, who leads Codex at OpenAI, has [publicly walked through](https://x.com/thsottiaux/status/2076119366647894371) connecting a Codex subscription through CLIProxyAPI.

1. Run CPA and sign it in with your ChatGPT account, following its README.
2. In Studio, add CPA as an upstream: provider *Generic*, protocol Responses, CPA's address as the **Base URL**, and one of CPA's API keys. See [Custom upstreams](07-upstreams.md#custom-upstreams).
3. Issue a gateway key bound to that upstream and configure Codex as above. Codex keeps talking to the gateway with the gateway key; the subscription stays in CPA.

Next, [check the connection](#check-the-connection).

## Chat clients and SDKs {#chat-clients-and-sdks}

Any client or SDK that supports the OpenAI Chat Completions API can connect, with the base URL `https://ov.example.com/gateway/v1`. Enter these settings wherever the client asks for an OpenAI-compatible provider:

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

When you stream Chat Completions, also ask for usage (`"stream_options": {"include_usage": true}`). Otherwise the provider reports no token counts for streamed replies, Studio cannot show usage, and the gateway has to estimate how full the context window is.

SDKs for the other two APIs work the same way, as long as the upstream uses the same API as the SDK:

- Point the Anthropic SDK at `https://ov.example.com/gateway`.
- For the Responses API, use `https://ov.example.com/gateway/v1`, send the full history in every request and set `store: false`. Other Responses requests are forwarded without memory.

Next, [check the connection](#check-the-connection).

## Open WebUI {#open-webui}

Open WebUI connects over Chat Completions with the base URL `https://ov.example.com/gateway/v1`. Set it up in three steps:

1. In Open WebUI, open Admin Panel → Settings → Connections and add an OpenAI-compatible connection with the base URL `https://ov.example.com/gateway/v1` and the gateway key.
2. Add these custom headers to the connection, so that each chat is its own conversation and the gateway recognizes Open WebUI's background tasks:

   ```json
   {
     "X-OpenViking-Session": "{{CHAT_ID}}",
     "X-OpenViking-Task": "{{TASK}}"
   }
   ```

3. Set `RAG_SYSTEM_CONTEXT=true` in Open WebUI's environment. Open WebUI then puts content retrieved from attachments into the system message instead of rewriting your message for one turn, which keeps the conversation history stable between requests.

Two things to know:

- **One connection key means one memory owner.** Every Open WebUI user who chats through this connection reads and writes the memory of the OpenViking user behind the key. Give the connection to one person, or accept that its users share memory (see [One key per person](09-gateway-keys.md#one-key-per-person)).
- Open WebUI also sends background requests (titles, tags, follow-up suggestions) through the same connection. The gateway recognizes title and summary requests. If the request log shows other background tasks as **New message**, point Open WebUI's task model at a connection that does not go through the gateway.

Next, [check the connection](#check-the-connection).

## OpenCode {#opencode}

OpenCode can use Chat Completions, Anthropic Messages or Responses, always with the base URL `https://ov.example.com/gateway/v1`. Add a provider to `~/.config/opencode/opencode.json`, merged with your existing settings:

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

The example uses Chat Completions. For another protocol, change `npm`; `baseURL` stays the same. The upstream bound to the key must speak the protocol you pick, and the **Connect** tab in Studio fills in the matching package:

| Upstream protocol | `npm` |
| --- | --- |
| Chat Completions | `@ai-sdk/openai-compatible` |
| Responses | `@ai-sdk/openai` |
| Anthropic Messages | `@ai-sdk/anthropic` |

Export the gateway key as `OPENVIKING_GATEWAY_KEY`, then select `openviking/<model>` in OpenCode. If the OpenViking plugin for OpenCode is also installed, the gateway steps aside for those conversations.

Next, [check the connection](#check-the-connection).

## pi {#pi}

pi can use Chat Completions, Anthropic Messages or Responses, with the base URL `https://ov.example.com/gateway/v1` (`https://ov.example.com/gateway` for Anthropic Messages). Add a provider to pi's model configuration, `~/.pi/agent/models.json`:

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

The example uses Chat Completions. For another protocol, set `api` and `baseUrl` as below; the upstream bound to the key must speak that protocol. With Anthropic Messages, leave `/v1` off, because the Anthropic SDK adds it:

| Upstream protocol | `api` | `baseUrl` |
| --- | --- | --- |
| Chat Completions | `openai-completions` | `https://ov.example.com/gateway/v1` |
| Responses | `openai-responses` | `https://ov.example.com/gateway/v1` |
| Anthropic Messages | `anthropic-messages` | `https://ov.example.com/gateway` |

- `apiKey` reads the gateway key from the `OPENVIKING_GATEWAY_KEY` environment variable; export it before you start pi. Keep the leading `$`, or pi sends the string itself as the key.
- pi usually sends no session header, so the gateway matches its conversations by their history (see [How conversations are recognized](#how-conversations-are-recognized)).
- If pi's own OpenViking extension is active, the gateway steps aside for those conversations. Use one or the other.

Next, [check the connection](#check-the-connection).

## DSH {#dsh}

[DeepSeek Harness](https://www.npmjs.com/package/@deepseek-ai/dsh) (`dsh`) builds on pi's model layer and takes the same `api` and base URL values: `https://ov.example.com/gateway/v1`, or `https://ov.example.com/gateway` for Anthropic Messages. Add a provider to the profile configuration. `dsh web` uses the `web` profile, so the file is `~/.dsh/profiles/web/cordis.patch.yml`; the DeepSeek Harness Desktop app uses `~/.dsh/profiles/desktop/cordis.patch.yml`:

```yaml
- id: llm-pi-ai
  config:
    providers:
      openviking:
        api: openai-completions
        baseURL: "https://ov.example.com/gateway/v1"
        apiKeyEnv: OPENVIKING_GATEWAY_KEY
        models:
          - id: "<model>"
```

If the file already has an `llm-pi-ai` entry, add `openviking` under its `providers` instead of writing a second entry. For Responses or Anthropic Messages, set `api` and `baseURL` as in the [pi table](#pi).

Export the gateway key as `OPENVIKING_GATEWAY_KEY`, the variable `apiKeyEnv` names, then start `dsh web` and pick `<model>` under the `openviking` provider in the model picker.

- You can also add the provider in the Web UI under Settings → Models → Add model provider → Custom model API, which writes the same file.
- Desktop does not see variables exported in a terminal. On macOS, export `OPENVIKING_GATEWAY_KEY` in your shell startup file (`~/.zprofile` or `~/.zshrc`); on Windows, set it with `setx`. Then quit Desktop with ⌘Q or from the tray and reopen it. If Desktop's recovery dialog runs, it moves `cordis.patch.yml` aside to a `.bak-<timestamp>` file, so add the provider again.
- A DSH provider speaks one protocol. To use the gateway over several, declare one provider per protocol.
- Unless DSH sends one of the session headers, the gateway matches its conversations by their history (see [How conversations are recognized](#how-conversations-are-recognized)).
- If the [OpenViking plugin for DSH](../agent-integrations/17-dsh.md) is installed in the same profile, the gateway steps aside for those conversations. Use one or the other.

Next, [check the connection](#check-the-connection).

## Volcano Engine Ark and BytePlus ModelArk SDKs {#volcano-engine-ark-and-byteplus-modelark-sdks}

Clients and SDKs already set up for Volcano Engine Ark, or BytePlus ModelArk (its international edition), can use any of the three APIs and only need the address replaced, usually with `https://ov.example.com/gateway/api/v3`. Under `/gateway`, the gateway accepts Ark's own paths (`/api/v3/chat/completions`, `/api/v3/responses`, `/api/v3/models` and `/api/compatible/v1/messages`) as well as the standard `/v1` paths:

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

The path a client uses only decides which API it speaks. The request still goes to an upstream bound to the key that uses that API and serves the model, usually one whose provider is Volcano Engine Ark or BytePlus ModelArk (see [Upstreams](07-upstreams.md#upstreams)). As with other chat clients, send `X-OpenViking-Session` with one value per conversation.

Next, [check the connection](#check-the-connection).

## Check the connection {#check-the-connection}

Check every new client setup once, with a short conversation. This matters most for Open WebUI, OpenCode, pi and DSH: their setups follow each client's documented provider settings, so treat them as a starting point.

1. In the client, send two messages in one conversation (one chat, or one agent session). You should get normal replies.
2. In Studio, open the **Requests** tab. Both requests should appear as **New message** with status 200.
3. Expand both requests. They should show the same **Conversation**, and **Saving** should read **On**, which means saving is enabled and healthy. The first turn is saved once the second message arrives; to see it, look for a `gateway-…` session on Studio's **Sessions** page, signed in as the key's user (see [Confirm that memory works](02-quickstart.md#confirm-that-memory-works)).

If something is off:

- **No requests appear.** Only requests that reach a model provider are logged; the client received the gateway's error directly. See [Clients get an error from the gateway](12-troubleshooting.md#clients-get-an-error-from-the-gateway).
- **The two requests have different conversations.** The client sends no session header and the history did not match; see [How conversations are recognized](#how-conversations-are-recognized).
- **Memory stays empty.** That is expected on a brand-new account. To prove that memory works end to end, follow [Confirm that memory works](02-quickstart.md#confirm-that-memory-works).

Every column and label of the log is explained in [Requests](10-overview-and-requests.md#requests).

## How conversations are recognized {#how-conversations-are-recognized}

A conversation is the series of requests the gateway treats as one chat: it keeps one context profile and one upstream, and is saved to an OpenViking session, or to a new one in some cases, for example when the client rewrites its history (see [Saving status](10-overview-and-requests.md#saving-status)). To decide which conversation a request belongs to, the gateway takes the first of these request headers that is present:

1. `X-OpenViking-Session`
2. `thread-id`
3. `x-claude-code-session-id` (Claude Code)
4. `x-opencode-session-id` (OpenCode)
5. `x-session-id`
6. `session-id` (Codex CLI)

If none is present, the gateway looks at the latest assistant reply in the history. If exactly one earlier conversation produced that reply, the request joins it; otherwise it starts a new conversation. Two conversations that only share an opening message are never merged. This handles ordinary back-and-forth chat, but retries, regenerated answers and identical conversations are ambiguous and may start a new conversation. So send `X-OpenViking-Session` whenever your client lets you set headers.

Conversations belong to the OpenViking user behind the key, separately for each API. Two gateway keys of the same user that send the same session value share one conversation; the same session value on a different API is a different conversation. The gateway removes all `X-OpenViking-*` headers before forwarding, so providers never see them.

A request that does not join an existing conversation still gets memory: new messages are recalled for, and memory added earlier is replayed, because the gateway finds it by the messages themselves. The difference is that the request starts a new conversation. Its turns are saved to a new OpenViking session, its memory budget starts from zero, and it uses the key's current context profile.

## Next steps {#next-steps}

- [Behaviors to expect](01-how-it-works.md#what-to-expect): what changes for your client once it goes through the gateway.
- [Context profiles](08-context-profiles.md): tune how much memory is recalled and whether conversations are saved.
- [Troubleshoot OpenViking Gateway](12-troubleshooting.md): fix errors, missing memory or unsaved conversations.
