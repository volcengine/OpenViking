# TRAE 与 TRAE CN 记忆集成

为 TRAE 与 TRAE CN 添加跨项目、跨会话的长期记忆。只需运行一次安装器，即可同时获得 prompt 自动召回、回合捕获与显式 OpenViking 工具。

源码：[examples/trae-memory-hooks](https://github.com/volcengine/OpenViking/tree/main/examples/trae-memory-hooks)

## 安装

可以单独或组合安装：

```bash
# TRAE
bash <(curl -fsSL https://raw.githubusercontent.com/volcengine/OpenViking/main/examples/memory-plugin-shared/install.sh) --harness trae

# TRAE CN
bash <(curl -fsSL https://raw.githubusercontent.com/volcengine/OpenViking/main/examples/memory-plugin-shared/install.sh) --harness trae-cn

# 同时安装
bash <(curl -fsSL https://raw.githubusercontent.com/volcengine/OpenViking/main/examples/memory-plugin-shared/install.sh) --harness trae,trae-cn
```

使用 TOS 镜像时替换 URL 并添加 `--dist tos`：

```bash
bash <(curl -fsSL https://ovrelease.tos-cn-beijing.volces.com/memory-plugin-shared/install.sh) \
  --harness trae,trae-cn --dist tos
```

安装器会配置完整集成，包括原生 Hook 与 OpenViking MCP Server，无需任何追加步骤。

## 工作方式

| 事件 | 行为 |
|------|------|
| `SessionStart` | 重放 pending 写入，并注入 profile/项目上下文。 |
| `UserPromptSubmit` | 在模型运行前检索并注入相关记忆。 |
| `Stop` | 捕获 `prompt` 以及 `last_assistant_message` 或 `text_content`。 |

TRAE 和 TRAE CN 分别使用独立状态、日志目录以及 `tr-`、`trcn-` session 前缀。长会话达到回合阈值后提交；失败写入进入共享 pending queue，并在下次 `SessionStart` 重放。

## 配置路径

| 客户端 | Hooks | macOS MCP | 通用 MCP 兜底 |
|--------|-------|-----------|---------------|
| TRAE | `~/.trae/hooks.json` | `~/Library/Application Support/Trae/User/mcp.json` | `~/.trae/mcp.json` |
| TRAE CN | `~/.trae-cn/hooks.json` | `~/Library/Application Support/Trae CN/User/mcp.json` | `~/.trae-cn/mcp.json` |

安装器只合并 OpenViking 条目，保留其他 Hook 和 MCP server。

## 验证

1. 安装后重启 TRAE。
2. 在对应 `hooks.json` 中确认 `SessionStart`、`UserPromptSubmit`、`Stop`。
3. 在 `mcp.json` 中确认 `openviking` server。
4. 设置 `OPENVIKING_DEBUG=1` 后检查 `~/.openviking/logs/trae-hooks.log` 或 `trae-cn-hooks.log`。

## 升级与卸载

重复运行安装命令即可升级。卸载单个版本：

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/volcengine/OpenViking/main/examples/memory-plugin-shared/install.sh) \
  --harness trae-cn --uninstall --yes
```

## 故障排查

| 现象 | 处理 |
|------|------|
| Stop 已触发但没有 session | 确认安装的是专用 TRAE 适配器；Claude transcript 解析器不兼容。 |
| 记忆出现在错误客户端下 | 检查 Hook 命令末尾参数是 `trae` 还是 `trae-cn`。 |
| 召回或捕获执行两次 | 清除旧 OpenViking Hook 后重跑安装器；每个事件应只保留一个托管条目。 |
| MCP 可用但没有自动召回 | 检查 `UserPromptSubmit`；仅配置 MCP 时仍由模型决定是否调用。 |
