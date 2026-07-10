# Cursor 记忆插件

为 Cursor 添加跨项目、跨会话的长期记忆。OpenViking Cursor Plugin 只需安装一次，即可自动召回相关记忆、捕获新回合并提供显式记忆工具，无需额外配置 MCP。

源码：[examples/cursor-memory-plugin](https://github.com/volcengine/OpenViking/tree/main/examples/cursor-memory-plugin)

## 安装

在 Cursor 的 Plugins/Customize 页面安装 `openviking-memory`。正式发布到公共 Marketplace 后，也可以在 Cursor Agent 中执行：

```text
/add-plugin openviking-memory
```

Plugin 会整体安装内部的 Hook、MCP Server、Rule 与 Skill，无需任何额外 MCP 配置。

在当前 Cursor Marketplace 尚未提供该 Plugin 时，使用共享安装器安装完整兼容 runtime；脚本支持安全重复执行：

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/volcengine/OpenViking/main/examples/memory-plugin-shared/install.sh) \
  --harness cursor
```

GitHub 访问受限的地区，使用火山引擎 TOS 镜像中的同一个安装器：

```bash
bash <(curl -fsSL https://ovrelease.tos-cn-beijing.volces.com/memory-plugin-shared/install.sh) \
  --harness cursor --dist tos
```

兼容安装器会同时写入 Hook 与 MCP 配置。Marketplace Plugin 安装成功后，应移除兼容兜底，不要同时启用两套。

## 工作方式

Cursor Plugin 是可安装的封装单元，运行能力全部包含在 Plugin 内：

```text
OpenViking Cursor Plugin
├── Hook         自动召回与捕获
├── MCP Server   显式 OpenViking 工具
├── Rule         常驻使用规则
└── Skill        记忆操作指引
```

| 事件 | 行为 |
|------|------|
| `sessionStart` | 重放失败写入，并注入 profile/项目基础上下文。 |
| `beforeSubmitPrompt` | 按当前 prompt 预取相关记忆并写入本地 Hook 状态。 |
| 首次 `postToolUse` | 通过 `additional_context` 注入预取结果。 |
| `stop` | 从 Cursor `transcript_path` 增量捕获用户与助手回合。 |
| `preCompact` / `sessionEnd` | 补捕获并提交 OpenViking session。 |

Cursor 当前公开文档中的 `beforeSubmitPrompt` 输出只稳定支持放行或阻止，没有稳定的直接上下文注入字段。因此按 prompt 召回结果会在首个工具返回后注入；无工具回答使用 `sessionStart` 基础记忆。插件的 always-on rule 会要求 Cursor 在需要精确历史时调用 recall/search MCP。

## 验证

Plugin 安装方式：在 Cursor 的 Plugins/Customize 页面确认 `openviking-memory` 已启用，再确认该 Plugin 提供的 OpenViking Hook 与 MCP Server 处于活动状态。Plugin 管理的配置不要求出现在用户级 JSON 文件中。

直接兼容兜底方式：

1. 检查 `~/.cursor/hooks.json` 中是否存在 `cursor-hook.mjs`。
2. 检查 `~/.cursor/mcp.json` 中是否存在 `openviking`。
3. 设置 `OPENVIKING_DEBUG=1`，新建一次使用工具的会话，然后检查 `~/.openviking/logs/cursor-hooks.log`。

Hook 状态位于 `~/.openviking/hook-state/cursor/`；OpenViking session 使用 `cu-` 前缀。

## 升级与卸载

Marketplace Plugin 应在 Cursor 的 Plugins/Customize 页面升级或卸载，其内部能力由 Cursor 统一管理。

直接兼容兜底可重复运行安装命令升级；卸载命令如下：

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/volcengine/OpenViking/main/examples/memory-plugin-shared/install.sh) \
  --harness cursor --uninstall --yes
```

兜底卸载只清理 OpenViking 管理的 Cursor 配置和运行文件。

## 故障排查

| 现象 | 处理 |
|------|------|
| 出现两个 OpenViking MCP 或重复召回 | Plugin 已启用时删除手工/兜底配置，只保留一种安装方式。 |
| Hook 找不到 Node | 确认 Cursor 进程 PATH 中存在 `node`，然后重启 Cursor。 |
| 连接或鉴权失败 | 检查 `~/.openviking/ovcli.conf`；Hook 与 MCP 使用同一份活动配置。 |

## 参见

- [鉴权](../guides/04-authentication.md)
- [Cursor Hooks 文档](https://cursor.com/docs/hooks)
