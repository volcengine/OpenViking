# CodeBuddy Code CLI 的 OpenViking 记忆插件

为 [CodeBuddy Code](https://cnb.cool/codebuddy/codebuddy-code) 提供长期语义记忆，由
[OpenViking](https://github.com/volcengine/OpenViking) 驱动。

插件通过生命周期 hooks 读写 OpenViking，并通过 MCP 暴露 OpenViking 的工具。两者解析同一份
插件配置，因此 hooks 与 MCP 对「服务器 / account / user」的理解永远一致——这条性质由共享测试
套件直接校验（`mcp-hook-parity`）。

## 状态

针对 CodeBuddy Code **v2.163.0** 构建（也在 v2.164.0 上跑过）。host 契约是**用一次性探针插件
在真实 CLI 会话里实测**得出的（交互 TUI、`-p`、`--input-format stream-json` 三种），不是照
文档抄的——完整事实、方法与仍未验证的项见
[`docs/HOST-CONTRACT.md`](docs/HOST-CONTRACT.md)。影响设计的实测结论：

- hooks 在交互 TUI 与 headless（`-p`）下都会触发，但 **`UserPromptSubmit`、`SessionEnd` 与
  `SubagentStart`/`SubagentStop` 只在交互 TUI 触发**；
- `additionalContext` 信封与 Claude Code 字节级兼容、**未知 JSON 字段被容忍**、空输出安全；
- `PreToolUse` **没有「劝告」通道** —— `allow` 携带的 `permissionDecisionReason` 到不了模型；
  但 **`deny` 的理由会到达**（作为失败的 tool_result），所以 URI 守卫选择 deny 而非提示；
- hook 超时会被杀，但**它的子孙进程不会被杀** ⇒ 分离式异步写安全；
- `SessionStart` **不带 `cwd`**（用 `process.cwd()` 兜底）；`Stop` 触发时 transcript 已完整。

## 目录结构

```
.codebuddy-plugin/plugin.json   清单（只有 `name` 必填）
hooks/hooks.json                hook 注册（用 ${CODEBUDDY_PLUGIN_ROOT}）
.mcp.json                       本插件的 MCP server
servers/mcp-proxy.mjs           stdio MCP 代理，转发到解析后的配置
scripts/                        手写的 hook 入口 + 测试
scripts/lib/                    手写的插件本地辅助模块
scripts/shared/                 由 examples/memory-plugin-shared/lib 生成 —— 不要手改
skills/                         由 examples/skills 生成 —— 不要手改
docs/HOST-CONTRACT.md           实测得到的 host 契约与方法
```

`scripts/shared/` 与 `skills/` 由仓库根目录的
`node examples/memory-plugin-shared/sync.mjs` 生成。改共享库（`examples/memory-plugin-shared/lib/`）
后重跑生成器；**永远不要手改生成物**。

## 各 hook 做什么

| 事件 | 入口 | 行为 |
| --- | --- | --- |
| `SessionStart` | `session-start.mjs` | 探测健康度 → 回放离线队列 → 把 profile/目录（resume/compact 时还有 archive）合成一个 `<openviking-context>` 注入 |
| `UserPromptSubmit` | `auto-recall.mjs` | 检索 OpenViking 并注入 `<openviking-context>`；**跳过 host 驱动的续写轮**（`is_internal_continuation`） |
| `PreToolUse` | `uri-guard.mjs` | 对路径是 `viking://` 的文件类工具调用 `deny`，并指向 OpenViking MCP 工具 |
| `Stop` | `auto-capture.mjs` | 增量读 transcript、推送新轮；pending 超过阈值时 commit |
| `PreCompact` | `pre-compact.mjs` | transcript 被改写前先 commit |
| `SessionEnd` | `session-end.mjs` | 收尾 commit，让最后几轮变成 archive |
| `SubagentStart` | `subagent-start.mjs` | 记住子代理自己的 OV 会话 id |
| `SubagentStop` | `subagent-stop.mjs` | 把子代理的 transcript 推进那个会话并 commit |

增量游标在 `${CODEBUDDY_PLUGIN_DATA}/capture-state`，**只随 ack 前进** ⇒ 瞬时失败会重推而不是
丢轮；重复投递的 `Stop` 不会重复发送。失败进离线队列（`~/.openviking/pending`），下次
`SessionStart` 回放。

⚠️ **capture 会把你的对话写进 OpenViking。** 这是设计目的，但值得明说：插件启用后，会话
transcript 会被发往配置的服务器，OpenViking 会从中抽取记忆。用
`OPENVIKING_MEMORY_ENABLED=0`、`OPENVIKING_BYPASS_SESSION` 或
`OPENVIKING_BYPASS_SESSION_PATTERNS` 可排除特定会话。

## 配置

解析顺序（与所有 OpenViking 记忆插件一致）：

```
OPENVIKING_* 环境变量 → 工作区 .openviking/config*.json → ovcli.conf 的 `plugin.codebuddy`
→ ovcli.conf 的 `plugin` → ov.conf 的 `codebuddy` 段 → schema 默认值
```

连接与凭据始终来自 `OPENVIKING_URL` / `OPENVIKING_API_KEY`（或
`OPENVIKING_BEARER_TOKEN`），其次是 `ovcli.conf`，最后是 `ov.conf` 的 `server` 段。**在一台
完全没有任何环境变量的机器上**，一个含 `{ "url": …, "api_key": … }` 的
`~/.openviking/ovcli.conf` 就够。

启停：`OPENVIKING_MEMORY_ENABLED=0|1`，或 `ov.conf` 里的 `codebuddy.enabled`。两者都没有时，
仅当 `ov.conf` 或 `ovcli.conf` 存在才算启用。

调试日志：`OPENVIKING_DEBUG=1`（或 `codebuddy.debug: true`）会把 JSON Lines 写到
`~/.openviking/logs/cb-hooks.log`（可用 `OPENVIKING_DEBUG_LOG` 覆盖路径）。

## 本地加载

```bash
codebuddy --plugin-dir examples/codebuddy-memory-plugin
```

`--plugin-dir` 是会话级的：不写任何 CodeBuddy 配置；去掉参数即无痕——除了插件自己的状态目录
`~/.codebuddy/plugins/data/<id>-inline/`。要长期生效，把 `examples/`（它声明了 `./codebuddy-memory-plugin`）作为 marketplace 装上，再装进 user 作用域：

```bash
codebuddy plugin marketplace add examples
codebuddy plugin install openviking-memory@openviking --scope user
```

## 测试与诊断

```bash
npm test                                # 34 个单测/hook 测试（mock OpenViking，不联网）
node scripts/ov-memory-doctor.mjs       # 安装 + 配置 + 连接 + 活动 报告
node scripts/ov-memory-doctor.mjs --json
```

doctor 会读插件登记、解析后的配置、线上服务器与 hooks 留下的状态。它的**安装段**只有经
marketplace 安装后才会转绿；在 `--plugin-dir` 开发态下它会 warn 而非 fail（`codebuddy plugin
list` 那一行除外）。

## 已知限制

- **`PreCompact` 只在真的发生压缩时触发**，已由真实 TUI 会话证实：host 会留下
  `{"type":"summary","providerData":{"source":"pre-compact"}}` 记录，而把轮归档的正是本插件的
  commit。想主动触发可用 `/compact`。
- **recall 与收尾 commit 只在交互 TUI 跑**：headless（`-p` / `stream-json`）不发
  `UserPromptSubmit` 与 `SessionEnd` ⇒ headless 会话只在 `Stop` 捕获、永不 commit。这是 host
  性质，不是插件缺陷。
- **URI 守卫只能拦不能劝**：CodeBuddy 在 `PreToolUse` 下没有 `additionalContext`，且 `allow`
  的理由到不了模型 ⇒ 无法「放行并提醒」。同理 `Bash` 不进守卫的 matcher。
- **子代理只在 `SubagentStop` 被捕获**：子代理内部 hook 不发 ⇒ 若子代理异常退出、事件没发，
  它的会话就不会入库。
- **技能由 CodeBuddy 发现，不是 OpenViking 安装**：`skills/` 里三个技能是文件；插件在
  session start 注入的**技能目录**则是服务器持有的那份。

## 命名

harness id 是 **`codebuddy`**。OpenViking 服务端的日志导入子系统里有一个 `workbuddy`
适配器解析同样的 transcript 格式，但那个名字属于服务端的 ingest 命名空间——它不是 harness
id，**不要**注册进 `HARNESS_KEYS`（写成 `plugin.workbuddy.*` 会被静默忽略）。
