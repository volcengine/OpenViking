---
description: 把任何使用 API Key 的模型客户端指向 OpenViking 网关，让它用上 OpenViking 记忆。
---

# OpenViking 网关

OpenViking 网关是一个模型 API 代理：客户端只要能修改 Base URL，接上它就能用上 OpenViking 记忆。接入只改两处：Base URL 换成网关地址，模型服务商的 API Key 换成网关密钥。不用装插件，也不用改代码或提示词。

想先直观了解网关的工作原理，可以看官网的动画演示：[openviking.ai/gateway](https://openviking.ai/gateway)。

网关在转发请求时完成三件事：

| 能力 | 网关做什么 |
| --- | --- |
| 自动召回 | 用户每发一条新消息，网关先检索 OpenViking，把相关记忆附加到这条消息上，再交给模型。 |
| 主动使用记忆 | 模型可以调用 OpenViking 工具检索、读取、写入和导入资料。工具由网关执行，客户端不用声明或实现任何工具。 |
| 沉淀与压缩 | 网关把对话保存回 OpenViking，由 OpenViking 从中提取新记忆。对话接近模型的上下文窗口时，网关让同一个模型写摘要，替换较早的内容。 |

**适合谁**：装不了插件或 MCP 的客户端，例如聊天应用、SDK 脚本和低代码平台；以及想集中管理模型服务商、密钥和记忆设置的团队。**不适用**：直接用订阅账号登录的客户端（可以改用[订阅反代上游](#自定义上游)），以及 Cursor、Trae 这类从厂商服务器发起模型调用的客户端。

网关支持三种模型 API：Anthropic Messages、OpenAI Chat Completions 和 OpenAI Responses（要求每个请求带完整历史）。请求转发到你配置的模型服务地址，本文称为**上游**。请求用哪种 API 发来，网关就用同一种 API 转发，不做协议转换。

网关目前处于 Beta 阶段。本页介绍它的工作方式和客户端接入；部署、Studio 中的管理和排障见[OpenViking 网关部署与运维](22-gateway-operations.md)。

> **注意**：OpenViking 网关和 VikingBot Gateway（`vikingbot gateway`）是两个组件。后者负责远程访问和聊天平台接入。

## 自定义上游

任何兼容这三种 API 之一的服务都能添加为上游，不一定是模型服务商本身。两种常见用法：

- **计费、配额和负载均衡。** 网关不做这些。需要时在网关后面接一层 LiteLLM、new-api 这类服务，把它添加为上游。
- **使用订阅额度。** 客户端不能用订阅账号登录网关，但可以把 [CLIProxyAPI](https://github.com/router-for-me/CLIProxyAPI) 这类反向代理添加为上游。它把 ChatGPT（Codex）、Claude 等订阅账号包装成 API，例如 Codex 就能经网关使用 ChatGPT 订阅额度。OpenAI Codex 负责人 Tibo 曾[公开介绍](https://x.com/thsottiaux/status/2076119366647894371)过用 CLIProxyAPI 接入 Codex 订阅的做法。**是否符合服务商的使用条款，请自行确认。**

添加时服务商选*通用*，见[上游](22-gateway-operations.md#上游)。

## 一张图看懂架构

![OpenViking 网关的两种运行形态。嵌入：客户端访问 OpenViking Server 的端口，Server 把 /gateway 下的所有路径（模型 API、上传和网关管理）交给同一进程里的网关，网关通过本机回环 HTTP、用用户本人的 OpenViking 密钥检索和保存记忆。独立：openviking-gateway 用自己的端口提供同样的 /gateway 路径和只含网关页面的 Studio，通过 HTTP(S) 调用已有的 OpenViking 服务。两种形态下，网关都用同一种 API 把请求转发给上游模型服务商；会话和记忆的正本在 OpenViking，网关本机只有两个 SQLite 文件（开启 OpenViking 加密时加密存储）](../../images/gateway/architecture.zh.svg)

- **运行在哪**：开启后嵌入 OpenViking Server，和 Server 共用端口；也可以用 `openviking-gateway` 独立运行，连接已有的 OpenViking 服务（开源版或商业版）。两种方式下网关的所有 URL 都在 `/gateway` 下（例如 `/gateway/v1`），客户端路径相同，见[部署方式](22-gateway-operations.md#部署方式)。
- **和 OpenViking 的关系**：网关只调用 OpenViking 的公开接口，向量化和记忆提取都在 OpenViking 中完成。
- **数据在哪**：会话和记忆的正本在 OpenViking。网关本机只有两个 SQLite 文件（开启 OpenViking 加密时加密存储），一个存上游、密钥和配置，一个存对话状态，见[安全与数据](22-gateway-operations.md#安全与数据)。
- **出故障时**：OpenViking 不可用时，对话照常进行，只是不带记忆，保存会自动重试。网关停止服务时，经过它的模型调用全部失败。
- **管理哪些对象**：同一账号的用户共用**上游**（模型服务地址和 API Key）和**上下文配置**（召回预算、是否保存对话、是否开放 OpenViking 工具等记忆设置）。每个网关密钥绑定一位 OpenViking 用户、一份上下文配置和一组上游。谁能修改什么，见[权限矩阵](../api/25-gateway.md#凭证和账号范围)。

## 接入后，对话会发生什么

先解释两个词。**召回**指从 OpenViking 搜出和当前消息相关的记忆，附加到消息上。**提示缓存**是服务商的计费机制：请求开头和上一次相同的部分按缓存价计费，从第一个不同的字节起按原价计费。缓存价通常只有原价的一小部分，具体比例见各服务商的定价。

接入后你会得到：每条新消息补上相关记忆，新对话开头带上用户画像；对话保存回 OpenViking 并提取成新记忆；长对话自动压缩；开启 OpenViking 工具后，模型还能主动检索、读取和写入记忆（见[下一节](#让任意客户端拥有-agentic-记忆)）。

代价有三项：每条新消息的第一次模型调用最多多等 2 秒；补充的记忆按输入 token 计费，后续请求里大部分按缓存价计费；触发压缩的那一轮多一次模型调用。

![一轮对话里发生了什么：网关识别对话和请求类型，把之前补充的记忆原样放回，只为新消息召回，再用同一种 API 转发给上游，并把这一轮放进待保存队列；下方说明原样放回如何让提示缓存持续命中，以及跨轮的保存和长对话压缩](../../images/gateway/one-turn.zh.svg)

网关自动认出同一段对话（方法见[网关如何识别对话](#网关如何识别对话)）。只有用户的新消息触发召回，工具步骤和生成标题这类辅助请求不触发。下文的预算、时长和开关都来自密钥使用的**上下文配置**，管理员可以逐项调整或关闭。

### 模型看到什么

模型 API 是无状态的，客户端每次请求都把整段对话重新发一遍。请求以一条新的用户消息结尾时，网关用这条消息的文本检索 OpenViking，把结果附加到消息末尾：

```text
发布日期最后定在哪天？

<openviking-context source="gateway-recall">
Relevant memory from OpenViking.
<memory uri="viking://user/alice/memories/events/release-planning.md" type="memory" detail="abstract">
团队把 2.0 版本的发布推迟到了 11 月第一周。
</memory>
</openviking-context>
```

这段内容不出现在回复里，客户端保存的历史也不变；服务商看得到它，返回的 token 用量也包含它。每条用户消息只检索一次，同一轮里的工具步骤和辅助请求沿用已经补充的内容。在同一个上下文窗口内（压缩之前），同一条记忆只补充一次，补充总量受单条消息和单个窗口两级预算限制。

### 对话开头的内容

新对话开始时，网关附上你的 OpenViking 用户画像；提供 `read` 工具时，还附上记忆和技能目录，方便模型在自动召回之外继续查找。这部分有独立的 4,000 token 预算，技能目录最多占四分之一，不占召回预算。即使第一条消息没有召回到记忆，画像和目录也会出现；某部分读取失败就省略它，对话照常继续。客户端压缩历史后，新的开头会再次附上这些内容。

开启召回或 OpenViking 工具时，开头还有一段说明，告诉模型这些内容从哪来、启用了哪些功能。例如同时开启召回、保存对话和三个 OpenViking 工具时：

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

### 记忆原样放回

客户端保存的历史里没有网关补充的内容，所以网关自己记下每个记忆块，在这段对话之后的每个请求里放回原来那条消息上，逐字节一致。原因有两个：服务商按前缀匹配提示缓存，历史有一个字节不同，后面的缓存就全部失效；Claude 的思考签名覆盖之前的对话，历史变了，Claude 会拒绝这段对话。记忆放在最新消息末尾而不放进系统提示词，也是为了缓存：系统提示词改一个字，整份缓存都会失效。

### 对话什么时候保存

网关把完成的轮次保存到密钥所属用户的 OpenViking 会话里，会话名形如 `gateway-…`。保存晚一轮：下一条用户消息到达时，网关才保存上一轮，因为这时才能确认客户端保留了它，被重新生成或放弃的回答不会保存。最后一轮在对话停顿 10 分钟后保存。

保存之后，网关会提交会话，OpenViking 在后台从中提取记忆。提交发生在两种时候：对话停顿 10 分钟，或者待提交内容累计到 20,000 token。保存前，网关会去掉自己添加的内容和 `<system-reminder>` 这类客户端噪声。子 Agent 请求、辅助请求和 token 计数请求从不保存。

### 长对话

对话用到模型上下文窗口的 90% 时，网关压缩它：让同一个模型为目前为止的对话写一份有长度上限的摘要，此后用摘要代替压缩位置之前的全部内容，客户端界面里的历史不变。开启了保存对话、模型也能用 OpenViking 的 grep 和 read 工具时，摘要后面会说明如何在已保存的对话里查找细节。每次压缩多一次模型请求，服务商缓存也失效一次。

网关需要知道模型的上下文窗口有多大。上游和上下文配置都没有设置时，网关按 1,000,000 token 计算，所以使用窗口更小的模型时，要请管理员填上实际窗口，否则长对话会在压缩前被服务商以过长为由拒绝。压缩的细节见[长对话](22-gateway-operations.md#长对话)。

## 让任意客户端拥有 Agentic 记忆

自动召回是网关替模型猜它需要什么；开启 OpenViking 工具后，由模型自己决定查什么、读什么、记什么。工具由网关执行，客户端的请求里一个工具都没有也能用。

![开启 OpenViking 工具后的一条回复：网关在客户端的请求上附加召回的记忆和 OpenViking 工具定义，模型调用 OpenViking 工具时由网关用该用户的 OpenViking 密钥执行，再把结果接回请求继续询问模型，直到模型给出最终回答；客户端收到的是一条带提示行的连贯回复，工具往返在下一轮被原样放回](../../images/gateway/tool-loop.zh.svg)

- **一条回复里完成多步操作**：网关拦下模型发出的 OpenViking 工具调用，用该用户的 OpenViking 密钥执行，把结果接回再问模型，直到模型不再调用。客户端只收到一条连贯的回复，流式和非流式都支持。
- **客户端自己的工具照常可用**：同一条回复里，模型可以同时调用 OpenViking 工具和客户端工具（例如 Bash）。客户端工具的调用原样交给客户端执行，照常经过客户端的权限确认。
- **下一轮还记得**：工具调用和结果不在客户端的历史里，网关把这些往返存下来，下一轮原样放回。模型记得自己查到过什么，提示缓存也不受影响。

### 不同客户端得到什么

| 客户端 | 不经过网关时 | 经过网关并开启 OpenViking 工具后 |
| --- | --- | --- |
| 聊天应用，例如 Cherry Studio、Open WebUI | 要用记忆得自己配 MCP 或写工具，普通对话只收发文本。 | 模型自己检索记忆、按 URI 读取原文。勾选 `remember` 后，能记下用户要求记住的事；勾选 `add_resource` 后，能把用户附带的文件导入 OpenViking。不带任何工具的纯聊天模式也能用。 |
| SDK 脚本、低代码平台 | 一问一答，多步操作要自己写循环。 | 一次请求里完成“检索 → 读取 → grep 精确匹配 → 作答”，代码不用改。 |
| Claude Code、Codex 等编程 Agent（未装 OpenViking 插件或 MCP） | 有自己的工具，但没有 OpenViking。 | OpenViking 工具和 Bash 等客户端工具在同一条回复里混用。需要逐次确认工具调用时，仍建议用插件或 MCP。 |

### 模型能用哪些工具

工具清单直接来自你的 OpenViking 服务提供的 MCP 工具，名称加上 `openviking_` 前缀；OpenViking 新增的工具也会自动可用。常用的几类：

| 用途 | 工具 | 模型拿来做什么 |
| --- | --- | --- |
| 检索 | `find`、`search`、`grep`、`glob` | 语义检索记忆和资料；按正则或文件名精确查找。 |
| 浏览和读取 | `list`、`tree`、`read` | 按 URI 展开召回条目的原文，浏览记忆和技能目录。 |
| 写入 | `remember`、`write`、`edit` | 存一条长期记忆，写入或局部修改记忆文件。 |
| 导入 | `add_resource`、`add_skill` | 按 URL 导入网页或代码仓库，导入对话附件，用一段文本新建技能，都不需要客户端有 shell。 |
| 删除和权限 | `forget`、`set_acl` 等 | 永久删除记忆，修改共享资源的访问权限。 |

### 默认设置

新建的上下文配置默认开启 OpenViking 工具，但只勾选检索、浏览和读取这类只读工具。会修改数据的工具（如 `remember`、`add_resource`、`forget`）要管理员主动勾选。轮数、总时长和 token 预算默认都不限，模型会一直使用工具直到完成。

客户端必须每轮回传完整历史；使用 Responses 时还要设置 `store: false`。客户端强制指定工具、要求结构化输出，或者自己已经接了 OpenViking 工具时，网关不再提供自己的工具。完整的默认工具列表、上限、文件导入方式和不提供工具的原因，见[OpenViking 工具](22-gateway-operations.md#openviking-工具)。

### 用户看到什么

模型的文字实时流出。每次 OpenViking 工具调用在回复里留一行提示，调用开始时出现，结束后补上 `done`、`failed` 或 `skipped`：

```text
> OpenViking find: "上次定的发布日期" — done
> OpenViking read: viking://user/alice/memories/release.md — done
```

提示行只显示工具名和调用对象（查询词、URI 等），用户看不到完整参数和结果。模型看不到提示行，网关也不把它保存到 OpenViking；管理员可以在上下文配置里关掉它。客户端收到的 token 用量是这条回复中所有模型调用之和。

### 风险和控制点

> **注意**：勾选的工具直接执行，不经过客户端的权限确认，写入和删除数据的工具也一样。提示行是事后告知，不是确认。模型能做的事以该用户自己的 OpenViking 权限为上限。

- **管理员的控制点**：上下文配置里的总开关和逐个工具勾选、每个上游的**允许 OpenViking 工具**开关，以及工具轮数、总时长和 token 上限。给聊天应用用的配置，建议至少不勾选 `forget` 和 `set_acl`。
- **和插件或 MCP 怎么选**：插件和 MCP 在客户端里显示每次调用和结果，调用前还会请求确认，所以 Claude Code、Codex 用它们更透明。网关工具主要服务接不了插件或 MCP 的客户端。

**实验性功能**：上下文配置还可以让模型自己管理上下文窗口：模型多两个工具，一个查看窗口用了多少，一个写好交接笔记后开启新窗口；窗口快满时网关会提醒它。这项功能默认关闭，见[实验性：Agent 自管上下文窗口](22-gateway-operations.md#实验性-agent-自管上下文窗口)。

## 网关还是插件

OpenViking 也能以插件形式接入 Claude Code、Codex、OpenCode、pi、OpenClaw、Hermes 等 Agent（见 [Agent 集成概览](../agent-integrations/01-overview.md)）。两种方式互相补充，差别都来自运行位置：

| | OpenViking 网关 | Agent 插件 |
| --- | --- | --- |
| 运行位置 | 在客户端和模型服务商之间，只看得到发给模型的请求。 | 在 Agent 内部，看得到 Agent 的会话、事件和本地工作区。 |
| 适用的客户端 | 能设置 Base URL 和 API Key 的客户端：聊天应用、SDK 和 API 应用、低代码平台、编程 Agent。 | 有 OpenViking 插件的 Agent。 |
| 覆盖不到的客户端 | 用订阅账号直接登录的客户端（Claude Code 的 Claude 登录、Codex 的 ChatGPT 登录；可以改用[订阅反代上游](#自定义上游)），以及从厂商服务器发起模型调用的客户端，例如 Cursor 和 Trae。 | 没有扩展接口的客户端。 |
| 按项目区分记忆 | 看不到工作目录和代码仓库。记忆按 OpenViking 用户归属，要按项目隔离，就给每个项目一个绑定不同 OpenViking 用户的网关密钥。 | 自动识别工作区和代码仓库。 |
| 可见性 | 补充的记忆在客户端里不可见。OpenViking 工具调用默认在回复里显示一行提示，不显示结果。Studio 的请求日志记录召回条数和工具轮数，不记录内容。工具调用不经过客户端的权限确认。 | 工具调用出现在对话记录里，经过 Agent 的权限确认。 |
| 保存时机 | 晚一轮：下一条消息到达时保存上一轮，最后一轮在对话停顿后保存。 | 跟随 Agent 自己的事件，例如每轮结束时。 |
| 安装与升级 | 所有人共用一个服务，不用在每台机器上安装；上游、密钥和记忆设置集中管理。 | 每台机器分别安装和升级。 |
| 密钥、数据和故障 | 集中保管服务商的 API Key 和尚未保存的对话文本（开启 OpenViking 加密时加密存储），每次模型调用多一跳。网关停止服务时，经过它的模型调用全部失败。 | 服务商的 API Key 留在各自的 Agent 里，只有保存的对话进入 OpenViking，不需要额外维护服务。 |

Claude Code、Codex 和 pi 需要按项目区分记忆，或者要求每次工具调用都能看到并确认时，继续用插件。没有插件的客户端，以及想集中管理服务商、密钥和记忆设置的场景，用网关。

**两者同时使用时，网关让路。** 请求中出现 OpenViking 插件的迹象时，网关不再为这段对话补充记忆、保存对话或提供 OpenViking 工具，只做转发，避免同一份内容被补充两次、保存两次。判断依据有两类：

- 插件插入的记忆块：`<openviking-context>`、`<relevant-memories>`、`<relevant-memory>` 或 `<memory-context>`；
- `X-OpenViking-Plugin` 请求头。插件和应用的作者可以发送它，明确告诉网关不要补充记忆。

这个判断对整段对话有效，其他对话不受影响；想重新用上网关记忆，需要不带插件开始新对话。Studio 的请求日志会把这些请求标为**检测到 OpenViking 插件**。

只接了 OpenViking MCP 工具、没装插件的客户端不算：网关照常补充记忆和保存对话，只是不再提供自己的 OpenViking 工具，免得模型看到两套同名工具（请求日志中显示为 `tool_name_collision`）。

## 快速开始

先准备好一套能正常工作的 OpenViking，`ov.conf` 中已经配好 embedding 和 VLM 模型（见[快速开始](../getting-started/02-quickstart.md)）。网关包含在核心包中，不需要另外安装。下面按服务端的情况选一条路径。已有 OpenViking 服务、不想改它的配置时，可以独立运行网关，见[独立部署](22-gateway-operations.md#独立部署)。

### 本地服务（不需要 API 密钥）

适用于绑定在 `127.0.0.1`、使用默认本地认证的服务。

1. **开启网关。** 运行 `openviking-server init`，在 **Enable OpenViking Gateway on the same port?** 处回答 yes；已有配置时，在菜单中选择 **Update gateway**。也可以直接把 `{"gateway": {"enabled": true}}` 合并进 `ov.conf`。
2. **启动或重启** `openviking-server`。
3. **在 Studio 中打开网关。** 打开 <http://127.0.0.1:1933/studio/>，在侧边栏选择 **OpenViking 网关**，不用填写密钥。默认账号还没有上游时，网关启动时会把服务端的 VLM 配置导入为上游，并创建一份上下文配置，所以“概览”标签页的**快速开始**清单前两步已经完成。导入的 Coding Plan 上游需要你确认后才能使用，见[导入 OpenViking 模型配置](22-gateway-operations.md#导入-openviking-模型配置)；没有可导入的配置时，按[上游](22-gateway-operations.md#上游)手动添加。
4. **签发网关密钥。** 在“密钥”标签页点**签发密钥**，填写名称，选择上下文配置和上游，然后签发。这个密钥以本地 `default` 用户的身份读写记忆。**复制网关密钥**对话框只显示一次完整的 `ovgw_…` 密钥，关闭前先复制。

然后向 `http://127.0.0.1:1933/gateway` [发送测试请求](#发送测试请求)。

### 远程服务（使用 API 密钥）

适用于通过网络访问、使用 `api_key` 认证的服务。

1. **开启网关。** 在服务端主机上运行 `openviking-server init`，绑定选择 **Remote (0.0.0.0)**，在 **Enable OpenViking Gateway on the same port?** 处回答 yes。接下来的 **Allow users to configure their own gateway upstreams?** 决定谁能修改上游和上下文配置：
   - 回答 yes：每个登录的用户都能配置本账号的上游和上下文配置。
   - 回答 no：init 生成一个以 `ovgm_` 开头的网关管理密钥，打印出来并写入 `ov.conf` 的 `gateway.management_key`。只有输入这个密钥才能修改上游和上下文配置，见[网关管理密钥](22-gateway-operations.md#网关管理密钥)。

   然后重启服务。也可以用 root 密钥登录 Studio，在 OpenViking 网关页面点**开启网关**：Studio 会写入 `ov.conf` 并重启服务（仅限只有一个 worker 的服务端）。
2. **准备 OpenViking API 密钥。** 用你自己的用户 API 密钥，或者按[认证](04-authentication.md)用 root 密钥创建账号和用户。
3. **登录 Studio。** 打开 `https://ov.example.com/studio/`，在**连接设置**中填入 API 密钥，然后在侧边栏选择 **OpenViking 网关**。
4. **添加上游。** 在“上游”标签页点**添加上游**，选择服务商和客户端使用的协议（下面的测试请求用 Chat Completions），粘贴服务商的 API Key 后保存。在上游列表里点**测试**，确认网关能连上服务商。如果标签页是只读的，说明部署者设置了网关管理密钥，见[在 Studio 中管理网关](22-gateway-operations.md#在-studio-中管理网关)。
5. **创建上下文配置。** 在“上下文配置”标签页点**使用推荐设置创建**。
6. **签发网关密钥**，做法同本地路径第 4 步，签发给你自己。要给其他用户签发，见[网关密钥](22-gateway-operations.md#网关密钥)。

### 发送测试请求

本地服务把地址换成 `http://127.0.0.1:1933/gateway`，并把 `<model>` 换成上游提供的模型：

```bash
export GATEWAY_URL=https://ov.example.com/gateway
export GATEWAY_KEY='ovgw_...'

curl -s "$GATEWAY_URL/v1/models" -H "Authorization: Bearer $GATEWAY_KEY"

curl -s "$GATEWAY_URL/v1/chat/completions" \
  -H "Authorization: Bearer $GATEWAY_KEY" \
  -H "Content-Type: application/json" \
  -H "X-OpenViking-Session: quickstart-1" \
  -d '{"model": "<model>", "messages": [{"role": "user", "content": "记住：我喜欢简短的回答。"}]}'
```

第一条命令列出上游配置的模型和别名；上游接受任意模型名时，列表为空。第二条命令应该返回服务商的一次正常回复。

### 确认记忆生效

1. **请求经过了网关。** 打开“请求日志”标签页，刚才的请求显示为**新消息**，状态 200。OpenViking 里有相关内容时，**记忆**列显示补充的条数（例如 `+3`）；全新账号下这一列为空。
2. **对话已保存。** 用同一个 `X-OpenViking-Session` 值再发一条消息，第一轮随即保存。以密钥所属用户的身份连接 Studio，**会话**页面会出现一个 `gateway-…` 会话。
3. **记忆已提取。** 对话停顿 10 分钟后，网关提交会话，OpenViking 在后台提取记忆，需要稍等片刻。然后换一个会话值开始新对话，问一个依赖这条记忆的问题，例如“你的回答应该多长？”。**记忆**列应显示召回的条数，回复也应该用上了这条记忆。

试用时想更快看到效果，可以另建一份上下文配置，把**最新回复等待时长**调短，用它签发一个密钥，再用这个密钥开始新对话。上下文配置的修改只对之后开始的对话生效。

## 接入客户端

每个客户端需要两样东西：网关地址和网关密钥。网关地址是服务地址加上 `/gateway`。下面的示例用 `https://ov.example.com/gateway`，请换成你的网关地址；Studio 的 OpenViking 网关页面顶部和“接入”标签页都会显示它。Anthropic 客户端直接使用网关地址，OpenAI 兼容客户端在后面加 `/v1`，方舟 SDK 加 `/api/v3`。客户端用的密钥还必须绑定一个已启用的上游，这个上游要支持客户端的协议，并提供客户端请求的模型。

| 客户端 | 上游协议 | Base URL | 对话识别方式 |
| --- | --- | --- | --- |
| [Claude Code](#claude-code) | Anthropic Messages | `https://ov.example.com/gateway` | Claude Code 的会话请求头 |
| [Codex CLI](#codex-cli) | Responses | `https://ov.example.com/gateway/v1` | Codex 的会话请求头 |
| [聊天客户端和 SDK](#聊天客户端和-sdk) | Chat Completions（或 SDK 使用的 API） | `https://ov.example.com/gateway/v1` | `X-OpenViking-Session`，需要自己发送 |
| [Open WebUI](#open-webui) | Chat Completions | `https://ov.example.com/gateway/v1` | 在连接上配置 `X-OpenViking-Session` 请求头 |
| [OpenCode](#opencode) | Chat Completions | `https://ov.example.com/gateway/v1` | OpenCode 的会话请求头 |
| [pi](#pi) | Chat Completions | `https://ov.example.com/gateway/v1` | 通常根据对话历史匹配 |
| [火山方舟与 BytePlus 方舟 SDK](#火山方舟与-byteplus-方舟-sdk) | 三种均可 | `https://ov.example.com/gateway/api/v3` 或 `https://ov.example.com/gateway/api/compatible` | 取决于客户端发送的请求头 |

Open WebUI、OpenCode 和 pi 的配置依据它们各自文档中的服务商设置编写，请当作起点，配好后验证一下：在同一段对话里发两条消息，然后在“请求日志”中展开这两个请求，它们应该属于同一段对话，并且第二条消息到达后，第一轮已经保存。

### Claude Code

Claude Code 使用 Anthropic Messages。启动前设置三个环境变量：

```bash
export ANTHROPIC_BASE_URL=https://ov.example.com/gateway
export ANTHROPIC_AUTH_TOKEN='<gateway-key>'
export CLAUDE_CODE_GATEWAY_HINT_HEADERS=1
```

- 地址不要带 `/v1`，Claude Code 会自己补全路径。
- `CLAUDE_CODE_GATEWAY_HINT_HEADERS=1` 让 Claude Code 给子 Agent、上下文压缩和后台请求加上标记，网关就不会为它们召回记忆，也不会把它们当作对话轮次保存。
- Claude Code 发送自己的会话 ID，网关据此识别对话，`--resume` 之后也一样。
- 上游必须接受 Claude Code 请求的模型名：模型列表留空、列出这些名称，或者用模型别名映射到服务商的模型。
- Claude 订阅登录不能通过网关使用，带订阅令牌的请求会被拒绝。请为上游配置服务商的 API Key。
- 同时装了 OpenViking 插件时，网关会让出这些对话（见[网关还是插件](#网关还是插件)）。只接了 OpenViking MCP 服务器时，网关照常补充记忆，OpenViking 工具交给 MCP 服务器。

### Codex CLI

Codex 使用 Responses API，每个请求都带完整历史，正好满足网关的要求。在 `~/.codex/config.toml` 中添加一个服务商，两个顶层设置必须写在所有 `[section]` 之前：

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
- ChatGPT 登录不能通过网关使用，请为上游配置服务商的 API Key。

### 聊天客户端和 SDK

支持 OpenAI Chat Completions API 的客户端和 SDK 都能接入。在客户端填写 OpenAI 兼容服务商的地方填入：

```text
Base URL: https://ov.example.com/gateway/v1
API key: <gateway-key>
Model: <model>
X-OpenViking-Session: <conversation-id>
```

`X-OpenViking-Session` 请求头可选，但建议发送，它告诉网关请求属于哪段对话。取值在同一段对话内保持不变、不同对话之间互不相同，例如应用里的聊天 ID。不发送时，网关根据历史匹配对话，见[网关如何识别对话](#网关如何识别对话)。

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

另外两种 API 的 SDK 用法相同，上游使用的 API 要和 SDK 一致：

- Anthropic SDK 指向 `https://ov.example.com/gateway`。
- Responses API 使用 `https://ov.example.com/gateway/v1`，每个请求都要带完整历史并设置 `store: false`；其他 Responses 请求直接转发，不补充记忆。

流式调用 Chat Completions 时，请同时请求返回用量（`"stream_options": {"include_usage": true}`）。否则服务商不报告流式回复的 token 数，Studio 显示不了用量，网关也只能估算上下文窗口用了多少。

### Open WebUI

在 Open WebUI 中添加一个 OpenAI 兼容连接（Admin Panel → Settings → Connections），Base URL 填 `https://ov.example.com/gateway/v1`，密钥填网关密钥。再给这个连接加上下面的自定义请求头，让每个聊天成为一段独立的对话，并让网关认出 Open WebUI 的后台任务：

```json
{
  "X-OpenViking-Session": "{{CHAT_ID}}",
  "X-OpenViking-Task": "{{TASK}}"
}
```

同时在 Open WebUI 的环境变量中设置 `RAG_SYSTEM_CONTEXT=true`。这样 Open WebUI 把附件检索结果放进 system 消息，而不是临时改写你这一轮的消息，请求之间的对话历史才能保持稳定。

- **一个连接密钥只对应一位记忆归属者。** 通过这个连接聊天的所有 Open WebUI 用户，读写的都是密钥背后那位 OpenViking 用户的记忆。要么只让一个人用这个连接，要么接受这些用户共享记忆。
- Open WebUI 还会通过同一个连接发送后台请求（生成标题、标签和追问建议）。网关能识别标题和摘要请求；如果请求日志里有其他后台任务被标成**新消息**，就把 Open WebUI 的任务模型改成一个不经过网关的连接。

### OpenCode

在 `~/.config/opencode/opencode.json` 中添加一个服务商，与已有设置合并：

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

把网关密钥导出为 `OPENVIKING_GATEWAY_KEY`，然后在 OpenCode 中选择 `openviking/<model>`。上游必须使用 Chat Completions。同时装了 OpenViking 的 OpenCode 插件时，网关会让出这些对话。

### pi

在 pi 的模型配置 `~/.pi/agent/models.json` 中添加一个服务商：

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

- `apiKey` 从环境变量 `OPENVIKING_GATEWAY_KEY` 读取网关密钥，启动 pi 前先导出它。开头的 `$` 不能省，否则 pi 会把这个字符串本身当作密钥发送。
- 上游必须使用 Chat Completions。
- pi 通常不发送会话请求头，网关根据历史匹配它的对话（见[网关如何识别对话](#网关如何识别对话)）。
- pi 自己的 OpenViking 扩展启用时，网关会让出这些对话，两者选一个即可。

### 火山方舟与 BytePlus 方舟 SDK

已经接入火山方舟或其海外站 BytePlus 方舟的客户端和 SDK，只需替换地址。在 `/gateway` 下，网关既接受方舟自己的路径（`/api/v3/chat/completions`、`/api/v3/responses`、`/api/v3/models` 和 `/api/compatible/v1/messages`），也接受标准的 `/v1` 路径：

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

客户端用哪个路径，只决定它使用哪种 API。请求仍然发往密钥绑定的、使用这种 API 并提供该模型的上游，通常是服务商选为“火山方舟”或“BytePlus 方舟（海外站）”的上游（见[上游](22-gateway-operations.md#上游)）。

## 网关如何识别对话

网关需要知道请求属于哪段对话：一段对话固定使用同一份上下文配置和同一个上游，并保存到同一个 OpenViking 会话。网关按以下顺序取第一个出现的请求头：

1. `X-OpenViking-Session`
2. `thread-id`
3. `x-claude-code-session-id`（Claude Code）
4. `x-opencode-session-id`（OpenCode）
5. `x-session-id`
6. `session-id`（Codex CLI）

这些请求头都没有时，网关查看历史中最近一条助手回复：如果之前某段对话产生过这条回复，请求就并入那段对话，否则开始一段新对话。开场消息相同的两段对话不会因此被合并。普通的一问一答这样就能识别，但重试、重新生成和内容完全相同的对话存在歧义，可能被当成新对话。所以只要客户端允许设置请求头，就发送 `X-OpenViking-Session`。

对话归属于密钥背后的 OpenViking 用户，并按 API 区分。同一用户的两个网关密钥发送相同的会话值，会共享同一段对话；同一个会话值用在另一种 API 上，则是另一段对话。网关转发前会去掉所有 `X-OpenViking-*` 请求头，服务商看不到它们。

请求没有归入已有对话时，记忆照样工作：新消息照常召回，之前补充的记忆也照常原样放回，因为网关是根据消息本身找到这些记忆的。区别在于这个请求会开始一段新对话：它的轮次保存到新的 OpenViking 会话，记忆预算从头计算，并使用密钥当前的上下文配置。

## 使用须知

- **召回最多等 2 秒。** 每条新消息的第一次模型调用要等 OpenViking 检索完成，最多等上下文配置里的**超时时间**（默认 2 秒）。OpenViking 超时或不可用时，这条消息不带记忆直接发给模型，之后也不会补上。
- **设置只对新对话生效。** 一段对话始终使用它开始时的上下文配置和上游。Claude Code、Codex 这类长期运行的客户端会把一段对话延续很多天，修改上下文配置后要等它们开始新对话才生效。对话闲置 30 天后，网关删除它的状态。
- **改动过的历史会重新保存。** 客户端编辑或删除了之前的消息、在回答保存后重新生成，或者自己压缩了对话时，网关把当前的完整历史保存到一个新的 OpenViking 会话，已经保存的轮次留在旧会话里。
- **特别长的会话可能碰到请求大小上限。** 网关压缩对话后，客户端仍发送完整历史，但看到的用量很小，所以按用量决定何时压缩的客户端很少再自己压缩。请求体超过 32 MiB 会被拒绝，运维人员可以调整这个上限，见[长对话](22-gateway-operations.md#长对话)。
- **一个密钥只对应一位记忆归属者。** 共用一个密钥的人共享其背后 OpenViking 用户的记忆。请给每人签发一个密钥；想让不同客户端使用不同的上下文配置，再按客户端分开签发。
- **不支持订阅登录。** 带 Claude 订阅令牌的请求会被拒绝。请为上游配置服务商的 API Key。
- **Responses 需要完整历史。** 依赖服务商端状态的 Responses 请求（`previous_response_id`、`conversation`、`background`），以及没有设置 `store: false` 的请求，直接转发，不补充记忆。之后查询这些响应时，请求仍然发往创建它们的上游。
- **其他接口直接转发。** 三种模型 API 和模型列表之外的接口（例如 embeddings）不补充记忆，直接转发给密钥绑定的、提供所请求模型且优先级最高的上游。

## 下一步

- [OpenViking 网关部署与运维](22-gateway-operations.md)：用 Docker Compose、Helm 或独立进程部署，管理上游、上下文配置和密钥，排查问题。
- [OpenViking 网关管理 API](../api/25-gateway.md)：权限矩阵和管理接口。
- [认证](04-authentication.md)：创建账号、用户和他们的密钥。
- [公网访问与反向代理](12-public-access.md)：为 OpenViking 配置 HTTPS。
- [Agent 集成概览](../agent-integrations/01-overview.md)：为支持插件的 Agent 安装插件。
- [MCP 集成](06-mcp-integration.md)：让 MCP 客户端直接使用 OpenViking 工具。
