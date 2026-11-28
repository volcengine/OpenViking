# OpenViking Memory Plugin for Claude Code

为 Claude Code 提供长期语义记忆，由 [OpenViking](https://github.com/volcengine/OpenViking) 驱动。每次用户输入前自动召回，每轮对话结束后自动捕获；模型不需要主动调用任何 MCP 工具。

本 README 介绍插件的安装，以及只有 Claude Code 集成才有的功能。召回、捕获、commit、会话开场上下文、离线队列和所有设置与其他宿主共用，文档见：

- [Claude Code 集成指南](../../../../docs/zh/agent-integrations/02-claude-code.md)
- [能力参考](../../../../docs/zh/agent-integrations/16-capability-reference.md)（共享行为、设置、宿主事件）
- [客户端配置](../../../../docs/zh/configuration/02-client.md)（`ovcli.conf` 里的插件设置）

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

本地起一个或者指向远程：[快速开始指南](../../../../docs/zh/getting-started/02-quickstart.md) 涵盖两种模式，也讲了远程使用时怎么签发 API key。默认端口 `1933`；本地模式无需鉴权。

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

如果你已经在维护 `ov.conf`，插件也读它——优先级链和全部设置见[客户端配置](../../../../docs/zh/configuration/02-client.md)。

#### 3. 安装插件

**远程 marketplace（推荐）** —— 无需 clone 仓库。仓库根目录自带 `.claude-plugin/marketplace.json`，其条目通过 `git-subdir` 拉取本插件：

```bash
claude plugin marketplace add https://raw.githubusercontent.com/volcengine/OpenViking/main/.claude-plugin/marketplace.json
claude plugin install openviking-memory@openviking
```

（`claude plugin marketplace add volcengine/OpenViking` 也可以，但会把整个仓库 clone 下来作为 marketplace。）

如果跳过了第 2 步，装完后再配置连接：手写 `~/.openviking/ovcli.conf`、运行插件自带的交互向导 `node <插件目录>/hosts/claude-code/setup.mjs`，或直接跑一行安装脚本。

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
PLUGIN_DIR="$(pwd)/examples/agent-hook-plugin"

# stdio MCP 代理 —— 自己读 ovcli.conf / OPENVIKING_*，不再需要拼 header。
claude mcp remove openviking -s user 2>/dev/null
claude mcp add --scope user -e OPENVIKING_HOOK_SOURCE=claude-code openviking -- node "$PLUGIN_DIR/servers/mcp-proxy.mjs"

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

## 验证

- 在 Claude Code 里运行 `/openviking-memory:ov`：输出服务器、身份、最近一次注入 / 召回和各项开关（`hosts/claude-code/ov-status.mjs`）。
- 服务器有响应后，输入框下方的状态行显示 `OV ✓`（见 [Statusline 状态行](#statusline-状态行)）。
- 运行体检脚本（见[故障排除](#故障排除)）。

## Hook 超时

`hooks/hooks.json` 默认值：

| Hook | 超时 | 备注 |
|------|------|------|
| `SessionStart` | `120s` | 充裕，因为 resume / compact 可能拉一个较大的 archive overview |
| `UserPromptSubmit` | `60s` | 给本地压缩器留出完成时间；其自身超时更短，失败时可在 hook 截止前安全降级 |
| `PreToolUse` | `5s` | `scripts/uri-guard.mjs claude-code` |
| `PostToolUse`（`Read`） | `5s` | Skill 经验，默认关闭 |
| `Stop` | `45s` | 捕获；detach 到后台，用户无需等待 |
| `PreCompact` | `30s` | Claude Code 改写 transcript 之前同步 commit |
| `SessionEnd` | `30s` | 最终 commit；detach |
| `SubagentStop` | `45s` | 子 agent 捕获并 commit；detach |

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

- 每个事件之后，hook（`hosts/claude-code/host.mjs`，经由 `state.mjs`）写快照到 `~/.openviking/state/{last-recall,last-capture,last-session-event,daily-stats}.json`。
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

**前提。** 卡片是一个 Claude Code hooks 模块（`hooks/hooks.json` 里的 `modules`，入口 `mods/register.ts`，代码在 `mods/usage/`），需要 Claude Code 2.1.286 或更高版本，并启用 hooks 模块。下面这些情况下，Claude Code 会忽略这个模块，命令 hook 照常运行：

- 版本不支持模块。
- 模块被关闭，例如设置了 `CLAUDE_CODE_ENABLE_FUNCTION_HOOKS=0`，或被灰度开关关闭。

在支持模块、但早于 2.1.286 的版本上，如果开启了模块，这个模块会因为用到 `session.append` 而加载失败。Claude Code 会记录这条错误，命令 hook 仍然照常注册。

**数据。** 模块读取本插件 `UserPromptSubmit` hook 返回的 `<openviking-context>` 块，以及 Claude 发起的 OpenViking MCP 调用和 `ov` CLI 调用。它不发网络请求，不向 OpenViking 写任何内容。

它在 Claude Code 的插件存储里保存每次回答的来源 URI、分数、脱敏后的搜索词，以及卡片所在对话行的 id 和该行回答文本的哈希（桌面端靠这个哈希找到卡片对应的回答），保留最近 20 个会话，所以 `claude --continue` 之后卡片还在。不保存 shell 命令和提示原文。卡片不会发给 Claude，不消耗 token。

**开发。** `claude plugin test examples/agent-hook-plugin` 会运行 `mods/usage/sources.test.ts` 和 `mods/seal/seal.test.ts`。提示封存的判断逻辑另用 `node --test` 测试，见 `hosts/claude-code/prompt-seal.test.mjs` 和 `hosts/claude-code/seal-prompt.test.mjs`。用 `claude --plugin-dir examples/agent-hook-plugin` 启动一次、生成 `.claude-plugin/types/` 后，可以用 `tsc -p examples/agent-hook-plugin/mods/usage`（或 `mods/seal`）做类型检查。

## 封存密钥

默认关闭，在你的机器上运行，需要 OpenViking 服务端 0.5.7 或更新版本。`ovcli.conf` 里有两个设置，各有一个环境变量覆盖：

```json
{ "plugin": { "claude_code": { "sealSecrets": true, "enrollToolSecrets": false } } }
```

- `sealSecrets`（`OPENVIKING_SEAL_SECRETS`）打开封存。
- `enrollToolSecrets`（`OPENVIKING_ENROLL_TOOL_SECRETS`）让工具结果也带登记发送，第一次出现在命令输出或文件里的密钥也会存下来。`sealSecrets` 关闭时不起作用。

打开 `sealSecrets` 后，Claude 读到的是 `{{ov_privacy:…}}` 引用而不是密钥，工具拿到的是真实值：

- **你的提示。** 每条提示在 Claude Code 发出之前先发到 OpenViking（`POST /api/v1/privacy-configs/seal`）。已经存进 Privacy 的密钥会换成对应的引用。服务端探测器认得出的 token（内置规则加上你的 `custom_rules`，去掉 `never_mask`）会登记成 `auto` 类别下的新条目，比如 `auto/github-k3j2m8`，同样换成引用。提示里含有这类临时条目时，Claude 会收到一句提示：除非明显只用一次，先把条目存到正式名字下并写一句用途说明，之后使用保存时返回的引用（比如配合 `ov privacy run`），并告诉你存了什么。斜杠命令和空提示不处理。
- **工具结果。** 工具结果里的每个字符串（Bash 输出、Read 读到的文件、MCP 工具的回答、失败命令的报错）在 Claude 读到之前都会封存。已保存的密钥会被替换；新密钥只在打开 `enrollToolSecrets` 时登记。会话记录里存的是封存后的结果。
- **工具参数。** Claude 在工具调用里写的引用，会在工具运行前换成真实值，所以命令或文件拿到的是真正的密钥，对话里保留的仍是 Claude 写的引用。只替换指向你自己 `credential` 或 `auto` 条目的完整引用，并且要服务端能解析出值、这个值再封存回来是同一个引用。含有 `ov privacy` 命令的字符串、OpenViking 自己的工具，以及把参数交给模型的工具（`Agent`、`Task`、`SendMessage`、`Workflow`、`Skill`）保留原样的引用。
- **附加和 `@` 提及的文件，以及提醒。** Claude Code 为模型注入的文本（提及文件的内容、工作中发现的 `CLAUDE.md`、各种提醒、其他 hook 的上下文）封存时不登记。会话记录里保存的是 Claude Code 自己的记录，仍是原文。

运行方式和出错时的行为：

- **每个会话读一次。** 插件每个会话运行一次 `node hosts/claude-code/seal-prompt.mjs`，读取设置、连接服务端的方式（和其他 hook 相同的 URL 和请求头），以及服务端能否封存：辅助脚本从服务端的 `/health` 读取版本，和插件自带的功能表（`lib/server-features.json`）比较，封存需要 0.5.7（`0.5.7.dev44` 这样的开发版算作 0.5.7）。之后所有封存和解析请求都由 hooks 模块通过 Claude Code 的 HTTP 接口直接发出。改了设置要到新会话才生效（`/clear` 也算新会话）。提示被拦下之后，下一条提示会重新读取这些内容，所以服务端能封存之后封存就恢复。关闭时，第一次读取之后不再运行任何东西。
- **无法封存时会拦下。** 包括：服务端版本低于 0.5.7；`/health` 失败（版本未知）；封存或解析请求失败（服务端连不上、返回错误或格式不对的回答、超时，每次请求 10 秒）；`OPENVIKING_SEAL_SECRETS` 打开了封存，但读取设置的辅助脚本没有给出结果。提示不会发出，Claude Code 会显示原因（版本问题会写出要求的版本和实际版本）和怎么办：升级服务端或恢复连接，或者关闭 `sealSecrets`。工具结果换成一句说明，告诉 Claude 结果被扣下了（工具已经运行过）；引用解析不了的工具调用会被拦下并给出原因；附件换成一句说明。对版本太旧或版本未知的服务端，根本不发封存请求。`OPENVIKING_SEAL_SECRETS=0` 不运行任何东西直接关闭封存，插件自己的辅助脚本坏了也能关掉。
- **会话说明**（告诉 Claude 引用是怎么回事的那段文字）随 hooks 模块在一个会话里封存的第一条提示一起发出，压缩（compact）之后或模块重新加载之后的第一条提示再带一次。
- **开销。** 每条提示、每个工具结果、每个注入的附件各一次服务端请求（Claude Code 对每个附件只问一次并记住结果），工具调用里的每个引用再加一次解析和一次校验。
- **不覆盖的内容。** 系统提示、斜杠命令参数、skill 正文，以及打开封存之前写下的历史，会原样到达 Claude。OpenViking 自己的工具按名字里含 `openviking` 识别：把 OpenViking MCP 服务器注册成别的名字时，它的 `privacy_upsert`、`write` 或 `remember` 参数里的引用会在调用前换成真实值。
- **误报的撤销。** 删除这个条目（`ov privacy delete auto <target>`），再把这个值加进 `never_mask`，以后就不会再登记。
- **前提。** 封存是插件 hooks 模块的一部分（`mods/seal/`），前提和[来源卡片](#来源卡片)相同。Claude Code 不加载这个模块时封存不会运行，所有内容原样发出。读取设置需要 Claude Code 的进程接口（CLI 有）。没有这个接口（或辅助脚本没有给出结果）时，不管 `ovcli.conf` 怎么写，封存都保持关闭，也不显示提示；在这类环境里设置 `OPENVIKING_SEAL_SECRETS=1`，提示会被拦下，并说明读不到设置。
- 封存相关的设置都不是工作区设置，仓库里的 `.openviking/config.json` 改不了它们。

用本地服务端试一下：先存一个值（`ov privacy upsert credential demo --values-json '{"token":"<value>"}'`），打开 `sealSecrets`，启动 `claude`，让它 `echo` 这个值：它读到的回答里是 `{{ov_privacy:…}}`。再让它用这个引用运行 `printf '%s' '{{ov_privacy:…}}' > /tmp/t`：`/tmp/t` 里是真实值。

## 本地召回压缩器

`recallCompress` 默认 `off`。设为 `client` 时，召回摘要（digest）通过 `claude -p` 在本地生成（默认 Haiku、low effort，可用 `recallCompressModel` 换模型），token 开销走你自己的订阅。设为 `auto` 时，找到可用的 `claude` CLI 就用本地压缩器，否则用服务端的摘要。压缩子进程运行时关闭所有 OpenViking hook，不会递归；压缩失败时回退为未压缩的上下文块。见[召回摘要](../../../../docs/zh/agent-integrations/16-capability-reference.md#_3-2-5-recall-digest)。

## Skill 经验

一个可选的 `PostToolUse` hook，匹配 `Read`（`hosts/claude-code/skill-experience.mjs`）。打开 `skillExperience`（`OPENVIKING_SKILL_EXPERIENCE`，默认关闭）后，读取 skills 目录下的 `SKILL.md` 时，会注入一小段 OpenViking 里相关的经验记忆，最多 `skillExperienceLimit` 条（默认 3）。

## 子 agent 捕获

Claude Code 是唯一捕获子 agent 的 hook 宿主：`SubagentStop` 时插件读取子 agent 的 transcript，捕获到它自己的会话 `cc-<session_id>__subagent-<agent_id>`，然后 commit。`skipSubagentSessions` 可关闭。见[子 agent](../../../../docs/zh/agent-integrations/16-capability-reference.md#_3-3-5-subagent-session-comparison)。

## 故障排除

先跑内置的体检脚本——它会检查安装（marketplace、启用状态、hooks、MCP 接线）、解析后的配置（哪个文件生效、API key 脱敏展示）、连接（可达性、鉴权、`/mcp`）和最近的 hook 活动，并给每个问题附上修复建议：

```bash
node "$(jq -r '.plugins["openviking-memory@openviking"][0].installPath' ~/.claude/plugins/installed_plugins.json)/hosts/claude-code/ov-memory-doctor.mjs"
```

也可以直接让 Claude 检查插件：`ov-memory-doctor` skill 会运行同一个脚本并解读报告。当 server 与插件在同一台机器上（loopback url）时，报告还会多一节 Server health：端口上是否有 server 在监听、ov.conf 里只有插件会读而 server 会拒绝启动的键、以及 `GET /ready`；其余 server 端检查（配置校验、实际 embedding 探测、native engine、磁盘）仍由 `openviking-server doctor` 负责。

设置 `OPENVIKING_DEBUG=1`，hook 日志写到 `~/.openviking/logs/cc-hooks.log`。

## 插件结构

Claude Code 使用插件根目录的默认位置；`hosts/claude-code/` 下是只有 Claude Code 才有的文件。

```
agent-hook-plugin/
├── .claude-plugin/plugin.json   # Claude Code 插件 manifest
├── hooks/hooks.json             # hook 注册 + hooks 模块
├── .mcp.json                    # MCP 服务器配置（本地 stdio 代理）
├── commands/ov.md               # /openviking-memory:ov
├── mods/
│   ├── register.ts              # hooks 模块入口
│   ├── seal/                    # 封存密钥
│   └── usage/                   # 来源卡片
├── scripts/
│   ├── hook.mjs                 # 所有 hook 事件：`hook.mjs <Event> claude-code`
│   ├── uri-guard.mjs            # PreToolUse 守卫
│   └── statusline.mjs           # 状态行
├── servers/mcp-proxy.mjs        # stdio -> OpenViking /mcp 桥接
├── skills/                      # 自带的 skill
├── lib/                         # 共享库和 hook 流程（lib/flow/）
└── hosts/claude-code/
    ├── host.mjs                 # 流程如何读取 Claude Code 的 payload 和 transcript
    ├── state.mjs                # 状态行快照
    ├── compressor.mjs           # 本地召回压缩器
    ├── ov-status.mjs            # /openviking-memory:ov 报告
    ├── ov-memory-doctor.mjs     # 诊断脚本（ov-memory-doctor skill）
    ├── skill-experience.mjs     # PostToolUse(Read) skill 经验
    ├── seal-prompt.mjs          # mods/seal 的设置辅助脚本
    ├── setup.mjs                # 交互式 ovcli.conf 向导
    └── STATUSLINE.md            # 状态行说明
```

## License

Apache-2.0，与 [OpenViking](https://github.com/volcengine/OpenViking) 相同。
