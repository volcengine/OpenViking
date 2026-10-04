# 目录 TTL

TTL 默认关闭，作用于用户和 peer 的 `events/YYYY/MM/DD` 日期目录及 `sessions/{session_id}`。resources 和其他记忆类别不在范围内。

## 配置与增量生效

配置入口保留库全局、类型默认值，以及以下根目录：

- `viking://user/{user_id}/memories/events`
- `viking://user/{user_id}/peers/{peer_id}/memories/events`
- `viking://user/{user_id}/sessions`

优先级为具体根目录 → 类型默认值（`user_events`、`peer_events`、`sessions`）→ 库全局 → 关闭。`disabled` 阻断继承，`inherit` 回退。account 配置沿用现有库配置入口；user/account 身份不增加新的优先级。

年月、日期、单 Session、子目录和文件只展示期限，不支持编辑。根目录策略变更只影响新建生命周期目录；已纳管目录保留冻结的期限和天数，未纳管的历史目录追加内容也不会自动纳管。

## 期限与续期

每个生命周期目录在 `.meta.json` 保存一个 `expires_at`，相对策略额外保存 `ttl_days`；沿用 `received_at` 记录内容时间。events 兼容读取旧 `.ttl.json`。AGFS 元数据更新保留同一文件中的其他业务字段，目录 stat 返回该目录自己的 `expires_at`。

- events：第一次成功写入内容时开始计时，路径日期仅用于归组。后续追加或修改不续期；相对和绝对期限都固定。
- Session：创建时继承 `sessions` 根策略。成功追加消息、完成有内容的 commit 后，按保存的 `ttl_days` 续期。任务重放沿用原完成时间。
- 读取、摘要生成、重建索引、失败写入、空消息批次和空 commit 不续期。绝对时间不自动延长。

目录内的消息、附件、归档、L0/L1/L2 共用同一到期时间，不做 JSONL 消息级 TTL。无 `ttl_generation`、单 Session 覆盖或逐文件模式。

## 可见性

UTC 时间达到 `expires_at` 后，目录及全部后代不可见。直接访问返回 404；Session 列表、文件列表、find/search/recall、grep/glob 会过滤到期内容，并补足可见候选。

结构化对象回显实际所属目录的 `expires_at`，未开启时明确返回 `null`。跨目录结果逐项回显；单对象的文本或列表响应在外层提供期限。纯 URI 列表兼容模式和下载字节格式不变，仍执行服务端过滤。年月及根目录没有共同期限，返回 `null`；根目录另展示 `policy`、`effective_policy`。

## 清理任务与性能

任务沿用 Session commit 的 QueueFS 离线执行框架。调度器按持久化到期索引领取候选，受每轮数量、字节和时间预算限制，默认在天级窗口内分散物理删除。

删除前以元数据文件锁复查目录期限；Session 复用现有 Session 写入互斥锁。目录内逐文件加锁删除，锁忙则重试，不使用 tree 锁，不逐文件判断过期时间。删除包括目录内全部正文、消息、附件、L0/L1、向量和 Meta；元数据最后删除，确认存储与索引清空后才移除到期登记。目录外的上层摘要保持原样，删除不触发 LLM、embedding 或摘要重建。

查询在同一批结果中复用 owner 元数据读取。清理重试保存在到期登记中，任务历史的保留时长不影响正确性。显示和计费允许随异步物理删除延迟；云端计费、网关转发和备份不由 OV 清理完成状态自动证明。

## 接口

- [TTL 配置](../configuration/01-server.md#ttl)：库、类型及根目录策略。
- [期限查询](../api/12-content.md#文档到期时间)：`GET /api/v1/content/ttl`，SDK/MCP `get_ttl`，CLI `ov ttl get`。
- [Session](../api/05-sessions.md#session-ttl)：统一继承根目录策略，创建/配置接口不接收 TTL 参数。
