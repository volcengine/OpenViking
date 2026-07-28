# HTTP Usage CountRecord Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将内置 HTTP Usage Sink 上报的事件元素改为 `CountRecord`，同时保持内部 `UsageEvent`、自定义 Sink 和 outbox 可靠性语义不变。

**Architecture:** `MemoryUsageExtractor` 继续生成 `UsageEvent`。`HttpUsageSink` 在持久化批次前执行单向转换，HTTP 批次中的 `events` 改为 `CountRecord`；`uniqueId` 继续承担重放去重键，并用于计算稳定 `batch_id`。

**Tech Stack:** Python 3.10+、dataclasses、标准库 datetime/json、pytest、Ruff。

---

## 文件结构

- Modify: `openviking/usage_reporter/http_sink.py`
  - 定义 `UsageEvent -> CountRecord` 私有转换。
  - 调整批次 ID 从 `uniqueId` 计算。
- Modify: `tests/unit/usage_reporter/test_http_sink.py`
  - 固定 recall/inject 字段映射、毫秒时间戳、扩展字段和未知事件行为。
- Modify: `docs/design/openviking-usage-reporter-sink-design.md`
  - 补充 `attributes -> extra.attributes` 的无损映射说明。

### Task 1: 固定 CountRecord 映射契约

**Files:**
- Test: `tests/unit/usage_reporter/test_http_sink.py`

- [ ] **Step 1: 扩展测试事件构造器**

让 `FakeUsageEvent` 支持 `event_type`、`occurred_at`、`resource_uri`、
`resource_type`、`task_id`、`evidence` 和 `attributes`，并继续通过
`to_dict()` 模拟真实 `UsageEvent`。

- [ ] **Step 2: 写 recall 映射失败测试**

构造 `memory.recalled` 事件并调用 `sink.write()`，读取 pending JSON，断言：

```python
assert payload["events"] == [
    {
        "CountName": "experience.recall.count",
        "OpType": "add",
        "amount": 1.0,
        "timestamp": 1785124800000,
        "uniqueId": "ue_recall",
        "tags": {
            "account_id": "2101858484",
            "user_id": "user-1",
            "resource_uri": "viking://user/user-1/memories/experiences/exchange.md",
            "resource_type": "experience",
        },
        "extra": {
            "session_id": "session-1",
            "task_id": "task-1",
            "archive_uri": "viking://user/user-1/sessions/session-1/history/archive_001",
            "message_id": "msg-1",
            "tool_call_id": "call-1",
            "tool_name": "search_experience",
            "attributes": {"rank": 1},
        },
    }
]
```

- [ ] **Step 3: 运行测试并确认 RED**

Run:

```bash
uv run pytest -q --no-cov tests/unit/usage_reporter/test_http_sink.py::TestHttpUsageSink::test_write_persists_recall_as_count_record
```

Expected: FAIL，当前 payload 仍包含 `event_type` 等 `UsageEvent` 字段。

- [ ] **Step 4: 写 inject、空字段和未知事件失败测试**

分别断言：

- `memory.injected -> experience.inject.count`
- `task_id=None`、空 `evidence`、空 `attributes` 不产生对应 `extra` 字段
- 未知 `event_type` 在 `sink.write()` 时抛出 `ValueError`

- [ ] **Step 5: 运行新增测试并确认 RED**

Run:

```bash
uv run pytest -q --no-cov tests/unit/usage_reporter/test_http_sink.py -k "count_record or unknown_event_type"
```

Expected: FAIL，CountRecord 转换尚不存在。

### Task 2: 实现 HTTP 边界转换

**Files:**
- Modify: `openviking/usage_reporter/http_sink.py`

- [ ] **Step 1: 增加事件类型映射和毫秒时间转换**

```python
_COUNT_NAMES = {
    "memory.recalled": "experience.recall.count",
    "memory.injected": "experience.inject.count",
}


def _timestamp_millis(value: str) -> int:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return int(parsed.timestamp() * 1000)
```

- [ ] **Step 2: 增加 CountRecord 转换**

```python
def _to_count_record(event: UsageEvent) -> dict[str, Any]:
    record = event.to_dict()
    event_type = str(record.get("event_type") or "")
    try:
        count_name = _COUNT_NAMES[event_type]
    except KeyError as exc:
        raise ValueError(f"unsupported usage event type: {event_type}") from exc

    evidence = record.get("evidence")
    evidence = evidence if isinstance(evidence, dict) else {}
    attributes = record.get("attributes")
    attributes = attributes if isinstance(attributes, dict) else {}
    extra = {
        "session_id": str(record.get("session_id") or ""),
        **{key: value for key, value in evidence.items() if value not in (None, "")},
    }
    if record.get("task_id") not in (None, ""):
        extra["task_id"] = record["task_id"]
    if attributes:
        extra["attributes"] = attributes

    return {
        "CountName": count_name,
        "OpType": "add",
        "amount": 1.0,
        "timestamp": _timestamp_millis(str(record["occurred_at"])),
        "uniqueId": str(record["event_id"]),
        "tags": {
            "account_id": str(record.get("account_id") or ""),
            "user_id": str(record.get("user_id") or ""),
            "resource_uri": str(record.get("resource_uri") or ""),
            "resource_type": str(record.get("resource_type") or ""),
        },
        "extra": extra,
    }
```

- [ ] **Step 3: 在持久化入口使用转换**

`_persist_events()` 先校验 `event_id`，再调用 `_to_count_record()`。移除旧的
逐事件 `prefix` 字段；资源归属继续由批次 `resource_id` 和
`V-Resource-Id` header 表达。

- [ ] **Step 4: 改用 uniqueId 生成 batch_id**

```python
unique_ids = "\n".join(str(event["uniqueId"]) for event in events)
batch_id = f"ub_{hashlib.sha256(unique_ids.encode('utf-8')).hexdigest()}"
```

- [ ] **Step 5: 运行新增测试并确认 GREEN**

Run:

```bash
uv run pytest -q --no-cov tests/unit/usage_reporter/test_http_sink.py -k "count_record or unknown_event_type"
```

Expected: PASS。

### Task 3: 回归可靠性与文档

**Files:**
- Modify: `docs/design/openviking-usage-reporter-sink-design.md`
- Test: `tests/unit/usage_reporter/test_http_sink.py`

- [ ] **Step 1: 补充 attributes 映射说明**

在 CountRecord 字段映射中明确：非空 `UsageEvent.attributes` 写入
`extra.attributes`，避免事件类型扩展字段丢失。

- [ ] **Step 2: 运行 HTTP Sink 全量测试**

Run:

```bash
uv run pytest -q --no-cov --tb=short tests/unit/usage_reporter/test_http_sink.py
```

Expected: 全部 PASS，覆盖 outbox、重试、拆包、dead-letter 和 destination
隔离。

- [ ] **Step 3: 运行 Usage Reporter 回归**

Run:

```bash
uv run pytest -q --no-cov --tb=short tests/unit/usage_reporter
```

Expected: 全部 PASS。

- [ ] **Step 4: 运行静态检查**

Run:

```bash
uv run ruff check openviking/usage_reporter/http_sink.py tests/unit/usage_reporter/test_http_sink.py
uv run ruff format --check openviking/usage_reporter/http_sink.py tests/unit/usage_reporter/test_http_sink.py
git diff --check
```

Expected: 全部通过。

- [ ] **Step 5: 提交实现**

```bash
git add openviking/usage_reporter/http_sink.py \
  tests/unit/usage_reporter/test_http_sink.py \
  docs/design/openviking-usage-reporter-sink-design.md
git commit -m "feat(usage): emit CountRecord over HTTP"
```
