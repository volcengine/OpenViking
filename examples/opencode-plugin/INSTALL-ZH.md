# 安装 OpenViking OpenCode 统一插件

这个插件新增了一个面向 OpenCode 的统一 OpenViking 插件：

- 面向 memory、resources 和 code context 的 OpenViking MCP 工具
- 长期记忆、session 同步、生命周期边界 commit、自动 recall

这是仓库中唯一继续维护的 OpenCode 插件示例。这个插件不再安装 `skills/openviking/SKILL.md`，也不要求 agent 使用 `ov` 命令。模型工具由 Claude Code 和 Codex 记忆插件同款的 stdio MCP proxy 提供。

## 前置条件

需要先准备：

- OpenCode
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

## 安装方式一：发布包安装

普通用户推荐通过 OpenCode 的 package plugin 机制启用：

```json
{
  "plugin": ["@openviking/opencode-plugin"]
}
```

## 安装方式二：源码安装

用于开发调试或 PR 测试。OpenCode 推荐插件目录：

```bash
~/.config/opencode/plugins
```

在仓库根目录执行：

```bash
mkdir -p ~/.config/opencode/plugins/openviking
cp examples/opencode-plugin/wrappers/openviking.js ~/.config/opencode/plugins/openviking.js
cp examples/opencode-plugin/index.mjs examples/opencode-plugin/package.json ~/.config/opencode/plugins/openviking/
cp -r examples/opencode-plugin/lib ~/.config/opencode/plugins/openviking/
cp -r examples/opencode-plugin/servers ~/.config/opencode/plugins/openviking/
```

安装后结构应类似：

```text
~/.config/opencode/plugins/
├── openviking.js
└── openviking/
    ├── index.mjs
    ├── package.json
    ├── lib/
    └── servers/
```

顶层 `openviking.js` 只负责把 OpenCode 能发现的一级 `.js` 入口转发到插件目录：

```js
export { OpenVikingPlugin, default } from "./openviking/index.mjs"
```

这个 wrapper 只用于上面这种源码安装目录结构。npm 包安装会通过 `package.json` 直接加载 `index.mjs`。
源码安装请使用 `.js` wrapper；OpenCode 的本地插件扫描器会发现 JavaScript/TypeScript 插件文件。

如果你使用 npm 包方式安装，也可以将 `examples/opencode-plugin` 作为一个普通 OpenCode 插件包使用。

## 配置

创建用户级配置文件：

```bash
~/.config/opencode/openviking-config.json
```

示例配置：

```json
{
  "enabled": true,
  "timeoutMs": 30000,
  "repoContext": { "enabled": true, "cacheTtlMs": 60000 },
  "autoRecall": {
    "enabled": true,
    "limit": 6,
    "scoreThreshold": 0.35,
    "maxContentChars": 500,
    "preferAbstract": true,
    "tokenBudget": 2000,
    "minQueryLength": 3
  },
  "commitTokenThreshold": 20000,
  "commitKeepRecentCount": 10,
  "profileTokenBudget": 10000,
  "resumeContextBudget": 32000
}
```

推荐通过环境变量提供 API Key，而不是写入配置文件：

```bash
export OPENVIKING_API_KEY="your-api-key-here"
```

API key 会从环境变量或 `~/.openviking/ovcli.conf` 读取，并由 hooks 和 MCP proxy 作为 `Authorization: Bearer ...` 发送。`account` 和 `user` 是 trusted mode
身份头，会作为 `X-OpenViking-Account`、`X-OpenViking-User` 发送；使用
user/admin API key 的 API_KEY mode 时应留空。
`peerId` 会作为 `X-OpenViking-Actor-Peer` 用于数据面的 memory/resource 请求；捕获 session message 时仍写入 body `peer_id`。需要 peer 维度路由时请显式配置。

`OPENVIKING_API_KEY`、`OPENVIKING_ACCOUNT`、`OPENVIKING_USER`、
`OPENVIKING_PEER_ID`
优先级高于 `openviking-config.json` 里的同名配置。

高级场景可以用 `OPENVIKING_PLUGIN_CONFIG` 指向其他配置文件路径。

## 验证

修改插件或 OpenViking 配置后，需要重启 OpenCode。

进入新的 OpenCode session 后，可以让 agent 浏览 OpenViking memory，或搜索一个已索引的资源。插件应暴露 OpenViking MCP server，OpenCode 中的工具名会带 `openviking_` 前缀：

- `openviking_recall`、`openviking_search`、`openviking_find`
- `openviking_read`、`openviking_list`、`openviking_grep`、`openviking_glob`
- `openviking_remember`、`openviking_add_resource`、`openviking_forget`、`openviking_health`
- `openviking_list_watches`、`openviking_cancel_watch`
- `openviking_code_search`、`openviking_code_outline`、`openviking_code_expand`

如果行为异常，先查看运行时文件：

```bash
ls ~/.config/opencode/openviking/
tail -n 100 ~/.config/opencode/openviking/openviking-memory.log
```

如果使用本地 server，也确认 OpenViking 可访问：

```bash
curl http://localhost:1933/health
```

## 可用 MCP 工具

插件会通过 OpenCode config 注册 OpenViking stdio MCP proxy。服务端实际返回的 `tools/list` 是最终工具清单；当前 OpenViking server 暴露：

- `openviking_recall`：面向当前任务的平衡召回
- `openviking_search`：跨 memories/resources/skills 的深度语义检索
- `openviking_find`：快速语义检索
- `openviking_remember`：存储重要事实或决策，供记忆提取
- `openviking_read`：读取一个或多个 `viking://` 文件
- `openviking_list`：列出 `viking://` 目录
- `openviking_grep`：精确文本或正则搜索
- `openviking_glob`：glob 文件匹配
- `openviking_add_resource`：添加 URL、本地文件、sitemap 或 feed
- `openviking_forget`：在用户明确确认后删除 `viking://` URI
- `openviking_list_watches` / `openviking_cancel_watch`：查看或取消资源 watch
- `openviking_code_search`、`openviking_code_outline`、`openviking_code_expand`：查看已索引代码符号
- `openviking_health`：检查 OpenViking server 健康状态

使用建议：

- 概念性问题用 `openviking_search`
- 精确符号、函数名、类名、报错字符串用 `openviking_grep`
- 枚举文件用 `openviking_glob`
- 读取内容用 `openviking_read`
- 探索目录结构用 `openviking_list`
- 删除前必须先获得用户明确确认，再调用 `openviking_forget`
- 如果 agent 误用 OpenCode 本地 `read`、`glob`、`grep` 工具访问 `viking://` URI，插件会阻止这次本地文件系统调用，并提示改用 MCP 工具。

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

可以通过配置里的 `runtime.dataDir` 修改这个目录。

这些是本地运行时文件，不建议提交到版本库。

## 故障排查

| 问题 | 排查方向 |
|------|----------|
| 插件没有加载 | package 安装检查 `~/.config/opencode/opencode.json` 是否包含 `@openviking/opencode-plugin`；源码安装检查 `~/.config/opencode/plugins/openviking.js` 是否存在 |
| MCP tools 连到了错误的 server | 检查 `~/.openviking/ovcli.conf`，或用 `OPENVIKING_*` 环境变量 / `OPENVIKING_PLUGIN_CONFIG` 指向正确配置 |
| OpenViking 返回 401 / 403 | 检查 `OPENVIKING_API_KEY`；trusted-mode 部署还要检查 `OPENVIKING_ACCOUNT` 和 `OPENVIKING_USER` |
| recall 为空 | 确认 OpenViking 中已有 memories/resources，并且 `autoRecall.enabled` 为 `true` |
| 本地 `openviking_add_resource` 失败 | 传入文件路径而不是目录；目前还不支持自动上传本地目录 |
