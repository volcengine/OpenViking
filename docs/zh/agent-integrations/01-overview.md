# Agent 集成概览

OpenViking 可以作为多种 Agent 运行时的长期记忆与上下文后端。按你的运行时挑选合适的接入方式即可。

## 该用哪个集成？

| 你在用… | 选这个 |
|---------|---------|
| **Claude Code** | [Claude Code 记忆插件](./02-claude-code.md) — 通过 hooks 实现自动召回与自动捕获 |
| **OpenClaw** | [OpenClaw 插件](./03-openclaw.md) — 全生命周期一体化集成 |
| **Codex / TraeCode CLI 2.0** | [Codex 记忆插件](./04-codex.md) — 生命周期 hooks 自动召回与增量捕获 |
| **Cursor** | [Cursor 记忆集成](./12-cursor.md) — 一条命令安装生命周期 Hook、MCP 工具、Rules 与 Skills |
| **TRAE / TRAE CN** | [TRAE 记忆集成](./13-trae.md) — 一个安装器完成 prompt 召回、回合捕获与 OpenViking 工具接入 |
| **DeepSeek Harness（`dsh`）** | [DeepSeek Harness 记忆插件](./17-dsh.md) — 进程内 Cordis 插件，pre-step 召回、事件捕获与 OpenViking MCP 工具 |
| **Hermes Agent** | [Hermes Agent](./05-hermes.md) — 内置 OpenViking 记忆提供方，无需安装插件 |
| **OpenCode** | [OpenCode 插件](./10-opencode.md) — MCP 工具 + 生命周期 hooks，覆盖仓库上下文、自动召回与捕获 |
| **pi** | [pi Coding Agent 扩展](./11-pi.md) — 原生扩展，自动召回、逐轮捕获、阈值 commit，并把服务端的 MCP 工具注册为 pi 原生工具 |
| **LangChain / LangGraph** | [LangChain 和 LangGraph](./07-langchain-langgraph.md) — retriever、tools、context backend、store 和 middleware |
| **多个本地开发 Agent / 希望使用桌面界面** | [OpenViking Helper](./14-openviking-helper.md) — 可视化完成 Agent 接入、会话分析和记忆管理 |
| **任意支持 Agent Plugins 1.0 的客户端** | [Agent Plugins 1.0 插件包](./15-agent-plugins.md) — 一个可移植的包：`openviking-memory` 技能 + OpenViking MCP 工具 |
| **Manus / Claude Desktop / ChatGPT / 其他 MCP 客户端** | [MCP 客户端](./06-mcp-clients.md) — 任何兼容 MCP 的客户端直接对接内置 `/mcp` 端点 |
| **ZCode / AstrBot / …** | [社区插件](./08-community-plugins.md) — 社区维护的各运行时集成 |

## 横向对比各集成能力

想知道各个集成在工具面、自动召回、会话与 commit、压缩接管、降级容错上的具体差异，见 [集成能力参考](./16-capability-reference.md)——一份覆盖全部集成的横向对照矩阵。

## 开发与维护插件

新增或维护集成时，请遵循 [Hook + MCP Agent 插件开发与维护规范](./18-plugin-development.md)。使用 VibeCoding 时，务必让 coding agent 在修改前阅读并遵循该规范；实现可以参考 Claude Code、Codex 和其他现有插件。

## 所有集成的共同前置

本页所有集成都需要连接到一个正在运行的 OpenViking 服务。如果你还没有，请先按 [快速开始](../getting-started/02-quickstart.md) 部署。默认端点是 `http://localhost:1933`；远程使用需要 API Key（参见 [鉴权](../guides/04-authentication.md)）。

## 低延迟召回

查询扩展和召回压缩都会增加模型调用。优先考虑响应速度时，可以关闭这两项；检索、预算控制和跨轮去重仍然保留：

```bash
export OPENVIKING_RECALL_QUERY_EXPANSION=off
export OPENVIKING_RECALL_COMPRESS=off
```

也可以在 `~/.openviking/ovcli.conf` 中配置：

```json
{
  "plugin": {
    "recallQueryExpansion": "off",
    "recallCompress": "off"
  }
}
```

### 选择压缩方式

| 值 | 行为 |
| --- | --- |
| `off` | 不压缩召回结果 |
| `server` | 请求 OpenViking 服务端压缩 |
| `client` | 只用本地压缩器，适用于 Claude Code 和 Codex |
| `auto` | 有本地压缩器时优先使用，否则请求服务端自动处理 |

Claude Code 和 Codex 默认使用 `auto`，本地压缩器分别是 `claude -p` 和 `codex exec`，见 [§3.2.5](./16-capability-reference.md#_3-2-5-召回再摘要)。其他支持云端压缩的集成默认保持 `off`，需要显式开启。服务端压缩适用于 Claude Code、Codex、OpenCode、DSH、pi、Cursor、TRAE、TRAE CN、ZCode、OpenClaw 和 Hermes，要求服务端支持 context-search rewrite。

这些设置控制自动召回。模型主动调用 MCP `search` 时，使用该次调用传入的参数。完整规则与旧服务端回退行为见[共享插件说明](https://github.com/volcengine/OpenViking/blob/main/examples/memory-plugin-shared/README.md#cloud-recall-compression)。

共享插件读取 `ovcli.conf` 的 `plugin` 段；`plugin.<harness>` 可覆盖某个客户端的配置。环境变量优先级更高；旧变量 `OPENVIKING_RECALL_REWRITE` 仍可作为 `OPENVIKING_RECALL_COMPRESS` 的别名使用，详见[插件配置](../configuration/02-client.md#插件配置)。修改后重启对应 Agent，使 hook 进程加载新配置。这些都是插件客户端配置，不需要修改服务端的 `ov.conf`。

### 请求超时

查询扩展、检索和 digest 压缩依次执行。查询扩展默认超时为 5 秒（`retrieval.recall_intent_timeout_s`），digest 默认超时为 30 秒（`retrieval.recall_rewrite_timeout_s`）。

`OPENVIKING_RECALL_CONTEXT_TIMEOUT_MS` 或 `plugin.recallContextTimeoutMs` 控制客户端等待整个 context 请求的上限。未设置时沿用插件的普通超时；请求带 session（会走查询扩展）时至少 15 秒，请求 digest 时至少 45 秒。自定义值应高于该请求会用到的服务端超时，并低于宿主 hook 的超时；客户端提前结束会丢失整个响应。
