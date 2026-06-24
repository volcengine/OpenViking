# 多版本管理（快照）指南

本指南介绍如何启用并使用 OpenViking 的多版本管理（快照）能力。多版本管理在 VikingFS 之上提供基于 Git 的 `commit`/`log`/`show`/`restore` 原语，让你把账号下的资源树保存成一系列不可变快照，随时回溯历史、对比版本，并把工作区恢复到任意历史状态。

多版本管理由内嵌在 Rust RAGFS 层的 [gitoxide](https://github.com/Byron/gitoxide) 驱动，以 `account_id` 为粒度维护一个逻辑 Git 仓库（每个账号一个仓库），对调用方完全透明——你无需手动执行任何 `git` 命令。

> 关于各命令参数和响应结构的完整 API 参考，见 [多版本管理 API](../api/11-snapshot.md)。

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
      "base_dir": "",
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

修改配置后，重启 OpenViking 服务（或重新初始化 SDK 客户端）使其生效。

> 仓库中提供了可直接参考的完整示例：[ov.conf.git-local.example](file:///cloudide/workspace/OpenViking/examples/snapshot/ov.conf.git-local.example) 与 [ov.conf.git-s3-tos.example](file:///cloudide/workspace/OpenViking/examples/snapshot/ov.conf.git-s3-tos.example)。

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
- 备份或迁移工作区时，把 `.ovgit` 一并复制即可保留完整的版本历史。
- 选择 `s3` 后端时，不会创建本地 `.ovgit` 目录，数据改为存放在 bucket 的 `{prefix}/{account}/...` 键下。

## 使用方法

启用后，三种调用方式都会出现快照相关命令。下面以一个"提交 → 修改 → 恢复"的最小流程演示。

### Python SDK

快照方法挂在 `client.snapshot.*` 命名空间下。

```python
import openviking as ov

client = ov.OpenViking()
client.initialize()

root = "viking://resources/my_project"

# 1. 写入初始内容并提交 v1
client.write(f"{root}/guide.md", "# Guide\n\nv1 content\n", mode="create", wait=True)
v1 = client.snapshot.commit(message="v1 initial import")
print("v1:", v1["commit_oid"])

# 2. 修改后再提交 v2
client.write(f"{root}/guide.md", "# Guide\n\nv2 content\n", mode="replace", wait=True)
v2 = client.snapshot.commit(message="v2 update")

# 3. 查看历史
for c in client.snapshot.log(limit=10):
    print(c["oid"][:8], c["message"])

# 4. 查看某个提交的元数据
print(client.snapshot.show(v1["commit_oid"])["message"])

# 5. 把工作区恢复到 v1（会在 v2 之上生成一个新的“正向”提交）
client.snapshot.restore(project_dir=root, source_commit=v1["commit_oid"], message="restore to v1")

client.close()
```

### CLI

CLI 子命令位于 `ov snapshot` 下：

```bash
# 提交当前工作区状态
ov snapshot commit -m "v1 initial import" -o json

# 回溯历史（最新在前）
ov snapshot log --limit 10 -o json

# 查看提交元数据
ov snapshot show <commit_oid> -o json

# 读取某个提交中的文件内容（默认输出到 stdout，可用 --out-file 写入本地文件）
ov snapshot show <commit_oid> --path viking://resources/my_project/guide.md --out-file ./guide.md

# 把目录恢复到某个历史快照（位置参数依次为 <source_commit> <project_dir>）
ov snapshot restore <commit_oid> viking://resources/my_project -m "restore to v1" -o json

# 先预演，确认会改动哪些文件
ov snapshot restore <commit_oid> viking://resources/my_project --dry-run -o json
```

### HTTP API

```bash
# 提交
curl -X POST "http://localhost:1933/api/v1/snapshot/commit" \
  -H "Content-Type: application/json" \
  -H "X-API-Key: your-key" \
  -d '{"message": "v1 initial import"}'

# 回溯历史
curl -X GET "http://localhost:1933/api/v1/snapshot/log?branch=main&limit=10" \
  -H "X-API-Key: your-key"

# 查看提交元数据
curl -X GET "http://localhost:1933/api/v1/snapshot/show?target_ref=<commit_oid>" \
  -H "X-API-Key: your-key"

# 恢复
curl -X POST "http://localhost:1933/api/v1/snapshot/restore" \
  -H "Content-Type: application/json" \
  -H "X-API-Key: your-key" \
  -d '{"project_dir": "viking://resources/my_project", "source_commit": "<commit_oid>", "message": "restore to v1"}'
```

## 重要语义：正向恢复

`restore` 采用**正向恢复（forward-commit）**：它读取 `source_commit` 的内容，把差异写回工作区，并在**当前 HEAD 之上生成一个新的提交**。因此：

- 新提交的父提交是恢复操作发生前的 HEAD，**不是** `source_commit`。
- HEAD 始终单调向前推进，**历史永远不会被改写或丢失**——回到旧版本本身也是一次新的提交。
- `restore` 只影响 `project_dir`（省略时为整棵账号树）范围内的文件，范围之外的文件保持不变。

## 注意事项

- 修改 `git` 配置后必须重启服务 / 重新初始化客户端才能生效。
- 启用 `s3` 后端时，`git.s3.bucket` 与 `git.s3.region` 为必填项，缺失会导致初始化失败。
- 恢复操作如涉及向量副作用（写入/删除文件），响应会返回一个 `task_id`，可通过 `GET /api/v1/tasks/{task_id}` 轮询后台向量重建进度（参见 [系统指南](05-observability.md) 与 [API 概览](../api/01-overview.md)）。
- 不要手动用外部 `git` 工具去操作 `.ovgit` 目录，它由 OpenViking 维护。

## 相关文档

- [多版本管理 API](../api/11-snapshot.md)：命令参数与响应的完整参考
- [配置说明](01-configuration.md)：`ov.conf` 完整配置项
- [多写存储指南](13-multi-write-storage.md)：资源数据的多后端复制
