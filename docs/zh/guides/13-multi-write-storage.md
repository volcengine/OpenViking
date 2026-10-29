# 多写存储指南

V2 多写在 primary 成功后返回，metadata 和 backup 在后台异步更新。primary
是唯一直接读取来源。

## 最小配置

```json
{
  "storage": {
    "workspace": "./data",
    "agfs": {
      "backend": "local",
      "backups": {
        "initial_partitions": 16,
        "checkpoint_interval_secs": 86400,
        "reconcile_existing_data": false,
        "provider": "filesystem",
        "items": [
          {
            "name": "local-backup",
            "backend": "local",
            "params": {
              "workspace": "./data/backup"
            }
          }
        ]
      }
    }
  }
}
```

V2 的顶层配置是：

| 字段 | 默认值 | 含义 |
| --- | --- | --- |
| `initial_partitions` | `16` | 为 account 创建的 metadata partition 数量 |
| `checkpoint_interval_secs` | `86400` | Checkpoint 扫描间隔，最小值为 `60` |
| `reconcile_existing_data` | `false` | 扫描 primary 已存在文件并写入 V2 metadata |
| `provider` | `"filesystem"` | Metadata segment provider：`filesystem` 或 `cache` |

每个 `items[]` 需要 `name`、`backend` 和 backend 专用 `params`。
`encryption` 可选。backup 名称必须唯一，且不能为 `primary`。

`provider` 为 `cache` 时，多写复用顶层 CacheRuntime。实际实现由顶层
`cache.provider` 选择；多写没有独立 Cache Provider 连接配置。

Python 配置层仍接受 V1 的 sync、ack、retry、operations、excludes 和
redirects 字段，输出 warning 后会在调用 Rust 前删除，字段不生效。

## 运行行为

- primary 是唯一直接读取来源和业务真相。
- primary 成功后立即返回，不等待 metadata 或 backup。
- 多写 metadata 只存储在 primary。
- backup 在后台最终收敛。
- 不提供同步状态或手工重试 API 和 CLI 命令。

进程可能在 primary 成功和事件持久化之间崩溃，导致一次复制事件丢失。此时
需要从 primary 重建受影响的 backup。

## Partition 数量

`initial_partitions` 仅在 account 的 partition manifest 首次创建时生效。
已有 account 在配置变化后继续使用持久化的 partition 数量和 routes。
本期不支持扩容或缩容。

## 首次启用与回滚

`.multiwrite.json` 不存在时，mount 先启用前台 V2 事件提交，并初始化 V2
metadata。若 `reconcile_existing_data=true`，启动流程还会扫描 primary 当前文件
并写入 V2 metadata。该流程不会删除 backup 中多余的旧文件。

默认 `reconcile_existing_data=false` 时，primary 上启用前已存在的文件不会作为
V2 write event 记录。只有在 backup 已与 primary 对齐，或明确跳过历史回填时
才保持默认值。

旧多写协议和旧迁移字段直接拒绝。启用多写前必须创建完整备份；回滚二进制
前必须先恢复该备份。

## 验证

使用普通文件 API 验证 primary 读写。使用[指标](../concepts/12-metrics.md)
监控队列深度、worker 失败、backup lag、协议版本和协议状态。

## 相关文档

- [多写存储](../concepts/14-multi-write-storage.md)
- [配置指南](./01-configuration.md)
- [OVPack 导入导出](./09-ovpack.md)
