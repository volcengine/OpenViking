---
description: 让模型通过 OpenViking 网关代为执行的工具检索、读取和写入 OpenViking 记忆，适用于任意客户端。
---

# 网关中的 OpenViking 工具

开启 OpenViking 工具后，模型在回答过程中自己检索、读取和写入 OpenViking 记忆，工具调用由网关替客户端执行。这项功能面向接不了 OpenViking MCP 服务器的聊天应用和 API 应用；Claude Code、Codex 这类支持 MCP 或有插件的客户端，用 MCP 或插件更合适，因为它们会显示每次工具调用和结果，并在调用前请求确认。在 Studio 中的位置：**上下文配置** → **OpenViking 工具**，以及每个上游的**允许 OpenViking 工具**。

## 让任意客户端拥有 Agentic 记忆 {#agentic-memory-for-any-client}

自动召回是网关替模型猜它需要什么；开启 OpenViking 工具后，由模型自己决定查什么、读什么、记什么。工具由网关执行，客户端的请求里一个工具都没有也能用。

![开启 OpenViking 工具后的一条回复：网关在客户端的请求上附加召回的记忆和 OpenViking 工具定义，模型调用 OpenViking 工具时由网关用该用户的 OpenViking 密钥执行，再把结果接回请求继续询问模型，直到模型给出最终回答；客户端收到的是一条带提示行的连贯回复，工具往返在下一轮被原样放回](../../images/gateway/tool-loop.zh.svg)

- **一条回复里完成多步操作**：网关拦下模型发出的 OpenViking 工具调用，用该用户的 OpenViking 密钥执行，把结果接回再问模型，直到模型不再调用。客户端只收到一条连贯的回复，流式和非流式都支持。
- **客户端自己的工具照常可用**：同一条回复里，模型可以同时调用 OpenViking 工具和客户端工具（例如 Bash）。客户端工具的调用原样交给客户端执行，照常经过客户端的权限确认。
- **下一轮还记得**：工具调用和结果不在客户端的历史里，网关把这些往返存下来，下一轮原样放回。模型记得自己查到过什么，[提示词缓存](01-how-it-works.md#replay)也不受影响。

## 不同客户端得到什么 {#what-each-kind-of-client-gets}

| 客户端 | 不经过网关时 | 经过网关并开启 OpenViking 工具后 |
| --- | --- | --- |
| 聊天应用，例如 Cherry Studio、Open WebUI | 要用记忆得自己配 MCP 或写工具，普通对话只收发文本。 | 模型自己检索记忆、按 URI 读取原文。勾选 `remember` 后，能记下用户要求记住的事；勾选 `add_resource` 后，能把用户附带的文件导入 OpenViking。不带任何工具的纯聊天模式也能用。 |
| SDK 脚本、低代码平台 | 一问一答，多步操作要自己写循环。 | 一次请求里完成“检索 → 读取 → grep 精确匹配 → 作答”，代码不用改。 |
| Claude Code、Codex 等编程 Agent（未装 OpenViking 插件或 MCP） | 有自己的工具，但没有 OpenViking。 | OpenViking 工具和 Bash 等客户端工具在同一条回复里混用。需要逐次确认工具调用时，仍建议用插件或 MCP。 |

## 模型能用哪些工具 {#which-tools-the-model-can-use}

工具清单直接来自你的 OpenViking 服务提供的 MCP 工具，名称加上 `openviking_` 前缀，所以 OpenViking 以后新增的工具也会自动可用。模型能做的事不超出该用户自己的 OpenViking 权限。常用的几类：

| 用途 | 工具 | 模型拿来做什么 |
| --- | --- | --- |
| 检索 | `find`、`search`、`grep`、`glob` | 语义检索记忆和资料；按正则或文件名精确查找。 |
| 浏览和读取 | `list`、`tree`、`read` | 按 URI 展开召回条目的原文，浏览记忆和技能目录。 |
| 写入 | `remember`、`write`、`edit` | 存一条长期记忆，写入或局部修改记忆文件。 |
| 导入 | `add_resource`、`add_skill` | 按 URL 导入网页或代码仓库，导入对话附件，用一段文本新建技能，都不需要客户端有 shell。 |
| 删除和权限 | `forget`、`set_acl` 等 | 永久删除记忆，修改共享资源的访问权限。 |

新建的上下文配置默认勾选哪些工具，见[选择工具](#choose-tools)。另有一项实验性设置 **Agent 自管上下文窗口**，会再给模型两个用于开启新上下文窗口的工具，见[实验性：Agent 自管上下文窗口](05-long-conversations.md#experimental-agent-managed-context-windows)。

## 用户看到什么 {#what-users-see}

模型的文字照常实时流出。开启**显示工具调用**时（默认开启，位于[上下文配置](08-context-profiles.md#context-profiles)的 **OpenViking 工具**部分），每次 OpenViking 工具调用都会在回复中调用发生的位置留下一行提示：

```text
> OpenViking find: "上次定的发布日期" — done
> OpenViking read: viking://user/alice/memories/release.md — done
```

- **提示写什么**：工具名；网关能识别调用对象时，还显示检索的查询词，读取、列出或写入的 URI，或者正在导入的文件或技能。用户看不到完整参数和结果。
- **何时出现**：流式回复中，调用一开始提示就出现，导入这类耗时的调用进行时，用户也能看到网关在工作；非流式回复在最终消息里带上同样的提示。
- **放在哪里**：每条提示单独成段。在 Chat Completions 中，提示是回复文本的一部分；在 Anthropic Messages 中，同一轮调用的提示放在同一个文本块里；在 Responses 中，放在同一条助手消息里。
- **只给用户看**：模型看不到这些提示，它收到的是真实的调用和结果；网关也不把提示保存到 OpenViking。
- **Token 用量**：客户端收到的用量是这条回复中所有模型调用之和，包括模型使用工具期间额外发起的调用。

### 工具调用提示 {#tool-call-notices}

每次调用结束后，网关在提示末尾补上结果：

| 结果 | 含义 |
| --- | --- |
| `done` | 调用完成。 |
| `failed` | 调用返回了错误。 |
| `skipped` | 网关拒绝了这次调用、没有执行，例如工具轮数或 token 预算已经用完。 |

要隐藏提示，关闭**显示工具调用**即可。和上下文配置的其他改动一样，它只影响新对话（见[修改何时生效](06-manage-in-studio.md#when-changes-take-effect)）。

## 风险和控制点 {#risks-and-controls}

> **警告**：勾选的工具直接执行，不经过客户端的权限确认，写入和删除数据的工具也一样；工具调用提示只在调用之后报告，不是确认步骤。如果用户需要逐次确认工具调用，请改用 OpenViking 插件或 MCP 服务器（见[网关还是插件](../guides/15-gateway.md#gateway-or-plugin)）。模型能做的事以该用户自己的 OpenViking 权限为上限。

勾选会修改数据的工具前，先确认能接受这一点。给聊天应用用的配置，建议至少不勾选 `forget` 和 `set_acl`。

管理员能控制的地方：

| 控制点 | Studio 中的位置 | 作用 |
| --- | --- | --- |
| **OpenViking 工具**总开关 | **上下文配置** → **OpenViking 工具** | 为使用该配置的对话开启或关闭网关工具。 |
| **工具**勾选 | **上下文配置** → **OpenViking 工具** | 决定模型能拿到哪些工具，见[选择工具](#choose-tools)。 |
| **允许 OpenViking 工具** | **上游** → **路由** | 允许上下文配置通过这个上游提供工具。关闭后，进行中的对话也随即停用。 |
| 工具轮数、总时长和 Token 预算 | **上下文配置** → **OpenViking 工具** → **高级设置** | 限制模型能连续使用工具多久，见[上限](#limits)。 |

## 选择工具 {#choose-tools}

[上下文配置](08-context-profiles.md#context-profiles)中 **OpenViking 工具**部分的**工具**清单，决定模型能拿到哪些 OpenViking 工具。新建的上下文配置默认开启 **OpenViking 工具**，用**使用推荐设置创建**新建的也一样；除了网关已知会修改数据的工具，其余工具默认都勾选：

| 默认状态 | 工具 |
| --- | --- |
| 勾选 | `find`、`search`、`grep`、`glob`、`list`、`tree`、`read`、`download`、`list_watches`、`get_acl`、`list_users`、`list_groups`、`health` |
| 不勾选（会修改数据） | `remember`、`write`、`edit`、`add_resource`、`add_skill`、`forget`、`set_acl`、`cancel_watch` |

想让模型保存记忆就勾选 `remember`，想让它导入网页或附件就勾选 `add_resource`。勾选任何会修改数据的工具之前，请先看[风险和控制点](#risks-and-controls)中的警告。

每个[上游](07-upstreams.md#upstreams)的**允许 OpenViking 工具**默认开启。两个开关都开启时，新对话获得选中的工具，名称加上 `openviking_` 前缀，例如 `openviking_find`、`openviking_read`。工具定义随对话中的每个请求发送，全部工具约占 3,500 个输入 token，所以取消用不到的工具也能省 token。

关于工具清单，还有几点需要知道：

- **清单来源**：**工具**清单来自你的 OpenViking 服务，Studio 会说明每个工具的用途；OpenViking 为工具提供了标注时，还会在旁边显示**只读**或**会修改数据**。OpenViking 把 `search` 标为**会修改数据**，但网关仍默认勾选它。
- **以后新增的工具**：OpenViking 以后新增的工具会默认可用，已有对话仍沿用开始时的工具清单。已有的上下文配置保留原来的勾选。
- **root 看到的清单为空**：OpenViking 不允许 root API 密钥调用它的工具，所以 root 查看时，网关用当前账号中某把[网关密钥](09-gateway-keys.md#gateway-keys)背后的 OpenViking 密钥读取清单。账号里还没有网关密钥时，清单可能为空，Studio 显示提示“**签发网关密钥后即可加载工具清单。**”请先签发一把密钥，或者用用户、账号管理员的身份登录后再编辑配置。在此之前保存的配置沿用默认勾选。
- **`download` 需要 shell**：`download` 返回一条一次性的 `curl` 命令，所以客户端需要有 shell 工具，而且客户端要能访问这条链接。哪些名称算 shell 工具、链接指向哪里，见[通过客户端的 shell 工具](#import-with-a-shell-tool)。

## 客户端要求 {#client-requirements}

三种模型 API 都支持 OpenViking 工具，流式和非流式请求都可以，前提是客户端每轮都回传完整对话：

| 协议 | 客户端要求 |
| --- | --- |
| Chat Completions | 回传完整的 `messages` 历史。 |
| Responses | 使用 `store: false` 和完整的 `input` 历史；下一轮回传本轮返回的全部 `output` 内容，包括 reasoning 和客户端工具调用。`item_reference` 无法还原完整历史。 |
| Anthropic Messages | 回传完整的 `messages` 历史，保留 thinking 和 signature 块。token 计数请求带相同的工具定义，但不执行工具。 |

模型在同一条回复中同时调用 OpenViking 工具和客户端工具时，网关处理 OpenViking 调用，客户端照常执行自己的工具并回传结果。Responses 支持客户端的 function 和 custom 工具，Anthropic Messages 支持客户端的 tool_use 调用。

依赖服务商端已存状态的 Responses 请求（`previous_response_id`、`conversation`、`background`），以及没有设置 `store: false` 的 Responses 请求，原样转发：既不补充召回的记忆，也不提供 OpenViking 工具。之后查询这些响应时，请求仍然发往创建它们的上游。

## 什么时候不提供工具 {#when-tools-are-not-offered}

即使[上下文配置](08-context-profiles.md#context-profiles)开启了 OpenViking 工具，个别请求发给模型时也可能不带工具。这时在**请求日志**标签页中展开该请求，详情会显示“**未提供：**”加上原因。Studio 显示原因名称，括号里是管理 API 中对应的值（`tool_skip_reason`）：

| 原因 | 说明 |
| --- | --- |
| **Responses 请求需回传完整对话，并设置 store: false**（`tools_require_full_history`） | Responses 请求需要 `store: false` 和完整 input 历史，不能使用 `item_reference`。 |
| **这个上游关闭了 OpenViking 工具**（`upstream_tools_disabled`） | 上游关闭了**允许 OpenViking 工具**。 |
| **请求要求返回多个候选结果（n > 1）**（`tools_multiple_choices`） | 请求要求返回多个候选（`n` 大于 1）。 |
| **请求要求结构化输出**（`tools_structured_output`） | 请求要求结构化输出（`response_format`、`text.format` 或 `output_config.format`）。 |
| **客户端传入了非函数类型的工具**（`tools_non_function`） | Chat Completions 客户端传入了 function 以外类型的工具。 |
| **客户端指定了必须调用的工具**（`tools_forced_choice`） | `tool_choice` 不是 `auto` 或 `none`：例如 `required`、Anthropic 的 `any` 或 `tool`、指定某个函数，或者 Responses 的 `allowed_tools` 这类对象形式。 |
| **DeepSeek 开启了思考模式，但之前回复的推理内容补不回来**（`deepseek_reasoning_history_required`） | 上游是 DeepSeek，开启了思考模式（标准 Responses 请求没有 `thinking` 字段，按开启处理），而之前的回复发到 DeepSeek 时会缺推理内容。在上游的**路由**部分开启**补全推理内容回传**（[说明及补不回的情况](07-upstreams.md#restore-reasoning)），或者发送 `"thinking": {"type": "disabled"}`。 |
| **客户端已接入 OpenViking 工具**（`tool_name_collision`） | 网关在这段对话剩下的部分不再提供自己的工具（回传的历史里模型已经调用过网关工具的请求仍保留工具）。判断条件见[客户端自己接了 OpenViking 工具时](#client-has-openviking-tools)。 |
| **这段对话首次需要工具时无法加载 OpenViking 工具清单**（`tools_unavailable`） | 无法从 OpenViking 加载工具清单，也没有之前加载成功的清单。连接恢复后开始新对话。 |
| **这段对话没有 OpenViking 工具**（`tools_not_selected_at_session_start`） | 上下文配置开启了工具，但这段对话的工具列表为空：配置里一个工具都没选，或者对话在网关升级前就已开始。开始新对话即可。 |

### 何时决定是否提供 {#when-the-decision-is-made}

对话是否带工具，由它第一个能用工具的请求决定（生成标题这类辅助请求不算），之后保持不变，所以修改上下文配置只影响新对话。唯一的例外是关闭：模型还没调用过网关工具时，客户端出现了自己的 OpenViking 工具，例如 MCP 服务器在第一轮之后才连上。

辅助请求、子 Agent 请求和检测到 OpenViking 插件的对话都不带 OpenViking 工具。这类请求如果回传了模型用过这些工具的历史，网关仍附上工具定义，让这段历史保持有效，但拒绝新的 OpenViking 调用。客户端自己的工具在每个请求里都照常可用。

### 客户端自己接了 OpenViking 工具时 {#client-has-openviking-tools}

满足以下任一条件，网关就认为客户端已经接了 OpenViking 工具（`tool_name_collision`）：

- MCP 服务名（`server_label`、`mcp_server_name`、`mcp_servers[].name`）含单词 `openviking`。
- 以 OpenViking 工具名结尾的工具名中也含这个单词，例如 `mcp__claude_ai_OpenViking__search`；连接器的 `authenticate` 工具不算。独占一行列出的延迟加载工具名也算。
- 带前缀、以 OpenViking 工具名结尾的工具（例如 `mcp__ov__read`），定义中出现 `viking://`。
- 任一请求中，客户端工具和网关工具同名，因为上游会拒绝重名工具。

误判同样会持续整段对话，开始新对话即可。

## 导入文件和技能 {#import-files-and-skills}

OpenViking 服务提供导入工具（`add_resource`、`add_skill`）、上下文配置也勾选了它们时，模型就能导入资料。按 URL 导入不需要额外条件；导入本地文件则需要客户端有 shell 工具，或者用户消息带附件。上传大小受网关的请求大小上限 `max_body_bytes` 限制（默认 32 MiB，见[副作用](05-long-conversations.md#side-effects)）。

### 通过客户端的 shell 工具 {#import-with-a-shell-tool}

shell 工具指名称类似 bash、shell、exec_command、terminal 或 run_command 的客户端工具。导入工具返回一个一次性上传链接，模型再用客户端的 shell 工具上传文件，这一步经过客户端的权限确认。技能目录会先打包成 zip。

上传链接，以及 `download` 返回的下载链接，指向 `public_url` 加 `/gateway/uploads` 或 `/gateway/downloads`。所以 `public_url` 必须是客户端能访问的地址（见[对外地址](../guides/22-gateway-operations.md#public-address)），前面的代理也要像其他路径一样转发这两个路径。没有设置 `public_url` 时，导入失败并提示 "Set gateway.public_url for client uploads and downloads"。把 `mcp_file_links` 设为 `direct` 后，网关原样透传 OpenViking 返回的链接，链接直接指向 OpenViking Server，此时 Server 的 `OPENVIKING_PUBLIC_BASE_URL`（或 `server.public_base_url`）必须是客户端能访问的地址。

### 通过消息附件 {#import-from-an-attachment}

没有 shell 工具时，模型可以导入用户消息里附带的文件，文件可以是 base64 数据，也可以是附件文本，支持 Chat Completions `file` 内容块（`file.file_data`，可以是 base64 或 `data:` URL）、Responses `input_file` 和 Anthropic `document.source` 中内嵌的 `base64`、`text` 数据。Open WebUI 通常只发送提取出的文本，这些文本作为文本文件导入。

附件中的 URL 和服务商的文件 ID 不会被抓取；要导入远程资源，请用导入工具的 URL 参数。

## 上限 {#limits}

工具轮数、总时长和 Token 预算默认都不限：和 Agent 自己的工具循环一样，模型会一直使用 OpenViking 工具直到完成。在 Studio 中，这些上限位于**上下文配置** → **OpenViking 工具** → **高级设置**，字段留空表示不限：

| 设置 | 默认值 | 达到上限时 |
| --- | --- | --- |
| **每次请求的工具轮数** | 不限 | 拒绝之后的 OpenViking 调用，模型用已有结果和客户端自己的工具继续。 |
| **单次调用超时** | 30 秒 | 这次调用向模型返回错误。 |
| **结果大小上限** | 65,536 字节 | 结果被截断。 |
| **总时长** | 不限 | 请求失败，报错 "Hidden tool request timed out"，见[失败处理](#failure-handling)。 |
| **Token 预算** | 不限 | 拒绝之后的 OpenViking 调用，模型用已有结果和客户端自己的工具继续。这不是计费上限，最终回答可能超出它。 |

API 名称和取值范围见[上下文配置的设置](13-configuration.md#context-profile-settings)。

> **注意**：非流式请求在客户端断开后仍会继续执行，所以客户端发送非流式请求时，请设置**总时长**。流式请求中，用户可以随时中断回复，客户端断开后网关也会停止。

Token 预算只计算模型使用 OpenViking 工具时新增的调用、结果和后续生成内容，客户端已有的对话、工具定义和图片不计入。网关保留客户端设置的回答长度上限（`max_tokens`、`max_completion_tokens` 或 `max_output_tokens`）。

想知道某个请求用了多少，在**请求日志**标签页中展开它：**OpenViking 工具**一项会显示轮数、额外模型调用次数和增加的 Token 数；预算用完、后续调用被拒绝时，还会显示**工具 Token 预算已用完**。

这几项默认值改为“不限”之前保存的上下文配置仍保留原来的值（5 轮、120 秒、100,000 token），清空字段即可取消限制。

## 失败处理 {#failure-handling}

单次 OpenViking 调用失败时，模型收到错误结果，可以据此继续回答。只有以下情况会让整个回答失败：

- 服务商拒绝了后续的模型请求。
- 模型调用了这段对话里没有提供的 OpenViking 工具，或者 OpenViking 调用被拒绝后仍坚持调用。
- **Hidden tool request timed out**：OpenViking 工具的往返超过了上下文配置中工具的**总时长**；没有设置总时长时，是往返中某次模型请求超过了 `upstream_timeout_seconds`。

这时客户端收到错误，请求日志显示 **OpenViking 工具调用失败**（`hidden_tool_loop_failed`）。错误的形式取决于请求类型：

| 请求 | 客户端收到什么 |
| --- | --- |
| 流式 | 流以错误事件结束：Responses 是 `response.failed`，Chat Completions 和 Anthropic Messages 是类型为 `gateway_tool_error` 的 `error`。此时 HTTP 状态码已经是 200。 |
| 非流式，服务商拒绝了后续请求 | 原样返回服务商的错误内容和状态码。 |
| 非流式，超时 | 504（`gateway_http_504`）。 |
| 非流式，其他失败 | 502，响应体为 `{"reason": "gateway_http_502", "detail": "..."}`。 |

另外两种情况：

- **某个上游反复失败**：关闭它的**允许 OpenViking 工具**。用过这些工具的 Claude 对话可能随之出现**工具历史无法继续使用**（`hidden_tool_history_unavailable`）：之前的思考内容无法继续使用，网关已将其去掉。可以按请求详情中的停用原因恢复设置，或者开始新对话。
- **重试**：重试的工具调用复用第一次调用的结果，中断的写入操作不会自动重做。

## 下一步 {#next-steps}

- [上下文配置](08-context-profiles.md#context-profiles)：为每类客户端一起配置召回、保存和工具。
- [文件导入或下载失败](12-troubleshooting.md#file-imports-or-downloads-fail)：排查上传和下载链接的问题。
- [上下文配置的设置](13-configuration.md#context-profile-settings)：各项工具设置的 API 名称和取值范围。
