# 多写存储

多写存储让 OpenViking 在一个统一的文件系统抽象下，同时使用一个主存储和多个备份存储。它适合数据高可用、跨区域副本、读加速、存储迁移等场景。

从 API 使用者视角看，`read()`、`write()`、`ls()`、`stat()` 等接口不变。多写逻辑位于 RAGFS 内部，调用方不需要关心文件最终落在哪个底层后端。

## 核心模型

多写存储由一个 primary 和多个 backup 组成：

| 角色 | 配置位置 | 说明 |
| --- | --- | --- |
| primary | `storage.agfs.backend` | 权威写入目标，也是读取兜底 |
| backup | `storage.agfs.backups.items[]` | 接收复制写入，可选参与读取 |

没有配置 `backups` 时，OpenViking 继续使用原有单后端模式。

## 写入路径

默认情况下，写入先落到 primary，再复制到 write-enabled backup。

```text
Client
  -> OpenViking API
  -> RAGFS MultiWrite
  -> primary
  -> backup1 / backup2 / ...
```

backup 未配置 `operations` 时默认参与写入。这样可以用最少配置得到冷备能力。

## 同步模式

多写支持两种一致性模式。

| 模式 | 配置值 | 行为 | 适用场景 |
| --- | --- | --- | --- |
| 异步多写 | `async` | primary 写成功后立即返回，backup 后台同步 | 低延迟写入、最终一致 |
| 同步多写 | `sync` | primary 写成功后等待 backup 确认 | 更强写入确认、可接受额外延迟 |

异步模式下，backup 可能在短时间内落后于 primary。同步模式下，可以通过 `write_ack_count` 和 `write_ack_timeout_ms` 控制需要等待多少 backup 确认，以及等待多久。

即使使用同步模式，未确认或超时的 backup 仍会由后台重试修复。

## 读取路径

读取不会默认访问所有 backup。只有显式声明 `read` 操作的 backup 才会进入读路由。

读取顺序如下：

```text
1. 按 priority 升序访问 read-enabled backup
2. 回退到 primary
3. 如果文件被 redirect，则访问 redirect target
4. 仍未命中则返回 NotFound
```

这种设计避免冷备节点默认参与读取，降低读到旧数据的风险。

## Redirect

Redirect 表示“某些文件不写入 primary，而是写入指定 backup”。

常见用途：

- 大文件进入对象存储。
- 特定后缀文件进入专门 backend。
- 主存储只保存常规内容，特殊文件由其他 backend 保存。

Redirect 策略配置在 primary 上。命中策略后，OpenViking 会把映射记录到内部元数据中。用户执行 `ls()`、`stat()`、`read()` 时仍能看到正常的文件系统视图。

## Exclude

Exclude 表示“某个 backup 不接收匹配的文件”。

常见用途：

- 内存或缓存 backend 不保存大文件。
- 某个 backup 只保存文本类资源。
- 某个低成本 backend 排除临时或超大文件。

Exclude 策略配置在 backup 上，只影响该 backup 是否接收写入。

## 内部元数据

多写使用两个内部元数据文件：

| 文件 | 作用 |
| --- | --- |
| `.redirect.json` | 记录 redirect 文件对应的目标 backend |
| `.sync_log.json` | 记录每个文件的同步版本和 backup 确认进度 |

这些文件对普通用户不可见，不会出现在常规列表结果中，也不应通过公开 API 直接读写。

如果 primary 开启静态数据加密，这些内部元数据也会跟随 primary 加密策略写入。

## 加密关系

多写不会改变 OpenViking 的透明加密模型。

规则如下：

- Python 层和公共 API 不感知加密实现。
- primary 在全局加密开启时必须加密。
- backup 可以独立决定是否加密。
- 内部元数据必须走 primary 的加密入口。

这意味着启用多写后，调用方式仍然不变；只需要通过配置决定每个 backend 的加密策略。

## 与 OVPack 的关系

多写只负责“启用之后的新写入”。它不会自动同步启用之前已经存在于 primary 中的历史文件。

如果要迁移存量数据，推荐流程是：

1. 使用 OVPack 或其他受控方式完成全量迁移。
2. 校验目标 backend 数据。
3. 启用多写配置。
4. 后续新增和修改的数据由多写持续复制。

## 限制

- 异步模式下 backup 可能短暂落后。
- 启用多写前的历史文件需要单独迁移或回填。
- redirect 文件依赖内部元数据恢复目录视图。
- 多进程同时写同一 primary 时，需要未来的分布式元数据锁能力。
- 热点目录会频繁更新内部元数据，可能带来额外写放大。

## 相关文档

- [存储架构](./05-storage.md)
- [配置指南](../guides/01-configuration.md)
- [多写存储指南](../guides/13-multi-write-storage.md)
- [OVPack 导入导出](../guides/09-ovpack.md)
