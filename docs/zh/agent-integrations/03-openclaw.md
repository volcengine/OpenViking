# OpenClaw 插件

为 [OpenClaw](https://github.com/openclaw/openclaw) 添加长效记忆。安装完成后，OpenClaw 会自动记住对话中的重要信息，并在每次回复前召回相关上下文。

源码：[examples/openclaw-plugin](https://github.com/volcengine/OpenViking/tree/main/examples/openclaw-plugin)

## 前置条件

| 组件 | 版本要求 |
| --- | --- |
| Node.js | >= 22 |
| OpenClaw | >= 2026.5.27 |

插件需要连接到一个正在运行的 OpenViking 服务——参见 [部署指南](../guides/03-deployment.md)。

<details>
<summary><b>从旧版 <code>memory-openviking</code> 升级？</b></summary>

旧插件不兼容，请先清理：

```bash
curl -fsSL https://raw.githubusercontent.com/volcengine/OpenViking/main/examples/openclaw-plugin/upgrade_scripts/cleanup-memory-openviking.sh -o cleanup-memory-openviking.sh
bash cleanup-memory-openviking.sh
```

</details>

## 安装

```bash
openclaw plugins install clawhub:@openviking/openclaw-plugin
openclaw openviking setup --base-url http://your-server:1933 --api-key sk-xxx --json
openclaw gateway restart
```

`setup` 向导写入配置并激活插件。安装完成后开始对话——OpenClaw 会自动记忆和召回。

<details>
<summary><b>备用方案：通过 <code>ov-install</code> 安装</b></summary>

当 ClawHub 不可用时：

```bash
npm install -g openclaw-openviking-setup-helper
ov-install --base-url http://your-server:1933
```

常用参数：

| 参数 | 含义 |
| --- | --- |
| `--workdir PATH` | OpenClaw 数据目录（默认 `~/.openclaw`） |
| `--plugin-version=VER` | 插件版本：npm 版本、dist-tag 或 Git ref |
| `--base-url URL` | OpenViking 服务地址 |
| `--api-key KEY` | OpenViking API Key |
| `--peer-role ROLE` | 记忆归属：`none`、`assistant` 或 `sender` |
| `--uninstall` | 卸载插件 |

完整参数列表见 [安装指南](https://github.com/volcengine/OpenViking/blob/main/examples/openclaw-plugin/INSTALL.md)。

</details>

## 选择记忆归属

`peer_role` 决定长期记忆是在 OpenViking user 层共享，还是归属到具体 peer：

| 值 | 记忆路径 | 适用场景 |
| --- | --- | --- |
| `none`（默认） | 共享记忆位于 `viking://user/<user_id>/memories/...`；不使用具体 peer 的记忆子树 | 通用场景：该 OpenViking 用户下的所有对话共享 user-level 记忆 |
| `assistant` | assistant 归因的 peer 记忆位于 `viking://user/<user_id>/peers/<assistant_id>/memories/...` | **人是 OpenViking user**：让 `main`、`research` 等不同助手的 peer 记忆分开 |
| `sender` | sender 归因的 peer 记忆位于 `viking://user/<user_id>/peers/<sender_id>/memories/...` | **Agent 是 OpenViking user**：让 `customer-42`、`customer-99` 等不同发送者的 peer 记忆分开 |

例如：

```bash
# Alice 是 OpenViking user；按 OpenClaw 助手分开 peer 记忆。
openclaw openviking setup --base-url http://your-server:1933 --api-key sk-xxx --peer-role assistant --json

# support-agent 是 OpenViking user；按给它发消息的人分开 peer 记忆。
openclaw openviking setup --base-url http://your-server:1933 --api-key sk-xxx --peer-role sender --json
```

安装和 setup 只接受 `sender`；已有的 `peer_role=person` 配置仍兼容，并按 `sender` 处理。OpenViking 会为每个用户初始化受管的 `peers/` 容器，因此 `none` 的含义是不使用具体的 `peers/<peer_id>/memories` 子树。Actor-peer 召回同时包含用户共享记忆和当前 peer 记忆；切换 scope 不会搬迁已有记忆。

## assemble 如何组装上下文

插件占用 OpenClaw 的 `contextEngine` 槽位。会话历史、长期记忆召回和本轮新输入分别处理；`assemble()` 返回供本次模型请求使用的上下文，不把组装出的摘要或召回内容持久化到 OpenClaw 的 session transcript，也不通过该调用向 OV session 追加消息。宿主可以用返回的 messages 更新本轮内存状态，这与写入持久化对话记录不同。

主 assemble 在每轮新输入开始执行时准备历史上下文。插件通过参数中是否包含 `prompt`、`availableTools`、`citationsMode` 中任一字段识别该调用。

### 主 assemble：历史和当前输入分开

主分支调用 `getSessionContext(tokenBudget)`，用返回的内容构造：

```text
summaryMessage = { role: "user", content: "[Session History Summary]\n" + latest_archive_overview }
messages = [summaryMessage] + OV active messages
systemPromptAddition = Session Context Guide（有归档时）+ 本轮召回结果（有命中时）
```

`latest_archive_overview` 是服务端返回的摘要正文，`[Session History Summary]` 是插件加在正文前的固定文本标题。仅在 overview 非空时插入这条合成 user 消息；active messages 保留近期未压缩对话。当前 `prompt` 由宿主加入本轮；插件只用它查询记忆，不把它重复追加到返回的历史中。召回结果属于本次请求的上下文，不直接作为新对话写回 OV。

overview 由 OV 服务端的工作记忆流程生成，插件读取结果。服务端先为 active messages 分配预算，剩余空间不足时不返回 overview；`pre_archive_abstracts` 当前为空数组。因此返回结果不是完整归档索引，需要原始细节时通过 `ov_archive_search` 查询归档。

插件为模型输出预留 token 空间，扣除使用指南和摘要的实际估算量，再从 active messages 头部裁掉超预算内容，并整理工具调用/结果等 provider 消息格式。摘要不会按插件计算出的 archive 预算硬截断，因此这些预算不能当作各层的严格配额；新增召回块若使总估算量超过 `tokenBudget`，该块会被省略。

OV 无数据、无归档且消息数少于宿主输入、转换后为空或读取失败时，历史分支回退到宿主 messages。即使历史透传，只要有合法 `prompt` 且启用了 `autoRecall`，主分支仍可尝试召回。召回无命中或失败不会阻止对话。

### transformContext

`transformContext` 在每次 LLM 调用前执行，无论最后一条消息是 user message 还是 tool response。该 hook 在 OV 集成中的最佳使用方式暂未明确。

### 捕获和压缩

- `ingest()` / `ingestBatch()` 不写入消息。常规捕获通过 `afterTurn`；插件对 OpenClaw 2026.9.3 及之后的稳定版本支持由 `commitTurn` 接收宿主交付的已结束轮次，旧版和独立 runner 使用 `afterTurn`。无法识别版本时，`commitTurn` 拒绝确认，避免确认未捕获的数据。
- 捕获逻辑清洗注入内容、转换文本和工具消息，再写入 OV session。达到 `pending_tokens >= tokenBudget × commitTokenThresholdRatio` 时，发起异步 session commit；默认比例为 `0.5`，默认保留最近 `10` 条消息，也可选 `turn_budget` 保留策略。`pending_tokens` 是服务端按保留策略计算的待归档消息 token 数，不是整个模型请求的 token 数。
- `ownsCompaction: true` 表示插件负责压缩。正常 `compact()` 提交 OV session（`wait=true`、保留数为 `0`），读取 overview 作为压缩摘要；下一次主 assemble 用摘要和 active messages 重建历史。被 bypass 的会话尝试委托宿主压缩器，宿主 bridge 不可用时返回跳过。

这里的 **session commit** 负责会话归档和记忆处理，与保存资源文件版本的 [snapshot commit](../guides/15-snapshot.md) 是不同操作。

## 验证

```bash
openclaw openviking status
```

一键检查插件注册、服务端连通性和版本兼容性。追加 `--json` 获取机器可读结果。

<details>
<summary><b>手动验证</b></summary>

确认插件占用了 `contextEngine` 槽位：

```bash
openclaw config get plugins.slots.contextEngine
# 期望输出：openviking
```

全链路健康检查：

```bash
python examples/openclaw-plugin/health_check_tools/ov-healthcheck.py
```

详见 [HEALTHCHECK.md](https://github.com/volcengine/OpenViking/blob/main/examples/openclaw-plugin/health_check_tools/HEALTHCHECK.md)。

</details>

<details>
<summary><b>配置</b></summary>

插件配置位于 `plugins.entries.openviking.config`，通常 setup 已经写好。

| 参数 | 默认值 | 含义 |
| --- | --- | --- |
| `baseUrl` | `http://127.0.0.1:1933` | OpenViking 服务端点 |
| `apiKey` | 空 | OpenViking API Key |
| `peer_role` | `none` | `none`、`assistant` 或 `sender`；旧值 `person` 作为 `sender` 的别名兼容 |
| `peer_prefix` | 空 | `peer_role=assistant` 时 assistant peer 身份的可选前缀 |
| `autoRecallTimeoutMs` | `5000` | 整个 auto-recall 流程的外层超时（毫秒）；本地嵌入硬件较慢时可调大（取值范围 1000–300000） |

```bash
openclaw config set plugins.entries.openviking.config.baseUrl http://your-server:1933
openclaw config set plugins.entries.openviking.config.apiKey your-api-key
```

</details>

## 卸载

```bash
curl -fsSL https://raw.githubusercontent.com/volcengine/OpenViking/main/examples/openclaw-plugin/upgrade_scripts/uninstall-openclaw-plugin.sh -o uninstall-openviking.sh
bash uninstall-openviking.sh
```

## 参见

- [集成能力参考](./16-capability-reference.md)
- [完整安装指南](https://github.com/volcengine/OpenViking/blob/main/examples/openclaw-plugin/INSTALL.md) — 所有安装路径与参数
- [插件设计说明](https://github.com/volcengine/OpenViking/blob/main/examples/openclaw-plugin/README.md) — 架构、身份与路由、hook 生命周期
- [Agent 操作指南](https://github.com/volcengine/OpenViking/blob/main/examples/openclaw-plugin/INSTALL-AGENT.md) — 给代用户执行安装的 agent 看
