# OVPack 资源导入导出

OVPack 是 OpenViking 的资源打包格式，用于资源备份、迁移和分享。

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

# 强制覆盖
openviking import ./exports/my-project.ovpack viking://resources/imported/ --force

# 跳过向量化（更快）
openviking import ./exports/my-project.ovpack viking://resources/imported/ --no-vectorize
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
            force=True,
            vectorize=True
        )
        print(f"导入成功: {imported_uri}")
        await client.wait_processed()
    finally:
        await client.close()
```

**HTTP API**
```bash
# 第一步：上传本地 ovpack 文件
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
    \"force\": true,
    \"vectorize\": true
  }"
```

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
openviking import ./migration.ovpack viking://resources/ --force
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
A: 使用 `--no-vectorize` 先快速导入，之后再统一向量化。

**Q: 导入时如何处理重名资源？**
A: 使用 `--force` 参数覆盖已存在的资源。
