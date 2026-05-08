# Claude Code 记忆插件

为 [Claude Code](https://docs.claude.com/zh-CN/docs/claude-code/overview) 提供长期语义记忆。每次用户输入前自动召回相关记忆，每轮对话结束后自动捕获上下文——模型不需要主动调用任何 MCP 工具。

源码：[examples/claude-code-memory-plugin](https://github.com/volcengine/OpenViking/tree/main/examples/claude-code-memory-plugin)

## 快速开始

### 一行安装（推荐）

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/volcengine/OpenViking/main/examples/claude-code-memory-plugin/setup-helper/install.sh)
```

脚本支持 macOS 和 Linux。它会检查依赖、询问你接入**自托管**服务器还是**火山引擎 OpenViking Cloud**（`https://api.vikingdb.cn-beijing.volces.com/openviking`）、按需配置 `~/.openviking/ovcli.conf`（已存在则复用）、把 OpenViking 仓库 clone 到 `~/.openviking/openviking-repo`、把 `claude` function 包装写入 shell rc，然后通过 `claude plugin install` 安装插件。每一步都是幂等的——重复执行安全。

如果你更喜欢手动操作，按下面三步走。

### 手动安装

#### 1. 用 shell function 在调用 `claude` 时注入 env

这是推荐路径。插件的 hooks **和**自带的 MCP 服务器都读 env vars，所以我们把它们设一次——但**只在调用 `claude` 时注入**，不全局 export。在 `~/.zshrc` 或 `~/.bashrc` 末尾追加：

```bash
claude() {
  if [ -f ~/.openviking/ovcli.conf ]; then
    OPENVIKING_URL=$(jq -r '.url' ~/.openviking/ovcli.conf) \
    OPENVIKING_API_KEY=$(jq -r '.api_key' ~/.openviking/ovcli.conf) \
    command claude "$@"
  else
    command claude "$@"
  fi
}
```

重新 source 后验证（用 bash 的话改成 `~/.bashrc`）：

```bash
source ~/.zshrc    # 或：source ~/.bashrc
type claude        # 期望输出：claude is a shell function
```

下一次启动 `claude` 后，进入 `/mcp` 应该能看到 OpenViking 这一项的 URL 是远程地址，认证也有效。

> **还没有 `ovcli.conf`？** 先按 [部署指南 → CLI 章节](../guides/03-deployment.md#cli) 创建一份。
>
> **纯本地模式**（`http://127.0.0.1:1933`，无鉴权）？这一步可以跳过——插件会静默使用本地默认值。
>
> **为什么用 function 而不是 `export`？** 全局 export 的 env vars 会被该 shell 派生的所有子进程继承——npm 脚本、构建工具、崩溃 dump、`/proc/<pid>/environ` 都会带上。函数包装把秘钥限定在 `claude` 进程树内。

#### 2. 安装插件

在 OpenViking 仓库根目录执行：

```bash
claude plugin marketplace add "$(pwd)/examples"
claude plugin install claude-code-memory-plugin@openviking-plugins-local
```

> 默认装在 user scope —— 任何目录下都生效。这里**不显式传 `--scope`**，因为老的 Claude Code 2.0.x（比如 2.0.76）不识别这个 flag 会直接报错。
>
> marketplace 条目让 Claude Code 直接引用源码目录，对 `scripts/`、`hooks/`、配置文件的修改下次 hook 触发即生效，无需重装。但移动 / 重命名 / 删除源码目录，或 `git checkout` 到不含这些文件的分支，会让插件失效。
>
> **Claude Code < 2.0？** `claude plugin` 是 2.0（2025-10）才引入的。更老的版本仍然有 `claude mcp add` 和 hooks 系统，可以走[插件 README 的兼容模式章节](https://github.com/volcengine/OpenViking/blob/main/examples/claude-code-memory-plugin/README_CN.md#兼容模式claude-code--20)手动接同样的功能。一键安装脚本会自动检测版本并询问你是否走这条路径。

#### 3. 启动 Claude Code

```bash
claude
```

进入后执行 `/mcp`，确认 OpenViking 这一项的 URL 是你的远程地址。如果插件似乎没在工作，开 `OPENVIKING_DEBUG=1` 看 `~/.openviking/logs/cc-hooks.log`。

## 为什么用 function 包装？

插件的 hook 会自动读 `ovcli.conf`，但**自带的 `.mcp.json` 条目读不到**。Claude Code 自己解析 `.mcp.json` 且只支持 `${VAR}` 替换，所以插件无法把配置文件里的值透明地注入 MCP URL 和认证头。

在 `claude` 调用时注入 env vars 是同时覆盖 hooks 和 MCP 的唯一路径。用 shell function 包装（而不是全局 `export`）能把 API Key 限定在 `claude` 一个进程树内，不会泄漏到其他子进程——见 [手动安装的步骤 1](#1-用-shell-function-在调用-claude-时注入-env) 的安全说明。

**配错的症状**：hook（auto-recall、auto-capture）正常工作，因为它们直接通过 Node 读配置文件；但按需 MCP 工具（`search`、`read`、`store`…）会静默连到 `http://127.0.0.1:1933`、认证头为空，且 `/mcp` 显示错误的 URL。

## 配置

### 解析优先级

每个插件字段按从高到低：

1. **环境变量**（`OPENVIKING_*`）
2. **`ovcli.conf`** — 只承载连接字段（`url`、`api_key`、`account`、`user`、`agent_id`）
3. **`ov.conf`** — 服务端配置；插件读取 `server.url`、`server.root_api_key`，以及（旧版兼容）`claude_code` 区块（如有）
4. **内置默认值**（`http://127.0.0.1:1933`，无鉴权）

> ⚠️ **仅适用于 hooks。** 这条优先级链由 `scripts/config.mjs` 实现，hook 脚本消费。它**不**适用于 MCP 服务器注册——见 [为什么用 function 包装？](#为什么用-function-包装) 一节。

### 主要环境变量

| 环境变量                                          | 默认值        | 说明                                                                    |
|--------------------------------------------------|---------------|------------------------------------------------------------------------|
| `OPENVIKING_URL` / `OPENVIKING_BASE_URL`         | —             | 完整服务器 URL                                                          |
| `OPENVIKING_API_KEY` / `OPENVIKING_BEARER_TOKEN` | —             | API Key；通过 `Authorization: Bearer <key>` 发送                         |
| `OPENVIKING_AUTO_RECALL`                         | `true`        | 每次用户输入前是否自动召回                                                |
| `OPENVIKING_RECALL_LIMIT`                        | `6`           | 单轮最多注入的记忆条数                                                   |
| `OPENVIKING_RECALL_TOKEN_BUDGET`                 | `2000`        | 内联内容的 token 预算；超出预算的条目降级为 URI 提示                       |
| `OPENVIKING_AUTO_CAPTURE`                        | `true`        | 是否自动捕获；同时控制写入类 hooks                                        |
| `OPENVIKING_BYPASS_SESSION`                      | `false`       | 一次性开关：`1`/`true` 让当前进程跳过所有 hook                            |
| `OPENVIKING_BYPASS_SESSION_PATTERNS`             | `""`          | CSV 形式的 glob，匹配 `session_id` 或 `cwd`                              |
| `OPENVIKING_MEMORY_ENABLED`                      | (auto)        | `0`/`false`=强制关闭；`1`/`true`=强制开启                                |
| `OPENVIKING_DEBUG`                               | `false`       | 把 hook 日志写到 `~/.openviking/logs/cc-hooks.log`                       |

多租户场景下，`OPENVIKING_ACCOUNT`、`OPENVIKING_USER`、`OPENVIKING_AGENT_ID` 用于设置对应的 `X-OpenViking-*` 请求头。完整环境变量列表（召回/捕获微调、生命周期、调试）见 [插件 README](https://github.com/volcengine/OpenViking/blob/main/examples/claude-code-memory-plugin/README.md#configuration)。

### 临时跳过当前 session

在 `/tmp` 下用 Claude Code 做 PoC，不污染长期记忆：

```bash
# 持久化：任何 session_id 或 cwd 命中模式的 session 都跳过
export OPENVIKING_BYPASS_SESSION_PATTERNS='/tmp/**,**/scratch/**,/Users/me/Dev/throwaway/*'

# 或一次性：
OPENVIKING_BYPASS_SESSION=1 claude
```

bypass 启用时，所有 hook 立即返回 approve，不与 OpenViking 通信。

## Statusline 状态行

插件会在 Claude Code 输入框下方渲染一行 OpenViking 状态。安装脚本把它注册到 `~/.claude/settings.json`（CC 插件 manifest 不支持 `statusLine`，必须走用户 settings）。

```text
OV ✓ │ ↩ 6 mem (0.92) · 50ms             本轮注入 6 条记忆，最高分 0.92
OV ⚠ slow                                 探针超过 1s 预算（服务器可能在抽风）
OV ✗ offline                              服务器不可达
OV ⚡ bypass                               命中 OPENVIKING_BYPASS_SESSION*
OV ✓ │ ✎ 573/20k · 2 arch                 待提交进度 + 本 session 已归档 2 次
OV ✓ │ 🔗 resumed │ +3 today              session 已恢复上下文；今日累计归档 3 次
```

hook 脚本把每轮的小快照写到 `~/.openviking/state/`；statusline 脚本读快照，再加 5 秒共享缓存的 `GET /health`。探针 1s 硬超时，多 session 共享缓存避免风暴。

`OPENVIKING_STATUSLINE=off` 可静默不删；`jq 'del(.statusLine)' ~/.claude/settings.json` 彻底移除。已有自定义 statusline 时安装会询问替换 / 跳过 / 手动 compose。

完整的段位说明（每个 segment 何时出现、为什么有时不显示）以及个性化 recipe（隐藏段位、改色、与已有 statusline 组合、自定义段位），参见 [`examples/claude-code-memory-plugin/STATUSLINE.md`](https://github.com/volcengine/OpenViking/blob/main/examples/claude-code-memory-plugin/STATUSLINE.md)。最方便的方式是打开 Claude Code 把链接喂给它，让它读完文档既能解释你看到的内容，又能按你的偏好调整。

## 与 Claude Code 内置 `MEMORY.md` 的对比

本插件**补充**Claude Code 原生记忆系统，不替代：

| 维度          | 内置 `MEMORY.md`                  | OpenViking 插件                                     |
|---------------|-----------------------------------|----------------------------------------------------|
| 存储          | 平铺 markdown                      | 向量库 + 结构化抽取                                 |
| 检索          | 整体加载到上下文                    | 语义相似度 + 排序 + token 预算                      |
| 范围          | 单项目                              | 跨项目、跨 session、跨 agent                        |
| 容量          | ~200 行（受上下文限制）             | 无限（服务端存储）                                   |
| 抽取          | 手写规则                            | LLM 驱动的实体 / 偏好 / 事件抽取                     |
| Subagents     | 与父 agent 共享                     | 隔离 session + agent 命名空间分类                    |

## Hook 行为

| Hook                  | 触发时机                                  | 行为                                                                                              |
|-----------------------|-----------------------------------------|---------------------------------------------------------------------------------------------------|
| `UserPromptSubmit`    | 每次用户输入                              | 搜索 OV → 排序 → 在 token 预算内注入 `<openviking-context>` 块                                       |
| `Stop`                | Claude 完成一次回复                       | 解析 transcript → 把新的用户轮推入 OV session → pending tokens 超阈值时 commit                        |
| `SessionStart`        | 新建 / 恢复 / compact 后的 session        | 在 `resume`/`compact` 场景，拉取最新归档摘要并注入为额外上下文                                          |
| `PreCompact`          | Claude Code 重写 transcript 之前          | 在 CC 改写前，把待写入消息 commit 成归档                                                            |
| `SessionEnd`          | Claude Code session 关闭                 | 最终 commit，让最后一个窗口归档                                                                      |
| `SubagentStart`       | 父 agent 通过 Task 工具派生 subagent      | 为 subagent 派生隔离的 OV session ID，持久化启动状态                                                  |
| `SubagentStop`        | Subagent 完成                             | 读取 subagent transcript → 推入隔离 session（带 subagent 类型的 agent 头）→ commit                     |

`Stop`、`SessionEnd`、`SubagentStop` 用 detached-worker 模式，用户感知不到 OV 的 RTT。需要确定性顺序时设置 `OPENVIKING_WRITE_PATH_ASYNC=false`。

`auto-capture` 在推送到 OV 前会剥离 `<openviking-context>`、`<system-reminder>`、`<relevant-memories>`、`[Subagent Context]` 等区块——否则插件本轮注入的召回上下文会作为下一轮"用户消息"被回灌进去，形成自污染。

### OV session id 派生

OV 端的 session id 直接包含 CC 的 `session_id`，方便肉眼对账：

- 主 session：`cc-<ccSessionId>`，例如 `cc-7d978bb3-cd9c-4ac6-828d-20965d66b783`
- subagent：`cc-<ccSessionId>__agent-<agentId>`，例如 `cc-7d978bb3-cd9c-4ac6-828d-20965d66b783__agent-abc123`

可在 `~/.openviking/state/last-capture.json` 看到当前 session 的 `cc_session_id` 和 `ov_session_id` 对应关系；CC 这边的 session id 也是 `~/.claude/projects/<encoded-cwd>/<session_id>.jsonl` 的文件名。

## 故障排查

| 现象                                          | 原因                                                          | 修复                                                                                          |
|----------------------------------------------|--------------------------------------------------------------|----------------------------------------------------------------------------------------------|
| 插件未激活                                    | 找不到 `ov.conf` / `ovcli.conf`                              | 跑 [一行安装脚本](#一行安装推荐)，或者设 `OPENVIKING_MEMORY_ENABLED=1` + URL/API_KEY env vars |
| Hook 触发但召回为空                           | OpenViking 服务器没起来，或 URL 不对                          | `curl "$(jq -r '.url' ~/.openviking/ovcli.conf)/health"`                                      |
| MCP 工具连到 `127.0.0.1` 而不是远程            | `.mcp.json` 只支持 `${VAR}` 替换，读不到 `ovcli.conf`           | 见 [为什么用 function 包装？](#为什么用-function-包装)                                          |
| 远程认证 401 / 403                            | API Key 错误，或多租户头缺失                                   | 检查 `OPENVIKING_API_KEY`；多租户场景还要核对 `OPENVIKING_ACCOUNT` / `OPENVIKING_USER`           |
| `Stop` hook 超时                              | 服务器慢 + 同步写路径                                         | 保持 `OPENVIKING_WRITE_PATH_ASYNC=true`（默认），或在 `hooks/hooks.json` 提高 `Stop` 超时         |

## 参见

- [完整插件 README](https://github.com/volcengine/OpenViking/blob/main/examples/claude-code-memory-plugin/README.md) — 完整环境变量表、hook 超时、调试日志、架构图
- [迁移说明](https://github.com/volcengine/OpenViking/blob/main/examples/claude-code-memory-plugin/MIGRATION.md) — 从旧版插件升级
- [MCP 集成指南](../guides/06-mcp-integration.md) — MCP 工具参数与其他客户端
- [部署指南 → CLI](../guides/03-deployment.md#cli) — `ovcli.conf` 配置
