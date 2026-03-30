# OpenClaw + OpenViking 上下文引擎插件

使用 [OpenViking](https://github.com/volcengine/OpenViking) 作为 [OpenClaw](https://github.com/openclaw/openclaw) 的长期记忆后端。在 OpenClaw 中，此插件注册为 `openviking` 上下文引擎。

## 文档入口

- 安装与升级：[INSTALL-ZH.md](./INSTALL-ZH.md)
- English install guide: [INSTALL.md](./INSTALL.md)
- Agent 专用操作文档：[INSTALL-AGENT.md](./INSTALL-AGENT.md)

## 技术架构

### 插件承担了什么职责

这个插件不只是“查记忆”的一层封装。按代码职责看，它同时扮演四个角色：

- `context-engine` 插件：实现 `assemble`、`afterTurn`、`compact`
- Hook 层：接管 `before_prompt_build`、`session_start`、`session_end`、`agent_end`、`before_reset`
- Tool 提供者：注册 `memory_recall`、`memory_store`、`memory_forget`、`ov_archive_expand`
- 运行时管理器：在 `local` 模式下负责拉起并监控 OpenViking 子进程

OpenClaw 仍然负责 agent 运行时和 prompt 编排，OpenViking 负责长期记忆检索、会话归档和记忆抽取。

### 身份与路由

插件并不是把所有请求都打到一个固定的 agent ID 上，而是尽量保持 OpenClaw 会话身份和 OpenViking 路由一致：

- `sessionId` 或 `sessionKey` 会被映射成 OpenViking 可接受的 session ID
- UUID 会直接复用；不安全的 ID 会退化成稳定的 SHA-256
- `X-OpenViking-Agent` 是按会话解析的，不是按进程写死的
- 如果 `plugins.entries.openviking.config.agentId` 不是 `default`，它会作为前缀，形成 `<configAgentId>_<sessionAgent>`
- 实际 HTTP 请求由 client 层补全 `X-OpenViking-*` 头，打开 `logFindRequests` 后可以看到详细路由日志

这样做的目的，是支持多 agent、多 session 场景下的记忆隔离，避免不同会话串到一起。

### Session 生命周期

Session 是这套设计的核心，不只是“保留一段历史”这么简单。

1. OpenClaw 会话标识先被映射成 OpenViking session ID。
2. `assemble()` 按 token budget 向 OpenViking 拉取会话上下文。
3. 归档摘要会被改写成 `[Session History Summary]` 和 `[Archive Index]`。
4. 当前活跃消息会重新转换回 OpenClaw 可消费的消息格式。
5. tool call / tool result 会做一次修复，保证 transcript 对各模型提供方更稳。
6. `afterTurn()` 只提取当前这一轮新增内容，清洗后追加进 OpenViking session。
7. 当 `pending_tokens` 超过 `commitTokenThreshold`，插件会提交 session，后端异步跑 Phase 2 记忆抽取。
8. `compact()` 会阻塞等待归档完成，再回读压缩后的上下文。

因此，OpenClaw 侧拿到的是适合推理的精简上下文，OpenViking 侧保存的是长会话归档和抽取后的长期记忆。

### `assemble()` 实际组装了什么

这里并不是简单地“把旧聊天记录塞回来”。

- archive overview 会变成压缩后的 session summary
- 历史 archive 会变成有顺序的 archive index
- 当前活跃消息保持未压缩状态
- 如果摘要不够精确，模型可以调用 `ov_archive_expand` 打开某个 archive 的原始消息

这也是插件能撑长会话的原因：它不会把整段原始 transcript 永远重复喂回 OpenClaw。

### 记忆召回与写入链路

每一轮对话前后，其实有两条记忆链路。

生成前：

- `before_prompt_build` 提取最后一条用户文本
- 同时检索 `viking://user/memories` 和 `viking://agent/memories`
- 结果会先去重，再重排，再按 token budget 截断
- 最终以 `<relevant-memories>` 的形式注入上下文

生成后：

- `afterTurn` 把本轮新增内容格式化成可写入文本
- assistant 文本、`toolUse`、`toolResult` 都会保留
- 注入过的记忆块和元数据噪音会先被剥掉
- OpenViking session commit 会触发 archive + memory extraction

这里的重排不是纯看向量分数。代码里还会额外提升 preference、event、leaf memory 以及 query 词面重合度。

### Transcript Ingest Assist

插件对“用户贴了一段多说话人转录文本”这种场景也单独做了处理。

- 通过说话人数阈值和文本长度判断是否像 transcript
- 命令文本、元数据块、纯提问型文本会被排除
- 一旦判断为 transcript-like ingest，会额外注入一段轻量提示，减少模型直接返回 `NO_REPLY`

这主要服务于“把聊天记录、会议记录、对话转录灌进记忆系统”这类使用方式。

### Local 和 Remote 运行模式

#### Local 模式

`local` 模式下，插件自己拉起 OpenViking。

- 会从 `OPENVIKING_PYTHON`、env 文件或系统默认值解析 Python
- 启动前先处理端口
- 如果端口上残留旧 OpenViking，会主动清理
- 如果端口被别的进程占用，会自动找下一个空闲端口
- 只有 `/health` 检查通过后，才认为服务可用
- 会缓存 local client，避免多个 OpenClaw 插件上下文重复拉起运行时

#### Remote 模式

`remote` 模式下，插件只走 HTTP API。

- 不会启动本地子进程
- `baseUrl` 和可选的 `apiKey` 来自 OpenClaw 插件配置
- session、召回、archive、tool 的整体逻辑保持不变

### 工具与运维入口

除了自动记忆行为，插件还直接暴露了几类工具：

- `memory_recall`：显式检索长期记忆
- `memory_store`：写入文本到 OpenViking session 并立即触发抽取
- `memory_forget`：按 URI 删除，或先搜索再删除唯一高置信候选
- `ov_archive_expand`：当摘要不够时，展开某个压缩 archive 的原始消息

对运维和调试来说，常用入口有：

- Web Console：看 OpenViking 文件和记忆状态
- `ov tui`：在终端里浏览本地数据
- `ov-install --current-version`：查看插件版本和 OpenViking 版本
- `openclaw config get plugins.entries.openviking.config`：查看当前插件配置

## 工具

### Web Console

OpenViking 自带 Web Console，可用于查看存储文件、调试写入和观察记忆状态。

示例启动命令：

```bash
python -m openviking.console.bootstrap --host 0.0.0.0 --port 8020 --openviking-url http://127.0.0.1:1933
```

### `ov tui`

可以用终端界面浏览本地 OpenViking 文件：

```bash
ov tui
```

### 查看版本

查看当前已安装的插件版本和 OpenViking 版本：

```bash
ov-install --current-version
```

### 查看插件配置

查看当前 OpenClaw 插件整体配置：

```bash
openclaw config get plugins.entries.openviking.config
```

### 日志

OpenClaw 插件侧日志：

```bash
openclaw logs --follow
```

OpenViking 服务侧日志，默认本地路径：

```bash
cat ~/.openviking/data/log/openviking.log
```

## 故障排查

| 现象 | 常见原因 | 排查方式 |
| --- | --- | --- |
| `plugins.slots.contextEngine` 不是 `openviking` | 插件槽位未设置，或被其它插件占用 | `openclaw config get plugins.slots.contextEngine` |
| `local` 模式启动异常 | Python 路径、env 文件或 `ov.conf` 有问题 | `source ~/.openclaw/openviking.env && openclaw gateway restart` |
| `port occupied` | 本地 OpenViking 端口已被占用 | 修改 `plugins.entries.openviking.config.port` 或释放端口 |
| 不同 session 的 recall 表现不稳定 | agent/session 路由和预期不一致 | 打开 `logFindRequests`，再看 `openclaw logs --follow` |
| 长对话后没有继续写入记忆 | `pending_tokens` 没达到阈值，或服务端抽取失败 | 检查插件配置、OpenClaw 日志和 `~/.openviking/data/log/openviking.log` |
| session summary 过于模糊 | 你需要 archive 级别细节，不是只看摘要 | 用 `[Archive Index]` 里的 ID 调用 `ov_archive_expand` |
| 当前版本和预期不一致 | 插件版本和 OpenViking 运行时版本未对齐 | 运行 `ov-install --current-version` |

## 使用教程

### 查看当前整体状态

建议一起执行：

```bash
ov-install --current-version
openclaw config get plugins.entries.openviking.config
openclaw config get plugins.slots.contextEngine
```

### 观察召回和写入链路

先看 OpenClaw 侧日志：

```bash
openclaw logs --follow
```

再看 OpenViking 侧日志：

```bash
cat ~/.openviking/data/log/openviking.log
```

这是判断 session commit、archive 生成、记忆召回和记忆抽取是否正常的最快方式。

### 切换到 Remote 模式

如果已经有远端 OpenViking 服务：

```bash
openclaw config set plugins.entries.openviking.config.mode remote
openclaw config set plugins.entries.openviking.config.baseUrl http://your-server:1933
openclaw config set plugins.entries.openviking.config.apiKey your-api-key
openclaw config set plugins.entries.openviking.config.agentId your-agent-id
openclaw gateway restart
```

### 查看压缩会话的原始细节

当模型只拿到了摘要，但你需要更精确的命令、路径或代码内容时：

```text
使用 [Archive Index] 里的 archive ID 调用 ov_archive_expand
```

### 浏览记忆内容

可以使用本地终端界面：

```bash
ov tui
```

如果希望用浏览器查看，也可以使用 Web Console。

---

安装、升级、卸载请查看 [INSTALL-ZH.md](./INSTALL-ZH.md)。
