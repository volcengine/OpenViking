# OpenViking 记忆

基于 [OpenViking](https://github.com/volcengine/OpenViking) 服务端，为 DeepSeek Harness 提供长期记忆。DeepSeek Harness Desktop 和 `dsh web` 均可使用。

- **召回**：每个带有你输入的步骤开始前，把相关记忆加入对话。
- **捕获**：把你的消息和助手的回复发送到 OpenViking，并提交为记忆。
- **工具与技能**：模型可以使用 OpenViking 工具（`mcp__openviking__search`、`read`、`remember`、`add_skill` 等），以及 `openviking-memory`、`openviking-skills`、`ov-experience-memory` 三个技能。
- **URI 保护**：文件工具指向 `viking://` 路径时会被拦下，并提示改用对应的 OpenViking 工具；`bash` 或 `pwsh` 命令里带 `viking://` URI 时照常执行，只给模型附一条提示。

## 1. 连接服务端

写 `~/.openviking/ovcli.conf`，填 `url` 和 `api_key`（trusted 模式的服务端还要填 `account` 和 `user`）。如果用的是 `http://127.0.0.1:1933` 上无鉴权的本地服务端，这步可以跳过。参见 [部署指南 → CLI](https://github.com/volcengine/OpenViking/blob/main/docs/zh/guides/03-deployment.md#cli)。

在 Desktop 里建议用 `ovcli.conf` 而不是环境变量。macOS 上 Desktop 只在启动时读取一次登录 shell 的环境变量；Windows 上使用用户环境变量。改动后需要彻底退出并重新打开 Desktop。

## 2. 安装

**通过安装器（macOS）**：

```bash
curl -fsSL https://openviking.ai/install | bash
```

安装器需要 Node.js 18 或更高版本。它会找到 Desktop，作为 `dsh-desktop` harness 安装进去。运行前先打开一次 Desktop 让它创建 profile，安装期间用 ⌘Q 退出 Desktop：关闭窗口只是把它隐藏起来。

**通过插件页面（手动；macOS、Windows 和 `dsh web`）**：插件 → 添加插件 → 输入 `@openviking/dsh-memory-plugin` → 安装，开关未打开时再打开。这一步只添加插件，不会询问 OpenViking 凭据，所以要先完成第 1 步。

**通过 Desktop 自带的 `dsh` 命令（macOS）**：先打开一次 Desktop，用 ⌘Q 退出，再运行：

```bash
"/Applications/DeepSeek Harness.app/Contents/Resources/runtime/cli/bin/dsh" plugin --profile desktop add @openviking/dsh-memory-plugin
```

通过 npm 安装的 `dsh` 无法管理 `desktop` profile。用命令行安装后，重新打开 Desktop。

## 3. 验证

新开一个对话。顶部附近应能看到 OpenViking 上下文块，模型的工具列表里应有 `mcp__openviking__*`。问一件之前对话里的事，确认召回生效。

在终端里检查安装结果（需先退出 Desktop）：

```bash
"/Applications/DeepSeek Harness.app/Contents/Resources/runtime/cli/bin/dsh" plugin --profile desktop ls
```

## 注意事项

- **没有设置页面。** 通过 `ovcli.conf` 或 `OPENVIKING_*` 环境变量配置插件。行为参数也可以写进 `~/.dsh/profiles/desktop/cordis.patch.yml` 的 `openviking-memory-runtime` 条目。恢复对话框里的“禁用第三方插件、备份 profile patch 并重启”会重置 profile 的插件，并把这个文件挪作备份，所以长期设置请放在 `ovcli.conf`，恢复后记得重新启用插件。
- **工具身份。** 自动召回和捕获按每个对话所在的项目区分。OpenViking 工具每个 profile 只启动一次，工作目录是 profile 目录，所以在 `OPENVIKING_RECALL_PEER_SCOPE=actor` 下它们没有项目 peer。需要时在 `ovcli.conf` 里设置 `actor_peer_id`，或设置 `OPENVIKING_PEER_ID`。这个 peer 也会取代每个对话的项目 peer 用于召回和捕获，因此所有对话共用它。
- **技能。** 内置技能只对预设里带技能工具的 agent 可见。`minimal` 预设不带，用它的 agent 看不到这些技能。
- **日志。** Desktop 不显示插件输出。要排查 OpenViking 工具，设置 `OPENVIKING_DEBUG=1` 和 `OPENVIKING_DEBUG_LOG=<文件>`，然后重新打开 Desktop。

## 卸载

插件 → OpenViking 记忆 → 卸载。或者在退出 Desktop 后运行：

```bash
"/Applications/DeepSeek Harness.app/Contents/Resources/runtime/cli/bin/dsh" plugin --profile desktop rm @openviking/dsh-memory-plugin
```

完整文档：[DeepSeek Harness 集成](https://github.com/volcengine/OpenViking/blob/main/docs/zh/agent-integrations/17-dsh.md) 和 [插件 README](https://github.com/volcengine/OpenViking/tree/main/examples/dsh-memory-plugin)。
