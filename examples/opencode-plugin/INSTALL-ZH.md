# 安装 OpenViking OpenCode 统一插件

这个插件新增了一个面向 OpenCode 的统一 OpenViking 插件：

- 外部仓库语义检索
- 长期记忆、session 同步、生命周期边界 commit、自动 recall

这是仓库中唯一继续维护的 OpenCode 插件示例。这个插件不再安装 `skills/openviking/SKILL.md`，也不要求 agent 使用 `ov` 命令。原 skill 风格的能力会通过 OpenCode tools 暴露。

## 前置条件

需要先准备：

- OpenCode
- OpenViking HTTP Server
- Node.js / npm，用于安装插件依赖
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
  "plugin": ["openviking-opencode-plugin"]
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
cp examples/opencode-plugin/wrappers/openviking.mjs ~/.config/opencode/plugins/openviking.mjs
cp examples/opencode-plugin/index.mjs examples/opencode-plugin/package.json ~/.config/opencode/plugins/openviking/
cp -r examples/opencode-plugin/lib ~/.config/opencode/plugins/openviking/
cd ~/.config/opencode/plugins/openviking
npm install
```

安装后结构应类似：

```text
~/.config/opencode/plugins/
├── openviking.mjs
└── openviking/
    ├── index.mjs
    ├── package.json
    ├── lib/
    └── node_modules/
```

顶层 `openviking.mjs` 只负责把 OpenCode 能发现的一级 `.mjs` 入口转发到插件目录：

```js
export { OpenVikingPlugin, default } from "./openviking/index.mjs"
```

这个 wrapper 只用于上面这种源码安装目录结构。npm 包安装会通过 `package.json` 直接加载 `index.mjs`。

如果你使用 npm 包方式安装，也可以将 `examples/opencode-plugin` 作为一个普通 OpenCode 插件包使用。

## 配置

创建用户级配置文件：

```bash
~/.config/opencode/openviking-config.json
```

示例配置：

```json
{
  "endpoint": "http://localhost:1933",
  "apiKey": "",
  "account": "",
  "user": "",
  "peerId": "",
  "enabled": true,
  "timeoutMs": 30000,
  "repoContext": { "enabled": true, "cacheTtlMs": 60000 },
  "autoRecall": {
    "enabled": true,
    "limit": 6,
    "scoreThreshold": 0.15,
    "maxContentChars": 500,
    "preferAbstract": true,
    "tokenBudget": 2000
  }
}
```

推荐通过环境变量提供 API Key，而不是写入配置文件：

```bash
export OPENVIKING_API_KEY="your-api-key-here"
```

`apiKey` 会作为 `X-API-Key` 发送。`account` 和 `user` 是 trusted mode
身份头，会作为 `X-OpenViking-Account`、`X-OpenViking-User` 发送；使用
user/admin API key 的 API_KEY mode 时应留空。
`peerId` 会作为 `X-OpenViking-Actor-Peer` 用于数据面的 memory/resource 请求；捕获 session message 时仍写入 body `peer_id`。需要 peer 维度路由时请显式配置。

`OPENVIKING_API_KEY`、`OPENVIKING_ACCOUNT`、`OPENVIKING_USER`、
`OPENVIKING_PEER_ID`
优先级高于 `openviking-config.json` 里的同名配置。

高级场景可以用 `OPENVIKING_PLUGIN_CONFIG` 指向其他配置文件路径。

## 验证

修改插件或 OpenViking 配置后，需要重启 OpenCode。

进入新的 OpenCode session 后，可以让 agent 浏览 OpenViking memory，或搜索一个已索引的资源。插件应暴露这些 tools：

- `memsearch`、`memread`、`membrowse`
- `memgrep`、`memglob`
- `memadd`、`memremove`、`memqueue`
- `memcommit`

如果行为异常，先查看运行时文件：

```bash
ls ~/.config/opencode/openviking/
tail -n 100 ~/.config/opencode/openviking/openviking-memory.log
```

如果使用本地 server，也确认 OpenViking 可访问：

```bash
curl http://localhost:1933/health
```

## 可用工具

插件会通过 OpenCode `tool` hook 暴露这些工具：

- `memsearch`：语义检索 memories/resources/skills
- `memread`：读取具体 `viking://` URI
- `membrowse`：浏览 OpenViking 文件系统
- `memcommit`：提交当前 session 并触发记忆提取
- `memgrep`：精确文本或模式搜索，替代原 `ov grep`
- `memglob`：文件 glob 枚举，替代原 `ov glob`
- `memadd`：添加远端 URL 或本地文件资源，替代常见 `ov add-resource` 场景
- `memremove`：删除资源，替代 `ov rm`
- `memqueue`：查看处理队列，替代 `ov observer queue`

使用建议：

- 概念性问题用 `memsearch`
- 精确符号、函数名、类名、报错字符串用 `memgrep`
- 枚举文件用 `memglob`
- 读取内容用 `memread`
- 探索目录结构用 `membrowse`
- 删除前必须先获得用户明确确认，再调用 `memremove` 且传入 `confirm: true`

## `memadd` 本地文件

`memadd` 支持三类输入：

- 远端 `http(s)` URL：直接调用 `/api/v1/resources`
- 本地文件路径：先调用 `/api/v1/resources/temp_upload`，再用返回的 `temp_file_id` 添加资源
- `file://` URL：按本地文件处理

相对路径会按 OpenCode 当前项目目录解析。示例：

```text
memadd path="https://example.com/spec.md" to="viking://resources/spec"
memadd path="./docs/notes.md" parent="viking://resources/"
memadd path="file:///home/alice/project/notes.md" reason="project notes"
```

当前仍不支持本地目录自动打 zip 上传；传入目录时会返回明确错误。

## 运行时文件

插件默认会把运行时文件写入：

```bash
~/.config/opencode/openviking/
```

可能包含：

- `openviking-memory.log`
- `openviking-session-map.json`

可以通过配置里的 `runtime.dataDir` 修改这个目录。

这些是本地运行时文件，不建议提交到版本库。

## 故障排查

| 问题 | 排查方向 |
|------|----------|
| 插件没有加载 | package 安装检查 `~/.config/opencode/opencode.json` 是否包含 `openviking-opencode-plugin`；源码安装检查 `~/.config/opencode/plugins/openviking.mjs` 是否存在 |
| Tools 连到了错误的 server | 检查 `~/.config/opencode/openviking-config.json` 里的 `endpoint`，或用 `OPENVIKING_PLUGIN_CONFIG` 指向正确配置文件 |
| OpenViking 返回 401 / 403 | 检查 `OPENVIKING_API_KEY`；trusted-mode 部署还要检查 `OPENVIKING_ACCOUNT` 和 `OPENVIKING_USER` |
| recall 为空 | 确认 OpenViking 中已有 memories/resources，并且 `autoRecall.enabled` 为 `true` |
| 本地 `memadd` 失败 | 传入文件路径而不是目录；目前还不支持自动上传本地目录 |
