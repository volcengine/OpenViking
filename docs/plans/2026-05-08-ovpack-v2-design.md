# OVPack V2 设计

## 目标

OVPack 用于在不同 OpenViking 实例之间迁移内容子树，同时避免携带过期 embedding，也避免包内内容写入受保护命名空间。文件格式仍然保持为 ZIP，但 v2 增加了显式 manifest，让导入和导出行为可预测、可校验。

## 格式

- `format_version` 为 `2`。
- ZIP 继续保留现有根目录布局，以兼容旧的解包和查看方式。
- 根目录包含 `._ovpack_manifest.json`，在 ZIP 内按现有隐藏文件转义规则存储为 `_._ovpack_manifest.json`。
- manifest 记录包类型、根元数据、内容条目，以及可迁移的向量标量记录。
- embedding 向量和记录 ID 不会被导出。导入后会在目标环境重新向量化。

## 导入策略

- 导入和导出仅允许 `resources`、`user`、`agent` scope。
- 拒绝 `session`、内部 scope，以及 `viking://` 根 URI。
- `.abstract.md`、`.overview.md`、`.relations.json`、manifest 文件等派生语义文件不会作为普通内容导入。
- 所有 ZIP 成员路径会在任何写入前完成校验，因此不安全或不允许的成员不会造成“前半部分已写入、后半部分失败”的部分导入。

## 冲突策略

`on_conflict` 是标准冲突选项：

- `fail`：默认行为；目标 root 已存在时抛错。
- `overwrite`：先删除已有 root，包括对应向量索引记录，再执行导入。
- `skip`：直接返回已有 root URI，不写入、不向量化。

## 向量元数据

导出时从向量库读取可迁移的标量字段，并写入 manifest。目录语义文本也会写入 manifest，因此导入时可以重建 L0/L1 记录，而不需要把派生语义文件作为用户内容导入。导入时，标量字段会作为 override 合并到新的 embedding message 中；account 和 owner 字段会根据目标上下文重新生成，不从包内继承。
