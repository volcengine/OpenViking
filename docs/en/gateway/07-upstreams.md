---
description: Add and configure the model endpoints that OpenViking Gateway forwards requests to.
---

# Manage gateway upstreams

This page is for whoever adds or fixes the model endpoints that OpenViking Gateway forwards requests to: add an upstream that works the first time, then look up each editor field in the order Studio shows it. In Studio: **Upstreams** tab. Adding, changing and testing upstreams needs write access; see [Who can change what](06-manage-in-studio.md#who-can-change-what).

## What an upstream is {#upstreams}

An upstream is one endpoint of a model service that the gateway forwards requests to, and it uses exactly one API. The gateway does not convert between APIs, so add one upstream for each API your clients use, even when they come from the same provider:

| Protocol | Used by |
| --- | --- |
| Anthropic Messages | Claude Code and Anthropic SDKs |
| Chat Completions | Most chat apps and SDKs |
| Responses | Codex CLI, when every request carries the full history (see [Client requirements](04-openviking-tools.md#client-requirements)) |

Upstreams are shared by the whole account, but a [gateway key](09-gateway-keys.md#gateway-keys) reaches only the upstreams selected when it was issued.

When you choose a provider in the editor, its default Base URL is filled in:

| Provider | Protocols | Default Base URL |
| --- | --- | --- |
| Generic | All three | None; enter it yourself |
| Anthropic | Anthropic Messages | `https://api.anthropic.com` |
| OpenAI | Chat Completions, Responses | `https://api.openai.com/v1` |
| DeepSeek | All three | `https://api.deepseek.com`; `https://api.deepseek.com/anthropic` for Anthropic Messages |
| Volcano Engine Ark | All three | `https://ark.cn-beijing.volces.com/api/v3` for Chat Completions and Responses; `https://ark.cn-beijing.volces.com/api/compatible` for Anthropic Messages |
| BytePlus ModelArk | All three | `https://ark.ap-southeast.bytepluses.com/api/v3` for Chat Completions and Responses; `https://ark.ap-southeast.bytepluses.com/api/compatible` for Anthropic Messages |

## Deployment default upstreams {#deployment-default-upstreams}

Operators can set `default_upstreams` in the gateway configuration for clients that [use OpenViking API keys directly](09-gateway-keys.md#openviking-keys); see the [configuration example](13-configuration.md#gateway-fields).

- Defaults apply only when the account has no stored upstreams. Once it stores any upstream, its stored set replaces the defaults as a whole, even if every stored upstream is disabled.
- Deployment defaults are not copied into account storage and do not appear in Studio. Issued gateway keys continue to use their selected, stored upstreams.
- Keep the object keys, such as `ark-chat`, stable: each key is the upstream ID frozen into conversations and used to route later turns.
- For managed credentials, write `"api_key": "$ARK_API_KEY"` and inject `ARK_API_KEY` from a Secret through `extraEnv` in Helm deployments. The gateway expands environment references when loading the file; keep plaintext API keys out of ConfigMaps.

## Add an upstream {#add-an-upstream}

1. On the **Upstreams** tab, choose **Add upstream**. The **New upstream** editor opens with four sections: **Endpoint**, **Credentials**, **Models** and **Routing**.
2. Under **Endpoint**, enter a **Name**, then choose the **Provider** and the **Protocol** your clients use. Below **Base URL**, **Requests go to** should show the full address of the provider's endpoint, for example `https://api.openai.com/v1/chat/completions`.
3. Under **Credentials**, keep **The gateway holds the API key** and paste the provider's **API key**.
4. Under **Models**, list the model names clients will request, or leave it empty to accept any name. Add a **Context windows** entry for any model with a window smaller than the default; see [Set the model's context window](05-long-conversations.md#context-window).
5. Choose **Add upstream**. You should see **Added “&lt;name&gt;”** and the new upstream in the list. If the editor footer shows **Still to fill in or fix** instead, correct the fields it names.
6. Choose **Test** on the new upstream's row, then choose a model and **Run test**. You should see **Connected**, then **Model works**; see [Test and delete](#test-and-delete) if not.
7. [Issue a gateway key](09-gateway-keys.md#issue-a-key) that selects the upstream. Existing keys keep the upstreams they were issued with.

## Endpoint {#endpoint}

The **Endpoint** section sets where the gateway sends requests and which API the [upstream](#upstreams) speaks. A new upstream starts as provider *Generic* with protocol *Chat Completions*.

| Field | What it decides |
| --- | --- |
| **Name** | The label shown in Studio and when you issue gateway keys. |
| **Provider** | Which protocols you can pick, and how the gateway handles that provider's differences (below). |
| **Protocol** | Which API the upstream accepts. Anthropic upstreams send both `x-api-key` and `Authorization: Bearer`; Chat Completions and Responses upstreams send only Bearer. |
| **Base URL** | The provider address the gateway appends the endpoint to (see [How the Base URL is joined](#base-url)). |
| **Append protocol endpoint path** | On by default. Turn it off only when the Base URL is already the complete request endpoint (see [Send to an exact endpoint](#exact-endpoint)). |

### What each provider changes {#provider-differences}

Choose *Generic* for a provider not in the list, for a compatible proxy such as LiteLLM or new-api, and for a reverse proxy such as [CLIProxyAPI](https://github.com/router-for-me/CLIProxyAPI) that exposes subscription accounts as an API; see [Custom upstreams](#custom-upstreams).

- *Generic*, *Anthropic* and *OpenAI* forward requests the same way.
- *DeepSeek*: when a request has tools, DeepSeek requires every earlier reply in the history to carry its reasoning, and many chat apps do not send it back. DeepSeek upstreams have [**Restore reasoning the client drops**](#restore-reasoning) on by default, so requests can keep thinking on and still get OpenViking tools. With that setting off, OpenViking tools are offered only when a request turns thinking off (`"thinking": {"type": "disabled"}`). Standard Responses requests have no `thinking` field, so they then get no tools. Memory is added as usual.
- *Volcano Engine Ark* and *BytePlus ModelArk* behave the same: each Chat Completions and Responses conversation gets a fixed `prompt_cache_key`, so Ark's prefix cache follows the conversation. If a request's model, thinking mode, sampling parameters, system prompt or tools differ from the conversation's first request, Ark rebuilds its cache, and the request is flagged **Cache parameters changed** (see [Issues on degraded requests](12-troubleshooting.md#issues-on-degraded-requests)).

### How the Base URL is joined {#base-url}

Copy the Base URL from the provider's SDK documentation. The gateway appends the protocol's endpoint to it:

| Protocol | Appended path | Note |
| --- | --- | --- |
| Chat Completions | `/chat/completions` | A bare domain does not get `/v1` added, so OpenAI needs `https://api.openai.com/v1`. |
| Responses | `/responses` | Same as Chat Completions. |
| Anthropic Messages | `/v1/messages` | The Base URL must not end in `/v1`; saving such a URL is rejected. |

- A trailing slash is removed. Addresses with a version path, such as `/api/paas/v4`, work as is.
- The Base URL may carry a query, such as `?api-version=…`. The client's query parameters follow it, and on a name clash the Base URL's value wins.
- Requests that clients send to the Ark paths (`/gateway/api/v3/`, `/gateway/api/compatible/v1/`) follow the same rules.
- An upstream has one Base URL. If a provider uses different addresses for different protocols, create separate upstreams.

The editor previews the final request URL as you type. If an OpenAI or Ark address is missing its path, or an Anthropic address ends in `/v1`, it warns you and offers a one-click fix.

Base URLs for some providers without a preset, all following the rules above:

| Service | Protocol | Base URL |
| --- | --- | --- |
| Azure OpenAI (v1 API) | Chat Completions or Responses | `https://RES.openai.azure.com/openai/v1` |
| Claude in Microsoft Foundry | Anthropic Messages | `https://RES.services.ai.azure.com/anthropic` |
| Gemini (OpenAI-compatible API) | Chat Completions | `https://generativelanguage.googleapis.com/v1beta/openai` |
| Zhipu | Chat Completions | `https://open.bigmodel.cn/api/paas/v4` |
| Zhipu | Responses | `https://open.bigmodel.cn/api/v1` |
| Ark Coding Plan | Chat Completions, Responses | `…/api/coding/v3` |
| Ark Coding Plan | Anthropic Messages | `…/api/coding` |

### Send to an exact endpoint {#exact-endpoint}

Turn off **Append protocol endpoint path** when the Base URL is already the complete request endpoint. For example, an Azure OpenAI deployment uses:

- **Protocol** *Chat Completions* and **Provider** *Generic*
- **Base URL** `https://RES.openai.azure.com/openai/deployments/DEPLOYMENT/chat/completions?api-version=2024-10-21`
- **Append protocol endpoint path** off
- **Send the key as** *api-key header*

The gateway then sends the protocol's requests to that exact URL without adding a protocol path. Sub-paths continue after it: Anthropic token counting goes to `…/count_tokens`. Other APIs, such as embeddings and the model list, are not routed to such an upstream, and the connection test skips it.

## Credentials {#credentials}

The **Credentials** section sets how the gateway signs in to the provider. By default the gateway holds the provider API key, and clients need only their [gateway key](09-gateway-keys.md#gateway-keys).

- **Who provides the API key**:
  - **The gateway holds the API key** (the default): the gateway sends the upstream's API key.
  - **Each client sends its own key**: besides the gateway key, every request carries the client's own provider API key in the `X-OpenViking-Upstream-Key` header. Without it the request fails with 401 "Upstream API key is missing". The gateway passes the key on as set in **Send the key as**, or fills it into the URL when the Base URL contains `{api_key}`; `X-OpenViking-*` headers themselves are never forwarded. How each client adds the header is in [When each client sends its own provider key](03-connect-clients.md#bring-your-own-provider-key).
- **Send the key as** (provider *Generic* only): `Authorization: Bearer` (the default), the `api-key` header (Azure OpenAI) or the `x-api-key` header.
- **Key in the URL**: for a provider that expects the key in the address, write `{api_key}` in the Base URL's path or query, for example `?key={api_key}`. The gateway fills in the URL-encoded key when sending and then sends no auth header. Don't write the key itself into the address: the Base URL is visible to everyone who can view upstreams.
- **API key**: stored encrypted and never shown again. Leave it blank to keep the stored key.
- **Extra headers** are added to every request to this upstream, such as a provider's version or organization header, and replace any client header with the same name. Their values are write-only like the API key: leave a value blank to keep it, or remove the row to delete the header. `Host`, `Content-Length`, `Transfer-Encoding` and `Connection` cannot be set.

> **Changing the Base URL clears the stored API key and header values.** Enter them again whenever you change the address. A stored key is never sent to a new address.

### Coding Plan and subscription keys {#coding-plan-keys}

Providers usually allow subscription keys such as Coding Plan keys only in their own coding tools, and using one through a shared gateway can get the subscription suspended. The gateway cannot tell from the key itself whether it is a subscription key, so you have to mark it: check **This key is a Coding Plan or subscription key**.

Once marked, every request to the upstream is rejected with 403 "Coding Plan upstreams are disabled; configure a model API key", unless you also check **Allow this plan through the gateway**. Check that only after confirming that your plan permits use outside the provider's tools.

Claude subscription tokens (`sk-ant-oat…`) are always rejected, whichever box you check; see [Behaviors to expect](01-how-it-works.md#what-to-expect).

## Models {#models}

The **Models** section sets which model names an [upstream](#upstreams) serves and what they are called at the provider. By default the list is empty and the upstream accepts any model name.

| Field | What it does |
| --- | --- |
| **Models** | The model names this upstream serves. Only listed names and aliases appear in the model list (`/gateway/v1/models`). |
| **Model aliases** | Map a name that clients use to the model sent to the upstream, for example the Claude model names Claude Code requests to a model your provider serves. Aliases appear in the model list, and the request log shows the name the client used. |
| **Context windows** | Each model's window size in tokens (at least 1,024), keyed by the model name actually sent to the upstream, after aliases. |

Long conversations are compacted at a share of the context window, so list every model whose window is smaller than the default; see [Set the model's context window](05-long-conversations.md#context-window).

## Routing {#routing}

The **Routing** section sets how the gateway chooses between upstreams. For each request, the gateway looks at the enabled [upstreams](#upstreams) bound to the key that use the request's API and serve the requested model, and picks the one with the highest **Priority**. Once a conversation starts, it stays on its upstream as long as that upstream can still serve it.

| Field | Default | What it does |
| --- | --- | --- |
| **Priority** | 0 | Higher wins. A change affects only new conversations; see [When changes take effect](06-manage-in-studio.md#when-changes-take-effect). |
| **Enabled** | On | A turned-off upstream receives no requests and disappears from the model list. |
| **Allow OpenViking tools** | On | Whether [OpenViking tools](04-openviking-tools.md#choose-tools) can be offered through this upstream. Turning it off takes effect at once, also in conversations that already have the tools. |
| **Restore reasoning the client drops** | Depends on the provider | Puts dropped reasoning back into later requests (below). |
| **Minimum cacheable prompt** | 1,024 tokens | Ark and ModelArk only: the shortest prompt the model can cache. It only affects how the request log reports cache eligibility. |

Turning an upstream off moves the conversations that were using it to the next candidate. That costs one provider cache miss and drops earlier Claude thinking blocks, and those requests are flagged **Upstream switched** (see [Issues on degraded requests](12-troubleshooting.md#issues-on-degraded-requests)). Turn an upstream off instead of deleting it if you may want it back.

The gateway does not fail over: when the chosen upstream returns an error, the client receives it, and an unreachable provider returns 502 "Model upstream is unavailable".

### Pick the upstream per request {#upstream-url-header}

A service in front of the gateway can choose the upstream for each request with the `X-OpenViking-Upstream-URL` header; clients may send it too. Its value must equal the **Base URL** of an upstream bound to the key (for an OpenViking key without stored upstreams, a [deployment default upstream](#deployment-default-upstreams)); only a trailing `/` is ignored. The header narrows routing to those upstreams, and the usual API, model and **Priority** rules choose among them, so a Chat Completions and a Responses upstream can share one Base URL.

- No bound upstream has that Base URL: the request fails with 403 "Upstream URL is not allowed". The gateway never sends a request to an address that is not configured.
- The gateway uses the selected upstream's credentials as usual. For **Each client sends its own key**, the key comes in `X-OpenViking-Upstream-Key`; a Coding Plan upstream is still rejected unless it is allowed.
- A conversation that names a different Base URL moves to that upstream, flagged **Upstream switched** as when its upstream is turned off.
- Without the header, routing is unchanged. Like every `X-OpenViking-*` header, it is not forwarded to the provider.

### Restore reasoning the client drops {#restore-reasoning}

When clients send the history back, they often leave out the reasoning of the model's earlier replies. **Restore reasoning the client drops** is on by default for DeepSeek, Volcano Engine Ark and BytePlus ModelArk, and off for other providers.

With it on, the gateway records the reasoning of every reply it relays and puts it back in later requests:

- Chat Completions: `reasoning_content`.
- Anthropic Messages: thinking blocks.
- Responses: reasoning items with plain-text content, before their item.

The restored content is byte-identical every time, so the provider's prompt cache keeps hitting, but it counts as input tokens. Replies whose reasoning the client sent back itself are left alone.

Some requests cannot get their reasoning back even with this on: the conversation moved to another upstream (**Upstream switched**), a Claude conversation lost its memory record (**Memory record missing**), or the client sent thinking back as plain text. On a DeepSeek upstream, such requests also get no OpenViking tools.

### Upstreams that fail every request {#blocked-upstreams}

The upstream list flags two states in which every request to an upstream fails:

| Label | Cause | Clients receive |
| --- | --- | --- |
| **Key missing** | The gateway holds the key but has none stored, for example after the Base URL changed. | 401 "Upstream API key is missing" |
| **Requests blocked** | A [Coding Plan upstream](#coding-plan-keys) not allowed through the gateway. | 403 |

Such an upstream still takes part in routing. If it has the highest priority among the matching upstreams, requests fail instead of moving to a lower-priority one. Fix it, or turn it off.

## Test and delete {#test-and-delete}

**Test** checks that an [upstream](#upstreams)'s saved endpoint, key, model and protocol work together. It always uses the saved settings, so save your changes first.

1. Choose **Test** in the upstream list or in the upstream editor's header. The gateway checks the saved connection; you should see **Connected**. An exact endpoint has no model list to check, so this step is skipped.
2. Select or enter a model name and choose **Run test**. The gateway sends one small generation request with the stored credentials; you should see **Model works**. The provider may charge for this request.

The model test includes OpenViking tools when the upstream allows them and leaves reasoning effort at the model default. Each result is explained in [Upstream tests](../api/25-gateway.md#upstream-tests).

**Test** is unavailable:

- without write access, where it is hidden;
- for upstreams set to **Each client sends its own key**, because there is no stored key to test with;
- for upstreams that have no stored API key yet.

Coding Plan upstreams stay blocked from model tests until **Allow this plan through the gateway** is checked.

**Delete** removes the upstream together with its stored API key and headers. An upstream that keys still use cannot be deleted: the delete dialog offers **Disable** instead, or you can revoke those keys first.

## Import OpenViking model settings {#import-openviking-model-settings}

Importing turns the OpenViking server's VLM credentials into upstreams in the current account, so you need not retype them. It is available only in an embedded deployment. Choose **Import from OpenViking** in the Upstreams tab header; while the list is still empty, the button is called **Use OpenViking's model settings**. Without a management key, only root (including local dev) can import; with one, [unlock editing](06-manage-in-studio.md#unlock-editing) first.

The result shows three groups: imported, needs confirmation and not imported.

- **Needs confirmation**: upstreams whose Base URL has a path segment named `coding` or `plan` (for example `/api/coding/v3`) are marked as [Coding Plan](#coding-plan-keys). Once you have confirmed that the provider's terms allow it, check **Allow this plan through the gateway** to use one.
- **Azure OpenAI** credentials (provider `azure`, or LiteLLM `azure/<deployment>`) become exact-endpoint upstreams that send the key in the `api-key` header, with the configured `api_version`.
- **Not imported**: Codex OAuth credentials, LiteLLM credentials without `api_base`, and LiteLLM routes that do not use the OpenAI Chat Completions API. The reasons are listed in [Import VLM settings](../api/25-gateway.md#import-vlm-settings).

Importing again does not create duplicates:

| Refreshed from the server | Kept as you changed it in the gateway |
| --- | --- |
| Address, API key, headers, protocol, provider, the Coding Plan mark, **Append protocol endpoint path**, **Send the key as** | Name, enabled state, priority, models, aliases, context windows, **Allow this plan through the gateway** |

If you uncheck the Coding Plan mark on an imported upstream, the next import marks it again, and the upstream is blocked until you check **Allow this plan through the gateway**.

## Custom upstreams: proxies and subscription quota {#custom-upstreams}

Any service that speaks one of the three APIs can be an upstream, not only a model provider. Two common uses:

- **Billing, quotas and load balancing.** The gateway does none of these. If you need them, put a service such as LiteLLM or new-api behind the gateway and add it as an upstream.
- **Subscription quota.** Clients cannot sign in to the gateway with a subscription, but you can add a reverse proxy such as [CLIProxyAPI](https://github.com/router-for-me/CLIProxyAPI) as an upstream. It exposes subscription accounts such as ChatGPT (Codex) and Claude as an API, so Codex, for example, can use a ChatGPT subscription through the gateway. OpenAI accepts this for Codex: Tibo, who leads Codex at OpenAI, has [publicly walked through](https://x.com/thsottiaux/status/2076119366647894371) connecting a Codex subscription this way, and the steps are in [Use a ChatGPT subscription](03-connect-clients.md#codex-chatgpt-subscription). **For any other subscription service, check its terms yourself before using it this way.**

To add one, choose *Generic* as the provider and follow [Add an upstream](#add-an-upstream).

API names, defaults and allowed values for every field are in [Upstream settings](13-configuration.md#upstream-settings).

## Next steps {#next-steps}

- [Issue a gateway key](09-gateway-keys.md#issue-a-key) that selects your upstreams.
- [Connect a client](03-connect-clients.md#connect-clients) to the gateway.
- [Set up a context profile](08-context-profiles.md#context-profiles) for recall and saving.
- [The upstream returned an error](12-troubleshooting.md#the-upstream-returned-an-error) if requests fail.
