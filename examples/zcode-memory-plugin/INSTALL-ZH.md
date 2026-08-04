# 安装 OpenViking ZCode 记忆 Hooks

为 ZCode（字节跳动 AI coding agent）安装生命周期 hooks（自动 recall、turn 捕获、`viking://` URI 守卫）、OpenViking stdio MCP proxy 以及凭据配置，让 ZCode 把 [OpenViking](https://github.com/volcengine/OpenViking) 当作长期记忆后端使用。

结构镜像 TRAE 和 Cursor 集成，复用同一份 shared runtime。

## 前置条件

- ZCode（字节跳动 AI coding agent）
- 本地或远端运行的 [OpenViking](https://github.com/volcengine/OpenViking) HTTP Server
- Node.js 18+
- 如果服务端启用了认证，需要可用的 OpenViking API Key

建议先启动 OpenViking：

```bash
openviking-server --config ~/.openviking/ov.conf
curl http://localhost:1933/health
```

## 安装

在本仓库 checkout 目录下执行：

```bash
bash examples/zcode-memory-plugin/setup-helper/install.sh
```

安装器会：

1. 把 `hooks/hooks.json` 里的 `__OPENVIKING_ZCODE_ROOT__` 渲染成插件绝对路径，写到 ZCode 配置目录（默认 `~/.zcode/hooks.json`）。
2. 在 ZCode 的 MCP 配置（默认 `~/.zcode/mcp.json`）里追加一个 `openviking` 条目，指向 `servers/mcp-proxy.mjs`。已存在的 `mcpServers` 条目会被保留。
3. 如果 `~/.openviking/ovcli.conf` 不存在，会交互式询问 URL 与 API Key（或在非交互模式下读取 `OPENVIKING_URL` / `OPENVIKING_API_KEY`）并写入。
4. 跑一次插件的 hook 测试做 smoke test。

### 非交互式安装

```bash
OPENVIKING_URL=https://your-openviking.example.com \
OPENVIKING_API_KEY=sk-... \
bash examples/zcode-memory-plugin/setup-helper/install.sh --yes
```

### 路径覆盖

| 环境变量 | 默认值 | 用途 |
|---|---|---|
| `ZCODE_CONFIG_DIR` | `~/.zcode` | ZCode 配置目录 |
| `ZCODE_HOOKS_FILE` | `hooks.json` | 配置目录里的 hooks 文件名 |
| `ZCODE_MCP_FILE` | `mcp.json` | MCP 配置文件名 |
| `OPENVIKING_HOME` | `~/.openviking` | OpenViking 主目录 |
| `OPENVIKING_CLI_CONFIG_FILE` | `~/.openviking/ovcli.conf` | OpenViking CLI 配置路径 |

## 配置

连接与身份由 shared runtime 解析。hooks 和 MCP proxy 读同一份来源，所以总是指向同一个 server。

1. **默认**：`~/.openviking/ovcli.conf` 优先 — 用 `ov config switch <name>` 同时切换 hooks、MCP、ZCode 内 `ov` 命令使用的凭据。
2. **环境变量强制**：设置 `OPENVIKING_CREDENTIAL_SOURCE=env`，强制使用 `OPENVIKING_URL` / `OPENVIKING_API_KEY` / `OPENVIKING_ACCOUNT` / `OPENVIKING_USER` / `OPENVIKING_PEER_ID`。
3. **兜底**：`~/.openviking/ov.conf` 老字段，然后是 `http://127.0.0.1:1933`（无认证）。

API key 会作为 `Authorization: Bearer <api_key>` 同时发给 REST API（hooks 用）和 `/mcp` endpoint（模型用）。actor peer 默认按 Claude 的项目目录命名规则从当前 workspace 路径推导 — 完整规则和关闭方法见 [shared library README](../memory-plugin-shared/README.md)。

## 验证

1. 完全退出 ZCode，再重启。
2. 新建一个 ZCode session，确认 OpenViking MCP server 已连接（在 ZCode 的 MCP 界面）。
3. 让 ZCode 记住一个临时偏好，等回答结束后，新建 session 再问一次，验证捕获 + 跨 session recall。
4. 需要 hook 诊断时，用 `OPENVIKING_DEBUG=1` 启动 ZCode，查看 `~/.openviking/logs/zcode-hooks.log`。

## 可用 MCP 工具

stdio proxy 直接转发 server 实际返回的 `tools/list`。当前 OpenViking server 暴露：

- `recall`、`search`、`find` — 语义检索
- `remember`、`forget`、`add_resource` — 记忆 / 资源管理
- `read`、`list`、`grep`、`glob` — `viking://` 文件系统
- `code_search`、`code_outline`、`code_expand` — 已索引代码导航
- `list_watches`、`cancel_watch`、`health`

工具名前缀由 ZCode 的 MCP 客户端按其命名规则自动添加。

## 升级与卸载

升级：在原 checkout 下重新跑一次安装器。卸载只移除 OpenViking 管理的文件：

```bash
rm ~/.zcode/hooks.json          # 如果是 ZCode 独占；否则编辑掉 OpenViking 块
# 再从 ~/.zcode/mcp.json 里删除 `openviking` 条目
```

之后重启 ZCode。

## 故障排查

| 现象 | 原因与修复 |
|------|-----------|
| Hooks 没跑 | 完全退出 ZCode、重启、新建 Agent session。确认 `~/.zcode/hooks.json` 里写的是插件绝对路径。 |
| MCP 连不上 | 检查 `~/.openviking/ovcli.conf` 里的 URL/API Key，再重启 ZCode。 |
| 新 session 召不回上一轮 | 用 `OPENVIKING_DEBUG=1` 查看 `~/.openviking/logs/zcode-hooks.log`，确认 `Stop` 没有出现 `/commit` 连接或认证错误。 |
| Hook 事件名不一致 | ZCode 的扩展面目前没有公开文档。如果它用了不同的事件名，只需重命名 `hooks/hooks.json` 里的 key；脚本本身与事件名无关。详见 [DESIGN.md](./DESIGN.md)。 |

## 相关文档

- [DESIGN.md](./DESIGN.md) — 关于 ZCode 扩展面的假设与待确认事项。
- [认证](../../docs/zh/guides/04-authentication.md)
- [MCP 集成指南](../../docs/zh/guides/06-mcp-integration.md)
