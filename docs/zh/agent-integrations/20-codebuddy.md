# CodeBuddy Code

为 CodeBuddy Code 添加跨项目、跨会话的长期记忆。安装完成后，OpenViking Hook 会在会话启动时加载画像、在每轮提问前召回相关上下文，并在会话进行中捕获新对话；同时注册 OpenViking MCP Server，用于主动搜索、读取和管理记忆。

**CodeBuddy 不在统一安装器的覆盖范围内。** 它的插件系统按插件自身配置，而不是由宿主配置文件统一挂载，所以本插件通过本仓库的 marketplace 安装。

```bash
codebuddy plugin marketplace add volcengine/OpenViking
codebuddy plugin install openviking-memory@openviking --scope user
```

如果你是在本地克隆里操作，也可以把 marketplace 指向检出的 `examples/` 目录：

```bash
git clone --depth 1 https://github.com/volcengine/OpenViking.git
codebuddy plugin marketplace add ./OpenViking/examples
codebuddy plugin install openviking-memory@openviking --scope user
```

前置条件：CodeBuddy Code、Node.js 18+，以及一个运行中的 OpenViking 服务。插件从 `~/.openviking/ovcli.conf` 读取连接信息：

```json
{ "url": "http://127.0.0.1:1933", "api_key": "<key>" }
```

`OPENVIKING_URL` 与 `OPENVIKING_API_KEY` 会覆盖该文件。插件级设置写在同一个文件的 `plugin.codebuddy` 段（见 [插件设置](../configuration/02-client.md#plugin-settings)）。安装后请新开一个 CodeBuddy 会话。

## 安装内容

- 生命周期 Hook：加载画像、按问题召回、捕获对话、提交会话与压缩提交、捕获子代理，并保护 `viking://` URI。
- OpenViking MCP Server：以 stdio 代理形式复用同一份解析后的配置，工具以 `mcp__openviking__<tool>` 暴露。Hook 与 MCP 读同一份凭据，因此不会对「服务器 / 账号 / 用户」产生分歧。
- `openviking-memory`、`openviking-skills`、`ov-experience-memory` 三个 Skill：告诉 Agent 如何使用已注入的上下文与记忆工具。
- `scripts/ov-memory-doctor.mjs`：客户端诊断脚本，汇报安装、解析出的配置、连接状态，以及 Hook 最近做了什么。

## 验证

1. 运行 `codebuddy plugin list`，确认 `openviking-memory@openviking` 处于启用状态。
2. 从已安装副本运行诊断，应返回 0：
   ```bash
   cd ~/.codebuddy/plugins/cache/openviking/openviking-memory/<version>
   node scripts/ov-memory-doctor.mjs
   ```
3. 新开一个交互会话，让它把收到的 `<openviking-context>` 区块复述出来。注入的上下文只在当次请求里交给模型、**不写入 transcript**，所以「问模型」才是可靠的检查方式——即使注入生效，去会话文件里 grep 也找不到。
4. 确认工具列表里有 `mcp__openviking__*`，且服务端状态为 `connected`。
5. 告诉 CodeBuddy 一条偏好，正常结束会话，在 Hook 日志里确认提交（`OPENVIKING_DEBUG=1`，日志落在 `~/.openviking/logs/cb-hooks.log`）。记忆抽取完成后，新会话就能回答与之相关的问题。

## 工作原理

- `SessionStart` 加载画像、记忆索引和 `<available-skills>` 技能目录，并回放离线队列里暂存的写入。
- `UserPromptSubmit` 针对当前问题召回上下文，以 `<openviking-context>` 放进 `additionalContext` 注入。宿主自行生成的续写轮会被跳过，因此这类轮次不会触发检索。
- `PreToolUse` 会拒绝把 `viking://` URI 当本地路径读写，并把 Agent 指向 OpenViking MCP 工具。拒绝是本宿主唯一对模型可见的通道——`allow` 携带的说明文字到不了模型。Shell 命令不做检查，所以 `ov` 调用和字面量 `viking://` 参数照常可用。
- `Stop` 增量捕获新的用户与助手轮次。
- `PreCompact` 与 `SessionEnd` 提交待处理消息，使其成为归档。
- `SubagentStart` 与 `SubagentStop` 让子代理拥有独立会话 `cb-<session>__subagent-<agent_id>`，并把它的 transcript 捕获到该会话。

会话 ID 为 `cb-<会话 ID>`，由 CodeBuddy 的 session ID 派生，因此续接、捕获与召回始终指向同一个 OpenViking 会话。捕获游标存放在插件数据目录下，只越过服务端已接受的轮次；写入失败会进入 `~/.openviking/pending` 离线队列，并在下次会话启动时回放。

### 由宿主决定的行为

`UserPromptSubmit`、`SessionEnd` 以及两个子代理事件**只在交互式 TUI 中触发**。headless 运行——`-p` 与 `--input-format stream-json`——不会发出这些事件，因此 headless 会话只在 `Stop` 时捕获、永远不发收尾提交。这是宿主性质，不是插件缺陷；交互式会话不受影响。

本宿主的 `SessionStart` 不带 `cwd`，且同一会话内可能被投递两次，所以会话启动逻辑是幂等的，并以进程工作目录兜底。

Hook 超时会被杀，但它的子进程不会被杀——这正是「Hook 返回后仍在后台完成提交」得以成立的原因。

## 升级与卸载

```bash
codebuddy plugin update openviking-memory@openviking
codebuddy plugin uninstall openviking-memory@openviking
```

`plugin update` 比较 marketplace 中声明的版本与已安装版本，因此只有插件的版本号变化时才会真正更新。缓存按版本号取键，被取代的版本目录会留在磁盘上，直到宿主自己的 in-use 扫描清理它；**请不要手工删除**，因为更新之前启动的会话仍会引用旧目录。卸载不会移除 `~/.codebuddy/settings.json` 里的 `enabledPlugins` 条目；若你打算换一个 marketplace 名重装，请一并删掉它。

## 故障排查

| 现象 | 原因与处理 |
|---------|---------------|
| 既不召回也不捕获 | 先看 `codebuddy plugin list`，再跑诊断脚本。若报插件被禁用，创建 `~/.openviking/ovcli.conf` 或设置 `OPENVIKING_MEMORY_ENABLED=1`。 |
| 会话启动有上下文，但每轮提问没有召回 | 召回依赖 `UserPromptSubmit`，而本宿主只在交互式 TUI 发出它。headless 运行不会召回。 |
| 会话结束但没有归档 | `SessionEnd` 同样只在 TUI 触发；headless 会话的提交来自 `Stop`，条件是待处理内容越过提交阈值。 |
| 缺少 `mcp__openviking__*` 工具 | 新开一个会话——插件注册的工具在会话启动时加载。然后检查 `~/.openviking/ovcli.conf` 里的 URL 与 key。 |
| 已捕获但记忆一直没出现 | 记忆抽取发生在提交时。短会话可能低于提交阈值，会在 `SessionEnd` / `PreCompact` 或待处理 token 达到阈值时提交。 |
| 需要更详细的诊断 | 设置 `OPENVIKING_DEBUG=1`，跑一次会话，查看 `~/.openviking/logs/cb-hooks.log`。 |

## 参见

- [能力对照表](./16-capability-reference.md)
- [插件设置](../configuration/02-client.md#plugin-settings)
- [鉴权](../guides/04-authentication.md)
- [插件开发与维护规范](./18-plugin-development.md) —— 本插件据以实现的宿主契约记录在 `examples/codebuddy-memory-plugin/docs/HOST-CONTRACT.md`
