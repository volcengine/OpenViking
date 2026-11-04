# 第 03 块：三文件存储、完整替换与发布恢复

本记录保留第 03 阶段结果。最终实现使用 generation/build_id 隔离的不可变来源与图、原生状态 CAS，并完成真实 MinIO 和 Redis 验收；详见 [集中交付记录](artifact-relations-delivery.md)。下文的固定三文件替换和待验收描述已被后续实现替代。

本块实现独立存储层，尚未接入 Compile、普通编辑、Worker 或查询路由。代码已覆盖三文件协议的正常路径、部分失败及协调读取；S3 实测和远程迟到写入的严格防护仍未完成，不能将本块报告为所有部署后端均已通过发布验收。

## 1. 用一个失败案例理解职责

旧图 A 已发布。构建 B 时，来源 B 整体替换成功，但图 B 替换失败：

- 状态仍为 `building/publishing`，协调读取返回完整旧图 A。
- 来源 B 与图 A 的构建 ID 不同；后续建图必须拒绝这一增量基线。
- 重试耗尽后保存 `failed`；存储层保留旧图，后续查询路由将按约定返回失败。
- 如果图 B 已替换，而 ready 保存失败，协调读取会检查状态与图的构建 ID、固定快照，确认发布后再持久化 ready。恢复写失败则继续报存储错误。

“文件完整”靠整体替换，“来源与图同一基线”靠构建 ID，“当前任务能否发布”靠状态与目标归属复核。三者分别处理不同问题。

## 2. 代码入口

新增 [artifact_relations_store.py](openviking/service/artifact_relations_store.py)，复用 `CompileStateStore` 的路径和 JSON 读取，不修改其已有写入行为。

| 方法 | 用途 |
| --- | --- |
| `merge_sources` | 合并一个成功 Compile 批次的来源记录，清空 `build_id`；解析器版本变化时丢弃旧版记录 |
| `request` | 持久化 queued 请求；执行中新普通版本只登记 pending，保留本轮固定快照 |
| `claim` | queued → computing，生成新构建 ID 并增加 attempts |
| `publish` | 复核任务归属，按 publishing → 来源 → 图 → ready 顺序整体替换 |
| `fail(retry=True)` | 未发布轮次退回 queued，保留固定快照、pending 和 attempts；下次认领使用新 ID |
| `fail` | 未发布轮次最终失败；已发布轮次先恢复 ready，不将其标记失败 |
| `get_publication` | 协调读取状态与图；必要时修复 ready；不读取来源或正文 |
| `get_baseline` | 在同一协调范围内读取状态、来源与旧图，供后续建图器判定增量基线 |

这些是内部方法，不新增 HTTP 接口。`get_publication` 返回状态和图，后续查询路由负责 failed、首次未就绪及实时 ACL 的对外处理。

Compile 的输出快照一旦由 `request` 登记，同一 operation 不能再合并批次来源，也不能改变固定快照。新 Compile 可以取代旧普通请求；旧 Worker 发布时将因归属或构建 ID 不匹配被拒绝。

## 3. 锁与整体替换

一次协调操作获取一个 PathLock batch：

```text
Exact：已有目标 state.json
Tree ：目标 artifact_graph/ 目录
```

Tree Lease 覆盖三份业务文件及本次临时对象。每次正式写入仍向底层传递 `lease_ref`，没有关闭自动锁检查。batch 获取避免按相反次序逐个取锁；后续业务层不能持有同一路径的锁后再嵌套调用这些方法。

只保留既定三份业务文件：

```text
artifact_graph/
  relation_sources.json
  artifact_relations.json
  artifact_relations_state.json
```

每次写入先完成唯一临时对象 `.replace-{UUID}.tmp`，再 `replace(temp, final)`。失败或取消后尝试清理本次临时对象。清理失败记录日志，不删除业务文件、不将已发布图判为失败。崩溃和清理失败可能留下临时对象；本块没有增加残留扫描器。

替换返回异常时，受协调锁重读正式对象并与完整候选值比较，确认“写已成功但响应丢失”。若无法确认则传播错误。取消不伪装成功；异步适配器等待底层 I/O 结束后传播取消，后续读取仍可恢复已完成的发布。

## 4. 底层补齐

| 位置 | 改动及原因 |
| --- | --- |
| `openviking/pyagfs/async_client.py`、`protocols.py` | 增加内部 `replace` 适配，保留账号检查、FsContext 与取消等待 |
| `crates/ragfs-python/src/lib.rs` | 暴露 `top.replace`，沿用现有 PathLock、路由及错误转换 |
| `core/encryption_wrapper.rs` | 同账号内转发完整密文替换；内容加密密钥按账号派生，不依赖文件名 |
| `core/multibackend_wrapper.rs` | 在主后端替换成功后登记目的文件 Write 与源文件 Remove 事件 |
| `plugins/s3fs/mod.rs` | Copy 成功即完成目的对象发布；Delete 临时源失败仍失效相关缓存并记录日志，不掩盖已发布结果 |

localfs 已有同文件系统原子替换，未修改其实现。缓存包装层正文读取直达后端；三文件协调读取不依赖目录列表或 stat 缓存。多后端转发已编译验证，本块没有验证备份端复制完成的链路。

## 5. 验证与边界

[存储故障测试](tests/service/test_artifact_relations_store.py) 覆盖批次并发合并、空 links 与缺失记录、发布各步骤失败、来源污染后的基线拒绝、ready 恢复、未知写结果确认、重试计数、固定 Compile 快照、pending、归属变化、构建 ID 变化及损坏记录。

[原生测试](tests/agfs/test_artifact_relations_store_binding.py) 使用隔离目录、两个独立 Rust binding 客户端和同一 localfs，分别验证明文与加密模式下的：

- 并发合并与跨实例读取；
- 完整图连续替换及仅保留三份业务 JSON；
- 已释放 Lease 的后续替换被拒绝，旧目的文件保留；
- 新 Lease 替换成功后另一客户端立即读到完整内容。

[异步适配测试](tests/agfs/test_async_client.py) 验证取消等待及账号隔离，并以受控延迟复现远程提交保护的缺口：旧请求在写前通过 Lease 校验，随后失去 Lease；新请求发布后，已发出的旧请求仍可完成无条件替换并覆盖新结果。这个诊断测试证明限制存在，不表示严格防护已经实现。

最终运行结果：

```bash
.venv/bin/python -m pytest \
  tests/service/test_artifact_relations_store.py \
  tests/agfs/test_artifact_relations_store_binding.py \
  tests/agfs/test_async_client.py -q --no-cov
# 36 passed；6 条已有依赖弃用警告

cargo test -p ragfs --lib test_localfs_replace_overwrites_existing_file
# 1 passed
```

本轮还运行过包含前两块模型、解析器及 Git binding 的专项回归，100 项通过；随后补充的 3 项协议/迟到请求测试及最终存储修改已由上面的 36 项检查覆盖，不将两次结果相加计算覆盖数量。

原生绑定构建使用：

```bash
cargo rustc -p ragfs-python --lib -- -C link-arg=-undefined -C link-arg=dynamic_lookup
```

普通 `cargo build -p ragfs-python` 在此 macOS 环境缺少扩展的动态符号链接参数，发生 Python 符号链接错误；使用上述参数后构建成功。源码无需为这个环境问题改动。

仓库全量 `cargo fmt --check` 存在大量已有格式差异，本块没有批量重排这些文件；新增 Python 文件通过 Ruff 检查和格式检查。

### 尚未完成的部署验收

1. 当前没有配置 `OV_GIT_S3_CONF` 或 `OPENVIKING_CONFIG_FILE`，未执行真实 S3/TOS 后端的替换、跨实例可见性及清理失败验证。
2. PathLock 写前校验不能提供远程提交点的严格 fencing。执行锁、构建 ID 和任务归属能拒绝旧请求的后续调用，但不能拦截已经发出的迟到请求。第 03/07 块这一验收项仍开放，生产接入前需要后端提交保护。
3. 目标删除重建的生命周期失效、执行锁、可靠通知、pending 消费与恢复扫描仍由第 07～09 块接入；本块没有将这些规则假装成完整 Worker。
4. 图节点、边、页面覆盖及增量与全量等价校验由后续建图器实现。此处只校验模型形状、状态与发布身份。

下一块是固定快照的目录结构读取与 diff 能力；上述部署验收限制继续保留，不因推进独立模块而视为完成。
