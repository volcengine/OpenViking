# Antigravity CLI（agy）记忆集成

为 Antigravity CLI（`agy`）添加跨项目、跨会话的长期记忆。OpenViking 生命周期 Hook 会在每次模型调用前加载相关上下文，并在 `Stop` 时捕获会话 transcript 并提交给记忆抽取器。内置的 `/mcp` 端点仍可用于主动搜索、读取和管理记忆。

## 前置条件

- Linux 或 macOS，并已安装 Antigravity CLI（`agy`）。
- Node.js 18+（Hook 是通过 `sh -c` 执行的普通 Node 脚本）。
- 一个正在运行的 OpenViking 服务；远程使用需要 API Key（参见[鉴权](../guides/04-authentication.md)）。

## 安装

安装器尚未接入该 harness，请手动注册 Hook：

```bash
# 1. 把适配器复制到一个固定位置
mkdir -p "$HOME/.openviking/agent-integrations/agy"
rsync -a --delete ./examples/agy-memory-hooks/ "$HOME/.openviking/agent-integrations/agy/"
```

然后编辑 `~/.gemini/config/hooks.json`：它必须是合法 JSON，只有一个 `openviking`
键，并且两条命令都指向绝对路径：

```json
{
  "openviking": {
    "PreInvocation": [
      { "type": "command", "command": "node $HOME/.openviking/agent-integrations/agy/scripts/auto-recall.mjs", "timeout": 20 }
    ],
    "Stop": [
      { "type": "command", "command": "node $HOME/.openviking/agent-integrations/agy/scripts/auto-capture.mjs", "timeout": 30 }
    ]
  }
}
```

`~` 会被展开，命令以 hooks.json 所在目录作为工作目录执行；`node` 必须在 `PATH`
中。修改后请完全退出并重启 CLI。

### 排除敏感项目

在 `~/.openviking/ov.conf` 中添加 `agy` 段；会话 id 或工作区路径匹配到模式的会话
将完全不生效（不召回、不捕获）：

```json
{
  "agy": {
    "bypassSessionPatterns": ["**sensitive-project**"]
  }
}
```

`OPENVIKING_BYPASS_SESSION_PATTERNS`（逗号分隔）会覆盖该数组。该段同样接受
`enabled`、`autoRecall`、`autoCapture`、`workspacePeer`、`scoreThreshold` 和
`recallLimit`。

## 安装内容

- `PreInvocation`：重放待写入队列，每个会话加载一次用户画像，然后针对当前用户输入
  召回相关记忆，并通过 `injectSteps[].ephemeralMessage` 注入。
- `Stop`：解析会话 transcript
  （`~/.gemini/antigravity-cli/brain/<uuid>/.system_generated/logs/transcript.jsonl`），
  捕获新增的用户/助手轮次并立即提交，使短会话也能进入记忆抽取流程。
- 只有对话类记录会成为轮次。agy 把工具结果也标记为模型来源
  （`MODEL/VIEW_FILE`、`MODEL/RUN_COMMAND` 等），把 IDE 改动通知标记为用户来源
  （`USER_EXPLICIT/CODE_ACTION`），因此适配器同时按记录类型筛选：用户轮次取
  `USER_INPUT`，助手轮次取 `PLANNER_RESPONSE`。原始命令输出和文件内容不会进入记忆。
- 用户输入按原文保存：agy 会用 `<USER_REQUEST>` 包裹提问，并追加带本地时间的
  `<ADDITIONAL_METADATA>` 块，两者都会被剥离。
- 轮次按 `step_index` 排序，而不是按文件中的先后顺序：agy 异步刷写 transcript，
  记录在 `Stop` 前后可能乱序落盘。
- 去重基于内容哈希（而非步骤游标），因此重复扫描是幂等的，也不会静默丢弃轮次。

## 验证

1. 重启 `agy`，在一个项目目录下新建会话。
2. 提问一个与已有项目或个人偏好相关的问题，确认回答使用了已有记忆。
3. 告诉 Agent 一个临时偏好，等待回复完成；新建会话后再次询问，确认该值能从记忆中召回。

需要排查 Hook 时，设置 `OPENVIKING_DEBUG=1` 后运行，并查看 `~/.openviking/logs/agy-hooks.log`。

## 相关文档

- [TRAE、TRAE CN 与 TRAE CLI 记忆集成](./13-trae.md)
- [鉴权](../guides/04-authentication.md)
- [Agent 集成总览](./01-overview.md)
