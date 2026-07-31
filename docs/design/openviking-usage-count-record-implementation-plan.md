# File Usage CountRecord Implementation Plan

**Goal:** 将 Usage Reporter 生成的 `UsageEvent` 转换为稳定的
`CountRecord` 日志协议，供部署侧日志采集系统读取并投递到下游。

**Architecture:** `MemoryUsageExtractor` 继续生成内部 `UsageEvent`。
`FileLogUsageSink` 在写入专用日志文件前执行单向转换，每行保存一个包含 Kafka
message key 和对应 `CountRecord` 的 JSON envelope。`unique_id` 作为稳定事件标识，
供下游在 best-effort 投递发生重复时去重。

**Tech Stack:** Python 3.10+、dataclasses、标准库
`datetime` / `json` / `logging`、pytest、Ruff。

---

## 文件结构

- `openviking/usage_reporter/file_log_sink.py`
  - 定义 `UsageEvent -> CountRecord` 私有转换。
  - 构造资源归属 message key。
  - 将记录追加到按 UTC 小时滚动的专用日志文件。
- `tests/unit/usage_reporter/test_file_log_sink.py`
  - 固定 recall/inject 字段映射、毫秒时间戳、扩展字段和未知事件行为。
  - 验证多 worker 共享文件时的写入和滚动行为。
- `docs/design/openviking-usage-reporter-sink-design.md`
  - 定义 Usage Reporter、Sink 扩展点和文件日志协议。

## CountRecord 映射契约

`memory.recalled` 事件映射为：

```json
{
  "count_name": "experience.recall.count",
  "op_type": "add",
  "amount": 1.0,
  "timestamp": 1785124800000,
  "unique_id": "ue_recall",
  "tags": {
    "account_id": "2101858484",
    "user_id": "user-1",
    "resource_uri": "viking://user/user-1/memories/experiences/exchange.md",
    "resource_type": "experience"
  },
  "extra": {
    "session_id": "session-1",
    "task_id": "task-1",
    "archive_uri": "viking://user/user-1/sessions/session-1/history/archive_001",
    "message_id": "msg-1",
    "tool_call_id": "call-1",
    "tool_name": "search_experience",
    "attributes": {
      "rank": 1
    }
  },
  "prefix": "ov-resource-id"
}
```

映射规则：

- `memory.recalled` 映射为
  `count_name=experience.recall.count`。
- `memory.injected` 映射为
  `count_name=experience.inject.count`。
- `op_type` 固定为 `add`，`amount` 固定为 `1.0`。
- `occurred_at` 转换为毫秒时间戳。
- `event_id` 写入 `unique_id`，为空时拒绝写入。
- 非空 `UsageEvent.attributes` 写入 `extra.attributes`。
- `resource_id_env` 指定的环境变量写入 `prefix`。
- 未知 `event_type` 拒绝写入，避免产生无法解释的计量记录。

## 文件日志协议

日志行格式：

```text
{"key":"<resource_id>|<account_id>|<user_id>|<resource_uri>","value":<CountRecord JSON>}
```

当 `resource_uri` 为空时，message key 的最后一段使用 `session_id`。
整行使用 JSON envelope，key 中的 `=`、空格或其他字符不会影响 key/value 拆分。
日志文件不复用 OpenViking stdout，按 UTC 小时滚动，并保留配置数量的历史
文件。多个 server worker 写入同一路径时，文件追加和滚动通过进程间锁串行化。

文件落盘及后续采集均采用 best-effort 语义。下游必须按
`CountRecord.unique_id` 去重。

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
