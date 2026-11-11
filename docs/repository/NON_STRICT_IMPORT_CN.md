# 非严格目录导入修正

## 目标与契约

修正既有 `strict=false`：选定范围内部分文档读取或解析失败时，继续处理并安全提交
成功部分；`strict=true` 保持严格扫描/解析，并拒绝残缺提交。不增加导入参数、
CLI 选项、SDK options 或队列消息字段。

旧提交层忽略 `strict`，只要目录包含实际失败项就整批拒绝，与解析层的容错行为
及既有非严格导入说明不一致。本次修正会改变这类调用的实际结果：
默认及显式 `strict=false` 由整体失败变为可能部分成功。
要求完整结果的调用方须显式设置 `strict=true`；不承诺旧整批拒绝行为保持不变。

过滤规则和不支持格式继续由既有扫描逻辑控制。正常完整导入、根节点失败、
整体认证失败和零成功文档的行为不放宽。

## 实现入口

- `utils/resource_processor.py` 按既有 `strict` 决定目录失败项是否阻断提交。
  部分完成状态传入持久化后处理结果，供 Task 返回。
- `parse/accessors/feishu_accessor.py` 和 `parse/parsers/directory.py`
  保留有证据的源错误码，无错误码时目录失败返回不增加空字段。
- `storage/resource_rnfv.py` 标记本轮源快照是否完整；内部 `allow_partial`
  仅用于计划校验，不是公开配置，正式目录与向量清单仍须完整。
- `storage/resource_diff.py` 对未观察到的旧内容保持不变；
  `storage/context_update_plan.py` 保留对应索引及未知子树，不按残缺源修复或删除它们。
- `resource/watch_manager.py` 与 `storage/queuefs/add_resource_processor.py`
  不推进部分导入的源 checkpoint；MCP 展示部分结果，不冒充完整成功。
  REST、SDK 和 CLI 继续使用既有 strict 契约，不新增参数透传。

## 正确性边界

结果包含 `status="partial"`、`import_complete=false`、`failed_files`。
Task 为 completed 仍可能携带部分导入结果；异步受理不等于导入完成。
Watch 将部分完成记录为 partial，保留原 strict，后续刷新可补齐。

部分刷新只 upsert 本轮确认的内容，不能根据缺项推断源删除。
目录摘要复用保留的兄弟摘要，必要摘要不可用时在内容写入前失败。
可能抹除未观察子项的目录/文件类型替换同样拒绝。完整刷新恢复既有删除推断。

非严格模式不绕过认证或目标权限。源明确报告 PERMISSION_DENIED 时，标题及
拆分变化使源到产物的对应关系不稳定：保守隔离整个旧目标快照到用户临时空间，
移除其搜索记录并失效祖先摘要，再导入可读取内容。祖先目录及摘要文件使用
批量精确锁，隔离前确保用户临时父目录存在；权限、存储和锁失败向上传播。
隔离不是跨存储原子事务，不保证失败回滚。

撤权证据必须来自上游明确的权限错误：当前仅 Feishu 已知权限错误码标记为
`upstream_feishu`，并由服务端 accessor 绑定本轮源 URI。通用 PERMISSION_DENIED、
HTTP 403 文案、本地网络安全策略及目标权限拒绝均不作为撤权证据。
隔离前持有目标树锁，核对旧摘要受保护的 source 元数据；摘要缺失时，仅可复用
拥有已完成同目标导入记录的持久 Watch 绑定，新注册 Watch 不能证明旧目标来源。
绑定缺失、冲突或与失败源不匹配时失败返回，不移动旧内容或失效祖先摘要。

索引清理独立于文件树枚举，按当前账户和精确目标 URI 子树调用向量后端删除，
覆盖文件已缺失的孤立记录；整个目标树缺失但同源绑定可核实时也执行清理。
不扩展到同名前缀兄弟或其他账户，清理失败传播；存在文件时先核对移动涉及的
子项权限。`vectors_only` 可跳过摘要补读，但本轮观察并更新的记录仍须补读旧
标量字段，未显式传入的 search_tags 等字段保留；未知子项不参与补读或修复。

临时读取/解析失败不触发隔离；没有明确撤权证据的隐藏条目按未知内容保留。
strict=true 拒绝该轮残缺结果，不新增撤权隔离行为。
委托 Connector 仍由外部引擎处理，本次只修正原生导入。

## 验证

```bash
python -m pytest -q \
  tests/misc/test_resource_processor_mv.py \
  tests/parse/test_add_directory.py \
  tests/storage/test_resource_diff_snapshot.py \
  tests/resource/test_watch_manager.py \
  tests/service/test_resource_failure_details.py
```

覆盖 strict 省略/false/true、部分/零成功、认证和撤权、真实 RAGFS 锁及隔离、
不完整源删除保护、摘要依赖与类型替换、Task/Watch 状态和 checkpoint。
扩大回归须在同依赖环境对照未修改基线，不通过删除测试掩盖失败。
