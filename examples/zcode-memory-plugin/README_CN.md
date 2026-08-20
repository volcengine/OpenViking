# OpenViking ZCode 记忆插件

本包为 ZCode 提供 OpenViking 长期记忆的生命周期适配器。复用 `memory-plugin-shared` 共享运行时——不重复任何记忆逻辑，仅新增一个 ZCode 薄适配层。

## 功能

- **SessionStart** — 注入用户画像和偏好/实体到上下文。
- **UserPromptSubmit** — 搜索 OpenViking 相关记忆并注入。
- **PreToolUse**（`Read|Glob|Grep`）— 拦截 `viking://` 虚拟路径的直接访问，引导使用 MCP 工具。
- **Stop** — 立即返回，并在 detached worker 中捕获增量用户/助手对话、提交 OpenViking 会话。

ZCode 不支持 `PreCompact`/`SessionEnd`/`SubagentStart`/`SubagentStop`，因此通过 Stop 时 commit 补足 compact/会话结束信号。Rollout 文件是权威增量对话源：稳定的 host `turnId` 用于去重，也让后续 Stop 能恢复漏掉的回合；只有 rollout 文件不可用时才回退到 Hook stdin。

仍可能丢失最后一个尚未完成的 in-flight turn。`SessionStart` 默认发送一小时 idle
auto-commit policy；服务端启用 `memory.session_auto_commit.idle_enabled=true`
后才会执行。设置 `OPENVIKING_COMMIT_IDLE_TIMEOUT_SECONDS=off` 可禁用该 policy。

| 环境变量 | 默认值 | 含义 |
| --- | --- | --- |
| `OPENVIKING_COMMIT_IDLE_TIMEOUT_SECONDS` | `3600` | 服务端 idle policy；`0`/`off` 可禁用 |

## 安装

使用共享安装脚本：

```bash
bash examples/memory-plugin-shared/install.sh --harness zcode
```

安装脚本通过 `~/.zcode/` 或 `zcode` 二进制检测 ZCode，将 hooks 和 MCP 配置合并到 `~/.zcode/cli/config.json`，并将 OpenViking 凭据写入 `~/.openviking/ovcli.conf`。

## 架构

插件通过 `sync.mjs` 将共享运行时 vendor 到 `scripts/shared/`。调度器（`zcode-hook.mjs`）按事件名分支；三个轻量 shim 设置环境变量并导入调度器，URI guard 使用独立入口。共享运行时提供召回、批量写入、待处理队列、凭据解析和 MCP 代理；`zcode-capture.mjs` 负责 ZCode 特有的确认与游标状态转换。

详见 [DESIGN.md](./DESIGN.md) 了解已验证的 ZCode 扩展面事实和设计决策来源。

## 测试

```bash
node --test scripts/*.test.mjs
```
