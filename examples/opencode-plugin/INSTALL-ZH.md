# 安装 OpenViking OpenCode 统一插件

这个插件新增了一个面向 OpenCode 的统一 OpenViking 插件：

- 面向 memory、resources 和 code context 的 OpenViking MCP 工具
- 长期记忆、session 同步、生命周期边界 commit、自动 recall

这是仓库中唯一继续维护的 OpenCode 插件示例。这个插件不要求 agent 使用 `ov` 命令。模型工具由与其他 OpenViking 记忆插件相同的 stdio MCP proxy 提供。

使用指南：[OpenCode](../../docs/zh/agent-integrations/10-opencode.md)。共同行为（capture 与 commit、recall、会话开场上下文、离线重试、子 agent、压缩）见[能力参考](../../docs/zh/agent-integrations/16-capability-reference.md)。不支持 OpenCode 1.x：插件只会在日志里写一行说明，然后什么都不做。

## 前置条件

需要先准备：

- OpenCode 2.0.15+
- OpenViking HTTP Server
- Node.js 18+
- 如果服务端启用了认证，需要可用的 OpenViking API Key

建议先启动 OpenViking：

```bash
openviking-server --config ~/.openviking/ov.conf
```

检查服务：

```bash
curl http://localhost:1933/health
```

## 安装方式一：一键安装（推荐）

OpenCode 与其他记忆插件共用同一个安装器。它会询问要安装的宿主和 OpenViking 凭据：

```bash
curl -fsSL https://openviking.ai/install | bash
```

安装器会把插件作为本地文件插件复制到 `~/.config/opencode/plugins/`，把 `openviking` MCP server 条目写进 `~/.config/opencode/opencode.json`，并配置 `~/.openviking/ovcli.conf`。

## 安装方式二：发布包安装

也可以通过 OpenCode 的 package plugin 机制启用：

```json
{
  "plugin": ["@openviking/opencode-plugin"]
}
```

OpenCode v2 会在加载时规范化为原生的 `plugins`，无需手工迁移配置。

## 安装方式三：源码安装

用于开发调试或 PR 测试。OpenCode 推荐插件目录：

```bash
~/.config/opencode/plugins
```

在仓库根目录执行：

```bash
node examples/memory-plugin-shared/sync.mjs
mkdir -p ~/.config/opencode/plugins/openviking
cp examples/opencode-plugin/wrappers/openviking.js ~/.config/opencode/plugins/openviking.js
cp examples/opencode-plugin/index.mjs examples/opencode-plugin/package.json ~/.config/opencode/plugins/openviking/
cp -r examples/opencode-plugin/lib ~/.config/opencode/plugins/openviking/
cp -r examples/opencode-plugin/servers ~/.config/opencode/plugins/openviking/
```

`sync.mjs` 会生成 `lib/shared/`，插件和 MCP 代理都从这里 import 共享模块。这个目录不在 git 里，所以复制前要先运行；之后每次 `git pull` 也要重新运行再复制。

安装后结构应类似：

```text
~/.config/opencode/plugins/
├── openviking.js
└── openviking/
    ├── index.mjs
    ├── host.mjs
    ├── package.json
    ├── lib/
    └── servers/
```

顶层 `openviking.js` 只负责把 OpenCode 能发现的一级 `.js` 入口转发到插件目录：

```js
export { OpenVikingPlugin, default } from "./openviking/index.mjs"
```

这个 wrapper 只用于上面这种源码安装目录结构。npm 包安装会通过 `package.json` 直接加载 `index.mjs`。源码安装请使用 `.js` wrapper；OpenCode 的本地插件扫描器会发现 JavaScript/TypeScript 插件文件。

如果你使用 npm 包方式安装，也可以将 `examples/opencode-plugin` 作为一个普通 OpenCode 插件包使用。

## 配置

设置写在共享的客户端配置文件 `~/.openviking/ovcli.conf` 里，可以放在共享的 `plugin` 段，或放在 `plugin.opencode` 覆盖段（对 OpenCode 优先生效）：

```json
{
  "url": "http://127.0.0.1:1933",
  "api_key": "your-api-key-here",
  "plugin": {
    "opencode": {
      "enabled": true,
      "mcpEnabled": true,
      "timeoutMs": 30000,
      "repoContext": true,
      "repoContextCacheTtlMs": 60000
    }
  }
}
```

`repoContext` 会把已索引的 `viking://resources/` 仓库注入 system prompt；`dataDir` 用来修改运行时文件目录。其他所有设置、解析顺序、身份 header 和 peer 见[客户端配置](../../docs/zh/configuration/02-client.md)。

推荐通过环境变量提供 API Key，而不是写进配置文件：

```bash
export OPENVIKING_API_KEY="your-api-key-here"
```

`OPENVIKING_API_KEY`、`OPENVIKING_ACCOUNT`、`OPENVIKING_USER` 和 `OPENVIKING_PEER_ID` 优先于 `ovcli.conf` 中对应的值。高级场景可以用 `OPENVIKING_CLI_CONFIG_FILE` 指向其他路径的 `ovcli.conf`。

### 仅 Hooks 模式

如果其他 MCP server 已经提供 OpenViking，可以关闭本插件附带的 MCP 注册，同时保留生命周期 hooks：

```json
{
  "plugin": {
    "opencode": { "mcpEnabled": false }
  }
}
```

repository context、自动 recall、消息 capture 和生命周期 commit 会继续工作，也不会添加或覆盖 OpenCode 的 `mcp.openviking` 配置。

## 验证

修改插件或 OpenViking 配置后，需要重启 OpenCode。

进入新的 OpenCode session 后，可以让 agent 浏览 OpenViking memory，或搜索一个已索引的资源。插件应暴露 OpenViking MCP server，OpenCode 中的工具名会带 `openviking_` 前缀：

- `openviking_search`、`openviking_find`
- `openviking_read`、`openviking_download`、`openviking_list`、`openviking_tree`、`openviking_grep`、`openviking_glob`
- `openviking_remember`、`openviking_write`、`openviking_edit`、`openviking_add_resource`、`openviking_add_skill`
- `openviking_list_watches`、`openviking_cancel_watch`、`openviking_forget`、`openviking_health`

如果行为异常，先运行诊断。它检查插件安装、客户端配置和服务端连接，并为发现的每个问题给出修复方法；npm 包在 `scripts/` 下带有同一个脚本，会话中也可以用 `ov-memory-doctor` skill 运行它：

```bash
node ~/.config/opencode/plugins/openviking/scripts/ov-memory-doctor.mjs
```

运行时文件记录插件日志和 session 状态：

```bash
ls ~/.config/opencode/openviking/
tail -n 100 ~/.config/opencode/openviking/openviking-memory.log
```

如果使用本地 server，也确认 OpenViking 可访问：

```bash
curl http://localhost:1933/health
```

## 可用 MCP 工具

插件会通过 OpenCode config 注册 OpenViking stdio MCP proxy。服务端实际返回的 `tools/list` 是最终工具清单；OpenCode 中的工具名带 `openviking_` 前缀。

- 如果 agent 误用 OpenCode 本地 `read`、`glob`、`grep` 工具访问 `viking://` URI，插件会阻止这次本地文件系统调用，并提示改用 MCP 工具。
- `bash` 命令里带 `viking://` URI 时照常执行，插件会在输出末尾附一段提示，建议改用 MCP 工具；URI 本来就是命令参数时，agent 可以忽略这段提示。

## `openviking_add_resource` 本地文件

`openviking_add_resource` 支持三类输入：

- 远端 `http(s)` URL：直接调用 `/api/v1/resources`
- 本地文件路径：先调用 `/api/v1/resources/temp_upload`，再用返回的 `temp_file_id` 添加资源
- `file://` URL：按本地文件处理

相对路径会按 OpenCode 当前项目目录解析。示例：

```text
openviking_add_resource(path="https://example.com/spec.md", to="viking://resources/spec")
openviking_add_resource(path="./docs/notes.md", to="viking://resources/notes.md")
openviking_add_resource(path="file:///home/alice/project/notes.md", description="project notes")
```

当前仍不支持本地目录自动打 zip 上传；传入目录时会返回明确错误。

## 运行时文件

插件默认会把运行时文件写入：

```bash
~/.config/opencode/openviking/
```

可能包含：

- `openviking-memory.log`
- `openviking-session-state.json`

可以通过 `plugin.opencode` 里的 `dataDir` 修改这个目录。

这些是本地运行时文件，不建议提交到版本库。

## 故障排查

| 问题 | 排查方向 |
|------|----------|
| 插件没有加载 | package 安装检查 `~/.config/opencode/opencode.json` 是否包含 `@openviking/opencode-plugin`；源码安装检查 `~/.config/opencode/plugins/openviking.js` 是否存在 |
| 加载时报找不到 `lib/shared/*.mjs` | 源码复制前没有运行 `sync.mjs`。在仓库根目录运行 `node examples/memory-plugin-shared/sync.mjs` 后重新复制 `lib/` |
| MCP tools 连到了错误的 server | 检查 `~/.openviking/ovcli.conf`，或用 `OPENVIKING_*` 环境变量 / `OPENVIKING_CLI_CONFIG_FILE` 指向正确配置 |
| OpenViking 返回 401 / 403 | 检查 `OPENVIKING_API_KEY`；trusted-mode 部署还要检查 `OPENVIKING_ACCOUNT` 和 `OPENVIKING_USER` |
| recall 为空 | 确认 OpenViking 中已有 memories/resources，并且 `autoRecall` 为 `true` |
| 本地 `openviking_add_resource` 失败 | 传入文件路径而不是目录；目前还不支持自动上传本地目录 |
