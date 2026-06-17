# RAGFS Cache-Aware Grep Traversal 设计

## 背景

`CachedFileSystem::grep()` 当前直接委托给被包装的 backend：

```rust
self.backend.grep(...).await
```

这样保留了 backend 的定制能力，但也意味着 cache wrapper 的 `read_dir()` 和 `read()` 缓存不会参与 grep。默认 `FileSystem::grep()` 本身会递归调用 `self.read_dir()` 和 `self.read()`；只有当 `self` 是 `CachedFileSystem` 时，grep 才能复用 cache 层。

这个差异对不同 backend 很重要：

- S3FS/MinIO override 了 `grep()`，先做 flat listing，再并发读取对象。这是冷缓存下的重要 fast path。
- `EncryptionWrappedFS::grep()` 必须在 wrapper 层做递归 traversal，再通过 `self.read()` 解密，不能委托到底层 ciphertext backend。
- `MultiWriteWrappedFS::grep()` 会过滤内部元数据，并补充 redirect file 的搜索语义。
- Python `glob()` 没有 Rust 独立接口，它复用 `VikingFS.tree()` 后在 Python 侧匹配 `rel_path`；因此 glob 已经可以通过 tree cached traversal 间接受益。

因此 grep cache 不能简单替换默认行为。它应该是显式 opt-in，并且默认保留 backend fast path。

## 目标

- 让 grep 在显式配置下可以复用 cache 层的目录 entries 缓存和文件内容缓存。
- 默认保持当前 backend grep 行为，避免影响 S3FS/MinIO 冷缓存性能。
- 让 tree、glob、grep 使用同一个 traversal strategy 配置，减少配置矩阵。
- 不缓存 grep 结果对象，只复用已有 `read_dir()` 和 `read()` 缓存。
- 保持 `recursive`、`case_insensitive`、`node_limit`、`exclude_path`、`level_limit` 的现有语义。

## 非目标

- 不设计 grep result cache。pattern、case、exclude、limit、level 等维度会让 key 和失效复杂化，第一版不做。
- 不改变 `find()` 或 semantic search 路径。
- 不改变 Python `glob()` 的实现方式。
- 不让 cache-aware grep 成为默认行为。
- 不为 multi-write 重写一套 cache-aware redirect grep 语义。

## 建议方案

把当前 tree 专用的 `CacheTreeMode` 泛化为 traversal 级别配置：

```rust
pub enum CacheTraversalMode {
    Backend,
    CachedTraversal,
}
```

`CachePolicy` 保存 `traversal_mode`，默认是 `Backend`。现有 tree 逻辑从 `tree_mode()` 迁移到 `traversal_mode()`。

### Backend Mode

`CachedFileSystem::tree_directory()` 和 `CachedFileSystem::grep()` 都继续委托给 backend：

```rust
self.backend.tree_directory(...).await
self.backend.grep(...).await
```

这个 mode 保留 S3FS flat listing、S3FS 并发 grep、multi-write redirect 处理、encryption wrapper 解密 grep，以及其他 backend override。

### CachedTraversal Mode

`CachedFileSystem::tree_directory()` 继续使用现有 `tree_directory_via_cache()`。

`CachedFileSystem::grep()` 新增 wrapper 私有 helper，例如 `grep_via_cache()`。它复刻默认 `FileSystem::grep()` 的逻辑，但递归过程中调用 cache wrapper 自己的方法：

- `self.stat()` 用于判断文件/目录，保持当前行为：stat 仍直接透传 backend。
- `self.read_dir()` 用于目录展开，因此可复用目录 entries 缓存。
- `self.read(path, 0, 0)` 用于文件内容读取，因此可复用完整文件缓存。
- regex 编译继续使用现有 `compile_grep_regex()`。
- 路径过滤继续使用现有 `normalize_prefix_path()`、`is_excluded_path()`、`relative_match_file()`、`relative_depth()`。

不向 provider 写入 grep result cache key。

## 数据流

Backend mode：

```text
VikingFS.grep()
  -> ragfs grep()
  -> CachedFileSystem::grep()
  -> backend.grep()
```

CachedTraversal mode：

```text
VikingFS.grep()
  -> ragfs grep()
  -> CachedFileSystem::grep()
  -> CachedFileSystem::grep_via_cache()
  -> stat() 判定节点类型
  -> read_dir() 展开目录，复用目录缓存
  -> read(path, 0, 0) 读取文件，复用文件缓存
  -> regex line match
```

Python `glob()` 不新增配置路径：

```text
VikingFS.glob()
  -> VikingFS.tree()
  -> ragfs tree_directory()
  -> traversal_mode 决定 backend tree 或 cached traversal
```

因此 tree、glob、grep 都由同一个 traversal mode 控制。

## 配置

建议使用一个统一配置项，而不是 grep/tree 分开配置：

```toml
[storage.cache]
enabled = true
traversal_mode = "backend"           # 默认值
# traversal_mode = "cached_traversal"
```

如果现有临时实现还没有暴露 `tree_mode` 到 `ov.conf`，则直接新增 `traversal_mode` 即可。如果已经暴露过 `tree_mode`，建议短期兼容读取旧字段，但内部统一映射到 `CacheTraversalMode`。

未知值应该在配置校验阶段报错。

## 安全规则

1. 默认 `Backend`。启用 cache 不应该静默改变 grep/tree 的后端执行路径。
2. multi-write 下强制 fallback 到 `Backend` traversal，避免绕过 `MultiWriteWrappedFS::grep()` 和 `tree_directory()` 的 redirect/metadata 语义。
3. encrypted multi-write 已经禁用 mount-level cache，保持现状。
4. 非 multi-write 的 encrypted mount 不能让 cached grep 对 ciphertext 做 regex。若 cache wrapper 位于 encryption wrapper 下方，则该 mount 必须 fallback 到 `Backend` traversal，继续使用 `EncryptionWrappedFS::grep()` 的明文语义；只有当 traversal 位于解密层之上时，才允许 cached grep。
5. provider 不可用时沿用 `read_dir()`/`read()` 的 fallback 行为。只要 backend 可读，cached traversal grep 不应因为 provider 故障失败。
6. 大目录保护继续生效：`entries.len() > max_cached_dir_entries` 时，grep 仍返回正确结果，但该目录 entries 不缓存。
7. `node_limit` 表示 match 数量上限，不是扫描文件数上限；cached grep 必须与默认 grep 保持一致。

## 性能预期

cached traversal grep 的收益主要来自 warm cache：

- 目录 entries 已缓存时，递归展开可以减少 backend `read_dir()` round trip。
- 文件内容已缓存时，重复 grep 可以减少 backend `read()` 或 object get。
- 对 S3FS/MinIO 冷缓存，cached traversal 可能比 backend grep 慢，因为它放弃了 flat listing + 并发读取的专用 fast path。
- 对单层超大目录，超过 `max_cached_dir_entries` 后目录 entries 不缓存，收益有限。
- 对 localfs，收益可能很小，因为本地读取成本低，而 cache provider 增加额外访问成本。

benchmark 必须报告：

- cache enabled/disabled
- `traversal_mode`
- cold cache/warm cache
- backend 类型
- 数据集目录形态

## 测试

围绕 `CachedFileSystem` 增加聚焦测试：

- 默认 traversal mode 是 `Backend`，`grep()` 继续委托 backend。
- `CachedTraversal` 下，第一次 grep 会调用 backend `read_dir()`/`read()` 并填充缓存。
- 目录和文件预热后，第二次 grep 命中 cache，避免重复 backend `read_dir()`/`read()`。
- `recursive=false`、`case_insensitive`、`node_limit`、`exclude_path`、`level_limit` 语义与默认 `FileSystem::grep()` 一致。
- oversized directory 仍能 grep 出正确结果，但目录 entries 不缓存。
- provider unavailable 时 fallback 到 backend，grep 仍返回正确结果。
- multi-write wrapper 即使配置 `CachedTraversal`，grep/tree 也 fallback 到 backend。

如有配置解析层测试，还应覆盖：

- 缺省 `traversal_mode` 等价于 `backend`。
- `cached_traversal` 能正确映射到 `CacheTraversalMode::CachedTraversal`。
- 未知值报错。

## 推进步骤

1. 将 `CacheTreeMode` 重命名或兼容迁移为 `CacheTraversalMode`。
2. 将 `CachePolicy::tree_mode()` 替换为 `traversal_mode()`，默认仍是 `Backend`。
3. 调整 tree 逻辑使用统一 traversal mode。
4. 为 `CachedFileSystem::grep()` 增加 cached traversal 分支和私有 helper。
5. 保留 multi-write fallback 到 backend traversal。
6. 增加 grep cached traversal 单测和配置解析测试。
7. 更新 cache 文档或 benchmark 说明，明确 grep 只有在 `cached_traversal` 下才可能复用 cache。
