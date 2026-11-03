---
description: 把 Claude Code、Codex、聊天应用、SDK 等客户端接入 OpenViking 网关，并检查接入是否生效。
---

# 把客户端接入 OpenViking 网关

本页按客户端给出具体配置，以及验证配置是否生效的方法。每个客户端都需要三样东西：网关地址、网关密钥（部署方开启 `openviking_keys` 时，也可以用用户的 OpenViking API 密钥，见[直接使用 OpenViking API 密钥](09-gateway-keys.md#openviking-keys)），以及这把密钥绑定的一个已启用上游——它要支持客户端使用的协议，并提供客户端请求的模型。还没有网关密钥？先看[快速上手](02-quickstart.md)。

以下两条限制对所有客户端都适用：

- 客户端不能用订阅账号登录网关。上游需要配置服务商的 API Key，或者接一个订阅反代：Codex 可以通过 CLIProxyAPI 上游使用 ChatGPT 订阅（见[使用 ChatGPT 订阅](#codex-chatgpt-subscription)）。其他订阅服务请自行确认其使用条款（另见[使用须知](01-how-it-works.md#what-to-expect)）。
- 使用 Responses 的客户端，每个请求都必须带上完整的对话历史（见[客户端要求](04-openviking-tools.md#client-requirements)）。

## 客户端设置一览 {#connect-clients}

网关地址是服务地址加上 `/gateway`。在 Studio 中，OpenViking 网关页面顶部和**接入**标签页都会显示它。下面的示例用 `https://ov.example.com/gateway`，请换成你自己的地址。Anthropic 客户端直接使用网关地址，OpenAI 兼容客户端在后面加 `/v1`，方舟 SDK 加 `/api/v3`。

| 客户端 | 上游协议 | Base URL | 对话识别方式 |
| --- | --- | --- | --- |
| [Claude Code](#claude-code) | Anthropic Messages | `https://ov.example.com/gateway` | Claude Code 的会话请求头 |
| [Codex CLI](#codex-cli) | Responses | `https://ov.example.com/gateway/v1` | Codex 的会话请求头 |
| [聊天客户端和 SDK](#chat-clients-and-sdks) | Chat Completions（或 SDK 使用的 API） | `https://ov.example.com/gateway/v1` | `X-OpenViking-Session`，需要自己发送 |
| [Open WebUI](#open-webui) | Chat Completions | `https://ov.example.com/gateway/v1` | 在连接上配置 `X-OpenViking-Session` 请求头 |
| [OpenCode](#opencode) | Chat Completions、Anthropic Messages 或 Responses | `https://ov.example.com/gateway/v1` | OpenCode 的会话请求头 |
| [pi](#pi) | Chat Completions、Anthropic Messages 或 Responses | `https://ov.example.com/gateway/v1`（Anthropic Messages 用 `https://ov.example.com/gateway`） | 通常根据对话历史匹配 |
| [DSH](#dsh) | Chat Completions、Anthropic Messages 或 Responses | `https://ov.example.com/gateway/v1`（Anthropic Messages 用 `https://ov.example.com/gateway`） | 通常根据对话历史匹配 |
| [火山方舟与 BytePlus 方舟 SDK](#volcano-engine-ark-and-byteplus-modelark-sdks) | 三种均可 | `https://ov.example.com/gateway/api/v3` 或 `https://ov.example.com/gateway/api/compatible` | 取决于客户端发送的请求头 |

最后一列的含义见[网关如何识别对话](#how-conversations-are-recognized)。

## 客户端自带服务商 API Key 时 {#bring-your-own-provider-key}

默认情况下，服务商的 API Key 由网关保管，客户端只需发送网关密钥。[上游](07-upstreams.md#upstreams)也可以把 **API Key 由谁提供**设为**每个客户端自带 API Key**（见[凭证](07-upstreams.md#credentials)）。这时客户端仍把网关密钥填在原来填 API Key 的地方，同时在每个请求里用 `X-OpenViking-Upstream-Key` 请求头带上自己的服务商 API Key。在 Studio 中，**接入**标签页的**客户端自带服务商 API Key**卡片会列出哪些上游需要它。

缺了这个请求头，所有请求都会返回 401 `Upstream API key is missing`。各客户端的添加方式见[在客户端里添加请求头](#add-a-header)。

## 覆盖上下文配置 {#override-profile-settings}

客户端可以用 `X-OpenViking-Context` 请求头，为新对话修改密钥所用上下文配置的设置。它的值是一个 JSON 对象，列出要修改的字段，例如 `{"recall":false}` 表示不召回，`{"disabled_tools":[]}` 表示提供全部 OpenViking 工具，包括会修改数据的工具。配置需要开启**允许客户端覆盖设置**（默认开启）；请求头能包含什么、可能返回哪些错误，见[允许客户端覆盖设置](08-context-profiles.md#client-overrides)。各客户端的添加方式见[在客户端里添加请求头](#add-a-header)。

对话会沿用开始时的设置，所以修改后的请求头从下一段新对话起生效，例如换一个新的 `X-OpenViking-Session` 值。与所有 `X-OpenViking-*` 请求头一样，它不会转发给服务商。

## 在客户端里添加请求头 {#add-a-header}

上面两个请求头都要随每个请求发送。各客户端的添加方式：

| 客户端 | 如何添加请求头 |
| --- | --- |
| Claude Code | `export ANTHROPIC_CUSTOM_HEADERS='<Name>: <value>'`。有多个请求头时每行一个，例如 `export ANTHROPIC_CUSTOM_HEADERS=$'X-OpenViking-Upstream-Key: <provider-key>\nX-OpenViking-Context: {"recall":false}'` |
| Codex CLI | 在服务商配置里，固定值用 `http_headers`，例如 `http_headers = { "X-OpenViking-Context" = '{"recall":false}' }`；从环境变量读取用 `env_http_headers`，例如 `env_http_headers = { "X-OpenViking-Upstream-Key" = "PROVIDER_API_KEY" }`，并把服务商 API Key 导出为 `PROVIDER_API_KEY` |
| OpenAI 和 Anthropic SDK | 在客户端上设置 `default_headers`，或在每个请求里传 `extra_headers` |
| Open WebUI | 加到连接的自定义请求头里，与会话请求头放在一起 |

## Claude Code {#claude-code}

Claude Code 使用 Anthropic Messages，Base URL 为 `https://ov.example.com/gateway`。启动前设置三个环境变量：

```bash
export ANTHROPIC_BASE_URL=https://ov.example.com/gateway
export ANTHROPIC_AUTH_TOKEN='<gateway-key>'
export CLAUDE_CODE_GATEWAY_HINT_HEADERS=1
```

- 地址不要带 `/v1`，Claude Code 会自己补全路径。
- `CLAUDE_CODE_GATEWAY_HINT_HEADERS=1` 让 Claude Code 给子 Agent、上下文压缩和后台请求加上标记，网关就不会为它们召回记忆，也不会把它们当作对话轮次保存。
- Claude Code 发送自己的会话 ID，网关据此识别对话，`--resume` 之后也一样。
- 上游必须接受 Claude Code 请求的模型名：模型列表留空、列出这些名称，或者用模型别名映射到服务商的模型（见[模型](07-upstreams.md#models)）。
- Claude 订阅登录不能通过网关使用，带订阅令牌的请求会被拒绝。请为上游配置服务商的 API Key。
- 同时装了 OpenViking 插件时，网关会让出这些对话（见[两者同时使用时](../guides/15-gateway.md#when-both-are-in-use)）。只接了 OpenViking MCP 服务器时，网关照常补充记忆，OpenViking 工具交给 MCP 服务器。

配好后，[检查接入是否生效](#check-the-connection)。

## Codex CLI {#codex-cli}

Codex 使用 Responses API，Base URL 为 `https://ov.example.com/gateway/v1`。它每个请求都带完整历史，正好满足网关的要求。在 `~/.codex/config.toml` 中添加一个服务商，两个顶层设置必须写在所有 `[section]` 之前：

```toml
model_provider = "openviking"
model = "<model>"

[model_providers.openviking]
name = "OpenViking Gateway"
base_url = "https://ov.example.com/gateway/v1"
wire_api = "responses"
env_key = "OPENVIKING_GATEWAY_KEY"
```

然后在运行 Codex 的环境里设置网关密钥：

```bash
export OPENVIKING_GATEWAY_KEY='<gateway-key>'
```

- 密钥绑定的上游必须使用 Responses，并提供 `<model>`。
- Codex 会先尝试 WebSocket 连接，被网关拒绝后自动改用 HTTP。
- Codex 发送自己的会话 ID，网关据此识别对话，`codex resume` 之后也一样。
- 遇到不认识的模型名，Codex 可能提示缺少模型元数据，不影响请求。
- 想用 ChatGPT 订阅代替 API Key，见下一节。

### 使用 ChatGPT 订阅 {#codex-chatgpt-subscription}

Codex 可以经网关使用你的 ChatGPT 订阅。Codex 不会把 ChatGPT 登录信息发给网关，而是由 [CLIProxyAPI](https://github.com/router-for-me/CLIProxyAPI)（CPA）作为上游接在网关后面：CPA 用你的 ChatGPT 账号登录，再以 Responses API 的形式提供服务。这种用法得到了 OpenAI 的认可：OpenAI Codex 负责人 Tibo 曾[公开介绍](https://x.com/thsottiaux/status/2076119366647894371)过用 CLIProxyAPI 接入 Codex 订阅的做法。

1. 按 CPA 的 README 运行 CPA，并用你的 ChatGPT 账号登录。
2. 在 Studio 中把 CPA 添加为上游：服务商选*通用*，协议选 Responses，**Base URL** 填 CPA 的地址，API Key 填 CPA 的某个 API 密钥。见[自定义上游](07-upstreams.md#custom-upstreams)。
3. 签发绑定该上游的网关密钥，再按上文配置 Codex。Codex 仍用网关密钥连接网关，订阅只保存在 CPA 里。

配好后，[检查接入是否生效](#check-the-connection)。

## 聊天客户端和 SDK {#chat-clients-and-sdks}

支持 OpenAI Chat Completions API 的客户端和 SDK 都能接入，Base URL 为 `https://ov.example.com/gateway/v1`。在客户端填写 OpenAI 兼容服务商的地方填入：

```text
Base URL: https://ov.example.com/gateway/v1
API key: <gateway-key>
Model: <model>
X-OpenViking-Session: <conversation-id>
```

`X-OpenViking-Session` 请求头可选，但建议发送，它告诉网关请求属于哪段对话。取值在同一段对话内保持不变、不同对话之间互不相同，例如应用里的聊天 ID。不发送时，网关根据历史匹配对话，见[网关如何识别对话](#how-conversations-are-recognized)。

用 OpenAI Python SDK：

```python
from openai import OpenAI

client = OpenAI(base_url="https://ov.example.com/gateway/v1", api_key="<gateway-key>")

reply = client.chat.completions.create(
    model="<model>",
    messages=[{"role": "user", "content": "发布日期最后定在哪天？"}],
    extra_headers={"X-OpenViking-Session": "chat-42"},
)
print(reply.choices[0].message.content)
```

流式调用 Chat Completions 时，请同时请求返回用量（`"stream_options": {"include_usage": true}`）。否则服务商不报告流式回复的 token 数，Studio 显示不了用量，网关也只能估算上下文窗口用了多少。

另外两种 API 的 SDK 用法相同，前提是上游使用的 API 和 SDK 一致：

- Anthropic SDK 指向 `https://ov.example.com/gateway`。
- Responses API 使用 `https://ov.example.com/gateway/v1`，每个请求都要带完整历史并设置 `store: false`；其他 Responses 请求直接转发，不补充记忆。

配好后，[检查接入是否生效](#check-the-connection)。

## Open WebUI {#open-webui}

Open WebUI 通过 Chat Completions 接入，Base URL 为 `https://ov.example.com/gateway/v1`。分三步配置：

1. 在 Open WebUI 中打开 Admin Panel → Settings → Connections，添加一个 OpenAI 兼容连接，Base URL 填 `https://ov.example.com/gateway/v1`，密钥填网关密钥。
2. 给这个连接加上下面的自定义请求头，让每个聊天成为一段独立的对话，并让网关认出 Open WebUI 的后台任务：

   ```json
   {
     "X-OpenViking-Session": "{{CHAT_ID}}",
     "X-OpenViking-Task": "{{TASK}}"
   }
   ```

3. 在 Open WebUI 的环境变量中设置 `RAG_SYSTEM_CONTEXT=true`。这样 Open WebUI 会把附件检索结果放进 system 消息，而不是临时改写你这一轮的消息，请求之间的对话历史才能保持稳定。

另外需要注意两点：

- **一个连接密钥只对应一位记忆归属者。** 通过这个连接聊天的所有 Open WebUI 用户，读写的都是密钥背后那位 OpenViking 用户的记忆。要么只让一个人用这个连接，要么接受这些用户共享记忆（见[每人一把密钥](09-gateway-keys.md#one-key-per-person)）。
- Open WebUI 还会通过同一个连接发送后台请求（生成标题、标签和追问建议）。网关能识别标题和摘要请求；如果请求日志里有其他后台任务被标成**新消息**，就把 Open WebUI 的任务模型改成一个不经过网关的连接。

配好后，[检查接入是否生效](#check-the-connection)。

## OpenCode {#opencode}

OpenCode 可以使用 Chat Completions、Anthropic Messages 或 Responses，Base URL 都是 `https://ov.example.com/gateway/v1`。在 `~/.config/opencode/opencode.json` 中添加一个服务商，与已有设置合并：

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

示例用的是 Chat Completions。换用其他协议时改 `npm`，`baseURL` 不变。密钥绑定的上游必须使用你选的协议；Studio 的**接入**标签页会自动填好对应的包：

| 上游协议 | `npm` |
| --- | --- |
| Chat Completions | `@ai-sdk/openai-compatible` |
| Responses | `@ai-sdk/openai` |
| Anthropic Messages | `@ai-sdk/anthropic` |

把网关密钥导出为 `OPENVIKING_GATEWAY_KEY`，然后在 OpenCode 中选择 `openviking/<model>`。同时装了 OpenViking 的 OpenCode 插件时，网关会让出这些对话。

配好后，[检查接入是否生效](#check-the-connection)。

## pi {#pi}

pi 可以使用 Chat Completions、Anthropic Messages 或 Responses，Base URL 为 `https://ov.example.com/gateway/v1`（Anthropic Messages 用 `https://ov.example.com/gateway`）。在 pi 的模型配置 `~/.pi/agent/models.json` 中添加一个服务商：

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

示例用的是 Chat Completions。换用其他协议时，按下表设置 `api` 和 `baseUrl`，密钥绑定的上游也必须使用这种协议。Anthropic Messages 的地址不带 `/v1`，因为 Anthropic SDK 会自己补上：

| 上游协议 | `api` | `baseUrl` |
| --- | --- | --- |
| Chat Completions | `openai-completions` | `https://ov.example.com/gateway/v1` |
| Responses | `openai-responses` | `https://ov.example.com/gateway/v1` |
| Anthropic Messages | `anthropic-messages` | `https://ov.example.com/gateway` |

- `apiKey` 从环境变量 `OPENVIKING_GATEWAY_KEY` 读取网关密钥，启动 pi 前先导出它。开头的 `$` 不能省，否则 pi 会把这个字符串本身当作密钥发送。
- pi 通常不发送会话请求头，网关根据历史匹配它的对话（见[网关如何识别对话](#how-conversations-are-recognized)）。
- pi 自己的 OpenViking 扩展启用时，网关会让出这些对话，两者选一个即可。

配好后，[检查接入是否生效](#check-the-connection)。

## DSH {#dsh}

[DeepSeek Harness](https://www.npmjs.com/package/@deepseek-ai/dsh)（`dsh`）的模型层基于 pi，`api` 和 Base URL 的取值与 pi 相同：`https://ov.example.com/gateway/v1`，Anthropic Messages 用 `https://ov.example.com/gateway`。在 profile 配置中添加一个服务商；`dsh web` 使用的 profile 是 `web`，所以文件是 `~/.dsh/profiles/web/cordis.patch.yml`；DeepSeek Harness Desktop 桌面应用用的是 `~/.dsh/profiles/desktop/cordis.patch.yml`：

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

如果文件里已经有 `llm-pi-ai` 条目，就把 `openviking` 加到它的 `providers` 下，不要再写第二个条目。使用 Responses 或 Anthropic Messages 时，按 [pi 一节的表格](#pi)设置 `api` 和 `baseURL`。

把网关密钥导出为 `OPENVIKING_GATEWAY_KEY`（也就是 `apiKeyEnv` 填的变量名），然后启动 `dsh web`，在模型选择器中选择 `openviking` 服务商下的 `<model>`。

- 也可以在 Web UI 的 Settings → Models → Add model provider → Custom model API 中添加，写入的是同一个文件。
- Desktop 看不到在终端里 export 的变量。macOS 上把 `OPENVIKING_GATEWAY_KEY` 写进 shell 启动文件（`~/.zprofile` 或 `~/.zshrc`）；Windows 上用 `setx` 设置。然后用 ⌘Q 或托盘菜单退出 Desktop，再重新打开。Desktop 的恢复对话框会把 `cordis.patch.yml` 挪成 `.bak-<时间戳>` 文件，运行过恢复后需要重新添加服务商。
- DSH 的一个服务商只使用一种协议。要通过多种协议使用网关，就为每种协议各声明一个服务商。
- 除非 DSH 发送了某个会话请求头，否则网关根据历史匹配它的对话（见[网关如何识别对话](#how-conversations-are-recognized)）。
- 同一个 profile 里装了 [OpenViking 的 DSH 插件](../agent-integrations/17-dsh.md)时，网关会让出这些对话，两者选一个即可。

配好后，[检查接入是否生效](#check-the-connection)。

## 火山方舟与 BytePlus 方舟 SDK {#volcano-engine-ark-and-byteplus-modelark-sdks}

已经接入火山方舟或其海外站 BytePlus 方舟的客户端和 SDK，三种 API 都可以用，只需替换地址，通常换成 `https://ov.example.com/gateway/api/v3`。在 `/gateway` 下，网关既接受方舟自己的路径（`/api/v3/chat/completions`、`/api/v3/responses`、`/api/v3/models` 和 `/api/compatible/v1/messages`），也接受标准的 `/v1` 路径：

| 原地址 | 网关地址 |
| --- | --- |
| `https://ark.cn-beijing.volces.com/api/v3` | `https://ov.example.com/gateway/api/v3` |
| `https://ark.ap-southeast.bytepluses.com/api/v3` | `https://ov.example.com/gateway/api/v3` |
| `https://ark.cn-beijing.volces.com/api/compatible`（Anthropic 兼容） | `https://ov.example.com/gateway/api/compatible` |

用网关密钥代替方舟的 API Key。以火山方舟 Python SDK 为例：

```python
from volcenginesdkarkruntime import Ark

client = Ark(base_url="https://ov.example.com/gateway/api/v3", api_key="<gateway-key>")
```

客户端用哪个路径，只决定它使用哪种 API。请求仍然发往密钥绑定的、使用这种 API 并提供该模型的上游，通常是服务商选为“火山方舟”或“BytePlus 方舟（海外站）”的上游（见[上游](07-upstreams.md#upstreams)）。和其他聊天客户端一样，请用 `X-OpenViking-Session` 为每段对话发送一个固定的值。

配好后，[检查接入是否生效](#check-the-connection)。

## 检查接入是否生效 {#check-the-connection}

每配好一个新客户端，都用一段简短的对话验证一次。Open WebUI、OpenCode、pi 和 DSH 尤其需要：它们的配置是依据各自文档中的服务商设置编写的，请当作起点。

1. 在客户端的同一段对话（同一个聊天或同一个 Agent 会话）里发两条消息。应该能正常收到回复。
2. 在 Studio 中打开**请求日志**标签页。两个请求都应显示为**新消息**，状态码为 200。
3. 展开这两个请求。它们的**对话**应该相同，**保存**一栏应显示**正常**，表示保存已开启且运行正常。第二条消息到达后第一轮就会保存；要看到它，请以密钥所属用户的身份登录 Studio，在**会话**页面找到 `gateway-…` 会话（见[确认记忆生效](02-quickstart.md#confirm-that-memory-works)）。

如果结果不对：

- **看不到任何请求。** 只有到达模型服务商的请求才会记录，说明客户端直接收到了网关返回的错误，见[客户端收到网关返回的错误](12-troubleshooting.md#clients-get-an-error-from-the-gateway)。
- **两个请求属于不同的对话。** 客户端没有发送会话请求头，且历史也没有匹配上，见[网关如何识别对话](#how-conversations-are-recognized)。
- **记忆一栏一直是空的。** 全新账号本来就是这样。要完整验证记忆是否生效，按[确认记忆生效](02-quickstart.md#confirm-that-memory-works)操作。

请求日志每一列和每个标签的含义见[请求日志](10-overview-and-requests.md#requests)。

## 网关如何识别对话 {#how-conversations-are-recognized}

对话是网关当作同一次聊天处理的一系列请求：一段对话固定使用同一份上下文配置和同一个上游，并保存到一个 OpenViking 会话，某些情况下改存到新会话，例如客户端改写历史时（见[保存状态](10-overview-and-requests.md#saving-status)）。为了判断请求属于哪段对话，网关按以下顺序取第一个出现的请求头：

1. `X-OpenViking-Session`
2. `thread-id`
3. `x-claude-code-session-id`（Claude Code）
4. `x-opencode-session-id`（OpenCode）
5. `x-session-id`
6. `session-id`（Codex CLI）

这些请求头都没有时，网关查看历史中最近一条助手回复：如果恰好有一段之前的对话产生过这条回复，请求就并入那段对话，否则开始一段新对话。开场消息相同的两段对话不会因此被合并。普通的一问一答这样就能识别，但重试、重新生成和内容完全相同的对话存在歧义，可能被当成新对话。所以只要客户端允许设置请求头，就发送 `X-OpenViking-Session`。

对话归属于密钥背后的 OpenViking 用户，并按 API 区分。同一用户的两个网关密钥发送相同的会话值，会共享同一段对话；同一个会话值用在另一种 API 上，则是另一段对话。网关转发前会去掉所有 `X-OpenViking-*` 请求头，服务商看不到它们。

请求没有归入已有对话时，记忆照样工作：新消息照常召回，之前补充的记忆也照常原样放回，因为网关是根据消息本身找到这些记忆的。区别在于这个请求会开始一段新对话：它的轮次保存到新的 OpenViking 会话，记忆预算从头计算，并使用密钥当前的上下文配置。

## 下一步 {#next-steps}

- [使用须知](01-how-it-works.md#what-to-expect)：客户端改走网关后，哪些行为会变化。
- [上下文配置](08-context-profiles.md)：调整召回多少记忆、是否保存对话。
- [OpenViking 网关排障](12-troubleshooting.md)：处理报错、记忆缺失或对话没有保存等问题。
