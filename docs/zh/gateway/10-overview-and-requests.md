---
description: 读懂 Studio 中 OpenViking 网关的“概览”和“请求日志”标签页：卡片、列、请求类型和保存状态。
---

# 网关概览与请求日志

本页逐一说明网关**概览**和**请求日志**两个标签页里的卡片、列、标签和保存状态，以及哪些数值说明网关运行正常。在 Studio 中打开 **OpenViking 网关**，选择**概览**或**请求日志**标签页即可。客户端通过网关发出第一个请求之前，这两个标签页都没有数据，接入方法见[接入客户端](03-connect-clients.md#connect-clients)。

## 谁能看到哪些请求 {#requests-and-overview}

两个标签页读的都是网关的请求日志：每个经过网关的模型请求对应一条记录。普通用户只看到自己的请求，账号管理员和 root 看到整个账号的请求。概览里的数字也是从这些记录算出来的，所以统计范围和请求日志一致。

设置 `request_log_sink: "stdout"` 后，新记录只写到 stdout。全新部署的请求日志页和概览中的请求统计保持为空；之前已存储的记录仍可见，直到过期。新请求请通过 [stdout 观测流程](../guides/22-gateway-operations.md#monitoring)查看。

日志只记元数据，从不保存提示词、回复或记忆内容，详见[请求日志记什么](11-security-and-data.md#what-request-logs-record)。存储中的记录默认保留 30 天；stdout 的保留期限由日志采集系统管理。见[保留期限](11-security-and-data.md#retention)。

## 概览 {#overview}

**概览**标签页汇总请求日志中最新的 10,000 条请求。收到第一个请求之前，页面上还会显示**快速开始**清单，见[快速开始清单和快速模式](06-manage-in-studio.md#get-started-checklist)。

| 卡片 | 显示什么 | 正常时 | 不正常时看 |
| --- | --- | --- | --- |
| **请求数** | 模型请求的数量、最近一次请求的时间，以及这些请求的输出 token | 随客户端发请求而增长 | [检查接入是否生效](03-connect-clients.md#check-the-connection) |
| **首次调用缓存命中率** | 每条新用户消息的第一次模型调用中，命中服务商[提示词缓存](01-how-it-works.md#replay)的输入 token 所占比例 | 接近不经过网关时服务商能达到的水平。这是判断网关是否健康最重要的指标。 | [首次调用缓存命中率下降](12-troubleshooting.md#the-first-call-cache-hit-rate-dropped) |
| **轮内缓存命中率** | 同一轮里后续调用的同一比例，例如工具步骤和 OpenViking 工具的往返 | 通常很高 | — |
| **召回的记忆条数** | 补充到新消息中的记忆条数，以及每次检索 OpenViking 的平均耗时（只统计实际检索了的消息） | 用户在 OpenViking 中有记忆后开始增长；全新账号为 0 属于正常 | [没有补充记忆](12-troubleshooting.md#no-memory-is-added) |
| **OpenViking** | **已连接**、**异常**或**启动中**，OpenViking 的版本，以及异常时的原因。卡片中的**保存对话**一栏统计最近一轮没有保存成功的对话数。 | **已连接**，且最近的请求中没有保存失败的对话 | [OpenViking 异常](12-troubleshooting.md#openviking-is-degraded)、[OpenViking 中看不到对话](12-troubleshooting.md#conversations-do-not-appear-in-openviking) |
| **降级请求** | 每种异常出现的次数，附说明和建议操作 | **没有降级请求** | [降级请求的异常](12-troubleshooting.md#issues-on-degraded-requests) |
| **最近请求** | 最新的几条请求，以及前往**请求日志**标签页的链接 | — | [请求日志](#requests) |

首次调用缓存命中率明显下降，通常说明网关发送的历史和上一次对不上了。OpenViking 连不上期间，请求照常发给模型，只是不带记忆，结束的轮次也保存失败；OpenViking 恢复后，每段对话的下一轮结束时补上缺的内容。**保存对话**一栏或**降级请求**卡片报告问题时，点**在请求日志中查看**会打开**请求日志**标签页，并筛选出**异常**。

### 长对话没有命中缓存 {#long-conversations-missing-the-cache}

某段对话最近连续 3 次模型调用的输入都不少于 32,000 token、相邻两次相隔不到 5 分钟，而服务商返回的缓存命中每次都是 0 时，顶部四张数字卡片（**请求数**、**首次调用缓存命中率**、**轮内缓存命中率**和**召回的记忆条数**）下方会出现“有 2 段长对话连续没有命中提示词缓存”这样的警告，并列出每段对话的模型、上游和连续未命中次数。如果这些都是 Anthropic 对话，而且上游也从没写入过缓存，警告会建议检查客户端是否设置了 `cache_control`。

下面这些变化之后缓存本来就无法命中，所以连续次数会从头算起：

- 一次[压缩](05-long-conversations.md#long-conversations)，或者模型开启了新的上下文窗口
- 切换到别的模型或上游
- 网关工具在请求中时有时无

某个上游最近从没返回过缓存命中时，显示的是一条提示：经过它的长对话缓存命中率一直是 0%。原因和处理方法见[首次调用缓存命中率下降](12-troubleshooting.md#the-first-call-cache-hit-rate-dropped)。

## 请求日志 {#requests}

**请求日志**标签页按时间倒序列出日志中最新的 1,000 条记录，停留在第一页时每 30 秒刷新一次。筛选和搜索也只覆盖这 1,000 条。

每个模型请求都会记录，包括网关自己拒绝的请求（例如模型不在密钥允许的范围内、没有匹配的上游、请求体超限），以及在服务商处失败、或因客户端离开而结束的请求。密钥确认之前就被拒绝的请求（例如网关密钥无效）不属于任何账户，不会列出。在准备记忆之前就被拒绝的请求显示为**直接转发**。见[客户端收到网关返回的错误](12-troubleshooting.md#clients-get-an-error-from-the-gateway)。

### 筛选和搜索 {#filter-and-search}

| 筛选 | 显示 |
| --- | --- |
| **全部** | 所有记录 |
| **新消息** | 以一条新用户消息开启一轮的请求 |
| **工具步骤** | 延续同一轮的请求，例如带着工具结果继续 |
| **异常** | 降级请求、**保存失败**的请求，以及状态码为 HTTP 400 及以上的请求，无论来自服务商（见[上游返回错误](12-troubleshooting.md#the-upstream-returned-an-error)）还是网关（见[客户端收到网关返回的错误](12-troubleshooting.md#clients-get-an-error-from-the-gateway)） |
| **请求类型** | 某一种[请求类型](#request-types) |

搜索范围包括模型、对话 ID、请求 ID、密钥名称或前缀，以及上游名称。日志只保存对话 ID 的哈希，所以 Studio 会先在浏览器里对粘贴的对话 ID 求哈希再匹配。这要求 Studio 通过 HTTPS 或 localhost 打开；用普通 HTTP 打开时，按对话 ID 搜索什么也找不到。

### 各列含义 {#columns}

| 列 | 显示 |
| --- | --- |
| **时间** | 请求到达的时间 |
| **类型** | [请求类型](#request-types) |
| **模型** | 客户端发送的模型名称 |
| **状态** | 客户端收到的 HTTP 状态码：服务商的状态码，或者网关拒绝或处理失败时网关自己的状态码 |
| **Token** | 输入 token、其中命中缓存的比例，以及输出 token |
| **记忆** | 这次请求中[召回](../guides/15-gateway.md#key-terms)做了什么（见下表） |
| **保存** | 这次请求的[保存状态](#saving-status)；鼠标悬停可以看到原因 |
| **异常** | 降级请求的异常 |

**记忆**列的写法：

| 显示 | 含义 |
| --- | --- |
| `+3` | 这次检索补充了 3 条记忆，旁边附检索耗时。 |
| `↺ 4` | 之前 4 条消息补充的记忆被原样放回。 |
| **召回失败** | 这次请求的记忆检索没有完成，例如 OpenViking 连不上、拒绝了密钥，或者没有在上下文配置的**超时时间**内返回。鼠标悬停可以看到原因，另见[没有补充记忆](12-troubleshooting.md#no-memory-is-added)。 |

### 请求详情 {#request-details}

展开一行可以看到：

- 对话 ID、上游、网关密钥和耗时
- token 明细
- 记忆检索结果
- 上下文窗口用量，以及历史是否经过压缩、模型是否开启了新窗口
- 保存状态；这次请求保存了一轮时，还有写入的消息数和 OpenViking 会话 ID
- OpenViking 工具详情，包括没有提供工具的原因
- 客户端在 `X-OpenViking-Context` 中传入的配置项，只记名称；它们只在对话的第一个请求中生效

有异常的请求，展开后还会说明发生了什么、该怎么处理。

### 请求类型 {#request-types}

每条记录都属于一种类型。类型决定这次请求是否检索记忆、所在的轮次是否保存。

| 类型 | 含义 | 记忆 |
| --- | --- | --- |
| 新消息 | 最后一条是新用户消息的请求 | 检索一次，并放回之前的记忆；回复结束这一轮时保存这一轮 |
| 工具步骤 | 同一轮的延续，例如带着工具结果继续 | 放回之前的记忆；结束这一轮的那个请求保存整轮 |
| 辅助请求 | 生成标题、摘要、压缩、建议和权限检查的请求；对话带过客户端工具后，同一对话里不带工具、也不带之前回复的单独请求，例如 Claude Code 汇总抓取的网页。运维可以在[客户端规则](13-configuration.md#client-rules)里补充某个客户端自己的请求 | 放回之前的记忆；从不保存，但 Claude Code 或 Codex 在一轮中间的压缩请求会写入它之前的历史 |
| 子 Agent | 客户端的子 Agent 发出的请求 | 放回之前的记忆；从不保存 |
| Token 计数 | token 计数请求（`/gateway/v1/messages/count_tokens`） | 放回之前的记忆，计数才和实际发送的内容一致 |
| 直接转发 | 原样转发、不带记忆的请求：其他接口、依赖服务商端状态的 Responses 请求、压缩过或无法解析的请求体 | 无 |

## 保存状态 {#saving-status}

**保存**列显示这次请求所在对话是否保存到 OpenViking，以及结束一轮的请求有没有把这一轮[保存](../guides/15-gateway.md#key-terms)成功。正常情况下显示**正常**。

| 状态 | 含义 | 网关怎么做 |
| --- | --- | --- |
| **正常**（`active`） | 保存已开启；这次请求结束了一轮时，这一轮已写入 OpenViking。 | 结束一轮的请求在回复之后把这一轮写入 OpenViking。 |
| **已关闭**（`disabled`） | 这段对话的[上下文配置](08-context-profiles.md#save-conversations)不保存对话。 | 不保存任何内容。 |
| **保存失败**（`failed`） | 这次请求结束了一轮，但写入 OpenViking 失败。每一轮只尝试一次，最多等 30 秒。 | 不重试。客户端在下一轮原样发回历史时，网关在下一轮结束时把缺的内容一起写入；没有下一轮时，这一轮不会保存。 |

展开的行和**保存**标记的悬停提示会显示原因：

| 显示的原因 | 代码 | 含义 |
| --- | --- | --- |
| OpenViking 拒绝了这个密钥，它可能填错了或已被吊销。 | `openviking_http_401` | 用户的 OpenViking 密钥已失效。 |
| OpenViking 无法访问或响应超时 | `openviking_unavailable` | OpenViking 没有运行或连不上。 |
| OpenViking 响应超时 | `openviking_timeout` | OpenViking 响应太慢。 |
| OpenViking 对这个账号持续无响应，暂时跳过 | `openviking_circuit_open` | 这个账号的 OpenViking 调用正被跳过，见[OpenViking 异常](12-troubleshooting.md#openviking-is-degraded)。 |
| OpenViking 返回了 HTTP …，或 OpenViking 报告了错误 … | `openviking_http_<status>`、`openviking_<CODE>` | OpenViking 拒绝了写入，例如 `402`（未订购）、`429`（限流），或者 `400`（`capture_memory_types` 中有 OpenViking 不认识的记忆类型）。 |
| 保存到 OpenViking 超时 | `save_timeout` | 写入没有在 30 秒内完成。 |
| 保存到 OpenViking 失败 | `save_failed` | 其他错误。网关日志中有一条 "could not save a finished turn" 警告，附带调用栈。 |
| 管理员已手动重新同步 | `manual_reset` | 不是失败：有人用了**重新同步对话…**（见下文），状态仍为**正常**，这段对话之后的记录会一直显示这个原因。 |

在更早版本的网关中开始的对话，可能一直带着当时记下的原因（例如 `history_changed`），状态为**正常**时不需要处理。保存一直失败时，见[OpenViking 中看不到对话](12-troubleshooting.md#conversations-do-not-appear-in-openviking)。

### 重新同步对话 {#resync-a-conversation}

重新同步会为一段对话新建一个 OpenViking 会话，这段对话下一轮结束时，网关把完整历史写进去。保存一直失败、修复原因后想补全整段对话，或者想要一份干净、完整的对话副本时，可以用它。已经保存的内容留在旧会话里，所以 OpenViking 可能会把部分记忆提取两次。

1. 在**请求日志**标签页展开这段对话的一条记录。重新同步需要记录上显示的那把密钥；它已被吊销时，请选同一段对话里更新的一条记录。
2. 点击**重新同步对话…**，再点**重新同步**。页面会提示“这段对话会在下一轮结束时重新保存。”
3. 等用户在这段对话里完成下一轮。在此之前什么都不会发生；这一轮结束时，网关把完整历史保存到新会话，这段对话的记录会显示原因“管理员已手动重新同步”。

## 下一步 {#next-steps}

- [OpenViking 网关排障](12-troubleshooting.md#common-symptoms)：根据卡片、标签或状态找到原因和处理方法。
- [记忆原样放回与提示词缓存](01-how-it-works.md#replay)：为什么首次调用缓存命中率这么重要。
- [观测](../guides/22-gateway-operations.md#monitoring)：在 Studio 之外检查网关的运行状态。
- [安全与数据](11-security-and-data.md#what-request-logs-record)：日志记录什么、保留多久。
