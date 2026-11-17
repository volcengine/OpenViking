# Kimi Code 记忆插件（独立分发，社区实现）

> **社区实现。** 本插件由社区维护，不是 OpenViking 官方一方集成；旧的
> `agent-hook-plugin` Kimi 适配器同样属于社区实现。

[Kimi Code](https://www.kimi.com/coding) 是 Moonshot AI 的编程 Agent。本插件通过 OpenViking 为它提供长期记忆：捕获模型实际看到的内容，在你提问时召回相关记忆，并通过 MCP 暴露服务端自己的工具。

本页只描述 `examples/kimi-code-memory-plugin` 中的独立分发插件。
`agent-hook-plugin/hosts/kimicode` 集成面向 Kimi Code CLI 0.43.x，不在本页范围内，也未做改动。

它是以自包含目录或 ZIP 分发的 Kimi Code 插件，用 `/plugins install` 安装，hook 与 MCP server 内联声明在 `kimi.plugin.json` 中。没有额外安装器，插件也从不自行改写宿主注册表。

本插件与旧的 `agent-hook-plugin` 适配器**都是 Kimi 原生插件**：Kimi 都从 managed 插件目录加载。区别在分发方式，不在插件形态。旧 manifest 指向 `./agent-integrations/kimicode/...`，该路径只有 OpenViking 安装器组装后才存在，因此它的源码目录不能直接安装；本插件是自包含的，仓库里的目录本身就是可运行产物。

hook payload、MCP 工具命名和 wire 协议已对照 **Kimi Code 2.6.2** 内嵌源码核对：POSIX detached 启动与进程组信号、Windows `taskkill /T`、`Stop`/`PreCompact`/`SessionEnd` 事件形状、fork 的 `base.{branch,line}` / `legacyUndoLine`、session 的 `environmentId`。这是源码证据，不是运行时测试。本机 Windows 上已用真实 OpenViking 0.5.5 服务验证 hook 驱动的捕获与召回；POSIX、远程/Web 环境、真实 turn-sliced fork 以及更旧或更新的宿主均未验证。详细证据见插件 README。

## 行为

| 事件 | 行为 | 输出 |
| --- | --- | --- |
| `SessionStart` | 重放队列中的写入；扫描此前已结束的会话 | 无 |
| `UserPromptSubmit` | 先补齐上一轮，再注入 profile 与召回 | 一段文本 |
| `PreToolUse` | 拒绝把 `viking://` 路径交给本地文件工具 | deny JSON 或无 |
| `Stop` | 送达已闭合轮次，超过阈值则提交 | 无 |
| `PreCompact` | 补齐后提交，不受阈值限制 | 无 |
| `SessionEnd` | 先写结束标记，再补齐并提交 | 无 |

**捕获**增量读取每个 agent 的 `wire.jsonl`，并投影*活动分支*：wire 是一棵树，分支切换只保留 base 前缀加上新段。只有当宿主会把某输入作为用户消息展示时，它才成为 `user_query`，因此 task 提示、注入内容和 hook result 不会变成记忆。助手文本、工具调用及其结果按模型看到的顺序保留；thinking、媒体和压缩摘要不捕获。

**送达与交接分开记录**：服务端确认才算送达；已持久化到本地 pending 的消息可以推进交接游标，但不计为送达，也阻止后续新消息抢先发送。永久失败的捕获会阻止提交，直到配置变化或人工接受损失——归档一份有洞的对话比等待更糟。

**召回**在首次成功提问时注入用户 profile 与服务端 skill 清单，之后只做逐轮召回。`noAutoInject`、`autoRecall`、`minQueryLength` 是三个独立开关，都不影响显式 MCP 调用。召回失败时放行：服务不可达、超时或无结果都不产生任何文本，绝不会把错误写进你的提问。

## 安装

本地检出可以直接安装：

```text
/plugins install <examples/kimi-code-memory-plugin 的路径>
```

主线仓库发布 release 后，其 ZIP 可以一条命令安装（tag 遵循仓库 `<包名>@<版本>` 惯例）：

```text
/plugins install https://github.com/volcengine/OpenViking/releases/download/kimi-code-memory-plugin@<version>/kimi-code-memory-plugin-<version>.zip
```

随后用 `/plugins info openviking-memory` 确认。hook 从 `PATH` 调用 `node`，因此宿主继承的 `PATH` 中必须有 Node.js 22.19+——桌面启动不读取 shell profile。

插件与其他 OpenViking 插件共用配置链，并按**会话所在目录**解析，所以 workspace 的 `.openviking/config.json` 作用于你当前的项目，而不是插件自己的目录。凭据始终来自 `ovcli.conf` 或环境变量，从不来自 workspace 文件。

## 工具与 Skills

MCP server 名为 `openviking`，显示为 `mcp__plugin-openviking-memory_openviking__<tool>`。工具列表就是服务端返回的内容；插件不保留副本。

| Skill | 用途 |
| --- | --- |
| `openviking-memory` | 检索、记住、遗忘 |
| `openviking-skills` | 查找、读取、创建、安装服务端 Skills |
| `openviking-rule-search` | 检索适用的 Rule |
| `ov-experience-memory` | 检索并应用 Experience |

Kimi Code 显示为 `openviking-memory:<skill>`。Skills 只描述服务端真实提供的工具，并说明当服务端版本缺少 `rule_search` 或 `add_skill` 时如何降级。

## 诊断

```bash
node ./scripts/doctor.mjs             # 完整报告
node ./scripts/doctor.mjs --offline   # 不发起网络请求
```

doctor 检查 manifest 与 hook 注册、宿主插件注册表、残留的手工 hook、解析后的配置与凭据、每个会话的阻塞状态、离线队列，以及服务端健康。被阻塞的会话会附带可直接执行的 `scripts/recover.mjs` 命令。

## 导入已有历史

hook 只从首次看到会话的时刻开始记忆。要导入插件安装前的会话：

```bash
node ./scripts/backfill.mjs --dry-run        # 只列计划；不发请求、不写状态
node ./scripts/backfill.mjs                  # 导入所有符合条件的会话
node ./scripts/backfill.mjs --session <id>   # 只导入一个 Kimi 会话
```

没有插件状态的会话从首条记录开始读，并在结束时提交；已有状态的会话只沿游标追平，因为服务端没有消息去重。命令会遵守 `autoCapture` 与 `bypassSession`，写入前检查 `/health`，并跳过非本地环境、legacy wire 和 fork 的继承前缀。活动会话的开放尾部留给下一次 hook 或再次运行，在该轮结束前其 commit 保持阻塞。

## 升级与卸载

重跑同一条命令即可升级；宿主会以同一插件名替换 managed 目录中的副本：

```text
/plugins install <examples/kimi-code-memory-plugin 的路径>
```

卸载：

```text
/plugins remove openviking-memory
```

`remove` 只删除宿主注册记录，保留插件文件。已写入 OpenViking 的记忆、待发队列和其他插件都不受影响。如果希望文件也消失，需要自行删除 managed 目录。

## 限制

- **至少一次送达。** 发送成功但状态写入失败的消息会被重放；稳定 wire id 抑制多数重复，该集合有上限。
- **已送达轮次无法撤回。** undo、clear 和分支切换只影响尚未送达的内容。
- **不自动导入安装前的历史。** 插件首次看到的会话从当前文件尾开始；导入过去需要显式运行 `scripts/backfill.mjs`。
- **不支持媒体。** 只捕获文本，从不读取媒体文件。
- **仅本地会话。** `environmentId` 非本地的会话会被跳过并留下诊断，因为它的 wire 不在这台机器上。
- **崩溃尾部依赖 sweep。** 只有同时存在结束标记、持有会话锁、且两次读取之间 wire 保持不变时，开放轮次才会被闭合。

完整配置表、恢复命令与开发流程见插件自身的 [README_CN](https://github.com/volcengine/OpenViking/blob/main/examples/kimi-code-memory-plugin/README_CN.md)。
