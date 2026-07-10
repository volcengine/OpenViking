# Cursor 记忆插件

为 Cursor 添加跨项目、跨会话的长期记忆。OpenViking Cursor Plugin 只需安装一次，即可自动召回相关记忆、捕获新回合并提供显式记忆工具，无需额外配置 MCP。

源码：[examples/cursor-memory-plugin](https://github.com/volcengine/OpenViking/tree/main/examples/cursor-memory-plugin)

## 安装

一条命令安装完整 Cursor Plugin。安装器支持幂等重复执行，并会同时配置 Hook、MCP Server、Rule 与 Skill：

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/volcengine/OpenViking/main/examples/memory-plugin-shared/install.sh) \
  --harness cursor
```

GitHub 访问受限的地区，使用火山引擎 TOS 镜像中的同一个安装器：

```bash
bash <(curl -fsSL https://ovrelease.tos-cn-beijing.volces.com/memory-plugin-shared/install.sh) \
  --harness cursor --dist tos
```

无需追加任何 Cursor 或 MCP 配置。安装完成后重启 Cursor。

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

1. 检查 `~/.cursor/hooks.json` 中是否存在 `cursor-hook.mjs`。
2. 检查 `~/.cursor/mcp.json` 中是否存在 `openviking`。
3. 检查 `~/.cursor/rules/openviking-memory.mdc` 和 `~/.cursor/skills/openviking-memory/SKILL.md`。
4. 设置 `OPENVIKING_DEBUG=1`，新建一次使用工具的会话，然后检查 `~/.openviking/logs/cursor-hooks.log`。

Hook 状态位于 `~/.openviking/hook-state/cursor/`；OpenViking session 使用 `cu-` 前缀。

## 升级与卸载

重复运行安装命令即可升级。卸载 Plugin：

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/volcengine/OpenViking/main/examples/memory-plugin-shared/install.sh) \
  --harness cursor --uninstall --yes
```

卸载命令只清理 OpenViking 管理的 Cursor 配置和运行文件。

## 故障排查

| 现象 | 处理 |
|------|------|
| 出现两个 OpenViking MCP 或重复召回 | 重跑安装器迁移旧 OpenViking 条目，然后重启 Cursor。 |
| Hook 找不到 Node | 确认 Cursor 进程 PATH 中存在 `node`，然后重启 Cursor。 |
| 连接或鉴权失败 | 检查 `~/.openviking/ovcli.conf`；Hook 与 MCP 使用同一份活动配置。 |

## 参见

- [鉴权](../guides/04-authentication.md)
- [Cursor Hooks 文档](https://cursor.com/docs/hooks)
