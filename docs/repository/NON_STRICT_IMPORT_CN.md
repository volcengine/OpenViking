# 首次非严格导入的部分成功

## 目标与契约

仅对新目标的首次原生导入，`strict=false`（默认）允许提交成功解析的文档，明确报告
失败项。`strict=true` 拒绝残缺导入。已有资源更新不因本次改动放宽：无论 strict
为何值，只要选定范围存在实际读取/解析失败，就不提交本轮成功部分。

不新增 import_policy、CLI 选项、SDK options 或队列消息字段，不强制将更新的
扫描参数改为 strict=true；既有过滤和不支持格式行为不变。正常完整导入及完整更新
沿用既有路径，认证失败、根节点不可访问、零成功文档仍整体失败。

## 首次判定

判定针对 TreeBuilder 解析后的最终目标 URI，在目标锁内执行，不以源 URL、请求次数
或目录是否为空作为依据：

1. 目标由当前任务从不存在状态预约；队列已有 cleanup_empty_target_on_failure
   表示当前任务拥有新空目标，转换为服务内部标志。自动后缀目标同样按实际最终 URI
   检查。旧空目录不能冒充本次预约。
2. 目标不存在或仅含存储层内部锁元数据；业务文件、子目录、旧摘要等均拒绝。
3. 严格读取当前账户/目标完整索引范围，无历史记录（包含孤立向量及 ACL 记录）。
   即使 build_index=false 也检查；索引不可用时失败，不假定不存在。
4. 持久 Watch 没有既往执行记录，Task 中没有该账户/目标的已完成导入记录。
   当前尚未完成的 Task 和首次新注册 Watch 不算旧导入；上次部分成功后的补齐算更新。

复用已验证为空的向量清单编译首次计划，不增加不完整源规划机制。服务内部判定标志
不允许从 parser args 指定，也不持久化到 Watch 参数。只清理当前任务拥有的空预约目录，
cleanup 使用 no_vector=true，不删除遗留索引或 ACL。

## 更新保护与 ACL

部分更新在内容提交计划执行前拒绝，清理仅限本轮 parser 临时产物、释放锁；旧正文、
目录/子项 ACL、祖先摘要及权限、标签和索引不变，不进入本轮后处理或推进 checkpoint。
显式传入新的 acl 也不能让不完整更新修改旧权限。

本 PR 不再自动隔离旧目录、删除旧目标/祖先索引或重建目录；不包含源撤权清理、来源绑定
推断和部分更新规划。这样避免删除作为 ACL 权威存储的索引后，重建资源继承上级共享权限。
源权限变化后的历史内容是否应清理，需要另行设计并验证，不能把“更新失败保留旧数据”
宣称为撤权隔离。本次仅保持既有更新语义，不承诺跨存储事务原子性或失败回滚。

## 结果与 Watch

首次部分成功返回 status=partial、import_complete=false、failed_files；Task completed
仍可能包含部分结果，调用方须读 Task。Watch 首轮记为 partial，不推进源 checkpoint。
后续 Watch 刷新属于更新，存在实际失败项时失败，不把新旧快照混合写入。

## 实现入口与验证

- utils/resource_processor.py：strict 门禁、最终目标锁内首次判定、临时产物清理。
- service/resource_service.py：已有预约所有权标志、Watch/Task 历史读取、无向量预约清理。
- resource/watch_manager.py、storage/queuefs/add_resource_processor.py：部分结果和 checkpoint。
- REST/SDK/CLI 继续用既有 strict；MCP 保留默认非严格首次导入及部分结果展示。

```bash
python -m pytest -q \
  tests/misc/test_resource_processor_mv.py \
  tests/service/test_resource_service_reserved_target_cleanup.py \
  tests/utils/test_local_artifact_persist.py \
  tests/utils/test_resource_processor_processing_mode.py
```

覆盖首次/预约/旧空目录/旧摘要/孤立索引/历史 Task、build_index 开关、临时失败与无权限、
完整更新及失败清理。真实 RAGFS 与生产 ACL 管理器验证旧数据未变及普通用户仍无读取权限。
扩大回归用同依赖环境对照未修改基线，不通过删除测试掩盖失败。
