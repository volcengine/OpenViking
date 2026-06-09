# 多写存储指南

本指南介绍如何配置 OpenViking 的多写存储能力。多写存储允许一个 primary 后端同时复制写入多个 backup 后端，用于高可用、跨区域副本、读加速和存储迁移。

多写能力位于 RAGFS 内部。OpenViking 的 Python SDK、HTTP API 和 CLI 使用方式保持不变。

## 前置条件

- 已有可用的 `ov.conf`。
- 已确认 primary backend 可以正常读写。
- 如果要接入 S3 兼容存储，已准备好 bucket、endpoint 和访问凭据。
- 如果要迁移已有数据，先完成存量数据迁移，再启用多写。

## 最小配置

下面示例使用本地目录作为 primary，并把写入复制到另一个本地目录。

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
              "local_dir": "./data/backup"
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
              "local_dir": "./data/local-az2"
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
              "prefix": "openviking"
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
- backup 可能短暂落后。

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
    "items": []
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
              "local_dir": "./data/encrypted-backup"
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

推荐迁移流程：

1. 停止或冻结写入窗口。
2. 使用 OVPack 或其他受控工具把存量数据迁移到目标 backup。
3. 校验目标 backend 的数据完整性。
4. 配置并启用 `storage.agfs.backups`。
5. 恢复写入。
6. 观察同步状态和错误日志。

如果无法冻结写入，可以先做一次全量迁移，再短暂停写做增量校验，最后启用多写。

## 验证配置

启动前建议运行：

```bash
openviking-server doctor
```

启动后可以用普通文件 API 验证：

```bash
openviking write viking://resources/multiwrite-check.txt \
  --content "multi-write check" \
  --wait

openviking read viking://resources/multiwrite-check.txt
```

如果使用本地 backup，可以直接检查 backup 目录中是否出现对应文件。生产环境更推荐使用系统健康检查和同步状态命令。

## 常见问题

### 为什么 backup 没有参与读取？

backup 默认只参与写入，不参与读取。需要在 backup 上显式配置：

```json
{
  "operations": [
    {
      "operation": "read",
      "priority": 10
    }
  ]
}
```

### 为什么启用多写后历史文件没有出现在 backup？

多写只处理启用后的新写入。历史文件需要先通过 OVPack、对象存储复制或后续 backfill 能力迁移。

### 异步模式下能否保证立即读到 backup 的最新数据？

不能。异步模式只保证最终一致。需要强读一致时，应让读取回退到 primary，或避免让可能滞后的 backup 参与读路由。

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
