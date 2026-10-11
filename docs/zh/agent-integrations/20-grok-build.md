# Grok Build

让 Grok Build 在不同项目和会话之间使用长期记忆。OpenViking 会为每次提问召回相关上下文、捕获已完成的回合，并通过 MCP 工具提供显式记忆操作。

## 重要的交付行为

Grok Build 当前会丢弃已放行 `UserPromptSubmit` hook 的标准输出。因此 OpenViking 在提交提问时完成召回并缓存结果，再通过第一次 `PostToolUse` 或 `PostToolUseFailure` 事件交付一次。

这有一个可见限制：自动召回要等本轮第一次工具结果之后才进入模型。没有工具调用的回合不会收到自动召回上下文。OpenViking MCP 工具从会话开始时就可用。

## 安装

前置条件：macOS 或 Linux、Node.js 18+，以及支持原生 Hooks、MCP 和 Skills 的 Grok Build 版本。

```bash
curl -fsSL https://openviking.ai/install | bash -s -- --harness grok
```

安装器会询问 OpenViking 连接配置。安装后重启 Grok Build。

## 安装内容

- `~/.grok/hooks/openviking-memory.json` 中的原生生命周期 Hook。
- `~/.grok/config.toml` 中带安装器标记的 `openviking` MCP 服务配置块。
- `~/.grok/skills/` 下的 `openviking-memory`、`openviking-skills` 和 `ov-experience-memory` Skill。
- `~/.openviking/agent-integrations/` 下的共享运行时。

安装器会保留无关的 Hook 和 TOML 配置。如果已有不归安装器管理的 `mcp_servers.openviking` 表，安装器会拒绝覆盖。

## 工作方式

1. `SessionStart` 重放之前离线会话排队的可重试写入。
2. `UserPromptSubmit` 为新提问召回上下文。第一次提问还会准备 profile、记忆索引和 OpenViking skill 清单。此时不写标准输出，因为 Grok 会丢弃它。
3. 第一次 `PostToolUse` 或 `PostToolUseFailure` 消费缓存，并用 `additionalContext` 返回。该回合后续工具结果不再输出这段内容。
4. `Stop` 读取 Grok 原生的 `lastAssistantMessage`，与缓存的提问配对，保存本轮并 commit，供后续记忆抽取。

会话使用 `gr-<session id>` 前缀。Hook 与 MCP 共用 `~/.openviking/ovcli.conf` 中的凭据。

## 验证

1. 重启 Grok Build 并新建会话。
2. 运行 `/hooks`，确认五个 OpenViking 事件已启用：`SessionStart`、`UserPromptSubmit`、`PostToolUse`、`PostToolUseFailure` 和 `Stop`。
3. 运行 `grok mcp list`，确认 `openviking` 已启用。
4. 提问一个已存储的偏好，并让该提问触发一次工具调用。确认第一次工具结果带有 OpenViking 上下文提示。
5. 完成一轮。使用 `OPENVIKING_DEBUG=1` 启动后，检查 `~/.openviking/logs/grok-hooks.log` 中的捕获与 commit。

## 升级与卸载

重新运行安装命令即可升级。卸载命令：

```bash
curl -fsSL https://openviking.ai/install | bash -s -- --uninstall --yes --harness grok
```

卸载只移除 OpenViking Hook 文件、受管理的 MCP 配置块、已安装的 Skill 和运行时文件。其他 Grok 配置保持不变。

## 排障

| 现象 | 原因与处理 |
|------|------------|
| 工具调用前看不到召回 | 这是预期行为。Grok 会丢弃已放行 `UserPromptSubmit` 的标准输出，第一次工具结果才携带缓存上下文。 |
| 没有工具调用的回答未使用自动召回 | 这是当前载体限制。需要立即使用记忆时，让 Grok 调用 OpenViking MCP 搜索工具。 |
| 安装器拒绝 MCP 配置 | `~/.grok/config.toml` 已有不受管理的 `mcp_servers.openviking` 表。安装前请重命名或移除该服务。 |
| MCP 无法连接 | 检查 `~/.openviking/ovcli.conf` 中的 URL 与 API key，然后重启 Grok Build。 |
| 中断或失败的回合没有被捕获 | OpenViking 捕获 `Stop`。Grok 只在正常完成时发送该事件，`StopCancelled` 与 `StopFailure` 不用于捕获。 |

## 另请参阅

- [集成能力对照](./16-capability-reference.md)
- [身份认证](../guides/04-authentication.md)
- [Grok Build Hooks 文档](https://github.com/xai-org/grok-build/blob/main/crates/codegen/xai-grok-pager/docs/user-guide/10-hooks.md)
