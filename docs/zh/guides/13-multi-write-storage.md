# 多写存储指南

本指南介绍如何配置 OpenViking 的多写存储能力。多写存储允许一个 primary 后端同时复制写入多个 backup 后端，用于保存副本、配置读路由和存储迁移。primary 故障时不会自动提升 backup。

多写能力位于 RAGFS 内部。OpenViking 的 Python SDK、HTTP API 和 CLI 使用方式保持不变。

## 前置条件

- 已有可用的 `ov.conf`。
- 已确认 primary backend 可以正常读写。
- 如果要接入 S3 兼容存储，已准备好 bucket、endpoint 和访问凭据。
- 如需迁移已有数据，请遵循 [OVPack 多写迁移流程](./09-ovpack.md#与多写存储配合)；仅启用 backups 不会复制历史文件。

## 最小配置

将下列片段合并到现有模型和服务端配置。下面示例使用本地目录作为 primary，并把写入复制到另一个本地目录。

```json
{
  "storage": {
    "workspace": "./data",
    "agfs": {
      "backend": "local",
      "backups": {
        "sync_type": "async",
        "items": [
          {
            "name": "local-backup",
            "backend": "local",
            "local": {
                "workspace": "./data/backup"
            }
          }
        ]
      }
    }
  }
}
```

说明：

- 顶层 `backend` 是 primary。
- `backups.items[]` 是 backup 列表。
- `name` 是 backup 的稳定身份，后续同步元数据会引用它。
- `backend = "local"` 的 backup 使用 `local.workspace` 指定本地目录。
- `sync_type` 不配置时默认按异步模式理解。

## 多 Backup 配置

可以配置多个 backup。下面示例同时写入本地副本和 S3 兼容对象存储。

```json
{
  "storage": {
    "workspace": "./data",
    "agfs": {
      "backend": "local",
      "backups": {
        "sync_type": "async",
        "items": [
          {
            "name": "local-az2",
            "backend": "local",
            "local": {
                "workspace": "./data/local-az2"
            }
          },
          {
            "name": "object-store",
            "backend": "s3",
            "s3": {
              "bucket": "openviking-backup",
              "region": "us-east-1",
              "endpoint": "https://s3.example.com",
              "access_key": "your-access-key",
              "secret_key": "your-secret-key",
              "prefix": "openviking",
              "directory_marker_mode": "none"
            }
          }
        ]
      }
    }
  }
}
```

建议：

- `name` 不要使用会频繁变化的机器名或临时编号。
- backup 的底层路径或 bucket 应避免与 primary 指向同一物理位置。
- 修改 backup `name` 会影响历史同步元数据的识别，生产环境应谨慎变更。

### S3 兼容存储注意事项

按实际 endpoint 和对象存储配置选择以下 `s3` 字段：

| 字段 | 是否必填 | 说明 |
| --- | --- | --- |
| `use_path_style` | 可选，默认 `true` | 使用路径风格 URL（`http://host/bucket/key`），按 endpoint 支持的方式选择。 |
| `directory_marker_mode` | backup 项必填 | `none` 不创建标记，`empty` 创建零字节标记，`nonempty` 创建带内容的标记。每个 S3 backup 项都要显式设置；省略时启动会报 `AGFSConfigError: invalid directory_marker_mode: null`。 |
| `use_ssl` | 可选 | HTTP 端点（如 `http://localhost:9000`）需要设置为 `false`。 |

**S3 兼容存储最小示例（RustFS/MinIO）：**

```json
{
  "name": "s3-backup",
  "backend": "s3",
  "s3": {
    "bucket": "my-bucket",
    "endpoint": "http://localhost:9000",
    "access_key": "your-access-key",
    "secret_key": "your-secret-key",
    "prefix": "openviking",
    "use_ssl": false,
    "use_path_style": true,
    "directory_marker_mode": "none"
  }
}
```

上面的示例使用 `none`，适用于不使用目录标记的 S3 兼容服务（RustFS、MinIO、Ceph 等）。backup 项的这个字段没有默认值；顶层 `storage` 的 S3 配置默认使用 `empty`。

### Docker 网络配置

在 Docker 中运行 OpenViking 并配置同主机的 S3 备份时，需要注意：

- **Linux Docker**：使用 `--network host` 或宿主机局域网 IP。Docker bridge 网络可通过网关 IP（如 `172.17.0.1:9000`）访问宿主机局域网。
- **macOS/Windows Docker Desktop**：从容器访问宿主机时可使用 `host.docker.internal`。Docker Desktop 4.34 及之后的版本也提供可选的 host networking，启用方式见 [Docker 官方说明](https://docs.docker.com/engine/network/drivers/host/)。

如果启用 S3 备份后服务静默崩溃，请优先排查 Docker 网络。RAGFS Rust binding 在容器内无法访问 S3 端点时会报 `dispatch failure` 错误。

## 同步模式选择

### 异步模式

异步模式适合大多数场景。

```json
{
  "backups": {
    "sync_type": "async",
    "items": []
  }
}
```

特点：

- primary 写入成功后立即返回。
- backup 写入在后台执行。
- 写入延迟低。
- 后端不可用期间，落后时间没有固定上限，需观察同步和恢复状态。

适合：

- 写入吞吐优先。
- backup 主要用于灾备。
- 可以接受最终一致性。

### 同步模式

同步模式会等待 backup 确认。

```json
{
  "backups": {
    "sync_type": "sync",
    "write_ack_count": 1,
    "write_ack_timeout_ms": 5000,
    "items": [{"name": "local-backup", "backend": "local", "local": {"workspace": "./backup-data"}}]
  }
}
```

参数说明：

| 参数 | 说明 |
| --- | --- |
| `write_ack_count` | 写入返回前至少需要多少个 backup 确认 |
| `write_ack_timeout_ms` | 等待 backup 确认的超时时间，单位毫秒 |

特点：

- 写入确认更强。
- 写入延迟受 backup 影响。
- 未确认的 backup 会继续由后台重试修复。
- primary 已写成功但 backup 未达确认数时，客户端可能收到错误；此时 primary 中可能已经存在数据。

适合：

- 希望尽量减少 primary 与 backup 的确认窗口。
- backup 延迟可控。
- 调用方能接受同步写入带来的额外延迟。

## 配置读加速

backup 默认不参与读取。要让 backup 服务读取，需要显式配置 `operations`。

```json
{
  "name": "cache-backend",
  "backend": "memfs",
  "operations": [
    {"operation": "write", "priority": 0},
    {
      "operation": "read",
      "priority": 10
    }
  ]
}
```

读取优先级规则：

- `priority` 越小越优先。
- 只有声明 `read` 的 backup 才参与读取。
- primary 始终作为最终兜底。
- 冷备 backup 不建议配置读能力。

如果一个 backup 只配置了 `read`，没有配置 `write`，它不会接收普通多写复制。只有在你明确知道该 backend 的数据来源时，才应使用这种配置。

## Redirect 配置

Redirect 用于把匹配的文件写入指定 backup，而不是写入 primary。

按扩展名重定向：

```json
{
  "storage": {
    "agfs": {
      "backend": "local",
      "redirects": [
        {
          "type": "FileExtensionPolicy",
          "extensions": ["(pdf|ppt|zip)"],
          "target": ["object-store"]
        }
      ],
      "backups": {
        "items": [
          {
            "name": "object-store",
            "backend": "s3",
            "s3": {
              "bucket": "openviking-large-files",
              "endpoint": "https://s3.example.com"
            }
          }
        ]
      }
    }
  }
}
```

按大小重定向：

```json
{
  "type": "FileOverSizePolicy",
  "max_size_mb": 100,
  "target": ["object-store"]
}
```

注意：

- `target` 必须引用已有 backup 的 `name`。
- redirect 文件仍会通过普通 API 呈现为可读、可列举、可查询状态。
- redirect 映射保存在 primary 的内部元数据中。

## Exclude 配置

Exclude 用于让某个 backup 跳过匹配文件。

```json
{
  "name": "cache-backend",
  "backend": "memfs",
  "excludes": [
    {
      "type": "FileOverSizePolicy",
      "max_size_mb": 50
    },
    {
      "type": "FileExtensionPolicy",
      "extensions": ["(mp4|zip)"]
    }
  ]
}
```

常见用法：

- 缓存 backend 排除大文件。
- 低成本备份排除无需保存的文件类型。
- 某个 backup 只保存文本或配置类资源。

如果 redirect 的目标 backup 同时 exclude 了该文件，说明配置互相冲突。请优先修正配置，不要依赖系统自动猜测其他目标。

## 加密配置

多写存储复用 OpenViking 的透明静态加密能力。

全局加密开启示例：

```json
{
  "encryption": {
    "enabled": true,
    "provider": "local",
    "local": {
      "key_file": "~/.openviking/master.key"
    }
  },
  "storage": {
    "workspace": "./data",
    "agfs": {
      "backend": "local",
      "backups": {
        "items": [
          {
            "name": "plain-cache",
            "backend": "memfs",
            "encryption": {
              "enabled": false
            }
          },
          {
            "name": "encrypted-backup",
            "backend": "local",
            "local": {
                "workspace": "./data/encrypted-backup"
            },
            "encryption": {
              "enabled": true
            }
          }
        ]
      }
    }
  }
}
```

规则：

- 全局 `encryption.enabled=true` 时，primary 必须加密。
- backup 可以通过 `encryption.enabled` 单独控制是否加密。
- Python SDK、HTTP API 和 CLI 不需要处理加解密。
- `.redirect.json` 和 `.sync_log.json` 等内部元数据会跟随 primary 加密策略。

## 存量数据迁移

多写只复制启用之后的新写入，不会自动复制历史文件。

使用 OVPack 迁移时，请遵循 [与多写存储配合](./09-ovpack.md#与多写存储配合)：先在干净目标配置多写，再通过该服务恢复，验证各副本后恢复业务写入。目标 account 初始化和恢复冲突处理也在该流程中说明。

## 验证配置

启动前建议运行：

```bash
openviking-server doctor
```

启动后可以用普通文件 API 验证：

```bash
openviking write viking://resources/multiwrite-check.txt \
  --content "multi-write check"

openviking read viking://resources/multiwrite-check.txt
```

如果使用本地 backup，可以直接检查 backup 目录中是否出现对应文件。还应运行 `ov system backend sync-status viking://resources`，逐个核对预期副本。普通读取成功可能只读到了 primary，不能证明复制完成。

## 常见问题

### 为什么 backup 没有参与读取？

backup 默认只参与写入，不参与读取。需要在 backup 上显式配置：

```json
{
  "operations": [
    {"operation": "write", "priority": 0},
    {
      "operation": "read",
      "priority": 10
    }
  ]
}
```

### 为什么启用多写后历史文件没有出现在 backup？

多写只处理启用后的新写入。历史文件请按 [OVPack 迁移流程](./09-ovpack.md#与多写存储配合) 处理；启用 backups 不会自动补齐。

### 异步模式下能否保证立即读到 backup 的最新数据？

不能。后台重试需要后端恢复可用才能追上进度。backup 返回旧数据但读取成功时，不会自动回退 primary；需要最新数据时，应让读路由使用 primary。

### 内部元数据文件会出现在用户列表里吗？

不会。`.redirect.json` 和 `.sync_log.json` 是内部文件，会被普通目录列表隐藏。

### sync 模式返回失败是否表示 primary 一定没写入？

不是。primary 写成功但 backup 未达到确认数时，客户端可能收到失败。此时 primary 数据可能已经存在，落后的 backup 会由后台重试修复。

## 相关文档

- [多写存储](../concepts/14-multi-write-storage.md)
- [存储架构](../concepts/05-storage.md)
- [配置指南](./01-configuration.md)
- [加密指南](./08-encryption.md)
- [OVPack 导入导出](./09-ovpack.md)
