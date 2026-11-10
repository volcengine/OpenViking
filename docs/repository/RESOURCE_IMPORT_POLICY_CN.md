# 资源导入完整性策略

## 契约

`import_policy` 是资源导入的顶层参数，与扫描/解析严格性 `strict` 独立：

| strict | import_policy | 行为 |
|---|---|---|
| false | require_complete（默认） | 汇总解析失败；目录存在实际读取/解析失败时不提交 |
| true | require_complete | 保留严格扫描/解析，拒绝残缺提交 |
| false | best_effort | 提交成功内容，明确报告缺项 |
| true | best_effort | 配置冲突，在源处理或上传前拒绝 |

主动过滤和非严格模式下不支持的格式沿用原行为，不作为新增完整性失败。
`args.import_policy` 和非法字段/取值不被当作别名接受。
委托 Connector 暂不支持 `best_effort`，入口明确拒绝，避免静默降级。

## 数据流与改动入口

- REST 共用 `server/resource_ingest.py:AddResourceRequest` 校验并传递策略；
  MCP 和临时上传入口使用相同校验。Python SDK 和 Rust CLI 在上传前校验冲突。
- `service/resource_service.py` 在源处理前校验，写入 `AddResourceMsg`；
  队列消费者和 Watch 重放保留策略，历史消息缺省为 `require_complete`。
- Feishu accessor 和 DirectoryParser 将有证据的源错误码传入 `failed_files`；
  无错误码时不向目录失败响应增加空字段，不从错误文案推断撤权。
- `utils/resource_processor.py` 按目录失败列表决定拒绝或部分提交；
  完整性状态传递至 RNFV 快照和最终结果。
- `storage/resource_diff.py` 和 `storage/context_update_plan.py` 对明确允许的
  不完整快照保留未观察到的旧内容及索引；不会推断删除或修复未知内容的索引。
  完整刷新恢复原删除推断。

## 提交与语义边界

部分提交结果包含 `status="partial"`、`import_complete=false`、`failed_files`。
异步接口受理只表示入队；必须读取 Task 结果确认完整或部分完成。
Watch 将部分完成记录为 `partial`，不推进该轮源 checkpoint。

语义计划复用保留的旧兄弟摘要，不能用残缺目录生成误导性汇总。
缺少必要的可复用摘要时在内容写入前拒绝；可能抹除未观察子项的
文件/目录类型替换同样拒绝。`vectors_only` 不要求旧摘要，但仍禁止误删。

## 认证与撤权

`best_effort` 不绕过认证。认证失败、根节点不可访问或零成功文档仍失败。
源显式报告 `PERMISSION_DENIED` 时，由于标题和拆分可能改变输出路径，
不能靠源文件名定位旧输出：保守隔离整个旧目标快照到当前用户临时空间，
移除其搜索记录，并失效祖先摘要及对应索引，再导入可读取内容。
隔离仍校验目标和祖先写权限，存储或权限失败向上传播，不静默成功。
祖先目录元数据及摘要文件使用批量精确锁；目录精确锁本身不覆盖子文件，
祖先树锁则会与已持有的目标子树锁冲突。
源准备阶段可能尚未分配解析临时目录，隔离前须创建当前用户临时父目录，
不能依赖先前导入曾为该用户创建目录。

短暂读取/解析失败不触发隔离；源列表静默隐藏的条目没有撤权证据，
只按未知内容保留。隔离不是跨存储原子事务，不保证失败回滚；
调用方必须区分部分提交与整体完成。

## 离线回归

```bash
python -m pytest -q \
  tests/server/test_import_policy.py \
  tests/service/test_import_policy_service.py \
  tests/storage/test_partial_import_plan.py \
  tests/misc/test_resource_processor_mv.py \
  tests/parse/test_add_directory.py \
  sdk/python/tests/test_import_policy.py
cargo test -p ov_cli
```

更广验证覆盖 `test_context_update_plan.py`、`test_resource_diff_snapshot.py`、
Feishu 导入、Watch、临时上传和 Connector。失败须用相同依赖环境对照
未修改基线，不通过删除测试或跳过校验掩盖回归。
