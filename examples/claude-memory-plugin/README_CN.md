# OpenViking Claude 记忆插件（方案 B）

基于 **OpenViking Session 记忆** 的 Claude Code 记忆插件。

- 在 Claude 会话期间累积会话数据（`Stop` 钩子）。
- 在 `SessionEnd` 时，插件调用 `session.commit()` 触发 OpenViking 记忆提取。
- 记忆召回由 `memory-recall` 技能处理。

## 此版本的设计选择

- 模式：**自动切换**
  - 优先尝试 HTTP 模式（从 `./ov.conf` 的 `server.host` + `server.port` 读取，健康检查 `/health`）
  - 如果服务器不可达，则回退到嵌入式本地模式
- 配置：**严格**
  - 必须在项目根目录有 `./ov.conf` 文件
- 插件状态目录：`./.openviking/memory/`

## 目录结构

```text
examples/claude-memory-plugin/
├── .claude-plugin/
│   └── plugin.json
├── hooks/
│   ├── hooks.json
│   ├── common.sh
│   ├── session-start.sh
│   ├── user-prompt-submit.sh
│   ├── stop.sh
│   └── session-end.sh
├── scripts/
│   ├── ov_memory.py
│   └── run_e2e_claude_session.sh
└── skills/
    └── memory-recall/
        └── SKILL.md
```

## 钩子行为

- `SessionStart`
  - 验证 `./ov.conf`
  - 自动检测后端模式（http/local）
  - 创建新的 OpenViking 会话并持久化插件状态
- `UserPromptSubmit`
  - 添加轻量级提示，表明记忆可用
- `Stop`（异步）
  - 解析最后一条对话记录
  - 总结本轮对话（当可用时使用 `claude -p --model haiku`；回退到本地总结）
  - 将用户和助手总结追加到 OpenViking 会话
  - 按最后一条用户消息 UUID 去重
- `SessionEnd`
  - 提交 OpenViking 会话以提取长期记忆

## 技能行为

`memory-recall` 运行桥接命令：

```bash
python3 .../ov_memory.py recall --query "<query>" --top-k 5
```

它搜索以下位置：

- `viking://user/memories/`
- `viking://agent/memories/`

然后返回简洁的、带来源链接的记忆摘要。

## 一键端到端测试

使用源配置运行真实的 Claude 无头会话端到端测试：

```bash
cd /Users/quemingjian/.codex/worktrees/6e45/OpenViking
bash /Users/quemingjian/.codex/worktrees/6e45/OpenViking/examples/claude-memory-plugin/scripts/run_e2e_claude_session.sh
```

自定义源配置和提示：

```bash
bash /Users/quemingjian/.codex/worktrees/6e45/OpenViking/examples/claude-memory-plugin/scripts/run_e2e_claude_session.sh \
  /Users/quemingjian/Source/OpenViking/ov.conf \
  "请只回复: CUSTOM_E2E_TOKEN"
```

脚本执行内容：

- 在 `/tmp` 下创建 Python 3.11 虚拟环境（一次性安装依赖）。
- 从源配置生成临时项目 `./ov.conf`，并注入 HTTP 服务器字段。
- 启动 OpenViking HTTP 服务器，使用此插件运行真实的 `claude -p` 会话，然后触发确定性的 Stop + SessionEnd 验证。
- 验证 `session_state.json`、`ingested_turns >= 1` 以及会话归档文件创建。
- 完成后恢复原始 `./ov.conf`。

## 注意事项

- 此 MVP 不修改 OpenViking 核心。
- 如果缺少 `./ov.conf`，钩子会安全降级并在 systemMessage 中报告状态。
- 状态文件：`./.openviking/memory/session_state.json`