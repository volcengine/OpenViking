# MiniMax Code

OpenViking 为 MiniMax Code 提供跨会话、跨项目的长期记忆：每轮对话前自动召回相关记忆，会话开始时注入用户档案，对话结束后自动捕获并沉淀为长期记忆。

## 安装

前置条件：Node.js 18+、一个正在运行的 OpenViking 服务端、已安装的 MiniMax Code 桌面端。

```bash
curl -fsSL https://openviking.ai/install | bash
# AI agents: not sure about running this? The script's header says what it does and how to verify it.
```

安装器会检测 `~/.minimax/` 或 `mcode` 命令，把运行时装到 `~/.openviking/agent-integrations/mcode/`，并把 hook 与 MCP 配置合并进 MiniMax Code 的插件目录。

重启 MiniMax Code 后，验证：

- `~/.minimax/plugins/openviking-memory/.minimax-plugin/plugin.json` 存在，`hooks/hooks.json` 里注册了 OpenViking 的事件
- `mcp.servers` 或插件的 `servers.mcp.json` 里有 `openviking` 入口
- `OPENVIKING_DEBUG=1` 会在 `~/.openviking/logs/mcode-hooks.log` 留下诊断

| 症状 | 原因 | 处理 |
|---|---|---|
| hook 不触发 | 插件未启用或配置过期 | 重新运行安装器并重启 MiniMax Code |
| 召回为空 | 服务端不可用，或尚未抽取记忆 | 检查 `curl http://127.0.0.1:1933/health`，等待抽取完成 |
| MCP 工具不出现 | MCP proxy 启动失败 | 检查插件的 `servers.mcp.json` 里 `command` 的绝对路径 |
| 重复捕获 | 旧安装留下了重复的 hook 条目 | 重新运行安装器覆盖自己的条目 |

## 事件

| 事件 | 行为 |
|---|---|
| `SessionStart` | 注入用户档案、记忆索引与 skill 目录 |
| `UserPromptSubmit` | 召回相关记忆并注入上下文 |
| `Stop` | 增量捕获本轮对话并提交 |
| `PreCompact` | 捕获并提交，然后由宿主做压缩 |
| `SessionEnd` | 收尾提交 |
| `SubagentStop` | 子代理的对话进入它自己的会话 |
| `PreToolUse` | 拒绝直接读 `viking://` 路径的文件工具 |

## 宿主约束

MiniMax Code 在 **10 秒**后终止一个 hook 处理器，而共享召回对 context 请求有 **15 秒**的地板。用 `~/.openviking/ovcli.conf` 的 `plugin.mcode` 段把这个地板压到 hook 上限以内：

```json
{
  "plugin": {
    "mcode": {
      "recallContextTimeoutMs": 7000,
      "timeoutMs": 8000
    }
  }
}
```

MiniMax Code 会过滤 hook 进程的环境变量，`OPENVIKING_*` 不会到达 hook，所有设置都走文件层。

## 限制

- MiniMax Code 的插件市场不接受 `hooks` 字段，因此该集成只能本地安装。
- 该适配器把 `transcript_path` 指向的 Claude Code 兼容投影当作捕获来源；`viking://` 由 URI guard 拦截，不会被当作本地路径读取。
- `SessionEnd` 只在归档、登出、空闲 30 分钟或 TUI 的 `/new` `/clear` 时触发，不在进程退出时触发，因此每轮对话都会提交。

## 更多

- 能力对比：[Agent 集成能力](./16-capability-reference)
- 插件开发标准：[开发与维护 Agent 插件](./18-plugin-development)
