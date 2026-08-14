# Agent Plugins 1.0 插件包

[Agent Plugins 1.0](https://agent-plugins.org/specification) 是一套与厂商无关的 AI 编码 Agent 插件打包规范。一个插件就是一个普通目录：`plugin.json` 清单、`skills/` 下自动发现的 Agent Skills，以及可选的 `mcp.json` MCP 服务声明。所有符合规范的客户端都以同样的方式加载它 —— 不再需要为每个客户端各写一套接入。

OpenViking 的这个插件包位于仓库的 [`agent-plugins/`](https://github.com/volcengine/OpenViking/tree/main/agent-plugins) 目录。

## 目录结构

```
agent-plugins/
├── plugin.json                          # Agent Plugins 1.0 清单（name: openviking）
├── mcp.json                             # 一个 stdio MCP server："openviking"
├── servers/
│   ├── mcp-proxy.mjs                    # stdio -> streamable-HTTP 代理，转发到服务端 /mcp
│   ├── config.mjs, debug-log.mjs        # 凭据 / 配置解析
│   └── shared/                          # 由 examples/memory-plugin-shared/lib 生成
├── skills/openviking-memory/SKILL.md    # 教模型完成「召回 + 沉淀」闭环
└── plugin.test.mjs                      # node --test 规范一致性校验
```

零 npm 依赖 —— 代理和测试只用 Node.js 标准库（需要 Node 18+ 以获得全局 `fetch`）。

## 安装

1. 准备一个可访问的 OpenViking 服务。还没有的话，先按 [快速开始](../getting-started/02-quickstart.md) 部署；本地默认端点是 `http://127.0.0.1:1933`。
2. 让你的 Agent Plugins 客户端指向 `agent-plugins/` 目录。各客户端的安装命令或插件目录不同，请查阅其文档。加载时客户端会：
   - 按 `mcp.json` 注册名为 `openviking` 的 MCP server，以 stdio 方式运行 `node <plugin>/servers/mcp-proxy.mjs`；
   - 从 `skills/` 发现 `openviking-memory` 技能。
3. 配置凭据（见下节）后开始会话。模型即可使用 `find` / `search` / `recall` / `read` / `list` / `grep` / `glob` / `remember` / `add_resource` / `forget` / `health`，较新的服务端还提供 `tree` / `write` / `edit`。

## 为什么用 stdio 代理，而不是 `streamable-http`

OpenViking 服务端本身在 `/mcp` 上就是 streamable HTTP，但 `mcp.json` 里直接写 `streamable-http` 条目无法做到可移植：服务地址因部署而异（有人是 localhost，有人是远端），而规范禁止把凭据写进静态 `headers`。stdio 代理同时解决这两点 —— 它在运行时从与 `ov` CLI 相同的本地来源解析 URL 和 API Key，逐请求注入，再把 JSON-RPC 原样通过 streamable HTTP 转发。

## 凭据解析顺序

从高到低 —— 与 `ov` CLI 及其他 OpenViking 插件完全一致：

1. 环境变量：`OPENVIKING_URL`（或 `OPENVIKING_BASE_URL`）、`OPENVIKING_API_KEY`（或 `OPENVIKING_BEARER_TOKEN`）、`OPENVIKING_ACCOUNT`、`OPENVIKING_USER`、`OPENVIKING_PEER_ID`
2. `~/.openviking/ovcli.conf`（`url`、`api_key`、`account`、`user`）—— 可用 `OPENVIKING_CLI_CONFIG_FILE` 覆盖路径
3. `~/.openviking/ov.conf` 的 `server` 段（`url`，或 `host` / `port`，以及 `root_api_key`）—— 可用 `OPENVIKING_CONFIG_FILE` 覆盖路径
4. 默认值：`http://127.0.0.1:1933`，不鉴权（本地模式）

```json
// ~/.openviking/ovcli.conf
{
  "url": "https://openviking.example.com",
  "api_key": "your-api-key"
}
```

配置文件的改动会被运行中的代理自动读取，无需重启。

调试：设置 `OPENVIKING_DEBUG=1`，日志以 JSON Lines 写入 `~/.openviking/logs/agent-plugins.log`（路径可用 `OPENVIKING_DEBUG_LOG` 覆盖）。`OPENVIKING_TIMEOUT_MS` 可调整默认 15s 的单请求超时。

## 能力边界：规范不含 hooks

Agent Plugins 1.0 只覆盖 skills 和 MCP servers；hooks、commands、agents 被有意排除在本版本之外，因为它们在各客户端之间语义差异太大。因此这个包提供的是**可移植的召回 + 写入能力面**，由模型驱动而非生命周期事件驱动：**自动会话捕获和 prompt 前自动召回不在此范围内**。

作为补偿，内置的 `openviking-memory` 技能直接把这套闭环教给模型 —— 任务开始时用 `find` / `search` / `recall` + `read` 召回，过程中和结束后用 `remember` / `write` / `edit` 沉淀，并给出使用召回内容时的优先级与安全规则。

**如果你的 harness 支持 hooks 机制，推荐使用专属插件。** hook 驱动的召回与捕获不需要模型花费工具调用、也不依赖模型「想起来要记」，比技能驱动的闭环更省 token、也更可靠。本 Agent Plugins 包适用于没有 hooks 的 harness，或你希望用同一个包覆盖多个客户端的场景。

Claude Code、Codex、Cursor、TRAE / TRAE CN、ZCode、OpenCode、pi 共用同一个安装脚本。它会依次询问界面语言、要安装的 harness、下载源和 OpenViking 凭据，所有步骤幂等，重复运行安全：

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/volcengine/OpenViking/main/examples/memory-plugin-shared/install.sh)
```

GitHub 访问受限的地区，从火山引擎 TOS 镜像运行同一个脚本：

```bash
bash <(curl -fsSL https://ovrelease.tos-cn-beijing.volces.com/memory-plugin-shared/install.sh)
```

| Harness | 专属集成 |
|---------|----------|
| Claude Code | [Claude Code 记忆插件](./02-claude-code.md) |
| Codex | [Codex 记忆插件](./04-codex.md) |
| OpenCode | [OpenCode 插件](./10-opencode.md) |
| Cursor | [Cursor 记忆集成](./12-cursor.md) |
| TRAE / TRAE CN | [TRAE 记忆集成](./13-trae.md) |
| pi | [pi Coding Agent 扩展](./11-pi.md) |
| OpenClaw | [OpenClaw 插件](./03-openclaw.md) — 独立安装流程 |
| ZCode | [社区集成](./08-community-plugins.md) |

按规范，客户端专属的集成后续也可以放进同一个包里 —— 使用反向域名命名的目录（如 `com.example.client/`）或清单的 `extensions` 字段 —— 且不会影响其他客户端。

## 开发

```bash
node --test agent-plugins/plugin.test.mjs
```

`plugin.test.mjs` 会校验：清单的 schema URL 及两个清单的规范版本一致、插件 name 规则、清单根字段闭集、semver、每个 `skills/*` 子目录都有带 `name` + `description` frontmatter 且 `name` 与目录同名的 `SKILL.md`、`mcp.json` 引用的文件存在且不逃逸插件根目录，以及包内所有 `.mjs` 都能通过 `node --check`。

`servers/shared/*.mjs` 是 `examples/memory-plugin-shared/lib` 的生成副本 —— 请改共享库后重新执行 `node examples/memory-plugin-shared/sync.mjs`；一旦漂移，`examples/memory-plugin-shared/sync.test.mjs` 会失败。两个测试文件都已接入 CI。
