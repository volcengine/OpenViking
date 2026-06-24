# 多版本管理（快照）

OpenViking 在 VikingFS 之上提供了一套基于 Git 的多版本管理能力，称为**快照（Snapshot）**。它把某个账号（account）下的整棵资源树保存成一系列不可变的提交（commit），让你能够回溯历史、对比版本，并把工作区恢复到任意一个历史状态。

快照能力底层由内嵌在 Rust RAGFS 层的 [gitoxide](https://github.com/Byron/gitoxide) 驱动，按 `account_id` 维护一个逻辑 Git 仓库（每个账号一个仓库），对调用方完全透明——你无需关心 `.ovgit` 目录、对象库或引用细节。

四个核心命令：

| 命令 | 作用 |
|------|------|
| `commit` | 把当前工作区状态保存成一个新快照 |
| `log` | 从最新提交开始回溯历史 |
| `show` | 查看某个提交的元数据，或读取该提交中某个文件的内容 |
| `restore` | 把目录（或整棵账号树）恢复到某个历史快照的状态 |

## 核心概念

- **提交（commit）**：一个快照对应一个提交，由 40 位十六进制的 SHA-1 `commit_oid` 唯一标识。多数命令也接受 OID 的缩写前缀，或分支名（如 `main`）。
- **分支（branch）**：默认分支为 `main`。除非显式传入，所有命令都作用在 `main` 上。
- **正向恢复（forward-commit restore）**：`restore` **不会**回退或改写历史。它会读取 `source_commit` 的内容，把差异写回工作区，并在当前 HEAD 之上**生成一个新的提交**。因此新提交的父提交是恢复操作发生前的 HEAD，而**不是** `source_commit`。HEAD 始终单调向前推进，历史永远不会丢失。
- **作用范围**：`commit` 可以通过 `paths` 限定只快照部分 URI；`restore` 可以通过 `project_dir` 限定只恢复某个子目录，目录之外的文件保持不变。

## API 实现介绍

- HTTP 路由：[snapshot.py](file:///cloudide/workspace/OpenViking/openviking/server/routers/snapshot.py)，前缀 `/api/v1/snapshot`。
- 命名空间（SDK）：[snapshot_namespace.py](file:///cloudide/workspace/OpenViking/openviking/snapshot_namespace.py)，暴露为 `client.snapshot.*`。
- 底层语义实现：[viking_fs.py](file:///cloudide/workspace/OpenViking/openviking/storage/viking_fs.py) 的 `commit` / `restore` / `show` / `log`。
- CLI 命令：[main.rs](file:///cloudide/workspace/OpenViking/crates/ov_cli/src/main.rs) 的 `SnapshotCmd`，子命令 [snapshot.rs](file:///cloudide/workspace/OpenViking/crates/ov_cli/src/commands/snapshot.rs)。

## API 参考

### commit()

把当前工作区状态保存成一个新的快照。

**参数**

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| message | str | 是 | - | 提交说明 |
| paths | List[str] | 否 | null | 限定本次快照的 `viking://` URI 列表，条目可以是文件或目录；目录会按照快照的剪枝规则递归展开。`null` 表示对整棵账号树做快照。传入空列表 `[]` 表示显式的空路径集（不会产生改动）。如果某个路径在 VFS 和前一次快照中都不存在，会输出一条 warn，并按"对该名称下任何子树执行删除"处理 |
| branch | str | 否 | `main` | 要推进的分支 |
| author_name | str | 否 | null | 覆盖默认的提交者名字（默认 `viking-bot`） |
| author_email | str | 否 | null | 覆盖默认的提交者邮箱 |

**Python SDK (Embedded / HTTP)**

```python
result = client.snapshot.commit(
    message="v1 initial import",
    paths=["viking://resources/my_md.md"],
)
print(result["commit_oid"])
```

**HTTP API**

```
POST /api/v1/snapshot/commit
```

```bash
curl -X POST "http://localhost:1933/api/v1/snapshot/commit" \
  -H "Content-Type: application/json" \
  -H "X-API-Key: your-key" \
  -d '{
    "message": "v1 initial import",
    "paths": ["viking://resources/my_md.md"]
  }'
```

**CLI**

```bash
ov snapshot commit -m "v1 initial import" --paths viking://resources/my_md.md -o json
```

**响应**

新建快照时：

```json
{
  "status": "ok",
  "result": {
    "result": "created",
    "commit_oid": "3f2a1b9c4d5e6f70819293a4b5c6d7e8f9a0b1c2",
    "changed": 3
  }
}
```

当工作区相对上一次提交没有任何变化时返回 `noop`，`commit_oid` 为当前 HEAD：

```json
{
  "status": "ok",
  "result": {
    "result": "noop",
    "commit_oid": "3f2a1b9c4d5e6f70819293a4b5c6d7e8f9a0b1c2"
  }
}
```

---

### log()

从某个分支的 HEAD 开始，沿首个父提交（`parents[0]`）逐层回溯历史，按时间从新到旧返回提交列表。

**参数**

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| branch | str | 否 | `main` | 要回溯的分支 |
| limit | int | 否 | 20 | 最多返回的提交数量。HTTP 接口限制范围为 1–500 |

**Python SDK (Embedded / HTTP)**

```python
history = client.snapshot.log(limit=10)
for commit in history:
    print(commit["oid"], commit["message"])
```

**HTTP API**

```
GET /api/v1/snapshot/log?branch={branch}&limit={limit}
```

```bash
curl -X GET "http://localhost:1933/api/v1/snapshot/log?branch=main&limit=10" \
  -H "X-API-Key: your-key"
```

**CLI**

```bash
ov snapshot log --limit 10 -o json
```

**响应**

`result` 是一个提交元数据列表，每个元素与 [show()](#show) 返回的提交元数据结构相同：

```json
{
  "status": "ok",
  "result": [
    {
      "oid": "9a0b1c2d3e4f5061728394a5b6c7d8e9f0a1b2c3",
      "tree": "11223344556677889900aabbccddeeff00112233",
      "parents": ["3f2a1b9c4d5e6f70819293a4b5c6d7e8f9a0b1c2"],
      "author": {
        "name": "viking-bot",
        "email": "bot@openviking.local",
        "time_seconds": 1750300000,
        "tz_offset_seconds": 28800
      },
      "committer": {
        "name": "viking-bot",
        "email": "bot@openviking.local",
        "time_seconds": 1750300000,
        "tz_offset_seconds": 28800
      },
      "message": "v2 modify delete add"
    }
  ]
}
```

> 当分支还没有任何提交时，HTTP 接口返回 `404 NOT_FOUND`。

---

### show()

查看某个提交的元数据；如果同时指定 `path`，则返回该提交中对应文件的内容。

**参数**

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| target_ref | str | 是 | - | 提交 OID（支持缩写前缀）、分支名或标签 |
| path | str | 否 | null | 某个文件的 `viking://` URI；省略时返回提交元数据 |

**Python SDK (Embedded / HTTP)**

```python
# 查看提交元数据
meta = client.snapshot.show("3f2a1b9c")
print(meta["message"], meta["parents"])

# 读取该提交中某个文件的内容
blob = client.snapshot.show("3f2a1b9c", path="viking://resources/my_project/guide.md")
```

> 注意：带 `path` 读取文件内容时，**Embedded（本地）客户端**直接返回原始 `bytes`；**HTTP 客户端**返回 `{"oid": str, "size": int, "bytes": bytes}` 字典。

**HTTP API**

```
GET /api/v1/snapshot/show?target_ref={ref}[&path={uri}]
```

```bash
# 提交元数据（返回 JSON）
curl -X GET "http://localhost:1933/api/v1/snapshot/show?target_ref=3f2a1b9c" \
  -H "X-API-Key: your-key"

# 读取文件内容（返回二进制流）
curl -X GET "http://localhost:1933/api/v1/snapshot/show?target_ref=3f2a1b9c&path=viking://resources/my_project/guide.md" \
  -H "X-API-Key: your-key"
```

不带 `path` 时返回提交元数据 JSON；带 `path` 时返回原始字节流（`Content-Type: application/octet-stream`），并附带两个响应头：

- `X-Snapshot-Oid`：blob 对象的 OID
- `X-Snapshot-Size`：blob 字节数

**CLI**

```bash
# 提交元数据
ov snapshot show 3f2a1b9c -o json

# 读取文件内容（默认输出到 stdout，可用 --out-file 写入本地文件）
ov snapshot show 3f2a1b9c --path viking://resources/my_project/guide.md --out-file ./guide.md
```

**响应（提交元数据）**

```json
{
  "status": "ok",
  "result": {
    "oid": "3f2a1b9c4d5e6f70819293a4b5c6d7e8f9a0b1c2",
    "tree": "00112233445566778899aabbccddeeff00112233",
    "parents": [],
    "author": {
      "name": "viking-bot",
      "email": "bot@openviking.local",
      "time_seconds": 1750299000,
      "tz_offset_seconds": 28800
    },
    "committer": {
      "name": "viking-bot",
      "email": "bot@openviking.local",
      "time_seconds": 1750299000,
      "tz_offset_seconds": 28800
    },
    "message": "v1 initial import"
  }
}
```

---

### restore()

把某个目录（或整棵账号树）恢复到 `source_commit` 时的状态。

这是**正向恢复**：它会计算 `source_commit` 与当前 HEAD 之间的差异并写回工作区，然后在当前 HEAD 之上生成一个**新的提交**。新提交的父提交是恢复前的 HEAD（而非 `source_commit`），历史不会被改写。`project_dir` 之外的文件保持不变。

**参数**

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| source_commit | str | 是 | - | 要恢复到的来源：提交 OID（支持缩写前缀）、分支名或标签 |
| project_dir | str | 否 | null | 要恢复的子目录 `viking://` URI；省略时恢复整棵账号树 |
| branch | str | 否 | `main` | 要推进的分支 |
| dry_run | bool | 否 | false | 仅计算并返回差异，不做任何写入 |
| message | str | 否 | null | 新提交的说明；省略时自动生成 |
| author_name | str | 否 | null | 覆盖默认的提交者名字 |
| author_email | str | 否 | null | 覆盖默认的提交者邮箱 |

**Python SDK (Embedded / HTTP)**

```python
result = client.snapshot.restore(
    project_dir="viking://resources/my_project",
    source_commit="3f2a1b9c",
    message="restore to v1",
)
print(result["result"], result["new_commit_oid"])

# 先预演，确认要改动哪些文件
plan = client.snapshot.restore(
    project_dir="viking://resources/my_project",
    source_commit="3f2a1b9c",
    dry_run=True,
)
print(plan["diff"])
```

**HTTP API**

```
POST /api/v1/snapshot/restore
```

```bash
curl -X POST "http://localhost:1933/api/v1/snapshot/restore" \
  -H "Content-Type: application/json" \
  -H "X-API-Key: your-key" \
  -d '{
    "project_dir": "viking://resources/my_project",
    "source_commit": "3f2a1b9c",
    "message": "restore to v1"
  }'
```

**CLI**

```bash
# 位置参数依次为 <source_commit> <project_dir>
ov snapshot restore 3f2a1b9c viking://resources/my_project -m "restore to v1" -o json

# 预演
ov snapshot restore 3f2a1b9c viking://resources/my_project --dry-run -o json
```

**响应（applied）**

成功写入并生成新提交时，`result` 为 `applied`。注意 `parent_commit` 等于恢复前的旧 HEAD，印证了正向恢复语义：

```json
{
  "status": "ok",
  "result": {
    "result": "applied",
    "new_commit_oid": "c3d4e5f60718293a4b5c6d7e8f9a0b1c2d3e4f50",
    "source_commit": "3f2a1b9c4d5e6f70819293a4b5c6d7e8f9a0b1c2",
    "parent_commit": "9a0b1c2d3e4f5061728394a5b6c7d8e9f0a1b2c3",
    "written": 1,
    "deleted": 1,
    "unchanged": 1,
    "written_paths": ["resources/my_project/guide.md"],
    "deleted_paths": ["resources/my_project/changelog.md"],
    "task_id": "snapshot_restore_reindex-..."
  }
}
```

当恢复产生向量副作用（写入/删除文件）时，响应会附带一个 `task_id`，可通过 `GET /api/v1/tasks/{task_id}` 轮询后台向量重建进度。

**响应（noop）**

来源与当前状态字节级一致、无需改动时返回 `noop`，不生成新提交：

```json
{
  "status": "ok",
  "result": {
    "result": "noop",
    "head": "9a0b1c2d3e4f5061728394a5b6c7d8e9f0a1b2c3",
    "source": "3f2a1b9c4d5e6f70819293a4b5c6d7e8f9a0b1c2"
  }
}
```

**响应（dry_run）**

`dry_run=true` 时只返回计划差异，不做任何写入。差异中的路径均相对于 `project_dir`：

```json
{
  "status": "ok",
  "result": {
    "result": "dry_run",
    "head": "9a0b1c2d3e4f5061728394a5b6c7d8e9f0a1b2c3",
    "source": "3f2a1b9c4d5e6f70819293a4b5c6d7e8f9a0b1c2",
    "diff": {
      "to_write": [{"path": "guide.md", "oid": "..."}],
      "to_delete": ["changelog.md"],
      "unchanged": ["notes/todo.md"]
    }
  }
}
```

## 典型流程

下面演示一个"提交 → 修改 → 恢复"的完整流程（Python SDK）：

```python
import openviking as ov

client = ov.OpenViking()
client.initialize()

root = "viking://resources/my_project"

# 1. 写入初始内容并提交 v1
client.write(f"{root}/guide.md", "# Guide\n\nv1 content\n", mode="create", wait=True)
v1 = client.snapshot.commit(message="v1 initial import")

# 2. 修改后再提交 v2
client.write(f"{root}/guide.md", "# Guide\n\nv2 content\n", mode="replace", wait=True)
v2 = client.snapshot.commit(message="v2 update")

# 3. 查看历史
for c in client.snapshot.log(limit=10):
    print(c["oid"][:8], c["message"])

# 4. 把工作区恢复到 v1（会在 v2 之上生成一个新提交）
client.snapshot.restore(project_dir=root, source_commit=v1["commit_oid"], message="restore to v1")

client.close()
```

更多端到端示例参见仓库中的 [examples/snapshot/](file:///cloudide/workspace/OpenViking/examples/snapshot) 目录，涵盖 SDK、HTTP、CLI 三种调用方式。

## 错误处理

| 场景 | HTTP 状态码 | 错误码 |
|------|-------------|--------|
| 分支/提交不存在，或 `show` 的 `path` 在该提交中不存在 | 404 | `NOT_FOUND` |
| 恢复期间分支被并发提交改写（CAS 冲突） | 409 | `CONFLICT` |
| 请求体包含未知字段（请求模型为 `extra="forbid"`） | 400 | `INVALID_ARGUMENT` |

## 相关文档

- [文件系统](03-filesystem.md)：快照建立在文件系统资源之上
- [系统](07-system.md)：通过 `GET /api/v1/tasks/{task_id}` 跟踪 restore 触发的后台向量重建
- [API 概览](01-overview.md)：完整端点总览
