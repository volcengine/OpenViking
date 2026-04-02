# OpenClaw + OpenViking 上下文引擎插件

使用 [OpenViking](https://github.com/volcengine/OpenViking) 作为 [OpenClaw](https://github.com/openclaw/openclaw) 的长期记忆后端。在 OpenClaw 中，此插件注册为 `openviking` 上下文引擎。

本文档不是安装教程，而是面向集成方和工程师的“当前实现设计说明”。它基于 `examples/openclaw-plugin` 里的现有代码，重点解释这套插件今天实际上如何工作，而不是未来可能的重构方向。

## 文档入口

- 安装与升级：[INSTALL-ZH.md](./INSTALL-ZH.md)
- English install guide: [INSTALL.md](./INSTALL.md)
- Agent 专用操作文档：[INSTALL-AGENT.md](./INSTALL-AGENT.md)

## 设计定位

- OpenClaw 仍然负责 agent runtime、prompt 编排和工具执行。
- OpenViking 负责长期记忆检索、session 归档、archive summary 和记忆抽取。
- `examples/openclaw-plugin` 不是一个单一职责的“记忆查询插件”，而是一组围绕 OpenClaw 生命周期工作的集成层。

按当前代码职责看，插件同时扮演四个角色：

- `context-engine`：实现 `assemble`、`afterTurn`、`compact`
- Hook 层：接管 `before_prompt_build`、`session_start`、`session_end`、`agent_end`、`before_reset`
- Tool 提供者：注册 `memory_recall`、`memory_store`、`memory_forget`、`ov_archive_expand`
- 运行时管理器：在 `local` 模式下拉起并监控 OpenViking 子进程

## 总体架构

![OpenClaw 与 OpenViking 插件总体架构](./images/openclaw-plugin-engine-overview.png)

上图对应的是当前实现里的整体边界：

- OpenClaw 在左侧，仍然是主运行时；插件并不接管 agent 执行本身。
- 插件中间层把 Hook、Context Engine、Tools、Runtime Manager 四部分合并在一个注册单元里。
- 所有 HTTP 调用最终都走 `OpenVikingClient`，由 client 层统一补 `X-OpenViking-*` 头和路由日志。
- OpenViking 服务端承接 session、memory、archive 和 Phase 2 抽取，底层存储落在 `viking://user/*`、`viking://agent/*`、`viking://session/*`。

这套拆分的意义，是让 OpenClaw 继续专注推理与编排，让 OpenViking 成为长期上下文的事实源。

## 身份与路由

这套插件不是把所有请求都打到一个固定 agent ID 上，而是尽量保持 OpenClaw 会话身份和 OpenViking 路由一致。

核心规则如下：

- `sessionId` 是 UUID 时直接复用。
- `sessionKey` 存在时优先用它生成稳定的 `ovSessionId`。
- 非安全路径字符会被规整或退化成稳定的 SHA-256。
- `X-OpenViking-Agent` 按 session 解析，不按进程写死。
- 若 `plugins.entries.openviking.config.agentId` 不是 `default`，会形成 `<configAgentId>_<sessionAgent>` 的前缀形式。
- client 层统一补全 `X-OpenViking-Account`、`X-OpenViking-User`、`X-OpenViking-Agent` 这些 header。

这样做是为了支持多 agent、多 session 并发时的记忆隔离，避免不同 OpenClaw 会话串用同一套长期上下文。

## Prompt 前召回链路

![Prompt 前的自动召回流程](./images/openclaw-plugin-recall-flow.png)

当前主召回路径仍然挂在 `before_prompt_build`，流程是：

1. 从 `messages` 或 `prompt` 中提取最后一条用户文本。
2. 基于当前 `sessionId/sessionKey` 解析本轮的 agent 路由。
3. 先做一次快速可用性检查，避免在 OpenViking 不可用时把 prompt 前链路拖死。
4. 并行检索 `viking://user/memories` 和 `viking://agent/memories`。
5. 在插件侧做去重、阈值筛选、重排和 token budget 裁剪。
6. 把最终记忆块以 `<relevant-memories>` 形式 prepend 到 prompt。

这里的重排不是单纯依赖向量分数。当前实现还会额外考虑：

- 是否是 `level == 2` 的叶子记忆
- 是否属于偏好类记忆
- 是否属于事件类记忆
- 与当前 query 的词面重合度

### Transcript ingest assist

除了普通 recall，这条链路还包含一个“转录文本辅助分支”。

如果最后一条用户输入看起来像多说话人的转录文本：

- 会先清理 metadata block、命令文本、纯提问文本等噪音
- 再按说话人数和文本长度做 transcript-like 判断
- 命中后 prepend 一个很轻量的 `<ingest-reply-assist>` 指令

它的目标不是改写记忆逻辑，而是降低模型在“用户粘贴聊天记录/会议纪要/对话转录”这类场景里直接返回 `NO_REPLY` 的概率。

## Session 生命周期

![Session 生命周期与压缩边界](./images/openclaw-plugin-session-lifecycle.png)

Session 是这套设计的主轴。当前实现里，它覆盖了“历史组装、增量写入、异步提交、阻塞压缩回读”四个动作。

### `assemble()` 负责什么

`assemble()` 并不是简单地把旧聊天记录塞回来，而是按 token budget 从 OpenViking 回读当前 session context，然后重新组装成 OpenClaw 可消费的消息：

- `latest_archive_overview` 被改写成 `[Session History Summary]`
- `pre_archive_abstracts` 被改写成 `[Archive Index]`
- 当前活跃消息保持 message block 形式回放
- assistant 的 tool part 会被还原成 `toolUse`
- tool output 会被拆成独立的 `toolResult`
- 之后再做一轮 `toolUse/toolResult` 配对修复，降低 transcript 结构不稳定的风险

因此，OpenClaw 拿到的是“压缩后的历史摘要 + archive 索引 + 当前活跃消息”，而不是无限增长的原始 transcript。

### `afterTurn()` 负责什么

`afterTurn()` 的职责更窄，专门处理本轮增量写入：

- 只切出本轮新增消息，不重写整段对话
- 只保留 `user` / `assistant` 相关文本内容
- 会把 `toolUse` / `toolResult` 格式化进 capture 文本
- 会先剥掉注入过的 `<relevant-memories>` 和元数据噪音
- 最终把清洗后的增量内容追加到 OpenViking session

之后插件会读取 session 的 `pending_tokens`。当它超过 `commitTokenThreshold` 时，会触发一次 `commit(wait=false)`：

- archive 和 Phase 2 记忆抽取在服务端异步继续跑
- 当前 turn 不会因为等待抽取而阻塞
- 如果打开 `logFindRequests`，日志里能看到 task id 和后续抽取结果

### `compact()` 负责什么

`compact()` 走的是另一条更严格的同步边界：

- 它调用 `commit(wait=true)`，阻塞等待 commit 完成
- 如果有 archive 生成，会再回读 `latest_archive_overview`
- 返回新的 token 估算、latest archive id 和 summary
- 如果摘要不够精确，模型可以再调用 `ov_archive_expand` 读取某个 archive 的原始消息

所以 `afterTurn()` 更像“增量写入 + 条件触发异步提交”，而 `compact()` 才是“明确等待压缩与归档完成”的正式边界。

## 工具层与可展开能力

这套插件除了自动行为，还直接暴露了 4 个工具：

- `memory_recall`：显式检索长期记忆
- `memory_store`：把文本写入 OpenViking session 并立即触发 commit
- `memory_forget`：按 URI 删除，或先搜索再删除唯一高置信候选
- `ov_archive_expand`：展开某个 archive 的原始消息

它们各自的作用不同：

- 自动 recall 解决“模型不知道该先查什么”的默认场景。
- `memory_recall` 给模型一个显式补查入口。
- `memory_store` 适合把一段明确的重要信息立刻落入记忆管线。
- `ov_archive_expand` 负责在 summary 不够细时回到 archive 级原文。

其中 `ov_archive_expand` 是 `assemble()` 的重要补充，因为 `assemble()` 默认给的是压缩后的索引和摘要，而不是完整历史正文。

## Local / Remote 运行模式

![运行模式与路由解析](./images/openclaw-plugin-runtime-routing.png)

当前实现支持两种运行方式，但 session、memory、archive 的上层逻辑是一致的。

### Local 模式

`local` 模式下，插件自己管理 OpenViking 子进程：

- 解析 `OPENVIKING_PYTHON`、env 文件或系统默认 Python
- 启动前先做端口准备
- 若目标端口上残留旧 OpenViking，会尝试清理
- 若端口被其他进程占用，会自动寻找下一个空闲端口
- 只有 `/health` 成功后，才把服务视为可用
- 会缓存 local client，避免同一环境里重复拉起多个运行时

这也是为什么插件不仅是“查记忆的逻辑层”，还是一个本地运行时管理器。

### Remote 模式

`remote` 模式下，插件只作为 HTTP 客户端工作：

- 不会启动本地子进程
- `baseUrl` 和可选 `apiKey` 由插件配置提供
- session context、memory find/read、commit、archive expand 这些行为保持不变

换句话说，`local` 和 `remote` 的差异主要在“谁负责把 OpenViking 服务启动起来”，不在上层的上下文模型本身。

## 与旧设计稿的关系

仓库里还有一份更偏“未来演进方向”的设计稿：`docs/design/openclaw-context-engine-refactor.md`。阅读时需要区分两者的口径：

- 本文描述的是当前实现已经落地的行为。
- 旧设计稿讨论的是“进一步把更多主链路迁入 context-engine 生命周期”的目标态。
- 当前版本里，自动 recall 的主入口仍然在 `before_prompt_build`，并没有完全迁到 `assemble()`。
- 当前版本里，`afterTurn()` 已经负责增量写入 OpenViking session，但它仍然依赖阈值触发异步 commit。
- 当前版本里，`compact()` 已经走 `commit(wait=true)`，但它的职责仍以“同步提交 + 结果回读”为主，而不是承载一切上层编排。

这段区分很重要，否则很容易把未来设计误读成现状。

## 运维与调试入口

如果你要排查这套插件，优先看这几类入口：

### 查看当前配置

```bash
ov-install --current-version
openclaw config get plugins.entries.openviking.config
openclaw config get plugins.slots.contextEngine
```

### 看日志

OpenClaw 插件侧日志：

```bash
openclaw logs --follow
```

OpenViking 服务侧日志：

```bash
cat ~/.openviking/data/log/openviking.log
```

### Web Console

```bash
python -m openviking.console.bootstrap --host 0.0.0.0 --port 8020 --openviking-url http://127.0.0.1:1933
```

### `ov tui`

```bash
ov tui
```

### 常见排查点

| 现象 | 更可能的原因 | 优先检查 |
| --- | --- | --- |
| `plugins.slots.contextEngine` 不是 `openviking` | 插件槽位未设置或被其他插件覆盖 | `openclaw config get plugins.slots.contextEngine` |
| `local` 模式启动失败 | Python 路径、env 文件或 `ov.conf` 不对 | `source ~/.openclaw/openviking.env && openclaw gateway restart` |
| recall 在不同 session 间不稳定 | 路由身份和预期不一致 | 打开 `logFindRequests`，再看 `openclaw logs --follow` |
| 长对话后没有持续抽取记忆 | `pending_tokens` 未过阈值，或服务端 Phase 2 失败 | 检查插件配置和 `~/.openviking/data/log/openviking.log` |
| summary 太粗，不够回答细节问题 | 你要的是 archive 级明细，不是摘要 | 用 `[Archive Index]` 里的 ID 调用 `ov_archive_expand` |

---

安装、升级、卸载请查看 [INSTALL-ZH.md](./INSTALL-ZH.md)。
