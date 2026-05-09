# OVPack 导入导出

OVPack 是 OpenViking 的上下文打包格式，用来把一个 `viking://` 子树导出成
`.ovpack` 文件，再导入到另一个 OpenViking 环境。它适合备份、迁移、分享资源或记忆
目录；它不是长期归档格式，也不是带签名的安全发布格式。

## 适用边界

OVPack 可以处理这些公开作用域：

- `viking://resources/...`
- `viking://user/...`
- `viking://agent/...`
- `viking://session/...`

OVPack 支持导出公开 scope root，例如 `viking://resources/`、`viking://user/`、
`viking://agent/`、`viking://session/`；这类顶级 scope 包只能导入到 `viking://`。
全量备份使用单独的 `backup` / `restore` 接口，自动包含这些公开 scope root。
`temp`、`queue` 等内部作用域不属于 OVPack 迁移范围。

`.abstract.md` 和 `.overview.md` 会作为语义侧边文件随包恢复；`.relations.json`、锁文件、
manifest 等内部文件不会进入包。导入后 OpenViking 会在目标环境重新生成语义和向量；session
包只恢复 session 文件状态，不触发向量化。导入接口不再提供 `vectorize` 或 `force` 参数；
冲突处理统一使用 `on_conflict`。OVPack 本身不再设置额外的包大小、文件数量或目录深度上限；
实际可处理规模由 ZIP、存储后端和运行环境决定。

## 快速开始

### CLI

```bash
# 导出资源目录
ov export viking://resources/my-project/ ./exports/my-project.ovpack

# 导入到目标父目录
ov import ./exports/my-project.ovpack viking://resources/imported/

# 目标 root 已存在时覆盖
ov import ./exports/my-project.ovpack viking://resources/imported/ --on-conflict overwrite
```

导入的第二个参数是“父目录”，不是最终 root。假设包内 root 名是 `my-project`：

```text
ov import ./exports/my-project.ovpack viking://resources/imported/
```

导入结果是：

```text
viking://resources/imported/my-project
```

### Python SDK

```python
from openviking import AsyncOpenViking


async def export_and_import():
    client = AsyncOpenViking()
    await client.initialize()
    try:
        await client.export_ovpack(
            uri="viking://resources/my-project/",
            to="./exports/my-project.ovpack",
        )

        imported_uri = await client.import_ovpack(
            file_path="./exports/my-project.ovpack",
            parent="viking://resources/imported/",
            on_conflict="overwrite",
        )
        print(imported_uri)

        # 导入会触发目标环境重新向量化；需要检索前可等待后台任务完成。
        await client.wait_processed()
    finally:
        await client.close()
```

### HTTP API

HTTP 导出接口直接返回文件流；HTTP 导入必须先上传本地 `.ovpack` 文件，再用
`temp_file_id` 导入。

```bash
# 导出
curl -X POST http://localhost:1933/api/v1/pack/export \
  -H "Content-Type: application/json" \
  -H "X-API-Key: your-admin-key" \
  -d '{"uri": "viking://resources/my-project/"}' \
  --output my-project.ovpack
```

```bash
# 第一步：上传本地 ovpack 文件
TEMP_FILE_ID=$(
  curl -sS -X POST http://localhost:1933/api/v1/resources/temp_upload \
    -H "X-API-Key: your-admin-key" \
    -F "file=@./exports/my-project.ovpack" \
  | jq -r ".result.temp_file_id"
)

# 第二步：导入
curl -X POST http://localhost:1933/api/v1/pack/import \
  -H "Content-Type: application/json" \
  -H "X-API-Key: your-admin-key" \
  -d "{
    \"temp_file_id\": \"$TEMP_FILE_ID\",
    \"parent\": \"viking://resources/imported/\",
    \"on_conflict\": \"overwrite\"
  }"
```

HTTP 接口不接受本机路径字段，例如 `file_path`、`temp_path`。本地文件读取和上传由 CLI 或
SDK 客户端完成，服务端只消费 `temp_file_id`。

## 冲突策略

`on_conflict` 只在导入 root 已存在时生效。

| 值 | 行为 |
| --- | --- |
| `fail` | 默认值。目标 root 已存在时返回 `409 CONFLICT`。 |
| `overwrite` | 删除已有 root，再写入包内容。 |
| `skip` | 保留已有 root，直接返回该 URI，不写入包内容。 |

`skip` 是 root 级跳过，不是文件级补齐导入：只要目标 root 已存在，就不会写入包内缺失文件。

示例错误：

```json
{
  "status": "error",
  "error": {
    "code": "CONFLICT",
    "message": "Resource already exists at viking://resources/imported/my-project. Use on_conflict='overwrite' to replace it.",
    "details": {
      "resource": "viking://resources/imported/my-project"
    }
  }
}
```

## 包结构

OVPack v2 是标准 ZIP 文件。ZIP 内部以一个 root 目录包住所有内容，并包含一个 manifest：

```text
my-project/
my-project/notes.txt
my-project/_._overview.md
my-project/_._ovpack_manifest.json
```

`_._ovpack_manifest.json` 是 `.ovpack_manifest.json` 在 ZIP 内的转义名称。OpenViking 会把
普通内容里的点文件名也按同一规则转义，例如 `.overview.md` 在 ZIP 内是 `_._overview.md`。

一个最小 manifest 示例：

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
  "vectors": {
    "": [
      {
        "level": 0,
        "text": "Project summary",
        "scalars": {
          "context_type": "resource",
          "level": 0,
          "abstract": "Project summary"
        }
      }
    ]
  }
}
```

字段说明：

- `kind` 固定为 `openviking.ovpack`。
- `format_version` 当前为 `2`。低于或高于当前支持版本的包都会被拒绝。
- `root.name` 是导入时创建的 root 名称。
- `entries[].path` 是相对 root 的路径。`""` 表示 root 目录本身。
- 文件条目包含 `size` 和 `sha256`。
- `content_sha256` 覆盖按路径排序后的文件列表，元素只包含 `path`、`size`、`sha256`。
- `vectors` 只保存可迁移的标量和目录摘要文本，不保存原始 embedding 向量。
- 包内如果带有 `context_type`，只作为导出时的标量信息；导入校验不依赖它，最终
  `context_type` 以目标路径推导结果为准。

当前会导出的向量标量字段是：

```text
type, context_type, level, name, description, tags, abstract
```

这些运行态字段不会导出，会在目标环境重新生成：

```text
created_at, updated_at, active_count
```

## 导入校验

导入会先校验包，再写入目标目录。核心规则如下：

1. ZIP 路径必须在同一个 root 下，不能包含绝对路径、反斜杠、盘符或 `..`。
2. 必须存在 `<root>/_._ovpack_manifest.json`。
3. manifest 必须是合法 JSON，`kind` 和 `format_version` 必须可识别。
4. `entries` 中声明的文件和目录集合必须和 ZIP 内容一致。
5. 每个文件的 `size` 和 `sha256` 必须匹配实际内容。
6. v2 包必须带 `content_sha256`，并且整体 checksum 必须匹配。
7. manifest root 的 source scope 必须和最终导入 root 的 target scope 一致。
8. `user`、`agent`、`session` 这类结构化 scope 不能通过导入改变 root 层级。
9. `.relations.json`、manifest、锁文件等内部文件不会作为普通内容导入。

如果旧包没有 manifest，会被拒绝：

```text
INVALID_ARGUMENT: Missing ovpack manifest
```

如果内容被改动，例如 manifest 里声明 `notes.txt` 的 sha256 是 `hello`，但 ZIP 里实际内容
变成 `jello`，会被拒绝：

```text
INVALID_ARGUMENT: ovpack file sha256 does not match manifest
```

这个校验保证的是“包内容没有偏离 manifest”。如果攻击者能同时改 manifest 和文件内容，
它不能替代签名、可信发布链或访问控制。

如果包从 `viking://session/sess_123` 导出，却导入到 `viking://resources/`，会因为
source scope 和 target scope 不一致被拒绝。导入到 `viking://session/sess_123/` 也会
被拒绝，因为它会生成 `viking://session/sess_123/sess_123` 这种嵌套 root。

## 旧包和未来版本

默认导入只接受带 manifest 的 OVPack。旧版无 manifest 包没有文件集合和 checksum 信息，
无法判断是否混入或删改内容，因此默认拒绝。需要迁移旧包时，应先在可信环境中用旧版本导入，
再用当前版本重新导出。

开发期间生成过的 v2 预览包如果缺少 `content_sha256`，也会被拒绝。处理方式同样是重新导出。

如果 manifest 的 `format_version` 不是当前支持版本，会返回不支持的版本错误。低版本不会自动
兼容；处理方式是在可信环境中重新导出为当前支持格式。未来版本包则应升级 OpenViking，或由
支持该版本的环境重新导出成当前支持的格式。

## 记忆迁移

记忆目录有固定结构。导入时要把包导入到“对应目录的父目录”，避免产生重复路径。

### 用户记忆

```bash
# 导出整个用户 memories 子树
ov export viking://user/default/memories/ ./exports/user-memories.ovpack

# 导入到 user space 根目录，结果是 viking://user/default/memories/
ov import ./exports/user-memories.ovpack viking://user/default/ --on-conflict overwrite
```

不要导入到 `viking://user/default/memories/`，否则会得到：

```text
viking://user/default/memories/memories
```

### Agent 记忆

```bash
# isolate_agent_scope_by_user = false
ov export viking://agent/default/memories/ ./exports/agent-memories.ovpack
ov import ./exports/agent-memories.ovpack viking://agent/default/ --on-conflict overwrite

# isolate_agent_scope_by_user = true
ov export viking://agent/default/user/alice/memories/ ./exports/agent-memories.ovpack
ov import ./exports/agent-memories.ovpack viking://agent/default/user/alice/ --on-conflict overwrite
```

## Session 迁移

Session 包按备份恢复语义处理：恢复原 session id，不进行向量化。

```bash
ov export viking://session/sess_123/ ./exports/sess_123.ovpack
ov import ./exports/sess_123.ovpack viking://session/ --on-conflict overwrite
```

导入结果是：

```text
viking://session/sess_123/
```

如果导出整个 session scope：

```bash
ov export viking://session/ ./exports/session.ovpack
ov import ./exports/session.ovpack viking:// --on-conflict overwrite
```

## 常见场景

### 全量备份和迁移

全量迁移使用专门的备份包，而不是普通 `export/import` 的父目录语义：

```bash
ov backup ./backups/openviking.ovpack
ov restore ./backups/openviking.ovpack --on-conflict overwrite
```

备份包恢复到原始公开 scope root：`resources`、`user`、`agent`、`session`。
它不包含 `temp`、`queue`、锁文件、manifest、`.relations.json` 等内部或运行态数据。
非 session 内容恢复后重新向量化；session 只恢复文件状态。

### 备份资源

```bash
DATE=$(date +%Y%m%d)
ov export viking://resources/ ./backups/resources_${DATE}.ovpack

# 恢复整个 resources scope 时，父目录必须是 viking://
ov import ./backups/resources_${DATE}.ovpack viking:// --on-conflict overwrite
```

### 跨环境迁移

```bash
# 机器 A
ov export viking://resources/my-project/ ./migration.ovpack

# 机器 B
ov import ./migration.ovpack viking://resources/ --on-conflict overwrite
```

### 分享一组资源

```bash
ov export viking://resources/shared-docs/ ./shared-docs.ovpack
ov import ./shared-docs.ovpack viking://resources/team-shared/
```

## 常见错误

| 错误 | 常见原因 | 处理方式 |
| --- | --- | --- |
| `Missing ovpack manifest` | 旧版无 manifest 包 | 在可信环境重新导出。 |
| `Missing ovpack manifest content_sha256` | 开发期 v2 预览包缺少整体 checksum | 重新导出。 |
| `sha256 does not match manifest` | 文件内容和 manifest 不一致 | 丢弃该包，或从可信源重新导出。 |
| `ovpack entries do not match manifest` | ZIP 中缺文件/目录，或混入额外文件/目录 | 丢弃该包，或重新导出。 |
| `source scope does not match target scope` | 将包导入到了不同 scope，例如 session 导入 resources | 导入到同 scope 的父目录。 |
| `source path is incompatible with target path` | 结构化 scope 的导入会改变 root 层级，例如 session 导入具体 session 内部 | 导入到正确的系统父目录。 |
| `Top-level scope ovpack packages must be imported to viking://` | 将 `resources`/`user`/`agent`/`session` 顶级包导入了非根父目录 | 改为导入 `viking://`。 |
| `Unsupported ovpack format_version` | 包格式版本不是当前支持版本 | 升级 OpenViking 或重新导出为当前支持版本。 |
| `Resource already exists` | 目标 root 已存在 | 使用 `--on-conflict overwrite` 或 `--on-conflict skip`。 |

## 常见问题

**OVPack 可以手动解压查看吗？**

可以。OVPack 是 ZIP 文件，可以用普通解压工具查看。不要手动改包后再导入；修改内容会破坏
manifest 校验。

**大包导入很慢怎么办？**

导入会重建目标环境的语义和向量。大包迁移建议按目录拆成多个 OVPack 分批导入。

**为什么不兼容旧版无 manifest 包？**

旧包没有可验证的文件列表，也没有 checksum。默认兼容会让被删改或混入文件的包直接写入目标
环境，风险高于收益。
