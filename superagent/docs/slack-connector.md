# Slack Connector 技术方案（调研稿）

> 状态：第一期已在 `feat/slack-connector` 分支实现（代码在 `packages/daemon/src/slack/`、`packages/cli/src/slack.mjs`，协议约定见 `docs/connector-protocol.md` 的 Slack provider contract）。本文保留调研过程和取舍；和实现不一致的地方以代码和协议文档为准。
> 标「待真机验证」的结论来自官方文档或社区经验，需要用真实 workspace 确认后再定稿。

## Slack 概念速览（对照飞书）

| Slack | 飞书里对应的东西 | 说明 |
| --- | --- | --- |
| Workspace | 一个企业/租户 | 消息、成员、频道都属于某个 workspace，`team_id` 是 `T…` |
| App | 开发者后台的一个应用 | 带一个 bot 用户（`U…`/`B…`）；权限由 scope 决定 |
| 内部 App（internal / customer-built） | 企业自建应用 | 只装在创建它的那个 workspace |
| 分发 App（distributed，未上架） | 自己发链接让别的企业安装的应用 | 打开 public distribution 后，别的 workspace 可经 OAuth 安装 |
| Marketplace App | 应用商店应用 | 经 Slack 审核上架；Socket Mode App 不能上架 |
| Bot token `xoxb-` | `tenant_access_token` | 以 bot 身份调 Web API（发消息、读频道） |
| App-level token `xapp-` | 长连接用的应用凭证 | 只用来开 Socket Mode 连接 |
| Public / private channel | 群 | bot 要被邀请进频道才收得到消息 |
| DM（`im`）/ 多人私聊（`mpim`） | 单聊 / 无名小群 | 频道 id 分别以 `D…`、`G…`/`C…` 开头 |
| Thread（`thread_ts`） | 话题 / 回复串 | Slack 里线程用得比飞书普遍得多 |
| Slack Connect 频道 | 外部群 | 和别的公司共享的频道，对方成员也在里面 |
| Guest | 外部访客 | 只能看到被拉进去的少数频道 |

## 1. 结论

1. **接入方式用 Socket Mode**（WebSocket 长连接，等价于飞书长连接）。它不需要公网回调地址，适合 Mac/Linux 服务器 本机常驻。Node 24 自带全局 `WebSocket`，可以不加依赖。
2. **freshness fence 不用为 Slack 单独实现。** 它已经是 Core 的通用机制（`ActionService.fence/assertFresh`），按 `scope_key` 判断：聊天事件的 scope 是 `[instance, 'conversation', tenant_id, conversation.id, thread_id]`。Slack Connector 只要把 `conversation`（含 `thread_id`）填对，就自动获得和飞书群一样的保护。
   Slack 真正要做的是**别让噪声事件进入 scope**：机器人自己消息的回显、编辑、链接预览（unfurl）、入群退群等。这些一旦作为 `DONE` 事件落库，会让同一 scope 里正在进行的决定变成 `stale_decision`。最典型的情况是：一个 decision 允许连发最多 3 条短消息，第 1 条的回显会挡住第 2 条（详见 §3）。
3. 每个用户在**自己的 workspace 建一个内部 App**，和飞书「每个实例一个自建应用」一样。内部 App 不受 2025-05 起对非 Marketplace 分发应用的 `conversations.history/replies` 限流（1 次/分钟、每次 15 条）影响。
4. Slack 没法像飞书 `registerApp` 那样扫码建应用。setup 的做法是：用预填 manifest 的链接建 App，然后粘贴两个 token（`xoxb-` Bot token + `xapp-` App-level token），最后仿照 Telegram 发随机口令绑定 owner。
5. **分期**：第一期做文本、文件、线程、reaction、撤回、媒体下载和 owner 通知，审批用编号文字（`发送 N`）。第二期做读历史、搜索、Block Kit 按钮（`choices`/审批卡片）和 `/vika` 斜杠命令。

## 2. 接入方式选型

| 方案 | 结论 | 原因 |
| --- | --- | --- |
| **Socket Mode** | ✅ 选它 | `apps.connections.open`（App-level token，`connections:write`）拿到 `wss://` 地址，事件、斜杠命令、交互回调都从同一条连接进来；不需要公网 URL |
| Events API（HTTP Request URL） | ❌ | 需要公网 HTTPS 回调，本机 daemon 做不到 |
| RTM API | ❌ | 只适用于 classic app，新 App 不能用 |

Socket Mode 的要点（官方文档）：

- 连上后先收到 `hello`，其中有 `num_connections` 和 `debug_info.approximate_connection_time`。
- 每个 envelope 是 `{envelope_id, type, payload, accepts_response_payload}`，`type` 有 `events_api` / `slash_commands` / `interactive` 三种。每条都要回 `{envelope_id}` ack，否则 Slack 会重投。ack 时限按 Events API 的 3 秒处理。
- 连接会被定期刷新（文档说「每几个小时」），刷新前约 10 秒会收到 `disconnect`（`warning` / `refresh_requested`）。关闭 Socket Mode 时收到 `link_disabled`。
- **一个 App 最多 10 条并发连接，每条 payload 随机投到其中一条。** 这一点和 Telegram 的 409 冲突不同：两个接收者同时连着不会报错，而是各自悄悄丢掉大约一半的消息（见 §5.3）。
- Socket Mode App 不能上架 Marketplace。我们不需要上架，没有影响。

## 3. freshness fence 与 Slack 的关系（重点）

现状（`packages/daemon/src/event.mjs`、`actions/service.mjs`、`store.mjs`）：

- `conversationKeyFor(instance, conversation)` 等于 `[instance, 'conversation', tenant_id, id, thread_id]`，非聊天事件则是 `[instance, subject.type, subject.id]`。
- `newerAcceptedSeq(scope_key, seq)` 只统计 `status='DONE'` 的事件。被规则忽略（`RULE_IGNORED`）的事件和 slash 命令不计入。
- 这一套对飞书、微信、Telegram、邮箱完全通用，Slack 照样适用。

Slack 需要在 **Connector 层**处理的事情：

| 问题 | 不处理的后果 | 做法 |
| --- | --- | --- |
| 机器人自己的消息会经 `message.*` 订阅回推（Bolt 默认开启 `ignoreSelf` 中间件就是为此；待真机验证） | 每条 bot 回复都会在本 scope 生成一条更新的事件：连发 burst 的第 2 条起被挡成 `stale_decision`，模型还会被自己的消息唤醒 | `check` 阶段用 `auth.test` 拿到 bot 的 `user_id`/`bot_id`，凡 `user`==bot user 或 `bot_id`==自己的消息直接丢弃 |
| `message_changed`（含 unfurl 给消息补预览）、`message_deleted`、`message_replied`、`channel_join/leave/topic/...` 等 subtype | 同上，不是新输入却会让决定过期 | 白名单：只认无 subtype、`thread_broadcast`、`file_share`、`me_message`、`bot_message`（其他 App 的消息）；其余直接丢弃 |
| 线程 | 如果把线程回复都算进频道 scope，忙的频道会让线程里的回复反复过期 | 线程回复（`thread_ts && thread_ts !== ts`）设置 `conversation.thread_id = thread_ts`，按线程单独成 scope；频道顶层消息 `thread_id=null`。这和飞书话题走的是同一套 `conversationKeyFor`。区别是 Slack 的根消息发出时还不在线程里，所以和飞书**普通群里的回复串**一样（根消息算频道 scope），而不像**话题群**（根消息自带 `thread_id`） |
| 同一条消息被重复投递（ack 超时重投、刷新连接时多条连接重叠、停机补拉） | 重复唤醒模型 | 事件 id 用消息身份 `<team>:<channel>:<ts>`，而不是 Slack 的 `event_id`，让 Core 的 `(instance, id)` 去重同时覆盖这三种情况（补拉来的历史消息没有 `event_id`） |

**一个可选的 Core 优化（先不做，看数据再说）**：在 Slack 频道里，「对顶层消息开线程回复」非常常见。现在决定所在的 scope 是整个频道，所以模型调查期间频道里任何一条无关的新消息都会挡住这条线程回复。模型会看到新消息、换 decision 重发，不会出错，只是多一个模型步。飞书群也是这个语义。其他 App 的消息也会送给模型（§9），所以有告警机器人刷屏的频道里，对顶层消息的决定会更容易过期。如果上线后 `delivery.blocked`/`stale_decision` 在 Slack 上明显偏多，可以考虑：当 `reply_in_thread` 的目标是频道顶层消息时，fence 改按「根消息 + 它的线程」判断。这个改动会同时影响飞书，需要单独评审。

## 4. 事件映射（Slack → canonical envelope）

只订阅 4 个 bot event：`message.channels`、`message.groups`、`message.im`、`message.mpim`。**不订阅 `app_mention`**：同一条消息会再来一个 `event_id` 不同的事件，造成重复。@ 了谁从文本里的 `<@U…>` 解析。

| canonical 字段 | Slack 来源 |
| --- | --- |
| `id` | `<team_id>:<channel>:<ts>`（理由见 §3） |
| `kind` | `message.created` |
| `occurred_at` | `ts` 的秒数 ×1000 |
| `subject` | `{type:'conversation', id: channel}` |
| `actor` | `{id: user, type: 'user'}`；其他 App 的 `bot_message` 用 `{id: bot_id, type: 'bot'}` |
| `conversation` | `id=channel`；`type`：`im`→`p2p`，`mpim`/`channel`/`group`→`group`（这样 Core 的群聊 burst、slash 资格判断直接可用）；`tenant_id=team_id`；`thread_id` 见 §3 |
| `message.id` | `sl:<team_id>:<channel>:<ts>`（符合工具 ID 正则 `^[A-Za-z0-9_.:@=+-]{1,256}$`）。Slack 的消息 ts 只在频道内唯一，所以 id 必须带 channel |
| `message.parent_id` / `thread_root_id` | 线程回复都填根消息的 id。Slack 没有引用回复，`resolveReplyTarget` 会把「线程成员」当成非引用，不会打 `reply_to=` 标签，这符合预期 |
| `message.mentions` | 文本中每个 `<@U…>` 生成 `{key:'<@U…>', id:'U…', name:<显示名>}`；Core 的 `renderInboundContent` 已经会把 key 换成 `@name` |
| `content.text` | `text` 先反转义 `&amp; &lt; &gt;`，再把 `<#C…\|name>` 换成 `#name`，`<url\|label>` 换成 `label (url)`，`<!here>`/`<!channel>` 换成 `@here`/`@channel`；其他 App 的消息 `text` 为空时从 `blocks`/`attachments` 的 fallback 里取文字 |
| `content.attachments` / 文件 | `files[]` 生成 `[file id=F… name=…]` 占位（Slack 一条消息最多 10 个文件），只给引用，不内联字节 |
| `reply_route` | `{action:'message.reply', input:{message_id}}` |
| `data` | 可带 `is_ext_shared`（Slack Connect 外部共享频道），供 prompt 做信息边界提示 |

注意：

- **显示名**：Core 的 `fromLabel` 只对 `source==='lark'` 走 roster 解析名字，其他来源只显示 id 片段。Slack 的 `U…` id 对模型不友好，见 §7 的 Core 改动 C2。
- **谁能触发**（已定）：和飞书一样，任何人都能用。workspace 里任何成员都能私聊 bot；频道要先邀请 bot 进去才收得到消息，这本身就是一道白名单。被 @ 是强信号，但和真人一样不是非回不可，仍按现有群聊规则判断。Connector 通过 `auth.test` 知道 bot 自己的 user id，把文本里 `<@自己>` 渲染成 `@<agentName>`，就能命中现有 prompt 的「点名即调用」规则。以后可以考虑由 Core 加一个可信的 `at=me` 标签，这样不用靠名字匹配，群聊 slash 命令也能恢复（`commands.mjs` 注释里提到的前提）。
- **编辑消息**：Slack 用户习惯发完立刻改错字。第一期丢弃 `message_changed`；以后如需支持，可以作为非聊天事件 `message.updated`，或者把新文本作为补充行送入。

## 5. 连接与可靠性

### 5.1 接收循环

1. 用 `apps.connections.open`（`Authorization: Bearer xapp-…`）拿 URL，再 `new WebSocket(url)`。
2. 收到 `hello` 时校验 `connection_info.app_id` 和 bot token 属于同一个 App，并记下 `num_connections`。
3. 收到 `events_api` 时**先 normalize 并写 stdout，再回 ack**。这和 Telegram「先 emit 再推进游标」同一语义；崩在两步之间，Slack 会重投，Core 再去重。
4. 收到 `disconnect`（`warning`/`refresh_requested`）时**先建新连接，再关旧连接**。重叠期间两条连接都可能收到事件，由 id 去重兜底。
5. 连接异常时按指数退避重连。如果 `apps.connections.open` 返回 `invalid_auth`、`not_allowed_token_type` 或 App 被卸载，就发 `STATUS failed` 并退出，交给 ConnectorManager，提示 `vika login slack`。

### 5.2 半开连接

Node 自带的 `WebSocket`（undici）会自动回 pong，但没有给 JS 暴露 ping API，所以客户端检测不到「TCP 还在、实际已经没有数据」的半开连接。缓解办法：按 `approximate_connection_time` 和一个上限（例如 30 分钟）中较小的那个，**主动轮换连接**（同样先连新的再关旧的），再加一个「N 分钟没有任何帧就重连」的看门狗。如果真机测试发现仍不够，再考虑加 `ws` 依赖（官方 SDK 用的就是它），或者用 `node:https` 手写升级连接以自己发 ping。

### 5.3 防止两个接收者分流

Slack 允许一个 App 同时开多条连接，用来做高可用和滚动重启。每条消息只投给其中**随机一条**，而不是每条都发一份。Telegram 遇到第二个轮询者会直接返回 409，这里却不会报任何错。所以如果两个进程拿着同一个 App 的 token（另一台机器复制了配置、旧 worker 没退干净、setup 验证时的连接没关），它们会各收到大约一半的消息，而且都不知道另一半去了哪里。我们自己刷新连接时短暂开两条不受影响，因为两条连接都写到同一个 stdout。所以：

- 和 Telegram 一样，在 `<dataDir>/slack/state.sqlite3` 用 PID lease 防止同一实例的 setup 和 worker 同时接收。
- `hello.num_connections` 大于我们自己持有的连接数时，说明别处还连着同一个 App（例如另一个实例误用了同一个 App）。这时记录 activity 并发 `STATUS failed`，提示「每个实例用独立的 Slack App」，不要默默丢消息。

### 5.4 停机期间的消息

Events API 对失败投递会在约 6 分钟内重试 3 次（立即、1 分钟、5 分钟）。文档没写 Socket Mode 在没有任何连接时的行为（待真机验证），可以保守地认为：**停机超过几分钟的消息会丢**。

- 第一期：和其他 IM 一样，不补拉（协议 0.2 本来就没有 durable ACK）。
- 第二期：Connector 按频道记录最后一条 `ts`，启动时用 `conversations.history`/`replies` 补拉，上限仿照邮箱「最多 50 条」。事件 id 用消息身份，所以补拉和实时投递可以自然去重；Core 的 `maxEventAgeMs`（默认 24h）会挡掉太旧的消息。

## 6. 出站动作与查询

### 6.1 manifest

```js
slack: connectorManifest({
  name: 'slack', version: 'slack-socket-mode-v1', capabilities: ['events', 'actions'],
  actions: {
    'message.send':  { content: ['markdown', 'file'], mentions: true },
    'message.reply': { content: ['markdown', 'file'], mentions: true, thread: true },
    'message.react': {},
    'message.retract': {},
  },
  queries: ['message.media', 'chat.members.list'], // 后续加 chat.list / chat.messages / thread.messages / message.search
  auth_schema: EMPTY_SCHEMA, config_schema: EMPTY_SCHEMA,
}),
```

- 不声明 `idempotent`：`chat.postMessage` 没有幂等键，结果为 `UNCERTAIN` 的投递由 Core outbox 挡住，不会重发（和 Telegram 一致）。
- 不声明 `card`：所以审批会自动走编号文字草稿（`proposeByMessage`），Slack 可以直接作为主渠道。

### 6.2 操作映射

| 操作 | Slack API | 说明 |
| --- | --- | --- |
| `message.send` | `chat.postMessage(channel, markdown_text)` | 返回 `message_id=sl:<team>:<channel>:<ts>`、`chat_id`。设 `unfurl_links/unfurl_media=false`，少一些 `message_changed` 噪声，也避免泄露链接预览 |
| `message.reply` | 同上加 `thread_ts` | `reply_in_thread=true` 时 `thread_ts` 取目标的线程根（目标本身就是根时取它的 `ts`；**不能用线程回复的 ts**）。`false` 时：目标在线程里就回到该线程，否则发到频道顶层（Slack 没有引用回复）。线程根要从 route 表查，仿照 Telegram 的 `recordRoute` |
| `message.react` | `reactions.add(channel, timestamp, name)` | Core 传的是飞书 emoji 类型（`OnIt`/`THUMBSUP`/`DONE`…），Connector 映射到 Slack 名字（`eyes`/`+1`/`white_check_mark`…），未知的拒绝。临时 `OnIt` 会经 Core 内部的 `message.reaction.delete` 调 `reactions.remove`，所以这个内部操作也要实现 |
| `message.retract` | `chat.delete(channel, ts)` | 只能删 bot 自己发的消息 |
| `message.media` | `files.info` + `url_private_download` | 带 `Authorization: Bearer xoxb-`；**禁止跟随重定向、只允许 Slack 文件域名**，防止 token 外泄（和 Telegram 同一原则）；大小上限沿用 20 MiB |
| `chat.members.list` | `conversations.members` + `users.info` | Agent 工具 `list_chat_members`，默认每页 100 人、上限 200，通过 `next_cursor` 翻页。返回 ID、显示名与已知的 `is_self` / `is_bot` / `deleted`；资料失败保留成员 ID 并标记未知。查到的姓名加入该频道提及缓存，重名必须用 ID。缺 scope 时提示补权限并重新安装 App，不中断 Connector |
| 文件发送 | `files.getUploadURLExternal` → POST 字节 → `files.completeUploadExternal(channel_id, thread_ts)` | 旧的 `files.upload` 已于 2025-11-12 下线。分享是异步的，complete 不直接返回消息 ts；如果要支持撤回文件消息，需要短轮询 `files.info` 的 `shares` 拿 ts，否则结果里 `message_id` 为空 |

### 6.3 文本格式

- `chat.postMessage` 的 `markdown_text` 参数接受 Markdown，上限 12,000 字符，不能和 `text`/`blocks` 同时用。**待真机验证**：它对标准 Markdown（`**粗体**`、表格、代码块）的支持程度，以及能否在里面写 `<@U…>` 提及。
- 如果 `markdown_text` 不能可靠渲染提及，就退回 `text` + 自己转 mrkdwn（`**x**`→`*x*`、`[t](u)`→`<u|t>`、转义 `& < >`）。
- 超长内容让模型改用文件发送（与 Telegram 的 prompt 相同）。

### 6.4 限流

- `chat.postMessage` 大约 1 条/秒/频道，允许短时突发。按频道串行发送并留约 1.1s 间隔（照搬 Telegram 的 `sendTail`/`nextSendAt`）。
- 只有 `ratelimited` 且带 `Retry-After` 时，才在 action 期限内重试最多 2 次。网络错误和 5xx 一律当作结果不确定，不重试。

## 7. Core 需要改的地方

必须（第一期）：

| # | 位置 | 改动 |
| --- | --- | --- |
| C1 | `config.mjs`、`deployment-config.cjs`、CLI manifest 校验 | `connectors`/`VIKA_CHANNEL` 加 `slack`；`ark` runtime 拒绝 slack（同 Telegram：Volc Worker 访问不到 actions.sock） |
| C2 | `runtime-input.mjs fromLabel` + 协议 | `fromLabel` 现在只有飞书走 roster，Slack 只会显示 `U0123ABCD`。建议在协议里给 `actor` 加可选的 `name`（不可信，复用 `sanitizeMemberName`），非飞书来源有名字就显示名字。Telegram、微信以后也能用 |
| C3 | `actions/tools.mjs` | `mention_user_ids` 的 pattern 写死了 `^ou_`，`reply_in_thread`、`mention_*` 的描述写的是「Lark only」。改成按 manifest 声明的能力描述，id pattern 放宽到 `ou_…` 或 Slack 的 `[UW][A-Z0-9]{8,}`。实现时 `mention_names` 也支持了 Slack：Core 把名字原样交给 Connector，由它按自己在该频道给模型看过的人解析 |
| C4 | `actions/tools.mjs get_message_media` | Slack 一条消息可以带多个文件：放宽 `key` 允许 `F[A-Z0-9]{8,}`，或者不传 key 时返回全部 |
| C5 | `conversation-policy.mjs` | 加 `slackConnectorPolicy`：线程习惯（频道里优先 `reply_in_thread`）、Markdown 上限、Slack Connect 频道是外部可见、reaction 名称。注意 `assembleConversationPolicy` 的分支：只接了 Slack 的实例会走 `renderBasePrompt`，里面的群聊规则偏飞书，还写死了 OpenViking 的两个 `oc_` 管理群 id，要确认 Slack-only 实例能用这份 prompt |
| C6 | `connectors/worker.mjs`、`catalog.mjs`、`operations.mjs` | 注册 `SlackClient` 和 manifest（`message.reaction.delete` 已在 OPERATIONS 中） |

可选 / 后续：

- 已知的 `source === 'lark'` 特判：`harness.mjs` 的 `onInboundChat`（群名解析）和 `roster.warm`；`chat-names.mjs` 依赖 lark-cli。Slack 的频道名可以由 Connector 通过 `chat.get` 提供，或者直接放进事件 `data`。
- §3 末尾的「线程回复按线程根判断新鲜度」。
- `choices` / 审批卡片：Slack Block Kit 按钮经 Socket Mode `interactive` envelope 回来，归一化成 `message.action`。但 `card-messages.mjs`、`lark_cards` 表、`approvalCard` 都是飞书卡片格式，需要先把卡片登记抽象成跨 IM 的能力，第二期再做。

## 8. 凭据、owner 与 setup

**Slack App manifest**（setup 时生成后放进 `https://api.slack.com/apps?new_app=1&manifest_json=<urlencode>` 预填链接）：

```json
{
  "display_information": { "name": "Vika" },
  "features": {
    "app_home": { "messages_tab_enabled": true, "messages_tab_read_only_enabled": false },
    "bot_user": { "display_name": "Vika", "always_online": true }
  },
  "oauth_config": { "scopes": { "bot": [
    "chat:write", "im:write", "reactions:write", "files:read", "files:write", "users:read",
    "channels:history", "groups:history", "im:history", "mpim:history",
    "channels:read", "groups:read", "im:read", "mpim:read"
  ] } },
  "settings": {
    "event_subscriptions": { "bot_events": ["message.channels", "message.groups", "message.im", "message.mpim"] },
    "socket_mode_enabled": true,
    "interactivity": { "is_enabled": true }
  }
}
```

第二期如果做搜索，需要加 user scope `search:read`（`search.messages` 只接受 user token），setup 还要多收一个 `xoxp-` token。

**setup 流程**（`packages/cli/src/slack.mjs`，参照 `telegram.mjs`）：

1. 显示预填链接（终端里可按 `o` 打开浏览器）→ 用户选 workspace 创建 App → Install to Workspace。
2. 用户复制 Bot User OAuth Token（`xoxb-`），到 Basic Information → App-Level Tokens 生成带 `connections:write` 的 `xapp-` token。两个都用隐藏输入粘贴。
3. 验证：`auth.test`（拿 team/bot user/bot_id）+ `apps.connections.open`（App-level token 有效）+ `hello.app_id` 与 bot 同属一个 App。
4. **owner 绑定**：显示随机口令，并提供 `slack://app?team=<T>&id=<A>&tab=messages` 链接直达 bot 私聊。用户私聊发口令后，固定 owner 的 `user_id` 和 DM 的 `channel`（或 `conversations.open` 得到）。`check` 返回 `owner: { id: U…, target: { chat_id: D… } }`。和微信不同，Slack bot 随时可以主动私聊 owner，所以可以当主渠道（`VIKA_CHANNEL=slack`）用于 `notify_owner` 和邮件审批。
5. token、owner、route 表存在 `<dataDir>/slack/state.sqlite3`（文件 0600、目录 0700），不写进 manifest。日志只记 `opaqueRef`，token 字段一律 `[REDACTED]`（worker logger 已经对含 `token` 的键做了脱敏）。

`vika login slack <实例>`：换 token，或重新绑定 owner（流程同 Telegram）。

**斜杠命令**：在 Slack 里输入 `/new` 会被客户端当作 Slack 命令拦下，提示「not a valid command」，消息到不了 bot。

- 第一期：在 prompt/README 里说明「前面加一个空格」，即发 ` /new`。Core 的 `commandCandidate` 会 `trim()`，私聊（`p2p`）照常识别，不需要改 Core。
- 第二期：在 App manifest 里注册 `/vika`，Socket Mode `slash_commands` envelope 进来后转成私聊里的标准文本事件（`/vika new` → `/new`），`reply_route` 用 `message.send {chat_id}`。需要给这个事件合成一个 message id，并只接受在 bot 私聊里调用的命令。

## 9. 需要产品决定的问题

1. ~~私聊范围~~ **已定：任何人都能用**（同飞书）；被 @ 不是必回，见 §4。
2. ~~其他 App 的消息~~ **已定：送给模型**，`actor.type='bot'`。模型如果配了内网 skill 或 GitHub 权限，能读懂并处理这类消息。代价和注意点：
   - 会多消耗 token；按现有 prompt，模型对自动广播大多保持沉默。
   - 高频告警会让该频道顶层消息上的决定更容易过期（§3）。
   - 两个 AI bot（最坏的情况是同一频道里另一个 Vika 实例）可能互相回复形成循环。飞书要 `im:message.group_msg.include_bot:read` 才推送其他 bot 的消息（`permission.json` 已申请，README 手动配置说明里补上了）；Slack 的 `message.*` 订阅没有这个开关，默认就会收到。
     **最终做法**（讨论后定，飞书和 Slack 共用）：多 bot 协作是重要场景，所以不用 @ 做机制，也不按小次数拦截。回不回、协作几轮由模型按「有必要才说话」判断，Core 只给它信息：`sender=bot` 和 `bots_since_human=N`。另有一根只防失控的保险丝：同一会话没有真人说话、已回应 `VIKA_BOT_REPLY_FUSE`（默认 20，0 关闭）条 bot 消息后拒绝再回（`bot_loop`），私聊通知 owner 一次，真人发言即重置。参考了 botmux：它没有轮数上限，靠「bot 消息必须显式 @ 才唤醒 + 每条发送必须明确 @ 谁」把循环门槛抬高；Vika 不走 @，而且全局只有一个 runtime，一个群失控会拖住所有群，所以保留这根保险丝。
   - 吵得太厉害的 bot 可以用 `ignoredSenderIds` 按 `bot_id` 屏蔽。
3. **Slack Connect 外部共享频道**：建议和飞书外部群一样照常接收，靠现有 prompt 的「群聊可能有外部客户」信息边界约束。Connector 再在事件里标出 `is_ext_shared`，让模型确定地知道对方在场。bot 必须被人主动拉进这类频道，所以不需要默认忽略。
4. **数据合规**：Slack 2025-05 的 API 条款强调第三方 App 不得批量下载会话数据用于索引/查询。Vika 会把收到的消息写进 SQLite 和 OV session。作为用户自建、只装在自己 workspace 的内部 App，风险较低，但企业 workspace 最好让管理员确认；很多企业还开启了「装 App 需管理员审批」。

## 10. 实施拆分（建议）

| 阶段 | 内容 | 主要文件 |
| --- | --- | --- |
| P1 Connector 核心 | `src/slack/{api,socket,events,client,state,media}.mjs`；Socket Mode 收发、事件过滤与映射、send/reply/react/retract/media、PID lease、连接轮换 | 新增 + `catalog.mjs`、`worker.mjs` |
| P1 Core | §7 的 C1–C6 | `config.mjs`、`tools.mjs`、`runtime-input.mjs`、`event.mjs`、`conversation-policy.mjs` |
| P1 CLI | setup / login / 主渠道选择 / 多实例 | `cli/src/{slack,setup,login,instances}.mjs`、`bin/vika.mjs` |
| P1 测试 | 仿照 `telegram.test.mjs`：假 `fetch` + 假 WebSocket 服务端（`node:http` upgrade）跑 hello/envelope/ack/disconnect/refresh、回显过滤、subtype 白名单、线程 scope、去重、429 | `test/slack.test.mjs`、CLI 测试 |
| P2 | 补拉、`chat.list`/`chat.messages`/`thread.messages`/`message.search`、Block Kit 交互、`/vika`、编辑事件 | — |

真机验证清单：bot 自身消息是否回推及其字段；`markdown_text` 渲染与提及；Socket Mode 在没有连接时的重投/丢弃行为；`url_private_download` 是否会重定向；文件分享拿到 ts 的延迟；连接刷新的频率。

## 参考

- [Using Socket Mode](https://docs.slack.dev/apis/events-api/using-socket-mode)
- [Events API（重试与禁用规则）](https://docs.slack.dev/apis/events-api/)
- [message event 与 subtype](https://docs.slack.dev/reference/events/message)
- [chat.postMessage](https://docs.slack.dev/reference/methods/chat.postMessage)
- [files.upload 下线与替代流程](https://docs.slack.dev/changelog/2024-04-a-better-way-to-upload-files-is-here-to-stay)、[files.completeUploadExternal](https://docs.slack.dev/reference/methods/files.completeUploadExternal)
- [2025-05 非 Marketplace App 限流与条款变更](https://docs.slack.dev/changelog/2025/05/29/rate-limit-changes-for-non-marketplace-apps)、[2025-06 澄清](https://docs.slack.dev/changelog/2025/06/03/rate-limits-clarity)
- [App manifest 与预填创建链接](https://docs.slack.dev/app-manifests/configuring-apps-with-app-manifests)
