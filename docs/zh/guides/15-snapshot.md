# 多版本管理（快照）指南

快照将指定范围内的文件树保存为不可变版本。使用 `commit` 保存、`log` 查看历史、`show` 读取旧版文件、`restore` 恢复已保存的内容。未提交或被排除的文件不在恢复范围内，ACL 和向量索引也不保存历史版本。

多版本管理由内嵌在 Rust RAGFS 层的 [gitoxide](https://github.com/Byron/gitoxide) 驱动，以 `account_id` 为粒度维护一个逻辑 Git 仓库（每个账号一个仓库），对调用方完全透明——你无需手动执行任何 `git` 命令。

> 关于各命令参数和响应结构的完整 API 参考，见 [多版本管理 API](../api/11-snapshot.md)。

## 何时需要 commit

普通 `write`、`rm` 等操作直接改变当前工作区，启用快照不代表每次写入都会自动生成版本。没有成功覆盖该路径的 snapshot commit，就不能通过快照找回它此前的内容。建议在导入完成、批量修改前后或一个业务阶段结束时提交，并保存返回的 `commit_oid`。

Snapshot 保存文件树的版本，不会回滚当前 ACL，也不保存向量索引的历史。`restore` 写回文件后按需异步重建索引；它会修改工作区，执行前先用 `dry_run` 检查计划。

`client.snapshot.commit()` 与会话的 `session.commit()` 职责不同：前者保存文件版本，后者归档对话并处理记忆。部分记忆流程会在更新 experience 后调用 snapshot，这不等于所有写入都自动快照。

## 提交范围与并发

USER / ADMIN 必须给 `commit` 和 `log` 传入 `paths`，给 `restore` 传入 `project_dir`，给 `show` 传入文件 `path`。只有本地 ROOT 可以省略这些范围参数。对自己有写权限的项目目录提交，可以避免把无关资源纳入同一次快照。

| `commit(paths=...)` 输入 | 含义 |
| --- | --- |
| 现存文件 URI | 处理该文件 |
| 现存目录 URI | 递归处理当前文件，并记录此前快照中该子树内文件的删除 |
| 缺失 URI | 记录此前快照中该路径及其子树的删除；此前也不存在则无改动 |
| `[]` | 显式空范围，不产生改动 |
| `None` / 省略 | 整棵账号树，仅限 ROOT |

局部提交沿用分支上一次快照作为基础，范围外文件保留原快照版本。删除后仍要提交被删除 URI 或覆盖它的父目录；从 `paths` 中去掉该 URI 会漏记删除。路径末尾 `/` 不是文件/目录类型声明，当前接口没有逐目标的显式类型参数。

非 ROOT 的显式路径提交先按当前状态选锁，再检查范围权限并生成快照：

| 目标状态 | Filesystem PathLock | Cache（Redis）PathLock |
| --- | --- | --- |
| 现存文件 | Exact | Exact |
| 现存目录 | Tree | Tree |
| 缺失路径 | 跳过该目标的锁 | Tree |

Filesystem 对缺失目标跳过锁，避免锁文件创建目标目录或缺失的父目录链；该目标仍参与快照删除处理。此时并发重建同一路径可能被漏记或读到尚未写完的内容，后续提交才能记录最终状态。ROOT 不经过这段显式路径加锁流程。

快照不能视为任意并发 I/O 的全局原子视图。需要确定的业务检查点时，应先结束该范围内的写入，再提交；锁只协调参与 [PathLock 协议](../concepts/09-transaction.md) 的操作。

## 前置条件

- 已有可用的 `ov.conf`。
- 已确认资源的读写正常（多版本管理建立在文件系统资源之上）。
- 如果选择 S3 后端存放 Git 对象，已准备好 bucket、region、endpoint 和访问凭据。

## 启用多版本管理

多版本管理默认**开启**（`git.enabled` 默认为 `true`）。Git 对象的存储后端可以选择 `local`（本地文件系统）或 `s3`（S3 兼容对象存储）；当不显式设置 `git.backend` 时，会**自动继承 `storage.agfs.backend`**（`storage.agfs.backend` 为 `memory` 时映射为 `local`）。如需关闭多版本管理，把 `git.enabled` 设为 `false` 即可。

### 本地后端（推荐用于单机部署）

```json
{
  "storage": {
    "workspace": "./data"
  },
  "git": {
    "enabled": true,
    "backend": "local",
    "default_branch": "main",
    "author_name": "viking-bot",
    "author_email": "bot@viking.local",
    "local": {
      "base_dir": ""
    }
  }
}
```

配置说明：

| 字段 | 默认值 | 说明 |
|------|--------|------|
| `git.enabled` | `true` | 是否启用多版本管理。设为 `false` 可关闭快照功能 |
| `git.backend` | 继承 `storage.agfs.backend` | Git 对象后端：`local` 或 `s3`。不显式设置时继承 `storage.agfs.backend`（`memory` 映射为 `local`） |
| `git.default_branch` | `main` | 未显式指定时使用的默认分支名 |
| `git.author_name` | `viking-bot` | 调用方未传 `author_name` 时使用的默认提交者名字 |
| `git.author_email` | `bot@viking.local` | 默认提交者邮箱 |
| `git.local.base_dir` | `""` | Git 对象/引用的存放目录。**留空时默认使用 `{storage.workspace}/.ovgit`** |

> 通常把 `git.local.base_dir` 留空即可，让快照数据自动落在工作区下的 `.ovgit` 目录，便于和资源数据一起备份与迁移。

### S3 后端（推荐用于分布式/云端部署）

把 Git 对象与引用存到 S3 兼容对象存储（如火山引擎 TOS、MinIO、AWS S3）。当 `backend` 为 `s3` 时，**必须**提供 `git.s3` 段，且 `bucket`、`region` 不能为空。

> 提示：`git.s3` 的 `bucket`、`region`、`endpoint`、`access_key`、`secret_key` 在未显式设置时会**自动继承 `storage.agfs.s3`** 的对应字段。因此当 `storage.agfs` 已经配置为 s3 后端时，通常无需重复填写 `git.s3`——只要不显式设置 `git.backend`，多版本管理会直接复用 `storage.agfs` 的 bucket 与访问凭据。

```json
{
  "storage": {
    "workspace": "./data"
  },
  "git": {
    "enabled": true,
    "backend": "s3",
    "default_branch": "main",
    "author_name": "viking-bot",
    "author_email": "bot@viking.local",
    "s3": {
      "bucket": "your-tos-bucket",
      "region": "cn-beijing",
      "endpoint": "https://tos-s3-cn-beijing.volces.com",
      "access_key": "<your-volcengine-ak>",
      "secret_key": "<your-volcengine-sk>",
      "prefix": ".ovgit",
      "use_path_style": false,
      "cas_mode": "native"
    }
  }
}
```

配置说明：

| 字段 | 默认值 | 说明 |
|------|--------|------|
| `git.s3.bucket` | 继承 `storage.agfs.s3.bucket` | 存放 Git 对象/引用的 bucket，必填（可由 `storage.agfs.s3` 继承） |
| `git.s3.region` | 继承 `storage.agfs.s3.region`，否则 `us-east-1` | bucket 所在区域，必填 |
| `git.s3.prefix` | `.ovgit` | 键前缀，所有数据存放在 `{prefix}/{account}/...` 下 |
| `git.s3.endpoint` | 继承 `storage.agfs.s3.endpoint`，否则 `""` | 自定义 S3 端点（MinIO/TOS 等）；标准 AWS S3 留空 |
| `git.s3.access_key` / `git.s3.secret_key` | 继承 `storage.agfs.s3` 对应字段，否则 `null` | 直接读取的凭据；留空则走 SDK 默认凭据链 |
| `git.s3.use_path_style` | `true` | `true` 用 path-style 寻址（MinIO 等）；`false` 用 virtual-host 寻址（TOS 等） |
| `git.s3.cas_mode` | `native` | 引用 CAS 模式。`native` 使用 S3 条件写（If-Match） |

修改服务端配置后，重启 OpenViking 服务。重新初始化 HTTP SDK 客户端不会重新加载服务端配置。

> 仓库中提供了可直接参考的完整示例：[ov.conf.git-local.example](https://github.com/volcengine/OpenViking/blob/main/examples/snapshot/ov.conf.git-local.example) 与 [ov.conf.git-s3-tos.example](https://github.com/volcengine/OpenViking/blob/main/examples/snapshot/ov.conf.git-s3-tos.example)。

## 目录结构变化：`.ovgit` 目录

启用 `local` 后端且 `base_dir` 留空时，OpenViking 会在工作区下新增一个 **`.ovgit`** 目录用于存放 Git 对象和引用：

```text
data/                      # storage.workspace
├── viking/                # 用户可见的资源树（viking:// 映射到这里）
│   └── ...
└── .ovgit/                # 多版本管理数据（新增）
    └── {account_id}/      # 每个账号一个逻辑 Git 仓库
        ├── objects/       # Git 对象（commit/tree/blob），标准 fanout 布局 aa/bb...
        ├── refs/
        │   └── heads/
        │       └── main   # 分支引用，内容为 40 位十六进制 OID
        └── HEAD           # 当前分支指针，内容为 "ref: refs/heads/main"
```

要点：

- `.ovgit` 是内部数据目录，**不会**通过 `viking://` 暴露，用户在文件系统 API（`ls`/`read` 等）中看不到也无法修改它。
- 它与 Git 的标准对象库布局一致（内容寻址的 `objects/`、loose 引用的 `refs/`），但由 OpenViking 自动管理，**无需也不应**手动运行 `git` 命令去操作它。
- 物理备份或迁移前先暂停写入，在同一检查点复制工作区和 `.ovgit`。[OVPack 导出](09-ovpack.md) 保存当前内容，不包含快照历史。
- 选择 `s3` 后端时，不会创建本地 `.ovgit` 目录，数据改为存放在 bucket 的 `{prefix}/{account}/...` 键下。

## 使用方法

启用后，三种调用方式都会出现快照相关命令。下面以一个"提交 → 修改 → 恢复"的最小流程演示。

### Python SDK

快照方法挂在 `client.snapshot.*` 命名空间下。

```python
from uuid import uuid4
from openviking_sdk import SyncHTTPClient

client = SyncHTTPClient(url="http://localhost:1933", api_key="your-key")
client.initialize()

root = f"viking://resources/snapshot-demo-{uuid4().hex[:8]}"

# 1. 写入初始内容并提交 v1
client.write(
    uri=f"{root}/guide.md",
    content="# Guide\n\nv1 content\n",
    mode="create",
)
v1 = client.snapshot.commit(message="v1 initial import", paths=[root])
if not v1.get("commit_oid"):
    raise RuntimeError(f"No snapshot created: {v1}")
print("v1:", v1["commit_oid"])

# 2. 修改后再提交 v2
client.write(
    uri=f"{root}/guide.md",
    content="# Guide\n\nv2 content\n",
    mode="replace",
)
v2 = client.snapshot.commit(message="v2 update", paths=[root])

# 3. 查看历史
for c in client.snapshot.log(limit=10, paths=[root]):
    print(c["oid"][:8], c["message"])

# 4. 读取历史文件内容
print(client.snapshot.show(v1["commit_oid"], path=f"{root}/guide.md"))

# 5. 预览恢复计划，暂不修改文件
print(client.snapshot.restore(project_dir=root, source_commit=v1["commit_oid"], dry_run=True))

client.close()
```

示例每次创建新目录，最后只预览恢复计划。检查计划后，重新连接客户端，使用相同的 `project_dir` 和 `source_commit`，并设置 `dry_run=False` 才会执行恢复。

### CLI

CLI 子命令位于 `ov snapshot` 下：

```bash
# 提交当前工作区状态
ov snapshot commit -m "v1 initial import" --paths viking://resources/my_project -o json

# 回溯历史（最新在前）
ov snapshot log --paths viking://resources/my_project --limit 10 -o json

# 填入上面返回的 commit_oid
COMMIT_OID="replace-with-commit-oid"

# 读取历史文件内容
ov snapshot show "$COMMIT_OID" --path viking://resources/my_project/guide.md

# 读取某个提交中的文件内容（默认输出到 stdout，可用 --out-file 写入本地文件）
ov snapshot show "$COMMIT_OID" --path viking://resources/my_project/guide.md --out-file ./guide.md

# 预览会改动的文件
ov snapshot restore "$COMMIT_OID" viking://resources/my_project --dry-run -o json
```

确认预览后，执行恢复：

```bash
ov snapshot restore "$COMMIT_OID" viking://resources/my_project -m "restore to v1" -o json
```

### HTTP API

```bash
# 提交
curl -X POST "http://localhost:1933/api/v1/snapshot/commit" \
  -H "Content-Type: application/json" \
  -H "X-API-Key: your-key" \
  -d '{"message": "v1 initial import", "paths": ["viking://resources/my_project"]}'

# 回溯历史
curl -X GET "http://localhost:1933/api/v1/snapshot/log?branch=main&limit=10&paths=viking://resources/my_project" \
  -H "X-API-Key: your-key"

# 读取历史文件内容
curl -X GET "http://localhost:1933/api/v1/snapshot/show?target_ref=<commit_oid>&path=viking://resources/my_project/guide.md" \
  -H "X-API-Key: your-key"

# 预览恢复
curl -X POST "http://localhost:1933/api/v1/snapshot/restore" \
  -H "Content-Type: application/json" \
  -H "X-API-Key: your-key" \
  -d '{"project_dir": "viking://resources/my_project", "source_commit": "<commit_oid>", "message": "restore to v1", "dry_run": true}'
```

将 `<commit_oid>` 替换为保存的提交 ID。上面的恢复请求只预览；确认执行时再把 `dry_run` 改为 `false`。

## 重要语义：正向恢复

`restore` 采用**正向恢复（forward-commit）**：它读取 `source_commit` 的内容，把差异写回工作区，并在**当前 HEAD 之上生成一个新的提交**。因此：

- 新提交的父提交是恢复操作发生前的 HEAD，**不是** `source_commit`。
- 恢复产生文件改动时会追加提交，不改写已有历史；没有改动时可能不生成新提交。快照存储本身仍需要备份。
- `restore` 只影响 `project_dir`（省略时为整棵账号树）范围内的文件，范围之外的文件保持不变。

## 使用 `.ovgitignore` 排除文件

账号根目录下的 `.ovgitignore` 是一个账号级的排除规则文件，作用类似根 `.gitignore`：匹配该规则的文件在 `commit` 时被排除出快照。它与系统内置的剪枝规则（`_system`、`tasks`、向量索引派生文件等）叠加生效。

要点：

- 规则文件本身**不会被 `.ovgitignore` 规则忽略**，即使规则匹配 `.ovgitignore` 也会被正常纳入快照——这样规则的变更可追溯、可恢复。
- 规则**只影响 `commit`**；`restore`、`show`、`log` 仍以提交内容为准，不把当前 `.ovgitignore` 当作过滤器。因此恢复一个历史快照时，即便其中某些文件匹配当前规则，仍会被正常恢复。
- 若某个文件在更早的提交中已被跟踪、之后新增规则匹配到它，下一次 `commit` 会把它从新快照中移除（工作区的文件本身不受影响）。
- `.ovgitignore` 不会进入向量索引/检索。

### 规则语法

`.ovgitignore` 为 UTF-8 文本，支持常见的 glob 子集：

- 空行被忽略。
- 首个非空白字符为 `#` 的行是注释。
- 行首/行尾空白会被裁剪。
- **不支持** `!` 取反（出现会让 `commit` 失败并报错）。
- **不支持** Git 风格的反斜杠转义。
- 文件大小上限 64 KiB。

匹配路径使用账号相对的 Git 树路径（`/` 分隔），如 `resources/proj/a.log`。例如 `*.log` 匹配任意深度的 `.log` 文件，`build/` 匹配名为 `build` 的目录及其内容，`/cache/**` 仅匹配账号根下的 `cache/`。

### Python SDK

```python
# 写入规则
client.snapshot.set_gitignore(content="*.log\n")

# 读取（不存在时返回空字符串）
print(client.snapshot.get_gitignore())

# 删除（不存在也视为成功，幂等）
client.snapshot.delete_gitignore()
```

随后提交时，匹配规则的文件会被排除，响应里的 `ignored` 字段给出本次被排除的候选路径数：

```python
v = client.snapshot.commit(message="with ignore", paths=["viking://resources/my_project"])
print(v["result"], v.get("ignored"))
```

### CLI

```bash
# 设置（用 --content 直接传内容，或用 --file 从文件读取）
ov snapshot ignore-set --content "*.log" -o json
ov snapshot ignore-set --file ./my-rules -o json

# 读取（-o json 返回 {"result": "<内容>"}；不加 -o json 时直接把内容打到 stdout）
ov snapshot ignore-get -o json

# 删除（幂等）
ov snapshot ignore-delete -o json
```

### HTTP API

```bash
# 读取
curl -X GET "http://localhost:1933/api/v1/snapshot/ignore" \
  -H "X-API-Key: your-key"

# 写入
curl -X PUT "http://localhost:1933/api/v1/snapshot/ignore" \
  -H "Content-Type: application/json" \
  -H "X-API-Key: your-key" \
  -d '{"content": "*.log\n"}'

# 删除
curl -X DELETE "http://localhost:1933/api/v1/snapshot/ignore" \
  -H "X-API-Key: your-key"
```

## 注意事项

- 修改服务端 `git` 配置后，重启服务使其生效。
- 启用 `s3` 后端时，`git.s3.bucket` 与 `git.s3.region` 为必填项，缺失会导致初始化失败。
- 恢复操作如涉及向量副作用（写入/删除文件），响应会返回一个 `task_id`，可通过 `GET /api/v1/tasks/{task_id}` 轮询后台向量重建进度（参见 [系统指南](05-observability.md) 与 [API 概览](../api/01-overview.md)）。
- `.ovgitignore` 内容过大（超过 64 KiB）或包含 `!` 取反、反斜杠转义等不支持语法时，`commit` 会失败并报 `invalid operation` 错误；写入时（`set_gitignore`）会预先校验大小。
- 不要手动用外部 `git` 工具去操作 `.ovgit` 目录，它由 OpenViking 维护。

## 相关文档

- [多版本管理 API](../api/11-snapshot.md)：命令参数与响应的完整参考
- [配置说明](01-configuration.md)：`ov.conf` 完整配置项
- [多写存储指南](13-multi-write-storage.md)：资源数据的多后端复制
