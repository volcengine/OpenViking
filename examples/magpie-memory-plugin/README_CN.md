# OpenViking for Magpie

为经过 Magpie 网关的模型请求自动召回 OpenViking 记忆，直接读取现有
`ovcli.conf`，无需在 Magpie 中重复填写服务地址和凭据。

市场展示名称为 **OpenViking**，npm 分发包名为 `@openviking/magpie-plugin`；两者独立配置。

## 安装

首次 npm 发布完成后，在 Magpie 中安装 OpenViking：

```sh
magpie plugin add @openviking/magpie-plugin
```

按 Magpie 当前的工具命名规则，通过 npm 安装时，工具名为
`magpie-plugin_status`、`magpie-plugin_find`、`magpie-plugin_read`、`magpie-plugin_ls` 和 `magpie-plugin_remember`。
本地目录安装的工具前缀使用目录名。

从源码安装时，在 OpenViking 仓库根目录执行：

```sh
node examples/memory-plugin-shared/sync.mjs
magpie plugin add ./examples/magpie-memory-plugin
```

本地目录安装需要先生成共享模块。`npm pack` 会自动生成并打包这些模块。
按 npm 包名安装需要先完成首次发布；直接将整个 OpenViking 仓库作为插件安装不会选中此子目录。

需要支持异步网关 Hook 和宿主 `origin` 分类的 Magpie，以及支持
`POST /api/v1/search/search` 的 `mode: "context"` 的 OV 服务。
`magpie-key` 分类需要 Magpie v0.1.2208 或更新版本，旧版本的未知来源会跳过召回。

## 配置

默认只读 `~/.openviking/ovcli.conf`。可在 **Magpie 进程的环境变量** 中通过
`OPENVIKING_CLI_CONFIG_FILE` 指定其他路径。远程运行时读取远程机器上的文件。
修改文件后下一次操作生效；修改环境变量需要重启 Magpie。

在现有配置的 `plugin` 下增加：

```json
{
  "magpie": {
    "enabled": true,
    "autoRecall": true,
    "autoCapture": false,
    "recallContextTimeoutMs": 700,
    "recallMaxTokens": 1600,
    "recallPeerScope": "all",
    "mcpEnabled": false
  }
}
```

行为配置沿用共享插件字段名，`plugin.magpie` 覆盖 `plugin` 的同名设置；
全局 `plugin.enabled: false` 始终关闭本插件。连接读取顶层的 `url`、`api_key`、
`account`、`user`、`actor_peer_id`、`timeout`（秒）、`extra_headers`（或 CLI 别名 `extra_header`），支持身份字段的已有别名。
账户、用户请求头沿用共享客户端的身份处理方式，需要与服务端认证模式匹配。
自定义请求头不能覆盖鉴权、身份或传输请求头，包括 `X-API-Key`。
已有 LDAP 配置按 `auth_mode`、`ldap_username`（或 `user`）、`ldap_password` 构造 Basic 鉴权；
OIDC 配置使用 `oidc_token`（或 `api_key`）作为 Bearer Token。两者由服务端解析身份，不断言账户或用户。
配置类型、别名、范围和召回请求体复用已有共享模块；文件读取策略、宿主超时、工具默认关闭等适配放在 Magpie 自身目录，不改变其他插件。
Magpie 的文件读取策略排除环境变量和 workspace 层。
工具和采集分别需要 `plugin.magpie.mcpEnabled: true`、`plugin.magpie.autoCapture: true` 显式开启，共享设置不会直接开启这两项能力。

Magpie 的“选项”支持 `autoRecall`、`autoCapture`、`mcpEnabled`、`recallMaxTokens`、
`recallContextTimeoutMs`、`nativeAgents`，优先于本插件的文件行为配置；Hook 和工具使用同一套设置。
服务连接、凭据和身份仍只从文件读取。`gatewayTimeoutMs` 控制宿主 Hook 预算。
包内示例不会自动保存；未填写的字段继续沿用文件设置或默认值。开启工具或采集必须填写布尔值 `true`。

不会修改文件、合并环境变量中的凭据，或回退读取 `ov.conf`、Root Key、Gateway Token。
配置缺失、损坏或服务不可用时，召回跳过，模型请求继续；不会沿用旧配置。

在文件中设置 `plugin.magpie.bypassSession: true`，或通过 `bypassSessionPatterns`
匹配 Magpie 会话 ID、工作目录，可跳过该会话的自动召回和采集。
已有待同步快照会暂停投递，取消 bypass 后可继续恢复；worker 在追加和 commit 前重新检查，
无法撤销已在途的操作。显式记忆工具仍由 `mcpEnabled` 单独控制。

## 自动召回

- 首轮用户消息同时读取当前认证用户的 `viking://~/memories/profile.md`，避免职业、邮箱等稳定资料依赖语义检索排名。
  资料读取与检索在召回超时预算内并行执行；任一路失败时仍可使用另一路结果。
  资料遵守 `profileTokenBudget`，且最多占 `recallMaxTokens` 的一半，使用共享的中日韩字符 token 估算器；
  资料与检索结果合计不超过 `recallMaxTokens`，与首轮决策一起持久化；重试和后续轮次回放原始快照。
- 仅处理宿主确认为本机的 `local`、`magpie-key` 请求，并要求 Magpie 提供非空会话 ID。
  共享网关 Key、转发请求或身份缺失时跳过，始终使用文件中配置的 OV 身份。
- 只有新的文本用户消息触发检索。工具续轮只回放已有记忆；重试复用首次决策，
  包括无结果和失败的决策。同样的问题出现在后续新轮次时仍可重新检索。
- 记忆附加到对应的**用户消息**，不修改系统指令。先持久化每条消息前缀对应的注入决策，
  后续请求在原位置恢复同样的内容，保持旧上下文稳定。
- 保留 Chat Completions、Anthropic Messages、Gemini 的系统指令、图片、工具、模型和流式设置。
  Responses 必须使用 `store: false` 并回传完整 `input`；服务商保存历史、后台请求和
  `item_reference` 均跳过。
- 状态按 OV 连接身份、peer 范围、配置文件位置，以及 Magpie Agent、会话、workspace、
  协议和模型隔离。凭据轮换不复用旧状态。分支和历史匹配使用完整前缀哈希，
  不根据相同文本猜测归属。未知的截断或压缩历史跳过增强；单条新用户消息可建立新前缀。
- 根据已知 OV 上下文块或显式 `nativeAgents` 配置避让自动增强；这不是插件安装检测，不能保证所有场景零重复。
  Magpie 自身跳过内部标题/分类请求；插件也跳过明确标记的辅助、压缩、标题和子 Agent 请求。
- `autoRecall: false` 关闭新检索，但仍回放已知历史；插件整体关闭则停止两者。
  查询过滤、召回参数、预算和结果相关性使用共享模块。
- 默认 700ms 检索预算。提高超时时也应提高 Magpie 的 `gatewayTimeoutMs`，为状态读写留余量。
  请求取消会中止检索。Actor 范围需要明确填写 `actor_peer_id`，缺失时跳过。

### 本机回放状态

默认保存在 `~/.openviking/magpie/recall/`，可用 `plugin.magpie.dataDir` 修改父目录。
文件仅保存消息锚点哈希、注入的记忆原文及空结果/失败状态，不保存原始凭据或客户端完整对话。
记忆原文可能含敏感内容；目录权限为 0700，文件为 0600。通过会话锁和原子替换支持多个宿主进程，
宿主意外退出留下的锁在一分钟后可回收。

本版本不自动删除回放文件。活跃会话应保留状态；删除、损坏或无法匹配历史时，已有对话跳过增强，
新建对话后可重新建立状态。带签名推理的恢复依赖完整历史及其回放状态，尚未验证真实模型服务的签名行为。
单会话状态上限 1 MiB，请求 JSON 上限 2 MiB、最多 2,000 条消息；超限或状态不可用时放行原请求。
诊断只记录固定状态和字节数，不记录凭据或记忆正文。

插件直接连接 OV 的普通 REST/MCP 服务，**不要求 OV 开启网关**。
自动召回本身不创建 OV Session。

Anthropic 协议内容块的缓存注解不参与召回锚点计算；原始请求字段和工具输入中的业务数据保持不变。

## 自动采集与原生插件避让

采集默认关闭。在 `ovcli.conf` 中设置 `plugin.magpie.autoCapture: true`，或在 Magpie 的“选项”中保存
`"autoCapture": true` 后启用。完整对话先写入本地队列，再由独立后台任务增量写入按连接、Agent、
会话、workspace、协议隔离的 OV Session；不保存系统指令、注入记忆、宿主提醒和私有推理。
启用后应新建会话：首轮会绑定当前连接和第一条用户消息，且该用户消息之前不能存在可采集的助手或工具内容。未知的已有历史，包括切换凭据后的旧对话，
会跳过采集，避免把之前的私有内容复制到新身份。
工具调用和结果成对保存；未完成的工具续轮、失败和截断的回复不作为完整轮次入库。
若完成回调丢失，下一条用户请求携带的完整历史可以补齐此前已经结束的对话。
已知失败或截断的轮次会以摘要标记持久化，后续历史不能将其重新补采；成功重试的完成回调可以解除拒绝状态。
工具续轮不作为失败处理。已知标题、记忆压缩交换在补采、最终快照和召回历史锚点中统一排除，原始消息保持不变。
拒绝状态的修订号会使已被 worker 读取的旧快照失效，旧 worker 也不会删除随后发布的成功重试快照。

完整轮次写入后，由后台任务调用普通 OV Session 的 `commit` 接口，`keep_recent_count` 为零。
关闭该 Session 自身的自动提交，避免两个提交方竞争，也不依赖服务端空闲调度器或 OV 网关。
重复回调不会再次 commit；返回结果代表归档并提交了提取任务，不代表已经生成新记忆。
若 commit 响应丢失，先核对会话元数据，再继续追加；无法确认时暂停队列，不重复请求提取。

自动召回或采集前，Magpie 仅将系统、用户文本中独立成行、完整闭合且内容非空的
`<openviking-context>`、`<relevant-memory>` 或 `<relevant-memories>` 块作为避让信号。
Markdown 围栏代码、引用块、缩进代码、行内提及、未闭合标签，以及助手、工具和非文本字段均不参与判定。
旧版 `[OpenViking memory context]` 单独出现时不再自动触发避让。

这只是消息内容启发式判断，**不能检测插件安装，也不能证明原生采集正在运行**。
未加引用、格式与真实注入完全相同的示例仍无法可靠区分；已知由原生插件管理的 Agent 应显式配置 `nativeAgents`。
命中后，该连接和会话同时跳过自动召回与采集，并立即清理待同步原文，无需等待 worker；判定持久化，避免标记从后续历史消失后再次启用。
worker 在追加消息或 commit 前重新读取配置，检查连接身份、采集开关和归属；本地清理无法撤销已经在途的 HTTP 操作或已送达的记录。
已有归属记录会保留；移除原生插件或纠正历史误判后应新建会话。显式工具不受自动能力让出的影响。

已知的原生 OpenViking 记忆压缩和 Claude Code 会话标题生成内部请求，即使没有注入标记，也会跳过自动召回和采集。这是基于已知提示词结构的兜底，无法完整识别所有辅助请求。对于已安装原生 OV 插件的 Agent，应配置 `nativeAgents`，一并让出未携带标记的内部请求。

远程网关看不到客户端安装列表；仅采集、不注入标记的原生插件也无法靠消息识别。
这类 Agent 可在选项中设置 `"nativeAgents": ["pi", "codex", "claude"]`，或填写
`plugin.magpie.nativeAgents`，明确让出两项自动能力。名称应与 Magpie 上报的 Agent ID 一致。
不会根据无关机器上的文件名推断插件正在运行。

采集状态在 `<dataDir>/capture/`，目录权限 0700、文件 0600。**待同步文件含对话和工具输出**，
不含连接凭据。单会话最多 32 个快照、单快照 3 MiB、最多 2,000 条消息。
同步成功后删除快照，保留进度摘要和归属判定。后台任务有重试预算，后续开启采集的请求也会触发恢复。
关闭文件中的采集设置会暂停相应队列；更换身份不会将旧队列发送给新用户。

通过稳定前缀 ID、持久化进度和进程锁防止重试或并发重复写入。OV 的 `source_message_ids` 不等于幂等键：
写入响应丢失时核对当前消息和最多 32 份归档中的来源 ID；无法确认时保留队列，不盲目重发。
已同步历史被改写时暂停该会话，避免把旧对话重复复制；可新建会话继续。
这些保守处理可能延迟或跳过采集。宿主在完成回调前崩溃、且再无完整历史请求时，无法仅靠尽力通知恢复尾消息。

## 可选工具

工具默认关闭。设置 `plugin.magpie.mcpEnabled: true`，或在 Magpie 选项中保存 `mcpEnabled: true`，然后在 Magpie Library/MCP 路由
中为需要的 Agent 开启插件服务。也可通过 Magpie 的 `/mcp/magpie/tools` 访问。

提供 `status`、`find`、`read`、`ls`、`remember`，实际名称由 Magpie 加插件前缀。
`status` 检查鉴权后的连接；`read` 最多读取 200 行，`ls` 最多返回 100 项。

**工具回调不提供调用者的认证身份。开启工具意味着将当前文件的 OV 身份授权给能调用
该 MCP 服务的调用者。** 共享网关应保持关闭，除非允许的工具调用者都应拥有该访问权限。
工具参数不能更换账户或凭据，资源权限仍由 OV 服务端控制。关闭后工具仍会列出，但拒绝调用，
`status` 返回 `disabled`。

`remember` 仅在用户明确要求记住内容时提交文本，创建 Session 并触发异步提取。
配置了 `actor_peer_id` 时，也将其写入消息的 `peer_id`，保留项目记忆的归属。
返回任务 ID 不代表记忆已生成。写入不自动重试；中断可能留下未完成的 Session，
结果不明确时应先检查 OV 再决定是否重试。

工具错误保留脱敏的 HTTP 状态、错误类别和追踪 ID，区分不存在、冲突、限流、超时和取消。
自动召回仍在错误时放行原请求，诊断仅记录固定状态。

## 市场信息

[marketplace.json](marketplace.json) 提供待提交的 **OpenViking** 市场条目，包含官方图标和中英文描述。
`kind: "middleware"` 沿用网关记忆插件的市场分类；插件实际运行入口仍是异步 `magpie.hooks`。
`package.json` 的 `magpie.icon` 使用同一图标，供 npm 搜索结果和已安装列表展示。

npm 发布不会自动加入推荐列表。发布后，需要将 `plugins` 中的条目提交到
[Magpie 社区注册表](https://github.com/magpie-community/plugins/blob/main/registry.json)。
条目不声明由 Magpie 社区维护。

开发、测试与完整接口说明见 [English README](README.md)。
