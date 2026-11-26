# 社区插件

社区维护的各运行时集成。各插件在目标平台、集成深度和维护状态上各有差异，使用前请先阅读各自的 README。

## ZCode 记忆集成

源码：[examples/agent-hook-plugin](https://github.com/volcengine/OpenViking/tree/main/examples/agent-hook-plugin)

ZCode 社区集成通过配置驱动的生命周期 Hook、OpenViking MCP 服务和 5 个 skill 提供跨项目、跨会话记忆：

- **SessionStart** 注入用户画像。
- **UserPromptSubmit** 召回相关记忆。
- **PreToolUse** 将直接读取 `viking://` 的操作引导至 MCP 工具。
- **Stop** 在 detached worker 中捕获 rollout 里的未处理回合，并 commit OpenViking session。

ZCode 不提供 `PreCompact`、`SessionEnd` 和 subagent 生命周期 Hook。因此该适配器在 `Stop` 时 commit，以 ZCode rollout 文件作为权威增量对话源，只有 rollout 文件不可用时才回退到 Hook stdin。

### 安装

前置条件：Node.js 18+、正在运行的 OpenViking 服务，以及 ZCode。

连接托管实例时，选择 **OpenViking 托管服务**，再选择服务商：

- **Volcengine**：输入服务 API Key，安装器会提供服务地址。
- **BytePlus**：输入 API Key。详见 [BytePlus 接入指南](../guides/27-byteplus-setup.md)。

旧版安装器连接 BytePlus 时使用**自定义 URL**。

```bash
curl -fsSL https://openviking.ai/install | bash
# AI agent 如不确定是否执行，可先阅读脚本头部，其中写明了它会做什么、如何核实。
```

安装器通过 `~/.zcode/` 或 `zcode` 二进制检测 ZCode，将运行时安装到 `~/.openviking/agent-integrations/zcode/`，并把 Hook 与 MCP 配置合并到 `~/.zcode/cli/config.json`，再把 `openviking-memory`、`openviking-skills`、`ov-experience-memory`、`openviking-rule-search` 和 `ov-memory-doctor` 安装到 `~/.zcode/skills/<name>`。你修改过的 skill，或已有的同名非托管目录，在升级和卸载时保留。

重启 ZCode 后，请确认：

- `~/.zcode/cli/config.json` 包含 `hooks.enabled: true`、`hooks.events` 下的 OpenViking 条目，以及 `mcp.servers.openviking`。
- `~/.zcode/skills/` 下有上述 5 个 skill 目录，每个目录包含 `SKILL.md`。
- 设置 `OPENVIKING_DEBUG=1` 后，可在 `~/.openviking/logs/zcode-hooks.log` 查看诊断日志。

| 现象 | 原因 | 处理方式 |
|------|------|----------|
| Hook 未执行 | Hook 配置被禁用或已过期 | 重跑安装器并重启 ZCode |
| 召回为空 | OpenViking 不可用或记忆尚未提取 | 检查 `curl http://127.0.0.1:1933/health`，并等待提取完成 |
| MCP 工具未出现 | MCP proxy 启动失败 | 检查 `~/.zcode/cli/config.json` 中 `mcp.servers.openviking` 的绝对路径命令 |
| 重复捕获 | 旧安装留下了重复 Hook 条目 | 先运行 `curl -fsSL https://openviking.ai/install \| bash -s -- --uninstall --yes --harness zcode`，再重新安装 |

实现细节与当前已验证的 ZCode 假设见插件目录中的 [README](https://github.com/volcengine/OpenViking/tree/main/examples/agent-hook-plugin) 和 [DESIGN.md](https://github.com/volcengine/OpenViking/blob/main/examples/agent-hook-plugin/DESIGN.md)。

安装完成后，在 ZCode 中新开一个会话，让它按 https://docs.openviking.net/agents/zh/onboarding.md 执行。它会检查连接，并带你完成首次使用，详见[一键接入](../getting-started/07-agent-onboarding.md)。

## Kimi Code 记忆集成

源码：[examples/agent-hook-plugin](https://github.com/volcengine/OpenViking/tree/main/examples/agent-hook-plugin)

Kimi Code 集成是原生 managed plugin。它复用 OpenViking 的共享 Hook 运行时，只在适配层保留 Kimi 特有的事件映射、wire transcript 解码、输出格式和 commit 策略：

- **UserPromptSubmit** 召回记忆，并输出 Kimi 可直接注入的原始文本。
- **PreToolUse** 拒绝 Read/Glob/Grep 直接访问 `viking://` URI。
- **Stop**、**PreCompact** 和 **SessionEnd** 增量捕获 `wire.jsonl` 回合；**Interrupt** 同步执行同一捕获流程，全部 OpenViking 请求共用 2 秒总预算。
- 原生插件 manifest 提供 OpenViking MCP server 和插件根目录 `skills/` 下的 `openviking-memory`、`openviking-skills`、`ov-experience-memory`、`openviking-rule-search`、`ov-memory-doctor` 5 个 skill，不修改 Kimi 的旧式配置文件。

### 安装

前置条件：Node.js 18+、正在运行的 OpenViking 服务，以及 Kimi Code CLI。

连接托管实例时，选择 **OpenViking 托管服务**，再选择服务商：

- **Volcengine**：输入服务 API Key，安装器会提供服务地址。
- **BytePlus**：输入 API Key。详见 [BytePlus 接入指南](../guides/27-byteplus-setup.md)。

旧版安装器连接 BytePlus 时使用**自定义 URL**。

```bash
curl -fsSL https://openviking.ai/install | bash
# AI agent 如不确定是否执行，可先阅读脚本头部，其中写明了它会做什么、如何核实。
```

安装器会在 `$KIMI_CODE_HOME/plugins/managed/openviking-memory/`
下组装自包含运行时（Kimi home 默认为 `~/.kimi-code/`），并且只更新
`plugins/installed.json` 中的 `openviking-memory` 记录，不动其他插件。
重跑同一命令可升级。只卸载该插件：

```bash
curl -fsSL https://openviking.ai/install | bash -s -- --uninstall --yes --harness kimicode
```

已验证的宿主契约和版本见
[`hosts/kimicode/DESIGN.md`](https://github.com/volcengine/OpenViking/blob/main/examples/agent-hook-plugin/hosts/kimicode/DESIGN.md)。

安装完成后，在 Kimi Code 中新开一个会话，让它按 https://docs.openviking.net/agents/zh/onboarding.md 执行。它会检查连接，并带你完成首次使用，详见[一键接入](../getting-started/07-agent-onboarding.md)。

## Antigravity 记忆集成

源码：[examples/agent-hook-plugin](https://github.com/volcengine/OpenViking/tree/main/examples/agent-hook-plugin)

Antigravity 集成同时服务 Antigravity 桌面 app 与 `agy` CLI，两者从 `~/.gemini/config/plugins/` 加载同一批插件：

- **SessionStart** 注入用户画像。
- **PreInvocation** 在每轮的第一次模型调用时召回相关记忆。Antigravity 在每次模型调用前都会运行这个 Hook，工具循环中的后续调用会跳过召回。
- **PreToolUse** 拒绝位置参数为 `viking://` URI 的文件工具调用，并引导至 OpenViking MCP 工具。Shell 命令（`run_command`）不检查。
- **Stop** 在后台进程中采集对话 transcript 里已完成的步骤，然后 commit OpenViking 会话。

Antigravity 的 `hooks.json` 格式与 Claude Code 不同：顶层 key 是 Hook 名，上下文以 `injectSteps` 系统消息返回，因此不能直接复用 Claude Code 的 `hooks.json`。

插件还带有 OpenViking MCP 服务和 `openviking-memory`、`openviking-skills`、`ov-experience-memory`、`openviking-rule-search`、`ov-memory-doctor` 5 个 skill：Agent 可以通过 MCP 工具主动检索和管理记忆，每个 skill 也可以用 `/<name>` 调用。

### 安装

前置条件：Node.js 18+、运行中的 OpenViking 服务，以及 Antigravity app 或 `agy`。

此非交互命令使用 `~/.openviking/ovcli.conf` 中已保存的连接。请先运行 `ov config`，选择 **OpenViking 托管服务**，再选择 **Volcengine**（服务 API Key）或 **BytePlus**（输入 API Key）。详见 [BytePlus 接入指南](../guides/27-byteplus-setup.md#连接-cli)。

```bash
curl -fsSL https://openviking.ai/install | bash -s -- --yes --harness antigravity
```

安装脚本把插件写到 `~/.gemini/config/plugins/openviking-memory`，运行时写到 `~/.openviking/agent-integrations/antigravity`。如果同名目录已存在且不是 OpenViking 写入的，安装脚本会保留它并停止。连接配置与其他集成一样读取 `~/.openviking/ovcli.conf`。插件目录包含 `plugin.json`、`hooks.json`、`mcp_config.json` 和 `skills/`。如果 `~/.gemini/config/mcp_config.json` 已经定义了名为 `openviking` 的 MCP 服务，安装脚本保留你的服务，不写插件的 `mcp_config.json`，避免每个工具出现两次。

重启 Antigravity app 或开启新的 `agy` 会话后，确认：

- `~/.gemini/antigravity-cli/log/` 下的 `agy` 日志出现 `loaded 1 named hooks`；
- 一轮对话结束后，`~/.openviking/hook-state/antigravity/<conversation-id>.json` 记录了 `lastStep`；
- 插件目录下有 `skills/openviking-memory/SKILL.md`，并且除非你自己的配置里已有 `openviking` 服务，还有 `mcp_config.json`。

卸载：

```bash
curl -fsSL https://openviking.ai/install | bash -s -- --uninstall --yes --harness antigravity
```

## AstrBot 插件

[AstrBot](https://github.com/AstrBotDevs/AstrBot) 是一个多平台 IM Bot 框架，支持 QQ、Telegram、Discord、飞书等 20+ 平台。

源码：[astrbot_plugin_openviking_memory](https://github.com/t0saki/astrbot_plugin_openviking_memory)

为 AstrBot 提供群聊/私聊的自动捕获、LLM 请求前的语义召回，以及可配置的 venue 记忆隔离。

**安装**：在 AstrBot WebUI → 插件市场搜索 **OpenViking Memory** 并安装；或从链接安装：`https://github.com/t0saki/astrbot_plugin_openviking_memory.git`

当前插件要求 AstrBot 4.23.1+ 和兼容的 OpenViking 服务。`global` 使用 User Key；`venue` 需要 Admin Key 创建用户。迁移旧配置前，先核对上方插件 README。

**主要特性**：

- 基于 hooks 的自动召回与捕获，模型不需要主动调用工具
- `self_scope=global` 共用一个 Bot 身份，`venue` 为群/私聊分别创建用户；每个人以 peer 表示。旧版 `isolation_mode` 会映射到这两种范围。
- 按消息数、token 阈值或空闲超时自动 commit
- 首次接入群聊时自动拉取平台历史消息入库

## Open WebUI tool server

[Open WebUI](https://github.com/open-webui/open-webui) 是一个自托管的 AI 聊天界面。

源码：[examples/openwebui-plugin](https://github.com/volcengine/OpenViking/tree/main/examples/openwebui-plugin)

一个独立的 FastAPI server，把 OpenViking 的一组精选端点以 OpenAPI tools 形式暴露，让 Open WebUI 作为原生工具调用。部署与端点说明见 README。

## 更多示例

[examples/](https://github.com/volcengine/OpenViking/tree/main/examples) 目录下还有 Agent 插件之外的部署与集成示例——Grafana 面板、Kubernetes Helm chart、多租户配置、快照流程和 SDK 片段等。

## 参见

- [集成能力参考](./16-capability-reference.md)
