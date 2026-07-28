# 资源 Watch

Watch API 管理资源的周期检查、暂停、恢复和手动触发。

## API 参考

### Watch Management（监控任务管理）

列出、查看、更新和触发通过 [`add_resource`](02-resources.md#add_resource) 配合 `watch_interval > 0` 创建的监控任务。控制面在 REST（`/api/v1/watches`）、`ov task watch` CLI 子命令组以及面向 Agent 的最小闭包 MCP 接口（`list_watches` / `cancel_watch`）三处镜像。

#### 1. API 实现介绍

此控制面封装了 `WatchManager` 原语，未改动任何服务端行为。每个端点和 CLI 命令都支持通过 `task_id`（路径）或 `to_uri`（查询参数）定位目标任务，两种键可以互换；如果同时提供，二者必须指向同一任务，否则返回 400。

**操作**：
- **列出**（`GET /api/v1/watches`）— 返回 `{tasks, total}`；可传 `?active_only=true` 过滤；传 `?to_uri=...` 时降级为单任务查找
- **查看**（`GET /api/v1/watches/{task_id}`）— 查看单个任务；可选 `?to_uri=` 做跨键一致性校验
- **更新**（`PATCH /api/v1/watches/{task_id}` 或 `PATCH /api/v1/watches?to_uri=...`）— 部分更新 `watch_interval`、`is_active`、`reason`、`instruction`。`is_active` 与 `watch_interval` 正交：翻转 `is_active` 可在不丢失配置周期的前提下暂停/恢复任务。
- **删除**（`DELETE /api/v1/watches/{task_id}` 或 `DELETE /api/v1/watches?to_uri=...`）
- **触发**（`POST /api/v1/watches/{task_id}/trigger` 或 `POST /api/v1/watches/trigger?to_uri=...`）— 触发即返回（fire-and-forget），重新摄取在后台异步执行

**代码入口**：
- `openviking/server/routers/watches.py` — `/api/v1/watches` REST 路由
- `crates/ov_cli/src/commands/watch.rs` — `ov task watch` CLI 子命令组
- `openviking/server/mcp_endpoint.py` — MCP `list_watches` / `cancel_watch` 工具，以及 `add_resource` 上的 `watch_interval` / `to` 参数
- `openviking/resource/watch_manager.py:WatchManager` — 任务持久化与调度原语

#### 2. 接口和参数说明

对每个单任务端点，路径中的 `{task_id}` 都可用查询参数 `?to_uri=` 替代。CLI 的 `<key>` 参数会自动分类：任何以 `viking://` 开头的值走 by-URI 路径，其他值视为 task_id（其它 scheme 如 `http://` 会在本地直接报错，避免静默 404）。

**`PATCH /watches` 请求体**（字段均可选，至少需提供一个）

| 字段 | 类型 | 说明 |
|------|------|------|
| watch_interval | float | 新的检查周期（分钟），必须 `> 0`；如需暂停而保留周期请改用 `is_active=false`。 |
| is_active | bool | 切换激活状态而保留配置周期（暂停 / 恢复）。 |
| reason | string | 更新该监控任务的记录原因。 |
| instruction | string | 更新语义处理指令。 |

未识别字段会被拒绝，并返回 HTTP `400` 和 `INVALID_ARGUMENT`（请求模型使用 `extra="forbid"`）。未传字段保留原值。

#### 3. 使用示例

**HTTP API**

```bash
# 列出活跃监控任务（去掉 ?active_only 可同时包含已暂停的任务）
curl -s "http://localhost:1933/api/v1/watches?active_only=true" \
  -H "X-API-Key: your-key"

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

`scheduled=true` 只表示后台执行已调度，不表示重新摄取已经完成；应再次查看任务的 `last_execution_time`。

**MCP**（Agent 控制面——仅最小闭包）

```text
list_watches()                                            # 每个任务一行；只暴露 URI，不暴露 task_id
cancel_watch(to_uri="viking://resources/guide.md")        # 按 URI 幂等删除
```

暂停 / 恢复 / 触发 / 更新故意不通过 MCP 暴露——这些 power-user 操作放在 CLI/REST 一侧，以保持 Agent 系统提示词的紧凑。Agent 侧若需创建监控任务或调整周期，仍走 [`add_resource`](02-resources.md#add_resource) 配合 `watch_interval`；可显式传 `to`，也可让系统绑定本次导入返回的 `root_uri`。

---

## 相关文档

- [资源](02-resources.md) - 创建带 watch_interval 的资源
- [后台任务](17-tasks.md) - 查询后台处理状态
