# Agent 集成概览

OpenViking 可以作为多种 Agent 运行时的长期记忆与上下文后端。按你的运行时挑选合适的接入方式即可。

## 该用哪个集成？

| 你在用… | 选这个 |
|---------|---------|
| **Claude Code** | [Claude Code 记忆插件](./02-claude-code.md) — 通过 hooks 实现自动召回与自动捕获 |
| **OpenClaw** | [OpenClaw 插件](./03-openclaw.md) — 全生命周期一体化集成 |
| **Codex** | [Codex 记忆插件](./04-codex.md) — 生命周期 hooks 自动召回与增量捕获 |
| **Cursor** | [Cursor 记忆集成](./12-cursor.md) — 一条命令安装生命周期 Hook、MCP 工具、Rules 与 Skills |
| **TRAE / TRAE CN** | [TRAE 记忆集成](./13-trae.md) — 一个安装器完成 prompt 召回、回合捕获与 OpenViking 工具接入 |
| **Hermes Agent** | [Hermes Agent](./05-hermes.md) — 内置 OpenViking 记忆提供方，无需安装插件 |
| **OpenCode** | [OpenCode 插件](./10-opencode.md) — MCP 工具 + 生命周期 hooks，覆盖仓库上下文、自动召回与捕获 |
| **pi** | [pi Coding Agent 扩展](./11-pi.md) — 原生扩展，自动召回、逐轮捕获与阈值 commit |
| **LangChain / LangGraph** | [LangChain 和 LangGraph](./07-langchain-langgraph.md) — retriever、tools、context backend、store 和 middleware |
| **多个本地开发 Agent / 希望使用桌面界面** | [OpenViking Helper](./14-openviking-helper.md) — 可视化完成 Agent 接入、会话分析和记忆管理 |
| **任意支持 Agent Plugins 1.0 的客户端** | [Agent Plugins 1.0 插件包](./15-agent-plugins.md) — 一个可移植的包：`openviking-memory` 技能 + OpenViking MCP 工具 |
| **Manus / Claude Desktop / ChatGPT / 其他 MCP 客户端** | [MCP 客户端](./06-mcp-clients.md) — 任何兼容 MCP 的客户端直接对接内置 `/mcp` 端点 |
| **ZCode / AstrBot / …** | [社区插件](./08-community-plugins.md) — 社区维护的各运行时集成 |

## 所有集成的共同前置

本页所有集成都需要连接到一个正在运行的 OpenViking 服务。如果你还没有，请先按 [快速开始](../getting-started/02-quickstart.md) 部署。默认端点是 `http://localhost:1933`；远程使用需要 API Key（参见 [鉴权](../guides/04-authentication.md)）。

## 低延迟召回

查询扩展和召回结果压缩是两个独立的可选模型调用。需要优先保证响应速度时，可以在 Agent 插件端同时关闭它们；语义检索、预算控制、档位降级和跨轮去重仍会正常工作。

下面这组环境变量同时适用于 Claude Code 和 Codex。查询扩展在 OpenCode 和 pi 上同样可以关闭；压缩只有 Claude Code 和 Codex 支持。

```bash
export OPENVIKING_RECALL_QUERY_EXPANSION=off
export OPENVIKING_RECALL_COMPRESS=off
```

两个插件都有本地压缩逻辑，但配置模型不同：

- Claude Code 默认使用 `recallCompress=auto`：优先调用本地 `claude -p`（Sonnet + low），本地 CLI 不可用时回落到 OpenViking 服务端生成 digest。`client` 强制只用本地，`server` 强制只用服务端。
- Codex 默认调用本地 `codex exec`，模型顺序为 `gpt-5.3-codex-spark`，其次 `gpt-5.6-luna` + low。它不会启用服务端压缩。

两端的共同默认配置是 `recallCompress=auto`。`OPENVIKING_RECALL_COMPRESS=off` 会同时关闭两端压缩；Codex 将 `auto` 或 `client` 解释为启用本地压缩。旧的 Claude Code 变量 `OPENVIKING_RECALL_REWRITE` 仍可兼容，但新配置请使用统一名称。

也可以把同样的设置写进 `~/.openviking/ovcli.conf`：

```json
{
  "url": "https://openviking.example.com",
  "api_key": "your-api-key",
  "plugin": {
    "recallQueryExpansion": "off",
    "recallCompress": "off"
  }
}
```

环境变量优先于 `ovcli.conf`。修改后重启对应的 Agent，让 hook 进程重新加载配置。上述设置属于插件客户端，不需要修改服务端的 `ov.conf`。

`plugin` 段目前由 Claude Code 和 Codex 插件读取，因此以其他 harness 命名的 `plugin` 条目当前不生效。OpenCode 和 pi 支持用环境变量 `OPENVIKING_RECALL_QUERY_EXPANSION`（或各自配置文件里的 `recallQueryExpansion`）关闭查询扩展，但不支持 `OPENVIKING_RECALL_COMPRESS`——这两个 harness 不会请求服务端 digest。

context 请求的等待时间比普通请求更长，因为客户端提前中断会丢掉整个响应，而不只是超时的那一段。服务端流水线是串行的，每个可选阶段各有保险丝：先是查询扩展（`retrieval.recall_intent_timeout_s`，5 秒），然后是检索、正文读取和预算规划，最后才是 digest 重写（`retrieval.recall_rewrite_timeout_s`，30 秒）。因此这个上限按请求实际启用的阶段决定——带 session、会走查询扩展时取 15 秒，同时还要 digest 时取 45 秒，两者都不涉及时沿用插件自身的普通超时。可以用 `OPENVIKING_RECALL_CONTEXT_TIMEOUT_MS`（或 `plugin.recallContextTimeoutMs`）指定这个上限，取值应高于该请求会用到的保险丝、低于 Agent 自身的 hook 超时。
