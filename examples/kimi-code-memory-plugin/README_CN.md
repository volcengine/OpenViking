# OpenViking Memory — Kimi Code 插件

为 [Kimi Code CLI](https://github.com/MoonshotAI/kimi-code) 提供长期语义记忆，
由 [OpenViking](https://github.com/volcengine/OpenViking) 驱动。

每次提交 prompt 时自动召回相关记忆；把对话（用户输入、助手可见文本、工具调用
与结果）增量捕获进一个长期 OpenViking 会话；并通过本地 stdio 代理暴露
OpenViking 的 MCP 工具，让模型主动检索、记忆、导入。以 Kimi 原生插件形式安装，
不需要 bash 安装器。

这是社区实现的 Kimi Code 插件，不是 Kimi Code / Moonshot AI 的官方插件。

## 安装

前置条件：Kimi Code CLI ≥ 2.1.1（已在 2.2.0 上验证）、Node.js ≥ 18、一个可达的
OpenViking 服务（本地 `127.0.0.1:1933` 无需凭据）。插件版本：**0.2.6**
（见 `kimi.plugin.json`）。

```text
/plugins install <本目录绝对路径或 zip URL>
```

本插件位于 monorepo 子目录，GitHub URL 方式不适用（kimi-code 只从仓库根
目录安装）；用本地目录或 manifest 位于压缩包根目录的 zip。

> 不要用共享的 `install.sh` 安装本宿主：它会以同一 id 写入另一个插件、覆盖当前
> 副本。安装与升级都走上面的 `/plugins install`。

然后 `/reload`（或开新会话）。验证：

```bash
node scripts/ov-memory-doctor.mjs --offline
```

## 升级与卸载

CLI 始终运行托管副本（`$KIMI_CODE_HOME/plugins/managed/openviking-memory/`），
而不是本源码目录——直接改源码在重装之前不生效。

```text
/plugins install <同样的安装来源>   # 升级：替换托管副本
/plugins remove openviking-memory   # 卸载：hook、MCP 与 skill 一并移除
```

重装与卸载都不会触碰 OpenViking 服务端、记忆、凭据和
`~/.openviking/kimicode-plugin-state`；变更的只是插件文件及其在 Kimi 中的注册。

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

开关在共享 schema 中只声明一次；解析顺序：`OPENVIKING_*` 环境变量 → 工作区
`.openviking/config*.json` → 本机 registry → `ovcli.conf` 的
`plugin.kimicode` → `plugin` → 默认值。连接与凭据来自环境变量或当前
`ovcli.conf`（`ov config switch` 会同时切换 hook 与 MCP）。最常用的开关：

| 开关（环境变量） | 默认值 | 作用 |
|---|---|---|
| `OPENVIKING_AUTO_RECALL` | 开 | 每次 prompt 时召回并注入 profile/记忆 |
| `OPENVIKING_AUTO_CAPTURE` | 开 | 在 Stop/PreCompact/SessionEnd/Interrupt 上增量捕获 |
| `OPENVIKING_COMMIT_TURN_THRESHOLD` | 8 | 阈值提交之间捕获的轮数 |
| `OPENVIKING_AUTO_COMMIT_ON_COMPACT` | 开 | 压缩前提交 |
| `OPENVIKING_RECALL_LIMIT` / `OPENVIKING_SCORE_THRESHOLD` | 10 / 0.35 | 召回条数与分数阈值 |
| `OPENVIKING_RECALL_PEER_SCOPE` | `all` | `actor` 时只检索本工作区 peer 的记忆 |
| `OPENVIKING_PENDING_REPLAY_LIMIT` | 2（代码内默认；manifest 无法携带该字段） | 每次 SessionStart 重放的 pending 条数 |

完整开关清单见
[`memory-plugin-shared`](https://github.com/volcengine/OpenViking/tree/main/examples/memory-plugin-shared)
（`lib/config-schema.mjs`）。Kimi 插件的状态文件在
`~/.openviking/kimicode-plugin-state`（可用 `OPENVIKING_KIMICODE_STATE_DIR`
迁移）。完整宿主契约与设计记录见 `DESIGN.md`。

## 开发

`scripts/shared/` 与 `skills/` 为生成物。插件已登记在 `sync.mjs` 的 `TARGETS`
与 `SKILL_TARGETS` 中，改动共享源码后运行生成器，不要手改副本：

```bash
node ../memory-plugin-shared/sync.mjs   # 改动共享源码后运行
node --test scripts/*.test.mjs
```

共享库没有提供、属于本宿主的能力（成形轮次上的 `turn_id`、宿主的信封规则、
长度上限、按前缀推进的发送确认、保留标签清洗、扁平 manifest 的预算检查）分别
在 `capture-shape.mjs`、`recall-sanitize.mjs`、`ov-session.mjs` 与
`ov-memory-doctor.mjs` 中实现。

目录结构、模块边界与验收清单遵循
[插件开发规范](../../docs/zh/agent-integrations/18-plugin-development.md)。

## 状态

Beta。插件版本 0.2.6，已在 Windows 上对照 Kimi Code CLI 2.2.0 验证（macOS/Linux
预期可用但尚未实测）。已知缺口：subagent 对话（`agents/<subagent>/wire.jsonl`）
尚未捕获（v2），详见 `DESIGN.md`。
