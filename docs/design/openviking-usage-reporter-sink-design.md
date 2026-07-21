# OpenViking Usage Reporter / Sink 技术方案

## 1. 背景

OpenViking 在 session commit 时会收到完整的 session messages。Agent runtime 调用 tool 后，会在 session messages 中留下 tool parts。部分 tool parts 可以表达某个记忆文件被检索、读取、注入等行为。

OpenViking 需要从 session 中解析出这些使用行为，并将结构化事件交给可扩展的下游。内核不绑定具体消息队列、Webhook、日志系统或数据库，而是提供通用的 Usage Reporter / Sink 扩展机制。

## 2. 业界做法

这种模式是开源基础设施里常见的设计。

OpenTelemetry Collector 把数据链路拆成 receivers、processors、exporters、pipelines。exporters 专门负责把数据发送到不同 backend 或 destination，例如 OTLP、Kafka、Prometheus、file 等。配置 exporter 本身不代表启用，需要在 pipeline 中声明。

参考：https://opentelemetry.io/docs/collector/configuration/

Vector 使用 sinks 概念，把 observability data 投递到不同目的地，例如 File、HTTP、Kafka、S3、ClickHouse、Prometheus remote write 等。

参考：https://vector.dev/docs/reference/configuration/sinks/

Fluent Bit 使用 Outputs 概念。官方定义里，Outputs 用来定义数据目的地，常见目的地包括远端服务、本地文件系统或标准接口，并且 Outputs 以 plugin 形式实现。

参考：https://docs.fluentbit.io/manual/data-pipeline/outputs

所以 OpenViking 采用“核心定义事件 + 插件式 Sink 输出”是合理的。它的核心价值是把“事件产生”和“事件去哪”解耦。

## 3. 设计目标

- OpenViking 内核只定义 UsageEvent 标准结构。
- OpenViking 内核负责从 session commit 中解析 UsageEvent。
- OpenViking 内核不绑定具体下游及其依赖。
- 部署方可以通过自定义 Sink 接入目标系统。
- Sink 失败默认不影响 session commit。
- 默认不上报完整 session，只上报结构化事件，并保留定位原始 session ToolPart 所需的证据信息。

## 4. 总体架构

数据流：

```text
Agent runtime 调用 tool
-> session message 留下 tool part
-> client 上传 session 并 commit
-> OpenViking archive session
-> UsageExtractor 从 session messages 解析 UsageEvent
-> UsageReporter 分发 UsageEvent
-> UsageSink 写入目标系统
```

模块拆分：

```text
UsageExtractor：负责从 session 里解析事件
UsageEvent：标准结构化事件
UsageReporter：负责分发事件
UsageSink：负责写入不同下游
```

## 5. UsageEvent

UsageEvent 是 OpenViking 内核和外部 Sink 之间的稳定协议。

示例：

```json
{
  "schema_version": "v1",
  "event_id": "ue_<sha256>",
  "event_type": "memory.injected",
  "resource_uri": "viking://user/test/memories/experiences/xxx.md",
  "resource_type": "experience",
  "account_id": "new",
  "user_id": "test",
  "session_id": "510bb5f9-4671-498e-adf4-27bb1b3691fe",
  "task_id": "b174eb56-e7d4-4fee-98a6-c53c0ddf62ed",
  "occurred_at": "2026-07-09T12:00:00Z",
  "evidence": {
    "archive_uri": "viking://user/test/sessions/510bb5f9/history/archive_001",
    "message_id": "msg_xxx",
    "tool_call_id": "call_xxx",
    "tool_name": "read_experience"
  },
  "attributes": {}
}
```

默认只上报结构化事件，不上报完整 session 内容。

`resource_uri` 和 `resource_type` 描述被使用的资源，不限定为记忆文件；事件类型特有的数据写入 `attributes`。当前 `MemoryUsageExtractor` 只接受属于 `UsageContext.user_id` 的规范 experience URI，其他用户 URI 不生成 UsageEvent。ToolPart 必须包含非空 `tool_id`，无法稳定标识具体调用的 ToolPart 不进入统计。

UsageEvent 是可独立传输和消费的完整事件。`UsageContext` 只用于 Extractor 构造事件，不再重复传给 Sink。

## 6. UsageSink 机制

OpenViking 开源包只定义 Sink 抽象：

```python
class UsageSink:
    async def write(self, *, events: list[UsageEvent]) -> None:
        ...
```

具体 Sink 作为外部扩展，通过 `class_path` 动态加载。

配置示例：

```yaml
server:
  usage_reporter:
    enabled: true
    sinks:
      - type: custom
        class_path: example_usage.custom_sink.CustomUsageSink
        config:
          endpoint: https://usage.example.com/events
```

OpenViking 用 `importlib` 动态加载：

```python
import importlib

def load_class(class_path: str):
    module_name, class_name = class_path.rsplit(".", 1)
    module = importlib.import_module(module_name)
    return getattr(module, class_name)
```

只有配置了该 Sink 时才 import 对应模块。OpenViking 内核不 import 或安装具体下游依赖。

每个 Sink 的 `write()` 调用最多等待 5 秒。超时或异常只记录日志，不影响其他 Sink。Reporter 在应用生命周期内只创建一次，应用退出时调用 Sink 可选的 `close()` 方法。同步和异步 `close()` 均受同一超时限制；同步 hook 在独立 daemon 线程中执行，超时后不会阻塞事件循环、后续 Sink 清理或进程退出。

## 7. 配置设计

默认关闭：

```yaml
server:
  usage_reporter:
    enabled: false
```

自定义 Sink：

```yaml
server:
  usage_reporter:
    enabled: true
    sinks:
      - type: custom
        class_path: example_usage.custom_sink.CustomUsageSink
        config:
          endpoint: https://usage.example.com/events
```

## 8. 对 OpenViking 的侵入

侵入点控制在 4 个地方。

1. 新增 config

增加 `usage_reporter` 配置段。

2. 新增数据模型

增加 `UsageEvent`、`UsageContext`。

3. session commit 增加 hook

在 session archive 成功后触发：

```text
archive session success
-> usage extractor
-> usage reporter
```

4. 新增 reporter/sink 模块

新增通用扩展点和 custom sink 动态加载能力。

不侵入的地方：

- 不改 `find/search` 语义。
- 不改 `read` 语义。
- 不把具体下游写死进 session commit。
- 不默认上传完整 session。
- 不强制写 MEMORY_FIELDS。
- 不强制写 search_tags。
- 不影响 snapshot。
- Sink 调用具有 5 秒超时边界，失败不会中断 phase2。

整体侵入属于低到中等，核心主链路只增加一个旁路 hook。

## 9. 可靠性策略

Usage Reporter 采用 best-effort 投递语义：

- Sink 成功：正常返回。
- Sink 失败或超时：记录日志，不影响 session commit，也不自动重试。
- 多个 Sink 相互隔离，某个 Sink 失败不影响其他 Sink。
- Sink 失败时事件可能丢失，因此本机制不保证 at-least-once。
- 如果 Sink 已写入成功，但进程在 phase2 写入完成标记前退出，phase2 恢复执行时可能重复发送同一事件。
- 每个事件包含稳定的 `event_id`。Sink 可将其作为 Kafka message key，消费端可按 `event_id` 去重。
- `event_id` 只用于识别重复事件，不代表事件一定成功送达。

`event_id` 为以下字段规范序列化后的 SHA-256：

```text
schema_version
+ event_type
+ account_id
+ user_id
+ session_id
+ task_id
+ evidence.archive_uri
+ evidence.tool_call_id
+ resource_uri
```

`occurred_at`、`message_id` 和 `attributes` 不参与计算，避免重放时间差或附加属性变化破坏幂等性。同一 archive 中同一 tool call 对同一资源产生的事件，在 phase2 重放后仍得到相同 `event_id`。

如果后续需要可靠投递，需要增加持久化 outbox、失败重试和发送确认机制。
