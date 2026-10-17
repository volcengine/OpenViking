# OpenViking Memory — Kimi Code 插件

为 [Kimi Code CLI](https://github.com/MoonshotAI/kimi-code) 提供长期语义记忆，
由 [OpenViking](https://github.com/volcengine/OpenViking) 驱动。

每次提交 prompt 时自动召回相关记忆；把对话（用户输入、助手可见文本、工具调用
与结果）增量捕获进一个长期 OpenViking 会话；并通过本地 stdio 代理暴露
OpenViking 的 MCP 工具，让模型主动检索、记忆、导入。以 Kimi 原生插件形式安装，
不需要 bash 安装器。

## 安装

前置条件：Kimi Code CLI ≥ 2.1.1、Node.js ≥ 18、一个可达的 OpenViking 服务
（本地 `127.0.0.1:1933` 无需凭据）。

```text
/plugins install <本目录路径或 git URL>
```

然后 `/reload`（或开新会话）。验证：

```bash
node scripts/ov-memory-doctor.mjs kimicode --offline
```

## 行为

| Hook | 行为 |
|---|---|
| `UserPromptSubmit` | 检索与当前 prompt 相关的 OpenViking 记忆并注入上下文（原始 stdout 文本——Kimi 唯一可靠追加的通道） |
| `Stop` / `PreCompact` / `SessionEnd` / `Interrupt` | 增量解码会话的 `wire.jsonl`，把新的用户/助手/工具消息追加到 `kc-<session-id>`；会话结束、压缩前或达到 token 阈值时提交 |
| `SessionStart` | 有界重放此前发送失败的消息 |
| `PreToolUse` | 拒绝 `Read`/`Glob`/`Grep` 访问 `viking://` URI，并引导模型使用 MCP 工具 |

MCP 工具（`find`、`search`、`grep`、`read`、`remember`、`add_resource`、
`add_skill` 等）经 stdio 代理到服务端；捆绑的三个 skill
（`openviking-memory`、`openviking-skills`、`ov-experience-memory`）教模型
何时使用它们。

所有 hook 同步执行——不起 detached 后台进程——Windows、macOS、Linux 行为一致。

## 配置

所有开关都在共享 schema 中声明；解析顺序：`OPENVIKING_*` 环境变量 → 工作区
`.openviking/config*.json` → 本机 registry → `ovcli.conf` 的
`plugin.kimicode` → `plugin` → 默认值。连接与凭据来自环境变量或当前
`ovcli.conf`（`ov config switch` 会同时切换 hook 与 MCP）。完整宿主契约与设计
记录见 `DESIGN.md`。

## 开发

`scripts/shared/` 与 `skills/` 为生成物——不要手改：

```bash
node ../memory-plugin-shared/sync.mjs   # 改动共享源码后运行
node --test scripts/*.test.mjs
```

目录结构、模块边界与验收清单遵循
[插件开发规范](../../docs/zh/agent-integrations/18-plugin-development.md)。

## 状态

Beta。已在 Windows 上对照 Kimi Code CLI 2.2.0 验证。已知缺口（subagent 内容
捕获、doctor 安装检查）见 `DESIGN.md`。
