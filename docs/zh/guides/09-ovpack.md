# OVPack 导入导出

OVPack 是 OpenViking 的打包格式，用于导出/导入任意上下文子树（例如资源、记忆），以便备份、迁移和分享。

## 快速开始

### 导出资源

将 OpenViking 中的资源导出为 `.ovpack` 文件。

**CLI**
```bash
openviking export viking://resources/my-project/ ./exports/my-project.ovpack
```

**Python SDK**
```python
from openviking import AsyncOpenViking

async def export_example():
    client = AsyncOpenViking()
    await client.initialize()
    try:
        exported_path = await client.export_ovpack(
            uri="viking://resources/my-project/",
            to="./exports/my-project.ovpack"
        )
        print(f"导出成功: {exported_path}")
    finally:
        await client.close()
```

**HTTP API**
```bash
curl -X POST http://localhost:1933/api/v1/pack/export \
  -H "Content-Type: application/json" \
  -H "X-API-Key: your-key" \
  -d '{
    "uri": "viking://resources/my-project/",
    "to": "./exports/my-project.ovpack"
  }'
```

### 导入资源

将 `.ovpack` 文件导入到 OpenViking 中。

**CLI**
```bash
# 基本导入
openviking import ./exports/my-project.ovpack viking://resources/imported/

# 显式冲突策略
openviking import ./exports/my-project.ovpack viking://resources/imported/ --on-conflict overwrite
```

**Python SDK**
```python
from openviking import AsyncOpenViking

async def import_example():
    client = AsyncOpenViking()
    await client.initialize()
    try:
        imported_uri = await client.import_ovpack(
            file_path="./exports/my-project.ovpack",
            parent="viking://resources/imported/",
            on_conflict="overwrite"
        )
        print(f"导入成功: {imported_uri}")
        await client.wait_processed()
    finally:
        await client.close()
```

**HTTP API**
```bash
# 第一步：上传本地 ovpack 文件
# 默认使用本地临时存储。
# 只有在明确需要分布式共享临时上传时，才额外传：-F "upload_mode=shared"
# Python HTTP client / CLI 也可以改为在 ovcli.conf 中设置：upload.mode = "shared"
TEMP_FILE_ID=$(
  curl -sS -X POST http://localhost:1933/api/v1/resources/temp_upload \
    -H "X-API-Key: your-key" \
    -F 'file=@./exports/my-project.ovpack' \
  | jq -r '.result.temp_file_id'
)

# 第二步：使用 temp_file_id 导入
curl -X POST http://localhost:1933/api/v1/pack/import \
  -H "Content-Type: application/json" \
  -H "X-API-Key: your-key" \
  -d "{
    \"temp_file_id\": \"$TEMP_FILE_ID\",
    \"parent\": \"viking://resources/imported/\",
    \"on_conflict\": \"overwrite\"
  }"
```

## 格式说明

OVPack v2 仍然是标准 ZIP 文件。每个包会在
`<root>/_._ovpack_manifest.json` 中保存 OpenViking manifest；这是隐藏文件名
`.ovpack_manifest.json` 在 ZIP 内的转义形式。

manifest 会记录 `kind`、`format_version`、导出的 root、内容条目以及可迁移的向量
标量元数据。`entries` 里的 `path` 是相对导出 root 的路径；空字符串 `""` 表示
root 目录本身，例如 `my-project/`。

例如，从 `viking://resources/demo/` 导出的包可以包含下面这样的 manifest：

```json
{
  "kind": "openviking.ovpack",
  "format_version": 2,
  "root": {
    "name": "demo",
    "uri": "viking://resources/demo",
    "scope": "resources"
  },
  "entries": [
    {
      "path": "",
      "kind": "directory"
    },
    {
      "path": "notes.txt",
      "kind": "file",
      "size": 5,
      "sha256": "2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824"
    }
  ],
  "content_sha256": "b2a6e9582119c7510d68e3446de3e71a486934bf450d68f65596259ed1cf7997",
  "vectors": {}
}
```

对于带 manifest entries 的包，导入会在写入资源前校验 ZIP 内文件集合、每个文件的
`size`、每个文件的 `sha256`，以及整体 `content_sha256`。缺少文件、混入额外文件、
或文件内容被修改，以及 v2 `content_sha256` 缺失/不匹配都会被拒绝。这个校验用于
确认包内容完整性；如果 manifest 和内容都可以被同时重写，它并不等价于签名或身份认证。

原始 embedding 向量不会被导出。`created_at`、`updated_at`、`active_count`
等运行态字段也不会被导出；导入后会在目标环境重新向量化并生成运行态状态。
旧版本没有 manifest 的 OVPack 仍可导入，但没有 manifest checksum 校验。带
manifest 的包会校验 `kind` 和 `format_version`，高于当前支持版本的包会被拒绝。
`.abstract.md`、`.overview.md`、`.relations.json` 等派生语义文件不会作为普通内容
导入。

## 记忆导入导出

OpenViking 的记忆会写入固定的目录结构中：

- 用户记忆：`viking://user/{user_space}/memories/`
- Agent 记忆：`viking://agent/{agent_id}/memories/` 或 `viking://agent/{agent_id}/user/{user_id}/memories/`

使用 OVPack 迁移记忆时，必须把 `.ovpack` 导入到对应 space 的父目录（而不是随便一个目录），否则会变成例如 `.../memories/memories/...` 的路径，OpenViking 将无法按“记忆”语义访问和使用这些文件。

### 导出/导入用户记忆（CLI）

```bash
# 导出：整个用户 memories 子树
openviking export viking://user/default/memories/ ./exports/user-memories.ovpack

# 导入：注意 parent 需要是 user space 根目录（导入后会生成 viking://user/default/memories/）
openviking import ./exports/user-memories.ovpack viking://user/default/ --on-conflict overwrite
```

### 导出/导入 Agent 记忆（CLI）

```bash
# isolate_agent_scope_by_user = false
openviking export viking://agent/default/memories/ ./exports/agent-memories.ovpack
openviking import ./exports/agent-memories.ovpack viking://agent/default/ --on-conflict overwrite

# isolate_agent_scope_by_user = true
openviking export viking://agent/default/user/alice/memories/ ./exports/agent-memories.ovpack
openviking import ./exports/agent-memories.ovpack viking://agent/default/user/alice/ --on-conflict overwrite
```

### 导出/导入记忆（Python SDK）

```python
from openviking import AsyncOpenViking

async def export_import_user_memories():
    client = AsyncOpenViking()
    await client.initialize()
    try:
        await client.export_ovpack(
            uri="viking://user/default/memories/",
            to="./exports/user-memories.ovpack",
        )

        await client.import_ovpack(
            file_path="./exports/user-memories.ovpack",
            parent="viking://user/default/",
            on_conflict="overwrite",
        )
    finally:
        await client.close()

async def export_import_agent_memories():
    client = AsyncOpenViking()
    await client.initialize()
    try:
        await client.export_ovpack(
            uri="viking://agent/default/memories/",
            to="./exports/agent-memories.ovpack",
        )
        await client.import_ovpack(
            file_path="./exports/agent-memories.ovpack",
            parent="viking://agent/default/",
            on_conflict="overwrite",
        )
    finally:
        await client.close()
```

### 导出/导入记忆（HTTP API）

```bash
# 导出用户记忆
curl -X POST http://localhost:1933/api/v1/pack/export \
  -H "Content-Type: application/json" \
  -H "X-API-Key: your-key" \
  -d '{
    "uri": "viking://user/default/memories/",
    "to": "./exports/user-memories.ovpack"
  }'

# 导入用户记忆（先上传，再用 temp_file_id 导入）
TEMP_FILE_ID=$(
  curl -sS -X POST http://localhost:1933/api/v1/resources/temp_upload \
    -H "X-API-Key: your-key" \
    -F 'file=@./exports/user-memories.ovpack' \
  | jq -r '.result.temp_file_id'
)
curl -X POST http://localhost:1933/api/v1/pack/import \
  -H "Content-Type: application/json" \
  -H "X-API-Key: your-key" \
  -d "{
    \"temp_file_id\": \"$TEMP_FILE_ID\",
    \"parent\": \"viking://user/default/\",
    \"on_conflict\": \"overwrite\"
  }"
```

### 导入后是否向量化

导入后会在目标环境重新向量化，便于 `find/search` 检索。OVPack 不再提供关闭向量化的导入参数。

## 使用场景

### 资源备份
```bash
DATE=$(date +%Y%m%d)
openviking export viking://resources/ ./backups/backup_${DATE}.ovpack
```

### 资源迁移
```bash
# 机器 A 导出
openviking export viking://resources/my-project/ ./migration.ovpack

# 机器 B 导入
openviking import ./migration.ovpack viking://resources/ --on-conflict overwrite
```

### 资源分享
```bash
# 导出
openviking export viking://resources/shared-docs/ ./shared-docs.ovpack

# 接收者导入
openviking import ./shared-docs.ovpack viking://resources/team-shared/
```

## 常见问题

**Q: OVPack 文件可以手动解压查看吗？**
A: 可以！OVPack 是标准的 ZIP 格式，可以用任何解压工具打开。

**Q: 大体积 OVPack 导入很慢怎么办？**
A: 当前导入会固定重建向量；如果导入耗时过长，建议拆分为更小的 OVPack 分批导入。

**Q: 导入时如何处理重名资源？**
A: 使用 `--on-conflict overwrite` 覆盖已有资源，或用 `--on-conflict skip` 保留已有资源。
