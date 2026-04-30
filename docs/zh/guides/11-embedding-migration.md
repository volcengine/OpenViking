# 嵌入模型蓝绿迁移

OpenViking 支持在不中断服务的前提下，将嵌入模型从当前版本平滑迁移到新版本。迁移过程通过 REST API 驱动，全程读写可用，每个阶段均支持安全回退。

## 前置条件

### 1. 服务版本

需要 OpenViking `0.2.x` 或更高版本。

### 2. 多嵌入器配置

在 `ov.conf` 中配置 `embeddings` 字段，为每个嵌入模型版本命名：

```json
{
  "embedding": {
    "dense": {
      "provider": "volcengine",
      "model": "doubao-embedding-v1",
      "dimension": 1024,
      "api_key": "your-api-key",
      "api_base": "https://ark.cn-beijing.volces.com/api/v3"
    },
    "max_concurrent": 10
  },
  "embeddings": {
    "v1": {
      "dense": {
        "provider": "volcengine",
        "model": "doubao-embedding-v1",
        "dimension": 1024,
        "api_key": "your-api-key",
        "api_base": "https://ark.cn-beijing.volces.com/api/v3"
      },
      "max_concurrent": 10
    },
    "v2": {
      "dense": {
        "provider": "volcengine",
        "model": "doubao-embedding-v2",
        "dimension": 2048,
        "api_key": "your-api-key",
        "api_base": "https://ark.cn-beijing.volces.com/api/v3"
      },
      "max_concurrent": 8
    }
  }
}
```

**配置要点**：

- `embedding` 字段保留，向后兼容。当 `embeddings` 为空时使用。
- `embeddings` 是一个字典，key 为版本名称（如 `v1`、`v2`），value 为完整的 `EmbeddingConfig`。
- 每个版本的配置必须包含 `dense` 子字段（与 `embedding` 字段结构一致）。
- 首次配置 `embeddings` 时，系统会自动创建迁移状态文件并将当前活跃配置记为 `default`。

### 3. 鉴权

所有迁移相关的写操作（`/start`、`/build`、`/switch`、`/disable-dual-write`、`/finish`、`/abort`、`/rollback`）需要 **admin** 或 **root** 角色。只读操作（`/status`、`/targets`）需要任意已认证身份。

```bash
# 请求中携带 API Key
curl -H "X-API-Key: your-admin-key" ...
```

## 迁移流程

### 第一步：查看可用目标

```bash
curl -H "X-API-Key: your-admin-key" \
  http://localhost:1933/api/v1/migration/targets
```

返回可用的目标嵌入器列表：

```json
{
  "status": "ok",
  "result": {
    "targets": [
      { "name": "v1", "provider": "volcengine", "model": "doubao-embedding-v1", "dimension": 1024 },
      { "name": "v2", "provider": "volcengine", "model": "doubao-embedding-v2", "dimension": 2048 }
    ]
  }
}
```

### 第二步：启动迁移

```bash
curl -X POST http://localhost:1933/api/v1/migration/start \
  -H "Content-Type: application/json" \
  -H "X-API-Key: your-admin-key" \
  -d '{"target_name": "v2"}'
```

此时系统进入 **双写** 阶段：
- 所有新写入的数据同时写入源集合和目标集合
- 读取仍然从源集合获取
- 目标集合的写入失败**不会**影响正常服务

### 第三步：开始重建索引

```bash
curl -X POST http://localhost:1933/api/v1/migration/build \
  -H "X-API-Key: your-admin-key"
```

系统启动后台重建任务：
- 扫描源集合中的所有 URI
- 跳过目标集合中已存在的 URI（支持增量重建）
- 用目标模型重新嵌入并写入目标集合

### 第四步：监控进度

```bash
curl http://localhost:1933/api/v1/migration/status \
  -H "X-API-Key: your-admin-key"
```

```json
{
  "status": "ok",
  "result": {
    "migration_id": "mig_a1b2c3d4e5f6",
    "phase": "building",
    "active_side": "source",
    "dual_write_enabled": true,
    "source_embedder_name": "v1",
    "target_embedder_name": "v2",
    "degraded_write_failures": 0,
    "reindex_progress": {
      "processed": 75000,
      "total": 100000,
      "errors": 0,
      "skipped": 1200
    }
  }
}
```

**进度字段说明**：

| 字段 | 含义 |
|------|------|
| `phase` | 当前阶段（`building` 表示正在重建） |
| `reindex_progress.processed` | 已处理的 URI 数量 |
| `reindex_progress.total` | 需要处理的总 URI 数量 |
| `reindex_progress.errors` | 处理失败的 URI 数量 |
| `reindex_progress.skipped` | 跳过的 URI 数量（目标已存在） |
| `degraded_write_failures` | 双写 standby 侧失败次数（用于判断目标集合健康状态） |

重建完成后，`phase` 自动变为 `building_complete`。

### 第五步：检查质量

在 `building_complete` 阶段，你可以：

- **查看错误率**：通过 `/status` 接口查看 `reindex_progress.errors / total`
- **重新重建**：如果不满意，可以再次调用 `POST /build`，系统会跳过已有嵌入，仅处理缺失和新增的 URI

```
POST /build → building → building_complete → POST /build → building → building_complete → ...
```

### 第六步：切换读取

确认质量满意后，切换读取到新模型：

```bash
curl -X POST http://localhost:1933/api/v1/migration/switch \
  -H "X-API-Key: your-admin-key"
```

此时：
- 所有读取操作从目标集合获取
- 双写仍在进行，确保读写一致性

### 第七步：关闭双写

观察服务稳定运行一段时间后，关闭双写：

```bash
curl -X POST http://localhost:1933/api/v1/migration/disable-dual-write \
  -H "X-API-Key: your-admin-key"
```

此时：
- 仅目标集合接收写入
- 源集合冻结

> ⚠️ **注意**：关闭双写后无法回滚。如需回退，请在 `switched` 阶段使用 `/rollback`。

### 第八步：完成迁移

```bash
curl -X POST http://localhost:1933/api/v1/migration/finish \
  -H "Content-Type: application/json" \
  -H "X-API-Key: your-admin-key" \
  -d '{"confirm_cleanup": true}'
```

- `confirm_cleanup: true`：删除源集合（释放存储空间）
- `confirm_cleanup: false`：保留源集合（默认）

完成后，迁移状态文件永久记录此次迁移。下一次启动服务时，系统自动使用目标配置。

## 完整 Happy Path

```bash
# 1. 查看目标
curl http://localhost:1933/api/v1/migration/targets -H "X-API-Key: admin-key"

# 2. 启动迁移
curl -X POST http://localhost:1933/api/v1/migration/start \
  -H "Content-Type: application/json" -H "X-API-Key: admin-key" \
  -d '{"target_name": "v2"}'

# 3. 开始重建
curl -X POST http://localhost:1933/api/v1/migration/build -H "X-API-Key: admin-key"

# 4. 轮询等待重建完成
while true; do
  STATUS=$(curl -s http://localhost:1933/api/v1/migration/status -H "X-API-Key: admin-key")
  PHASE=$(echo "$STATUS" | python -c "import sys,json;print(json.load(sys.stdin)['result']['phase'])")
  echo "当前阶段: $PHASE"
  if [ "$PHASE" = "building_complete" ]; then break; fi
  sleep 30
done

# 5. 切换读取
curl -X POST http://localhost:1933/api/v1/migration/switch -H "X-API-Key: admin-key"

# 6. 关闭双写（观察稳定后）
curl -X POST http://localhost:1933/api/v1/migration/disable-dual-write -H "X-API-Key: admin-key"

# 7. 完成迁移
curl -X POST http://localhost:1933/api/v1/migration/finish \
  -H "Content-Type: application/json" -H "X-API-Key: admin-key" \
  -d '{"confirm_cleanup": true}'
```

## 回滚操作

### 非破坏性回滚（switched → dual_write）

如果切换到新模型后发现效果不理想，可以从 `switched` 阶段安全回退：

```bash
curl -X POST http://localhost:1933/api/v1/migration/rollback \
  -H "X-API-Key: your-admin-key"
```

此操作：
- 读取切回源集合
- 双写保持开启
- 目标集合**不删除**（数据保留，可再次 `/build` 后重新切换）

### 破坏性终止（任意阶段 → idle）

如果决定放弃此次迁移，在任意阶段均可终止：

```bash
curl -X POST http://localhost:1933/api/v1/migration/abort \
  -H "X-API-Key: your-admin-key"
```

此操作根据当前阶段执行不同清理：
- `dual_write`：关闭双写、删除目标集合
- `building`：取消重建、关闭双写、删除目标集合、清理队列
- `building_complete`：关闭双写、删除目标集合、清理队列
- `switched` / `dual_write_off`：关闭双写、删除目标集合

> ⚠️ **注意**：`abort` 是破坏性操作，会删除目标集合及其中的所有数据。

## 崩溃恢复

迁移过程中如果服务崩溃重启，系统会自动恢复：

| 崩溃时阶段 | 恢复行为 |
|-----------|---------|
| `dual_write` | 自动重建双写适配器，继续双写 |
| `building` | 自动重建双写适配器 + 恢复重建引擎（从断点继续） |
| `building_complete` | 保持该状态，等待运维确认切换 |
| `switched` | 自动恢复，读取从目标集合获取 |
| `dual_write_off` | 自动恢复，仅目标集合接收写入 |
| `completed` | 自动清理运行时状态，回到 idle |

重启后通过 `/status` 查看当前状态。

## 状态文件

迁移过程中涉及两个状态文件：

| 文件 | 路径 | 用途 | 生命周期 |
|------|------|------|---------|
| 运行时状态 | `{workspace}/.migration/state/migration_runtime_state.json` | 迁移进度和临时状态 | 迁移完成后删除 |
| 迁移历史 | `{config_dir}/embedding_migration_state.json` | 当前活跃配置和迁移历史 | **永久保留** |

迁移历史文件格式：

```json
{
  "version": 1,
  "current_active": "v2",
  "history": [
    {
      "id": "mig_a1b2c3d4e5f6",
      "from_name": "v1",
      "to_name": "v2",
      "status": "completed"
    }
  ]
}
```

## 状态机总览

```
                    ┌─────────────── 正向流程 ───────────────┐
                    │                                        │
idle ──(start)──→ dual_write ──(build)──→ building ──→ building_complete
                        │                │           │
                        └──(abort)───────┘           │
                                                     │
                    building_complete ──(switch)──→ switched ──(disable-dw)──→ dual_write_off ──(finish)──→ completed → idle
                                                     │                              │
                                                     └──(rollback)──→ dual_write    └──(abort)──→ idle
```

## API 参考

| 端点 | 方法 | 说明 | 鉴权 |
|------|------|------|------|
| `/api/v1/migration/targets` | GET | 列出可用的迁移目标 | 已认证 |
| `/api/v1/migration/status` | GET | 获取当前迁移状态和进度 | 已认证 |
| `/api/v1/migration/start` | POST | 启动迁移（进入双写） | admin/root |
| `/api/v1/migration/build` | POST | 开始后台重建索引 | admin/root |
| `/api/v1/migration/switch` | POST | 切换读取到目标模型 | admin/root |
| `/api/v1/migration/disable-dual-write` | POST | 关闭双写 | admin/root |
| `/api/v1/migration/finish` | POST | 完成迁移 | admin/root |
| `/api/v1/migration/abort` | POST | 终止迁移（破坏性） | admin/root |
| `/api/v1/migration/rollback` | POST | 回滚到双写阶段（非破坏性） | admin/root |

## 常见问题

### Q: 重建过程中能否正常读写？

可以。重建期间双写已开启，新写入的 URI 通过双写进入目标集合。reindex 重复处理已有 URI 仅导致幂等 upsert，无数据风险。

### Q: 重建需要多长时间？

取决于数据量和嵌入 API 的并发限制。可以通过 `/status` 接口实时监控进度。大集合支持多次 `/build`，每次仅处理缺失和新增的 URI。

### Q: 可以跳过某个阶段吗？

不可以。迁移流程遵循严格的状态机，每个阶段有明确的前置条件。跳过阶段会触发 409 Conflict 错误。

### Q: 关闭双写后还能回滚吗？

不能。关闭双写后源集合已停止接收写入，回滚需要追赶期间的增量数据，复杂度不值得。如需回退，请在 `switched` 阶段使用 `/rollback`。

### Q: 如何判断目标集合是否健康？

观察 `/status` 返回的 `degraded_write_failures` 字段。该计数器记录双写 standby 侧的写入失败次数。如果该值持续增长，说明目标集合可能存在问题，应考虑 `/abort` 重新开始。
