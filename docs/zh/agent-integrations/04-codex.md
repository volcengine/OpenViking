# Codex 记忆插件

为 [Codex](https://developers.openai.com/codex) 提供长期语义记忆。每次用户输入前自动召回相关记忆，每轮对话结束后增量捕获，compaction 前提交给 OpenViking 的记忆抽取器；同时把 Codex 直接接到 OpenViking 自带的 `/mcp` endpoint，模型可以直接调用 `search` / `store` / `read` / `grep` / `glob` / `list` / `forget` / `add_resource` 等工具——**没有本地 MCP server 进程要维护**。

源码：[examples/codex-memory-plugin](https://github.com/volcengine/OpenViking/tree/main/examples/codex-memory-plugin)

## 快速开始

### 一行安装（推荐）

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/volcengine/OpenViking/main/examples/codex-memory-plugin/setup-helper/install.sh)
```

脚本会检查 `codex`、`git`、Node.js 22+；首次运行时把 OpenViking 仓库 clone 到 `~/.openviking/openviking-repo`，已存在则自动 `git fetch + reset --hard` 到 main；注册本地 `openviking-plugins-local` marketplace、启用 `openviking-memory@openviking-plugins-local`、把 `features.plugin_hooks = true` 写入 `~/.codex/config.toml`，并预填 Codex 的 plugin 缓存让插件立即解析到。每一步幂等，反复执行安全。

存在 `~/.openviking/ovcli.conf` 时直接读它，把 `/mcp` URL 渲染进缓存里的 `.mcp.json`；同时往你的 shell rc 追加一个 `codex()` 函数包装，每次调用 codex 时从 ovcli.conf 把 `OPENVIKING_API_KEY` / `OPENVIKING_ACCOUNT` / `OPENVIKING_USER` / `OPENVIKING_AGENT_ID` 注入到环境变量。API key 只留在 `ovcli.conf` 里，**`.mcp.json` 磁盘文件里只通过 `bearer_token_env_var` 引用变量名，永远不会包含 key 明文**。

安装完成后启动 Codex：

```bash
source ~/.zshrc    # 或 ~/.bashrc
codex              # 首次启动进 /hooks 审批一次
```

### 手动安装

前置：

```bash
node --version    # >= 22
codex --version   # >= 0.130.0
codex features list | grep codex_hooks
```

installer 替你做的三件事，你也可以自己手动做：

1. **shell 函数包装**追加到 `~/.zshrc` / `~/.bashrc`。installer 实际生成的版本（见 `setup-helper/install.sh`）还会在每次 codex 启动时**重新渲染缓存里的 `.mcp.json` bearer 字段** —— 这是切 `OPENVIKING_CLI_CONFIG_FILE`（有 / 无 key 来回换）所必需的。下面是固定 conf 场景的简化版：

   ```bash
   codex() {
     local _ov_conf="${OPENVIKING_CLI_CONFIG_FILE:-$HOME/.openviking/ovcli.conf}"
     local _ov_url _ov_key _ov_account _ov_user
     if [ -f "$_ov_conf" ] && command -v node >/dev/null 2>&1; then
       local _ov_env
       _ov_env=$(node -e '
         try {
           const c = JSON.parse(require("node:fs").readFileSync(process.argv[1], "utf8"));
           const out = (k, v) => v ? `${k}=${JSON.stringify(String(v))}\n` : "";
           process.stdout.write(
             out("OV_URL", c.url) +
             out("OV_KEY", c.api_key) +
             out("OV_ACCOUNT", c.account) +
             out("OV_USER", c.user)
           );
         } catch {}
       ' "$_ov_conf" 2>/dev/null)
       eval "$_ov_env"
     fi
     _ov_url="${OPENVIKING_URL:-${OV_URL:-}}"
     _ov_key="${OPENVIKING_API_KEY:-${OV_KEY:-}}"
     _ov_account="${OPENVIKING_ACCOUNT:-${OV_ACCOUNT:-}}"
     _ov_user="${OPENVIKING_USER:-${OV_USER:-}}"
     unset OV_URL OV_KEY OV_ACCOUNT OV_USER
     # 空值不导出 —— Codex 看到 bearer_token_env_var 指向空 env var 会硬错。
     local -a _env_args=()
     [ -n "$_ov_url" ]     && _env_args+=("OPENVIKING_URL=$_ov_url")
     [ -n "$_ov_key" ]     && _env_args+=("OPENVIKING_API_KEY=$_ov_key")
     [ -n "$_ov_account" ] && _env_args+=("OPENVIKING_ACCOUNT=$_ov_account")
     [ -n "$_ov_user" ]    && _env_args+=("OPENVIKING_USER=$_ov_user")
     _env_args+=("OPENVIKING_AGENT_ID=${OPENVIKING_AGENT_ID:-codex}")
     env "${_env_args[@]}" codex "$@"
   }
   ```

2. **插件安装**——通过指向插件目录的本地 marketplace。`setup-helper/install.sh` 里有完整的 `codex plugin marketplace add` 调用。

3. **占位符渲染**——仓库里 checked-in 的 `.mcp.json` 保留 `__OPENVIKING_MCP_URL__`，`hooks/hooks.json` 保留 `__OPENVIKING_PLUGIN_ROOT__`；这两个占位符必须在拷贝到 Codex 缓存目录 (`~/.codex/plugins/cache/...`) 时被 `sed` 替换成绝对值。installer 自动做。

## 配置

每个连接 / 身份字段的优先级从高到低（环境变量永远最高）：

1. **环境变量**（`OPENVIKING_*`）
2. **`ovcli.conf`** — `~/.openviking/ovcli.conf` 或 `OPENVIKING_CLI_CONFIG_FILE`
3. **`ov.conf`** — `~/.openviking/ov.conf` 或 `OPENVIKING_CONFIG_FILE`（只用 `server.url` / `server.root_api_key` 当连接 fallback；`codex.*` 调参块仍被读取但已废弃，见下面 [调参](#调参)）
4. **内置默认值**（`http://127.0.0.1:1933`，无鉴权）

Hook 每次触发都重新解析这条优先级链——改完 ovcli.conf 下一次 hook 立即生效。MCP server URL 在 install 时固化进 `.mcp.json`（改 URL 要重跑 installer），但 API key 通过 `bearer_token_env_var` 在 codex 启动时从 env 读，所以**轮换 API key 只需重启 codex，不必重装**。

鉴权头同时发给 REST API（hook 用）和 `/mcp` endpoint（模型用）：`Authorization: Bearer <api_key>`。

对于**无鉴权的本地 OV**（`ovcli.conf` 没有 `api_key`，或者根本没 ovcli.conf），`.mcp.json` 渲染时**不会**写入 `bearer_token_env_var`。Codex 0.130 一旦看到 `bearer_token_env_var` 指向空/未设置的 env var，会直接 `Environment variable ... is empty` 硬错。`codex()` shell 函数包装会在**每次 codex 启动时**根据当前 `OPENVIKING_CLI_CONFIG_FILE` 指向的 ovcli.conf 重新渲染这个字段，所以切换 conf（比如 benchmark 隔离）**不用重跑 installer**。多租户身份头通过 `env_http_headers` 始终传。

### 关键环境变量

| 环境变量 | 默认值 | 说明 |
|---------|--------|------|
| `OPENVIKING_URL` / `OPENVIKING_BASE_URL` | — | 完整服务器 URL（`/mcp` endpoint 在 install 时由此推导） |
| `OPENVIKING_API_KEY` / `OPENVIKING_BEARER_TOKEN` | — | API key，通过 `Authorization: Bearer` 发送 |
| `OPENVIKING_ACCOUNT` / `OPENVIKING_USER` / `OPENVIKING_AGENT_ID` | — | 多租户身份头 |
| `OPENVIKING_CLI_CONFIG_FILE` | `~/.openviking/ovcli.conf` | 备用 `ovcli.conf` 路径 |
| `OPENVIKING_CONFIG_FILE` | `~/.openviking/ov.conf` | 备用 `ov.conf` 路径 |
| `OPENVIKING_CODEX_ACTIVE_WINDOW_MS` | `120000` | SessionStart 活动窗口阈值 |
| `OPENVIKING_CODEX_IDLE_TTL_MS` | `1800000` | SessionStart 闲置 TTL 清理阈值 |
| `OPENVIKING_DEBUG` | `false` | 把 hook 日志写到 `~/.openviking/logs/codex-hooks.log` |

### 调参

调参用 `OPENVIKING_*` 环境变量，写进 shell rc 即可。每次 codex 启动都生效。

```sh
# ~/.zshrc
export OPENVIKING_RECALL_LIMIT=6
export OPENVIKING_CAPTURE_ASSISTANT_TURNS=1
export OPENVIKING_AUTO_COMMIT_ON_COMPACT=1
export OPENVIKING_DEBUG=1
```

完整字段列表见 [插件 README](https://github.com/volcengine/OpenViking/blob/main/examples/codex-memory-plugin/README.md#tuning-the-plugin)。

> **遗留 `codex.*` 块**：早期版本支持把调参字段放在 `~/.openviking/ov.conf` 的 `codex` 块下，仍向后兼容。但 codex 是 client 侧插件，per-machine 的调参不该住在 server-scope 的 `ov.conf` 里——新部署请用环境变量。

## Hook 行为

| Hook | 触发时机 | 行为 |
|------|---------|------|
| `SessionStart`（matcher `clear\|startup`） | 全新进程 / `/new` / `/clear` | 活动窗口启发式：如果最近 2 分钟内恰好只有一个其他 state 文件被更新，就把它 commit 掉（视为刚刚结束的 session）。尾部的闲置 TTL 清理会捕获 30 分钟以上的孤儿 state（SIGTERM / `/exit` 等）。`source=resume` 是硬 no-op。 |
| `UserPromptSubmit` | 每次用户输入 | 走 REST `/search/find` 搜索 OpenViking → 排序 → 把 top 结果通过 `hookSpecificOutput.additionalContext` 注入到模型上下文。 |
| `Stop` | 每轮结束 | 把新的 user/assistant turn 追加到由 Codex `session_id` 索引的长生命周期 OV session。**不**每轮 commit。 |
| `PreCompact` | Codex 即将做摘要前 | 补齐追加 + commit，让 OV 抽取器跑在完整的 pre-compact transcript 上；commit 后清空 `ovSessionId`，下一次 `Stop` 会打开一个全新的 OV session。 |

`Stop` 故意不每轮 commit——commit 会触发记忆抽取，每轮抽取会过度碎片化记忆树。完整的「哪个 hook 负责封住哪个 OV session」决策树见 [`DESIGN.md`](https://github.com/volcengine/OpenViking/blob/main/examples/codex-memory-plugin/DESIGN.md)。

### 已知盲区：SIGTERM / Ctrl+C / `/exit` 不触发任何 hook

如果你 `/exit` 之前没跑 `/compact`，那个 codex session 对应的 OV session 会在服务端保持打开状态。两条兜底路径会回收孤儿：

1. 下一次 `SessionStart`（source=startup|clear）的闲置 TTL 清理会 commit 30 分钟以上的孤儿 state。
2. 如果你在孤儿之后立即 `/new` 或 `/clear`，活动窗口启发式会精准命中并 commit。

## MCP 工具

插件通过 streamable HTTP 把 Codex 接到 OpenViking 自带的 `/mcp` endpoint。工具列表、每个工具的语义、协议细节统一见 [MCP 集成指南](../guides/06-mcp-integration.md)，这里不重复。

`.mcp.json` 在 install 时写入 OV server URL，用 `bearer_token_env_var: "OPENVIKING_API_KEY"` + `env_http_headers` 传多租户身份头。**API key 永远不会落到 `.mcp.json` 文件里**，是 codex 启动时由 shell 函数包装从 env 取。

## 故障排查

| 现象 | 原因 | 修复 |
|------|------|------|
| `MCP server is not logged in. Run codex mcp login` | codex 启动时 `OPENVIKING_API_KEY` 不在 env 里，OV 返回 401，Codex 回落到 OAuth | 确认 `codex()` shell 函数已 source（`type codex` 应该返回"shell function"）、且 `ovcli.conf` 里有 `api_key` |
| `4 hooks need review before they can run` | 首次启动的安全审批 | 进入 Codex 输入 `/hooks` 批准 |
| 审批后还是 `hook (failed) exited with code 1` | `hooks.json` 占位符没渲染，cache 是旧的 | 重新跑一次一行安装脚本 |
| Hook 触发但召回为空 | OpenViking 服务器不可达或 URL 不对 | `curl "$(jq -r '.url' ~/.openviking/ovcli.conf)/health"` |
| Hook 401/403 但 MCP 工具可用，或反之 | env vs ovcli.conf 不一致 | Hook 每次都重读 ovcli.conf，MCP 只在 codex 启动读 env。改完 env 要重启 codex。 |

调试日志：设 `OPENVIKING_DEBUG=1`（或老配置 `ov.conf` 里 `codex.debug=true`），会把 JSON-Lines 事件写到 `~/.openviking/logs/codex-hooks.log`。

## 与 Claude Code 插件的差异

| 维度 | Claude Code 插件 | Codex 插件 |
|------|------------------|-----------|
| Plugin root env | `CLAUDE_PLUGIN_ROOT`（CC 会展开） | `CODEX_PLUGIN_ROOT`（Codex 0.130 **不展开**；installer 渲染成绝对路径） |
| `UserPromptSubmit` 输出 | `decision: "approve"` + `additionalContext` | 只有 `additionalContext` —— Codex 没有 `approve` 这个 decision |
| Compaction hook | 无 | `PreCompact` —— 在上下文丢失前 commit 完整 transcript |
| 配置区块 | `claude_code` | `codex` |
| 默认配置文件 | `~/.openviking/ov.conf` | `~/.openviking/ovcli.conf`，回落到 `ov.conf` |
| MCP server | 本地 stdio（CC `.mcp.json` 不支持 env-var Bearer） | streamable-HTTP，直连 OpenViking 自带 `/mcp` |

## 参见

- [插件 README](https://github.com/volcengine/OpenViking/blob/main/examples/codex-memory-plugin/README.md) — 完整环境变量、Validation SOP、架构图
- [`DESIGN.md`](https://github.com/volcengine/OpenViking/blob/main/examples/codex-memory-plugin/DESIGN.md) — commit 决策树
- [MCP 集成指南](../guides/06-mcp-integration.md) — 协议、工具列表、OpenViking 如何暴露 `/mcp`
- [部署指南 → CLI](../guides/03-deployment.md#cli) — `ovcli.conf` 配置
