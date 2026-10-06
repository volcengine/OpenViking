---
description: 把任何使用 API Key 的模型客户端指向上下文网关，让它用上 OpenViking 记忆。
---

# 上下文网关

**任何**能修改 Base URL 的模型客户端，接上上下文网关就能用上 OpenViking 记忆，还能让模型主动使用记忆。客户端只改两处：Base URL 改成网关地址，模型服务商的 API Key 换成网关密钥。不用安装插件，也不用改代码或提示词。

开启 OpenViking 工具后，模型能在一条回复里自己检索记忆、读取原文、记下新内容、导入资料。这些工具由网关代为执行，客户端不需要声明或实现任何工具，所以聊天应用、SDK 脚本、低代码平台这类**任意**客户端，也能像 Agent 一样操作记忆。

| 能力 | 网关做什么 |
| --- | --- |
| 自动召回 | 用户每发一条新消息，网关先检索 OpenViking，把相关记忆附加到这条消息上再交给模型。之后的请求里，网关把这些内容原样放回原位，服务商的提示缓存不会因此失效。 |
| 主动使用记忆 | 模型调用 OpenViking 工具来检索、读取、写入和导入，网关执行调用后把结果接回给模型，客户端只收到一条连贯的回复。新建的上下文配置默认开启，只提供只读工具。 |
| 沉淀与压缩 | 网关把对话保存回 OpenViking，由 OpenViking 从中提取新的记忆。对话接近模型的上下文窗口时，网关让同一个模型写一份摘要，替换较早的内容。 |

**适合谁**：装不了插件或 MCP 的客户端，例如聊天应用、SDK 脚本和低代码平台；以及想集中管理模型服务商、密钥和记忆设置的团队。**不适用**：直接用订阅账号登录的客户端（可以改用[订阅反代上游](#自定义上游)），以及 Cursor、Trae 这类从厂商服务器发起模型调用的客户端。

网关支持三种常用的模型 API：Anthropic Messages、OpenAI Chat Completions 和 OpenAI Responses（要求每个请求都带完整历史）。它把每个请求转发给你配置的模型服务商，这里称为**上游**；请求用哪种 API 发来，就用同一种 API 转发，网关不做转换。

> **注意**：上下文网关是一个单独运行的进程 `openviking-context-gateway`，不在 OpenViking Server 进程里。名字相近的 `vikingbot gateway` 是 VikingBot 的长期运行入口，负责远程访问和接入聊天平台；两者是 OpenViking 里用途不同的组件。

上下文网关目前处于 Beta 阶段。本页介绍网关的工作方式和客户端接入方法。为团队部署网关和日常运维，见[上下文网关部署与运维](22-context-gateway-operations.md)。

## 自定义上游

上游可以自定义：任何兼容这三种 API 之一的服务都能添加为上游，不一定是模型服务商本身。常见的两种用法：

- **计费、配额和负载均衡。** 网关不负责这些。需要时在网关后面接一层 LiteLLM、new-api 这类网关，把它添加为上游。
- **使用订阅额度。** 客户端不能直接用订阅账号登录网关，但可以把 [CLIProxyAPI](https://github.com/router-for-me/CLIProxyAPI) 这类反向代理添加为上游。它把 ChatGPT（Codex）、Claude 等订阅账号包装成 API，例如 Codex 就可以经网关用上 ChatGPT 订阅额度。OpenAI Codex 负责人 Tibo 曾[公开介绍](https://x.com/thsottiaux/status/2076119366647894371)过用 CLIProxyAPI 接入 Codex 订阅的做法。**是否符合服务商的使用条款，请自行确认。**

添加方法见[上游](22-context-gateway-operations.md#上游)，服务商选*通用*。

## 一张图看懂架构

![上下文网关架构：客户端只改 Base URL 和 API Key，经反向代理把模型请求发给网关；网关用用户自己的 OpenViking 密钥搜索 OpenViking、保存对话，再用同一种 API 把请求转发给上游模型服务商；会话和记忆的正本在 OpenViking，网关本机只有两个加密的 SQLite 文件](../../images/context-gateway/architecture.zh.svg)

- **运行在哪**：网关是独立的服务，可以和 OpenViking 装在同一台机器、放在同一个 Pod，也可以分开部署（见[部署方式](22-context-gateway-operations.md#部署方式)）。共享部署时网关端口不对外开放，由反向代理把模型 API 和工具上传路径转发给网关，其余路径转发给 OpenViking；只在本机试用时可以省掉反向代理。
- **和 OpenViking 的关系**：网关只调用 OpenViking 的公开接口，向量化和记忆提取都在 OpenViking 里完成。
- **数据在哪**：会话和记忆的正本在 OpenViking。网关本机只有两个加密的 SQLite 文件，一个存上游、密钥和配置，一个存对话状态；对话闲置 30 天后，网关删除它的状态。删除和隔离方式见[安全与数据](22-context-gateway-operations.md#安全与数据)。
- **出故障时**：OpenViking 不可用时对话照常进行，只是不带记忆，保存对话会自动重试。网关停止服务时，经过它的模型调用全部失败。
- **谁来管**：账号管理员在 Studio 里给每位用户签发网关密钥。每个密钥绑定一位 OpenViking 用户、一份**上下文配置**（召回预算、是否保存对话、是否开放 OpenViking 工具等记忆设置）和一组**上游**（请求转发到的模型服务地址和 API Key）。

## 接入后，对话会发生什么

先说明两个概念。**召回**是指从 OpenViking 搜出和这条消息相关的记忆，附加到消息上。**提示缓存**是服务商的计费机制：请求开头和上一次相同的部分按缓存价计费，从第一个不同的字节起按原价重新计算。缓存价通常只有原价的一小部分，具体比例见各服务商的定价说明。

| 你要做的 | 你会得到 | 额外开销 | 需要留意 |
| --- | --- | --- | --- |
| 把 Base URL 改成网关地址，API Key 换成网关密钥。Claude Code 还要设置 `CLAUDE_CODE_GATEWAY_HINT_HEADERS=1`，给子 Agent 和后台请求加上标记，避免为它们召回和保存。 | 每条新消息都补充相关记忆，对话开头还提供用户画像；对话保存回 OpenViking 并提取成新记忆；长对话自动压缩；可选：模型主动检索、读取和写入 OpenViking（见[下一节](#让任意客户端拥有-agentic-记忆)）。 | 每条新消息的第一次模型调用最多多等 2 秒；补充的记忆按输入 token 计费，之后的请求里大部分按缓存价计费；触发压缩的那一轮多一次模型调用。 | 补充的记忆在客户端里看不到（服务商看得到），Studio 的请求日志也只记条数和耗时；没有设置上下文窗口时，网关按 1,000,000 token 的窗口决定何时压缩，使用窗口更小的模型时要请管理员填上实际窗口，否则长对话会因过长被服务商拒绝；客户端装了 OpenViking 插件或名为 `openviking` 的 MCP 服务器时，网关不为这段对话召回、保存，也不提供 OpenViking 工具。 |

![一轮对话里发生了什么：网关识别对话和请求类型，把之前补充的记忆原样放回，只为新消息召回，再用同一种 API 转发给上游，并把这一轮放进待保存队列；下方说明原样放回如何让提示缓存持续命中，以及跨轮的保存和长对话压缩](../../images/context-gateway/one-turn.zh.svg)

网关会自动认出同一段对话，只有用户的新消息触发召回，工具步骤和生成标题这类辅助请求不召回。下面依次说明各个环节。

**模型看到什么。** 模型 API 是无状态的，客户端每次请求都要把整段对话重新发一遍。当请求以一条新的用户消息结尾时，网关用这条消息的文本搜索 OpenViking，把结果附加到这条消息的末尾：

```text
发布日期最后定在哪天？

<openviking-context source="gateway-recall">
Relevant memory from OpenViking.
<memory uri="viking://user/alice/memories/events/release-planning.md" type="memory" detail="abstract">
团队把 2.0 版本的发布推迟到了 11 月第一周。
</memory>
</openviking-context>
```

客户端看不到这段内容：它不出现在回复里，客户端自己保存的历史也保持原样。不过服务商返回的 token 用量包含这部分，网关把用量原样传回客户端。每条用户消息只搜索一次；工具步骤、子 Agent 调用以及生成标题之类的辅助请求，沿用已经补充的内容。在同一个上下文窗口内，也就是对话被压缩之前，同一条记忆只补充一次；单条消息和单个上下文窗口的预算也限制了补充的总量。

**开头的内容。** 新对话开始时会带上你的 OpenViking 用户画像。启用读取工具后，还会提供记忆和技能目录，让模型能在自动检索之外继续查找资料。这些内容使用独立的 4,000 token 预算，技能目录最多占四分之一。你可以在上下文配置中关闭画像或调整预算。某部分读取失败时会省略该部分，对话照常继续。

开启召回或 OpenViking 工具时，开头还有一段说明，告诉模型这些内容来自哪里、哪些功能已启用。例如，开启召回、保存对话和三个 OpenViking 工具时：

```text
<openviking-context source="gateway-session-start">
The OpenViking Context Gateway, a proxy between the client and the model, added this block. The user did not write it, and the client does not show it.
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

即使第一条消息没有搜到相关记忆，画像和目录仍可出现。开头的说明、画像和目录都不占召回预算。客户端压缩历史后，新的开头会再次提供这些内容。

**原样放回。** 客户端保存的历史里没有网关补充的内容，所以网关自己记下每个记忆块，在这段对话后续的每个请求里，把它放回最初附加的那条消息上，逐字节保持一致。服务商按前缀缓存提示词，Claude 的思考签名又覆盖了之前的对话，所以历史必须保持一致：这样服务商的缓存才能持续命中，Claude 也不会拒绝这段对话。记忆放在最新消息的末尾而不是系统提示词里，也是同样的原因：系统提示词改动一个字，之后的整份缓存都会失效。

**对话什么时候保存。** 网关把已完成的轮次保存到密钥所属用户的 OpenViking 会话里，这些会话名为 `context-gateway-…`。下一条用户消息到达时，网关才保存上一轮，因为这时才能确认客户端保留了它，所以重新生成或被放弃的回答不会被保存。对话的最后一轮在停顿 10 分钟后保存，随后网关提交会话，OpenViking 在后台从中提取记忆。会话里待提交的内容累计到 20,000 token 时，网关也会提交一次。保存前，网关会去掉自己添加的内容，以及 `<system-reminder>` 这类客户端噪声。子 Agent 请求、辅助请求和 token 计数请求从不保存。

**长对话。** 对话用到模型上下文窗口的 90%（默认值）时，网关会压缩它：由同一个模型为到目前为止的对话写一份有长度上限的摘要，此后这份摘要取代压缩位置之前的全部内容，不保留任何原文；客户端界面里的历史保持不变。如果开启了保存对话，并且模型能用 OpenViking 的 grep 和 read 工具，摘要后面会说明如何在已保存的对话里查找细节。摘要由模型自己生成，不再使用 OpenViking 的 Working Memory 摘要，网关新建的 OpenViking 会话也都关闭了 Working Memory。上游和上下文配置都没有设置模型的上下文窗口时，网关按 1,000,000 token 计算，所以窗口更小的模型需要设置窗口。每次压缩会多一次模型请求，服务商缓存也会失效一次。详见[长对话](22-context-gateway-operations.md#长对话)。

以上数字都来自密钥使用的**上下文配置**。你可以在其中调整预算和时间，也可以分别关闭每项功能。

## 让任意客户端拥有 Agentic 记忆

自动召回是网关替模型猜它需要什么；开启 OpenViking 工具后，由模型自己决定查什么、读什么、记什么。工具由网关执行，客户端不用声明或实现任何工具，请求里一个工具都没有也能用。

![开启 OpenViking 工具后的一条回复：网关在客户端的请求上附加召回的记忆和 OpenViking 工具定义，模型调用 OpenViking 工具时由网关用该用户的 OpenViking 密钥执行，再把结果接回请求继续询问模型，直到模型给出最终回答；客户端收到的是一条带提示行的连贯回复，工具往返在下一轮被原样放回](../../images/context-gateway/tool-loop.zh.svg)

- **一条回复里完成多步操作**：网关拦下模型发出的 OpenViking 工具调用，用该用户的 OpenViking 密钥执行，把结果接回后再问模型，直到模型不再调用。客户端只收到一条连贯的回复，流式和非流式都支持。
- **客户端自己的工具照常可用**：同一条回复里，模型可以同时调用 OpenViking 工具和客户端工具（例如 Bash）。客户端工具的调用原样交给客户端执行，照常经过客户端的权限确认。
- **下一轮还记得**：模型发出的工具调用和网关拿回的结果不在客户端的历史里，网关把这些工具往返存下来，下一轮原样放回。模型记得自己查到过什么，提示缓存也不会因此失效。

### 不同客户端得到什么

| 客户端 | 不经过网关时 | 经过网关并开启 OpenViking 工具后 |
| --- | --- | --- |
| 聊天应用，例如 Cherry Studio、Open WebUI | 要用记忆得自己配 MCP 或写工具，普通对话只收发文本。 | 模型自己检索记忆、按 URI 读取原文；勾选 `remember` 后还能记下用户要求记住的事。勾选 `add_resource` 后，用户附带的文件可以导入 OpenViking：客户端发送原文件就导入原文件，Open WebUI 通常只发送提取出的文本。即使是不带任何工具的纯聊天模式，模型也能调用 OpenViking 工具。 |
| SDK 脚本、低代码平台 | 一问一答，要多步操作就得自己写循环。 | 一次请求里完成“检索 → 读取 → grep 精确匹配 → 作答”，代码不用改。 |
| Claude Code、Codex 等编程 Agent（未装 OpenViking 插件或 MCP） | 有自己的工具，但没有 OpenViking。 | OpenViking 工具和 Bash 等客户端工具在同一条回复里混用，不用装插件或配 MCP。需要逐次确认工具调用时，仍建议使用插件或 MCP。 |

### 模型能用哪些工具

工具直接来自你的 OpenViking 服务提供的 MCP 工具清单，名称前加 `openviking_` 前缀，OpenViking 新增的工具也会自动可用。常用的几类：

| 用途 | 工具 | 模型拿来做什么 |
| --- | --- | --- |
| 检索 | `find`、`search`、`grep`、`glob` | 语义检索记忆和资料；按正则或文件名精确查找。 |
| 浏览和读取 | `list`、`tree`、`read` | 按 URI 展开召回条目的原文，浏览记忆和技能目录。 |
| 写入 | `remember`、`write`、`edit` | 存一条长期记忆，写入或局部修改记忆文件。 |
| 导入 | `add_resource`、`add_skill` | 按 URL 导入网页或代码仓库、导入对话附件、用一段文本新建技能，都不需要客户端有 shell。 |
| 删除和权限 | `forget`、`set_acl` 等 | 永久删除记忆，修改共享资源的访问权限。 |

### 怎么开启、有什么上限

- **默认只提供只读工具。** 新建的上下文配置默认打开 **OpenViking 工具**，“使用推荐设置创建”也一样。默认勾选的是只读工具：`find`、`search`、`grep`、`glob`、`list`、`tree`、`read`、`list_watches`、`get_acl`、`list_users`、`list_groups` 和 `health`。会修改数据的工具默认不勾选：`remember`、`write`、`edit`、`add_resource`、`add_skill`、`forget`、`set_acl` 和 `cancel_watch`。想让模型保存记忆，就勾选 `remember`；想让它导入网页或附件，就勾选 `add_resource`。OpenViking 以后新增的工具会自动勾选。已有的上下文配置保留原来的设置。全部工具定义约占 3,500 个输入 token，会随对话中的每个请求发送，所以取消用不到的工具也能节省 token。改动只影响新对话。
- **上限。** 默认每个请求最多 5 轮工具调用、新增 100,000 token。用完后网关拒绝之后的 OpenViking 调用，模型用已有结果继续回答；模型被拒后仍坚持调用，这个请求就会报错。单次调用超过 30 秒，这次调用向模型返回错误；整个请求超过 120 秒，请求失败。
- **客户端要求。** 客户端要每轮回传完整历史；使用 OpenAI Responses 时要设置 `store: false`。客户端强制指定某个工具或要求结构化输出、上游关闭了**允许 OpenViking 工具**，或者上游是 DeepSeek、关闭了**补全推理内容回传**而请求又没有关闭思考模式时，网关不提供 OpenViking 工具。DeepSeek 要求带工具的请求回传之前每条回复的推理内容，而很多客户端不会发回来；DeepSeek 上游默认开启**补全推理内容回传**，由网关补回这部分内容，所以保持思考模式也能使用工具，见[上游](22-context-gateway-operations.md#上游)。对话是否带工具，在它的第一个请求时就决定了。

完整的条件、上限设置、文件导入方式和失败处理，见[OpenViking 工具](22-context-gateway-operations.md#openviking-工具)。

### 用户看到什么

模型的文字实时流出。每次 OpenViking 工具调用都在回复里留一行提示，调用开始时出现，结束后补上 `done`、`failed` 或 `skipped`：

```text
> OpenViking find: "上次定的发布日期" — done
> OpenViking read: viking://user/alice/memories/release.md — done
```

提示行只显示工具名和查询词、URI 这类调用对象，完整参数和调用结果用户看不到。提示行不发给模型，也不保存到 OpenViking，管理员可以在上下文配置里关掉它。客户端收到的 token 用量是这条回复里所有模型调用之和。

### 代价与管理员的控制点

> **注意**：勾选的工具直接执行，不经过客户端的权限确认，包括写入和删除数据的工具。提示行是事后告知，不是确认。模型能做的事以该用户自己的 OpenViking 权限为上限。

- **收紧工具。** 给聊天应用用的上下文配置，建议至少取消 `forget` 和 `set_acl`。这是建议，不是默认值。
- **四个控制点**：上下文配置里的总开关、逐个工具勾选、每个上游的**允许 OpenViking 工具**开关，以及工具轮数和 token 上限。
- **和插件或 MCP 怎么选。** 插件和 MCP 在客户端里显示每次调用和结果，并在调用前请求确认，所以 Claude Code、Codex 用它们更透明。网关工具主要服务接不了插件或 MCP 的客户端。

**实验性：Agent 自管上下文窗口。** 模型能使用 OpenViking 工具时，上下文配置还可以让它自己管理上下文窗口：模型会多两个工具，一个查看当前窗口用了多少，一个写好交接笔记后开启新窗口；窗口快满时，网关还会提醒它。这项功能默认关闭。详见[实验性：Agent 自管上下文窗口](22-context-gateway-operations.md#实验性-agent-自管上下文窗口)。

## 网关还是插件

OpenViking 也可以通过运行在 Agent 内部的插件接入 Claude Code、Codex、OpenCode、pi、OpenClaw、Hermes 等 Agent（见 [Agent 集成概览](../agent-integrations/01-overview.md)）。两种方式互相补充，差别都来自它们运行的位置：

| | 上下文网关 | Agent 插件 |
| --- | --- | --- |
| 运行位置 | 在客户端和模型服务商之间，只能看到发给模型的请求。 | 在 Agent 内部，能看到 Agent 的会话、事件和本地工作区。 |
| 适用的客户端 | 凡是能设置 Base URL 和 API Key 的客户端：聊天应用、SDK 和 API 应用、低代码平台、编程 Agent。 | 有 OpenViking 插件的 Agent。 |
| 无法覆盖的客户端 | 直接用订阅账号登录的客户端（Claude Code 的 Claude 登录、Codex 的 ChatGPT 登录；可以改用[订阅反代上游](#自定义上游)），以及模型调用从厂商服务器发出的客户端，例如 Cursor 和 Trae。 | 没有扩展接口的客户端。 |
| 按项目区分记忆 | 看不到工作目录和代码仓库。记忆按 OpenViking 用户归属，需要按项目隔离时，给每个项目使用绑定不同 OpenViking 用户的网关密钥。 | 自动识别工作区和代码仓库。 |
| 能看到什么 | 补充的记忆在客户端里不可见。OpenViking 工具调用默认在回复里显示为一行提示，但不显示调用结果。两者都可以到 Studio 查看。工具调用不经过客户端的权限确认。 | 工具调用出现在对话记录里，并经过 Agent 的权限确认。 |
| 保存时机 | 晚一轮：下一条消息到达时保存上一轮，最后一轮在对话停顿一段时间后保存。 | 跟随 Agent 自己的事件，例如每轮结束时。 |
| 安装与升级 | 所有人共用一个服务，不用在每台机器上安装。上游、密钥和记忆设置集中管理。 | 每台机器分别安装和升级。 |
| 密钥、数据和故障 | 集中保管服务商的 API Key 和尚未保存的对话文本（加密存储），每次模型调用多经过一跳。网关停止服务时，经由它的模型调用都会失败。 | 服务商的 API Key 留在各自的 Agent 里，只有保存的对话进入 OpenViking，不需要额外维护服务。 |

Claude Code、Codex 和 pi 如果需要按项目区分记忆，或者要求每次工具调用都能看到并确认，就继续用插件。没有插件的客户端，以及想集中管理服务商、密钥和记忆设置的场景，用网关。

**两者同时使用。** 请求中出现 OpenViking 插件的迹象时，网关会停止为这段对话补充记忆、保存对话和提供 OpenViking 工具，只负责转发，这样同一份内容不会被补充两次或保存两次。网关根据以下三类迹象判断：

- 插件插入提示词的记忆块：`<openviking-context>`、`<relevant-memories>`、`<relevant-memory>` 或 `<memory-context>`；
- 名称中带 `openviking` 段的客户端工具，例如 `openviking_search` 或 `mcp__openviking__find`，所以把 OpenViking MCP 服务器配置成 `openviking` 这个名字的客户端也算在内；
- `X-OpenViking-Plugin` 请求头，插件和应用的作者可以发送它，明确表示不需要网关补充记忆。

这个判断对这段对话一直有效；想重新用上网关记忆，需要在不带插件的情况下开始新对话。其他对话不受影响。在 Studio 的“请求日志”标签页里，这些请求会标上**检测到 OpenViking 插件**。

## 快速开始

下面的流程在一台机器上运行 OpenViking Server、网关和测试客户端。你需要：

- 一套能正常工作的 OpenViking，`ov.conf` 中已经配置好 embedding 和 VLM 模型（见[快速开始](../getting-started/02-quickstart.md)）；
- Python 3.10 或更高版本；
- 一个模型服务商的 API Key。订阅登录和 Coding Plan 密钥无法通过网关使用。

### 1. 安装网关

把 `context-gateway` 可选依赖安装到 OpenViking 所在的环境：

::: code-group

```bash [pip]
pip install "openviking[context-gateway]"
```

```bash [uv]
uv tool install "openviking[context-gateway]" --upgrade
```

:::

安装后，`openviking-context-gateway --help` 会打印命令用法。在 Linux 和 macOS 上，还可以加装 `context-gateway-fast`（`"openviking[context-gateway,context-gateway-fast]"`），换用更快的事件循环和 HTTP 解析器。

### 2. 生成加密密钥和管理令牌

网关需要两样东西：一个加密密钥，用来加密它存储的数据；一个管理令牌，OpenViking Server 凭它代你调用网关的管理接口。两者各生成一次，保存在只有你能读取的文件里：

```bash
mkdir -p ~/.openviking
cat > ~/.openviking/context-gateway.env <<EOF
export OPENVIKING_CONTEXT_GATEWAY_ENCRYPTION_KEY="$(python3 -c 'import base64, os; print(base64.urlsafe_b64encode(os.urandom(32)).decode())')"
export OPENVIKING_CONTEXT_GATEWAY_ADMIN_TOKEN="$(python3 -c 'import secrets; print(secrets.token_urlsafe(48))')"
EOF
chmod 600 ~/.openviking/context-gateway.env
```

OpenViking Server 和网关都要读取这两个环境变量，所以启动它们的每个终端都要先加载这个文件。加密密钥务必保留好：一旦更换，网关就读不出之前存储的数据。

### 3. 开启 API Key 认证和网关

网关用每个人自己的 OpenViking 密钥访问记忆，所以 OpenViking 必须运行在 API Key 模式。把下面的设置合并进 `~/.openviking/ov.conf`，root key 用一个足够长的随机值：

```json
{
  "server": {
    "auth_mode": "api_key",
    "root_api_key": "<root-key>"
  },
  "context_gateway": {
    "enabled": true,
    "public_url": "http://127.0.0.1:1935"
  }
}
```

其余设置保持默认：网关监听 `127.0.0.1:1935`，通过 `http://127.0.0.1:1933` 访问 OpenViking，数据存放在 `~/.openviking/context-gateway`。`public_url` 是客户端使用的地址，Studio 的接入说明会显示它。

### 4. 启动 OpenViking Server

```bash
source ~/.openviking/context-gateway.env
openviking-server
```

如果服务已经在运行，就从这个终端重新启动它，让它同时读到新配置和管理令牌。

### 5. 创建账号和用户密钥

在另一个终端里，用 root key 创建一个账号及其首位管理员，做法见[认证](04-authentication.md)：

```bash
curl -X POST http://127.0.0.1:1933/api/v1/admin/accounts \
  -H "X-API-Key: <root-key>" \
  -H "Content-Type: application/json" \
  -d '{"account_id": "acme", "admin_user_id": "alice"}'
# 返回：{"result": {"account_id": "acme", "admin_user_id": "alice", "user_key": "..."}}
```

保存 alice 的 `user_key`。在这个流程里它有两个用途：在 Studio 里以账号管理员身份登录，以及作为网关访问 alice 记忆时使用的 OpenViking 密钥。团队使用时，给每个人单独创建用户密钥（调用 `POST /api/v1/admin/accounts/acme/users`，并指定 `"role": "user"`）。

### 6. 启动网关

```bash
source ~/.openviking/context-gateway.env
openviking-context-gateway --config ~/.openviking/ov.conf
```

检查它能否连上 OpenViking：

```bash
curl -s http://127.0.0.1:1935/health
# {"status":"ok","service":"context-gateway","openviking":{"status":"ok","healthy":true,"version":"…","auth_mode":"api_key"}}
```

刚启动时，`openviking` 可能还显示 `{"status":"starting"}`。如果显示 `"status":"degraded"`，见[故障排查](22-context-gateway-operations.md#故障排查)。

### 7. 在 Studio 中完成设置

打开 <http://127.0.0.1:1933/studio>，进入**连接设置**，把 alice 的密钥同时填入**用户 API 密钥**和**管理员 API 密钥**。然后在侧边栏的“设置”分组里选择**上下文网关**。第一个请求到达之前，“概览”标签页会显示**快速开始**清单，步骤与下面相同：

1. **添加上游。** 在“上游”标签页选择**添加上游**。填写名称，选择服务商，再选择客户端使用的协议（本流程用 Chat Completions）。Studio 会填入服务商的 Base URL，例如 OpenAI 为 `https://api.openai.com/v1`；选*通用*时需要自己填写。保持选中**由网关保管 API Key**，再粘贴服务商的 API Key。保存后在上游列表里点**测试**，确认网关能连上服务商。
2. **创建上下文配置。** 在“上下文配置”标签页选择**使用推荐设置创建**，会创建一份名为“默认”的配置，其中 OpenViking 工具只勾选了只读工具。
3. **签发网关密钥。** 在“密钥”标签页选择**签发密钥**。填写名称，在 **OpenViking 用户**中选择 alice（账号里只有她一个用户时已经自动选好），再选择“默认”配置和刚添加的上游，然后签发。**复制网关密钥**对话框只显示一次完整的 `ovcg_…` 密钥，关闭之前先复制好。
4. **接入客户端。** “接入”标签页列出了每种客户端的配置，并已填好你的网关地址。下文[接入客户端](#接入客户端)也列出了同样的配置。

### 8. 发送测试请求

```bash
export GATEWAY_KEY='ovcg_...'

curl -s http://127.0.0.1:1935/v1/models -H "Authorization: Bearer $GATEWAY_KEY"
# {"object":"list","data":[{"id":"…","object":"model","owned_by":"context-gateway"}]}

curl -s http://127.0.0.1:1935/v1/chat/completions \
  -H "Authorization: Bearer $GATEWAY_KEY" \
  -H "Content-Type: application/json" \
  -H "X-OpenViking-Session: quickstart-1" \
  -d '{"model": "<model>", "messages": [{"role": "user", "content": "记住：我喜欢简短的回答。"}]}'
```

模型列表里是你在上游中填写的模型和别名；如果上游的模型列表留空，这里也是空的。第二条命令应该返回服务商的一次正常回复。

### 9. 确认记忆生效

- **请求经过了网关。** 打开“请求日志”标签页，刚才的请求显示为**新消息**，状态为 200。OpenViking 中有相关内容时，**记忆**列会显示补充的条数（例如 `+3`）；全新账号下这一列为空。
- **对话已保存。** 用同一个 `X-OpenViking-Session` 请求头再发一条消息。第二条消息一到，第一轮就会保存；最后一轮在停顿 10 分钟后保存。之后，以 alice 身份连接 Studio 时，这段对话会以 `context-gateway-…` 会话的形式出现在**会话**页面。
- **记忆已提取。** 会话提交之后，OpenViking 才会提取记忆：对话停顿 10 分钟时提交一次，待提交内容累计到 20,000 token 时也会提交。提取在后台进行，需要稍等片刻。然后换一个会话请求头的值开始新对话，问一个依赖这条记忆的问题，例如“你的回答应该多长？”。**记忆**列会显示召回的条数，回复也应该用上了这些记忆。

试用阶段想更快看到效果，可以另建一份上下文配置，把**最新回复等待时长**调短，用它签发一个密钥，再用这个密钥开始新对话。上下文配置的修改只对之后开始的对话生效。

## 接入客户端

每个客户端都需要两样东西：网关地址和网关密钥。示例使用 `https://ov.example.com`，请换成你自己的网关地址；Studio 在上下文网关页面顶部和“接入”标签页都会显示它。另外，客户端所用的密钥必须绑定一个已启用的上游，这个上游要支持客户端的协议，并提供客户端请求的模型。

| 客户端 | 上游协议 | Base URL | 对话识别方式 |
| --- | --- | --- | --- |
| [Claude Code](#claude-code) | Anthropic Messages | `https://ov.example.com` | Claude Code 的会话请求头 |
| [Codex CLI](#codex-cli) | Responses | `https://ov.example.com/v1` | Codex 的会话请求头 |
| [聊天客户端和 SDK](#聊天客户端和-sdk) | Chat Completions（或 SDK 使用的 API） | `https://ov.example.com/v1` | `X-OpenViking-Session`，需要自己发送 |
| [Open WebUI](#open-webui) | Chat Completions | `https://ov.example.com/v1` | 连接上配置的 `X-OpenViking-Session` 请求头 |
| [OpenCode](#opencode) | Chat Completions | `https://ov.example.com/v1` | OpenCode 的会话请求头 |
| [pi](#pi) | Chat Completions | `https://ov.example.com/v1` | 通常根据对话历史匹配 |
| [火山方舟与 BytePlus 方舟 SDK](#火山方舟与-byteplus-方舟-sdk) | 三种均可 | `https://ov.example.com/api/v3` 或 `https://ov.example.com/api/compatible` | 取决于客户端发送的请求头 |

Open WebUI、OpenCode 和 pi 的配置依据各自文档中的服务商设置编写，请把它们当作起点，配好后检查一下效果：在同一段对话里发两条消息，然后在“请求日志”中展开这两个请求。它们应该属于同一段对话，而且第二条消息到达后，第一轮已经保存。

### Claude Code

Claude Code 使用 Anthropic Messages。启动前设置三个环境变量：

```bash
export ANTHROPIC_BASE_URL=https://ov.example.com
export ANTHROPIC_AUTH_TOKEN='<gateway-key>'
export CLAUDE_CODE_GATEWAY_HINT_HEADERS=1
```

- 地址不要带 `/v1`，Claude Code 会自己补全路径。
- `CLAUDE_CODE_GATEWAY_HINT_HEADERS=1` 让 Claude Code 给子 Agent、上下文压缩和后台请求加上标记，网关就不会为这些请求搜索记忆，也不会把它们当成对话轮次保存。
- Claude Code 会发送自己的会话 ID，所以网关能自动识别对话，`--resume` 之后也一样。
- 上游必须接受 Claude Code 请求的模型名。可以把上游的模型列表留空、列出这些名称，或者用模型别名把它们映射到服务商提供的模型。
- Claude 订阅登录无法通过网关使用，携带订阅令牌的请求会被拒绝。请为上游配置服务商的 API Key。
- 如果 Claude Code 同时装了 OpenViking 插件或 OpenViking MCP 服务器，网关会让出这些对话（见[网关还是插件](#网关还是插件)）。

### Codex CLI

Codex 使用 Responses API，每个请求都带完整历史，正好满足网关的要求。在 `~/.codex/config.toml` 中添加一个服务商，两个顶层设置必须写在所有 `[section]` 之前：

```toml
model_provider = "openviking"
model = "<model>"

[model_providers.openviking]
name = "OpenViking Context Gateway"
base_url = "https://ov.example.com/v1"
wire_api = "responses"
env_key = "OPENVIKING_GATEWAY_KEY"
```

然后在运行 Codex 的环境里设置网关密钥：

```bash
export OPENVIKING_GATEWAY_KEY='<gateway-key>'
```

- 密钥绑定的上游必须使用 Responses，并提供 `<model>`。
- Codex 会先尝试 WebSocket 连接，网关拒绝后它会自动改用 HTTP。
- Codex 会发送自己的会话 ID，所以网关能自动识别对话，`codex resume` 之后也一样。
- 遇到不认识的模型名，Codex 可能提示缺少模型元数据，这个提示不影响请求。
- ChatGPT 登录无法通过网关使用，请在上游配置服务商的 API Key。

### 聊天客户端和 SDK

支持 OpenAI Chat Completions API 的客户端和 SDK 都能使用网关。在客户端要求填写 OpenAI 兼容服务商的地方填入：

```text
Base URL: https://ov.example.com/v1
API key: <gateway-key>
Model: <model>
X-OpenViking-Session: <conversation-id>
```

`X-OpenViking-Session` 请求头可选，但建议发送，它告诉网关请求属于哪段对话。取值要在同一段对话内保持不变、不同对话之间互不相同，例如应用里的聊天 ID。不发送时，网关根据历史匹配对话，见[网关如何识别对话](#网关如何识别对话)。

使用 OpenAI Python SDK：

```python
from openai import OpenAI

client = OpenAI(base_url="https://ov.example.com/v1", api_key="<gateway-key>")

reply = client.chat.completions.create(
    model="<model>",
    messages=[{"role": "user", "content": "发布日期最后定在哪天？"}],
    extra_headers={"X-OpenViking-Session": "chat-42"},
)
print(reply.choices[0].message.content)
```

另外两种 API 的 SDK 用法相同。Anthropic SDK 指向 `https://ov.example.com`。Responses API 使用 `https://ov.example.com/v1`，每个请求都要带完整历史并设置 `store: false`，其他 Responses 请求会直接转发，不补充记忆。上游使用的 API 必须和 SDK 一致。

流式调用 Chat Completions 时，还要请求返回用量（`"stream_options": {"include_usage": true}`）。否则服务商不会为流式回复报告 token 数，Studio 无法显示用量，网关也只能根据请求内容估算上下文窗口用了多少。

### Open WebUI

在 Open WebUI 中添加一个 OpenAI 兼容连接（Admin Panel → Settings → Connections），Base URL 填 `https://ov.example.com/v1`，密钥填网关密钥。再给这个连接添加以下自定义请求头，这样每个聊天都是一段独立的对话，Open WebUI 的后台任务也能被识别出来：

```json
{
  "X-OpenViking-Session": "{{CHAT_ID}}",
  "X-OpenViking-Task": "{{TASK}}"
}
```

同时在 Open WebUI 的环境变量中设置 `RAG_SYSTEM_CONTEXT=true`。这样 Open WebUI 会把从附件中检索到的内容放进 system 消息，而不是临时改写你这一轮的消息，对话历史在请求之间就能保持稳定。

- **一个连接密钥只对应一位记忆归属者。** 通过这个连接聊天的所有 Open WebUI 用户，读写的都是密钥背后那位 OpenViking 用户的记忆。要么只把这个连接给一个人使用，要么接受这些用户共享记忆。
- Open WebUI 还会通过同一个连接发送后台请求（生成标题、标签和追问建议）。网关能识别标题和摘要请求。如果请求日志里有其他后台任务被标成**新消息**，就把 Open WebUI 的任务模型改成一个不经过网关的连接。

### OpenCode

在 `~/.config/opencode/opencode.json` 中添加一个服务商，与已有的设置合并：

```json
{
  "provider": {
    "openviking": {
      "npm": "@ai-sdk/openai-compatible",
      "name": "OpenViking",
      "options": {
        "baseURL": "https://ov.example.com/v1",
        "apiKey": "{env:OPENVIKING_GATEWAY_KEY}"
      },
      "models": {
        "<model>": {}
      }
    }
  }
}
```

把网关密钥导出为 `OPENVIKING_GATEWAY_KEY`，然后在 OpenCode 中选择 `openviking/<model>`。上游必须使用 Chat Completions。如果同时安装了 OpenViking 的 OpenCode 插件，网关会让出这些对话。

### pi

在 pi 的模型配置 `~/.pi/agent/models.json` 中添加一个服务商：

```json
{
  "providers": {
    "openviking": {
      "baseUrl": "https://ov.example.com/v1",
      "apiKey": "OPENVIKING_GATEWAY_KEY",
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

`apiKey` 填的是保存网关密钥的环境变量名，启动 pi 前先导出它。上游必须使用 Chat Completions。除非 pi 发送了[网关如何识别对话](#网关如何识别对话)中列出的某个请求头，否则网关会根据历史匹配它的对话。如果 pi 自己的 OpenViking 扩展处于启用状态，网关会让出这些对话，两者选一个使用即可。

### 火山方舟与 BytePlus 方舟 SDK

已经配置好火山方舟或其海外站 BytePlus 方舟的客户端和 SDK，只需替换域名。网关既接受方舟自己的路径（`/api/v3/chat/completions`、`/api/v3/responses`、`/api/v3/models` 和 `/api/compatible/v1/messages`），也接受标准的 `/v1` 路径：

| 原地址 | 网关地址 |
| --- | --- |
| `https://ark.cn-beijing.volces.com/api/v3` | `https://ov.example.com/api/v3` |
| `https://ark.ap-southeast.bytepluses.com/api/v3` | `https://ov.example.com/api/v3` |
| `https://ark.cn-beijing.volces.com/api/compatible`（Anthropic 兼容） | `https://ov.example.com/api/compatible` |

用网关密钥代替方舟的 API Key。使用火山方舟 Python SDK：

```python
from volcenginesdkarkruntime import Ark

client = Ark(base_url="https://ov.example.com/api/v3", api_key="<gateway-key>")
```

路径只决定客户端使用哪种 API。请求仍然发往密钥绑定的、使用这种 API 并提供该模型的上游，通常是服务商选为“火山方舟”或“BytePlus 方舟（海外站）”的上游（见[上游](22-context-gateway-operations.md#上游)）。

## 网关如何识别对话

网关需要知道请求属于哪段对话。一段对话固定使用同一份上下文配置和同一个上游，并保存到同一个 OpenViking 会话。网关按以下顺序取第一个出现的请求头：

1. `X-OpenViking-Session`
2. `thread-id`
3. `x-claude-code-session-id`（Claude Code）
4. `x-opencode-session-id`（OpenCode）
5. `x-session-id`
6. `session-id`（Codex CLI）

这些请求头都没有时，网关查看历史中最近一条助手回复。如果恰好有一段之前的对话产生过这条回复，请求就并入那段对话；否则开始一段新对话。两段对话不会仅仅因为开场消息相同而被合并。普通的一问一答这样就能识别，但重试、重新生成的回答和内容完全相同的对话存在歧义，可能被当成新对话。所以只要客户端允许设置请求头，就发送 `X-OpenViking-Session`。

对话归属于密钥背后的 OpenViking 用户，并且按 API 分开。同一用户的两个网关密钥如果发送相同的会话值，会共享同一段对话；同一个会话值用在另一种 API 上，则是另一段对话。网关在转发前会去掉所有 `X-OpenViking-*` 请求头，服务商看不到它们。

请求没有归入已有对话时，记忆照样工作：新消息照常搜索，之前补充的记忆也照常原样放回，因为网关是根据消息本身找到这些记忆的。区别在于这个请求会开始一段新对话：它的轮次保存到新的 OpenViking 会话，记忆预算从头计算，并使用密钥当前的上下文配置。

## 使用须知

- **记忆按消息补充。** 每条新消息的第一次模型调用要等 OpenViking 搜索完成，最多等上下文配置里的**超时时间**（默认 2 秒）。OpenViking 超时或不可用时，这条消息不带记忆直接发给模型，之后也不会补上。OpenViking 停止服务期间，模型请求照常可用。
- **设置只对新对话生效。** 一段对话始终使用它开始时的上下文配置和上游。Claude Code、Codex 这类长期运行的客户端会把一段对话延续很多天，所以修改上下文配置后，要等它们开始新对话才会生效。对话闲置 30 天后，网关会删除它的状态。
- **保存晚一轮。** 对话的最后一轮在停顿 10 分钟后保存，记忆要等 OpenViking 处理完提交后才会出现。
- **改动过的历史会重新保存。** 如果客户端编辑或删除了之前的消息、在回答保存后重新生成，或者压缩了对话，网关会把当前的完整历史保存到一个新的 OpenViking 会话。已经保存的轮次留在旧会话里。
- **客户端保留完整历史。** 网关压缩对话后，客户端仍会发送完整历史，网关在每个请求里把压缩位置之前的部分换成摘要。压缩之后客户端看到的用量很小，按 token 用量决定是否压缩的客户端很少再自己压缩，所以特别长的会话最终可能碰到请求大小上限。
- **一个密钥只对应一位记忆归属者。** 使用同一个密钥的人共享其背后 OpenViking 用户的记忆。请给每人签发一个密钥；想让不同客户端使用不同的上下文配置，就按客户端再分开签发。
- **不支持订阅登录。** 携带 Claude 订阅令牌的请求会被拒绝。请在上游配置服务商的 API Key。
- **Responses 需要完整历史。** 依赖服务商端状态的 Responses 请求（`previous_response_id`、`conversation`、`background`），以及没有设置 `store: false` 的请求，会直接转发，不补充记忆。之后查询这些响应时，请求仍然发往创建它们的上游。
- **其他接口直接转发。** 三种模型 API 和模型列表之外的接口（例如 embeddings）不补充记忆，直接转发给密钥绑定的、提供所请求模型且优先级最高的上游。
- **请求有大小上限。** 超过 32 MiB 的请求体会被拒绝，运维人员可以调整这个上限。

## 下一步

- [上下文网关部署与运维](22-context-gateway-operations.md)：用 Docker Compose 或 Helm 部署，管理上游、上下文配置和密钥，排查问题。
- [认证](04-authentication.md)：创建账号、用户和他们的密钥。
- [公网访问与反向代理](12-public-access.md)：为 OpenViking 配置 HTTPS。
- [Agent 集成概览](../agent-integrations/01-overview.md)：为支持插件的 Agent 安装插件。
- [MCP 集成](06-mcp-integration.md)：让 MCP 客户端直接使用 OpenViking 工具。
