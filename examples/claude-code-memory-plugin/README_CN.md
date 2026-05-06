# OpenViking Memory Plugin for Claude Code

为 Claude Code 提供长期语义记忆，由 [OpenViking](https://github.com/volcengine/OpenViking) 驱动。每次用户输入前自动召回相关记忆，每轮对话结束后自动捕获上下文——模型不需要主动调用任何 MCP 工具。

> 公开的 Claude Code 插件 marketplace 正在规划，暂未上线。当前请从本地源码安装（见下文）。

## 快速开始

### 一行安装（推荐）

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/volcengine/OpenViking/main/examples/claude-code-memory-plugin/setup-helper/install.sh)
```

仅支持 macOS 和 Linux。脚本会校验依赖、询问你接入**自托管**服务器还是**火山引擎 OpenViking Cloud**（`https://api.vikingdb.cn-beijing.volces.com/openviking`）、按需配置 `~/.openviking/ovcli.conf`（已存在则复用）、把仓库 clone 到 `~/.openviking/openviking-repo`、把 `claude` shell function 包装写进 rc，最后跑 `claude plugin install`。重复执行安全。

如果你更喜欢手动操作，按下面四步走。

### 手动安装

#### 1. 准备一个可用的 OpenViking 服务器

本地起一个或者指向远程：[快速开始指南](../../docs/zh/getting-started/02-quickstart.md) 涵盖两种模式，也讲了远程使用时怎么签发 API key。默认端口 `1933`；本地模式无需鉴权。

验证服务能通：

```bash
curl http://localhost:1933/health   # 或者你的远程 URL
```

#### 2. 告诉插件服务器在哪

最简单的方式——写 `~/.openviking/ovcli.conf`（也是 `ov` CLI 用的同一个文件）：

```json
{
  "url": "https://your-openviking-server.example.com",
  "api_key": "<your-api-key>",
  "account": "my-team",
  "user": "alice",
  "agent_id": "claude-code"
}
```

如果是纯本地模式（`http://127.0.0.1:1933`，无鉴权），这一步可以跳过——插件会静默走本地默认值。

如果你已经在维护 `ov.conf`，插件也读它——完整优先级链和按字段覆盖见下方 [配置](#配置)。

#### 3. 安装插件

仓库的 `examples/.claude-plugin/marketplace.json` 把本插件暴露为一个本地 marketplace 条目。在 OpenViking 仓库根目录：

```bash
claude plugin marketplace add "$(pwd)/examples"
claude plugin install claude-code-memory-plugin@openviking-plugins-local
```

> 两条命令默认都装在 user scope —— 插件在任何目录下都生效。这里**不显式传 `--scope user`**，因为老的 Claude Code 2.0.x（比如 2.0.76）不识别这个 flag 会直接报错。在支持 `--scope` 的新版本上，如果装完发现落到了 local scope，可以跑一次 `claude plugin enable claude-code-memory-plugin@openviking-plugins-local --scope user` 提升到 user scope。
>
> marketplace 条目让 Claude Code 直接引用源码目录,对 `scripts/`、`hooks/`、配置文件的修改下次 hook 触发即生效,无需重装。但移动 / 重命名 / 删除源码目录,或 `git checkout` 到不含这些文件的分支,会立刻让插件失效。后续会发布公开 marketplace 以支持一键安装。

##### 兼容模式（Claude Code < 2.0）

`claude plugin` 子命令是 Claude Code 2.0（2025-10）才引入的。再老的版本只有 `claude mcp add` 和 hooks 系统，但仍然能手动接出同样的功能：

```bash
PLUGIN_DIR="$(pwd)/examples/claude-code-memory-plugin"

claude mcp remove openviking -s user 2>/dev/null
claude mcp add --scope user --transport http openviking \
  '${OPENVIKING_URL:-http://127.0.0.1:1933}/mcp' \
  --header 'Authorization: Bearer ${OPENVIKING_API_KEY:-}' \
  --header 'X-OpenViking-Account: ${OPENVIKING_ACCOUNT:-}' \
  --header 'X-OpenViking-User: ${OPENVIKING_USER:-}' \
  --header 'X-OpenViking-Agent: ${OPENVIKING_AGENT_ID:-}'

# 把插件 hooks 合并进 ~/.claude/settings.json（自动备份）
mkdir -p ~/.claude && [ -f ~/.claude/settings.json ] || echo '{}' > ~/.claude/settings.json
cp -p ~/.claude/settings.json ~/.claude/settings.json.bak.$(date +%s)
sed "s|\${CLAUDE_PLUGIN_ROOT}|$PLUGIN_DIR|g" "$PLUGIN_DIR/hooks/hooks.json" > /tmp/ov-hooks.json
jq --slurpfile h /tmp/ov-hooks.json '.hooks = ((.hooks // {}) * $h[0].hooks)' \
  ~/.claude/settings.json > /tmp/ov-settings.json
jq -e . /tmp/ov-settings.json >/dev/null && mv /tmp/ov-settings.json ~/.claude/settings.json
rm -f /tmp/ov-hooks.json
```

`claude mcp add` 的所有参数**必须用单引号**保留 `${VAR}` 字面量 —— Claude Code 在启动 MCP server 时才展开它们，用的就是 shell wrapper 注入的环境变量。换成双引号 shell 会提前把它们展开成空串，配置就废了。一键安装脚本会自动做这一切，并在改 `~/.claude/settings.json` 前提示你。

#### 4. 启动 Claude Code

```bash
claude
```

如果插件似乎没在工作，开 `OPENVIKING_DEBUG=1` 看 `~/.openviking/logs/cc-hooks.log`。

## 配置 MCP

插件的 hook 会自动读 `ovcli.conf` / `ov.conf`，但**自带的 MCP 服务器条目不会**——Claude Code 自己解析 `.mcp.json` 且只支持 `${VAR}` 替换，所以插件无法把配置文件里的值透明地注入 MCP URL 和认证头。

**判断树——你需要做点什么吗？**

```
你的 OpenViking 服务器在哪？
├─ 本地 (127.0.0.1, 无鉴权)
│    └─ ✅ 什么都不用做——自带 .mcp.json 已经能跑。
└─ 远程
     └─ ✅ 在 shell rc 里加下面这段 function 包装。
```

**推荐路径——用 function 包装 `claude`，调用时从 `ovcli.conf` 注入 env：**

```bash
# ~/.zshrc 或 ~/.bashrc
claude() {
  local _ov_conf="${OPENVIKING_CLI_CONFIG_FILE:-$HOME/.openviking/ovcli.conf}"
  if [ -f "$_ov_conf" ] && command -v jq >/dev/null 2>&1; then
    local _ov_url _ov_key
    _ov_url=$(jq -r '.url // empty'     "$_ov_conf" 2>/dev/null)
    _ov_key=$(jq -r '.api_key // empty' "$_ov_conf" 2>/dev/null)
    OPENVIKING_URL="${OPENVIKING_URL:-$_ov_url}" \
    OPENVIKING_API_KEY="${OPENVIKING_API_KEY:-$_ov_key}" \
      command claude "$@"
  else
    command claude "$@"
  fi
}
```

重新 source rc（`source ~/.zshrc`，bash 用户改成 `source ~/.bashrc`）后重启 `claude`——`/mcp` 应该显示远程 URL 且认证有效。

> **为什么用 function 而不是 `export`？** 全局 export 的 API Key 会被该 shell 派生的所有子进程继承——npm 脚本、构建工具、崩溃 dump、`/proc/<pid>/environ` 都会带上。函数包装把秘钥限定在 `claude` 进程树内。
>
> 还没有 `ovcli.conf`？先按 [部署指南 → CLI 章节](../../docs/zh/guides/03-deployment.md#cli) 创建一份。

**如果 function 包装不方便：**

- **直接编辑插件的 `.mcp.json`**，把值硬编码进去。插件未来更新可能覆盖。
- **在项目 `.mcp.json` 或 `~/.claude.json` 里另起一个 MCP 条目**。参考 [MCP 集成指南](../../docs/zh/guides/06-mcp-integration.md)。

**配错的症状**：hook（auto-recall、auto-capture）正常工作，因为它们直接通过 Node 读配置文件；但按需 MCP 工具（`search`、`read`、`store`…）会静默连到 `http://127.0.0.1:1933`、认证头为空，且 `/mcp` 显示错误的 URL。

## 配置

### 解析优先级

每个插件字段按从高到低：

1. **环境变量**（`OPENVIKING_*`——见下方表格）
2. **`ovcli.conf`** — CLI 客户端配置（`~/.openviking/ovcli.conf` 或 `OPENVIKING_CLI_CONFIG_FILE`）；只承载连接字段（`url`、`api_key`、`account`、`user`、`agent_id`）
3. **`ov.conf`** — 服务器配置（`~/.openviking/ov.conf` 或 `OPENVIKING_CONFIG_FILE`）；插件读 `server.url`、`server.root_api_key`，以及可选的遗留 `claude_code` 块（见 [遗留 `claude_code` 块](#遗留-claude_code-块在-ovconf-里)）
4. **内置默认值**（`http://127.0.0.1:1933`，无鉴权）

> ⚠️ **仅适用于 hooks。** 这条优先级链由 `scripts/config.mjs` 实现，hook 脚本消费。它**不适用**于 MCP 服务器注册——见 [配置 MCP](#配置-mcp)。

### 环境变量

插件全部行为均可通过 env vars 配置。连接 / 身份变量同时影响 hook 和（通过 shell rc）MCP 服务器；调优变量仅影响 hook。

#### 连接 / 身份

| 环境变量                                          | 说明                                                                |
|--------------------------------------------------|--------------------------------------------------------------------|
| `OPENVIKING_URL` / `OPENVIKING_BASE_URL`         | 完整服务器 URL（如 `https://remote.example.com`）                  |
| `OPENVIKING_API_KEY` / `OPENVIKING_BEARER_TOKEN` | API key；以 `Authorization: Bearer <key>` 发送                     |
| `OPENVIKING_ACCOUNT`                             | 多租户 account（`X-OpenViking-Account` 头）                        |
| `OPENVIKING_USER`                                | 多租户 user（`X-OpenViking-User` 头）                              |
| `OPENVIKING_AGENT_ID`                            | Agent 身份，默认 `claude-code`（`X-OpenViking-Agent` 头）           |

#### 召回调优

| 环境变量                                | 默认值        | 说明                                                                |
|----------------------------------------|---------------|--------------------------------------------------------------------|
| `OPENVIKING_AUTO_RECALL`               | `true`        | 启用每轮自动召回                                                   |
| `OPENVIKING_RECALL_LIMIT`              | `6`           | 每轮最多注入的记忆条数                                             |
| `OPENVIKING_RECALL_TOKEN_BUDGET`       | `2000`        | 内联内容的 token 预算；超出预算的项降级为 URI hint                  |
| `OPENVIKING_RECALL_MAX_CONTENT_CHARS`  | `500`         | 单条记忆内容字符上限                                               |
| `OPENVIKING_RECALL_PREFER_ABSTRACT`    | `true`        | 有 abstract 时优先用 abstract 而非完整 body                        |
| `OPENVIKING_SCORE_THRESHOLD`           | `0.35`        | 最低相关度得分（0–1）                                               |
| `OPENVIKING_MIN_QUERY_LENGTH`          | `3`           | 短于此长度的 query 跳过召回                                        |
| `OPENVIKING_LOG_RANKING_DETAILS`       | `false`       | 每候选打分日志（很啰嗦）                                           |

#### 捕获调优

| 环境变量                                | 默认值        | 说明                                                                |
|----------------------------------------|---------------|--------------------------------------------------------------------|
| `OPENVIKING_AUTO_CAPTURE`              | `true`        | 启用自动捕获；同时 gate 写 hook（PreCompact / SessionEnd / SubagentStop） |
| `OPENVIKING_CAPTURE_MODE`              | `semantic`    | `semantic`（总是捕获）或 `keyword`（基于触发词）                   |
| `OPENVIKING_CAPTURE_MAX_LENGTH`        | `24000`       | 捕获判定时 sanitized 文本的长度上限                                |
| `OPENVIKING_CAPTURE_ASSISTANT_TURNS`   | `true`        | 捕获 assistant 回合(文本 + tool 输入/输出)。设为 `0` 可退回仅用户   |
| `OPENVIKING_COMMIT_TOKEN_THRESHOLD`    | `20000`       | client-driven commit 的 pending-token 阈值                         |
| `OPENVIKING_RESUME_CONTEXT_BUDGET`     | `32000`       | resume 时拉取 archive overview 的 token 预算                       |

#### 生命周期 / 行为 / 杂项

| 环境变量                                | 默认值        | 说明                                                                |
|----------------------------------------|---------------|--------------------------------------------------------------------|
| `OPENVIKING_TIMEOUT_MS`                | `15000`       | 召回 + 通用请求 HTTP 超时（ms）                                    |
| `OPENVIKING_CAPTURE_TIMEOUT_MS`        | `30000`       | 捕获路径 HTTP 超时（须低于 `Stop` hook 超时）                      |
| `OPENVIKING_WRITE_PATH_ASYNC`          | `true`        | 把写 hook detach 到后台 worker，避免 CC 等待 commit RTT            |
| `OPENVIKING_BYPASS_SESSION`            | `false`       | 一次性：`1`/`true`=当前进程所有 hook 直接放行                      |
| `OPENVIKING_BYPASS_SESSION_PATTERNS`   | `""`          | CSV 的 glob 模式，匹配 `session_id` 或 `cwd`                       |
| `OPENVIKING_MEMORY_ENABLED`            | (auto)        | `0`/`false`/`no`=强制禁用；`1`/`true`/`yes`=强制启用               |
| `OPENVIKING_DEBUG`                     | `false`       | `1`/`true`=向 `~/.openviking/logs/cc-hooks.log` 输出 debug 日志    |
| `OPENVIKING_DEBUG_LOG`                 | `~/.openviking/logs/cc-hooks.log` | 覆盖日志路径                                  |
| `OPENVIKING_CONFIG_FILE`               | `~/.openviking/ov.conf`           | 覆盖 `ov.conf` 路径                          |
| `OPENVIKING_CLI_CONFIG_FILE`           | `~/.openviking/ovcli.conf`        | 覆盖 `ovcli.conf` 路径                       |

纯环境变量启动（无需配置文件）：

```bash
OPENVIKING_MEMORY_ENABLED=1 \
OPENVIKING_URL=https://openviking.example.com \
OPENVIKING_API_KEY=sk-xxx \
OPENVIKING_ACCOUNT=my-team \
OPENVIKING_USER=alice \
OPENVIKING_RECALL_LIMIT=8 \
claude
```

### 启用 / 禁用

1. **`OPENVIKING_MEMORY_ENABLED` 环境变量** — `0`/`false`/`no` 强制禁用；`1`/`true`/`yes` 强制启用（无配置文件时强制启用，连接信息须由环境变量提供）
2. **`ov.conf` 的 `claude_code.enabled`** — `false` 禁用
3. **配置文件存在性** — `ov.conf` 或 `ovcli.conf` 存在则启用；否则**静默禁用**（不报错，hook 直接放行）

### 跳过某些会话

在 `/tmp` PoC 目录里用 Claude Code 而不污染长期记忆：

```bash
# 持久：任何 session_id 或 cwd 命中模式的会话
export OPENVIKING_BYPASS_SESSION_PATTERNS='/tmp/**,**/scratch/**,/Users/me/Dev/throwaway/*'

# 或一次性：
OPENVIKING_BYPASS_SESSION=1 claude
```

bypass 命中时所有 hook 直接放行，不联系 OpenViking。

### 遗留 `claude_code` 块（在 `ov.conf` 里）

早期插件版本把调优字段配在 `~/.openviking/ov.conf` 的 `claude_code` 块里。出于向后兼容，这种方式仍能用——上面每个 env var 都有对应的 camelCase 字段（`OPENVIKING_RECALL_LIMIT` → `claude_code.recallLimit`、`OPENVIKING_BYPASS_SESSION_PATTERNS` → `claude_code.bypassSessionPatterns` JSON 数组等）。env vars 优先级更高。新部署应优先使用 env vars + shell rc——服务端配置文件不应承载每开发机自己的调优偏好。

## Hook 超时

`hooks/hooks.json` 默认值：

| Hook                | 超时   | 备注                                                                                          |
|---------------------|--------|----------------------------------------------------------------------------------------------|
| `SessionStart`      | `120s` | 充裕，因为 resume / compact 可能拉一个较大的 archive overview                                |
| `UserPromptSubmit`  | `8s`   | 自动召回必须快，prompt 提交不能被 hook 阻塞                                                  |
| `Stop`              | `45s`  | 自动捕获要解析 transcript + 推 turn；async detach 让用户感知接近 0                          |
| `PreCompact`        | `30s`  | 同步 commit，CC 紧接着会改 transcript                                                        |
| `SessionEnd`        | `30s`  | 最终 commit；async detach                                                                    |
| `SubagentStart`     | `10s`  | 轻量：只持久化隔离 state                                                                     |
| `SubagentStop`      | `45s`  | 读子 agent transcript 并 commit；async detach                                                |

`claude_code.captureTimeoutMs` 须低于 `Stop` hook 超时，让脚本能优雅失败并仍能更新增量 state。

## 调试日志

设置 `claude_code.debug: true` 或 `OPENVIKING_DEBUG=1`，hook 日志写到 `~/.openviking/logs/cc-hooks.log`。

- `auto-recall` 默认输出关键阶段 + 紧凑的 `ranking_summary`
- 仅在排查每候选打分时才把 `claude_code.logRankingDetails` 设为 `true`，否则非常啰嗦
- 深度排查请用 `scripts/debug-recall.mjs` / `scripts/debug-capture.mjs` 单跑示例输入，不要长期开 hook 日志

## 故障排除

| 症状                                         | 原因                                                  | 解决方案                                                                                       |
|----------------------------------------------|------------------------------------------------------|-----------------------------------------------------------------------------------------------|
| 插件没激活                                    | 找不到 `ov.conf` / `ovcli.conf`                       | 创建一个；或设 `OPENVIKING_MEMORY_ENABLED=1` 加上 URL/API_KEY 等环境变量                       |
| Hook 触发但召回为空                           | OpenViking 服务器没起 / URL 不对                      | `curl http://localhost:1933/health`（或你的远程 URL）                                          |
| 自动捕获抽取出 0 条记忆                        | `ov.conf` 里 embedding/extraction 模型配错            | 检查 `embedding` / `vlm` 配置；看服务器日志                                                    |
| MCP 工具命中本地 `127.0.0.1` 而不是远程       | `.mcp.json` 仅解析 `${VAR}`，不读 ovcli.conf          | 见 [配置 MCP](#配置-mcp) — export 环境变量或编辑 `.mcp.json`                                    |
| 远程鉴权 401 / 403                            | API key / account / user 头错配                      | 核对 `OPENVIKING_API_KEY`、`OPENVIKING_ACCOUNT`、`OPENVIKING_USER`（或 `ov.conf` 对应字段）    |
| `Stop` hook 超时                              | 服务器慢 + 同步写路径                                 | 保持 `writePathAsync: true`（默认），或调大 `hooks/hooks.json` 里的 `Stop` 超时               |
| 旧上下文反复出现在 OV 里                      | 早期版本把召回块当成用户消息回写了                    | 升级到当前版本——`auto-capture` 现在推送前会剥离 `<openviking-context>`                      |
| 日志太吵                                      | `logRankingDetails: true` 没关                        | 设为 `false`；按需用 `debug-recall.mjs` / `debug-capture.mjs`                                  |

## 与 Claude Code 内置记忆的对比

Claude Code 自带 `MEMORY.md` 文件系统，本插件**与之互补**：

| 特性     | 内置 `MEMORY.md`            | OpenViking 插件                                |
|----------|-----------------------------|-----------------------------------------------|
| 存储     | 扁平 markdown               | 向量数据库 + 结构化抽取                        |
| 搜索     | 整体加载进上下文            | 语义相似度 + 排序 + token 预算                |
| 范围     | 单项目                      | 跨项目、跨会话、跨 agent                       |
| 容量     | ~200 行（受上下文限制）     | 不受限（服务端存储）                           |
| 抽取     | 手写规则                    | LLM 驱动的实体 / 偏好 / 事件抽取               |
| 子 agent | 与父共享                    | 隔离 session + 类型化 agent namespace          |

---

## 架构

```
┌────────────────────────────────────────────────────────────┐
│                      Claude Code                           │
│                                                            │
│  SessionStart   UserPromptSubmit   Stop   PreCompact       │
│  SessionEnd     SubagentStart      SubagentStop            │
└────┬───────────────┬───────────────┬───────────┬───────────┘
     │               │               │           │
     │   ┌───────────▼───────────┐   │           │
     │   │  hook 脚本 (.mjs)     │   │           │     ┌──────────────┐
     │   │  读 transcript +      │───┼───────────┼────►│              │
     │   │  调 OV HTTP API       │   │           │     │  OpenViking  │
     │   └───────────────────────┘   │           │     │  Server      │
     │                               │           │     │  (Python)    │
     │                  ┌────────────▼───────────▼───►│              │
     │                  │  MCP tools (HTTP /mcp)      │              │
     │                  │  search / read / store / …  │              │
     └─────────────────►│                             │              │
        OV session      └─────────────────────────────►              │
        context inject                                └──────────────┘
```

没有内置 MCP server，没有 TypeScript 编译步骤，也没有运行时 npm 引导。Hook 都是直接走 HTTP 调 OpenViking 的 `.mjs` 文件；MCP 来自 OpenViking 服务器自身的 `/mcp` endpoint。

首次接触时创建一个持久化的 OpenViking session，整个 Claude Code 会话期间复用。OV session ID 是 `cc-<sha256(cc_session_id)>`，所以 resume / compact / 多 hook 事件都打到同一个 session，OV 的 `auto_commit_threshold` 自然驱动归档与记忆抽取。

### 各 hook 职责

| Hook                  | 触发时机                              | 行为                                                                                              |
|-----------------------|--------------------------------------|--------------------------------------------------------------------------------------------------|
| `UserPromptSubmit`    | 每个用户回合                          | 搜 OV → 排序 → 在 token 预算内注入 `<openviking-context>` 块                                      |
| `Stop`                | Claude 完成一次响应                   | 解析 transcript → 把新的用户回合推到 OV session → pending tokens 超阈值时 commit                  |
| `SessionStart`        | 新建 / resume / compact 后的会话      | `resume`/`compact` 时拉取最新 archive overview 注入                                              |
| `PreCompact`          | Claude Code 重写 transcript 之前      | 在 CC 改 transcript 之前先把 pending 提交为归档                                                  |
| `SessionEnd`          | Claude Code 会话关闭                  | 最后一次 commit                                                                                  |
| `SubagentStart`       | 父 session 通过 Task 工具孵化子 agent | 为子 agent 派生隔离的 OV session ID，写 start state                                              |
| `SubagentStop`        | 子 agent 结束                         | 读子 agent transcript → 推到隔离 session（带子 agent 类型 header）→ commit                       |

### 异步写路径

`Stop`、`SessionEnd`、`SubagentStop` 用 detach-worker 模式：父 hook drain stdin，立刻向 stdout 输出 `{decision:"approve"}` 解封 Claude Code，然后 spawn 一个 detached 子进程做 HTTP commit。用户从不等 OV。`PreCompact` 必须保持同步，因为 Claude Code 紧接着会改 transcript。

调试时如果需要严格顺序，把 `claude_code.writePathAsync` 设为 `false`。

### 防止记忆自污染

`auto-capture` 在把每个 turn 推给 OV 之前，会剥掉 `<openviking-context>`、`<system-reminder>`、`<relevant-memories>`、`[Subagent Context]` 这些注入块。否则插件本轮注入的召回上下文会在下一轮被当成"用户消息"再次写回 OV，形成自我引用的污染回路。

### 服务器暴露的 MCP 工具

插件的 `.mcp.json` 连到 OpenViking 服务器原生 HTTP MCP endpoint `/mcp`，服务器暴露 9 个 Claude 可按需调用的工具：

| 工具           | 说明                                                |
|----------------|----------------------------------------------------|
| `search`       | 跨 memories / resources / skills 的语义搜索        |
| `read`         | 读一个或多个 `viking://` URI 的内容                |
| `list`         | 列出 `viking://` 目录下条目                        |
| `store`        | 把消息存到长期记忆（触发抽取）                     |
| `add_resource` | 把本地文件 / URL 加为资源                           |
| `grep`         | 在 `viking://` 文件里做正则内容搜索                |
| `glob`         | 按 glob 模式匹配文件                               |
| `forget`       | 删除任意 `viking://` URI                           |
| `health`       | 检查 OpenViking 服务健康                           |

工具参数详见 [MCP 集成指南](../../docs/zh/guides/06-mcp-integration.md)。

### 插件结构

```
claude-code-memory-plugin/
├── .claude-plugin/
│   └── plugin.json          # plugin manifest
├── hooks/
│   └── hooks.json           # 7 个 hook 注册
├── scripts/
│   ├── config.mjs           # 共享配置加载（env > ovcli.conf > ov.conf）
│   ├── debug-log.mjs        # 写 ~/.openviking/logs/cc-hooks.log
│   ├── auto-recall.mjs      # UserPromptSubmit
│   ├── auto-capture.mjs     # Stop
│   ├── session-start.mjs    # SessionStart
│   ├── session-end.mjs      # SessionEnd
│   ├── pre-compact.mjs      # PreCompact
│   ├── subagent-start.mjs   # SubagentStart
│   ├── subagent-stop.mjs    # SubagentStop
│   ├── debug-recall.mjs     # 召回独立诊断
│   ├── debug-capture.mjs    # 捕获独立诊断
│   └── lib/
│       ├── ov-session.mjs   # OV HTTP 客户端 + session 帮助 + bypass 检查
│       └── async-writer.mjs # 写路径 detach-worker 帮助
├── .mcp.json                # MCP 配置（HTTP /mcp on OpenViking）
├── package.json             # 仅 type:module 标记，无运行时依赖
└── README.md
```

## License

Apache-2.0 — 同 [OpenViking](https://github.com/volcengine/OpenViking)。
