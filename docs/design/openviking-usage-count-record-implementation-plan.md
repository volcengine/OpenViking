# File Usage Event Log Implementation Plan

**Goal:** 将 Usage Reporter 生成的 `UsageEvent` 转换为稳定的
计量日志协议，供部署侧日志采集系统读取并投递到下游。

**Architecture:** `MemoryUsageExtractor` 继续生成内部 `UsageEvent`。
`FileLogUsageSink` 在写入专用日志文件前执行单向转换，每行保存一个扁平 JSON
计量事件。`object_id` 作为稳定事件标识，供下游在 best-effort 投递发生重复时
与 `tenant_id` 组成复合键去重。

**Tech Stack:** Python 3.10+、dataclasses、标准库
`datetime` / `json` / `logging`、pytest、Ruff。

---

## 文件结构

- `openviking/usage_reporter/file_log_sink.py`
  - 定义 `UsageEvent -> 计量日志` 私有转换。
  - 构造资源归属 `tenant_id`。
  - 将记录追加到按 UTC 小时滚动的专用日志文件。
- `tests/unit/usage_reporter/test_file_log_sink.py`
  - 固定 recall/inject 字段映射、UTC 时间、租户字段和未知事件行为。
  - 验证多 worker 共享文件时的写入和滚动行为。
- `docs/design/openviking-usage-reporter-sink-design.md`
  - 定义 Usage Reporter、Sink 扩展点和文件日志协议。

## 计量日志映射契约

`memory.recalled` 事件映射为：

```json
{
  "event_time": "2026-08-05 11:30:00",
  "tenant_id": "resource_id:ov-resource-id;account_id:2101858484;user_id:user-1;resource_uri:viking://user/user-1/memories/experiences/exchange.md",
  "event_name": "experience.recall.count",
  "object_id": "ue_recall",
  "count": 1,
  "tags": {
    "resource_type": "experience"
  }
}
```

映射规则：

- `memory.recalled` 映射为 `event_name=experience.recall.count`。
- `memory.injected` 映射为 `event_name=experience.inject.count`。
- `count` 固定为 `1`。
- `occurred_at` 转换为 UTC `YYYY-MM-DD HH:MM:SS`。
- `event_id` 写入 `object_id`，为空时拒绝写入。
- `tenant_id` 拼接部署 `resource_id`、`account_id`、`user_id` 和 `resource_uri`。
- `resource_id` 从 `resource_id_env` 指定的环境变量读取，未配置时拒绝启动 Sink。
- `tags.resource_type` 记录资源类型。
- 未知 `event_type` 拒绝写入，避免产生无法解释的计量记录。

## 文件日志协议

日志行格式：

```text
{"event_time":"<UTC time>","tenant_id":"resource_id:<resource>;account_id:<account>;user_id:<user>;resource_uri:<uri>","event_name":"<event>","object_id":"<event_id>","count":1,"tags":{"resource_type":"experience"}}
```

日志文件不复用 OpenViking stdout，按 UTC 小时滚动，并保留配置数量的历史
文件。多个 server worker 写入同一路径时，文件追加和滚动通过进程间锁串行化。

文件落盘及后续采集均采用 best-effort 语义。下游必须按
`(tenant_id, object_id)` 复合键去重，不能跨 tenant 仅按 `object_id` 全局去重。
次数查询按 `tenant_id`、`event_name` 和 `event_time` 范围过滤，并计算
`sum(count)`。

## 验证

```bash
uv run pytest -q --no-cov tests/unit/usage_reporter
uv run ruff check \
  openviking/usage_reporter/file_log_sink.py \
  tests/unit/usage_reporter/test_file_log_sink.py
uv run ruff format --check \
  openviking/usage_reporter/file_log_sink.py \
  tests/unit/usage_reporter/test_file_log_sink.py
git diff --check
```
