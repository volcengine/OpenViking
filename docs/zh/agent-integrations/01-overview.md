# 选择 Agent 接入方式

OpenViking 可以作为多种 Agent 运行时的长期记忆与上下文后端。按你的运行时挑选合适的接入方式即可。

## 该用哪个集成？

| 你在用… | 选这个 |
|---------|---------|
| **Claude Code** | [Claude Code 记忆插件](./02-claude-code.md) — 通过 hooks 实现自动召回与自动捕获 |
| **OpenClaw** | [OpenClaw 插件](./03-openclaw.md) — 全生命周期一体化集成 |
| **Codex / TraeCode CLI 2.0** | [Codex 记忆插件](./04-codex.md) — 生命周期 hooks 自动召回与增量捕获 |
| **Cursor** | [Cursor 记忆集成](./12-cursor.md) — 一条命令安装生命周期 Hook、MCP 工具、Rules 与 Skills |
| **TRAE / TRAE CN** | [TRAE 记忆集成](./13-trae.md) — 一个安装器完成 prompt 召回、回合捕获与 OpenViking 工具接入 |
| **DeepSeek Harness（`dsh`）/ DeepSeek Harness Desktop** | [DeepSeek Harness 记忆插件](./17-dsh.md) — 进程内 Cordis 插件，pre-step 召回、事件捕获与 OpenViking MCP 工具 |
| **Hermes Agent** | [Hermes Agent](./05-hermes.md)。自动捕获和召回记忆，旧版本使用内置提供方。 |
| **OpenCode** | [OpenCode 插件](./10-opencode.md) — MCP 工具 + 生命周期 hooks，覆盖仓库上下文、自动召回与捕获 |
| **pi** | [pi Coding Agent 扩展](./11-pi.md) — 原生扩展，自动召回、逐轮捕获、阈值 commit，并把服务端的 MCP 工具注册为 pi 原生工具 |
| **LangChain / LangGraph** | [LangChain 和 LangGraph](./07-langchain-langgraph.md) — retriever、tools、context backend、store 和 middleware |
| **VikingBot** | [VikingBot 安装与配置](../guides/17-vikingbot.md) — 内置 Agent，提供 `openviking_rule_search` 规则搜索工具（Beta） |
| **多个本地开发 Agent / 希望使用桌面界面** | [OpenViking Helper](./14-openviking-helper.md) — 可视化完成 Agent 接入、会话分析和记忆管理 |
| **任意支持 Agent Plugins 1.0 的客户端** | [Agent Plugins 1.0 插件包](./15-agent-plugins.md) — 一个可移植的包：`openviking-memory` 技能 + OpenViking MCP 工具 |
| **Manus / Claude Desktop / ChatGPT / 其他 MCP 客户端** | [MCP 客户端](./06-mcp-clients.md) — 任何兼容 MCP 的客户端直接对接内置 `/mcp` 端点 |
| **聊天客户端 / SDK 与 API 应用 / 其他没有插件的客户端** | [OpenViking 网关](../guides/15-gateway.md) — 能填 Base URL 和 API Key 的客户端都能接入网关，由网关召回记忆、保存对话，客户端里不用装任何东西 |
| **ZCode / CodeBuddy Code / AstrBot / …** | [社区插件](./08-community-plugins.md) — 社区维护的各运行时集成 |

Claude Code、Codex、OpenCode、pi、DSH 等 Agent 既能用自己的插件，也能接入 OpenViking 网关。两者怎么选，见[网关还是插件](../guides/15-gateway.md#gateway-or-plugin)。

## 支持 Claude Code 插件规范的客户端：直接安装插件

挑选某个客户端专属页面之前，先检查你的客户端是否已原生加载 Claude Code 插件规范。OpenViking 通过标准的 `.claude-plugin/marketplace.json` 发布记忆插件，不少客户端会用自家插件命令解析这个 marketplace，无需逐家写集成即可安装同一插件：

| 客户端 | Claude Code 兼容性证据 |
|--------|------------------------|
| GitHub Copilot | 官方文档：会查找 `.claude-plugin/plugin.json` / `.claude-plugin/marketplace.json` |
| Factory Droid | 官方文档：翻译 Claude 布局，marketplace 回退读 `.claude-plugin/marketplace.json` |
| Qoder | marketplace 解析回退到 `.claude-plugin/marketplace.json` |
| Grok Build（xAI） | 官方文档："fully compatible with Claude Code with zero configuration" |
| CodeBuddy / WorkBuddy（腾讯） | 官方文档：`.claude-plugin/` 回退；`${CLAUDE_PLUGIN_ROOT}` 为别名 |
| Devin for Terminal | 官方 changelog：无 `.devin-plugin/` 时使用 Claude 兼容 manifest |
| MiniMax Code（0.4.0+） | 源码：`CLAUDE_MANIFEST_PATH = '.claude-plugin/plugin.json'` |
| OpenClaw | 官方文档：auto-detects Claude bundle layouts |
| Qwen Code | 源码：安装扩展时抓取 `.claude-plugin/marketplace.json` |
| Oh My Pi | 源码：专门的 `claude-plugins` discovery provider |

如果客户端在上表中（或其文档提到 Claude Code 兼容层），用客户端自己的插件命令添加 OpenViking marketplace 并安装 `openviking-memory@openviking`，例如在 GitHub Copilot 中：

```bash
copilot plugin marketplace add volcengine/OpenViking
```

然后用同一插件命令安装 `openviking` marketplace 中的 `openviking-memory` 插件。仅当宿主没有 Claude Code 兼容层时，才回退使用上文各客户端专属页面。

## 横向对比各集成能力

想知道各个集成在工具面、自动召回、会话与 commit、压缩接管、降级容错上的具体差异，见 [集成能力参考](./16-capability-reference.md)——一份覆盖全部集成的横向对照矩阵。

## 开发与维护插件

新增或维护集成时，请遵循 [Hook + MCP Agent 插件开发与维护规范](./18-plugin-development.md)。使用 VibeCoding 时，务必让 coding agent 在修改前阅读并遵循该规范；实现可以参考 Claude Code、Codex 和其他现有插件。

## 所有集成的共同前置

本页所有集成都需要连接到一个正在运行的 OpenViking 服务。如果你还没有，请先按 [快速开始](../getting-started/02-quickstart.md) 部署。默认端点是 `http://localhost:1933`；远程使用需要 API Key（参见 [鉴权](../guides/04-authentication.md)）。

## 低延迟召回

接入并验证插件后，可按[调整召回延迟](19-recall-tuning.md)配置查询扩展、结果压缩与超时。
