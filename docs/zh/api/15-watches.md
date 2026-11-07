# 资源 Watch

Watch API 管理资源的周期检查、暂停、恢复和手动触发。

## API 参考

### Watch Management（监控任务管理）

列出、查看、更新和触发通过 [`add_resource`](02-resources.md#add-resource) 配合 `watch_interval > 0` 创建的监控任务。REST（`/api/v1/watches`）和 `ov task watch` 提供完整管理操作；MCP 提供 `list_watches` 和 `cancel_watch`，用于列出和删除 Watch。

#### 1. API 实现介绍

通过 `task_id` 定位任务，或通过 `to_uri` 查找唯一可访问的任务。多个 Connector Watch 可以共享目标；此时按 URI 操作会返回 `409 Conflict`，需要改用 `task_id`。同时提供两个参数时，它们必须指向同一任务，否则返回 `400`。

**操作**：
- **列出**（`GET /api/v1/watches`）— 返回 `{tasks, total}`；可传 `?active_only=true` 过滤、`?limit=100` 分页，或 `?to_uri=...` 查找单个任务
- **查看**（`GET /api/v1/watches/{task_id}`）— 查看单个任务；可选 `?to_uri=` 做跨键一致性校验
- **更新**（`PATCH /api/v1/watches/{task_id}` 或 `PATCH /api/v1/watches?to_uri=...`）— 部分更新 `watch_interval`、`is_active`、`reason`、`instruction`。`is_active` 与 `watch_interval` 独立：修改 `is_active` 可在不丢失配置周期的前提下暂停/恢复任务。
- **删除**（`DELETE /api/v1/watches/{task_id}` 或 `DELETE /api/v1/watches?to_uri=...`）
- **触发**（`POST /api/v1/watches/{task_id}/trigger` 或 `POST /api/v1/watches/trigger?to_uri=...`）— 触发即返回（fire-and-forget），重新摄取在后台异步执行

**代码入口**：
- `openviking/server/routers/watches.py` — `/api/v1/watches` REST 路由
- `crates/ov_cli/src/commands/watch.rs` — `ov task watch` CLI 子命令组
- `openviking/server/mcp_endpoint.py` — MCP `list_watches` / `cancel_watch` 工具，以及 `add_resource` 上的 `watch_interval` / `to` 参数
- `openviking/resource/watch_manager.py:WatchManager` — 任务持久化与调度原语

#### 2. 接口和参数说明

对每个单任务端点，路径中的 `{task_id}` 都可用查询参数 `?to_uri=` 替代。CLI 的 `<key>` 参数会自动分类：任何以 `viking://` 开头的值走 by-URI 路径，其他值视为 task_id（其它 scheme 如 `http://` 会在本地直接报错，避免静默 404）。

**`GET /watches` 分页**

| 参数 | 类型 | 说明 |
|------|------|------|
| limit | integer | 每页 1–200 条；传入后启用分页。 |
| cursor | string | 使用上一页返回的 `next_cursor`；单独传 cursor 时每页默认 100 条。 |
| active_only | boolean | 默认 false；后续页需保持同一过滤条件。 |

分页返回 `{tasks, total, has_more, next_cursor}`。`total` 是当前可见且符合过滤条件
的 Watch 总数；末页 `next_cursor` 为 `null`。按 `created_at DESC, task_id DESC`
排序。使用相同服务和表的 MySQL 节点可共享游标；请求身份和过滤条件必须一致。
非法游标、分页与 `to_uri` 单任务查找混用返回 `400`。不传 `limit/cursor` 时
保留原来的不分页响应。

每页读取当前数据，不提供跨请求快照；首页之后新增且排在游标之前的 Watch 需从
首页重新查询，删除游标对应的 Watch 不影响继续翻页。MySQL 使用游标条件和
`LIMIT`，只把公开字段读入应用，不读取鉴权和 checkpoint。File 从已有内存缓存
扫描并选出一页，不重读磁盘快照，也不排序整个列表。精确总数仍需统计匹配记录。

**`PATCH /watches` 请求体**（字段均可选，至少需提供一个）

| 字段 | 类型 | 说明 |
|------|------|------|
| watch_interval | float | 新的检查周期（分钟），必须 `> 0`；如需暂停而保留周期请改用 `is_active=false`。 |
| is_active | bool | 切换激活状态而保留配置周期（暂停 / 恢复）。 |
| reason | string | 更新该监控任务的记录原因。 |
| instruction | string | 更新语义处理指令。 |

自动调度在目标锁内读取当前参数并占用 Watch，冻结本轮参数后才完成 due 选取。
占用后的参数修改从下一轮生效。暂停阻止后续自动选取，已选取或正在执行的任务继续运行；暂停期间
仍可手动触发。

托管 Connector 的控制面还可独立提交 `credential_state` 通知，不能与上述字段组合。该对象包含引用归属字段 `account_id`、`resource_id`、`user_id`、`ov_user_id`、`ov_account_id`、`type`、`platform`，以及正整数存储版本 `version` 和布尔值 `authorized`。内核核对 watch 私有引用并忽略过期或重复通知；撤销时记录自动暂停，重新授权时仅恢复自动暂停的任务。手动暂停/恢复清除自动暂停标记，执行历史保留。通知水位和暂停标记只存储在私有 watch 数据中，不出现在查询响应；历史无标记的暂停任务保留原状态。

未识别字段会被拒绝，并返回 HTTP `400` 和 `INVALID_ARGUMENT`（请求模型使用 `extra="forbid"`）。未传字段保留原值。

#### 3. 使用示例

**HTTP API**

```bash
# 列出活跃监控任务（去掉 ?active_only 可同时包含已暂停的任务）
curl -s "http://localhost:1933/api/v1/watches?active_only=true" \
  -H "X-API-Key: your-key"

# 首页；后续页携带返回的 next_cursor，并保持过滤条件
curl -s "http://localhost:1933/api/v1/watches?active_only=true&limit=100" \
  -H "X-API-Key: your-key"
curl -sG "http://localhost:1933/api/v1/watches" \
  -H "X-API-Key: your-key" \
  --data-urlencode "active_only=true" --data-urlencode "limit=100" \
  --data-urlencode "cursor=<next_cursor>"

# 暂停一个监控任务而保留其检查周期
curl -X PATCH "http://localhost:1933/api/v1/watches/<task_id>" \
  -H "X-API-Key: your-key" -H "Content-Type: application/json" \
  -d '{"is_active": false}'

# 触发一次立即刷新（fire-and-forget，立即返回，再次摄取在后台执行）
curl -X POST "http://localhost:1933/api/v1/watches/<task_id>/trigger" \
  -H "X-API-Key: your-key"

# 按 URI 而非 task_id 定位任务
curl -X DELETE "http://localhost:1933/api/v1/watches?to_uri=viking://resources/guide.md" \
  -H "X-API-Key: your-key"
```

**Python SDK**

以下使用 `SyncHTTPClient`；使用 `AsyncHTTPClient` 时，每次调用都需加 `await`。

```python
watches = client.list_watches(active_only=True)
client.update_watch(to_uri="viking://resources/guide.md", is_active=False)
client.trigger_watch(to_uri="viking://resources/guide.md")
client.delete_watch(to_uri="viking://resources/guide.md")
```

**TypeScript SDK**

```typescript
const watches = await client.listWatches({ activeOnly: true });
await client.updateWatch(
  { toUri: "viking://resources/guide.md" },
  { isActive: false },
);
await client.triggerWatch({ toUri: "viking://resources/guide.md" });
await client.deleteWatch({ toUri: "viking://resources/guide.md" });
```

**Go SDK**

```go
watches, err := client.ListWatches(ctx, &openviking.ListWatchesOptions{
    ActiveOnly: true,
})
updated, err := client.UpdateWatch(ctx, openviking.UpdateWatchOptions{
    ToURI:    "viking://resources/guide.md",
    IsActive: openviking.Bool(false),
})
triggered, err := client.TriggerWatch(ctx, openviking.WatchRef{
    ToURI: "viking://resources/guide.md",
})
deleted, err := client.DeleteWatch(ctx, openviking.WatchRef{
    ToURI: "viking://resources/guide.md",
})
_, _, _, _ = watches, updated, triggered, deleted
```

**CLI**

以下示例使用 `ov task watch` 子命令：

```bash
# 列出活跃监控任务（去掉 --active-only 可同时包含已暂停的任务）
ov task watch ls --active-only

# 查看单个监控任务（key 可以是 viking:// URI 或 task_id）
ov task watch show viking://resources/guide.md

# 暂停 / 恢复，不丢失配置周期
ov task watch pause viking://resources/guide.md
ov task watch resume viking://resources/guide.md

# 更新周期（或 --active / --reason / --instruction 的任意组合）
ov task watch update viking://resources/guide.md --interval 30

# 触发一次立即刷新（fire-and-forget）
ov task watch trigger viking://resources/guide.md

# 删除监控任务
ov task watch rm viking://resources/guide.md
```

**响应**

列出任务时返回：

```json
{
  "status": "ok",
  "result": {
    "tasks": [
      {
        "task_id": "7f02e980-8df9-4f27-a570-4d8428cbed8a",
        "path": "https://example.com/guide.md",
        "source_type": "url",
        "to_uri": "viking://resources/guide.md",
        "parent_uri": "viking://resources",
        "reason": "keep documentation current",
        "instruction": "",
        "watch_interval": 30,
        "build_index": true,
        "summarize": false,
        "processor_kwargs": {},
        "created_at": "2026-07-24T10:00:00",
        "last_execution_time": null,
        "last_task_id": null,
        "last_status": null,
        "last_error": null,
        "next_execution_time": "2026-07-24T10:30:00",
        "is_active": true,
        "account_id": "default",
        "user_id": "default",
        "original_role": "user"
      }
    ],
    "total": 1
  }
}
```

`source_type` 是可选的来源元数据。显式 Connector `add_type` 优先（例如 `tos` 或
`feishu_project`）；原生导入返回 `feishu`、`git`、`url` 或 `local`。历史任务或
无法分类的任务返回 `null`。

首次执行前，`last_task_id`、`last_status` 和 `last_error` 均为 `null`。执行后，
`last_task_id` 指向对应的普通导入任务（预检查失败时可能为 `null`），`last_status`
为 `completed`、`failed` 或 `cancelled`；失败时 `last_error` 返回经过凭证脱敏且最多
500 字符的错误信息。可用 `ov task status <last_task_id>` 查看对应导入任务详情。

查看单个任务以及成功更新时，`result` 直接是同一结构的任务对象。删除和触发分别返回：

```json
{
  "status": "ok",
  "result": {
    "task_id": "7f02e980-8df9-4f27-a570-4d8428cbed8a",
    "to_uri": "viking://resources/guide.md",
    "deleted": true
  }
}
```

```json
{
  "status": "ok",
  "result": {
    "task_id": "7f02e980-8df9-4f27-a570-4d8428cbed8a",
    "to_uri": "viking://resources/guide.md",
    "scheduled": true
  }
}
```

`scheduled=true` 只表示后台执行已调度，不表示重新摄取已经完成；应再次查看任务，直到
`last_execution_time` 更新，并检查 `last_status` 和 `last_error`。

**MCP**

```text
list_watches()                                            # 每个任务一行；只暴露 URI，不暴露 task_id
cancel_watch(to_uri="viking://resources/guide.md")        # 按 URI 幂等删除
```

删除 Watch 会移除定时任务，已导入的资源保留。MCP 只提供列出和删除 Watch。创建 Watch 时，使用 [`add_resource`](02-resources.md#add-resource) 并设置 `watch_interval`；暂停、恢复、触发和调整已有 Watch 的周期使用 CLI 或 REST。重复导入不会更新已有 Watch。

---

## 相关文档

- [资源](02-resources.md) - 创建带 watch_interval 的资源
- [后台任务](17-tasks.md) - 查询后台处理状态
