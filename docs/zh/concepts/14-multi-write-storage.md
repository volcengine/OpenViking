# 主备存储

V2 多写以一个 primary backend 作为业务真相，并把文件复制到配置的 backup。

## 读写模型

- 所有直接读取只访问 primary。backup 不参与业务读取。
- primary 变更成功后立即返回。
- metadata 持久化和 backup 应用在后台异步执行。
- backup 落后不会改变 primary 已返回的结果。
- 多写 metadata 只存储在 primary。

该模型是最终一致。进程可能在 primary 成功和事件持久化之间崩溃，导致该次
复制事件丢失。发生此情况后，需要从 primary 重建受影响的 backup。

## Metadata 模型

每个 account 使用固定数量的 metadata partition。事件路由到一个 partition，
追加到 segment log，再由每个 backup 独立消费。Checkpoint 压缩旧历史，GC
清理不再需要的 metadata。

`initial_partitions` 仅在 account metadata 首次创建时决定 partition 数量。
已有 account 始终使用持久化的 partition 数量和 routes，修改配置不会改变
现有布局。本期不支持扩容或缩容。

## 首次启用导入

`.multiwrite.json` 不存在时，启动流程创建 `V2/migrating` 状态，先启用前台
V2 事件提交，再在后台导入 primary 当前文件。导入成功后切换为 `stable`。
导入不会删除 backup 中多余的旧文件。

旧多写协议和旧迁移字段直接拒绝，不执行转换或解释。

## 运维

V2 不提供同步状态或手工重试 API 和 CLI 命令。请使用多写 Prometheus 指标
观察队列深度、worker 失败、backup lag、协议版本和协议状态。

## 相关文档

- [存储架构](./05-storage.md)
- [指标](./12-metrics.md)
- [配置指南](../guides/01-configuration.md)
- [多写存储指南](../guides/13-multi-write-storage.md)
- [OVPack 导入导出](../guides/09-ovpack.md)
