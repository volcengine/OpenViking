# 导入本地 Agent 日志（openviking-server ingest）

`openviking-server ingest` 把你本地已有的 AI 编码 / agent harness 的对话日志（Claude Code、Codex、OpenCode、Hermes、OpenClaw）解析成标准消息，再通过 OpenViking 既有的会话管线“重放”进去（`创建会话 → 批量追加消息 → 提交`，提交时触发记忆抽取），从而把这些历史与新增对话沉淀为长期记忆。它与各 harness 的“记忆插件”互补：插件在对话**进行时**实时挂载捕获，而本工具用于**导入既有日志**与**离线监听新增日志**，无需插件、也无需改动对应 harness。

与插件方案的关键区别：本工具是 OpenViking 的**客户端**，跑在日志所在的机器上，通过 SDK 指向本地或远端 server；它默认**完全关闭**，不会“装上就扫你本地文件”。

源码：[openviking/ingest](https://github.com/volcengine/OpenViking/tree/main/openviking/ingest)

## 默认关闭

该特性默认双重关闭，必须显式开启：

- 总开关 `ingest.enabled` 默认 `false`；
- 每个 harness 的 `enabled` 默认 `false`，未列出的 harness 不会被读取；
- 存量回填需手动运行命令，且支持 `--dry-run`（只统计、不写入）与 `--since`（限定时间窗）先行验证。

## 支持的 harness

| harness | 状态 | 默认日志路径 | 说明 |
|---|---|---|---|
| `claude_code` | 支持 | `~/.claude/projects/*/*.jsonl` | append-only JSONL，字节偏移游标 |
| `codex` | 支持 | `~/.codex/sessions/YYYY/MM/DD/rollout-*.jsonl` | append-only JSONL |
| `hermes` | 支持 | `~/.hermes/sessions/*.jsonl` | 群聊 agent，user 取原始用户名 |
| `openclaw` | 支持 | `~/.openclaw/agents/*/sessions/*.jsonl` | 群聊 agent，user 取原始用户名 |
| `opencode` | 实验性 | `~/.local/share/opencode/opencode.db` | SQLite，按 `(time, id)` 轮询；旧版文件存储暂不支持 |
| `cursor` | 暂缓 | `~/Library/Application Support/Cursor/User/**/state.vscdb` | 无文档、随版本漂移的 KV blob，暂未实现 |

> 这里的 harness（agent 框架）指 CC / Codex 等整套工具，区别于 OpenViking 里“tool（工具调用）”的概念。

## 在 ov.conf 中开启

在 `ov.conf` 增加 `ingest` 段，列出要导入的 harness 并设置其模式：

```json
{
  "ingest": {
    "enabled": true,
    "server_url": "$OPENVIKING_URL",
    "api_key": "$OPENVIKING_API_KEY",
    "account": "default",
    "user": "default",
    "harnesses": {
      "claude_code": { "enabled": true, "mode": "both" },
      "codex":       { "enabled": true, "mode": "backfill" },
      "opencode":    { "enabled": false, "mode": "watch", "experimental": true },
      "hermes":      { "enabled": false, "mode": "both", "user_field": "sender" },
      "openclaw":    { "enabled": false, "mode": "both", "user_field": "sender" }
    }
  }
}
```

- `mode`：`off` | `backfill`（一次性导入存量）| `watch`（监听新增）| `both`。
- `paths`：覆盖该 harness 的默认发现路径（可填多个）。
- `user_field`：群聊 harness 中存放原始用户名的字段名，用作 user 侧 peer_id。
- `commit`：提交策略，含 `commit_token_threshold`、`commit_idle_seconds`、`keep_recent_count`。
- 部署期开关也可用环境变量覆盖：`OPENVIKING_INGEST_ENABLED`、`OPENVIKING_INGEST_SERVER_URL`、`OPENVIKING_INGEST_API_KEY`。

`server_url` 留空时回退到 `OPENVIKING_URL` 或 `http://localhost:1933`，因此既能指向本地 server，也能指向远端。

## 使用

`openviking-server ingest` 命令随 OpenViking 一同安装。

```bash
# 查看已注册 harness 及其配置
openviking-server ingest list-sources

# 先干跑：统计会回填多少 session / 消息，不写入
openviking-server ingest backfill --dry-run

# 只回填某个 harness、且只回填某日期之后的会话
openviking-server ingest backfill --harness claude_code --since 2026-06-01

# 正式回填（存量）
openviking-server ingest backfill

# 监听新增日志并增量重放（前台阻塞）
openviking-server ingest watch --harness claude_code

# 按每个 harness 配置的 mode 执行：先回填再监听
openviking-server ingest run

# 查看各会话已导入到哪里（读取游标状态）
openviking-server ingest status
```

`--reset` 会在重放前删除并重建对应的 OV 会话；不加 `--reset` 时，重复运行是幂等的（游标保证不会重复追加）。

## peer_id

每条消息都会带上 peer_id，便于 OpenViking 同时为人类与模型建立画像：

- assistant 消息：`{harness}/{模型名}`（provider 有意义时为 `{harness}/{provider}/{模型名}`），例如 `claude_code/claude-opus-4-8`、`opencode/bytedance_ark/doubao-...`；
- user 消息：单用户开发型 harness（claude_code / codex / opencode）取会话 cwd 所在仓库的 git 身份（`user.email` / `user.name`），无 git 仓库时回退为配置的 `ingest.user`；群聊 harness（hermes / openclaw）取日志里的原始用户名（由 `user_field` 指定）。

非 ASCII 标识（如中文用户名）会回退为合法的 `ext-<base64>` 形式。

## 工作原理

每个 harness 对应一个轻量适配器，把其日志解析为标准消息，交给“重放器”执行 `ensure_session → 批量追加（每批 ≤100）→ commit`。记忆抽取只在 **commit** 时由 server 端触发。OV 会话 id 形如 `import__{harness}__{原始会话id}`，确定且幂等。

- **存量回填**：枚举所有会话，从游标读到末尾后逐会话提交一次。
- **监听增量**：参照 OpenViking 自身的 `WatchScheduler`，用**定时轮询**（非文件系统事件）+ 持久游标驱动；漏一拍、休眠或重启后，下一拍从游标读到末尾即可自愈。JSONL 用字节偏移游标（含半行/截断/轮转处理），SQLite 用 `(time, id)` 游标只读读取（兼容 WAL）。

游标状态持久化在 `~/.openviking/ingest/state.db`，因此回填与监听都能在重启后续传，且不会重复入库。

## 成本与隐私

- 提交会触发记忆抽取（LLM 调用）。一次性回填数月历史可能产生大量调用，建议先 `--dry-run`、用 `--since` 收窄时间窗、按 harness 分批开启。
- 日志中可能含敏感内容（凭据、文件内容）。请在受信任的部署中使用，并确认 `server_url` 指向你期望的 server。
- tool 调用的输入/输出默认按低价值丢弃，仅入库 user / assistant 文本。

## 参见

- [概览](./01-overview.md) — 各 harness 的记忆插件（实时捕获方案）
- [部署指南 → CLI](../guides/03-deployment.md#cli) — `ov.conf` / 凭据配置
