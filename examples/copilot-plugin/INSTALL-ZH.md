# 为 GitHub Copilot 安装 OpenViking 记忆插件

把 [OpenViking](https://github.com/volcengine/OpenViking) 接入 GitHub Copilot 作为长期记忆后端。支持三个 Copilot 接入面：

| 接入面 | 状态 | 配置位置 | 顶层字段 |
|---|---|---|---|
| VSCode Copilot Chat / agent mode | GA | `.vscode/mcp.json`（工作区）或用户 profile | `servers` |
| `gh copilot` CLI | GA | `~/.copilot/mcp-config.json` | `mcpServers` |
| GitHub.com Copilot cloud agent + code review | 公开预览 | 仓库 Settings → Copilot → MCP servers（UI 粘贴） | `mcpServers` |

> **先读这条**：GitHub Copilot **没有**生命周期 hooks。安装后，由模型自己调用 OpenViking 的 MCP 工具（由附带的 Agent Skill 引导），而不是被自动注入 recall。完整诚实的范围说明见 [DESIGN.md](./DESIGN.md)。

## 前置条件

- 一个受支持的 Copilot 接入面（VSCode Copilot Chat / agent mode、`gh copilot` CLI，或开了 Copilot 的 GitHub.com 仓库）
- 本地或远端运行的 [OpenViking](https://github.com/volcengine/OpenViking) HTTP 服务
- Node.js 18+（仅在用可选的 stdio proxy 和配置生成器测试时需要）
- 若服务端要求鉴权，需要 OpenViking API key

先启动 OpenViking：

```bash
openviking-server --config ~/.openviking/ov.conf
curl http://localhost:1933/health
```

## 安装 —— 选你的接入面

### `gh copilot` CLI（默认）

```bash
bash examples/copilot-plugin/setup-helper/install.sh --cli --with-skill
```

写入 `~/.copilot/mcp-config.json`（顶层 `mcpServers`，`type: "http"`），并把 `openviking-memory` Agent Skill 装到 `~/.copilot/skills/`。

也可以直接用官方 CLI（等价于安装器写入的内容）：

```bash
copilot mcp add --transport http \
  --header "Authorization: Bearer YOUR_OPENVIKING_API_KEY" \
  openviking https://your-openviking.example.com/mcp
```

### VSCode Copilot Chat / agent mode

```bash
bash examples/copilot-plugin/setup-helper/install.sh --vscode
```

在当前工作区写入 `.vscode/mcp.json`（顶层 `servers`，`type: "http"`）。提交到 git 与团队共享；或用 `MCP: Open User Configuration` 命令装到用户 profile。

> **快捷方式**：如果你已经把 OpenViking 接到了 Claude Desktop，VSCode 可以自动发现那份配置 —— 在 VSCode 设置里启用 `chat.mcp.discovery.enabled`。

### GitHub.com 仓库级（Copilot cloud agent + code review）

```bash
bash examples/copilot-plugin/setup-helper/install.sh --repo
```

打印一段 JSON。**先建一个名为 `COPILOT_MCP_OPENVIKING_API_KEY` 的 Agents secret**，然后把 JSON 粘到仓库的 Settings → Copilot → MCP servers 页面。配置里以 `${COPILOT_MCP_OPENVIKING_API_KEY}` 引用 secret，真实 key 绝不写进配置。

默认是**只读工具白名单**（`recall, search, find, read, list, grep, glob`），因为 cloud agent 调用工具是无需批准自主进行的。仅当你确实想让 cloud agent 写记忆时，才把 `remember` 加进 `tools` 数组。

## 非交互式安装

```bash
OPENVIKING_URL=https://your-openviking.example.com \
OPENVIKING_API_KEY=sk-... \
bash examples/copilot-plugin/setup-helper/install.sh --cli --with-skill --yes
```

## 可选：用 stdio MCP proxy 代替直连 HTTP

多数用户应直接用 HTTP（见上）。仅当希望凭据从 `~/.openviking/ovcli.conf` 自动解析（轮换 key 时方便），或本地服务没开鉴权时，才用 stdio proxy。

proxy 在 [`servers/mcp-proxy.mjs`](./servers/mcp-proxy.mjs)，复用与 ZCode / Codex / Cursor 插件相同的凭据解析。在 MCP 配置里引用它：

```json
{
  "mcpServers": {
    "openviking": {
      "type": "local",
      "command": "node",
      "args": ["/abs/path/to/examples/copilot-plugin/servers/mcp-proxy.mjs"],
      "tools": ["*"]
    }
  }
}
```

（VSCode 的 `.vscode/mcp.json` 在 `servers` 下用同样的结构，但不要写 `type` 字段。）

## 验证

1. **重启目标 Copilot 接入面**（VSCode：reload window；CLI：开新会话）。
2. 确认 OpenViking MCP server 已连接：
   - VSCode：打开 Chat 视图，看 MCP 面板 / Configure Tools。
   - CLI：`copilot mcp list`。
3. 测 recall：新会话里让 agent「用 OpenViking recall 一下我们之前对 X 的决定」，或问一个答案依赖既有 OpenViking 记忆的问题。
4. 测 remember：告诉 agent「记住我喜欢用空格缩进」，然后开新会话问「我喜欢哪种缩进？」。
5. 如果 recall 没触发，装上 Agent Skill（`--with-skill`）再试；skill 的作用就是让模型主动调 `recall`。

## 路径覆盖

| 环境变量 | 默认 | 用途 |
|---|---|---|
| `COPILOT_CLI_CONFIG_DIR` | `~/.copilot` | Copilot CLI 配置目录 |
| `COPILOT_VSCODE_DIR` | `./.vscode` | VSCode 工作区 `.vscode` 目录 |
| `COPILOT_SKILLS_DIR` | `~/.copilot/skills` | Agent Skill 安装位置 |
| `OPENVIKING_HOME` | `~/.openviking` | OpenViking 主目录 |
| `OPENVIKING_CLI_CONFIG_FILE` | `~/.openviking/ovcli.conf` | OpenViking CLI 配置路径 |

## 凭据解析

由 shared runtime 解析（与其他插件相同）：

1. **默认**：活动的 `~/.openviking/ovcli.conf` 优先 —— 用 `ov config switch <name>` 一次性切换 MCP 和 Copilot 里跑的 `ov` 命令。
2. **环境变量强制**：`OPENVIKING_CREDENTIAL_SOURCE=env` 强制使用 `OPENVIKING_URL` / `OPENVIKING_API_KEY` / `OPENVIKING_ACCOUNT` / `OPENVIKING_USER` / `OPENVIKING_PEER_ID`。
3. **回退**：`~/.openviking/ov.conf`（旧字段），再回退到 `http://127.0.0.1:1933`（无鉴权）。

鉴权用 `Authorization: Bearer <api_key>`。默认从当前工作区路径派生 peer，可覆盖 —— 见 [shared library README](../memory-plugin-shared/README.md)。

## 升级与卸载

重新运行安装器即可升级。卸载时把目标接入面里的 OpenViking 配置块删掉：

```bash
# CLI
copilot mcp remove openviking
rm -rf ~/.copilot/skills/openviking-memory    # 如果用过 --with-skill

# VSCode
# 编辑 .vscode/mcp.json，删掉 "servers" 下的 "openviking" 条目

# GitHub.com 仓库
# 仓库 Settings → Copilot → MCP servers → 删掉 openviking 块
```

## 排错

| 症状 | 原因与修复 |
|---------|---------------|
| MCP 连不上 | 检查 `~/.openviking/ovcli.conf` 里的 URL/API key，然后重启 Copilot 接入面。 |
| 工具不出现（CLI） | 跑 `copilot mcp list`，检查 `~/.copilot/mcp-config.json` 里的 `tools` 白名单。 |
| 工具不出现（VSCode） | 在 chat 输入栏打开 Configure Tools，把 OpenViking 工具勾上。 |
| 企业用户看到「MCP disabled」 | 组织/企业的「MCP servers in Copilot」策略默认关闭；联系管理员。（不影响 Copilot Free/Pro/Pro+/Max。） |
| recall 不自动发生 | 装上 Agent Skill（`--with-skill`）。没有 skill，模型就没有「要主动调 recall」的策略。即便有 skill，recall 也是 best-effort —— Copilot 没有 hooks。 |
| 仓库级 cloud agent 显示 MCP server 但工具不工作 | 公开预览限制：cloud agent 只支持 tools（不支持 resources/prompts），且不支持 OAuth。用 API key 鉴权。 |

## 相关文档

- [DESIGN.md](./DESIGN.md) —— 诚实范围、假设、决策依据。
- [RESEARCH.md](./RESEARCH.md) —— Copilot 扩展面每条结论的来源链接。
- [鉴权](../../docs/en/guides/04-authentication.md)
- [MCP 集成指南](../../docs/en/guides/06-mcp-integration.md)
