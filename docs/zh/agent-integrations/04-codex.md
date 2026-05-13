# Codex 记忆插件

为 [Codex](https://developers.openai.com/codex) 提供长期语义记忆。每次用户输入前自动召回相关记忆，每轮对话结束后增量捕获，并在 compaction 之前提交给 OpenViking 的记忆抽取器——模型不需要主动调用任何 MCP 工具。

源码：[examples/codex-memory-plugin](https://github.com/volcengine/OpenViking/tree/main/examples/codex-memory-plugin)

## 快速开始

### 一行安装（推荐）

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/volcengine/OpenViking/main/examples/codex-memory-plugin/setup-helper/install.sh)
```

脚本会检查 `codex`、`git`、Node.js 22+；首次运行时把 OpenViking 仓库 clone 到 `~/.openviking/openviking-repo`，已存在则自动 `git fetch` + `reset --hard` 到 main；注册本地 `openviking-plugins-local` marketplace、启用 `openviking-memory@openviking-plugins-local`、把 `features.plugin_hooks = true` 写入 `~/.codex/config.toml`，并预填 Codex 的 plugin 缓存让插件立即解析到。每一步幂等，反复执行安全。

存在 `~/.openviking/ovcli.conf` 时直接读它；否则插件回落到 `http://127.0.0.1:1933`，或读取 env 中的 `OPENVIKING_URL` / `OPENVIKING_API_KEY`。

安装完成后启动 Codex：

```bash
codex
```

首次启动时 Codex 会提示 `4 hooks need review before they can run`——进入 `/hooks` 审批一次后续都不会再问。之后每次用户输入会自动召回，每轮 `Stop` 会自动捕获。

### 手动安装

前置：

```bash
node --version    # >= 22
codex --version   # >= 0.130.0
codex features list | grep codex_hooks
```

在 `~/.codex/config.toml` 启用插件生命周期 hooks：

```toml
[features]
plugin_hooks = true
```

在 OpenViking 检出目录注册一个本地 marketplace：

```bash
mkdir -p /tmp/ov-codex-mp/.claude-plugin
ln -s "$(pwd)/examples/codex-memory-plugin" /tmp/ov-codex-mp/openviking-memory
cat > /tmp/ov-codex-mp/.claude-plugin/marketplace.json <<'EOF'
{
  "name": "openviking-plugins-local",
  "plugins": [
    { "name": "openviking-memory", "source": "./openviking-memory" }
  ]
}
EOF

codex plugin marketplace add /tmp/ov-codex-mp
cat >> ~/.codex/config.toml <<'EOF'

[plugins."openviking-memory@openviking-plugins-local"]
enabled = true
EOF
```

预填 Codex 的 plugin 缓存，首次启动直接命中：

```bash
INSTALL_DIR=~/.codex/plugins/cache/openviking-plugins-local/openviking-memory
mkdir -p "$INSTALL_DIR"
cp -R "$(pwd)/examples/codex-memory-plugin" "$INSTALL_DIR/0.4.1"
```

配置 OpenViking 客户端（与 `ov` CLI 共用同一份文件）：

```jsonc
// ~/.openviking/ovcli.conf
{
  "url": "https://ov.example.com",
  "api_key": "<你的 API key>",
  "account": "default",
  "user": "<你的 user>"
}
```

只用本地服务器（`http://127.0.0.1:1933`）可以跳过这一步。

只有修改 `src/memory-server.ts` 才需要 `npm install && npm run build`——仓库里已经带了 `servers/memory-server.js`。

## 配置

每个连接 / 身份字段的优先级从高到低（环境变量永远最高）：

1. **环境变量**（`OPENVIKING_*`）
2. **`ovcli.conf`** — `~/.openviking/ovcli.conf` 或 `OPENVIKING_CLI_CONFIG_FILE`
3. **`ov.conf`** — `~/.openviking/ov.conf` 或 `OPENVIKING_CONFIG_FILE`（顶层 `server.*` + 可选的 `codex.*` 调参块）
4. **内置默认值**（`http://127.0.0.1:1933`，无鉴权）

只设 `OPENVIKING_URL` 一个变量就足以跑 env-only 模式（不需要任何配置文件），适合 daemon 派生 agent 的场景。

鉴权头同时发送 `Authorization: Bearer <api_key>`（主）和 `X-API-Key`（兼容旧版自托管服务）。

### 关键环境变量

| 环境变量 | 默认值 | 说明 |
|---------|--------|------|
| `OPENVIKING_URL` / `OPENVIKING_BASE_URL` | — | 完整服务器 URL |
| `OPENVIKING_API_KEY` / `OPENVIKING_BEARER_TOKEN` | — | API key（两个变量都通过 `Authorization: Bearer` 发送） |
| `OPENVIKING_ACCOUNT` / `OPENVIKING_USER` / `OPENVIKING_AGENT_ID` | — | 多租户身份头 |
| `OPENVIKING_CLI_CONFIG_FILE` | `~/.openviking/ovcli.conf` | 备用 `ovcli.conf` 路径 |
| `OPENVIKING_CONFIG_FILE` | `~/.openviking/ov.conf` | 备用 `ov.conf` 路径 |
| `OPENVIKING_CODEX_ACTIVE_WINDOW_MS` | `120000` | SessionStart 活动窗口阈值 |
| `OPENVIKING_CODEX_IDLE_TTL_MS` | `1800000` | SessionStart 闲置 TTL 清理阈值 |
| `OPENVIKING_DEBUG` | `false` | 把 hook 日志写到 `~/.openviking/logs/codex-hooks.log` |

可选的 Codex 专属调参放在 `ovcli.conf` 的 `codex` 块下：

```jsonc
{
  "url": "https://ov.example.com",
  "api_key": "...",
  "codex": {
    "agentId": "codex",
    "recallLimit": 6,
    "autoCommitOnCompact": true,
    "debug": false
  }
}
```

完整字段列表见 [插件 README](https://github.com/volcengine/OpenViking/blob/main/examples/codex-memory-plugin/README.md#configuration)。

## Hook 行为

| Hook | 触发时机 | 行为 |
|------|---------|------|
| `SessionStart`（matcher `clear\|startup`） | 全新进程 / `/new` / `/clear` | 活动窗口启发式：如果最近 2 分钟内恰好只有一个其他 state 文件被更新，就把它 commit 掉（视为刚刚结束的 session）。尾部的闲置 TTL 清理会捕获 30 分钟以上的孤儿 state（SIGTERM / `/exit` 等）。`source=resume` 是硬 no-op。 |
| `UserPromptSubmit` | 每次用户输入 | 搜索 OpenViking → 排序 → 把 top 结果通过 `hookSpecificOutput.additionalContext` 注入到模型上下文。 |
| `Stop` | 每轮结束 | 把新的 user/assistant turn 追加到由 Codex `session_id` 索引的长生命周期 OV session。**不**每轮 commit。 |
| `PreCompact` | Codex 即将做摘要前 | 补齐追加 + commit，让 OV 抽取器跑在完整的 pre-compact transcript 上；commit 后清空 `ovSessionId`，下一次 `Stop` 会打开一个全新的 OV session。 |

`Stop` 故意不每轮 commit——commit 会触发记忆抽取，每轮抽取会过度碎片化记忆树。完整的「哪个 hook 负责封住哪个 OV session」决策树见 [`DESIGN.md`](https://github.com/volcengine/OpenViking/blob/main/examples/codex-memory-plugin/DESIGN.md)。

MCP 服务器（`servers/memory-server.js`）由 `scripts/start-memory-server.mjs` 懒加载启动 —— 首次 MCP 调用时跑一次 `npm ci` 到 `${CODEX_PLUGIN_DATA}/runtime`，后续复用。

### 已知盲区：SIGTERM / Ctrl+C / `/exit` 不触发任何 hook

Codex 在进程退出时不发任何 hook。如果你 `/exit` 之前没跑 `/compact`，那个 codex session 对应的 OV session 会在服务端保持打开状态。两条兜底路径会回收孤儿：

1. 下一次 `SessionStart`（source=startup|clear）的闲置 TTL 清理会 commit 30 分钟以上的孤儿 state。
2. 如果你在孤儿之后立即 `/new` 或 `/clear`，活动窗口启发式会精准命中并 commit。

如果你想保住某个 session 的记忆，退出前先 `/compact`，或者用 `openviking_store` 显式记下结论。

## MCP 工具

除了自动 hook，插件还暴露四个 MCP 工具供显式调用：

- `openviking_recall` — 搜索记忆
- `openviking_store` — 创建一个短 OV session、写入内容、commit
- `openviking_forget` — 删除指定 URI 的记忆
- `openviking_health` — 服务可达性探测

## 故障排查

| 现象 | 原因 | 修复 |
|------|------|------|
| `MCP startup failed: connection closed: initialize response` | MCP 启动脚本找不到（典型成因：plugin 缓存陈旧） | 重新跑[一行安装脚本](#一行安装推荐)——它会自动刷新 `~/.openviking/openviking-repo` |
| `4 hooks need review before they can run` | 首次启动的安全审批 | 进入 Codex 输入 `/hooks` 批准一次 |
| Hook 触发但召回为空 | OpenViking 服务器不可达或 URL 不对 | `curl "$(jq -r '.url' ~/.openviking/ovcli.conf)/health"` |
| 远程鉴权 401 / 403 | API key 错或多租户头缺失 | 检查 `OPENVIKING_API_KEY`；多租户场景还要核对 `OPENVIKING_ACCOUNT` / `OPENVIKING_USER` |
| `Stop` hook 超时 | 服务器慢 + 同步写路径 | 调高 `hooks/hooks.json` 里 `Stop` 的 timeout |

调试日志：设 `OPENVIKING_DEBUG=1` 或 `ovcli.conf` 里 `codex.debug=true`，会把 JSON-Lines 事件写到 `~/.openviking/logs/codex-hooks.log`。

## 与 Claude Code 插件的差异

| 维度 | Claude Code 插件 | Codex 插件 |
|------|------------------|-----------|
| Plugin root env | `CLAUDE_PLUGIN_ROOT` | `CODEX_PLUGIN_ROOT` |
| `UserPromptSubmit` 输出 | `decision: "approve"` + `additionalContext` | 只有 `additionalContext` —— Codex 没有 `approve` 这个 decision |
| Compaction hook | 无 | `PreCompact` —— 在上下文丢失前 commit 完整 transcript |
| 配置区块 | `claude_code` | `codex` |
| 默认配置文件 | `~/.openviking/ov.conf` | `~/.openviking/ovcli.conf`，回落到 `ov.conf` |

## 参见

- [插件 README](https://github.com/volcengine/OpenViking/blob/main/examples/codex-memory-plugin/README.md) — 完整环境变量、Validation SOP、架构图
- [`DESIGN.md`](https://github.com/volcengine/OpenViking/blob/main/examples/codex-memory-plugin/DESIGN.md) — commit 决策树
- [MCP 集成指南](../guides/06-mcp-integration.md) — 用于不支持生命周期 hook 的客户端
- [部署指南 → CLI](../guides/03-deployment.md#cli) — `ovcli.conf` 配置
