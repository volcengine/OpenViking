---
description: Deploy OpenViking Gateway with Docker Compose, Helm or as a standalone process, manage it in Studio, and troubleshoot it.
---

# OpenViking Gateway deployment and operations

This page is for the people who run OpenViking Gateway: how to deploy it, how to manage upstreams, context profiles and keys in Studio, where data is stored and how to back it up, and how to track down problems. For what the gateway does and how to connect clients, see [OpenViking Gateway](15-gateway.md); for the overall design, see [Architecture at a glance](15-gateway.md#architecture-at-a-glance).

The gateway is in beta. Settings and APIs may change between releases, so read the release notes before you upgrade.

## Requirements

- **OpenViking authentication**: `api_key` or local dev mode. The gateway does not support trusted authentication.
- **OpenViking version**: an embedded gateway ships with the server, so there is nothing to check. A standalone gateway needs OpenViking Server 0.4.16 or later.
- **Installation**: the gateway is part of the OpenViking core package. The optional `gateway-fast` extra (`pip install "openviking[gateway-fast]"`) adds a faster event loop and HTTP parser.
- **Sign-in**: remote deployments need an OpenViking API key. Users, account admins and root can all sign in to the gateway pages in Studio. Local dev mode needs no key and always uses the `default/default` identity.
- **Storage**: a local disk. The gateway's SQLite databases serve one host only; network filesystems and storage shared between hosts are not supported.

## Keys and credentials

The gateway involves four keys, each with one job:

| Key | Purpose | Where it is used |
| --- | --- | --- |
| OpenViking API key | Signs in to Studio; decides which account and user memory is read and written as | `X-API-Key` or `Authorization: Bearer` on management requests |
| Gateway key (`ovgw_…`) | Connects a client; each one binds a user, a context profile and a set of upstreams | The client's API key field |
| Gateway management key (optional) | Restricts who can change upstreams and context profiles | `gateway.management_key`; the `X-OpenViking-Gateway-Management-Key` request header |
| Upstream API key | Calls the model provider | The upstream editor |

The gateway has no encryption key of its own: it encrypts its local storage with OpenViking's encryption settings; see [Encryption](#encryption).

### Gateway management key

Without a management key, every signed-in user in an account can add and change that account's upstreams and context profiles. With one, only requests that carry it can change them, including requests from OpenViking admins and root. Everyone else can view and test them, and pick them when issuing keys. The management key does not limit who can sign in, or whether users can issue gateway keys for themselves. The full rules are in the [access matrix](../api/25-gateway.md#credentials-and-account-scope).

To set one:

- **`openviking-server init`** (embedded): for a remote deployment, answer no to **Allow users to configure their own gateway upstreams?**. Init generates a value starting with `ovgm_`, prints it and saves it to `gateway.management_key` in `ov.conf`.
- **`openviking-gateway init`** (standalone): answer yes to **Require a management key to change upstreams and profiles?**. Init generates an `ovgm_` value, prints it once and saves it to `management_key` in `gateway.conf`.
- **By hand**: use at least 32 characters, for example from `openssl rand -base64 32`. Put it in `management_key`, or set the `OPENVIKING_GATEWAY_MANAGEMENT_KEY` environment variable, which overrides the file in both forms; see [Configuration reference](#configuration-reference).
- **Compose and Helm**: Compose passes `OPENVIKING_GATEWAY_MANAGEMENT_KEY` into the container; Helm reads it from the `management-key` entry of the Secret named in `gateway.existingSecret`. See [Docker Compose](#docker-compose) and [Helm](#helm).

There is no command-line flag for the management key, because flags show up in process lists and shell history. Setting, changing or removing it takes a restart. It does not revoke issued gateway keys or change existing upstreams and context profiles.

### Encryption

The gateway encrypts stored credentials and conversation state with OpenViking's own encryption (see the [Encryption guide](08-encryption.md)):

- **Embedded**: the gateway follows the server's `encryption` section in `ov.conf`. There is nothing to set for the gateway itself.
- **Standalone**: add an `encryption` block to `gateway.conf`. It has exactly the same fields as the `encryption` section of `ov.conf`, for example:

  ```json
  {
    "openviking_url": "https://ov.example.com",
    "encryption": {
      "enabled": true,
      "provider": "local",
      "local": { "key_file": "~/.openviking/master.key" }
    }
  }
  ```

When encryption is off or the block is missing, the gateway stores everything in plain text, protected only by file permissions. Turning encryption on later is safe: values written before stay readable, and new writes are encrypted. Turning it off, or switching to a different root key, makes the encrypted values unreadable, so keep the settings the data was written with. Back up the root key as the Encryption guide describes; without it, the gateway's databases cannot be read.

Data written by the v0.5 gateway, which used its own key, is converted on the first start; see [Migration](#migration).

## Deploy

### Deployment options

| Form | How to enable it | Client address |
| --- | --- | --- |
| Embedded in the server (including Compose and Helm) | Turn on `gateway.enabled` in `ov.conf` | The server address plus `/gateway`, `http://127.0.0.1:1933/gateway` by default |
| Standalone | `openviking-gateway init`, then `openviking-gateway` | The gateway's own address plus `/gateway`, `http://127.0.0.1:1935/gateway` by default |

Both forms serve the same model and management paths under `/gateway`. An embedded gateway needs no second process and no path-based reverse proxy. A standalone gateway leaves the existing OpenViking service's configuration untouched.

### Enable it in the server

Run `openviking-server init`, answer yes to **Enable OpenViking Gateway on the same port?**, then start or restart the server. With an existing configuration, choose **Update gateway** from the menu. For a remote deployment, init also asks whether users may configure upstreams themselves; see [Gateway management key](#gateway-management-key).

You can also merge this into `ov.conf` and restart:

```json
{
  "gateway": {
    "enabled": true
  }
}
```

On a server that uses API keys, root can also choose **Enable gateway** on Studio's OpenViking Gateway page, which writes `gateway.enabled: true` to `ov.conf` and restarts the server (the restart from Studio needs a single-worker server; otherwise Studio only saves the file and you restart the server yourself).

Open `/studio/` on the server and choose **OpenViking Gateway** in the sidebar. In dev mode, if the default account has no upstreams, the gateway imports the server's usable VLM credentials as upstreams at startup and creates a `default` context profile. Imported Coding Plan upstreams need confirmation before use. If nothing can be imported, add an upstream by hand.

**Letting an agent change the configuration.** An agent that holds the root API key can edit `ov.conf` and restart the server through the [runtime configuration API](../api/08-admin.md#runtime-configuration). A standalone gateway does not offer this API.

### Routes

Every gateway URL is under `/gateway`, in both forms. An embedded server hands the whole `/gateway` prefix to the gateway and keeps every other path for itself:

| Path prefix | Purpose |
| --- | --- |
| `/gateway/v1/` | Model APIs and the model list |
| `/gateway/api/v3/` | Ark-compatible clients |
| `/gateway/api/compatible/v1/` | Ark Anthropic-compatible clients |
| `/gateway/uploads` | Tool uploads |
| `/gateway/admin/` | Management API (authenticated) |

Clients therefore use these base URLs, with `http://127.0.0.1:1933` replaced by your server's address:

| Client | Base URL |
| --- | --- |
| Claude Code and Anthropic SDKs | `http://127.0.0.1:1933/gateway` |
| OpenAI-compatible clients and SDKs, including Codex, OpenCode, pi and Open WebUI | `http://127.0.0.1:1933/gateway/v1` |
| Volcano Engine Ark and BytePlus ModelArk SDKs | `http://127.0.0.1:1933/gateway/api/v3` |

`/health` stays the server's own health check; an embedded gateway has no separate `/health`.

A standalone gateway serves the same `/gateway` paths on its own port, plus these at the root:

- `/` redirects to `/studio/`, where Studio has only the gateway and connection pages.
- `/health` returns `service: "openviking-gateway"`, `auth_mode` and `openviking_url`, and also the caller's identity when the request carries a valid OpenViking credential.

**Public address.** For a shared deployment, set `public_url` to the address clients use to reach the server (or the standalone gateway), without `/gateway`; the gateway appends `/gateway` itself. When it is empty, an embedded gateway uses the server's public address (`OPENVIKING_PUBLIC_BASE_URL` or `server.public_base_url`). Studio's setup guides build client settings from it: Anthropic clients use `<public_url>/gateway`, and OpenAI-style clients `<public_url>/gateway/v1`. Without it, the guides use the address the browser used to open Studio, but upload links for file imports only work with `public_url` set; see [Import files and skills](#import-files-and-skills).

### Docker Compose

Set `OPENVIKING_GATEWAY_ENABLED=true` in `.env`, then run `docker compose up -d`; or turn on `gateway.enabled` in the mounted `ov.conf`. The gateway runs inside the `openviking` service; there is no separate service or profile. Without Compose, add `-e OPENVIKING_GATEWAY_ENABLED=true` to `docker run`:

```bash
docker run -d -p 1933:1933 -v ~/.openviking:/app/.openviking \
  -e OPENVIKING_GATEWAY_ENABLED=true \
  ghcr.io/volcengine/openviking:latest
```

The server listens on `0.0.0.0` inside the container, so `ov.conf` must use API-key authentication.

- **Management key**: set `OPENVIKING_GATEWAY_MANAGEMENT_KEY` in `.env`, and the bundled Compose file passes it into the container; or put it in `gateway.management_key` in the mounted `ov.conf`. The Compose file also passes `OPENVIKING_GATEWAY_ENCRYPTION_KEY`, which is only read once to migrate v0.5 data; see [Migration](#migration).
- **Public address**: set `OPENVIKING_PUBLIC_BASE_URL` (the bundled Compose file passes it); the gateway uses it when it has no `public_url` of its own.
- **After a configuration change**: run `docker compose restart openviking`; read startup errors with `docker compose logs openviking`.

The bundled Caddy forwards all paths to `openviking:1933`. Clients can keep using the compatibility port 1934, or go straight to the server on 1933. For HTTPS, a single `reverse_proxy openviking:1933` carries Studio, the management API and model traffic; see [Public access](12-public-access.md).

### Helm

Set `gateway.enabled: true` in the chart values. The gateway runs inside the server container, keeps its data in `<persistence.mountPath>/gateway`, and uses the server port for both the Service and the Ingress. Keep `replicaCount: 1`.

```yaml
gateway:
  enabled: true
  existingSecret: ""  # optional: Secret name, entry management-key
config:
  gateway:
    public_url: https://ov.example.com
```

- The gateway encrypts its data with the chart's `config.encryption` settings, the same as the server.
- With a `management-key` entry in the `existingSecret` Secret, changing upstreams and context profiles requires that management key.
- Model requests stream: the Ingress must turn off response buffering and allow request bodies of at least 32 MiB and timeouts of 600 seconds.

The NGINX annotations, the commands to create the Secrets and the notes on upgrading from the sidecar chart are in the [chart README](https://github.com/volcengine/OpenViking/blob/main/deploy/helm/README.md#openviking-gateway).

### Standalone deployment

A standalone gateway does not read `ov.conf`. It only needs the OpenViking address, so an existing open-source or OpenViking Cloud service needs no configuration change:

```bash
openviking-gateway init   # writes ~/.openviking/gateway.conf
openviking-gateway
```

`openviking-gateway init` asks for the OpenViking address and checks it (showing the server's authentication mode and version), the listen address (`127.0.0.1:1935` by default), and whether to require a [management key](#gateway-management-key). It writes `~/.openviking/gateway.conf`, or the file named by `--config`, readable only by you. Run it again to change the settings: it offers the current values as defaults and keeps existing secrets unless you change them. The resulting file looks like this:

```json
{
  "openviking_url": "https://ov.example.com",
  "host": "127.0.0.1",
  "port": 1935
}
```

Open the gateway's `/studio/` and sign in with the API key of an OpenViking user, account admin or root. The connection page shows which OpenViking address the gateway uses. A standalone gateway cannot read the remote server's VLM settings, so it cannot import model settings; add upstreams by hand.

**Where the settings come from.** The only flag is `--config`. When `--config` or `OPENVIKING_GATEWAY_CONFIG_FILE` is set, it must point to an existing file; otherwise the gateway reads the first of `~/.openviking/gateway.conf` and `/etc/openviking/gateway.conf` that exists. Environment variables override any field in it. A container needs no file at all: set `OPENVIKING_GATEWAY_OPENVIKING_URL`, plus for example `OPENVIKING_GATEWAY_HOST=0.0.0.0`. Without an OpenViking address from either source, the gateway exits and asks you to run `openviking-gateway init` or set `OPENVIKING_GATEWAY_OPENVIKING_URL`. Every field and the override rule are in the [Configuration reference](#configuration-reference).

**Startup checks.** Before it starts, a standalone gateway contacts OpenViking once:

- If OpenViking uses trusted authentication, the gateway refuses to start.
- When the gateway binds to a non-loopback address, it starts only after confirming that OpenViking uses `api_key` authentication. If OpenViking is unreachable or runs in dev mode, the gateway exits. Start OpenViking first, or bind to `127.0.0.1`.
- When OpenViking runs on another host, connect over TLS or a private network.

**Connecting to the public OpenViking service.** When the gateway connects to `https://api.vikingdb.cn-beijing.volces.com/openviking` without a management key, the startup log and Studio show the `public_openviking_without_management_key` warning: anyone with an API key for that service can sign in to this gateway and change the upstreams in their account. If the gateway is only for you or your team, set `OPENVIKING_GATEWAY_MANAGEMENT_KEY` and restart to restrict changes. This does not limit who can sign in or whether they can issue gateway keys for themselves.

### Reverse proxy and HTTPS

Forward the whole site to the server (embedded) or to the gateway (standalone); no path-based routing is needed. With Caddy:

```caddyfile
ov.example.com {
    reverse_proxy 127.0.0.1:1933 {
        flush_interval -1
    }
}
```

Whatever proxy you use, it needs three things: response buffering off, so streamed output arrives in real time; request bodies of at least 32 MiB; and read timeouts of at least 600 seconds. Also keep the query strings of `/gateway/uploads` out of proxy logs, because they carry one-time upload tokens. Set up authentication before you expose the service; see [Public access](12-public-access.md).

### Scaling

One storage directory serves one deployment on one host, and an embedded and a standalone gateway must not share a directory. An embedded gateway follows the server's worker setting; a standalone gateway takes `workers`, from 1 to 64. For load balancing, failover or rate limiting across providers, add a service such as LiteLLM as an upstream.

## Manage the gateway in Studio

Connect Studio with an OpenViking API key and open **OpenViking Gateway** in the sidebar. Upstreams and context profiles are shared by the whole account; gateway keys and request logs belong to individual users.

- **No management key set**: every signed-in user can add and change the account's upstreams and context profiles.
- **Management key set**: the Upstreams and Profiles tabs are read-only, for OpenViking admins and root too. Read-only users can still view upstreams (without their API keys and headers) and pick them when issuing keys for themselves, but model tests require write access because they make billable provider requests. Choose **Enter gateway management key** to unlock editing and testing; the header then shows **Editing upstreams and profiles**. Editing stays unlocked until you choose **Lock** or close the browser tab. If the management key is changed, Studio locks again on its own.
- **Gateway keys**: everyone can issue keys for themselves; account admins can issue keys for users in their account; root first switches account in the sidebar. See [Gateway keys](#gateway-keys).

Saved upstreams and context profiles take effect at once for the keys that use them (a context profile only affects conversations that start afterwards). If no usable upstream or context profile exists yet and you cannot change them, ask whoever deployed the gateway.

### Upstreams

An upstream is one endpoint of a model service and uses exactly one API: Anthropic Messages, Chat Completions or Responses. The gateway does not convert between APIs, so add one upstream for each API your clients use, even when they come from the same provider. Claude Code uses Anthropic Messages, Codex CLI uses Responses, and most chat apps and SDKs use Chat Completions.

When you choose a provider in the editor, its default Base URL is filled in:

| Provider | Protocols | Default Base URL |
| --- | --- | --- |
| Generic | All three | None; enter it yourself |
| Anthropic | Anthropic Messages | `https://api.anthropic.com` |
| OpenAI | Chat Completions, Responses | `https://api.openai.com/v1` |
| DeepSeek | All three | `https://api.deepseek.com`; `https://api.deepseek.com/anthropic` for Anthropic Messages |
| Volcano Engine Ark | All three | `https://ark.cn-beijing.volces.com/api/v3` for Chat Completions and Responses; `https://ark.cn-beijing.volces.com/api/compatible` for Anthropic Messages |
| BytePlus ModelArk | All three | `https://ark.ap-southeast.bytepluses.com/api/v3` for Chat Completions and Responses; `https://ark.ap-southeast.bytepluses.com/api/compatible` for Anthropic Messages |

#### Endpoint

- **Provider** decides which protocols you can pick and lets the gateway handle each provider's differences. Choose *Generic* for a provider not in the list, for a compatible proxy such as LiteLLM or new-api, and for a reverse proxy such as [CLIProxyAPI](https://github.com/router-for-me/CLIProxyAPI) that exposes subscription accounts as an API (see [Custom upstreams](15-gateway.md#custom-upstreams); check yourself whether using subscription quota this way is allowed).
  - *Generic*, *Anthropic* and *OpenAI* forward requests the same way.
  - *DeepSeek*: when a request has tools, DeepSeek requires every earlier reply in the history to carry its reasoning, and many chat apps do not send it back. DeepSeek upstreams have **Restore reasoning the client drops** on by default (see [Routing](#routing)), so requests can keep thinking on and still get OpenViking tools. With that setting off, OpenViking tools are offered only when a request turns thinking off (`"thinking": {"type": "disabled"}`). Standard Responses requests have no `thinking` field, so they then get no tools. Memory is added as usual.
  - *Volcano Engine Ark* and *BytePlus ModelArk* behave the same: each Chat Completions and Responses conversation gets a fixed `prompt_cache_key`, so Ark's prefix cache follows the conversation. If a request's model, thinking mode, sampling parameters, system prompt or tools differ from the conversation's first request, Ark rebuilds its cache, and the request is flagged **Cache parameters changed** (`ark_cache_parameters_changed`).
- **Protocol** decides which API the upstream accepts. Anthropic upstreams send both `x-api-key` and `Authorization: Bearer`; Chat Completions and Responses upstreams send only Bearer.
- **Base URL**: copy it from the provider's SDK documentation. The gateway appends the endpoint to it:
  - Chat Completions and Responses append `/chat/completions` or `/responses`. A bare domain does not get `/v1` added, so OpenAI needs `https://api.openai.com/v1`.
  - Anthropic Messages appends `/v1/messages`, so the Base URL must not end in `/v1`; saving such a URL is rejected.
  - A trailing slash is removed. Addresses with a version path, such as `/api/paas/v4`, work as is.
  - Requests that clients send to the Ark paths (`/gateway/api/v3/`, `/gateway/api/compatible/v1/`) follow the same rules.
  - An upstream has one Base URL. If a provider uses different addresses for different protocols, create separate upstreams. For example, Zhipu uses `https://open.bigmodel.cn/api/paas/v4` for Chat Completions and `https://open.bigmodel.cn/api/v1` for Responses. Ark Coding Plan uses `…/api/coding/v3` for Chat Completions and Responses and `…/api/coding` for Anthropic Messages.
- **Append protocol endpoint path** is on by default. Turn it off when Base URL is already the complete request endpoint; the gateway then sends every upstream request to that exact URL without adding a protocol path.

  The editor previews the final request URL as you type. If an OpenAI or Ark address is missing its path, or an Anthropic address ends in `/v1`, it warns you and offers a one-click fix.

#### Credentials

- **Who provides the API key**:
  - **The gateway holds the API key** (the default): the gateway sends the upstream's API key, and clients need only their gateway key.
  - **Each client sends its own key**: besides the gateway key, every request carries the client's own provider API key in the `X-OpenViking-Upstream-Key` header. Without it the request fails with 401 "Upstream API key is missing". The gateway passes the key to the provider in the provider's usual header; `X-OpenViking-*` headers themselves are never forwarded.
- **API key and extra headers are write-only.** Leave a field blank to keep the stored value; remove a header row to delete that header. **Changing the Base URL clears the stored API key and header values**, so enter them again whenever you change the address. A stored key is never sent to a new address.
- **Extra headers** are added to every request to this upstream, such as a provider's version or organization header, and replace any client header with the same name. `Host`, `Content-Length`, `Transfer-Encoding` and `Connection` cannot be set.
- **This key is a Coding Plan or subscription key**: providers usually allow subscription keys such as Coding Plan keys only in their own coding tools, and using one through a shared gateway can get the subscription suspended. When you check this, every request to the upstream is rejected with 403 "Coding Plan upstreams are disabled; configure a model API key", unless you also check **Allow this plan through the gateway**. The gateway cannot tell from the key itself whether it is a subscription key, so you have to mark it. Claude subscription tokens (`sk-ant-oat…`) are always rejected.

#### Models

- **Models** lists the model names this upstream serves. Leave it empty to accept any name. Only listed names and aliases appear in the model list (`/gateway/v1/models`).
- **Model aliases** map a name that clients use to the model sent to the upstream, for example the Claude model names Claude Code requests to a model your provider serves. Aliases appear in the model list, and the request log shows the name the client used.
- **Context windows** records each model's window size in tokens (at least 1,024), keyed by the model name actually sent to the upstream, after aliases. Long conversations are compacted at a share of this window, so list every model whose window is smaller than 1,000,000 tokens; see [Long conversations](#long-conversations).

#### Routing

- **Priority**: for each request, the gateway looks at the enabled upstreams bound to the key that use the request's API and serve the requested model, and picks the one with the highest priority. Once a conversation starts, it stays on its upstream as long as that upstream can still serve it, so priority changes only affect new conversations.
- **Enabled**: a turned-off upstream receives no requests and disappears from the model list. Conversations that were using it move to the next candidate, which costs one provider cache miss and drops earlier Claude thinking blocks; these requests are flagged **Upstream switched** (`upstream_changed`). Turn an upstream off instead of deleting it if you may want it back.
- **Allow OpenViking tools**: decides whether OpenViking tools can be offered through this upstream. Turning it off takes effect at once, also in conversations that already have the tools.
- **Restore reasoning the client drops**: when clients send the history back, they often leave out the reasoning of the model's earlier replies. With this on, the gateway records the reasoning of every reply it relays and puts it back in later requests: `reasoning_content` for Chat Completions, thinking blocks for Anthropic Messages, and reasoning items with plain-text content before their item for Responses. The restored content is byte-identical every time, so the provider's prompt cache keeps hitting, but it counts as input tokens. Replies whose reasoning the client sent back itself are left alone. It is on by default for DeepSeek, Volcano Engine Ark and BytePlus ModelArk, and off for other providers. Some requests cannot get their reasoning back even with this on: the conversation moved to another upstream (`upstream_changed`), a Claude conversation lost its memory record (`missing_injection_record`), or the client sent thinking back as plain text. On a DeepSeek upstream, such requests also get no OpenViking tools.
- **Minimum cacheable prompt** (Ark and ModelArk only): the shortest prompt the model can cache. It only affects how the request log reports cache eligibility.

All upstream settings except priority take effect at once, in ongoing conversations too. The gateway does not fail over: when the chosen upstream returns an error, the client receives it, and an unreachable provider returns 502 "Model upstream is unavailable".

#### Test and delete

With write access, choose **Test** in the upstream list. The gateway checks the saved connection first. Once that succeeds, select or enter a model name and run the model test. The gateway sends one small generation request with the stored credentials and reports whether the endpoint, key, model and protocol worked together. It includes OpenViking tools when the upstream allows them and leaves reasoning effort at the model default. The provider may charge for the model request. Coding Plan upstreams stay blocked from model tests until gateway use is approved. Each result is explained in [Upstream tests](../api/25-gateway.md#upstream-tests).

An upstream that keys still use cannot be deleted. Revoke those keys first, or turn the upstream off instead.

### Import OpenViking model settings

In an embedded deployment, **Use OpenViking's model settings** (**Import from OpenViking** in the Upstreams tab header) turns the server's VLM credentials into upstreams in the current account. Without a management key, only root (including local dev) can import; with one, unlock editing first.

- The result shows three groups: imported, needs confirmation and not imported. Upstreams whose path contains `coding` or `plan` are marked as Coding Plan. Once you have confirmed that the provider's terms allow it, check **Allow this plan through the gateway** to use one.
- Importing again does not create duplicates. It refreshes the address, API key, headers, protocol and provider from the server, and keeps what you changed in the gateway: name, enabled state, priority, models, aliases, context windows and the Coding Plan confirmation.
- Codex OAuth and Azure credentials, LiteLLM credentials without `api_base`, and LiteLLM routes that do not use the OpenAI Chat Completions API are skipped. The reasons are listed in [Import VLM settings](../api/25-gateway.md#import-vlm-settings).

### Context profiles

A context profile is a named set of memory settings. Each gateway key uses one profile, and one profile can serve many keys. For example, a "Coding" profile might recall memories, resources and skills, while a "Chat" profile recalls only memories, with a smaller budget.

The Profiles tab shows one card per profile, summarizing each section and counting the keys that use it. While the tab is empty, **Create with recommended settings** creates a profile named "Default" in one click and **Customize** opens the editor; after that, use **New profile**. A profile that keys use cannot be deleted. To make a variant, **Duplicate** is the quickest way.

> **Note**: Saved changes apply only to conversations that start afterwards. A conversation records its profile at its first request and keeps it, because changing what the model sees mid-conversation would break the provider cache. Clients that open a new conversation for every chat pick up changes quickly. Claude Code, Codex and clients that send a fixed session header keep the old settings until they start a new conversation, or until the conversation has been idle for 30 days (`session_ttl_days`).

| Section | What it does | Main settings and defaults |
| --- | --- | --- |
| Recall memory | Searches OpenViking with each new user message and appends what is relevant to that message. | Sources: memories, resources, skills · Budget per message: 1,600 tokens · Budget per context window: 30,000 tokens · Relevance threshold: 0.35 · Time limit: 2 s |
| Save conversations | Writes finished turns to an OpenViking session and commits it, so OpenViking can extract memories. | Save the latest reply after: 600 s · Commit after: 20,000 tokens · Keep recent messages: 10 |
| Long conversations | When a conversation nears the context window, has the same model write a summary that replaces the earlier messages. | Compaction: on · Compact at: 0.9 of the window · Summary length: 8,000 tokens · Agent-managed context windows: off |
| OpenViking tools | Lets the model use OpenViking's tools while it answers. | On · read-only tools selected; see [OpenViking tools](#openviking-tools) |

Each section has its own switch, and Long conversations has one each for compaction and agent-managed context windows. Recall and saving are independent: a profile can recall without saving, or save without recalling.

**Recall details:**

- **User profile at conversation start** works independently of recall. When the `read` tool is offered, memory and skill catalogs are added at the start as well. **Opening context budget** is 4,000 tokens by default, with at most a quarter for skills; 0 leaves all of it out. It does not count against the recall budget.
- OpenViking selects and formats the recalled content within the budget. It may also return just a URI for the model to read later.
- The search query is the user message with client noise removed, cut to **Query length** (8,000) characters. Messages shorter than 3 characters are not searched.
- Each message gets at most the smaller of **Budget per message** and what is left of **Budget per context window**. With less than 64 tokens left, the search is skipped. The budget per context window starts over whenever the conversation is compacted, by the gateway or by the client. A budget of 0 turns recall off. These token counts are conservative estimates, not the provider's billing counts.
- If OpenViking does not answer within the **Time limit**, the message goes to the model without memory and does not get it later, even if the client retries.
- **Limit by category** (under **Advanced settings**) sets a maximum number of entries for each of six categories: events, entities, preferences, experiences, resources and skills. Only categories above 0 are searched. When it is off, all sources are ranked together.

**Recall summary.** With **Show recall summary** on (off by default), the reply to each new user message starts with a short summary of what the gateway added:

```text
> OpenViking context: user profile, memory index, skill list, earlier sessions
> OpenViking recall: 4 items (3 memories, 1 resource) — booking_duplicate_handling, user_lang_pref, +2 more
```

- The `context` line appears only in the first reply of a conversation. It lists what the opening actually contained: the user profile, the memory catalog, the skill catalog, and where earlier parts of the conversation are stored in OpenViking.
- The `recall` line counts the recalled entries by kind and names up to three. If the search fails, it gives the reason instead, for example `> OpenViking recall failed: OpenViking unavailable`. It is left out when nothing was found, recall is off or the budget is used up, so after the first reply such messages get no summary.
- Tool steps and other follow-up requests show no summary. When the client retries the same message, the summary is the same as the first time.
- In Anthropic Messages the summary is its own text block, in Responses its own assistant message, and in Chat Completions the start of the reply text. Requests that ask for structured output, and Chat Completions requests with `n` above 1, get no summary.
- The model never sees the summary: when the client sends the reply back with the next message, the gateway removes it before forwarding, and it is not saved to OpenViking.

**Saving details:**

- A turn is saved when the next user message arrives. **Save the latest reply after** sets how long the gateway waits before it saves the last turn and commits the session, so short conversations get committed too.
- **Commit after**: the session is committed whenever this many tokens are waiting. After each commit, OpenViking extracts memories in the background.
- **Keep recent messages**: after a commit, the session keeps the newest whole turns, at least this many messages.

The API name and range of every setting are in the [Configuration reference](#configuration-reference).

### Gateway keys

A gateway key (`ovgw_…`) is what a client uses in place of a provider API key. Each key belongs to one OpenViking user and uses one context profile and a set of upstreams.

On the Keys tab, choose **Issue key** and fill in:

- **Name**: something to recognize the key by, for example "alice · Claude Code".
- **Who is this for?**: users issue keys only for themselves; account admins can choose a user in their account; root switches account in the sidebar first. The gateway reads the chosen user's API key from OpenViking. If OpenViking stores only key hashes, it cannot, and Studio shows **Paste a user API key** so you can enter that user's current key. A root API key cannot be bound to a gateway key. In local dev mode, keys read and write memory as the `default` user.
- **Context profile**.
- **Upstreams**: at least one. The key can reach only the upstreams you select.
- **Allowed models** (optional): leave empty to allow every model these upstreams serve. Enter the names clients send, including aliases. Requests for other models fail with 403 "Model is not allowed by this key", and the model list shows only allowed names.

Before issuing, the gateway checks the user's key with OpenViking. If you paste a root key, a key from another account or an invalid key, or OpenViking is unreachable, issuing fails; see [Issuing a key fails](#issuing-a-key-fails).

After issuing, the **Copy your gateway key** dialog shows the full key. This is the only time you see it: the gateway keeps only a hash and the first few characters. The dialog also has setups for Claude Code, Codex CLI and chat clients, with the real key and gateway address filled in, ready to paste. If a key is lost, issue a new one.

- **Keys cannot be edited.** To change a key's context profile, upstreams, allowed models or user, issue a new key and revoke the old one.
- **Conversations belong to the user, not the key.** If the client sends the same session ID, a new key for the same user continues the existing conversation, so replacing a key does not interrupt it.
- **Revoking** a key stops it working at once. The user's conversation state and logs stay until they are explicitly deleted or expire.
- **When the OpenViking key changes**: after the user's OpenViking API key is regenerated or the user is deleted, OpenViking rejects the old key the gateway stored. The gateway notices on a later request (up to about 30 seconds later) and deletes the gateway keys bound to it; conversation state and logs stay. Issue a new key with the current OpenViking key. If OpenViking is only unreachable or returns an error, the gateway keeps the key, and requests still reach the model, just without memory.
- **One key per person.** Everyone who shares a key shares the memory of its OpenViking user. Issue one key per person, and separate keys per client if you want different context profiles.

Account admins and root can use **Delete this user's gateway data…** on the Keys tab. It revokes all of the user's gateway keys and deletes the conversation state the gateway keeps for them (including content not yet saved to OpenViking) and their request logs. Memories already in OpenViking are not affected; see [Security and data](#security-and-data).

### Requests and overview

Users see only their own requests; account admins and root see the whole account's.

The **Overview** tab summarizes the latest 10,000 requests within the log retention period (30 days by default):

- Until the first request arrives, a **Get started** checklist, in the order upstream → context profile → key → client.
- **Requests**: the number of model requests, when the last one arrived, and their output tokens.
- **First-call cache hit rate**: the share of input tokens served from the provider's prompt cache on the first model call of each new user message. This is the most important sign of a healthy gateway. It should stay close to what the provider reaches without the gateway. A clear drop usually means the history the gateway sends no longer matches the previous request; see [The first-call cache hit rate dropped](#the-first-call-cache-hit-rate-dropped).
- **Within-turn cache hit rate**: the same for later calls in a turn, such as tool steps and OpenViking tool rounds. It is usually high.
- **Memory entries recalled**: entries added to new messages, and how long an OpenViking search took on average (counting only messages that searched).
- **OpenViking**: Connected, Degraded or Starting, with the version, and the reason when degraded. While OpenViking is unreachable, requests still reach the model without memory. The card's **Saving conversations** part counts conversations that are retrying or have paused saving.
- **Degraded requests**: how often each issue occurred, with an explanation; see [Issues on degraded requests](#issues-on-degraded-requests).
- **Recent requests**, with a link to the Requests tab.

The **Requests** tab lists the latest 1,000 records, newest first, and refreshes every 30 seconds while you are on the first page. Filter by **All**, **New messages**, **Tool steps** or **Issues**, or by **Request type**, or search by model, conversation or key.

The columns are type, model (the name the client sent), the provider's HTTP status, tokens (input, cached share, output), **Memory**, **Saving** status and **Issue**. In the **Memory** column, `+3` means this request's search added 3 entries, shown with the search time, and `↺ 4` means memory added to 4 earlier messages was replayed unchanged. Expand a row for the conversation ID, upstream, key, duration, token breakdown, memory search result, context window use (and whether it was compacted or the model started a new window), saving status with the next retry time, and OpenViking tool details. For a request with an issue, the row also explains what happened and what to do.

Only requests that reached a model provider are logged. Requests the gateway rejects first are not, such as an invalid key, a model the key does not allow, no matching upstream or a body over the size limit; the client receives the error directly.

| Type | Meaning | Memory |
| --- | --- | --- |
| New message | A request whose last message is a new user message | Searched once, earlier memory replayed; the turn is saved |
| Tool step | The same turn continuing, for example with tool results | Earlier memory replayed; saved with its turn |
| Housekeeping | Title, summary, compaction, suggestion and permission-check requests | Earlier memory replayed; never saved |
| Sub-agent | Requests from a client's sub-agents | Earlier memory replayed; never saved |
| Token count | Token-counting requests (`/gateway/v1/messages/count_tokens`) | Earlier memory replayed, so the count matches what is actually sent |
| Forwarded | Requests passed through unchanged, without memory: other endpoints, Responses requests that rely on provider-side state, compressed or unreadable bodies | None |
| Memory sync | Background saving events, not model requests | — |

The **Saving** column shows each conversation's saving status:

- **On**: saving works normally.
- **Off**: the conversation's context profile does not save.
- **Retrying**: OpenViking rejected a save or could not be reached. The gateway retries after 10, 20, 40 and 80 seconds.
- **Paused**: five attempts in a row failed. The gateway then retries every 5 minutes until it succeeds. Unsaved turns are kept until they are saved or the conversation expires.
- **Paused** with the reason `gateway_key_revoked`: the conversation's gateway key was revoked or deleted, and the gateway stops retrying. Saving resumes when the user next sends a request in this conversation with a valid key.

The expanded row shows the reason, for example that OpenViking rejected the key (`openviking_http_401`, meaning the user's OpenViking key is no longer valid) or that OpenViking is unreachable (`openviking_unavailable`). A history change (`history_changed`) is normal: the client edited, regenerated or compacted the conversation, and the gateway continues saving the current history in a new OpenViking session.

**Resync conversation…** in an expanded row starts a new OpenViking session for the conversation. The next time the user sends a message, the gateway writes the full history to it. What was already saved stays in the old session, so some memories may be extracted twice. Use it when a conversation stays paused after you fixed the cause, or when you want a clean copy of a conversation. Nothing happens until the next message arrives. Resync needs the key shown on the record; if that key has been revoked, pick a newer record of the same conversation.

## OpenViking tools

With OpenViking tools on, the model can use the tools your OpenViking service provides while it answers, within the user's own OpenViking permissions. The gateway runs the calls, the client keeps receiving the answer until the reply ends, and the token usage it sees includes every model call made along the way. For what this means for each kind of client and what users see in the reply, see [Agentic memory for any client](15-gateway.md#agentic-memory-for-any-client). This section covers client requirements, settings and limits.

The feature is meant for chat apps and API apps that cannot connect to the OpenViking MCP server. Clients that support MCP or have a plugin, such as Claude Code and Codex, are better served by those, because they show each tool call and its result and ask for approval first.

### Client requirements

All three protocols support streaming and non-streaming requests:

| Protocol | What the client must do |
| --- | --- |
| Chat Completions | Send the full `messages` history. |
| Responses | Use `store: false` and the full `input` history, and on the next turn send back the whole returned `output`, including reasoning and client tool calls. Requests with `previous_response_id`, `conversation` or `background` are forwarded as usual but get no gateway tools; `item_reference` cannot rebuild the full history. |
| Anthropic Messages | Send the full `messages` history with thinking and signature blocks intact. Token-count requests carry the same tool definitions but run no tools. |

When the model calls OpenViking tools and client tools in the same reply, the gateway handles the OpenViking calls, and the client runs its own tools and returns their results as usual. Responses supports client function and custom tools; Anthropic Messages supports client tool_use calls.

### Choose tools

New context profiles have **OpenViking tools** on, including those made with **Create with recommended settings**. The **Tools** list comes from your OpenViking service, and only read-only tools are selected by default:

- **Selected by default**: `find`, `search`, `grep`, `glob`, `list`, `tree`, `read`, `list_watches`, `get_acl`, `list_users`, `list_groups` and `health`.
- **Not selected by default** (they change data): `remember`, `write`, `edit`, `add_resource`, `add_skill`, `forget`, `set_acl` and `cancel_watch`. Check `remember` to let the model save memories, and `add_resource` to let it import web pages or attachments.

Tools that OpenViking adds later are available by default; ongoing conversations keep the tool list they started with. Existing context profiles keep their selection.

**Allow OpenViking tools** is on by default for every upstream. With both switches on, new conversations get the selected tools with an `openviking_` prefix, such as `openviking_find` and `openviking_read`. Studio describes what each tool does, and shows a read-only label only when OpenViking provides that information. Tool definitions go with every request in the conversation, and the full set adds about 3,500 input tokens, so unchecking tools nobody needs also saves tokens.

> **Note**: Selected tools run without the client's permission prompts, including tools that write or delete data. Before you check a tool that changes data, make sure that is acceptable. For profiles used by chat apps, leave `forget` and `set_acl` unchecked. A tool-call notice only reports what already happened; it is not an approval step.

### Tool call notices

With **Show tool calls** on (the default), each OpenViking tool call the gateway runs adds a one-line notice to the reply, at the point where the call happened:

```text
> OpenViking find: "release date" — done
```

- The notice names the tool and, when it can tell, what the call works on: the search query, the URI read, listed or written, or the file or skill being imported.
- In a streaming reply, the notice appears as soon as the call starts, so users can see the gateway working during slow calls such as imports. A non-streaming reply carries the same notices in the final message.
- When the call ends, the outcome is added: `done` when it finished, `failed` when it returned an error, and `skipped` when the gateway refused the call without running it, for example because the round limit or token budget was used up.
- Each notice is its own paragraph. In Chat Completions the notices are part of the reply text; in Anthropic Messages the notices for one round of calls share a text block; in Responses they share an assistant message.
- The model never sees the notices; it gets the real calls and results. The gateway does not save notices to OpenViking.

To hide the notices, turn off **Show tool calls** in the profile's **OpenViking tools** section. Like the other tool settings, the change applies to new conversations.

### When tools are not offered

Whether a conversation gets tools is decided at its first request that could use them (housekeeping requests such as title generation do not count) and then stays fixed, so profile changes only reach new conversations. The one exception switches tools off: the client turns out to have OpenViking tools of its own before the model has called a gateway tool, for example because an MCP server connected after the first turn.

Housekeeping requests, sub-agent requests and conversations where an OpenViking plugin was detected never get OpenViking tools. If such a request sends back history in which the model used these tools, the gateway still attaches the tool definitions so the history stays valid, but refuses new OpenViking calls. The client's own tools stay available in every request.

When a request goes without tools, its detail in the request log gives the reason:

| Reason | Meaning |
| --- | --- |
| `tools_require_full_history` | A Responses request needs `store: false` and the full input history, without `item_reference`. |
| `upstream_tools_disabled` | The upstream has **Allow OpenViking tools** off. |
| `tools_multiple_choices` | The request asks for several choices (`n` above 1). |
| `tools_structured_output` | The request asks for structured output (`response_format`, `text.format` or `output_config.format`). |
| `tools_non_function` | A Chat Completions client sent a tool that is not a function. |
| `tools_forced_choice` | `tool_choice` is `required` or names a specific tool. |
| `deepseek_reasoning_history_required` | The upstream is DeepSeek, the request does not turn thinking off (`"thinking": {"type": "disabled"}`), and earlier replies would reach the upstream without their reasoning: either **Restore reasoning the client drops** is off, or this request cannot restore it (the conversation changed upstream, lost its memory record, or had thinking sent back as text). Standard Responses requests have no `thinking` field and count as thinking on. |
| `tool_name_collision` | The client already has OpenViking tools, so the gateway stops offering its own for the rest of the conversation (except on a branch where the model already called them). The conditions are listed below. |
| `tools_unavailable` | The tool list could not be loaded from OpenViking, and no earlier list was available. Start a new conversation once the connection is back. |
| `tools_not_selected_at_session_start` | The profile has tools on, but this conversation's tool list is empty: either the profile selects no tools, or the conversation started before a gateway upgrade. Start a new conversation. |

The gateway decides that the client already has OpenViking tools (`tool_name_collision`) when any of these holds:

- An MCP server name (`server_label`, `mcp_server_name`, `mcp_servers[].name`) contains the word `openviking`.
- A tool whose name ends in an OpenViking tool name also contains that word, such as `mcp__claude_ai_OpenViking__search`. A connector's `authenticate` tool does not count. Deferred tools listed by name on a line of their own count too.
- A prefixed tool whose name ends in an OpenViking tool name, such as `mcp__ov__read`, mentions `viking://` in its definition.
- In any request, a client tool has the same name as a gateway tool, because upstreams reject duplicate tool names.

A false match also lasts for the whole conversation; start a new one.

### Import files and skills

The model can import material when your OpenViking service provides the import tools and the profile has them checked. Importing by URL needs nothing more. Importing a local file needs one of these:

- **A client shell tool** (named like bash, shell, exec_command, terminal or run_command): the import tool returns a one-time upload link, and the model uploads the file with the client's shell tool, which goes through the client's permission prompt. Skill directories are zipped first. The link points to `public_url` plus `/gateway/uploads`, so `public_url` must be an address the client can reach, and any proxy in front must forward that path like the others. Without `public_url`, imports fail with "Set gateway.public_url for client uploads".
- **An attachment on the user message**: without a shell tool, the model can import a file attached to a user message, sent either as base64 data or as attachment text. This covers embedded `base64` and `text` data in Responses `input_file` and Anthropic `document.source`. Open WebUI usually sends only the extracted text, which is imported as a text file. Attachment URLs and provider file IDs are not fetched; to import a remote resource, use the import tool's URL parameter.

Uploads are limited by `max_body_bytes` (32 MiB by default).

### Limits

These limits are under **Advanced settings** in the profile's **OpenViking tools** section:

| Setting | API name | Default | Range | At the limit |
| --- | --- | --- | --- | --- |
| Rounds per request | `tool_max_rounds` | No limit | 1 or more | Further OpenViking calls are refused; the model continues with the results it has and the client's own tools. |
| Time limit per call | `tool_timeout_seconds` | 30 s | Up to 120 s | The call returns an error to the model. |
| Result size | `tool_result_bytes` | 65,536 bytes | 1,024–1,048,576 | The result is truncated. |
| Total time | `tool_total_seconds` | No limit | More than 0 s | The request fails with 504 "Hidden tool request timed out". |
| Token budget | `tool_total_tokens` | No limit | 1,024 or more | Further OpenViking calls are refused; the model continues with the results it has and the client's own tools. This is not a billing cap; the final answer can go past it. |

Rounds, total time and the token budget are unlimited by default. As in an agent's own tool loop, the model keeps using OpenViking tools until it is done. In a streaming request, the user can stop the reply at any time, and the gateway stops when the client disconnects. **A non-streaming request keeps running after the client disconnects, so set Total time if your clients send non-streaming requests.** An empty field means no limit. Profiles saved before these defaults became "no limit" keep their old values (5 rounds, 120 seconds, 100,000 tokens); clear the fields to remove the limits.

The token budget counts only what using OpenViking tools adds: the extra calls, their results and the model output that follows. The client's existing conversation, tool definitions and images do not count. The gateway keeps the client's limit on answer length (`max_tokens`, `max_completion_tokens` or `max_output_tokens`).

### Failure handling

- **A single call fails**: the model receives an error result and can carry on with its answer.
- **The whole answer fails**: for example, the provider rejects a follow-up model request, or the model keeps calling OpenViking tools after they were refused. The client receives an error, and the request log shows **OpenViking tools failed** (`hidden_tool_loop_failed`). Streaming Responses requests end with a `response.failed` event; Chat Completions and Anthropic Messages return a `gateway_tool_error` error.
- **One upstream keeps failing**: turn off its **Allow OpenViking tools**. Claude conversations that already used the tools may then show **Tool history unavailable** (`hidden_tool_history_unavailable`): the earlier thinking can no longer be used, and the gateway removed it. Restore the setting named in the request detail's skip reason, or start a new conversation.
- **Retries**: a retried tool call reuses the first call's result, and an interrupted write is never repeated automatically.

## Long conversations

A model's context window limits how long a conversation can get. With **Compaction** on (the default), the gateway replaces the earlier part of a conversation with a summary before the window fills up.

**When it compacts.** On every new user message and every tool step, the gateway estimates how full the window is: the token usage the provider reported for this conversation's previous reply, plus an estimate for the messages added since. Without that usage (for example, streamed Chat Completions without `stream_options.include_usage`), it estimates the whole request. When the estimate reaches **Compact at** (0.9 of the window), the gateway compacts. Housekeeping, sub-agent and token-count requests never trigger compaction.

**Where the window size comes from.** The gateway first looks for the model in the upstream's **Context windows** (by the model name sent to the upstream), then uses the profile's **Default context window**, and without either assumes 1,000,000 tokens. For a model with a smaller window, set one of the two; otherwise the provider rejects the conversation as too long before compaction ever starts.

**How the summary is written.** The gateway sends one extra, non-streaming request to the same upstream and model. The client's system prompt, tools and thinking settings stay the same, so most of the request hits the provider's prompt cache. Settings that a plain-text summary cannot follow are removed: a forced tool choice becomes "no tool call", and structured output formats and stop sequences are dropped. The request contains the conversation up to the cut, plus an instruction for the model to summarize it for itself. The summary covers:

- the user's goals and the latest request, word for word;
- key decisions and current progress;
- files, paths and identifiers;
- errors and how they were fixed;
- open items;
- key facts from the latest tool results.

An earlier summary is folded into the new one. The summary is plain text of at most **Summary length** (8,000) tokens. Thinking counts against the output cap, and some models (such as DeepSeek V4.1) think by default even when the request has no thinking settings, so the gateway always allows 16,000 tokens of output on top of the summary length; when the client sets an explicit thinking budget, it adds that budget instead. The provider bills this request like any other.

**What the model receives afterwards.** Where the cut falls depends on the request:

- On a new user message, the cut falls right before that message. The model receives the system and developer messages, the summary as one user message, and the new message.
- On a tool step, the cut falls after the latest tool result, and the model continues the task from the summary.

The summary message starts by saying that the gateway replaced the earlier part of the conversation and that the user did not write it. It ends with the conversation's opening gateway note and user profile, so these survive compaction. Every later request that resends this history, including housekeeping and token-count requests, gets the same replacement; sub-agents usually send their own separate history and are not affected. Each compaction costs one provider cache miss at the cut, and caching works normally after that.

**Nothing before the cut is kept word for word**, not even the latest turns. The model's earlier replies and their thinking depend on the history being replaced, and keeping only part of it would break Claude's thinking signatures. Everything generated after the cut is sent as usual, thinking included.

**Finding what was cut.** The conversation itself is still saved in OpenViking. When **Save conversations** is on and the conversation has the `openviking_grep` and `openviking_read` tools, the summary ends with directions: the conversation's OpenViking session (`viking://user/<user_id>/sessions/<session_id>/`), where its messages are stored, and how to search them (grep the session for a specific pattern to get line numbers, then read the lines around a match). When a cut falls in the middle of a turn, the part before it is saved to OpenViking right away, so the model can search it within the same turn.

**When compaction fails.** If the summary request fails, or the reply calls a tool, is empty or is cut off at the length limit, the gateway sends the full history instead and does not try to compact this conversation again for 60 seconds. The request detail shows **Compaction failed** with the reason. If usage is still at or above **Compact at** after the summary (for example because the system prompt and tools alone fill most of the window), the gateway keeps the summary and also waits 60 seconds before trying again, with the reason `still_over_threshold`.

**No Working Memory summaries.** The summary comes from the conversation's own model, not from OpenViking's session summaries. New OpenViking sessions that the gateway creates have Working Memory turned off: commits still archive messages and extract memories, but no session summary is written.

**The memory budget starts over.** **Budget per context window** resets after each compaction, so memory added before the cut may be added again.

**The client still sends the full history.** After the gateway compacts, the client keeps sending its full history, and the gateway replaces the part before the cut on every request. The client sees small usage numbers, so clients that compact by usage rarely compact on their own, and a very long session can eventually exceed `max_body_bytes`. When the client compacts or edits its history, the gateway saves the new history to a new OpenViking session; if the search tools are available, the opening gateway note of the new history tells the model where the earlier sessions are.

Each request's detail in the request log shows how full the context window was, whether earlier history was replaced, and the size and time of a newly written summary.

### Experimental: agent-managed context windows

With **Agent-managed context windows** on (off by default), the model decides when to start a fresh context window and writes its own hand-off notes, instead of waiting for the gateway's summary. The setting only works in conversations that get [OpenViking tools](#openviking-tools); other conversations are compacted as usual. When compaction is on, it remains the fallback if the model never starts a new window.

The model sees:

- **Two extra tools.** They are not in the profile's **Tools** list; only this setting controls them.
  - `openviking_new_context(reason, notes, next_steps)` starts a new window; `next_steps` is optional. It must be the only tool call in its round. Called together with other tools, every call in the round returns an error and the window is not reset. It also returns an error if the gateway already replaced the context at the same point in this request (for example, it just compacted).
  - `openviking_context_remaining()` returns the window number, estimated tokens used and left, the user turns in this window, the time since the user's previous message, and a one-line recommendation.
- **One more line in the opening gateway note**, telling the model that it manages its own context windows with these two tools.
- **A status line**: after each new user message, the memory block ends with `[context-status] window wN · ~X/Y tokens (P%) · T since your previous message`.
- **Two reminders**: when the window reaches **Soft reminder at** (0.7), the gateway reminds the model once to start a new window after the current step, with notes that keep the exact paths, line numbers and values it will need. At **Hard reminder at** (0.85), it tells the model to start a new window now, and repeats on every step until the model does or compaction takes over. Reminders are attached to the new user message, or to the tool result during a tool step.

When the model calls `openviking_new_context`, the gateway replaces the conversation so far with a window header, and the model carries on in the same reply. The header contains, in order: that this is window N started by the model and that the user did not write it; the reason, notes and next steps; the user's latest message word for word; how to search earlier windows (under the same conditions as for compaction); and the conversation's opening gateway note. Later requests rebuild the same window from the client's history. The content before the reset is saved to OpenViking right away and stays searchable. The reason and notes appear only in the header and are not saved to OpenViking. Each reset costs one provider cache miss but needs no summary request.

Each request's detail in the request log shows the window number, whether the model started a new window, and which reminder it received. How well this works depends on the model, and the behavior may change in later releases.

## Security and data

**Isolation by user.** Each gateway key is bound to one OpenViking user. The gateway searches memory, saves conversations and calls tools with that user's own OpenViking key, so it can never do more than the user can, and each user's conversation state is kept separate. Sessions and memories always live in OpenViking; the gateway keeps only the state it needs to continue conversations.

**What the gateway stores.** `storage_path` holds two SQLite databases (for an embedded gateway, `storage.workspace/gateway` by default; see [Configuration reference](#configuration-reference)). Only the system user that runs the gateway can read the files, and with [encryption](#encryption) on, every stored value is encrypted as well:

- `management.sqlite3`: upstreams, context profiles, gateway keys, request logs, and which upstream created each Responses response. This includes upstream API keys, header values and the OpenViking keys bound to gateway keys, which are in plain text when encryption is off; gateway keys themselves are stored only as a hash and a short prefix.
- `kernel.sqlite3`: the state of each conversation, including the memory text added to messages (needed for replay), OpenViking tool rounds, summaries and conversation text waiting to be saved.

**What request logs record.** Metadata only: request type, model, status, upstream, a hash of the conversation ID, token counts, memory counts and timing, saving status and issues. Never message text, recalled memory, tool arguments or results, or any key. The gateway writes no HTTP access log, and its error messages never repeat submitted values.

**What providers see.** Every upstream receives the full model input, including memory the gateway added and OpenViking tool results. Only add providers you trust with that data.

**Retention.** A cleanup job runs every hour:

| Data | Kept for | Setting |
| --- | --- | --- |
| Conversation state | 30 days after last use | `session_ttl_days` |
| Request logs | 30 days | `log_retention_days` |
| Which upstream created each Responses response | 30 days | `response_ttl_seconds` |
| Sessions and memories in OpenViking | OpenViking's own rules | — |

**Deleting data.** Revoking a gateway key, or the bound OpenViking key becoming invalid, does not delete user data. There are three ways to delete it:

- An account admin or root uses **Delete this user's gateway data…** in Studio, or calls `DELETE /gateway/admin/users/{user_id}/data`. This revokes the user's gateway keys and deletes their conversation state and logs, including content not yet saved.
- Root calls `DELETE /gateway/admin/account/data?account_id=<account>` to delete all gateway data of an account.
- In an embedded deployment, deleting a user or account in OpenViking also deletes the matching gateway data. A standalone deployment has no such step; its data stays until it is explicitly deleted or expires.

None of this touches memories and sessions already stored in OpenViking. The reverse also holds: after you delete a memory in OpenViking, the copy the gateway saved for an ongoing conversation is still replayed until that conversation expires. To clear it now, delete the user's gateway data.

**Access control.**

- Clients authenticate with gateway keys, sent in `Authorization: Bearer` or `x-api-key`. Keys can be revoked at any time.
- The management key only controls changes to upstreams and context profiles. It grants no access to memory or logs and no right to issue keys; see the [access matrix](../api/25-gateway.md#credentials-and-account-scope).
- Account admins can issue gateway keys for any user in their account (when OpenViking stores only key hashes, the user has to supply their key). A gateway key lets its holder recall memory and call tools as that user, so the right to issue keys amounts to reading the memory of every user in the account. Give it only to admins you trust.
- `/gateway/uploads` needs no gateway key. Each upload is authorized by a one-time token signed by OpenViking, and the gateway only forwards it to OpenViking's upload endpoint. Keep its query string out of proxy logs.
- `X-OpenViking-*` headers, client credentials and cookies are never forwarded to upstreams.

**Backups.** Back up `storage_path`, including both databases and their `-wal` and `-shm` files; with encryption on, also keep a backup of OpenViking's root key. For a consistent copy, stop the gateway first, or use SQLite's online backup (`sqlite3 kernel.sqlite3 ".backup kernel.backup.sqlite3"`, and the same for `management.sqlite3`). What losing each file means:

- **`kernel.sqlite3` lost**: memory added earlier cannot be replayed. Each ongoing conversation takes one provider cache miss, Claude conversations lose their earlier thinking once (**Memory record missing**, `missing_injection_record`), and turns not yet saved are lost.
- **`management.sqlite3` lost**: upstreams, context profiles, keys and request logs are gone. Set them up again and issue new keys.
- **Root key lost** (encryption on): the encrypted values in both databases cannot be read. Start again with an empty `storage_path`.

## Day-to-day operations

- **Watch the first-call cache hit rate.** It reflects prompt caching across turns and should stay close to what the provider reaches without the gateway. The **Within-turn cache hit rate** covers tool steps and is normally high. If the first-call rate drops noticeably, see [The first-call cache hit rate dropped](#the-first-call-cache-hit-rate-dropped).
- **Use the request log to look into a single request.** Filter by new messages, tool steps or issues, then expand a row to see the recall result, saving status and why OpenViking tools were left out; see [Requests and overview](#requests-and-overview).
- **Set the public address.** Set `public_url` to the address clients use, without `/gateway`, so client settings and upload links stay correct behind a reverse proxy.
- **Back up regularly.** Back up the gateway's storage directory and, with encryption on, OpenViking's root key; see **Backups** under [Security and data](#security-and-data).

### Upgrades

- Read the release notes before you upgrade. If a new gateway version cannot read the old storage format, it stops at startup with `Unsupported gateway schema`; see [The gateway does not start](#the-gateway-does-not-start).
- A standalone gateway needs OpenViking Server `min_server_version` (0.4.16) or later. With an older server, memory search and saving stop and no keys can be issued. When you upgrade the two separately, upgrade OpenViking first, then the gateway. An embedded gateway ships with its server and skips this check.
- To move from the sidecar or an older standalone gateway, see [Migration](#migration).

### Monitoring

The gateway exports no metrics such as Prometheus and writes no HTTP access log. Check its state on Studio's Overview and Requests tabs, or through the management API's `/gateway/admin/overview` and `/gateway/admin/logs`. Use `/health` for liveness: the server's health check in an embedded deployment, and the gateway's own in a standalone one.

## Design trade-offs

The table explains why the gateway's main design choices were made and what they cost, so you can judge whether it fits your setup:

| Design | Choice and reason | Cost |
| --- | --- | --- |
| Where it plugs in | On the model call path instead of a plugin in every agent: clients that cannot install plugins can use it, and provider keys are managed in one place. | It cannot see the working directory; keeping projects' memory apart means a different OpenViking user per project. |
| Who runs the tools | The gateway runs OpenViking tools during the reply, stores the rounds and replays them unchanged on the next turn: even clients with no tools and no MCP support get a model that uses memory on its own. | No per-call approval; clients must send the full history every turn; admins need to narrow the tool list and limits. |
| Where memory goes | At the end of the newest user message, not in the system prompt: one changed character in the system prompt invalidates the whole cache, while appending at the end and replaying unchanged keeps the prefix stable. | The gateway must reliably record the exact text of every addition; a lost record costs one cache miss. |
| When turns are saved | One turn behind: only when the next message brings the previous turn back is it clear the user kept it. | The last turn is saved only after **Save the latest reply after** (10 minutes by default). |
| Long conversations | The gateway has the same model write the summary: only the gateway knows what the model actually received, and the summary request mostly hits the cache. | The turn that triggers compaction costs one extra, billed model call; text before the cut is no longer sent to the model. |
| Local state | SQLite on one host, with no Postgres or Redis dependency, which suits self-hosting. | The gateway cannot fail over between machines and is a single point on the model call path. |
| Client identity | The gateway issues its own keys instead of using OpenViking keys directly: they can be revoked on their own, and one person's clients can use different context profiles. | The gateway has to store users' OpenViking keys, so turn on encryption; a leaked gateway key exposes that user's memory. |

## Configuration reference

Both forms use the same fields; they differ only in where the file lives and how you create it:

- **Embedded**: the `gateway` section of `ov.conf`, written by `openviking-server init`. Host, port and workers come from the server.
- **Standalone**: the root of `gateway.conf`, with no `gateway` wrapper, written by `openviking-gateway init`. When `--config` or `OPENVIKING_GATEWAY_CONFIG_FILE` is set, it must point to an existing file; otherwise the gateway reads the first of `~/.openviking/gateway.conf` and `/etc/openviking/gateway.conf` that exists. `OPENVIKING_CONFIG_FILE` only selects the server's configuration.

| Field | Form | Default | Meaning |
| --- | --- | --- | --- |
| `enabled` | Embedded | `false` | Turns the gateway on inside the server |
| `openviking_url` | Standalone | — (required) | Address of the OpenViking Server |
| `host` | Standalone | `127.0.0.1` | Listen address |
| `port` | Standalone | `1935` | Listen port |
| `workers` | Standalone | `1` | Worker processes, 1–64 |
| `encryption` | Standalone | not set (plain text) | Same fields as the `encryption` section of `ov.conf`; see [Encryption](#encryption). Embedded gateways use the server's section |
| `public_url` | Both | empty | The external address clients use, without `/gateway` (the gateway appends it); when empty, an embedded gateway uses the server's public address; with neither, setup guides use the request's origin and file uploads do not work |
| `storage_path` | Both | Embedded: `gateway` under `storage.workspace`; standalone: `~/.openviking/gateway` | Local storage directory. When unset, an embedded gateway keeps its data with the server's workspace, so a container that mounts a volume only on the workspace keeps it too; if `~/.openviking/gateway` already holds gateway data, the gateway keeps using it and says so in the startup log |
| `management_key` | Both | empty | Optional gateway management key, at least 32 characters |
| `min_server_version` | Both | `0.4.16` | Lowest OpenViking Server version a standalone gateway accepts |
| `session_ttl_days` | Both | `30` | Days of inactivity before a conversation expires; minimum 1 |
| `response_ttl_seconds` | Both | `2592000` | Seconds to remember which upstream created a Responses response; minimum 60 |
| `log_retention_days` | Both | `30` | Days to keep request logs; minimum 1 |
| `max_body_bytes` | Both | `33554432` | Size limit for requests and uploads; minimum 1,024 bytes |
| `upstream_timeout_seconds` | Both | `600` | Timeout for provider requests; must be above 0 |
| `health_interval_seconds` | Both | `60` | Seconds between OpenViking status checks; minimum 1 |

**Environment variables override the file.** The rule is the same in both forms: a non-empty `OPENVIKING_GATEWAY_<FIELD NAME IN UPPER CASE>` variable, such as `OPENVIKING_GATEWAY_PUBLIC_URL` or `OPENVIKING_GATEWAY_MANAGEMENT_KEY`, wins over the value in the file; an empty variable counts as unset. A value in the file can also be a `"$VAR"` reference. The `encryption` block can only be set in the file. Containers usually need no file: set the fields they need as variables.

URLs must be HTTP(S) and contain no credentials, query or fragment. Unknown fields fail validation, except removed ones, which are ignored with a startup warning: `encryption_key_env`, and in `ov.conf` also the v0.5 fields listed in [Migration](#migration).

**Context profile settings.** Studio label first, then the name in the management API (which calls context profiles `policies`):

| Studio label | API name | Default | Range | Meaning |
| --- | --- | --- | --- | --- |
| Name | `name` | `Default` | | Display name. |
| Recall memory | `recall` | `true` | | Search memory for each new user message. |
| User profile at conversation start | `profile` | `true` | | Provide the user profile when a conversation starts, independently of recall. |
| Opening context budget | `profile_max_tokens` | `4000` | 0–32,000 | Separate budget for the profile and catalogs; 0 leaves them out. Catalogs need the `read` tool. |
| Show recall summary | `show_recall` | `false` | | Start the reply with a summary of what OpenViking added, or why recall failed. The model never sees it. |
| Sources | `context_types` | `memory`, `resource`, `skill` | At least one | What to search: memories, resources, skills. |
| Budget per message | `max_tokens` | `1600` | 64–32,000 | Most tokens added to one message. |
| Budget per context window | `session_max_tokens` | `30000` | ≥ 0 | Most tokens added within one context window; resets after each compaction. 0 turns recall off. |
| Relevance threshold | `score_threshold` | `0.35` | 0–1 | Lowest relevance score accepted. |
| Time limit | `recall_timeout` | `2` (seconds) | Up to 30 | How long a search may take before the message goes on without memory. |
| Query length | `query_max_chars` | `8000` | 3–32,000 | Characters of the message used as the search query. |
| Limit by category | `quotas` | `{}` (off) | Keys: `events`, `entities`, `preferences`, `experiences`, `resources`, `skills` | Most entries per category; only categories above 0 are searched. |
| Save conversations | `capture` | `true` | | Save finished turns to OpenViking. |
| Save the latest reply after | `idle_seconds` | `600` (seconds) | ≥ 1 | Quiet time before the last turn is saved and the session committed. |
| Commit after | `commit_tokens` | `20000` | ≥ 1 | Tokens waiting in the session that trigger a commit. |
| Keep recent messages | `keep_recent_messages` | `10` | 0–1,000 | Messages left in the session after a commit. |
| Compaction | `compaction` | `true` | | Replace the earlier part of a conversation with a summary near the context window. |
| Compact at | `compaction_threshold` | `0.9` | 0.5–0.98 | Share of the context window that triggers compaction. |
| Summary length | `summary_max_tokens` | `8000` | 1,000–32,000 | Most tokens in one summary. |
| Default context window | `context_window` | Not set (1,000,000 assumed) | ≥ 1,024 | Window used when the upstream does not list the model. |
| Agent-managed context windows | `agent_windows` | `false` | | Experimental. Let the model start new context windows itself; needs OpenViking tools. |
| Soft reminder at | `window_soft_ratio` | `0.7` | 0.3–0.95, below `window_hard_ratio` | Share of the window at which the model is reminded to start a new window soon. |
| Hard reminder at | `window_hard_ratio` | `0.85` | 0.4–0.97 | Share of the window at which the model is told to start a new window now. |
| OpenViking tools | `gateway_tools` | `true` | | Offer OpenViking tools for Chat Completions, full-history Responses and Anthropic Messages. |
| Unchecked tools | `disabled_tools` | The 8 tools that change data; see [Choose tools](#choose-tools) | OpenViking tool names, without `openviking_` | Tools new conversations do not get; every other available tool is offered, including ones added later. API requests that omit the field get the default list; an empty list with the main switch on offers every available tool. |
| Show tool calls | `show_tool_calls` | `true` | | Add a one-line notice to the reply for each OpenViking tool call. |
| Rounds per request | `tool_max_rounds` | `null` (no limit) | 1 or more | See [Limits](#limits). |
| Time limit per call | `tool_timeout_seconds` | `30` | Up to 120 | |
| Result size | `tool_result_bytes` | `65536` | 1,024–1,048,576 | |
| Total time | `tool_total_seconds` | `null` (no limit) | More than 0 | |
| Token budget | `tool_total_tokens` | `null` (no limit) | 1,024 or more | |

**Upstream settings:**

| Studio label | API name | Default | Meaning |
| --- | --- | --- | --- |
| Name | `name` | | Display name. |
| Protocol | `protocol` | | `anthropic` (Anthropic Messages), `chat` (Chat Completions) or `responses` (Responses). |
| Provider | `vendor` | `generic` | `generic`, `anthropic`, `openai`, `deepseek`, `ark` (Volcano Engine Ark) or `byteplus` (BytePlus ModelArk). Studio offers only the protocols a provider supports; the API accepts any pair. |
| Base URL | `base_url` | | Provider address; defaults and path rules are in [Upstreams](#upstreams). |
| Append protocol endpoint path | `append_endpoint_path` | `true` | Appends the selected protocol's path; when false, uses `base_url` as the complete request URL. |
| Who provides the API key | `auth_mode` | `managed` | `managed` (the gateway holds the API key) or `passthrough` (each client sends its own key). |
| API key | `api_key` | | Write-only; see [Credentials](#credentials). |
| Extra headers | `headers` | `{}` | Write-only; see [Credentials](#credentials). |
| Models | `models` | `[]` | Model names served; empty accepts any name. |
| Model aliases | `aliases` | `{}` | Name clients use → model sent to the upstream. |
| Context windows | `context_windows` | `{}` | Model → tokens, each at least 1,024. |
| Priority | `priority` | `0` | Higher values win for new conversations. |
| Enabled | `enabled` | `true` | A turned-off upstream receives no requests. |
| Allow OpenViking tools | `allow_gateway_tools` | `true` | Whether OpenViking tools can be offered through this upstream. |
| Restore reasoning the client drops | `replay_reasoning` | `null` (follows the provider) | Puts reasoning the client dropped back on earlier replies. `null` means on for `deepseek`, `ark` and `byteplus` and off for other providers; `true` or `false` overrides that. Studio saves `null` while the switch matches the provider default. |
| This key is a Coding Plan or subscription key | `coding_plan` | `false` | Rejects requests to this upstream unless allowed. |
| Allow this plan through the gateway | `allow_coding_plan` | `false` | Allows use of a key marked as Coding Plan. |
| Minimum cacheable prompt | `cache_min_tokens` | `1024` | Ark and ModelArk only; affects how cache eligibility is reported. |

Gateway key fields, every management operation, the access rules and the error reasons are in the [Management API](../api/25-gateway.md).

## Migration

These steps apply when you move from the sidecar or standalone gateway of earlier releases to the embedded gateway.

1. **Back up first.** Back up `storage_path` (both databases and `encryption.key`, if it is there). Stop the old gateway process or sidecar before you enable the embedded gateway.
2. **Remove old fields.** Delete `host`, `port`, `workers`, `url`, `openviking_url`, `admin_token_env` and `encryption_key_env` from the `gateway` section of `ov.conf`. The server ignores them and logs a warning at startup, so an old `ov.conf` still starts, but clean them up. `openviking-server init` → **Update gateway** removes them for you. To keep running the gateway on its own instead, run `openviking-gateway init`, or move its settings to a separate `gateway.conf`, without the `gateway` wrapper and `enabled`. The standalone command no longer takes `--openviking-url`, `--host`, `--port`, `--workers` or `--storage-path`; set them in `gateway.conf` or as environment variables.
3. **Fix the public address.** Point `public_url` at the server's public address, without `/gateway`, or remove it. The old quick start set it to `http://127.0.0.1:1935`, which the embedded gateway no longer listens on, so setup guides and upload links would point at nothing. When `public_url` points at another port, `openviking-server init` → **Update gateway** offers to clear it.
4. **Update client addresses and deployment config.** Every gateway URL now lives under `/gateway`, so clients change both the port and the path: `http://host:1935/v1` becomes `http://host:1933/gateway/v1`, a Claude Code `ANTHROPIC_BASE_URL` of `http://host:1935` becomes `http://host:1933/gateway`, and Ark SDKs move to `http://host:1933/gateway/api/v3`. The old paths are not served any more. Scripts that call the management API move from `/api/v1/admin/gateway/...` to `/gateway/admin/...`. Remove the old 1935 Service and Ingress routes, the Compose `gateway` service and profile, and the Helm sidecar settings `gateway.workers`, `gateway.port` and `gateway.resources`. 1935 is still the standalone default port, and a standalone gateway uses the same `/gateway` paths.
5. **Let the first start convert stored data.** The v0.5 gateway encrypted its data with its own key. On the first start, the new gateway (embedded or standalone) decrypts it with that key and rewrites it under the current [encryption](#encryption) settings, or in plain text when encryption is off, so turn on OpenViking encryption before this start if you want the data to stay encrypted. The old key is read from the `OPENVIKING_GATEWAY_ENCRYPTION_KEY` environment variable, or from `encryption.key` in `storage_path`. `encryption_key_env` is ignored: if you used it to point at another variable, export the key as `OPENVIKING_GATEWAY_ENCRYPTION_KEY` for this start. If old encrypted data exists and neither source has the key, the gateway refuses to start and the error names both places. With Helm, keep the `encryption-key` entry in the `existingSecret` Secret for the upgrade release only. After a successful start the old key is never read again, so you can remove the variable, the Secret entry and `encryption.key`.
6. **Remove the shared admin token.** `OPENVIKING_GATEWAY_ADMIN_TOKEN` is **not** used as a management key. To restrict who can change upstreams and context profiles, set `management_key` explicitly.
7. **Check upstream addresses.** On first start, the gateway rewrites stored Base URLs once, so each upstream keeps calling the same address as before the upgrade:
   - Ark and ModelArk Chat Completions and Responses upstreams with only a domain get `/api/v3` added.
   - Other Chat Completions and Responses upstreams whose path does not end in a version segment such as `/v1` or `/v4` get `/v1` added. This covers bare domains (`https://api.deepseek.com` → `https://api.deepseek.com/v1`) and addresses with a path prefix (`https://openrouter.ai/api` → `https://openrouter.ai/api/v1`, or a self-hosted proxy under `/litellm`).
   - Ark and ModelArk Anthropic upstreams with only a domain get `/api/compatible` added.
   - Anthropic Base URLs ending in `/v1` lose the `/v1`.

   Addresses that already end in another version segment are left alone, such as Zhipu's `https://open.bigmodel.cn/api/paas/v4`. Before the upgrade it became `…/v4/v1` and failed; now it works as entered. New and edited upstreams follow the rules in [Upstreams](#upstreams), and an Anthropic Base URL ending in `/v1` is rejected.
8. **Note the changes to logs and revocation.** Request logs gain a user column; older records have no user and appear only in account admin and root views. Revoking a key no longer deletes the user's data; explicit deletion and retention still apply.

## Troubleshooting

### Common symptoms

| Symptom | Cause | What to do |
| --- | --- | --- |
| A conversation gets no recall and is not saved | An OpenViking plugin was detected, and the gateway left recall, saving and tools to the plugin. | Expected: it keeps content from being added or saved twice. See [Gateway or plugin?](15-gateway.md#gateway-or-plugin). |
| Saving shows **Retrying**, then **Paused** | OpenViking rejected the save or could not be reached, or the conversation's gateway key was revoked (`gateway_key_revoked`), for example after the user's OpenViking key was regenerated. | Saving resumes on its own once OpenViking is back. After a revocation, issue a new gateway key; the user's next request resumes saving. See [Conversations do not appear in OpenViking](#conversations-do-not-appear-in-openviking). |
| The profile has tools on, but the conversation has none | No request that could use tools has arrived yet, loading the tool list failed on the first one, the client does not send the full history, or the client has its own OpenViking tools. | Check the reason in the request log (see [When tools are not offered](#when-tools-are-not-offered)). `tool_name_collision` is expected when the client has the OpenViking MCP server; for other reasons, fix the cause and start a new conversation. |
| Requests go without memory | OpenViking unreachable or too slow, budget used up, nothing relevant, and so on. | See [No memory is added](#no-memory-is-added). |
| The provider rejects a long conversation as too long | The gateway does not know the model's real window and compacts too late. | See [Long conversations hit the context limit](#long-conversations-hit-the-context-limit). |

### The gateway does not start

When an embedded gateway cannot start, the whole OpenViking Server stops. Read the startup error in the server log (`docker compose logs openviking` with Compose).

- **v0.5 data without the old key**: the storage directory holds data encrypted by the v0.5 gateway, but there is neither `OPENVIKING_GATEWAY_ENCRYPTION_KEY` nor `encryption.key` in `storage_path`. Provide the original key; see step 5 of [Migration](#migration).
- **Old fields**: remove the fields listed in step 2 of [Migration](#migration).
- **Management key too short**: it needs at least 32 characters. An unset environment variable referenced with `$VAR` causes the same error.
- **Encryption settings**: the server's `encryption` section (embedded) or the `encryption` block in `gateway.conf` (standalone) is invalid, or its root key cannot be loaded. See the [Encryption guide](08-encryption.md).
- **Unsupported storage format** (`Unsupported gateway schema`): the storage directory was written by an incompatible gateway version (an older one, or a newer one if you downgraded). Use a fresh, empty `storage_path`.
- **Standalone without an OpenViking URL**: run `openviking-gateway init`, or set `OPENVIKING_GATEWAY_OPENVIKING_URL`.
- **Standalone config file not found**: the file named by `--config` or `OPENVIKING_GATEWAY_CONFIG_FILE` does not exist. Fix the path, or create the file with `openviking-gateway init --config <path>`.
- **Standalone on a non-loopback address**: the startup check must reach OpenViking and confirm `api_key` authentication, or the gateway exits. Start OpenViking first (in containers, wait until it is ready), or bind to `127.0.0.1`.

### Studio shows a setup card

| Card | What to do |
| --- | --- |
| The gateway is not enabled on this OpenViking server (404) | Run `openviking-server init` → **Update gateway**, or set `gateway.enabled: true` in `ov.conf` and restart. On a server that uses API keys, root can choose **Enable gateway** on that page instead. Account admins and regular users see a card that only asks them to contact whoever deployed the server. |
| Connect to OpenViking first, or Your OpenViking API key is invalid (401) | Open connection settings and enter your current OpenViking API key. |
| The gateway does not support Trusted mode (`trusted_mode_unsupported`) | Switch the server to `api_key` authentication. |
| The gateway cannot reach OpenViking (`openviking_unreachable`) | Standalone gateways only: check `openviking_url` (in `gateway.conf` or `OPENVIKING_GATEWAY_OPENVIKING_URL`) and the network. |

If the Upstreams and Profiles tabs are read-only, the deployer set a gateway management key: choose **Enter gateway management key** to unlock them. An OpenViking root or admin API key does not unlock editing. After the management key is changed (`management_key_invalid`), Studio locks again.

### Issuing a key fails

The **Issue a gateway key** dialog shows why issuing failed; the code in parentheses is the `reason` the management API returned.

| Message in Studio | Cause and fix |
| --- | --- |
| The server only stores a hash of this user's API key and cannot read it. Paste their user API key instead. (`user_key_unreadable`) | OpenViking Server stores only key hashes (`encryption.api_key_hashing.enabled`). Choose **Paste a user API key** and enter that user's own key. |
| This is a root API key. Use the user API key of a user in this account. (`root_key_not_allowed`) | A root API key cannot be bound to a gateway key. Use the API key of a user or account admin instead. Local dev mode's `default/default` is exempt. |
| This OpenViking API key belongs to another account. (`other_account`) | The key belongs to a different account from the one you are managing in Studio. |
| OpenViking rejected this key. (`openviking_http_401`) | The key is incomplete or was regenerated, or the user was deleted. Use the user's current key. |
| OpenViking couldn't tell which user this key belongs to (`openviking_identity_missing`) | OpenViking returned no user for this key. Use the key of a user in this account. |
| The gateway couldn't reach OpenViking to check this key. (`openviking_unavailable`) | The gateway cannot reach OpenViking; for a standalone gateway, check `openviking_url`. |
| OpenViking is older than the gateway requires. (`openviking_version_mismatch`) | Standalone gateways only: OpenViking Server is older than `min_server_version` (0.4.16). Upgrade it. A server installed from source without version tags may report a development version such as `0.1.dev123`; install a release, or set `min_server_version` to match. |

### Clients get an error from the gateway

These rejections happen before the request reaches a provider, so they do not appear in the request log.

| Status and message | Cause and fix |
| --- | --- |
| 401 "Invalid gateway key. It may have been revoked, or removed because its OpenViking API key was regenerated…" | The client sent no gateway key, a revoked key or its provider API key, or the gateway deleted the key after the user's OpenViking API key was regenerated. Check which key the client actually sends, and issue a new one if needed. |
| 403 "Claude subscription OAuth credentials are not supported" | The client sent a Claude subscription token, for example Claude Code signed in with a subscription and no `ANTHROPIC_AUTH_TOKEN` set; or the upstream stores a subscription token. Use an API key. |
| 403 "Model is not allowed by this key" | The model is not in the key's allowed models. Issue a key that allows it. |
| 404 "No allowed upstream matches this protocol and model" | No enabled upstream bound to the key uses the API the client called and serves the requested model. Check the upstreams' protocol, models and aliases, whether they are enabled, and which upstreams the key is bound to. |
| 401 "Upstream API key is missing" | The upstream expects each client to send its own key in `X-OpenViking-Upstream-Key`, or an upstream where the gateway holds the key has none stored. |
| 403 "Coding Plan upstreams are disabled; configure a model API key" | The upstream is marked as a Coding Plan key. Use a model API key, or deliberately check **Allow this plan through the gateway**. |
| 404 "Unknown response for this key", 403 "Response upstream is no longer allowed" | The Responses response being looked up was not created with this key, or the upstream that created it is now turned off or no longer bound to the key. |
| 413 | The request body is larger than `max_body_bytes`, or than your proxy's own limit. |
| 426 | A WebSocket connection to the Responses API. Expected: Codex falls back to HTTP. |
| 502 "Model upstream is unavailable" | The gateway could not reach the provider, or the call took longer than `upstream_timeout_seconds`. Choose **Test** on the upstream. |
| 504 "Hidden tool request timed out" | The OpenViking tool rounds took longer than the profile's tool **Total time**; or, with no total time set, one model request during the tool rounds took longer than `upstream_timeout_seconds`. |

### No memory is added

First find the request in the request log. If it is not there, the client did not go through the gateway, or the gateway rejected the request (see the previous section). If it is there, expand it and check the memory search result:

- **Nothing relevant found** (`empty`): normal for a new user. Memories exist only after a conversation is committed and OpenViking has extracted them.
- **Recall is off or the budget is used up** (`disabled`): the profile has recall off, the conversation used up its budget, or the message is shorter than 3 characters.
- **OpenViking is unreachable or didn't answer in time** (`openviking_unavailable`): check the OpenViking card on the Overview tab. If OpenViking is running but slow, raise the **Time limit**.
- **OpenViking rejected the key** (`openviking_http_401`): the user's OpenViking key is no longer valid. Issue a new gateway key with the current key.
- **OpenViking is older than the gateway requires** (`openviking_version_mismatch`): see [Issuing a key fails](#issuing-a-key-fails).
- **OpenViking plugin in use** (`plugin_present`): the gateway detected an OpenViking plugin and stepped aside for this conversation.
- **The type is not New message**: tool steps, housekeeping and sub-agent requests never search; they replay the memory earlier messages received.

A message whose search failed never gets memory later; the next message searches again.

### Conversations do not appear in OpenViking

- **Saving runs one turn behind.** A turn appears when the next message arrives; the last turn waits for **Save the latest reply after** (10 minutes by default).
- **The profile does not save.** The **Saving** column shows Off.
- **Saving is retrying or paused.** The expanded row shows the reason. Once the cause is fixed (for example, OpenViking is reachable again), the gateway retries within 5 minutes. With `gateway_key_revoked`, issue a new gateway key; the next request resumes saving. If the conversation stays paused, use **Resync conversation…**.
- **An OpenViking plugin was detected** (`plugin_present`): the gateway saves nothing for that conversation.
- **You are looking as a different user.** Sessions belong to the OpenViking user behind the gateway key and are named `gateway-…`.
- **Memories show up later than sessions.** OpenViking extracts memories in the background, only after a commit.

### The first-call cache hit rate dropped

Compare it with what the provider reaches without the gateway, then look at the issues flagged on recent requests:

- **Memory record missing** (`missing_injection_record`): the gateway's storage was lost or restored from an old backup, or the record expired, so memory added earlier could not be replayed. Ongoing conversations recover after one miss.
- **Upstream switched** (`upstream_changed`): the conversation's upstream was turned off, is not bound to the key the client now uses, or no longer serves the model, so the conversation moved to another upstream.
- **Cache parameters changed** (`ark_cache_parameters_changed`): on Ark or ModelArk, the client changed the model, thinking mode, sampling parameters, system prompt or tools within a conversation.
- **No issue flagged**: the client itself may change earlier messages or its system prompt every turn, for example by inserting the current time, which the gateway cannot fix. Each compaction, and each new context window the model starts, also costs one miss; that is expected.

### Long conversations hit the context limit

When the provider rejects a long conversation as too long, it is usually one of three causes:

- **The gateway does not know the model's window.** The upstream's **Context windows** has no entry for the model and the profile has no **Default context window**, so the gateway assumes 1,000,000 tokens and compacts too late for a smaller model. Add the model's window.
- **The conversation's profile has compaction off.** Profile changes only reach new conversations.
- **Compaction failed.** The request detail shows the reason, and the gateway tries again after 60 seconds. If summaries keep getting cut off at the length limit, raise **Summary length**. `still_over_threshold` means the system prompt, tools and summary alone fill most of the window: lower **Summary length**, or check the model's context window setting.

### File imports fail

- "Set gateway.public_url for client uploads": set `public_url` to an address the client can reach.
- The upload link is unreachable or returns 404: make sure `public_url` is not an old `:1935` address, does not already end in `/gateway`, and that your proxy forwards `/gateway/uploads` like the other paths.
- 413: the file is larger than `max_body_bytes` or the proxy's limit.
- 502 "OpenViking upload is unavailable": the gateway cannot reach OpenViking Server.
- The model has no import tool: the OpenViking service does not provide it, the profile has it unchecked, or this request cannot use gateway tools. Check the tool list and the request detail.

### Issues on degraded requests

The Requests and Overview tabs flag requests the gateway could not fully handle. Studio shows the issue label; the code in parentheses is the matching value in the management API:

| Issue | What happened | What to do |
| --- | --- | --- |
| Memory unavailable (`memory_store_failure`) | The gateway could not read or write its storage, or the user's gateway data was just deleted. The request went to the model without memory; in Claude conversations, earlier thinking was removed. | Check disk space, permissions and the gateway log. Expected right after deleting a user's gateway data. |
| Forwarded unchanged (`unsafe_json`) | The request had numbers that cannot be re-encoded exactly, or duplicate keys, so the gateway forwarded it byte for byte without memory. | Usually specific to one client; nothing to change in the gateway. |
| Upstream switched (`upstream_changed`) | The conversation's upstream could no longer serve it, so it moved to another one, with one cache miss and earlier Claude thinking removed. | Turn the upstream back on, or give the client a key that includes it; or accept the switch. |
| Memory record missing (`missing_injection_record`) | In a Claude conversation, memory added earlier is no longer on record, so earlier thinking was removed once. | Expected after storage loss or in very old conversations. If it keeps happening, start a new conversation. |
| OpenViking plugin in use (`plugin_present`) | An OpenViking plugin was detected, and this conversation no longer uses gateway memory. | Expected when a plugin is in use. Use either the plugin or the gateway for a given client. |
| Cache parameters changed (`ark_cache_parameters_changed`) | On Ark or ModelArk, parameters that affect caching differ from the conversation's first request, so the prompt cache probably missed. | Keep the model, thinking mode, sampling parameters, system prompt and tools the same within a conversation. |
| Tool history unavailable (`hidden_tool_history_unavailable`) | A Claude conversation used OpenViking tools, but this request cannot use them, so earlier thinking was removed. | Check the tool skip reason in the request detail, restore the original settings, or start a new conversation. |
| OpenViking tools failed (`hidden_tool_loop_failed`) | The model could not finish its answer while using OpenViking tools, and the client received an error. | See [Failure handling](#failure-handling). |
| Reply not saved (`capture_parse_failure`) | A streamed reply could not be parsed, so it was not saved. The client was not affected. | Nothing to do; it is for information only. |
| Key not verified (`revocation_check_unavailable`) | OpenViking could not confirm the gateway key, so the request was served without checking whether the key was revoked. | Check that OpenViking is reachable and healthy. |
