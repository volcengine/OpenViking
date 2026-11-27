# Claude Code

为 [Claude Code](https://docs.claude.com/zh-CN/docs/claude-code/overview) 添加跨项目、跨会话（session）的长期记忆功能。安装完成后，每轮对话均会自动召回相关记忆并捕获新内容，无需模型主动调用任何工具。

源码：[examples/agent-hook-plugin](https://github.com/volcengine/OpenViking/tree/main/examples/agent-hook-plugin)（Claude Code 专属文件在 [hosts/claude-code](https://github.com/volcengine/OpenViking/tree/main/examples/agent-hook-plugin/hosts/claude-code)） | [博客：动机与效果展示](https://blog.openviking.ai/post/openviking-coding-agent/)

## 安装

安装脚本会询问要安装的 harness 和 OpenViking 凭据；重复运行是安全的。

连接托管实例时，选择 **OpenViking 托管服务**，再选择服务商：

- **Volcengine**：输入服务 API Key，安装器会提供服务地址。
- **BytePlus**：输入 API Key。详见 [BytePlus 接入指南](../guides/27-byteplus-setup.md)。

旧版安装器连接 BytePlus 时使用**自定义 URL**。

```bash
curl -fsSL https://openviking.ai/install | bash
# AI agent 如不确定是否执行，可先阅读脚本头部，其中写明了它会做什么、如何核实。
```

> **插件更新**：Claude Code 2.1.224 及以上版本，安装脚本注册的是 OpenViking 文档站托管的 marketplace 并开启自动更新，新版插件在后台下载，重启 Claude Code 后生效。更早的 Claude Code 注册的是本地目录 marketplace，无法自动更新——更新请重跑安装脚本。

<details>
<summary><b>手动安装</b></summary>

1. **配置连接**——编写 `~/.openviking/ovcli.conf`（`url`、`api_key`，可选 `account`/`user`），或在安装后运行自带向导 `node <plugin-dir>/hosts/claude-code/setup.mjs`。本地无认证服务（`http://127.0.0.1:1933`）可跳过此步。

2. **从远程 marketplace 安装插件**：

   ```bash
   claude plugin marketplace add https://raw.githubusercontent.com/volcengine/OpenViking/main/.claude-plugin/marketplace.json
   claude plugin install openviking-memory@openviking
   ```

3. **启动 Claude Code**，运行 `/mcp` 确认 OpenViking 条目已连接。

Claude Code 低于 2.0 时安装脚本会跳过并提示升级。

</details>

安装完成后，新开一个 Claude Code 会话，让它按 https://docs.openviking.net/agents/zh/onboarding.md 操作。它会检查连接，并带你完成首次使用；参见[一键接入](../getting-started/07-agent-onboarding.md)。

## 验证

启动 `claude`，然后：

- `/plugins` → **openviking-memory** 出现在 "Installed" 下，下方 **openviking** MCP 已连接。
- `/mcp` → OpenViking 条目显示你的服务器地址且认证有效。
- `/openviking-memory:ov` → 查看服务器、身份、最近一次注入与召回，以及各开关。

如果插件没有生效，设置 `OPENVIKING_DEBUG=1` 并查看 `~/.openviking/logs/cc-hooks.log`。

## Claude Code 上的不同之处

Claude Code 具备插件用到的全部事件：会话开始、提交提示、轮次结束、压缩前、会话结束、子 agent 结束和工具调用。捕获、提交、召回和离线队列按[能力参考](./16-capability-reference.md)所述工作。

- **子 agent**：每个子 agent 结束时捕获到它自己的会话 `cc-<id>__subagent-<agent_id>`。见[子 agent](./16-capability-reference.md#subagents)。
- **状态命令**：`/openviking-memory:ov` 显示插件状态。
- **状态行**：输入框下方的 OpenViking 指示器显示连接、召回、捕获和会话状态。见 [STATUSLINE.md](https://github.com/volcengine/OpenViking/blob/main/examples/agent-hook-plugin/hosts/claude-code/STATUSLINE.md)。
- **来源卡片**和**封存密钥**，见下文。

## 来源卡片

插件会在每次回答下方加一行卡片，列出这次回答用到的 OpenViking 来源：自动召回加进提示的内容，以及 Claude 自己搜索、读取的内容。例如 `OV · 8 sources · 4 past events · 3 work memories · 1 team doc · 1 read in full  [Expand]`。用卡片上的按钮展开单张卡片，或用 `/openviking-usage expand`、`/openviking-usage collapse` 展开或折叠全部卡片。卡片默认折叠。

卡片需要 Claude Code 2.1.286 或更高版本，并启用插件 hooks 模块。版本更早或模块未启用时不显示卡片，召回、捕获等 hook 照常工作。

## 封存密钥 {#seal-secrets}

开启 `sealSecrets` 后，插件会把 OpenViking 服务端已知的密钥在 Claude 读到的内容里换成 <code v-pre>{{ov_privacy:…}}</code> 引用，交给工具时再换回真值。默认关闭，需要 OpenViking 服务端 0.5.7 或更新版本。它运行在你的机器上。

| 设置 | 环境变量 | 默认值 | 作用 |
|------|----------|--------|------|
| `sealSecrets` | `OPENVIKING_SEAL_SECRETS` | `false` | 开启封存 |
| `enrollToolSecrets` | `OPENVIKING_ENROLL_TOOL_SECRETS` | `false` | 工具输出中的新密钥也登记 |

这几项写在 `ovcli.conf` 或环境变量里；改动在新 session 中生效。封存由插件的 hooks 模块执行，所以要求与[来源卡片](#来源卡片)相同；没有加载该模块时不会封存任何内容。

- **封存的内容**：你的提示词、工具结果，以及 Claude Code 附带的文件、提醒和 hook 上下文。新密钥从提示词中登记；工具结果中的新密钥只在开启 `enrollToolSecrets` 时登记。Claude 在工具调用里写下的引用，在工具运行前换成真值。本机对话记录里，提示词和工具结果存的是引用；附带的文件和提醒保留 Claude Code 自己的记录，即原文。
- **无法封存时**（服务端低于 0.5.7，或读不到服务端版本、封存或解析请求失败）会拦下：提示词不会发出，工具结果被扣下，引用解析不了的工具调用不会运行。提示里会说明原因。升级服务端或恢复连接，或者关闭 `sealSecrets`。
- **覆盖不到的内容**：系统提示词、斜杠命令参数、skill 正文，以及开启封存之前写下的历史。

## 工作区 peer

记忆按当前所在仓库推导出的 peer 归档，详见[工作区 peer](../configuration/02-client.md#工作区-peer)。要让一个不是仓库的目录拥有独立记忆，在其中创建 `.openviking/config.json`，内容为 `{"version": 1, "peer": {"id": "my-project"}}`。

## 故障排查

| 问题 | 原因 | 解决方法 |
|------|------|----------|
| 插件未生效 | 缺少 `ov.conf` 或 `ovcli.conf` | 运行[安装脚本](#安装)，或设置 `OPENVIKING_MEMORY_ENABLED=1` 以及 URL/API_KEY 环境变量 |
| MCP 工具连到 `127.0.0.1` 而不是远程服务器 | `~/.openviking/ovcli.conf` 中没有 `url` | 修正 `ovcli.conf` 后重启 Claude Code |
| 远程认证 401 / 403 | 凭据无效或权限不足 | 检查 `ovcli.conf` 中当前使用的 key；MCP 代理在认证失败后会重新读取它 |

## 参见

- [能力参考](./16-capability-reference.md)——所有宿主共用的捕获、提交、召回、离线重试和设置
- [插件 README](https://github.com/volcengine/OpenViking/blob/main/examples/agent-hook-plugin/hosts/claude-code/README_CN.md)
- [插件设置](../configuration/02-client.md#插件配置)
- [MCP 客户端](./06-mcp-clients.md)
