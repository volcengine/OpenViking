---
description: 把任何使用 API Key 的模型客户端指向 OpenViking 网关，让它用上 OpenViking 记忆。
---

# OpenViking 网关

OpenViking 网关是一个模型 API 代理：客户端只要能修改 Base URL，接上它就能用上 OpenViking 记忆。接入只改两处：Base URL 换成网关地址，模型服务商的 API Key 换成网关密钥。不用装插件，也不用改代码或提示词。

网关在转发请求时完成三件事：

| 能力 | 网关做什么 |
| --- | --- |
| 自动召回 | 用户每发一条新消息，网关先检索 OpenViking，把相关记忆附加到这条消息上，再交给模型。 |
| 主动使用记忆 | 模型可以调用 OpenViking 工具检索、读取、写入和导入资料。工具由网关执行，客户端不用声明或实现任何工具。 |
| 沉淀与压缩 | 网关把对话保存回 OpenViking，由 OpenViking 从中提取新记忆。对话接近模型的上下文窗口时，网关让同一个模型写摘要，替换较早的内容。 |

想先直观了解网关的工作原理，可以看官网的动画演示：[openviking.ai/gateway](https://openviking.ai/gateway)。想马上接入客户端，直接看[把客户端接入 OpenViking 网关](../gateway/03-connect-clients.md)；其他任务或 Studio 标签页对应哪一页，见[下一步去哪](#next-steps)。

> **注意**：OpenViking 网关目前处于 Beta 阶段。它和 VikingBot Gateway（`vikingbot gateway`）是两个组件，后者负责远程访问和聊天平台接入。

## 适合谁 {#who-it-is-for}

- **适合**：装不了插件或 MCP 的客户端，例如聊天应用、SDK 脚本和低代码平台；以及想集中管理模型服务商、密钥和记忆设置的团队。
- **不适用**：直接用订阅账号登录的客户端（可以改用[订阅反代上游](../gateway/07-upstreams.md#custom-upstreams)，Codex 就可以这样使用 ChatGPT 订阅，见[使用 ChatGPT 订阅](../gateway/03-connect-clients.md#codex-chatgpt-subscription)），以及 Cursor、Trae 这类从厂商服务器发起模型调用的客户端。

网关支持三种模型 API：Anthropic Messages、OpenAI Chat Completions 和 OpenAI Responses（要求每个请求带完整历史，见[客户端要求](../gateway/04-openviking-tools.md#client-requirements)）。请求转发到你配置的模型服务地址，称为**上游**。请求用哪种 API 发来，网关就用同一种 API 转发，不做协议转换。

## 关键术语 {#key-terms}

网关文档的各个页面都按下表的含义使用这些术语。同一 OpenViking 账号的用户共用上游和上下文配置；网关密钥和请求日志归各个用户自己。

| 术语 | 含义 | 详见 |
| --- | --- | --- |
| 上游 | 网关把请求转发过去的模型服务地址，连同它的 API Key；每个上游只使用三种 API 中的一种。 | [上游](../gateway/07-upstreams.md#upstreams) |
| 上下文配置 | 一组有名字的记忆设置，例如召回预算、是否保存对话、是否开放 OpenViking 工具；每个网关密钥使用一份。 | [上下文配置](../gateway/08-context-profiles.md#context-profiles) |
| 网关密钥 | 客户端用来代替服务商 API Key 的密钥（`ovgw_…`），绑定一位 OpenViking 用户、一份上下文配置和一组上游。 | [网关密钥](../gateway/09-gateway-keys.md#gateway-keys) |
| OpenViking API 密钥 | 你在 OpenViking 的用户密钥：用它登录 Studio，它也决定以哪个账号、哪个用户的身份读写记忆。 | [密钥和凭证](22-gateway-operations.md#keys-and-credentials) |
| 网关管理密钥 | 可选的密钥（`ovgm_…`）；设置后，只有带上它的请求才能修改上游和上下文配置。谁能修改什么，见[权限矩阵](../api/25-gateway.md#credentials-and-account-scope)。 | [网关管理密钥](22-gateway-operations.md#gateway-management-key) |
| 对话 | 网关认定属于同一次聊天的一组请求；一段对话沿用开始时的上下文配置和上游。它的轮次保存到一个 OpenViking 会话，某些情况下改存到新会话，例如客户端改写历史时（见[保存状态](../gateway/10-overview-and-requests.md#saving-status)）。 | [网关如何识别对话](../gateway/03-connect-clients.md#how-conversations-are-recognized) |
| 召回 | 从 OpenViking 搜出和当前消息相关的记忆，附加到这条消息上。 | [模型看到什么](../gateway/01-how-it-works.md#what-the-model-sees) |
| 保存 | 把已完成的轮次写入 OpenViking 会话（晚一轮进行），供之后的提交从中提取新记忆。 | [对话什么时候保存](../gateway/01-how-it-works.md#when-conversations-are-saved) |
| 提交 | 告诉 OpenViking 会话里已保存的轮次可以处理了：OpenViking 归档较早的轮次（会话中只留最新的消息），并在后台从中提取记忆。已保存的轮次要等提交之后才会变成记忆。 | [对话什么时候保存](../gateway/01-how-it-works.md#when-conversations-are-saved) |
| 压缩 | 对话接近模型的上下文窗口时，让同一个模型写一份摘要，替换截断点之前的全部内容。 | [网关中的长对话](../gateway/05-long-conversations.md#long-conversations) |
| 提示词缓存 | 服务商的计费机制：请求开头和上一次请求相同的部分按缓存价计费（通常只有原价的一小部分），从第一个不同的字节起按原价计费。 | [记忆原样放回与提示词缓存](../gateway/01-how-it-works.md#replay) |
| 本地 dev 模式 | 以 dev 模式运行的 OpenViking 服务：绑定 `127.0.0.1`、未配置 `root_api_key`，打开 Studio 不需要 API 密钥，所有操作都以 `default` 用户身份进行。 | [本地服务](../gateway/02-quickstart.md#local-server-no-api-key) |
| OpenViking 工具 | 你的 OpenViking 服务提供的工具（名为 `openviking_…`），由网关提供给模型，并在模型回答时由网关自己执行。 | [OpenViking 工具](../gateway/04-openviking-tools.md#agentic-memory-for-any-client) |

## 一张图看懂架构 {#architecture-at-a-glance}

![OpenViking 网关的两种运行形态。嵌入：客户端访问 OpenViking Server 的端口，Server 把 /gateway 下的所有路径（模型 API、上传和网关管理）交给同一进程里的网关，网关通过本机回环 HTTP、用用户本人的 OpenViking 密钥检索和保存记忆。独立：openviking-gateway 用自己的端口提供同样的 /gateway 路径和只含网关页面的 Studio，通过 HTTP(S) 调用已有的 OpenViking 服务。两种形态下，网关都用同一种 API 把请求转发给上游模型服务商；会话和记忆的正本在 OpenViking，网关自身的数据默认存在本机两个 SQLite 文件中，也可以存在共享的 Redis 中，开启 OpenViking 加密时加密存储](../../images/gateway/architecture.zh.svg)

- **运行在哪**：开启后嵌入 OpenViking Server，和 Server 共用端口；也可以用 `openviking-gateway` 独立运行，连接已有的 OpenViking 服务（开源版或商业版）。两种方式下网关的所有 URL 都在 `/gateway` 下（例如 `/gateway/v1`），客户端路径相同，见[选择部署方式](22-gateway-operations.md#deployment-options)。
- **和 OpenViking 的关系**：网关只调用 OpenViking 的公开接口，向量化和记忆提取都在 OpenViking 中完成。
- **数据在哪**：会话和记忆的正本在 OpenViking。网关默认使用两个 SQLite 文件，多副本部署可以改用共享 Redis。开启 OpenViking 加密后，两种后端中存储的值都会加密。见[安全与数据](../gateway/11-security-and-data.md)。
- **出故障时**：OpenViking 不可用时，对话照常进行，只是不带记忆，保存会自动重试。网关停止服务时，经过它的模型调用全部失败。

## 能得到什么，代价是什么 {#what-you-get-and-what-it-costs}

接入后你会得到：

- 每条新消息都补上相关记忆；
- 新对话开头带上你的用户画像；
- 对话保存回 OpenViking，并提取成新记忆；
- 长对话自动压缩；
- 开启 OpenViking 工具后，模型还能主动检索、读取和写入记忆（见[让任意客户端拥有 Agentic 记忆](../gateway/04-openviking-tools.md#agentic-memory-for-any-client)）。

代价有三项：

- 每条新消息的第一次模型调用最多多等 2 秒；
- 补充的记忆按输入 token 计费，后续请求里大部分按缓存价计费；
- 触发压缩的那一轮多一次模型调用。

每一项在一轮对话里具体怎么发生，见[OpenViking 网关的工作原理](../gateway/01-how-it-works.md)。

## 网关还是插件 {#gateway-or-plugin}

OpenViking 也能以插件形式接入 Agent，例如 Claude Code、Codex、Cursor、TRAE、OpenCode、pi、DSH、OpenClaw 和 Hermes（见 [Agent 集成概览](../agent-integrations/01-overview.md)）。两者读写的是同一份 OpenViking 记忆，区别在于运行位置：网关位于模型调用的链路上，改的是模型收到的请求；插件运行在某个 Agent 内部，靠这个 Agent 的事件工作。没有哪一种总是更好，下面的差别大多由这一点决定，有些还因插件而异。

### 逐项对比 {#gateway-and-plugin-compared}

| | OpenViking 网关 | Agent 插件 |
| --- | --- | --- |
| 运行位置 | 客户端和模型服务商之间的一个共享服务，只看得到模型请求。 | 通过 hook 或扩展接口运行在每个 Agent 内部，看得到 Agent 的会话、事件和工作区。 |
| 适用客户端 | 能设置 Base URL 和 API Key、使用 Anthropic Messages、Chat Completions 或 Responses 的客户端：聊天应用、SDK 脚本、低代码平台和编程 Agent。Cursor 和 TRAE 不行，它们的模型调用从厂商服务器发出。 | 只有带 OpenViking 插件的 Agent。其他 MCP 客户端可以接入 OpenViking 工具，但没有自动召回和保存。 |
| 模型登录方式 | 需要服务商的 API Key，由网关保管，或[由客户端各自发送](../gateway/03-connect-clients.md#bring-your-own-provider-key)。客户端不能用订阅账号登录，可以改用[订阅反代上游](../gateway/07-upstreams.md#custom-upstreams)。Codex 可以通过 [CLIProxyAPI](../gateway/03-connect-clients.md#codex-chatgpt-subscription) 使用 ChatGPT 订阅；其他订阅服务请自行确认其使用条款。 | 不经手模型调用，Agent 原来怎么登录就怎么登录，订阅也可以。 |
| 召回 | 每条新的用户消息召回一次，结果附加在这条消息末尾；检索默认最多等 2 秒。默认检索记忆、资源和技能。 | 在 Agent 的提问事件里进行。多数插件会带上会话 ID，让服务端结合对话扩展查询；自动召回不含资源。 |
| 保存 | 晚一轮保存已完成的轮次，连同客户端自己的工具调用和结果。子 Agent 请求和杂项请求不保存。 | 跟随 Agent 的事件，通常在每轮结束时。子 Agent 各不相同：Claude Code、OpenCode 和 DSH 存成单独的会话，Codex 并入主会话，Hermes 不保存。 |
| OpenViking 工具 | 由网关在回复过程中执行，不支持工具或 MCP 的客户端也能用。默认只开放只读工具。调用[不经过客户端的权限确认](../gateway/04-openviking-tools.md#risks-and-controls)，每次调用显示一行提示。 | 由 Agent 当作自己的工具执行，调用和结果出现在对话记录里，遵循 Agent 的工具权限设置。 |
| 提示词缓存 | 在后续请求里把每个记忆块逐字节原样放回，缓存前缀保持不变。**概览**页显示缓存命中率。 | 因插件而异。基于 hook 的插件把记忆块交给 Agent，由 Agent 保存在自己的历史里；pi 和 OpenCode v2 也会让之前的召回保持不变；OpenClaw 每轮在系统提示词里重建召回内容。 |
| 长对话 | 接近模型上下文窗口时由网关压缩，任何客户端都适用，包括从不压缩的应用。[实验性的 Agent 自管上下文窗口](../gateway/05-long-conversations.md#experimental-agent-managed-context-windows)让模型自己决定何时开启新窗口。 | 由 Agent 自己压缩，多数插件会先提交已捕获的内容。pi 和 OpenClaw 可以用 OpenViking 的摘要替换 Agent 的摘要。 |
| 按项目区分记忆 | 看不到工作目录。要按项目隔离，就给每个项目一个绑定不同 OpenViking 用户的网关密钥。 | 基于共享插件代码的插件会自动给记忆标上当前代码仓库。 |
| 可见性 | 补充的记忆在客户端里看不到，除非上下文配置开启了[召回摘要](../gateway/08-context-profiles.md#recall-summary)。Studio 的请求日志记录条数和保存状态，不记录内容。 | Claude Code 的状态栏和 `/openviking-memory:ov` 命令显示召回条数和补充内容的来源；pi 的状态栏显示连接状态。其他插件没有自己的状态显示。 |
| 部署与运维 | 一个服务，嵌入 OpenViking Server 或独立运行。用户只改 Base URL 和密钥；上游、上下文配置和密钥都在 Studio 里管理。 | 每台机器分别安装和升级，各自配置 OpenViking 凭证。 |
| 团队使用 | 管理员给每人签发一个网关密钥，可以单独吊销。网关保管服务商 API Key 时，用户看不到它。 | 每人用自己的 OpenViking API 密钥安装插件。行为设置可以写进代码仓库的 `.openviking/config.json`，按仓库共享。 |
| 出故障时 | OpenViking 不可用时，请求照常发出，只是不带记忆，保存会自动重试。网关本身停止服务时（嵌入形态即 OpenViking Server 停止时），经过它的模型调用全部失败。服务商 API Key 和尚未保存的对话文本集中存放在网关，每次模型调用也多经过一跳。 | OpenViking 不可用时，Agent 等到插件的召回超时（多数为 15 秒，Hermes 为 4 秒）后不带记忆继续；多数插件会把写入失败的内容排队存到磁盘。模型调用从不依赖 OpenViking。 |

各插件的细节见 [Agent 集成能力](../agent-integrations/16-capability-reference.md)，网关的细节见 [OpenViking 网关的工作原理](../gateway/01-how-it-works.md)。

### 怎么选 {#which-to-choose}

以下情况选网关：

- 客户端没有 OpenViking 插件：聊天应用、SDK 脚本、低代码平台，或者你自己写的、调用模型 API 的应用；
- 多人或多个客户端要共用一套配置：服务商、记忆设置和每人的密钥都在 Studio 里管理，用户的机器上什么都不用装；
- 客户端自己没有记忆工具或压缩功能，网关可以把两者都补上；
- 你的客户端本来就用服务商 API Key，或者模型流量本来就经过一个集中代理。

以下情况选插件：

- 你用 Claude 订阅账号登录 Claude Code；
- 你用 Cursor 或 TRAE，只有插件能接入；
- 你希望记忆不用配置就按代码仓库标记，或者希望每次 OpenViking 工具调用都在 Agent 里展示并经过确认；
- 你希望保存跟随 Agent 的事件，例如子 Agent 的会话、Agent 压缩前的保存、退出时的提交；
- 你不想在模型调用链路上多加一个服务。

用 ChatGPT 订阅的 Codex 两种方式都行：装它的插件，或者经网关接 [CLIProxyAPI 上游](../gateway/03-connect-clients.md#codex-chatgpt-subscription)。

两者也可以混用：编程 Agent 用各自的插件，其他客户端都走网关。同一个客户端两者都装时，见下一节。

> **抢先试用新功能**：网关掌控整个模型请求，因此能做到插件通过 Agent 扩展接口做不到的功能。[Agent 自管上下文窗口](../gateway/05-long-conversations.md#experimental-agent-managed-context-windows)是第一个，由模型自己决定何时开启新的上下文窗口；之后还会有更多这类功能加入网关。想抢先试用，就把客户端接入网关。

### 两者同时使用时 {#when-both-are-in-use}

请求中出现 OpenViking 插件的迹象时，网关会让路：不再为这段[对话](#key-terms)补充记忆、保存对话或提供 OpenViking 工具，只做转发，避免同一份内容被补充两次、保存两次。判断依据有两类：

- 插件插入的记忆块：`<openviking-context>`（基于共享插件代码的插件，例如 Claude Code、Codex、OpenCode、pi 和 DSH）、`<relevant-memories>`（OpenClaw）、`<memory-context>`（Hermes）或 `<relevant-memory>`；
- `X-OpenViking-Plugin` 请求头。插件和应用的作者可以发送它，明确告诉网关不要补充记忆。

这个判断对整段对话有效，其他对话不受影响；想重新用上网关记忆，需要不带插件开始新对话。Studio 的请求日志会把这些请求标为**检测到 OpenViking 插件**。实际效果是记忆由插件负责，网关只做转发；客户端仍然需要网关密钥，以及配有服务商 API Key 的上游。

只接了 OpenViking MCP 工具、没装插件的客户端不算：网关照常补充记忆和保存对话，只是不再提供自己的 OpenViking 工具，免得模型看到两套同名工具。这时在**请求日志**标签页的请求详情里，**OpenViking 工具**一项显示**未提供：客户端已接入 OpenViking 工具**，见[什么时候不提供工具](../gateway/04-openviking-tools.md#when-tools-are-not-offered)。

## 下一步去哪 {#next-steps}

### 按任务查找 {#by-task}

| 我想… | 去看 |
| --- | --- |
| 弄清一轮对话里发生什么、模型看到什么、为什么这样设计 | [OpenViking 网关的工作原理](../gateway/01-how-it-works.md) |
| 从拿到网关密钥到确认记忆生效，完整走一遍 | [快速上手](../gateway/02-quickstart.md) |
| 接入 Claude Code、Codex、聊天应用或 SDK | [把客户端接入 OpenViking 网关](../gateway/03-connect-clients.md) |
| 让模型使用记忆工具，或处理长对话 | [OpenViking 工具](../gateway/04-openviking-tools.md)和[网关中的长对话](../gateway/05-long-conversations.md) |
| 部署、对外暴露、升级和监控网关 | [部署与运维](22-gateway-operations.md) |
| 审查存了哪些数据、谁能看到、怎么删除 | [安全与数据](../gateway/11-security-and-data.md) |
| 排查报错、警告或记忆没有生效 | [排障](../gateway/12-troubleshooting.md) |
| 查某项设置的名称、默认值或取值范围 | [配置参考](../gateway/13-configuration.md) |
| 通过 HTTP 管理网关 | [管理 API](../api/25-gateway.md) |

### 按 Studio 标签页查找 {#by-studio-tab}

在 Studio 侧边栏打开 **OpenViking 网关**，每个标签页都有对应的文档：

| 标签页 | 用途 | 文档 |
| --- | --- | --- |
| **概览** | 一眼看清请求量、提示词缓存、记忆召回和 OpenViking 的运行状态 | [概览](../gateway/10-overview-and-requests.md#overview) |
| **上游** | 网关转发请求的模型服务地址 | [上游](../gateway/07-upstreams.md) |
| **上下文配置** | 对话如何使用记忆和工具 | [上下文配置](../gateway/08-context-profiles.md) |
| **密钥** | 签发和吊销网关密钥 | [网关密钥](../gateway/09-gateway-keys.md) |
| **请求日志** | 每次模型请求，以及其中记忆的处理情况 | [请求日志](../gateway/10-overview-and-requests.md#requests) |
| **接入** | 网关地址和每种客户端的具体设置 | [把客户端接入 OpenViking 网关](../gateway/03-connect-clients.md) |

如果**上游**或**上下文配置**是只读的，或者 Studio 要求你**输入网关管理密钥**，见[谁能修改什么](../gateway/06-manage-in-studio.md#who-can-change-what)。

### 相关文档 {#related-docs}

- [认证](04-authentication.md)：创建账号、用户和他们的密钥。
- [公网访问与反向代理](12-public-access.md)：为 OpenViking 配置 HTTPS。
- [Agent 集成概览](../agent-integrations/01-overview.md)：为支持插件的 Agent 安装插件。
- [MCP 集成](06-mcp-integration.md)：让 MCP 客户端直接使用 OpenViking 工具。
