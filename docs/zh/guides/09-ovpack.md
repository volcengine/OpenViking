# OVPack 导入导出

OVPack 是 OpenViking 的可恢复内容包格式，用来迁移或备份 `viking://` 下的公开内容树。
它保存文件内容、语义侧边文件、可迁移的索引标量，以及可选的 dense 向量快照。

OVPack 不是裸 ZIP 拷贝，也不是可信发布格式。导入会校验 manifest、文件列表、目录列表和
checksum，保证包内容没有偏离 manifest；如果攻击者能同时篡改文件和 manifest，仍需要依赖
外部签名、传输安全和访问控制。

## 支持范围

普通 `export/import` 处理一个包根：

- `viking://resources/...`
- `viking://user/...`
- `viking://agent/...`
- `viking://session/...`

全量迁移使用单独的 `backup/restore`，它会把公开 scope root 一起打进备份包：

- `viking://resources`
- `viking://user`
- `viking://agent`
- `viking://session`

`temp`、`queue`、`upload`、锁文件、watch control 文件、`.relations.json` 等内部或运行态数据
不属于 OVPack 迁移范围。

## 快速开始

### 导出和导入资源目录

```bash
ov export viking://resources/my-project ./exports/my-project.ovpack
ov import ./exports/my-project.ovpack viking://resources/imported/
```

导入参数是目标父目录，不是最终 root。假设包根名是 `my-project`，上面的导入结果是：

```text
viking://resources/imported/my-project
```

覆盖已有 root：

```bash
ov import ./exports/my-project.ovpack viking://resources/imported/ --on-conflict overwrite
```

### 导出向量快照

默认导出不保存 dense 向量，导入后由目标环境重新向量化：

```bash
ov export viking://resources/my-project ./exports/my-project.ovpack
ov import ./exports/my-project.ovpack viking://resources/imported/
```

如果确认导出环境和导入环境使用同一 embedding 配置，可以显式导出 dense 向量快照：

```bash
ov export viking://resources/my-project ./exports/my-project.ovpack --include-vectors
ov import ./exports/my-project.ovpack viking://resources/imported/ --vector-mode auto
```

`--vector-mode` 控制导入时如何处理包内向量：

| 值 | 行为 |
| --- | --- |
| `auto` | 默认值。若包内有 dense 快照且 embedding 元数据兼容，则直接恢复；否则重新向量化。 |
| `recompute` | 忽略包内 dense 快照，始终重新向量化。 |
| `require` | 必须恢复兼容 dense 快照；没有快照、快照不完整、模型或维度不兼容都会报错。 |

兼容性校验会比较包内记录的 embedding provider、model、input、query/document 参数和维度。
当前 OVPack 向量快照只支持纯 dense 索引；如果底层向量索引的 `VectorIndex.IndexType` 是 hybrid，`--include-vectors` 会直接拒绝导出。导入到 hybrid index 环境时，`auto` 会重新向量化，`require` 会报错。

导出 dense 向量快照前，OpenViking 会先做数据一致性检查。也就是检查导出范围内按系统规则
应该进入向量索引的内容，是否已经有对应索引记录。缺失时会拒绝导出，避免生成不完整的
迁移包。

可以单独调用一致性检查来调试当前数据状态：

```bash
ov system consistency viking://resources/my-project
```

接口只返回摘要和最多 20 条缺失记录，不返回完整 expected 列表。`--include-vectors`
导出失败时，错误 details 只携带 1 条缺失 key，避免错误日志过大。

Python SDK：

```python
report = await client.check_consistency("viking://resources/my-project")
print(report["ok"], report["missing_records"])
```

HTTP API：

```bash
curl -X POST http://localhost:1933/api/v1/system/consistency \
  -H "Content-Type: application/json" \
  -H "X-API-Key: your-admin-key" \
  -d '{"uri":"viking://resources/my-project"}'
```

### 全量备份和恢复

全量迁移不要用 `export viking://`；使用专门的备份包：

```bash
ov backup ./backups/openviking.ovpack
ov restore ./backups/openviking.ovpack --on-conflict overwrite
```

备份包只能通过 `restore` 恢复，不能通过普通 `import` 导入到任意父目录。

## Python SDK

```python
from openviking import AsyncOpenViking


async def migrate_project():
    client = AsyncOpenViking()
    await client.initialize()
    try:
        await client.export_ovpack(
            uri="viking://resources/my-project",
            to="./exports/my-project.ovpack",
            include_vectors=False,
        )

        imported_uri = await client.import_ovpack(
            file_path="./exports/my-project.ovpack",
            parent="viking://resources/imported/",
            on_conflict="overwrite",
            vector_mode="auto",
        )
        print(imported_uri)
        await client.wait_processed()
    finally:
        await client.close()
```

全量备份：

```python
await client.backup_ovpack("./backups/openviking.ovpack", include_vectors=True)
await client.restore_ovpack(
    "./backups/openviking.ovpack",
    on_conflict="overwrite",
    vector_mode="auto",
)
```

## HTTP API

HTTP 导出接口直接返回文件流；HTTP 导入和恢复必须先上传本地 `.ovpack`，再用
`temp_file_id` 调用 pack 接口。

导出：

```bash
curl -X POST http://localhost:1933/api/v1/pack/export \
  -H "Content-Type: application/json" \
  -H "X-API-Key: your-admin-key" \
  -d '{"uri":"viking://resources/my-project","include_vectors":false}' \
  --output my-project.ovpack
```

导入：

```bash
TEMP_FILE_ID=$(
  curl -sS -X POST http://localhost:1933/api/v1/resources/temp_upload \
    -H "X-API-Key: your-admin-key" \
    -F "file=@./exports/my-project.ovpack" \
  | jq -r ".result.temp_file_id"
)

curl -X POST http://localhost:1933/api/v1/pack/import \
  -H "Content-Type: application/json" \
  -H "X-API-Key: your-admin-key" \
  -d "{
    \"temp_file_id\": \"$TEMP_FILE_ID\",
    \"parent\": \"viking://resources/imported/\",
    \"on_conflict\": \"overwrite\",
    \"vector_mode\": \"auto\"
  }"
```

全量备份：

```bash
curl -X POST http://localhost:1933/api/v1/pack/backup \
  -H "Content-Type: application/json" \
  -H "X-API-Key: your-admin-key" \
  -d '{"include_vectors":true}' \
  --output openviking-backup.ovpack
```

## 冲突策略

`on_conflict` 只在导入 root 已存在时生效。

| 值 | 行为 |
| --- | --- |
| `fail` | 默认值。目标 root 已存在时返回 `409 CONFLICT`。 |
| `overwrite` | 删除已有 root，再写入包内容和重建索引。 |
| `skip` | 目标 root 已存在时直接返回该 URI，不写入任何包内容。 |

`skip` 是 root 级跳过，不是文件级补齐导入。

## 包结构

OVPack v2 是标准 ZIP。ZIP 内部有一个包根目录：

```text
my-project/
my-project/files/
my-project/files/notes.txt
my-project/files/.abstract.md
my-project/files/.overview.md
my-project/_ovpack/
my-project/_ovpack/index_records.jsonl
my-project/_ovpack/dense.f32                # 仅 --include-vectors 且存在可导出向量时出现
my-project/_ovpack/manifest.json
```

`files/` 下保存用户内容，路径与 OpenViking 中的相对路径完全一致，不再对点文件做 `_._` 转义。
`_ovpack/` 下保存 OVPack 内部文件，不参与用户内容导入。

manifest 只保存包结构、文件 checksum 和内部索引文件的 checksum，不直接内嵌每个文件的索引记录：

```json
{
  "kind": "openviking.ovpack",
  "format_version": 2,
  "root": {
    "name": "my-project",
    "uri": "viking://resources/my-project",
    "scope": "resources"
  },
  "entries": [
    {"path": "", "kind": "directory"},
    {
      "path": "notes.txt",
      "kind": "file",
      "size": 5,
      "sha256": "2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824"
    }
  ],
  "content_sha256": "b2a6e9582119c7510d68e3446de3e71a486934bf450d68f65596259ed1cf7997",
  "index": {
    "records": {
      "path": "_ovpack/index_records.jsonl",
      "count": 2,
      "sha256": "..."
    },
    "dense": {
      "path": "_ovpack/dense.f32",
      "count": 1,
      "dtype": "float32",
      "byte_order": "little",
      "dimensions": 1024,
      "sha256": "...",
      "embedding": {
        "provider": "volcengine",
        "model": "doubao-embedding-vision-251215",
        "input": "multimodal",
        "dimensions": 1024
      }
    }
  }
}
```

`entries[].path == ""` 表示包根目录本身。多层目录使用相对路径，例如：

```json
[
  {"path": "", "kind": "directory"},
  {"path": "docs", "kind": "directory"},
  {"path": "docs/a.md", "kind": "file", "size": 12, "sha256": "..."}
]
```

`index_records.jsonl` 一行一条索引记录，覆盖文件、目录、根目录：

```jsonl
{"record_id":"r000001","path":"","kind":"directory","level":0,"text":"root abstract","scalars":{"abstract":"root abstract","context_type":"resource","level":0}}
{"record_id":"r000002","path":"notes.txt","kind":"file","level":2,"scalars":{"abstract":"note summary","tags":"demo"},"vector":{"dense":{"offset":0,"dimensions":1024}}}
```

`dense.f32` 是连续 little-endian float32 数组。`index_records.jsonl` 中的 `vector.dense.offset`
是 float 数组偏移，不是字节偏移。示例中 `offset=0, dimensions=1024` 表示从第 0 个 float
开始读取 1024 个 float。

## 索引字段

默认会导出可迁移的索引标量：

```text
type, context_type, level, name, description, tags, abstract
```

这些字段不会从包里直接恢复，而是在目标环境重新生成：

```text
id, uri, account_id, owner_user_id, owner_agent_id, owner_space,
created_at, updated_at, active_count
```

如果使用 `--include-vectors`，会额外导出纯 dense 向量和 embedding 元数据。导入时即使恢复
dense 快照，也会按目标 URI、目标账号和当前时间重建运行态字段。hybrid index 当前不支持
向量快照导出。

## 导入校验

导入会先完整校验，再写入目标环境。核心校验包括：

1. ZIP 成员必须都在同一个包根下，不能包含绝对路径、反斜杠、盘符或 `..`。
2. 必须存在 `<root>/_ovpack/manifest.json`。
3. `kind` 必须是 `openviking.ovpack`，`format_version` 必须等于当前支持版本。
4. `root.name` 必须和 ZIP 根目录一致，`root.uri` 的末段也必须和 `root.name` 一致。
5. manifest 声明的文件集合、目录集合必须和 ZIP 内容一致，不能缺失也不能混入额外内容。
6. 每个文件的 `size` 和 `sha256` 必须匹配实际内容。
7. `content_sha256` 必须匹配按路径排序后的文件清单。
8. `_ovpack/index_records.jsonl` 和可选 `_ovpack/dense.f32` 必须匹配 manifest 中的 hash、数量和维度。
9. source scope 和 target scope 必须一致；`user`、`agent`、`session` 这类结构化 scope 还要求 root 层级一致。
10. 校验通过前不会写入包内容；冲突策略也在写入前处理。

典型拒绝示例：

```text
INVALID_ARGUMENT: Missing ovpack manifest
INVALID_ARGUMENT: ovpack file sha256 does not match manifest
INVALID_ARGUMENT: ovpack entries do not match manifest
INVALID_ARGUMENT: ovpack source scope does not match target scope
INVALID_ARGUMENT: ovpack package does not contain a dense vector snapshot
```

## 导入路径规则

普通子树包导入到同 scope 的父目录，并保留包根：

```bash
ov export viking://resources/a ./exports/a.ovpack
ov import ./exports/a.ovpack viking://resources/imported/
```

结果：

```text
viking://resources/imported/a
```

顶级 scope 包只能导入到 `viking://`：

```bash
ov export viking://resources ./exports/resources.ovpack
ov import ./exports/resources.ovpack viking:// --on-conflict overwrite
```

以下导入会被拒绝：

```bash
# resources 包不能导入 session
ov import ./exports/a.ovpack viking://session/

# session 子树不能导入 resources
ov import ./exports/sess_123.ovpack viking://resources/

# session 子树不能把自身路径当父目录，否则会变成 session/sess_123/sess_123
ov import ./exports/sess_123.ovpack viking://session/sess_123/
```

## 记忆和 Session

记忆目录有固定结构。导入时把包导入到对应目录的父目录，避免产生重复路径。

用户记忆：

```bash
ov export viking://user/default/memories ./exports/user-memories.ovpack
ov import ./exports/user-memories.ovpack viking://user/default/ --on-conflict overwrite
```

Agent 记忆：

```bash
ov export viking://agent/default/memories ./exports/agent-memories.ovpack
ov import ./exports/agent-memories.ovpack viking://agent/default/ --on-conflict overwrite
```

Session 只恢复文件状态，不触发向量化：

```bash
ov export viking://session/sess_123 ./exports/sess_123.ovpack
ov import ./exports/sess_123.ovpack viking://session/ --on-conflict overwrite
```

结果：

```text
viking://session/sess_123
```

## 旧包和未来版本

当前实现只接受 OVPack v2。旧版无 manifest 包没有文件集合、目录集合和 checksum 信息，无法判断
是否被删改或混入内容，因此默认拒绝。需要迁移旧包时，应先在可信旧环境中导入，再用当前版本
重新导出。

未来版本包也不会静默兼容。处理方式是升级 OpenViking，或在支持该版本的环境中重新导出为当前
支持格式。

## 常见错误

| 错误 | 常见原因 | 处理方式 |
| --- | --- | --- |
| `Missing ovpack manifest` | 旧版无 manifest 包 | 在可信环境重新导出为 v2。 |
| `Unsupported ovpack format_version` | 包格式版本不是当前支持版本 | 升级 OpenViking 或重新导出。 |
| `sha256 does not match manifest` | 文件或内部索引内容被改动 | 丢弃该包，或从可信源重新导出。 |
| `ovpack entries do not match manifest` | ZIP 中缺文件/目录，或混入额外文件/目录 | 丢弃该包，或重新导出。 |
| `source scope does not match target scope` | 跨 scope 导入，例如 session 导入 resources | 导入到同 scope 的父目录。 |
| `source path is incompatible with target path` | 结构化 scope 的 root 层级会改变 | 导入到正确系统父目录。 |
| `Top-level scope ovpack packages must be imported to viking://` | 将顶级 scope 包导入了非根父目录 | 改为导入 `viking://`。 |
| `Backup ovpack packages must be restored` | 用普通 import 导入 backup 包 | 使用 `ov restore`。 |
| `Resource already exists` | 目标 root 已存在 | 使用 `--on-conflict overwrite` 或 `--on-conflict skip`。 |
| `incomplete OpenViking vector index snapshot` | 使用 `--include-vectors` 时，导出范围内应索引内容缺少索引记录 | 先执行 `ov system consistency <uri>` 定位问题，再等待处理完成或重新 reindex。 |
| `dense vector snapshot is incompatible` | 包内 embedding 元数据和当前配置不一致 | 用 `--vector-mode recompute`，或换到兼容配置。 |

## 常见问题

**OVPack 可以手动解压查看吗？**

可以。OVPack 是 ZIP 文件，可以用普通解压工具查看。不要手动修改后再导入，修改会破坏
manifest 校验；如果同时修改 manifest 和内容，则需要依赖外部签名和可信来源判断。

**为什么不默认导出向量？**

向量只在 embedding 模型、输入模式、参数和维度完全兼容时才可直接复用。默认重新向量化更稳；
需要冷迁移加速时，再显式使用 `--include-vectors` 和 `--vector-mode auto/require`。

**大包导入很慢怎么办？**

默认导入会重建目标环境的语义和向量。大包迁移可以使用 `--include-vectors` 减少重算，或按目录
拆成多个 OVPack 分批导入。
