# OpenViking Memory Plugin for Claude Code

为 Claude Code 提供长期语义记忆，由 [OpenViking](https://github.com/volcengine/OpenViking) 驱动。每次用户输入前自动召回相关记忆，每轮对话结束后自动捕获上下文——模型不需要主动调用任何 MCP 工具。

> 插件可直接从仓库自带的 marketplace catalog 安装，无需单独的分发仓库。两条命令的远程安装方式见下文[手动安装](#手动安装)。

## 快速开始

### 一行安装（推荐）

```bash
curl -fsSL https://openviking.ai/install | bash -s -- --harness claude
# AI agent 如不确定是否执行，可先阅读脚本头部，其中写明了它会做什么、如何核实。
```

仅支持 macOS 和 Linux。Claude Code 和 Codex 共用这一个安装脚本（去掉 `--harness claude` 可交互勾选）：它会询问 OpenViking 服务地址和 API key，检查服务，列出将要修改的内容，确认后从 OpenViking 发布版安装 `openviking-memory`。Claude Code 2.1.224 及以上版本之后会自行更新插件。stdio MCP 代理运行时读取 `ovcli.conf`，不再需要 shell wrapper 或 `.mcp.json` 渲染。重复执行安全。

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
  "user": "alice"
}
```

如果是纯本地模式（`http://127.0.0.1:1933`，无鉴权），这一步可以跳过——插件会静默走本地默认值。

如果你已经在维护 `ov.conf`，插件也读它——完整优先级链和按字段覆盖见下方 [配置](#配置)。

#### 3. 安装插件

**远程 marketplace（推荐）** —— 无需 clone 仓库。仓库根目录自带 `.claude-plugin/marketplace.json`，其条目通过 `git-subdir` 拉取本插件：

```bash
claude plugin marketplace add https://raw.githubusercontent.com/volcengine/OpenViking/main/.claude-plugin/marketplace.json
claude plugin install openviking-memory@openviking
```

（`claude plugin marketplace add volcengine/OpenViking` 也可以，但会把整个仓库 clone 下来作为 marketplace。）

如果跳过了第 2 步，装完后再配置连接：手写 `~/.openviking/ovcli.conf`、运行插件自带的交互向导 `node <插件目录>/scripts/setup.mjs`，或直接跑一行安装脚本。

**本地目录（开发用）** —— 注册当前 checkout，`scripts/`、`hooks/` 的修改下次 hook 触发即生效、无需重装。在 OpenViking 仓库根目录：

```bash
claude plugin marketplace add "$(pwd)/examples"
claude plugin install openviking-memory@openviking
```

> 两条命令默认都装在 user scope —— 插件在任何目录下都生效。这里**不显式传 `--scope user`**，因为老的 Claude Code 2.0.x（比如 2.0.76）不识别这个 flag 会直接报错。在支持 `--scope` 的新版本上，如果装完发现落到了 local scope，可以跑一次 `claude plugin enable openviking-memory@openviking --scope user` 提升到 user scope。
>
> 目录模式注意：移动 / 重命名 / 删除源码目录，或 `git checkout` 到不含这些文件的分支，会立刻让插件失效。两种模式注册的 marketplace 都叫 `openviking`，插件 id 恒为 `openviking-memory@openviking`；切换模式时先移除 marketplace 再添加另一个来源（安装脚本会自动处理）。

##### 兼容模式（Claude Code < 2.0）

`claude plugin` 子命令是 Claude Code 2.0（2025-10）才引入的。再老的版本只有 `claude mcp add` 和 hooks 系统，但仍然能手动接出同样的功能：

```bash
PLUGIN_DIR="$(pwd)/examples/claude-code-memory-plugin"

# stdio MCP 代理 —— 自己读 ovcli.conf / OPENVIKING_*，不再需要拼 header。
claude mcp remove openviking -s user 2>/dev/null
claude mcp add --scope user openviking -- node "$PLUGIN_DIR/servers/mcp-proxy.mjs"

# 把插件 hooks 合并进 ~/.claude/settings.json（自动备份）
mkdir -p ~/.claude && [ -f ~/.claude/settings.json ] || echo '{}' > ~/.claude/settings.json
cp -p ~/.claude/settings.json ~/.claude/settings.json.bak.$(date +%s)
sed "s|\${CLAUDE_PLUGIN_ROOT}|$PLUGIN_DIR|g" "$PLUGIN_DIR/hooks/hooks.json" > /tmp/ov-hooks.json
jq --slurpfile h /tmp/ov-hooks.json '.hooks = ((.hooks // {}) * $h[0].hooks)' \
  ~/.claude/settings.json > /tmp/ov-settings.json
jq -e . /tmp/ov-settings.json >/dev/null && mv /tmp/ov-settings.json ~/.claude/settings.json
rm -f /tmp/ov-hooks.json
```

一行安装脚本不会做以上配置：检测到 2.0 之前的版本时，它会跳过 Claude Code 并提示升级。

#### 4. 启动 Claude Code

```bash
claude
```

如果插件似乎没在工作，开 `OPENVIKING_DEBUG=1` 看 `~/.openviking/logs/cc-hooks.log`。

## 配置 MCP

插件的 hook 和 MCP 条目现在使用同一条配置链。仓库里的 `.mcp.json` 会把 `servers/mcp-proxy.mjs` 作为本地 stdio MCP server 启动；这个代理读取 `OPENVIKING_*`、`~/.openviking/ovcli.conf` 和 `~/.openviking/ov.conf`，再把 JSON-RPC 转发到 OpenViking 服务端原生 `/mcp` endpoint，并补齐认证与身份头。

正常插件安装不需要额外 export，也不需要渲染 `.mcp.json`。更新 `ovcli.conf` 或相关 `OPENVIKING_*` 环境变量后重启 Claude Code，代理会和 hook 脚本命中同一个 OpenViking 目标。

代理要求 Node.js 18+。只有在 `OPENVIKING_DEBUG=1` 或 `claude_code.debug=true` 时才写 debug log；stdout 严格保留给 MCP 协议输出。

## 配置

### 解析优先级

每个插件字段按从高到低：

1. **环境变量**（`OPENVIKING_*`——见下方表格）
2. **`ovcli.conf`** — CLI 客户端配置（`~/.openviking/ovcli.conf` 或 `OPENVIKING_CLI_CONFIG_FILE`）；只承载连接字段（`url`、`api_key`、`account`、`user`）
3. **`ov.conf`** — 服务器配置（`~/.openviking/ov.conf` 或 `OPENVIKING_CONFIG_FILE`）；插件读 `server.url`、`server.root_api_key`，以及可选的遗留 `claude_code` 块（见 [遗留 `claude_code` 块](#遗留-claude_code-块在-ovconf-里)）
4. **内置默认值**（`http://127.0.0.1:1933`，无鉴权）

同一组连接与身份字段也会被 stdio MCP 代理使用。

### 环境变量

插件全部行为均可通过 env vars 配置。连接 / 身份变量同时影响 hook 和 MCP 代理；调优变量仅影响 hook。

#### 连接 / 身份

| 环境变量                                          | 说明                                                                |
|--------------------------------------------------|--------------------------------------------------------------------|
| `OPENVIKING_URL` / `OPENVIKING_BASE_URL`         | 完整服务器 URL（如 `https://remote.example.com`）                  |
| `OPENVIKING_API_KEY` / `OPENVIKING_BEARER_TOKEN` | API key；以 `Authorization: Bearer <key>` 发送                     |
| `OPENVIKING_ACCOUNT`                             | 多租户 account（`X-OpenViking-Account` 头）                        |
| `OPENVIKING_USER`                                | 多租户 user（`X-OpenViking-User` 头）                              |
| `OPENVIKING_PEER_ID`                             | 可选的稳定 peer，用于自动召回和 session message 写入               |

设置 `OPENVIKING_PEER_ID` 后，数据面的 recall/profile 请求会把它作为 `X-OpenViking-Actor-Peer` 发送；捕获到 session message 时仍写入 body `peer_id`。未显式配置 peer 时，subagent 捕获会回退到 Claude 的 `agent_id`，让不同 subagent 默认落到不同 peer memory。

#### 召回调优

| 环境变量                                | 默认值        | 说明                                                                |
|----------------------------------------|---------------|--------------------------------------------------------------------|
| `OPENVIKING_AUTO_RECALL`               | `true`        | 启用每轮自动召回                                                   |
| `OPENVIKING_RECALL_LIMIT`              | `10`          | 遗留配额缩放输入；转换为六类 coding 配额，不是最终结果上限          |
| `OPENVIKING_RECALL_TOKEN_BUDGET`       | `2000`        | 仅用于最终 raw-find fallback 的内联 token 预算                      |
| `OPENVIKING_RECALL_MAX_CONTENT_CHARS`  | `500`         | 单条记忆内容字符上限                                               |
| `OPENVIKING_RECALL_PREFER_ABSTRACT`    | `true`        | 有 abstract 时优先用 abstract 而非完整 body                        |
| `OPENVIKING_SCORE_THRESHOLD`           | `0.35`        | 最低相关度得分（0–1）                                               |
| `OPENVIKING_MIN_QUERY_LENGTH`          | `3`           | 短于此长度的 query 跳过召回                                        |
| `OPENVIKING_RECALL_QUERY_FILTERS`      | `""`          | 逗号分隔的正则规则，在 prompt 变成检索 query 前生效 —— 见[输入过滤器](#输入过滤器) |
| `OPENVIKING_LOG_RANKING_DETAILS`       | `false`       | 每候选打分日志（很啰嗦）                                           |
| `OPENVIKING_RECALL_MAX_TOKENS`         | `1600`        | 服务端组装上下文块的 token 预算（与本地压缩输入上限相互独立）         |
| `OPENVIKING_RECALL_DEDUP_TURNS`        | `5`           | 跨轮冷却：最近 N 轮已注入过的 URI 本轮跳过                           |
| `OPENVIKING_RECALL_EVENTS_DECAY_PROTECTION` | （关） | 事件记忆在此期限内（`7d`、`12h`）保持原分，之后衰减，较早的事件不再压过新的。以 `events_time_decay_protection` 发送。 |
| `OPENVIKING_RECALL_QUERY_EXPANSION`    | `auto`        | `auto` 让服务端结合会话上下文扩展短提问；`off` 关闭                   |
| `OPENVIKING_RECALL_COMPRESS`           | `auto`        | digest 压缩：`off`、`client`（本地宿主 CLI）、`server`、`auto`（本地优先、失败回落服务端） |
| `OPENVIKING_RECALL_COMPRESS_MAX_BULLETS` | `6`         | digest 条数上限                                                     |

召回的不只是记忆，也包括 skill：服务端组装的上下文块可能带有 skill 条目（`type="skills"`），既有你自己的 skill，也有账号内共享的 skill。

#### 捕获调优

| 环境变量                                | 默认值        | 说明                                                                |
|----------------------------------------|---------------|--------------------------------------------------------------------|
| `OPENVIKING_AUTO_CAPTURE`              | `true`        | 启用自动捕获；同时 gate 写 hook（PreCompact / SessionEnd / SubagentStop） |
| `OPENVIKING_CAPTURE_MODE`              | `semantic`    | `semantic`（总是捕获）或 `keyword`（基于触发词）                   |
| `OPENVIKING_CAPTURE_MAX_LENGTH`        | `24000`       | 捕获判定时 sanitized 文本的长度上限                                |
| `OPENVIKING_CAPTURE_ASSISTANT_TURNS`   | `true`        | 捕获 assistant 回合(文本 + tool 输入/输出)。设为 `0` 可退回仅用户   |
| `OPENVIKING_COMMIT_TOKEN_THRESHOLD`    | `20000`       | client-driven commit 的 pending-token 阈值                         |
| `OPENVIKING_RESUME_CONTEXT_BUDGET`     | `32000`       | resume 时拉取 archive overview 的 token 预算                       |
| `OPENVIKING_CAPTURE_FILTERS`           | `""`          | 逗号分隔的正则规则，作用于每个被捕获的回合 —— 见[输入过滤器](#输入过滤器) |

#### 会话启动注入

| 环境变量                                  | 默认值    | 说明                                                                |
|------------------------------------------|-----------|--------------------------------------------------------------------|
| `OPENVIKING_NO_AUTO_INJECT`              | `false`   | 会话启动时不注入用户画像、记忆索引和 skill 清单；resume/compact 的 archive overview 和逐轮召回照常进行 |
| `OPENVIKING_PROFILE_TOKEN_BUDGET`        | `10000`   | `profile.md` 及 `preferences/`、`entities/` 索引共用的 CJK-aware token 预算 |
| `OPENVIKING_SKILL_CATALOG`               | `true`    | 在会话启动块里加入 `<available-skills>` skill 清单                 |
| `OPENVIKING_SKILL_CATALOG_TOKEN_BUDGET`  | `1200`    | `<available-skills>` 的 token 预算（0–20000），不占用户画像的预算；设为 `0` 即不注入清单 |
| `OPENVIKING_SESSION_START_MAX_BYTES`    | `9500`    | SessionStart 注入的总字节上限，保证低于 Claude Code 10,000 字符的内联限制；resume/compact 时归档最多占一半；`0` 取消上限 |

在 `ovcli.conf` 里，这几项对应 `plugin` 或 `plugin.claude_code` 下的 `noAutoInject`、`profileTokenBudget`、`skillCatalog` 和 `skillCatalogTokenBudget`。

每次 `SessionStart`（`startup`、`clear`、`resume`、`compact`）都会注入一个 `<openviking-context>` 块，依次包含 `<user-profile>`、`<available-memories>` 和 `<available-skills>`；`resume`/`compact` 时后面还会接上最新的 archive overview。skill 清单来自一次 `GET /api/v1/skills?node_limit=200` 调用：先列你自己的 skill，再列账号内共享在 `viking://agent/skills` 下的 skill，与你自己某个 skill 同名的共享 skill 不再列出。每条描述截到约 40 个 token，描述里出现的 `<openviking-context>` 等注入块标签会被转义。完整清单超出预算时只列名称，名称也放不全时以 `... +N more, search OpenViking skills to find the rest` 收尾；连一个名称都放不下时，整块缩成一行 `<available-skills>N OpenViking skills; search OpenViking skills to find them.</available-skills>`。没有任何 skill，或服务端不支持 `GET /api/v1/skills` 时，不注入清单。

```text
<openviking-context source="startup">
<user-profile uri="viking://user/default/memories/profile.md">...</user-profile>
<available-memories>...</available-memories>
<available-skills>
  OpenViking skills (stored in OpenViking, not local files). Before following one, read <dir>/<name>/SKILL.md with the OpenViking read tool.
  viking://user/default/skills/
    - pr-review — Review a pull request against the team checklist.
  viking://agent/skills/
    - deploy-runbook — Shared deployment runbook for the payments service.
</available-skills>
</openviking-context>
```

插件自带的 `openviking-skills` skill 告诉 Claude 拿到清单后怎么做：查找和使用 skill，用 MCP `add_skill` 工具创建、安装或共享 skill，删除 skill，以及在你要求时把 `~/.claude/skills` 或 `<repo>/.claude/skills` 下的本地 skill 迁入 OpenViking。依赖本机环境的 skill（由插件分发、由 CLI 安装器软链接进来，或需要本地二进制）留在本地，每个 skill 都要经你确认后才会上传。

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

#### Secret exec 配置

| 环境变量                                | 默认值        | 说明                                                                |
|----------------------------------------|---------------|--------------------------------------------------------------------|
| `OPENVIKING_SECRET_EXEC`               | `false`       | 让带凭据引用的 Bash 命令经 runner 执行，见 [Secret exec](#secret-exec) |
| `OPENVIKING_SECRET_EXEC_APPROVAL`      | `inherit`     | `inherit` 让命令照常走你自己的权限模式与规则，和没开 secret exec 时一样；`ask` 在每条使用凭据的命令执行前确认 |

在 `ovcli.conf` 里对应 `plugin` 或 `plugin.claude_code` 下的 `secretExec` 与 `secretExecApproval`。workspace 配置文件不能设置这两项。

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

### 输入过滤器

两个配置项在插件送出文本之前加了一层有序的正则规则：

- `recallQueryFilters` / `OPENVIKING_RECALL_QUERY_FILTERS` —— 作用于 prompt，在它变成检索 query 之前。
- `captureFilters` / `OPENVIKING_CAPTURE_FILTERS` —— 作用于写路径（`Stop`、`PreCompact`、`SessionEnd`、`SubagentStop`）上的每个回合，在它被存下来之前。

规则是 sed 风格的字符串，按顺序作用于同一段文本：

| 写法 | 含义 |
|------|------|
| `s<d>模式<d>替换<d>[flags]` | 替换；替换串里可以用 `$1`、`$&`、`$$` |
| `d<d>模式<d>[flags]` | 命中则丢弃这段文本 |
| `k<d>模式<d>[flags]` | 只有命中才保留（多条串联即 AND） |
| `user:` / `assistant:` 前缀 | 该规则只对这个角色生效 |

`<d>` 是任意标点分隔符（`/`、`|`、`#`、`:`），模式里用 `\` 转义它。flags 支持 `i`、`m`、`s`、`u`、`g`（`g` 表示替换全部；对 `d`/`k` 没有意义，会被去掉）。

| 规则 | 效果 |
|------|------|
| `s/^\s*(ultrathink\|think harder?)\s+//i` | 去掉 query 前面的思考关键词 |
| `d\|^\s*[/!]\|` | slash 命令和 `!` bash 模式的 prompt 不触发召回（用 `\|` 当分隔符，`/` 就不必转义） |
| `k/^\?ov\b/` 配 `s/^\?ov\s*//` | 改成显式触发：只有以 `?ov` 开头才召回，并去掉这个触发词 |
| `s/\b(sk\|ghp\|xoxb)_[A-Za-z0-9_-]+/[redacted]/g` | 存进记忆前把 token 打码 |
| `user:d/^\s*\/(clear\|compact)\b/` | 这类命令回合永不入库，且只针对用户侧 |
| `s/^(请\|麻烦)(你\|帮我)?//` | 去掉中文客套前缀 |

写进 `ovcli.conf` 时是 JSON 数组，所以反斜杠要写两遍：

```json
{
  "plugin": {
    "claude_code": {
      "recallQueryFilters": ["s/^\\s*ultrathink\\s+//i", "d|^\\s*[/!]|"],
      "captureFilters": ["s/\\b(sk|ghp)_[A-Za-z0-9_-]{10,}/[redacted]/g"]
    }
  }
}
```

- **环境变量是逗号分隔的列表**，先按逗号切分再解析，所以需要字面逗号的规则（比如带下界的 `{10,}`）只能写进上面的数组。（`\x2c` 能表示模式里其它位置的字面逗号，但它不是量词语法。）
- **顺序有意义，丢弃优先。** 第一条命中的 `d`（或未命中的 `k`）就决定了结果。过滤发生在 `OPENVIKING_MIN_QUERY_LENGTH` 和内置的应答语／slash 命令启发式之前，所以剥掉前缀后只剩「好的」的文本会按应答语丢弃。被替换成空串不算丢弃 —— query 空了只是长度不够。
- **过滤只管送出去的内容，管不到已经存下的。** 会话中途新增 `d`/`k` 规则还会让捕获游标计数的回合列表变短，这会被当成 transcript 被改写、从最后一个用户回合重放 —— 和切换 `OPENVIKING_CAPTURE_ASSISTANT_TURNS` 是同一个现象。
- **替换会作用到工具调用，取舍判断不会。** 在写路径上，`s` 规则也会改写工具调用输入里的每个字符串值和工具结果的输出，所以脱敏规则同样能盖住出现在命令或命令输出里的密钥；`d`/`k` 规则只看回合的文本。
- **坏规则只会被跳过，不会让 hook 挂掉。** `ov-memory-doctor` 会列出生效的规则，并对编译失败的那条给出确切的解析或 RegExp 报错。

### 插件配置放在 `ovcli.conf`

客户端侧的调优应写在 `~/.openviking/ovcli.conf` 的 `plugin` 区域。共享键对所有 harness 生效，分 harness 的对象可覆盖它：

```json
{
  "url": "http://127.0.0.1:1933",
  "plugin": {
    "recallCompress": "auto"
  }
}
```

解析顺序：env vars → `plugin.claude_code` → `plugin` → `ov.conf` 里遗留的 `claude_code` 块 → 内置默认值。
除非用户显式覆盖，插件不会发送 `limit=10`、`max_tokens=1600`、
`query_expansion="auto"` 等由服务端拥有的 Context 默认值。
显式设置遗留 `recallLimit` 时，插件会将其转换为各分类 coding 配额，而不会作为
最终结果上限执行。因此值为 1 到 5 时，有效总配额仍为 6，即六个 coding 域各一个
检索槽位。新的直接 API 接入应优先配置 `quotas`。

### digest 压缩

`recallCompress` 决定 digest 在哪里生成，默认值为 `auto`。`client` 始终通过 `claude -p` 在本地压缩（默认 Haiku + 低推理档），token 成本留在你自己的订阅额度里。`server` 让 OpenViking 生成 digest。`auto` 优先本地，探测不到可用的宿主 CLI 时回落到服务端。压缩器执行失败或输出校验失败时会退回未压缩的上下文块；任一压缩器精确返回 `NO_RELEVANT_MEMORY` 都是成功的空结果，不注入任何内容。压缩子进程运行时所有 OpenViking hook 均被禁用，不会递归。旧的环境变量 `OPENVIKING_RECALL_REWRITE` 和配置键 `recallRewrite` 仍作为低优先级兼容别名保留。

### 遗留 `claude_code` 块（在 `ov.conf` 里）

早期插件版本把调优字段配在 `~/.openviking/ov.conf` 的 `claude_code` 块里。出于向后兼容，这种方式仍能用——上面每个 env var 都有对应的 camelCase 字段（`OPENVIKING_RECALL_LIMIT` → `claude_code.recallLimit`、`OPENVIKING_BYPASS_SESSION_PATTERNS` → `claude_code.bypassSessionPatterns` JSON 数组等）。env vars 优先级更高。新部署应优先使用 env vars + shell rc——服务端配置文件不应承载每开发机自己的调优偏好。

## Secret exec

默认关闭。设 `OPENVIKING_SECRET_EXEC=1`，或在 `ovcli.conf` 里设 `secretExec: true` 后，Claude 可以使用登记在 OpenViking Privacy 里的凭据，而值不进入对话。它把 `privacy_tree` 列出的引用直接写进 Bash 命令，引号按命令需要随意写：

```bash
curl -H "Authorization: Bearer {{ov_privacy:alice:viking://~/privacy/credential/github#/values/pat}}" https://api.github.com/user
```

一个独立的 `PreToolUse` hook（与 URI guard 无关）把这样的命令交给插件的 runner：`node scripts/secret-run.mjs '<原命令>'`。一个独立的 `PreToolUse` hook（与 URI guard 无关）把这样的命令包成 `node scripts/secret-run.mjs '<原命令>'`。hook 不联网，也从不接触值。命令执行时，runner 用你的 OpenViking key 逐个取值（`POST /api/v1/privacy-configs/resolve`），以环境变量交给命令，在引用原来的位置展开，值从不拼进 Shell 代码。命令在 stdout、stderr 里输出的值会被换回引用，这一点没有开关可以关闭。任何一个引用取不到值，命令都不会执行；开关关闭时 runner 什么值都不取。stdin、退出码和信号原样透传。同一个开关还会拒绝 MCP 的 `privacy_resolve`（它会把值放进对话），并在会话开始时注入一段简短的用法说明。

hook 默认不给出权限决定：由你自己的权限模式与规则决定，和 Claude 写的原命令一样。Claude Code 按改写后的命令匹配规则，而原命令在改写后被包在引号里，规则看不见。所以 hook 在 runner 前面为原命令里的每条命令各加一行永不执行的守卫行：

```bash
false && curl -H "Authorization: Bearer {{…}}" https://api.github.com/user
/usr/bin/node /…/scripts/secret-run.mjs 'curl -H "Authorization: Bearer {{…}}" https://api.github.com/user'
```

Claude Code 按命令文本检查，不管其中一段实际会不会执行，所以守卫行会像原命令一样被检查：

| 你的规则 | 结果 |
| --- | --- |
| 原命令里的每条命令都已放行，runner 也已放行 | 直接执行，不提示 |
| 原命令里有一条命中拒绝规则 | 拒绝，bypass 模式下也一样 |
| 原命令里有一条没放行，或 runner 没放行 | 走正常审批 |

改写后的命令需要的权限 = 原命令需要的权限 + runner 的权限，所以放行 runner 不会连带放行别的命令。放行一次即可，例如加一条规则 `Bash(*/node */scripts/secret-run.mjs *)`；之后你本来就放行的命令，带上凭据也照样不提示。

下面三种情况不管规则怎么写都会请你确认：

- 设了 `OPENVIKING_SECRET_EXEC_APPROVAL=ask`。每条使用凭据的命令执行前都确认，提示里显示完整命令和要用到的凭据。
- 命令不只是一串普通命令和管道：里面有 here-document、`$( )`、反引号、子 Shell，或 `if`、`for` 这类复合语句。hook 不去猜其中每条命令的起止，所以不写守卫行，改为请你确认。
- 命令里自己写了 runner。放行 runner 的规则看不到它要跑什么。

它防的是失误，不防存心要把值弄出去的模型：把引用和 runner 都藏起来的命令，或者自己读 OpenViking key 去调服务器的命令，都拦不住。hook 从不自行放行命令。Claude Code 会检查不执行的那一行，这是实测行为（在 Claude Code 2.1.321 上验证），不是文档承诺的契约。

不在覆盖范围内的：

- 它不保证密钥永远不到达模型。它只覆盖使用引用的命令；你粘贴的 key、`cat .env` 的输出、召回内容仍会发给模型，除非经过 [OpenViking Gateway](../../docs/zh/guides/15-gateway.md) 在上行时封存。
- 遮罩只匹配值的原文和它在 JSON 字符串里的转义形式。命令以编码或截取的形式输出的值（例如 `curl -v -u user:{{…}}` 打印的 Basic 头）不会被遮罩。
- runner 用一个小扫描器判断命令的引号上下文。判断错时（例如 `$( )` 里的 `case` 模式），引用可能被展开成不带引号的形式，含空白的值随之被拆开，拆开后的形式遮罩匹配不到。
- 包裹后的命令在独立的 Shell 里运行：`cd` 不会延续到下一条命令，Shell 配置里的 alias 和函数也不可用。
- runner 需要能访问 OpenViking 服务器，在沙箱内也一样。
- Shell 与 Claude Code 的 Bash 工具一致：`CLAUDE_CODE_SHELL` 或 `$SHELL` 是 bash 或 zsh 时用它，否则用 `PATH` 里的 zsh 或 bash，两者都没装时才用 `/bin/sh`。引用出现在带引号定界符的 here-document（`<<'EOF'`）里会被拒绝，因为 Shell 不会在那里展开。
- 值以数据的形式交给命令。会把自身参数再当作 Shell 代码解析的命令（`eval`、`sh -c '…'`、`ssh host '…'`）也会解析这个值。
- workspace 配置文件不能开启它，但 Claude Code 的项目设置（`.claude/settings.json`）可以在 `env` 里设置 `OPENVIKING_SECRET_EXEC`，就像它也能添加自己的 hook 一样。

## Hook 超时

`hooks/hooks.json` 默认值：

| Hook                | 超时   | 备注                                                                                          |
|---------------------|--------|----------------------------------------------------------------------------------------------|
| `SessionStart`      | `120s` | 充裕，因为 resume / compact 可能拉一个较大的 archive overview                                |
| `UserPromptSubmit`  | `60s`  | 给默认本地压缩器留出完成时间；其自身超时更短，失败时可在 hook 截止前安全降级                  |
| `Stop`              | `45s`  | 自动捕获要解析 transcript + 推 turn；async detach 让用户感知接近 0                          |
| `PreCompact`        | `30s`  | 同步 commit，CC 紧接着会改 transcript                                                        |
| `SessionEnd`        | `30s`  | 最终 commit；async detach                                                                    |
| `SubagentStart`     | `10s`  | 轻量：只持久化隔离 state                                                                     |
| `SubagentStop`      | `45s`  | 读子 agent transcript 并 commit；async detach                                                |

`claude_code.captureTimeoutMs` 须低于 `Stop` hook 超时，让脚本能优雅失败并仍能更新增量 state。

## Statusline 状态行

插件会在 Claude Code 输入框下方渲染一行 OpenViking 状态。安装脚本会把它注册到 `~/.claude/settings.json`（CC 插件 manifest 不支持 `statusLine` 字段，必须走这条路）。

示例：

```text
OV ✓ │ Fable 5 · ctx 42% │ ↩ 6 mem · 50ms          注入 6 条记忆；模型 + 上下文占比
OV ⚠ slow                                  探针超过 1s 预算（服务器可能在抽风）
OV ✗ offline                               服务器不可达
OV ⚡ bypass │ Fable 5 · ctx 42%            命中 OPENVIKING_BYPASS_SESSION*
OV ✓ │ ✎ 573/20k · 2 arch                  待提交进度 + 本 session 已归档 2 次
OV ✓ │ 🔗 resumed │ +3 today               session 已恢复上下文；今日累计归档 3 次
```

`ctx` 百分比复刻 Claude Code 原生上下文指示器（自定义 statusLine 会替换掉原生那条），配色阈值与原生一致：`<70%` 灰、`70–89%` 黄、`≥90%` 红。不想显示可设 `OPENVIKING_STATUSLINE_CTX=off`。

完整段位说明 + 个性化 recipe（隐藏段位、改色、与已有 statusline 组合、自定义段位），见 [`STATUSLINE.md`](./STATUSLINE.md)。

数据来源：

- `auto-recall.mjs` / `auto-capture.mjs` / `session-start.mjs` 每轮写快照到 `~/.openviking/state/{last-recall,last-capture,last-session-event,daily-stats}.json`。
- `scripts/statusline.mjs` 读快照，再加 5 秒共享缓存的 `GET /health`。
- 网络调用 1s 硬超时；多个 CC session 共享缓存避免风暴。

关闭 / 调整：

- `OPENVIKING_STATUSLINE=off` ——不删注册，仅静默。
- `NO_COLOR=1` 或非 TTY ——自动去 ANSI 颜色。
- 彻底卸载：`jq 'del(.statusLine)' ~/.claude/settings.json > t && mv t ~/.claude/settings.json`。
- 已有自定义 statusline？安装时会询问替换 / 跳过 / 稍后手动 compose。

## 来源卡片

插件会在每次回答下方加一张卡片，列出这次回答用到的 OpenViking 来源：自动召回加进提示的内容，以及 Claude 自己搜索、读取的内容。卡片默认折叠成一行：

```
OV · 14 sources · 6 past events · 5 work memories · 2 team docs · 1 skill · 1 read in full  [Expand]
```

展开后列出全部来源和 Claude 自己的查询：

```
OV · 14 sources · … · 1 read in full  [Collapse]
  ◷ 10/3 发版检查清单补充 · found by Claude · read in full
  ⚙ openviking-release · auto-recalled
  …
Claude's own lookups
  ⌕ Searched “lark-daemon release” · 6 results
  ▤ Read 10/3 发版检查清单补充
```

- 分组：★ 偏好，◷ 过往事件，◆ 工作记忆（笔记、经验、你的 agent 的记忆），▤ 团队文档，⚙ Skill。
- **read in full**：Claude 用 `read`（MCP 工具，或 `ov read`/`cat`/`abstract`/`overview`）打开了这个文件。只出现在搜索结果里的不算。
- 没用到任何 OpenViking 内容的回答不显示卡片。
- 用卡片上的按钮展开或折叠单张卡片；用 `/openviking-usage expand` 或 `/openviking-usage collapse` 展开或折叠全部卡片。界面文字只有英文。

**前提。** 卡片是一个 Claude Code hooks 模块（`hooks/hooks.json` 里的 `modules`，代码在 `mods/usage/`），需要 Claude Code 2.1.286 或更高版本，并启用 hooks 模块。下面这些情况下，Claude Code 会忽略这个模块，命令 hook 照常运行：

- 版本不支持模块。
- 模块被关闭，例如设置了 `CLAUDE_CODE_ENABLE_FUNCTION_HOOKS=0`，或被灰度开关关闭。

在支持模块、但早于 2.1.286 的版本上，如果开启了模块，这个模块会因为用到 `session.append` 而加载失败。Claude Code 会记录这条错误，命令 hook 仍然照常注册。

**数据。** 模块读取本插件 `UserPromptSubmit` hook 返回的 `<openviking-context>` 块，以及 Claude 发起的 OpenViking MCP 调用和 `ov` CLI 调用。它不发网络请求，不向 OpenViking 写任何内容。

它在 Claude Code 的插件存储里保存每次回答的来源 URI、分数、脱敏后的搜索词，以及卡片所在对话行的 id 和该行回答文本的哈希（桌面端靠这个哈希找到卡片对应的回答），保留最近 20 个会话，所以 `claude --continue` 之后卡片还在。不保存 shell 命令和提示原文。卡片不会发给 Claude，不消耗 token。

**开发。** `claude plugin test examples/claude-code-memory-plugin` 会运行 `mods/usage/sources.test.ts`。用 `claude --plugin-dir examples/claude-code-memory-plugin` 启动一次、生成 `.claude-plugin/types/` 后，可以用 `tsc -p examples/claude-code-memory-plugin/mods/usage` 做类型检查。

## 调试日志

设置 `claude_code.debug: true` 或 `OPENVIKING_DEBUG=1`，hook 日志写到 `~/.openviking/logs/cc-hooks.log`。

- `auto-recall` 默认输出关键阶段 + 紧凑的 `ranking_summary`
- 仅在排查每候选打分时才把 `claude_code.logRankingDetails` 设为 `true`，否则非常啰嗦

## 故障排除

先跑内置的体检脚本——它会检查安装（marketplace、启用状态、hooks、MCP 接线）、解析后的配置（哪个文件生效、API key 脱敏展示）、连接（可达性、鉴权、`/mcp`）和最近的 hook 活动，并给每个问题附上修复建议：

```bash
node "$(jq -r '.plugins["openviking-memory@openviking"][0].installPath' ~/.claude/plugins/installed_plugins.json)/scripts/ov-memory-doctor.mjs"
```

也可以直接让 Claude 检查插件：`ov-memory-doctor` skill 会运行同一个脚本并解读报告。当 server 与插件在同一台机器上（loopback url）时，报告还会多一节 Server health：端口上是否有 server 在监听、ov.conf 里只有插件会读而 server 会拒绝启动的键、以及 `GET /ready`；其余 server 端检查（配置校验、实际 embedding 探测、native engine、磁盘）仍由 `openviking-server doctor` 负责。

| 症状                                         | 原因                                                  | 解决方案                                                                                       |
|----------------------------------------------|------------------------------------------------------|-----------------------------------------------------------------------------------------------|
| 插件没激活                                    | 找不到 `ov.conf` / `ovcli.conf`                       | 创建一个；或设 `OPENVIKING_MEMORY_ENABLED=1` 加上 URL/API_KEY 等环境变量                       |
| Hook 触发但召回为空                           | OpenViking 服务器没起 / URL 不对                      | `curl http://localhost:1933/health`（或你的远程 URL）                                          |
| 自动捕获抽取出 0 条记忆                        | `ov.conf` 里 embedding/extraction 模型配错            | 检查 `embedding` / `vlm` 配置；看服务器日志                                                    |
| MCP 工具命中了错误的服务器                    | `ovcli.conf` / 环境变量过期，或改完配置没有重启 Claude Code | 见 [配置 MCP](#配置-mcp)，核对 `~/.openviking/ovcli.conf` 后重启 Claude Code                    |
| 远程鉴权 401 / 403                            | API key / account / user 头错配                      | 核对 `OPENVIKING_API_KEY`、`OPENVIKING_ACCOUNT`、`OPENVIKING_USER`（或 `ov.conf` 对应字段）    |
| `Stop` hook 超时                              | 服务器慢 + 同步写路径                                 | 保持 `writePathAsync: true`（默认），或调大 `hooks/hooks.json` 里的 `Stop` 超时               |
| 旧上下文反复出现在 OV 里                      | 早期版本把召回块当成用户消息回写了                    | 升级到当前版本——`auto-capture` 现在推送前会剥离 `<openviking-context>`                      |
| 日志太吵                                      | `logRankingDetails: true` 没关                        | 设为 `false`；日志里仍保留紧凑的 `ranking_summary`                                             |

## 与 Claude Code 内置记忆的对比

Claude Code 自带 `MEMORY.md` 文件系统，本插件**与之互补**：

| 特性     | 内置 `MEMORY.md`            | OpenViking 插件                                |
|----------|-----------------------------|-----------------------------------------------|
| 存储     | 扁平 markdown               | 向量数据库 + 结构化抽取                        |
| 搜索     | 整体加载进上下文            | 语义相似度 + 排序 + token 预算                |
| 范围     | 单项目                      | 跨项目、跨会话、peer 维度                      |
| 容量     | ~200 行（受上下文限制）     | 不受限（服务端存储）                           |
| 抽取     | 手写规则                    | LLM 驱动的实体 / 偏好 / 事件抽取               |
| 子 agent | 与父共享                    | 隔离 session + peer 维度捕获                   |

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
     │                  │  MCP tools (stdio proxy→/mcp)              │
     │                  │ find/search/recall/remember/… │              │
     └─────────────────►│                             │              │
        OV session      └─────────────────────────────►              │
        context inject                                └──────────────┘
```

没有 TypeScript 编译步骤，也没有运行时 npm 引导。Hook 都是直接走 HTTP 调 OpenViking 的 `.mjs` 文件；MCP 使用 `servers/mcp-proxy.mjs` 作为零依赖 stdio 桥接，转发到 OpenViking 服务器自身的 `/mcp` endpoint。

首次接触时创建一个持久化的 OpenViking session，整个 Claude Code 会话期间复用。OV session ID 是 `cc-<cc_session_id>`（CC session_id 原样保留，不做哈希），所以 resume / compact / 多 hook 事件都打到同一个 session。归档与记忆抽取由客户端触发：`Stop` hook 在服务端报告的 pending tokens 超过 `commitTokenThreshold`（默认 20000）时 commit，`PreCompact` / `SessionEnd` / `SubagentStop` 则无条件 commit。

### 各 hook 职责

| Hook                  | 触发时机                              | 行为                                                                                              |
|-----------------------|--------------------------------------|--------------------------------------------------------------------------------------------------|
| `UserPromptSubmit`    | 每个用户回合                          | 搜 OV → 排序 → 在 token 预算内注入 `<openviking-context>` 块                                      |
| `Stop`                | Claude 完成一次响应                   | 解析 transcript → 把新的用户回合推到 OV session → pending tokens 超阈值时 commit                  |
| `SessionStart`        | 新建 / resume / compact 后的会话      | 注入 `profile.md`、记忆索引和 `<available-skills>`；`resume`/`compact` 时再注入最新的 archive overview |
| `PreCompact`          | Claude Code 重写 transcript 之前      | 在 CC 改 transcript 之前先把 pending 提交为归档                                                  |
| `SessionEnd`          | Claude Code 会话关闭                  | 最后一次 commit                                                                                  |
| `SubagentStart`       | 父 session 通过 Task 工具孵化子 agent | 为子 agent 派生隔离的 OV session ID，写 start state                                              |
| `SubagentStop`        | 子 agent 结束                         | 读子 agent transcript → 推到带子 agent peer 身份的隔离 session → commit                          |
| `PreToolUse`          | 原生 `Read` / `Glob` / `Grep` / `Edit` / `Write` 的路径是 `viking://` URI | 拒绝该调用，提示 Claude 改用对应的 OpenViking MCP 工具；对 skill URI（`viking://~/skills/...`、`viking://user/<id>/skills/...`、`viking://agent/skills/...`）的 `Write` / `Edit` 会被引导到 `add_skill` |
| `PreToolUse`          | `Bash` 命令里带 `viking://` URI | 照常执行命令，并附一条提醒：如果本意是访问 OpenViking 内容，应改用 OpenViking MCP 工具 |
| `PreToolUse`          | `Bash` 命令里带凭据引用，或调用 `privacy_resolve` | 可选的 [secret exec](#secret-exec)：把命令包进 runner；拒绝 `privacy_resolve` |
| `PostToolUse`         | `Read` 读到 `SKILL.md` 文件           | 可选（默认关闭）：OV 有相关 skill 经验记忆时注入经验块                                           |

### 异步写路径

`Stop`、`SessionEnd`、`SubagentStop` 用 detach-worker 模式：父 hook drain stdin，立刻向 stdout 输出 `{decision:"approve"}` 解封 Claude Code，然后 spawn 一个 detached 子进程做 HTTP commit。用户从不等 OV。`PreCompact` 必须保持同步，因为 Claude Code 紧接着会改 transcript。

调试时如果需要严格顺序，把 `claude_code.writePathAsync` 设为 `false`。

### 防止记忆自污染

`auto-capture` 在把每个 turn 推给 OV 之前，会剥掉 `<openviking-context>`、`<system-reminder>`、`<relevant-memories>`、`[Subagent Context]` 这些注入块。否则插件本轮注入的召回上下文会在下一轮被当成"用户消息"再次写回 OV，形成自我引用的污染回路。

### 服务器暴露的 MCP 工具

插件的 `.mcp.json` 启动本地 stdio 代理，代理再连到 OpenViking 服务器原生 HTTP MCP endpoint `/mcp`。Claude 可按需调用服务器提供的检索、记忆、资源、skill、watch 和文件系统工具。创建或替换 skill 用 `add_skill`：`write` 和 `edit` 会拒绝你自己的 `skills/` 子树，`add_resource` 也不接受 skill 目标路径。

完整工具清单和参数详见 [MCP 集成指南](../../docs/zh/guides/06-mcp-integration.md)。

### 插件结构

```
claude-code-memory-plugin/
├── .claude-plugin/
│   └── plugin.json          # plugin manifest
├── hooks/
│   └── hooks.json           # 9 个 hook 注册 + 来源卡片模块
├── commands/
│   └── ov.md                # /ov 状态命令
├── skills/
│   ├── openviking-memory/   # 记忆工具使用指南
│   ├── openviking-skills/   # 查找、添加、共享和迁移 OpenViking skill
│   ├── ov-experience-memory/
│   └── ov-memory-doctor/    # 安装 / 配置 / 连接 / 本机 server 排障
├── mods/
│   └── usage/               # 来源卡片（Claude Code hooks 模块）
├── servers/
│   └── mcp-proxy.mjs        # stdio -> OpenViking /mcp 桥接
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
│   ├── secret-exec.mjs      # PreToolUse：包裹使用凭据引用的命令
│   ├── secret-run.mjs       # 这些命令经过的 runner
│   ├── ov-status.mjs        # /ov 状态报告
│   ├── ov-memory-doctor.mjs # 体检脚本（ov-memory-doctor skill）
│   └── lib/
│       ├── ov-session.mjs   # OV HTTP 客户端 + session 帮助 + bypass 检查
│       └── async-writer.mjs # 写路径 detach-worker 帮助
├── .mcp.json                # MCP 配置（本地 stdio 代理）
├── package.json             # 仅 type:module 标记，无运行时依赖
└── README.md
```

## License

Apache-2.0 — 同 [OpenViking](https://github.com/volcengine/OpenViking)。
