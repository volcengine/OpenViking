# Codex 记忆插件

为 [Codex](https://developers.openai.com/codex) 提供跨会话记忆。插件通过 hooks 自动召回、捕获和提交对话，通过 MCP 提供检索、读取和记忆管理工具。

源码：[examples/codex-memory-plugin](https://github.com/volcengine/OpenViking/tree/main/examples/codex-memory-plugin) | [博客：动机与效果展示](https://blog.openviking.ai/post/openviking-coding-agent/)

## 安装

Claude Code 和 Codex 共用同一个安装脚本。它会依次询问界面语言（English/中文）、要安装的 harness、下载源和 OpenViking 凭据；安装步骤支持重复执行。

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/volcengine/OpenViking/main/examples/memory-plugin-shared/install.sh)
```

TraeCode CLI 2.0 可以直接安装这一 Codex 格式插件，默认安装入口是 `--harness trae-cli`：

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/volcengine/OpenViking/main/examples/memory-plugin-shared/install.sh) \
  --harness trae-cli
```

GitHub 访问受限的地区，从火山引擎 TOS 镜像运行同一个安装脚本（或在下载源提问时选择「TOS 镜像」）。Codex 走 TOS 时安装自 TOS 托管的 git 仓库，保留远程更新能力：

```bash
bash <(curl -fsSL https://ovrelease.tos-cn-beijing.volces.com/memory-plugin-shared/install.sh)
```

现在不再需要任何 shell wrapper——插件自带的 stdio MCP 代理会在运行时读取 `~/.openviking/ovcli.conf`（或 `OPENVIKING_*` 环境变量），与 hooks 使用同一套配置链。安装完成后启动 Codex（TraeCode CLI 2.0 是 `trae-cli`）：

```bash
codex
```

### 首次启动：信任 hooks

安装插件不会自动信任它的 hooks。首次启动时 Codex 会显示类似下面的审阅提示，具体界面随 Codex 版本变化。选 **Trust all and continue**，或先选 **Review hooks** 查看命令。选 *Continue without trusting* 后 hooks 不会运行，直到在 `/hooks` 中开启。

```text
Hooks need review
6 hooks are new or changed.
Hooks can run outside the sandbox after you trust them.

  1. Review hooks
> 2. Trust all and continue
  3. Continue without trusting (hooks won't run)
```

也可以在 `/hooks` 中检查 OpenViking 命令，信任并启用准备使用的条目；同时在 `/plugins` 中确认 `openviking-memory` 已启用。当前插件声明了 6 个 hook，新增或修改定义后需要重新审阅，规则见[官方 hook 信任说明](https://learn.chatgpt.com/docs/hooks#review-and-trust-hooks)。

Hook 关闭时，MCP 工具仍可能正常使用。自动召回需要 `UserPromptSubmit`，捕获需要 `Stop`，其余生命周期提交见下表。之前跳过设置的，可以回到 `/hooks` 启用相关条目。

<details>
<summary><b>手动安装</b></summary>

前置条件：需安装 Node.js >= 22、Codex >= 0.130.0，并启用 `plugin_hooks` 特性。

1. **配置连接** — 手写 `~/.openviking/ovcli.conf`（`url`、`api_key`，可选 `account`/`user`），或装完后运行插件自带向导 `node <插件目录>/scripts/setup.mjs`。

2. **从远程 marketplace 安装插件**：

   ```bash
   codex plugin marketplace add volcengine/OpenViking
   codex plugin add openviking-memory@openviking
   ```

   若你的 Codex 版本未默认启用 plugin hooks，在 `~/.codex/config.toml` 中加上 `[features]` → `plugin_hooks = true`。之后可用 `codex plugin marketplace upgrade openviking` 更新。

</details>

## 验证

启动 `codex` 后，当前会话首次提交 prompt 时触发的 `SessionStart` 会加载 profile，之后插件将在每次用户输入前自动召回相关记忆。若设置环境变量 `OPENVIKING_DEBUG=1`，则会将相关事件日志写入 `~/.openviking/logs/codex-hooks.log`。
TraeCode CLI 2.0 用户启动 `trae-cli`，并可用 `trae-cli plugin list` 确认插件已启用。

## 工作原理

插件按以下事件处理记忆：

| Codex 事件 | 插件行为 |
| --- | --- |
| `SessionStart`（`startup`、`clear`、`resume`） | 注入 `profile.md`、`preferences/` 和 `entities/` 的 URI 与摘要索引，以及 `<available-skills>` 清单。复用支持中日韩文本的公共 profile 构建逻辑；恢复会话时还可注入最新 archive digest。 |
| `UserPromptSubmit` | 搜索并注入与当前输入相关的记忆。 |
| `Stop` | 将新增对话追加到 OpenViking 会话。 |
| `PreCompact` | 补齐并提交压缩前的完整对话记录。 |
| `SessionEnd` | 正常退出时提交会话，触发后续记忆提取。 |
| `PreToolUse`（`Bash`） | 检查命令中的 `viking://` URI，并提示模型使用 OpenViking MCP 工具。命令仍会执行；URI 是 `ov` 参数等有意传入的数据时，可以忽略提示。 |

新会话启动时还会清理上次运行遗留、已超过闲置 TTL 的孤儿会话。

> **已知局限**：`SessionEnd` 需要 Codex 0.145 及以上版本，且只在正常退出时触发（`/quit`、`/exit`、连按两次 `Ctrl-C`、EOF、`codex exec` 运行结束）。`SIGTERM`、直接关闭终端、`kill -9` 或崩溃都不会触发；当 TUI 挂在 `codex app-server` 守护进程上时，该事件会被延后。这些会话——以及 Codex 低于 0.145 的所有会话（以及没有该事件的 TraeCode CLI 版本）——由下一次 `SessionStart` 的闲置 TTL（生存时间，默认为 30 分钟）清扫回收。

`<available-skills>` 清单先列你自己的 skill，再列 `viking://agent/skills` 下共享给整个账号的 skill；共享 skill 与你自己的某个 skill 同名时不再列出。清单有独立的 token 预算，不占 profile 预算：放不下描述时只列名称，连一个名称都放不下时缩成一行总数。清单第一行提示模型：按某个 skill 操作前，先用 OpenViking `read` 工具读取它的 `SKILL.md`。插件在 `openviking-memory`、`ov-experience-memory` 之外还自带 `openviking-skills` skill，告诉模型如何查找 skill、用 MCP `add_skill` 工具新建或替换 skill、从 Git 或本地文件夹安装 skill、把 skill 共享给整个账号，以及在你要求时把本地 skill 迁移到 OpenViking。

工具调用和结果会作为独立的 `tool` part 捕获，`tool_output` 原样上报。截断由服务端负责：超过 `tool_output_externalization.threshold_chars`（默认 `20000`）的输出会写入 session 的 tool-result 存储，part 中只保留 synopsis stub 和 `tool_output_ref`，原文仍可通过 [`/api/v1/sessions/{id}/tool-results`](../api/05-sessions.md#read-tool-result) 读回。

<details>
<summary><b>配置</b></summary>

凭据来源：默认环境变量优先——只要设置了任一 `OPENVIKING_*` 凭据环境变量（`OPENVIKING_URL`/`OPENVIKING_BASE_URL`、`OPENVIKING_BEARER_TOKEN`/`OPENVIKING_API_KEY`、`OPENVIKING_ACCOUNT`、`OPENVIKING_USER`、`OPENVIKING_PEER_ID`），其取值就会覆盖当前激活的 `ovcli.conf`。只有在这些环境变量都未设置时，才由激活的 `ovcli.conf`（`OPENVIKING_CLI_CONFIG_FILE` 或 `~/.openviking/ovcli.conf`）统一驱动 hook、MCP 代理和 Codex 内部运行的 `ov` 命令，此时 `ov config switch <name>` 会在下次启动时生效。若希望在设置了凭据环境变量的情况下仍强制使用 ovcli 配置，可设置 `OPENVIKING_CREDENTIAL_SOURCE=cli`。两者都未覆盖的字段依次回退到 `ovcli.conf`、`ov.conf` 和内置默认值。

| 环境变量 | 默认值 | 说明 |
|---------|--------|------|
| `OPENVIKING_URL` / `OPENVIKING_BASE_URL` | — | 完整的服务器 URL |
| `OPENVIKING_API_KEY` | — | API 密钥（将通过 `Authorization: Bearer` 标头发送） |
| `OPENVIKING_CLI_CONFIG_FILE` | `~/.openviking/ovcli.conf` | hook、MCP 和 Codex 内部 `ov` 命令共同使用的当前 CLI 配置 |
| `OPENVIKING_CREDENTIAL_SOURCE` | `auto` | `auto` 下已设置的凭据环境变量优先；设为 `cli` 强制使用激活的 ovcli 配置；设为 `env` 只读环境变量，两个配置文件都不读 |
| `OPENVIKING_NO_AUTO_INJECT` | `false` | 关闭会话启动阶段的固定 profile/背景注入（包括 skill 清单），但不关闭逐 prompt 语义召回 |
| `OPENVIKING_PROFILE_TOKEN_BUDGET` | `10000` | `profile.md` 及 `preferences/`、`entities/` 索引共用的 CJK-aware token 预算 |
| `OPENVIKING_SKILL_CATALOG` | `true` | 在会话启动注入中加入 `<available-skills>` 清单；设为 `false` 则不加 |
| `OPENVIKING_SKILL_CATALOG_TOKEN_BUDGET` | `1200` | `<available-skills>` 清单的 CJK-aware token 预算，独立于 `OPENVIKING_PROFILE_TOKEN_BUDGET`；设为 `0` 同样不加清单 |
| `OPENVIKING_SESSION_START_MAX_BYTES` | `9500` | SessionStart 注入的总字节上限，保证低于 Codex 默认的 hook 输出上限（约 10,000 字节），模型拿到的是全文而不是截断预览；resume 时会话归档最多占一半。设为 `0` 取消上限 |
| `OPENVIKING_CODEX_IDLE_TTL_MS` | `1800000` | `SessionStart` 闲置 TTL 清理阈值（毫秒） |
| `OPENVIKING_CODEX_LOCK_WAIT_MS` | `120000`（SessionEnd）、`40000`（PreCompact） | 捕获类 hook 等待单会话状态锁的时长（毫秒） |
| `OPENVIKING_CODEX_COMMITTED_TTL_MS` | `2592000000` | 已提交会话的转录游标保留时长（毫秒），过期后删除状态文件 |
| `OPENVIKING_RECALL_QUERY_FILTERS` | `""` | CSV 格式的 sed 风格正则规则，在 prompt 变成检索 query 前生效（[语法与示例](https://github.com/volcengine/OpenViking/blob/main/examples/codex-memory-plugin/README.md#input-filters)） |
| `OPENVIKING_CAPTURE_FILTERS` | `""` | CSV 格式的 sed 风格正则规则，作用于每个被捕获的回合（同一套语法） |
| `OPENVIKING_DEBUG` | `false` | 是否将日志写入 `~/.openviking/logs/codex-hooks.log` |

这些配置大多也可以写在 `ovcli.conf` 的 `plugin` 段下——见[插件配置](../configuration/02-client.md#插件配置)。两个过滤器建议使用 JSON 数组，避免环境变量中的逗号被当作分隔符。

如果更看重召回响应速度，请参阅[低延迟召回](./01-overview.md#低延迟召回)，其中说明了如何通过环境变量或 `ovcli.conf` 关闭查询扩展与 Codex 本地结果压缩。

更多调参说明（如 `OPENVIKING_RECALL_LIMIT`、`OPENVIKING_CAPTURE_ASSISTANT_TURNS` 等），请参考 [插件 README](https://github.com/volcengine/OpenViking/blob/main/examples/codex-memory-plugin/README.md#tuning-the-plugin)。

</details>

## 工作区 peer

记忆按你所在仓库派生出的 peer 归档，因此同一个项目在不同 clone、worktree 和子目录下共用同一份记忆。默认的 `peer.source: "git"` 取仓库归一化后的 `origin` URL——`origin` 为 `git@github.com:volcengine/OpenViking.git` 时，peer 就是 `github.com-volcengine-openviking`——其次是仓库根路径；不在仓库中则完全不发送 peer，在那里记下的内容进入用户级空间 `viking://user/<you>/memories`。fork 的 `origin` 不同，因此默认是独立的 peer。

可通过 `OPENVIKING_PEER_SOURCE`、`ovcli.conf` 中的 `plugin.peerSource`，或工作区 `.openviking/config.json`（`"version": 1` 的配置文件，可提交给团队共用）中的 `peer.source` 修改：`"cwd"` 恢复此前的行为——把工作目录路径中的非字母数字字符全部替换成 `-`；`"none"` 表示不发送 peer；也可以用 `"team-{dir}"` 这样的模板自定义。要[让一个不是仓库的目录拥有独立记忆](../configuration/02-client.md#让一个目录拥有独立记忆)，在该目录下创建 `.openviking/config.json`，内容为 `{"version": 1, "peer": {"id": "my-project"}}`。此前按工作目录派生的 peer 下写入的记忆仍能被召回，无需迁移。分层优先级和工作区配置文件的完整 schema 见[客户端配置 → 工作区配置](../configuration/02-client.md#工作区配置)。

## 故障排查

| 现象 | 可能原因 | 修复方法 |
|------|------|------|
| MCP 工具调用报认证错误 | 当前 ovcli 配置没有 authenticated server 所需的有效 `api_key` | 修正 `~/.openviking/ovcli.conf`（或运行 `node <插件目录>/scripts/setup.mjs`）后重启 Codex；stdio 代理会在启动时和认证失败后重新读取配置 |
| MCP 工具调用报连接错误 | 服务器不可达或 URL 配置错误 | 执行 `curl "$(jq -r '.url' ~/.openviking/ovcli.conf)/health"` 检查服务器状态 |
| 提示审阅 hook，或插件已装但没有自动召回/捕获 | 相关 hook 未信任、未启用，或插件被禁用 | 在 `/hooks` 审阅并启用相关条目，再到 `/plugins` 确认插件已启用。 |
| `ov config switch` 后插件仍指向旧服务器 | 原代理仍保留连接，或凭据环境变量覆盖了选中的配置 | 重启 Codex，并检查凭据环境变量与 `OPENVIKING_CLI_CONFIG_FILE`。远程调用会在认证失败后检查文件变化，不会在每次成功请求时重载 |
| Hook 与 MCP 指向不同服务器 | 某一侧残留了过期的 `OPENVIKING_*` 凭据环境变量（默认环境变量优先于 ovcli.conf） | 清除过期环境变量（让 ovcli.conf 同时驱动两者）、设置 `OPENVIKING_CREDENTIAL_SOURCE=cli`，或保证环境变量一致 |

## 参见

- [集成能力参考](./16-capability-reference.md)
- [博客：在 Claude Code / Codex 中接入 OpenViking](https://blog.openviking.ai/post/openviking-coding-agent/) — 为什么以及如何给你的 Coding Agent 加上长期记忆
- [插件 README](https://github.com/volcengine/OpenViking/blob/main/examples/codex-memory-plugin/README.md) — 完整的环境变量说明与架构图
- [DESIGN.md](https://github.com/volcengine/OpenViking/blob/main/examples/codex-memory-plugin/DESIGN.md) — 提交（commit）决策树
- [MCP 客户端](./06-mcp-clients.md) — MCP 协议、工具列表及其他客户端
- [部署指南 → CLI](../guides/03-deployment.md#cli) — `ovcli.conf` 配置说明

### 召回压缩

设置 `OPENVIKING_RECALL_COMPRESS=server` 可让 OpenViking 服务端压缩召回内容，Codex 不启动本地压缩进程。`client` 仅用本地压缩，`auto`（默认）在本地压缩器不可用时走服务端，`off` 关闭压缩。服务端已有摘要时直接使用；明确返回无相关记忆时不注入。

Codex 通过共享 `buildRecallBlockDetailed()` 执行召回、排序、预算和旧服务端回退，仅保留会话映射、模型调用与 hook 输出适配。本地压缩失败保留预算内的检索结果；原始检索回退在不使用本地压缩时遵循 `recallPreferAbstract`，不再固定读取所有叶子全文。预算包含正文、URI 和包装文本。配置详见 [共享插件说明](https://github.com/volcengine/OpenViking/blob/main/examples/memory-plugin-shared/README.md#cloud-recall-compression)。
